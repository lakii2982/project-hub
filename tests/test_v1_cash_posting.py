"""PH-230 cash posting: draft->posted, one canonical event, read-only coverage."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, AuditEvent, DomainEvent, LedgerEntry,
                         LedgerPosting, Membership, Organization, PluginState, SourceConsumption)
from pmo_plugins.cash import funding_services as funding
from pmo_plugins.cash import transaction_services as cash
from pmo_plugins.cash.models import CashReceipt, CashTransaction, FundingAccount
from pmo_plugins.settlement.models import OpenItem, Settlement
from tests.test_v1_cash import CashFixture

POSTED_TOPIC = 'cash.transaction_recorded.v1'
POSTED_ACTION = 'cash.cash_transaction.posted'


class CashPostingFixture(CashFixture):
    def post(self, row, *, member=None, **changes):
        values = {'expected_version': row.version, 'request_token': uuid.uuid4(),
                  'dry_run': False}
        values.update(changes)
        return cash.record_cash_transaction(member or self.owner, row.pk, **values)

    def facts(self):
        return tuple(model.objects.count() for model in (
            FundingAccount, CashTransaction, CashReceipt, DomainEvent, AuditEvent,
            AccountingEvent, LedgerPosting, LedgerEntry, Settlement, OpenItem, SourceConsumption))


class CashPostingTests(CashPostingFixture, TestCase):
    def test_draft_to_posted_writes_one_canonical_event_and_receipt(self):
        row = self.tx(gross_cents=100)
        before = self.facts()
        posted = self.post(row)
        self.assertEqual((posted.status, posted.version), ('posted', 2))
        after = self.facts()
        self.assertEqual(after[:2], before[:2])
        self.assertEqual(after[2], before[2] + 1)  # one post receipt
        self.assertEqual(after[3], before[3] + 2)  # generic audit outbox + canonical event
        self.assertEqual(after[4], before[4] + 2)  # generic + suppressed-source audits
        self.assertEqual(before[5:], after[5:])
        canonical = DomainEvent.objects.filter(topic=POSTED_TOPIC)
        self.assertEqual(canonical.count(), 1)
        event = canonical.get()
        ref = {'namespace': 'cash', 'kind': 'cash_transaction', 'id': str(row.pk), 'version': 2}
        self.assertEqual(event.data['source'], ref)
        self.assertEqual(event.data['topic'], POSTED_TOPIC)
        self.assertEqual(event.data['schema_version'], 1)
        self.assertEqual(event.data['organization_id'], str(self.org.pk))
        self.assertEqual(event.data['payload'], {'changed_refs': [ref], 'posting_event_refs': []})
        self.assertEqual(DomainEvent.objects.filter(
            topic='cash.cash_transaction.posted.v1').count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action=POSTED_ACTION, entity='CashTransaction',
                                                   entity_id=str(row.pk)).count(), 1)
        receipt = CashReceipt.objects.get(action='cash_transaction.post')
        self.assertEqual(receipt.result, ref)
        snapshot = cash.cash_transaction_snapshot(self.owner, row.pk)
        self.assertEqual((snapshot['status'], snapshot['status_display'], snapshot['can_post']),
                         ('posted', '已过账', False))
        self.assertIsNotNone(snapshot['posted_at'])

    def test_dry_run_preview_and_receipt_replay(self):
        row = self.tx()
        before = self.facts()
        preview = self.post(row, dry_run=True)
        self.assertEqual((preview.status, preview.version), ('posted', 2))
        self.assertEqual(before, self.facts())
        token = uuid.uuid4()
        posted = self.post(row, request_token=token)
        after = self.facts()
        replay = self.post(row, request_token=token)
        self.assertEqual(replay.pk, posted.pk)
        self.assertEqual(after, self.facts())
        dry_replay = cash.record_cash_transaction(self.owner, row.pk,
            expected_version=row.version, request_token=token)
        self.assertEqual(dry_replay.status, 'posted')
        self.assertEqual(after, self.facts())
        self.denied(lambda: self.post(row, request_token=token,
                                      occurred_on=self.today - timedelta(days=1)))
        self.denied(lambda: self.post(row, request_token=token, posting_period='2000-01'))

    def test_rejections_have_no_half_write(self):
        row = self.tx()
        token = uuid.uuid4()
        self.denied(lambda: self.post(row, occurred_on=self.today - timedelta(days=1)))
        self.denied(lambda: self.post(row, posting_period='2000-01'))
        self.denied(lambda: self.post(row, posting_period='2026-1'))
        self.denied(lambda: self.post(row, expected_version=row.version + 1))
        for value in (True, 1.0, 0, -1, '1'):
            self.denied(lambda value=value: self.post(row, expected_version=value))
        for value in (True, 1.0, 'today'):
            self.denied(lambda value=value: self.post(row, occurred_on=value))
        for value in (0, 1, 1.0, None, 'false'):
            self.denied(lambda value=value: cash.record_cash_transaction(
                self.owner, row.pk, expected_version=row.version,
                request_token=uuid.uuid4(), dry_run=value))
        self.org.locked_through = self.today
        self.org.save()
        self.denied(lambda: self.post(row))
        self.org.locked_through = None
        self.org.save()
        AccountingPeriod.objects.update_or_create(organization=self.org,
            period=self.today.strftime('%Y-%m'),
            defaults={'status': 'posted', 'closed_through': self.today})
        self.denied(lambda: self.post(row))
        AccountingPeriod.objects.filter(organization=self.org).delete()
        future = CashTransaction.objects.create(
            organization=self.org, funding_account=self.account,
            transaction_date=self.today + timedelta(days=1), direction='in', gross_cents=100,
            external_source_key='FUTURE-POST', payee={'kind': 'unidentified', 'bank_label': None},
            evidence=[{'evidence_id': 'receipt-1', 'sha256': 'a' * 64, 'kind': 'bank_proof'}],
            status='draft', version=1, created_by=self.owner)
        self.denied(lambda: self.post(future))
        other = Organization.objects.create(name='其他公司')
        foreign_account = FundingAccount.objects.create(organization=other, name='外部户',
            opening_on=self.today, created_by=self.owner)
        foreign = CashTransaction.objects.create(
            organization=other, funding_account=foreign_account,
            transaction_date=self.today, direction='in', gross_cents=100,
            external_source_key='FOREIGN-POST', payee={'kind': 'unidentified', 'bank_label': None},
            evidence=[{'evidence_id': 'receipt-2', 'sha256': 'b' * 64, 'kind': 'bank_proof'}],
            status='draft', version=1, created_by=self.owner)
        self.denied(lambda: self.post(foreign))
        Membership.objects.filter(pk=self.owner.pk).update(active=False)
        self.denied(lambda: self.post(row))
        Membership.objects.filter(pk=self.owner.pk).update(active=True)
        PluginState.objects.update_or_create(organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.denied(lambda: self.post(row))
        PluginState.objects.filter(organization=self.org, plugin_id='cash').delete()
        self.post(row)
        self.denied(lambda: self.post(row))
        fresh = CashTransaction.objects.get(pk=row.pk)
        self.denied(lambda: cash.record_cash_transaction(self.finance, row.pk,
            expected_version=fresh.version, request_token=token, dry_run=False))
        self.assertEqual(DomainEvent.objects.filter(topic=POSTED_TOPIC).count(), 1)

    def test_coverage_is_read_only_estimate_with_inclusive_as_of(self):
        incoming = self.tx(direction='in', gross_cents=100, external_source_key='C-IN')
        outgoing = self.tx(direction='out', gross_cents=40, external_source_key='C-OUT')
        draft = self.tx(direction='in', gross_cents=5, external_source_key='C-DRAFT')
        self.post(incoming)
        self.post(outgoing)
        other = Organization.objects.create(name='跨公司')
        foreign = FundingAccount.objects.create(organization=other, name='跨公司户',
            opening_on=self.today, created_by=self.owner)
        before = self.facts()
        data = funding.cash_account_coverage(self.owner, self.account.pk)
        self.assertEqual((data['posted_in_cents'], data['posted_out_cents']), (100, 40))
        self.assertEqual((data['posted_net_estimate_cents'], data['available_estimate_cents']),
                         (60, 60))
        self.assertEqual((data['cleared_cents'], data['posted_count'], data['draft_count']),
                         (0, 2, 1))
        self.assertEqual(data['as_of'], self.today.isoformat())
        self.assertIn('非现金余额', data['estimate_label'])
        self.assertEqual(funding.cash_account_coverage(
            self.auditor, self.account.pk, as_of=self.today)['posted_count'], 2)
        yesterday = funding.cash_account_coverage(
            self.auditor, self.account.pk, as_of=self.today - timedelta(days=1))
        self.assertEqual((yesterday['posted_count'], yesterday['draft_count'],
                          yesterday['posted_net_estimate_cents']), (0, 0, 0))
        future = funding.cash_account_coverage(self.owner, self.account.pk,
                                               as_of=self.today + timedelta(days=30))
        self.assertEqual(future['posted_count'], 2)
        for actor in (self.pm, self.plain):
            self.denied(lambda actor=actor: funding.cash_account_coverage(actor, self.account.pk))
        self.denied(lambda: funding.cash_account_coverage(self.owner, foreign.pk))
        self.denied(lambda: funding.cash_account_coverage(self.owner, self.account.pk,
                                                          as_of='2026-1-1'))
        self.denied(lambda: funding.cash_account_coverage(self.owner, self.account.pk,
                                                          as_of=True))
        self.assertEqual(before, self.facts())
        PluginState.objects.update_or_create(organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.assertEqual(funding.cash_account_coverage(
            self.finance, self.account.pk)['posted_count'], 2)
        self.assertEqual(len(cash.cash_transaction_list(self.auditor)), 3)
        self.denied(lambda: self.post(draft))

    def test_projection_can_post_and_status_filters(self):
        draft = self.tx()
        self.assertTrue(cash.cash_transaction_snapshot(self.owner, draft.pk)['can_post'])
        self.assertTrue(cash.cash_transaction_snapshot(self.finance, draft.pk)['can_post'])
        self.assertFalse(cash.cash_transaction_snapshot(self.auditor, draft.pk)['can_post'])
        self.assertIsNone(cash.cash_transaction_snapshot(self.owner, draft.pk)['posted_at'])
        posted = self.post(draft)
        self.assertFalse(cash.cash_transaction_snapshot(self.owner, posted.pk)['can_post'])
        self.assertEqual([row['id'] for row in cash.cash_transaction_list(
            self.owner, status='posted')], [str(posted.pk)])
        self.assertEqual(cash.cash_transaction_list(self.owner, status='draft'), [])
        self.denied(lambda: cash.cash_transaction_list(self.owner, status='bogus'))
        self.denied(lambda: cash.cash_transaction_list(self.pm))
        self.denied(lambda: cash.cash_transaction_list(self.plain))

    def test_roles_and_module_disable(self):
        row = self.tx()
        for actor in (self.pm, self.plain, self.auditor):
            self.denied(lambda actor=actor: self.post(row, member=actor))
        PluginState.objects.update_or_create(organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.denied(lambda: self.post(row))
        self.assertEqual(cash.cash_transaction_snapshot(self.auditor, row.pk)['status'], 'draft')
        self.assertEqual(len(cash.cash_transaction_list(self.finance)), 1)
        self.assertEqual(funding.cash_account_coverage(
            self.auditor, self.account.pk)['draft_count'], 1)

    def test_posted_facts_are_immutable(self):
        posted = self.post(self.tx())
        canonical_before = DomainEvent.objects.filter(topic=POSTED_TOPIC).count()
        posted.gross_cents = 999
        with self.assertRaises(ValidationError):
            posted.save()
        with self.assertRaises(ValidationError):
            posted.delete()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CashTransaction.objects.filter(pk=posted.pk).update(gross_cents=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CashTransaction.objects.filter(pk=posted.pk).update(status='draft')
        self.assertEqual(DomainEvent.objects.filter(topic=POSTED_TOPIC).count(),
                         canonical_before)

    def test_posting_transition_freezes_every_field_but_status_and_version(self):
        draft = self.tx()
        for changes in ({'created_by': self.finance}, {'created_at': timezone.now()},
                        {'gross_cents': draft.gross_cents + 1},
                        {'version': draft.version + 2}):
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    CashTransaction.objects.filter(pk=draft.pk).update(
                        status='posted', **changes)
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'draft')
        posted = self.post(draft)
        self.assertEqual(posted.status, 'posted')
        self.assertEqual(posted.version, draft.version + 1)
