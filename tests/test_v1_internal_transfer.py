"""PH-245 internal transfer: pair one out leg and one in leg of the same company cash."""
from io import StringIO
import json
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, DomainEvent, LedgerEntry, LedgerPosting,
                         Organization, PluginState, SourceConsumption)
from pmo_plugins.cash import funding_services as funding
from pmo_plugins.cash import internal_transfer_services as internal
from pmo_plugins.cash import transaction_services as cash
from pmo_plugins.cash.models import (CashClassification, CashClassificationReversal,
                                     CashReceipt, InternalTransfer,
                                     InternalTransferReceipt, InternalTransferReversal)
from tests.test_v1_contracts import Fixture

LINKED_TOPIC = 'cash.internal_transfer_linked.v1'
REVERSED_TOPIC = 'cash.internal_transfer_reversed.v1'


class TransferFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.finance = self.member('finance')
        self.auditor = self.member('auditor')
        self.plain = self.member('member')
        self.out_account = self.account('转出基本户')
        self.in_account = self.account('转入基本户')
        self.out_cash = self.post(self.out_account, 'out', 50000, 'OUT-1')
        self.in_cash = self.post(self.in_account, 'in', 50000, 'IN-1')

    def account(self, name):
        draft = funding.save_funding_account(self.owner, name=name,
                                             request_token=uuid.uuid4(), dry_run=False)
        return funding.activate_funding_account(
            self.owner, draft.pk, expected_version=draft.version,
            request_token=uuid.uuid4(), dry_run=False)

    def post(self, account, direction, gross, key, *, posted=True, day=None,
             member=None, organization=None):
        if organization is not None:
            member = self.member('owner')
            member.organization = organization
            member.save(update_fields=['organization'])
        row = cash.save_cash_transaction(
            member or self.owner, funding_account_id=account.pk,
            transaction_date=day or self.today, direction=direction, gross_cents=gross,
            external_source_key=key, payee={'kind': 'unidentified'},
            evidence=[{'reference': '内部划转回单'}], request_token=uuid.uuid4(),
            dry_run=False)
        if posted:
            row = cash.record_cash_transaction(
                member or self.owner, row.pk, expected_version=row.version,
                request_token=uuid.uuid4(), dry_run=False)
        return row

    def link(self, *, out_cash=None, in_cash=None, member=None, **changes):
        values = {'out_cash_transaction_id': (out_cash or self.out_cash).pk,
                  'in_cash_transaction_id': (in_cash or self.in_cash).pk,
                  'evidence': [{'reference': '内部互转依据'}],
                  'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return internal.link_internal_transfer(member or self.owner, **values)

    def reverse(self, transfer, *, member=None, **changes):
        values = {'reason': '内部互转依据错误', 'evidence': [{'reference': '撤转依据'}],
                  'request_token': uuid.uuid4(), 'expected_version': transfer.version,
                  'dry_run': False}
        values.update(changes)
        return internal.reverse_internal_transfer(member or self.owner, transfer.pk, **values)

    def facts(self):
        return tuple(model.objects.count() for model in (
            InternalTransfer, InternalTransferReversal, InternalTransferReceipt,
            CashClassification, CashClassificationReversal, CashReceipt,
            AccountingEvent, LedgerPosting, LedgerEntry, SourceConsumption))

    def verify_payload(self):
        out = StringIO()
        try:
            call_command('verify_integrity', '--json', stdout=out)
        except CommandError:
            pass
        return json.loads(out.getvalue())


class TransferLifecycleTests(TransferFixture, TestCase):
    def test_golden_link_writes_pair_classifications_and_linked_event(self):
        transfer = self.link()
        self.assertEqual((transfer.status, transfer.version, transfer.gross_cents),
                         ('active', 1, 50000))
        self.assertEqual(transfer.internal_transfer_ref['kind'], 'internal_transfer')
        self.assertEqual(CashClassification.objects.filter(
            classification='transfer', status='active').count(), 2)
        self.assertEqual(AccountingEvent.objects.count(), 2)
        self.assertEqual(LedgerPosting.objects.count(), 2)
        self.assertEqual(SourceConsumption.objects.filter(consumer='cash').count(), 2)
        linked = DomainEvent.objects.get(topic=LINKED_TOPIC)
        payload = linked.data['payload']
        self.assertEqual(len(payload['changed_refs']), 3)
        self.assertEqual(payload['changed_refs'][0], transfer.internal_transfer_ref)
        self.assertEqual(payload['posting_event_refs'], [])
        # Company cash book net is zero: one cash_out and one cash_in posting of equal gross.
        from core.cash_accounting import cash_book_totals
        out_in, out_out = cash_book_totals(self.org.pk, self.out_account.pk,
                                           through=self.today)
        in_in, in_out = cash_book_totals(self.org.pk, self.in_account.pk,
                                         through=self.today)
        self.assertEqual((out_in - out_out) + (in_in - in_out), 0)
        self.assertEqual(CashReceipt.objects.filter(
            action='cash_classification.record').count(), 2)  # two classification receipts
        self.assertEqual(InternalTransferReceipt.objects.count(), 1)
        self.assertTrue(self.verify_payload()['ok'])

    def test_reverse_appends_mirror_and_keeps_originals(self):
        transfer = self.link()
        reversal = self.reverse(transfer)
        transfer.refresh_from_db()
        self.assertEqual((transfer.status, reversal.status), ('reversed', 'posted'))
        self.assertEqual(CashClassificationReversal.objects.count(), 2)
        self.assertEqual(InternalTransferReversal.objects.count(), 1)
        self.assertEqual(reversal.reversal_ref['kind'], 'internal_transfer_reversal')
        reversed_event = DomainEvent.objects.get(topic=REVERSED_TOPIC)
        payload = reversed_event.data['payload']
        self.assertEqual(reversed_event.data['source'], reversal.reversal_ref)
        self.assertEqual(payload['original_internal_transfer_ref'],
                         transfer.internal_transfer_ref)
        self.assertEqual(len(payload['changed_refs']), 3)
        self.assertEqual(len(payload['classification_reversal_refs']), 2)
        self.assertEqual(payload['posting_event_refs'], [])
        self.assertEqual(CashClassification.objects.filter(
            classification='transfer', status='active').count(), 0)
        from core.cash_accounting import cash_book_totals
        out_in, out_out = cash_book_totals(self.org.pk, self.out_account.pk,
                                           through=self.today)
        in_in, in_out = cash_book_totals(self.org.pk, self.in_account.pk,
                                         through=self.today)
        self.assertEqual((out_in - out_out) + (in_in - in_out), 0)
        self.assertTrue(self.verify_payload()['ok'])

    def test_idempotent_replay_and_changed_input_conflict(self):
        token = uuid.uuid4()
        first = self.link(request_token=token)
        replay = self.link(request_token=token)
        self.assertEqual(first.pk, replay.pk)
        with self.assertRaises(ValidationError):
            self.link(request_token=token, out_cash=self.in_cash, in_cash=self.out_cash)
        reverse_token = uuid.uuid4()
        reversal = self.reverse(first, request_token=reverse_token)
        replay_reversal = self.reverse(first, request_token=reverse_token)
        self.assertEqual(reversal.pk, replay_reversal.pk)

    def test_dry_run_has_no_side_effects(self):
        before = self.facts()
        row = self.link(dry_run=True)
        self.assertTrue(row._state.adding)
        self.assertEqual(before, self.facts())

    def test_rejections_leave_zero_half_write(self):
        before = self.facts()
        for kwargs in ({'out_cash': self.in_cash, 'in_cash': self.out_cash},
                       {'in_cash': self.out_cash},
                       {'out_cash': self.out_cash, 'in_cash': self.out_cash}):
            with self.assertRaises(ValidationError):
                self.link(**kwargs)
        self.assertEqual(before, self.facts())

    def test_rejects_draft_cash(self):
        draft = self.post(self.in_account, 'in', 50000, 'IN-DRAFT', posted=False)
        with self.assertRaises(ValidationError):
            self.link(in_cash=draft)

    def test_rejects_same_account_amount_or_date_mismatch(self):
        same_account = self.post(self.out_account, 'in', 50000, 'IN-SAME')
        with self.assertRaises(ValidationError):
            self.link(in_cash=same_account)
        other_amount = self.post(self.in_account, 'in', 40000, 'IN-AMOUNT')
        with self.assertRaises(ValidationError):
            self.link(in_cash=other_amount)
        other_day = self.post(self.in_account, 'in', 50000, 'IN-DATE',
                              day=self.today - timedelta(days=1))
        with self.assertRaises(ValidationError):
            self.link(in_cash=other_day)

    def test_rejects_future_and_locked_period(self):
        from pmo_plugins.cash.models import CashTransaction
        future_cash = CashTransaction.objects.create(
            organization=self.org, funding_account=self.in_account,
            transaction_date=self.today + timedelta(days=1), direction='in',
            gross_cents=50000, external_source_key='IN-FUTURE-FORGED',
            payee={'kind': 'unidentified'}, evidence=[], status='posted', version=2,
            created_by=self.owner)
        with self.assertRaises(ValidationError):
            self.link(in_cash=future_cash)
        locked_cash = self.post(self.in_account, 'in', 50000, 'IN-LOCKED')
        from core.models import Organization
        Organization.objects.filter(pk=self.org.pk).update(
            locked_through=self.today)
        with self.assertRaises(ValidationError):
            self.link(in_cash=locked_cash)

    def test_rejects_already_transferred_cash(self):
        self.link()
        second_in = self.post(self.in_account, 'in', 50000, 'IN-2')
        with self.assertRaises(ValidationError):
            self.link(in_cash=second_in)

    def test_permissions_and_module_disable(self):
        with self.assertRaises(PermissionDenied):
            self.link(member=self.plain)
        self.assertEqual(internal.internal_transfer_list(self.auditor), [])
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        with self.assertRaises(PermissionDenied):
            self.link()
        PluginState.objects.filter(organization=self.org, plugin_id='cash').delete()
        self.link()

    def test_integrity_detects_mismatched_pair(self):
        out_cash = self.post(self.out_account, 'out', 50000, 'OUT-BROKEN')
        in_cash = self.post(self.in_account, 'in', 50000, 'IN-BROKEN')
        InternalTransfer.objects.create(
            organization=self.org, out_cash_transaction=out_cash,
            in_cash_transaction=in_cash, out_cash_ref=out_cash.transaction_ref,
            in_cash_ref=in_cash.transaction_ref, transfer_date=self.today,
            gross_cents=40000, out_transaction_date=self.today,
            in_transaction_date=self.today, status='active', version=1,
            created_by=self.owner)
        result = internal.internal_transfer_integrity(self.owner)
        self.assertFalse(result['ok'])
        self.assertTrue(any('pair_mismatch' in finding for finding in result['findings']))

    def test_verify_detects_forged_pair_mismatch(self):
        out_cash = self.post(self.out_account, 'out', 50000, 'OUT-VERIFY-BAD')
        in_cash = self.post(self.in_account, 'in', 50000, 'IN-VERIFY-BAD')
        InternalTransfer.objects.create(
            organization=self.org, out_cash_transaction=out_cash,
            in_cash_transaction=in_cash, out_cash_ref=out_cash.transaction_ref,
            in_cash_ref=in_cash.transaction_ref, transfer_date=self.today,
            gross_cents=40000, out_transaction_date=self.today,
            in_transaction_date=self.today, status='active', version=1,
            created_by=self.owner)
        errors = self.verify_payload()['errors']
        self.assertTrue(any('internal_transfer:' in error and 'pair_mismatch' in error
                            for error in errors), errors)

    def test_verify_detects_missing_linked_event(self):
        transfer = self.link()
        DomainEvent.objects.filter(topic=LINKED_TOPIC).delete()
        errors = self.verify_payload()['errors']
        self.assertTrue(any('linked_event_count' in error for error in errors), errors)

    def test_snapshot_and_list_projections(self):
        transfer = self.link()
        snapshot = internal.internal_transfer_snapshot(self.owner, transfer.pk)
        self.assertEqual((snapshot['out_account_id'], snapshot['in_account_id']),
                         (str(self.out_account.pk), str(self.in_account.pk)))
        self.assertEqual((snapshot['out_transaction_date'],
                          snapshot['in_transaction_date']),
                         (self.today.isoformat(), self.today.isoformat()))
        self.assertEqual(len(internal.internal_transfer_list(self.owner)), 1)
        self.assertEqual(len(internal.internal_transfer_list(
            self.owner, status='reversed')), 0)
