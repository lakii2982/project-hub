"""PH-283 settlement coverage read-only HTTP boundaries."""
from django.test import Client, TestCase

from core.models import PluginState
from tests.test_v1_settlement_coverage_read import CoverageFixture

SNAPSHOT_URL = "/modules/settlement/settlement-coverage-snapshot/"
ISSUES_URL = "/modules/settlement/settlement-coverage-issues/"


class CoverageHTTPFixture(CoverageFixture):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)


class SettlementCoverageHTTPTests(CoverageHTTPFixture, TestCase):
    def test_owner_finance_auditor_and_pm_can_read(self):
        self.clear(amount=10000)
        for member in (self.owner, self.finance, self.auditor, self.pm):
            self.login(member)
            response = self.client.get(SNAPSHOT_URL)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "未清覆盖核对")
            issues = self.client.get(ISSUES_URL)
            self.assertEqual(issues.status_code, 200)
            self.assertContains(issues, "异常清单")

    def test_member_denied(self):
        self.login(self.plain)
        self.assertEqual(self.client.get(SNAPSHOT_URL).status_code, 403)
        self.assertEqual(self.client.get(ISSUES_URL).status_code, 403)

    def test_module_disabled_history_readable_via_history_read(self):
        self.clear(amount=10000)
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id="settlement",
            defaults={"enabled": False, "changed_by": self.owner.user})
        self.login(self.owner)
        self.assertEqual(self.client.get(SNAPSHOT_URL).status_code, 200)
        self.assertEqual(self.client.get(ISSUES_URL).status_code, 200)
        self.assertEqual(self.client.post(SNAPSHOT_URL, {}).status_code, 405)
        self.assertEqual(self.client.post(ISSUES_URL, {}).status_code, 405)

    def test_filters_and_read_only_methods(self):
        self.clear(amount=10000)
        self.login(self.owner)
        response = self.client.get(SNAPSHOT_URL, {
            "as_of": self.today.isoformat(), "project": str(self.item.project_id)})
        self.assertEqual(response.status_code, 200)
        response = self.client.get(ISSUES_URL, {"as_of": "2000-01-01"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "未发现异常")
