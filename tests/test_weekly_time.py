"""Q-35 工时随手记与自动人工成本：周填报、费率快照、缺口、权限与幂等。"""
import re
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.masterdata import create_rate, ensure_labor_destinations
from core.models import (
    AuditEvent, CostRate, Employee, LedgerEntry, Membership, NonProjectDestination,
    Organization, Project, ProjectMember, Timesheet,
)
from core.reporting import project_cost_burndown, project_summary
from core.services import review as review_record, reverse as reverse_record, time_cost
from core.weekly_time import MODE, WeekTimeError, missing_rate_gaps, save_week, week_bounds


class WeeklyTimeFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_model = get_user_model()
        cls.org = Organization.objects.create(name="周报测试公司")
        cls.today = timezone.localdate()
        cls.week_start = cls.today - timedelta(days=cls.today.weekday() + 7)
        cls.week_end = cls.week_start + timedelta(days=6)
        cls.rate_basis = "周报测试基准费率"
        users = {}
        for name, role in [("owner", "owner"), ("finance", "finance"), ("pm", "pm"),
                           ("member", "member"), ("auditor", "auditor")]:
            users[name] = cls.user_model.objects.create_user(f"wt-{name}", password="test-pass")
            setattr(cls, name, Membership.objects.create(organization=cls.org, user=users[name], role=role))
        cls.member_employee = Employee.objects.create(
            organization=cls.org, name="成员甲", member=cls.member)
        cls.pm_employee = Employee.objects.create(
            organization=cls.org, name="项目经理乙", member=cls.pm)
        cls.rate_less_employee = Employee.objects.create(
            organization=cls.org, name="新同事丙", title="尚未配置费率")
        cls.newbie_user = cls.user_model.objects.create_user("wt-newbie", password="test-pass")
        cls.newbie = Membership.objects.create(organization=cls.org, user=cls.newbie_user, role="member")
        cls.rate_less_employee.member = cls.newbie
        cls.rate_less_employee.save(update_fields=["member"])
        CostRate.objects.create(employee=cls.member_employee, effective_from=date(2024, 1, 1),
                                hourly_cents=12_000, basis=cls.rate_basis)
        CostRate.objects.create(employee=cls.pm_employee, effective_from=date(2024, 1, 1),
                                hourly_cents=16_000, basis="项目经理费率")
        cls.project = Project.objects.create(
            organization=cls.org, code="WT-001", name="周报项目", client="示例客户",
            manager=cls.pm, stage=Project.Stage.EXECUTION,
            start_date=cls.today - timedelta(days=90), end_date=cls.today + timedelta(days=90),
            budget_cents=10_000_000, budget_approved=True, scope="周报范围", acceptance_criteria="周报验收",
            contract_reference="WT-CONTRACT")
        ProjectMember.objects.create(project=cls.project, member=cls.pm)
        ProjectMember.objects.create(project=cls.project, member=cls.member)
        ProjectMember.objects.create(project=cls.project, member=cls.newbie)
        cls.other_project = Project.objects.create(
            organization=cls.org, code="WT-002", name="非成员项目", client="另一个客户",
            manager=cls.pm, stage=Project.Stage.EXECUTION,
            start_date=cls.today - timedelta(days=30), end_date=cls.today + timedelta(days=60),
            scope="非成员范围", acceptance_criteria="非成员验收", contract_reference="WT-002-C")
        cls.foreign_org = Organization.objects.create(name="外部公司")
        cls.foreign_user = cls.user_model.objects.create_user("wt-foreign", password="test-pass")
        cls.foreign = Membership.objects.create(
            organization=cls.foreign_org, user=cls.foreign_user, role="owner")
        cls.foreign_project = Project.objects.create(
            organization=cls.foreign_org, code="F-001", name="外部项目", client="外部客户",
            manager=cls.foreign, stage=Project.Stage.EXECUTION,
            start_date=cls.today - timedelta(days=10), end_date=cls.today + timedelta(days=30),
            scope="外部", acceptance_criteria="外部", contract_reference="F-C")
        cls.foreign_employee = Employee.objects.create(
            organization=cls.foreign_org, name="外部员工", member=cls.foreign)
        ensure_labor_destinations(cls.owner)
        cls.admin_destination = NonProjectDestination.objects.get(organization=cls.org, code="ADMIN")

    def setUp(self):
        self.client.logout()

    # ---- helpers -------------------------------------------------------
    def login(self, member):
        self.client.force_login(member.user)

    def url(self):
        return reverse("timesheets-week")

    def row(self, index, work_date, target, minutes, note, row_id=None, token=None, version=None):
        # Q-45：页面按小时填报（0.1 小时精度），内部仍以整数分钟记账。
        hours = format((Decimal(minutes) / Decimal(60)).normalize(), "f")
        return {
            f"row-{index}-work_date": work_date.isoformat(),
            f"row-{index}-target": target,
            f"row-{index}-hours": hours,
            f"row-{index}-note": note,
            f"row-{index}-id": str(row_id) if row_id else "",
            f"row-{index}-token": token if token is not None else str(uuid.uuid4()),
            f"row-{index}-version": str(version) if version else "",
        }

    def project_target(self, project=None):
        return f"project:{(project or self.project).pk}"

    def post_week(self, employee, rows, week_start=None, **extra):
        data = {"week": (week_start or self.week_start).isoformat(), "employee": str(employee.pk)}
        for row in rows:
            data.update(row)
        data.update(extra)
        return self.client.post(self.url(), data)

    def simplified(self, employee=None):
        qs = Timesheet.objects.filter(organization=self.org, accounting_mode=MODE, status="approved")
        return qs.filter(employee=employee) if employee else qs


class WeeklyEntryTests(WeeklyTimeFixture):
    def test_member_fills_week_and_cost_is_snapshot_sum(self):
        self.login(self.member)
        page = self.client.get(self.url())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "按周填报工时")
        response = self.post_week(self.member_employee, [
            self.row(0, self.week_start, self.project_target(), 240, "周一接口联调"),
            self.row(1, self.week_start + timedelta(days=1), self.project_target(), 120, "周二验收演示"),
        ])
        self.assertEqual(response.status_code, 302)
        rows = list(self.simplified(self.member_employee).order_by("work_date"))
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.minutes for row in rows], [240, 120])
        self.assertEqual(rows[0].hourly_rate_cents, 12_000)
        self.assertEqual(rows[0].cost_cents, time_cost(12_000, 240))
        summary = project_summary(self.project)
        self.assertEqual(summary["labor_cost_cents"], time_cost(12_000, 360))
        self.assertEqual(summary["cost_cents"], time_cost(12_000, 360))
        self.assertEqual(summary["labor_gap_count"], 0)
        self.assertEqual(summary["total_minutes"], 360)
        burndown = project_cost_burndown(self.project, today=self.today)
        self.assertEqual(burndown["actual_cost_cents"], time_cost(12_000, 360))
        self.assertFalse(LedgerEntry.objects.filter(project=self.project).exists())

    def test_repeated_submission_is_idempotent(self):
        self.login(self.member)
        rows = [self.row(0, self.week_start, self.project_target(), 180, "需求梳理"),
                self.row(1, self.week_start + timedelta(days=2), self.project_target(), 60, "方案评审")]
        self.assertEqual(self.post_week(self.member_employee, rows).status_code, 302)
        first = self.simplified(self.member_employee).count()
        first_cost = project_summary(self.project)["labor_cost_cents"]
        again = self.post_week(self.member_employee, rows)
        self.assertEqual(again.status_code, 302)
        self.assertEqual(self.simplified(self.member_employee).count(), first)
        self.assertEqual(project_summary(self.project)["labor_cost_cents"], first_cost)
        self.assertEqual(self.simplified(self.member_employee).first().version, 1)

    def test_rate_snapshot_is_taken_by_business_date(self):
        CostRate.objects.create(employee=self.member_employee,
                                effective_from=self.week_start + timedelta(days=2),
                                hourly_cents=18_000, basis="周中调价")
        self.login(self.member)
        response = self.post_week(self.member_employee, [
            self.row(0, self.week_start, self.project_target(), 60, "旧费率日"),
            self.row(1, self.week_start + timedelta(days=2), self.project_target(), 60, "新费率日"),
        ])
        self.assertEqual(response.status_code, 302)
        rows = {row.work_date: row for row in self.simplified(self.member_employee)}
        self.assertEqual(rows[self.week_start].hourly_rate_cents, 12_000)
        self.assertEqual(rows[self.week_start].cost_cents, 12_000)
        self.assertEqual(rows[self.week_start + timedelta(days=2)].hourly_rate_cents, 18_000)
        self.assertEqual(rows[self.week_start + timedelta(days=2)].cost_cents, 18_000)
        # A later rate never reprices rows already snapshotted by business date.
        CostRate.objects.create(employee=self.member_employee,
                                effective_from=self.week_start + timedelta(days=4),
                                hourly_cents=99_000, basis="后续调价")
        rows[self.week_start].refresh_from_db()
        self.assertEqual(rows[self.week_start].hourly_rate_cents, 12_000)

    def test_missing_rate_is_a_gap_not_a_fake_zero(self):
        self.login(self.newbie)
        response = self.post_week(self.rate_less_employee, [
            self.row(0, self.week_start, self.project_target(), 90, "新同事上手任务"),
        ])
        self.assertEqual(response.status_code, 302)
        row = self.simplified(self.rate_less_employee).get()
        self.assertIsNone(row.cost_cents)
        self.assertIsNone(row.hourly_rate_cents)
        self.assertEqual(row.cost_state, "missing_rate")
        self.assertEqual(row.get_status_display(), "待定价（缺费率）")
        summary = project_summary(self.project)
        self.assertEqual(summary["labor_gap_count"], 1)
        self.assertEqual(summary["labor_cost_cents"], 0)
        self.login(self.owner)
        page = self.client.get(reverse("dashboard"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "缺费率缺口")
        self.assertContains(page, self.rate_less_employee.name)
        self.assertContains(page, "共 1 条")  # Q-45：缺口数量由徽章表达，不再写长说明
        gap = missing_rate_gaps(self.owner)
        self.assertEqual(gap["count"], 1)
        self.assertEqual(gap["minutes"], 90)
        # Appending a matching rate closes the gap and prices the row once.
        rate = create_rate(self.owner, {"employee": self.rate_less_employee,
                                        "effective_from": self.week_start,
                                        "hourly_amount": Decimal("200"), "basis": "补录费率"})
        self.assertEqual(rate.hourly_cents, 20_000)
        row.refresh_from_db()
        self.assertEqual(row.cost_cents, time_cost(20_000, 90))
        self.assertEqual(row.cost_state, "priced")
        self.assertEqual(missing_rate_gaps(self.owner)["count"], 0)
        self.assertNotContains(self.client.get(reverse("dashboard")), "缺费率缺口")

    def test_correction_updates_the_same_row_once(self):
        self.login(self.member)
        self.post_week(self.member_employee, [
            self.row(0, self.week_start, self.project_target(), 60, "初填"),
        ])
        row = self.simplified(self.member_employee).get()
        self.assertEqual(row.cost_cents, 12_000)
        response = self.post_week(self.member_employee, [
            self.row(0, self.week_start, self.project_target(), 120, "更正后的工作说明",
                     row_id=row.pk, token=str(row.token), version=row.version),
        ])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.simplified(self.member_employee).count(), 1)
        row.refresh_from_db()
        self.assertEqual(row.minutes, 120)
        self.assertEqual(row.note, "更正后的工作说明")
        self.assertEqual(row.version, 2)
        self.assertEqual(row.cost_cents, time_cost(12_000, 120))
        self.assertEqual(project_summary(self.project)["labor_cost_cents"], time_cost(12_000, 120))
        correction = AuditEvent.objects.filter(action="time.corrected", entity="Timesheet",
                                               entity_id=str(row.pk)).latest("id")
        self.assertEqual(correction.payload["before"]["cost_cents"], 12_000)
        self.assertEqual(correction.payload["after"]["cost_cents"], 24_000)
        page = self.client.get(self.url() + f"?week={self.week_start.isoformat()}")
        self.assertContains(page, "已更正 v2")

    def test_non_project_work_is_distinguishable_and_not_project_cost(self):
        self.login(self.member)
        target = f"nonproject:{self.admin_destination.pk}"
        response = self.post_week(self.member_employee, [
            self.row(0, self.week_start, target, 120, "内部行政与例会"),
            self.row(1, self.week_start + timedelta(days=1), self.project_target(), 60, "项目交付"),
        ])
        self.assertEqual(response.status_code, 302)
        rows = list(self.simplified(self.member_employee).order_by("work_date"))
        nonproject = [row for row in rows if row.project_id is None]
        self.assertEqual(len(nonproject), 1)
        self.assertEqual(nonproject[0].non_project_destination_id, self.admin_destination.pk)
        self.assertIsNone(nonproject[0].cost_cents)
        self.assertEqual(nonproject[0].cost_state, "nonproject")
        self.assertEqual(project_summary(self.project)["labor_cost_cents"], 12_000)
        page = self.client.get(self.url() + f"?week={self.week_start.isoformat()}")
        self.assertContains(page, "非项目")
        detail = self.client.get(reverse("timesheets"))
        self.assertContains(detail, "非项目投入")

    def test_employee_identity_is_derived_server_side(self):
        self.login(self.member)
        # A crafted employee parameter must never open or write another person's week.
        page = self.client.get(self.url() + f"?employee={self.pm_employee.pk}")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, self.pm_employee.name)
        response = self.post_week(self.pm_employee, [
            self.row(0, self.week_start, self.project_target(), 60, "只写本人"),
        ])
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.simplified(self.member_employee).count(), 1)
        self.assertFalse(self.simplified(self.pm_employee).exists())

    def test_cross_tenant_and_invisible_project_are_rejected(self):
        self.login(self.member)
        response = self.post_week(self.member_employee, [
            self.row(0, self.week_start, f"project:{self.foreign_project.pk}", 60, "外部项目"),
        ])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.simplified().count(), 0)
        self.login(self.owner)  # owner has no employee profile in this fixture
        response = self.post_week(self.foreign_employee, [
            self.row(0, self.week_start, self.project_target(), 60, "外部员工"),
        ])
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.simplified().filter(employee=self.foreign_employee).count(), 0)
        foreign_destination = NonProjectDestination.objects.create(
            organization=self.foreign_org, code="ADMIN", name="外部非项目")
        self.login(self.member)
        response = self.post_week(self.member_employee, [
            self.row(0, self.week_start, f"nonproject:{foreign_destination.pk}", 60, "外部去向"),
        ])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.simplified().count(), 0)

    def test_failed_submission_shows_errors_and_keeps_entered_data(self):
        self.login(self.member)
        response = self.post_week(self.member_employee, [
            self.row(0, self.week_start, self.project_target(), 240, "保留这条已填内容"),
            self.row(1, self.week_start + timedelta(days=1), self.project_target(), 0, ""),
        ])
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "保留这条已填内容", status_code=400)
        self.assertContains(response, "请填写工作说明", status_code=400)
        self.assertContains(response, "工时须为0.1至24小时（例如 8 或 7.5）", status_code=400)
        self.assertContains(response, 'id="week-error-summary"', status_code=400)
        self.assertEqual(self.simplified().count(), 0)
        self.assertEqual(project_summary(self.project)["labor_cost_cents"], 0)

    def test_hour_input_boundaries_round_half_up_and_reject_invalid(self):
        self.login(self.member)

        def post(hours, day=0):
            return self.client.post(self.url(), {
                "week": self.week_start.isoformat(),
                "employee": str(self.member_employee.pk),
                "row-0-work_date": (self.week_start + timedelta(days=day)).isoformat(),
                "row-0-target": self.project_target(),
                "row-0-hours": hours,
                "row-0-note": "小时边界",
                "row-0-token": str(uuid.uuid4()),
                "row-0-id": "",
                "row-0-version": "",
            })

        for invalid in ("abc", "0", "-1", "24.1", "nan", "inf", ""):
            with self.subTest(hours=invalid):
                self.assertEqual(post(invalid).status_code, 400)
                self.assertContains(post(invalid), "工时须为0.1至24小时", status_code=400)
        self.assertEqual(self.simplified().count(), 0)
        # 7.51h rounds half up to 451 minutes; 24h is the allowed upper bound.
        self.assertEqual(post("7.51").status_code, 302)
        self.assertEqual(self.simplified().get().minutes, 451)
        self.assertEqual(post("24", day=1).status_code, 302)
        self.assertEqual(self.simplified().order_by("-id").first().minutes, 1440)

    def test_locked_period_future_date_and_daily_total_are_rejected(self):
        self.login(self.member)
        Organization.objects.filter(pk=self.org.pk).update(locked_through=self.week_start)
        response = self.post_week(self.member_employee, [
            self.row(0, self.week_start, self.project_target(), 60, "锁账期间"),
        ])
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "已锁账", status_code=400)
        Organization.objects.filter(pk=self.org.pk).update(locked_through=None)
        next_week = self.week_start + timedelta(days=14)
        response = self.post_week(self.member_employee, [
            self.row(0, next_week, self.project_target(), 60, "未来日期"),
        ], week_start=next_week)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "不能在未来", status_code=400)
        response = self.post_week(self.member_employee, [
            self.row(0, self.week_start, self.project_target(), 1440, "满额"),
            self.row(1, self.week_start, self.project_target(), 60, "超额"),
        ])
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "不能超过24小时", status_code=400)

    def test_pm_sees_project_totals_but_not_personal_rates(self):
        self.login(self.member)
        self.post_week(self.member_employee, [
            self.row(0, self.week_start, self.project_target(), 300, "成员投入"),
        ])
        self.login(self.pm)
        page = self.client.get(self.url() + f"?week={self.week_start.isoformat()}")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "本周项目汇总")
        self.assertContains(page, self.project.code)
        for secret in ("小时费率", "费率依据", "成本费率", self.rate_basis):
            self.assertNotContains(page, secret)
        self.login(self.member)
        own = self.client.get(self.url() + f"?week={self.week_start.isoformat()}")
        self.assertContains(own, "成员甲 的本周工时")
        self.assertNotContains(own, "本周项目汇总")
        self.assertNotContains(own, "¥")

    def test_tokenless_real_rendered_values_post_twice_create_one_row(self):
        # The old rendered blank rows carried token="" and no id/version. A double-click or
        # cached-page resubmit must still count the same投入 exactly once.
        self.login(self.member)
        row = self.row(0, self.week_start, self.project_target(), 240, "双击重试的同一投入")
        row["row-0-token"] = ""
        first = self.post_week(self.member_employee, [row])
        second = self.post_week(self.member_employee, [row])
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(self.simplified(self.member_employee).count(), 1)
        self.assertEqual(project_summary(self.project)["labor_cost_cents"], time_cost(12_000, 240))

    def test_rendered_blank_rows_carry_stable_tokens_and_are_idempotent(self):
        self.login(self.member)
        page = self.client.get(self.url() + f"?week={self.week_start.isoformat()}")
        html = page.content.decode("utf-8")
        tokens = re.findall(r'name="row-(\d+)-token" value="([0-9a-f-]{36})"', html)
        self.assertGreaterEqual(len(tokens), 2)
        self.assertNotEqual(tokens[0][1], tokens[1][1])
        index, token = tokens[0]
        dates = dict(re.findall(r'name="row-(\d+)-work_date"[^>]*value="([0-9-]{10})"', html))
        rows = [self.row(int(index), date.fromisoformat(dates[index]), self.project_target(),
                         180, "渲染表单重试", token=token)]
        first = self.post_week(self.member_employee, rows)
        second = self.post_week(self.member_employee, rows)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(self.simplified(self.member_employee).count(), 1)
        self.assertEqual(self.simplified(self.member_employee).get().version, 1)

    def test_locked_period_rate_backfill_is_rejected_until_unlocked(self):
        self.login(self.newbie)
        self.post_week(self.rate_less_employee, [
            self.row(0, self.week_start, self.project_target(), 90, "锁账前无费率投入"),
        ])
        row = self.simplified(self.rate_less_employee).get()
        self.assertIsNone(row.cost_cents)
        Organization.objects.filter(pk=self.org.pk).update(locked_through=self.week_end)
        with self.assertRaises(ValidationError):
            create_rate(self.owner, {"employee": self.rate_less_employee,
                                     "effective_from": self.week_start,
                                     "hourly_amount": Decimal("150"), "basis": "锁账补录"})
        row.refresh_from_db()
        self.assertIsNone(row.cost_cents)
        self.assertEqual(missing_rate_gaps(self.owner)["count"], 1)
        Organization.objects.filter(pk=self.org.pk).update(locked_through=None)
        create_rate(self.owner, {"employee": self.rate_less_employee,
                                 "effective_from": self.week_start,
                                 "hourly_amount": Decimal("150"), "basis": "解锁补录"})
        row.refresh_from_db()
        self.assertEqual(row.cost_cents, time_cost(15_000, 90))
        self.assertEqual(missing_rate_gaps(self.owner)["count"], 0)

    def test_extreme_week_inputs_do_not_500(self):
        self.login(self.member)
        for value in ("0001-01-01", "9999-12-31"):
            response = self.client.get(f"{self.url()}?week={value}")
            self.assertEqual(response.status_code, 200)
            posted = self.post_week(self.member_employee, [
                self.row(0, date.fromisoformat(value), self.project_target(), 60, "极端周"),
            ], week_start=date.fromisoformat(value))
            self.assertEqual(posted.status_code, 400)
        self.assertEqual(self.simplified(self.member_employee).count(), 0)

    def test_legacy_review_and_reverse_reject_simplified_rows(self):
        self.login(self.member)
        self.post_week(self.member_employee, [
            self.row(0, self.week_start, self.project_target(), 120, "免审批周填报"),
        ])
        row = self.simplified(self.member_employee).get()
        with self.assertRaises(ValidationError):
            review_record(self.finance, "time", row.pk, "approve")
        with self.assertRaises(ValidationError):
            reverse_record(self.finance, "time", row.pk, "旧冲销入口")
        row.refresh_from_db()
        self.assertEqual(row.status, "approved")
        self.assertEqual(row.version, 1)
        self.assertFalse(LedgerEntry.objects.filter(timesheet=row).exists())
        self.login(self.owner)
        response = self.client.post(reverse("review", args=["time", row.pk]),
                                    {"decision": "approve", "note": ""})
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.status, "approved")
        self.assertFalse(LedgerEntry.objects.filter(timesheet=row).exists())

    def test_auditor_is_read_only(self):
        self.login(self.auditor)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(self.post_week(self.member_employee, [
            self.row(0, self.week_start, self.project_target(), 60, "审阅账户写入"),
        ]).status_code, 403)
        self.assertEqual(self.simplified().count(), 0)

    def test_service_validation_reports_row_keys(self):
        with self.assertRaises(WeekTimeError) as caught:
            save_week(self.member, self.member_employee, self.week_start, [
                {"key": "row-3", "id": "", "token": str(uuid.uuid4()), "version": "",
                 "work_date": self.week_start.isoformat(), "target": "", "minutes": "60",
                 "note": "缺投入对象"},
            ])
        self.assertEqual(list(caught.exception.row_errors), ["row-3"])
        self.assertTrue(any("请选择项目或非项目投入" in message
                            for message in caught.exception.row_errors["row-3"]))
        self.assertEqual(self.simplified().count(), 0)
