"""PH-026 阶段③：项目列表页（表格 / 筛选 / 空态 / 财务脱敏）服务端渲染契约。

复用 Q-34 夹具的最新合同额、1.0 毛利与缺口事实，只断言渲染口径：结果计数、六列表格、
q/stage/owner 筛选与组合、初始/筛选两种空态、data-label 窄屏标记及成员财务脱敏。
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from core.models import Membership, Project, ProjectMember
from tests.test_q34_project_finance import Q34FinanceFixture


@override_settings(PMO_SCOPE="micro")
class ProjectListPageTests(Q34FinanceFixture):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        # 追加一个规划阶段、负责人为 owner 的项目，用于 owner 与阶段筛选的独立分支。
        cls.owner_project = Project.objects.create(
            organization=cls.org, code="PH026-003", name="规划项目", client="规划客户",
            manager=cls.owner, stage=Project.Stage.PLANNING,
            start_date=cls.today, end_date=cls.today + timedelta(days=30),
            contract_cents=2_000_000, budget_cents=1_000_000, budget_approved=True,
            scope="范围", acceptance_criteria="验收", contract_reference="PH026-C-3")
        # 项目经理只是该项目团队成员、不是负责人：列表可见项目，但财务必须脱敏。
        ProjectMember.objects.create(project=cls.owner_project, member=cls.pm)

    def get_page(self, **params):
        response = self.client.get(reverse("projects"), params)
        return response

    def test_owner_table_renders_count_columns_amounts_and_gaps(self):
        self.login(self.owner)
        response = self.get_page()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_count"], 3)
        self.assertContains(response, "共 3 个项目")
        text = response.content.decode()
        # 六列表头与窄屏 data-label 一一对应。
        for heading in (
            '<caption class="sr-only">项目清单</caption>',
            '<th scope="col">项目</th>',
            '<th scope="col">客户</th>',
            '<th scope="col" class="numeric">合同额</th>',
            '<th scope="col" class="numeric">进度</th>',
            '<th scope="col" class="numeric">毛利</th>',
            '<th scope="col">缺口</th>',
        ):
            self.assertIn(heading, text)
        for label in ("客户", "合同额", "进度", "毛利", "缺口"):
            self.assertIn(f'data-label="{label}"', text)
        # 最新追加合同额（不是历史 100,000）与 1.0 毛利出现在表格。
        self.assertIn("120,000.00", text)
        self.assertIn("34,040.00", text)
        self.assertIn("20,000.00", text)
        # 缺口徽章：缺费率（warning）、未开票（info）、合同额未配置（warning）。
        self.assertIn('class="badge warning">缺费率/待入账工时', text)
        self.assertIn('class="badge neutral">未开票合同额', text)
        self.assertIn("合同额未配置", text)
        self.assertIn("Q34-001", text)
        self.assertIn(reverse("project-detail", args=[self.project.pk]), text)

    def test_filters_apply_keyword_stage_owner_and_combination(self):
        self.login(self.owner)
        url = reverse("projects")

        def assert_result(params, count, includes=(), excludes=()):
            response = self.client.get(url, params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["total_count"], count, params)
            self.assertContains(response, f"共 {count} 个项目")
            self.assertContains(response, "（已按条件筛选）")
            text = response.content.decode()
            for value in includes:
                self.assertIn(value, text)
            for value in excludes:
                self.assertNotIn(value, text)
            return response

        assert_result({"q": "无合同"}, 1,
                      includes=("无合同项目",), excludes=("收益现金项目", "规划项目"))
        assert_result({"q": "Q34-001"}, 1, includes=("收益现金项目",))
        assert_result({"stage": Project.Stage.PLANNING}, 1,
                      includes=("规划项目",), excludes=("收益现金项目",))
        assert_result({"stage": Project.Stage.EXECUTION}, 2,
                      includes=("收益现金项目", "无合同项目"), excludes=("规划项目",))
        assert_result({"owner": str(self.owner.pk)}, 1,
                      includes=("规划项目",), excludes=("收益现金项目",))
        selected = assert_result({"owner": str(self.owner.pk)}, 1)
        self.assertContains(selected, f'value="{self.owner.pk}" selected')
        assert_result({"owner": str(self.pm.pk)}, 2,
                      includes=("收益现金项目", "无合同项目"), excludes=("规划项目",))
        assert_result({"q": "Q34", "stage": Project.Stage.EXECUTION,
                       "owner": str(self.pm.pk)}, 2,
                      includes=("收益现金项目", "无合同项目"), excludes=("规划项目",))
        assert_result({"q": "Q34-001", "stage": Project.Stage.EXECUTION,
                       "owner": str(self.pm.pk)}, 1,
                      includes=("收益现金项目",), excludes=("无合同项目",))

    def test_filtered_empty_state_keeps_conditions_and_clear_link(self):
        self.login(self.owner)
        response = self.client.get(reverse("projects"), {"q": "不存在的项目", "owner": str(self.pm.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_count"], 0)
        self.assertContains(response, "共 0 个项目（已按条件筛选）")
        self.assertContains(response, "没有匹配的项目")
        self.assertContains(response, "保留当前筛选条件，调整或清除后重试。")
        self.assertContains(response, "清除筛选")
        self.assertContains(response, f'href="{reverse("projects")}"')
        self.assertContains(response, 'value="不存在的项目"')
        self.assertNotContains(response, "还没有项目")
        self.assertNotContains(response, 'class="project-table"')

    def test_initial_empty_state_for_user_without_projects(self):
        self.login(self.outsider)
        empty = self.get_page()
        self.assertEqual(empty.status_code, 200)
        self.assertContains(empty, "共 0 个项目")
        self.assertContains(empty, "还没有项目")
        self.assertNotContains(empty, "没有匹配的项目")
        self.assertNotContains(empty, "清除筛选")
        self.assertNotContains(empty, 'class="project-table"')
        # 成员无新建项目权限：空态只说明下一步，不展示主操作。
        self.assertNotContains(empty, "新建项目")
        # 有权限的角色（PM）在初始空态获得“新建项目”主按钮。
        user_model = get_user_model()
        empty_pm = Membership.objects.create(
            organization=self.org,
            user=user_model.objects.create_user("ph026-empty-pm", password="test-pass"),
            role="pm")
        self.login(empty_pm)
        permitted = self.get_page()
        self.assertEqual(permitted.status_code, 200)
        self.assertContains(permitted, "还没有项目")
        self.assertContains(permitted, "新建项目")
        self.assertContains(permitted, reverse("new-object", args=["project"]))

    def test_member_table_redacts_finance_amounts_and_gaps(self):
        self.login(self.member)
        response = self.get_page()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_count"], 1)
        self.assertFalse(response.context["finance_reader"])
        self.assertContains(response, "共 1 个项目")
        self.assertContains(response, "收益现金项目")
        self.assertContains(response, "示例客户")
        self.assertContains(response, 'class="progress-cell"')
        # 合同额 / 毛利 / 缺口一律“—”，不显示假零或金额。
        self.assertContains(response, '<td data-label="合同额" class="numeric">—</td>')
        self.assertContains(response, '<td data-label="毛利" class="numeric">—</td>')
        self.assertContains(response, '<td data-label="缺口"><div class="gap-cell">—</div></td>')
        self.assertNotContains(response, "120,000.00")
        self.assertNotContains(response, "34,040.00")
        self.assertNotContains(response, "¥")
        self.assertNotContains(response, "缺费率/待入账工时")

    def test_invalid_owner_filter_returns_404(self):
        self.login(self.owner)
        self.assertEqual(self.get_page(owner="abc").status_code, 404)

    def test_pm_sees_finance_only_for_projects_they_manage(self):
        """pm 可见作为团队成员的项目，但只能看到自己负责项目的财务列。"""
        self.login(self.pm)
        response = self.get_page()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_count"], 3)
        self.assertTrue(response.context["finance_reader"])
        text = response.content.decode()
        self.assertIn("规划项目", text)          # 作为团队成员可见
        self.assertIn("120,000.00", text)        # 自己负责项目的合同额
        # 非本人负责的 owner_project 合同额（20,000.00）必须脱敏为“—”。
        self.assertNotIn("¥ 20,000.00", text)
        row_start = text.index("规划项目")
        row_end = text.index("</tr>", row_start)
        row_html = text[row_start:row_end]
        self.assertIn('<td data-label="合同额" class="numeric">—</td>', row_html)
        self.assertIn('<td data-label="毛利" class="numeric">—</td>', row_html)
