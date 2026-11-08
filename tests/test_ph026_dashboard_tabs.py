"""PH-026 阶段 2：工作台三页签（收益与现金 / 到期提醒 / 项目动态）服务端渲染契约。

无 JavaScript 时三个页签是真实链接（``?tab=…``），服务端按 ``active_tab`` 渲染
唯一可见面板；非当前 ``.panel-stack`` 必须在服务端 HTML 中携带 ``hidden`` 属性，
且三个面板全部存在于 DOM。只覆盖渲染契约，不改 Q-33 口径、权限或业务数据。
"""
import re

from django.test import Client, override_settings
from django.urls import reverse

from core.business_dashboard import BASIS_RECEIPT
from tests.test_q33_dashboard import DashboardFixture

PANEL_IDS = ("overview", "due", "activity")


def panel_tag(html, panel_id):
    """Return the server-rendered opening tag of one .panel-stack div."""
    match = re.search(r'<div class="panel-stack" id="dash-panel-%s"[^>]*>' % panel_id, html)
    if match is None:
        raise AssertionError(f"panel dash-panel-{panel_id} not found in server HTML")
    return match.group(0)


def tab_anchor(html, tab_id):
    """Return the whole tab anchor element (含页签文本/计数徽章)."""
    match = re.search(r'<a class="tab" id="dash-tab-%s".*?</a>' % tab_id, html)
    if match is None:
        raise AssertionError(f"tab dash-tab-{tab_id} not found in server HTML")
    return match.group(0)


class TabPageAssertions:
    def assert_panel_visible(self, html, panel_id):
        self.assertNotIn(" hidden", panel_tag(html, panel_id),
                         f"panel dash-panel-{panel_id} should be visible")

    def assert_panel_hidden(self, html, panel_id):
        self.assertIn(" hidden", panel_tag(html, panel_id),
                      f"panel dash-panel-{panel_id} should carry the hidden attribute")


@override_settings(PMO_SCOPE="micro")
class MicroDashboardTabTests(TabPageAssertions, DashboardFixture):
    def setUp(self):
        self.client = Client()
        self.login(self.owner)

    def get_dashboard(self, **query):
        page = self.client.get(reverse("dashboard"), query)
        self.assertEqual(page.status_code, 200)
        return page

    def test_default_render_shows_overview_and_hides_other_panels(self):
        page = self.get_dashboard()
        self.assertEqual(page.context["active_tab"], "overview")
        html = page.content.decode()
        self.assert_panel_visible(html, "overview")
        for panel_id in ("due", "activity"):
            self.assert_panel_hidden(html, panel_id)
        # 三面板全部存在于 DOM（无 JS 靠服务端重渲染切换，不靠 JS 注入）。
        self.assertIn('id="due-reminders"', html)
        self.assertIn("到期提醒", html)
        self.assertIn("项目动态", html)

    def test_tablist_aria_contract_and_roving_tabindex(self):
        html = self.get_dashboard().content.decode()
        self.assertIn('<div class="tablist" role="tablist" aria-label="工作台页签" data-link-tabs>', html)
        self.assertIn('aria-selected="true" tabindex="0"', tab_anchor(html, "overview"))
        for tab_id in ("due", "activity"):
            self.assertIn('aria-selected="false" tabindex="-1"', tab_anchor(html, tab_id))
        self.assertIn('href="?tab=overview"', tab_anchor(html, "overview"))
        self.assertIn('href="?tab=due"', tab_anchor(html, "due"))
        self.assertIn('href="?tab=activity"', tab_anchor(html, "activity"))
        for panel_id in PANEL_IDS:
            self.assertIn(f'role="tabpanel" aria-labelledby="dash-tab-{panel_id}" tabindex="0"',
                          panel_tag(html, panel_id))

    def test_due_tab_renders_due_panel_and_hides_others(self):
        page = self.get_dashboard(tab="due")
        self.assertEqual(page.context["active_tab"], "due")
        html = page.content.decode()
        self.assert_panel_visible(html, "due")
        self.assert_panel_hidden(html, "overview")
        self.assert_panel_hidden(html, "activity")
        self.assertIn('id="due-reminders"', html)
        self.assertIn("到期提醒", html)
        # owner 在该夹具下必有到期事项 → 页签计数徽章输出。
        self.assertIn('<span class="tab-count numeric">', tab_anchor(html, "due"))

    def test_activity_tab_renders_project_cards_and_alerts(self):
        page = self.get_dashboard(tab="activity")
        self.assertEqual(page.context["active_tab"], "activity")
        html = page.content.decode()
        self.assert_panel_visible(html, "activity")
        for panel_id in ("overview", "due"):
            self.assert_panel_hidden(html, panel_id)
        self.assertIn("项目动态", html)
        self.assertIn("需要关注", html)
        self.assertIn(reverse("project-detail", args=[self.project.pk]), html)

    def test_invalid_or_empty_tab_falls_back_to_overview(self):
        for bogus in ("bogus", ""):
            with self.subTest(tab=bogus):
                page = self.get_dashboard(tab=bogus)
                self.assertEqual(page.context["active_tab"], "overview")
                html = page.content.decode()
                self.assert_panel_visible(html, "overview")
                for panel_id in ("due", "activity"):
                    self.assert_panel_hidden(html, panel_id)

    def test_basis_param_works_alone_and_combined_with_overview_tab(self):
        alone = self.get_dashboard(basis=BASIS_RECEIPT)
        self.assertEqual(alone.context["basis"], BASIS_RECEIPT)
        self.assertEqual(alone.context["active_tab"], "overview")
        self.assert_panel_visible(alone.content.decode(), "overview")
        self.assertIn('aria-current="page">按收款确认收入', alone.content.decode())
        combined = self.get_dashboard(tab="overview", basis=BASIS_RECEIPT)
        self.assertEqual(combined.context["active_tab"], "overview")
        self.assertEqual(combined.context["basis"], BASIS_RECEIPT)
        self.assert_panel_visible(combined.content.decode(), "overview")
        self.assertIn('aria-current="page">按收款确认收入', combined.content.decode())

    def test_member_due_tab_shows_empty_state_without_count_or_links(self):
        # member 无 due 权限（due 为 None）：页签无计数徽章，面板渲染空态而非数据。
        self.login(self.member)
        default = self.get_dashboard()
        self.assertIsNone(default.context["due"])
        self.assertNotIn("tab-count", default.content.decode())
        page = self.get_dashboard(tab="due")
        self.assertEqual(page.context["active_tab"], "due")
        html = page.content.decode()
        self.assert_panel_visible(html, "due")
        self.assert_panel_hidden(html, "overview")
        self.assert_panel_hidden(html, "activity")
        self.assertIn("暂无到期事项", html)
        self.assertNotIn(reverse("due-list"), html)  # 无权限角色没有到期提醒入口链接
        self.assertNotIn("tab-count", html)


@override_settings(PMO_SCOPE="full")
class FullScopeDashboardTests(DashboardFixture):
    """非 micro 的旧版工作台保持现状：既没有页签容器，也没有 panel-stack。"""

    def test_full_scope_dashboard_renders_no_tablist_or_panels(self):
        client = Client()
        client.force_login(self.owner.user)
        page = client.get(reverse("dashboard"))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertNotIn('role="tablist"', html)
        self.assertNotIn("panel-stack", html)
        self.assertNotIn("dash-tab-overview", html)
        self.assertNotIn("工作台页签", html)
        # 旧版工作台内容保持在页签结构之外。
        self.assertIn("项目动态", html)
