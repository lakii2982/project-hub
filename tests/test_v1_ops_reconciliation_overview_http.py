"""PH-291 cross-domain read-only operations reconciliation overview (HTTP)."""
from datetime import timedelta

from django.test import Client, TestCase

from core.models import AuditEvent, DomainEvent, LedgerEntry, PluginState
from pmo_plugins.contracts.models import Contract, ContractChange
from tests.test_v1_ops_reconciliation_overview import OpsOverviewFixture

URL = '/modules/delivery/ops-reconciliation-overview/'


class OpsOverviewHTTPTests(OpsOverviewFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member=None):
        self.client.force_login((member or self.owner).user)

    def get(self, params=None, *, member=None):
        self.login(member)
        return self.client.get(URL, params or {})

    def writes(self):
        return tuple(model.objects.count() for model in
                     (AuditEvent, DomainEvent, LedgerEntry, Contract, ContractChange))

    def test_each_read_role_can_open_overview(self):
        for member in (self.owner, self.finance, self.auditor, self.pm):
            response = self.get(member=member)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '运营核对总览')

    def test_member_and_anonymous_denied(self):
        self.assertEqual(self.get(member=self.plain).status_code, 403)
        response = self.client.get(URL)
        self.assertIn(response.status_code, (302, 403))
        if response.status_code == 302:
            self.assertIn('/login/', response['Location'])

    def test_page_shows_sections_missing_and_no_write(self):
        self.pending_change()
        before = self.writes()
        response = self.get()
        self.assertEqual(before, self.writes())
        self.assertContains(response, '合同')
        self.assertContains(response, '结算与未清')
        self.assertContains(response, '管理收入')
        self.assertContains(response, '工时与可计费')
        self.assertContains(response, '导入预览')
        self.assertContains(response, '现金')
        self.assertContains(response, 'import_preview_persistence_not_implemented')

    def test_pm_page_scope_and_cash_missing(self):
        finance_project = self.project2
        hidden = self.direct_contract('C8', project=finance_project)
        ContractChange.objects.create(
            organization=self.org, contract=hidden, status='draft',
            change_type='price_change', requested_on=self.today, net_delta_cents=2,
            tax_delta_cents=0, gross_delta_cents=2, created_by_member=self.owner)
        self.pending_change()
        response = self.get(member=self.pm)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'cash_not_scoped_to_project')
        self.assertContains(response, '1')  # the visible pending change count
        self.assertNotContains(response, 'C8')

    def test_invalid_filters_return_400(self):
        future = (self.today + timedelta(days=1)).isoformat()
        for params in ({'as_of': future}, {'period': '2026-13'}, {'period': 'bad'},
                       {'unknown': '1'}):
            self.assertEqual(self.get(params).status_code, 400)

    def test_post_not_allowed(self):
        self.login()
        self.assertEqual(self.client.post(URL).status_code, 405)

    def test_module_disabled_history_still_readable(self):
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='delivery',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '运营核对总览')
