"""PH-026 阶段 ④：费用四页签（登记费用 / 待确认 / 8 周计划 / 全部记录）服务端渲染契约。

无 JavaScript 时四个页签是真实链接（``?tab=…``），服务端按 ``active_tab`` 渲染
唯一可见面板；非当前 ``.panel-stack`` 必须在服务端 HTML 中携带 ``hidden`` 属性，
四个面板全部存在于 DOM。只覆盖渲染契约与 ``?project=`` 过滤，不改 Q-36 口径、
金额/权限/幂等或任何服务端写路径。
"""
import re

from django.test import Client, override_settings
from django.urls import reverse

from core.models import Document
from tests.test_q36_expenses import ExpenseFixture

PANEL_IDS = ("register", "pending", "plan", "all")


def panel_tag(html, panel_id):
    """Return the server-rendered opening tag of one .panel-stack div."""
    match = re.search(r'<div class="panel-stack" id="expense-panel-%s"[^>]*>' % panel_id, html)
    if match is None:
        raise AssertionError(f"panel expense-panel-{panel_id} not found in server HTML")
    return match.group(0)


def tab_anchor(html, tab_id):
    """Return the whole tab anchor element (含页签文本/计数徽章)."""
    match = re.search(r'<a class="tab" id="expense-tab-%s".*?</a>' % tab_id, html, re.S)
    if match is None:
        raise AssertionError(f"tab expense-tab-{tab_id} not found in server HTML")
    return match.group(0)


class TabPageAssertions:
    def assert_panel_visible(self, html, panel_id):
        self.assertNotIn(" hidden", panel_tag(html, panel_id),
                         f"panel expense-panel-{panel_id} should be visible")

    def assert_panel_hidden(self, html, panel_id):
        self.assertIn(" hidden", panel_tag(html, panel_id),
                      f"panel expense-panel-{panel_id} should carry the hidden attribute")

    def assert_only_panel_visible(self, html, visible_id):
        self.assert_panel_visible(html, visible_id)
        for panel_id in PANEL_IDS:
            if panel_id != visible_id:
                self.assert_panel_hidden(html, panel_id)


@override_settings(PMO_SCOPE="micro")
class ExpenseTabTests(TabPageAssertions, ExpenseFixture):
    def setUp(self):
        super().setUp()
        self.login(self.owner)

    def get_page(self, **query):
        page = self.client.get(reverse("expenses"), query)
        self.assertEqual(page.status_code, 200)
        return page

    def test_owner_default_render_is_register_tab_with_every_panel_in_dom(self):
        page = self.get_page()
        self.assertEqual(page.context["active_tab"], "register")
        html = page.content.decode()
        self.assert_only_panel_visible(html, "register")
        # 四面板全部服务端渲染在 DOM 中（无 JS 靠服务端重渲染切换）。
        for panel_id in PANEL_IDS:
            self.assertIn(f'id="expense-panel-{panel_id}"', html)
        for label in ("登记费用", "待确认", "8 周计划", "全部记录"):
            self.assertIn(label, html)
        self.assertIn("登记一笔费用或外包支出", html)
        self.assertIn("未来 8 周未付支出计划", html)
        self.assertIn("已登记费用与付款记录", html)
        self.assertIn("暂无待确认费用", html)

    def test_pm_default_render_is_also_register_tab(self):
        self.login(self.pm)
        page = self.get_page()
        self.assertEqual(page.context["active_tab"], "register")
        self.assert_only_panel_visible(page.content.decode(), "register")

    def test_tablist_aria_contract_and_real_tab_links(self):
        html = self.get_page().content.decode()
        self.assertIn('<div class="tablist" role="tablist" aria-label="费用页签" data-link-tabs>', html)
        for tab_id in PANEL_IDS:
            anchor = tab_anchor(html, tab_id)
            self.assertIn(f'href="?tab={tab_id}"', anchor)
            self.assertIn(f'aria-controls="expense-panel-{tab_id}"', anchor)
        self.assertIn('aria-selected="true" tabindex="0"', tab_anchor(html, "register"))
        for tab_id in ("pending", "plan", "all"):
            self.assertIn('aria-selected="false" tabindex="-1"', tab_anchor(html, tab_id))
        for panel_id in PANEL_IDS:
            self.assertIn(f'role="tabpanel" aria-labelledby="expense-tab-{panel_id}" tabindex="0"',
                          panel_tag(html, panel_id))

    def test_each_requested_tab_renders_only_its_panel(self):
        self.register()  # 让待确认面板有真实内容可渲染
        for tab_id in PANEL_IDS:
            with self.subTest(tab=tab_id):
                page = self.get_page(tab=tab_id)
                self.assertEqual(page.context["active_tab"], tab_id)
                html = page.content.decode()
                self.assert_only_panel_visible(html, tab_id)
                self.assertIn(f'aria-selected="true" tabindex="0"', tab_anchor(html, tab_id))
                for other in PANEL_IDS:
                    if other != tab_id:
                        self.assertIn('aria-selected="false" tabindex="-1"',
                                      tab_anchor(html, other))

    def test_invalid_or_empty_tab_falls_back_to_default_register(self):
        for bogus in ("bogus", "", "REGISTER", "unknown-tab"):
            with self.subTest(tab=bogus):
                page = self.get_page(tab=bogus)
                self.assertEqual(page.context["active_tab"], "register")
                self.assert_only_panel_visible(page.content.decode(), "register")

    def test_project_filter_is_kept_and_preserved_on_tab_links(self):
        self.register(project=self.project, note="本项目费用")
        self.register(project=self.other_project, note="他人项目费用")
        page = self.get_page(project=self.project.pk)
        self.assertEqual(page.context["project"], self.project)
        self.assertEqual(page.context["active_tab"], "register")
        snapshot = page.context["snapshot"]
        self.assertEqual(snapshot["totals"]["registered_count"], 1)
        self.assertEqual([row["document"].project_id for row in snapshot["rows"]],
                         [self.project.pk])
        html = page.content.decode()
        for tab_id in PANEL_IDS:
            self.assertIn(f'href="?tab={tab_id}&amp;project={self.project.pk}"',
                          tab_anchor(html, tab_id))
        # 过滤仍按项目生效：只出现本项目，不出现其它项目。
        self.assertIn("Q36-001", html)
        self.assertNotIn("Q36-002", html)

    def test_project_filter_applies_to_pending_tab_data(self):
        self.register(project=self.project, note="本项目待确认")
        self.register(project=self.other_project, note="他人项目待确认")
        page = self.get_page(tab="pending", project=self.project.pk)
        self.assertEqual(page.context["active_tab"], "pending")
        snapshot = page.context["snapshot"]
        self.assertEqual(len(snapshot["pending_rows"]), 1)
        html = page.content.decode()
        self.assert_only_panel_visible(html, "pending")
        self.assertIn("Q36-001", html)
        self.assertNotIn("Q36-002", html)

    def test_pending_tab_empty_state_then_gap_table_with_count_badge(self):
        page = self.get_page(tab="pending")
        html = page.content.decode()
        self.assert_only_panel_visible(html, "pending")
        self.assertIn("暂无待确认费用", html)
        self.assertNotIn('class="tab-count', html)
        self.register()
        page = self.get_page(tab="pending")
        html = page.content.decode()
        self.assertIn("待确认费用缺口", html)
        self.assertIn("确认计入成本", html)
        self.assertNotIn("暂无待确认费用", html)
        self.assertIn('class="tab-count numeric">1</span>', tab_anchor(html, "pending"))

    def test_plan_tab_renders_eight_week_plan_and_empty_state(self):
        page = self.get_page(tab="plan")
        html = page.content.decode()
        self.assert_only_panel_visible(html, "plan")
        self.assertIn("当前没有已确认未付支出", html)
        self.confirm(amount_cents=1_000_000)
        page = self.get_page(tab="plan")
        self.assertEqual(len(page.context["snapshot"]["plan"]["items"]), 1)
        html = page.content.decode()
        self.assertIn("未来 8 周未付支出计划", html)
        self.assertIn("8 周内未付", html)
        # 8 周计划面板包含来源去重后的未付支出。
        self.assertIn("Q36-001", html)

    def test_all_tab_renders_records_and_empty_state(self):
        page = self.get_page(tab="all")
        html = page.content.decode()
        self.assert_only_panel_visible(html, "all")
        self.assertIn("已登记费用与付款记录", html)
        self.assertIn("还没有登记费用", html)
        self.register()
        page = self.get_page(tab="all")
        html = page.content.decode()
        self.assertIn("已登记费用与付款记录", html)
        self.assertIn("外包开发首期", html)
        self.assertNotIn("还没有登记费用", html)

    def test_legacy_count_notice_is_rendered_in_all_panel(self):
        Document.objects.create(
            project=self.project, kind=Document.Kind.EXPENSE, title="历史专业流程费用",
            category="vendor", amount_cents=500_000, occurred_on=self.today,
            reference="LEGACY-Q36-TABS", note="历史专业流程只读费用",
            status=Document.Status.APPROVED, submitted_by=self.owner.user)
        page = self.get_page(tab="all")
        html = page.content.decode()
        self.assert_only_panel_visible(html, "all")
        self.assertIn("历史专业流程费用记录只读保留", html)

    def test_failed_register_post_with_conflicting_tab_query_still_pins_register(self):
        """POST ?tab=all 失败时仍必须回到登记页签并携带表单错误（变异可检出）。"""
        response = self.client.post(reverse("expense-register") + "?tab=all", {
            "project": self.project.pk, "category": "vendor", "amount": "not-a-number",
            "occurred_on": self.today.isoformat(), "counterparty": "演练供应商",
            "note": "冲突页签下的失败登记",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["active_tab"], "register")
        html = response.content.decode()
        self.assert_only_panel_visible(html, "register")
        self.assertGreater(len(response.context["expense_form"].errors), 0)
        self.assertIn("冲突页签下的失败登记", html)

    def test_failed_register_post_rerenders_register_tab_with_form_errors(self):
        response = self.client.post(reverse("expense-register"), {
            "project": self.project.pk, "category": "vendor", "amount": "not-a-number",
            "occurred_on": self.today.isoformat(), "counterparty": "演练供应商",
            "note": "金额非法的登记请求",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["active_tab"], "register")
        html = response.content.decode()
        self.assert_only_panel_visible(html, "register")
        self.assertGreater(len(response.context["expense_form"].errors), 0)
        self.assertIn("field-errors", html)
        # 失败后保留已提交的输入，不静默清空表单。
        self.assertIn("金额非法的登记请求", html)
        # 失败重渲染仍是 400 并停留在登记页签，而非跳转或落到默认之外的页签。
        self.assertIn("登记一笔费用或外包支出", html)
