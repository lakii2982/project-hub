"""PH-240 refund and bank return: real reverse cash facts within derived coverage."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, AuditEvent, DomainEvent, LedgerEntry,
                         LedgerPosting, Membership, Organization, PluginState, SourceConsumption)
from pmo_plugins.cash import funding_services as funding
from pmo_plugins.cash import refund_services as refunds
from pmo_plugins.cash import transaction_services as cash
from pmo_plugins.cash.models import (BankReturn, CashPartyMatch, CashReceipt, CashTransaction,
                                     FundingAccount, Refund)
from pmo_plugins.settlement.models import Clearance, ClearanceReceipt, OpenItem, Settlement
from tests.test_v1_clearance import ClearanceFixture

REFUND_TOPIC = 'cash.refunded.v1'
RETURN_TOPIC = 'cash.returned.v1'
RECORDED_TOPIC = 'cash.transaction_recorded.v1'


class RefundFixture(ClearanceFixture):
    def posted(self, *, direction='in', gross_cents=10000, member=None, **changes):
        values = {'funding_account_id': self.account.pk, 'transaction_date': self.today,
                  'direction': direction, 'gross_cents': gross_cents,
                  'external_source_key': 'BANK-' + uuid.uuid4().hex[:8],
                  'payee': {'kind': 'unidentified', 'bank_label': '测试流水'},
                  'evidence': [{'reference': '银行回单'}],
                  'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        row = cash.save_cash_transaction(member or self.owner, **values)
        return cash.record_cash_transaction(
            member or self.owner, row.pk, expected_version=row.version,
            request_token=uuid.uuid4(), dry_run=False)

    def refund(self, *, original=None, amount=10000, member=None, **changes):
        values = {'original_cash_transaction_id': (original or self.cash_row).pk,
                  'refund_date': self.today, 'gross_cents': amount,
                  'reason': '客户取消订单退款', 'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return refunds.record_refund(member or self.owner, **values)

    def bank_return(self, *, original=None, amount=10000, member=None, **changes):
        values = {'original_cash_transaction_id': (original or self.cash_row).pk,
                  'return_date': self.today, 'gross_cents': amount,
                  'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return refunds.record_bank_return(member or self.owner, **values)

    def coverage(self, original=None):
        return refunds.cash_refund_coverage(self.owner, (original or self.cash_row).pk)

    def facts(self):
        return tuple(model.objects.count() for model in (
            FundingAccount, CashTransaction, CashPartyMatch, CashReceipt, Refund, BankReturn,
            DomainEvent, AuditEvent, AccountingEvent, LedgerPosting, LedgerEntry, Settlement,
            OpenItem, SourceConsumption, Clearance, ClearanceReceipt))


class RefundLifecycleTests(RefundFixture, TestCase):
    def test_refund_within_clearance_coverage_creates_reverse_posted_cash(self):
        self.clear(amount=30000)
        coverage = self.coverage()
        self.assertEqual((coverage['posted_gross_cents'], coverage['cleared_cents'],
                          coverage['refunded_cents'], coverage['returned_cents'],
                          coverage['refundable_cents']), (53000, 30000, 0, 0, 23000))
        before = self.facts()
        refund = self.refund(amount=23000)
        after = self.facts()
        self.assertEqual((refund.gross_cents, refund.reason, refund.status),
                         (23000, '客户取消订单退款', 'active'))
        new_cash = refund.refund_cash_transaction
        self.assertEqual((new_cash.status, new_cash.version, new_cash.direction,
                          new_cash.gross_cents, new_cash.funding_account_id),
                         ('posted', 2, 'out', 23000, self.cash_row.funding_account_id))
        self.assertNotEqual(new_cash.external_source_key, self.cash_row.external_source_key)
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1], before[1] + 1)  # one new reverse cash fact
        self.assertEqual(after[3], before[3] + 2)  # refund receipt + posting receipt
        self.assertEqual(after[4], before[4] + 1)  # Refund fact
        self.assertEqual(after[5], before[5])      # no bank return
        self.assertEqual(after[6], before[6] + 4)  # new cash + refund generic/canonical pairs
        self.assertEqual(after[7], before[7] + 4)  # 2 audits per actions
        self.assertEqual(before[8:11], after[8:11])  # no accounting/ledger rows
        self.assertEqual(after[13], before[13])      # no source consumption
        canonical = DomainEvent.objects.get(topic=REFUND_TOPIC)
        self.assertEqual(canonical.data['source'], refund.refund_ref)
        self.assertEqual(canonical.data['payload'],
                         {'changed_refs': [refund.refund_ref, new_cash.transaction_ref],
                          'posting_event_refs': []})
        self.assertEqual(DomainEvent.objects.filter(
            topic=RECORDED_TOPIC, data__source__id=str(new_cash.pk)).count(), 1)
        self.assertTrue(CashReceipt.objects.filter(
            action='refund', result__id=str(refund.pk)).exists())
        self.assertTrue(CashReceipt.objects.filter(
            action='cash_transaction.post', result__id=str(new_cash.pk)).exists())
        coverage = self.coverage()
        self.assertEqual((coverage['refunded_cents'], coverage['refundable_cents']),
                         (23000, 0))
        self.assertEqual(coverage['cleared_cents'], 30000)
        self.denied(lambda: self.refund(amount=1))

    def test_refund_then_clearance_is_capped_by_remaining(self):
        refund = self.refund(amount=30000)
        new_cash = refund.refund_cash_transaction
        coverage = self.coverage()
        self.assertEqual((coverage['refunded_cents'], coverage['refundable_cents']),
                         (30000, 23000))
        self.denied(lambda: self.clear(amount=30000))
        clearance = self.clear(amount=23000)
        self.assertEqual(clearance.cleared_cents, 23000)
        coverage = self.coverage()
        self.assertEqual(coverage['cleared_cents'] + coverage['refunded_cents'],
                         coverage['posted_gross_cents'])
        self.assertEqual(coverage['refundable_cents'], 0)
        candidates = __import__(
            'pmo_plugins.settlement.clearance_services', fromlist=['x']
        ).cash_candidates_for_open_item(self.owner, self.item.pk)
        candidate = next(row for row in candidates if row['id'] == str(self.cash_row.pk))
        self.assertEqual((candidate['cleared_cents'], candidate['refunded_cents'],
                          candidate['available_cents']), (23000, 30000, 0))
        self.assertNotEqual(new_cash.pk, self.cash_row.pk)

    def test_bank_return_reverses_direction_both_ways(self):
        incoming = self.cash_row
        returned = self.bank_return(original=incoming, amount=10000)
        self.assertEqual((returned.return_cash_transaction.direction,
                          returned.return_cash_transaction.status), ('out', 'posted'))
        coverage = self.coverage(incoming)
        self.assertEqual((coverage['returned_cents'], coverage['refundable_cents']),
                         (10000, 43000))
        second = self.bank_return(original=returned.return_cash_transaction, amount=10000)
        self.assertEqual(second.return_cash_transaction.direction, 'in')
        canonical = DomainEvent.objects.get(
            topic=RETURN_TOPIC, data__source__id=str(returned.pk))
        self.assertEqual(canonical.data['source'], returned.bank_return_ref)
        self.assertEqual(canonical.data['payload']['changed_refs'],
                         [returned.bank_return_ref,
                          returned.return_cash_transaction.transaction_ref])

    def test_idempotent_replay_and_changed_input_conflict(self):
        row = self.posted(direction='out', gross_cents=20000)
        token = uuid.uuid4()
        first = self.bank_return(original=row, amount=5000, request_token=token)
        before = self.facts()
        replay = self.bank_return(original=row, amount=5000, request_token=token)
        self.assertEqual((replay.pk, replay.gross_cents), (first.pk, 5000))
        self.assertEqual(before, self.facts())
        self.denied(lambda: self.bank_return(original=row, amount=6000,
                                             request_token=token))
        refund_token = uuid.uuid4()
        refund = self.refund(amount=1000, request_token=refund_token)
        before = self.facts()
        replay = self.refund(amount=1000, request_token=refund_token)
        self.assertEqual(replay.pk, refund.pk)
        self.assertEqual(before, self.facts())
        self.denied(lambda: self.refund(amount=2000, request_token=refund_token))

    def test_dry_run_has_no_side_effects(self):
        before = self.facts()
        preview = self.refund(amount=10000, dry_run=True)
        self.assertEqual((preview.gross_cents, preview.status), (10000, 'active'))
        self.assertEqual(preview.refund_cash_transaction.direction, 'out')
        self.assertEqual(before, self.facts())
        preview = self.bank_return(amount=10000, dry_run=True)
        self.assertEqual(preview.return_cash_transaction.direction, 'out')
        self.assertEqual(before, self.facts())

    def test_rejections_have_no_half_write(self):
        draft = cash.save_cash_transaction(
            self.owner, funding_account_id=self.account.pk, transaction_date=self.today,
            direction='in', gross_cents=10000, external_source_key='DRAFT-1',
            payee={'kind': 'unidentified'}, evidence=[{'reference': '草稿'}],
            request_token=uuid.uuid4(), dry_run=False)
        self.denied(lambda: self.refund(original=draft))
        out_row = self.posted(direction='out', gross_cents=10000)
        self.denied(lambda: self.refund(original=out_row))
        self.denied(lambda: self.refund(amount=53001))
        self.denied(lambda: self.refund(amount=0))
        self.denied(lambda: self.refund(amount=-1))
        for value in (True, 1.0, '100', 10 ** 12 + 1):
            self.denied(lambda value=value: self.refund(amount=value))
        self.denied(lambda: self.refund(reason=''))
        self.denied(lambda: self.refund(reason='x' * 501))
        self.denied(lambda: self.refund(refund_date=self.today + timedelta(days=1)))
        for value in (0, 1, 1.0, None, 'false'):
            self.denied(lambda value=value: self.refund(amount=1000, dry_run=value))
        for value in (True, 1.0, '100'):
            self.denied(lambda value=value: self.bank_return(amount=value))
        row = self.posted(direction='out', gross_cents=5000)
        self.denied(lambda: self.bank_return(original=row, amount=5001))
        self.denied(lambda: self.bank_return(original=draft))

    def test_period_lock_close_and_cross_org(self):
        self.org.locked_through = self.today
        self.org.save()
        self.denied(lambda: self.refund(amount=1000))
        self.denied(lambda: self.bank_return(amount=1000))
        self.org.locked_through = None
        self.org.save()
        AccountingPeriod.objects.update_or_create(
            organization=self.org, period=self.today.strftime('%Y-%m'),
            defaults={'status': 'posted', 'closed_through': self.today})
        self.denied(lambda: self.refund(amount=1000))
        self.denied(lambda: self.bank_return(amount=1000))
        AccountingPeriod.objects.filter(organization=self.org).delete()
        other = Organization.objects.create(name='其他公司')
        other_user = self.member('owner')
        other_member = Membership.objects.create(
            organization=other, user=other_user.user, role='owner')
        account = funding.save_funding_account(
            other_member, name='其他户', request_token=uuid.uuid4(), dry_run=False)
        account = funding.activate_funding_account(
            other_member, account.pk, expected_version=account.version,
            request_token=uuid.uuid4(), dry_run=False)
        foreign = cash.save_cash_transaction(
            other_member, funding_account_id=account.pk, transaction_date=self.today,
            direction='in', gross_cents=1000, external_source_key='OTHER-1',
            payee={'kind': 'unidentified'}, evidence=[{'reference': '外部'}],
            request_token=uuid.uuid4(), dry_run=False)
        foreign = cash.record_cash_transaction(
            other_member, foreign.pk, expected_version=foreign.version,
            request_token=uuid.uuid4(), dry_run=False)
        self.denied(lambda: self.refund(original=foreign))

    def test_roles_module_disable_and_reads(self):
        readable_row = self.posted(direction='in', gross_cents=10000)
        self.refund(original=readable_row, amount=1000)
        out_row = self.posted(direction='out', gross_cents=1000)
        self.bank_return(original=out_row, amount=500)
        row = self.posted(direction='in', gross_cents=10000)
        for actor in (self.auditor, self.pm, self.plain):
            self.denied(lambda actor=actor: self.refund(original=row, member=actor))
            self.denied(lambda actor=actor: self.bank_return(original=row, member=actor))
        for actor in (self.owner, self.finance, self.auditor):
            self.assertEqual(refunds.cash_refund_coverage(actor, readable_row.pk)
                             ['posted_gross_cents'], 10000)
            self.assertTrue(refunds.refund_list(actor))
            self.assertTrue(refunds.bank_return_list(actor))
        for actor in (self.pm, self.plain):
            self.denied(lambda actor=actor: refunds.cash_refund_coverage(actor,
                                                                         readable_row.pk))
            self.denied(lambda actor=actor: refunds.refund_list(actor))
            self.denied(lambda actor=actor: refunds.bank_return_list(actor))
        finance_row = self.posted(direction='in', gross_cents=10000)
        self.assertEqual(self.refund(original=finance_row, amount=1000,
                                     member=self.finance).status, 'active')
        PluginState.objects.update_or_create(organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.denied(lambda: self.refund(amount=1000))
        self.assertTrue(refunds.refund_list(self.auditor))
        self.assertEqual(refunds.cash_refund_coverage(
            self.auditor, finance_row.pk)['posted_gross_cents'], 10000)

    def test_append_only_database_guards(self):
        refund = self.refund(amount=1000)
        bank_return = self.bank_return(original=self.posted(direction='out', gross_cents=1000),
                                       amount=1000)
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                Refund.objects.filter(pk=refund.pk).delete()
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                BankReturn.objects.filter(pk=bank_return.pk).delete()
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                Refund.objects.filter(pk=refund.pk).update(gross_cents=1)
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                BankReturn.objects.filter(pk=bank_return.pk).update(gross_cents=1)
        with self.assertRaises(ValidationError):
            refund.reason = '改原因'
            refund.save()
        with self.assertRaises(ValidationError):
            bank_return.gross_cents = 1
            bank_return.save()
        # The produced posted cash is a normal immutable posted fact.
        produced = refund.refund_cash_transaction
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                CashTransaction.objects.filter(pk=produced.pk).update(gross_cents=1)


class RefundVerifyIntegrityTests(RefundFixture, TestCase):
    def _payload(self):
        from io import StringIO
        import json
        from django.core.management import call_command
        from django.core.management.base import CommandError
        out = StringIO()
        try:
            call_command('verify_integrity', '--json', stdout=out)
        except CommandError:
            pass
        return json.loads(out.getvalue())

    def _forge_classification_bridge(self, cash):
        """Simulate the PH-243/PH-239 legal bridge rows: classification-source event/posting."""
        claim = SourceConsumption.objects.create(
            organization=self.org, consumer='cash', source_id=cash.pk,
            coverage_key='bank_effect', source=cash.transaction_ref,
            amount_cents=cash.gross_cents,
            event_ref={'namespace': 'core', 'kind': 'accounting_event',
                       'id': str(uuid.uuid4()), 'version': 1})
        event = AccountingEvent.objects.create(
            id=uuid.UUID(claim.event_ref['id']), organization=self.org,
            source={'namespace': 'cash', 'kind': 'cash_classification',
                    'id': str(uuid.uuid4()), 'version': 1},
            event_type='cash.classification', occurred_on=cash.transaction_date,
            posting_period=cash.transaction_date.strftime('%Y-%m'),
            algorithm_version='cash_classification_bank_effect_v1',
            claim=claim, claim_effect_cents=cash.gross_cents)
        LedgerPosting.objects.create(
            organization=self.org, accounting_event=event, line_index=0,
            amount_cents=cash.gross_cents, book='cost', scope_kind='project',
            scope_id=str(self.project.pk), origin='current')
        return event

    def test_classification_style_cash_bridge_is_allowed_by_refund_guard(self):
        # PH-243 freezes the legal classification bridge; even when the classified cash was
        # produced by a refund, the refund guard must not reject its AccountingEvent/posting.
        incoming = self.posted(direction='in', gross_cents=1000)
        refund = self.refund(amount=1000)
        self._forge_classification_bridge(incoming)
        self._forge_classification_bridge(refund.refund_cash_transaction)
        errors = self._payload()['errors']
        self.assertFalse([error for error in errors if 'unexpected_refund' in error], errors)

    def test_refund_provenance_ledger_rows_are_rejected(self):
        refund = self.refund(amount=1000)
        claim = SourceConsumption.objects.create(
            organization=self.org, consumer='cash',
            source_id=refund.refund_cash_transaction_id, coverage_key='refund_ledger',
            source=refund.refund_cash_transaction.transaction_ref, amount_cents=1000,
            event_ref={'namespace': 'core', 'kind': 'accounting_event',
                       'id': str(uuid.uuid4()), 'version': 1})
        event = AccountingEvent.objects.create(
            id=uuid.UUID(claim.event_ref['id']), organization=self.org,
            source={'namespace': 'cash', 'kind': 'refund', 'id': str(refund.pk),
                    'version': 1},
            event_type='cash.refund.bridge', occurred_on=self.today,
            posting_period=self.today.strftime('%Y-%m'), algorithm_version='test',
            claim=claim, claim_effect_cents=1000)
        LedgerPosting.objects.create(
            organization=self.org, accounting_event=event, line_index=0,
            amount_cents=1000, book='cost', scope_kind='project',
            scope_id=str(self.project.pk), origin='current')
        errors = self._payload()['errors']
        self.assertTrue(any('unexpected_refund_accounting_event' in error for error in errors),
                        errors)
        self.assertTrue(any('unexpected_refund_cash_posting' in error for error in errors),
                        errors)

    def verify(self):
        from io import StringIO
        import json
        from django.core.management import call_command
        out = StringIO()
        call_command('verify_integrity', '--json', stdout=out)
        return json.loads(out.getvalue())

    def assert_verify_error(self, *needles):
        from io import StringIO
        import json
        from django.core.management import call_command
        from django.core.management.base import CommandError
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_integrity', '--json', stdout=out)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload['ok'])
        for needle in needles:
            self.assertTrue(any(needle in error for error in payload['errors']),
                            payload['errors'])

    def test_valid_refund_and_return_pass_integrity(self):
        self.clear(amount=30000)
        refund = self.refund(amount=20000)
        self.bank_return(original=refund.refund_cash_transaction, amount=5000)
        self.assertTrue(self.verify()['ok'])
        coverage = self.coverage()
        self.assertGreaterEqual(coverage['refundable_cents'], 0)

    def test_deleted_or_tampered_events_fail_integrity(self):
        refund = self.refund(amount=1000)
        event = DomainEvent.objects.get(topic=REFUND_TOPIC)
        original = dict(event.data)
        event.data = {**original, 'payload': {'changed_refs': [], 'posting_event_refs': []}}
        event.save(update_fields=['data'])
        self.assert_verify_error('event_envelope')
        DomainEvent.objects.filter(pk=event.pk).update(data=original)
        self.assertTrue(self.verify()['ok'])
        DomainEvent.objects.filter(pk=event.pk).delete()
        self.assert_verify_error('event_count')

    def test_negative_coverage_fails_integrity(self):
        self.refund(amount=30000)
        forged_cash = self.posted(direction='out', gross_cents=30000)
        Refund.objects.create(
            organization=self.org, original_cash_transaction=self.cash_row,
            refund_cash_transaction=forged_cash,
            original_cash_ref=self.cash_row.transaction_ref, refund_date=self.today,
            gross_cents=30000, reason='伪造超额退款', status='active', version=1,
            created_by=self.owner)
        self.assert_verify_error('refund_coverage_negative')

    def test_wrong_return_direction_fails_integrity(self):
        incoming = self.posted(direction='in', gross_cents=1000)
        wrong = self.posted(direction='in', gross_cents=1000)
        BankReturn.objects.create(
            organization=self.org, original_cash_transaction=incoming,
            return_cash_transaction=wrong, original_cash_ref=incoming.transaction_ref,
            return_date=self.today, gross_cents=1000, status='active', version=1,
            created_by=self.owner)
        self.assert_verify_error('return_cash')
