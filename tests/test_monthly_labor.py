"""PH217 actual business service chain, immutable sources and zero-cost lifecycle."""
from copy import deepcopy
from datetime import date, timedelta
import uuid
from unittest import mock
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone
from core.models import (Organization, Membership, Employee, Project, Timesheet, NonProjectDestination,
    AccountingEvent, LedgerPosting, SourceConsumption, AuditEvent, DomainEvent, LedgerEntry, PluginState)
from core.labor_cutover import propose_cutover, approve_cutover
from core.services import submit_time, review, reverse
from pmo_plugins.costing import services as s
from pmo_plugins.costing.models import SalaryPool, CostPolicy, AllocationRun, AllocationTarget, CostEvent, LaborReceipt


class MonthlyLaborBusinessTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.first = self.today.replace(day=1)
        self.period = self.first.strftime('%Y-%m')
        self.org = Organization.objects.create(name='演练月池公司')
        self.owner = self.member('owner'); self.finance = self.member('finance')
        self.employee = Employee.objects.create(organization=self.org, name='无账号演练员工')
        self.project = Project.objects.create(organization=self.org, manager=self.owner, code='P1', name='演练项目',
            client='历史文本', start_date=self.first, end_date=self.today)
        self.project2 = Project.objects.create(organization=self.org, manager=self.owner, code='P2', name='另一项目',
            client='历史文本', start_date=self.first, end_date=self.today)
        cutover = propose_cutover(self.owner, self.first, uuid.uuid4())
        approve_cutover(self.finance, cutover.pk, cutover.version, uuid.uuid4())

    def member(self, role):
        user = get_user_model().objects.create_user(username=role + uuid.uuid4().hex)
        return Membership.objects.create(organization=self.org, user=user, role=role)

    def facts(self):
        return tuple(model.objects.count() for model in (SalaryPool, CostPolicy, AllocationRun, AllocationTarget,
            CostEvent, LaborReceipt, AccountingEvent, LedgerPosting, SourceConsumption, AuditEvent, DomainEvent, LedgerEntry))

    def denied(self, call):
        before = self.facts()
        with self.assertRaises((ValidationError, PermissionDenied, TypeError)):
            call()
        self.assertEqual(before, self.facts())

    def save_pool(self, cents=101, **changes):
        values = dict(employee_id=self.employee.pk, period=self.period, valuation_state='reconciled_actual',
            covered_through=self.today, components=[{'component_code': '工资', 'amount_cents': cents}],
            reference='客户财务核实实际发生工资', request_token=uuid.uuid4(), dry_run=False)
        values.update(changes)
        return s.save_salary_pool(self.owner, **values)

    def confirmed(self, cents=101):
        pool = self.save_pool(cents)
        pool = s.submit_salary_pool(self.owner, pool.pk, expected_version=pool.version, request_token=uuid.uuid4(), dry_run=False)
        return s.confirm_salary_pool(self.finance, pool.pk, expected_version=pool.version,
            request_token=uuid.uuid4(), review_note='另一财务独立核实构成和发生依据', dry_run=False)

    def capacity(self, pool, minutes=60):
        pool = s.propose_capacity(self.owner, pool.pk, capacity_minutes=minutes, calendar_basis='核实本覆盖期实际产能',
            expected_version=pool.version, request_token=uuid.uuid4(), dry_run=False)
        pool = s.confirm_capacity(self.finance, pool.pk, expected_version=pool.version, request_token=uuid.uuid4(),
            review_note='独立核实容量', dry_run=False)
        return pool, pool.policies.filter(capacity_confirmed=True).latest('created_at')

    def time(self, minutes, project=None, destination=None):
        data = dict(project=project, employee=self.employee, task=None, work_date=self.today, minutes=minutes, note='实际投入')
        if destination:
            data['non_project_destination'] = destination
        row = submit_time(self.owner, data, uuid.uuid4())
        self.assertIsNone(row.cost_cents)
        return review(self.finance, 'time', row.pk, 'approve')

    def allocate(self, pool, policy, **changes):
        args = dict(capacity_policy_id=policy.pk, expected_version=pool.version, request_token=uuid.uuid4(), dry_run=False)
        args.update(changes)
        return s.allocate_pool(self.owner, pool.pk, **args)

    def test_confirmation_allocation_101_oracle_and_complete_reversal(self):
        t1 = self.time(3, self.project)
        self.time(31, self.project2)
        admin = NonProjectDestination.objects.get(organization=self.org, code='ADMIN')
        self.time(26, destination=admin)
        pool = self.confirmed()
        snapshot = deepcopy(pool.approved_salary_snapshot)
        self.assertEqual(sum(LedgerPosting.objects.values_list('amount_cents', flat=True)), 101)
        self.assertFalse(LedgerEntry.objects.exists())
        self.assertFalse(pool.original_policy.capacity_confirmed)
        pool, policy = self.capacity(pool)
        self.assertEqual(pool.approved_salary_snapshot, snapshot)
        run = self.allocate(pool, policy)
        amounts = {(t.target_kind, t.target_id): t.allocated_cents for t in run.targets.all()}
        self.assertEqual(amounts[('project', str(self.project.pk))], 5)
        self.assertEqual(amounts[('project', str(self.project2.pk))], 52)
        self.assertEqual(amounts[('non_project_pool', str(admin.pk))], 44)
        self.assertEqual(sum(amounts.values()), 101)
        self.assertEqual(sum(LedgerPosting.objects.values_list('amount_cents', flat=True)), 101)
        self.denied(lambda: reverse(self.finance, 'time', t1.pk, '先撤分配'))
        self.assertEqual(s.project_labor_summary(self.owner, self.project.pk)['current_labor_cents'], 5)
        s.reverse_allocation(self.owner, run.pk, request_token=uuid.uuid4(), reason='核对后撤回分配', dry_run=False)
        self.assertEqual(sum(LedgerPosting.objects.values_list('amount_cents', flat=True)), 101)
        pool.refresh_from_db()
        s.reverse_salary_pool(self.finance, pool.pk, expected_version=pool.version, request_token=uuid.uuid4(), reason='确认原来源错误', dry_run=False)
        self.assertEqual(sum(LedgerPosting.objects.values_list('amount_cents', flat=True)), 0)
        pool.refresh_from_db()
        self.assertEqual(pool.approved_salary_snapshot, snapshot)
        self.assertEqual(s.resolve_salary_snapshot(self.finance, pool.pk, approved_version=pool.approved_source_version), snapshot)
        self.denied(lambda: self.save_pool())
        self.assertTrue(all(e.occurred_on == self.today and e.posting_period == self.period for e in AccountingEvent.objects.all()))
        self.assertEqual(DomainEvent.objects.filter(topic='core.posting_reversed.v1').count(), 2)

    def test_default_dry_run_zero_persistence_and_replay_conflict(self):
        before = self.facts()
        result = self.save_pool(dry_run=True)
        self.assertTrue(result['dry_run']); self.assertEqual(before, self.facts())
        token = uuid.uuid4()
        pool = self.save_pool(request_token=token)
        self.assertEqual(self.save_pool(request_token=token).pk, pool.pk)
        self.denied(lambda: self.save_pool(cents=102, request_token=token))
        before = self.facts()
        result = s.submit_salary_pool(self.owner, pool.pk, expected_version=pool.version, request_token=uuid.uuid4())
        self.assertTrue(result['dry_run']); self.assertEqual(before, self.facts())
        pool = s.submit_salary_pool(self.owner, pool.pk, expected_version=pool.version, request_token=uuid.uuid4(), dry_run=False)
        before = self.facts()
        result = s.confirm_salary_pool(self.finance, pool.pk, expected_version=pool.version, request_token=uuid.uuid4(), review_note='核对')
        self.assertTrue(result['dry_run']); self.assertEqual(before, self.facts())

    def test_zero_amount_has_single_active_lifecycle(self):
        pool = self.confirmed(0)
        self.assertEqual(LedgerPosting.objects.count(), 1)
        pool, policy = self.capacity(pool)
        run = self.allocate(pool, policy)
        pool.refresh_from_db()
        self.denied(lambda: self.allocate(pool, policy))
        token = uuid.uuid4()
        s.reverse_allocation(self.owner, run.pk, request_token=token, reason='零额仍须撤销', dry_run=False)
        self.denied(lambda: s.reverse_allocation(self.owner, run.pk, request_token=uuid.uuid4(), reason='不能重复', dry_run=False))
        pool.refresh_from_db()
        run2 = self.allocate(pool, policy)
        self.assertNotEqual(run.pk, run2.pk)
        self.assertEqual(AllocationRun.objects.filter(status='posted').count(), 1)
        s.reverse_allocation(self.owner, run2.pk, request_token=uuid.uuid4(), reason='完整撤销', dry_run=False)
        pool.refresh_from_db()
        s.reverse_salary_pool(self.finance, pool.pk, expected_version=pool.version, request_token=uuid.uuid4(), reason='零额终态反冲', dry_run=False)
        pool.refresh_from_db()
        self.denied(lambda: self.allocate(pool, policy))

    def test_permission_date_module_and_immutable_policy(self):
        pool = self.confirmed()
        for role in ('pm', 'member'):
            actor = self.member(role)
            self.denied(lambda: s.get_labor_detail(actor, pool.pk))
        self.denied(lambda: s.propose_capacity(self.owner, pool.pk, capacity_minutes=0, calendar_basis='无容量',
            expected_version=pool.version, request_token=uuid.uuid4(), dry_run=False))
        self.denied(lambda: self.save_pool(covered_through=self.today + timedelta(days=1)))
        old = self.org.locked_through
        self.org.locked_through = self.first; self.org.save()
        self.denied(lambda: s.propose_capacity(self.owner, pool.pk, capacity_minutes=60, calendar_basis='跨锁覆盖',
            expected_version=pool.version, request_token=uuid.uuid4(), dry_run=False))
        self.org.locked_through = old; self.org.save()
        policy = pool.original_policy; policy.denominator_minutes = 60
        with self.assertRaises(ValidationError): policy.save()
        PluginState.objects.create(organization=self.org, plugin_id='costing', version='0.1.0', enabled=False, changed_by=self.owner.user)
        self.denied(lambda: s.get_salary_pool(self.owner, pool.pk))

    def test_outbox_failure_rolls_back_all_financial_and_business_rows(self):
        pool = self.save_pool()
        pool = s.submit_salary_pool(self.owner, pool.pk, expected_version=pool.version, request_token=uuid.uuid4(), dry_run=False)
        before = self.facts()
        with mock.patch('pmo_plugins.costing.services.DomainEvent.objects.create', side_effect=RuntimeError('event failure')):
            with self.assertRaises(RuntimeError):
                s.confirm_salary_pool(self.finance, pool.pk, expected_version=pool.version, request_token=uuid.uuid4(), review_note='核对', dry_run=False)
        self.assertEqual(before, self.facts())
        pool.refresh_from_db(); self.assertFalse(pool.recognition_event_id)

    def test_frozen_time_or_policy_forgery_refuses_allocation(self):
        row = self.time(3, self.project)
        pool, policy = self.capacity(self.confirmed())
        from django.db import transaction, DatabaseError
        other_employee = Employee.objects.create(organization=self.org, name='另一个员工')
        for field, changed in [('minutes', 4), ('project_id', self.project2.pk), ('employee_id', other_employee.pk)]:
            row.refresh_from_db(); original = getattr(row, field)
            try:
                with transaction.atomic():
                    Timesheet.objects.filter(pk=row.pk).update(**{field: changed})
            except DatabaseError:
                # PostgreSQL immutable source guard is stronger than service detection.
                row.refresh_from_db(); self.assertEqual(getattr(row, field), original)
            else:
                self.denied(lambda: self.allocate(pool, policy))
                Timesheet.objects.filter(pk=row.pk).update(**{field: original})
        other = self.member('finance')
        # A stored policy pointer cannot silently reinterpret the approved salary source.
        wrong_policy = CostPolicy.objects.create(organization=self.org, employee=self.employee, salary_pool=pool,
            period=self.period, covered_through=self.today, component_snapshot=[], calendar_digest='0'*64,
            approved_by=other, approved_at=timezone.now(), approval_audit=pool.approval_audit)
        try:
            with transaction.atomic():
                SalaryPool.objects.filter(pk=pool.pk).update(original_policy=wrong_policy)
        except DatabaseError:
            pool.refresh_from_db(); self.assertNotEqual(pool.original_policy_id, wrong_policy.pk)
        else:
            self.denied(lambda: self.allocate(pool, policy))

    def test_all_later_default_dry_runs_leave_exactly_no_rows(self):
        pool = self.confirmed()
        before = self.facts()
        result = s.propose_capacity(self.owner, pool.pk, capacity_minutes=60, calendar_basis='已核对容量',
            expected_version=pool.version, request_token=uuid.uuid4())
        self.assertTrue(result['dry_run']); self.assertEqual(before, self.facts())
        pool = s.propose_capacity(self.owner, pool.pk, capacity_minutes=60, calendar_basis='已核对容量',
            expected_version=pool.version, request_token=uuid.uuid4(), dry_run=False)
        before = self.facts()
        result = s.confirm_capacity(self.finance, pool.pk, expected_version=pool.version, request_token=uuid.uuid4(), review_note='独立确认')
        self.assertTrue(result['dry_run']); self.assertEqual(before, self.facts())
        pool = s.confirm_capacity(self.finance, pool.pk, expected_version=pool.version, request_token=uuid.uuid4(), review_note='独立确认', dry_run=False)
        policy = pool.policies.filter(capacity_confirmed=True).get()
        before = self.facts()
        result = s.allocate_pool(self.owner, pool.pk, capacity_policy_id=policy.pk,
            expected_version=pool.version, request_token=uuid.uuid4())
        self.assertTrue(result['dry_run']); self.assertEqual(before, self.facts())
        run = self.allocate(pool, policy)
        before = self.facts()
        result = s.reverse_allocation(self.owner, run.pk, request_token=uuid.uuid4(), reason='预检撤销')
        self.assertTrue(result['dry_run']); self.assertEqual(before, self.facts())
        s.reverse_allocation(self.owner, run.pk, request_token=uuid.uuid4(), reason='明确撤销', dry_run=False)
        pool.refresh_from_db(); before = self.facts()
        result = s.reverse_salary_pool(self.finance, pool.pk, expected_version=pool.version,
            request_token=uuid.uuid4(), reason='预检终态冲销')
        self.assertTrue(result['dry_run']); self.assertEqual(before, self.facts())

    def test_actual_bound_beneficiary_and_creator_cannot_self_confirm(self):
        pool = self.save_pool()
        pool = s.submit_salary_pool(self.owner, pool.pk, expected_version=pool.version, request_token=uuid.uuid4(), dry_run=False)
        self.denied(lambda: s.confirm_salary_pool(self.owner, pool.pk, expected_version=pool.version,
            request_token=uuid.uuid4(), review_note='制单自审', dry_run=False))
        Employee.objects.filter(pk=self.employee.pk).update(member=self.finance)
        self.denied(lambda: s.confirm_salary_pool(self.finance, pool.pk, expected_version=pool.version,
            request_token=uuid.uuid4(), review_note='本人自审', dry_run=False))
        Employee.objects.filter(pk=self.employee.pk).update(member=None)
        confirmed = s.confirm_salary_pool(self.finance, pool.pk, expected_version=pool.version,
            request_token=uuid.uuid4(), review_note='无账号员工独立确认', dry_run=False)
        self.assertIsNotNone(confirmed.recognition_event_id)

    def test_two_people_can_alternate_salary_and_capacity_independence(self):
        pool = self.confirmed()
        pool = s.propose_capacity(self.finance, pool.pk, capacity_minutes=60, calendar_basis='B核实产能',
            expected_version=pool.version, request_token=uuid.uuid4(), dry_run=False)
        self.denied(lambda: s.confirm_capacity(self.finance, pool.pk, expected_version=pool.version,
            request_token=uuid.uuid4(), review_note='B不能自审', dry_run=False))
        pool = s.confirm_capacity(self.owner, pool.pk, expected_version=pool.version,
            request_token=uuid.uuid4(), review_note='A核实B提出的容量', dry_run=False)
        self.assertTrue(pool.policies.filter(capacity_confirmed=True, approved_by=self.owner).exists())
