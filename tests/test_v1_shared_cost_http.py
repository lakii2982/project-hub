"""PH-330 read-only reconciliation pages and permissions."""
import uuid

from django.test import TestCase

from pmo_plugins.costing import shared_cost_services
from tests.test_v1_shared_cost_allocation import CostFixture


class SharedCostHttpTests(CostFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.recon_url = '/modules/costing/shared-cost-reconciliation/'
        self.budget_url = '/modules/costing/budget-etc/'

    def test_owner_reads_reconciliation_and_missing_budget(self):
        self.allocate()
        self.client.force_login(self.owner.user)
        page = self.client.get(self.recon_url, {'period': self.period})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '共享费用分摊只读核对')
        self.assertContains(page, '10000')
        budget = self.client.get(self.budget_url, {'project': str(self.project.pk)})
        self.assertEqual(budget.status_code, 200)
        self.assertContains(budget, '预算/ETC 只读核对')
        self.assertContains(budget, '缺失')
        self.assertEqual(self.client.get(self.recon_url, {'period': 'bad'}).status_code, 400)

    def test_roles_and_disabled_module_history_read(self):
        self.client.force_login(self.plain.user)
        self.assertEqual(self.client.get(self.recon_url).status_code, 403)
        self.client.force_login(self.auditor.user)
        self.assertEqual(self.client.get(self.recon_url).status_code, 200)
        self.client.logout()
        from core.models import PluginState
        PluginState.objects.filter(organization=self.org, plugin_id='costing').update(enabled=False)
        self.client.force_login(self.owner.user)
        self.assertEqual(self.client.get(self.recon_url).status_code, 200)
        self.assertEqual(self.client.get(self.budget_url).status_code, 200)
