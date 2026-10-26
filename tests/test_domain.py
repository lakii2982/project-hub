import copy
import uuid
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, transaction
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from core.access import visible_projects
from core.models import (
    AuditEvent,
    CashPlan,
    CostRate,
    Document,
    Employee,
    LedgerEntry,
    Membership,
    Milestone,
    Organization,
    Project,
    ProjectMember,
    Task,
    Timesheet,
)
from core.services import (
    cents,
    create_task,
    plan_remaining,
    review,
    reverse,
    submit_document,
    submit_time,
    transition,
    verify_audit,
)


class DomainRulesTestCase(TestCase):
    """Exercise the externally meaningful PMO accounting and workflow rules."""

    @classmethod
    def setUpTestData(cls):
        cls.day_one = date(2025, 1, 10)
        cls.day_two = date(2025, 2, 10)
        cls.rate_start = date(2024, 1, 1)
        cls.user_model = get_user_model()

        cls.org = Organization.objects.create(name="测试项目公司")

        cls.owner_user = cls.user_model.objects.create_user("owner", password="test-pass")
        cls.finance_user = cls.user_model.objects.create_user("finance", password="test-pass")
        cls.pm_user = cls.user_model.objects.create_user("pm", password="test-pass")
        cls.member_user = cls.user_model.objects.create_user("member", password="test-pass")
        cls.unassigned_pm_user = cls.user_model.objects.create_user(
            "unassigned-pm", password="test-pass"
        )

        cls.owner = Membership.objects.create(
            organization=cls.org, user=cls.owner_user, role=Membership.Role.OWNER
        )
        cls.finance = Membership.objects.create(
            organization=cls.org, user=cls.finance_user, role=Membership.Role.FINANCE
        )
        cls.pm = Membership.objects.create(
            organization=cls.org, user=cls.pm_user, role=Membership.Role.PM
        )
        cls.member = Membership.objects.create(
            organization=cls.org, user=cls.member_user, role=Membership.Role.MEMBER
        )
        cls.unassigned_pm = Membership.objects.create(
            organization=cls.org,
            user=cls.unassigned_pm_user,
            role=Membership.Role.PM,
        )

        cls.member_employee = Employee.objects.create(
            organization=cls.org,
            name="项目成员",
            title="工程师",
            member=cls.member,
        )
        cls.pm_employee = Employee.objects.create(
            organization=cls.org,
            name="项目经理",
            title="项目经理",
            member=cls.pm,
        )
        for employee in (cls.member_employee, cls.pm_employee):
            CostRate.objects.create(
                employee=employee,
                effective_from=cls.rate_start,
                hourly_cents=12_000,
                basis="2024年度预算费率",
            )

        cls.project = Project.objects.create(
            organization=cls.org,
            code="P-001",
            name="交付平台",
            client="示例客户",
            manager=cls.pm,
            template=Project.Template.SOFTWARE,
            stage=Project.Stage.PLANNING,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            contract_cents=1_000_000,
            budget_cents=600_000,
            scope="交付项目经营工作台",
            acceptance_criteria="验收单与交付清单一致",
            contract_reference="",
        )
        ProjectMember.objects.create(project=cls.project, member=cls.pm)
        ProjectMember.objects.create(project=cls.project, member=cls.member)

        cls.other_org = Organization.objects.create(name="其他组织")
        other_owner_user = cls.user_model.objects.create_user(
            "other-owner", password="test-pass"
        )
        cls.other_pm_user = cls.user_model.objects.create_user(
            "other-pm", password="test-pass"
        )
        other_owner = Membership.objects.create(
            organization=cls.other_org,
            user=other_owner_user,
            role=Membership.Role.OWNER,
        )
        cls.other_pm = Membership.objects.create(
            organization=cls.other_org,
            user=cls.other_pm_user,
            role=Membership.Role.PM,
        )
        cls.other_project = Project.objects.create(
            organization=cls.other_org,
            code="OTHER-001",
            name="其他项目",
            client="其他客户",
            manager=cls.other_pm,
            stage=Project.Stage.PLANNING,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            scope="其他组织的数据",
            acceptance_criteria="不可跨组织访问",
            contract_reference="OTHER-CONTRACT",
        )
        ProjectMember.objects.create(project=cls.other_project, member=other_owner)
        ProjectMember.objects.create(project=cls.other_project, member=cls.other_pm)

    def time_data(self, **overrides):
        data = {
            "project": self.project,
            "employee": self.member_employee,
            "task": None,
            "work_date": self.day_one,
            "minutes": 480,
            "note": "项目交付工作",
        }
        data.update(overrides)
        return data

    def document_data(self, kind="expense", **overrides):
        data = {
            "project": self.project,
            "kind": kind,
            "title": f"{kind}业务单据",
            "category": "delivery",
            "amount_cents": 100_000,
            "tax_cents": 0,
            "recoverable_tax": False,
            "occurred_on": self.day_one,
            "counterparty": "业务往来单位",
            "reference": f"{kind.upper()}-{uuid.uuid4()}",
            "note": "业务单据",
            "plan": None,
        }
        data.update(overrides)
        return data

    def approved_plan(self, amount_cents=100_000, direction="in"):
        return CashPlan.objects.create(
            project=self.project,
            direction=direction,
            title="现金计划",
            counterparty="业务往来单位",
            amount_cents=amount_cents,
            due_date=self.day_one,
            reference=f"PLAN-{uuid.uuid4()}",
            created_by=self.owner_user,
            approved=True,
        )

    def submitted_and_approved_time(self, data=None, submitter=None, reviewer=None):
        row = submit_time(
            submitter or self.pm,
            data or self.time_data(),
            uuid.uuid4(),
        )
        return review(reviewer or self.finance, "time", row.pk, "approve")

    def test_cents_preserves_two_decimal_precision_for_ten_thousand_yuan(self):
        self.assertEqual(cents("10,000.01"), 1_000_001)
        self.assertEqual(cents(Decimal("10000.00")), 1_000_000)

    def test_cents_rejects_fractional_cent(self):
        with self.assertRaises(ValidationError):
            cents("10000.001")

    def test_cents_rejects_nan_negative_and_non_numeric_values(self):
        for invalid in ("NaN", "Infinity", "-0.01", "not-money"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    cents(invalid)

    def test_time_rate_is_selected_by_work_date(self):
        CostRate.objects.create(
            employee=self.member_employee,
            effective_from=self.day_two,
            hourly_cents=24_000,
            basis="2025年2月调价",
        )

        january = submit_time(self.pm, self.time_data(), uuid.uuid4())
        february = submit_time(
            self.pm,
            self.time_data(work_date=self.day_two, note="二月交付工作"),
            uuid.uuid4(),
        )

        self.assertEqual(january.hourly_rate_cents, 12_000)
        self.assertEqual(january.cost_cents, 96_000)
        self.assertEqual(february.hourly_rate_cents, 24_000)
        self.assertEqual(february.cost_cents, 192_000)

    def test_rate_change_after_approval_does_not_rewrite_history_cost(self):
        approved = self.submitted_and_approved_time()
        CostRate.objects.create(
            employee=self.member_employee,
            effective_from=date(2025, 1, 1),
            hourly_cents=30_000,
            basis="审批后补录的新费率",
        )

        approved.refresh_from_db()
        self.assertEqual(approved.hourly_rate_cents, 12_000)
        self.assertEqual(approved.cost_cents, 96_000)
        self.assertEqual(
            LedgerEntry.objects.get(timesheet=approved, book="cost").amount_cents,
            96_000,
        )

    def test_duplicate_submit_token_does_not_create_second_timesheet(self):
        token = uuid.uuid4()
        first = submit_time(self.pm, self.time_data(), token)
        second = submit_time(self.pm, self.time_data(), token)

        self.assertEqual(second.pk, first.pk)
        self.assertEqual(Timesheet.objects.filter(token=token).count(), 1)

    def test_same_time_token_with_changed_content_is_rejected(self):
        token = uuid.uuid4()
        submit_time(self.pm, self.time_data(), token)

        with self.assertRaisesMessage(ValidationError, "内容已改变"):
            submit_time(self.pm, self.time_data(minutes=60), token)

    def test_same_document_token_with_changed_amount_is_rejected(self):
        token = uuid.uuid4()
        submit_document(self.owner, self.document_data(), token)

        with self.assertRaisesMessage(ValidationError, "内容已改变"):
            submit_document(
                self.owner,
                self.document_data(amount_cents=200_000),
                token,
            )

    def test_submit_document_rereads_approved_plan_under_lock(self):
        plan = CashPlan.objects.create(
            project=self.project,
            direction=CashPlan.Direction.IN,
            title="待审批收款计划",
            counterparty="测试客户",
            amount_cents=100_000,
            due_date=self.day_one,
            reference="PLAN-STALE-REFRESH",
            created_by=self.pm_user,
            approved=False,
        )
        review(self.owner, "plan", plan.pk, "approve")

        receipt = submit_document(
            self.owner,
            self.document_data(
                kind="receipt",
                amount_cents=50_000,
                plan=plan,
                reference="RECEIPT-STALE-PLAN",
            ),
            uuid.uuid4(),
        )

        self.assertEqual(receipt.plan_id, plan.pk)
        self.assertEqual(receipt.status, Timesheet.Status.SUBMITTED)

    def test_duplicate_approval_does_not_post_twice(self):
        row = submit_time(self.pm, self.time_data(), uuid.uuid4())
        first = review(self.finance, "time", row.pk, "approve")
        second = review(self.finance, "time", row.pk, "approve")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(LedgerEntry.objects.filter(timesheet=row).count(), 1)
        self.assertEqual(
            LedgerEntry.objects.get(timesheet=row).amount_cents,
            row.cost_cents,
        )

    def test_employee_cannot_approve_own_submitted_time(self):
        row = submit_time(
            self.pm,
            self.time_data(employee=self.pm_employee),
            uuid.uuid4(),
        )

        with self.assertRaises(ValidationError):
            review(self.pm, "time", row.pk, "approve")

        row.refresh_from_db()
        self.assertEqual(row.status, Timesheet.Status.SUBMITTED)
        self.assertFalse(LedgerEntry.objects.filter(timesheet=row).exists())

    def test_employee_cannot_approve_time_submitted_by_an_agent(self):
        row = submit_time(
            self.owner,
            self.time_data(employee=self.pm_employee),
            uuid.uuid4(),
        )

        with self.assertRaises(ValidationError):
            review(self.pm, "time", row.pk, "approve")

        row.refresh_from_db()
        self.assertEqual(row.status, Timesheet.Status.SUBMITTED)

    def test_cross_project_daily_total_over_1440_minutes_is_rejected(self):
        second_project = Project.objects.create(
            organization=self.org,
            code="P-002",
            name="第二项目",
            client="示例客户",
            manager=self.pm,
            stage=Project.Stage.PLANNING,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            scope="第二项目范围",
            acceptance_criteria="第二项目验收标准",
            contract_reference="P-002-CONTRACT",
        )
        ProjectMember.objects.create(project=second_project, member=self.pm)
        ProjectMember.objects.create(project=second_project, member=self.member)

        submit_time(
            self.member,
            self.time_data(minutes=720),
            uuid.uuid4(),
        )
        with self.assertRaisesMessage(ValidationError, "当日总工时不能超过24小时"):
            submit_time(
                self.member,
                self.time_data(project=second_project, minutes=721),
                uuid.uuid4(),
            )

        self.assertEqual(
            Timesheet.objects.filter(
                employee=self.member_employee, work_date=self.day_one
            ).count(),
            1,
        )

    def test_other_organization_pm_cannot_access_or_write_project(self):
        self.assertFalse(
            visible_projects(self.other_pm).filter(pk=self.project.pk).exists()
        )
        with self.assertRaises(PermissionDenied):
            submit_time(
                self.other_pm,
                self.time_data(
                    project=self.project,
                    employee=self.member_employee,
                ),
                uuid.uuid4(),
            )

    def test_unassigned_pm_cannot_access_or_write_project(self):
        self.assertFalse(
            visible_projects(self.unassigned_pm).filter(pk=self.project.pk).exists()
        )
        with self.assertRaises(PermissionDenied):
            submit_time(
                self.unassigned_pm,
                self.time_data(employee=self.member_employee),
                uuid.uuid4(),
            )

    def test_expense_and_payment_post_only_to_their_respective_books(self):
        expense = submit_document(
            self.owner,
            self.document_data(amount_cents=80_000),
            uuid.uuid4(),
        )
        payment = submit_document(
            self.owner,
            self.document_data(kind="payment", amount_cents=60_000),
            uuid.uuid4(),
        )
        review(self.finance, "document", expense.pk, "approve")
        review(self.finance, "document", payment.pk, "approve")

        self.assertEqual(
            LedgerEntry.objects.get(document=expense, book="cost").amount_cents,
            80_000,
        )
        self.assertFalse(
            LedgerEntry.objects.filter(document=expense, book="cash_out").exists()
        )
        self.assertEqual(
            LedgerEntry.objects.get(document=payment, book="cash_out").amount_cents,
            60_000,
        )
        self.assertFalse(
            LedgerEntry.objects.filter(document=payment, book="cost").exists()
        )

    def test_recoverable_tax_switch_changes_expense_cost(self):
        taxable = submit_document(
            self.owner,
            self.document_data(
                amount_cents=100_000,
                tax_cents=13_000,
                recoverable_tax=True,
                reference="EXPENSE-TAXABLE",
            ),
            uuid.uuid4(),
        )
        non_taxable = submit_document(
            self.owner,
            self.document_data(
                amount_cents=100_000,
                tax_cents=13_000,
                recoverable_tax=False,
                reference="EXPENSE-NON-TAXABLE",
            ),
            uuid.uuid4(),
        )
        review(self.finance, "document", taxable.pk, "approve")
        review(self.finance, "document", non_taxable.pk, "approve")

        self.assertEqual(
            LedgerEntry.objects.get(document=taxable, book="cost").amount_cents,
            87_000,
        )
        self.assertEqual(
            LedgerEntry.objects.get(document=non_taxable, book="cost").amount_cents,
            100_000,
        )

    def test_partial_receipt_settles_plan_and_overallocation_is_rejected(self):
        cash_plan = self.approved_plan(amount_cents=100_000)
        first = submit_document(
            self.owner,
            self.document_data(
                kind="receipt",
                amount_cents=60_000,
                plan=cash_plan,
                reference="RECEIPT-PARTIAL",
            ),
            uuid.uuid4(),
        )
        review(self.finance, "document", first.pk, "approve")
        self.assertEqual(plan_remaining(cash_plan), 40_000)

        second = submit_document(
            self.owner,
            self.document_data(
                kind="receipt",
                amount_cents=50_000,
                plan=cash_plan,
                reference="RECEIPT-OVER",
            ),
            uuid.uuid4(),
        )
        with self.assertRaisesMessage(ValidationError, "超过计划未结金额"):
            review(self.finance, "document", second.pk, "approve")

        second.refresh_from_db()
        self.assertEqual(second.status, Timesheet.Status.SUBMITTED)
        self.assertEqual(
            LedgerEntry.objects.filter(
                document__plan=cash_plan, book="cash_in"
            ).aggregate(total=Sum("amount_cents"))["total"],
            60_000,
        )

    def test_reversal_adds_negative_record_restores_plan_and_is_idempotent(self):
        cash_plan = self.approved_plan(amount_cents=100_000)
        receipt = submit_document(
            self.owner,
            self.document_data(
                kind="receipt",
                amount_cents=40_000,
                plan=cash_plan,
            ),
            uuid.uuid4(),
        )
        review(self.finance, "document", receipt.pk, "approve")
        self.assertEqual(plan_remaining(cash_plan), 60_000)

        reversed_once = reverse(
            self.finance, "document", receipt.pk, "客户退款冲销"
        )
        reversed_again = reverse(
            self.finance, "document", receipt.pk, "重复提交冲销"
        )

        self.assertEqual(reversed_once.pk, reversed_again.pk)
        entries = LedgerEntry.objects.filter(document=receipt).order_by("id")
        self.assertEqual(entries.count(), 2)
        self.assertEqual(entries.first().amount_cents, 40_000)
        self.assertEqual(entries.last().amount_cents, -40_000)
        self.assertEqual(entries.last().reverses_id, entries.first().pk)
        self.assertEqual(plan_remaining(cash_plan), 100_000)

    def test_locked_period_rejects_backdated_time(self):
        Organization.objects.filter(pk=self.org.pk).update(locked_through=self.day_one)

        with self.assertRaisesMessage(ValidationError, "已锁账"):
            submit_time(self.pm, self.time_data(), uuid.uuid4())

        self.assertFalse(Timesheet.objects.exists())

    def test_open_period_allows_reversal_of_locked_history(self):
        row = self.submitted_and_approved_time()
        Organization.objects.filter(pk=self.org.pk).update(locked_through=self.day_one)

        reversed_row = reverse(self.finance, "time", row.pk, "锁期前工时冲销")
        reversal = LedgerEntry.objects.get(
            timesheet=row, reverses__isnull=False
        )

        self.assertEqual(reversed_row.status, Timesheet.Status.REVERSED)
        self.assertEqual(reversal.occurred_on, timezone.localdate())
        self.assertGreater(reversal.occurred_on, self.day_one)
        self.assertLess(reversal.amount_cents, 0)

    def test_create_task_rereads_project_and_rejects_stale_closed_project(self):
        Project.objects.filter(pk=self.project.pk).update(
            stage=Project.Stage.CANCELLED
        )

        with self.assertRaisesMessage(ValidationError, "项目已结束"):
            create_task(
                self.pm,
                {
                    "project": self.project,
                    "name": "不应创建的陈旧项目任务",
                    "owner": self.pm,
                    "status": Task.Status.TODO,
                    "start_date": self.day_one,
                    "due_date": self.day_two,
                    "estimate_minutes": 480,
                    "description": "",
                    "completion_note": "",
                },
            )

        self.assertFalse(
            Task.objects.filter(name="不应创建的陈旧项目任务").exists()
        )

    def test_execution_transition_requires_approved_budget_baseline(self):
        with self.assertRaisesMessage(ValidationError, "审批预算基线"):
            transition(
                self.pm,
                self.project.pk,
                Project.Stage.EXECUTION,
                self.project.version,
                "启动项目执行",
            )

    def test_execution_transition_requires_contract_reference(self):
        Project.objects.filter(pk=self.project.pk).update(budget_approved=True)
        project = Project.objects.get(pk=self.project.pk)

        with self.assertRaisesMessage(ValidationError, "合同或立项依据"):
            transition(
                self.pm,
                project.pk,
                Project.Stage.EXECUTION,
                project.version,
                "启动项目执行",
            )

    def test_execution_transition_requires_a_task(self):
        Project.objects.filter(pk=self.project.pk).update(
            budget_approved=True, contract_reference="P-001-CONTRACT"
        )
        Milestone.objects.create(
            project=self.project,
            name="首版交付",
            due_date=self.day_two,
            criteria="首版功能可用",
        )
        project = Project.objects.get(pk=self.project.pk)

        with self.assertRaisesMessage(ValidationError, "至少配置一项任务和一个里程碑"):
            transition(
                self.pm,
                project.pk,
                Project.Stage.EXECUTION,
                project.version,
                "启动项目执行",
            )

    def test_execution_transition_requires_a_milestone(self):
        Project.objects.filter(pk=self.project.pk).update(
            budget_approved=True, contract_reference="P-001-CONTRACT"
        )
        # Historical unlinked task row: the transition still requires a
        # verifiable milestone even when old task data exists.
        Task.objects.create(
            project=self.project,
            name="完成首版交付",
            owner=self.pm,
            status=Task.Status.TODO,
            start_date=self.day_one,
            due_date=self.day_two,
            estimate_minutes=480,
            description="",
            completion_note="",
        )
        project = Project.objects.get(pk=self.project.pk)

        with self.assertRaisesMessage(ValidationError, "至少配置一项任务和一个里程碑"):
            transition(
                self.pm,
                project.pk,
                Project.Stage.EXECUTION,
                project.version,
                "启动项目执行",
            )

    def test_audit_chain_is_consistent_and_linked(self):
        self.submitted_and_approved_time()
        submit_time(
            self.pm,
            self.time_data(work_date=self.day_two, note="第二条工时"),
            uuid.uuid4(),
        )
        events = list(AuditEvent.objects.filter(organization=self.org).order_by("id"))

        self.assertGreaterEqual(len(events), 3)
        self.assertTrue(verify_audit(self.org))
        self.assertEqual(events[0].previous_hash, "")
        self.assertEqual(events[1].previous_hash, events[0].event_hash)
        self.assertEqual(events[2].previous_hash, events[1].event_hash)

    def test_audit_verification_detects_in_memory_payload_tampering(self):
        self.submitted_and_approved_time()
        events = [
            copy.copy(event)
            for event in AuditEvent.objects.filter(organization=self.org).order_by("id")
        ]
        events[0].payload = {**events[0].payload, "amount_cents": 999_999_999}

        queryset = mock.MagicMock()
        queryset.order_by.return_value.iterator.return_value = iter(events)
        with mock.patch.object(AuditEvent.objects, "filter", return_value=queryset):
            self.assertFalse(verify_audit(self.org))

    def test_append_only_audit_event_rejects_database_update(self):
        event = submit_time(self.pm, self.time_data(), uuid.uuid4())
        audit_event = AuditEvent.objects.get(
            entity="Timesheet", entity_id=str(event.pk)
        )
        original_payload = audit_event.payload

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                AuditEvent.objects.filter(pk=audit_event.pk).update(
                    payload={"tampered": True}
                )

        audit_event.refresh_from_db()
        self.assertEqual(audit_event.payload, original_payload)

    def test_append_only_ledger_entry_rejects_database_delete(self):
        timesheet = self.submitted_and_approved_time()
        ledger_entry = LedgerEntry.objects.get(timesheet=timesheet, book="cost")

        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                LedgerEntry.objects.filter(pk=ledger_entry.pk).delete()

        self.assertTrue(LedgerEntry.objects.filter(pk=ledger_entry.pk).exists())

