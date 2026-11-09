"""PH-026 阶段④：现金流合并页（Q-49）契约。

`/cashflow/` 是「概览 / 发票 / 收款 / 资金账户 / 8 周计划」五页签模块：服务端按
`?tab=` 渲染唯一可见面板（其余 `hidden`），无 JavaScript 可用；`/billing/` 与
`/cash/` 保留为同页别名（默认页签分别为发票与资金账户）。只覆盖渲染、权限与
页签跳转，不改 Q-37/Q-38 的金额、幂等或权限口径。
"""
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import Membership, Organization, Project
from pmo_plugins.billing import services as billing

TABS = ("overview", "invoices", "receipts", "accounts", "plan")


def visible_panels(html):
    import re
    return [match.group(1) for match in re.finditer(
        r'<div class="panel-stack" id="cashflow-panel-([a-z]+)"([^>]*)>', html)
        if " hidden" not in match.group(2)]


class CashflowTabFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="PH026 现金流公司")
        users = get_user_model()
        cls.owner_user = users.objects.create_user("ph026-cash-owner", password="test-pass")
        cls.pm_user = users.objects.create_user("ph026-cash-pm", password="test-pass")
        cls.member_user = users.objects.create_user("ph026-cash-member", password="test-pass")
        cls.owner = Membership.objects.create(organization=cls.org, user=cls.owner_user, role="owner")
        cls.pm = Membership.objects.create(organization=cls.org, user=cls.pm_user, role="pm")
        cls.member = Membership.objects.create(organization=cls.org, user=cls.member_user, role="member")
        cls.today = timezone.localdate()
        cls.project = Project.objects.create(
            organization=cls.org, manager=cls.pm, code="PH026-CF-001",
            name="现金流项目", client="示例客户", stage=Project.Stage.EXECUTION,
            start_date=cls.today - timedelta(days=30), end_date=cls.today + timedelta(days=60),
            contract_cents=1_000_000)
        cls.account = billing.register_account(
            cls.owner, name="现金流账户", kind="bank", opening_balance_cents=0,
            note="测试账户", token=uuid.uuid4())
        cls.invoice = billing.register_invoice(
            cls.owner, project=cls.project, number="PH026-CF-INV-1", amount_cents=106_000,
            tax_rate_bp=600, issued_on=cls.today, contract_reference="PH026-CF-C1",
            note="阶段④页签测试", token=uuid.uuid4())
        billing.register_receipt(
            cls.owner, project=cls.project, non_project=False, account=cls.account,
            invoice=cls.invoice, amount_cents=50_000, received_on=cls.today,
            counterparty="示例客户", reference="PH026-CF-BANK-1", note="首期收款",
            token=uuid.uuid4())


@override_settings(PMO_SCOPE="micro")
class CashflowTabTests(CashflowTabFixture):
    def setUp(self):
        self.client = Client()
        self.client.force_login(self.owner_user)

    def page(self, path=None, **params):
        return self.client.get(path or reverse("cashflow"), params)

    def test_default_overview_and_tab_protocol(self):
        page = self.page()
        self.assertEqual(page.status_code, 200)
        self.assertIn("cashflow.html", [t.name for t in page.templates])
        html = page.content.decode()
        self.assertEqual(page.context["active_tab"], "overview")
        self.assertEqual(visible_panels(html), ["overview"])
        for tab in ("overview", "invoices", "receipts", "accounts", "plan"):
            self.assertIn(f'class="tab" id="cashflow-tab-{tab}" role="tab" href="?tab={tab}"', html)
        self.assertIn('data-link-tabs', html)
        for section in ("概览", "发票", "收款", "资金账户", "8 周计划"):
            self.assertIn(section, html)

    def test_each_tab_deep_link_shows_only_its_panel(self):
        for tab in TABS:
            with self.subTest(tab=tab):
                page = self.page(tab=tab)
                self.assertEqual(page.context["active_tab"], tab)
                self.assertEqual(visible_panels(page.content.decode()), [tab])

    def test_invalid_tab_falls_back_to_overview(self):
        page = self.page(tab="bogus")
        self.assertEqual(page.context["active_tab"], "overview")
        self.assertEqual(visible_panels(page.content.decode()), ["overview"])

    def test_legacy_billing_and_cash_routes_render_merged_page(self):
        billing_page = self.page(reverse("billing"))
        self.assertEqual(billing_page.status_code, 200)
        self.assertEqual(billing_page.context["active_tab"], "invoices")
        self.assertIn("cashflow.html", [t.name for t in billing_page.templates])
        cash_page = self.page(reverse("cash"))
        self.assertEqual(cash_page.status_code, 200)
        self.assertEqual(cash_page.context["active_tab"], "accounts")
        # 内容仍在同一页面 DOM 中（其余面板 hidden），旧入口的既有内容不丢失。
        for text in ("开票与收款", "登记发票", "登记收款", "收付款流水", "未来 8 周现金计划"):
            self.assertIn(text, billing_page.content.decode())

    def test_navigation_uses_single_cashflow_entry(self):
        dashboard = self.client.get(reverse("dashboard")).content.decode()
        self.assertIn("现金流", dashboard)
        self.assertIn(reverse("cashflow"), dashboard)
        self.assertNotIn(reverse("billing"), dashboard)
        self.assertNotIn(reverse("cash"), dashboard)

    def test_role_access_is_unchanged(self):
        for user in (self.pm_user,):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("cashflow")).status_code, 200)
        for user in (self.member_user,):
            self.client.force_login(user)
            for name in ("cashflow", "billing", "cash"):
                with self.subTest(route=name):
                    self.assertEqual(self.client.get(reverse(name)).status_code, 403)

    def test_project_drilldown_keeps_filter(self):
        page = self.page(tab="invoices", project=self.project.pk)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["selected_project"].pk, self.project.pk)
        self.assertContains(page, "筛选项目")
        self.assertContains(page, self.project.code)

    def test_project_finance_expense_drilldown_uses_all_tab(self):
        """项目财务「费用明细」钻取必须带 ?tab=all，落到全部记录而不是登记表单。"""
        page = self.client.get(reverse("project-detail", args=[self.project.pk]))
        self.assertContains(page, f"?tab=all&amp;project={self.project.pk}")

    def test_tab_links_keep_project_filter(self):
        page = self.page(tab="overview", project=self.project.pk)
        html = page.content.decode()
        for tab in TABS:
            self.assertIn(f'href="?tab={tab}&amp;project={self.project.pk}"', html)

    def test_invoice_post_redirects_to_invoices_tab(self):
        response = self.client.post(reverse("billing-invoice-create"), {
            "project": self.project.pk, "number": "PH026-CF-INV-2",
            "amount": "5000.00", "tax_rate": "", "issued_on": self.today.isoformat(),
            "contract_reference": "", "note": "", "request_token": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('cashflow')}?tab=invoices")

    def test_account_create_post_redirects_to_accounts_tab(self):
        response = self.client.post(reverse("cash-account-create"), {
            "name": "第二个账户", "kind": "bank", "opening_balance": "0",
            "note": "", "request_token": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, f"{reverse('cashflow')}?tab=accounts")
