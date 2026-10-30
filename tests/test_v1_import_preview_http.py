"""PH-274 onboarding import preview HTTP boundaries."""
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from core.models import PluginState
from tests.test_v1_import_preview import ImportPreviewFixture

LIST_URL = "/modules/onboarding/import-preview-list/"
RUN_URL = "/modules/onboarding/import-preview-run/"
UPLOAD_URL = "/modules/onboarding/import-preview-upload/"


class ImportPreviewHTTPFixture(ImportPreviewFixture):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def upload(self, content):
        return SimpleUploadedFile("demo.csv", content, content_type="text/csv")

    def post(self, content, *, member=None, kind=""):
        self.login(member or self.owner)
        return self.client.post(UPLOAD_URL, {"kind": kind, "file": self.upload(content)})

    def run_preview(self, *, member=None, content=None, kind=""):
        self.login(member or self.owner)
        params = {"kind": kind}
        if content is not None:
            params["content"] = content.decode("utf-8")
            params["file_name"] = "pasted.csv"
        return self.client.get(RUN_URL, params)


class ImportPreviewHTTPTests(ImportPreviewHTTPFixture, TestCase):
    def test_owner_uploads_and_previews_csv(self):
        response = self.post(self.time_csv())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "预览结果")
        self.assertContains(response, "识别模板")

    def test_read_page_and_auditor_readonly_preview(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(LIST_URL).status_code, 200)
        self.assertContains(self.client.get(LIST_URL), "导入预览")
        self.assertEqual(self.post(self.time_csv(), member=self.finance).status_code, 200)
        # Auditor uses the history_read GET channel (read-only preview) and cannot upload.
        response = self.run_preview(member=self.auditor, content=self.time_csv())
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "预览结果")
        self.assertEqual(self.post(self.time_csv(), member=self.auditor).status_code, 403)

    def test_pm_and_member_denied(self):
        for member in (self.pm, self.plain):
            self.login(member)
            self.assertEqual(self.client.get(LIST_URL).status_code, 403)
            self.assertEqual(self.run_preview(member=member).status_code, 403)
            self.assertEqual(self.post(self.time_csv(), member=member).status_code, 403)

    def test_invalid_input_returns_form_error(self):
        response = self.post(b"bad,header\n1,2\n")
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "message error", status_code=400)
        response = self.post(b"")
        self.assertEqual(response.status_code, 400)
        response = self.run_preview(content=b"bad,header\n1,2\n")
        self.assertEqual(response.status_code, 400)

    def test_module_disabled_denied(self):
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id="onboarding",
            defaults={"enabled": False, "changed_by": self.owner.user})
        self.login(self.owner)
        self.assertEqual(self.client.get(LIST_URL).status_code, 403)
        self.assertEqual(self.run_preview().status_code, 403)
        self.assertEqual(self.post(self.time_csv()).status_code, 403)

    def test_explicit_kind_mismatch_returns_400(self):
        response = self.post(self.time_csv(), kind="expense")
        self.assertEqual(response.status_code, 400)
