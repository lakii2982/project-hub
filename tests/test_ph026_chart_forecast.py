"""PH-026 阶段⑤：未来 13 周现金预测图与成本燃尽图的渲染契约。

只覆盖渲染与可访问性结构：原生 SVG、图例线型/纹理/点形、单位/区间/口径、
title/desc、真实零基线、负值可读、空态不画全零曲线、相邻精确值数据表，
以及不引入图表库/画布/远程脚本/渐变/内联样式。数值断言全部取服务端
``cash_forecast`` 行（不重算财务口径）。
"""
import re
from datetime import timedelta
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import CashPlan, Membership, Organization, Project, ProjectMember
from core.reporting import cash_forecast
from core.templatetags.money import money
from tests.test_project_detail import BurndownFixture

ROOT = Path(__file__).resolve().parent.parent


def extract_svg(html, css_class):
    """Return the outer HTML of one chart <svg> by its class, or fail loudly."""
    match = re.search(
        r'<svg class="%s".*?</svg>' % re.escape(css_class), html, re.S)
    if match is None:
        raise AssertionError(f"svg.{css_class} not found in server HTML")
    return match.group(0)


class ForecastChartFixture(TestCase):
    """Minimal deterministic org with approved in-window cash plans."""

    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.org = Organization.objects.create(name="PH026 现金图测试公司")
        user_model = get_user_model()
        cls.owner_user = user_model.objects.create_user("ph026-chart-owner", password="test-pass")
        cls.finance_user = user_model.objects.create_user("ph026-chart-finance", password="test-pass")
        cls.owner = Membership.objects.create(
            organization=cls.org, user=cls.owner_user, role=Membership.Role.OWNER)
        cls.finance = Membership.objects.create(
            organization=cls.org, user=cls.finance_user, role=Membership.Role.FINANCE)
        cls.project = Project.objects.create(
            organization=cls.org, code="PH026-001", name="现金预测项目", client="虚构客户",
            manager=cls.owner, stage=Project.Stage.EXECUTION,
            start_date=cls.today - timedelta(days=60), end_date=cls.today + timedelta(days=180),
            contract_cents=30_000_000, budget_cents=20_000_000, budget_approved=True,
            scope="测试范围", acceptance_criteria="测试验收", contract_reference="PH026-C-1")
        ProjectMember.objects.create(project=cls.project, member=cls.owner)
        cls.no_plan_project = Project.objects.create(
            organization=cls.org, code="PH026-002", name="无计划项目", client="虚构客户",
            manager=cls.owner, stage=Project.Stage.EXECUTION,
            start_date=cls.today - timedelta(days=30), end_date=cls.today + timedelta(days=120),
            contract_cents=0, budget_cents=0, budget_approved=True,
            scope="测试范围", acceptance_criteria="测试验收", contract_reference="")
        ProjectMember.objects.create(project=cls.no_plan_project, member=cls.owner)
        # 未来 13 周内的已批准计划：第 1 周流入 15,000 元、第 2 周流出 6,000 元、
        # 第 3 周流出 20,000 元，累计净流入在末段转为 -11,000 元（负值可读）。
        cls.plan_in = CashPlan.objects.create(
            project=cls.project, direction=CashPlan.Direction.IN, title="计划收款",
            counterparty="虚构客户", amount_cents=1_500_000,
            due_date=cls.today + timedelta(days=2), reference="PH026-PLAN-IN",
            created_by=cls.owner_user, approved=True, active=True)
        cls.plan_out_small = CashPlan.objects.create(
            project=cls.project, direction=CashPlan.Direction.OUT, title="计划付款",
            counterparty="虚构供应商", amount_cents=600_000,
            due_date=cls.today + timedelta(days=9), reference="PH026-PLAN-OUT-1",
            created_by=cls.owner_user, approved=True, active=True)
        cls.plan_out_big = CashPlan.objects.create(
            project=cls.project, direction=CashPlan.Direction.OUT, title="大额计划付款",
            counterparty="虚构供应商", amount_cents=2_000_000,
            due_date=cls.today + timedelta(days=16), reference="PH026-PLAN-OUT-2",
            created_by=cls.owner_user, approved=True, active=True)
        # 逾期计划不并入未来预测，但必须单列提示。
        cls.overdue_plan = CashPlan.objects.create(
            project=cls.project, direction=CashPlan.Direction.IN, title="逾期计划收款",
            counterparty="虚构客户", amount_cents=777_700,
            due_date=cls.today - timedelta(days=3), reference="PH026-PLAN-OVERDUE",
            created_by=cls.owner_user, approved=True, active=True)

    def login(self, member):
        self.client.force_login(member.user)
        return member


class ForecastChartRenderTests(ForecastChartFixture):
    def setUp(self):
        self.client = Client()
        self.login(self.owner)

    def test_finance_page_renders_forecast_chart_with_required_contract(self):
        response = self.client.get(reverse("finance"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        svg = extract_svg(html, "cashflow-chart")

        # SVG 可访问性：role=img + title/desc。
        self.assertIn('role="img"', svg)
        self.assertIn('aria-labelledby="cashflow-chart-title cashflow-chart-desc"', svg)
        self.assertIn('<title id="cashflow-chart-title">未来 13 周现金预测图', svg)
        self.assertIn('<desc id="cashflow-chart-desc">', svg)

        # 标题附近写单位、时间区间与业务口径。
        for text in ("单位：人民币元", "时间区间：未来 13 周",
                     "业务口径：依据已批准收付款计划测算"):
            self.assertIn(text, html)
        self.assertIn("计划≠实际", html)
        self.assertIn("净现金流≠利润", html)

        # 图例用文字写明线型/纹理/点形，不只靠颜色区分。
        for text in ("预计流入 · 零线以上实心柱",
                     "预计流出 · 零线以下斜线纹理柱",
                     "预计累计净流入 · 虚线菱形点"):
            self.assertIn(text, html)

        # 原生 SVG：纹理用 pattern（不是渐变），收支柱共用真实零基线。
        self.assertIn('<pattern id="cashflow-out-hatch"', html)
        self.assertIn('fill="url(#cashflow-out-hatch)"', svg)
        self.assertIn('<line class="zero" x1="96" y1="124"', svg)
        self.assertIn('<line class="zero" x1="96" y1="319"', svg)  # 累计零线落在正负之间
        self.assertNotIn("linearGradient", svg)
        self.assertNotIn("radialGradient", svg)
        self.assertNotIn("<canvas", html)
        self.assertNotIn("<script", svg)
        self.assertNotIn("<animate", html)

        # 柱高几何来自服务端行：流量刻度取流入/流出峰值 2,000,000 分。
        self.assertIn('height="75"', svg)   # 第 1 周预计流入 1,500,000 / 2,000,000
        self.assertIn('height="30"', svg)   # 第 2 周预计流出 600,000 / 2,000,000
        self.assertIn('height="100"', svg)  # 第 3 周预计流出 2,000,000 / 2,000,000

        # 负累计值可读：末周负值有负号与负值样式，并落在零线下方。
        self.assertIn('class="cashflow-end-label is-negative"', svg)
        self.assertIn("¥ -11,000.00", svg)
        self.assertIn("-11,000.00", html)

        # 渲染不得带内联样式或裸十六进制色值。
        self.assertNotIn('style="', svg)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{6}\b", svg))

    def test_dashboard_uses_the_same_forecast_chart_partial(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn('class="cashflow-chart"', html)
        self.assertIn('id="cashflow-chart-title"', html)
        self.assertIn("单位：人民币元", html)
        self.assertIn("依据已批准收付款计划测算", html)

    def test_adjacent_table_is_precise_equivalent_of_server_rows(self):
        response = self.client.get(reverse("finance"))
        html = response.content.decode()
        table_match = re.search(
            r'<caption class="cashflow-caption">.*?</table>', html, re.S)
        self.assertIsNotNone(table_match, "cashflow exact-value table not found")
        table = table_match.group(0)

        # caption 注明单位与口径。
        self.assertIn("单位：人民币元", table)
        self.assertIn("依据已批准收付款计划测算", table)
        self.assertIn("精确值以此表为准", table)

        rows = cash_forecast([self.project])
        self.assertEqual(len(rows), 13)
        # 断言服务端预测与计划一致（第 1 周流入、第 2/3 周流出、累计转负）。
        self.assertEqual(rows[0]["in_cents"], 1_500_000)
        self.assertEqual(rows[1]["out_cents"], 600_000)
        self.assertEqual(rows[2]["out_cents"], 2_000_000)
        self.assertEqual(rows[2]["balance_cents"], -1_100_000)
        self.assertEqual(rows[3]["balance_cents"], -1_100_000)

        # 每一行都与服务端 cashflow 行逐分一致（期间 + 流入 + 流出 + 累计）。
        for row in rows:
            balance_class = "text-warning" if row["balance_cents"] < 0 else ""
            expected = (
                '<th scope="row">{period}</th>'
                '<td data-label="预计流入" class="numeric text-teal">{inflow}</td>'
                '<td data-label="预计流出" class="numeric">{outflow}</td>'
                '<td data-label="预计累计净流入" class="numeric {balance_class}">{balance}</td>'
            ).format(
                period=row["period"],
                inflow=money(row["in_cents"]),
                outflow=money(row["out_cents"]),
                balance_class=balance_class,
                balance=money(row["balance_cents"]),
            )
            self.assertIn(expected, table)

        # 逾期计划单列提示，且不并入未来预测（表中没有它的金额）。
        self.assertIn("1 项逾期计划待重新排期", html)
        self.assertIn("待收 ¥ 7,777.00", html)
        self.assertNotIn("7,777.00", table)


class ForecastChartEmptyStateTests(ForecastChartFixture):
    def setUp(self):
        self.client = Client()
        self.login(self.owner)

    def test_project_without_plans_renders_empty_state_and_no_zero_chart(self):
        response = self.client.get(
            reverse("finance"), {"project": self.no_plan_project.pk})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()

        # 明确空态：无计划不画全零曲线，也不渲染图表或纹理。
        self.assertIn("未来 13 周暂无已批准的收付款计划", html)
        self.assertIn("未生成现金预测图，不绘制全零曲线", html)
        self.assertNotIn('class="cashflow-chart"', html)
        self.assertNotIn('class="cashflow-balance-line"', html)
        self.assertNotIn("cashflow-out-hatch", html)

        # 等价数据表保留结构，但正文给空态，而不是 13 行全零伪数据。
        self.assertIn('class="cashflow-caption"', html)
        self.assertIn("未生成预测明细", html)
        self.assertNotIn('<th scope="row">', html.split('id="cashflow"')[1].split("</table>")[0])


class BurndownChartRenderTests(BurndownFixture):
    """燃尽图沿用既有数值/几何，只复核图例、单位、数据表与不可用原因。"""

    def test_project_detail_renders_burndown_legend_table_and_units(self):
        client = Client()
        client.force_login(self.finance_user)
        response = client.get(reverse("project-detail", args=[self.http_project.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        svg = extract_svg(html, "cost-burndown-chart")

        self.assertIn('role="img"', svg)
        self.assertIn('aria-labelledby="cost-burndown-title cost-burndown-desc"', svg)
        self.assertIn('<title id="cost-burndown-title">', svg)
        self.assertIn('<desc id="cost-burndown-desc">单位人民币元。', svg)

        # 单位、区间（截至日）、口径与线型/点形图例。
        for text in ("单位：人民币元", "项目成本账本口径", "截至 ",
                     "实际预算余额", "计划参考", "实线圆点", "虚线方点"):
            self.assertIn(text, html)
        self.assertIn('class="cost-burndown-legend-line actual"', html)
        self.assertIn('class="cost-burndown-legend-line reference"', html)

        # 相邻等价数据表：单位/口径 caption + 精确值（7,654,321 分）。
        self.assertIn('class="cost-burndown-data"', html)
        self.assertIn("按当前批准预算重述 · 单位：人民币元", html)
        self.assertIn("尚未发生", html)
        self.assertIn("¥ 76,543.21", html)
        self.assertIn("¥ 100,000.00", html)  # 当前批准预算 1,000,000 元

        # 约束：原生 SVG，无内联样式/裸十六进制/画布/渐变/动画。
        self.assertNotIn('style="', svg)
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{6}\b", svg))
        for forbidden in ("<canvas", "linearGradient", "radialGradient", "<animate"):
            self.assertNotIn(forbidden, svg)

    def test_unapproved_budget_shows_unavailable_reason_without_chart(self):
        project = self.make_project(
            "PH026-UNAPPROVED", self.today - timedelta(days=5),
            self.today + timedelta(days=30), 600_000, approved=False)
        client = Client()
        client.force_login(self.finance_user)
        response = client.get(reverse("project-detail", args=[project.pk]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn("成本预算余额图暂不可用", html)
        self.assertIn("预算尚未批准", html)
        self.assertNotIn('class="cost-burndown-chart"', html)
        self.assertNotIn('class="cost-burndown-data"', html)


class ChartSourceConstraintTests(TestCase):
    """静态约束：图表模板与新增样式只用语义令牌，不含内联样式/裸色值。"""

    def test_chart_templates_have_no_inline_style_or_raw_hex(self):
        for name in ("templates/partials/cashflow.html",
                     "templates/partials/project_cost_burndown.html"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertNotIn('style="', text, name)
            self.assertIsNone(re.search(r"#[0-9a-fA-F]{6}\b", text), name)

    def test_forecast_chart_css_uses_semantic_tokens_only(self):
        css = (ROOT / "static/app.css").read_text(encoding="utf-8")
        start = css.index("27. PH-026 阶段⑤")
        end = css.index("响应式：断点与 ui/ui-tokens.css", start)
        section = css[start:end]
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b", section))
        self.assertIsNone(re.search(r"font-size:\s*\d+(?:\.\d+)?px", section))
        self.assertNotIn("gradient", section)
        for token in ("var(--chart-1)", "var(--chart-2)", "var(--chart-5)",
                      "var(--color-surface)", "var(--font-size-xs)"):
            self.assertIn(token, section)
