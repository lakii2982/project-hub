"""PH-022 counterexamples: project-detail tabs, historical tasks and cost burndown.

Business rules under test (user-confirmed 2026-10-23):
* the only cost source is ``LedgerEntry.book == "cost"``; cash entries and
  unapproved/submitted documents never add cost;
* a negative reversal stays in the cumulative curve;
* the whole history is restated with the *current* approved budget;
* the plan reference is a straight line from budget at ``start_date`` to zero
  at ``end_date``, clamped outside that interval and never called approved;
* the actual line stops at "today"; future ledger dates are not fabricated;
* no finance reader means ``cost_burndown is None`` and no chart data in HTML.

The chart helper is coordinate-only.  No SVG markup or amount formatting is
asserted here; the browser/keyboard contract is checked by the integrator.
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.charts import line_chart
from core.models import (
    CashPlan,
    Document,
    LedgerEntry,
    Membership,
    Milestone,
    Organization,
    Project,
    ProjectMember,
    Task,
)
from core.reporting import project_cost_burndown, project_summary


class BurndownFixture(TestCase):
    """Deterministic dated fixture.  All names and amounts are fictional."""

    @classmethod
    def setUpTestData(cls):
        cls.today = date(2025, 6, 30)
        cls.org = Organization.objects.create(name="PH022燃尽测试公司")
        cls.other_org = Organization.objects.create(name="PH022外部公司")
        users = get_user_model()
        cls.owner_user = users.objects.create_user("ph022-owner", password="test-pass")
        cls.finance_user = users.objects.create_user("ph022-finance", password="test-pass")
        cls.member_user = users.objects.create_user("ph022-member", password="test-pass")
        cls.foreign_user = users.objects.create_user("ph022-foreign", password="test-pass")
        cls.owner = Membership.objects.create(
            organization=cls.org, user=cls.owner_user, role=Membership.Role.OWNER)
        cls.finance = Membership.objects.create(
            organization=cls.org, user=cls.finance_user, role=Membership.Role.FINANCE)
        cls.member = Membership.objects.create(
            organization=cls.org, user=cls.member_user, role=Membership.Role.MEMBER)
        cls.foreign = Membership.objects.create(
            organization=cls.other_org, user=cls.foreign_user, role=Membership.Role.OWNER)

        cls.project = cls.make_project("PH022-001", date(2025, 1, 1), date(2025, 12, 31), 1_000_000)
        ProjectMember.objects.create(project=cls.project, member=cls.member)
        cls.milestone = Milestone.objects.create(
            project=cls.project, name="PH022首版里程碑", due_date=date(2025, 3, 1), criteria="首版可用")
        cls.cash_plan = CashPlan.objects.create(
            project=cls.project, direction=CashPlan.Direction.IN, title="PH022回款计划",
            counterparty="虚构客户", amount_cents=400_000, due_date=date(2025, 4, 1),
            milestone=cls.milestone, reference="PH022-PLAN-1", created_by=cls.owner_user,
            approved=True)
        cls.linked_done = Task.objects.create(
            project=cls.project, name="关联里程碑任务", owner=cls.member, status=Task.Status.DONE,
            start_date=date(2025, 1, 2), due_date=date(2025, 2, 1), milestone=cls.milestone)
        cls.linked_open = Task.objects.create(
            project=cls.project, name="关联回款任务", owner=cls.member, status=Task.Status.TODO,
            start_date=date(2025, 5, 1), due_date=date(2025, 6, 20), cash_plan=cls.cash_plan)
        cls.historical_done = Task.objects.create(
            project=cls.project, name="历史已完成任务", owner=cls.member, status=Task.Status.DONE,
            start_date=date(2024, 1, 2), due_date=date(2024, 2, 1))
        cls.historical_done_two = Task.objects.create(
            project=cls.project, name="历史已完成任务二", owner=cls.member, status=Task.Status.DONE,
            start_date=date(2024, 3, 2), due_date=date(2024, 4, 1))
        cls.historical_open = Task.objects.create(
            project=cls.project, name="历史逾期未链接任务", owner=cls.member, status=Task.Status.TODO,
            start_date=date(2025, 1, 5), due_date=date(2025, 2, 5))

        # Before project start: must be kept in the cumulative curve.
        cls.pre_start_cost = cls.add_cost(
            cls.project, 200_000, date(2024, 12, 20), "pre-start")
        # Normal cost, later partly reversed.
        cls.cost_a = cls.add_cost(cls.project, 300_000, date(2025, 2, 10), "cost-a")
        cls.cost_b = cls.add_cost(cls.project, 150_000, date(2025, 5, 15), "cost-b")
        cls.reversal = cls.add_cost(
            cls.project, -100_000, date(2025, 6, 1), "reversal", reverses=cls.cost_a)
        # Future-dated ledger rows are not "actual" yet.
        cls.future_cost = cls.add_cost(cls.project, 250_000, date(2025, 9, 1), "future")
        # Cash movements must never become cost.
        cls.add_cash(cls.project, "cash_in", 500_000, date(2025, 2, 15), "cash-in")
        cls.add_cash(cls.project, "cash_out", 200_000, date(2025, 3, 1), "cash-out")
        # Submitted expense without a ledger entry is not cost.
        cls.pending_document = cls.add_document(
            cls.project, Document.Kind.EXPENSE, 999_999, date(2025, 4, 1), "pending")

        cls.other_project = cls.make_project(
            "PH022-OTHER", date(2025, 1, 1), date(2025, 12, 31), 900_000)
        cls.add_cost(cls.other_project, 777_777, date(2025, 3, 3), "other-project")
        cls.foreign_project = cls.make_project(
            "PH022-FOREIGN", date(2025, 1, 1), date(2025, 12, 31), 800_000, manager=cls.foreign)
        cls.add_cost(cls.foreign_project, 888_888, date(2025, 3, 4), "foreign-project")

        # Separate HTTP project around the real current date; views call the
        # reporting function without an explicit today argument.
        cls.http_today = timezone.localdate()
        cls.http_project = cls.make_project(
            "PH022-HTTP", cls.http_today - timedelta(days=60),
            cls.http_today + timedelta(days=120), 10_000_000, manager=cls.owner)
        ProjectMember.objects.create(project=cls.http_project, member=cls.member)
        cls.http_secret_cents = 7_654_321
        cls.add_cost(cls.http_project, cls.http_secret_cents,
                     cls.http_today - timedelta(days=10), "http-secret")

    @classmethod
    def make_project(cls, code, start, end, budget, manager=None, approved=True):
        return Project.objects.create(
            organization=cls.org if manager is not cls.foreign else cls.other_org,
            code=code, name=f"{code}虚构项目", client="虚构客户", manager=manager or cls.owner,
            stage=Project.Stage.EXECUTION, start_date=start, end_date=end,
            budget_cents=budget, budget_approved=approved, scope="测试范围",
            acceptance_criteria="测试验收", contract_reference="PH022-CONTRACT")

    @classmethod
    def add_document(cls, project, kind, amount, day, key):
        return Document.objects.create(
            project=project, kind=kind, title=f"PH022 {key}", category="ph022",
            amount_cents=abs(amount), tax_cents=0, occurred_on=day,
            reference=f"PH022-{key}-{uuid.uuid4().hex}", submitted_by=cls.owner_user)

    @classmethod
    def add_cost(cls, project, amount, day, key, reverses=None):
        document = cls.add_document(project, Document.Kind.EXPENSE, amount, day, key)
        return LedgerEntry.objects.create(
            project=project, book=LedgerEntry.Book.COST, amount_cents=amount, category="ph022",
            occurred_on=day, document=document, reverses=reverses,
            posting_key=f"ph022:{key}:{uuid.uuid4().hex}")

    @classmethod
    def add_cash(cls, project, book, amount, day, key):
        kind = Document.Kind.RECEIPT if book == "cash_in" else Document.Kind.PAYMENT
        document = cls.add_document(project, kind, amount, day, key)
        return LedgerEntry.objects.create(
            project=project, book=book, amount_cents=amount, category="ph022",
            occurred_on=day, document=document, posting_key=f"ph022:{key}:{uuid.uuid4().hex}")

    # -- assertions -----------------------------------------------------
    def chart_series(self, result):
        chart = result["chart"]
        self.assertIsInstance(chart, dict)
        self.assertEqual(chart["viewbox"], "0 0 760 300")
        for key in ("left", "right", "top", "bottom"):
            Decimal(chart[key])
        self.assertIsInstance(chart["zero_y"], str)
        Decimal(chart["zero_y"])
        self.assertEqual([s["key"] for s in chart["series"]], ["actual", "plan"])
        self.assertFalse(chart["series"][0]["reference"])
        self.assertTrue(chart["series"][1]["reference"])
        for series in chart["series"]:
            self.assertTrue(series["label"])
            for pair in series["points"].split():
                x, y = pair.split(",")
                self.assertGreaterEqual(Decimal(x), Decimal(chart["left"]))
                self.assertLessEqual(Decimal(x), Decimal(chart["right"]))
                self.assertGreaterEqual(Decimal(y), Decimal(chart["top"]))
                self.assertLessEqual(Decimal(y), Decimal(chart["bottom"]))
            for dot in series["dots"]:
                Decimal(dot["cx"])
                Decimal(dot["cy"])
        for tick in chart["y_ticks"]:
            Decimal(tick["y"])
            self.assertTrue(tick["label"])
        for tick in chart["x_ticks"]:
            Decimal(tick["x"])
            self.assertTrue(tick["label"])
        return chart

    def assert_rows_contract(self, result, today):
        rows = result["rows"]
        self.assertTrue(rows)
        previous = None
        for row in rows:
            self.assertIsInstance(row["date"], date)
            if previous is not None:
                self.assertLess(previous, row["date"])
            previous = row["date"]
            self.assertIsInstance(row["plan_cents"], int)
            if row["date"] > today:
                self.assertIsNone(row["actual_cents"])
            else:
                self.assertIsInstance(row["actual_cents"], int)
        return rows


class ProjectCostBurndownTests(BurndownFixture):
    """Pure reporting function: accounting, boundary and coordinate counterexamples."""

    def test_cost_uses_only_cost_ledger_and_never_cash_or_pending_documents(self):
        result = project_cost_burndown(self.project, today=self.today)

        self.assertTrue(result["available"])
        self.assertEqual(result["as_of"], self.today)
        self.assertEqual(result["budget_cents"], 1_000_000)
        # 200,000 + 300,000 + 150,000 - 100,000; future/cash/pending/foreign excluded.
        self.assertEqual(result["actual_cost_cents"], 550_000)
        self.assertEqual(result["remaining_cents"], 450_000)
        rows = self.assert_rows_contract(result, self.today)
        self.assertEqual(
            sum(e.amount_cents for e in LedgerEntry.objects.filter(
                project=self.project, book=LedgerEntry.Book.COST, occurred_on__lte=self.today)),
            result["actual_cost_cents"])
        by_date = {row["date"]: row for row in rows}
        # Row balances are budget minus cumulative cost, not the cost total.
        self.assertEqual(by_date[self.today]["actual_cents"], result["remaining_cents"])
        self.assertEqual(by_date[self.today]["actual_cents"], 450_000)
        self.assertIn(self.today, by_date)
        self.assertEqual(by_date[self.future_cost.occurred_on]["actual_cents"], None)
        chart = self.chart_series(result)
        actual_points = chart["series"][0]["points"].split()
        dated_actual_rows = [row for row in rows if row["actual_cents"] is not None]
        self.assertGreaterEqual(len(actual_points), 1)
        # The SVG line may compress flat segments, but must never invent a
        # future-dated actual point.
        self.assertLessEqual(len(actual_points), len(dated_actual_rows))

    def test_reversal_restores_balance_and_negative_rows_are_kept(self):
        result = project_cost_burndown(self.project, today=self.today)
        by_date = {row["date"]: row for row in result["rows"]}

        # Balance = 1,000,000 approved budget - cumulative cost (650k, then
        # 550k after the -100k reversal), so the reversal raises the balance.
        self.assertEqual(by_date[date(2025, 5, 15)]["actual_cents"], 350_000)
        self.assertEqual(by_date[date(2025, 6, 1)]["actual_cents"], 450_000)
        self.assertGreater(by_date[date(2025, 6, 1)]["actual_cents"],
                           by_date[date(2025, 5, 15)]["actual_cents"])
        self.assertEqual(result["actual_cost_cents"], 550_000)

    def test_overspend_returns_negative_balance_and_chart_crosses_zero(self):
        project = self.make_project(
            "PH022-OVER", date(2025, 1, 1), date(2025, 12, 31), 400_000)
        self.add_cost(project, 500_000, date(2025, 3, 1), "over-cost")
        result = project_cost_burndown(project, today=self.today)

        self.assertTrue(result["available"])
        self.assertEqual(result["actual_cost_cents"], 500_000)
        self.assertEqual(result["remaining_cents"], -100_000)
        chart = self.chart_series(result)
        zero_y = Decimal(chart["zero_y"])
        actual = [pair.split(",") for pair in chart["series"][0]["points"].split()]
        self.assertGreater(Decimal(actual[-1][1]), zero_y)

    def test_negative_net_cost_can_exceed_budget_without_being_clipped(self):
        project = self.make_project(
            "PH022-ABOVE", date(2025, 1, 1), date(2025, 12, 31), 100_000)
        self.add_cost(project, 50_000, date(2025, 2, 1), "small-cost")
        self.add_cost(project, -80_000, date(2025, 3, 1), "negative-adjustment")
        result = project_cost_burndown(project, today=self.today)

        self.assertEqual(result["actual_cost_cents"], -30_000)
        self.assertEqual(result["remaining_cents"], 130_000)
        self.assertGreater(result["remaining_cents"], result["budget_cents"])
        chart = self.chart_series(result)
        zero_y = Decimal(chart["zero_y"])
        actual = [pair.split(",") for pair in chart["series"][0]["points"].split()]
        self.assertLess(Decimal(actual[-1][1]), zero_y)  # above budget in SVG coordinates

    def test_current_budget_restates_every_history_point(self):
        # A stale baseline document must not be used to rebuild old budget versions.
        self.add_document(self.project, Document.Kind.BASELINE, 700_000, date(2024, 11, 1), "old-baseline")
        result = project_cost_burndown(self.project, today=self.today)
        by_date = {row["date"]: row for row in result["rows"]}

        self.assertTrue(result["available"])
        self.assertEqual(result["budget_cents"], 1_000_000)
        self.assertIn("当前批准预算", result["note"])
        self.assertEqual(by_date[date(2025, 1, 1)]["plan_cents"], 1_000_000)
        self.assertEqual(by_date[date(2025, 12, 31)]["plan_cents"], 0)
        plan = [(row["date"], row["plan_cents"]) for row in result["rows"]
                if date(2025, 1, 1) <= row["date"] <= date(2025, 12, 31)]
        self.assertEqual(plan, sorted(plan, key=lambda item: item[0]))
        self.assertEqual([value for _, value in plan], sorted(
            [value for _, value in plan], reverse=True))

    def test_plan_line_clamps_outside_dates_and_survives_degenerate_ranges(self):
        # Future start with a pre-start cost: plan stays at the start budget.
        future = self.make_project(
            "PH022-FUTURE", self.today + timedelta(days=30), self.today + timedelta(days=90), 200_000)
        self.add_cost(future, 50_000, self.today - timedelta(days=10), "future-pre-start")
        future_result = project_cost_burndown(future, today=self.today)
        self.assertTrue(future_result["available"])
        self.chart_series(future_result)
        self.assert_rows_contract(future_result, self.today)
        for row in future_result["rows"]:
            if row["date"] < future.start_date:
                self.assertEqual(row["plan_cents"], 200_000)
        self.assertEqual(future_result["actual_cost_cents"], 50_000)

        # Past end: plan is zero after the end date, including today.
        late = self.make_project(
            "PH022-LATE", self.today - timedelta(days=400), self.today - timedelta(days=40), 300_000)
        self.add_cost(late, 20_000, self.today - timedelta(days=20), "late-cost")
        late_result = project_cost_burndown(late, today=self.today)
        self.assertTrue(late_result["available"])
        self.chart_series(late_result)
        for row in late_result["rows"]:
            if row["date"] > late.end_date:
                self.assertEqual(row["plan_cents"], 0)
        late_by_date = {row["date"]: row for row in late_result["rows"]}
        self.assertEqual(late_by_date[late.end_date]["plan_cents"], 0)

        # start_date == end_date must not divide by zero.
        same_day = self.make_project("PH022-SAME", self.today - timedelta(days=10),
                                     self.today - timedelta(days=10), 90_000)
        self.add_cost(same_day, 10_000, self.today - timedelta(days=10), "same-day")
        same_result = project_cost_burndown(same_day, today=self.today)
        self.assertTrue(same_result["available"])
        self.chart_series(same_result)
        self.assert_rows_contract(same_result, self.today)
        self.assertTrue(all(isinstance(row["plan_cents"], int) for row in same_result["rows"]))

    def test_actual_line_stops_at_today_and_keeps_pre_start_costs(self):
        result = project_cost_burndown(self.project, today=self.today)
        rows = self.assert_rows_contract(result, self.today)
        dates = {row["date"] for row in rows}

        self.assertIn(self.project.start_date, dates)
        self.assertIn(self.project.end_date, dates)
        self.assertIn(self.today, dates)
        self.assertIn(self.pre_start_cost.occurred_on, dates)
        missing = {row["date"] for row in rows if row["actual_cents"] is None}
        self.assertIn(self.future_cost.occurred_on, missing)
        self.assertIn(self.project.end_date, missing)
        self.assertTrue(all(day > self.today for day in missing))
        self.assertEqual(result["as_of"], self.today)

    def test_zero_budget_and_no_cost_still_render_a_readable_chart(self):
        no_cost = self.make_project(
            "PH022-EMPTY", date(2025, 1, 1), date(2025, 12, 31), 250_000)
        empty_result = project_cost_burndown(no_cost, today=self.today)
        self.assertTrue(empty_result["available"])
        self.assertEqual(empty_result["actual_cost_cents"], 0)
        self.assertEqual(empty_result["remaining_cents"], 250_000)
        self.chart_series(empty_result)
        self.assert_rows_contract(empty_result, self.today)
        self.assertIsNotNone(empty_result["chart"]["zero_y"])

        zero_budget = self.make_project(
            "PH022-ZERO", date(2025, 1, 1), date(2025, 12, 31), 0)
        zero_empty = project_cost_burndown(zero_budget, today=self.today)
        self.assertTrue(zero_empty["available"])
        self.assertEqual(zero_empty["budget_cents"], 0)
        self.assertEqual(zero_empty["remaining_cents"], 0)
        self.chart_series(zero_empty)
        self.add_cost(zero_budget, 100, date(2025, 3, 1), "zero-over")
        zero_over = project_cost_burndown(zero_budget, today=self.today)
        self.assertEqual(zero_over["remaining_cents"], -100)
        self.chart_series(zero_over)

    def test_unapproved_budget_has_empty_state_and_no_chart(self):
        project = self.make_project(
            "PH022-UNAPPROVED", date(2025, 1, 1), date(2025, 12, 31), 500_000, approved=False)
        self.add_cost(project, 100_000, date(2025, 2, 1), "unapproved-cost")
        result = project_cost_burndown(project, today=self.today)

        self.assertFalse(result["available"])
        self.assertTrue(result["reason"])
        self.assertIn("批准", result["reason"])
        self.assertIsNone(result["chart"])
        self.assertEqual(result["rows"], [])

    def test_other_project_and_other_organization_rows_are_isolated(self):
        result = project_cost_burndown(self.project, today=self.today)
        self.assertEqual(result["actual_cost_cents"], 550_000)
        self.assertNotIn(777_777, [row["actual_cents"] for row in result["rows"]])
        self.assertNotIn(888_888, [row["actual_cents"] for row in result["rows"]])
        other = project_cost_burndown(self.other_project, today=self.today)
        self.assertEqual(other["actual_cost_cents"], 777_777)
        foreign = project_cost_burndown(self.foreign_project, today=self.today)
        self.assertEqual(foreign["actual_cost_cents"], 888_888)

    def test_linked_tasks_drive_progress_while_history_keeps_status_rules(self):
        today = timezone.localdate()
        project = self.make_project(
            "PH022-SUMMARY", today - timedelta(days=30), today + timedelta(days=30), 0)
        milestone = Milestone.objects.create(
            project=project, name="汇总里程碑", due_date=today - timedelta(days=5), criteria="测试")
        Task.objects.create(
            project=project, name="汇总关联完成", owner=self.member, status=Task.Status.DONE,
            start_date=today - timedelta(days=20), due_date=today - timedelta(days=10),
            milestone=milestone)
        Task.objects.create(
            project=project, name="汇总关联逾期", owner=self.member, status=Task.Status.TODO,
            start_date=today - timedelta(days=20), due_date=today - timedelta(days=1),
            milestone=milestone)
        Task.objects.create(
            project=project, name="汇总历史完成", owner=self.member, status=Task.Status.DONE,
            start_date=today - timedelta(days=20), due_date=today - timedelta(days=10))
        Task.objects.create(
            project=project, name="汇总历史逾期", owner=self.member, status=Task.Status.TODO,
            start_date=today - timedelta(days=20), due_date=today - timedelta(days=2))

        summary = project_summary(project)
        # One done of two linked tasks; unlinked historical tasks are not progress.
        self.assertEqual(summary["progress"], 50)
        self.assertEqual(summary["overdue_tasks"], 1)


class ChartHelperTests(TestCase):
    """core.charts is a pure integer/Decimal coordinate helper, never SVG."""

    def test_line_chart_returns_decimal_coordinates_and_keeps_negative_values(self):
        rows = [
            {"date": date(2025, 1, 1), "label": "01.01", "actual": 100_000},
            {"date": date(2025, 1, 2), "label": "01.02", "actual": -50_000},
            {"date": date(2025, 1, 4), "label": "01.04", "actual": 0},
        ]
        chart = line_chart(rows, [{"key": "actual", "label": "余额", "reference": False}])
        self.assertEqual(chart["viewbox"], "0 0 760 300")
        self.assertIsNotNone(chart["zero_y"])
        chart_series = chart["series"][0]
        self.assertEqual(chart_series["key"], "actual")
        points = [pair.split(",") for pair in chart_series["points"].split()]
        self.assertEqual(len(points), 3)
        self.assertGreater(Decimal(points[1][1]), Decimal(chart["zero_y"]))
        self.assertLess(Decimal(points[0][1]), Decimal(chart["zero_y"]))
        # A one-day gap is half of the following two-day gap.
        first_gap = Decimal(points[1][0]) - Decimal(points[0][0])
        second_gap = Decimal(points[2][0]) - Decimal(points[1][0])
        self.assertLess(abs(second_gap - first_gap * 2), Decimal("0.02"))
        self.assertNotIn("<svg", str(chart))

    def test_line_chart_rejects_float_money(self):
        with self.assertRaises((TypeError, ValueError)):
            line_chart(
                [{"date": date(2025, 1, 1), "actual": 1.5}],
                [{"key": "actual", "label": "余额", "reference": False}],
            )


class ProjectDetailHTTPTests(BurndownFixture):
    """Real routing: tab markup, permissions, preserved actions and context safety."""

    def test_horizontal_tabs_have_deep_links_and_accessible_roles(self):
        self.client.force_login(self.owner_user)
        response = self.client.get(reverse("project-detail", args=[self.project.pk]))

        self.assertEqual(response.status_code, 200)
        for anchor in ("overview", "tasks", "milestones", "finance", "risks", "team"):
            self.assertContains(response, f'href="#{anchor}"')
            self.assertContains(response, f'id="{anchor}"')
        self.assertContains(response, 'role="tablist"')
        self.assertContains(response, 'role="tab"')
        self.assertContains(response, 'role="tabpanel"')
        self.assertContains(response, 'aria-selected=')
        self.assertContains(response, 'aria-controls=')
        self.assertContains(response, 'tabindex="0"')
        self.assertContains(response, 'tabindex="-1"')
        # Overview is the default; no-JS anchor navigation remains present.
        self.assertContains(response, "交付概览")
        self.assertContains(response, "里程碑")
        self.assertContains(response, "风险与问题")
        self.assertContains(response, "项目团队")

    def test_existing_actions_and_forms_are_not_lost(self):
        self.client.force_login(self.owner_user)
        response = self.client.get(reverse("project-detail", args=[self.project.pk]))

        for url in (
            reverse("project-edit", args=[self.project.pk]),
            reverse("transition", args=[self.project.pk]),
            reverse("new-object", args=["task"]),
            reverse("new-object", args=["milestone"]),
            reverse("new-object", args=["document"]),
            reverse("new-object", args=["plan"]),
            reverse("new-object", args=["risk"]),
            reverse("new-object", args=["team"]),
            reverse("task-update", args=[self.linked_open.pk]),
            reverse("milestone-accept", args=[self.milestone.pk]),
        ):
            self.assertContains(response, url)

    def test_finance_reader_gets_chart_context_and_visible_explanation(self):
        self.client.force_login(self.finance_user)
        response = self.client.get(reverse("project-detail", args=[self.http_project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("cost_burndown", response.context)
        burndown = response.context["cost_burndown"]
        self.assertIsNotNone(burndown)
        self.assertTrue(burndown["available"])
        self.assertEqual(burndown["actual_cost_cents"], self.http_secret_cents)
        self.assertEqual(burndown["budget_cents"], 10_000_000)
        self.assertEqual(burndown["remaining_cents"], 10_000_000 - self.http_secret_cents)
        self.assertContains(response, "成本预算余额")
        self.assertContains(response, "按当前批准预算计算")

    def test_member_without_finance_read_gets_none_and_no_data_in_html(self):
        self.client.force_login(self.member_user)
        response = self.client.get(reverse("project-detail", args=[self.http_project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("cost_burndown", response.context)
        self.assertIsNone(response.context["cost_burndown"])
        self.assertNotContains(response, "按当前批准预算计算")
        self.assertNotContains(response, "data-cost-burndown")
        self.assertNotContains(response, "cost_burndown")
        self.assertNotContains(response, "7654321")
        self.assertNotContains(response, "76,543")
        self.assertNotContains(response, 'href="#finance"')
        self.assertContains(response, "我的费用单据")

    def test_auditor_with_finance_read_gets_numbers_but_no_burndown_chart(self):
        users = get_user_model()
        auditor_user = users.objects.create_user("ph022-auditor", password="test-pass")
        Membership.objects.create(
            organization=self.org, user=auditor_user, role=Membership.Role.AUDITOR)

        self.client.force_login(auditor_user)
        response = self.client.get(reverse("project-detail", args=[self.http_project.pk]))

        self.assertEqual(response.status_code, 200)
        # Auditor keeps the pre-existing finance figures/documents, but the
        # newly added cost burndown chart is gated to finance readers only.
        self.assertTrue(response.context["can_finance"])
        self.assertContains(response, 'href="#finance"')
        self.assertIn("cost_burndown", response.context)
        self.assertIsNone(response.context["cost_burndown"])
        self.assertNotContains(response, "cost-burndown-chart")
        self.assertNotContains(response, "cost-burndown-plot")
        self.assertNotContains(response, "cost-burndown-title")
        self.assertNotContains(response, "按当前批准预算计算")

    def test_member_own_expenses_remain_visible_but_other_users_do_not(self):
        own_document = self.add_document(
            self.http_project, Document.Kind.EXPENSE, 12_300, self.today, "member-own")
        own_document.submitted_by = self.member_user
        own_document.title = "会员自己的费用"
        own_document.save(update_fields=["submitted_by", "title"])
        other_document = self.add_document(
            self.http_project, Document.Kind.EXPENSE, 45_600, self.today, "other-expense")
        other_document.title = "其他人的费用"
        other_document.save(update_fields=["title"])

        self.client.force_login(self.member_user)
        response = self.client.get(reverse("project-detail", args=[self.http_project.pk]))
        document_ids = {document.pk for document in response.context["documents"]}

        self.assertIn(own_document.pk, document_ids)
        self.assertNotIn(other_document.pk, document_ids)
        self.assertIsNone(response.context["cost_burndown"])
        self.assertContains(response, "我的费用单据")

    def test_linked_and_historical_tasks_are_split_without_losing_records(self):
        self.client.force_login(self.owner_user)
        response = self.client.get(reverse("project-detail", args=[self.project.pk]))

        self.assertIn("tasks", response.context)
        self.assertIn("historical_tasks", response.context)
        self.assertEqual(
            {task.pk for task in response.context["tasks"]},
            {self.linked_done.pk, self.linked_open.pk})
        self.assertEqual(
            {task.pk for task in response.context["historical_tasks"]},
            {self.historical_done.pk, self.historical_done_two.pk, self.historical_open.pk})
        for task in (self.linked_done, self.linked_open, self.historical_done,
                     self.historical_open):
            self.assertContains(response, task.name)
        self.assertContains(response, "历史")

    def test_unapproved_budget_detail_shows_pending_state_not_a_baseline(self):
        project = self.make_project(
            "PH022-HTTP-UNAPPROVED", self.today - timedelta(days=5), self.today + timedelta(days=30),
            600_000, approved=False)
        ProjectMember.objects.create(project=project, member=self.member)
        self.client.force_login(self.finance_user)
        response = self.client.get(reverse("project-detail", args=[project.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.context["cost_burndown"])
        burndown = response.context["cost_burndown"]
        self.assertFalse(burndown["available"])
        self.assertIsNone(burndown["chart"])
        self.assertContains(response, "批准")
        self.assertNotContains(response, "按当前批准预算计算")

    def test_cross_org_and_unassigned_project_keep_existing_404_path(self):
        self.client.force_login(self.foreign_user)
        self.assertEqual(
            self.client.get(reverse("project-detail", args=[self.project.pk])).status_code, 404)
        self.assertEqual(
            self.client.get(reverse("project-detail", args=[self.foreign_project.pk])).status_code,
            200)

        self.client.force_login(self.member_user)
        self.assertEqual(
            self.client.get(reverse("project-detail", args=[self.other_project.pk])).status_code,
            404)
        self.assertEqual(
            self.client.get(reverse("project-detail", args=[self.project.pk])).status_code, 200)
