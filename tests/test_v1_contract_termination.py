"""PH-265 termination guard, history preservation and verify_integrity coverage."""
import json
import uuid
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import AuditEvent, DomainEvent, LedgerEntry
from pmo_plugins.contracts import approval_services, change_services
from pmo_plugins.contracts.models import Contract, ContractChange, ContractVersion
from tests.test_v1_contract_change import ContractChangeFixture


class ContractTerminationTests(ContractChangeFixture, TestCase):
    def verify_errors(self):
        out = StringIO()
        try:
            call_command('verify_integrity', '--json', stdout=out)
        except CommandError:
            pass
        else:
            return []
        return json.loads(out.getvalue())['errors']

    def approved_with_history(self):
        old = self.approved_contract(effective_on=self.today - timedelta(days=10))
        line = old.lines.get()
        ref = {'namespace': 'contracts', 'kind': 'contract_line', 'id': str(line.pk),
               'version': 1}
        return old, ref

    def test_termination_preserves_history_and_settlement_guard(self):
        old, ref = self.approved_with_history()
        change = self.draft_change(change_type='termination', lines=[])
        approved = self.decide(change, note='客户终止合同')
        self.contract.refresh_from_db()
        self.assertEqual((approved.status, self.contract.status), ('approved', 'terminated'))
        self.assertEqual(self.contract.terminated_on, self.today)
        self.assertEqual(ContractVersion.objects.filter(contract=self.contract).count(), 1)
        self.assertEqual(ContractVersion.objects.filter(contract=self.contract, status='approved').count(), 1)
        self.assertEqual(old.lines.count(), 1)
        self.assertEqual(approval_services.approved_terms_snapshot(
            self.finance, ref, self.today - timedelta(days=1))['line_id'], str(old.lines.get().pk))
        with self.assertRaises(Exception):
            approval_services.approved_terms_snapshot(self.finance, ref, self.today)
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.assertEqual(self.verify_errors(), [])
        # terminated contract cannot be changed again
        with self.assertRaises(Exception):
            self.draft_change()

    def test_verify_integrity_detects_missing_or_tampered_change_event(self):
        self.approved_with_history()
        change = self.draft_change(change_type='termination', lines=[])
        self.decide(change)
        self.assertEqual(self.verify_errors(), [])
        event = DomainEvent.objects.get(topic='contracts.change_effective.v1')
        event.delete()
        errors = self.verify_errors()
        self.assertTrue(any(f'contract_change:{change.pk}:effective_event_count' in row
                            for row in errors), errors)

    def test_verify_integrity_detects_version_and_status_corruption(self):
        old, _ = self.approved_with_history()
        self.draft_change(change_type='termination', lines=[])
        # a raw approved change without the canonical event/status link is corruption
        with connection.cursor() as cursor:
            for trigger in ('contracts_contractchange_insert_guard',
                            'contracts_contractchange_update_guard',
                            'contracts_contractchange_no_delete'):
                cursor.execute(f'DROP TRIGGER IF EXISTS {trigger}')
        ContractChange.objects.update(status='approved', version=2)
        errors = self.verify_errors()
        self.assertTrue(any('contract_change' in row for row in errors), errors)


    def test_db_trigger_rejects_self_decision_and_new_change_on_terminated(self):
        old, _ = self.approved_with_history()
        change = self.draft_change(change_type='termination', lines=[])
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ContractChange.objects.filter(pk=change.pk).update(
                    status='approved', version=2, decided_by_member=self.owner,
                    decided_on=timezone.now())
        self.decide(change, member=self.finance, note='客户终止')
        self.contract.refresh_from_db()
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                ContractChange.objects.create(
                    organization=self.org, contract=self.contract, change_type='price_change',
                    requested_on=self.today, created_by_member=self.owner, status='draft',
                    version=1, draft_content={'lines': [self.line(11000)]}, evidence=[])

    def test_verify_integrity_detects_self_decision_and_change_after_termination(self):
        self.approved_with_history()
        change = self.draft_change(change_type='termination', lines=[])
        self.decide(change, note='终止')
        with connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                # PostgreSQL installs one combined guard trigger per table and
                # needs ON TABLE when dropping it.
                cursor.execute(
                    'DROP TRIGGER IF EXISTS contracts_change_guard ON contracts_contractchange')
            else:
                for trigger in ('contracts_contractchange_update_guard',
                                'contracts_contractchange_insert_guard'):
                    cursor.execute(f'DROP TRIGGER IF EXISTS {trigger}')
        ContractChange.objects.filter(pk=change.pk).update(
            decided_by_member=self.owner, version=3)
        errors = self.verify_errors()
        self.assertTrue(any(f'contract_change:{change.pk}:self_decided' in row for row in errors), errors)
        ContractChange.objects.create(
            organization=self.org, contract=self.contract, change_type='price_change',
            requested_on=self.today, created_by_member=self.owner, status='draft',
            version=1, draft_content={'lines': [self.line(11000)]}, evidence=[])
        errors = self.verify_errors()
        self.assertTrue(any('created_after_termination' in row for row in errors), errors)
        self.assertTrue(any(f'contract:{self.contract.pk}:draft_change_after_termination' in row
                            for row in errors), errors)

    def test_verify_integrity_detects_forged_version_approved_changed_refs(self):
        self.approved_with_history()
        change = self.draft_change()
        self.decide(change, note='批准变更')
        version = ContractVersion.objects.get(contract=self.contract, status='approved')
        event = DomainEvent.objects.get(topic='contracts.version_approved.v1',
                                        data__source__id=str(version.pk))
        data = dict(event.data)
        payload = dict(data['payload'])
        payload['changed_refs'] = [{'namespace': 'contracts', 'kind': 'contract_version',
                                    'id': 'forged', 'version': 1}]
        data['payload'] = payload
        event.data = data
        event.save()
        errors = self.verify_errors()
        self.assertTrue(any(f'contract_version:{version.pk}:approved_changed_refs' in row
                            for row in errors), errors)
