import json
import uuid
from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from core.masterdata import (
    create_employee,
    create_rate,
    update_employee,
    update_project,
)
from core.masterdata_views import masterdata_context
from core.models import (
    AuditEvent,
    CostRate,
    Employee,
    Membership,
    Milestone,
    Organization,
    Project,
    ProjectMember,
    Task,
    Timesheet,
)
from core.services import submit_time


class MasterDataTestCase(TestCase):
    """Business boundaries for the single master-data maintenance service."""

    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.org = Organization.objects.create(name="基础资料公司")
        cls.other_org = Organization.objects.create(name="其他基础资料公司")

        def account(username, org, role):
            user = users.objects.create_user(username, password="test-pass")
            return Membership.objects.create(organization=org, user=user, role=role)

        cls.owner = account("md-owner", cls.org, Membership.Role.OWNER)
        cls.finance = account("md-finance", cls.org, Membership.Role.FINANCE)
        cls.pm = account("md-pm", cls.org, Membership.Role.PM)
        cls.pm2 = account("md-pm2", cls.org, Membership.Role.PM)
        cls.member = account("md-member", cls.org, Membership.Role.MEMBER)
        cls.auditor = account("md-auditor", cls.org, Membership.Role.AUDITOR)
        cls.other_owner = account("md-other-owner", cls.other_org, Membership.Role.OWNER)
        cls.other_pm = account("md-other-pm", cls.other_org, Membership.Role.PM)

        cls.employee = Employee.objects.create(
            organization=cls.org, name="在职员工", title="工程师", member=cls.member
        )
        cls.pm_employee = Employee.objects.create(
            organization=cls.org, name="项目经理员工", title="项目经理", member=cls.pm
        )
        cls.other_employee = Employee.objects.create(
            organization=cls.other_org, name="外公司员工", member=cls.other_owner
        )
        cls.rate = CostRate.objects.create(
            employee=cls.employee,
            effective_from=date(2024, 1, 1),
            hourly_cents=12_000,
            basis="2024年度已确认费率",
        )
        cls.other_rate = CostRate.objects.create(
            employee=cls.other_employee,
            effective_from=date(2024, 1, 1),
            hourly_cents=99_999,
            basis="其他公司私有费率",
        )

        cls.project = Project.objects.create(
            organization=cls.org,
            code="MD-001",
            name="基础资料项目",
            client="原客户",
            manager=cls.pm,
            stage=Project.Stage.PLANNING,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            budget_cents=500_000,
            contract_cents=800_000,
            etc_cents=120_000,
            scope="原范围",
            acceptance_criteria="原验收标准",
            contract_reference="MD-CONTRACT",
        )
        ProjectMember.objects.create(project=cls.project, member=cls.pm)
        ProjectMember.objects.create(project=cls.project, member=cls.member)
        cls.hidden_project = Project.objects.create(
            organization=cls.org,
            code="MD-002",
            name="未参与项目",
            client="隐藏客户",
            manager=cls.pm2,
            stage=Project.Stage.PLANNING,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        cls.other_project = Project.objects.create(
            organization=cls.other_org,
            code="OTHER-MD-001",
            name="外公司项目",
            client="外公司客户",
            manager=cls.other_pm,
            stage=Project.Stage.PLANNING,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
        )
        cls.task = Task.objects.create(
            project=cls.project,
            name="既有任务",
            owner=cls.pm,
            start_date=date(2025, 2, 1),
            due_date=date(2025, 2, 10),
            estimate_minutes=480,
        )
        cls.milestone = Milestone.objects.create(
            project=cls.project, name="既有里程碑", due_date=date(2025, 3, 1), criteria="原标准"
        )

    def employee_data(self, **overrides):
        data = {
            "name": "员工资料",
            "title": "顾问",
            "member": self.auditor,
            "capacity_hours": Decimal("8"),
            "active": True,
        }
        data.update(overrides)
        return data

    def project_data(self, **overrides):
        data = {
            "name": self.project.name,
            "client": self.project.client,
            "manager": self.project.manager,
            "start_date": self.project.start_date,
            "end_date": self.project.end_date,
            "scope": self.project.scope,
            "acceptance_criteria": self.project.acceptance_criteria,
            "contract_reference": self.project.contract_reference,
            "etc_amount": Decimal("1200.00"),
        }
        data.update(overrides)
        return data

    def time_data(self, **overrides):
        data = {
            "project": self.project,
            "employee": self.employee,
            "task": None,
            "work_date": date(2025, 1, 10),
            "minutes": 480,
            "note": "",
        }
        data.update(overrides)
        return data

    def test_create_and_update_employee_converts_capacity_to_whole_minutes(self):
        employee = create_employee(
            self.owner, self.employee_data(capacity_hours=Decimal("7.5"))
        )
        self.assertEqual(employee.organization_id, self.org.pk)
        self.assertEqual(employee.capacity_minutes, 450)
        self.assertEqual(employee.member_id, self.auditor.pk)

        updated = update_employee(
            self.owner,
            employee.pk,
            self.employee_data(name="更新员工", capacity_hours=Decimal("8.25")),
        )
        self.assertEqual(updated.pk, employee.pk)
        self.assertEqual(updated.name, "更新员工")
        self.assertEqual(updated.capacity_minutes, 495)
        self.assertTrue(Employee.objects.filter(pk=employee.pk).exists())

    def test_capacity_must_be_whole_minutes_within_one_day(self):
        before = Employee.objects.count()
        for index, hours in enumerate(
            [Decimal("0"), Decimal("-1"), Decimal("24.01"), Decimal("0.001")]
        ):
            with self.assertRaises(ValidationError):
                create_employee(
                    self.owner,
                    self.employee_data(
                        name=f"无效容量{index}",
                        member=None,
                        capacity_hours=hours,
                    ),
                )
        self.assertEqual(Employee.objects.count(), before)

        full_day = create_employee(
            self.owner,
            self.employee_data(
                name="全天容量", member=None, capacity_hours=Decimal("24")
            ),
        )
        self.assertEqual(full_day.capacity_minutes, 1440)

    def test_employee_writes_require_owner_or_finance(self):
        before = Employee.objects.count()
        for actor in (self.pm, self.member, self.auditor):
            with self.assertRaises(PermissionDenied):
                create_employee(actor, self.employee_data(name="越权员工"))
        self.assertEqual(Employee.objects.count(), before)
        created = create_employee(
            self.finance, self.employee_data(name="财务添加员工", member=None)
        )
        self.assertEqual(created.organization_id, self.org.pk)

    def test_employee_account_must_be_same_company_and_unique(self):
        with self.assertRaises(ValidationError):
            create_employee(
                self.owner, self.employee_data(name="重复账户", member=self.member)
            )
        with self.assertRaises(ValidationError):
            create_employee(
                self.owner,
                self.employee_data(name="跨公司账户", member=self.other_owner),
            )

    def test_update_employee_rejects_cross_tenant_and_is_audited(self):
        data = self.employee_data(
            name="审计后姓名", member=self.member, capacity_hours=Decimal("8")
        )
        with self.assertRaises((ValidationError, PermissionDenied)):
            update_employee(self.owner, self.other_employee.pk, data)
        for actor in (self.pm, self.member, self.auditor):
            with self.assertRaises(PermissionDenied):
                update_employee(actor, self.employee.pk, data)

        update_employee(self.owner, self.employee.pk, data)
        event = (
            AuditEvent.objects.filter(
                organization=self.org,
                entity="Employee",
                entity_id=str(self.employee.pk),
            )
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(event)
        self.assertEqual(event.actor_id, self.owner.user_id)
        payload = json.dumps(event.payload, ensure_ascii=False, default=str)
        self.assertIn("审计后姓名", payload)

    def test_disabled_employee_is_rejected_for_new_time_and_rates(self):
        submit_time(self.member, self.time_data(), uuid.uuid4())
        snapshot = Timesheet.objects.get(employee=self.employee)
        original = (snapshot.hourly_rate_cents, snapshot.cost_cents, snapshot.rate_basis)

        employee = Employee.objects.get(pk=self.employee.pk)
        update_employee(
            self.owner,
            employee.pk,
            self.employee_data(
                name=employee.name,
                title=employee.title,
                member=self.member,
                capacity_hours=Decimal("8"),
                active=False,
            ),
        )
        employee.refresh_from_db()
        self.assertFalse(employee.active)

        with self.assertRaises(ValidationError):
            create_rate(
                self.owner,
                {
                    "employee": employee,
                    "effective_from": date(2025, 1, 1),
                    "hourly_amount": Decimal("150.00"),
                    "basis": "停用后新费率",
                },
            )
        with self.assertRaises(ValidationError):
            submit_time(self.member, self.time_data(), uuid.uuid4())

        snapshot.refresh_from_db()
        self.assertEqual(
            (snapshot.hourly_rate_cents, snapshot.cost_cents, snapshot.rate_basis),
            original,
        )
        self.assertTrue(Employee.objects.filter(pk=employee.pk).exists())

    def test_create_rate_stores_integer_cents_and_rejects_duplicate_date(self):
        before = CostRate.objects.count()
        rate = create_rate(
            self.finance,
            {
                "employee": self.pm_employee,
                "effective_from": date(2025, 1, 1),
                "hourly_amount": Decimal("123.45"),
                "basis": "2025年度确认费率",
            },
        )
        self.assertEqual(rate.employee_id, self.pm_employee.pk)
        self.assertEqual(rate.hourly_cents, 12_345)
        self.assertEqual(rate.basis, "2025年度确认费率")

        with self.assertRaises(ValidationError):
            create_rate(
                self.owner,
                {
                    "employee": self.pm_employee,
                    "effective_from": date(2025, 1, 1),
                    "hourly_amount": Decimal("200.00"),
                    "basis": "同日覆盖",
                },
            )
        self.assertEqual(CostRate.objects.count(), before + 1)

    def test_rate_writes_require_owner_or_finance_and_same_company(self):
        data = {
            "employee": self.pm_employee,
            "effective_from": date(2025, 2, 1),
            "hourly_amount": Decimal("100.00"),
            "basis": "权限测试",
        }
        for actor in (self.pm, self.member, self.auditor):
            with self.assertRaises(PermissionDenied):
                create_rate(actor, data)
        with self.assertRaises((ValidationError, PermissionDenied)):
            create_rate(self.owner, {**data, "employee": self.other_employee})
        for amount in (Decimal("-1.00"), Decimal("1.001")):
            with self.assertRaises(ValidationError):
                create_rate(self.finance, {**data, "hourly_amount": amount})
        self.assertFalse(
            CostRate.objects.filter(
                employee=self.pm_employee, effective_from=date(2025, 2, 1)
            ).exists()
        )

    def test_rate_maintenance_does_not_rewrite_existing_time_snapshot(self):
        submit_time(self.member, self.time_data(), uuid.uuid4())
        snapshot = Timesheet.objects.get(employee=self.employee)
        before = (snapshot.hourly_rate_cents, snapshot.cost_cents, snapshot.rate_basis)

        create_rate(
            self.owner,
            {
                "employee": self.employee,
                "effective_from": date(2024, 6, 1),
                "hourly_amount": Decimal("999.99"),
                "basis": "覆盖工时日前的新费率",
            },
        )
        snapshot.refresh_from_db()
        self.assertEqual(
            (snapshot.hourly_rate_cents, snapshot.cost_cents, snapshot.rate_basis),
            before,
        )
        later = submit_time(
            self.member,
            self.time_data(work_date=date(2024, 12, 1)),
            uuid.uuid4(),
        )
        self.assertEqual(later.hourly_rate_cents, 99_999)
        self.assertEqual(later.rate_basis, "覆盖工时日前的新费率")
        self.assertTrue(CostRate.objects.filter(pk=self.rate.pk).exists())

    def test_project_update_requires_owner_or_managing_pm(self):
        for actor in (self.finance, self.member, self.auditor, self.pm2):
            with self.assertRaises(PermissionDenied):
                update_project(
                    actor,
                    self.project.pk,
                    {"name": "越权改名"},
                    self.project.version,
                    "权限边界测试",
                )
        with self.assertRaises((PermissionDenied, ValidationError)):
            update_project(
                self.other_pm,
                self.project.pk,
                {"name": "跨公司改名"},
                self.project.version,
                "跨公司权限测试",
            )
        updated = update_project(
            self.owner,
            self.project.pk,
            self.project_data(name="负责人改名", etc_amount=Decimal("1500.50")),
            self.project.version,
            "负责人维护项目资料",
        )
        self.assertEqual(updated.name, "负责人改名")
        self.assertEqual(updated.etc_cents, 150_050)
        self.assertEqual(updated.version, self.project.version + 1)
        project = Project.objects.get(pk=self.project.pk)
        self.assertEqual(
            update_project(
                self.pm,
                project.pk,
                {"client": "项目经理改名客户"},
                project.version,
                "项目经理维护本项目资料",
            ).client,
            "项目经理改名客户",
        )

    def test_project_update_rejects_stale_version_and_blank_reason(self):
        project = Project.objects.get(pk=self.project.pk)
        with self.assertRaises(ValidationError):
            update_project(
                self.owner,
                project.pk,
                {"name": "过期版本"},
                project.version + 1,
                "版本已过期",
            )
        for reason in ("", "   "):
            with self.assertRaises(ValidationError):
                update_project(
                    self.owner,
                    project.pk,
                    {"name": "无依据修改"},
                    project.version,
                    reason,
                )
        project.refresh_from_db()
        self.assertEqual(project.name, "基础资料项目")
        self.assertEqual(project.version, self.project.version)

    def test_project_manager_must_be_active_owner_or_pm_and_pm_cannot_transfer(self):
        for manager in (self.other_pm, self.member, self.finance):
            with self.assertRaises((PermissionDenied, ValidationError)):
                update_project(
                    self.owner,
                    self.project.pk,
                    {"manager": manager},
                    self.project.version,
                    "无效负责人",
                )
        with self.assertRaises((PermissionDenied, ValidationError)):
            update_project(
                self.pm,
                self.project.pk,
                {"manager": self.pm2},
                self.project.version,
                "项目经理尝试转交",
            )
        project = Project.objects.get(pk=self.project.pk)
        self.assertEqual(project.manager_id, self.pm.pk)

        inactive = Membership.objects.create(
            organization=self.org,
            user=get_user_model().objects.create_user("md-inactive", password="test-pass"),
            role=Membership.Role.PM,
            active=False,
        )
        with self.assertRaises(ValidationError):
            update_project(
                self.owner,
                project.pk,
                {"manager": inactive},
                project.version,
                "停用负责人",
            )
        transferred = update_project(
            self.owner,
            project.pk,
            {"manager": self.pm2},
            project.version,
            "负责人交接",
        )
        self.assertEqual(transferred.manager_id, self.pm2.pk)

    def test_project_dates_are_validated_without_shifting_tasks_or_milestones(self):
        with self.assertRaises(ValidationError):
            update_project(
                self.owner,
                self.project.pk,
                {"start_date": date(2026, 1, 1), "end_date": date(2025, 12, 31)},
                self.project.version,
                "无效日期范围",
            )
        project = Project.objects.get(pk=self.project.pk)
        self.assertEqual(project.start_date, date(2025, 1, 1))

        update_project(
            self.owner,
            project.pk,
            {"start_date": date(2025, 1, 15), "end_date": date(2025, 11, 30)},
            project.version,
            "调整计划日期",
        )
        task = Task.objects.get(pk=self.task.pk)
        milestone = Milestone.objects.get(pk=self.milestone.pk)
        self.assertEqual(task.start_date, date(2025, 2, 1))
        self.assertEqual(task.due_date, date(2025, 2, 10))
        self.assertEqual(milestone.due_date, date(2025, 3, 1))

    def test_omitted_project_fields_are_kept_and_ledger_baseline_is_not_rewritten(self):
        project = Project.objects.get(pk=self.project.pk)
        updated = update_project(
            self.owner,
            project.pk,
            {"name": "仅更新名称"},
            project.version,
            "只更新项目名称",
        )
        self.assertEqual(updated.name, "仅更新名称")
        self.assertEqual(updated.client, project.client)
        self.assertEqual(updated.manager_id, project.manager_id)
        self.assertEqual(updated.start_date, project.start_date)
        self.assertEqual(updated.end_date, project.end_date)
        self.assertEqual(updated.scope, project.scope)
        self.assertEqual(updated.acceptance_criteria, project.acceptance_criteria)
        self.assertEqual(updated.contract_reference, project.contract_reference)
        self.assertEqual(updated.etc_cents, 120_000)

        project.refresh_from_db()
        try:
            update_project(
                self.owner,
                project.pk,
                {
                    "name": project.name,
                    "code": "MD-HACK",
                    "budget_cents": 1,
                    "contract_cents": 2,
                    "budget_approved": True,
                },
                project.version,
                "尝试绕过预算审批",
            )
        except (ValidationError, PermissionDenied):
            pass
        project.refresh_from_db()
        self.assertEqual(project.code, "MD-001")
        self.assertEqual(project.budget_cents, 500_000)
        self.assertEqual(project.contract_cents, 800_000)
        self.assertFalse(project.budget_approved)

    def test_closed_project_rejects_ordinary_master_data_edits(self):
        Project.objects.filter(pk=self.project.pk).update(stage=Project.Stage.CLOSED)
        project = Project.objects.get(pk=self.project.pk)
        with self.assertRaises(ValidationError):
            update_project(
                self.owner,
                project.pk,
                {"name": "关闭后仍改名"},
                project.version,
                "关闭项目不应接受普通编辑",
            )
        project.refresh_from_db()
        self.assertEqual(project.name, "基础资料项目")

    def test_project_update_audit_contains_before_and_after_values(self):
        project = Project.objects.get(pk=self.project.pk)
        update_project(
            self.owner,
            project.pk,
            {"name": "审计新名称", "client": "审计新客户"},
            project.version,
            "保留变更前后值",
        )
        event = (
            AuditEvent.objects.filter(
                organization=self.org,
                entity="Project",
                entity_id=str(project.pk),
            )
            .order_by("-id")
            .first()
        )
        self.assertIsNotNone(event)
        payload = json.dumps(event.payload, ensure_ascii=False, default=str)
        self.assertIn("基础资料项目", payload)
        self.assertIn("审计新名称", payload)
        self.assertIn("原客户", payload)
        self.assertIn("审计新客户", payload)

    def test_context_projects_respect_visibility_and_never_include_rates(self):
        required = {
            "tab",
            "projects",
            "employees",
            "rates",
            "can_write_projects",
            "can_write_people",
        }
        owner_context = masterdata_context(self.owner, "projects")
        self.assertTrue(required <= set(owner_context))
        self.assertEqual(owner_context["tab"], "projects")
        owner_projects = list(owner_context["projects"])
        self.assertIn(self.project, owner_projects)
        self.assertIn(self.hidden_project, owner_projects)
        self.assertNotIn(self.other_project, owner_projects)
        self.assertEqual(owner_context["rates"], [])
        self.assertEqual(owner_context["employees"], [])
        self.assertTrue(owner_context["can_write_projects"])
        self.assertTrue(owner_context["can_write_people"])

        member_context = masterdata_context(self.member, "projects")
        member_projects = list(member_context["projects"])
        self.assertIn(self.project, member_projects)
        self.assertNotIn(self.hidden_project, member_projects)
        self.assertNotIn(self.other_project, member_projects)
        self.assertEqual(member_context["rates"], [])
        self.assertFalse(member_context["can_write_projects"])
        self.assertFalse(member_context["can_write_people"])

        pm_context = masterdata_context(self.pm, "projects")
        pm_projects = list(pm_context["projects"])
        self.assertIn(self.project, pm_projects)
        self.assertNotIn(self.hidden_project, pm_projects)
        self.assertNotIn(self.other_project, pm_projects)
        self.assertTrue(pm_context["can_write_projects"])
        self.assertFalse(pm_context["can_write_people"])

        pm2_context = masterdata_context(self.pm2, "projects")
        pm2_projects = list(pm2_context["projects"])
        self.assertIn(self.hidden_project, pm2_projects)
        self.assertNotIn(self.project, pm2_projects)

    def test_context_people_tabs_require_finance_write_and_stay_in_tenant(self):
        for actor in (self.pm, self.member, self.auditor):
            for tab in ("employees", "rates"):
                with self.assertRaises(PermissionDenied):
                    masterdata_context(actor, tab)

        employees = masterdata_context(self.finance, "employees")
        self.assertEqual(employees["tab"], "employees")
        self.assertIn(self.project, list(employees["projects"]))
        self.assertIn(self.employee, list(employees["employees"]))
        self.assertNotIn(self.other_employee, list(employees["employees"]))
        self.assertEqual(employees["rates"], [])
        self.assertTrue(employees["can_write_people"])
        self.assertFalse(employees["can_write_projects"])

        rates = masterdata_context(self.owner, "rates")
        self.assertEqual(rates["tab"], "rates")
        self.assertIn(self.project, list(rates["projects"]))
        self.assertEqual(list(rates["rates"]), [self.rate])
        self.assertIn(self.employee, list(rates["employees"]))
        self.assertNotIn(self.other_employee, list(rates["employees"]))

    def test_context_unknown_tab_falls_back_to_projects(self):
        for tab in ("unknown", "", None):
            context = masterdata_context(self.owner, tab)
            self.assertEqual(context["tab"], "projects")
            self.assertIn(self.project, list(context["projects"]))
            self.assertEqual(context["rates"], [])

    def test_rate_rechecks_disabled_employee_in_persisted_record(self):
        Employee.objects.filter(pk=self.employee.pk).update(active=False)
        self.assertTrue(self.employee.active)
        with self.assertRaises(ValidationError):
            create_rate(self.owner, {"employee": self.employee, "effective_from": date(2026, 1, 1),
                                    "hourly_amount": Decimal("120"), "basis": "新的有效费率依据"})

    def test_manager_rechecks_persisted_active_membership(self):
        Membership.objects.filter(pk=self.pm2.pk).update(active=False)
        with self.assertRaises(ValidationError):
            update_project(self.owner, self.project.pk, {"manager": self.pm2}, self.project.version, "负责人调整依据")

    def test_typed_version_and_money_reject_float_or_boolean(self):
        for value in (True, 1.5, "1"):
            with self.assertRaises(ValidationError):
                update_project(self.owner, self.project.pk, {}, value, "基础资料核对依据")
        for amount in (12.5, True):
            with self.assertRaises(ValidationError):
                create_rate(self.owner, {"employee": self.employee, "effective_from": date(2026, 1, 1),
                                        "hourly_amount": amount, "basis": "新的有效费率依据"})

    def test_account_creation_does_not_create_employee_outside_master_data(self):
        self.client.force_login(self.owner.user, backend="django.contrib.auth.backends.ModelBackend")
        response = self.client.post("/new/member/", {"username": "md-new-login", "name": "新账户",
            "role": "member", "password": "Demo-Only-Pass!9274", "submission_token": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        account = Membership.objects.get(user__username="md-new-login")
        self.assertFalse(Employee.objects.filter(member=account).exists())

    def test_employee_edit_form_keeps_own_linked_account(self):
        from core.masterdata_forms import EmployeeEditForm

        form = EmployeeEditForm(
            data=self.employee_data(name="保留本人账户", member=self.member.pk),
            member=self.owner,
            employee=self.employee,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["member"], self.member)

    def test_employee_edit_form_hides_bound_cross_company_and_inactive_accounts(self):
        from django.contrib.auth import get_user_model

        from core.masterdata_forms import EmployeeEditForm

        inactive = Membership.objects.create(
            organization=self.org,
            user=get_user_model().objects.create_user(
                "md-form-inactive", password="test-pass"
            ),
            role=Membership.Role.MEMBER,
            active=False,
        )
        form = EmployeeEditForm(member=self.owner, employee=self.pm_employee)
        choices = set(form.fields["member"].queryset)
        self.assertIn(self.auditor, choices)
        self.assertNotIn(self.member, choices)
        self.assertNotIn(self.other_owner, choices)
        self.assertNotIn(inactive, choices)

        for account in (self.member, self.other_owner, inactive):
            candidate = EmployeeEditForm(
                data=self.employee_data(name="不可用账户", member=account.pk),
                member=self.owner,
                employee=self.pm_employee,
            )
            self.assertFalse(candidate.is_valid(), account.pk)

    def test_employee_edit_form_rejects_sub_minute_capacity_but_accepts_eight_hours(self):
        from core.masterdata_forms import EmployeeEditForm

        valid = EmployeeEditForm(
            data=self.employee_data(
                name="八小时容量", member=None, capacity_hours=Decimal("8")
            ),
            member=self.owner,
            employee=self.employee,
        )
        self.assertTrue(valid.is_valid(), valid.errors)
        self.assertEqual(valid.cleaned_data["capacity_hours"], Decimal("8"))

        quarter = EmployeeEditForm(
            data=self.employee_data(member=None, capacity_hours=Decimal("0.25")),
            member=self.owner, employee=self.employee,
        )
        self.assertTrue(quarter.is_valid(), quarter.errors)

        invalid = EmployeeEditForm(
            data=self.employee_data(
                name="非整数分钟容量", member=None, capacity_hours=Decimal("8.01")
            ),
            member=self.owner,
            employee=self.employee,
        )
        self.assertFalse(invalid.is_valid())

    def test_project_form_manager_choices_follow_owner_and_pm_boundary(self):
        from django.contrib.auth import get_user_model

        from core.masterdata_forms import ProjectMasterDataForm

        inactive_pm = Membership.objects.create(
            organization=self.org,
            user=get_user_model().objects.create_user(
                "md-form-inactive-pm", password="test-pass"
            ),
            role=Membership.Role.PM,
            active=False,
        )
        owner_form = ProjectMasterDataForm(
            data=self.project_data(
                manager=self.pm2.pk,
                version=self.project.version,
                reason="负责人选择边界",
            ),
            member=self.owner,
            project=self.project,
        )
        self.assertTrue(owner_form.is_valid(), owner_form.errors)
        choices = set(owner_form.fields["manager"].queryset)
        for account in (self.owner, self.pm, self.pm2):
            self.assertIn(account, choices)
        for account in (self.finance, self.member, self.other_pm, inactive_pm):
            self.assertNotIn(account, choices)

        pm_form = ProjectMasterDataForm(
            data=self.project_data(
                manager=self.pm2.pk,
                version=self.project.version,
                reason="项目经理转交负责人",
            ),
            member=self.pm,
            project=self.project,
        )
        self.assertFalse(pm_form.is_valid())
        pm_choices = set(pm_form.fields["manager"].queryset)
        self.assertIn(self.pm, pm_choices)
        self.assertNotIn(self.pm2, pm_choices)

    def test_project_form_rejects_invalid_dates_and_short_reason(self):
        from core.masterdata_forms import ProjectMasterDataForm

        invalid_dates = ProjectMasterDataForm(
            data=self.project_data(
                start_date=date(2026, 1, 1),
                end_date=date(2025, 12, 31),
                manager=self.pm.pk,
                version=self.project.version,
                reason="无效日期范围",
            ),
            member=self.owner,
            project=self.project,
        )
        self.assertFalse(invalid_dates.is_valid())

        short_reason = ProjectMasterDataForm(
            data=self.project_data(
                manager=self.pm.pk,
                version=self.project.version,
                reason="短",
            ),
            member=self.owner,
            project=self.project,
        )
        self.assertFalse(short_reason.is_valid())

    def test_project_form_valid_changes_reach_update_project_service(self):
        from core.masterdata_forms import ProjectMasterDataForm

        data = self.project_data(
            name="表单变更名称",
            client="表单变更客户",
            manager=self.pm.pk,
            version=self.project.version,
            reason="表单正常维护依据",
        )
        form = ProjectMasterDataForm(data=data, member=self.owner, project=self.project)
        self.assertTrue(form.is_valid(), form.errors)
        cleaned = form.cleaned_data.copy()
        version, reason = cleaned.pop("version"), cleaned.pop("reason")
        updated = update_project(self.owner, self.project.pk, cleaned, version, reason)
        self.assertEqual(updated.name, "表单变更名称")
        self.assertEqual(updated.client, "表单变更客户")
        self.assertEqual(updated.version, version + 1)
        self.assertTrue(AuditEvent.objects.filter(organization=self.org,
                        payload__reason="表单正常维护依据").exists())

    def test_project_form_accepts_execution_metadata_without_changing_budget_or_stage(self):
        from core.masterdata_forms import ProjectMasterDataForm
        self.project.stage = Project.Stage.EXECUTION
        self.project.save(update_fields=["stage"])
        data = self.project_data(manager=self.pm.pk, end_date=date(2026, 1, 31),
                                 version=self.project.version, reason="更新基础资料日期依据")
        form = ProjectMasterDataForm(data=data, member=self.owner, project=self.project)
        for field in ("scope", "acceptance_criteria", "end_date", "contract_reference"):
            self.assertFalse(form.fields[field].disabled)
        self.assertTrue(form.is_valid(), form.errors)
        cleaned = form.cleaned_data.copy()
        version, reason = cleaned.pop("version"), cleaned.pop("reason")
        updated = update_project(self.owner, self.project.pk, cleaned, version, reason)
        self.assertEqual(updated.end_date, date(2026, 1, 31))
        self.assertEqual(updated.stage, Project.Stage.EXECUTION)
        self.assertEqual(updated.budget_cents, 500_000)

    def test_employee_form_does_not_restore_inactive_current_account(self):
        from core.masterdata_forms import EmployeeEditForm
        user = self.member.user
        user.is_active = False
        user.save(update_fields=["is_active"])
        form = EmployeeEditForm(member=self.owner, employee=self.employee)
        self.assertNotIn(self.member, set(form.fields["member"].queryset))
