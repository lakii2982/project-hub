"""PH-250 submit / independent approve / reject state machine for bank-fee drafts."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, AuditEvent, DomainEvent, LedgerEntry,
                         LedgerPosting, Organization, PluginState, SourceConsumption)
from pmo_plugins.costing import bank_charge_services as fees
from pmo_plugins.costing.models import BankCharge, BankChargeReceipt
from tests.test_v1_bank_charge import BankChargeFixture

SUBMITTED_TOPIC = 'costing.bank_charge.submitted.v1'
APPROVED_TOPIC = 'costing.bank_charge.approved.v1'
REJECTED_TOPIC = 'costing.bank_charge.rejected.v1'


class BankChargeApprovalFixture(BankChargeFixture):
    def setUp(self):
        super().setUp()
        self.finance2 = self.member('finance')

    def draft(self, member=None):
        return self.fee(member=member or self.finance)

    def submit(self, charge=None, member=None, **changes):
        charge = charge or self.draft()
        values = {'request_token': uuid.uuid4(), 'dry_run': False,
                  'expected_version': charge.version}
        values.update(changes)
        return fees.submit_bank_charge(member or self.finance, charge.pk, **values)

    def decide(self, charge, decision='approve', member=None, note='', **changes):
        values = {'request_token': uuid.uuid4(), 'dry_run': False,
                  'expected_version': charge.version}
        values.update(changes)
        return fees.decide_bank_charge(member or self.owner, charge.pk, decision=decision,
                                       note=note, **values)


class BankChargeApprovalServiceTests(BankChargeApprovalFixture, TestCase):
    def test_submit_moves_to_pending_without_version_or_cost_effect(self):
        charge = self.draft()
        before = self.facts()
        submitted = self.submit(charge)
        after = self.facts()
        self.assertEqual((submitted.status, submitted.version), ('pending', charge.version))
        self.assertEqual(submitted.submitted_by_id, self.finance.pk)
        self.assertIsNotNone(submitted.submitted_at)
        self.assertEqual(after[1], before[1])        # no new BankCharge
        self.assertEqual(after[2], before[2] + 1)    # submit receipt
        self.assertEqual(after[3], before[3] + 1)    # generic submit audit
        self.assertEqual(after[4], before[4] + 1)    # its non-canonical outbox event
        self.assertEqual(before[5:], after[5:])      # no accounting/ledger/claim/settlement rows
        self.assertEqual(DomainEvent.objects.filter(topic=SUBMITTED_TOPIC).count(), 1)

    def test_submit_replay_and_duplicate_have_no_half_write(self):
        charge = self.draft()
        token = uuid.uuid4()
        first = self.submit(charge, request_token=token)
        after = self.facts()
        replay = self.submit(charge, request_token=token)
        self.assertEqual(replay.pk, first.pk)
        self.assertEqual(after, self.facts())
        self.denied(lambda: self.submit(charge))
        self.assertEqual(after, self.facts())

    def test_submit_rejects_non_writers_module_disable_and_closed_periods(self):
        charge = self.draft()
        for member in (self.auditor, self.plain):
            self.denied(lambda member=member: self.submit(charge, member=member),
                        exceptions=(PermissionDenied,))
        self.assertEqual(charge.status, 'draft')
        self.org.locked_through = self.today
        self.org.save()
        self.denied(lambda: self.submit(charge))
        self.org.locked_through = None
        self.org.save()
        AccountingPeriod.objects.update_or_create(
            organization=self.org, period=self.today.strftime('%Y-%m'),
            defaults={'status': 'posted', 'closed_through': self.today})
        self.denied(lambda: self.submit(charge))
        AccountingPeriod.objects.filter(organization=self.org).delete()
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'costing', False)
        self.denied(lambda: self.submit(charge), exceptions=(PermissionDenied,))

    def test_independent_approval_chain_and_self_approval_rejection(self):
        charge = self.draft(member=self.finance)
        self.submit(charge, member=self.finance)
        # creator/ submitter cannot decide
        self.denied(lambda: self.decide(charge, member=self.finance), exceptions=(ValidationError,))
        approved = self.decide(charge, member=self.owner)
        self.assertEqual((approved.status, approved.version), ('approved', 1))
        self.assertEqual(approved.approved_by_id, self.owner.pk)
        self.assertEqual(approved.reject_reason, '')
        self.assertEqual(DomainEvent.objects.filter(topic=APPROVED_TOPIC).count(), 1)
        self.assertEqual(DomainEvent.objects.filter(topic='costing.bank_charge.cost_posted.v1').count(), 1)
        self.denied(lambda: self.decide(charge, member=self.finance2), exceptions=(ValidationError,))

    def test_approve_requires_pending_and_reject_requires_reason(self):
        charge = self.draft()
        self.denied(lambda: self.decide(charge, member=self.owner), exceptions=(ValidationError,))
        self.submit(charge)
        self.denied(lambda: self.decide(charge, decision='reject', member=self.owner, note=' '))
        rejected = self.decide(charge, decision='reject', member=self.owner, note='金额依据不足')
        self.assertEqual((rejected.status, rejected.reject_reason), ('rejected', '金额依据不足'))
        self.assertEqual(rejected.approved_by_id, self.owner.pk)
        self.assertEqual(DomainEvent.objects.filter(topic=REJECTED_TOPIC).count(), 1)
        self.denied(lambda: self.decide(charge, member=self.finance2), exceptions=(ValidationError,))

    def test_reject_replay_and_role_permissions(self):
        charge = self.submit(self.draft())
        token = uuid.uuid4()
        rejected = self.decide(charge, decision='reject', member=self.owner, note='依据不足',
                               request_token=token)
        after = self.facts()
        replay = self.decide(charge, decision='reject', member=self.owner, note='依据不足',
                             request_token=token)
        self.assertEqual(replay.pk, rejected.pk)
        self.assertEqual(after, self.facts())
        for member in (self.auditor, self.plain):
            self.denied(lambda member=member: self.decide(charge, member=member),
                        exceptions=(PermissionDenied,))
