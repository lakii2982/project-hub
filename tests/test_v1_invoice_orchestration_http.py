"""PH-370 orchestration page/action HTTP contract."""
import json
import uuid

from django.test import TestCase

from tests.test_v1_invoice_orchestration import OrchFixture


class OrchestrationHttpTests(OrchFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.url = '/modules/invoicing/invoice-orchestration/'
        self.execute_url = '/modules/invoicing/invoice-orchestration-execute/'

    def test_owner_get_and_execute_cash_refund(self):
        self.client.force_login(self.owner.user)
        page = self.client.get(self.url, {'invoice_id': str(self.invoice_row.pk)})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '跨 owner 编排执行')
        payload = {'original_cash_transaction_id': str(self.cash.pk),
                   'refund_date': self.today.isoformat(), 'gross_cents': 1000,
                   'reason': 'HTTP 编排'}
        response = self.client.post(self.execute_url, {
            'invoice_id': str(self.invoice_row.pk),
            'reversal_id': str(self.reversal.pk),
            'action': 'cash_refund',
            'payload': json.dumps(payload),
            'expected_version': str(self.invoice_row.version)})
        self.assertEqual(response.status_code, 302)

    def test_invalid_payload_and_roles(self):
        self.client.force_login(self.owner.user)
        response = self.client.post(self.execute_url, {
            'invoice_id': str(self.invoice_row.pk), 'reversal_id': str(self.reversal.pk),
            'action': 'cash_refund', 'payload': '{}',
            'expected_version': str(self.invoice_row.version)})
        self.assertEqual(response.status_code, 400)
        self.client.force_login(self.auditor.user)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.assertEqual(self.client.post(self.execute_url, {}).status_code, 403)
