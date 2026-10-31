"""PH-309 read-only operations reconciliation workbench HTTP boundaries."""
from django.test import Client, TestCase

from core.models import PluginState
from tests.test_v1_ops_reconciliation_overview import OpsOverviewFixture

URL = "/modules/delivery/ops-workbench/"


class OpsWorkbenchHTTPTests(OpsOverviewFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def test_owner_finance_auditor_and_pm_can_read(self):
        for member in (self.owner, self.finance, self.auditor, self.pm):
            self.login(member)
            response = self.client.get(URL)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "运营核对工作台")
            self.assertContains(response, "核对分区")

    def test_member_denied(self):
        self.login(self.plain)
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_filters_and_validation(self):
        self.login(self.owner)
        response = self.client.get(URL, {
            'as_of': self.today.isoformat(),
            'period': self.today.strftime('%Y-%m')})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(URL, {'as_of': 'bad-date'}).status_code, 400)
        self.assertEqual(self.client.get(URL, {'unexpected': '1'}).status_code, 400)
        self.assertEqual(self.client.post(URL, {}).status_code, 405)

    def test_module_disabled_history_readable(self):
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='delivery',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.login(self.owner)
        self.assertEqual(self.client.get(URL).status_code, 200)
