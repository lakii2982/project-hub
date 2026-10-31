"""PH-371 tax cost adjustment HTTP boundaries."""
import uuid

from django.test import Client, TestCase

from core.models import PluginState
from pmo_plugins.costing.models import SharedCostAllocationBatch
from tests.test_v1_tax_cost_adjustment import TaxAdjustmentFixture

PAGE = '/modules/costing/tax-adjustment-page/'
EXECUTE = '/modules/costing/tax-adjustment-execute/'
REVERSE = '/modules/costing/tax-adjustment-reverse/'


class TaxAdjustmentHTTPTests(TaxAdjustmentFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def test_owner_page_and_execute(self):
        self.confirm('not_recoverable')
        self.login(self.owner)
        page = self.client.get(PAGE)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '税额成本调整')
        response = self.client.post(EXECUTE, {
            'qualification': self.qualification['qualification_ref']['id'],
            'request_token': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SharedCostAllocationBatch.objects.count(), 1)

    def test_member_and_auditor_execute_denied(self):
        self.confirm('not_recoverable')
        self.login(self.plain)
        self.assertEqual(self.client.get(PAGE).status_code, 403)
        self.assertEqual(self.client.post(EXECUTE, {}).status_code, 403)
        self.login(self.auditor)
        self.assertEqual(self.client.get(PAGE).status_code, 200)
        self.assertEqual(self.client.post(EXECUTE, {}).status_code, 403)
        self.assertEqual(SharedCostAllocationBatch.objects.count(), 0)

    def test_module_disabled_read_kept_write_denied(self):
        self.confirm('not_recoverable')
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='costing',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.login(self.owner)
        self.assertEqual(self.client.get(PAGE).status_code, 200)
        self.assertEqual(self.client.post(EXECUTE, {
            'qualification': self.qualification['qualification_ref']['id'],
            'request_token': str(uuid.uuid4())}).status_code, 403)
        self.assertEqual(SharedCostAllocationBatch.objects.count(), 0)

    def test_reverse_page_after_adjustment(self):
        self.confirm('not_recoverable')
        result = self.adjust()
        self.login(self.owner)
        response = self.client.post(REVERSE, {
            'batch': result['batch_ref']['id'], 'reason': 'HTTP 冲销',
            'request_token': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(SharedCostAllocationBatch.objects.filter(kind='reversal').count(), 1)
