"""PH-026 阶段④：工时模块三页签（工时明细 / 按周填报 / 项目汇总）服务端渲染契约。

无 JavaScript 时页签是真实 ``?tab=…`` 链接，服务端按 ``active_tab`` 渲染唯一可见
面板；非当前 ``.panel-stack`` 必须在服务端 HTML 中携带 ``hidden`` 属性，且三个面板
全部存在于 DOM。``/time/week/`` 复用同一页面（active_tab=week），POST 仍在此保存，
成功跳回 ``/time/?tab=week&week=…``，校验失败以 400 保留输入。只覆盖渲染契约，
不改 Q-35 周填报口径、权限或业务数据。
"""
import re
from datetime import timedelta

from django.test import Client
from django.urls import reverse

from tests.test_weekly_time import WeeklyTimeFixture

PANEL_IDS = ("detail", "week", "summary")
PANEL_TAG = re.compile(r'<div class="panel-stack" id="time-panel-([a-z]+)"[^>]*>')


def panel_tag(html, panel_id):
    """Return the server-rendered opening tag of one .panel-stack div."""
    match = re.search(r'<div class="panel-stack" id="time-panel-%s"[^>]*>' % panel_id, html)
    if match is None:
        raise AssertionError(f"panel time-panel-{panel_id} not found in server HTML")
    return match.group(0)


def tab_anchor(html, tab_id):
    """Return the whole tab anchor element (含页签文本)."""
    match = re.search(r'<a class="tab" id="time-tab-%s".*?</a>' % tab_id, html)
    if match is None:
        raise AssertionError(f"tab time-tab-{tab_id} not found in server HTML")
    return match.group(0)


def rendered_panel_ids(html):
    return [match.group(1) for match in PANEL_TAG.finditer(html)]


def visible_panel_ids(html):
    return [match.group(1) for match in PANEL_TAG.finditer(html)
            if " hidden" not in match.group(0)]


class TabPageAssertions:
    def assert_panel_visible(self, html, panel_id):
        self.assertNotIn(" hidden", panel_tag(html, panel_id),
                         f"panel time-panel-{panel_id} should be visible")

    def assert_panel_hidden(self, html, panel_id):
        self.assertIn(" hidden", panel_tag(html, panel_id),
                      f"panel time-panel-{panel_id} should carry the hidden attribute")

    def assert_single_visible_panel(self, html, panel_id):
        self.assertEqual(visible_panel_ids(html), [panel_id],
                         f"only time-panel-{panel_id} may be visible")
        self.assert_panel_visible(html, panel_id)
        # 三块面板始终服务端渲染在 DOM 中，非当前面板靠 hidden 隐藏。
        self.assertEqual(rendered_panel_ids(html), list(PANEL_IDS))

    def assert_selected_tab(self, html, tab_id):
        anchor = tab_anchor(html, tab_id)
        self.assertIn('aria-selected="true"', anchor)
        self.assertIn('tabindex="0"', anchor)

    def assert_unselected_tabs(self, html, tab_ids):
        for tab_id in tab_ids:
            anchor = tab_anchor(html, tab_id)
            self.assertIn('aria-selected="false"', anchor)
            self.assertIn('tabindex="-1"', anchor)


class TimeTabTests(TabPageAssertions, WeeklyTimeFixture):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def time_page(self, member=None, **query):
        self.login(member or self.member)
        page = self.client.get(reverse("timesheets"), query)
        self.assertEqual(page.status_code, 200)
        return page

    def week_page(self, member=None, **query):
        self.login(member or self.member)
        page = self.client.get(reverse("timesheets-week"), query)
        self.assertEqual(page.status_code, 200)
        return page

    def save_member_week(self):
        self.login(self.member)
        response = self.post_week(self.member_employee, [
            self.row(0, self.week_start, self.project_target(), 240, "周一接口联调"),
            self.row(1, self.week_start + timedelta(days=1), self.project_target(), 120, "周二验收演示"),
        ])
        self.assertEqual(response.status_code, 302)
        self.client.logout()

    # ---- 默认页签与 ?tab= 白名单 -------------------------------------
    def test_default_render_shows_only_week_panel(self):
        page = self.time_page()
        self.assertEqual(page.context["active_tab"], "week")
        html = page.content.decode()
        self.assert_single_visible_panel(html, "week")
        self.assert_selected_tab(html, "week")
        self.assert_unselected_tabs(html, ("detail", "summary"))
        # 明细面板保留既有内容；周填报与项目汇总也已在 DOM 中（无 JS 由服务端切换）。
        self.assertIn("工时明细", html)
        self.assertIn('class="week-form"', html)
        self.assertIn("项目汇总", html)

    def test_week_tab_renders_only_week_panel_with_aria(self):
        page = self.time_page(tab="week")
        self.assertEqual(page.context["active_tab"], "week")
        html = page.content.decode()
        self.assert_single_visible_panel(html, "week")
        self.assert_selected_tab(html, "week")
        self.assert_unselected_tabs(html, ("detail", "summary"))
        self.assertIn("保存本周工时", html)
        self.assertIn(f'action="{reverse("timesheets-week")}"', html)
        # 周导航是同一页面的真实深链，带 tab=week 与周区间。
        self.assertIn(f'href="{reverse("timesheets")}?tab=week&week=', html)
        self.assertIn("← 上一周", html)
        self.assertIn("下一周 →", html)

    def test_summary_tab_renders_only_summary_panel_for_manager(self):
        self.save_member_week()
        page = self.time_page(tab="summary", week=self.week_start.isoformat(), member=self.owner)
        self.assertEqual(page.context["active_tab"], "summary")
        html = page.content.decode()
        self.assert_single_visible_panel(html, "summary")
        self.assert_selected_tab(html, "summary")
        self.assert_unselected_tabs(html, ("detail", "week"))
        # 项目汇总表：项目 / 参与人数 / 小时 / 人工成本 / 缺口。
        for header in ("项目", "参与人数", "小时", "人工成本", "缺口"):
            self.assertIn(header, html)
        self.assertIn(self.project.code, html)
        self.assertIn(f'href="{reverse("timesheets")}?tab=summary&week=', html)
        totals = page.context["project_totals"]
        self.assertEqual(totals[0]["project_id"], self.project.pk)
        # 240 + 120 分钟在汇总表内仍以小时展示（6 小时），内部账务仍是整数分钟。
        self.assertEqual(totals[0]["minutes"], 360)
        self.assertEqual(totals[0]["people"], 1)
        self.assertIn('data-label="小时" class="numeric">6<', html)

    def test_summary_tab_shows_empty_state_for_member(self):
        page = self.time_page(tab="summary")
        self.assertEqual(page.context["active_tab"], "summary")
        html = page.content.decode()
        self.assert_single_visible_panel(html, "summary")
        self.assertIn("所选区间暂无工时", html)
        # 非 manager 不渲染项目汇总表（个人投入请走按周填报），仅给空态。
        self.assertNotIn('class="week-totals"', html)

    def test_invalid_tab_falls_back_to_week(self):
        for bogus in ("bogus", "", "overview", "week,summary"):
            with self.subTest(tab=bogus):
                page = self.time_page(tab=bogus)
                self.assertEqual(page.context["active_tab"], "week")
                html = page.content.decode()
                self.assert_single_visible_panel(html, "week")
                self.assert_selected_tab(html, "week")
                self.assert_unselected_tabs(html, ("detail", "summary"))

    # ---- /time/week/ 复用同一页面 -------------------------------------
    def test_week_route_renders_same_page_with_week_tab(self):
        page = self.week_page()
        self.assertEqual(page.context["active_tab"], "week")
        html = page.content.decode()
        self.assert_single_visible_panel(html, "week")
        self.assert_selected_tab(html, "week")
        self.assert_unselected_tabs(html, ("detail", "summary"))
        # 同一模板：页签容器、三块面板与明细内容都在 /time/week/ 的 DOM 中。
        self.assertIn('data-link-tabs', html)
        self.assertIn("工时明细", html)
        self.assertIn("本周合计", html)

    def test_successful_save_redirects_to_canonical_week_url(self):
        self.login(self.member)
        response = self.post_week(self.member_employee, [
            self.row(0, self.week_start, self.project_target(), 60, "保存后跳回周页签"),
        ])
        self.assertEqual(response.status_code, 302)
        location = response["Location"]
        self.assertEqual(location, f"{reverse('timesheets')}?tab=week&week={self.week_start.isoformat()}")
        page = self.client.get(location)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["active_tab"], "week")
        html = page.content.decode()
        self.assert_single_visible_panel(html, "week")
        self.assertContains(page, "保存后跳回周页签")

    # ---- 无 JS 的真实链接与 aria 契约 ---------------------------------
    def test_tabs_are_real_links_with_data_link_tabs(self):
        html = self.time_page().content.decode()
        self.assertIn('<div class="tablist" role="tablist" aria-label="工时页签" data-link-tabs>', html)
        for panel_id in PANEL_IDS:
            anchor = tab_anchor(html, panel_id)
            href = f'href="{reverse("timesheets")}?tab={panel_id}'
            if panel_id != "detail":
                # 周/汇总页签携带当前周，切换页签时保持同一区间。
                href += "&week="
            self.assertIn(href, anchor)
            self.assertIn('role="tab"', anchor)
            self.assertIn(f'aria-controls="time-panel-{panel_id}"', anchor)
            self.assertIn(f'role="tabpanel" aria-labelledby="time-tab-{panel_id}" tabindex="0"',
                          panel_tag(html, panel_id))

    # ---- 失败保留输入并在周面板显示错误 -------------------------------
    def test_failed_week_submission_renders_same_page_with_kept_input(self):
        self.login(self.member)
        response = self.post_week(self.member_employee, [
            self.row(0, self.week_start, self.project_target(), 240, "保留这条已填内容"),
            self.row(1, self.week_start + timedelta(days=1), self.project_target(), 0, ""),
        ])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.context["active_tab"], "week")
        html = response.content.decode()
        self.assert_single_visible_panel(html, "week")
        self.assertContains(response, "保留这条已填内容", status_code=400)
        self.assertContains(response, "请填写工作说明", status_code=400)
        self.assertContains(response, 'id="week-error-summary"', status_code=400)
        self.assertIn(f'action="{reverse("timesheets-week")}"', html)
        self.assertEqual(self.simplified().count(), 0)

    # ---- 明细的 ?project= 过滤仍生效 ---------------------------------
    def test_legacy_project_bookmark_defaults_to_detail_tab(self):
        """旧书签 /time/?project=N（无 tab 参数）应落在工时明细，而不是按周填报。"""
        page = self.time_page(project=self.project.pk)
        self.assertEqual(page.context["active_tab"], "detail")
        html = page.content.decode()
        self.assertEqual(visible_panel_ids(html), ["detail"])

    def test_detail_project_filter_still_applies(self):
        self.save_member_week()
        page = self.time_page(project=self.project.pk, member=self.owner)
        self.assertIsNotNone(page.context["selected_project"])
        self.assertEqual(page.context["selected_project"].pk, self.project.pk)
        html = page.content.decode()
        self.assertIn("筛选项目", html)
        self.assertIn("工时明细", html)
        self.assertIn(self.project.code, html)
        self.login(self.owner)
        self.assertEqual(self.client.get(
            reverse("timesheets") + f"?project={self.foreign_project.pk}").status_code, 404)
        self.assertEqual(self.client.get(
            reverse("timesheets") + "?tab=bogus&project=abc").status_code, 404)
