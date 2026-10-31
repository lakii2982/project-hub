"""PH-321 contracts-only FX rate/read-only page boundaries."""
from django.test import Client, TestCase

from pmo_plugins.contracts import fx_services
from tests.test_v1_fx import FxFixture


class FxHTTPTests(FxFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.url = '/modules/contracts/fx-rates/'
        self.save_url = '/modules/contracts/fx-rate-save/'

    def test_owner_page_renders_and_records_rate(self):
        self.client.force_login(self.owner.user)
        before = fx_services.FxRateSnapshot.objects.count()
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '汇率快照与合同原币/记账币核对')
        self.assertContains(page, '登记汇率')
        self.assertEqual(fx_services.FxRateSnapshot.objects.count(), before)
        response = self.client.post(self.save_url, {
            'quote_currency': 'USD', 'rate': '7.20000000',
            'effective_on': self.today.isoformat(), 'source': 'manual',
            'reference': '银行牌价'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(fx_services.FxRateSnapshot.objects.count(), before + 1)
        page = self.client.get(self.url)
        self.assertContains(page, 'USD→CNY')
        self.assertContains(page, '7.20000000')

    def test_invalid_post_and_roles(self):
        self.client.force_login(self.owner.user)
        self.assertEqual(self.client.post(self.save_url, {
            'quote_currency': 'CNY', 'rate': '1', 'effective_on': self.today.isoformat(),
            'source': 'manual'}).status_code, 400)
        self.assertEqual(self.client.post(self.save_url, {
            'quote_currency': 'USD', 'rate': '0', 'effective_on': self.today.isoformat(),
            'source': 'manual'}).status_code, 400)
        future = (self.today + __import__('datetime').timedelta(days=10)).isoformat()
        self.assertEqual(self.client.post(self.save_url, {
            'quote_currency': 'USD', 'rate': '7.2', 'effective_on': future,
            'source': 'manual'}).status_code, 302)
        self.assertTrue(fx_services.FxRateSnapshot.objects.filter(
            quote_currency='USD', effective_on=future).exists())
        self.client.force_login(self.auditor.user)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, '登记汇率（owner/finance）')
        self.client.force_login(self.pm.user)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.client.force_login(self.member('member').user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_disabled_module_history_read(self):
        self.client.force_login(self.owner.user)
        self.record()
        from core.plugin_runtime import get_registry, set_enabled
        manifests = get_registry().manifests
        for _ in range(len(manifests) + 1):
            for plugin_id in manifests:
                if plugin_id == 'contracts':
                    continue
                try:
                    set_enabled(self.owner, plugin_id, False)
                except Exception:
                    pass
        set_enabled(self.owner, 'contracts', False)
        self.client.force_login(self.auditor.user)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'USD→CNY')
