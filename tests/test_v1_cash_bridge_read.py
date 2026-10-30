"""PH-279 read-only cash reconciliation snapshot: bridge/refund/transfer consistency."""
import uuid

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from core.models import (AccountingEvent, AuditEvent, DomainEvent, LedgerPosting, PluginState,
                         SourceConsumption)
from pmo_plugins.cash import internal_transfer_services as internal
from pmo_plugins.cash import reconciliation_services as recon
from pmo_plugins.cash import refund_services as refunds
from pmo_plugins.cash.models import (CashClassification, CashClassificationReversal,
                                     CashTransaction, FundingAccount, InternalTransfer,
                                     Refund)
from tests.test_v1_cash_classification import ClassificationFixture


class BridgeFixture(ClassificationFixture):
    def snapshot(self, *, member=None, as_of=None, account=None):
        return recon.cash_bridge_snapshot(
            member or self.owner, as_of=as_of, funding_account_id=account)

    def issues(self, *, member=None, as_of=None, account=None):
        return recon.cash_bridge_issues(
            member or self.owner, as_of=as_of, funding_account_id=account)

    def codes(self, *, member=None):
        return [item['issue_code'] for item in self.issues(member=member)['issues']]

    def second_account(self):
        return self.active_account(name='第二基本户')

    def out_in_pair(self, *, gross=10000):
        out = self.posted(direction='out', gross_cents=gross)
        incoming = self.posted(direction='in', gross_cents=gross, account=self.second_account())
        return out, incoming

    def link(self, out, incoming):
        return internal.link_internal_transfer(
            self.owner, out_cash_transaction_id=out.pk,
            in_cash_transaction_id=incoming.pk,
            evidence=[{'reference': '互转依据 T-001'}],
            request_token=uuid.uuid4(), dry_run=False)


class CashBridgeSnapshotTests(BridgeFixture, TestCase):
    def test_clean_receipt_snapshot(self):
        row = self.posted(gross_cents=10000)
        self.classify(row, classification='receipt')
        result = self.snapshot()
        self.assertFalse(result['offset_implemented'])
        self.assertEqual(result['summary']['posted_cash_count'], 1)
        item = result['transactions'][0]
        self.assertEqual(item['gross_cents'], 10000)
        self.assertEqual(item['classification']['classification'], 'receipt')
        self.assertEqual(item['accounting_event']['amount_cents'], 10000)
        self.assertEqual(item['postings'][0]['amount_cents'], 10000)
        self.assertEqual(item['postings'][0]['book'], 'cash_in')
        self.assertEqual(item['claim']['coverage_key'], 'bank_effect')
        self.assertEqual(item['claim']['net_effect_cents'], 10000)
        self.assertEqual(self.issues()['issue_count'], 0)
        self.assertEqual(self.issues()['issues'], [])

    def test_unclassified_posted_cash_is_issue(self):
        self.posted(gross_cents=500)
        self.assertEqual(self.codes(), ['classification_missing'])

    def test_refund_coverage_is_consistent_and_not_classification_missing(self):
        row = self.posted(gross_cents=10000)
        self.classify(row, classification='receipt')
        refund = refunds.record_refund(
            self.owner, original_cash_transaction_id=row.pk, refund_date=self.today,
            gross_cents=2000, reason='客户退款', request_token=uuid.uuid4(), dry_run=False)
        result = self.snapshot()
        self.assertEqual(result['summary']['refund_count'], 1)
        self.assertEqual(result['summary']['refund_total_cents'], 2000)
        self.assertEqual(result['summary']['posted_cash_count'], 2)  # original + refund cash
        self.assertEqual(self.issues()['issue_count'], 0)
        derived = [item for item in result['transactions'] if item['derived_cash'] == 'refund']
        self.assertEqual(len(derived), 1)
        self.assertIsNone(derived[0]['classification'])
        self.assertEqual(result['transactions'][0]['refunds']['total_cents'], 2000)

    def test_internal_transfer_pair_and_company_net(self):
        out, incoming = self.out_in_pair(gross=10000)
        transfer = self.link(out, incoming)
        result = self.snapshot()
        self.assertEqual(result['summary']['internal_transfer_count'], 2)
        self.assertEqual(self.issues()['issue_count'], 0)
        for item in result['transactions']:
            self.assertEqual(item['internal_transfer']['ref']['id'], str(transfer.pk))
            self.assertEqual(item['internal_transfer']['status'], 'active')
            self.assertEqual(item['classification']['classification'], 'transfer')
        net = sum(account['book_net_cents'] for account in result['accounts'])
        self.assertEqual(net, 0)
        self.assertEqual(self.issues()['issue_count'], 0)

    def test_as_of_filters_future_facts(self):
        row = self.posted(gross_cents=700)
        self.classify(row, classification='receipt')
        result = self.snapshot(as_of=self.today.isoformat())
        self.assertEqual(result['summary']['posted_cash_count'], 1)
        result = self.snapshot(as_of='2000-01-01')
        self.assertEqual(result['summary']['posted_cash_count'], 0)
        self.assertEqual(result['summary']['issue_count'], 0)

    def test_issue_codes_for_bridge_tampering(self):
        row = self.posted(gross_cents=1000)
        classification = self.classify(row, classification='receipt')
        event = AccountingEvent.objects.get(source=classification.classification_ref)
        # duplicate bridge event
        AccountingEvent.objects.create(
            organization=self.org, source=classification.classification_ref,
            event_type='cash.classification', occurred_on=event.occurred_on,
            posting_period=event.posting_period, algorithm_version=event.algorithm_version,
            claim=event.claim, claim_effect_cents=event.claim_effect_cents)
        # wrong posting amount on a separate transaction
        second = self.posted(gross_cents=2000)
        second_classification = self.classify(second, classification='receipt')
        second_event = AccountingEvent.objects.get(
            source=second_classification.classification_ref)
        posting = second_event.postings.get()
        LedgerPosting.objects.filter(pk=posting.pk).update(amount_cents=999)
        codes = self.codes()
        self.assertIn('event_duplicate', codes)
        self.assertIn('posting_amount_mismatch', codes)

    def test_read_only_and_module_disabled_history_readable(self):
        row = self.posted(gross_cents=100)
        self.classify(row, classification='receipt')
        before = (AccountingEvent.objects.count(), LedgerPosting.objects.count(),
                  SourceConsumption.objects.count(), AuditEvent.objects.count(),
                  DomainEvent.objects.count())
        self.snapshot()
        self.issues()
        after = (AccountingEvent.objects.count(), LedgerPosting.objects.count(),
                 SourceConsumption.objects.count(), AuditEvent.objects.count(),
                 DomainEvent.objects.count())
        self.assertEqual(before, after)
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.assertEqual(self.snapshot()['summary']['posted_cash_count'], 1)
        self.assertEqual(self.issues()['issue_count'], 0)

    def test_permissions(self):
        self.posted(gross_cents=100)
        for member in (self.finance, self.auditor):
            self.assertIn('posted_cash_count', self.snapshot(member=member)['summary'])
        for member in (self.pm, self.plain):
            with self.assertRaises(PermissionDenied):
                self.snapshot(member=member)
            with self.assertRaises(PermissionDenied):
                self.issues(member=member)

    def test_offset_placeholder_is_explicit(self):
        result = self.snapshot()
        self.assertFalse(result['offset_implemented'])
        self.assertEqual(result['scope'], 'company')
