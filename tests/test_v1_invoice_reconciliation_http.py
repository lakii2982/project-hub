"""PH-325 read-only invoice reconciliation browser/HTTP boundaries."""
import uuid
from datetime import timedelta

from django.test import Client, TestCase
from django.utils import timezone

from core.models import PluginState
from pmo_plugins.invoicing.models import Invoice
from tests.test_v1_invoice import InvoiceFixture

URL = '/modules/invoicing/invoice-reconciliation/'


class InvoiceReconciliationHTTPTests(InvoiceFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.auditor = self.member('auditor')
        self.pm = self.member('pm')
        self.plain = self.member('member')

    def login(self, member):
        self.client.force_login(member.user)

    def test_owner_lists_and_opens_detail(self):
        self.login(self.owner)
        invoice, _ = self.allocated()
        row = Invoice.objects.get(pk=invoice['invoice_ref']['id'])
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, row.invoice_number)
        self.assertContains(response, '发票对账')
        detail = self.client.get(URL, {'invoice': str(row.pk)})
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, row.invoice_number)
        self.assertContains(detail, '含税/不含税/税额')

    def test_member_forbidden_and_auditor_read_only(self):
        self.login(self.plain)
        self.assertEqual(self.client.get(URL).status_code, 403)
        self.login(self.auditor)
        self.assertEqual(self.client.get(URL).status_code, 200)
        self.login(self.auditor)
        self.assertNotEqual(self.client.post(URL, {}).status_code, 302)

    def test_unknown_query_parameter_rejected(self):
        self.login(self.owner)
        response = self.client.get(URL, {'unexpected': '1'})
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '不允许的查询参数', status_code=400)

    def test_history_read_survives_disabled_module(self):
        self.login(self.owner)
        PluginState.objects.filter(organization=self.org, plugin_id='invoicing').update(
            enabled=False, updated_at=timezone.now())
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PluginState.objects.filter(
            organization=self.org, plugin_id='invoicing').get().enabled, False)

    def test_pm_sees_only_managed_project_and_cannot_open_foreign_invoice(self):
        from core.models import Project
        Project.objects.filter(pk=self.project.pk).update(manager=self.pm)
        self.login(self.pm)
        invoice, _ = self.allocated()
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, Invoice.objects.get(
            pk=invoice['invoice_ref']['id']).invoice_number)
        other = Project.objects.create(
            organization=self.org, name='外部项目', client='客户',
            code='P-X-' + uuid.uuid4().hex[:6], manager=self.owner,
            start_date=self.today, end_date=self.today + timedelta(days=30))
        self.assertNotContains(response, other.name)

    def test_get_has_no_write_side_effect(self):
        invoice, _ = self.allocated()
        from core.models import AuditEvent
        before = (Invoice.objects.count(), AuditEvent.objects.count())
        self.login(self.owner)
        self.client.get(URL, {'invoice': invoice['invoice_ref']['id']})
        self.assertEqual((Invoice.objects.count(), AuditEvent.objects.count()), before)
