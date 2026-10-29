"""Monthly routing through the existing personal API, without new financial tools."""
import uuid
from django.test import TestCase
from django.contrib.auth import get_user_model
from core.models import Membership, Timesheet, LedgerEntry
from core.labor_cutover import propose_cutover, approve_cutover
from core.services import review
from core.plugin_runtime import set_enabled
from tests import test_agent_api as legacy
from tests import test_labor_cutover as browser
from tests import test_monthly_labor as business


class MonthlyTimeApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        legacy.PersonalAgentApiTests.setUpTestData.__func__(cls)
        user = get_user_model().objects.create_user('monthly-api-finance')
        cls.finance = Membership.objects.create(organization=cls.org, user=user, role='finance')
        row = propose_cutover(cls.owner, cls.day.replace(day=1), uuid.uuid4())
        approve_cutover(cls.finance, row.pk, row.version, uuid.uuid4())

    setUp = legacy.PersonalAgentApiTests.setUp
    post = legacy.PersonalAgentApiTests.post
    time_payload = legacy.PersonalAgentApiTests.time_payload

    def test_api_and_browser_share_monthly_time_without_legacy_cost(self):
        response = self.post('/api/v1/timesheets', self.time_payload())
        self.assertEqual(response.status_code, 202)
        row = Timesheet.objects.get()
        self.assertEqual(row.accounting_mode, 'monthly_pool')
        self.assertIsNone(row.hourly_rate_cents)
        self.assertIsNone(row.cost_cents)
        review(self.finance, 'time', row.pk, 'approve')
        self.assertFalse(LedgerEntry.objects.exists())
        self.assertNotIn('cost_cents', response.content.decode())

    def test_disabled_monthly_module_cannot_fall_back_to_legacy_rate(self):
        set_enabled(self.owner, 'costing', False)
        self.assertEqual(self.post('/api/v1/timesheets', self.time_payload()).status_code, 403)
        self.assertFalse(Timesheet.objects.exists())

    def test_api_refuses_nonproject_and_accounting_mode_override(self):
        for extra in ({'non_project_destination_id': str(uuid.uuid4())}, {'accounting_mode': 'legacy_snapshot'}, {'minutes': True}):
            self.assertEqual(self.post('/api/v1/timesheets', self.time_payload(**extra), key=str(uuid.uuid4())).status_code, 400)
        self.assertFalse(Timesheet.objects.exists())


class CutoverSourceAndRoleTests(TestCase):
    setUp = browser.LaborBrowserTests.setUp

    def test_reversed_legacy_cost_still_blocks_cutover(self):
        from core.models import CostRate, AuditEvent, LaborCutover
        from core.services import submit_time, reverse
        from django.core.exceptions import ValidationError
        CostRate.objects.create(employee=self.employee, effective_from=self.today.replace(day=1), hourly_cents=100, basis='旧行快照')
        row = submit_time(self.members['owner'], {'project': self.project, 'employee': self.employee,
            'work_date': self.today, 'minutes': 60, 'note': '旧真实工时'}, uuid.uuid4())
        review(self.members['finance'], 'time', row.pk, 'approve')
        reverse(self.members['finance'], 'time', row.pk, '原分完整冲销')
        count = AuditEvent.objects.count()
        with self.assertRaises(ValidationError):
            propose_cutover(self.members['owner'], self.today.replace(day=1), uuid.uuid4())
        self.assertFalse(LaborCutover.objects.exists())
        self.assertEqual(AuditEvent.objects.count(), count)
        self.assertEqual(sum(LedgerEntry.objects.values_list('amount_cents', flat=True)), 0)

    def test_pm_cannot_use_nonproject_to_record_someone_else(self):
        from core.models import AuditEvent, NonProjectDestination
        from core.services import submit_time
        from django.core.exceptions import PermissionDenied
        row = propose_cutover(self.members['owner'], self.today.replace(day=1), uuid.uuid4())
        approve_cutover(self.members['finance'], row.pk, row.version, uuid.uuid4())
        count = AuditEvent.objects.count()
        with self.assertRaises(PermissionDenied):
            submit_time(self.members['pm'], {'project': None,
                'non_project_destination': NonProjectDestination.objects.get(organization=self.org, code='ADMIN'),
                'employee': self.employee, 'work_date': self.today, 'minutes': 60, 'note': '越权代录'}, uuid.uuid4())
        self.assertFalse(Timesheet.objects.exists())
        self.assertEqual(AuditEvent.objects.count(), count)
        self.assertFalse(LedgerEntry.objects.exists())


class MonthlyLedgerAndListTests(TestCase):
    setUp = business.MonthlyLaborBusinessTests.setUp
    member = business.MonthlyLaborBusinessTests.member
    save_pool = business.MonthlyLaborBusinessTests.save_pool
    confirmed = business.MonthlyLaborBusinessTests.confirmed
    capacity = business.MonthlyLaborBusinessTests.capacity
    time = business.MonthlyLaborBusinessTests.time
    allocate = business.MonthlyLaborBusinessTests.allocate

    def test_ledger_csv_contains_current_allocation_and_exact_reversal_sources(self):
        import csv, io
        from pmo_plugins.costing import services
        self.time(60, self.project)
        pool, policy = self.capacity(self.confirmed())
        run = self.allocate(pool, policy)
        self.client.force_login(self.owner.user)
        response = self.client.get('/export/ledger/', {'project': self.project.pk})
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(sum(int(row[5]) for row in rows[1:]), 101)
        self.assertEqual(rows[1][9], 'current')
        self.assertEqual(rows[1][10:13], [str(run.accounting_event_id), str(pool.pk), str(run.pk)])
        services.reverse_allocation(self.owner, run.pk, request_token=uuid.uuid4(), reason='导出原分追溯核对', dry_run=False)
        response = self.client.get('/export/ledger/', {'project': self.project.pk})
        rows = list(csv.reader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(sum(int(row[5]) for row in rows[1:]), 0)
        self.assertTrue(any(row[13] == str(run.accounting_event_id) and row[8] for row in rows[1:]))

    def test_pm_sees_own_nonproject_input_but_not_other_employee_input(self):
        from core.models import Employee, NonProjectDestination
        from core.services import submit_time
        pm = self.member('pm')
        employee = Employee.objects.create(organization=self.org, member=pm, name='项目经理本人')
        admin = NonProjectDestination.objects.get(organization=self.org, code='ADMIN')
        row = submit_time(pm, {'project': None, 'non_project_destination': admin, 'employee': employee,
            'work_date': self.today, 'minutes': 30, 'note': '本人非项目投入'}, uuid.uuid4())
        other = submit_time(self.owner, {'project': None, 'non_project_destination': admin, 'employee': self.employee,
            'work_date': self.today, 'minutes': 10, 'note': '其他员工的非项目投入'}, uuid.uuid4())
        self.client.force_login(pm.user)
        response = self.client.get('/time/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item.pk for item in response.context['timesheets']], [row.pk])
        review(self.finance, 'time', row.pk, 'approve')
        self.assertContains(self.client.get('/time/'), '本人非项目投入')
        self.assertNotContains(self.client.get('/time/'), other.note)

