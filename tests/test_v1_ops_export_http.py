"""PH-304 authorized read-only reconciliation CSV export HTTP boundaries."""
from django.test import Client, TestCase

from core.models import PluginState
from tests.test_v1_ops_reconciliation_overview import OpsOverviewFixture

URL = "/modules/delivery/ops-export/"


class OpsExportHTTPTests(OpsOverviewFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def test_owner_page_and_download(self):
        self.login(self.owner)
        response = self.client.get(URL, {'dataset': 'ops_overview'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "核对底稿导出")
        self.assertContains(response, "导出计划")
        download = self.client.get(URL, {
            'dataset': 'ops_overview', 'download': '1', 'as_of': self.today.isoformat()})
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download['Content-Type'], 'text/csv; charset=utf-8')
        self.assertIn('attachment;', download['Content-Disposition'])
        body = b''.join(download.streaming_content) if download.streaming else download.content
        self.assertTrue(body.startswith('\ufeff'.encode('utf-8')))
        self.assertIn('ops_overview', body.decode('utf-8'))

    def test_finance_and_auditor_can_read(self):
        for member in (self.finance, self.auditor):
            self.login(member)
            self.assertEqual(self.client.get(URL, {'dataset': 'settlement_coverage'}).status_code, 200)

    def test_member_denied_and_pm_cash_dataset_denied(self):
        self.login(self.plain)
        self.assertEqual(self.client.get(URL, {'dataset': 'ops_overview'}).status_code, 403)
        self.login(self.pm)
        self.assertEqual(self.client.get(URL, {'dataset': 'ops_overview'}).status_code, 200)
        self.assertEqual(self.client.get(URL, {'dataset': 'cash_bridge'}).status_code, 403)

    def test_invalid_params(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(URL, {'dataset': 'unknown'}).status_code, 400)
        self.assertEqual(self.client.get(URL, {'dataset': 'ops_overview',
                                               'unexpected': '1'}).status_code, 400)
        self.assertEqual(self.client.get(URL, {'dataset': 'ops_overview',
                                               'as_of': 'bad-date'}).status_code, 400)
        self.assertEqual(self.client.post(URL, {'dataset': 'ops_overview'}).status_code, 405)

    def test_plan_download_token_idempotent(self):
        import uuid
        token = uuid.uuid4()
        self.login(self.owner)
        page = self.client.get(URL, {'dataset': 'ops_overview', 'token': str(token),
                                     'as_of': self.today.isoformat()})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, str(token))
        first = self.client.get(URL, {
            'dataset': 'ops_overview', 'download': '1', 'token': str(token),
            'as_of': self.today.isoformat()})
        self.assertEqual(first.status_code, 200)
        first_id = first['X-Content-SHA256']
        self.assertEqual(len(first_id), 64)
        second = self.client.get(URL, {
            'dataset': 'ops_overview', 'download': '1', 'token': str(token),
            'as_of': self.today.isoformat()})
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second['X-Content-SHA256'], first_id)

    def test_module_disabled_history_readable(self):
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='delivery',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.login(self.owner)
        self.assertEqual(self.client.get(URL, {'dataset': 'ops_overview'}).status_code, 200)
