"""PH-241 advance offset: a customer advance partially offsets one receivable open item.

The slice writes no accounting event, ledger posting, revenue, cost or source consumption;
scheme a makes an advance-classified cash fact unavailable to PH-232 clearance and asserts a
cash fact can never be both cleared and offset.
"""
from io import StringIO
import json
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import (AccountingEvent, DomainEvent, LedgerEntry, LedgerPosting,
                         PluginState, SourceConsumption)
from pmo_plugins.cash import classification_services as classification
from pmo_plugins.cash import refund_services as refunds
from pmo_plugins.cash.models import CashClassification
from pmo_plugins.settlement import aging_services as aging
from pmo_plugins.settlement import clearance_services as clearance
from pmo_plugins.settlement import offset_services as offsets
from pmo_plugins.settlement.models import Offset, SettlementReceipt
from tests.test_v1_clearance import ClearanceFixture


class OffsetFixture(ClearanceFixture):
    def setUp(self):
        super().setUp()
        self.advance = self.make_advance()

    def make_advance(self, *, gross_cents=30000, payee=None, direction='in',
                     transaction_date=None, classification_kind='advance', classify=True,
                     posted=True):
        row = self.post_cash(gross_cents=gross_cents, direction=direction,
                             payee=payee or {'kind': 'counterparty', 'id': str(self.party.pk)},
                             transaction_date=transaction_date)
        if classify:
            classification.record_classification(
                self.owner, cash_transaction_id=row.pk,
                classification=classification_kind,
                evidence=[{'reference': '预收/分类依据'}],
                request_token=uuid.uuid4(), dry_run=False)
        return row

    def offset(self, *, advance=None, open_item=None, amount=20000, member=None, **changes):
        values = {'open_item_id': (open_item or self.item).pk,
                  'advance_ref': (advance or self.advance).transaction_ref,
                  'offset_cents': amount, 'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return offsets.create_offset(member or self.owner, **values)

    def balances(self, member=None):
        return offsets.open_item_offset_snapshot(member or self.owner, self.item.pk)

    def advance_snapshot(self, advance=None, member=None):
        return offsets.advance_offset_snapshot(
            member or self.owner, (advance or self.advance).transaction_ref)

    def facts(self):
        return tuple(model.objects.count() for model in (
            Offset, SettlementReceipt, DomainEvent, AccountingEvent, LedgerPosting,
            LedgerEntry, SourceConsumption, CashClassification))

    def verify_payload(self):
        out = StringIO()
        try:
            call_command('verify_integrity', '--json', stdout=out)
        except CommandError:
            pass
        return json.loads(out.getvalue())

    def verify_ok(self):
        return self.verify_payload()['ok']


class OffsetLifecycleTests(OffsetFixture, TestCase):
    def test_golden_partial_offset_and_balances(self):
        first = self.offset(amount=20000)
        self.assertEqual((first.status, first.version, first.offset_cents),
                         ('active', 1, 20000))
        self.assertEqual(first.offset_ref['kind'], 'offset')
        balances = self.balances()
        self.assertEqual((balances['gross_cents'], balances['cleared_cents'],
                          balances['offset_cents'], balances['remaining_cents']),
                         (53000, 0, 20000, 33000))
        snapshot = self.advance_snapshot()
        self.assertEqual((snapshot['gross_cents'], snapshot['cleared_cents'],
                          snapshot['offset_cents'], snapshot['available_cents']),
                         (30000, 0, 20000, 10000))
        second = self.offset(amount=10000)
        self.assertEqual(self.balances()['remaining_cents'], 23000)
        self.assertEqual(self.advance_snapshot()['available_cents'], 0)
        listed = offsets.offset_list(self.owner, open_item_id=self.item.pk)
        self.assertIn(str(first.pk), {row['id'] for row in listed})
        self.assertTrue(self.verify_ok())

    def test_golden_full_offset_then_reject_one_cent(self):
        self.offset(amount=30000)
        self.assertEqual(self.balances()['remaining_cents'], 23000)
        self.assertEqual(self.advance_snapshot()['available_cents'], 0)
        with self.assertRaises(ValidationError):
            self.offset(amount=1)

    def test_aging_and_clearance_coverage_include_offset(self):
        self.offset(amount=20000)
        data = aging.open_item_aging(self.owner)
        item = next(row for row in data['items'] if row['id'] == str(self.item.pk))
        self.assertEqual((item['offset_cents'], item['remaining_cents']), (20000, 33000))
        coverage = clearance.clearance_coverage_for_cash(
            self.owner, self.advance.transaction_ref)
        self.assertEqual((coverage['offset_cents'], coverage['total_cents'],
                          coverage['offset_implemented']), (20000, 20000, True))
        balances = clearance.open_item_balances(self.owner, self.item.pk)
        self.assertEqual((balances['offset_cents'], balances['remaining_cents']),
                         (20000, 33000))

    def test_idempotent_replay_and_changed_input_conflict(self):
        token = uuid.uuid4()
        first = self.offset(amount=20000, request_token=token)
        replay = self.offset(amount=20000, request_token=token)
        self.assertEqual(first.pk, replay.pk)
        with self.assertRaises(ValidationError):
            self.offset(amount=10000, request_token=token)

    def test_dry_run_has_no_side_effects(self):
        before = self.facts()
        row = self.offset(amount=20000, dry_run=True)
        self.assertTrue(row._state.adding)
        self.assertEqual(before, self.facts())

    def test_rejections_leave_zero_half_write(self):
        before = self.facts()
        with self.assertRaises(ValidationError):
            self.offset(amount=30001)
        with self.assertRaises(ValidationError):
            self.offset(amount=0)
        self.assertEqual(before, self.facts())

    def test_rejects_over_open_item_remaining(self):
        big = self.make_advance(gross_cents=60000)
        with self.assertRaises(ValidationError):
            self.offset(advance=big, amount=53001)
        self.offset(advance=big, amount=53000)
        self.assertEqual(self.balances()['remaining_cents'], 0)

    def test_rejects_cross_counterparty(self):
        other_party = self.make_party(display_name='另一客户')
        other_advance = self.make_advance(payee={'kind': 'counterparty',
                                                 'id': str(other_party.pk)})
        with self.assertRaises(ValidationError):
            self.offset(advance=other_advance, amount=1000)

    def test_rejects_non_advance_classified_cash(self):
        receipt = self.make_advance(classification_kind='receipt')
        with self.assertRaises(ValidationError):
            self.offset(advance=receipt, amount=1000)

    def test_rejects_unposted_advance(self):
        draft = self.make_advance(posted=False, classify=False)
        with self.assertRaises((ValidationError, PermissionDenied)):
            self.offset(advance=draft, amount=1000)

    def test_advance_cash_cannot_be_cleared(self):
        with self.assertRaises(ValidationError):
            clearance.create_clearance(
                self.owner, open_item_id=self.item.pk,
                cash_transaction_ref=self.advance.transaction_ref,
                cleared_cents=1000, request_token=uuid.uuid4(), dry_run=False)
        candidates = clearance.cash_candidates_for_open_item(self.owner, self.item.pk)
        self.assertNotIn(str(self.advance.pk), {row['id'] for row in candidates})

    def test_cleared_cash_cannot_be_classified_advance(self):
        cleared_cash = self.make_advance(gross_cents=20000, classify=False)
        clearance.create_clearance(
            self.owner, open_item_id=self.item.pk,
            cash_transaction_ref=cleared_cash.transaction_ref,
            cleared_cents=5000, request_token=uuid.uuid4(), dry_run=False)
        with self.assertRaises(ValidationError):
            classification.record_classification(
                self.owner, cash_transaction_id=cleared_cash.pk,
                classification='advance', evidence=[{'reference': '分类依据'}],
                request_token=uuid.uuid4(), dry_run=False)

    def test_cleared_advance_cash_cannot_be_offset(self):
        from pmo_plugins.settlement.models import Clearance
        with transaction.atomic():
            Clearance.objects.create(
                organization=self.org, open_item=self.item,
                cash_transaction_id=self.advance.pk,
                cash_transaction_ref=self.advance.transaction_ref, cleared_cents=5000,
                occurred_on=self.today, status='active', version=1, created_by=self.owner)
        with self.assertRaises(ValidationError):
            self.offset(amount=1000)

    def test_refund_consumes_advance_available(self):
        big = self.make_advance(gross_cents=53000)
        refunds.record_refund(
            self.owner, original_cash_transaction_id=big.pk, refund_date=self.today,
            gross_cents=30000, reason='客户退款', request_token=uuid.uuid4(),
            dry_run=False)
        with self.assertRaises(ValidationError):
            self.offset(advance=big, amount=30000)
        self.offset(advance=big, amount=23000)
        self.assertEqual(self.advance_snapshot(big)['available_cents'], 0)

    def test_offset_then_refund_is_capped_by_remaining_coverage(self):
        big = self.make_advance(gross_cents=53000)
        self.offset(advance=big, amount=20000)
        refunds.record_refund(
            self.owner, original_cash_transaction_id=big.pk, refund_date=self.today,
            gross_cents=33000, reason='客户退款', request_token=uuid.uuid4(),
            dry_run=False)
        with self.assertRaises(ValidationError):
            refunds.record_refund(
                self.owner, original_cash_transaction_id=big.pk, refund_date=self.today,
                gross_cents=1, reason='超额退款', request_token=uuid.uuid4(),
                dry_run=False)

    def test_permissions(self):
        with self.assertRaises(PermissionDenied):
            self.offset(member=self.pm, amount=1000)
        with self.assertRaises(PermissionDenied):
            self.offset(member=self.plain, amount=1000)
        self.assertEqual(offsets.offset_list(self.auditor, open_item_id=self.item.pk), [])

    def test_module_disabled_rejects_write(self):
        self.offset(amount=1000)
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        with self.assertRaises(PermissionDenied):
            self.offset(amount=1000)
        self.assertTrue(offsets.offset_list(self.owner))


class OffsetVerifyIntegrityTests(OffsetFixture, TestCase):
    def test_valid_offset_passes_integrity(self):
        self.offset(amount=20000)
        payload = self.verify_payload()
        self.assertTrue(payload['ok'], payload['errors'])

    def test_forged_offset_without_advance_classification_fails(self):
        other = self.post_cash(gross_cents=10000)
        with transaction.atomic():
            Offset.objects.create(
                organization=self.org, project=self.item.project, open_item=self.item,
                advance_cash_transaction_id=other.pk, advance_ref=other.transaction_ref,
                offset_cents=1000, occurred_on=self.today, status='active', version=1,
                created_by=self.owner)
        errors = self.verify_payload()['errors']
        self.assertTrue(any(':missing_advance_classification' in error for error in errors),
                        errors)

    def test_forged_clearance_and_offset_overlap_fails(self):
        from pmo_plugins.settlement.models import Clearance
        with transaction.atomic():
            Clearance.objects.create(
                organization=self.org, open_item=self.item,
                cash_transaction_id=self.advance.pk,
                cash_transaction_ref=self.advance.transaction_ref, cleared_cents=1000,
                occurred_on=self.today, status='active', version=1, created_by=self.owner)
            Offset.objects.create(
                organization=self.org, project=self.item.project, open_item=self.item,
                advance_cash_transaction_id=self.advance.pk,
                advance_ref=self.advance.transaction_ref, offset_cents=20000,
                occurred_on=self.today, status='active', version=1, created_by=self.owner)
        errors = self.verify_payload()['errors']
        self.assertTrue(any(':clearance_offset_overlap' in error for error in errors), errors)
        self.assertTrue(any(':advance_cash_cleared' in error for error in errors), errors)

    def test_forged_counterparty_mismatch_fails(self):
        other_party = self.make_party(display_name='另一客户')
        other_advance = self.make_advance(
            payee={'kind': 'counterparty', 'id': str(other_party.pk)})
        with transaction.atomic():
            Offset.objects.create(
                organization=self.org, project=self.item.project, open_item=self.item,
                advance_cash_transaction_id=other_advance.pk,
                advance_ref=other_advance.transaction_ref, offset_cents=1000,
                occurred_on=self.today, status='active', version=1, created_by=self.owner)
        errors = self.verify_payload()['errors']
        self.assertTrue(any(':counterparty_mismatch' in error for error in errors), errors)

    def test_forged_advance_ref_version_fails(self):
        with transaction.atomic():
            Offset.objects.create(
                organization=self.org, project=self.item.project, open_item=self.item,
                advance_cash_transaction_id=self.advance.pk,
                advance_ref={**self.advance.transaction_ref,
                             'version': self.advance.version + 1},
                offset_cents=1000, occurred_on=self.today, status='active', version=1,
                created_by=self.owner)
        errors = self.verify_payload()['errors']
        self.assertTrue(any(':advance_ref_version' in error for error in errors), errors)

    def test_forged_over_offset_fails(self):
        self.offset(amount=30000)
        with transaction.atomic():
            Offset.objects.create(
                organization=self.org, project=self.item.project, open_item=self.item,
                advance_cash_transaction_id=self.advance.pk,
                advance_ref=self.advance.transaction_ref, offset_cents=1000,
                occurred_on=self.today, status='active', version=1, created_by=self.owner)
        errors = self.verify_payload()['errors']
        self.assertTrue(any(':over_offset' in error for error in errors), errors)
