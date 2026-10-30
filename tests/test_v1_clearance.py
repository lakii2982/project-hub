"""PH-232 clearance: posted cash partially clears active open items, reversible once."""
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

from core.models import (AccountingEvent, AccountingPeriod, AuditEvent, DomainEvent, LedgerEntry,
                         LedgerPosting, Membership, Organization, PluginState, SourceConsumption)
from core.services import audit
from pmo_plugins.cash import funding_services as funding
from pmo_plugins.cash import transaction_services as cash
from pmo_plugins.cash.models import CashReceipt, CashTransaction, FundingAccount
from pmo_plugins.contracts.party_services import create_party
from pmo_plugins.management_revenue.models import RevenueRecognition
from pmo_plugins.settlement import aging_services as aging
from pmo_plugins.settlement import clearance_services as clearance
from pmo_plugins.settlement.models import (Clearance, ClearanceReceipt, OpenItem, Settlement,
                                           SettlementReceipt)
from tests.test_v1_settlement import SettlementFixture

CLEARED_TOPIC = 'settlement.cleared.v1'
REVERSED_TOPIC = 'settlement.clearance_reversed.v1'


class ClearanceFixture(SettlementFixture):
    def setUp(self):
        super().setUp()
        self.account = self.cash_account()
        self.item = self.make_open_item()
        self.cash_row = self.post_cash()

    # ---------- cash setup through named services ----------
    def cash_account(self):
        draft = funding.save_funding_account(self.owner, name='核销基本户',
            request_token=uuid.uuid4(), dry_run=False)
        return funding.activate_funding_account(self.owner, draft.pk,
            expected_version=draft.version, request_token=uuid.uuid4(), dry_run=False)

    def post_cash(self, *, gross_cents=53000, direction='in', payee=None, identify=False,
                  reverse_identify=False, transaction_date=None, member=None):
        values = {'funding_account_id': self.account.pk,
                  'transaction_date': transaction_date or self.today,
                  'direction': direction, 'gross_cents': gross_cents,
                  'external_source_key': 'CLR-' + uuid.uuid4().hex[:8],
                  'payee': payee or {'kind': 'counterparty', 'id': str(self.party.pk)},
                  'evidence': [{'reference': '银行回单'}], 'request_token': uuid.uuid4(),
                  'dry_run': False}
        row = cash.save_cash_transaction(member or self.owner, **values)
        if identify:
            match = cash.match_party(self.owner, row.pk, action='identify',
                identified_payee={'kind': 'counterparty', 'id': str(self.party.pk)},
                evidence=[{'reference': '匹配依据'}], expected_version=row.version,
                request_token=uuid.uuid4(), dry_run=False)
            if reverse_identify:
                cash.match_party(self.owner, row.pk, action='reverse',
                    match_ref=match.match_ref, reason='识别错误',
                    evidence=[{'reference': '撤识别'}], expected_version=row.version,
                    request_token=uuid.uuid4(), dry_run=False)
            row.refresh_from_db()
        if direction == 'in':
            row = cash.record_cash_transaction(self.owner, row.pk,
                expected_version=row.version, request_token=uuid.uuid4(), dry_run=False)
        return row

    def make_open_item(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        settlement = self.settle(self.term_values('payment_term')[:1])
        return settlement.items.get()

    def clear(self, *, open_item=None, cash_row=None, amount=30000, member=None, **changes):
        values = {'open_item_id': (open_item or self.item).pk,
                  'cash_transaction_ref': (cash_row or self.cash_row).transaction_ref,
                  'cleared_cents': amount, 'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return clearance.create_clearance(member or self.owner, **values)

    def reverse(self, row, *, member=None, **changes):
        values = {'reason': '核销依据错误，撤核重做', 'request_token': uuid.uuid4(),
                  'expected_version': row.version, 'dry_run': False}
        values.update(changes)
        return clearance.reverse_clearance(member or self.owner, row.pk, **values)

    def balances(self, member=None):
        return clearance.open_item_balances(member or self.owner, self.item.pk)

    def facts(self):
        return tuple(model.objects.count() for model in (
            Clearance, ClearanceReceipt, DomainEvent, AuditEvent, AccountingEvent,
            LedgerPosting, LedgerEntry, Settlement, OpenItem, SourceConsumption,
            CashTransaction, CashReceipt, RevenueRecognition))


class ClearanceLifecycleTests(ClearanceFixture, TestCase):
    def test_golden_partial_then_full_then_reverse_restores_balances(self):
        first = self.clear(amount=30000)
        self.assertEqual((first.status, first.version, first.cleared_cents),
                         ('active', 1, 30000))
        self.assertEqual(first.clearance_ref['kind'], 'clearance')
        balances = self.balances()
        self.assertEqual((balances['gross_cents'], balances['cleared_cents'],
                          balances['remaining_cents']), (53000, 30000, 23000))
        candidates = clearance.cash_candidates_for_open_item(self.owner, self.item.pk)
        self.assertEqual(candidates[0]['cleared_cents'], 30000)
        self.assertEqual(candidates[0]['available_cents'], 23000)
        data = aging.open_item_aging(self.owner)
        item = next(row for row in data['items'] if row['id'] == str(self.item.pk))
        self.assertEqual((item['cleared_cents'], item['remaining_cents']), (30000, 23000))
        self.assertEqual(data['totals']['remaining_cents'], 23000)
        second = self.clear(amount=23000)
        self.assertEqual(self.balances()['remaining_cents'], 0)
        self.assertEqual(clearance.cash_candidates_for_open_item(
            self.owner, self.item.pk)[0]['available_cents'], 0)
        self.assertEqual(aging.open_item_aging(self.owner)['totals']['remaining_cents'], 0)
        self.reverse(first)
        self.assertEqual(self.balances()['remaining_cents'], 30000)
        self.reverse(second)
        self.assertEqual(self.balances()['remaining_cents'], 53000)

    def test_create_writes_audit_receipt_and_one_canonical_event(self):
        before = self.facts()
        row = self.clear(amount=30000)
        after = self.facts()
        self.assertEqual(after[0], before[0] + 1)
        self.assertEqual(after[1], before[1] + 1)
        self.assertEqual(after[2], before[2] + 2)  # generic outbox + canonical
        self.assertEqual(after[3], before[3] + 2)  # generic + suppressed source audits
        self.assertEqual(before[4:], after[4:])
        canonical = DomainEvent.objects.filter(topic=CLEARED_TOPIC)
        self.assertEqual(canonical.count(), 1)
        event = canonical.get()
        self.assertEqual(event.data['source'], row.clearance_ref)
        self.assertEqual(event.data['payload'],
                         {'changed_refs': [row.clearance_ref], 'posting_event_refs': []})
        self.assertEqual(event.data['schema_version'], 1)
        self.assertEqual(event.data['organization_id'], str(self.org.pk))
        self.assertTrue(AuditEvent.objects.filter(action='settlement.clearance.created',
                                                  entity='Clearance',
                                                  entity_id=str(row.pk)).exists())
        receipt = ClearanceReceipt.objects.get(action='clearance.create')
        self.assertEqual(receipt.result, row.clearance_ref)
        self.assertFalse(AuditEvent.objects.filter(
            action='settlement.clearance.reversed').exists())

    def test_reverse_writes_one_canonical_event_and_keeps_amounts(self):
        row = self.clear(amount=30000)
        before = self.facts()
        reversed_row = self.reverse(row)
        after = self.facts()
        self.assertEqual((reversed_row.status, reversed_row.version), ('reversed', 1))
        self.assertEqual((reversed_row.cleared_cents, reversed_row.cash_transaction_id),
                         (30000, self.cash_row.pk))
        self.assertEqual(after[0], before[0])
        self.assertEqual(after[1], before[1] + 1)
        self.assertEqual(after[2], before[2] + 2)
        self.assertEqual(after[3], before[3] + 2)
        self.assertEqual(before[4:], after[4:])
        canonical = DomainEvent.objects.filter(topic=REVERSED_TOPIC)
        self.assertEqual(canonical.count(), 1)
        event = canonical.get()
        self.assertEqual(event.data['source'], reversed_row.clearance_ref)
        self.assertEqual(event.data['payload']['changed_refs'], [reversed_row.clearance_ref])
        self.assertEqual(reversed_row.clearance_ref['version'], 1)
        self.assertEqual(self.balances()['remaining_cents'], 53000)
        self.denied(lambda: self.reverse(reversed_row))

    def test_limits_and_amount_validation(self):
        for amount in (0, -1, True, False, 1.0, 10**12 + 1, '30000', None):
            self.denied(lambda amount=amount: self.clear(amount=amount))
        self.denied(lambda: self.clear(amount=53001))
        big_cash = self.post_cash(gross_cents=100000)
        self.denied(lambda: self.clear(cash_row=big_cash, amount=53001))
        small_cash = self.post_cash(gross_cents=10000)
        self.denied(lambda: self.clear(cash_row=small_cash, amount=10001))
        self.clear(amount=23000)
        self.denied(lambda: self.clear(amount=30001))
        self.clear(amount=30000)
        self.denied(lambda: self.clear(amount=1))

    def test_cash_must_be_posted_incoming_with_effective_counterparty(self):
        draft = cash.save_cash_transaction(self.owner, funding_account_id=self.account.pk,
            transaction_date=self.today, direction='in', gross_cents=30000,
            external_source_key='DRAFT-CLR', payee={'kind': 'unidentified', 'bank_label': '客户'},
            evidence=[{'reference': '草稿回单'}], request_token=uuid.uuid4(), dry_run=False)
        self.denied(lambda: self.clear(cash_row=draft))
        outgoing = self.post_cash(direction='out')
        self.denied(lambda: self.clear(cash_row=outgoing))
        unidentified = self.post_cash(payee={'kind': 'unidentified', 'bank_label': '未知'})
        self.denied(lambda: self.clear(cash_row=unidentified))
        other = create_party(self.owner, display_name='另一客户', roles=['customer'],
            active_from=self.today, request_token=uuid.uuid4())
        wrong_party = self.post_cash(payee={'kind': 'counterparty', 'id': str(other.pk)})
        self.denied(lambda: self.clear(cash_row=wrong_party))
        stale_ref = dict(self.cash_row.transaction_ref, version=99)
        self.denied(lambda: self.clear(cash_transaction_ref=stale_ref))
        self.denied(lambda: self.clear(cash_transaction_ref=dict(
            self.cash_row.transaction_ref, kind='funding_account')))
        self.denied(lambda: self.clear(open_item_id=uuid.uuid4()))

    def test_identified_match_is_effective_and_reversal_removes_it(self):
        matched = self.post_cash(payee={'kind': 'unidentified', 'bank_label': '客户回款'},
                                 identify=True)
        row = self.clear(cash_row=matched, amount=10000)
        self.assertEqual(row.cleared_cents, 10000)
        reversed_identify = self.post_cash(
            payee={'kind': 'unidentified', 'bank_label': '客户回款'},
            identify=True, reverse_identify=True)
        self.denied(lambda: self.clear(cash_row=reversed_identify, amount=1000))
        self.reverse(row)

    def test_period_locked_closed_and_occurred_on(self):
        self.denied(lambda: self.clear(occurred_on=self.today - timedelta(days=1)))
        self.denied(lambda: self.clear(occurred_on=True))
        self.denied(lambda: self.clear(occurred_on=1.0))
        self.org.locked_through = self.today
        self.org.save()
        self.denied(lambda: self.clear())
        self.org.locked_through = None
        self.org.save()
        AccountingPeriod.objects.update_or_create(organization=self.org,
            period=self.today.strftime('%Y-%m'),
            defaults={'status': 'posted', 'closed_through': self.today})
        self.denied(lambda: self.clear())
        AccountingPeriod.objects.filter(organization=self.org).delete()
        accepted = self.clear(occurred_on=self.today)
        self.assertEqual(accepted.occurred_on, self.today)

    def test_idempotent_replay_and_changed_input_conflict(self):
        token = uuid.uuid4()
        first = self.clear(amount=10000, request_token=token)
        before = self.facts()
        replay = self.clear(amount=10000, request_token=token)
        self.assertEqual((replay.pk, replay.cleared_cents), (first.pk, 10000))
        self.assertEqual(before, self.facts())
        self.denied(lambda: self.clear(amount=20000, request_token=token))
        reverse_token = uuid.uuid4()
        reversed_row = self.reverse(first, request_token=reverse_token)
        before = self.facts()
        again = self.reverse(first, request_token=reverse_token)
        self.assertEqual(again.pk, reversed_row.pk)
        self.assertEqual(before, self.facts())
        self.denied(lambda: self.reverse(first, reason='另一个原因', request_token=reverse_token))

    def test_reverse_rejections_and_dry_run(self):
        row = self.clear(amount=30000)
        before = self.facts()
        preview = clearance.reverse_clearance(self.owner, row.pk, reason='预览撤核',
            request_token=uuid.uuid4(), expected_version=row.version)
        self.assertEqual(preview.status, 'reversed')
        self.assertEqual(before, self.facts())
        self.denied(lambda: self.reverse(row, reason=''))
        self.denied(lambda: self.reverse(row, reason='x' * 1001))
        self.denied(lambda: self.reverse(row, expected_version=row.version + 1))
        for value in (True, 1.0, 0, -1):
            self.denied(lambda value=value: self.reverse(row, expected_version=value))
        self.reverse(row)
        self.denied(lambda: self.reverse(row))

    def test_roles_and_read_scope(self):
        row = self.clear(amount=10000)
        for actor in (self.pm, self.plain, self.auditor):
            self.denied(lambda actor=actor: self.clear(member=actor))
            self.denied(lambda actor=actor: self.reverse(row, member=actor))
        for actor in (self.owner, self.finance, self.auditor):
            self.assertEqual(self.balances(actor)['remaining_cents'], 43000)
            self.assertTrue(clearance.clearance_list(actor))
            self.assertTrue(clearance.clearance_snapshot(actor, row.pk)['id'])
        self.assertTrue(clearance.open_item_balances(self.pm, self.item.pk)['open_item_id'])
        self.assertEqual(len(clearance.clearance_list(self.pm)), 1)
        self.denied(lambda: clearance.open_item_balances(self.plain, self.item.pk))
        self.denied(lambda: clearance.clearance_list(self.plain))
        other_pm = self.member('pm')
        self.denied(lambda: clearance.open_item_balances(other_pm, self.item.pk))
        self.assertEqual(clearance.clearance_list(other_pm), [])
        self.denied(lambda: clearance.clearance_list(other_pm, project_id=self.project.pk))
        self.assertEqual(clearance.cash_candidates_for_open_item(self.pm, self.item.pk), [])
        self.assertTrue(clearance.cash_candidates_for_open_item(self.auditor, self.item.pk))
        self.assertTrue(clearance.cash_candidates_for_open_item(self.finance, self.item.pk))

    def test_module_disable_revocation_and_dry_run(self):
        before = self.facts()
        preview = clearance.create_clearance(self.owner, open_item_id=self.item.pk,
            cash_transaction_ref=self.cash_row.transaction_ref, cleared_cents=30000,
            request_token=uuid.uuid4())
        self.assertEqual(preview.status, 'active')
        self.assertEqual(before, self.facts())
        Membership.objects.filter(pk=self.owner.pk).update(active=False)
        self.denied(lambda: self.clear())
        Membership.objects.filter(pk=self.owner.pk).update(active=True)
        PluginState.objects.update_or_create(organization=self.org, plugin_id='settlement',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.denied(lambda: self.clear())
        PluginState.objects.filter(organization=self.org, plugin_id='settlement').delete()
        row = self.clear(amount=10000)
        PluginState.objects.update_or_create(organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.denied(lambda: self.clear())
        self.denied(lambda: self.reverse(row))
        self.assertTrue(clearance.open_item_balances(self.owner, self.item.pk)['open_item_id'])
        self.assertTrue(clearance.clearance_list(self.owner))
        self.denied(lambda: clearance.cash_candidates_for_open_item(self.owner, self.item.pk))

    def test_no_ledger_income_or_open_item_mutation(self):
        item_fields = (self.item.gross_cents, self.item.status, self.item.version,
                       self.item.partition_key)
        settlement_count = Settlement.objects.count()
        ledger = (AccountingEvent.objects.count(), LedgerPosting.objects.count(),
                  LedgerEntry.objects.count(), SourceConsumption.objects.count(),
                  RevenueRecognition.objects.count())
        row = self.clear(amount=30000)
        self.reverse(row)
        self.assertEqual((self.item.gross_cents, self.item.status, self.item.version,
                          self.item.partition_key), item_fields)
        self.assertEqual(Settlement.objects.count(), settlement_count)
        self.assertEqual(ledger, (AccountingEvent.objects.count(), LedgerPosting.objects.count(),
                                  LedgerEntry.objects.count(), SourceConsumption.objects.count(),
                                  RevenueRecognition.objects.count()))
        self.assertFalse(DomainEvent.objects.filter(topic='cash.transaction_recorded.v1',
                                                    data__source__kind='clearance').exists())

    def test_aging_and_list_filters(self):
        self.clear(amount=30000)
        data = aging.open_item_aging(self.owner)
        item = next(row for row in data['items'] if row['id'] == str(self.item.pk))
        self.assertEqual(item['remaining_cents'], 23000)
        self.assertEqual(item['remaining_display'], '230.00')
        self.assertEqual(data['totals']['remaining_cents'], 23000)
        self.assertEqual(len(aging.aging_export_rows(self.owner)), 1)
        rows = clearance.clearance_list(self.owner, project_id=self.project.pk)
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(clearance.clearance_list(self.owner,
                                                     open_item_id=self.item.pk)), 1)
        self.assertEqual(aging.open_item_aging(self.auditor)['items'][0]['cleared_cents'], 30000)

    def test_immutability_guards(self):
        row = self.clear(amount=30000)
        row.cleared_cents = 1
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            row.delete()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Clearance.objects.filter(pk=row.pk).update(cleared_cents=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Clearance.objects.filter(pk=row.pk).update(status='active')
        reversed_row = self.reverse(row)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Clearance.objects.filter(pk=reversed_row.pk).update(status='active')
        receipt = ClearanceReceipt.objects.first()
        receipt.action = 'changed'
        with self.assertRaises(ValidationError):
            receipt.save()
        with self.assertRaises(ValidationError):
            receipt.delete()
        with self.assertRaises(ValidationError):
            Clearance.objects.count() and Clearance.objects.first().delete()


class ClearanceVerifyIntegrityTests(ClearanceFixture, TestCase):
    def verify(self):
        out = StringIO()
        call_command('verify_integrity', '--json', stdout=out)
        return json.loads(out.getvalue())

    def assert_verify_error(self, *needles):
        out = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_integrity', '--json', stdout=out)
        payload = json.loads(out.getvalue())
        self.assertFalse(payload['ok'])
        for needle in needles:
            self.assertTrue(any(needle in error for error in payload['errors']),
                            payload['errors'])

    def test_created_and_reversed_clearance_pass_integrity(self):
        self.clear(amount=30000)
        payload = self.verify()
        self.assertTrue(payload['ok'], payload['errors'])
        second = self.clear(amount=20000)
        self.assertTrue(self.verify()['ok'])
        self.reverse(second)
        payload = self.verify()
        self.assertTrue(payload['ok'], payload['errors'])

    def test_deleted_or_tampered_cleared_event_fails_integrity(self):
        self.clear(amount=30000)
        event = DomainEvent.objects.get(topic=CLEARED_TOPIC)
        original = dict(event.data)
        event.data = {**original, 'payload': {'changed_refs': [], 'posting_event_refs': []}}
        event.save(update_fields=['data'])
        self.assert_verify_error('cleared_envelope')
        DomainEvent.objects.filter(pk=event.pk).update(data=original)
        self.assertTrue(self.verify()['ok'])
        DomainEvent.objects.filter(pk=event.pk).delete()
        self.assert_verify_error('cleared_event_count')

    def test_deleted_reversed_event_fails_integrity(self):
        reversed_row = self.reverse(self.clear(amount=30000))
        event = DomainEvent.objects.get(topic=REVERSED_TOPIC)
        DomainEvent.objects.filter(pk=event.pk).delete()
        self.assert_verify_error('reversed_event_count')
        source = audit(self.owner, 'plugin.test.clearance_reversed_restore', reversed_row, {})
        ref = reversed_row.clearance_ref
        DomainEvent.objects.create(
            organization=self.org, source=source, topic=REVERSED_TOPIC,
            data={'event_id': str(uuid.uuid4()), 'topic': REVERSED_TOPIC, 'schema_version': 1,
                  'organization_id': str(self.org.pk), 'source': ref,
                  'occurred_at': timezone.now().isoformat(),
                  'payload': {'changed_refs': [ref], 'posting_event_refs': []}})
        self.assertTrue(self.verify()['ok'])

    def test_active_clearance_with_reversed_event_fails_integrity(self):
        row = self.clear(amount=30000)
        source = audit(self.owner, 'plugin.test.bogus_clearance_reversed', row, {})
        ref = row.clearance_ref
        DomainEvent.objects.create(
            organization=self.org, source=source, topic=REVERSED_TOPIC,
            data={'event_id': str(uuid.uuid4()), 'topic': REVERSED_TOPIC, 'schema_version': 1,
                  'organization_id': str(self.org.pk), 'source': ref,
                  'occurred_at': timezone.now().isoformat(),
                  'payload': {'changed_refs': [ref], 'posting_event_refs': []}})
        self.assert_verify_error('active_has_reversed_event')

    def test_over_cleared_balances_fail_integrity(self):
        self.clear(amount=30000)
        Clearance.objects.create(organization=self.org, open_item=self.item,
            cash_transaction_id=self.cash_row.pk,
            cash_transaction_ref=self.cash_row.transaction_ref, cleared_cents=30000,
            occurred_on=self.today, status='active', version=1, created_by=self.owner)
        # Both aggregate balances must be guarded: 60000 cleared against 53000 on each side.
        self.assert_verify_error(f'open_item:{self.item.pk}:over_cleared',
                                 f'cash_transaction:{self.cash_row.pk}:over_cleared')

    def test_missing_cash_reference_fails_integrity(self):
        Clearance.objects.create(organization=self.org, open_item=self.item,
            cash_transaction_id=uuid.uuid4(),
            cash_transaction_ref={'namespace': 'cash', 'kind': 'cash_transaction',
                                  'id': str(uuid.uuid4()), 'version': 1},
            cleared_cents=1000, occurred_on=self.today, status='active', version=1,
            created_by=self.owner)
        self.assert_verify_error(':cash')

    def test_tampered_reversed_event_envelope_fails_integrity(self):
        self.reverse(self.clear(amount=30000))
        event = DomainEvent.objects.get(topic=REVERSED_TOPIC)
        data = dict(event.data)
        data['source'] = {**data['source'], 'version': 2}
        event.data = data
        event.save(update_fields=['data'])
        self.assert_verify_error('reversed_envelope')

    def test_reversed_clearance_still_requires_its_cleared_event(self):
        # The cleared event is append-only: reversal adds a second event, it never removes the
        # first one, so a reversed clearance missing its cleared event is corrupt too.
        self.reverse(self.clear(amount=30000))
        DomainEvent.objects.filter(topic=CLEARED_TOPIC).delete()
        self.assert_verify_error('cleared_event_count')

    def test_clearance_accounting_event_fails_integrity(self):
        claim = SourceConsumption.objects.filter(organization=self.org).first()
        self.assertIsNotNone(claim)
        AccountingEvent.objects.create(organization=self.org,
            source={'namespace': 'settlement', 'kind': 'clearance',
                    'id': str(uuid.uuid4()), 'version': 1},
            event_type='settlement.cleared', occurred_on=self.today,
            posting_period=self.today.strftime('%Y-%m'), algorithm_version='v1',
            claim=claim, claim_effect_cents=0)
        self.assert_verify_error('clearance:unexpected_accounting_event')

    def test_clearance_receipt_is_append_only_and_integrity_ok(self):
        # Documented gap for root: verify_integrity has no clearance-receipt rule yet, and the
        # append-only trigger already prevents deleting the receipt.
        self.clear(amount=30000)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ClearanceReceipt.objects.all().delete()
        self.assertTrue(self.verify()['ok'])
