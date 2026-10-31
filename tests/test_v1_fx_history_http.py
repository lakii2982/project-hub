"""PH-388 FX history/reconciliation page boundaries."""
from django.test import Client, TestCase

from pmo_plugins.contracts import fx_services
from tests.test_v1_fx import FxFixture


class FxHistoryHTTPTests(FxFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.url = '/modules/contracts/fx-rates/'

    def test_owner_page_renders_history_and_reconciliation(self):
        row = self.record()
        self.client.force_login(self.owner.user)
        page = self.client.get(self.url, {'quote_currency': 'USD',
                                          'effective_on': self.today.isoformat()})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '汇率历史反查')
        self.assertContains(page, '原币/记账币/汇率引用对账')
        self.assertContains(page, 'USD→CNY')
        self.assertContains(page, str(row['ref']['id']))

    def test_exact_date_missing_and_future_rejected(self):
        self.client.force_login(self.owner.user)
        missing = self.client.get(self.url, {'quote_currency': 'USD',
                                             'effective_on': '2026-10-02'})
        self.assertEqual(missing.status_code, 200)
        self.assertContains(missing, 'fx_rate_exact_date_missing')
        future = (__import__('datetime').date.today() + __import__('datetime').timedelta(days=10))
        response = self.client.get(self.url, {'quote_currency': 'USD',
                                              'effective_on': future.isoformat()})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '不能查询未来生效汇率用于历史')

    def test_roles_and_disabled_module_history_read(self):
        row = self.record()
        self.client.force_login(self.auditor.user)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.force_login(self.pm.user)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.force_login(self.member('member').user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        from core.plugin_runtime import get_registry, set_enabled
        from django.core.exceptions import ValidationError
        for _ in range(len(get_registry().manifests) + 1):
            for plugin_id in get_registry().manifests:
                if plugin_id == 'contracts':
                    continue
                try:
                    set_enabled(self.owner, plugin_id, False)
                except ValidationError:
                    pass
        set_enabled(self.owner, 'contracts', False)
        self.client.force_login(self.auditor.user)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'USD→CNY')
        self.assertEqual(fx_services.FxRateSnapshot.objects.count(), 1)
