"""Q-42 acceptance: one service-level end-to-end chain + page/export/reminder parity.

The browser walkthrough is recorded in docs/q42-browser-acceptance.md and
docs/screenshots/q42-*.png; these tests keep the same numbers checkable in CI.
"""
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import onboarding
from core.business_dashboard import dashboard_snapshot
from core.due_reminders import due_reminders
from core.models import (Document, Employee, Membership, Organization, Project,
                         ProjectMember, Timesheet)
from core.project_finance import project_finance
from core.simplified_exports import build_export, export_csv_text
from pmo_plugins.billing import services as billing
from pmo_plugins.expenses import services as expenses


@override_settings(PMO_SCOPE="micro")
class EndToEndChainTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.yesterday = cls.today - timedelta(days=1)
        cls.owner, _ = onboarding.start_company(
            name="青禾软件", username="q42owner", password="Q42-Accept-2026!x",
            password_confirm="Q42-Accept-2026!x")
        customer = onboarding.create_first_customer(cls.owner, "星河零售")
        cls.project = onboarding.create_first_project(
            cls.owner, name="零售数据平台", code="Q42-001", customer=customer,
            start_date=cls.yesterday, end_date=cls.today + timedelta(days=30))
        onboarding.record_first_contract(cls.owner, cls.project, 1_200_000, "Q42 首次合同")
        cls.timesheet = onboarding.record_first_time(
            cls.owner, project=cls.project, work_date=cls.yesterday, minutes=120,
            note="需求梳理与启动会", hourly_rate_cents=30_000)
        cls.account = billing.register_account(
            cls.owner, name="Q42 基本户", kind="bank", opening_balance_cents=500_000,
            note="", token=uuid.uuid4())
        cls.expense = expenses.register_expense(
            cls.owner, project=cls.project, category="vendor", amount_cents=200_000,
            occurred_on=cls.today, expected_pay_on=cls.today + timedelta(days=10),
            note="Q42 演示外包首期", counterparty="演示外包商", token=uuid.uuid4())
        expenses.confirm_expense(cls.owner, cls.expense.pk)
        expenses.record_payment(
            cls.owner, cls.expense.pk, account=cls.account, amount_cents=50_000,
            paid_on=cls.today, note="Q42 首笔付款", token=uuid.uuid4())
        cls.invoice = billing.register_invoice(
            cls.owner, project=cls.project, number="Q42-INV-001", amount_cents=800_000,
            tax_rate_bp=None, issued_on=cls.today, contract_reference="", note="",
            token=uuid.uuid4())
        billing.register_receipt(
            cls.owner, project=cls.project, non_project=False, account=cls.account,
            invoice=cls.invoice, amount_cents=300_000, received_on=cls.today,
            counterparty="星河零售", reference="Q42-RCPT-001", note="Q42 首笔收款",
            token=uuid.uuid4())

    def test_core_chain_numbers_match_across_pages_exports_and_reminders(self):
        finance = project_finance(self.project)
        self.assertEqual(finance["contract_cents"], 1_200_000)
        self.assertEqual(finance["invoiced_net_cents"], 800_000)
        self.assertEqual(finance["to_invoice_cents"], 400_000)
        self.assertEqual(finance["received_cents"], 300_000)
        self.assertEqual(finance["outstanding_cents"], 500_000)
        self.assertEqual(finance["labor_cost_cents"], 60_000)  # 120min × ¥300/h
        self.assertEqual(finance["expense_cost_cents"], 200_000)
        self.assertEqual(finance["cost_total_cents"], 260_000)
        self.assertEqual(finance["gross_margin_cents"], 540_000)
        self.assertEqual(finance["paid_cents"], 50_000)
        self.assertEqual(finance["cash_net_cents"], 250_000)

        dashboard = dashboard_snapshot(self.owner, today=self.today)
        self.assertEqual(dashboard["month"]["invoiced_cents"], 800_000)
        self.assertEqual(dashboard["month"]["received_cents"], 300_000)
        self.assertEqual(dashboard["month"]["spent_cents"], 50_000)
        self.assertEqual(dashboard["outstanding"]["outstanding_cents"], 500_000)
        self.assertEqual(dashboard["cash"]["account_total_cents"], 750_000)
        self.assertEqual(dashboard["cash"]["payment_unlinked_count"], 0)

        rows = build_export(self.owner, "project-finance")["rows"]
        row = next(item for item in rows if item[0] == "Q42-001")
        self.assertEqual([row[3], row[4], row[6], row[7], row[8], row[9], row[10],
                          row[11], row[12], row[13], row[15], row[16]],
                         [1_200_000, 800_000, 800_000, 400_000, 300_000, 500_000,
                          60_000, 200_000, 260_000, 540_000, 50_000, 250_000])
        time_rows = build_export(self.owner, "time")["rows"]
        self.assertEqual(sum(int(item[4]) for item in time_rows), 120)
        expense_rows = build_export(self.owner, "expenses")["rows"]
        self.assertEqual([expense_rows[0][4], expense_rows[0][6], expense_rows[0][7]],
                         [200_000, 50_000, 150_000])
        billing_rows = build_export(self.owner, "billing")["rows"]
        invoice = next(item for item in billing_rows if item[4] == "Q42-INV-001")
        receipt = next(item for item in billing_rows if item[4] == "Q42-RCPT-001")
        self.assertEqual([invoice[5], invoice[6], invoice[7], invoice[8], invoice[9]],
                         [800_000, 0, 800_000, 300_000, 500_000])
        self.assertEqual([receipt[5], receipt[8]], [300_000, 300_000])

        due = due_reminders(self.owner, today=self.today)
        unpaid = [item for item in due["items"] if item["kind"] == "unpaid_invoice"]
        self.assertEqual(len(unpaid), 1)
        self.assertEqual(unpaid[0]["amount_cents"], 500_000)
        self.assertEqual(due["counts"]["overdue_expense"], 0)
        self.assertEqual(due["counts"]["missing_rate"], 0)

        # Money invariants: every stored amount stays int cents on the same basis.
        for value in (finance["contract_cents"], finance["cost_total_cents"],
                      finance["gross_margin_cents"], dashboard["cash"]["account_total_cents"]):
            self.assertIs(type(value), int)
        self.assertEqual(self.timesheet.hourly_rate_cents, 30_000)
        self.assertEqual(self.timesheet.cost_cents, 60_000)
        self.assertEqual(self.timesheet.cost_state, "priced")

    def test_project_page_does_not_double_count_simplified_payment_as_legacy_cash(self):
        finance = project_finance(self.project)
        self.assertEqual(finance["cash_net_cents"], 250_000)
        self.assertEqual(finance["paid_cents"], 50_000)
        # Q-36 payment writes a core cash_out ledger row; it must not also appear as
        # "historical professional cash" on the project finance strip.
        self.assertEqual(finance["legacy_cash_net_cents"], 0)

    def test_exports_are_deterministic_and_audited(self):
        first = build_export(self.owner, "billing")
        second = build_export(self.owner, "billing")
        self.assertEqual(export_csv_text(first), export_csv_text(second))
        self.assertEqual(first["filename"], second["filename"])
        self.assertEqual(first["filename"],
                         f"project-hub-micro-billing-{self.owner.organization_id}-"
                         f"{self.today.isoformat()}.csv")
        client = Client()
        client.force_login(self.owner.user)
        response = client.get(reverse("micro-export-download", args=["billing"]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Disposition"].endswith(f'{first["filename"]}"'))
        from core.models import AuditEvent
        self.assertTrue(AuditEvent.objects.filter(
            organization=self.owner.organization, action="data.exported").exists())

    def test_member_and_cross_tenant_are_denied(self):
        User = get_user_model()
        member_user = User.objects.create_user("q42member")
        member = Membership.objects.create(organization=self.owner.organization,
                                          user=member_user, role="member")
        Employee.objects.create(organization=self.owner.organization, name="演示成员", member=member)
        foreign_user = User.objects.create_user("foreignowner")
        foreign_org = Organization.objects.create(name="隔离公司")
        foreign_owner = Membership.objects.create(organization=foreign_org, user=foreign_user, role="owner")
        Employee.objects.create(organization=foreign_org, name="隔离负责人", member=foreign_owner)
        foreign_project = Project.objects.create(
            organization=foreign_org, code="F-001", name="隔离项目", manager=foreign_owner,
            stage=Project.Stage.INITIATION, start_date=self.today,
            end_date=self.today + timedelta(days=30), contract_reference="隔离")
        client = Client()
        client.force_login(member_user)
        for url in ("billing", "expenses", "micro-exports", "settings"):
            with self.subTest(url=url):
                self.assertEqual(client.get(reverse(url)).status_code, 403)
        self.assertEqual(client.get(reverse("timesheets-week")).status_code, 200)
        client.force_login(foreign_user)
        self.assertEqual(client.get(reverse("project-detail", args=[self.project.pk])).status_code, 404)
        self.assertEqual(client.get(reverse("project-detail", args=[foreign_project.pk])).status_code, 200)
