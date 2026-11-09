"""Q-41 简化导出与站内到期提醒：权限、口径一致、去重幂等与只读。"""
import csv
import io
import uuid
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.due_reminders import due_reminders
from core.models import (
    AuditEvent, CostRate, Document, Employee, LedgerEntry, Membership, Organization,
    Project, ProjectMember, Timesheet,
)
from core.project_finance import project_finance
from core.weekly_time import save_week, week_start_for
from pmo_plugins.billing.services import (
    register_account, register_invoice, register_receipt,
)
from pmo_plugins.expenses.models import ExpensePayment
from pmo_plugins.expenses.services import confirm_expense, record_payment, register_expense


@override_settings(PMO_SCOPE="micro")
class Q41Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        user_model = get_user_model()
        cls.org = Organization.objects.create(name="Q41 项目公司")
        cls.today = timezone.localdate()
        cls.week_start = week_start_for(cls.today - timedelta(days=7))
        cls.owner = Membership.objects.create(
            organization=cls.org, user=user_model.objects.create_user("q41-owner", password="test-pass"),
            role="owner")
        cls.pm = Membership.objects.create(
            organization=cls.org, user=user_model.objects.create_user("q41-pm", password="test-pass"),
            role="pm")
        cls.other_pm = Membership.objects.create(
            organization=cls.org, user=user_model.objects.create_user("q41-pm2", password="test-pass"),
            role="pm")
        cls.member = Membership.objects.create(
            organization=cls.org, user=user_model.objects.create_user("q41-member", password="test-pass"),
            role="member")
        cls.finance = Membership.objects.create(
            organization=cls.org, user=user_model.objects.create_user("q41-finance", password="test-pass"),
            role="finance")
        cls.auditor = Membership.objects.create(
            organization=cls.org, user=user_model.objects.create_user("q41-auditor", password="test-pass"),
            role="auditor")
        cls.member_employee = Employee.objects.create(organization=cls.org, name="成员", member=cls.member)
        CostRate.objects.create(employee=cls.member_employee, effective_from=date(2024, 1, 1),
                                hourly_cents=12_000, basis="Q41 测试费率")
        cls.pm_employee = Employee.objects.create(organization=cls.org, name="项目经理", member=cls.pm)
        CostRate.objects.create(employee=cls.pm_employee, effective_from=date(2024, 1, 1),
                                hourly_cents=16_000, basis="Q41 测试费率")
        cls.newbie = Membership.objects.create(
            organization=cls.org, user=user_model.objects.create_user("q41-newbie", password="test-pass"),
            role="member")
        cls.newbie_employee = Employee.objects.create(organization=cls.org, name="新人", member=cls.newbie)

        def make_project(code, name, manager):
            project = Project.objects.create(
                organization=cls.org, code=code, name=name, client="示例客户", manager=manager,
                stage=Project.Stage.EXECUTION, start_date=cls.today - timedelta(days=90),
                end_date=cls.today + timedelta(days=90), contract_cents=10_000_000,
                budget_cents=8_000_000, budget_approved=True, scope="范围",
                acceptance_criteria="验收", contract_reference=f"{code}-C")
            ProjectMember.objects.create(project=project, member=manager)
            return project

        cls.project = make_project("Q41-001", "收益现金项目", cls.pm)
        cls.other_project = make_project("Q41-002", "另一项目", cls.pm)
        cls.private_project = make_project("Q41-003", "他人项目", cls.other_pm)
        cls.empty_project = make_project("Q41-004", "空项目", cls.pm)
        for membership in (cls.member, cls.newbie):
            ProjectMember.objects.create(project=cls.project, member=membership)

        cls.account = register_account(cls.owner, name="Q41 银行", kind="bank",
                                       opening_balance_cents=0, note="Q41 账户", token=uuid.uuid4())
        cls.invoice = register_invoice(
            cls.owner, project=cls.project, number="Q41-INV-001", amount_cents=4_240_000,
            tax_rate_bp=600, issued_on=cls.today - timedelta(days=20),
            contract_reference="Q41-C", note="首期开票", token=uuid.uuid4())
        cls.receipt = register_receipt(
            cls.owner, project=cls.project, non_project=False, account=cls.account,
            invoice=cls.invoice, amount_cents=1_500_000, received_on=cls.today - timedelta(days=15),
            counterparty="示例客户", reference="Q41-BANK-1", note="首期收款", token=uuid.uuid4())
        register_invoice(cls.owner, project=cls.other_project, number="Q41-INV-002",
                         amount_cents=500_000, tax_rate_bp=None,
                         issued_on=cls.today - timedelta(days=4), token=uuid.uuid4())
        cls.private_invoice = register_invoice(
            cls.owner, project=cls.private_project, number="Q41-INV-003", amount_cents=700_000,
            tax_rate_bp=None, issued_on=cls.today - timedelta(days=2), token=uuid.uuid4())

        cls.expense = register_expense(
            cls.pm, project=cls.project, category="vendor", amount_cents=500_000,
            occurred_on=cls.today - timedelta(days=20), expected_pay_on=cls.today - timedelta(days=6),
            note="Q41 已确认逾期支出", counterparty="供应商", token=uuid.uuid4())
        confirm_expense(cls.pm, cls.expense.pk)
        record_payment(cls.pm, cls.expense.pk, account=cls.account, amount_cents=200_000,
                       paid_on=cls.today - timedelta(days=3), note="Q41 部分付款", token=uuid.uuid4())
        cls.pending_expense = register_expense(
            cls.pm, project=cls.project, category="software", amount_cents=300_000,
            occurred_on=cls.today - timedelta(days=2), expected_pay_on=cls.today + timedelta(days=9),
            note="Q41 待确认费用", counterparty="云服务商", token=uuid.uuid4())

        save_week(cls.member, cls.member_employee, cls.week_start, [{
            "key": "row-0", "id": "", "token": str(uuid.uuid4()), "version": "",
            "work_date": (cls.week_start + timedelta(days=1)).isoformat(),
            "target": f"project:{cls.project.pk}", "minutes": "480", "note": "Q41 定价工时"}])
        save_week(cls.newbie, cls.newbie_employee, cls.week_start, [{
            "key": "row-0", "id": "", "token": str(uuid.uuid4()), "version": "",
            "work_date": (cls.week_start + timedelta(days=2)).isoformat(),
            "target": f"project:{cls.project.pk}", "minutes": "90", "note": "Q41 缺费率工时"}])

        cls.foreign_org = Organization.objects.create(name="Q41 外部公司")
        cls.foreign_owner = Membership.objects.create(
            organization=cls.foreign_org,
            user=user_model.objects.create_user("q41-foreign", password="test-pass"), role="owner")
        cls.foreign_project = Project.objects.create(
            organization=cls.foreign_org, code="Q41-F-1", name="外部项目", client="外部客户",
            manager=cls.foreign_owner, stage=Project.Stage.EXECUTION,
            start_date=cls.today - timedelta(days=10), end_date=cls.today + timedelta(days=30),
            contract_cents=5_000_000, scope="外部", acceptance_criteria="外部", contract_reference="F-1")
        register_invoice(cls.foreign_owner, project=cls.foreign_project, number="FOREIGN-INV-1",
                         amount_cents=900_000, tax_rate_bp=None,
                         issued_on=cls.today - timedelta(days=2), token=uuid.uuid4())

    def login(self, member):
        self.client.force_login(member.user)

    def get_csv(self, kind):
        response = self.client.get(reverse("micro-export-download", args=[kind]))
        self.assertEqual(response.status_code, 200)
        text = response.content.decode("utf-8-sig")
        return response, list(csv.reader(io.StringIO(text)))


class Q41ExportTests(Q41Fixture):
    def test_owner_can_open_export_index_and_each_csv(self):
        self.login(self.owner)
        response = self.client.get(reverse("micro-exports"))
        self.assertEqual(response.status_code, 200)
        for label in ("项目收益与现金", "工时", "费用", "开票与收款"):
            self.assertContains(response, label)
        for kind in ("project-finance", "time", "expenses", "billing"):
            response, rows = self.get_csv(kind)
            self.assertGreaterEqual(len(rows), 2)
            self.assertIn("attachment", response["Content-Disposition"])

    def test_project_finance_csv_matches_project_page_snapshot(self):
        self.login(self.owner)
        response, rows = self.get_csv("project-finance")
        header = rows[0]
        row = rows[1 + [item[0] for item in rows[1:]].index("Q41-001")]
        data = dict(zip(header, row))
        snapshot = project_finance(self.project)
        self.assertEqual(int(data["合同额_分"]), snapshot["contract_cents"])
        self.assertEqual(int(data["已开票价税合计_分"]), snapshot["invoiced_gross_cents"])
        self.assertEqual(int(data["已开票不含税_分"]), snapshot["invoiced_net_cents"])
        self.assertEqual(int(data["已收款_分"]), snapshot["received_cents"])
        self.assertEqual(int(data["未收款_分"]), snapshot["outstanding_cents"])
        self.assertEqual(int(data["1.0人工成本_分"]), snapshot["labor_cost_cents"])
        self.assertEqual(int(data["1.0直接费用_分"]), snapshot["expense_cost_cents"])
        self.assertEqual(int(data["毛利_分"]), snapshot["gross_margin_cents"])
        self.assertEqual(int(data["项目现金净额_分"]), snapshot["cash_net_cents"])
        self.assertIn("缺费率", data["缺口"])
        self.assertFalse(any("费率" in column or "工资" in column for column in header))
        from core.business_dashboard import dashboard_snapshot
        dash = dashboard_snapshot(self.owner, today=self.today)
        dash_row = {item["project"].code: item for item in dash["rows"]}["Q41-001"]
        self.assertEqual(int(data["已开票不含税_分"]), dash_row["revenue_cents"])
        self.assertEqual(int(data["1.0成本合计_分"]), dash_row["cost_cents"])
        self.assertEqual(int(data["毛利_分"]), dash_row["gross_cents"])
        empty = next(dict(zip(header, row)) for row in rows[1:] if row[0] == "Q41-004")
        self.assertIn("未开票合同额", empty["缺口"])  # gap is explicit, not a fake zero

    def test_time_csv_has_minutes_but_no_personal_rate_or_cost(self):
        self.login(self.owner)
        response, rows = self.get_csv("time")
        header = rows[0]
        self.assertIn("分钟", header)
        self.assertNotIn("费率", header)
        self.assertNotIn("成本", header)
        joined = "\n".join(",".join(row) for row in rows[1:])
        self.assertIn("Q41 定价工时", joined)
        self.assertIn("Q41 缺费率工时", joined)
        self.assertNotIn("12000", joined)  # hourly cents never exported

    def test_expense_csv_matches_expense_panel_and_marks_status(self):
        self.login(self.owner)
        response, rows = self.get_csv("expenses")
        header = rows[0]
        body = [dict(zip(header, row)) for row in rows[1:]]
        confirmed = next(row for row in body if "已确认" == row["状态"] and "Q41 已确认逾期支出" in row["说明"])
        self.assertEqual(int(confirmed["金额_分"]), 500_000)
        self.assertEqual(int(confirmed["已付_分"]), 200_000)
        self.assertEqual(int(confirmed["未付_分"]), 300_000)
        pending = next(row for row in body if row["状态"] == "待确认")
        self.assertEqual(int(pending["金额_分"]), 300_000)
        from pmo_plugins.expenses.services import project_expense_summary
        summary = project_expense_summary(self.pm, self.project)
        self.assertEqual(sum(1 for row in body if row["状态"] == "待确认"), summary["pending_count"])
        self.assertEqual(sum(int(row["金额_分"]) for row in body if row["状态"] == "已确认"), summary["cost_cents"])

    def test_billing_csv_matches_billing_totals(self):
        self.login(self.owner)
        response, rows = self.get_csv("billing")
        header = rows[0]
        body = [dict(zip(header, row)) for row in rows[1:]]
        from pmo_plugins.billing.services import project_totals
        totals = project_totals(self.project)
        invoice = next(row for row in body if row["类型"] == "发票" and row["编号/来源"] == "Q41-INV-001")
        self.assertEqual(int(invoice["价税合计_分"]), 4_240_000)
        self.assertEqual(int(invoice["其中税额_分"]), 240_000)
        self.assertEqual(int(invoice["不含税_分"]), 4_000_000)
        self.assertEqual(int(invoice["未收_分"]), 2_740_000)
        self.assertEqual(int(invoice["价税合计_分"]), totals["invoiced_cents"])
        self.assertEqual(int(invoice["未收_分"]), totals["outstanding_cents"])
        receipt = next(row for row in body if row["类型"] == "收款" and row["编号/来源"] == "Q41-BANK-1")
        self.assertEqual(int(receipt["已收或退款_分"]), 1_500_000)

    def test_exports_are_reproducible_and_audited(self):
        self.login(self.owner)
        first = self.client.get(reverse("micro-export-download", args=["project-finance"]))
        second = self.client.get(reverse("micro-export-download", args=["project-finance"]))
        self.assertEqual(first.content, second.content)
        self.assertEqual(first["Content-Disposition"], second["Content-Disposition"])
        self.assertIn(f"micro-project-finance-{self.org.pk}-{self.today.isoformat()}.csv",
                      first["Content-Disposition"])
        self.assertEqual(AuditEvent.objects.filter(action="data.exported").count(), 2)

    def test_csv_formula_injection_is_neutralized(self):
        self.project.name = "=SUM(A1:A2)"
        self.project.save(update_fields=["name"])
        self.login(self.owner)
        response, rows = self.get_csv("project-finance")
        row = next(row for row in rows if row[0] == "Q41-001")
        self.assertTrue(row[1].startswith("'="), row[1])

    def test_only_owner_and_own_organisation_can_export(self):
        for member in (self.pm, self.member, self.finance, self.auditor):
            self.login(member)
            self.assertEqual(self.client.get(reverse("micro-exports")).status_code, 403)
            self.assertEqual(self.client.get(reverse("micro-export-download", args=["time"])).status_code, 403)
        self.login(self.owner)
        response, rows = self.get_csv("billing")
        text = "\n".join(",".join(row) for row in rows)
        self.assertIn("Q41-INV-003", text)  # owner is authorized for the whole organisation
        self.assertNotIn("FOREIGN", text)   # never another tenant

    @override_settings(PMO_SCOPE="full")
    def test_export_routes_are_micro_only(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("micro-exports")).status_code, 404)
        self.assertEqual(self.client.get(reverse("micro-export-download", args=["time"])).status_code, 404)


class Q41ReminderTests(Q41Fixture):
    def test_due_reminders_are_deduped_per_business_fact_and_read_only(self):
        from pmo_plugins.billing.models import Invoice, Receipt
        before = (AuditEvent.objects.count(), Document.objects.count(), Invoice.objects.count(),
                  Receipt.objects.count(), Timesheet.objects.count(), ExpensePayment.objects.count(),
                  LedgerEntry.objects.count())
        first = due_reminders(self.owner)
        second = due_reminders(self.owner)
        after = (AuditEvent.objects.count(), Document.objects.count(), Invoice.objects.count(),
                 Receipt.objects.count(), Timesheet.objects.count(), ExpensePayment.objects.count(),
                 LedgerEntry.objects.count())
        self.assertEqual(before, after)
        self.assertEqual([item["key"] for item in first["items"]],
                         [item["key"] for item in second["items"]])
        self.assertEqual(len({item["key"] for item in first["items"]}), first["count"])
        self.assertEqual(first["counts"]["unpaid_invoice"], 3)  # owner sees all projects
        self.assertEqual(first["counts"]["overdue_expense"], 1)
        self.assertEqual(first["counts"]["missing_rate"], 1)
        self.assertEqual(mail.outbox, [])

    def test_due_page_and_dashboard_render_links_for_owner(self):
        self.login(self.owner)
        response = self.client.get(reverse("due-list"))
        self.assertEqual(response.status_code, 200)
        for text in ("到期提醒", "Q41-INV-001", "Q41 已确认逾期支出", "缺费率工时", "查看来源"):
            self.assertContains(response, text)
        self.assertContains(response, "/cashflow/?tab=invoices")
        self.assertContains(response, "/expenses/?tab=all")
        self.assertContains(response, "/time/?tab=detail")
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, "到期提醒")
        self.assertContains(dashboard, "查看全部")

    def test_pm_sees_only_own_projects_and_member_is_denied(self):
        self.login(self.pm)
        pm_due = due_reminders(self.pm)
        keys = {item["key"] for item in pm_due["items"]}
        self.assertNotIn("invoice:%s" % self.private_invoice.pk, keys)
        self.assertIn("invoice:%s" % self.invoice.pk, keys)
        self.assertEqual(self.client.get(reverse("due-list")).status_code, 200)
        self.login(self.member)
        self.assertEqual(self.client.get(reverse("due-list")).status_code, 403)
        # PH-026 后 member 的工作台固定渲染「到期提醒」空态面板：不携带任何提醒数据或入口链接。
        member_dash = self.client.get(reverse("dashboard"))
        self.assertContains(member_dash, "暂无到期事项")
        self.assertNotContains(member_dash, reverse("due-list"))
        self.login(self.finance)
        self.assertEqual(self.client.get(reverse("due-list")).status_code, 403)

    def test_partial_receipt_does_not_duplicate_invoice_reminder(self):
        self.login(self.owner)
        before = due_reminders(self.owner)["counts"]["unpaid_invoice"]
        register_receipt(self.owner, project=self.project, non_project=False, account=self.account,
                         invoice=self.invoice, amount_cents=100_000,
                         received_on=self.today - timedelta(days=1), reference="Q41-BANK-2",
                         token=uuid.uuid4())
        after = due_reminders(self.owner)["counts"]["unpaid_invoice"]
        self.assertEqual(before, after)

    @override_settings(PMO_SCOPE="full")
    def test_due_route_is_micro_only(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("due-list")).status_code, 404)

