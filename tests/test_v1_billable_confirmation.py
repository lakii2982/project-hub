"""PH-269 time billable-confirmation service and verify_integrity regressions."""
import json
import uuid
from datetime import timedelta
from io import StringIO

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, AuditEvent, DomainEvent, Employee,
                         LedgerEntry, Organization, SourceConsumption, Timesheet)
from pmo_plugins.time import services
from pmo_plugins.time.models import BillableConfirmation, BillableConfirmationReceipt
from tests.test_v1_contract_change import ContractChangeFixture


class TimeBillableFixture(ContractChangeFixture):
    def setUp(self):
        super().setUp()
        self.auditor = self.member('auditor')
        self.employee = Employee.objects.create(organization=self.org, name='可计费员工')
        self.timesheet = Timesheet.objects.create(
            organization=self.org, accounting_mode='monthly_pool', project=self.project,
            employee=self.employee, work_date=self.today, minutes=480, note='客户交付工时',
            status='approved', submitted_by=self.owner.user)
        approved = self.approved_contract()
        line = approved.lines.get()
        self.timesheet_ref = {'namespace': 'time', 'kind': 'timesheet',
                              'id': str(self.timesheet.pk), 'version': self.timesheet.version}
        self.line_ref = {'namespace': 'contracts', 'kind': 'contract_line',
                         'id': str(line.pk), 'version': 1}

    def draft(self, minutes=360, **changes):
        values = dict(timesheet_ref=self.timesheet_ref, contract_line_ref=self.line_ref,
                      confirmed_minutes=minutes, confirmed_on=self.today,
                      evidence=[{'kind': 'customer_email', 'reference': '客户确认邮件'}],
                      request_token=uuid.uuid4(), dry_run=False)
        values.update(changes)
        return services.save_billable_confirmation_draft(self.owner, **values)

    def confirm(self, confirmation, member=None, **changes):
        version = confirmation['version'] if isinstance(confirmation, dict) else confirmation.version
        identifier = confirmation['id'] if isinstance(confirmation, dict) else confirmation.pk
        values = dict(expected_version=version, request_token=uuid.uuid4(), dry_run=False)
        values.update(changes)
        return services.confirm_billable(member or self.finance, identifier, **values)

    def facts(self):
        return tuple(model.objects.count() for model in (
            BillableConfirmation, BillableConfirmationReceipt, AuditEvent, DomainEvent,
            LedgerEntry, AccountingEvent, SourceConsumption))

    def verify_errors(self):
        out = StringIO()
        try:
            call_command('verify_integrity', '--json', stdout=out)
        except CommandError:
            pass
        else:
            return []
        return json.loads(out.getvalue())['errors']

    def denied(self, call, exceptions=(ValidationError, PermissionDenied, TypeError)):
        before = self.facts()
        with self.assertRaises(exceptions):
            call()
        self.assertEqual(before, self.facts())


class BillableConfirmationServiceTests(TimeBillableFixture, TestCase):
    def test_draft_records_no_event_or_financial_fact(self):
        before = self.facts()
        row = self.draft()
        after = self.facts()
        self.assertEqual((row['status'], row['version']), ('draft', 1))
        self.assertFalse(DomainEvent.objects.filter(topic='time.billable_confirmed.v1').exists())
        self.assertEqual((after[0], after[1]), (before[0] + 1, before[1] + 1))
        self.assertEqual((after[4], after[5], after[6]), (before[4], before[5], before[6]))
        self.assertEqual(self.verify_errors(), [])

    def test_draft_replay_conflict_and_input_boundaries(self):
        token = uuid.uuid4()
        row = self.draft(request_token=token)
        replay = self.draft(request_token=token)
        self.assertEqual(replay['id'], row['id'])
        self.denied(lambda: self.draft(request_token=token, minutes=120))
        self.denied(lambda: self.draft(evidence=[]))
        self.denied(lambda: self.draft(minutes=481))
        self.denied(lambda: self.draft(minutes=True))
        self.denied(lambda: self.draft(minutes=1.5))
        self.denied(lambda: self.draft(confirmed_on=self.today + timedelta(days=1)))
        self.timesheet.status = 'submitted'
        self.timesheet.save()
        self.timesheet_ref['version'] = self.timesheet.version
        self.denied(lambda: self.draft())

    def test_draft_edit_allows_business_fields_and_confirmation_uses_edited_values(self):
        row = self.draft()
        edited = services.save_billable_confirmation_draft(
            self.owner, timesheet_ref=self.timesheet_ref, contract_line_ref=self.line_ref,
            confirmed_minutes=120, confirmed_on=self.today,
            evidence=[{'kind': 'customer_email', 'reference': '客户更新确认'}],
            request_token=uuid.uuid4(), confirmation_id=row['id'], expected_version=1,
            dry_run=False)
        self.assertEqual((edited['status'], edited['version'], edited['confirmed_minutes']),
                         ('draft', 2, 120))
        confirmed = self.confirm(edited)
        stored = BillableConfirmation.objects.get(pk=row['id'])
        self.assertEqual((stored.status, stored.confirmed_minutes, stored.version),
                         ('confirmed', 120, 3))
        self.assertEqual(stored.confirmed_on, self.today)
        self.assertEqual(stored.evidence[0]['reference'], '客户更新确认')
        self.assertEqual(confirmed['confirmed_minutes'], 120)
        self.assertEqual(DomainEvent.objects.filter(topic='time.billable_confirmed.v1').count(), 1)
        self.assertEqual(self.verify_errors(), [])

    def test_independent_confirmation_creates_exactly_one_canonical_event(self):
        before = self.facts()
        row = self.draft()
        self.denied(lambda: self.confirm(row, member=self.owner))
        confirmed = self.confirm(row)
        after = self.facts()
        self.assertEqual((confirmed['status'], confirmed['version']), ('confirmed', 2))
        stored = BillableConfirmation.objects.get(pk=row['id'])
        self.assertEqual((stored.status, stored.confirmed_by_member_id), ('confirmed', self.finance.pk))
        events = DomainEvent.objects.filter(topic='time.billable_confirmed.v1')
        self.assertEqual(events.count(), 1)
        data = events.get().data
        self.assertEqual(data['source'], stored.source_ref)
        self.assertEqual(data['payload']['changed_refs'],
                         [stored.source_ref, stored.timesheet_ref, stored.contract_line_ref])
        self.assertEqual(data['payload']['posting_event_refs'], [])
        self.assertEqual((after[4], after[5], after[6]),
                         (before[4] + 0, before[5] + 0, before[6] + 0))
        self.assertEqual(self.verify_errors(), [])
        self.denied(lambda: self.confirm(row, member=self.finance))
        self.denied(lambda: self.confirm(row, member=self.owner))

    def test_confirm_replay_is_idempotent(self):
        row = self.draft()
        token = uuid.uuid4()
        first = self.confirm(row, request_token=token)
        events = DomainEvent.objects.filter(topic='time.billable_confirmed.v1').count()
        replay = self.confirm(row, request_token=token)
        self.assertEqual(replay['id'], first['id'])
        self.assertEqual(DomainEvent.objects.filter(topic='time.billable_confirmed.v1').count(), events)

    def test_period_lock_roles_and_disabled_module(self):
        self.org.locked_through = self.today
        self.org.save()
        self.denied(lambda: self.draft())
        self.org.locked_through = None
        self.org.save()
        AccountingPeriod.objects.create(organization=self.org, period=self.today.strftime('%Y-%m'),
                                        status='posted', closed_through=self.today)
        self.denied(lambda: self.draft())
        AccountingPeriod.objects.filter(organization=self.org).delete()
        for role in ('auditor', 'pm', 'member'):
            with self.assertRaises((ValidationError, PermissionDenied)):
                self.draft(member=self.member(role)) if False else services.save_billable_confirmation_draft(
                    self.member(role), timesheet_ref=self.timesheet_ref,
                    contract_line_ref=self.line_ref, confirmed_minutes=60,
                    confirmed_on=self.today, evidence=[{'kind': 'x', 'reference': 'y'}],
                    request_token=uuid.uuid4(), dry_run=False)
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'time', False)
        self.denied(lambda: self.draft())

    def test_verify_integrity_detects_missing_event_and_self_confirmation(self):
        self.draft()
        row = self.confirm(self.draft())
        DomainEvent.objects.filter(topic='time.billable_confirmed.v1').delete()
        errors = self.verify_errors()
        self.assertTrue(any('confirmed_event_count' in row for row in errors), errors)
        from django.db import connection
        with connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                # PostgreSQL uses one merged guard trigger per table.
                cursor.execute('DROP TRIGGER IF EXISTS time_billableconfirmation_guard '
                               'ON time_billableconfirmation')
            else:
                for trigger in ('time_billableconfirmation_insert_guard',
                                'time_billableconfirmation_update_guard',
                                'time_billableconfirmation_no_delete'):
                    cursor.execute(f'DROP TRIGGER IF EXISTS {trigger}')
        BillableConfirmation.objects.filter(pk=row['id']).update(
            confirmed_by_member=self.owner, version=3)
        errors = self.verify_errors()
        self.assertTrue(any('self_confirmed' in row for row in errors), errors)
