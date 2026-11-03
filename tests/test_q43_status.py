"""Q-43 read-only system status page: health, migrations, backup visibility, no secrets."""
import hashlib
import json
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Membership, Organization


def digest(value):
    return hashlib.sha256(value).hexdigest()


class StatusPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Q43 系统状态公司")
        users = get_user_model()
        cls.owner_user = users.objects.create_user("q43-owner", password="not-a-real-secret")
        cls.member_user = users.objects.create_user("q43-member", password="not-a-real-secret")
        cls.auditor_user = users.objects.create_user("q43-auditor", password="not-a-real-secret")
        cls.owner = Membership.objects.create(organization=cls.org, user=cls.owner_user, role="owner")
        cls.member = Membership.objects.create(organization=cls.org, user=cls.member_user, role="member")
        cls.auditor = Membership.objects.create(organization=cls.org, user=cls.auditor_user, role="auditor")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="q43-status-")
        self.addCleanup(self.temp.cleanup)
        self.backup_root = Path(self.temp.name)

    def write_backup(self, *, valid=True, with_files=True):
        backup = self.backup_root / "2026-11-04-120000"
        backup.mkdir()
        database = backup / "database.sqlite3"
        uploads = backup / "uploads.tar.gz"
        database.write_bytes(b"sqlite snapshot")
        uploads.write_bytes(b"uploads archive")
        manifest = {
            "format_version": 1,
            "database": {"filename": "database.sqlite3", "sha256": digest(database.read_bytes())},
            "uploads": {"filename": "uploads.tar.gz", "sha256": digest(uploads.read_bytes())},
            "evidence": [{"path": "q43/a.txt", "sha256": "a" * 64}],
        }
        if not valid:
            manifest["format_version"] = 99
        if not with_files:
            database.unlink()
        (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return backup

    def get(self, user, **kwargs):
        self.client.force_login(user)
        return self.client.get(reverse("system-status"), **kwargs)

    def test_owner_sees_read_only_status_without_secrets_or_paths(self):
        self.write_backup()
        with override_settings(PMO_BACKUP_DIR=str(self.backup_root)):
            response = self.get(self.owner_user)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("系统状态", body)
        self.assertIn("READ ONLY", body)
        self.assertIn("数据库", body)
        self.assertIn("最近备份", body)
        self.assertIn("清单与文件存在", body)
        self.assertNotIn(str(self.backup_root), body)
        self.assertNotIn(self.owner_user.password, body)
        from django.conf import settings
        self.assertNotIn(settings.SECRET_KEY, body)
        self.assertNotIn("POSTGRES_PASSWORD", body)
        self.assertNotIn("password=", body)

    def test_missing_backup_is_not_reported_as_success(self):
        with override_settings(PMO_BACKUP_DIR=str(self.backup_root / "missing")):
            response = self.get(self.owner_user)
        body = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("尚未发现本地备份", body)
        self.assertIn("这不是备份通过", body)
        self.assertNotIn("清单与文件存在", body)

    def test_incomplete_backup_is_flagged_not_passed(self):
        self.write_backup(valid=False, with_files=False)
        with override_settings(PMO_BACKUP_DIR=str(self.backup_root)):
            response = self.get(self.owner_user)
        body = response.content.decode()
        self.assertIn("备份不完整/不可信", body)
        self.assertNotIn("清单与文件存在", body)

    def test_readonly_members_can_view_but_post_is_rejected(self):
        for user in (self.member_user, self.auditor_user):
            response = self.get(user)
            self.assertEqual(response.status_code, 200)
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse("system-status")).status_code, 405)

    def test_anonymous_is_redirected_and_settings_links_status(self):
        response = self.client.get(reverse("system-status"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])
        self.client.force_login(self.owner_user)
        settings_page = self.client.get(reverse("settings"))
        self.assertContains(settings_page, reverse("system-status"))

    def test_database_status_reports_zero_pending_on_migrated_db(self):
        from core.system_status import database_status
        status = database_status()
        self.assertTrue(status["reachable"])
        self.assertEqual(status["pending_migrations"], 0)
        from django.conf import settings
        expected = str(settings.DATABASES["default"]["ENGINE"]).rsplit(".", 1)[-1]
        self.assertEqual(status["engine"], expected)

    def test_evidence_count_is_workspace_scoped(self):
        from datetime import date
        from core.models import Document, Evidence, Project
        from core.system_status import evidence_summary

        def add(organization, code):
            project = Project.objects.create(
                organization=organization, manager=self.owner, code=code, name=code,
                client="演练客户", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
            document = Document.objects.create(
                project=project, kind="expense", title=code, amount_cents=100,
                occurred_on=date(2026, 1, 1), reference=code, submitted_by=self.owner_user)
            Evidence.objects.create(document=document, file=f"q43/{code}.txt",
                                     original_name=f"{code}.txt", sha256="a" * 64,
                                     uploaded_by=self.owner_user)
            return project

        add(self.org, "Q43-OWN")
        other = Organization.objects.create(name="Q43 其他公司")
        add(other, "Q43-OTHER")
        self.assertEqual(evidence_summary(self.owner)["count"], 1)
        self.assertEqual(Evidence.objects.count(), 2)

    def test_evidence_missing_files_are_counted(self):
        from core.models import Document, Evidence
        from datetime import date
        project = __import__("core.models", fromlist=["Project"]).Project.objects.create(
            organization=self.org, manager=self.owner, code="Q43-001", name="附件项目",
            client="演练客户", start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
        document = Document.objects.create(project=project, kind="expense", title="附件费用",
                                           amount_cents=100, occurred_on=date(2026, 1, 1),
                                           reference="Q43-EXP", submitted_by=self.owner_user)
        Evidence.objects.create(document=document, file="q43/missing.txt",
                                 original_name="missing.txt", sha256="a" * 64,
                                 uploaded_by=self.owner_user)
        with override_settings(PMO_BACKUP_DIR=str(self.backup_root)):
            response = self.get(self.owner_user)
        body = response.content.decode()
        self.assertIn("缺失文件", body)
        self.assertIn("1", body)


