"""PH-244 HTTP boundaries: draft-only browser workflow, roles, CSRF, validation, disabled module."""
import uuid

from django.test import Client, TestCase

from pmo_plugins.costing.models import BankCharge
from tests.test_v1_bank_charge import BankChargeFixture

LIST = '/modules/costing/bank-charge-list/'
FORM = '/modules/costing/bank-charge-form/'
SAVE = '/modules/costing/bank-charge-save/'


class BankChargeHTTPFixture(BankChargeFixture):
    def data(self, **changes):
        values = {'charge_id': '', 'cash_transaction_id': str(self.posted.pk), 'gross': '2.00',
                  'tax': '', 'counterparty': '', 'project': str(self.project.pk),
                  'scope_kind': 'project', 'fee_bearer': 'company', 'occurred_on': '',
                  'posting_period': '', 'evidence': '银行手续费回单', 'expected_version': '',
                  'request_token': str(uuid.uuid4())}
        values.update(changes)
        return values

    def login(self, member):
        self.client.force_login(member.user)


class BankChargeHTTPTests(BankChargeHTTPFixture, TestCase):
    def test_owner_and_finance_save_draft_and_read_detail_reconciliation(self):
        self.login(self.finance)
        response = self.client.post(SAVE, self.data())
        self.assertEqual(response.status_code, 302)
        charge = BankCharge.objects.get()
        self.assertEqual((charge.status, charge.version, charge.gross_cents), ('draft', 1, 200))
        self.assertEqual(self.client.get(LIST).status_code, 200)
        detail = self.client.get(LIST, {'id': charge.pk})
        self.assertEqual(detail.status_code, 200)
        content = detail.content.decode()
        self.assertIn('净额 / 手续费对账', content)
        self.assertIn('未批准手续费不计入对账', content)
        self.assertIn('未批准草稿', content)
        self.login(self.owner)
        self.assertEqual(self.client.get(FORM).status_code, 200)

    def test_roles_csrf_and_disabled_module(self):
        self.login(self.auditor)
        self.assertEqual(self.client.get(LIST).status_code, 200)
        self.assertEqual(self.client.post(SAVE, self.data()).status_code, 403)
        self.assertEqual(self.client.get(FORM).status_code, 403)
        self.login(self.pm)
        self.assertEqual(self.client.get(LIST).status_code, 403)
        self.login(self.plain)
        self.assertEqual(self.client.get(LIST).status_code, 403)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner.user)
        self.assertEqual(protected.post(SAVE, self.data()).status_code, 403)
        self.login(self.owner)
        self.assertEqual(self.client.post(SAVE, self.data()).status_code, 302)
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'costing', False)
        self.assertEqual(self.client.post(SAVE, self.data()).status_code, 403)
        # Disabled modules refuse browser routes like cash; history stays readable through the
        # owner service read path (covered by the service test).
        self.login(self.auditor)
        self.assertEqual(self.client.get(LIST).status_code, 403)

    def test_form_rejects_approval_fields_and_bad_amounts_without_writes(self):
        self.login(self.owner)
        cases = [{'status': 'approved'}, {'approved_by_member_id': '1'},
                 {'gross': '0'}, {'gross': '-1'}, {'gross': 'abc'},
                 {'gross': '1000000000001'}, {'tax': '-1'}, {'scope_kind': 'nonproject'},
                 {'fee_bearer': 'counterparty'}, {'existing_cost_ref': 'x'},
                 {'evidence': ''}, {'posting_period': '2026-1'}]
        for changes in cases:
            with self.subTest(changes=changes):
                response = self.client.post(SAVE, self.data(**changes))
                self.assertEqual(response.status_code, 400)
        self.assertEqual(BankCharge.objects.count(), 0)

    def test_draft_cash_and_foreign_cash_rejected_via_http(self):
        self.login(self.owner)
        draft = self.tx(gross_cents=500)
        response = self.client.post(SAVE, self.data(cash_transaction_id=str(draft.pk)))
        self.assertEqual(response.status_code, 400)
        foreign = self.foreign_posted_cash()
        response = self.client.post(SAVE, self.data(cash_transaction_id=str(foreign.pk)))
        self.assertIn(response.status_code, (400, 403))
        self.assertEqual(BankCharge.objects.count(), 0)

    def test_edit_flow_versions_same_charge_and_replay_is_idempotent(self):
        self.login(self.owner)
        token = str(uuid.uuid4())
        self.assertEqual(self.client.post(SAVE, self.data(request_token=token)).status_code, 302)
        charge = BankCharge.objects.get()
        form = self.client.get(FORM, {'id': charge.pk})
        self.assertEqual(form.status_code, 200)
        self.assertIn('修改手续费草稿', form.content.decode())
        edited = self.client.post(SAVE, self.data(
            charge_id=str(charge.pk), expected_version='1', gross='1.50',
            request_token=str(uuid.uuid4())))
        self.assertEqual(edited.status_code, 302)
        charge.refresh_from_db()
        self.assertEqual((charge.version, charge.gross_cents), (2, 150))
        self.assertEqual(BankCharge.objects.count(), 1)
        # same token + same input replay keeps one draft and one result
        replay = self.client.post(SAVE, self.data(request_token=token))
        self.assertEqual(replay.status_code, 302)
        self.assertEqual(BankCharge.objects.count(), 1)
