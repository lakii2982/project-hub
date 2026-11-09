"""PH-026 阶段⑤：组织设置三页签（成员 / 成本费率 / 审计日志）服务端渲染契约。

无 JavaScript 时三个页签都是指向 ``?tab=…`` 的真实链接，服务端按 ``active_tab``
渲染唯一可见面板；三个面板全部在 DOM 中，非当前面板携带 ``hidden``。审计页签只读，
筛选仅使用 ``AuditEvent`` 真实字段；``/audit/`` 对 auditor 保留独立只读页，
owner/finance 转到设置页审计页签。测试不改权限、金额或业务写路径。
"""
import re
import uuid
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from core.models import AuditEvent, CostRate, Employee, Membership, Organization
from core.services import audit

PANEL_IDS = ("members", "rates", "audit")


def panel_tag(html, panel_id):
    """Return the server-rendered opening tag of one .panel-stack div."""
    match = re.search(r'<div class="panel-stack" id="settings-panel-%s"[^>]*>' % panel_id, html)
    if match is None:
        raise AssertionError(f"panel settings-panel-{panel_id} not found in server HTML")
    return match.group(0)


def tab_anchor(html, tab_id):
    """Return the whole tab anchor element (含页签文本)."""
    match = re.search(r'<a class="tab" id="settings-tab-%s".*?</a>' % tab_id, html)
    if match is None:
        raise AssertionError(f"tab settings-tab-{tab_id} not found in server HTML")
    return match.group(0)


def panel_html(html, panel_id):
    """Slice the rendered HTML of one panel; audit is the last panel before the footer."""
    start = html.index('id="settings-panel-%s"' % panel_id)
    if panel_id == "members":
        end = html.index('id="settings-panel-rates"', start)
    elif panel_id == "rates":
        end = html.index('id="settings-panel-audit"', start)
    else:
        end = html.index("<footer", start)
    return html[start:end]


def audit_page_section(html):
    """Slice the auditor-only /audit/ panel; base.html's logout form lives outside it."""
    start = html.index('<section class="panel">')
    return html[start:html.index("<footer", start)]


class SettingsTabAssertions:
    def assert_panel_visible(self, html, panel_id):
        self.assertNotIn(" hidden", panel_tag(html, panel_id),
                         f"panel settings-panel-{panel_id} should be visible")

    def assert_panel_hidden(self, html, panel_id):
        self.assertIn(" hidden", panel_tag(html, panel_id),
                      f"panel settings-panel-{panel_id} should carry the hidden attribute")

    def assert_only_visible(self, html, panel_id):
        for other in PANEL_IDS:
            if other == panel_id:
                self.assert_panel_visible(html, other)
            else:
                self.assert_panel_hidden(html, other)


class SettingsFixture:
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="设置页签测试公司")
        user_model = get_user_model()
        users = {role: user_model.objects.create_user(f"settings-{role}")
                 for role in ("owner", "finance", "pm", "member", "auditor")}
        cls.owner = Membership.objects.create(organization=cls.org, user=users["owner"], role="owner")
        cls.finance = Membership.objects.create(organization=cls.org, user=users["finance"], role="finance")
        cls.pm = Membership.objects.create(organization=cls.org, user=users["pm"], role="pm")
        cls.member = Membership.objects.create(organization=cls.org, user=users["member"], role="member")
        cls.auditor = Membership.objects.create(organization=cls.org, user=users["auditor"], role="auditor")
        cls.employee = Employee.objects.create(
            organization=cls.org, name="页签员工", title="工程师", capacity_minutes=480)
        cls.rate = CostRate.objects.create(
            employee=cls.employee, effective_from=date(2024, 1, 1),
            hourly_cents=12_345, basis="页签费率依据")

    def setUp(self):
        self.client = Client()

    def login(self, member=None):
        self.client.force_login((member or self.owner).user)

    def get_settings(self, **query):
        page = self.client.get(reverse("settings"), query)
        self.assertEqual(page.status_code, 200)
        return page

    def make_audit_events(self):
        """三条真实审计事件：不同操作者 / 动作 / 业务对象，供筛选与链状态测试。"""
        audit(self.owner, "settings.test.organization", self.org, {"note": "组织"})
        audit(self.finance, "settings.test.employee", self.employee, {"note": "员工"})
        audit(self.owner, "settings.test.rate", self.rate, {"note": "费率"})


class SettingsTabRenderTests(SettingsTabAssertions, SettingsFixture, TestCase):
    def test_default_members_tab_renders_only_members_panel(self):
        self.login()
        page = self.get_settings()
        self.assertEqual(page.context["active_tab"], "members")
        html = page.content.decode()
        self.assert_only_visible(html, "members")
        self.assertIn("组织信息", panel_html(html, "members"))
        self.assertIn("工作空间规则", panel_html(html, "members"))
        self.assertIn("员工档案", panel_html(html, "members"))
        self.assertIn("账户与权限", panel_html(html, "members"))
        self.assertIn("成员账号生命周期", panel_html(html, "members"))

    def test_tablist_aria_contract_and_real_link_hrefs(self):
        self.login()
        html = self.get_settings().content.decode()
        self.assertIn(
            '<div class="tablist" role="tablist" aria-label="组织设置页签" data-link-tabs>', html)
        self.assertIn('aria-selected="true" tabindex="0"', tab_anchor(html, "members"))
        for tab_id in ("rates", "audit"):
            self.assertIn('aria-selected="false" tabindex="-1"', tab_anchor(html, tab_id))
        for tab_id in PANEL_IDS:
            self.assertIn(f'id="settings-tab-{tab_id}"', tab_anchor(html, tab_id))
            self.assertIn(f'href="?tab={tab_id}"', tab_anchor(html, tab_id))
            self.assertIn(f'aria-controls="settings-panel-{tab_id}"', tab_anchor(html, tab_id))
            self.assertIn(f'role="tabpanel" aria-labelledby="settings-tab-{tab_id}" tabindex="0"',
                          panel_tag(html, tab_id))
        # 无 JavaScript 时页签必须是真实链接，不依赖脚本注入。
        self.assertNotIn("javascript:", html)

    def test_each_deep_link_renders_exactly_one_visible_panel(self):
        self.login()
        for tab_id in PANEL_IDS:
            with self.subTest(tab=tab_id):
                page = self.get_settings(tab=tab_id)
                self.assertEqual(page.context["active_tab"], tab_id)
                html = page.content.decode()
                self.assert_only_visible(html, tab_id)
                self.assertIn('aria-selected="true" tabindex="0"', tab_anchor(html, tab_id))

    def test_all_panels_are_server_rendered_even_when_hidden(self):
        self.login()
        html = self.get_settings(tab="members").content.decode()
        # 三个面板均在 DOM：员工（成员）、费率依据（成本费率）、筛选表单（审计）。
        self.assertIn("页签员工", html)
        self.assertIn("页签费率依据", html)
        self.assertIn('id="settings-panel-audit"', html)
        self.assertIn("筛选审计日志", html)

    def test_invalid_or_empty_tab_falls_back_to_members(self):
        self.login()
        for bogus in ("bogus", "", "Members", "rate"):
            with self.subTest(tab=bogus):
                page = self.get_settings(tab=bogus)
                self.assertEqual(page.context["active_tab"], "members")
                self.assert_only_visible(page.content.decode(), "members")

    def test_old_section_anchor_navigation_is_gone(self):
        self.login()
        html = self.get_settings().content.decode()
        self.assertNotIn('class="section-nav"', html)
        self.assertNotIn('href="#rates"', html)
        self.assertNotIn('href="#memberships"', html)


class SettingsTabPostTests(SettingsTabAssertions, SettingsFixture, TestCase):
    def test_invalid_org_post_stays_on_members_tab(self):
        self.login()
        # POST 来自成员页签的维护表单；即使 URL 带 ?tab=rates，失败也固定回成员页签。
        page = self.client.post(f"{reverse('settings')}?tab=rates",
                                {"name": "", "locked_through": ""})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["active_tab"], "members")
        self.assertIn("name", page.context["form"].errors)
        html = page.content.decode()
        self.assertIn(" hidden", panel_tag(html, "rates"))
        self.assertNotIn(" hidden", panel_tag(html, "members"))

    def test_valid_org_post_saves_and_redirects_to_members_tab(self):
        self.login()
        page = self.client.post(reverse("settings"),
                                {"name": "页签改名公司", "locked_through": ""})
        self.assertEqual(page.status_code, 302)
        self.assertEqual(page["Location"], reverse("settings"))
        followed = self.client.get(page["Location"], follow=True)
        self.assertEqual(followed.status_code, 200)
        self.assertEqual(followed.context["active_tab"], "members")
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "页签改名公司")
        self.assertContains(followed, "公司设置与锁账日期已更新")

    def test_invalid_membership_post_returns_400_on_members_tab(self):
        self.login()
        page = self.client.post(reverse("settings"), {
            "membership_id": str(self.pm.pk), "role": "pm", "project_scope": [], "active": ""})
        self.assertEqual(page.status_code, 400)
        self.assertEqual(page.context["active_tab"], "members")
        self.assertIn("request_token", page.context["membership_form"].errors)
        self.assert_only_visible(page.content.decode(), "members")

    def test_valid_membership_post_redirects_and_stays_on_members(self):
        self.login()
        page = self.client.post(reverse("settings"), {
            "membership_id": str(self.pm.pk), "role": "pm", "project_scope": [],
            "active": "", "request_token": str(uuid.uuid4())})
        self.assertEqual(page.status_code, 302)
        followed = self.client.get(page["Location"], follow=True)
        self.assertEqual(followed.context["active_tab"], "members")
        self.assertContains(followed, "成员账号已更新")


class SettingsRatesTabTests(SettingsTabAssertions, SettingsFixture, TestCase):
    def test_rates_tab_renders_existing_rate_data_and_entry(self):
        self.login()
        page = self.get_settings(tab="rates")
        self.assertEqual(page.context["active_tab"], "rates")
        html = page.content.decode()
        self.assert_only_visible(html, "rates")
        panel = panel_html(html, "rates")
        self.assertIn("成本费率", panel)
        self.assertIn("页签员工", panel)
        self.assertIn("¥ 123.45 / h", panel)
        self.assertIn("页签费率依据", panel)
        self.assertIn(reverse("new-object", args=["rate"]), panel)

    def test_rates_tab_keeps_empty_state(self):
        CostRate.objects.all().delete()
        self.login()
        page = self.get_settings(tab="rates")
        panel = panel_html(page.content.decode(), "rates")
        self.assertIn("暂无成本费率", panel)
        self.assertIn(reverse("new-object", args=["rate"]), panel)


class SettingsAuditTabTests(SettingsTabAssertions, SettingsFixture, TestCase):
    def test_audit_tab_empty_state_and_chain_ok_badge(self):
        self.login()
        page = self.get_settings(tab="audit")
        self.assertEqual(page.context["active_tab"], "audit")
        html = page.content.decode()
        self.assert_only_visible(html, "audit")
        panel = panel_html(html, "audit")
        self.assertIn("日志链校验通过", panel)
        self.assertIn("暂无审计记录", panel)
        self.assertNotIn("日志链校验未通过", panel)

    def test_audit_filters_match_entity_action_and_actor(self):
        self.make_audit_events()
        self.login()
        cases = {
            "entity": ("Employee", "settings.test.employee", "settings.test.organization"),
            "action": ("rate", "settings.test.rate", "settings.test.employee"),
            "actor": ("finance", "settings.test.employee", "settings.test.organization"),
        }
        for field, (value, wanted, unwanted) in cases.items():
            with self.subTest(field=field):
                panel = panel_html(self.get_settings(tab="audit", **{field: value}).content.decode(),
                                   "audit")
                self.assertIn(wanted, panel)
                self.assertNotIn(unwanted, panel)
                self.assertEqual(self.get_settings(tab="audit", **{field: value}).context["audit_filters"][field],
                                 value)

    def test_audit_filter_empty_state_keeps_conditions_and_clear_link(self):
        self.make_audit_events()
        self.login()
        page = self.get_settings(tab="audit", entity="NoSuchEntity")
        self.assertIsNotNone(page.context["audit_events"])
        self.assertEqual(len(page.context["audit_events"]), 0)
        self.assertTrue(page.context["audit_filters_active"])
        panel = panel_html(page.content.decode(), "audit")
        self.assertIn("没有符合筛选条件的审计记录", panel)
        self.assertIn("筛选条件：业务对象含“NoSuchEntity”", panel)
        self.assertIn('href="?tab=audit"', panel)
        self.assertNotIn("暂无审计记录", panel)

    def test_audit_chain_failure_shows_clear_error(self):
        self.make_audit_events()
        # 审计事件触发 append-only 约束，不能 UPDATE；直接追加一条哈希错误的记录模拟链异常。
        from django.utils import timezone
        AuditEvent.objects.create(
            organization=self.org, actor=self.owner.user, action="settings.test.tampered",
            entity="Organization", entity_id=str(self.org.pk), payload={},
            previous_hash="", event_hash="0" * 64, created_at=timezone.now())
        self.login()
        panel = panel_html(self.get_settings(tab="audit").content.decode(), "audit")
        self.assertIn("日志链校验异常", panel)
        self.assertIn("日志链校验未通过", panel)
        self.assertIn("role=\"alert\"", panel)

    def test_audit_unavailable_without_events_is_error_not_empty_log(self):
        self.login()
        with mock.patch("core.views.verify_audit", return_value=False):
            panel = panel_html(self.get_settings(tab="audit").content.decode(), "audit")
        self.assertIn("日志链校验未通过", panel)
        self.assertNotIn("暂无审计记录", panel)

    def test_audit_panel_is_read_only(self):
        self.login()
        panel = panel_html(self.get_settings().content.decode(), "audit")
        self.assertIn('method="get"', panel)
        self.assertNotIn('method="post"', panel)
        self.assertNotIn("csrfmiddlewaretoken", panel)
        self.assertNotIn("new-object", panel)
        self.assertNotIn("成员账号生命周期", panel)
        self.assertNotIn("成本费率", panel)


class AuditPageBoundaryTests(SettingsFixture, TestCase):
    def test_owner_and_finance_are_redirected_to_settings_audit_tab(self):
        for member in (self.owner, self.finance):
            with self.subTest(role=member.role):
                self.login(member)
                response = self.client.get(reverse("audit"))
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response["Location"], f"{reverse('settings')}?tab=audit")
                followed = self.client.get(response["Location"])
                self.assertEqual(followed.status_code, 200)
                self.assertEqual(followed.context["active_tab"], "audit")
                html = followed.content.decode()
                self.assertNotIn(" hidden", panel_tag(html, "audit"))

    def test_auditor_keeps_read_only_audit_page(self):
        self.make_audit_events()
        self.login(self.auditor)
        page = self.client.get(reverse("audit"))
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn("审计日志（只读）", html)
        self.assertIn("settings.test.organization", html)
        # auditor 不因这次改版获得设置页的成员/费率维护入口。
        self.assertNotIn("settings-tab-", html)
        self.assertNotIn("成员账号生命周期", html)
        self.assertNotIn("成本费率", html)
        self.assertNotIn("暂无审计记录", html)

    def test_auditor_audit_page_filters_are_read_only(self):
        self.make_audit_events()
        self.login(self.auditor)
        page = self.client.get(reverse("audit"), {"entity": "Employee"})
        self.assertEqual(page.status_code, 200)
        section = audit_page_section(page.content.decode())
        self.assertIn("settings.test.employee", section)
        self.assertNotIn("settings.test.organization", section)
        self.assertIn('method="get"', section)
        self.assertNotIn('method="post"', section)

    def test_pm_and_member_are_denied(self):
        for member in (self.pm, self.member):
            with self.subTest(role=member.role):
                self.login(member)
                self.assertEqual(self.client.get(reverse("audit")).status_code, 403)
