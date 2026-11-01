"""PH-353 onboarding import submission HTTP boundaries."""
import uuid

from django.test import Client, TestCase

from core.models import Document, PluginState
from tests.test_v1_import_submission import ImportSubmissionFixture

PAGE_URL = '/modules/onboarding/import-submit/'
COMMIT_URL = '/modules/onboarding/import-submit-commit/'


class ImportSubmissionHTTPFixture(ImportSubmissionFixture):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def form_data(self, preview, *, snapshot=None, **changes):
        snapshot = snapshot or self.snapshot()
        data = {
            'preview_ref': str(preview.pk),
            'rows_digest': preview.rows_digest,
            'precheck_digest': snapshot['precheck_digest'],
            'precheck_as_of_date': self.today.isoformat(),
            'period': self.today.strftime('%Y-%m'),
            'request_token': str(uuid.uuid4()),
            'expected_version': str(preview.version),
        }
        data.update(changes)
        return data

    def post_commit(self, preview, *, member=None, snapshot=None, **changes):
        self.login(member or self.owner)
        return self.client.post(
            COMMIT_URL, self.form_data(preview, snapshot=snapshot, **changes))

    def get_page(self, *, member=None, params=None):
        self.login(member or self.owner)
        return self.client.get(PAGE_URL, params or {})


class ImportSubmissionHTTPTests(ImportSubmissionHTTPFixture, TestCase):
    def test_owner_finance_page_and_dry_run_then_commit(self):
        preview = self.persist()
        snapshot = self.snapshot()
        page = self.get_page()
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '导入提交')
        dry = self.post_commit(preview, snapshot=snapshot, dry_run='on')
        self.assertEqual(dry.status_code, 200)
        self.assertContains(dry, '预演结果')
        self.assertEqual(Document.objects.count(), 0)
        committed = self.post_commit(preview, snapshot=snapshot)
        self.assertEqual(committed.status_code, 302)
        self.assertEqual(Document.objects.count(), 2)
        history = self.get_page()
        self.assertContains(history, '提交历史')

        second = self.persist(rows=[self.expense_row('E-9', 'INV-9')])
        finance_page = self.get_page(member=self.finance)
        self.assertEqual(finance_page.status_code, 200)
        self.assertEqual(
            self.post_commit(second, member=self.finance).status_code, 302)

    def test_auditor_history_read_only_pm_and_member_denied(self):
        preview = self.persist()
        self.assertEqual(self.get_page(member=self.auditor).status_code, 200)
        self.assertEqual(self.post_commit(preview, member=self.auditor).status_code, 403)
        for member in (self.pm, self.plain):
            self.assertEqual(self.get_page(member=member).status_code, 403)
            self.assertEqual(self.post_commit(preview, member=member).status_code, 403)
        self.assertEqual(Document.objects.count(), 0)

    def test_module_disabled_history_read_but_write_denied(self):
        preview = self.persist()
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='onboarding',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        history = self.get_page(member=self.auditor)
        self.assertEqual(history.status_code, 200)
        self.assertContains(history, '模块已停用')
        self.assertEqual(self.post_commit(preview).status_code, 403)
        self.assertEqual(Document.objects.count(), 0)

    def test_unknown_fields_and_query_parameters_are_rejected(self):
        preview = self.persist()
        extra = self.post_commit(preview, amount_cents='100')
        self.assertEqual(extra.status_code, 400)
        self.assertContains(extra, '包含不允许的字段', status_code=400)
        unknown = self.get_page(params={'amount_cents': '100'})
        self.assertEqual(unknown.status_code, 400)
        self.assertContains(unknown, '包含不允许的查询参数', status_code=400)

    def test_wrong_methods_return_405(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(COMMIT_URL).status_code, 405)
        self.assertEqual(self.client.post(PAGE_URL, {}).status_code, 405)

    def test_rejection_envelope_is_rendered_without_writes(self):
        preview = self.persist()
        response = self.post_commit(preview, precheck_digest='0' * 64)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '提交被拒绝', status_code=400)
        self.assertContains(response, 'precheck_stale', status_code=400)
        self.assertEqual(Document.objects.count(), 0)

    def test_unknown_preview_is_rejected(self):
        preview = self.persist()
        response = self.post_commit(preview, preview_ref=str(uuid.uuid4()))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'preview_not_found', status_code=400)
        self.assertEqual(Document.objects.count(), 0)
