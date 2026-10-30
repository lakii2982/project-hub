"""PH-279 read-only cash bridge snapshot HTTP boundaries."""
from django.test import Client, TestCase

from core.models import PluginState
from tests.test_v1_cash_bridge_read import BridgeFixture

SNAPSHOT_URL = "/modules/cash/cash-bridge-snapshot/"
ISSUES_URL = "/modules/cash/cash-bridge-issues/"


class CashBridgeHTTPFixture(BridgeFixture):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)


class CashBridgeHTTPTests(CashBridgeHTTPFixture, TestCase):
    def test_owner_finance_auditor_can_read(self):
        row = self.posted(gross_cents=1000)
        self.classify(row, classification='receipt')
        for member in (self.owner, self.finance, self.auditor):
            self.login(member)
            response = self.client.get(SNAPSHOT_URL)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "现金核对快照")
            issues = self.client.get(ISSUES_URL)
            self.assertEqual(issues.status_code, 200)
            self.assertContains(issues, "异常清单")

    def test_pm_and_member_denied(self):
        for member in (self.pm, self.plain):
            self.login(member)
            self.assertEqual(self.client.get(SNAPSHOT_URL).status_code, 403)
            self.assertEqual(self.client.get(ISSUES_URL).status_code, 403)

    def test_module_disabled_http_routes_refuse_but_service_history_readable(self):
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id="cash",
            defaults={"enabled": False, "changed_by": self.owner.user})
        self.login(self.owner)
        # The plugin runtime gates disabled modules at the HTTP entry; the named read
        # services stay readable for history (service test covers that path).
        self.assertEqual(self.client.get(SNAPSHOT_URL).status_code, 403)
        self.assertEqual(self.client.get(ISSUES_URL).status_code, 403)
        from pmo_plugins.cash import reconciliation_services as recon
        self.assertEqual(recon.cash_bridge_snapshot(self.owner)['summary']['posted_cash_count'], 0)

    def test_filters_and_read_only_methods(self):
        row = self.posted(gross_cents=1000)
        self.classify(row, classification='receipt')
        self.login(self.owner)
        response = self.client.get(SNAPSHOT_URL, {"as_of": self.today.isoformat()})
        self.assertEqual(response.status_code, 200)
        response = self.client.get(ISSUES_URL, {"as_of": "2000-01-01"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "未发现异常")
        self.assertEqual(self.client.post(SNAPSHOT_URL, {}).status_code, 405)
        self.assertEqual(self.client.post(ISSUES_URL, {}).status_code, 405)
