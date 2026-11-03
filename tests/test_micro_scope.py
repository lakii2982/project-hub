"""Q-32 小微版入口收口：导航白名单、直接 URL 门控与停用不删数据。"""
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from core.models import Document, Membership, Organization, PluginState, Project, ProjectMember
from core.plugin_runtime import get_registry, is_enabled
from core.scope import is_micro_scope
from core.services import audit, submit_document
from pmo_plugins.delivery.models import Acceptance

DISABLED_PAGE = "该功能不在小微版入口内"


@override_settings(PMO_SCOPE="micro")
class MicroScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="小微版测试公司")
        cls.other_org = Organization.objects.create(name="隔离公司")
        user_model = get_user_model()
        users = {}
        for role in ("owner", "pm", "member", "auditor"):
            users[role] = user_model.objects.create_user(
                f"micro-{role}", password="Micro-Test-9182")
        cls.owner = Membership.objects.create(organization=cls.org, user=users["owner"], role="owner")
        cls.pm = Membership.objects.create(organization=cls.org, user=users["pm"], role="pm")
        cls.member = Membership.objects.create(organization=cls.org, user=users["member"], role="member")
        cls.auditor = Membership.objects.create(organization=cls.org, user=users["auditor"], role="auditor")
        cls.foreign_owner = Membership.objects.create(
            organization=cls.other_org, user=user_model.objects.create_user(
                "micro-foreign", password="Micro-Test-9182"), role="owner")
        cls.project = Project.objects.create(
            organization=cls.org, manager=cls.pm, code="MICRO-001", name="小微版项目",
            client="演练客户", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        ProjectMember.objects.create(project=cls.project, member=cls.member)
        cls.document = Document.objects.create(
            project=cls.project, kind="expense", title="历史费用只读记录", reference="MICRO-DOC-001",
            amount_cents=1200, occurred_on=date(2025, 1, 5), submitted_by=cls.owner.user)

    def setUp(self):
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)
        return member

    def assert_disabled(self, response):
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, DISABLED_PAGE, status_code=403)

    def test_owner_navigation_only_keeps_small_business_entries(self):
        self.login(self.owner)
        page = self.client.get(reverse("dashboard"))
        self.assertEqual(page.status_code, 200)
        for label in ("工作台", "项目", "工时", "设置"):
            self.assertContains(page, label)
        for hidden in ("项目财务", "经营助手", "审批中心", "插件管理", "个人 Agent 接入",
                       "通知中心", "到期提醒", "验收与收入", "应收与账龄", "人工成本池"):
            self.assertNotContains(page, hidden)
        self.assertNotContains(page, 'href="/finance/"')
        self.assertContains(page, reverse("settings"))

    def test_navigation_is_role_scoped(self):
        self.login(self.pm)
        pm_page = self.client.get(reverse("dashboard"))
        self.assertContains(pm_page, "工作台")
        self.assertNotContains(pm_page, reverse("settings"))
        self.login(self.member)
        member_page = self.client.get(reverse("dashboard"))
        self.assertEqual(member_page.status_code, 200)
        self.assertContains(member_page, "工作台")
        self.assertNotContains(member_page, reverse("settings"))

    def test_complex_pages_are_disabled_by_direct_url(self):
        self.login(self.owner)
        for path in ("/finance/", "/modules/delivery/acceptance-list/", "/assistant/",
                     "/approvals/", "/plugins/", "/reminders/", "/notifications/",
                     "/connections/"):
            with self.subTest(path=path):
                self.assert_disabled(self.client.get(path))

    def test_disabled_business_write_is_rejected_without_side_effects(self):
        self.login(self.owner)
        before = Acceptance.objects.count()
        response = self.client.post("/modules/delivery/acceptance-save/", {})
        self.assert_disabled(response)
        self.assertEqual(Acceptance.objects.count(), before)

    def test_history_remains_readable_after_disabling(self):
        audit(self.owner, "document.created", self.document, {"title": self.document.title})
        self.login(self.owner)
        detail = self.client.get(reverse("project-detail", args=[self.project.pk]))
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, "历史费用只读记录")
        self.assertNotContains(detail, "已确认管理收入")
        audit_page = self.client.get(reverse("audit"))
        self.assertEqual(audit_page.status_code, 200)
        self.assertContains(audit_page, "document.created")

    def test_project_detail_does_not_call_management_revenue(self):
        self.login(self.owner)
        with mock.patch("pmo_plugins.management_revenue.services.project_revenue_summary") as summary:
            response = self.client.get(reverse("project-detail", args=[self.project.pk]))
        self.assertEqual(response.status_code, 200)
        summary.assert_not_called()

    def test_plugins_are_effectively_disabled_but_installed_and_auditable(self):
        self.assertFalse(is_enabled(self.org, "contracts"))
        self.assertFalse(is_enabled(self.org, "delivery"))
        self.assertIn("contracts", get_registry().manifests)
        self.assertEqual(Acceptance.objects.count(), 0)
        self.assertEqual(PluginState.objects.filter(organization=self.org).count(), 0)

    def test_permission_and_tenant_boundaries_survive_disabling(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(
            reverse("project-detail", args=[self.project.pk])).status_code, 200)
        self.login(self.member)
        self.assertEqual(self.client.get(reverse("audit")).status_code, 403)
        self.login(self.foreign_owner)
        self.assertEqual(self.client.get(
            reverse("project-detail", args=[self.project.pk])).status_code, 404)

    def test_settings_page_exposes_read_only_audit(self):
        self.login(self.owner)
        page = self.client.get(reverse("settings"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "审计日志（只读）")
        self.assertContains(page, reverse("audit"))
