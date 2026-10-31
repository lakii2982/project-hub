"""PH-322 period/project close append-only run invariants."""
import uuid
from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, AuditEvent, DomainEvent,
                         LedgerEntry, Membership, Organization, Project)
from pmo_plugins.contracts.models import Contract, Counterparty
from pmo_plugins.delivery import close_services as services
from pmo_plugins.delivery.models import DeliveryReceipt, PeriodCloseRun, ProjectClose


class CloseFixture(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.org = Organization.objects.create(name='PH322 公司')
        self.owner = self.member('owner')
        self.finance = self.member('finance')
        self.auditor = self.member('auditor')
        self.pm = self.member('pm')
        self.plain = self.member('member')
        self.project = Project.objects.create(
            organization=self.org, code='P322', name='关期项目', client='客户',
            manager=self.owner, start_date=self.today - timedelta(days=60),
            end_date=self.today, stage='execution')
        party = Counterparty.objects.create(
            organization=self.org, display_name='关期客户', roles=['customer'],
            active_from=self.today - timedelta(days=90))
        self.contract = Contract.objects.create(
            organization=self.org, project=self.project, party=party,
            contract_code='C322', status='active', signed_on=self.today - timedelta(days=60))
        self.current = AccountingPeriod.objects.create(
            organization=self.org, period=self.today.strftime('%Y-%m'),
            status='posted', closed_through=self.today)
        previous = self.today.replace(day=1) - timedelta(days=1)
        self.period = previous.strftime('%Y-%m')
        self.period_row = AccountingPeriod.objects.create(
            organization=self.org, period=self.period, status='active')
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'delivery', True)

    def member(self, role):
        user = get_user_model().objects.create_user(username=role + uuid.uuid4().hex)
        return Membership.objects.create(organization=self.org, user=user, role=role)

    def close_period(self, **changes):
        values = dict(request_token=uuid.uuid4(), expected_version=self.period_row.version,
                      dry_run=False, reason='月末关期')
        values.update(changes)
        return services.close_period(self.owner, self.period, **values)

    def facts(self):
        from pmo_plugins.cash.models import CashTransaction
        from pmo_plugins.costing.models import SalaryPool
        from pmo_plugins.management_revenue.models import RevenueRecognition
        from pmo_plugins.settlement.models import OpenItem
        return (LedgerEntry.objects.count(), AccountingEvent.objects.count(),
                RevenueRecognition.objects.count(), SalaryPool.objects.count(),
                CashTransaction.objects.count(), OpenItem.objects.count())

    def close_project(self, **changes):
        values = dict(request_token=uuid.uuid4(), expected_version=self.project.version,
                      dry_run=False, reason='项目结项', allow_open_period=False)
        values.update(changes)
        return services.close_project(self.owner, self.project.pk, **values)


class PeriodCloseTests(CloseFixture, TestCase):
    def test_precheck_shape_and_digest(self):
        result = services.period_close_precheck(self.owner, self.period)
        self.assertEqual(result['scope'], 'period')
        self.assertEqual(result['state'], 'open')
        self.assertIsInstance(result['blockers'], list)
        self.assertIsInstance(result['warnings'], list)
        self.assertIsInstance(result['missing'], list)
        self.assertEqual(len(result['precheck_digest']), 64)
        for item in result['blockers'] + result['warnings']:
            self.assertIn('issue_code', item)
            self.assertIn('reminder_ref', item)
            self.assertEqual(item['reminder_ref']['kind'], 'close_reminder')

    def test_dry_run_zero_writes_and_execute_pair(self):
        before = (PeriodCloseRun.objects.count(), DeliveryReceipt.objects.count(),
                  AuditEvent.objects.count(), self.period_row.version)
        plan = self.close_period(dry_run=True)
        self.assertTrue(plan['dry_run'])
        self.assertEqual(before, (PeriodCloseRun.objects.count(), DeliveryReceipt.objects.count(),
                                  AuditEvent.objects.count(), self.period_row.version))
        result = self.close_period()
        self.period_row.refresh_from_db()
        self.assertEqual(self.period_row.status, 'posted')
        self.assertEqual(self.period_row.closed_through,
                         self.today.replace(day=1) - timedelta(days=1))
        runs = list(PeriodCloseRun.objects.order_by('sequence'))
        self.assertEqual([run.state for run in runs], ['closing', 'closed'])
        self.assertEqual([run.sequence for run in runs], [1, 2])
        self.assertEqual(runs[1].pk, uuid.UUID(result['run_ref']['id']))
        self.assertTrue(DeliveryReceipt.objects.filter(action='period.close').exists())
        self.assertFalse(DomainEvent.objects.filter(topic__startswith='delivery.period').exists())

    def test_stale_version_replay_and_changed_input(self):
        token = uuid.uuid4()
        first = self.close_period(request_token=token)
        replay = self.close_period(request_token=token)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['run_ref'], first['run_ref'])
        self.assertEqual(PeriodCloseRun.objects.count(), 2)
        with self.assertRaises(ValidationError):
            self.close_period(request_token=token, reason='改输入')
        with self.assertRaises(ValidationError):
            services.reopen_period(self.owner, self.period, request_token=uuid.uuid4(),
                                   expected_version=1, dry_run=False, reason='x')

    def test_reopen_appends_reopened_and_requires_reason(self):
        self.close_period()
        self.period_row.refresh_from_db()
        with self.assertRaises(ValidationError):
            services.reopen_period(self.owner, self.period, request_token=uuid.uuid4(),
                                   expected_version=self.period_row.version, dry_run=False,
                                   reason='')
        result = services.reopen_period(
            self.owner, self.period, request_token=uuid.uuid4(),
            expected_version=self.period_row.version, dry_run=False, reason='发现错账')
        self.period_row.refresh_from_db()
        self.assertEqual(self.period_row.status, 'active')
        self.assertIsNone(self.period_row.closed_through)
        self.assertEqual(PeriodCloseRun.objects.order_by('sequence').last().state, 'reopened')
        self.assertEqual(PeriodCloseRun.objects.count(), 3)
        self.assertEqual(result['state'], 'open')

    def test_permissions_and_module_disabled(self):
        with self.assertRaises(PermissionDenied):
            services.close_period(self.plain, self.period, request_token=uuid.uuid4(),
                                  expected_version=1, dry_run=True)
        with self.assertRaises(PermissionDenied):
            services.close_period(self.auditor, self.period, request_token=uuid.uuid4(),
                                  expected_version=1, dry_run=True)
        self.assertEqual(services.period_close_precheck(self.auditor, self.period)['scope'],
                         'period')
        from core.plugin_runtime import set_enabled
        for plugin_id in ('invoicing', 'onboarding', 'management_revenue', 'settlement',
                          'time', 'contracts'):
            try:
                set_enabled(self.owner, plugin_id, False)
            except ValidationError:
                pass
        set_enabled(self.owner, 'delivery', False)
        with self.assertRaises(PermissionDenied):
            self.close_period()

    def test_reopen_precheck_is_usable_for_closed_period(self):
        self.close_period()
        result = services.reopen_period_precheck(self.owner, self.period)
        self.assertEqual(result['mode'], 'reopen')
        self.assertEqual(result['state'], 'closed')
        self.assertTrue(result['eligible'])
        self.assertEqual(result['blockers'], [])
        self.assertEqual(len(result['precheck_digest']), 64)
        self.period_row.refresh_from_db()
        self.assertEqual(result['period_version'], self.period_row.version)

    def test_pm_period_precheck_and_page_denied(self):
        with self.assertRaises(PermissionDenied):
            services.period_close_precheck(self.pm, self.period)
        with self.assertRaises(PermissionDenied):
            services.reopen_period_precheck(self.pm, self.period)

    def test_business_fact_counts_period_close_and_reopen(self):
        before = self.facts()
        self.close_period(dry_run=True)
        self.assertEqual(self.facts(), before)
        self.close_period()
        self.assertEqual(self.facts(), before)
        self.period_row.refresh_from_db()
        services.reopen_period(self.owner, self.period, request_token=uuid.uuid4(),
                               expected_version=self.period_row.version, dry_run=True,
                               reason='核对')
        self.assertEqual(self.facts(), before)
        services.reopen_period(self.owner, self.period, request_token=uuid.uuid4(),
                               expected_version=self.period_row.version, dry_run=False,
                               reason='核对')
        self.assertEqual(self.facts(), before)

    def test_verifier_rejects_double_reopen_and_gap(self):
        self.close_period()
        latest = PeriodCloseRun.objects.order_by('sequence').last()
        PeriodCloseRun.objects.create(
            organization=self.org, accounting_period=self.period_row,
            period=self.period, sequence=latest.sequence + 1, action='reopen',
            state='reopened', from_period_version=self.period_row.version,
            closed_through=None, blockers=[], warnings=[], precheck_digest='0' * 64,
            reason='伪造重开', actor=self.owner, request_token=uuid.uuid4())
        PeriodCloseRun.objects.create(
            organization=self.org, accounting_period=self.period_row,
            period=self.period, sequence=latest.sequence + 2, action='reopen',
            state='reopened', from_period_version=self.period_row.version,
            closed_through=None, blockers=[], warnings=[], precheck_digest='0' * 64,
            reason='重复重开', actor=self.owner, request_token=uuid.uuid4())
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_integrity', '--json', stdout=output)
        self.assertIn('reopen_transition', output.getvalue())
        PeriodCloseRun.objects.create(
            organization=self.org, accounting_period=self.period_row,
            period=self.period, sequence=latest.sequence + 9, action='close',
            state='closing', from_period_version=self.period_row.version,
            closed_through=None, blockers=[], warnings=[], precheck_digest='0' * 64,
            actor=self.owner, request_token=uuid.uuid4())
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_integrity', '--json', stdout=output)
        self.assertIn('sequence', output.getvalue())

    def test_verifier_rejects_receipt_result_mismatch(self):
        DeliveryReceipt.objects.create(
            organization=self.org, request_token=uuid.uuid4(), action='period.close',
            digest='0' * 64, result_id=uuid.uuid4())
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_integrity', '--json', stdout=output)
        self.assertIn('close_receipt:', output.getvalue())

    def test_append_only_trigger_and_verify_probe(self):
        self.close_period()
        run = PeriodCloseRun.objects.first()
        for operation in (
                lambda: PeriodCloseRun.objects.filter(pk=run.pk).update(sequence=99),
                lambda: PeriodCloseRun.objects.filter(pk=run.pk).delete()):
            with transaction.atomic():
                with self.assertRaises(IntegrityError):
                    operation()
        PeriodCloseRun.objects.create(
            organization=self.org, accounting_period=self.period_row,
            period=self.period, sequence=99, action='close', state='closing',
            from_period_version=self.period_row.version, closed_through=None,
            blockers=[], warnings=[], precheck_digest='0' * 64,
            actor=self.owner, request_token=uuid.uuid4())
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_integrity', '--json', stdout=output)
        self.assertIn('period_close:', output.getvalue())


class ProjectCloseTests(CloseFixture, TestCase):
    def test_precheck_blocker_contract_missing_and_zero_writes(self):
        other = Project.objects.create(
            organization=self.org, code='P322X', name='无合同项目', client='客户',
            manager=self.owner, start_date=self.today - timedelta(days=10),
            end_date=self.today, stage='execution')
        precheck = services.project_close_precheck(self.owner, other.pk)
        codes = {item['issue_code'] for item in precheck['blockers']}
        self.assertIn('contract_not_active', codes)
        with self.assertRaises(ValidationError):
            services.close_project(self.owner, other.pk, request_token=uuid.uuid4(),
                                   expected_version=other.version, dry_run=False)
        other.refresh_from_db()
        self.assertEqual(other.stage, 'execution')
        self.assertEqual(ProjectClose.objects.count(), 0)

    def test_close_and_reopen_restore_stage(self):
        result = self.close_project()
        self.project.refresh_from_db()
        self.assertEqual(self.project.stage, 'closed')
        self.assertEqual(self.project.previous_stage, 'execution')
        runs = list(ProjectClose.objects.order_by('sequence'))
        self.assertEqual([run.state for run in runs], ['closing', 'closed'])
        self.assertTrue(DeliveryReceipt.objects.filter(action='project.close').exists())
        with self.assertRaises(ValidationError):
            services.close_project(self.owner, self.project.pk, request_token=uuid.uuid4(),
                                   expected_version=self.project.version, dry_run=False)
        reopened = services.reopen_project(
            self.owner, self.project.pk, request_token=uuid.uuid4(),
            expected_version=self.project.version, dry_run=False, reason='客户追加需求')
        self.project.refresh_from_db()
        self.assertEqual(self.project.stage, 'execution')
        self.assertEqual(self.project.previous_stage, 'closed')
        self.assertEqual(ProjectClose.objects.order_by('sequence').last().state, 'reopened')
        self.assertEqual(reopened['restore_stage'], 'execution')

    def test_cancelled_project_rejected_and_pm_scope(self):
        cancelled = Project.objects.create(
            organization=self.org, code='P322C', name='取消项目', client='客户',
            manager=self.owner, start_date=self.today, end_date=self.today,
            stage='cancelled')
        with self.assertRaises(ValidationError):
            services.close_project(self.owner, cancelled.pk, request_token=uuid.uuid4(),
                                   expected_version=cancelled.version, dry_run=False)
        with self.assertRaises(PermissionDenied):
            services.project_close_precheck(self.pm, self.project.pk)
        self.assertEqual(services.project_close_precheck(self.owner, self.project.pk)['scope'],
                         'project')

    def test_project_business_fact_counts_dry_run_and_replay(self):
        before = self.facts()
        self.close_project(dry_run=True)
        self.assertEqual(self.facts(), before)
        self.assertEqual(ProjectClose.objects.count(), 0)
        token = uuid.uuid4()
        first = self.close_project(request_token=token)
        replay = self.close_project(request_token=token)
        self.assertTrue(replay['replayed'])
        self.assertEqual(replay['run_ref'], first['run_ref'])
        self.assertEqual(self.facts(), before)
        with self.assertRaises(ValidationError):
            self.close_project(request_token=token, reason='改输入')
        self.project.refresh_from_db()
        services.reopen_project(self.owner, self.project.pk, request_token=uuid.uuid4(),
                                expected_version=self.project.version, dry_run=True,
                                reason='核对')
        self.assertEqual(self.facts(), before)
        services.reopen_project(self.owner, self.project.pk, request_token=uuid.uuid4(),
                                expected_version=self.project.version, dry_run=False,
                                reason='核对')
        self.assertEqual(self.facts(), before)

    def test_project_trigger_guard_and_cancelled_run_probe(self):
        self.close_project()
        run = ProjectClose.objects.first()
        for operation in (
                lambda: ProjectClose.objects.filter(pk=run.pk).update(sequence=99),
                lambda: ProjectClose.objects.filter(pk=run.pk).delete()):
            with transaction.atomic():
                with self.assertRaises(IntegrityError):
                    operation()
        cancelled = Project.objects.create(
            organization=self.org, code='P322R', name='取消有run', client='客户',
            manager=self.owner, start_date=self.today, end_date=self.today,
            stage='cancelled')
        ProjectClose.objects.create(
            organization=self.org, project=cancelled, sequence=1, action='close',
            state='closed', from_stage='execution', from_project_version=cancelled.version,
            blockers=[], warnings=[], precheck_digest='0' * 64,
            actor=self.owner, request_token=uuid.uuid4())
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_integrity', '--json', stdout=output)
        self.assertIn('cancelled_with_run', output.getvalue())

    def test_verify_probe_dangling_project_closing(self):
        self.close_project()
        ProjectClose.objects.create(
            organization=self.org, project=self.project, sequence=99, action='close',
            state='closing', from_stage='execution', from_project_version=self.project.version,
            blockers=[], warnings=[], precheck_digest='0' * 64,
            actor=self.owner, request_token=uuid.uuid4())
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_integrity', '--json', stdout=output)
        self.assertIn('project_close:', output.getvalue())
