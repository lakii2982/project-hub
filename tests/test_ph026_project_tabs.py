"""PH-026 阶段 3：项目详情六页签（?tab= 服务端渲染）契约。

无 JavaScript 时页签是真实链接（``?tab=…``），服务端按 ``active_tab`` 渲染唯一
可见面板；非当前 ``.project-panel`` 必须在服务端 HTML 中携带 ``hidden`` 属性，
且当前用户有权访问的面板全部存在于 DOM。只覆盖渲染与权限回退契约，不改
Q-33/Q-34 口径、权限或业务数据。
"""
import re
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Document, Membership, Organization, Project, ProjectMember

OWNER_TABS = ("overview", "tasks", "milestones", "finance", "risks", "team")
MEMBER_TABS = ("overview", "tasks", "milestones", "risks", "team", "expenses")
PANEL_TAG = re.compile(r'<section id="project-panel-([a-z]+)"[^>]*>')


def panel_tag(html, panel_id):
    """Return the server-rendered opening tag of one .project-panel section."""
    match = re.search(r'<section id="project-panel-%s"[^>]*>' % panel_id, html)
    if match is None:
        raise AssertionError(f"panel project-panel-{panel_id} not found in server HTML")
    return match.group(0)


def tab_anchor(html, tab_id):
    """Return the whole tab anchor element (含页签文本)."""
    match = re.search(r'<a class="tab" id="project-tab-%s".*?</a>' % tab_id, html)
    if match is None:
        raise AssertionError(f"tab project-tab-{tab_id} not found in server HTML")
    return match.group(0)


def rendered_panel_ids(html):
    return [match.group(1) for match in PANEL_TAG.finditer(html)]


def visible_panel_ids(html):
    return [match.group(1) for match in PANEL_TAG.finditer(html)
            if " hidden" not in match.group(0)]


class TabPageAssertions:
    def assert_panel_visible(self, html, panel_id):
        self.assertNotIn(" hidden", panel_tag(html, panel_id),
                         f"panel project-panel-{panel_id} should be visible")

    def assert_panel_hidden(self, html, panel_id):
        self.assertIn(" hidden", panel_tag(html, panel_id),
                      f"panel project-panel-{panel_id} should carry the hidden attribute")

    def assert_single_visible_panel(self, html, panel_id):
        self.assertEqual(visible_panel_ids(html), [panel_id],
                         f"only project-panel-{panel_id} may be visible")
        self.assert_panel_visible(html, panel_id)

    def assert_selected_tab(self, html, tab_id):
        anchor = tab_anchor(html, tab_id)
        self.assertIn('aria-selected="true"', anchor)
        self.assertIn('tabindex="0"', anchor)

    def assert_unselected_tabs(self, html, tab_ids):
        for tab_id in tab_ids:
            anchor = tab_anchor(html, tab_id)
            self.assertIn('aria-selected="false"', anchor)
            self.assertIn('tabindex="-1"', anchor)


class ProjectTabFixture(TestCase):
    """Small deterministic fixture: one project, an owner and a plain member."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="PH026 项目详情页签公司")
        users = get_user_model()
        cls.owner_user = users.objects.create_user("ph026-tab-owner", password="test-pass")
        cls.member_user = users.objects.create_user("ph026-tab-member", password="test-pass")
        cls.owner = Membership.objects.create(
            organization=cls.org, user=cls.owner_user, role=Membership.Role.OWNER)
        cls.member = Membership.objects.create(
            organization=cls.org, user=cls.member_user, role=Membership.Role.MEMBER)
        today = timezone.localdate()
        cls.project = Project.objects.create(
            organization=cls.org, manager=cls.owner, code="PH026-TAB-001",
            name="阶段三页签项目", client="虚构客户", stage=Project.Stage.EXECUTION,
            start_date=today - timedelta(days=10), end_date=today + timedelta(days=50),
            budget_cents=1_234_567, budget_approved=True, scope="测试范围",
            acceptance_criteria="测试验收", contract_reference="PH026-CONTRACT")
        ProjectMember.objects.create(project=cls.project, member=cls.member)
        # Owner-only financial fact: member must never see this amount in HTML.
        cls.owner_document = Document.objects.create(
            project=cls.project, kind=Document.Kind.EXPENSE, title="老板费用单据",
            category="ph026", amount_cents=987_654, tax_cents=0,
            occurred_on=today, reference="PH026-EXP-1", submitted_by=cls.owner_user)


class ProjectDetailTabTests(TabPageAssertions, ProjectTabFixture):
    def setUp(self):
        self.client = Client()
        self.login(self.owner)

    def login(self, member):
        self.client.force_login(member.user)

    def detail(self, **query):
        page = self.client.get(reverse("project-detail", args=[self.project.pk]), query)
        self.assertEqual(page.status_code, 200)
        return page

    def test_owner_default_render_has_only_overview_visible(self):
        page = self.detail()
        self.assertEqual(page.context["active_tab"], "overview")
        self.assertTrue(page.context["can_finance"])
        html = page.content.decode()

        self.assert_single_visible_panel(html, "overview")
        self.assertEqual(rendered_panel_ids(html), list(OWNER_TABS))
        for panel_id in OWNER_TABS[1:]:
            self.assert_panel_hidden(html, panel_id)
        self.assert_selected_tab(html, "overview")
        self.assert_unselected_tabs(html, OWNER_TABS[1:])

    def test_tablist_is_real_links_and_accessible(self):
        html = self.detail().content.decode()

        self.assertIn(
            '<div class="tablist" role="tablist" aria-label="项目详情页签" data-link-tabs>', html)
        for tab_id in OWNER_TABS:
            anchor = tab_anchor(html, tab_id)
            self.assertIn(f'href="?tab={tab_id}"', anchor)
            self.assertIn(f'aria-controls="project-panel-{tab_id}"', anchor)
        for panel_id in OWNER_TABS:
            tag = panel_tag(html, panel_id)
            self.assertIn(f'role="tabpanel" aria-labelledby="project-tab-{panel_id}" tabindex="0"', tag)

    def test_each_tab_deep_link_shows_exactly_its_panel(self):
        for tab_id in OWNER_TABS:
            with self.subTest(tab=tab_id):
                page = self.detail(tab=tab_id)
                self.assertEqual(page.context["active_tab"], tab_id)
                html = page.content.decode()
                self.assert_single_visible_panel(html, tab_id)
                self.assert_selected_tab(html, tab_id)
                self.assert_unselected_tabs(
                    html, [other for other in OWNER_TABS if other != tab_id])

    def test_invalid_or_unauthorized_tab_falls_back_to_overview(self):
        for bogus in ("bogus", "expenses"):
            with self.subTest(tab=bogus):
                page = self.detail(tab=bogus)
                self.assertEqual(page.context["active_tab"], "overview")
                html = page.content.decode()
                self.assert_single_visible_panel(html, "overview")
                self.assert_selected_tab(html, "overview")
                self.assertNotIn('href="?tab=expenses"', html)

    def test_legacy_hash_tab_markup_is_gone(self):
        html = self.detail().content.decode()

        self.assertNotIn("data-project-tabs", html)
        self.assertNotIn("section-nav project-tabs", html)
        self.assertNotIn('data-panel-title', html)
        self.assertNotIn('href="#finance"', html)
        self.assertNotIn('href="#overview"', html)
        self.assertNotIn('id="tab-overview"', html)
        self.assertNotIn('id="overview"', html)

    def test_member_without_finance_gets_my_expenses_as_last_tab(self):
        self.login(self.member)
        page = self.detail()
        self.assertFalse(page.context["can_finance"])
        self.assertEqual(page.context["active_tab"], "overview")
        html = page.content.decode()

        self.assertEqual(rendered_panel_ids(html), list(MEMBER_TABS))
        self.assertNotIn("project-tab-finance", html)
        self.assertNotIn("project-panel-finance", html)
        self.assertNotIn('href="?tab=finance"', html)
        self.assertIn(
            '<a class="tab" id="project-tab-expenses" role="tab" href="?tab=expenses" '
            'aria-controls="project-panel-expenses"', html)
        self.assertLess(html.index("project-tab-team"), html.index("project-tab-expenses"))
        self.assert_panel_hidden(html, "expenses")
        self.assertContains(page, "我的费用单据")
        # 无财务权限的面板与数据在服务端 HTML 中完全不出现。
        self.assertNotContains(page, "9,876.54")
        self.assertNotContains(page, "987654")
        self.assertNotContains(page, "项目财务")

    def test_member_finance_deep_link_falls_back_to_overview(self):
        self.login(self.member)
        page = self.detail(tab="finance")
        self.assertEqual(page.context["active_tab"], "overview")
        html = page.content.decode()
        self.assert_single_visible_panel(html, "overview")
        self.assert_selected_tab(html, "overview")

    def test_member_can_open_my_expenses_tab(self):
        self.login(self.member)
        page = self.detail(tab="expenses")
        self.assertEqual(page.context["active_tab"], "expenses")
        html = page.content.decode()
        self.assert_single_visible_panel(html, "expenses")
        self.assert_selected_tab(html, "expenses")
        self.assert_panel_hidden(html, "overview")

    def test_member_document_submit_returns_to_my_expenses_tab(self):
        """无财务权限成员提交费用单据后回到“我的费用”，而非回退到概览。"""
        self.login(self.member)
        response = self.client.post(
            reverse("new-object", args=["document"]) + f"?project={self.project.pk}&kind=expense",
            {"project": self.project.pk, "kind": "expense", "title": "成员费用",
             "category": "vendor", "amount": "100.00", "tax": "0",
             "occurred_on": timezone.localdate().isoformat(), "reference": "PH026-MEMBER-EXP",
             "submission_token": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            f"{reverse('project-detail', args=[self.project.pk])}?tab=expenses")

    def test_finance_number_is_visible_to_owner_only(self):
        owner_html = self.detail().content.decode()
        self.assertIn("9,876.54", owner_html)

        self.login(self.member)
        self.assertNotIn("9,876.54", self.detail().content.decode())
