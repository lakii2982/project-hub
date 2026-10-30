"""PH-250 HTTP boundaries for bank-fee submit / independent approve / reject."""
import uuid

from django.test import Client, TestCase

from pmo_plugins.costing.models import BankCharge
from tests.test_v1_bank_charge import TOPIC
from tests.test_v1_bank_charge_http import BankChargeHTTPFixture, LIST, SAVE

ACTION = '/modules/costing/bank-charge-action/'


class BankChargeApprovalHTTPFixture(BankChargeHTTPFixture):
    def setUp(self):
        super().setUp()
        self.finance2 = self.member('finance')

    def approved_draft(self, member=None):
        self.login(member or self.finance)
        self.assertEqual(self.client.post(SAVE, self.data()).status_code, 302)
        return BankCharge.objects.get()

    def action(self, charge, action, *, member=None, decision=None, note='', **changes):
        self.login(member or self.owner)
        data = {'charge_id': str(charge.pk), 'expected_version': str(charge.version),
                'request_token': str(uuid.uuid4())}
        if decision is not None:
            data['decision'] = decision
        if note:
            data['note'] = note
        data.update(changes)
        return self.client.post(f'{ACTION}?id={charge.pk}&action={action}', data)


class BankChargeApprovalHTTPTests(BankChargeApprovalHTTPFixture, TestCase):
    def test_submit_then_independent_approve_posts_cost_and_shows_chain(self):
        charge = self.approved_draft(member=self.finance)
        response = self.action(charge, 'submit', member=self.finance)
        self.assertEqual(response.status_code, 302)
        charge.refresh_from_db()
        self.assertEqual(charge.status, 'pending')
        page = self.client.get(LIST, {'id': charge.pk})
        self.assertEqual(page.status_code, 200)
        self.assertIn('审批链与成本', page.content.decode())
        response = self.action(charge, 'approve', member=self.owner, decision='approve')
        self.assertEqual(response.status_code, 302)
        charge.refresh_from_db()
        self.assertEqual(charge.status, 'approved')
        self.assertEqual(charge.approved_by_id, self.owner.pk)
        detail = self.client.get(LIST, {'id': charge.pk}).content.decode()
        self.assertIn('已批准', detail)
        self.assertIn('成本金额（元）', detail)

    def test_self_approval_and_second_decision_have_no_effect(self):
        charge = self.approved_draft(member=self.finance)
        self.action(charge, 'submit', member=self.finance)
        # NOTE② regression: the creator/submitter must not be offered the approve action.
        own_view = self.client.get(LIST, {'id': charge.pk}).content.decode()
        self.assertNotIn('独立批准（另一负责人）', own_view)
        self.login(self.owner)
        self.assertIn('独立批准（另一负责人）',
                      self.client.get(LIST, {'id': charge.pk}).content.decode())
        self.login(self.finance)
        response = self.action(charge, 'approve', member=self.finance, decision='approve')
        self.assertEqual(response.status_code, 302)
        charge.refresh_from_db()
        self.assertEqual(charge.status, 'pending')
        self.action(charge, 'approve', member=self.owner, decision='approve')
        charge.refresh_from_db()
        response = self.action(charge, 'approve', member=self.finance2, decision='approve')
        self.assertEqual(response.status_code, 302)
        charge.refresh_from_db()
        self.assertEqual(charge.status, 'approved')

    def test_reject_requires_note_and_records_reason(self):
        charge = self.approved_draft()
        self.action(charge, 'submit', member=self.finance)
        response = self.action(charge, 'reject', member=self.owner, decision='reject')
        self.assertEqual(response.status_code, 302)
        charge.refresh_from_db()
        self.assertEqual(charge.status, 'pending')
        self.action(charge, 'reject', member=self.owner, decision='reject', note='依据不足')
        charge.refresh_from_db()
        self.assertEqual((charge.status, charge.reject_reason), ('rejected', '依据不足'))

    def test_roles_csrf_disabled_module_and_action_guards(self):
        charge = self.approved_draft()
        self.login(self.auditor)
        self.assertEqual(self.client.post(f'{ACTION}?id={charge.pk}&action=submit',
                                          {'charge_id': str(charge.pk),
                                           'expected_version': str(charge.version),
                                           'request_token': str(uuid.uuid4())}).status_code, 403)
        self.login(self.pm)
        self.assertEqual(self.client.get(LIST).status_code, 403)
        self.login(self.plain)
        self.assertEqual(self.client.get(LIST).status_code, 403)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner.user)
        self.assertEqual(protected.post(f'{ACTION}?id={charge.pk}&action=submit',
                                        {'charge_id': str(charge.pk),
                                         'expected_version': str(charge.version),
                                         'request_token': str(uuid.uuid4())}).status_code, 403)
        self.login(self.finance)
        self.assertEqual(self.client.post(f'{ACTION}?id={charge.pk}&action=unknown',
                                          {}).status_code, 403)
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'costing', False)
        self.login(self.owner)
        response = self.action(charge, 'submit', member=self.owner)
        self.assertEqual(response.status_code, 403)
        charge.refresh_from_db()
        self.assertEqual(charge.status, 'draft')
