import csv
import io
import uuid
from datetime import date
from unittest import mock

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import Client, TestCase
from django.urls import reverse

from core.models import (
    CostRate,
    Document,
    Employee,
    Evidence,
    LedgerEntry,
    Membership,
    Organization,
    Project,
    ProjectMember,
    Task,
    Timesheet,
)
from core.services import review, submit_time


class HTTPAccessTestCase(TestCase):
    """Exercise real URL routing, permissions, CSRF, and export safety."""

    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="HTTP测试公司")
        user_model = get_user_model()
        cls.owner_user = user_model.objects.create_user("http-owner", password="test-pass")
        cls.finance_user = user_model.objects.create_user("http-finance", password="test-pass")
        cls.pm_user = user_model.objects.create_user("http-pm", password="test-pass")
        cls.member_user = user_model.objects.create_user("http-member", password="test-pass")
        cls.auditor_user = user_model.objects.create_user(
            "http-auditor", password="test-pass"
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
        cls.auditor = Membership.objects.create(
            organization=cls.org, user=cls.auditor_user, role=Membership.Role.AUDITOR
        )
        cls.private_rate_basis = "PRIVATE_RATE_BASIS_HTTP_98765"
        cls.private_rate_hourly_cents = 98_765
        cls.employee = Employee.objects.create(
            organization=cls.org,
            name="HTTP员工",
            member=cls.member,
        )
        CostRate.objects.create(
            employee=cls.employee,
            effective_from=date(2024, 1, 1),
            hourly_cents=cls.private_rate_hourly_cents,
            basis=cls.private_rate_basis,
        )
        cls.project = Project.objects.create(
            organization=cls.org,
            code="HTTP-001",
            name="HTTP测试项目",
            client="测试客户",
            manager=cls.pm,
            stage=Project.Stage.PLANNING,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            scope="HTTP测试范围",
            acceptance_criteria="HTTP测试验收",
            contract_reference="HTTP-CONTRACT",
        )
        ProjectMember.objects.create(project=cls.project, member=cls.pm)
        ProjectMember.objects.create(project=cls.project, member=cls.member)
        cls.timesheet = submit_time(
            cls.member,
            {
                "project": cls.project,
                "employee": cls.employee,
                "task": None,
                "work_date": date(2025, 1, 10),
                "minutes": 120,
                "note": "HTTP审批测试",
            },
            uuid.uuid4(),
        )
        cls.approved_timesheet = submit_time(
            cls.member,
            {
                "project": cls.project,
                "employee": cls.employee,
                "task": None,
                "work_date": date(2025, 1, 11),
                "minutes": 60,
                "note": "已入账人工成本测试",
            },
            uuid.uuid4(),
        )
        review(
            cls.finance,
            "time",
            cls.approved_timesheet.pk,
            "approve",
        )
        cls.task = Task.objects.create(
            project=cls.project,
            name="HTTP任务",
            owner=cls.pm,
            status=Task.Status.TODO,
            start_date=date(2025, 1, 1),
            due_date=date(2025, 1, 20),
        )
        cls.document = Document.objects.create(
            project=cls.project,
            kind=Document.Kind.EXPENSE,
            title="凭证访问测试",
            category="other",
            amount_cents=100_000,
            tax_cents=0,
            recoverable_tax=False,
            occurred_on=date(2025, 1, 10),
            counterparty="测试供应商",
            reference="HTTP-EVIDENCE-1",
            status=Timesheet.Status.APPROVED,
            submitted_by=cls.owner_user,
        )
        cls.evidence = Evidence.objects.create(
            document=cls.document,
            file="evidence/http-secret.txt",
            original_name="secret.txt",
            sha256="0" * 64,
            uploaded_by=cls.owner_user,
        )
        cls.business_entry = LedgerEntry.objects.create(
            project=cls.project,
            book=LedgerEntry.Book.COST,
            amount_cents=cls.document.amount_cents,
            category="software",
            occurred_on=cls.document.occurred_on,
            document=cls.document,
            posting_key="http-business-cost",
        )
        cls.labor_entry = LedgerEntry.objects.get(
            project=cls.project,
            timesheet=cls.approved_timesheet,
            book=LedgerEntry.Book.COST,
        )

        cls.other_org = Organization.objects.create(name="HTTP其他公司")
        other_pm_user = user_model.objects.create_user("http-other-pm", password="test-pass")
        cls.other_pm = Membership.objects.create(
            organization=cls.other_org,
            user=other_pm_user,
            role=Membership.Role.PM,
        )
        cls.other_project = Project.objects.create(
            organization=cls.other_org,
            code="OTHER-HTTP-001",
            name="其他公司项目",
            client="其他客户",
            manager=cls.other_pm,
            stage=Project.Stage.PLANNING,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            scope="不可访问",
            acceptance_criteria="不可访问",
            contract_reference="OTHER-HTTP-CONTRACT",
        )
        ProjectMember.objects.create(project=cls.other_project, member=cls.other_pm)

        cls.formula_project = Project.objects.create(
            organization=cls.org,
            code="=EVIL",
            name='=HYPERLINK("https://example.invalid")',
            client="公式测试",
            manager=cls.pm,
            stage=Project.Stage.PLANNING,
            start_date=date(2025, 1, 1),
            end_date=date(2025, 12, 31),
            scope="公式导出测试",
            acceptance_criteria="公式必须中和",
            contract_reference="FORMULA-CONTRACT",
        )
        formula_document = Document.objects.create(
            project=cls.formula_project,
            kind=Document.Kind.EXPENSE,
            title="公式账目",
            category="+SUM(1,1)",
            amount_cents=100,
            tax_cents=0,
            recoverable_tax=False,
            occurred_on=date(2025, 1, 10),
            reference="FORMULA-1",
            status=Timesheet.Status.APPROVED,
            submitted_by=cls.owner_user,
        )
        LedgerEntry.objects.create(
            project=cls.formula_project,
            book=LedgerEntry.Book.COST,
            amount_cents=100,
            category="+SUM(1,1)",
            occurred_on=date(2025, 1, 10),
            document=formula_document,
            posting_key="formula-export-test",
        )

    def setUp(self):
        self.client.raise_request_exception = False

    def mocked_render(self):
        return mock.patch(
            "core.views.render",
            side_effect=lambda request, template, context=None, **kwargs: HttpResponse(
                status=200
            ),
        )

    def test_anonymous_user_cannot_access_workspace_or_evidence(self):
        dashboard = self.client.get(reverse("dashboard"))
        evidence = self.client.get(
            reverse("download-evidence", args=[self.evidence.pk])
        )

        self.assertEqual(dashboard.status_code, 302)
        self.assertIn("/login/", dashboard.url)
        self.assertEqual(evidence.status_code, 302)
        self.assertIn("/login/", evidence.url)

    def test_member_cannot_read_finance_settings_or_audit(self):
        self.client.force_login(self.member_user)
        routes = [
            reverse("finance"),
            reverse("settings"),
            reverse("audit"),
            reverse("approvals"),
        ]

        with self.mocked_render():
            responses = [self.client.get(route) for route in routes]

        self.assertEqual([response.status_code for response in responses], [403] * 4)

    def test_thirteen_main_pages_render_with_real_templates(self):
        self.client.force_login(self.owner_user)
        routes = [
            reverse("health"),
            reverse("dashboard"),
            reverse("projects"),
            reverse("approvals"),
            reverse("finance"),
            reverse("timesheets"),
            reverse("assistant"),
            reverse("settings"),
            reverse("audit"),
            reverse("import-data", args=["time"]),
            reverse("import-data", args=["expense"]),
            reverse("import-data", args=["plan"]),
            reverse("new-object", args=["project"]),
        ]

        responses = [self.client.get(route) for route in routes]

        self.assertEqual(len(responses), 13)
        self.assertEqual(
            [(route, response.status_code) for route, response in zip(routes, responses)],
            [(route, 200) for route in routes],
        )

    def test_pm_and_auditor_time_views_hide_personal_rate_details(self):
        hidden_labels = ("小时费率 / 依据", "成本费率", "费率依据")

        for user in (self.pm_user, self.auditor_user):
            self.client.force_login(user)
            response = self.client.get(reverse("timesheets"))

            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, self.private_rate_basis)
            for label in hidden_labels:
                self.assertNotContains(response, label)

        self.client.force_login(self.pm_user)
        approvals = self.client.get(reverse("approvals"))
        self.assertEqual(approvals.status_code, 200)
        self.assertNotContains(approvals, self.private_rate_basis)
        for label in hidden_labels:
            self.assertNotContains(approvals, label)

        self.client.force_login(self.auditor_user)
        self.assertEqual(self.client.get(reverse("approvals")).status_code, 403)

    def test_time_export_columns_depend_on_finance_role(self):
        restricted_headers = ("成本_分", "费率_分每小时")

        for user in (self.pm_user, self.auditor_user):
            self.client.force_login(user)
            response = self.client.get(reverse("export-data", args=["time"]))
            text = response.content.decode("utf-8-sig")
            header = next(csv.reader(io.StringIO(text)))

            self.assertEqual(response.status_code, 200)
            self.assertIn("日期", header)
            self.assertIn("分钟", header)
            self.assertIn("状态", header)
            self.assertNotIn("成本_分", header)
            self.assertNotIn("费率_分每小时", header)
            self.assertNotIn(self.private_rate_basis, text)

        self.client.force_login(self.owner_user)
        owner_response = self.client.get(reverse("export-data", args=["time"]))
        owner_header = next(
            csv.reader(io.StringIO(owner_response.content.decode("utf-8-sig")))
        )

        self.assertEqual(owner_response.status_code, 200)
        for restricted_header in restricted_headers:
            self.assertIn(restricted_header, owner_header)

    def test_ledger_export_is_limited_to_owner_and_finance(self):
        for user in (self.owner_user, self.finance_user):
            self.client.force_login(user)
            self.assertEqual(
                self.client.get(reverse("export-data", args=["ledger"])).status_code,
                200,
            )

        for user in (self.pm_user, self.auditor_user):
            self.client.force_login(user)
            self.assertEqual(
                self.client.get(reverse("export-data", args=["ledger"])).status_code,
                403,
            )

    def test_project_views_hide_labor_entries_but_keep_total_cost(self):
        expected_total_cost = (
            self.business_entry.amount_cents + self.labor_entry.amount_cents
        )

        for user in (self.pm_user, self.auditor_user):
            self.client.force_login(user)

            detail = self.client.get(
                reverse("project-detail", args=[self.project.pk])
            )
            detail_entry_ids = {
                entry.pk for entry in detail.context["entries"]
            }
            self.assertEqual(detail.status_code, 200)
            self.assertIn(self.business_entry.pk, detail_entry_ids)
            self.assertNotIn(self.labor_entry.pk, detail_entry_ids)
            self.assertEqual(
                detail.context["summary"]["cost_cents"],
                expected_total_cost,
            )

            finance = self.client.get(
                reverse("finance"),
                {"project": str(self.project.pk)},
            )
            finance_entry_ids = {
                entry.pk for entry in finance.context["entries"]
            }
            self.assertEqual(finance.status_code, 200)
            self.assertIn(self.business_entry.pk, finance_entry_ids)
            self.assertNotIn(self.labor_entry.pk, finance_entry_ids)
            self.assertEqual(
                finance.context["metrics"]["cost_cents"],
                expected_total_cost,
            )

    def test_member_cannot_post_review_or_lock_period(self):
        self.client.force_login(self.member_user)

        review_response = self.client.post(
            reverse("review", args=["time", self.timesheet.pk]),
            {"decision": "approve"},
        )
        settings_response = self.client.post(
            reverse("settings"),
            {"name": "不应修改", "locked_through": "2025-01-10"},
        )

        self.assertEqual(review_response.status_code, 403)
        self.assertEqual(settings_response.status_code, 403)
        self.timesheet.refresh_from_db()
        self.assertEqual(self.timesheet.status, Timesheet.Status.SUBMITTED)
        self.org.refresh_from_db()
        self.assertEqual(self.org.name, "HTTP测试公司")

    def test_cross_company_project_and_evidence_are_not_accessible(self):
        self.client.force_login(self.other_pm.user)

        project_response = self.client.get(
            reverse("project-detail", args=[self.project.pk])
        )
        evidence_response = self.client.get(
            reverse("download-evidence", args=[self.evidence.pk])
        )

        self.assertEqual(project_response.status_code, 404)
        self.assertEqual(evidence_response.status_code, 404)

    def test_member_cannot_download_another_users_evidence(self):
        self.client.force_login(self.member_user)

        response = self.client.get(
            reverse("download-evidence", args=[self.evidence.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_get_cannot_mutate_review_reverse_or_status_routes(self):
        self.client.force_login(self.owner_user)
        before = {
            "timesheet": Timesheet.objects.get(pk=self.timesheet.pk).status,
            "entries": LedgerEntry.objects.filter(timesheet=self.timesheet).count(),
            "task": Task.objects.get(pk=self.task.pk).status,
            "project_stage": Project.objects.get(pk=self.project.pk).stage,
            "project_version": Project.objects.get(pk=self.project.pk).version,
        }
        routes = [
            reverse("review", args=["time", self.timesheet.pk]),
            reverse("reverse", args=["time", self.timesheet.pk]),
            reverse("task-update", args=[self.task.pk]),
            reverse("transition", args=[self.project.pk]),
        ]

        responses = [self.client.get(route) for route in routes]

        self.assertEqual([response.status_code for response in responses], [405] * 4)
        self.assertEqual(
            Timesheet.objects.get(pk=self.timesheet.pk).status,
            before["timesheet"],
        )
        self.assertEqual(
            LedgerEntry.objects.filter(timesheet=self.timesheet).count(),
            before["entries"],
        )
        self.assertEqual(Task.objects.get(pk=self.task.pk).status, before["task"])
        project = Project.objects.get(pk=self.project.pk)
        self.assertEqual(project.stage, before["project_stage"])
        self.assertEqual(project.version, before["project_version"])

    def test_post_without_csrf_token_is_rejected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner_user)

        response = client.post(
            reverse("review", args=["time", self.timesheet.pk]),
            {"decision": "approve"},
        )

        self.assertEqual(response.status_code, 403)
        self.timesheet.refresh_from_db()
        self.assertEqual(self.timesheet.status, Timesheet.Status.SUBMITTED)

    def test_ledger_csv_neutralizes_formula_injection(self):
        self.client.force_login(self.owner_user)

        response = self.client.get(reverse("export-data", args=["ledger"]))
        content = response.content.decode("utf-8-sig")

        self.assertEqual(response.status_code, 200)
        self.assertIn("'=EVIL", content)
        self.assertIn("'=HYPERLINK", content)
        self.assertIn("'+SUM(1,1)", content)
        self.assertNotIn("\n=EVIL", content)
