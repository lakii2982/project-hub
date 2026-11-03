"""Q-34 项目页收益/现金：只读口径、合同额追加历史、缺口与权限。"""
import io
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.contract_changes import contract_amount_cents, contract_history, record_contract_change
from core.models import (
    AuditEvent, CostRate, Document, Employee, LedgerEntry, Membership, Organization,
    Project, ProjectContractChange, ProjectMember,
)
from core.project_finance import project_finance
from core.weekly_time import save_week, week_start_for
from pmo_plugins.billing.services import (
    register_account, register_invoice, register_receipt,
)
from pmo_plugins.expenses.services import confirm_expense, record_payment, register_expense


class Q34FinanceFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user_model = get_user_model()
        cls.org = Organization.objects.create(name="Q34 项目公司")
        cls.today = timezone.localdate()
        cls.week_start = week_start_for(cls.today - timedelta(days=7))
        users = {}
        for name, role in [("owner", "owner"), ("pm", "pm"), ("finance", "finance"),
                           ("member", "member"), ("auditor", "auditor")]:
            users[name] = cls.user_model.objects.create_user(f"q34-{name}", password="test-pass")
            setattr(cls, name, Membership.objects.create(organization=cls.org, user=users[name], role=role))
        cls.member_employee = Employee.objects.create(organization=cls.org, name="成员", member=cls.member)
        cls.pm_employee = Employee.objects.create(organization=cls.org, name="项目经理", member=cls.pm)
        for employee, rate in [(cls.member_employee, 12_000), (cls.pm_employee, 16_000)]:
            CostRate.objects.create(employee=employee, effective_from=date(2024, 1, 1),
                                    hourly_cents=rate, basis="Q34 测试费率")
        cls.newbie = Membership.objects.create(organization=cls.org, user=cls.user_model.objects.create_user(
            "q34-newbie", password="test-pass"), role="member")
        cls.newbie_employee = Employee.objects.create(organization=cls.org, name="新人", member=cls.newbie)
        cls.project = Project.objects.create(
            organization=cls.org, code="Q34-001", name="收益现金项目", client="示例客户",
            manager=cls.pm, stage=Project.Stage.EXECUTION,
            start_date=cls.today - timedelta(days=90), end_date=cls.today + timedelta(days=90),
            contract_cents=10_000_000, budget_cents=8_000_000, budget_approved=True,
            scope="范围", acceptance_criteria="验收", contract_reference="Q34-C-1")
        for membership in (cls.pm, cls.member, cls.newbie):
            ProjectMember.objects.create(project=cls.project, member=membership)
        cls.empty_project = Project.objects.create(
            organization=cls.org, code="Q34-002", name="无合同项目", client="示例客户",
            manager=cls.pm, stage=Project.Stage.EXECUTION,
            start_date=cls.today - timedelta(days=30), end_date=cls.today + timedelta(days=60),
            contract_cents=0, scope="范围", acceptance_criteria="验收", contract_reference="")
        ProjectMember.objects.create(project=cls.empty_project, member=cls.pm)
        from core.masterdata import ensure_labor_destinations
        ensure_labor_destinations(cls.owner)

        # 1.0 billed facts: 发票价税合计 42,400 元（6% 税），收款 15,000 元。
        cls.account = register_account(cls.owner, name="Q34 银行", kind="bank",
                                       opening_balance_cents=0, note="Q34 账户", token=uuid.uuid4())
        cls.invoice = register_invoice(
            cls.owner, project=cls.project, number="Q34-INV-001", amount_cents=4_240_000,
            tax_rate_bp=600, issued_on=cls.today - timedelta(days=20),
            contract_reference="Q34-C-1", note="首期开票", token=uuid.uuid4())
        cls.receipt = register_receipt(
            cls.owner, project=cls.project, non_project=False, account=cls.account,
            invoice=cls.invoice, amount_cents=1_500_000, received_on=cls.today - timedelta(days=15),
            counterparty="示例客户", reference="Q34-BANK-1", note="首期收款", token=uuid.uuid4())

        # 人工：已定价周填报 8 小时；另有 1 条无费率行形成缺口。
        save_week(cls.member, cls.member_employee, cls.week_start, [{
            "key": "row-0", "id": "", "token": str(uuid.uuid4()), "version": "",
            "work_date": (cls.week_start + timedelta(days=1)).isoformat(),
            "target": f"project:{cls.project.pk}", "minutes": "480", "note": "Q34 人工投入"}])
        save_week(cls.newbie, cls.newbie_employee, cls.week_start, [{
            "key": "row-0", "id": "", "token": str(uuid.uuid4()), "version": "",
            "work_date": (cls.week_start + timedelta(days=2)).isoformat(),
            "target": f"project:{cls.project.pk}", "minutes": "90", "note": "Q34 无费率缺口"}])

        # 费用：一笔已确认并部分付款；一笔待确认。
        cls.expense = register_expense(
            cls.pm, project=cls.project, category="vendor", amount_cents=500_000,
            occurred_on=cls.today - timedelta(days=12), expected_pay_on=cls.today + timedelta(days=5),
            note="Q34 外包支出", counterparty="供应商", token=uuid.uuid4())
        confirm_expense(cls.pm, cls.expense.pk)
        record_payment(cls.pm, cls.expense.pk, account=cls.account, amount_cents=200_000,
                       paid_on=cls.today - timedelta(days=3), note="Q34 首笔付款", token=uuid.uuid4())
        cls.pending_expense = register_expense(
            cls.pm, project=cls.project, category="software", amount_cents=300_000,
            occurred_on=cls.today - timedelta(days=2), expected_pay_on=cls.today + timedelta(days=9),
            note="Q34 待确认云服务", counterparty="云服务商", token=uuid.uuid4())
        # 历史专业流程成本（不属于 Q-35/Q-36 1.0 口径）：必须排除在 1.0 毛利外并单列缺口。
        legacy_doc = Document.objects.create(
            project=cls.project, kind=Document.Kind.EXPENSE, title="历史专业费用",
            amount_cents=250_000, occurred_on=cls.today - timedelta(days=30),
            status=Document.Status.APPROVED, submitted_by=cls.owner.user)
        LedgerEntry.objects.create(
            project=cls.project, book=LedgerEntry.Book.COST, amount_cents=250_000,
            category="vendor", occurred_on=cls.today - timedelta(days=30),
            document=legacy_doc, posting_key=f"legacy:q34:{cls.project.pk}")

        # 合同额追加变更：100,000 → 120,000 元；原立项预估保持不变。
        cls.change = record_contract_change(
            cls.owner, project=cls.project, amount_cents=12_000_000,
            note="客户追加二期范围", effective_on=cls.today - timedelta(days=10),
            token=uuid.uuid4())

        cls.foreign_org = Organization.objects.create(name="Q34 外部公司")
        cls.foreign_owner = Membership.objects.create(
            organization=cls.foreign_org,
            user=cls.user_model.objects.create_user("q34-foreign", password="test-pass"), role="owner")
        cls.outsider = Membership.objects.create(
            organization=cls.org,
            user=cls.user_model.objects.create_user("q34-outsider", password="test-pass"), role="member")
        cls.foreign_project = Project.objects.create(
            organization=cls.foreign_org, code="Q34-F-1", name="外部项目", client="外部客户",
            manager=cls.foreign_owner, stage=Project.Stage.EXECUTION,
            start_date=cls.today - timedelta(days=10), end_date=cls.today + timedelta(days=30),
            contract_cents=5_000_000, scope="外部", acceptance_criteria="外部", contract_reference="F-1")

    def login(self, member):
        self.client.force_login(member.user)

    def project_url(self, project=None):
        return reverse("project-detail", args=[(project or self.project).pk])


@override_settings(PMO_SCOPE="micro")
class Q34AggregationTests(Q34FinanceFixture):
    def test_basis_contract_revenue_margin_and_cash_are_distinct(self):
        snapshot = project_finance(self.project)
        self.assertEqual(snapshot["contract_cents"], 12_000_000)
        self.assertEqual(snapshot["contract_original_cents"], 10_000_000)
        self.assertEqual(snapshot["invoiced_gross_cents"], 4_240_000)
        self.assertEqual(snapshot["invoiced_tax_cents"], 240_000)
        self.assertEqual(snapshot["invoiced_net_cents"], 4_000_000)
        self.assertEqual(snapshot["received_cents"], 1_500_000)
        self.assertEqual(snapshot["outstanding_cents"], 2_740_000)
        self.assertEqual(snapshot["to_invoice_cents"], 7_760_000)
        self.assertEqual(snapshot["labor_cost_cents"], 96_000)
        self.assertEqual(snapshot["expense_cost_cents"], 500_000)
        self.assertEqual(snapshot["cost_total_cents"], 596_000)
        self.assertEqual(snapshot["revenue_cents"], 4_000_000)
        self.assertEqual(snapshot["gross_margin_cents"], 3_404_000)
        self.assertEqual(snapshot["paid_cents"], 200_000)
        self.assertEqual(snapshot["cash_net_cents"], 1_300_000)
        # 验收 1：合同额 ≠ 收入，毛利 ≠ 现金净额，页面固定口径说明。
        self.assertNotEqual(snapshot["contract_cents"], snapshot["revenue_cents"])
        self.assertNotEqual(snapshot["gross_margin_cents"], snapshot["cash_net_cents"])
        self.assertIn("合同额不是收入", snapshot["basis_note"])
        self.assertEqual(snapshot["revenue_basis"], "invoice")
        self.assertEqual(snapshot["revenue_basis_label"], "按开票确认收入（不含税）")
        self.assertEqual(snapshot["margin_rate_percent"], "85.1")
        self.assertEqual(snapshot["historical_cost_cents"], 250_000)  # 历史专业成本不进入 1.0 毛利
        from pmo_plugins.billing.services import project_totals
        billing = project_totals(self.project)
        self.assertEqual(snapshot["invoiced_gross_cents"], billing["invoiced_cents"])
        self.assertEqual(snapshot["received_cents"], billing["received_cents"])
        self.assertEqual(snapshot["outstanding_cents"], billing["outstanding_cents"])

    def test_gaps_have_counts_and_amounts_not_fake_zero(self):
        snapshot = project_finance(self.project)
        gaps = {gap["code"]: gap for gap in snapshot["gaps"]}
        self.assertEqual(gaps["labor_rate"]["count"], 1)
        self.assertEqual(gaps["expense_pending"]["count"], 1)
        self.assertEqual(gaps["expense_pending"]["amount_cents"], 300_000)
        self.assertEqual(gaps["uninvoiced"]["count"], 1)
        self.assertEqual(gaps["uninvoiced"]["amount_cents"], 7_760_000)
        self.assertEqual(gaps["historical_cost"]["amount_cents"], 250_000)
        self.assertEqual(gaps["historical_cost"]["count"], 1)
        self.assertNotIn("contract_missing", gaps)
        missing_contract = project_finance(self.empty_project)
        self.assertEqual({gap["code"] for gap in missing_contract["gaps"]}, {"contract_missing"})
        self.assertIsNone(missing_contract["margin_rate_percent"])

    def test_contract_changes_append_and_keep_original_history(self):
        project = self.empty_project
        first = record_contract_change(self.owner, project=project, amount_cents=5_000_000,
                                       note="首次合同额", effective_on=self.today - timedelta(days=3),
                                       token=uuid.uuid4())
        second = record_contract_change(self.owner, project=project, amount_cents=6_500_000,
                                        note="追加范围", effective_on=self.today - timedelta(days=1),
                                        token=uuid.uuid4())
        self.assertEqual(contract_amount_cents(project), 6_500_000)
        project.refresh_from_db()
        self.assertEqual(project.contract_cents, 0)  # 原立项金额不被原地改写
        history = contract_history(project)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["change"].pk, second.pk)
        self.assertEqual(history[0]["previous_cents"], 5_000_000)
        self.assertEqual(history[0]["delta_cents"], 1_500_000)
        self.assertEqual(history[1]["previous_cents"], 0)
        self.assertEqual(ProjectContractChange.objects.filter(project=project).count(), 2)
        change_audits = AuditEvent.objects.filter(action="project.contract_changed",
                                                  entity_id=str(project.pk)).count()
        self.assertEqual(change_audits, 2)

    def test_contract_change_rejects_invalid_input_and_replays_token(self):
        project = self.empty_project
        with self.assertRaises(ValidationError):
            record_contract_change(self.owner, project=project, amount_cents=100.5,
                                   note="金额不是整数分", effective_on=self.today, token=uuid.uuid4())
        with self.assertRaises(ValidationError):
            record_contract_change(self.owner, project=project, amount_cents=-1,
                                   note="负数金额", effective_on=self.today, token=uuid.uuid4())
        with self.assertRaises(ValidationError):
            record_contract_change(self.owner, project=project, amount_cents=1_000,
                                   note="短", effective_on=self.today, token=uuid.uuid4())
        with self.assertRaises(ValidationError):
            record_contract_change(self.owner, project=project, amount_cents=1_000,
                                   note="日期字符串无效", effective_on="2026-01-01", token=uuid.uuid4())
        token = uuid.uuid4()
        row = record_contract_change(self.owner, project=project, amount_cents=2_000_000,
                                     note="幂等追加", effective_on=self.today, token=token)
        self.assertEqual(record_contract_change(self.owner, project=project, amount_cents=2_000_000,
                                                note="幂等追加", effective_on=self.today, token=token), row)
        with self.assertRaises(ValidationError):
            record_contract_change(self.owner, project=project, amount_cents=2_100_000,
                                   note="同 token 改金额", effective_on=self.today, token=token)
        with self.assertRaises(ValidationError):
            record_contract_change(self.owner, project=project, amount_cents=2_000_000,
                                   note="同金额无变化", effective_on=self.today, token=uuid.uuid4())
        with self.assertRaises(ValidationError):
            record_contract_change(self.owner, project=project, amount_cents=0,
                                   note="零合同额", effective_on=self.today, token=uuid.uuid4())
        with self.assertRaises(ValidationError):
            record_contract_change(self.owner, project=project, amount_cents=2_200_000,
                                   note="非法 token", effective_on=self.today, token="not-a-uuid")
        with self.assertRaises(PermissionDenied):
            record_contract_change(self.owner, project=self.foreign_project, amount_cents=1_000,
                                   note="跨租户合同额", effective_on=self.today, token=uuid.uuid4())

    def test_no_receipt_gap_is_counted(self):
        register_invoice(self.owner, project=self.empty_project, number="Q34-NO-RCPT-1",
                         amount_cents=100_000, tax_rate_bp=None,
                         issued_on=self.today - timedelta(days=3), token=uuid.uuid4())
        snapshot = project_finance(self.empty_project)
        gaps = {gap["code"]: gap for gap in snapshot["gaps"]}
        self.assertEqual(gaps["no_receipt"]["count"], 1)
        self.assertEqual(gaps["no_receipt"]["amount_cents"], 100_000)

    def test_matches_q33_business_dashboard_numbers(self):
        try:
            from core.business_dashboard import dashboard_snapshot
        except ImportError:  # pragma: no cover - only before the Q-33 PR is merged
            self.skipTest("Q-33 core.business_dashboard is not merged yet")
        dash = dashboard_snapshot(self.owner, today=self.today)
        row = {item["project"].code: item for item in dash["rows"]}[self.project.code]
        snapshot = project_finance(self.project)
        self.assertEqual(snapshot["revenue_cents"], row["revenue_cents"])
        self.assertEqual(snapshot["cost_total_cents"], row["cost_cents"])
        self.assertEqual(snapshot["gross_margin_cents"], row["gross_cents"])
        self.assertEqual(snapshot["outstanding_cents"], row["outstanding_cents"])
        self.assertEqual(snapshot["cash_net_cents"], row["cash_net_cents"])
        self.assertEqual(snapshot["historical_cost_cents"], row["historical_cost_cents"])

    def test_contract_form_requires_submission_token(self):
        self.login(self.owner)
        before = ProjectContractChange.objects.filter(project=self.project).count()
        response = self.client.post(reverse("project-contract-change", args=[self.project.pk]), {
            "amount": "130000", "effective_on": self.today.isoformat(),
            "note": "缺少提交编号",
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ProjectContractChange.objects.filter(project=self.project).count(), before)

    def test_project_page_renders_basis_gaps_and_drilldown_links_without_writes(self):
        self.login(self.owner)
        before = (AuditEvent.objects.count(), LedgerEntry.objects.count(),
                  Document.objects.count(), ProjectContractChange.objects.count(),
                  Document.objects.filter(status="submitted").count())
        response = self.client.get(self.project_url())
        after = (AuditEvent.objects.count(), LedgerEntry.objects.count(),
                 Document.objects.count(), ProjectContractChange.objects.count(),
                 Document.objects.filter(status="submitted").count())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(before, after)  # 验收 3：只读聚合零写
        text = response.content.decode("utf-8")
        for expected in ("项目收益与现金", "合同额不是收入", "已开票（不含税）", "待开票",
                         "已收款", "未收款", "人工成本", "直接费用", "毛利", "项目现金净额",
                         "缺费率/待入账工时", "待确认费用", "未开票合同额", "历史专业成本",
                         f"/time/?project={self.project.pk}", f"/expenses/?project={self.project.pk}"):
            self.assertIn(expected, text)
        self.assertIn("Q34-INV-001", text)  # 开票明细下钻
        self.assertIn("Q34-BANK-1", text)   # 收款明细下钻
        self.assertIn("客户追加二期范围", text)  # 合同额变更历史
        self.assertNotIn("合同额变更已追加", text)

    def test_contract_change_http_is_owner_pm_append_only(self):
        self.login(self.owner)
        response = self.client.post(reverse("project-contract-change", args=[self.project.pk]), {
            "amount": "130000", "effective_on": self.today.isoformat(),
            "note": "客户再次追加范围",
            "submission_token": str(uuid.uuid4()),
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(contract_amount_cents(self.project), 13_000_000)
        self.project.refresh_from_db()
        self.assertEqual(self.project.contract_cents, 10_000_000)
        self.login(self.member)
        denied = self.client.post(reverse("project-contract-change", args=[self.project.pk]), {
            "amount": "999999", "effective_on": self.today.isoformat(),
            "note": "成员不能改合同额", "submission_token": str(uuid.uuid4())})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(contract_amount_cents(self.project), 13_000_000)


@override_settings(PMO_SCOPE="micro")
class Q34PermissionTests(Q34FinanceFixture):
    def test_member_does_not_see_finance_snapshot_or_amounts(self):
        self.login(self.member)
        response = self.client.get(self.project_url())
        self.assertEqual(response.status_code, 200)
        text = response.content.decode("utf-8")
        self.assertNotIn("项目收益与现金", text)
        self.assertNotIn("12,000,000", text)
        self.assertNotIn("4,000,000", text)

    def test_cross_tenant_and_unmanaged_project_are_rejected(self):
        self.login(self.foreign_owner)
        self.assertEqual(self.client.get(self.project_url()).status_code, 404)
        self.login(self.owner)
        self.assertEqual(self.client.get(reverse("project-detail", args=[self.foreign_project.pk])).status_code, 404)
        # 成员仅能打开本人参与的项目；公司内非项目成员同样 404。
        self.login(self.outsider)
        self.assertEqual(self.client.get(self.project_url()).status_code, 404)

    def test_project_cards_show_current_contract_and_redact_for_member(self):
        self.login(self.owner)
        response = self.client.get(reverse("projects"))
        self.assertContains(response, "当前合同额")
        self.assertContains(response, "120,000.00")  # 最新追加变更，不是历史 100,000
        self.login(self.member)
        response = self.client.get(reverse("projects"))
        self.assertNotContains(response, "当前合同额")
        self.assertNotContains(response, "120,000.00")

    def test_billing_drilldown_filters_by_project(self):
        register_invoice(self.owner, project=self.empty_project, number="Q34-INV-002",
                         amount_cents=800_000, tax_rate_bp=None,
                         issued_on=self.today - timedelta(days=2), token=uuid.uuid4())
        self.login(self.owner)
        response = self.client.get(reverse("billing") + f"?project={self.project.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "筛选项目")
        self.assertContains(response, "Q34-INV-001")
        # 明细记录按项目过滤；Q34-INV-002 只可能出现在登记发票下拉选项里。
        self.assertEqual(response.content.decode().count("Q34-INV-002"), 1)
        self.assertEqual(self.client.get(reverse("billing") + f"?project={self.foreign_project.pk}").status_code, 404)

    def test_time_drilldown_filters_to_project_and_denies_foreign_project(self):
        self.login(self.owner)
        response = self.client.get(reverse("timesheets") + f"?project={self.project.pk}")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "筛选项目")
        self.assertContains(response, "Q34 人工投入")
        self.assertEqual(self.client.get(reverse("timesheets") + f"?project={self.foreign_project.pk}").status_code, 404)


@override_settings(PMO_SCOPE="micro")
class Q34SeedConsistencyTests(TestCase):
    """Seed demo must show the same PH-26001 margin on the project page and Q-33 dashboard."""

    def test_seed_project_margin_matches_dashboard(self):
        try:
            from core.business_dashboard import dashboard_snapshot
        except ImportError:  # pragma: no cover - only before the Q-33 PR is merged
            self.skipTest("Q-33 core.business_dashboard is not merged yet")
        with TemporaryDirectory() as tmp, override_settings(BASE_DIR=Path(tmp)):
            call_command("seed_demo", stdout=io.StringIO())
        owner = Membership.objects.get(role="owner")
        project = Project.objects.get(code="PH-26001")
        snapshot = project_finance(project)
        dash = dashboard_snapshot(owner)
        row = {item["project"].code: item for item in dash["rows"]}[project.code]
        self.assertEqual(snapshot["revenue_cents"], row["revenue_cents"])
        self.assertEqual(snapshot["cost_total_cents"], row["cost_cents"])
        self.assertEqual(snapshot["gross_margin_cents"], row["gross_cents"])
        self.assertEqual(snapshot["historical_cost_cents"], row["historical_cost_cents"])
        self.assertEqual(snapshot["gross_margin_cents"], 8_452_755)
        self.assertEqual(snapshot["cost_total_cents"], 2_868_000)
