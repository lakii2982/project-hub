"""PH-250 approved bank fee posts exactly one company cost fact set; verify_integrity boundaries."""
import uuid
from io import StringIO

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, AuditEvent, DomainEvent, LedgerEntry,
                         LedgerPosting, Organization, SourceConsumption)
from pmo_plugins.costing import bank_charge_services as fees
from pmo_plugins.costing.models import BankCharge, BankChargeReceipt
from tests.test_v1_bank_charge_approval import BankChargeApprovalFixture

COST_EVENT_TYPE = 'costing.bank_charge.cost'


class BankChargeCostTests(BankChargeApprovalFixture, TestCase):
    def verify_errors(self):
        out = StringIO()
        try:
            call_command('verify_integrity', '--json', stdout=out)
        except CommandError:
            pass
        else:
            return []
        import json
        return json.loads(out.getvalue())['errors']

    def approved_charge(self):
        charge = self.submit(self.draft(member=self.finance))
        return self.decide(charge, member=self.owner)

    def test_approval_posts_exactly_one_cost_fact_set_and_no_cash_effect(self):
        charge = self.draft(member=self.finance)
        self.submit(charge, member=self.finance)
        before = self.facts()
        approved = self.decide(charge, member=self.owner)
        after = self.facts()
        self.assertEqual((approved.status, approved.version), ('approved', 1))
        self.assertEqual(after[1], before[1])        # no new BankCharge
        self.assertEqual(after[2], before[2] + 1)    # decision receipt
        self.assertEqual(after[3], before[3] + 2)    # approved + cost_posted audits
        self.assertEqual(after[4], before[4] + 2)    # approved + cost_posted outbox rows
        self.assertEqual(after[5], before[5] + 1)    # one AccountingEvent
        self.assertEqual(after[6], before[6] + 1)    # one LedgerPosting
        self.assertEqual(after[8], before[8] + 1)    # one SourceConsumption claim
        self.assertEqual((after[0], after[9], after[10], after[11]),
                         (before[0], before[9], before[10], before[11]))
        claim = SourceConsumption.objects.get(coverage_key=f'bank_charge:{charge.pk}')
        self.assertEqual((claim.consumer, claim.amount_cents, str(claim.source_id)),
                         ('cost', charge.gross_cents, str(charge.pk)))
        event = AccountingEvent.objects.get(claim=claim)
        self.assertEqual((event.source, event.event_type, event.claim_effect_cents,
                          event.occurred_on, event.posting_period),
                         (charge.source_ref, COST_EVENT_TYPE, charge.gross_cents,
                          charge.occurred_on, charge.posting_period))
        posting = LedgerPosting.objects.get(accounting_event=event)
        self.assertEqual((posting.book, posting.origin, posting.scope_kind, posting.scope_id,
                          posting.amount_cents, posting.line_index),
                         ('cost', 'current', 'project', charge.scope_id, charge.gross_cents, 1))
        snapshot = fees.bank_charge_cost_snapshot(self.auditor, charge.pk)
        self.assertEqual(snapshot['cost_amount_cents'], charge.gross_cents)
        self.assertEqual(snapshot['cost_event_ref'], {'namespace': 'core', 'kind': 'accounting_event',
                                                      'id': str(event.pk), 'version': 1})
        self.assertEqual(self.verify_errors(), [])

    def test_duplicate_decision_cannot_duplicate_cost(self):
        charge = self.approved_charge()
        before = self.facts()
        self.denied(lambda: self.decide(charge, member=self.finance2), exceptions=(ValidationError,))
        self.assertEqual(before, self.facts())

    def test_rejected_charge_has_no_cost_facts(self):
        charge = self.submit(self.draft())
        before = self.facts()
        rejected = self.decide(charge, decision='reject', member=self.owner, note='依据不足')
        after = self.facts()
        self.assertEqual(rejected.status, 'rejected')
        self.assertEqual((after[5], after[6], after[8]), (before[5], before[6], before[8]))

    # ---------- synthetic corruption probes (direct DB states, rollback restores triggers) ----------
    def force_approved(self, charge, *, approved_by=None):
        # PostgreSQL installs one combined guard trigger and requires ON TABLE when
        # dropping it; SQLite installs per-operation triggers without a table clause.
        with connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                cursor.execute(
                    'DROP TRIGGER IF EXISTS costing_bankcharge_guard ON costing_bankcharge')
            else:
                for trigger in ('costing_bankcharge_insert_guard',
                                'costing_bankcharge_update_guard',
                                'costing_bankcharge_transition_guard',
                                'costing_bankcharge_no_delete',
                                'costing_bankcharge_guard'):
                    cursor.execute(f'DROP TRIGGER IF EXISTS {trigger}')
        now = timezone.now()
        BankCharge.objects.filter(pk=charge.pk).update(
            status='approved', submitted_by=self.finance, submitted_at=now,
            approved_by=approved_by or self.owner, approved_at=now, reject_reason='')
        charge.refresh_from_db()

    def add_cost_facts(self, charge, *, posting_amount=None, create_posting=True,
                       duplicate_event=False, claim_consumer='cost', claim_key=None):
        amount = charge.gross_cents if posting_amount is None else posting_amount
        key = claim_key or f'bank_charge:{charge.pk}'
        event_id = uuid.uuid4()
        claim = SourceConsumption.objects.create(
            organization=charge.organization, source=charge.source_ref, source_id=charge.pk,
            consumer=claim_consumer, coverage_key=key,
            amount_cents=charge.gross_cents,
            event_ref={'namespace': 'core', 'kind': 'accounting_event', 'id': str(event_id),
                       'version': 1})
        event = AccountingEvent.objects.create(
            id=event_id, organization=charge.organization, source=charge.source_ref,
            event_type=COST_EVENT_TYPE, occurred_on=charge.occurred_on,
            posting_period=charge.posting_period, algorithm_version='bank_fee_cost_v1',
            claim=claim, claim_effect_cents=charge.gross_cents)
        if create_posting:
            LedgerPosting.objects.create(
                organization=charge.organization, accounting_event=event, book='cost',
                amount_cents=amount, scope_kind='project', scope_id=charge.scope_id,
                origin='current', line_index=1)
        if duplicate_event:
            AccountingEvent.objects.create(
                id=uuid.uuid4(), organization=charge.organization, source=charge.source_ref,
                event_type=COST_EVENT_TYPE, occurred_on=charge.occurred_on,
                posting_period=charge.posting_period, algorithm_version='bank_fee_cost_v1',
                claim=claim, claim_effect_cents=charge.gross_cents)
        return claim, event

    def test_verify_integrity_detects_broken_approved_cost_facts(self):
        charge = self.draft()
        self.force_approved(charge)
        errors = self.verify_errors()
        self.assertEqual(errors, [])  # PH-253-compatible transitional state without facts
        self.add_cost_facts(charge, create_posting=False)
        errors = self.verify_errors()
        self.assertTrue(any(f'bank_charge:{charge.pk}:cost_posting_count' in row for row in errors), errors)

    def test_verify_integrity_detects_duplicate_cost_event_and_amount_mismatch(self):
        charge = self.draft()
        self.force_approved(charge)
        self.add_cost_facts(charge, duplicate_event=True)
        errors = self.verify_errors()
        self.assertTrue(any(f'bank_charge:{charge.pk}:cost_event_count' in row for row in errors), errors)
        charge2 = self.posted_cash(external_source_key='FEE-COST-2')
        draft2 = fees.save_bank_charge_draft(
            self.owner, cash_transaction_ref=charge2.transaction_ref, gross_cents=200,
            scope_id=self.project.pk, evidence='回单', request_token=uuid.uuid4(), dry_run=False)
        self.force_approved(draft2)
        self.add_cost_facts(draft2, posting_amount=draft2.gross_cents + 1)
        errors = self.verify_errors()
        self.assertTrue(any(f'bank_charge:{draft2.pk}:cost_posting' in row for row in errors), errors)

    def test_verify_integrity_detects_self_approval_and_unapproved_cost_facts(self):
        charge = self.draft()
        self.force_approved(charge, approved_by=charge.created_by)
        errors = self.verify_errors()
        self.assertTrue(any(f'bank_charge:{charge.pk}:self_approved' in row for row in errors), errors)
        cash2 = self.posted_cash(external_source_key='FEE-SELF-' + uuid.uuid4().hex[:6])
        charge2 = fees.save_bank_charge_draft(
            self.owner, cash_transaction_ref=cash2.transaction_ref, gross_cents=100,
            scope_id=self.project.pk, evidence='回单', request_token=uuid.uuid4(), dry_run=False)
        self.add_cost_facts(charge2)
        errors = self.verify_errors()
        self.assertTrue(any(f'bank_charge:{charge2.pk}:cost_fact_on_unapproved' in row for row in errors), errors)
