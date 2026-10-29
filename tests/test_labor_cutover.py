"""Real HTTP boundaries for the monthly labor browser workflow."""
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from core.models import (Organization, Membership, Employee, Project, ProjectMember,
                         LaborCutover, NonProjectDestination, Timesheet, LedgerEntry)
from pmo_plugins.costing.models import SalaryPool


class LaborBrowserTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.org = Organization.objects.create(name='人工HTTP演练公司')
        self.members = {}
        for role in ('owner', 'finance', 'auditor', 'pm', 'member'):
            user = get_user_model().objects.create_user('labor-http-' + role)
            self.members[role] = Membership.objects.create(organization=self.org, user=user, role=role)
        self.employee = Employee.objects.create(organization=self.org, member=self.members['member'], name='演练员工')
        self.project = Project.objects.create(organization=self.org, manager=self.members['pm'], code='LH1', name='人工演练项目', start_date=self.today, end_date=self.today)
        ProjectMember.objects.create(project=self.project, member=self.members['member'])
        self.secret = 'PRIVATE_SALARY_HTTP_EVIDENCE_482193'

    def login(self, role):
        self.client.force_login(self.members[role].user)

    def action(self, role, name, pool=None, **data):
        self.login(role)
        endpoint = '/modules/costing/labor-action/?action=' + name
        if pool:
            endpoint += '&id=' + str(pool.pk)
        return self.client.post(endpoint, {'request_token': str(uuid.uuid4()), **data})

    def enable(self):
        response = self.action('owner', 'cutover', effective_from=self.today.replace(day=1).isoformat())
        self.assertEqual(response.status_code, 302)
        cutover = LaborCutover.objects.get(organization=self.org)
        self.assertEqual(cutover.status, 'submitted')
        self.assertEqual(self.action('owner', 'approve_cutover', expected_version=cutover.version).status_code, 400)
        self.assertEqual(self.action('finance', 'approve_cutover', expected_version=cutover.version).status_code, 302)
        cutover.refresh_from_db()
        self.assertEqual(cutover.status, 'approved')
        return cutover

    def salary_data(self, **changes):
        data = {'employee': self.employee.pk, 'period': self.today.strftime('%Y-%m'),
                'covered_through': self.today.isoformat(), 'valuation_state': 'incurred_provisional',
                'source_evidence': self.secret, 'request_token': str(uuid.uuid4()),
                'components-TOTAL_FORMS': '1', 'components-INITIAL_FORMS': '0',
                'components-MIN_NUM_FORMS': '1', 'components-MAX_NUM_FORMS': '30',
                'components-0-code': '演练工资', 'components-0-amount': '1234.56'}
        data.update(changes)
        return data

    def pool(self):
        self.enable()
        self.login('owner')
        self.assertEqual(self.client.post('/modules/costing/labor-save/', self.salary_data()).status_code, 302)
        return SalaryPool.objects.get(organization=self.org)

    def test_five_roles_sensitive_detail_scope_and_cross_company_uuid(self):
        pool = self.pool()
        for role in self.members:
            self.login(role)
            response = self.client.get('/modules/costing/labor-detail/', {'id': str(pool.pk)})
            if role in {'owner', 'finance', 'auditor'}:
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, self.secret)
            else:
                self.assertEqual(response.status_code, 403)
                self.assertNotIn(self.secret.encode(), response.content)
                self.assertIsNone(response.context.get('pool'))
        other = Organization.objects.create(name='隔离公司')
        foreign = Membership.objects.create(organization=other, user=get_user_model().objects.create_user('foreign-labor-owner'), role='owner')
        self.client.force_login(foreign.user)
        self.assertEqual(self.client.get('/modules/costing/labor-detail/', {'id': str(pool.pk)}).status_code, 403)
        self.assertEqual(self.client.get('/modules/costing/labor-form/', {'id': str(pool.pk), 'action': 'save'}).status_code, 403)
        response = self.client.post('/modules/costing/labor-save/', self.salary_data(pool_id=str(pool.pk), expected_version=pool.version))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(SalaryPool.objects.count(), 1)

    def test_form_errors_preserve_components_and_reject_extra_amount_fields(self):
        self.enable()
        self.login('owner')
        response = self.client.post('/modules/costing/labor-save/', self.salary_data(**{'components-0-amount': '-1'}))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, self.secret, status_code=400)
        self.assertContains(response, '演练工资', status_code=400)
        self.assertContains(response, 'value="-1"', status_code=400)
        self.assertFalse(SalaryPool.objects.exists())
        response = self.client.post('/modules/costing/labor-save/', self.salary_data(total_cents='1'))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SalaryPool.objects.exists())
        for role in ('auditor', 'pm', 'member'):
            self.login(role)
            self.assertEqual(self.client.post('/modules/costing/labor-save/', self.salary_data()).status_code, 403)

    def test_http_confirm_capacity_allocate_trace_unallocate(self):
        pool = self.pool()
        self.assertEqual(pool.total_comprehensive_cost_cents, 123456)
        self.assertEqual(self.action('owner', 'submit', pool, expected_version=pool.version).status_code, 302)
        pool.refresh_from_db()
        self.assertEqual(self.action('owner', 'confirm', pool, expected_version=pool.version, review_note='本人不应审批').status_code, 400)
        self.assertEqual(self.action('finance', 'confirm', pool, expected_version=pool.version, review_note='独立核对构成和已发生依据').status_code, 302)
        pool.refresh_from_db()
        self.assertIsNotNone(pool.recognition_event_id)
        self.assertEqual(self.action('owner', 'capacity', pool, expected_version=pool.version, denominator_minutes='60', evidence='已核实覆盖有效产能').status_code, 302)
        pool.refresh_from_db()
        self.assertEqual(self.action('finance', 'confirm_capacity', pool, expected_version=pool.version, review_note='核实有效产能').status_code, 302)
        pool.refresh_from_db()
        self.login('member')
        response = self.client.post('/new/time/', {'employee': self.employee.pk, 'work_date': self.today.isoformat(),
            'hours': '0.5', 'note': '可追溯的真实投入', 'project': self.project.pk, 'submission_token': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        timesheet = Timesheet.objects.get(employee=self.employee)
        self.login('finance')
        response = self.client.post(f'/review/time/{timesheet.pk}/', {'decision': 'approve', 'note': '独立核实投入'})
        self.assertEqual(response.status_code, 302)
        policy = pool.policies.get(capacity_confirmed=True)
        response = self.action('finance', 'allocate', pool, expected_version=pool.version, capacity_policy_id=str(policy.pk))
        self.assertEqual(response.status_code, 302)
        run = pool.allocation_runs.get(status='posted')
        self.login('finance')
        response = self.client.get('/modules/costing/labor-detail/', {'id': str(pool.pk)})
        self.assertContains(response, str(run.accounting_event_id))
        self.assertContains(response, '1234.56')
        self.assertContains(response, f'原始投入 #{timesheet.pk}')
        self.assertContains(response, '批准版本')
        self.assertContains(response, self.today.isoformat())
        self.assertContains(response, '独立确认记录 #')
        self.assertNotContains(response, '&#x27;evidence_id&#x27;')
        self.assertNotContains(response, '&#x27;namespace&#x27;')
        self.assertEqual(self.action('finance', 'unallocate', pool, expected_version=pool.version, reason='演练更正投入', confirmed='on').status_code, 302)
        run.refresh_from_db()
        self.assertNotEqual(run.status, 'posted')

    def test_cutover_http_project_and_nonproject_use_same_timesheet(self):
        self.enable()
        self.login('member')
        common = {'employee': self.employee.pk, 'work_date': self.today.isoformat(), 'hours': '0.5', 'note': '演练真实投入'}
        response = self.client.post('/new/time/', {**common, 'project': self.project.pk, 'submission_token': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        destination = NonProjectDestination.objects.get(organization=self.org, code='ADMIN')
        response = self.client.post('/new/time/', {**common, 'non_project_destination': str(destination.pk), 'submission_token': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Timesheet.objects.filter(employee=self.employee, accounting_mode='monthly_pool').count(), 2)
        self.assertTrue(all(t.cost_cents is None for t in Timesheet.objects.all()))
        self.assertFalse(LedgerEntry.objects.exists())

    def test_invalid_period_preserved_and_missing_cutover_approval_is_normal_error(self):
        self.login('owner')
        response = self.client.get('/modules/costing/labor-list/', {'period': 'not-a-month'})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'value="not-a-month"', status_code=400)
        self.assertFalse(LaborCutover.objects.exists())
        response = self.action('finance', 'approve_cutover', expected_version='1')
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '公司尚未提出人工启用安排', status_code=400)
        self.assertFalse(LaborCutover.objects.exists())

    def test_org_form_cannot_lock_inside_partially_unallocated_coverage(self):
        from core.forms import OrgForm
        from core.services import submit_time, review
        from core.models import LedgerPosting
        from pmo_plugins.costing import services
        pool = self.pool()
        pool = services.submit_salary_pool(self.members['owner'], pool.pk, expected_version=pool.version, request_token=uuid.uuid4(), dry_run=False)
        pool = services.confirm_salary_pool(self.members['finance'], pool.pk, expected_version=pool.version, request_token=uuid.uuid4(), review_note='核对已发生工资', dry_run=False)
        row = submit_time(self.members['member'], {'project': self.project, 'employee': self.employee,
            'work_date': self.today, 'minutes': 30, 'note': '真实已批投入'}, uuid.uuid4())
        review(self.members['finance'], 'time', row.pk, 'approve', '核对投入')
        pool = services.propose_capacity(self.members['owner'], pool.pk, capacity_minutes=60, calendar_basis='核实覆盖期产能', expected_version=pool.version, request_token=uuid.uuid4(), dry_run=False)
        pool = services.confirm_capacity(self.members['finance'], pool.pk, expected_version=pool.version, request_token=uuid.uuid4(), review_note='独立确认产能', dry_run=False)
        policy = pool.policies.get(capacity_confirmed=True)
        run = services.allocate_pool(self.members['finance'], pool.pk, capacity_policy_id=policy.pk,
            expected_version=pool.version, request_token=uuid.uuid4(), dry_run=False)
        self.assertEqual(run.status, 'posted')
        unallocated = NonProjectDestination.objects.get(organization=self.org, code='UNALLOCATED')
        self.assertTrue(LedgerPosting.objects.filter(accounting_event=run.accounting_event,
            scope_id=str(unallocated.pk), amount_cents__gt=0).exists())
        cutoff = self.today.replace(day=1)
        data = {'name': self.org.name, 'locked_through': cutoff.isoformat()}
        form = OrgForm(data, instance=self.org)
        self.assertFalse(form.is_valid())
        self.assertIn('未分配人工', str(form.errors))
        self.login('owner')
        response = self.client.post('/settings/', data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '未分配人工')
        self.org.refresh_from_db()
        self.assertIsNone(self.org.locked_through)
