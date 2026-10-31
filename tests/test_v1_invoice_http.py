"""PH-252 invoicing browser/HTTP boundaries."""
import json
import uuid

from django.test import Client, TestCase

from core.models import PluginState
from pmo_plugins.invoicing.models import Invoice
from tests.test_v1_invoice import InvoiceFixture

LIST_URL = '/modules/invoicing/invoice-list/'
FORM_URL = '/modules/invoicing/invoice-form/'
RECORD_URL = '/modules/invoicing/invoice-record/'
DETAIL_URL = '/modules/invoicing/invoice-detail/'
ALLOCATE_URL = '/modules/invoicing/invoice-allocate/'
REVERSE_URL = '/modules/invoicing/invoice-reverse/'


class InvoiceHTTPTests(InvoiceFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def record(self, member=None, **changes):
        self.login(member or self.owner)
        data = {'direction': 'sales', 'counterparty': str(self.contract.party_id),
                'invoice_number': 'HTTP-' + uuid.uuid4().hex[:8].upper(),
                'invoice_date': self.today.isoformat(), 'gross_cents': 10000,
                'net_cents': 9000, 'tax_cents': 1000,
                'request_token': str(uuid.uuid4())}
        data.update(changes)
        return self.client.post(RECORD_URL, data)

    def test_owner_records_and_reads_detail(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(LIST_URL).status_code, 200)
        self.assertEqual(self.client.get(FORM_URL).status_code, 200)
        response = self.record()
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get()
        detail = self.client.get(DETAIL_URL, {'id': str(invoice.pk)})
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, invoice.invoice_number)
        self.assertContains(detail, '红冲≠退款')

    def test_allocate_and_reverse_through_pages(self):
        self.login(self.owner)
        self.record()
        invoice = Invoice.objects.get()
        lines = self.lines()
        response = self.client.post(ALLOCATE_URL, {
            'invoice_id': str(invoice.pk), 'expected_version': 1,
            'lines': json.dumps(lines), 'request_token': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        detail = self.client.get(DETAIL_URL, {'id': str(invoice.pk)})
        self.assertContains(detail, '合同收费明细' if False else 'contract_line')
        reverse = self.client.post(REVERSE_URL, {
            'invoice_id': str(invoice.pk), 'expected_version': 1,
            'reason': '客户退回', 'reversal_date': self.today.isoformat(),
            'request_token': str(uuid.uuid4())})
        self.assertEqual(reverse.status_code, 302)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, 'reversed')

    def test_member_denied_and_auditor_read_only(self):
        self.login(self.plain)
        self.assertEqual(self.client.get(LIST_URL).status_code, 403)
        self.assertEqual(self.record(member=self.plain).status_code, 403)
        self.login(self.auditor)
        self.assertEqual(self.client.get(LIST_URL).status_code, 200)
        self.assertEqual(self.record(member=self.auditor).status_code, 403)

    def test_module_disabled_write_denied_read_kept(self):
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='invoicing',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.login(self.owner)
        self.assertEqual(self.client.get(LIST_URL).status_code, 200)
        self.assertEqual(self.record().status_code, 403)
