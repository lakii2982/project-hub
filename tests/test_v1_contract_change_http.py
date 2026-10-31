"""PH-265 contract change browser workflow boundaries."""
import uuid

from django.test import Client, TestCase
from django.utils import timezone

from core.models import DomainEvent
from pmo_plugins.contracts.models import Contract, ContractChange
from tests.test_v1_contract_change import ContractChangeFixture


class ContractChangeHTTPTests(ContractChangeFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.approved_contract()
        self.auditor = self.member('auditor')
        self.client = Client()

    def form(self, kind='termination', member=None):
        self.client.force_login((member or self.owner).user)
        return self.client.get('/modules/contracts/contract-change/',
                               {'contract': str(self.contract.pk), 'type': kind})

    def test_form_and_change_history_render_and_roles(self):
        self.assertEqual(self.form().status_code, 200)
        self.assertEqual(self.form(kind='price_change').status_code, 200)
        self.client.force_login(self.finance.user)
        self.assertEqual(self.client.get('/modules/contracts/contract-changes/',
                                         {'contract': str(self.contract.pk)}).status_code, 200)
        self.client.force_login(self.pm.user)
        self.assertEqual(self.client.get('/modules/contracts/contract-changes/',
                                         {'contract': str(self.contract.pk)}).status_code, 200)
        self.assertEqual(self.client.get('/modules/contracts/contract-change/',
                                         {'contract': str(self.contract.pk)}).status_code, 403)
        self.client.force_login(self.auditor.user)
        self.assertEqual(self.client.get('/modules/contracts/contract-changes/',
                                         {'contract': str(self.contract.pk)}).status_code, 200)

    def test_termination_flow_through_browser_and_independent_review(self):
        self.client.force_login(self.owner.user)
        csrf = self.client.cookies['csrftoken'].value if 'csrftoken' in self.client.cookies else ''
        page = self.form()
        self.assertContains(page, '合同变更草稿', status_code=200)
        response = self.client.post('/modules/contracts/contract-change-save/', {
            'contract_id': str(self.contract.pk), 'change_type': 'termination',
            'requested_on': self.today.isoformat(), 'request_token': str(uuid.uuid4()),
            'csrfmiddlewaretoken': csrf or page.context['csrf_token']})
        self.assertEqual(response.status_code, 302)
        change = ContractChange.objects.get(contract=self.contract, change_type='termination')
        # the creator cannot self-approve through the browser either
        self.client.force_login(self.owner.user)
        response = self.client.post('/modules/contracts/contract-change-review/', {
            'change_id': str(change.pk), 'expected_version': str(change.version),
            'decision': 'approved', 'note': '自审', 'request_token': str(uuid.uuid4()),
            'csrfmiddlewaretoken': csrf})
        self.assertEqual(response.status_code, 400)
        change.refresh_from_db()
        self.assertEqual(change.status, 'draft')
        self.client.force_login(self.finance.user)
        response = self.client.post('/modules/contracts/contract-change-review/', {
            'change_id': str(change.pk), 'expected_version': str(change.version),
            'decision': 'approved', 'note': '客户终止确认', 'request_token': str(uuid.uuid4()),
            'csrfmiddlewaretoken': csrf})
        self.assertEqual(response.status_code, 302)
        change.refresh_from_db()
        self.contract.refresh_from_db()
        self.assertEqual((change.status, self.contract.status), ('approved', 'terminated'))
        self.assertEqual(DomainEvent.objects.filter(topic='contracts.change_effective.v1').count(), 1)

    def test_csrf_unknown_fields_and_disabled_module_refuse_without_writes(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner.user)
        self.assertEqual(protected.post('/modules/contracts/contract-change-review/', {}).status_code, 403)
        self.client.force_login(self.owner.user)
        before = (ContractChange.objects.count(), DomainEvent.objects.count())
        response = self.client.post('/modules/contracts/contract-change-save/', {
            'contract_id': str(self.contract.pk), 'change_type': 'termination',
            'requested_on': self.today.isoformat(), 'request_token': str(uuid.uuid4()),
            'status': 'approved'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(before, (ContractChange.objects.count(), DomainEvent.objects.count()))
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'invoicing', False)
        for key in ('onboarding', 'management_revenue', 'settlement', 'delivery', 'time', 'contracts'):
            set_enabled(self.owner, key, False)
        response = self.client.post('/modules/contracts/contract-change-save/', {
            'contract_id': str(self.contract.pk), 'change_type': 'termination',
            'requested_on': self.today.isoformat(), 'request_token': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 403)
        self.client.force_login(self.member('member').user)
        self.assertEqual(self.client.get('/modules/contracts/contract-changes/',
                                         {'contract': str(self.contract.pk)}).status_code, 403)
