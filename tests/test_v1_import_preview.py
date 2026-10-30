"""PH-274 onboarding read-only import preview: template detection, mapping and row precheck."""
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from core.models import (AccountingEvent, AuditEvent, DomainEvent, Employee, LedgerEntry,
                         LedgerPosting, Membership, PluginState, Project, SourceConsumption)
from pmo_plugins.onboarding import preview_services as previews
from tests.test_v1_contracts import Fixture


class ImportPreviewFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.finance = self.member("finance")
        self.auditor = self.member("auditor")
        self.plain = self.member("member")
        self.employee = Employee.objects.create(
            organization=self.org, name="张三", active=True)

    def time_csv(self, rows=None):
        lines = ["source_id,project_code,employee_name,work_date,hours,note"]
        for row in (rows or [("T-1", "P1", "张三", "2026-10-01", "8", "备注")]):
            lines.append(",".join(row))
        return ("\n".join(lines) + "\n").encode("utf-8")

    def expense_csv(self):
        return ("source_id,project_code,title,amount,tax,recoverable_tax,"
                "occurred_on,counterparty,reference,note\n"
                "E-1,P1,差旅费,120.50,6.00,1,2026-10-02,示例客户,INV-1,备注\n"
                ).encode("utf-8")

    def plan_csv(self):
        return ("source_id,project_code,direction,title,amount,due_date,"
                "counterparty,reference\n"
                "PL-1,P1,in,客户回款,5000.00,2026-10-03,示例客户,PO-1\n"
                ).encode("utf-8")

    def preview(self, content, *, kind=None, member=None, name="demo.csv"):
        return previews.preview_import(
            member or self.owner, kind=kind, file_name=name, content=content)

    def business_counts(self):
        return tuple(model.objects.count() for model in (
            LedgerEntry, LedgerPosting, AccountingEvent, SourceConsumption))


class ImportPreviewServiceTests(ImportPreviewFixture, TestCase):
    def test_time_template_detection_mapping_and_rows(self):
        result = self.preview(self.time_csv())
        self.assertEqual(result["template"], "time")
        self.assertEqual(result["summary"]["row_count"], 1)
        self.assertEqual(result["rows"][0]["normalized"]["minutes"], 480)
        self.assertEqual(result["rows"][0]["normalized"]["work_date"], "2026-10-01")
        self.assertEqual([item["field"] for item in result["mapped_fields"]],
                         ["source_id", "project_code", "employee_name", "work_date",
                          "hours", "note"])
        self.assertEqual(result["row_errors"], [])
        self.assertFalse(result["summary"]["persisted"])
        self.assertTrue(result["summary"]["preview_only"])
        self.assertFalse(AuditEvent.objects.filter(
            action="onboarding.import_previewed").exists() is False)

    def test_expense_and_plan_templates(self):
        expense = self.preview(self.expense_csv(), kind="expense")
        self.assertEqual(expense["template"], "expense")
        self.assertEqual(expense["rows"][0]["normalized"]["amount_cents"], 12050)
        self.assertEqual(expense["rows"][0]["normalized"]["tax_cents"], 600)
        plan = self.preview(self.plan_csv())
        self.assertEqual(plan["template"], "plan")
        self.assertEqual(plan["rows"][0]["normalized"]["direction"], "in")
        self.assertEqual(plan["rows"][0]["normalized"]["amount_cents"], 500000)

    def test_unknown_columns_are_unmapped_and_missing_required_rejected(self):
        content = ("source_id,project_code,employee_name,work_date,hours,note,extra\n"
                   "T-1,P1,张三,2026-10-01,8,备注,附加\n").encode("utf-8")
        result = self.preview(content)
        self.assertEqual(result["unmapped_columns"], ["extra"])
        with self.assertRaises(ValidationError):
            self.preview(("source_id,project_code\nT-1,P1\n").encode("utf-8"))
        with self.assertRaises(ValidationError):
            self.preview(self.time_csv(), kind="expense")

    def test_row_errors_and_conflicts_are_ephemeral(self):
        content = self.time_csv([
            ("T-1", "P1", "张三", "2026-10-01", "8", "ok"),
            ("T-1", "P1", "张三", "bad-date", "0.5", "dup"),
            ("T-2", "NOPE", "李四", "2026-10-01", "8", "bad master"),
        ])
        result = self.preview(content)
        self.assertGreaterEqual(len(result["row_errors"]), 4)
        self.assertEqual(len(result["conflicts"]), len(result["row_errors"]))
        self.assertTrue(all(item["ephemeral"] for item in result["conflicts"]))
        self.assertEqual(result["summary"]["preview_row_count"], 3)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValidationError):
            self.preview(b"")
        with self.assertRaises(ValidationError):
            self.preview(b"\xff\xfe\x00bad")
        with self.assertRaises(ValidationError):
            self.preview(b"a" * (1024 * 1024 + 1))
        with self.assertRaises(ValidationError):
            self.preview(b"source_id,project_code,employee_name,work_date,hours,note\n")
        rows = [("T-%d" % index, "P1", "张三", "2026-10-01", "8", "")
                for index in range(501)]
        with self.assertRaises(ValidationError):
            self.preview(self.time_csv(rows))

    def test_no_business_facts_written(self):
        before = self.business_counts()
        self.preview(self.time_csv())
        self.assertEqual(before, self.business_counts())
        audit = AuditEvent.objects.get(action="onboarding.import_previewed")
        self.assertEqual(audit.payload["kind"], "time")
        self.assertNotIn("张三", str(audit.payload))
        self.assertNotIn("备注", str(audit.payload))

    def test_repeat_preview_is_deterministic(self):
        first = self.preview(self.time_csv())
        second = self.preview(self.time_csv())
        self.assertEqual(first["rows"], second["rows"])
        self.assertEqual(first["mapped_fields"], second["mapped_fields"])
        self.assertEqual(first["summary"], second["summary"])

    def test_permissions_and_module_disable(self):
        self.assertEqual(self.preview(self.time_csv(), member=self.finance)["template"], "time")
        self.assertEqual(self.preview(self.time_csv(), member=self.auditor)["template"], "time")
        with self.assertRaises(PermissionDenied):
            self.preview(self.time_csv(), member=self.pm)
        with self.assertRaises(PermissionDenied):
            self.preview(self.time_csv(), member=self.plain)
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id="onboarding",
            defaults={"enabled": False, "changed_by": self.owner.user})
        with self.assertRaises(PermissionDenied):
            self.preview(self.time_csv())

    def test_template_list(self):
        rows = previews.import_template_list(self.owner)
        self.assertEqual({row["kind"] for row in rows}, {"time", "expense", "plan"})
        self.assertIn("source_id", rows[0]["columns"])
