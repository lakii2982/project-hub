"""Q-51 客户/项目/合同 CSV 批量导入：模板、逐行校验、原子提交、幂等与权限。"""
import csv
import io
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.contract_changes import contract_amount_cents
from core.imports import (HEADERS, commit_rows, import_token, parse_csv,
                          project_import_errors)
from core.models import (Membership, Organization, Project, ProjectContractChange)


class ProjectImportTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="批量导入测试公司")
        cls.other_org = Organization.objects.create(name="批量导入隔离公司")
        users = get_user_model()
        cls.owner_user = users.objects.create_user("q51-owner", password="test-pass")
        cls.pm_user = users.objects.create_user("q51-pm", password="test-pass")
        cls.member_user = users.objects.create_user("q51-member", password="test-pass")
        cls.auditor_user = users.objects.create_user("q51-auditor", password="test-pass")
        cls.finance_user = users.objects.create_user("q51-finance", password="test-pass")
        cls.foreign_user = users.objects.create_user("q51-foreign", password="test-pass")
        cls.owner = Membership.objects.create(
            organization=cls.org, user=cls.owner_user, role=Membership.Role.OWNER)
        cls.pm = Membership.objects.create(
            organization=cls.org, user=cls.pm_user, role=Membership.Role.PM)
        cls.member = Membership.objects.create(
            organization=cls.org, user=cls.member_user, role=Membership.Role.MEMBER)
        cls.auditor = Membership.objects.create(
            organization=cls.org, user=cls.auditor_user, role=Membership.Role.AUDITOR)
        cls.finance = Membership.objects.create(
            organization=cls.org, user=cls.finance_user, role=Membership.Role.FINANCE)
        cls.foreign_owner = Membership.objects.create(
            organization=cls.other_org, user=cls.foreign_user, role=Membership.Role.OWNER)
        cls.today = timezone.localdate()

    def setUp(self):
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)
        return member

    # ---- helpers ----------------------------------------------------------
    def row(self, source_id="Q51-1", code="Q51-001", amount="12345.67",
            client="示例客户", name="示例项目", start=None, end=None, **overrides):
        start = start or (self.today - timedelta(days=30)).isoformat()
        end = end or (self.today + timedelta(days=335)).isoformat()
        row = {"source_id": source_id, "client_name": client,
               "project_code": code, "project_name": name,
               "start_date": start, "end_date": end,
               "contract_amount": amount, "contract_reference": f"HT-{source_id}"}
        row.update(overrides)
        return row

    def csv_upload(self, rows, headers=None, kind="projects"):
        headers = headers or HEADERS[kind]
        lines = [",".join(headers)]
        for row in rows:
            lines.append(",".join(str(row.get(header, "")) for header in headers))
        return SimpleUploadedFile(
            "projects.csv", ("\n".join(lines) + "\n").encode("utf-8-sig"),
            content_type="text/csv")

    def post_preview(self, rows):
        return self.client.post(reverse("import-data", args=["projects"]),
                                {"action": "preview",
                                 "file": self.csv_upload(rows)})

    def post_commit(self):
        return self.client.post(reverse("import-data", args=["projects"]),
                                {"action": "commit"})

    def import_rows(self, rows):
        preview = self.post_preview(rows)
        self.assertEqual(preview.status_code, 200)
        commit = self.post_commit()
        self.assertEqual(commit.status_code, 302)
        return commit

    # ---- pure row validation ---------------------------------------------
    def test_parse_csv_enforces_template_headers_and_unique_source_ids(self):
        bad_headers = self.csv_upload(
            [self.row()], headers=["source_id", "client_name", "project_code"])
        with self.assertRaises(ValidationError):
            parse_csv("projects", bad_headers)
        duplicate = self.csv_upload([self.row(source_id="DUP-1"),
                                     self.row(source_id="DUP-1", code="DUP-002")])
        with self.assertRaises(ValidationError) as caught:
            parse_csv("projects", duplicate)
        self.assertIn("来源编号", str(caught.exception))

    def test_row_validation_rejects_illegal_values_with_readable_reasons(self):
        cases = [
            (self.row(amount="0"), "合同额须为大于0"),
            (self.row(amount="10000000000.01"), "100亿元"),
            (self.row(amount="1.005"), "最多两位小数"),
            (self.row(amount="abc"), "合同额"),
            (self.row(amount="-1"), "合同额"),
            (self.row(start=self.today.isoformat(), end="not-a-date"), "YYYY-MM-DD"),
            (self.row(start=(self.today + timedelta(days=1)).isoformat()),
             "开始日期不能晚于今天"),
            (self.row(start=self.today.isoformat(), end=self.today.isoformat()),
             None),
            (self.row(client=""), "客户名称"),
            (self.row(name=""), "项目名称"),
            (self.row(code="A"), "项目编号"),
        ]
        for row, expected in cases:
            with self.subTest(expected=expected, row=row):
                errors = project_import_errors(self.owner, [row])
                if expected is None:
                    self.assertEqual(errors, [])
                else:
                    self.assertEqual(len(errors), 1)
                    self.assertIn(expected, errors[0]["message"])
                    self.assertEqual(errors[0]["line"], 2)

    def test_amount_boundaries_are_exact_cents(self):
        self.assertEqual(project_import_errors(self.owner, [self.row(amount="0.01")]), [])
        self.assertEqual(
            project_import_errors(self.owner, [self.row(amount="10000000000")]), [])
        self.login(self.owner)
        self.import_rows([self.row(source_id="Q51-MIN", code="Q51-MIN", amount="0.01"),
                          self.row(source_id="Q51-MAX", code="Q51-MAX",
                                   amount="10000000000")])
        minimum = ProjectContractChange.objects.get(
            token=import_token(self.owner, "projects", "Q51-MIN"))
        maximum = ProjectContractChange.objects.get(
            token=import_token(self.owner, "projects", "Q51-MAX"))
        self.assertEqual(minimum.amount_cents, 1)
        self.assertEqual(maximum.amount_cents, 10 ** 12)

    def test_contract_amount_over_limit_is_reported_in_preview_without_writes(self):
        self.login(self.owner)
        page = self.post_preview([
            self.row(source_id="OVER-1", code="OVER-001", amount="10000000000.01"),
            self.row(source_id="OVER-2", code="OVER-002", amount="100000000000"),
        ])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "共 2 行有问题")
        self.assertContains(page, "100亿元")
        self.assertNotIn("import_projects", self.client.session)
        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(ProjectContractChange.objects.count(), 0)
        # Exactly 100亿元 (10^12 分) is inside the bound and commits atomically.
        self.post_preview([self.row(source_id="EDGE-1", code="EDGE-001",
                                    amount="10000000000")])
        self.assertEqual(self.post_commit().status_code, 302)
        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(ProjectContractChange.objects.get(
            token=import_token(self.owner, "projects", "EDGE-1")
        ).amount_cents, 10 ** 12)

    # ---- HTTP preview: every bad row is listed, nothing is written --------
    def test_preview_lists_all_error_rows_and_blocks_commit(self):
        Project.objects.create(
            organization=self.org, code="Q51-TAKEN", name="已存在项目", client="老客户",
            manager=self.owner, start_date=self.today - timedelta(days=10),
            end_date=self.today + timedelta(days=10))
        rows = [
            self.row(source_id="E1", code="E-001"),
            self.row(source_id="E2", code="E-001"),
            self.row(source_id="E3", code="E-003", start="2026/01/01"),
            self.row(source_id="E4", code="E-004", client=""),
            self.row(source_id="E5", code="E-005", amount="0"),
            self.row(source_id="E6", code="E-006", amount="1.005"),
            self.row(source_id="E7", code="E-007",
                     start=(self.today + timedelta(days=1)).isoformat()),
            self.row(source_id="E8", code="E-008", end="2000-01-01"),
            self.row(source_id="E9", code="Q51-TAKEN"),
        ]
        self.login(self.owner)
        page = self.post_preview(rows)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "校验未通过，整批未写入")
        self.assertContains(page, "共 8 行有问题")
        self.assertContains(page, "第 4 行")
        self.assertContains(page, "项目编号与第2行重复")
        self.assertContains(page, "开始日期须为YYYY-MM-DD")
        self.assertContains(page, "客户名称须为1至120个字符")
        self.assertContains(page, "合同额须为大于0")
        self.assertContains(page, "最多两位小数")
        self.assertContains(page, "开始日期不能晚于今天")
        self.assertContains(page, "计划交付日期不能早于开始日期")
        self.assertContains(page, "项目编号已存在")
        self.assertNotContains(page, 'name="action" value="commit"')
        self.assertNotIn("import_projects", self.client.session)
        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(ProjectContractChange.objects.count(), 0)
        # A commit without a stored preview cannot write anything.
        commit = self.post_commit()
        self.assertEqual(commit.status_code, 200)
        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(ProjectContractChange.objects.count(), 0)

    def test_commit_rows_rolls_back_the_whole_batch_on_a_bad_row(self):
        rows = [self.row(source_id="A-1", code="A-001"),
                self.row(source_id="A-2", code="A-002", amount="not-money")]
        with self.assertRaises(ValidationError):
            commit_rows(self.owner, "projects", rows)
        self.assertEqual(Project.objects.count(), 0)
        self.assertEqual(ProjectContractChange.objects.count(), 0)

    # ---- successful import: projects + append-only contracts --------------
    def test_success_creates_projects_and_appends_contract_amounts(self):
        self.login(self.owner)
        rows = [self.row(source_id="OK-1", code="OK-001", client="甲客户",
                         name="甲项目", amount="128000.50"),
                self.row(source_id="OK-2", code="OK-002", client="乙客户",
                         name="乙项目", amount="560000", reference="HT-乙")]
        preview = self.post_preview(rows)
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, "确认导入 2 行")
        self.assertEqual(Project.objects.count(), 0)
        commit = self.post_commit()
        self.assertEqual(commit.status_code, 302)
        self.assertEqual(commit.url, reverse("projects"))
        self.assertEqual(Project.objects.count(), 2)
        listing = self.client.get(reverse("projects"))
        self.assertContains(listing, "已导入2行客户/项目/合同")
        first = Project.objects.get(code="OK-001")
        self.assertEqual(first.client, "甲客户")
        self.assertEqual(first.name, "甲项目")
        self.assertEqual(first.manager_id, self.owner.pk)
        self.assertEqual(first.template, Project.Template.SOFTWARE)
        self.assertEqual(first.stage, Project.Stage.INITIATION)
        self.assertEqual(first.contract_cents, 0)  # append-only change carries the amount
        self.assertEqual(first.budget_cents, 0)
        self.assertEqual(contract_amount_cents(first), 12800050)
        change = ProjectContractChange.objects.get(project=first)
        self.assertEqual(change.previous_amount_cents, 0)
        self.assertEqual(change.amount_cents, 12800050)
        self.assertEqual(change.token, import_token(self.owner, "projects", "OK-1"))
        self.assertEqual(change.effective_on, first.start_date)
        self.assertIn("OK-1", change.note)
        self.assertEqual(contract_amount_cents(Project.objects.get(code="OK-002")), 56000000)
        self.assertEqual(ProjectContractChange.objects.count(), 2)

    def test_reuploading_the_same_source_ids_is_idempotent(self):
        self.login(self.owner)
        rows = [self.row(source_id="IDEM-1", code="IDEM-001"),
                self.row(source_id="IDEM-2", code="IDEM-002")]
        self.import_rows(rows)
        self.assertEqual(Project.objects.count(), 2)
        self.assertEqual(ProjectContractChange.objects.count(), 2)
        page = self.post_preview(rows)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "校验未通过")
        self.assertContains(page, "确认导入 2 行")
        commit = self.post_commit()
        self.assertEqual(commit.status_code, 302)
        self.assertEqual(Project.objects.count(), 2)
        self.assertEqual(ProjectContractChange.objects.count(), 2)

    def test_reusing_a_source_id_with_changed_content_is_rejected(self):
        self.login(self.owner)
        self.import_rows([self.row(source_id="MUT-1", code="MUT-001")])
        page = self.post_preview(
            [self.row(source_id="MUT-1", code="MUT-001", amount="999.00")])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "同一来源编号的导入内容已改变")
        self.assertNotIn("import_projects", self.client.session)
        self.assertEqual(Project.objects.count(), 1)
        self.assertEqual(ProjectContractChange.objects.count(), 1)

    # ---- permissions and tenant scope ------------------------------------
    def test_only_owner_and_pm_can_import_projects(self):
        for member in (self.member, self.auditor, self.finance):
            with self.subTest(role=member.role):
                self.login(member)
                response = self.client.get(
                    reverse("import-data", args=["projects"]))
                self.assertEqual(response.status_code, 403)
                template = self.client.get(
                    reverse("import-template", args=["projects"]))
                self.assertEqual(template.status_code, 403)
                self.post_preview([self.row()])
                self.assertEqual(Project.objects.count(), 0)
        self.login(self.owner)
        self.assertEqual(self.client.get(
            reverse("import-data", args=["projects"])).status_code, 200)
        self.login(self.pm)
        self.assertEqual(self.post_preview([self.row()]).status_code, 200)
        self.post_commit()
        self.assertEqual(Project.objects.get(code="Q51-001").manager_id, self.pm.pk)

    def test_anonymous_users_are_redirected_to_login(self):
        response = self.client.get(reverse("import-data", args=["projects"]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_source_ids_are_scoped_per_tenant(self):
        self.login(self.owner)
        self.import_rows([self.row(source_id="TENANT-1", code="TENANT-001")])
        self.client.logout()
        self.login(self.foreign_owner)
        self.import_rows([self.row(source_id="TENANT-1", code="TENANT-001",
                                   client="另一公司客户")])
        self.assertEqual(Project.objects.filter(organization=self.org).count(), 1)
        self.assertEqual(
            Project.objects.filter(organization=self.other_org).count(), 1)
        self.assertEqual(ProjectContractChange.objects.count(), 2)

    # ---- template download ------------------------------------------------
    def test_template_download_has_utf8_bom_headers_and_importable_example(self):
        self.login(self.owner)
        response = self.client.get(
            reverse("import-template", args=["projects"]))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/csv"))
        self.assertIn("attachment", response["Content-Disposition"])
        body = response.content
        self.assertTrue(body.startswith(b"\xef\xbb\xbf"))
        reader = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
        self.assertEqual(reader[0], HEADERS["projects"])
        self.assertEqual(len(reader), 2)
        example = dict(zip(reader[0], reader[1]))
        self.assertEqual(project_import_errors(self.owner, [example]), [])
        # Other kinds keep a downloadable template too.
        time_template = self.client.get(reverse("import-template", args=["time"]))
        self.assertEqual(time_template.status_code, 200)
        self.assertEqual(
            list(csv.reader(io.StringIO(
                time_template.content.decode("utf-8-sig"))))[0], HEADERS["time"])
        self.assertEqual(self.client.get("/import/nope/template.csv").status_code, 404)

    @override_settings(PMO_SCOPE="micro")
    def test_micro_scope_shows_projects_entry_and_import_page(self):
        self.login(self.owner)
        page = self.client.get(reverse("projects"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, reverse("import-data", args=["projects"]))
        self.assertContains(page, "批量导入")
        self.assertEqual(self.client.get(
            reverse("import-data", args=["projects"])).status_code, 200)
        self.assertEqual(self.client.get(
            reverse("import-template", args=["projects"])).status_code, 200)
        self.login(self.member)
        member_page = self.client.get(reverse("projects"))
        self.assertNotContains(member_page, reverse("import-data", args=["projects"]))
        # Invoicing import stays out of the micro scope.
        self.login(self.owner)
        self.assertEqual(
            self.client.get("/import/invoicing/template.csv").status_code, 403)
