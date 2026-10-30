"""PH-269 time billable-confirmation browser boundaries."""
import json
import uuid

from django.test import Client, TestCase

from pmo_plugins.time.models import BillableConfirmation
from tests.test_v1_billable_confirmation import TimeBillableFixture


class BillableConfirmationHTTPTests(TimeBillableFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def data(self, **changes):
        values = {'timesheet_id': str(self.timesheet.pk),
                  'contract_line_id': self.contract_line_id(),
                  'confirmed_minutes': '360',
                  'confirmed_on': self.today.isoformat(),
                  'evidence': json.dumps([{'kind': 'customer_email', 'reference': '客户确认邮件'}]),
                  'request_token': str(uuid.uuid4())}
        values.update(changes)
        return values

    def contract_line_id(self):
        return self.line_ref['id']

    def test_list_form_and_roles(self):
        self.client.force_login(self.owner.user)
        self.assertEqual(self.client.get('/modules/time/billable-confirmation-list/').status_code, 200)
        self.assertEqual(self.client.get('/modules/time/billable-confirmation-form/').status_code, 200)
        self.client.force_login(self.auditor.user)
        self.assertEqual(self.client.get('/modules/time/billable-confirmation-list/').status_code, 200)
        self.assertEqual(self.client.get('/modules/time/billable-confirmation-form/').status_code, 403)
        self.client.force_login(self.pm.user)
        self.assertEqual(self.client.get('/modules/time/billable-confirmation-list/').status_code, 200)
        self.client.force_login(self.member('member').user)
        self.assertEqual(self.client.get('/modules/time/billable-confirmation-list/').status_code, 403)

    def test_save_then_independent_confirm(self):
        self.client.force_login(self.owner.user)
        response = self.client.post('/modules/time/billable-confirmation-save/', self.data())
        self.assertEqual(response.status_code, 302)
        row = BillableConfirmation.objects.get()
        self.assertEqual((row.status, row.confirmed_minutes), ('draft', 360))
        self.client.force_login(self.finance.user)
        response = self.client.post('/modules/time/billable-confirmation-confirm/', {
            'confirmation_id': str(row.pk), 'expected_version': str(row.version),
            'request_token': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.status, 'confirmed')
        self.assertEqual(row.confirmed_by_member_id, self.finance.pk)
        page = self.client.get('/modules/time/billable-confirmation-list/')
        self.assertContains(page, '客户已确认')

    def test_csrf_roles_and_disabled_module(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner.user)
        self.assertEqual(protected.post('/modules/time/billable-confirmation-save/', self.data()).status_code, 403)
        self.client.force_login(self.member('member').user)
        self.assertEqual(self.client.post('/modules/time/billable-confirmation-save/', self.data()).status_code, 403)
        self.client.force_login(self.auditor.user)
        self.assertEqual(self.client.post('/modules/time/billable-confirmation-save/', self.data()).status_code, 403)
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'time', False)
        self.client.force_login(self.owner.user)
        self.assertEqual(self.client.post('/modules/time/billable-confirmation-save/', self.data()).status_code, 403)

    def test_unknown_or_derived_fields_are_not_mass_assigned(self):
        self.client.force_login(self.owner.user)
        response = self.client.post('/modules/time/billable-confirmation-save/',
                                    self.data(status='confirmed', confirmed_by_member=str(self.owner.pk)))
        self.assertEqual(response.status_code, 302)
        row = BillableConfirmation.objects.get()
        self.assertEqual(row.status, 'draft')
        self.assertIsNone(row.confirmed_by_member_id)
