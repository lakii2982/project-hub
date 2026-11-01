"""PH-338 invoice import projection HTTP boundaries and onboarding submission reuse."""
import uuid

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase

from core.models import PluginState
from pmo_plugins.invoicing import urls as invoicing_urls
from pmo_plugins.invoicing.models import Invoice
from tests.test_v1_invoice_import import InvoiceImportFixture

PAGE_URL = '/modules/invoicing/invoice-import/'
ONBOARDING_UPLOAD_URL = '/modules/onboarding/import-preview-upload/'
ONBOARDING_COMMIT_URL = '/modules/onboarding/import-submit-commit/'


class InvoiceImportHTTPFixture(InvoiceImportFixture):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self._previous_entry_point = settings.PMO_PLUGIN_ENTRY_POINT
        settings.PMO_PLUGIN_ENTRY_POINT = invoicing_urls.entry_point()
        self.addCleanup(setattr, settings, 'PMO_PLUGIN_ENTRY_POINT',
                        self._previous_entry_point)

    def login(self, member):
        self.client.force_login(member.user)

    def get_page(self, *, member=None, params=None):
        self.login(member or self.owner)
        return self.client.get(PAGE_URL, params or {})

    def upload_csv(self, rows, *, member=None):
        self.login(member or self.owner)
        upload = SimpleUploadedFile(
            'invoices.csv', self.csv_bytes(rows), content_type='text/csv')
        return self.client.post(
            ONBOARDING_UPLOAD_URL, {'kind': 'invoicing', 'file': upload})

    def commit_form(self, preview, **changes):
        snapshot = self.snapshot()
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

    def post_commit(self, preview, *, member=None, **changes):
        self.login(member or self.owner)
        return self.client.post(
            ONBOARDING_COMMIT_URL, self.commit_form(preview, **changes))


class InvoiceImportHTTPTests(InvoiceImportHTTPFixture, TestCase):
    def test_owner_finance_page_and_auditor_read_only(self):
        for member in (self.owner, self.finance):
            page = self.get_page(member=member)
            self.assertEqual(page.status_code, 200)
            self.assertContains(page, '发票批量导入')
            self.assertContains(page, '冻结模板与字段映射')
            self.assertContains(page, '上传并预检')
        audit = self.get_page(member=self.auditor)
        self.assertEqual(audit.status_code, 200)
        self.assertContains(audit, '冻结模板与字段映射')
        self.assertNotContains(audit, '上传并预检')
        for member in (self.pm, self.plain):
            self.assertEqual(self.get_page(member=member).status_code, 403)

    def test_module_disabled_history_read_and_post_405(self):
        preview_result = self.preview(self.invoice_rows('INV-HTTP-DIS'))
        preview = self.persist(preview_result['rows'],
                               conflicts=preview_result['conflicts'])
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='invoicing',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        page = self.get_page(member=self.auditor)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '模块已停用')
        self.assertContains(page, str(preview.pk))
        self.login(self.owner)
        self.assertEqual(self.client.post(PAGE_URL, {}).status_code, 405)

    def test_unknown_query_parameters_are_rejected(self):
        response = self.get_page(params={'amount_cents': '100'})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '包含不允许的查询参数', status_code=400)

    def test_preview_selection_projection(self):
        result = self.preview(self.invoice_rows('INV-HTTP-SEL'))
        preview = self.persist(result['rows'], conflicts=result['conflicts'])
        response = self.get_page(params={'preview': str(preview.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'INV-HTTP-SEL')
        self.assertContains(response, str(preview.rows_digest))

    def test_full_preview_upload_then_onboarding_commit(self):
        rows = self.invoice_rows('INV-HTTP-GOLD')
        upload = self.upload_csv(rows)
        self.assertEqual(upload.status_code, 200)
        self.assertContains(upload, '预览结果')

        result = self.preview(rows)
        preview = self.persist(result['rows'], conflicts=result['conflicts'])
        committed = self.post_commit(preview)
        self.assertEqual(committed.status_code, 302)
        self.assertEqual(Invoice.objects.count(), 1)
        history = self.get_page()
        self.assertContains(history, '批次提交收据')
        self.assertContains(history, 'invoicing')

    def test_auditor_cannot_upload_or_commit(self):
        result = self.preview(self.invoice_rows('INV-HTTP-AUD'))
        preview = self.persist(result['rows'], conflicts=result['conflicts'])
        self.assertEqual(self.get_page(member=self.auditor).status_code, 200)
        self.assertEqual(
            self.upload_csv(self.invoice_rows('INV-HTTP-AUD2'),
                            member=self.auditor).status_code, 403)
        self.assertEqual(
            self.post_commit(preview, member=self.auditor).status_code, 403)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_commit_rejection_envelope_is_shared_with_onboarding(self):
        result = self.preview(self.invoice_rows('INV-HTTP-REJ'))
        preview = self.persist(result['rows'], conflicts=result['conflicts'])
        response = self.post_commit(preview, precheck_digest='0' * 64)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '提交被拒绝', status_code=400)
        self.assertContains(response, 'precheck_stale', status_code=400)
        self.assertEqual(Invoice.objects.count(), 0)
