"""PH-265 termination guard, history preservation and verify_integrity coverage."""
import json
import uuid
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase

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
