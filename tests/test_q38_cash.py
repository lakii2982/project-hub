"""Q-38 资金账户派生余额、收付款流水与只读 8 周计划。

验收覆盖：余额 = 期初 + 收款 − 付款；流水对账；计划两类来源按 source key 去重、
结清移出、过期单列不挪周；预计余额为负提示缺口；只读计划零写入；付款必须关联
账户（Q-36 缺口收口）；权限/租户；1440/390 页面可渲染与账户维护。
"""
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import (AuditEvent, CashPlan, Document, LedgerEntry, Membership,
                         Organization, Project, ProjectMember)
from pmo_plugins.billing import services as billing
from pmo_plugins.billing.models import Account, Receipt
from pmo_plugins.cashbook import services as cash
from pmo_plugins.expenses import services as expenses
from pmo_plugins.expenses.models import ExpensePayment


class CashFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.today = timezone.localdate()
        cls.org = Organization.objects.create(name="现金测试公司")
        cls.other_org = Organization.objects.create(name="隔离现金公司")
        users = get_user_model()
        cls.members = {}
        for name, role in (("owner", "owner"), ("pm", "pm"), ("pm2", "pm"),
                           ("member", "member"), ("finance", "finance"), ("auditor", "auditor")):
            user = users.objects.create_user(f"q38-{name}")
            cls.members[name] = Membership.objects.create(
                organization=cls.org, user=user, role=role)
        cls.owner = cls.members["owner"]
        cls.pm = cls.members["pm"]
        cls.pm2 = cls.members["pm2"]
        cls.member = cls.members["member"]
        cls.finance = cls.members["finance"]
        cls.auditor = cls.members["auditor"]
        cls.foreign = Membership.objects.create(
            organization=cls.other_org,
            user=users.objects.create_user("q38-foreign"), role="owner")
        cls.project = cls.make_project(cls.org, cls.pm, "Q38-001", "现金主项目")
        cls.other_project = cls.make_project(cls.org, cls.pm2, "Q38-002", "他人项目")
        cls.empty_project = cls.make_project(cls.org, cls.pm, "Q38-003", "无计划项目")
        cls.foreign_project = cls.make_project(cls.other_org, cls.foreign, "F-001", "隔离项目")
        ProjectMember.objects.create(project=cls.project, member=cls.member)
        cls.account = billing.register_account(
            cls.owner, name="现金主账户", kind="bank", opening_balance_cents=1_000_000,
            note="", token=uuid.uuid4())
        cls.wallet = billing.register_account(
            cls.owner, name="现金钱包", kind="alipay", opening_balance_cents=0,
            note="", token=uuid.uuid4())

    @classmethod
    def make_project(cls, org, manager, code, name):
        return Project.objects.create(
            organization=org, code=code, name=name, client="演练客户", manager=manager,
            stage=Project.Stage.EXECUTION, start_date=cls.today - timedelta(days=30),
            end_date=cls.today + timedelta(days=120), contract_cents=10_000_000,
            budget_cents=5_000_000, budget_approved=True, scope="范围",
            acceptance_criteria="验收", contract_reference="HT-Q38")

    # ---- helpers ---------------------------------------------------------
    def receipt(self, *, project=None, account=None, amount=400_000, reference=None,
                member=None):
        return billing.register_receipt(
            member or self.owner, project=project or self.project, non_project=False,
            account=account or self.account, invoice=None, amount_cents=amount,
            received_on=self.today, counterparty="演练客户",
            reference=reference or uuid.uuid4().hex[:12], note="",
            token=uuid.uuid4())

    def expense(self, *, project=None, amount=300_000, expected_pay_on=None,
                confirmed=True, member=None):
        row = expenses.register_expense(
            member or self.pm, project=project or self.project, category="vendor",
            amount_cents=amount, occurred_on=self.today - timedelta(days=2),
            expected_pay_on=expected_pay_on, note="演练外包", counterparty="演练供应商",
            token=uuid.uuid4())
        if confirmed:
            expenses.confirm_expense(member or self.pm, row.pk)
        return row

    def pay(self, expense, *, account=None, amount=100_000, member=None, paid_on=None):
        return expenses.record_payment(
            member or self.pm, expense.pk, account=account or self.account,
            amount_cents=amount, paid_on=paid_on or self.today, note="付款",
            token=uuid.uuid4())

    def plan(self, *, project=None, amount=500_000, due_in=5, direction="in",
             approved=True, active=True):
        return CashPlan.objects.create(
            project=project or self.project, direction=direction, title="收款计划",
            counterparty="演练客户", amount_cents=amount,
            due_date=self.today + timedelta(days=due_in), reference="PLAN",
            created_by=self.pm.user, approved=approved, active=active)

    def login(self, member):
        self.client.force_login(member.user)
        return member

    def setUp(self):
        self.client = Client()


class BalanceAndFlowTests(CashFixture):
    def test_account_balance_is_opening_plus_receipts_minus_payments(self):
        self.receipt(amount=400_000)
        paid = self.expense(amount=300_000)
        self.pay(paid, amount=100_000)
        self.receipt(account=self.wallet, amount=50_000, project=None
                     if False else self.other_project, member=self.owner)
        self.assertEqual(billing.account_balance_cents(self.account), 1_300_000)
        self.assertEqual(billing.account_balance_cents(self.wallet), 50_000)
        rows = {row.pk: row for row in cash.cash_snapshot(self.owner)["accounts"]}
        self.assertEqual(rows[self.account.pk].received_cents, 400_000)
        self.assertEqual(rows[self.account.pk].paid_cents, 100_000)
        self.assertEqual(rows[self.account.pk].balance_cents, 1_300_000)
        self.assertEqual(cash.cash_snapshot(self.owner)["account_total_cents"], 1_350_000)
        # Balance is derived only: no editable balance field on the model.
        with self.assertRaises(Exception):
            Account._meta.get_field("balance_cents")

    def test_refund_reduces_account_and_flows_reconcile(self):
        receipt = self.receipt(amount=400_000, reference="Q38-IN-1")
        billing.refund_receipt(self.owner, receipt.pk, 50_000, "客户退回部分款项",
                               uuid.uuid4())
        paid = self.expense(amount=200_000)
        self.pay(paid, amount=80_000)
        snapshot = cash.cash_snapshot(self.owner)
        self.assertEqual(snapshot["account_total_cents"], 1_000_000 + 400_000 - 50_000 - 80_000)
        flows = snapshot["flows"]
        receipt_rows = [row for row in flows if row["kind"] in {"receipt", "refund"}]
        payment_rows = [row for row in flows if row["kind"] == "payment"]
        self.assertEqual(sum(row["signed_cents"] for row in receipt_rows), 350_000)
        self.assertEqual(sum(row["signed_cents"] for row in payment_rows), -80_000)
        self.assertEqual(sum(row["signed_cents"] for row in flows), 270_000)
        self.assertEqual(receipt_rows[0]["account"].pk, self.account.pk)
        self.assertEqual(payment_rows[0]["project"].pk, self.project.pk)

    def test_payment_requires_company_active_account(self):
        paid = self.expense(amount=200_000)
        with self.assertRaises(ValidationError):
            expenses.record_payment(self.pm, paid.pk, account=None, amount_cents=100,
                                    paid_on=self.today, token=uuid.uuid4())
        foreign_account = billing.register_account(
            self.foreign, name="隔离账户", kind="bank", opening_balance_cents=0, note="")
        with self.assertRaises(ValidationError):
            expenses.record_payment(self.pm, paid.pk, account=foreign_account,
                                    amount_cents=100, paid_on=self.today,
                                    token=uuid.uuid4())
        self.wallet.active = False
        self.wallet.save(update_fields=["active"])
        with self.assertRaises(ValidationError):
            expenses.record_payment(self.pm, paid.pk, account=self.wallet,
                                    amount_cents=100, paid_on=self.today,
                                    token=uuid.uuid4())
        self.assertEqual(ExpensePayment.objects.count(), 0)
        payment = self.pay(paid, amount=100_000)
        self.assertEqual(payment.account_id, self.account.pk)
        self.assertTrue(AuditEvent.objects.filter(
            action="expense.payment_recorded", entity_id=str(payment.pk)).exists())

    def test_account_maintenance_owner_only_and_audited(self):
        self.assertEqual(self.account.version, 1)
        updated = billing.update_account(
            self.owner, self.account.pk, opening_balance_cents=2_000_000,
            active=False, version=1)
        self.assertEqual(updated.opening_balance_cents, 2_000_000)
        self.assertFalse(updated.active)
        with self.assertRaises(ValidationError):
            billing.update_account(self.owner, self.account.pk, opening_balance_cents=1,
                                   active=True, version=1)  # stale version
        with self.assertRaises(PermissionDenied):
            billing.update_account(self.pm, self.account.pk, opening_balance_cents=1,
                                   active=True, version=2)
        self.assertTrue(AuditEvent.objects.filter(
            action="billing.account.updated", entity_id=str(self.account.pk)).exists())
        self.assertEqual(Account.objects.get(pk=self.account.pk).opening_balance_cents,
                         2_000_000)


class PlanTests(CashFixture):
    def test_plan_combines_payment_plans_and_unpaid_expenses_deduped(self):
        plan = self.plan(amount=500_000, due_in=5)
        unpaid = self.expense(amount=300_000,
                              expected_pay_on=self.today + timedelta(days=10))
        settled = self.expense(amount=150_000,
                               expected_pay_on=self.today + timedelta(days=3))
        self.pay(settled, amount=150_000)
        snapshot = cash.cash_snapshot(self.owner)
        plan_data = snapshot["plan"]
        keys = [item["source_id"] for item in plan_data["items"]]
        self.assertIn(f"payment_plan:{plan.pk}", keys)
        self.assertIn(f"expense:{unpaid.pk}", keys)
        self.assertNotIn(f"expense:{settled.pk}", keys)  # settled removed
        self.assertEqual(len(keys), len(set(keys)))     # no duplicate source keys
        self.assertEqual(plan_data["payment_plan_count"], 1)
        self.assertEqual(plan_data["expense_count"], 1)
        self.assertEqual(plan_data["within_in_cents"], 500_000)
        self.assertEqual(plan_data["within_out_cents"], 300_000)
        self.assertEqual(plan_data["within_net_cents"], 200_000)

    def test_legacy_and_simplified_settlement_remove_plans(self):
        legacy_plan = self.plan(amount=100_000, due_in=2)
        document = Document.objects.create(
            project=self.project, kind=Document.Kind.RECEIPT, title="旧流程收款",
            amount_cents=100_000, occurred_on=self.today, reference="Q38-LEGACY-IN",
            plan=legacy_plan, submitted_by=self.owner.user, status="approved")
        LedgerEntry.objects.create(
            project=self.project, book=LedgerEntry.Book.CASH_IN, amount_cents=100_000,
            category="other", occurred_on=self.today, document=document,
            posting_key=f"q38-legacy:{document.pk}")
        fifo_plan = self.plan(amount=300_000, due_in=7)
        later_plan = self.plan(amount=200_000, due_in=20)
        self.receipt(amount=250_000)
        snapshot = cash.cash_snapshot(self.owner)
        keys = [item["source_id"] for item in snapshot["plan"]["items"]]
        self.assertNotIn(f"payment_plan:{legacy_plan.pk}", keys)
        self.assertIn(f"payment_plan:{fifo_plan.pk}", keys)
        later = {item["source_id"]: item for item in snapshot["plan"]["items"]}
        # 250k simplified receipts settle the older 300k plan first (FIFO, read-only).
        self.assertEqual(later[f"payment_plan:{fifo_plan.pk}"]["amount_cents"], 50_000)
        self.assertEqual(later[f"payment_plan:{later_plan.pk}"]["amount_cents"], 200_000)
        self.assertEqual(snapshot["gaps"]["unplanned_receipt_cents"], 0)

    def test_no_plans_with_receipts_is_unplanned_gap_not_fake_zero(self):
        # Review probe: opening 10,000 + project receipt 4,000 and no CashPlan at all.
        self.receipt(project=self.empty_project, amount=400_000)
        snapshot = cash.cash_snapshot(self.owner)
        self.assertEqual(snapshot["plan"]["items"], [])
        self.assertEqual(snapshot["gaps"]["unplanned_receipt_cents"], 400_000)
        self.assertNotEqual(snapshot["gaps"]["unplanned_receipt_cents"], 0)

    def test_all_plans_disabled_or_unapproved_is_unplanned_gap(self):
        self.plan(project=self.empty_project, amount=500_000, due_in=5, active=False)
        self.plan(project=self.empty_project, amount=500_000, due_in=6, approved=False)
        self.receipt(project=self.empty_project, amount=400_000)
        snapshot = cash.cash_snapshot(self.owner)
        self.assertEqual(snapshot["plan"]["items"], [])
        self.assertEqual(snapshot["gaps"]["unplanned_receipt_cents"], 400_000)

    def test_unplanned_receipts_and_settled_plans_are_gaps_not_fake_zero(self):
        self.plan(amount=100_000, due_in=5)
        self.receipt(amount=250_000)
        snapshot = cash.cash_snapshot(self.owner)
        self.assertEqual(snapshot["gaps"]["unplanned_receipt_cents"], 150_000)
        self.assertEqual(len(snapshot["plan"]["items"]), 0)  # plan fully settled and removed

    def test_overdue_and_beyond_are_listed_separately_and_never_moved(self):
        overdue = self.plan(amount=100_000, due_in=-1)
        beyond = self.plan(amount=200_000, due_in=70)
        current = self.plan(amount=300_000, due_in=3)
        snapshot = cash.cash_snapshot(self.owner)
        overdue_keys = [item["source_id"] for item in snapshot["plan"]["overdue"]]
        self.assertIn(f"payment_plan:{overdue.pk}", overdue_keys)
        week_keys = [item["source_id"] for week in snapshot["plan"]["weeks"]
                     for item in week["items"]]
        self.assertNotIn(f"payment_plan:{overdue.pk}", week_keys)
        self.assertIn(f"payment_plan:{current.pk}", week_keys)
        self.assertNotIn(f"payment_plan:{beyond.pk}", week_keys)
        self.assertEqual(snapshot["plan"]["beyond_count"], 1)
        self.assertEqual(snapshot["plan"]["overdue_count"], 1)
        self.assertEqual(snapshot["plan"]["overdue_in_cents"], 100_000)

    def test_unapproved_inactive_and_payable_plans_are_excluded(self):
        self.plan(amount=100_000, due_in=5, approved=False)
        self.plan(amount=100_000, due_in=5, active=False)
        payable = self.plan(amount=100_000, due_in=5, direction="out")
        snapshot = cash.cash_snapshot(self.owner)
        keys = [item["source_id"] for item in snapshot["plan"]["items"]]
        self.assertEqual(keys, [])
        self.assertGreaterEqual(snapshot["gaps"]["legacy_payable_plan_count"], 1)
        self.assertEqual(snapshot["gaps"]["legacy_payable_plan_cents"], 100_000)
        self.assertNotIn(f"payment_plan:{payable.pk}", keys)

    def test_projected_negative_balance_is_a_gap(self):
        # Account opening 10,000.00; one confirmed unpaid expense of 15,000.00 due in week 1.
        self.expense(amount=1_500_000, expected_pay_on=self.today + timedelta(days=1))
        snapshot = cash.cash_snapshot(self.owner)
        plan = snapshot["plan"]
        self.assertTrue(plan["negative"])
        self.assertLess(plan["projected_end_cents"], 0)
        self.assertEqual(plan["weeks"][0]["out_cents"], 1_500_000)
        self.assertEqual(plan["projected_end_cents"], 1_000_000 - 1_500_000)

    def test_plan_read_is_zero_write(self):
        self.plan(amount=500_000, due_in=5)
        self.expense(amount=300_000, expected_pay_on=self.today + timedelta(days=2))
        counts = {
            "documents": Document.objects.count(),
            "ledger": LedgerEntry.objects.count(),
            "audit": AuditEvent.objects.count(),
            "payments": ExpensePayment.objects.count(),
            "receipts": Receipt.objects.count(),
        }
        for _ in range(2):
            snapshot = cash.cash_snapshot(self.owner)
            self.assertIn("weeks", snapshot["plan"])
        self.assertEqual(counts["documents"], Document.objects.count())
        self.assertEqual(counts["ledger"], LedgerEntry.objects.count())
        self.assertEqual(counts["audit"], AuditEvent.objects.count())
        self.assertEqual(counts["payments"], ExpensePayment.objects.count())
        self.assertEqual(counts["receipts"], Receipt.objects.count())


@override_settings(PMO_SCOPE="micro")
class CashHttpTests(CashFixture):
    def test_navigation_and_role_access(self):
        self.login(self.owner)
        page = self.client.get(reverse("dashboard"))
        self.assertContains(page, "现金")
        self.assertContains(page, reverse("cash"))
        self.assertEqual(self.client.get(reverse("cash")).status_code, 200)
        self.login(self.pm)
        self.assertContains(self.client.get(reverse("dashboard")), "现金")
        self.assertEqual(self.client.get(reverse("cash")).status_code, 200)
        for member in (self.member, self.finance, self.auditor):
            self.login(member)
            with self.subTest(role=member.role):
                self.assertEqual(self.client.get(reverse("cash")).status_code, 403)

    def test_cash_page_renders_balance_flows_plan_and_gaps(self):
        plan = self.plan(amount=500_000, due_in=5)
        unpaid = self.expense(amount=300_000,
                              expected_pay_on=self.today + timedelta(days=10))
        receipt = self.receipt(amount=400_000, reference="Q38-HTTP-IN")
        paid = self.expense(amount=200_000)
        self.pay(paid, amount=80_000)
        self.login(self.owner)
        page = self.client.get(reverse("cash"))
        self.assertEqual(page.status_code, 200)
        for text in ("资金账户余额", "未来 8 周现金计划", "收付款流水", "数据缺口",
                     "预计 8 周后余额", "Q38-HTTP-IN", "现金主账户"):
            self.assertContains(page, text)
        self.assertContains(page, "13,200.00")  # 10,000.00 + 4,000.00 − 800.00
        self.assertContains(page, f"payment_plan:{plan.pk}")
        self.assertContains(page, f"expense:{unpaid.pk}")
        self.assertIn("cashbook/cash.html", [t.name for t in page.templates])

    def test_account_create_and_update_http(self):
        self.login(self.owner)
        created = self.client.post(reverse("cash-account-create"), {
            "name": "新现金账户", "kind": "cash", "opening_balance": "200.00",
            "note": "演练", "request_token": str(uuid.uuid4())})
        self.assertEqual(created.status_code, 302)
        account = Account.objects.get(name="新现金账户")
        self.assertEqual(account.opening_balance_cents, 20000)
        updated = self.client.post(reverse("cash-account-update", args=[account.pk]), {
            "opening_balance": "300.00", "active": "on", "version": "1"})
        self.assertEqual(updated.status_code, 302)
        account.refresh_from_db()
        self.assertEqual(account.opening_balance_cents, 30000)
        self.assertEqual(account.version, 2)
        stale = self.client.post(reverse("cash-account-update", args=[account.pk]), {
            "opening_balance": "999.00", "active": "on", "version": "1"})
        self.assertEqual(stale.status_code, 302)
        self.assertEqual(Account.objects.get(pk=account.pk).opening_balance_cents, 30000)
        self.login(self.pm)
        forbidden = self.client.post(reverse("cash-account-create"), {
            "name": "PM 账户", "kind": "cash", "opening_balance": "0",
            "note": "", "request_token": str(uuid.uuid4())})
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(Account.objects.filter(name="PM 账户").count(), 0)

    def test_pm_sees_company_balance_but_only_own_project_plans(self):
        own = self.plan(project=self.project, amount=100_000, due_in=5)
        other = self.plan(project=self.other_project, amount=200_000, due_in=5)
        self.receipt(project=self.other_project, amount=50_000, member=self.owner)
        self.login(self.pm)
        snapshot = cash.cash_snapshot(self.pm)
        keys = [item["source_id"] for item in snapshot["plan"]["items"]]
        self.assertIn(f"payment_plan:{own.pk}", keys)
        self.assertNotIn(f"payment_plan:{other.pk}", keys)
        self.assertEqual(snapshot["account_total_cents"],
                         cash.cash_snapshot(self.owner)["account_total_cents"])

    def test_unplanned_receipt_gap_rendered_and_no_fake_zero(self):
        self.receipt(project=self.empty_project, amount=400_000)
        self.login(self.owner)
        page = self.client.get(reverse("cash"))
        self.assertContains(page, "未匹配收款计划的收款 ¥ 4,000.00")
        self.assertNotContains(page, "本期无已知现金缺口")
        snapshot = cash.cash_snapshot(self.owner)
        self.assertEqual(snapshot["gaps"]["unplanned_receipt_cents"], 400_000)

    def test_zero_gap_line_only_when_all_gaps_are_zero(self):
        self.login(self.owner)
        clean = self.client.get(reverse("cash"))
        self.assertContains(clean, "本期无已知现金缺口")
        # A pending unconfirmed expense is a gap and must suppress the "no known gap" line.
        expenses.register_expense(
            self.pm, project=self.project, category="software", amount_cents=100_000,
            occurred_on=self.today, expected_pay_on=None, note="待确认费用",
            counterparty="云服务商", token=uuid.uuid4())
        page = self.client.get(reverse("cash"))
        self.assertContains(page, "未确认费用 1 笔")
        self.assertNotContains(page, "本期无已知现金缺口")

    def test_negative_projected_balance_suppresses_no_gap_line(self):
        # Re-review probe: opening 10,000 + confirmed unpaid expense 15,000, no other gaps.
        self.expense(amount=1_500_000, expected_pay_on=self.today + timedelta(days=1))
        self.login(self.owner)
        page = self.client.get(reverse("cash"))
        self.assertContains(page, "现金缺口")
        self.assertNotContains(page, "本期无已知现金缺口")
        snapshot = cash.cash_snapshot(self.owner)
        self.assertTrue(snapshot["plan"]["negative"])
        self.assertLess(snapshot["plan"]["projected_end_cents"], 0)

    def test_pm_sees_company_cash_but_not_other_project_plans(self):
        self.plan(project=self.other_project, amount=100_000, due_in=5)
        self.login(self.pm)
        page = self.client.get(reverse("cash"))
        # Q-45 移除了口径说明长句；公司现金口径与本人计划范围仍由数据和页面结构表达。
        self.assertContains(page, "资金账户余额")
        self.assertNotContains(page, "Q38-002")  # 他人项目计划不进入 PM 可见范围

    def test_legacy_unlinked_payment_is_visible_gap(self):
        paid = self.expense(amount=200_000)
        payment = self.pay(paid, amount=100_000)
        ExpensePayment.objects.filter(pk=payment.pk).update(account=None)
        self.login(self.owner)
        page = self.client.get(reverse("cash"))
        self.assertContains(page, "历史付款 1 笔未关联资金账户")
        snapshot = cash.cash_snapshot(self.owner)
        self.assertEqual(snapshot["gaps"]["unlinked_payment_count"], 1)
        self.assertEqual(snapshot["gaps"]["unlinked_payment_cents"], 100_000)
        self.assertEqual(snapshot["account_total_cents"], 1_000_000)
