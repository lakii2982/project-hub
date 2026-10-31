"""PH-316 workbench-to-export linkage HTTP boundaries."""
from urllib.parse import urlencode

from django.test import Client, TestCase

from core.models import PluginState
from tests.test_v1_ops_reconciliation_overview import OpsOverviewFixture

WORKBENCH_URL = "/modules/delivery/ops-workbench/"
EXPORT_URL = "/modules/delivery/ops-export/"


class WorkbenchExportHTTPTests(OpsOverviewFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def test_workbench_links_to_export_with_filters(self):
        self.login(self.owner)
        response = self.client.get(WORKBENCH_URL, {'as_of': self.today.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "导出底稿")
        self.assertContains(response, 'dataset=settlement_coverage')
        self.assertContains(response, 'dataset=cash_bridge')
        self.assertContains(response, 'dataset=fee_pairing')
        self.assertContains(response, self.today.isoformat())
        self.assertNotContains(response, 'dataset=identity_access')

    def test_export_from_workbench_link(self):
        self.login(self.owner)
        response = self.client.get(EXPORT_URL, {
            'dataset': 'settlement_coverage',
            'as_of': self.today.isoformat(), 'download': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv; charset=utf-8')
        body = response.content.decode('utf-8')
        self.assertTrue(body.startswith('\ufeff'))
        self.assertIn('settlement_coverage', body)

    def test_pm_links_exclude_cash_and_export_project_rows(self):
        self.login(self.pm)
        response = self.client.get(WORKBENCH_URL, {'as_of': self.today.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'dataset=settlement_coverage')
        self.assertNotContains(response, 'dataset=cash_bridge')
        exported = self.client.get(EXPORT_URL, {
            'dataset': 'settlement_coverage', 'as_of': self.today.isoformat(),
            'download': '1'})
        self.assertEqual(exported.status_code, 200)
        denied = self.client.get(EXPORT_URL, {
            'dataset': 'cash_bridge', 'as_of': self.today.isoformat(), 'download': '1'})
        self.assertEqual(denied.status_code, 403)

    def test_member_denied_and_no_new_write_channel(self):
        self.login(self.plain)
        self.assertEqual(self.client.get(WORKBENCH_URL).status_code, 403)
        self.login(self.owner)
        self.assertEqual(self.client.post(WORKBENCH_URL, {}).status_code, 405)
        self.assertEqual(self.client.post(EXPORT_URL, {}).status_code, 405)

    def test_module_disabled_history_readable(self):
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='delivery',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.login(self.owner)
        self.assertEqual(self.client.get(WORKBENCH_URL).status_code, 200)
