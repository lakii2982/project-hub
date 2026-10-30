"""PH-280 fee pairing browser boundaries."""
from datetime import timedelta

from django.test import Client, TestCase

from core.models import AuditEvent, DomainEvent, LedgerPosting, SourceConsumption
from tests.test_v1_bank_charge_pairing import FeePairingFixture


class BankChargePairingHTTPTests(FeePairingFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.url = '/modules/costing/bank-charge-pairing/'
        self.charge = self.approved(self.cash(), gross=200)

    def test_page_renders_paired_gold_and_ph251_placeholders(self):
        self.client.force_login(self.owner.user)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '手续费批准/成本与净额只读配对')
        self.assertContains(page, 'posted')
        self.assertContains(page, 'PH-251')
        self.assertContains(page, '配对清单（只读）')
        self.client.force_login(self.auditor.user)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.force_login(self.pm.user)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.force_login(self.member('member').user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_filters_and_invalid_inputs(self):
        self.client.force_login(self.owner.user)
        self.assertEqual(self.client.get(self.url + '?status=approved').status_code, 200)
        self.assertEqual(self.client.get(
            self.url + '?cash_transaction_id=' + str(self.charge.cash_transaction_id)).status_code, 200)
        self.assertEqual(self.client.get(
            self.url + '?as_of=' + (self.today + timedelta(days=1)).isoformat()).status_code, 400)
        self.assertEqual(self.client.get(self.url + '?as_of=bad-date').status_code, 400)
        self.assertEqual(self.client.get(self.url + '?balance=1').status_code, 400)
        self.assertEqual(self.client.get(self.url + '?cost_cents=1').status_code, 400)
        self.assertEqual(self.client.get(self.url + '?cash_transaction_id=bogus').status_code, 400)

    def test_issue_table_disabled_module_and_no_writes(self):
        self.synthetic(self.cash(suffix='missing'), gross=200)
        self.client.force_login(self.owner.user)
        page = self.client.get(self.url)
        self.assertContains(page, 'approved_without_cost')
        from core.plugin_runtime import get_registry, set_enabled
        for plugin_id in ('onboarding', 'time', 'management_revenue', 'settlement', 'delivery',
                          'contracts', 'costing'):
            if plugin_id in get_registry().manifests:
                set_enabled(self.owner, plugin_id, False)
        before = (AuditEvent.objects.count(), DomainEvent.objects.count(),
                  LedgerPosting.objects.count(), SourceConsumption.objects.count())
        self.client.force_login(self.auditor.user)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '成本模块已停用')
        response = self.client.post(self.url, {'gross_cents': '1', 'balance': '1'})
        self.assertIn(response.status_code, (400, 403, 405))
        self.assertEqual(before, (AuditEvent.objects.count(), DomainEvent.objects.count(),
                                  LedgerPosting.objects.count(), SourceConsumption.objects.count()))
