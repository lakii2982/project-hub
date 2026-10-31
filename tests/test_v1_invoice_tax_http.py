"""PH-320 invoice tax qualification/orchestration browser boundaries."""
import json
import uuid

from django.test import Client, TestCase

from pmo_plugins.invoicing import services
from pmo_plugins.invoicing.models import TaxQualification
from tests.test_v1_invoice_tax import TaxFixture


class TaxHTTPTests(TaxFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.url = '/modules/invoicing/invoice-tax/'
        self.confirm_url = '/modules/invoicing/invoice-tax-confirm/'
        self.link_url = '/modules/invoicing/invoice-tax-link/'

    def test_owner_page_and_confirm(self):
        opened = self.open()
        self.client.force_login(self.owner.user)
        page = self.client.get(self.url + '?id=' + self.invoice_id)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '发票税额资格确认与跨 owner 编排')
        self.assertContains(page, 'pending')
        response = self.client.post(self.confirm_url, {
            'qualification_id': opened['qualification_ref']['id'],
            'qualification_status': 'recoverable', 'decided_on': self.today.isoformat(),
            'evidence': json.dumps([{'kind': 'tax_decision', 'evidence_id': 'http'}], ensure_ascii=False),
            'expected_version': '1', 'invoice_id': self.invoice_id})
        self.assertEqual(response.status_code, 302)
        row = TaxQualification.objects.get(pk=opened['qualification_ref']['id'])
        self.assertEqual(row.status, 'confirmed')
        page = self.client.get(self.url + '?id=' + self.invoice_id)
        self.assertContains(page, 'recoverable')

    def test_link_and_invalid_posts(self):
        from pmo_plugins.invoicing.models import Invoice
        invoice, _ = self.allocated()
        invoice_id = invoice['invoice_ref']['id']
        invoice_row = Invoice.objects.get(pk=invoice_id)
        reversal = self.make_reversal(invoice_row)
        opened = self.open()
        self.confirm(opened)
        self.client.force_login(self.owner.user)
        owner_ref = {'namespace': 'settlement', 'kind': 'settlement',
                     'id': str(self.settlement.pk), 'version': self.settlement.version}
        response = self.client.post(self.link_url, {
            'invoice_id': invoice_id, 'reversal_id': str(reversal.pk),
            'link_kind': 'settlement_adjustment',
            'owner_ref': json.dumps(owner_ref),
            'evidence': json.dumps([{'kind': 'owner_fact', 'evidence_id': 'http'}]),
            'expected_version': '1'})
        self.assertEqual(response.status_code, 302)
        page = self.client.get(self.url + '?id=' + invoice_id)
        self.assertContains(page, 'settlement_adjustment')
        self.assertEqual(self.client.post(self.confirm_url, {
            'qualification_id': opened['qualification_ref']['id'],
            'qualification_status': 'recoverable', 'decided_on': self.today.isoformat(),
            'evidence': '[]', 'expected_version': '1'}).status_code, 400)
        self.assertEqual(self.client.post(self.link_url, {
            'invoice_id': invoice_id, 'link_kind': 'bogus',
            'owner_ref': '{}', 'evidence': '[]', 'expected_version': '1'}).status_code, 400)

    def test_roles_and_disabled_module_history_read(self):
        opened = self.open()
        self.confirm(opened)
        self.client.force_login(self.auditor.user)
        page = self.client.get(self.url + '?id=' + self.invoice_id)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, '确认/更正税额资格（owner/finance）')
        self.client.force_login(self.plain.user)
        self.assertEqual(self.client.get(self.url + '?id=' + self.invoice_id).status_code, 403)
        self.client.force_login(self.pm.user)
        self.assertEqual(self.client.get(self.url + '?id=' + self.invoice_id).status_code, 403)
        from core.plugin_runtime import get_registry, set_enabled
        manifests = get_registry().manifests
        for _ in range(len(manifests) + 1):
            for plugin_id in manifests:
                if plugin_id == 'invoicing':
                    continue
                try:
                    set_enabled(self.owner, plugin_id, False)
                except Exception:
                    pass
        set_enabled(self.owner, 'invoicing', False)
        self.client.force_login(self.auditor.user)
        page = self.client.get(self.url + '?id=' + self.invoice_id)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'recoverable')
