"""Q-33 老板工作台：本月开票/收款/支出、现金余额、未收款与项目毛利排行（只读）。"""
import io
import uuid
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db.models import Sum
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.business_dashboard import (BASIS_INVOICE, BASIS_RECEIPT, dashboard_snapshot,
                                     resolve_basis)
from core.models import (AuditEvent, CostRate, Document, Employee, LedgerEntry, Membership,
                         Organization, Project, ProjectMember, Timesheet)
from core.reporting import project_summary
from pmo_plugins.billing import services as billing
from pmo_plugins.billing.services import billing_snapshot
from pmo_plugins.expenses import services as expenses
from pmo_plugins.billing.models import Account, Invoice, Receipt
from pmo_plugins.expenses.models import ExpensePayment


class DashboardFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.org = Organization.objects.create(name="Q33 收益工作台公司")
        cls.other_org = Organization.objects.create(name="Q33 隔离公司")
        user_model = get_user_model()
        users = {role: user_model.objects.create_user(f"q33-{role}") for role in
                 ("owner", "pm", "pm2", "member", "finance", "auditor", "foreign")}
        cls.owner = Membership.objects.create(organization=cls.org, user=users["owner"], role="owner")
        cls.pm = Membership.objects.create(organization=cls.org, user=users["pm"], role="pm")
        cls.pm2 = Membership.objects.create(organization=cls.org, user=users["pm2"], role="pm")
        cls.member = Membership.objects.create(organization=cls.org, user=users["member"], role="member")
        cls.finance = Membership.objects.create(organization=cls.org, user=users["finance"], role="finance")
        cls.auditor = Membership.objects.create(organization=cls.org, user=users["auditor"], role="auditor")
        cls.foreign_owner = Membership.objects.create(
            organization=cls.other_org, user=users["foreign"], role="owner")
        cls.project = Project.objects.create(
            organization=cls.org, manager=cls.pm, code="Q33-001", name="收益项目",
            client="演练客户", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        cls.other_project = Project.objects.create(
            organization=cls.org, manager=cls.pm2, code="Q33-002", name="他人项目",
            client="其他客户", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        cls.uninvoiced_project = Project.objects.create(
            organization=cls.org, manager=cls.pm, code="Q33-003", name="未开票项目",
            client="缺口客户", start_date=date(2025, 1, 1), end_date=date(2025, 12, 31))
        cls.foreign_project = Project.objects.create(
            organization=cls.other_org, manager=cls.foreign_owner, code="Q33-F01",
            name="隔离项目", client="隔离客户", start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31))
        ProjectMember.objects.create(project=cls.project, member=cls.member)
        cls.account = billing.register_account(
            cls.owner, name="公司基本户", kind="bank", opening_balance_cents=1_000_000,
            note="", token=uuid.uuid4())
        cls.wallet = billing.register_account(
            cls.owner, name="收款钱包", kind="alipay", opening_balance_cents=0,
            note="", token=uuid.uuid4())
        cls.invoice = billing.register_invoice(
            cls.owner, project=cls.project, number="Q33-INV-001", amount_cents=1_060_000,
            tax_rate_bp=600, issued_on=cls.today, contract_reference="",
            note="", token=uuid.uuid4())
        billing.register_invoice(
            cls.owner, project=cls.other_project, number="Q33-INV-002",
            amount_cents=500_000, tax_rate_bp=None, issued_on=cls.today,
            contract_reference="", note="", token=uuid.uuid4())
        billing.register_receipt(
            cls.owner, project=cls.project, non_project=False, account=cls.account,
            invoice=cls.invoice, amount_cents=400_000, received_on=cls.today,
            counterparty="", reference="Q33-RCPT-001", note="", token=uuid.uuid4())
        billing.register_receipt(
            cls.owner, project=None, non_project=True, account=cls.wallet, invoice=None,
            amount_cents=50_000, received_on=cls.today, counterparty="零星客户",
            reference="Q33-RCPT-002", note="", token=uuid.uuid4())
        # Q-36 cost + payment on the first project (cash only for the payment).
        cls.expense = expenses.register_expense(
            cls.pm, project=cls.project, category="vendor", amount_cents=300_000,
            occurred_on=cls.today, expected_pay_on=cls.today + timedelta(days=10),
            note="演练外包费用", counterparty="演练供应商", token=uuid.uuid4())
        expenses.confirm_expense(cls.pm, cls.expense.pk)
        cls.payment = expenses.record_payment(
            cls.pm, cls.expense.pk, amount_cents=100_000, paid_on=cls.today,
            note="第一笔付款", token=uuid.uuid4())
        # Q-35 missing-rate gap: a simplified timesheet with no cost rate stays unpriced.
        cls.unpriced_employee = Employee.objects.create(
            organization=cls.org, name="无费率成员", title="工程师")
        Timesheet.objects.create(
            organization=cls.org, project=cls.project, employee=cls.unpriced_employee,
            work_date=cls.today, minutes=120, note="缺费率演示", status="approved",
            accounting_mode="simplified", cost_state="unpriced", submitted_by=cls.owner.user,
            hourly_rate_cents=None, cost_cents=None, token=uuid.uuid4())
        # Legacy professional receipt stays read-only and never enters simplified revenue.
        Document.objects.create(
            project=cls.project, kind=Document.Kind.RECEIPT, title="历史专业收款",
            amount_cents=900_000, occurred_on=cls.today,
            reference="Q33-LEGACY-RCPT", submitted_by=cls.owner.user, status="approved")

    def login(self, member):
        self.client.force_login(member.user)
        return member


class SnapshotTests(DashboardFixture):
    def test_owner_snapshot_numbers_match_billing_and_expenses(self):
        snapshot = dashboard_snapshot(self.owner, today=self.today)
        self.assertEqual(snapshot["basis"], BASIS_INVOICE)
        # §4.1: 本月开票收入不含税 = 1,060,000 − 60,000 + 500,000（无税）。
        self.assertEqual(snapshot["month"]["invoiced_cents"], 1_500_000)
        self.assertEqual(snapshot["month"]["invoiced_gross_cents"], 1_560_000)
        self.assertEqual(snapshot["month"]["received_cents"], 450_000)
        self.assertEqual(snapshot["month"]["spent_cents"], 100_000)
        self.assertEqual(snapshot["outstanding"]["invoiced_cents"], 1_560_000)
        self.assertEqual(snapshot["outstanding"]["received_cents"], 400_000)
        self.assertEqual(snapshot["outstanding"]["outstanding_cents"], 1_160_000)
        billing_totals = billing_snapshot(self.owner)
        self.assertEqual(snapshot["outstanding"]["outstanding_cents"],
                         billing_totals["outstanding_cents"])
        self.assertEqual(snapshot["cash"]["account_total_cents"],
                         billing_totals["account_total_cents"])
        self.assertEqual(snapshot["cash"]["account_total_cents"], 1_450_000)
        self.assertEqual(snapshot["cash"]["payment_unlinked_count"], 1)
        self.assertEqual(snapshot["cash"]["payment_unlinked_cents"],
                         ExpensePayment.objects.aggregate(total=Sum("amount_cents"))["total"])

    def test_revenue_basis_invoice_default_and_receipt_switch(self):
        self.assertEqual(resolve_basis(None), BASIS_INVOICE)
        self.assertEqual(resolve_basis("unknown"), BASIS_INVOICE)
        receipt = dashboard_snapshot(self.owner, basis=BASIS_RECEIPT, today=self.today)
        rows = {row["project"].code: row for row in receipt["rows"]}
        self.assertEqual(rows["Q33-001"]["revenue_cents"], 400_000)
        self.assertEqual(rows["Q33-002"]["revenue_cents"], 0)
        self.assertTrue(rows["Q33-002"]["margin_undefined"])
        self.assertEqual(rows["Q33-001"]["margin_bp"], 2500)  # (400,000 − 300,000) / 400,000
        invoice = dashboard_snapshot(self.owner, basis=BASIS_INVOICE, today=self.today)
        invoice_rows = {row["project"].code: row for row in invoice["rows"]}
        # 1,060,000 gross − 60,000 tax = 1,000,000 net revenue.
        self.assertEqual(invoice_rows["Q33-001"]["revenue_cents"], 1_000_000)
        self.assertEqual(invoice_rows["Q33-001"]["invoiced_gross_cents"], 1_060_000)
        self.assertEqual(invoice_rows["Q33-001"]["invoiced_net_cents"], 1_000_000)
        self.assertEqual(invoice_rows["Q33-001"]["cost_cents"], 300_000)
        self.assertEqual(invoice_rows["Q33-001"]["gross_cents"], 700_000)

    def test_ranking_sorted_by_gross_and_zero_revenue_margin_undefined(self):
        snapshot = dashboard_snapshot(self.owner, today=self.today)
        gross = [row["gross_cents"] for row in snapshot["ranking"]]
        self.assertEqual(gross, sorted(gross, reverse=True))
        rows = {row["project"].code: row for row in snapshot["rows"]}
        self.assertTrue(rows["Q33-003"]["margin_undefined"])
        self.assertIsNone(rows["Q33-003"]["margin_bp"])
        self.assertTrue(rows["Q33-003"]["uninvoiced"])

    def test_gaps_are_counted_instead_of_fake_zeros(self):
        snapshot = dashboard_snapshot(self.owner, today=self.today)
        gaps = snapshot["gaps"]
        self.assertGreaterEqual(gaps["uninvoiced_count"], 1)
        self.assertGreaterEqual(gaps["no_receipt_count"], 1)
        self.assertGreaterEqual(gaps["labor_gap_count"], 1)
        self.assertEqual(gaps["legacy_receipt_count"], 1)
        self.assertEqual(gaps["payment_unlinked_count"], 1)
        self.assertEqual(gaps["non_project_receipt_count"], 1)
        self.assertGreaterEqual(snapshot["cash"]["payment_unlinked_cents"], 100_000)

    def test_pm_scope_is_own_projects_and_billing_account_visibility_matches(self):
        snapshot = dashboard_snapshot(self.pm, today=self.today)
        codes = {row["project"].code for row in snapshot["rows"]}
        self.assertEqual(codes, {"Q33-001", "Q33-003"})
        self.assertEqual(snapshot["ranking"][0]["project"].code, "Q33-001")
        # /billing/ exposes the same company account balances to owner and PM; Q-33 aligns.
        self.assertEqual(snapshot["cash"]["account_total_cents"], 1_450_000)
        # PM does not see the other PM's project or non-project receipts in monthly cash.
        self.assertEqual(snapshot["month"]["received_cents"], 400_000)

    def test_member_finance_auditor_are_denied(self):
        for member in (self.member, self.finance, self.auditor):
            with self.subTest(role=member.role):
                with self.assertRaises(PermissionDenied):
                    dashboard_snapshot(member, today=self.today)

    def test_cross_tenant_data_is_excluded(self):
        foreign_account = billing.register_account(
            self.foreign_owner, name="隔离账户", kind="bank", opening_balance_cents=7_000_000,
            note="", token=uuid.uuid4())
        billing.register_invoice(
            self.foreign_owner, project=self.foreign_project, number="Q33-F-INV",
            amount_cents=9_000_000, tax_rate_bp=None, issued_on=self.today,
            contract_reference="", note="", token=uuid.uuid4())
        billing.register_receipt(
            self.foreign_owner, project=self.foreign_project, non_project=False,
            account=foreign_account, invoice=None, amount_cents=5_000_000,
            received_on=self.today, counterparty="", reference="Q33-F-RCPT",
            note="", token=uuid.uuid4())
        snapshot = dashboard_snapshot(self.owner, today=self.today)
        codes = {row["project"].code for row in snapshot["rows"]}
        self.assertNotIn("Q33-F01", codes)
        self.assertEqual(snapshot["month"]["invoiced_cents"], 1_500_000)
        self.assertEqual(snapshot["cash"]["account_total_cents"], 1_450_000)

    def test_taxed_invoice_regression_uses_net_revenue_and_monthly_invoiced(self):
        # Explicit reviewer probe: invoice 1,060,000 with 60,000 tax → revenue 1,000,000.
        invoice = Invoice.objects.get(number="Q33-INV-001")
        self.assertEqual(invoice.amount_cents, 1_060_000)
        self.assertEqual(invoice.tax_cents, 60_000)
        snapshot = dashboard_snapshot(self.owner, today=self.today)
        row = {item["project"].code: item for item in snapshot["rows"]}["Q33-001"]
        self.assertEqual(row["revenue_cents"], invoice.amount_cents - invoice.tax_cents)
        self.assertEqual(snapshot["month"]["invoiced_cents"], 1_500_000)
        self.assertEqual(snapshot["month"]["invoiced_gross_cents"], 1_560_000)
        # 未收款 remains the cash/gross basis (invoiced − received).
        self.assertEqual(snapshot["outstanding"]["outstanding_cents"], 1_160_000)

    def test_q36_payment_is_not_counted_as_historical_professional_payment(self):
        # Fixture has one Q-36 ExpensePayment and its payment Document; it is 1.0 cash,
        # never a historical professional payment.
        self.assertEqual(dashboard_snapshot(self.owner, today=self.today)["gaps"]["legacy_payment_count"], 0)
        Document.objects.create(
            project=self.project, kind=Document.Kind.PAYMENT, title="历史专业付款",
            amount_cents=150_000, occurred_on=self.today, reference="Q33-LEGACY-PAY",
            submitted_by=self.owner.user, status="approved")
        self.assertEqual(dashboard_snapshot(self.owner, today=self.today)["gaps"]["legacy_payment_count"], 1)

    def test_uninvoiced_boundary_uses_net_revenue(self):
        # Gross > 0 but fully taxed (net 0) is still "uninvoiced income"; margin undefined.
        Invoice.objects.create(
            organization=self.org, project=self.uninvoiced_project, number="Q33-EDGE-NET-0",
            amount_cents=100_000, tax_cents=100_000, tax_rate_bp=None, issued_on=self.today,
            token=uuid.uuid4(), created_by=self.owner.user)
        snapshot = dashboard_snapshot(self.owner, today=self.today)
        row = {item["project"].code: item for item in snapshot["rows"]}["Q33-003"]
        self.assertEqual(row["invoiced_gross_cents"], 100_000)
        self.assertEqual(row["invoiced_net_cents"], 0)
        self.assertEqual(row["revenue_cents"], 0)
        self.assertTrue(row["uninvoiced"])
        self.assertTrue(row["margin_undefined"])
        self.assertIsNone(row["margin_bp"])

    def test_margin_excludes_historical_professional_cost_and_lists_gap(self):
        legacy = Document.objects.create(
            project=self.project, kind=Document.Kind.EXPENSE, title="历史专业费用",
            amount_cents=200_000, occurred_on=self.today, reference="Q33-LEGACY-EXP",
            submitted_by=self.owner.user, status="approved")
        LedgerEntry.objects.create(project=self.project, book=LedgerEntry.Book.COST,
                                   amount_cents=200_000, category="vendor",
                                   occurred_on=self.today, document=legacy)
        snapshot = dashboard_snapshot(self.owner, today=self.today)
        row = {item["project"].code: item for item in snapshot["rows"]}["Q33-001"]
        self.assertEqual(row["cost_cents"], 300_000)  # Q-36 only
        self.assertEqual(row["historical_cost_cents"], 200_000)
        self.assertEqual(snapshot["gaps"]["historical_cost_cents"], 200_000)
        self.assertEqual(snapshot["gaps"]["historical_cost_projects"], 1)

    def test_snapshot_is_read_only(self):
        before = (LedgerEntry.objects.count(), AuditEvent.objects.count(),
                  Document.objects.count(), ExpensePayment.objects.count())
        dashboard_snapshot(self.owner, today=self.today)
        dashboard_snapshot(self.owner, basis=BASIS_RECEIPT, today=self.today)
        after = (LedgerEntry.objects.count(), AuditEvent.objects.count(),
                 Document.objects.count(), ExpensePayment.objects.count())
        self.assertEqual(before, after)


@override_settings(PMO_SCOPE="micro")
class DashboardHttpTests(DashboardFixture):
    def setUp(self):
        self.client = Client()

    def test_owner_dashboard_renders_revenue_cash_and_ranking(self):
        self.login(self.owner)
        page = self.client.get(reverse("dashboard"))
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["basis"], BASIS_INVOICE)
        self.assertIsNotNone(page.context["business"])
        for text in ("本月开票", "本月已收款", "本月已支出", "未收款合计",
                     "资金账户余额", "项目毛利排行", "无定义", "未开票", "缺费率"):
            self.assertContains(page, text)
        self.assertContains(page, reverse("project-detail", args=[self.project.pk]))
        self.assertContains(page, "11,600.00")  # outstanding ¥11,600.00 (gross cash)
        self.assertContains(page, "15,000.00")  # monthly net invoiced ¥15,000.00
        self.assertContains(page, "15,600.00")  # gross invoiced shown as subtext

    def test_basis_query_switch_and_invalid_defaults_to_invoice(self):
        self.login(self.owner)
        receipt = self.client.get(reverse("dashboard"), {"basis": "receipt"})
        self.assertEqual(receipt.context["basis"], BASIS_RECEIPT)
        self.assertContains(receipt, "按收款确认收入")
        invalid = self.client.get(reverse("dashboard"), {"basis": "nonsense"})
        self.assertEqual(invalid.context["basis"], BASIS_INVOICE)

    def test_member_dashboard_hides_finance_and_has_no_aggregate(self):
        self.login(self.member)
        page = self.client.get(reverse("dashboard"))
        self.assertEqual(page.status_code, 200)
        self.assertIsNone(page.context["business"])
        self.assertContains(page, "仅向公司负责人和项目经理开放")
        self.assertNotContains(page, "未收款合计")
        self.assertNotContains(page, "项目毛利排行")

    def test_dashboard_render_is_read_only(self):
        self.login(self.owner)
        before = (LedgerEntry.objects.count(), AuditEvent.objects.count())
        self.client.get(reverse("dashboard"))
        after = (LedgerEntry.objects.count(), AuditEvent.objects.count())
        self.assertEqual(before, after)


class SeedDemoBillingTests(TestCase):
    """`seed_demo` must produce the Q-33 demo facts (accounts, invoices, receipts, gaps)."""

    def test_seed_demo_creates_dashboard_facts_and_remains_idempotent(self):
        stdout = io.StringIO()
        with TemporaryDirectory() as tmp, override_settings(BASE_DIR=Path(tmp)):
            call_command("seed_demo", stdout=stdout)
            call_command("seed_demo", stdout=stdout)  # repeat is a no-op, not a duplicate
        self.assertEqual(Account.objects.count(), 2)
        self.assertEqual(Invoice.objects.count(), 3)
        self.assertEqual(Receipt.objects.count(), 3)
        owner = Membership.objects.get(role="owner")
        snapshot = dashboard_snapshot(owner)
        self.assertEqual(snapshot["month"]["invoiced_cents"], 20_581_132)  # net of tax
        self.assertEqual(snapshot["month"]["invoiced_gross_cents"], 21_600_000)
        self.assertEqual(snapshot["month"]["received_cents"], 9_100_000)
        self.assertEqual(snapshot["month"]["spent_cents"], 700_000)
        self.assertEqual(snapshot["outstanding"]["outstanding_cents"], 13_000_000)
        self.assertEqual(snapshot["cash"]["account_total_cents"], 17_100_000)
        self.assertEqual(snapshot["gaps"]["labor_gap_count"], 1)
        self.assertGreaterEqual(snapshot["gaps"]["payment_unlinked_count"], 1)
        self.assertGreater(snapshot["gaps"]["historical_cost_cents"], 0)
        self.assertEqual(snapshot["gaps"]["legacy_payment_count"], 3)
