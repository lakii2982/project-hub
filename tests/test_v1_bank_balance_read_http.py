"""PH-288 read-only bank-account balance snapshot HTTP boundaries."""
from django.test import Client, TestCase

from core.models import PluginState
from tests.test_v1_bank_balance_read import BankBalanceFixture

SNAPSHOT_URL = "/modules/cash/bank-balance-snapshot/"
ISSUES_URL = "/modules/cash/bank-balance-issues/"


class BankBalanceHTTPFixture(BankBalanceFixture):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)


class BankBalanceHTTPTests(BankBalanceHTTPFixture, TestCase):
    def test_owner_finance_auditor_can_read(self):
        row = self.posted(gross_cents=1000, direction='in')
        self.classify(row, classification='receipt')
        for member in (self.owner, self.finance, self.auditor):
            self.login(member)
            response = self.client.get(SNAPSHOT_URL)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "银行账户余额对账")
            issues = self.client.get(ISSUES_URL)
            self.assertEqual(issues.status_code, 200)
            self.assertContains(issues, "异常清单")

    def test_pm_and_member_denied(self):
        for member in (self.pm, self.plain):
            self.login(member)
            self.assertEqual(self.client.get(SNAPSHOT_URL).status_code, 403)
            self.assertEqual(self.client.get(ISSUES_URL).status_code, 403)

    def test_module_disabled_history_readable_via_history_read(self):
        self.posted(gross_cents=1000, direction='in')
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id="cash",
            defaults={"enabled": False, "changed_by": self.owner.user})
        self.login(self.owner)
        self.assertEqual(self.client.get(SNAPSHOT_URL).status_code, 200)
        self.assertEqual(self.client.get(ISSUES_URL).status_code, 200)
        self.assertEqual(self.client.post(SNAPSHOT_URL, {}).status_code, 405)
        self.assertEqual(self.client.post(ISSUES_URL, {}).status_code, 405)

    def test_filters(self):
        row = self.posted(gross_cents=1000, direction='in')
        self.classify(row, classification='receipt')
        self.login(self.owner)
        response = self.client.get(SNAPSHOT_URL, {
            "as_of": self.today.isoformat(),
            "period": self.today.strftime('%Y-%m'),
            "funding_account": str(self.account.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.account.name)
