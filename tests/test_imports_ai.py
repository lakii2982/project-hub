from datetime import date, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from core.ai import SCHEMA_VERSION, extract_draft, validate_draft
from core.imports import HEADERS, commit_rows, parse_csv, preview_rows
from core.models import (
    AuditEvent,
    CostRate,
    Employee,
    Membership,
    Organization,
    Project,
    ProjectMember,
    Timesheet,
)


class ImportAndAITestCase(TestCase):
    """Exercise CSV batch safety and source-checked AI draft validation."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="导入测试公司")
        user_model = get_user_model()
        cls.owner_user = user_model.objects.create_user("import-owner", password="test-pass")
        cls.pm_user = user_model.objects.create_user("import-pm", password="test-pass")
        cls.member_user = user_model.objects.create_user("import-member", password="test-pass")
        cls.owner = Membership.objects.create(
            organization=cls.org, user=cls.owner_user, role=Membership.Role.OWNER
        )
        cls.pm = Membership.objects.create(
            organization=cls.org, user=cls.pm_user, role=Membership.Role.PM
        )
        cls.member = Membership.objects.create(
            organization=cls.org, user=cls.member_user, role=Membership.Role.MEMBER
        )
        cls.employee = Employee.objects.create(
            organization=cls.org,
            name="导入员工",
            member=cls.member,
            active=True,
        )
        CostRate.objects.create(
            employee=cls.employee,
            effective_from=date(2024, 1, 1),
            hourly_cents=12_000,
            basis="导入测试费率",
        )
        cls.project = Project.objects.create(
            organization=cls.org,
            code="IMP-001",
            name="导入测试项目",
            client="测试客户",
            manager=cls.pm,
            stage=Project.Stage.PLANNING,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            scope="导入测试范围",
            acceptance_criteria="导入结果可核验",
            contract_reference="IMP-CONTRACT",
        )
        ProjectMember.objects.create(project=cls.project, member=cls.pm)
        ProjectMember.objects.create(project=cls.project, member=cls.member)

    def csv_upload(self, kind, rows, headers=None, encoding="utf-8-sig"):
        headers = headers or HEADERS[kind]
        lines = [",".join(headers)]
        for row in rows:
            lines.append(",".join(str(row.get(header, "")) for header in headers))
        return SimpleUploadedFile(
            "import.csv",
            ("\n".join(lines) + "\n").encode(encoding),
            content_type="text/csv",
        )

    def time_row(self, source_id="TIME-001", hours="2", employee_name=None):
        return {
            "source_id": source_id,
            "project_code": self.project.code,
            "employee_name": employee_name or self.employee.name,
            "work_date": "2025-01-10",
            "hours": hours,
            "note": "批量导入工时",
        }

    def expense_row(self, source_id="EXP-001", amount="100.00"):
        return {
            "source_id": source_id,
            "project_code": self.project.code,
            "title": "批量导入费用",
            "amount": amount,
            "tax": "0",
            "recoverable_tax": "0",
            "occurred_on": "2025-01-10",
            "counterparty": "测试供应商",
            "reference": f"REF-{source_id}",
            "note": "批量导入费用",
        }

    def plan_row(self, source_id="PLAN-001", amount="500.00"):
        return {
            "source_id": source_id,
            "project_code": self.project.code,
            "direction": "in",
            "title": "批量导入收款计划",
            "amount": amount,
            "due_date": "2025-02-10",
            "counterparty": "测试客户",
            "reference": f"REF-{source_id}",
        }

    def valid_draft(self):
        text = "项目 IMP-001 于 2025-01-10 发生费用 123.45 元，供应商测试，凭证 REF-1"
        return {
            "schema_version": SCHEMA_VERSION,
            "project_code": "IMP-001",
            "kind": "expense",
            "title": "测试费用",
            "amount": "123.45",
            "occurred_on": "2025-01-10",
            "counterparty": "测试供应商",
            "reference": "REF-1",
            "evidence": {
                "amount": "123.45",
                "project_code": "IMP-001",
                "occurred_on": "2025-01-10",
            },
            "missing_fields": [],
        }, text

    def test_csv_preview_rolls_back_business_and_audit_records(self):
        count = preview_rows(self.pm, "time", [self.time_row()])

        self.assertEqual(count, 1)
        self.assertFalse(Timesheet.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(organization=self.org).exists())

    def test_csv_commit_is_idempotent_for_same_source_id(self):
        row = self.time_row()
        first = commit_rows(self.pm, "time", [row])
        second = commit_rows(self.pm, "time", [row])

        self.assertEqual([obj.pk for obj in second], [obj.pk for obj in first])
        self.assertEqual(Timesheet.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action="time.submitted").count(), 1)

    def test_csv_commit_rolls_back_entire_batch_on_any_row_error(self):
        rows = [
            self.time_row(source_id="TIME-OK"),
            self.time_row(source_id="TIME-BAD", employee_name="不存在的员工"),
        ]

        with self.assertRaises(ValidationError):
            commit_rows(self.pm, "time", rows)

        self.assertFalse(Timesheet.objects.exists())
        self.assertFalse(AuditEvent.objects.filter(organization=self.org).exists())

    def test_csv_commit_conflicts_when_same_source_id_changes_hours(self):
        commit_rows(self.pm, "time", [self.time_row(hours="2")])

        with self.assertRaisesMessage(ValidationError, "内容已改变"):
            commit_rows(self.pm, "time", [self.time_row(hours="3")])

    def test_csv_commit_conflicts_when_same_source_id_changes_amount(self):
        commit_rows(self.pm, "expense", [self.expense_row(amount="100.00")])

        with self.assertRaisesMessage(ValidationError, "内容已改变"):
            commit_rows(self.pm, "expense", [self.expense_row(amount="200.00")])

    def test_plan_import_conflicts_when_same_source_id_changes_amount(self):
        commit_rows(self.pm, "plan", [self.plan_row(amount="500.00")])

        with self.assertRaisesMessage(ValidationError, "内容已改变"):
            commit_rows(self.pm, "plan", [self.plan_row(amount="600.00")])

    def test_csv_parse_accepts_utf8_bom_and_chinese_values(self):
        uploaded = self.csv_upload("time", [self.time_row()])

        rows = parse_csv("time", uploaded)

        self.assertEqual(rows[0]["employee_name"], self.employee.name)
        self.assertEqual(rows[0]["note"], "批量导入工时")

    def test_csv_parse_rejects_extra_columns(self):
        headers = HEADERS["time"] + ["unexpected"]
        row = {**self.time_row(), "unexpected": "x"}

        with self.assertRaisesMessage(ValidationError, "CSV列必须为"):
            parse_csv("time", self.csv_upload("time", [row], headers=headers))

    def test_csv_parse_rejects_duplicate_source_ids(self):
        rows = [
            self.time_row(source_id="TIME-DUP"),
            self.time_row(source_id="TIME-DUP", hours="3"),
        ]

        with self.assertRaisesMessage(ValidationError, "重复"):
            parse_csv("time", self.csv_upload("time", rows))

    def test_validate_draft_rejects_extra_fields(self):
        data, text = self.valid_draft()
        data["unexpected"] = "not allowed"

        with self.assertRaises(ValueError):
            validate_draft(data, text, {"IMP-001"})

    def test_validate_draft_rejects_forged_amount_evidence(self):
        data, text = self.valid_draft()
        data["amount"] = "999.99"

        with self.assertRaises(ValueError):
            validate_draft(data, text, {"IMP-001"})

    def test_validate_draft_rejects_invalid_project_code(self):
        data, text = self.valid_draft()
        data["project_code"] = "IMP-999"
        data["evidence"]["project_code"] = "IMP-999"
        text = text.replace("IMP-001", "IMP-999")

        with self.assertRaises(ValueError):
            validate_draft(data, text, {"IMP-001"})

    def test_validate_draft_rejects_future_date(self):
        future = (timezone.localdate() + timedelta(days=1)).isoformat()
        text = f"项目 IMP-001 于 {future} 发生费用 123.45 元"
        data, _ = self.valid_draft()
        data["occurred_on"] = future
        data["evidence"]["occurred_on"] = future

        with self.assertRaises(ValueError):
            validate_draft(data, text, {"IMP-001"})

    def test_validate_draft_rejects_nan_amount(self):
        data, text = self.valid_draft()
        data["amount"] = "NaN"
        data["evidence"]["amount"] = "NaN"
        text = text.replace("123.45", "NaN")

        with self.assertRaises(ValueError):
            validate_draft(data, text, {"IMP-001"})

    def test_validate_draft_rejects_missing_evidence(self):
        data, text = self.valid_draft()
        data["evidence"] = {}

        with self.assertRaises(ValueError):
            validate_draft(data, text, {"IMP-001"})

    @override_settings(AI_ENABLED=False)
    def test_disabled_external_model_does_not_call_network(self):
        data, text = self.valid_draft()

        with mock.patch("core.ai.urllib.request.urlopen") as urlopen:
            with self.assertRaises(ValidationError):
                extract_draft(self.owner, text, [self.project])

        urlopen.assert_not_called()
