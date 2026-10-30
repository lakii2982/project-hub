"""PH-244 bank-fee draft facts: draft-only, canonical event, reconciliation, zero financial effect."""
from datetime import timedelta
from decimal import Decimal
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, AuditEvent, DomainEvent, LedgerEntry,
                         LedgerPosting, Membership, Organization, PluginState, SourceConsumption)
from pmo_plugins.cash import transaction_services as cash
from pmo_plugins.cash.models import CashTransaction, FundingAccount
from pmo_plugins.costing import bank_charge_services as fees
from pmo_plugins.costing.models import BankCharge, BankChargeReceipt, CostEvent
from pmo_plugins.settlement.models import OpenItem, Settlement
from tests.test_v1_cash import CashFixture

TOPIC = 'costing.bank_charge_saved.v1'
SAVED_ACTION = 'costing.bank_charge.saved'


class BankChargeFixture(CashFixture):
    def setUp(self):
        super().setUp()
        self.posted = self.posted_cash()

    def posted_cash(self, member=None, **changes):
        values = {'gross_cents': 9800, 'direction': 'in'}
        values.update(changes)
        row = self.tx(member=member, **values)
        return cash.record_cash_transaction(member or self.owner, row.pk,
                                            expected_version=row.version,
                                            request_token=uuid.uuid4(), dry_run=False)

    def fee(self, member=None, **changes):
        values = dict(cash_transaction_ref=self.posted.transaction_ref, gross_cents=200,
                      tax_cents=None, counterparty_id=None, scope_kind='project',
                      scope_id=self.project.pk, evidence='银行手续费回单',
                      request_token=uuid.uuid4(), dry_run=False)
        values.update(changes)
        return fees.save_bank_charge_draft(member or self.owner, **values)

    def facts(self):
        return tuple(model.objects.count() for model in (
            CashTransaction, BankCharge, BankChargeReceipt, AuditEvent, DomainEvent,
            AccountingEvent, LedgerPosting, LedgerEntry, SourceConsumption, Settlement, OpenItem,
            CostEvent))

    def denied(self, call, exceptions=(ValidationError, PermissionDenied)):
        before = self.facts()
        with self.assertRaises(exceptions):
            call()
        self.assertEqual(before, self.facts())
        return True

    def foreign_posted_cash(self):
        org = Organization.objects.create(name='其他公司')
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user(username='fee-foreign-' + uuid.uuid4().hex)
        member = Membership.objects.create(organization=org, user=user, role='owner')
        account = self.save_account(member=member, name='外部基本户')
        account = self.activate(account, member=member)
        return self.posted_cash(member=member, account=account,
                                external_source_key='EXT-' + uuid.uuid4().hex[:8])


class BankChargeServiceTests(BankChargeFixture, TestCase):
    def test_create_draft_writes_one_canonical_event_and_zero_financial_effect(self):
        before = self.facts()
        charge = self.fee()
        after = self.facts()
        self.assertEqual(charge.status, 'draft')
        self.assertEqual(charge.version, 1)
        self.assertEqual((charge.gross_cents, charge.tax_cents), (200, None))
        self.assertEqual((charge.existing_cost_ref, charge.fee_bearer, charge.scope_kind),
                         (None, 'company', 'project'))
        self.assertEqual(str(charge.cash_transaction_id), str(self.posted.pk))
        self.assertEqual(after[1], before[1] + 1)   # one BankCharge
        self.assertEqual(after[2], before[2] + 1)   # one receipt
        self.assertEqual(after[3], before[3] + 2)   # generic + suppressed plugin audit
        self.assertEqual(after[4], before[4] + 2)   # generic outbox + canonical event
        self.assertEqual(before[5:], after[5:])     # no accounting/ledger/consumption/settlement rows
        canonical = DomainEvent.objects.filter(topic=TOPIC)
        self.assertEqual(canonical.count(), 1)
        ref = {'namespace': 'costing', 'kind': 'bank_charge', 'id': str(charge.pk), 'version': 1}
        event = canonical.get()
        self.assertEqual(event.data['source'], ref)
        self.assertEqual(event.data['schema_version'], 1)
        self.assertEqual(event.data['organization_id'], str(self.org.pk))
        self.assertEqual(event.data['payload'], {'changed_refs': [ref], 'posting_event_refs': []})
        self.assertEqual(AuditEvent.objects.filter(action=SAVED_ACTION).count(), 1)
        receipt = BankChargeReceipt.objects.get()
        self.assertEqual(receipt.result_ref, ref)
        self.assertEqual(receipt.action, 'bank_charge.draft.save')
        # Provider-side projection and reconciliation.
        snapshot = fees.get_bank_charge(self.finance, charge.pk)
        self.assertEqual((snapshot['status'], snapshot['version'], snapshot['gross_cents']),
                         ('draft', 1, 200))
        reconciliation = fees.net_fee_reconciliation(self.finance, self.posted.pk)
        self.assertEqual(reconciliation['posted_net_cents'], 9800)
        self.assertEqual(reconciliation['approved_fee_cents'], 0)
        self.assertEqual(reconciliation['implied_gross_cents'], 9800)
        self.assertEqual(reconciliation['draft_fee_cents'], 200)
        self.assertEqual((reconciliation['charge_status'], reconciliation['reconciled']),
                         ('draft', False))
        self.assertEqual(len(fees.list_bank_charges(self.auditor)), 1)

    def test_no_charge_reconciliation_has_zero_approved_fee(self):
        result = fees.net_fee_reconciliation(self.finance, self.posted.pk)
        self.assertEqual((result['draft_fee_cents'], result['charge_status'], result['reconciled']),
                         (0, 'none', False))

    def test_dry_run_and_idempotent_replay_write_nothing(self):
        before = self.facts()
        preview = fees.save_bank_charge_draft(
            self.owner, cash_transaction_ref=self.posted.transaction_ref, gross_cents=200,
            scope_id=self.project.pk, evidence='回单', request_token=uuid.uuid4(), dry_run=True)
        self.assertTrue(preview['dry_run'])
        self.assertIsNone(preview['charge_id'])
        self.assertEqual(before, self.facts())
        token = uuid.uuid4()
        charge = self.fee(request_token=token)
        after = self.facts()
        replay = self.fee(request_token=token)
        self.assertEqual(replay.pk, charge.pk)
        self.assertEqual(after, self.facts())
        dry_replay = fees.save_bank_charge_draft(
            self.owner, cash_transaction_ref=self.posted.transaction_ref, gross_cents=200,
            scope_id=self.project.pk, evidence='银行手续费回单', request_token=token, dry_run=True)
        self.assertTrue(dry_replay['replayed'])
        self.assertEqual(after, self.facts())
        self.denied(lambda: self.fee(request_token=token, gross_cents=201))

    def test_revision_reuses_same_charge_and_appends_second_event(self):
        token = uuid.uuid4()
        charge = self.fee(request_token=token, gross_cents=200)
        revised = self.fee(expected_version=1, gross_cents=150)
        self.assertEqual(revised.pk, charge.pk)
        self.assertEqual(revised.version, 2)
        self.assertEqual(BankCharge.objects.count(), 1)
        self.assertEqual(revised.gross_cents, 150)
        self.assertEqual(DomainEvent.objects.filter(topic=TOPIC).count(), 2)
        latest = DomainEvent.objects.filter(topic=TOPIC).order_by('-created_at', '-id').first()
        self.assertEqual(latest.data['source'],
                         {'namespace': 'costing', 'kind': 'bank_charge', 'id': str(charge.pk),
                          'version': 2})
        self.denied(lambda: self.fee(expected_version=1, gross_cents=300))
        self.denied(lambda: self.fee(gross_cents=300))

    def test_rejections_have_no_half_write(self):
        draft = self.tx(gross_cents=500)
        outgoing = self.posted_cash(direction='out', payee={'kind': 'unidentified'})
        foreign = self.foreign_posted_cash()
        other_org = Organization.objects.create(name='第三公司')
        future = self.today + timedelta(days=1)
        previous_month = (self.today.replace(day=1) - timedelta(days=1))
        # source state / tenant / direction / version / amount
        self.denied(lambda: self.fee(cash_transaction_ref=draft.transaction_ref))
        self.denied(lambda: self.fee(cash_transaction_ref=outgoing.transaction_ref))
        self.denied(lambda: self.fee(cash_transaction_ref=foreign.transaction_ref))
        self.denied(lambda: self.fee(
            cash_transaction_ref={**self.posted.transaction_ref, 'version': 1}))
        self.denied(lambda: self.fee(cash_transaction_ref={**self.posted.transaction_ref,
                                                           'namespace': 'cash', 'kind': 'funding_account'}))
        for value in (0, -1, True, 1.0, '200', 10**12 + 1, 9801):
            self.denied(lambda value=value: self.fee(gross_cents=value))
        for value in (-1, True, 1.0, '10', 10**12 + 1):
            self.denied(lambda value=value: self.fee(tax_cents=value))
        # deferred slices and caller overrides
        self.denied(lambda: self.fee(existing_cost_ref={'namespace': 'costing', 'kind': 'cost_event',
                                                        'id': 'x', 'version': 1}))
        self.denied(lambda: self.fee(fee_bearer='counterparty'))
        self.denied(lambda: self.fee(scope_kind='nonproject'))
        for value in ('approved', 'pending', 'rejected'):
            self.denied(lambda value=value: self.fee(status=value))
        self.denied(lambda: self.fee(scope_id=other_org.pk))
        self.denied(lambda: self.fee(scope_id=999999))
        self.denied(lambda: self.fee(evidence=[]))
        self.denied(lambda: self.fee(evidence='   '))
        # period / lock / closed
        self.denied(lambda: self.fee(occurred_on=future))
        self.denied(lambda: self.fee(occurred_on=previous_month))
        self.denied(lambda: self.fee(posting_period=previous_month.strftime('%Y-%m')))
        self.denied(lambda: self.fee(posting_period='2026-1'))
        self.org.locked_through = self.today
        self.org.save()
        self.denied(lambda: self.fee())
        self.org.locked_through = None
        self.org.save()
        AccountingPeriod.objects.update_or_create(
            organization=self.org, period=self.today.strftime('%Y-%m'),
            defaults={'status': 'posted', 'closed_through': self.today})
        self.denied(lambda: self.fee())
        AccountingPeriod.objects.filter(organization=self.org).delete()

    def test_roles_and_disabled_module_keep_history_readable(self):
        self.denied(lambda: self.fee(member=self.auditor), exceptions=(PermissionDenied,))
        self.denied(lambda: self.fee(member=self.plain), exceptions=(PermissionDenied,))
        self.denied(lambda: fees.list_bank_charges(self.plain), exceptions=(PermissionDenied,))
        charge = self.fee(member=self.finance)
        self.assertEqual(fees.get_bank_charge(self.auditor, charge.pk)['status'], 'draft')
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'costing', False)
        self.denied(lambda: self.fee(), exceptions=(PermissionDenied,))
        self.assertEqual(len(fees.list_bank_charges(self.auditor)), 1)
        self.assertEqual(fees.net_fee_reconciliation(self.auditor, self.posted.pk)['draft_fee_cents'],
                         200)

    def test_database_guards_block_delete_and_terminal_status(self):
        charge = self.fee()
        with self.assertRaises((ValidationError, IntegrityError)):
            charge.delete()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BankCharge.objects.filter(pk=charge.pk).update(status='approved')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                BankCharge.objects.filter(pk=charge.pk).update(version=5)
        receipt = BankChargeReceipt.objects.get()
        with self.assertRaises((ValidationError, IntegrityError)):
            receipt.delete()

    def test_corrupt_facts_are_reported_by_verify_integrity(self):
        charge = self.fee()
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command('verify_integrity', '--json', stdout=out)
        self.assertIn('"ok": true', out.getvalue())
        BankCharge.objects.filter(pk=charge.pk).update(gross_cents=9801, version=2)
        out = StringIO()
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command('verify_integrity', '--json', stdout=out)
        self.assertIn(f'bank_charge:{charge.pk}:amount_over_net', out.getvalue())


class BankChargeVerifyIntegrityTests(BankChargeFixture, TestCase):
    """PH-253: per-version saved-event count and conditional PH-250 cost-fact boundaries."""

    def verify_errors(self):
        from io import StringIO
        from django.core.management import call_command
        from django.core.management.base import CommandError
        out = StringIO()
        try:
            call_command('verify_integrity', '--json', stdout=out)
        except CommandError:
            pass
        else:
            return []
        import json
        return json.loads(out.getvalue())['errors']

    def saved_events(self, charge):
        return list(DomainEvent.objects.filter(
            topic=TOPIC, data__source__id=str(charge.pk)).order_by('created_at', 'id'))

    def duplicate_saved_event(self, charge):
        from core.services import audit
        template = self.saved_events(charge)[0].data
        source = audit(self.owner, 'plugin.costing.bank_charge.saved', charge, {})
        DomainEvent.objects.create(event_id=uuid.uuid4(), organization=self.org,
                                   source=source, topic=TOPIC, data=template)

    def force_status(self, charge, status):
        """Test-only status transition; rollback restores the migration triggers."""
        from django.db import connection
        with connection.cursor() as cursor:
            for trigger in ('costing_bankcharge_update_guard', 'costing_bankcharge_insert_guard',
                            'costing_bankcharge_no_delete'):
                cursor.execute(f'DROP TRIGGER IF EXISTS {trigger}')
        BankCharge.objects.filter(pk=charge.pk).update(status=status)
        charge.refresh_from_db()

    def add_cost_facts(self, charge, *, posting_amount=None, duplicate_event=False,
                       create_posting=True):
        amount = charge.gross_cents if posting_amount is None else posting_amount
        event_id = uuid.uuid4()
        claim = SourceConsumption.objects.create(
            organization=charge.organization,
            source={'namespace': 'costing', 'kind': 'bank_charge', 'id': str(charge.pk),
                    'version': charge.version},
            source_id=charge.pk, consumer='cost', coverage_key=f'bank_charge:{charge.pk}',
            amount_cents=charge.gross_cents,
            event_ref={'namespace': 'core', 'kind': 'accounting_event', 'id': str(event_id),
                       'version': 1})
        event = AccountingEvent.objects.create(
            id=event_id, organization=charge.organization,
            source={'namespace': 'costing', 'kind': 'bank_charge', 'id': str(charge.pk),
                    'version': charge.version},
            event_type='costing.bank_charge.cost', occurred_on=charge.occurred_on,
            posting_period=charge.posting_period, algorithm_version='bank_fee_cost_v1',
            claim=claim, claim_effect_cents=charge.gross_cents)
        posting = None
        if create_posting:
            posting = LedgerPosting.objects.create(
                organization=charge.organization, accounting_event=event, book='cost',
                amount_cents=amount, scope_kind='project', scope_id=charge.scope_id,
                origin='current', line_index=1)
        if duplicate_event:
            AccountingEvent.objects.create(
                id=uuid.uuid4(), organization=charge.organization, source=event.source,
                event_type='costing.bank_charge.cost', occurred_on=charge.occurred_on,
                posting_period=charge.posting_period, algorithm_version='bank_fee_cost_v1',
                claim=claim, claim_effect_cents=charge.gross_cents)
        return claim, event, posting

    def test_empty_database_and_normal_drafts_pass(self):
        self.assertEqual(self.verify_errors(), [])
        charge = self.fee()
        self.assertEqual(self.verify_errors(), [])
        self.fee(expected_version=1, gross_cents=150)
        self.assertEqual(charge.version, 1)
        self.assertEqual(self.verify_errors(), [])

    def test_missing_middle_version_event_is_detected(self):
        charge = self.fee()
        self.fee(expected_version=1, gross_cents=150)
        self.saved_events(charge)[0].delete()
        errors = self.verify_errors()
        self.assertTrue(any(f'bank_charge:{charge.pk}:saved_event_count' in row for row in errors), errors)

    def test_duplicate_same_version_event_is_detected(self):
        charge = self.fee()
        self.duplicate_saved_event(charge)
        errors = self.verify_errors()
        self.assertTrue(any(f'bank_charge:{charge.pk}:saved_event_count' in row for row in errors), errors)

    def test_tampered_saved_event_envelopes_are_detected(self):
        charge = self.fee()
        events = self.saved_events(charge)
        bad = events[0].data
        bad['source']['version'] = 99
        events[0].data = bad
        events[0].save(update_fields=['data'])
        errors = self.verify_errors()
        self.assertTrue(any(f'bank_charge:{charge.pk}:saved_event_envelope' in row for row in errors), errors)
        bad['source']['version'] = 1
        bad['payload']['changed_refs'] = [{'namespace': 'costing', 'kind': 'bank_charge',
                                           'id': str(charge.pk), 'version': 2}]
        events[0].data = bad
        events[0].save(update_fields=['data'])
        errors = self.verify_errors()
        self.assertTrue(any(f'bank_charge:{charge.pk}:saved_event_envelope' in row for row in errors), errors)

    def test_approved_without_cost_facts_is_the_transitional_state(self):
        charge = self.fee()
        self.force_status(charge, 'approved')
        self.assertEqual(self.verify_errors(), [])

    def test_approved_with_valid_cost_facts_passes(self):
        charge = self.fee()
        self.force_status(charge, 'approved')
        self.add_cost_facts(charge)
        self.assertEqual(self.verify_errors(), [])

    def test_duplicate_cost_event_is_detected(self):
        charge = self.fee()
        self.force_status(charge, 'approved')
        self.add_cost_facts(charge, duplicate_event=True)
        errors = self.verify_errors()
        self.assertTrue(any(f'bank_charge:{charge.pk}:cost_event_count' in row for row in errors), errors)

    def test_missing_cost_posting_is_detected(self):
        charge = self.fee()
        self.force_status(charge, 'approved')
        self.add_cost_facts(charge, create_posting=False)
        errors = self.verify_errors()
        self.assertTrue(any(f'bank_charge:{charge.pk}:cost_posting_count' in row for row in errors), errors)

    def test_approved_cost_amount_mismatch_is_detected(self):
        charge = self.fee()
        self.force_status(charge, 'approved')
        self.add_cost_facts(charge, posting_amount=charge.gross_cents + 1)
        errors = self.verify_errors()
        self.assertTrue(any(f'bank_charge:{charge.pk}:cost_posting' in row for row in errors), errors)

    def test_unapproved_states_must_not_carry_cost_facts(self):
        for status in ('pending', 'rejected'):
            with self.subTest(status=status):
                cash = self.posted_cash(external_source_key='FEE-' + status + '-' + uuid.uuid4().hex[:6])
                charge = fees.save_bank_charge_draft(
                    self.owner, cash_transaction_ref=cash.transaction_ref, gross_cents=100,
                    scope_id=self.project.pk, evidence='回单', request_token=uuid.uuid4(),
                    dry_run=False)
                self.force_status(charge, status)
                self.add_cost_facts(charge)
                errors = self.verify_errors()
                self.assertTrue(any(f'bank_charge:{charge.pk}:cost_fact_on_unapproved' in row
                                    for row in errors), errors)

    def test_orphan_cost_event_is_detected(self):
        charge = self.fee()
        event_id = uuid.uuid4()
        claim = SourceConsumption.objects.create(
            organization=self.org,
            source={'namespace': 'costing', 'kind': 'bank_charge', 'id': str(uuid.uuid4()),
                    'version': 1},
            source_id=uuid.uuid4(), consumer='cost', coverage_key='bank_charge:orphan',
            amount_cents=100,
            event_ref={'namespace': 'core', 'kind': 'accounting_event', 'id': str(event_id),
                       'version': 1})
        AccountingEvent.objects.create(
            id=event_id, organization=self.org,
            source={'namespace': 'costing', 'kind': 'bank_charge', 'id': str(uuid.uuid4()),
                    'version': 1},
            event_type='costing.bank_charge.cost', occurred_on=self.today,
            posting_period=self.today.strftime('%Y-%m'), algorithm_version='bank_fee_cost_v1',
            claim=claim, claim_effect_cents=100)
        errors = self.verify_errors()
        self.assertTrue(any(':orphan_cost_event' in row for row in errors), errors)
        self.assertTrue(any(':unexpected_source_consumption' in row for row in errors), errors)
