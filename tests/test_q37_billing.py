"""Q-37 极简开票、收款与资金账户：金额守恒、只追加、权限与缺口。

验收要点：未收款 = 已开票 − 已收款；部分收款与作废/退款负值追加；同一收款只消费
一次；超额核销/重复提交拒绝；税率可选不猜测；项目归属必填或显式非项目；
不写 LedgerEntry；权限与跨租户拒绝；页面与聚合数字一致。
"""
import uuid
from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import FieldDoesNotExist, PermissionDenied, ValidationError
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (Document, LedgerEntry, Membership, Organization, Project,
                         ProjectMember)
from core.services import verify_audit
from pmo_plugins.billing import services
from pmo_plugins.billing.models import Account, Invoice, Receipt


class BillingFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.org = Organization.objects.create(name="极简开票测试公司")
        cls.other_org = Organization.objects.create(name="隔离公司")
        user_model = get_user_model()
        users = {}
        for role in ("owner", "pm", "pm2", "member", "auditor", "finance"):
            users[role] = user_model.objects.create_user(f"q37-{role}")
        cls.owner = Membership.objects.create(organization=cls.org, user=users["owner"], role="owner")
        cls.pm = Membership.objects.create(organization=cls.org, user=users["pm"], role="pm")
        cls.pm2 = Membership.objects.create(organization=cls.org, user=users["pm2"], role="pm")
        cls.member = Membership.objects.create(organization=cls.org, user=users["member"], role="member")
        cls.auditor = Membership.objects.create(organization=cls.org, user=users["auditor"], role="auditor")
        cls.finance = Membership.objects.create(organization=cls.org, user=users["finance"], role="finance")
        cls.foreign_owner = Membership.objects.create(
            organization=cls.other_org,
            user=user_model.objects.create_user("q37-foreign"),
            role="owner")
        cls.project = Project.objects.create(
            organization=cls.org, manager=cls.pm, code="Q37-001", name="极简开票项目",
            client="演练客户", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31),
            contract_cents=1_000_000, contract_reference="HT-Q37-001")
        cls.other_project = Project.objects.create(
            organization=cls.org, manager=cls.pm2, code="Q37-002", name="他人项目",
            client="其他客户", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        ProjectMember.objects.create(project=cls.project, member=cls.member)
        cls.foreign_project = Project.objects.create(
            organization=cls.other_org, manager=cls.foreign_owner, code="F-001",
            name="隔离项目", client="隔离客户", start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31))

    def account(self, name="公司银行账户", opening=10000):
        return services.register_account(
            self.owner, name=name, kind="bank", opening_balance_cents=opening, note="")

    def invoice(self, *, amount=10600, rate=600, number=None, project=None, token=None):
        return services.register_invoice(
            self.owner, project=project or self.project, number=number or uuid.uuid4().hex[:10],
            amount_cents=amount, tax_rate_bp=rate, issued_on=self.today,
            contract_reference="", note="", token=token or uuid.uuid4())

    def receipt(self, *, account, amount=4000, invoice=None, project=None,
                reference=None, token=None, member=None):
        return services.register_receipt(
            member or self.owner, project=project or self.project, non_project=False,
            account=account, invoice=invoice, amount_cents=amount,
            received_on=self.today, counterparty="", reference=reference or uuid.uuid4().hex[:12],
            note="", token=token or uuid.uuid4())


class BillingServiceTests(BillingFixture):
    def test_invoice_tax_optional_and_outstanding_partial_receipts(self):
        bank = self.account()
        invoice = self.invoice(amount=10600, rate=600)
        self.assertEqual((invoice.amount_cents, invoice.tax_cents, invoice.net_cents), (10600, 600, 10000))
        self.assertEqual(invoice.tax_rate_display, "6%")
        self.assertEqual(services.project_totals(self.project)["outstanding_cents"], 10600)

        first = self.receipt(account=bank, amount=4000, invoice=invoice)
        self.assertEqual(services.project_totals(self.project)["received_cents"], 4000)
        self.assertEqual(services.project_totals(self.project)["outstanding_cents"], 6600)
        self.assertEqual(services.invoice_remaining_cents(invoice), 6600)
        self.assertEqual(services.account_balance_cents(bank), 14000)

        self.receipt(account=bank, amount=6600, invoice=invoice)
        self.assertEqual(services.invoice_remaining_cents(invoice), 0)
        self.assertEqual(services.project_totals(self.project)["outstanding_cents"], 0)
        self.assertEqual(services.account_balance_cents(bank), 20600)
        self.assertEqual(first.amount_cents, 4000)

    def test_tax_missing_is_not_guessed_and_rounding_is_half_up(self):
        plain = self.invoice(amount=10000, rate=None)
        self.assertIsNone(plain.tax_rate_bp)
        self.assertEqual(plain.tax_cents, 0)
        self.assertEqual(plain.tax_rate_display, "未填")
        rounded = self.invoice(amount=100, rate=1300)
        # 100 × 1300 / 11300 = 11.5044… -> 12 分（ROUND_HALF_UP）
        self.assertEqual(rounded.tax_cents, 12)
        self.assertEqual(services.tax_from_gross(105, 500), 5)

    def test_duplicate_submission_and_reference_rejected(self):
        bank = self.account()
        token = uuid.uuid4()
        first = self.receipt(account=bank, reference="BANK-Q37-1", token=token)
        again = self.receipt(account=bank, reference="BANK-Q37-1", token=token)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(Receipt.objects.filter(organization=self.org).count(), 1)
        with self.assertRaises(ValidationError):
            self.receipt(account=bank, reference="BANK-Q37-1", token=uuid.uuid4())
        self.assertEqual(Receipt.objects.filter(organization=self.org).count(), 1)

        invoice_token = uuid.uuid4()
        self.invoice(number="INV-Q37-1", token=invoice_token)
        self.invoice(number="INV-Q37-1", token=invoice_token)
        self.assertEqual(Invoice.objects.filter(organization=self.org).count(), 1)
        with self.assertRaises(ValidationError):
            self.invoice(number="INV-Q37-1", token=uuid.uuid4())
        with self.assertRaises(ValidationError):
            self.invoice(number="INV-Q37-1", project=self.other_project, token=uuid.uuid4())

    def test_over_allocation_and_over_refund_rejected(self):
        bank = self.account()
        invoice = self.invoice(amount=10000, rate=None)
        receipt = self.receipt(account=bank, amount=4000, invoice=invoice, reference="BANK-OVER-1")
        with self.assertRaises(ValidationError):
            self.receipt(account=bank, amount=6001, invoice=invoice)
        with self.assertRaises(ValidationError):
            self.receipt(account=bank, amount=1, invoice=invoice, project=self.other_project)
        self.receipt(account=bank, amount=1000, invoice=invoice)
        with self.assertRaises(ValidationError):
            services.refund_receipt(self.owner, receipt.pk, 4001, "退款超过可退金额", uuid.uuid4())
        services.refund_receipt(self.owner, receipt.pk, 4000, "客户退回全部款项", uuid.uuid4())
        with self.assertRaises(ValidationError):
            services.refund_receipt(self.owner, receipt.pk, 1, "超出可退金额", uuid.uuid4())

    def test_void_and_refund_append_negative_records_preserving_source(self):
        bank = self.account()
        invoice = self.invoice(amount=10600, rate=600, number="INV-VOID-1")
        receipt = self.receipt(account=bank, amount=4000, invoice=invoice)
        void = services.void_invoice(self.owner, invoice.pk, "客户名称开错，重新开票", uuid.uuid4())
        original = Invoice.objects.get(pk=invoice.pk)
        self.assertEqual(Invoice.objects.filter(organization=self.org).count(), 2)
        self.assertEqual(void.amount_cents, -10600)
        self.assertEqual(void.tax_cents, -600)
        self.assertEqual(void.reverses_id, original.pk)
        self.assertEqual(original.amount_cents, 10600)  # 原票不改
        self.assertEqual(services.project_totals(self.project)["invoiced_cents"], 0)
        self.assertEqual(services.project_totals(self.project)["outstanding_cents"], -4000)
        with self.assertRaises(ValidationError):
            services.void_invoice(self.owner, invoice.pk, "重复作废应被拒绝", uuid.uuid4())

        refund = services.refund_receipt(self.owner, receipt.pk, 1500, "客户退回部分款项", uuid.uuid4())
        self.assertEqual(refund.amount_cents, -1500)
        self.assertEqual(refund.reverses_id, receipt.pk)
        self.assertEqual(receipt.amount_cents, 4000)  # 原收款不改
        self.assertEqual(services.account_balance_cents(bank), 10000 + 4000 - 1500)
        self.assertEqual(services.project_totals(self.project)["received_cents"], 2500)
        with self.assertRaises(ValidationError):
            services.refund_receipt(self.owner, refund.pk, 100, "退款记录不能再次退款", uuid.uuid4())

    def test_receipt_consumed_once_per_source_and_missing_attribution(self):
        bank = self.account()
        with self.assertRaises(ValidationError):
            services.register_receipt(
                self.owner, project=None, non_project=False, account=bank, invoice=None,
                amount_cents=100, received_on=self.today, counterparty="", reference="NOATTR",
                note="", token=uuid.uuid4())
        non_project = services.register_receipt(
            self.owner, project=None, non_project=True, account=bank, invoice=None,
            amount_cents=500, received_on=self.today, counterparty="利息", reference="NONPROJ-1",
            note="", token=uuid.uuid4())
        self.assertTrue(non_project.non_project)
        totals = services.project_totals(self.project)
        self.assertEqual(totals["received_cents"], 0)  # 非项目收款不进入项目现金
        self.assertEqual(services.account_balance_cents(bank), 10500)
        with self.assertRaises(ValidationError):
            services.register_receipt(
                self.owner, project=self.project, non_project=True, account=bank, invoice=None,
                amount_cents=100, received_on=self.today, counterparty="", reference="NONPROJ-2",
                note="", token=uuid.uuid4())
        from django.db import transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Receipt.objects.create(
                    organization=self.org, account=bank, project=None, non_project=False,
                    amount_cents=100, received_on=self.today, reference="DB-BAD",
                    token=uuid.uuid4(), created_by=self.owner.user)

    def test_no_ledger_entry_written_and_audit_chain_kept(self):
        bank = self.account()
        invoice = self.invoice(amount=10600, rate=600)
        receipt = self.receipt(account=bank, amount=4000, invoice=invoice)
        services.refund_receipt(self.owner, receipt.pk, 500, "客户退回部分款项", uuid.uuid4())
        services.void_invoice(self.owner, invoice.pk, "客户名称开错", uuid.uuid4())
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.assertEqual(Document.objects.count(), 0)
        self.assertTrue(verify_audit(self.org))

    def test_account_balance_is_derived_not_editable(self):
        with self.assertRaises(FieldDoesNotExist):
            Account._meta.get_field("balance_cents")
        account = self.account(opening=50000)
        self.assertEqual(services.account_balance_cents(account), 50000)
        with self.assertRaises(ValidationError):
            account.delete()

    def test_permissions_and_tenant_isolation(self):
        with self.assertRaises(PermissionDenied):
            services.billing_projects(self.member)
        with self.assertRaises(PermissionDenied):
            services.billing_projects(self.finance)
        with self.assertRaises(PermissionDenied):
            services.billing_projects(self.auditor)
        self.assertIsNone(services.project_billing_summary(self.member, self.project))
        self.assertIsNone(services.project_billing_summary(self.pm, self.other_project))

        bank = self.account()
        with self.assertRaises(PermissionDenied):
            services.register_invoice(self.pm, project=self.other_project, number="PM-OTHER",
                                      amount_cents=100, tax_rate_bp=None,
                                      issued_on=self.today, token=uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            services.register_account(self.pm, name="PM 账户", kind="cash",
                                      opening_balance_cents=0, note="")
        with self.assertRaises(PermissionDenied):
            services.register_receipt(self.pm, project=None, non_project=True, account=bank,
                                      invoice=None, amount_cents=100, received_on=self.today,
                                      reference="PM-NONPROJ", token=uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            services.register_receipt(self.foreign_owner, project=self.project, non_project=False,
                                      account=bank, invoice=None, amount_cents=100,
                                      received_on=self.today, reference="FOREIGN-1",
                                      token=uuid.uuid4())
        foreign_account = services.register_account(
            self.foreign_owner, name="隔离账户", kind="cash", opening_balance_cents=0, note="")
        with self.assertRaises(PermissionDenied):
            services.register_receipt(self.owner, project=self.project, non_project=False,
                                      account=foreign_account, invoice=None, amount_cents=100,
                                      received_on=self.today, reference="X-TENANT-1",
                                      token=uuid.uuid4())
        self.assertEqual(Receipt.objects.filter(organization=self.org).count(), 0)

    def test_cross_tenant_invoice_cannot_be_allocated(self):
        bank = self.account()
        foreign_account = services.register_account(
            self.foreign_owner, name="隔离账户", kind="cash", opening_balance_cents=0, note="")
        foreign_invoice = services.register_invoice(
            self.foreign_owner, project=self.foreign_project, number="F-INV-1",
            amount_cents=1000, tax_rate_bp=None, issued_on=self.today, token=uuid.uuid4())
        self.assertEqual(foreign_account.organization_id, self.other_org.pk)
        with self.assertRaises(ValidationError):
            services.register_receipt(self.owner, project=self.project, non_project=False,
                                      account=bank, invoice=foreign_invoice, amount_cents=100,
                                      received_on=self.today, reference="XT-INV-1",
                                      token=uuid.uuid4())


@override_settings(PMO_SCOPE="micro")
class BillingHttpTests(BillingFixture):
    def setUp(self):
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)
        return member

    def test_navigation_keeps_billing_for_owner_and_pm_only(self):
        self.login(self.owner)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "开票与收款")
        self.assertContains(page, reverse("billing"))
        self.login(self.pm)
        self.assertContains(self.client.get(reverse("dashboard")), "开票与收款")
        self.login(self.member)
        self.assertNotContains(self.client.get(reverse("dashboard")), "开票与收款")
        self.login(self.auditor)
        self.assertNotContains(self.client.get(reverse("dashboard")), "开票与收款")

    def test_billing_page_renders_empty_state_and_forms(self):
        self.login(self.owner)
        page = self.client.get(reverse("billing"))
        self.assertEqual(page.status_code, 200)
        for text in ("登记发票", "登记收款", "资金账户", "未收款 = 已开票 − 已收款",
                     "付款未接入", "还没有发票", "还没有资金账户", "新建资金账户"):
            self.assertContains(page, text)
        self.assertIn("billing/billing.html", [t.name for t in page.templates])

    def test_role_outside_billing_gets_403(self):
        for member in (self.member, self.auditor, self.finance):
            self.login(member)
            with self.subTest(role=member.role):
                self.assertEqual(self.client.get(reverse("billing")).status_code, 403)

    def test_invoice_receipt_void_refund_roundtrip_and_page_numbers(self):
        self.login(self.owner)
        response = self.client.post(reverse("billing-account-create"), {
            "name": "招商银行 1024", "kind": "bank", "opening_balance": "100.00",
            "note": "演练账户", "request_token": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        account = Account.objects.get(name="招商银行 1024")
        self.assertEqual(account.opening_balance_cents, 10000)

        response = self.client.post(reverse("billing-invoice-create"), {
            "project": self.project.pk, "number": "INV-HTTP-1", "amount": "106.00",
            "tax_rate": "6", "issued_on": self.today.isoformat(),
            "contract_reference": "", "note": "第一张发票",
            "request_token": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get(number="INV-HTTP-1")
        self.assertEqual((invoice.amount_cents, invoice.tax_cents), (10600, 600))

        response = self.client.post(reverse("billing-receipt-create"), {
            "project": self.project.pk, "account": account.pk, "invoice": invoice.pk,
            "amount": "40.00", "received_on": self.today.isoformat(),
            "counterparty": "演练客户", "reference": "BANK-HTTP-1", "note": "",
            "request_token": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        page = self.client.get(reverse("billing"))
        self.assertContains(page, "未收款")
        self.assertContains(page, "66.00")  # 未收款 6600 分
        self.assertContains(page, "140.00")  # 账户余额 10000 + 4000 分
        self.assertContains(page, "BANK-HTTP-1")

        receipt = Receipt.objects.get(reference="BANK-HTTP-1")
        response = self.client.post(reverse("billing-receipt-refund", args=[receipt.pk]), {
            "amount": "10.00", "reason": "客户退回部分款项", "request_token": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(services.project_totals(self.project)["received_cents"], 3000)
        refund = Receipt.objects.get(reverses=receipt)
        self.assertEqual(refund.amount_cents, -1000)

        response = self.client.post(reverse("billing-invoice-void", args=[invoice.pk]), {
            "reason": "客户名称开错，重新开票", "request_token": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(Invoice.objects.filter(reverses=invoice).exists())
        totals = services.project_totals(self.project)
        self.assertEqual(totals["invoiced_cents"], 0)
        self.assertEqual(totals["outstanding_cents"], -3000)

    def test_invalid_and_duplicate_submissions_are_rejected_without_rows(self):
        self.login(self.owner)
        self.client.post(reverse("billing-account-create"), {
            "name": "现金账户", "kind": "cash", "opening_balance": "0",
            "note": "", "request_token": str(uuid.uuid4())})
        account = Account.objects.get(name="现金账户")

        bad = self.client.post(reverse("billing-invoice-create"), {
            "project": self.project.pk, "number": "", "amount": "0",
            "tax_rate": "", "issued_on": self.today.isoformat(),
            "request_token": str(uuid.uuid4())})
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(Invoice.objects.count(), 0)

        self.client.post(reverse("billing-invoice-create"), {
            "project": self.project.pk, "number": "INV-DUP", "amount": "10.00",
            "tax_rate": "", "issued_on": self.today.isoformat(),
            "request_token": str(uuid.uuid4())})
        duplicate = self.client.post(reverse("billing-invoice-create"), {
            "project": self.project.pk, "number": "INV-DUP", "amount": "10.00",
            "tax_rate": "", "issued_on": self.today.isoformat(),
            "request_token": str(uuid.uuid4())})
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(Invoice.objects.count(), 1)

        self.client.post(reverse("billing-receipt-create"), {
            "project": self.project.pk, "account": account.pk, "invoice": "",
            "amount": "5.00", "received_on": self.today.isoformat(),
            "counterparty": "", "reference": "BANK-DUP", "note": "",
            "request_token": str(uuid.uuid4())})
        duplicate_receipt = self.client.post(reverse("billing-receipt-create"), {
            "project": self.project.pk, "account": account.pk, "invoice": "",
            "amount": "5.00", "received_on": self.today.isoformat(),
            "counterparty": "", "reference": "BANK-DUP", "note": "",
            "request_token": str(uuid.uuid4())})
        self.assertEqual(duplicate_receipt.status_code, 400)
        self.assertContains(duplicate_receipt, "重复提交已被拒绝", status_code=400)
        self.assertEqual(Receipt.objects.count(), 1)

    def test_cross_tenant_post_cannot_touch_other_company_project(self):
        self.login(self.foreign_owner)
        before = Invoice.objects.count()
        response = self.client.post(reverse("billing-invoice-create"), {
            "project": self.project.pk, "number": "X-TENANT", "amount": "10.00",
            "tax_rate": "", "issued_on": self.today.isoformat(),
            "request_token": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Invoice.objects.count(), before)

    def test_project_page_shows_billing_summary_and_q36_payment_bridge(self):
        bank = self.account()
        invoice = self.invoice(number="INV-PAGE-1", amount=10600, rate=600)
        self.receipt(account=bank, amount=4000, invoice=invoice)
        self.login(self.owner)
        page = self.client.get(reverse("project-detail", args=[self.project.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "开票与收款")
        self.assertContains(page, "未收款")
        self.assertContains(page, "66.00")
        # Q-36 delivered the payment side, so the old "付款未接入" gap marker is closed.
        self.assertContains(page, "付款由费用与付款记录接入")
        self.assertContains(page, "费用与外包（成本 / 现金）")

    def test_pm_only_sees_own_project_billing_numbers(self):
        self.account(name="老板账户")
        self.invoice(number="INV-PM-SCOPE", amount=20000, rate=None, project=self.other_project)
        self.login(self.pm)
        page = self.client.get(reverse("billing"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "INV-PM-SCOPE")
        self.assertNotContains(page, "200.00")
        self.login(self.owner)
        owner_page = self.client.get(reverse("billing"))
        self.assertContains(owner_page, "INV-PM-SCOPE")
        self.assertContains(owner_page, "200.00")

    def test_missing_attribution_and_legacy_gap_shown(self):
        bank = self.account()
        services.register_receipt(self.owner, project=None, non_project=True, account=bank,
                                  invoice=None, amount_cents=500, received_on=self.today,
                                  counterparty="利息", reference="NONPROJ-PAGE", note="",
                                  token=uuid.uuid4())
        Document.objects.create(project=self.project, kind="receipt", title="旧流程收款",
                                amount_cents=1000, occurred_on=self.today,
                                reference="LEGACY-1", submitted_by=self.owner.user, status="approved")
        self.login(self.owner)
        page = self.client.get(reverse("billing"))
        self.assertContains(page, "历史收款 1 笔未接入")
        self.assertContains(page, "非项目收款 1 笔")
        self.assertContains(page, "非项目收款")


class BillingValidationBranchTests(BillingFixture):
    """补充拒绝分支：金额/税率/期间/来源/状态/归属/幂等冲突都必须报错且不落库。"""

    def test_invoice_rejections_do_not_write(self):
        with self.assertRaises(ValidationError):
            self.invoice(amount=0)
        with self.assertRaises(ValidationError):
            self.invoice(amount=-100)
        with self.assertRaises(ValidationError):
            self.invoice(rate=10001)
        with self.assertRaises(ValidationError):
            self.invoice(rate=-1)
        self.assertEqual(Invoice.objects.count(), 0)
        with self.assertRaises(ValidationError):
            services.register_invoice(
                self.owner, project=self.project, number="  ", amount_cents=100,
                tax_rate_bp=None, issued_on=self.today, token=uuid.uuid4())
        with self.assertRaises(ValidationError):
            services.register_invoice(
                self.owner, project=self.project, number="FUTURE-1", amount_cents=100,
                tax_rate_bp=None, issued_on=self.today.replace(year=self.today.year + 1),
                token=uuid.uuid4())
        first = self.invoice(number="TOKEN-OWNER", token=uuid.uuid4())
        other_user = self.pm.user
        with self.assertRaises(ValidationError):
            services.register_invoice(
                self.pm, project=self.project, number=first.number, amount_cents=100,
                tax_rate_bp=None, issued_on=self.today, token=first.token)
        self.assertIsNotNone(other_user)

    def test_account_rejections_and_owner_only(self):
        self.account(name="唯一账户")
        with self.assertRaises(ValidationError):
            self.account(name="唯一账户")
        with self.assertRaises(ValidationError):
            services.register_account(self.owner, name="", kind="bank",
                                      opening_balance_cents=0, note="")
        with self.assertRaises(ValidationError):
            services.register_account(self.owner, name="坏类型", kind="bitcoin",
                                      opening_balance_cents=0, note="")
        with self.assertRaises(ValidationError):
            services.register_account(self.owner, name="超界", kind="bank",
                                      opening_balance_cents=10**12 + 1, note="")
        with self.assertRaises(PermissionDenied):
            services.register_account(self.pm, name="PM 账户", kind="cash",
                                      opening_balance_cents=0, note="")
        self.assertEqual(Account.objects.count(), 1)

    def test_receipt_rejections(self):
        bank = self.account()
        other_bank = self.account(name="现金备用金", opening=0)
        services.register_account  # noqa: B018 - 明确账户由 owner 建立
        with self.assertRaises(ValidationError):
            services.register_receipt(
                self.owner, project=self.project, non_project=False, account=bank,
                invoice=None, amount_cents=100, received_on=self.today,
                reference="", token=uuid.uuid4())
        with self.assertRaises(ValidationError):
            services.register_receipt(
                self.owner, project=self.project, non_project=False, account=bank,
                invoice=None, amount_cents=0, received_on=self.today,
                reference="ZERO", token=uuid.uuid4())
        other_bank.active = False
        other_bank.save(update_fields=["active"])
        with self.assertRaises(ValidationError):
            services.register_receipt(
                self.owner, project=self.project, non_project=False, account=other_bank,
                invoice=None, amount_cents=100, received_on=self.today,
                reference="INACTIVE", token=uuid.uuid4())
        invoice = self.invoice(amount=1000, rate=None, number="INV-BRANCH-1")
        other_invoice = self.invoice(amount=1000, rate=None, number="INV-BRANCH-2",
                                     project=self.other_project)
        with self.assertRaises(ValidationError):
            self.receipt(account=bank, amount=100, invoice=other_invoice)
        services.void_invoice(self.owner, invoice.pk, "作废后不得再收款", uuid.uuid4())
        with self.assertRaises(ValidationError):
            self.receipt(account=bank, amount=100, invoice=invoice)
        self.assertEqual(Receipt.objects.count(), 0)

    def test_void_and_refund_rejections(self):
        bank = self.account()
        invoice = self.invoice(amount=1000, rate=None, number="INV-VOID-BRANCH")
        with self.assertRaises(ValidationError):
            services.void_invoice(self.owner, invoice.pk, "太短", uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            services.void_invoice(self.foreign_owner, invoice.pk, "跨租户作废应被拒绝", uuid.uuid4())
        receipt = self.receipt(account=bank, amount=500, reference="REF-REFUND-1")
        with self.assertRaises(ValidationError):
            services.refund_receipt(self.owner, receipt.pk, 500, "太短", uuid.uuid4())
        with self.assertRaises(ValidationError):
            services.refund_receipt(self.owner, receipt.pk, 0, "金额为零应被拒绝", uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            services.refund_receipt(self.foreign_owner, receipt.pk, 100,
                                    "跨租户退款应被拒绝", uuid.uuid4())
        self.assertEqual(Receipt.objects.count(), 1)

    def test_invoice_token_collision_with_other_project_rejected(self):
        token = uuid.uuid4()
        self.invoice(number="TOKEN-A", token=token)
        with self.assertRaises(ValidationError):
            self.invoice(number="TOKEN-B", project=self.other_project, token=token)


@override_settings(PMO_SCOPE="micro")
class BillingHttpBranchTests(BillingFixture):
    def setUp(self):
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)
        return member

    def test_pm_can_open_billing_but_cannot_manage_accounts(self):
        self.login(self.pm)
        page = self.client.get(reverse("billing"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'action="/billing/accounts/save/"')
        self.assertContains(page, "资金账户由公司负责人维护")
        response = self.client.post(reverse("billing-account-create"), {
            "name": "PM 私建账户", "kind": "cash", "opening_balance": "0",
            "note": "", "request_token": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Account.objects.count(), 0)

    def test_missing_records_and_invalid_correction_forms_do_not_crash(self):
        self.login(self.owner)
        page = self.client.post(reverse("billing-invoice-void", args=[999999]), {
            "reason": "记录不存在时应报错", "request_token": str(uuid.uuid4())})
        self.assertEqual(page.status_code, 302)
        short = self.client.post(reverse("billing-receipt-refund", args=[999999]), {
            "amount": "1.00", "reason": "短", "request_token": str(uuid.uuid4())})
        self.assertEqual(short.status_code, 302)
        account = self.client.post(reverse("billing-account-create"), {
            "name": "", "kind": "bank", "opening_balance": "0", "note": "",
            "request_token": str(uuid.uuid4())})
        self.assertEqual(account.status_code, 400)
        self.assertEqual(Account.objects.count(), 0)

    def test_receipt_form_requires_exactly_one_attribution(self):
        self.login(self.owner)
        self.client.post(reverse("billing-account-create"), {
            "name": "收款账户", "kind": "bank", "opening_balance": "0",
            "note": "", "request_token": str(uuid.uuid4())})
        account = Account.objects.get(name="收款账户")
        neither = self.client.post(reverse("billing-receipt-create"), {
            "project": "", "account": account.pk, "invoice": "", "amount": "1.00",
            "received_on": self.today.isoformat(), "reference": "ATTR-NONE",
            "request_token": str(uuid.uuid4())})
        self.assertEqual(neither.status_code, 400)
        self.assertContains(neither, "请选择所属项目", status_code=400)
        self.assertEqual(Receipt.objects.count(), 0)
