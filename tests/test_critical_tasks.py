"""PH-025 counterexamples: key-task links, blueprints and project-level time."""
import uuid
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.agent_auth import issue_token
from core.blueprints import BLUEPRINTS, apply_blueprint
from core.models import (
    CashPlan,
    CostRate,
    Employee,
    LedgerEntry,
    Membership,
    Milestone,
    Organization,
    Project,
    ProjectMember,
    Task,
    Timesheet,
)
from core.services import create_task, edit_task, review, submit_time, update_task


class CriticalTaskFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.day_one = date(2025, 1, 10)
        cls.day_two = date(2025, 2, 10)
        cls.today = timezone.localdate()
        cls.user_model = get_user_model()
        cls.org = Organization.objects.create(name="关键任务测试公司")
        cls.other_org = Organization.objects.create(name="外部公司")

        def user(name):
            return cls.user_model.objects.create_user(name, password="test-pass")

        cls.owner_user = user("critical-owner")
        cls.finance_user = user("critical-finance")
        cls.pm_user = user("critical-pm")
        cls.member_user = user("critical-member")
        cls.unassigned_user = user("critical-unassigned")
        cls.inactive_user = user("critical-inactive")
        cls.foreign_user = user("critical-foreign")

        cls.owner = Membership.objects.create(organization=cls.org, user=cls.owner_user, role=Membership.Role.OWNER)
        cls.finance = Membership.objects.create(organization=cls.org, user=cls.finance_user, role=Membership.Role.FINANCE)
        cls.pm = Membership.objects.create(organization=cls.org, user=cls.pm_user, role=Membership.Role.PM)
        cls.member = Membership.objects.create(organization=cls.org, user=cls.member_user, role=Membership.Role.MEMBER)
        cls.unassigned = Membership.objects.create(organization=cls.org, user=cls.unassigned_user, role=Membership.Role.MEMBER)
        cls.inactive = Membership.objects.create(organization=cls.org, user=cls.inactive_user, role=Membership.Role.MEMBER, active=False)
        cls.foreign_owner = Membership.objects.create(organization=cls.other_org, user=cls.foreign_user, role=Membership.Role.OWNER)

        cls.employee = Employee.objects.create(organization=cls.org, name="关键任务员工", member=cls.member)
        cls.pm_employee = Employee.objects.create(organization=cls.org, name="关键任务经理", member=cls.pm)
        CostRate.objects.create(employee=cls.employee, effective_from=date(2024, 1, 1), hourly_cents=12_000, basis="2024费率")
        CostRate.objects.create(employee=cls.pm_employee, effective_from=date(2024, 1, 1), hourly_cents=15_000, basis="2024费率")

        cls.project = Project.objects.create(
            organization=cls.org, code="KEY-001", name="关键任务项目", client="客户", manager=cls.pm,
            stage=Project.Stage.PLANNING, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31),
            scope="关键任务范围", acceptance_criteria="关键任务验收", contract_reference="KEY-CONTRACT")
        ProjectMember.objects.create(project=cls.project, member=cls.pm)
        ProjectMember.objects.create(project=cls.project, member=cls.member)
        cls.milestone = Milestone.objects.create(
            project=cls.project, name="首版交付", due_date=cls.day_two, criteria="首版功能可用")
        cls.second_milestone = Milestone.objects.create(
            project=cls.project, name="终验交付", due_date=cls.day_two + timedelta(days=10), criteria="终验通过")
        cls.plan = CashPlan.objects.create(
            project=cls.project, direction=CashPlan.Direction.IN, title="首版回款", counterparty="客户",
            amount_cents=500_000, due_date=cls.day_two, milestone=cls.milestone, reference="KEY-CASH-1",
            created_by=cls.pm_user)
        cls.plan_without_milestone = CashPlan.objects.create(
            project=cls.project, direction=CashPlan.Direction.OUT, title="采购付款", counterparty="供应商",
            amount_cents=100_000, due_date=cls.day_two, reference="KEY-CASH-2", created_by=cls.pm_user)
        cls.inactive_plan = CashPlan.objects.create(
            project=cls.project, direction=CashPlan.Direction.IN, title="已停用计划", counterparty="客户",
            amount_cents=100_000, due_date=cls.day_two, active=False, reference="KEY-CASH-3",
            created_by=cls.pm_user)

        cls.other_project = Project.objects.create(
            organization=cls.org, code="KEY-002", name="同公司其他项目", client="客户", manager=cls.pm,
            stage=Project.Stage.PLANNING, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        ProjectMember.objects.create(project=cls.other_project, member=cls.pm)
        cls.other_milestone = Milestone.objects.create(
            project=cls.other_project, name="其他里程碑", due_date=cls.day_two, criteria="其他项目验收")
        cls.other_plan = CashPlan.objects.create(
            project=cls.other_project, direction=CashPlan.Direction.IN, title="其他回款", counterparty="客户",
            amount_cents=10_000, due_date=cls.day_two, reference="KEY-CASH-4", created_by=cls.pm_user)

        cls.foreign_project = Project.objects.create(
            organization=cls.other_org, code="KEY-001", name="外部项目", client="外部客户", manager=cls.foreign_owner,
            stage=Project.Stage.PLANNING, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        cls.foreign_milestone = Milestone.objects.create(
            project=cls.foreign_project, name="外部里程碑", due_date=cls.day_two, criteria="外部验收")
        cls.foreign_plan = CashPlan.objects.create(
            project=cls.foreign_project, direction=CashPlan.Direction.IN, title="外部回款", counterparty="外部客户",
            amount_cents=10_000, due_date=cls.day_two, reference="KEY-CASH-5", created_by=cls.foreign_user)

        cls.second_project = Project.objects.create(
            organization=cls.org, code="KEY-003", name="第二工时项目", client="客户", manager=cls.pm,
            stage=Project.Stage.PLANNING, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        ProjectMember.objects.create(project=cls.second_project, member=cls.pm)
        ProjectMember.objects.create(project=cls.second_project, member=cls.member)

    def task_data(self, **changes):
        data = {
            "project": self.project, "name": "关键任务", "owner": self.member, "status": Task.Status.TODO,
            "start_date": self.day_one, "due_date": self.day_two, "milestone": self.milestone,
            "cash_plan": None, "description": "", "completion_note": "",
        }
        data.update(changes)
        return data

    def edit_data(self, task, **changes):
        data = {
            "project": task.project, "name": task.name, "owner": task.owner, "start_date": task.start_date,
            "due_date": task.due_date, "dependency": task.dependency, "milestone": task.milestone,
            "cash_plan": task.cash_plan, "description": task.description,
        }
        data.update(changes)
        return data

    def time_data(self, **changes):
        data = {
            "project": self.project, "employee": self.employee, "task": None,
            "work_date": self.day_one, "minutes": 120, "note": "项目级工时",
        }
        data.update(changes)
        return data


class KeyTaskLinkPolicyTests(CriticalTaskFixture):
    def test_create_requires_at_least_one_same_project_link(self):
        with self.assertRaisesMessage(ValidationError, "至少关联"):
            create_task(self.pm, self.task_data(milestone=None, cash_plan=None))
        self.assertFalse(Task.objects.filter(name="关键任务").exists())

    def test_new_task_defaults_estimate_to_zero_and_keeps_milestone(self):
        task = create_task(self.pm, self.task_data())
        self.assertEqual(task.milestone_id, self.milestone.pk)
        self.assertIsNone(task.cash_plan_id)
        self.assertEqual(task.estimate_minutes, 0)

    def test_active_cash_plan_without_milestone_is_a_valid_link(self):
        task = create_task(self.pm, self.task_data(milestone=None, cash_plan=self.plan_without_milestone))
        self.assertIsNone(task.milestone_id)
        self.assertEqual(task.cash_plan_id, self.plan_without_milestone.pk)

    def test_cross_project_and_cross_tenant_links_are_rejected(self):
        for milestone in (self.other_milestone, self.foreign_milestone):
            with self.assertRaisesMessage(ValidationError, "同一项目"):
                create_task(self.pm, self.task_data(milestone=milestone))
        for plan in (self.other_plan, self.foreign_plan):
            with self.assertRaisesMessage(ValidationError, "同一项目"):
                create_task(self.pm, self.task_data(milestone=None, cash_plan=plan))

    def test_inactive_cash_plan_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "停用"):
            create_task(self.pm, self.task_data(milestone=None, cash_plan=self.inactive_plan))

    def test_stale_plan_is_reloaded_before_validating_active_state(self):
        CashPlan.objects.filter(pk=self.plan.pk).update(active=False)
        self.assertTrue(self.plan.active)
        with self.assertRaisesMessage(ValidationError, "停用"):
            create_task(self.pm, self.task_data(cash_plan=self.plan))

    def test_link_object_cannot_forge_its_persisted_project(self):
        import copy
        forged = copy.copy(self.other_milestone)
        forged.project_id = self.project.pk
        with self.assertRaisesMessage(ValidationError, "同一项目"):
            create_task(self.pm, self.task_data(milestone=forged))

    def test_cash_plan_and_milestone_must_name_the_same_milestone(self):
        with self.assertRaisesMessage(ValidationError, "不一致"):
            create_task(self.pm, self.task_data(milestone=self.second_milestone, cash_plan=self.plan))
        task = create_task(self.pm, self.task_data(milestone=self.milestone, cash_plan=self.plan))
        self.assertEqual(task.milestone_id, task.cash_plan.milestone_id)

    def test_owner_must_be_an_active_company_project_member(self):
        with self.assertRaisesMessage(ValidationError, "本公司有效成员"):
            create_task(self.pm, self.task_data(owner=self.foreign_owner))
        with self.assertRaisesMessage(ValidationError, "本公司有效成员"):
            create_task(self.pm, self.task_data(owner=self.inactive))
        with self.assertRaisesMessage(ValidationError, "加入项目团队"):
            create_task(self.pm, self.task_data(owner=self.unassigned))
        task = create_task(self.pm, self.task_data(owner=self.member))
        self.assertEqual(task.owner_id, self.member.pk)

    def test_dependency_must_be_same_project(self):
        historical = Task.objects.create(project=self.other_project, name="其他项目任务", owner=self.pm,
                                         start_date=self.day_one, due_date=self.day_two)
        with self.assertRaisesMessage(ValidationError, "同一项目"):
            create_task(self.pm, self.task_data(dependency=historical))
        task = create_task(self.pm, self.task_data(dependency=None))
        self.assertIsNone(task.dependency_id)

    def test_edit_requires_link_version_and_rejects_cycles(self):
        task = create_task(self.pm, self.task_data())
        with self.assertRaisesMessage(ValidationError, "至少关联"):
            edit_task(self.pm, task.pk, self.edit_data(task, milestone=None, cash_plan=None),
                      task.version, "移除关键关联")
        with self.assertRaisesMessage(ValidationError, "刷新"):
            edit_task(self.pm, task.pk, self.edit_data(task), task.version + 1, "版本冲突")
        edited = edit_task(self.pm, task.pk, self.edit_data(task, milestone=self.second_milestone),
                           task.version, "调整关键节点")
        self.assertEqual(edited.milestone_id, self.second_milestone.pk)
        self.assertEqual(edited.version, task.version + 1)

        second = create_task(self.pm, self.task_data(name="后续关键任务", milestone=self.second_milestone,
                                                     dependency=edited))
        with self.assertRaisesMessage(ValidationError, "循环"):
            edit_task(self.pm, edited.pk, self.edit_data(edited, dependency=second),
                      edited.version, "形成循环")

    def test_status_rules_still_apply_to_historical_and_linked_tasks(self):
        historical = Task.objects.create(project=self.project, name="历史无链接任务", owner=self.member,
                                         start_date=self.day_one, due_date=self.day_two)
        moved = update_task(self.member, historical.pk, "doing", historical.version)
        self.assertEqual(moved.status, "doing")
        self.assertIsNone(moved.milestone_id)
        with self.assertRaisesMessage(ValidationError, "刷新"):
            update_task(self.member, historical.pk, "blocked", historical.version)

        blocker = create_task(self.pm, self.task_data(name="前置关键任务"))
        dependent = create_task(self.pm, self.task_data(name="后继关键任务", dependency=blocker))
        with self.assertRaisesMessage(ValidationError, "前置任务尚未完成"):
            update_task(self.pm, dependent.pk, "doing", dependent.version)
        with self.assertRaisesMessage(ValidationError, "交付说明"):
            update_task(self.pm, blocker.pk, "done", blocker.version, "")
        done = update_task(self.pm, blocker.pk, "done", blocker.version, "已归档验收证据")
        self.assertEqual(done.status, "done")


class BlueprintCriticalTaskTests(CriticalTaskFixture):
    def test_blueprint_only_creates_verifiable_milestones_and_key_tasks(self):
        project = Project.objects.create(
            organization=self.org, code="BP-001", name="蓝图项目", client="客户", manager=self.pm,
            stage=Project.Stage.PLANNING, start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        ProjectMember.objects.create(project=project, member=self.pm)
        apply_blueprint(self.pm, project)
        rows = BLUEPRINTS[project.template]
        self.assertEqual(project.milestones.count(), len(rows))
        self.assertEqual(project.tasks.count(), len(rows))
        daily_names = {name for name, _ in rows}
        for task in project.tasks.select_related("milestone"):
            self.assertIsNotNone(task.milestone_id)
            self.assertEqual(task.milestone.project_id, project.pk)
            self.assertNotIn(task.name, daily_names)
            self.assertEqual(task.estimate_minutes, 0)
            for daily_word in ("日常", "培训", "服务执行", "迭代开发"):
                self.assertNotIn(daily_word, task.milestone.name)
            for daily_word in ("日常", "培训", "服务执行", "迭代开发"):
                self.assertNotIn(daily_word, task.name)
        self.assertFalse(project.tasks.filter(milestone__isnull=True, cash_plan__isnull=True).exists())
        self.assertFalse(project.tasks.filter(cash_plan__isnull=False).exists())
        self.assertFalse(project.cash_plans.exists())
        self.assertFalse(project.milestones.filter(accepted_at__isnull=False).exists())


class ProjectTimeAccountingTests(CriticalTaskFixture):
    def test_time_without_task_is_project_level_and_rejects_foreign_task(self):
        data = self.time_data()
        data.pop("task")
        row = submit_time(self.member, data, uuid.uuid4())
        self.assertIsNone(row.task_id)
        self.assertEqual(row.project_id, self.project.pk)
        foreign_task = Task.objects.create(project=self.other_project, name="其他项目历史任务", owner=self.pm,
                                           start_date=self.day_one, due_date=self.day_two)
        with self.assertRaisesMessage(ValidationError, "不属于所选项目"):
            submit_time(self.member, self.time_data(task=foreign_task, work_date=self.day_two), uuid.uuid4())

    def test_rate_snapshot_and_line_rounding(self):
        row = submit_time(self.member, self.time_data(), uuid.uuid4())
        self.assertEqual(row.hourly_rate_cents, 12_000)
        self.assertEqual(row.cost_cents, 24_000)
        CostRate.objects.create(employee=self.employee, effective_from=date(2025, 2, 1),
                                hourly_cents=60_000, basis="2025年调薪费率")
        later = submit_time(self.member, self.time_data(work_date=self.day_two, minutes=90), uuid.uuid4())
        self.assertEqual(later.hourly_rate_cents, 60_000)
        self.assertEqual(later.rate_basis, "2025年调薪费率")
        self.assertEqual(later.cost_cents, 90_000)

        rounding_employee = Employee.objects.create(organization=self.org, name="取整员工")
        CostRate.objects.create(employee=rounding_employee, effective_from=date(2024, 1, 1),
                                hourly_cents=12_345, basis="取整费率")
        rounded = submit_time(self.pm, self.time_data(employee=rounding_employee, minutes=1), uuid.uuid4())
        self.assertEqual(rounded.cost_cents, 206)

    def test_locked_period_future_date_and_daily_cap_are_rejected(self):
        Organization.objects.filter(pk=self.org.pk).update(locked_through=self.day_one)
        with self.assertRaisesMessage(ValidationError, "已锁账"):
            submit_time(self.member, self.time_data(), uuid.uuid4())
        Organization.objects.filter(pk=self.org.pk).update(locked_through=None)
        with self.assertRaisesMessage(ValidationError, "未来"):
            submit_time(self.member, self.time_data(work_date=self.today + timedelta(days=1)), uuid.uuid4())

        submit_time(self.member, self.time_data(minutes=1_440), uuid.uuid4())
        with self.assertRaisesMessage(ValidationError, "24小时"):
            submit_time(self.member, self.time_data(project=self.second_project, minutes=1), uuid.uuid4())

    def test_source_token_idempotency_is_preserved_without_task(self):
        token = uuid.uuid4()
        first = submit_time(self.member, self.time_data(), token)
        second = submit_time(self.member, self.time_data(), token)
        self.assertEqual(first.pk, second.pk)
        with self.assertRaisesMessage(ValidationError, "来源编号"):
            submit_time(self.member, self.time_data(minutes=180), token)

    def test_time_approval_creates_cost_only_and_never_touches_cash(self):
        row = submit_time(self.member, self.time_data(minutes=120), uuid.uuid4())
        approved = review(self.finance, "time", row.pk, "approve")
        self.assertEqual(approved.status, "approved")
        self.assertIsNone(approved.task_id)
        self.assertEqual(LedgerEntry.objects.filter(timesheet=row, book="cost").count(), 1)
        self.assertFalse(LedgerEntry.objects.filter(timesheet=row, book__in=["cash_in", "cash_out"]).exists())
        self.assertFalse(LedgerEntry.objects.filter(project=self.project, book__in=["cash_in", "cash_out"]).exists())
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.amount_cents, 500_000)


class AgentApiKeyTaskTests(CriticalTaskFixture):
    def setUp(self):
        self.token, raw = issue_token(self.member, "关键任务助手",
                                      ["tasks:read", "time:write_self", "tasks:write_self"], [self.project.pk])
        self.client = Client(HTTP_AUTHORIZATION="Bearer " + raw)
        self.key = str(uuid.uuid4())

    def post(self, url, data, key=None):
        return self.client.post(url, data=data, content_type="application/json",
                                HTTP_IDEMPOTENCY_KEY=key or self.key)

    def test_tasks_read_returns_only_linked_tasks_with_link_fields(self):
        linked = create_task(self.pm, self.task_data(name="API关键任务"))
        Task.objects.create(project=self.project, name="API历史无链接任务", owner=self.member,
                            start_date=self.day_one, due_date=self.day_two)
        response = self.client.get(f"/api/v1/projects/{self.project.pk}/tasks")
        self.assertEqual(response.status_code, 200, response.content)
        rows = response.json()["tasks"]
        self.assertEqual([row["id"] for row in rows], [linked.pk])
        row = rows[0]
        self.assertEqual(row["milestone_id"], self.milestone.pk)
        self.assertEqual(row["milestone_name"], self.milestone.name)
        self.assertIsNone(row["cash_plan_id"])
        self.assertEqual(row["owner"], str(self.member))
        self.assertTrue(row["owned_by_me"])
        self.assertEqual(row["version"], linked.version)
        self.assertIn("dependency_id", row)

    def test_optional_task_id_stays_same_project_and_idempotent(self):
        linked = create_task(self.pm, self.task_data(name="API关联任务"))
        payload = {"project_id": self.project.pk, "task_id": linked.pk, "work_date": str(self.day_one),
                   "minutes": 60, "note": "关联任务历史兼容", "dry_run": False}
        first = self.post("/api/v1/timesheets", payload)
        second = self.post("/api/v1/timesheets", payload)
        self.assertEqual(first.status_code, 202, first.content)
        self.assertEqual(second.json()["id"], first.json()["id"])
        self.assertEqual(Timesheet.objects.get().task_id, linked.pk)

        other_task = Task.objects.create(project=self.other_project, name="跨项目任务", owner=self.pm,
                                         start_date=self.day_one, due_date=self.day_two)
        wrong = self.post("/api/v1/timesheets", {**payload, "task_id": other_task.pk}, key=str(uuid.uuid4()))
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(Timesheet.objects.count(), 1)

        omitted = self.post("/api/v1/timesheets", {key: value for key, value in payload.items() if key != "task_id"},
                            key=str(uuid.uuid4()))
        self.assertEqual(omitted.status_code, 202, omitted.content)
        self.assertEqual(Timesheet.objects.order_by("-pk").first().task_id, None)

    def test_historical_task_status_keeps_owner_and_version_rules(self):
        historical = Task.objects.create(project=self.project, name="API历史任务", owner=self.member,
                                         start_date=self.day_one, due_date=self.day_two)
        other = Task.objects.create(project=self.project, name="API他人历史任务", owner=self.pm,
                                    start_date=self.day_one, due_date=self.day_two)
        data = {"status": "doing", "expected_version": historical.version, "dry_run": False}
        response = self.post(f"/api/v1/tasks/{historical.pk}/status", data)
        self.assertEqual(response.status_code, 200, response.content)
        historical.refresh_from_db()
        self.assertEqual(historical.status, "doing")
        self.assertEqual(historical.version, 2)
        self.assertEqual(self.post(f"/api/v1/tasks/{other.pk}/status", data).status_code, 404)


class BrowserKeyTaskTests(CriticalTaskFixture):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.pm_user)

    def create_url(self):
        return reverse("new-object", args=["task"]) + f"?project={self.project.pk}"

    def task_form(self, **changes):
        data = {
            "project": self.project.pk, "name": "浏览器关键任务", "owner": self.member.pk,
            "start_date": str(self.day_one), "due_date": str(self.day_two),
            "milestone": self.milestone.pk, "cash_plan": "", "description": "浏览器创建",
            "submission_token": str(uuid.uuid4()),
        }
        data.update(changes)
        return data

    def test_browser_can_create_and_edit_linked_key_tasks(self):
        response = self.client.post(self.create_url(), self.task_form())
        self.assertEqual(response.status_code, 302, response.content)
        task = Task.objects.get(name="浏览器关键任务")
        self.assertEqual(task.milestone_id, self.milestone.pk)
        self.assertEqual(task.estimate_minutes, 0)

        response = self.client.post(reverse("task-edit", args=[task.pk]), {
            "project": self.project.pk, "name": task.name, "owner": task.owner_id,
            "start_date": str(task.start_date), "due_date": str(task.due_date),
            "milestone": self.second_milestone.pk, "cash_plan": "", "dependency": "",
            "description": "浏览器调整", "version": task.version, "reason": "调整关键节点关联",
        })
        self.assertEqual(response.status_code, 302, response.content)
        task.refresh_from_db()
        self.assertEqual(task.milestone_id, self.second_milestone.pk)
        self.assertEqual(task.version, 2)

    def test_browser_missing_link_shows_form_error_without_creating_task(self):
        response = self.client.post(self.create_url(), self.task_form(milestone="", cash_plan=""))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Task.objects.filter(name="浏览器关键任务").exists())
        self.assertTrue(response.context["form"].errors)

    def test_browser_time_post_without_task_is_accepted(self):
        response = self.client.post(reverse("new-object", args=["time"]) + f"?project={self.project.pk}", {
            "project": self.project.pk, "employee": self.employee.pk, "work_date": str(self.day_one),
            "hours": "1.50", "note": "无任务项目工时", "submission_token": str(uuid.uuid4()),
        })
        self.assertEqual(response.status_code, 302, response.content)
        row = Timesheet.objects.get(note="无任务项目工时")
        self.assertIsNone(row.task_id)
        self.assertEqual(row.minutes, 90)
        self.assertEqual(row.project_id, self.project.pk)


