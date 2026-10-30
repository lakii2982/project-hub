"""PH-288 read-only bank-account balance reconciliation snapshot."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, AuditEvent, DomainEvent, LedgerEntry,
                         LedgerPosting, PluginState, SourceConsumption)
from pmo_plugins.cash import bank_balance_services as bank
from pmo_plugins.cash import internal_transfer_services as internal
from pmo_plugins.cash import refund_services as refunds
from pmo_plugins.cash.models import (CashClassification, CashTransaction, FundingAccount,
                                     InternalTransfer, Refund)
from tests.test_v1_cash_classification import ClassificationFixture


class BankBalanceFixture(ClassificationFixture):
    def second_account(self):
        return self.active_account(name='第二基本户')

    def snapshot(self, *, member=None, account=None, as_of=None, period=None):
        return bank.bank_account_balance_snapshot(
            member or self.owner, funding_account_id=account, as_of=as_of, period=period)

    def issues(self, *, member=None, as_of=None, period=None):
        return bank.bank_account_issues(member or self.owner, as_of=as_of, period=period)

    def codes(self, *, member=None, period=None):
        return [item['issue_code'] for item in self.issues(member=member, period=period)['issues']]

    def account_row(self, result, account=None):
        target = str((account or self.account).pk)
        return next(row for row in result['accounts']
                    if row['account_ref']['id'] == target)

    def refund(self, cash_row, amount=2000, *, day=None):
        return refunds.record_refund(
            self.owner, original_cash_transaction_id=cash_row.pk,
            refund_date=day or self.today,
            gross_cents=amount, reason='客户退款', request_token=uuid.uuid4(), dry_run=False)

    def transfer(self, out_row, in_row):
        return internal.link_internal_transfer(
            self.owner, out_cash_transaction_id=out_row.pk,
            in_cash_transaction_id=in_row.pk,
            evidence=[{'reference': '互转依据 T-001'}],
            request_token=uuid.uuid4(), dry_run=False)

    def fact_counts(self):
        return tuple(model.objects.count() for model in (
            FundingAccount, CashTransaction, Refund, CashClassification,
            AccountingEvent, LedgerPosting, LedgerEntry, SourceConsumption,
            AuditEvent, DomainEvent))


class BankBalanceServiceTests(BankBalanceFixture, TestCase):
    def test_golden_account_balance_and_clean_issues(self):
        incoming = [self.posted(gross_cents=10000, direction='in') for _ in range(3)]
        outgoing = self.posted(gross_cents=5000, direction='out')
        for row in incoming:
            self.classify(row, classification='receipt')
        self.classify(outgoing, classification='payment')
        refund = self.refund(incoming[0], amount=2000)
        second = self.second_account()
        out_leg = self.posted(gross_cents=1000, direction='out')
        in_leg = self.posted(gross_cents=1000, direction='in', account=second)
        self.transfer(out_leg, in_leg)
        result = self.snapshot()
        row = self.account_row(result)
        self.assertEqual(row['posted_in_cents'], 30000)
        # 5000 payment + 2000 refund cash + 1000 transfer out leg
        self.assertEqual(row['posted_out_cents'], 8000)
        self.assertEqual(row['posted_net_cents'], 22000)
        self.assertEqual(row['refunded_cents'], 2000)
        self.assertEqual(row['internal_transfer_out_cents'], 1000)
        self.assertEqual(row['internal_transfer_in_cents'], 0)
        self.assertEqual(row['ledger_net_cents'], 24000)
        self.assertEqual(row['opening_balance_cents'], 0)
        self.assertEqual(row['closing_balance_cents'], 22000)
        self.assertEqual(row['closing_balance_cents'],
                         row['opening_balance_cents'] + row['posted_in_cents']
                         - row['posted_out_cents'])
        self.assertFalse(row['offset_implemented'])
        self.assertEqual(row['offset_cents'], 0)
        self.assertEqual(self.issues()['issue_count'], 0)
        self.assertEqual(self.issues()['issues'], [])

    def test_unclassified_posted_cash_and_ledger_mismatch(self):
        self.posted(gross_cents=1000, direction='in')
        codes = self.codes()
        self.assertIn('unclassified_posted_cash', codes)
        self.assertIn('ledger_net_mismatch', codes)

    def test_append_only_extra_posting_breaks_ledger_net(self):
        row = self.posted(gross_cents=1000, direction='in')
        classification = self.classify(row, classification='receipt')
        event = AccountingEvent.objects.get(source=classification.classification_ref)
        LedgerPosting.objects.create(
            organization=self.org, accounting_event=event, version=1, status='posted',
            book='cash_in', amount_cents=999, scope_kind='funding_account',
            scope_id=str(self.account.pk), line_index=1, origin='current')
        self.assertIn('ledger_net_mismatch', self.codes())

    def test_negative_balance_is_issue(self):
        self.posted(gross_cents=5000, direction='out')
        self.assertIn('negative_balance', self.codes())

    def test_period_opening_and_future_after_closed_period(self):
        yesterday = self.today - timedelta(days=1)
        prior_day = self.today.replace(day=1) - timedelta(days=1)
        prior = self.posted(gross_cents=7000, direction='in', transaction_date=prior_day)
        self.classify(prior, classification='receipt')
        current = self.posted(gross_cents=3000, direction='in')
        self.classify(current, classification='receipt')
        period = self.today.strftime('%Y-%m')
        result = self.snapshot(period=period)
        row = self.account_row(result)
        self.assertEqual(row['opening_balance_cents'], 7000)
        self.assertEqual(row['posted_in_cents'], 3000)
        self.assertEqual(row['closing_balance_cents'], 10000)
        # clean period with prior history must not raise a false ledger_net_mismatch
        self.assertEqual(self.issues(period=period)['issue_count'], 0)
        AccountingPeriod.objects.create(
            organization=self.org, period=period, status='posted',
            closed_through=yesterday)
        self.assertIn('future_fact_after_closed_period', self.codes(period=period))

    def test_prior_refund_excluded_from_period_window(self):
        prior_day = self.today.replace(day=1) - timedelta(days=1)
        prior = self.posted(gross_cents=7000, direction='in', transaction_date=prior_day)
        self.classify(prior, classification='receipt')
        self.refund(prior, amount=1000, day=prior_day)
        current = self.posted(gross_cents=3000, direction='in')
        self.classify(current, classification='receipt')
        period = self.today.strftime('%Y-%m')
        result = self.snapshot(period=period)
        row = self.account_row(result)
        self.assertEqual(row['opening_balance_cents'], 6000)  # 7000 in - 1000 prior refund out
        self.assertEqual(row['posted_in_cents'], 3000)
        self.assertEqual(row['refunded_cents'], 0)  # prior refund excluded from the window
        self.assertEqual(row['closing_balance_cents'], 9000)
        self.assertEqual(self.issues(period=period)['issue_count'], 0)

    def test_future_transfer_excluded_by_as_of(self):
        second = self.second_account()
        out_leg = self.posted(gross_cents=1000, direction='out')
        in_leg = self.posted(gross_cents=1000, direction='in', account=second)
        tomorrow = self.today + timedelta(days=1)
        InternalTransfer.objects.create(
            organization=self.org, out_cash_transaction=out_leg,
            in_cash_transaction=in_leg, out_cash_ref=out_leg.transaction_ref,
            in_cash_ref=in_leg.transaction_ref, transfer_date=tomorrow,
            gross_cents=1000, out_transaction_date=out_leg.transaction_date,
            in_transaction_date=in_leg.transaction_date, status='active', version=1,
            created_by=self.owner)
        self.assertEqual(self.snapshot(as_of=self.today.isoformat())['summary'][
            'internal_transfer_count'], 0)
        self.assertEqual(self.snapshot(as_of=tomorrow.isoformat())['summary'][
            'internal_transfer_count'], 1)

    def test_as_of_filters(self):
        row = self.posted(gross_cents=1000, direction='in')
        self.classify(row, classification='receipt')
        self.assertEqual(self.snapshot(as_of='2000-01-01')['summary']['posted_in_cents'], 0)
        self.assertEqual(self.snapshot(as_of=self.today.isoformat())['summary'][
            'posted_in_cents'], 1000)

    def test_permissions_and_module_disabled_history(self):
        self.posted(gross_cents=1000, direction='in')
        for member in (self.finance, self.auditor):
            self.assertEqual(self.snapshot(member=member)['scope'], 'company')
        for member in (self.pm, self.plain):
            with self.assertRaises(PermissionDenied):
                self.snapshot(member=member)
            with self.assertRaises(PermissionDenied):
                self.issues(member=member)
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.assertGreaterEqual(self.snapshot()['summary']['account_count'], 1)

    def test_read_only_fact_counts(self):
        row = self.posted(gross_cents=1000, direction='in')
        self.classify(row, classification='receipt')
        before = self.fact_counts()
        self.snapshot()
        self.issues()
        self.assertEqual(before, self.fact_counts())

    def test_derived_balance_semantics(self):
        self.posted(gross_cents=4000, direction='in')
        self.posted(gross_cents=1500, direction='out')
        row = self.account_row(self.snapshot())
        self.assertEqual(row['posted_net_cents'],
                         row['posted_in_cents'] - row['posted_out_cents'])
        self.assertEqual(row['closing_balance_cents'],
                         row['opening_balance_cents'] + row['posted_net_cents'])
