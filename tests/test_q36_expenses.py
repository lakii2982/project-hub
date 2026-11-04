"""Q-36 费用/外包登记、成本确认、付款主从与 8 周现金计划。

验收覆盖：登记/确认/更正留痕；整数分与 ROUND_HALF_UP；同一支出只计一次成本；
分次付款与未付进入计划；重复提交幂等；越权/跨租户/非项目成员拒绝；附件类型与大小；
项目页与工作台成本一致、缺确认显示缺口。
"""
import io
import json
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import CommandError, call_command
from django.db import connection
from django.db.models import Sum
from django.test import Client, TestCase, override_settings
from django.test.utils import skipUnless
from django.urls import reverse
from django.utils import timezone

from core.models import (AuditEvent, Document, LedgerEntry, Membership, Organization,
                         Project, ProjectMember)
from core.reporting import project_summary
from core.services import reverse as reverse_record
from pmo_plugins.billing.models import Account
from pmo_plugins.billing.services import register_account
from pmo_plugins.expenses.models import ExpenseDetail, ExpensePayment
from pmo_plugins.expenses.services import (cash_plan, correct_expense, dashboard_expense_facts,
                                           confirm_expense, expense_snapshot,
                                           project_expense_summary, record_payment,
                                           register_expense, to_cents)


class ExpenseFixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.today = timezone.localdate()
        cls.org = Organization.objects.create(name="费用测试公司")
        cls.foreign_org = Organization.objects.create(name="外部费用公司")
        roles = [("owner", "owner"), ("finance", "finance"), ("pm", "pm"), ("member", "member"),
                 ("auditor", "auditor"), ("otherpm", "pm")]
        cls.members = {}
        for name, role in roles:
            user = users.objects.create_user(f"q36-{name}", password="test-pass-9182")
            cls.members[name] = Membership.objects.create(organization=cls.org, user=user, role=role)
        cls.owner = cls.members["owner"]
        cls.finance = cls.members["finance"]
        cls.pm = cls.members["pm"]
        cls.member = cls.members["member"]
        cls.auditor = cls.members["auditor"]
        cls.other_pm = cls.members["otherpm"]
        cls.foreign = Membership.objects.create(
            organization=cls.foreign_org,
            user=users.objects.create_user("q36-foreign", password="test-pass-9182"), role="owner")
        cls.project = cls.make_project(cls.org, cls.pm, "Q36-001", "费用主项目")
        cls.other_project = cls.make_project(cls.org, cls.other_pm, "Q36-002", "他人项目")
        cls.foreign_project = cls.make_project(cls.foreign_org, cls.foreign, "F-001", "外部项目")
        ProjectMember.objects.create(project=cls.project, member=cls.member)
        cls.account = register_account(cls.owner, name="费用测试账户", kind="bank",
                                       opening_balance_cents=0, note="", token=uuid.uuid4())
        cls.tokens = []

    @classmethod
    def make_project(cls, org, manager, code, name):
        return Project.objects.create(
            organization=org, code=code, name=name, client="演练客户", manager=manager,
            stage=Project.Stage.EXECUTION, start_date=cls.today - timedelta(days=60),
            end_date=cls.today + timedelta(days=120), budget_cents=10_000_000,
            budget_approved=True, scope="范围", acceptance_criteria="验收", contract_reference="HT-1")

    def setUp(self):
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def token(self):
        value = uuid.uuid4()
        self.tokens.append(value)
        return value

    def register(self, member=None, **overrides):
        data = dict(project=self.project, category="vendor", amount_cents=1_000_000,
                    occurred_on=self.today, expected_pay_on=self.today + timedelta(days=7),
                    note="外包开发首期", counterparty="演练供应商", token=self.token())
        data.update(overrides)
        return register_expense(member or self.owner, **data)

    def confirm(self, member=None, **overrides):
        return confirm_expense(member or self.owner, self.register(**overrides).pk)

    def pay(self, document, member=None, amount_cents=400_000, paid_on=None, note="首笔付款",
            token=None):
        return record_payment(member or self.owner, document.pk, account=self.account,
                              amount_cents=amount_cents, paid_on=paid_on or self.today,
                              note=note, token=token or self.token())

    def ledger(self, book="cost", document=None):
        qs = LedgerEntry.objects.filter(project=self.project, book=book)
        return qs.filter(document=document) if document is not None else qs


class MoneyTests(ExpenseFixture):
    def test_yuan_parsing_is_decimal_half_up_and_bounded(self):
        self.assertEqual(to_cents(Decimal("123.45")), 12345)
        self.assertEqual(to_cents("123.455"), 12346)   # ROUND_HALF_UP, never float truncation
        self.assertEqual(to_cents("0.005"), 1)
        self.assertEqual(to_cents(100), 100)           # integers are already cents
        for bad in (0, -1, True, 1.1, 12.34, "abc", "1e999999", Decimal("NaN")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    to_cents(bad)


class RegisterConfirmTests(ExpenseFixture):
    def test_register_leaves_cost_pending_then_confirm_counts_once(self):
        document = self.register()
        self.assertEqual(document.status, "submitted")
        self.assertTrue(ExpenseDetail.objects.filter(document=document).exists())
        self.assertEqual(self.ledger().count(), 0)
        self.assertEqual(project_summary(self.project)["cost_cents"], 0)
        self.assertGreater(AuditEvent.objects.filter(action="expense.registered").count(), 0)

        confirm_expense(self.owner, document.pk)
        document.refresh_from_db()
        self.assertEqual(document.status, "approved")
        self.assertEqual(self.ledger().count(), 1)
        entry = self.ledger().get()
        self.assertEqual(entry.amount_cents, 1_000_000)
        self.assertEqual(project_summary(self.project)["cost_cents"], 1_000_000)
        self.assertTrue(AuditEvent.objects.filter(action="expense.confirmed").exists())

    def test_repeat_confirm_and_repeat_register_are_idempotent(self):
        token = self.token()
        first = self.register(token=token)
        again = self.register(token=token)
        self.assertEqual(first.pk, again.pk)
        confirm_expense(self.owner, first.pk)
        confirm_expense(self.owner, first.pk)
        confirm_expense(self.pm, first.pk)
        self.assertEqual(Document.objects.filter(kind="expense", project=self.project).count(), 1)
        self.assertEqual(self.ledger().count(), 1)
        self.assertEqual(project_summary(self.project)["cost_cents"], 1_000_000)

    def test_same_token_with_changed_content_is_rejected(self):
        token = self.token()
        self.register(token=token)
        with self.assertRaises(ValidationError):
            self.register(token=token, amount_cents=1_000_001)

    def test_member_is_not_a_finance_writer(self):
        with self.assertRaises(PermissionDenied):
            self.register(member=self.member)
        with self.assertRaises(PermissionDenied):
            self.register(member=self.auditor)

    def test_non_manager_pm_and_foreign_tenant_are_rejected(self):
        # A PM who does not manage the project must not even learn it exists.
        with self.assertRaises(ValidationError):
            self.register(member=self.other_pm)
        # Another tenant's project can never be written from this company.
        with self.assertRaises(ValidationError):
            self.register(project=self.foreign_project)
        with self.assertRaises(ValidationError):
            self.register(project=Project.objects.create(
                organization=self.org, code="Q36-CLOSED", name="已关闭", client="客户",
                manager=self.pm, stage=Project.Stage.CLOSED,
                start_date=self.today - timedelta(days=5), end_date=self.today))

    def test_future_or_locked_period_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.register(occurred_on=self.today + timedelta(days=1))
        Organization.objects.filter(pk=self.org.pk).update(locked_through=self.today)
        try:
            with self.assertRaises(ValidationError):
                self.register()
        finally:
            Organization.objects.filter(pk=self.org.pk).update(locked_through=None)

    def test_optional_attachment_is_hashed_and_bad_type_rejected(self):
        document = self.register(attachment=SimpleUploadedFile(
            "演练凭证.txt", "演示用虚构凭证，不含真实客户数据".encode("utf-8")))
        evidence = document.evidence_files.get()
        self.assertEqual(len(evidence.sha256), 64)
        self.assertEqual(evidence.original_name, "演练凭证.txt")
        with self.assertRaises(ValidationError):
            self.register(attachment=SimpleUploadedFile("malware.exe", b"x"))


class PaymentTests(ExpenseFixture):
    def test_split_payments_post_cash_only_and_never_a_second_cost(self):
        document = self.confirm(amount_cents=1_000_000)
        first = self.pay(document, amount_cents=400_000)
        second = self.pay(document, amount_cents=600_000, note="尾款")
        self.assertEqual(ExpensePayment.objects.filter(expense=document).count(), 2)
        self.assertEqual(self.ledger("cost").count(), 1)
        self.assertEqual(self.ledger("cash_out").count(), 2)
        self.assertEqual(self.ledger("cash_out").aggregate(total=Sum("amount_cents"))["total"],
            1_000_000)
        summary = project_summary(self.project)
        self.assertEqual(summary["cost_cents"], 1_000_000)
        self.assertEqual(summary["cash_out_cents"], 1_000_000)
        snapshot = expense_snapshot(self.owner, self.project)
        row = snapshot["rows"][0]
        self.assertEqual(row["paid_cents"], 1_000_000)
        self.assertEqual(row["unpaid_cents"], 0)
        self.assertEqual(cash_plan(self.owner, self.project)["items"], [])
        audit_actions = set(AuditEvent.objects.values_list("action", flat=True))
        self.assertIn("expense.payment_recorded", audit_actions)
        self.assertTrue(first.pk and second.pk)

    def test_unpaid_remainder_enters_eight_week_plan_once(self):
        document = self.confirm(amount_cents=1_000_000, expected_pay_on=self.today + timedelta(days=10))
        self.pay(document, amount_cents=250_000)
        plan = cash_plan(self.owner, self.project)
        self.assertEqual(len(plan["items"]), 1)
        item = plan["items"][0]
        self.assertEqual(item["source_kind"], "expense")
        self.assertEqual(item["source_id"], f"expense:{document.pk}")
        self.assertEqual(item["amount_cents"], 750_000)
        self.assertEqual(item["settled_cents"], 250_000)
        self.assertEqual(plan["within_amount_cents"], 750_000)
        self.assertEqual(plan["weeks"][1]["amount_cents"], 750_000)

    def test_payment_over_remaining_is_rejected(self):
        document = self.confirm(amount_cents=1_000_000)
        self.pay(document, amount_cents=900_000)
        with self.assertRaises(ValidationError):
            self.pay(document, amount_cents=100_001)
        self.pay(document, amount_cents=100_000)
        self.assertEqual(self.ledger("cash_out").count(), 2)

    def test_payment_token_idempotency_and_prepay_then_confirm(self):
        document = self.register(amount_cents=500_000)
        token = self.token()
        first = self.pay(document, amount_cents=200_000, token=token)
        again = self.pay(document, amount_cents=200_000, token=token)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(self.ledger("cash_out").count(), 1)
        confirm_expense(self.owner, document.pk)
        self.assertEqual(self.ledger("cost").count(), 1)
        self.assertEqual(self.ledger().get(book="cost").amount_cents, 500_000)
        row = expense_snapshot(self.owner, self.project)["rows"][0]
        self.assertEqual((row["paid_cents"], row["unpaid_cents"]), (200_000, 300_000))

    def test_overdue_plan_is_listed_separately_and_not_moved(self):
        document = self.confirm(amount_cents=300_000, expected_pay_on=self.today - timedelta(days=3))
        self.pay(document, amount_cents=100_000)
        plan = cash_plan(self.owner, self.project)
        self.assertEqual(plan["overdue_count"], 1)
        self.assertEqual(plan["overdue_amount_cents"], 200_000)
        self.assertEqual(plan["within_amount_cents"], 0)
        self.assertEqual(plan["items"][0]["overdue"], True)

    def test_beyond_eight_weeks_is_not_in_window(self):
        document = self.confirm(amount_cents=200_000, expected_pay_on=self.today + timedelta(days=70))
        plan = cash_plan(self.owner, self.project)
        self.assertEqual(plan["within_amount_cents"], 0)
        self.assertEqual(plan["beyond_amount_cents"], 200_000)
        self.assertEqual(plan["unpaid_total_cents"], 200_000)

    def test_payment_on_other_tenant_expense_is_rejected(self):
        foreign = register_expense(self.foreign, project=self.foreign_project, category="vendor",
                                   amount_cents=100_000, occurred_on=self.today,
                                   note="外部费用", token=self.token())
        with self.assertRaises(Document.DoesNotExist):
            self.pay(foreign)
        with self.assertRaises(Document.DoesNotExist):
            confirm_expense(self.owner, foreign.pk)


class CorrectionTests(ExpenseFixture):
    def test_correction_before_confirm_keeps_cost_zero_and_leaves_audit(self):
        document = self.register(amount_cents=1_000_000)
        result = correct_expense(self.owner, document.pk, category="travel", amount_cents=800_000,
                                 occurred_on=self.today - timedelta(days=1),
                                 expected_pay_on=self.today + timedelta(days=2),
                                 note="更正为差旅", counterparty="演练旅行商",
                                 version=1)
        self.assertTrue(result["changed"])
        self.assertEqual(self.ledger().count(), 0)
        detail = ExpenseDetail.objects.get(document=document)
        self.assertEqual(detail.version, 2)
        self.assertEqual(project_summary(self.project)["cost_cents"], 0)
        self.assertTrue(AuditEvent.objects.filter(action="expense.corrected").exists())

    def test_confirmed_correction_restates_cost_with_append_only_reversal(self):
        document = self.confirm(amount_cents=1_000_000)
        result = correct_expense(self.pm, document.pk, category="vendor", amount_cents=1_200_000,
                                 occurred_on=self.today - timedelta(days=2),
                                 expected_pay_on=self.today + timedelta(days=3),
                                 note="更正外包金额", counterparty="演练供应商", version=1)
        self.assertTrue(result["changed"])
        self.assertEqual(project_summary(self.project)["cost_cents"], 1_200_000)
        entries = list(self.ledger().order_by("id"))
        self.assertEqual(len(entries), 3)  # original + reversal + restated
        self.assertEqual(sum(entry.amount_cents for entry in entries), 1_200_000)
        self.assertTrue(self.ledger().filter(amount_cents=-1_000_000, reverses__isnull=False).exists())

    def test_note_only_correction_after_confirm_does_not_restate_ledger(self):
        document = self.confirm(amount_cents=1_000_000)
        before = self.ledger().count()
        correct_expense(self.owner, document.pk, category="vendor", amount_cents=1_000_000,
                        occurred_on=self.today, expected_pay_on=self.today + timedelta(days=7),
                        note="补充更清楚的说明", counterparty="演练供应商", version=1)
        self.assertEqual(self.ledger().count(), before)

    def test_stale_version_and_below_paid_amount_are_rejected(self):
        document = self.confirm(amount_cents=1_000_000)
        self.pay(document, amount_cents=400_000)
        with self.assertRaises(ValidationError):
            correct_expense(self.owner, document.pk, category="vendor", amount_cents=300_000,
                            occurred_on=self.today, expected_pay_on=None,
                            note="减少金额", counterparty="", version=1)
        with self.assertRaises(ValidationError):
            correct_expense(self.owner, document.pk, category="vendor", amount_cents=1_000_000,
                            occurred_on=self.today, expected_pay_on=None,
                            note="过期表单", counterparty="", version=9)


@override_settings(PMO_SCOPE="micro")
class PageAndConsistencyTests(ExpenseFixture):
    def test_owner_navigation_and_project_page_expose_expense_entry(self):
        self.login(self.owner)
        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, reverse("expenses"))
        project_page = self.client.get(reverse("project-detail", args=[self.project.pk]))
        self.assertContains(project_page, f"/expenses/?project={self.project.pk}")
        self.login(self.member)
        member_dashboard = self.client.get(reverse("dashboard"))
        self.assertNotContains(member_dashboard, reverse("expenses"))

    def test_expense_page_register_confirm_and_payment_flow(self):
        self.login(self.pm)
        page = self.client.get(reverse("expenses"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "登记一笔费用或外包支出")
        register = self.client.post(reverse("expense-register"), {
            "project": self.project.pk, "category": "vendor", "amount": "12000.00",
            "occurred_on": self.today.isoformat(),
            "expected_pay_on": (self.today + timedelta(days=5)).isoformat(),
            "counterparty": "演练外包商", "note": "演练：外包开发首期",
            "submission_token": str(uuid.uuid4()),
        })
        self.assertEqual(register.status_code, 302)
        document = Document.objects.get(kind="expense", project=self.project)
        page = self.client.get(reverse("expenses"), {"project": self.project.pk})
        self.assertContains(page, "待确认费用缺口")
        self.assertContains(page, "确认计入成本")
        confirmed = self.client.post(reverse("expense-confirm", args=[document.pk]),
                                     {"project": self.project.pk})
        self.assertEqual(confirmed.status_code, 302)
        document.refresh_from_db()
        self.assertEqual(document.status, "approved")
        self.assertEqual(self.ledger().count(), 1)
        payment = self.client.post(reverse("expense-payment", args=[document.pk]), {
            "account": self.account.pk, "amount": "4000.00",
            "paid_on": self.today.isoformat(), "note": "第一笔",
            "submission_token": str(uuid.uuid4()), "project": self.project.pk})
        self.assertEqual(payment.status_code, 302)
        self.assertEqual(ExpensePayment.objects.filter(expense=document).count(), 1)
        self.assertEqual(self.ledger("cash_out").count(), 1)
        self.assertEqual(self.ledger("cost").count(), 1)

    def test_dashboard_project_cost_and_gap_stay_consistent(self):
        self.confirm(amount_cents=1_000_000)
        self.register(amount_cents=300_000, note="待确认外包")  # gap
        self.login(self.owner)
        dashboard = self.client.get(reverse("dashboard"))
        project_page = self.client.get(reverse("project-detail", args=[self.project.pk]))
        summary = project_summary(self.project)
        self.assertEqual(dashboard.context["metrics"]["cost_cents"], summary["cost_cents"])
        self.assertEqual(project_page.context["summary"]["cost_cents"], summary["cost_cents"])
        self.assertEqual(project_page.context["expenses"]["cost_cents"], 1_000_000)
        self.assertEqual(project_page.context["expenses"]["pending_amount_cents"], 300_000)
        self.assertContains(dashboard, "待确认费用（缺口）")
        self.assertContains(dashboard, "3,000.00")  # the gap amount is shown, not a fake zero
        self.assertContains(project_page, "待确认缺口")
        self.assertContains(project_page, "费用与外包")

    def test_member_is_denied_and_foreign_tenant_sees_only_its_own_empty_page(self):
        self.login(self.member)
        self.assertEqual(self.client.get(reverse("expenses")).status_code, 403)
        self.confirm(amount_cents=1_000_000)
        self.login(self.foreign)
        foreign_page = self.client.get(reverse("expenses"))
        self.assertEqual(foreign_page.status_code, 200)
        self.assertContains(foreign_page, "还没有登记费用")
        self.assertNotContains(foreign_page, "费用主项目")

    def test_cross_tenant_confirm_and_payment_are_404(self):
        document = self.register()
        foreign = register_expense(self.foreign, project=self.foreign_project, category="vendor",
                                   amount_cents=100_000, occurred_on=self.today,
                                   note="外部费用", token=self.token())
        self.login(self.owner)
        posts = {
            reverse("expense-confirm", args=[foreign.pk]): {},
            reverse("expense-payment", args=[foreign.pk]): {
                "account": self.account.pk, "amount": "1.00",
                "paid_on": self.today.isoformat(),
                "submission_token": str(uuid.uuid4())},
        }
        for url, payload in posts.items():
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url, payload).status_code, 404)
        self.assertEqual(Document.objects.get(pk=foreign.pk).status, "submitted")
        self.assertFalse(ExpensePayment.objects.filter(expense=foreign).exists())
        self.assertEqual(Document.objects.get(pk=document.pk).status, "submitted")

    def test_simplified_expense_role_policy_is_owner_and_pm_only(self):
        document = self.confirm(amount_cents=1_000_000)
        self.assertIsNone(project_expense_summary(self.member, self.project))
        self.assertIsNone(dashboard_expense_facts(self.member))
        # finance/auditor keep legacy read records but are outside the 1.0 expense surface.
        self.assertIsNone(project_expense_summary(self.finance, self.project))
        self.assertIsNone(project_expense_summary(self.auditor, self.project))
        self.assertIsNone(dashboard_expense_facts(self.finance))
        self.assertIsNone(dashboard_expense_facts(self.auditor))
        for member in (self.member, self.finance, self.auditor):
            with self.subTest(role=member.role):
                self.login(member)
                self.assertEqual(self.client.get(reverse("expenses")).status_code, 403)
        pm_summary = project_expense_summary(self.pm, self.project)
        self.assertEqual(pm_summary["cost_cents"], 1_000_000)
        self.assertEqual(pm_summary["pending_count"], 0)
        self.assertEqual(document.status, "approved")

    def test_empty_state_renders(self):
        self.login(self.owner)
        page = self.client.get(reverse("expenses"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "还没有登记费用")
        self.assertContains(page, "当前没有已确认未付支出")


class IntegrityCorrectionTests(ExpenseFixture):
    """Append-only correction chains must keep verify_integrity green (Q-36 review regression)."""

    def verify(self):
        out = io.StringIO()
        try:
            call_command("verify_integrity", "--json", stdout=out)
            return json.loads(out.getvalue())
        except CommandError:
            result = json.loads(out.getvalue())
            result["command_failed"] = True
            return result

    def active_cost_entries(self, document):
        return self.ledger("cost", document).filter(
            reverses__isnull=True, reversal__isnull=True)

    def test_reviewer_repro_confirm_then_correct_keeps_verify_integrity_green(self):
        # Register ¥10,000 -> confirm -> correct to ¥12,000 was reported as
        # ledger:source_amount_or_book + document:posting_count before the chain fix.
        document = self.register(amount_cents=1_000_000)
        confirm_expense(self.owner, document.pk)
        correct_expense(self.owner, document.pk, category="vendor", amount_cents=1_200_000,
                        occurred_on=self.today, expected_pay_on=None,
                        note="复现复核报告：确认后更正金额", counterparty="演练供应商", version=1)
        self.assertEqual(self.ledger("cost", document).count(), 3)
        self.assertEqual(project_summary(self.project)["cost_cents"], 1_200_000)
        result = self.verify()
        self.assertTrue(result["ok"], result["errors"])
        self.assertFalse(result.get("command_failed"))

    def test_multiple_amount_and_category_corrections_verify_and_count_once(self):
        document = self.confirm(amount_cents=1_000_000)
        correct_expense(self.owner, document.pk, category="vendor", amount_cents=1_200_000,
                        occurred_on=self.today - timedelta(days=2),
                        expected_pay_on=self.today + timedelta(days=5),
                        note="第一次更正金额", counterparty="演练供应商", version=1)
        correct_expense(self.pm, document.pk, category="travel", amount_cents=1_200_000,
                        occurred_on=self.today - timedelta(days=3),
                        expected_pay_on=self.today + timedelta(days=6),
                        note="第二次更正类别与日期", counterparty="演练旅行商", version=2)
        correct_expense(self.owner, document.pk, category="travel", amount_cents=1_500_000,
                        occurred_on=self.today - timedelta(days=3),
                        expected_pay_on=self.today + timedelta(days=6),
                        note="第三次更正金额", counterparty="演练旅行商", version=3)
        document.refresh_from_db()
        self.assertEqual(document.amount_cents, 1_500_000)
        self.assertEqual(self.active_cost_entries(document).count(), 1)
        self.assertEqual(self.ledger("cost", document).aggregate(
            total=Sum("amount_cents"))["total"], 1_500_000)
        self.assertEqual(project_summary(self.project)["cost_cents"], 1_500_000)
        result = self.verify()
        self.assertTrue(result["ok"], result["errors"])

    def test_corrected_expense_can_still_be_reversed_and_verify(self):
        document = self.confirm(amount_cents=1_000_000)
        correct_expense(self.owner, document.pk, category="vendor", amount_cents=1_300_000,
                        occurred_on=self.today, expected_pay_on=self.today + timedelta(days=4),
                        note="先更正再冲销", counterparty="演练供应商", version=1)
        reverse_record(self.owner, "document", document.pk, "复核后整笔冲销")
        document.refresh_from_db()
        self.assertEqual(document.status, "reversed")
        self.assertEqual(self.active_cost_entries(document).count(), 0)
        self.assertEqual(self.ledger("cost", document).aggregate(
            total=Sum("amount_cents"))["total"], 0)
        self.assertEqual(project_summary(self.project)["cost_cents"], 0)
        result = self.verify()
        self.assertTrue(result["ok"], result["errors"])

    def test_verify_integrity_detects_document_amount_drift_after_correction(self):
        document = self.confirm(amount_cents=1_000_000)
        correct_expense(self.owner, document.pk, category="vendor", amount_cents=1_200_000,
                        occurred_on=self.today, expected_pay_on=None,
                        note="更正后金额漂移探测", counterparty="", version=1)
        Document.objects.filter(pk=document.pk).update(amount_cents=1_300_000)
        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertTrue(any("source_amount_or_book" in row for row in result["errors"]),
                        result["errors"])

    def test_verify_integrity_detects_duplicate_active_cost_posting(self):
        document = self.confirm(amount_cents=1_000_000)
        correct_expense(self.owner, document.pk, category="vendor", amount_cents=1_200_000,
                        occurred_on=self.today, expected_pay_on=None,
                        note="重复成本探测", counterparty="", version=1)
        LedgerEntry.objects.create(
            project=self.project, book="cost", amount_cents=1_200_000, category="vendor",
            occurred_on=self.today, document=document, posting_key="tamper-duplicate-cost")
        result = self.verify()
        self.assertFalse(result["ok"])
        self.assertTrue(any("posting_count" in row for row in result["errors"]),
                        result["errors"])


class AttachmentIdempotencyTests(ExpenseFixture):
    def test_same_registration_token_with_same_attachment_is_idempotent(self):
        token = self.token()
        payload = b"demo attachment: fictional outsourcing acceptance note"
        first = self.register(token=token, attachment=SimpleUploadedFile("演练验收.txt", payload))
        again = self.register(token=token, attachment=SimpleUploadedFile("演练验收.txt", payload))
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(first.evidence_files.count(), 1)
        self.assertEqual(Document.objects.filter(kind="expense", project=self.project).count(), 1)

    def test_same_registration_token_with_changed_attachment_is_rejected(self):
        token = self.token()
        self.register(token=token, attachment=SimpleUploadedFile("演练验收.txt", b"first version"))
        with self.assertRaises(ValidationError):
            self.register(token=token, attachment=SimpleUploadedFile(
                "演练验收.txt", b"different bytes under the same token"))

    def test_repeated_correction_with_same_attachment_does_not_duplicate_evidence(self):
        document = self.confirm(amount_cents=1_000_000)
        payload = b"corrected acceptance evidence, fictional"
        first = correct_expense(self.owner, document.pk, category="vendor",
                                amount_cents=1_100_000, occurred_on=self.today,
                                expected_pay_on=None, note="补充附件后的更正",
                                counterparty="", version=1,
                                attachment=SimpleUploadedFile("更正凭证.txt", payload))
        self.assertTrue(first["changed"])
        again = correct_expense(self.pm, document.pk, category="vendor",
                                amount_cents=1_100_000, occurred_on=self.today,
                                expected_pay_on=None, note="补充附件后的更正",
                                counterparty="", version=2,
                                attachment=SimpleUploadedFile("更正凭证.txt", payload))
        self.assertFalse(again["changed"])
        self.assertEqual(document.evidence_files.count(), 1)


class LegacyExpenseReadOnlyTests(ExpenseFixture):
    def legacy_document(self):
        return Document.objects.create(
            project=self.project, kind=Document.Kind.EXPENSE, title="历史专业流程费用",
            category="vendor", amount_cents=500_000, occurred_on=self.today,
            reference="LEGACY-Q36-1", note="历史专业流程只读费用",
            status=Document.Status.APPROVED, submitted_by=self.owner.user)

    def test_legacy_expenses_are_disclosed_read_only_and_never_restated(self):
        legacy = self.legacy_document()
        self.login(self.owner)
        page = self.client.get(reverse("expenses"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "历史专业流程费用记录只读保留")
        self.assertContains(page, "1 笔")
        snapshot = expense_snapshot(self.owner, self.project)
        self.assertEqual(snapshot["legacy_count"], 1)
        self.assertEqual(snapshot["rows"], [])

    def test_legacy_expense_writes_return_friendly_errors_not_404(self):
        legacy = self.legacy_document()
        with self.assertRaises(ValidationError):
            confirm_expense(self.owner, legacy.pk)
        with self.assertRaises(ValidationError):
            record_payment(self.owner, legacy.pk, account=self.account,
                           amount_cents=100_000, paid_on=self.today, note="legacy",
                           token=self.token())
        with self.assertRaises(ValidationError):
            correct_expense(self.owner, legacy.pk, category="vendor", amount_cents=500_000,
                            occurred_on=self.today, expected_pay_on=None, note="legacy",
                            counterparty="", version=1)
        self.login(self.owner)
        posts = {
            reverse("expense-confirm", args=[legacy.pk]): {},
            reverse("expense-payment", args=[legacy.pk]): {
                "account": self.account.pk, "amount": "1000.00",
                "paid_on": self.today.isoformat(), "submission_token": str(uuid.uuid4())},
            reverse("expense-correct", args=[legacy.pk]): {
                "category": "vendor", "amount": "5000.00",
                "occurred_on": self.today.isoformat(), "note": "legacy",
                "counterparty": "", "version": "1"},
        }
        for url, payload in posts.items():
            with self.subTest(url=url):
                response = self.client.post(url, payload)
                self.assertEqual(response.status_code, 302)
        legacy.refresh_from_db()
        self.assertEqual(legacy.status, "approved")
        self.assertEqual(legacy.amount_cents, 500_000)
        self.assertFalse(self.ledger("cost", legacy).exists())
        self.assertFalse(ExpensePayment.objects.filter(expense=legacy).exists())


@skipUnless(connection.vendor == "postgresql",
            "PostgreSQL-only FOR UPDATE regression: SQLite does not reject outer-join locks")
class PostgresLockRegressionTests(ExpenseFixture):
    def test_confirm_payment_and_correction_lock_without_outer_join_error(self):
        document = self.register(amount_cents=1_000_000)
        confirm_expense(self.owner, document.pk)
        self.pay(document, amount_cents=400_000)
        result = correct_expense(self.owner, document.pk, category="travel",
                                 amount_cents=1_200_000, occurred_on=self.today,
                                 expected_pay_on=None, note="PostgreSQL 锁回归",
                                 counterparty="", version=1)
        self.assertTrue(result["changed"])
        self.assertEqual(project_summary(self.project)["cost_cents"], 1_200_000)
