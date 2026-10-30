"""PH-275 read-only change impact browser boundaries."""
from django.test import Client, TestCase

from core.models import AuditEvent, DomainEvent
from tests.test_v1_contract_change_impact import ImpactFixture


class ContractChangeImpactHTTPTests(ImpactFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.change = self.draft_change()

    def impact_url(self, change_id=None):
        url = '/modules/contracts/contract-change-impact/'
        return url + (f'?id={change_id}' if change_id else '')

    def test_contract_detail_and_impact_page_render_read_only_totals(self):
        self.make_facts()
        self.client.force_login(self.owner.user)
        page = self.client.get('/modules/contracts/contract/?id=' + str(self.contract.pk))
        self.assertContains(page, '变更影响（只读）')
        impact = self.client.get(self.impact_url(self.change.pk))
        self.assertEqual(impact.status_code, 200)
        self.assertContains(impact, '变更影响（只读）')
        self.assertContains(impact, '不自动重开旧版本')
        self.assertContains(impact, '1,000.00')
        self.assertContains(impact, '管理收入确认（净额）')

    def test_roles_pm_summary_member_denied_and_unknown(self):
        self.client.force_login(self.auditor.user)
        self.assertEqual(self.client.get(self.impact_url(self.change.pk)).status_code, 200)
        self.client.force_login(self.pm.user)
        response = self.client.get(self.impact_url(self.change.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '变更影响（只读）')
        self.client.force_login(self.member('member').user)
        self.assertEqual(self.client.get(self.impact_url(self.change.pk)).status_code, 403)
        self.client.force_login(self.owner.user)
        self.assertEqual(self.client.get(self.impact_url()).status_code, 403)
        self.assertEqual(self.client.get(self.impact_url('00000000-0000-0000-0000-000000000000')).status_code, 403)

    def test_disabled_module_history_read_keeps_impact_available(self):
        from core.plugin_runtime import get_registry, is_enabled, set_enabled
        for plugin_id in ('time', 'management_revenue', 'settlement', 'delivery', 'contracts'):
            if plugin_id in get_registry().manifests:
                set_enabled(self.owner, plugin_id, False)
        self.assertFalse(is_enabled(self.org, 'contracts'))
        self.client.force_login(self.auditor.user)
        response = self.client.get(self.impact_url(self.change.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '变更影响（只读）')

    def test_query_parameters_do_not_change_facts_or_trigger_recalculation(self):
        self.make_facts()
        before = (AuditEvent.objects.count(), DomainEvent.objects.count())
        self.client.force_login(self.owner.user)
        response = self.client.get(self.impact_url(self.change.pk) +
                                   '&status=approved&recalculate=1&amount=999')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(before, (AuditEvent.objects.count(), DomainEvent.objects.count()))
        from pmo_plugins.settlement.models import Settlement
        self.assertEqual(Settlement.objects.filter(status='active').count(), 1)
