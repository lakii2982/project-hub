"""PH-276 reversal browser boundaries and read-only pairing page."""
import uuid

from django.test import Client, TestCase

from core.models import AuditEvent, DomainEvent
from pmo_plugins.time.models import BillableConfirmation
from tests.test_v1_billable_reversal import ReversalFixture


class BillableReversalHTTPTests(ReversalFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.row = self.confirmed()
        self.detail_url = '/modules/time/billable-confirmation-detail/'
        self.reverse_url = '/modules/time/billable-confirmation-reverse/'

    def detail(self, confirmation_id=None):
        return self.detail_url + '?id=' + str(confirmation_id or self.row['id'])

    def test_detail_page_renders_facts_and_read_only_pairing(self):
        self.client.force_login(self.owner.user)
        page = self.client.get(self.detail('/'.join(str(self.row['id']).split('-'))))
        page = self.client.get(self.detail())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '确认事实')
        self.assertContains(page, '收入/结算只读配对')
        self.assertContains(page, 'unpaired')
        self.client.force_login(self.auditor.user)
        self.assertEqual(self.client.get(self.detail()).status_code, 200)
        self.client.force_login(self.pm.user)
        self.assertEqual(self.client.get(self.detail()).status_code, 200)
        self.client.force_login(self.member('member').user)
        self.assertEqual(self.client.get(self.detail()).status_code, 403)
        self.client.force_login(self.owner.user)
        self.assertEqual(self.client.get(self.detail(uuid.uuid4())).status_code, 403)

    def test_reverse_post_independent_member_and_list_state(self):
        self.client.force_login(self.reverser.user)
        response = self.client.post(self.reverse_url, {
            'confirmation_id': str(self.row['id']), 'expected_version': '2',
            'request_token': str(uuid.uuid4()), 'reason': '客户书面撤回'})
        self.assertEqual(response.status_code, 302)
        row = BillableConfirmation.objects.get(pk=self.row['id'])
        self.assertEqual((row.status, row.version), ('reversed', 3))
        listing = self.client.get('/modules/time/billable-confirmation-list/')
        self.assertContains(listing, '详情/配对')
        self.assertContains(listing, '已撤销')

    def test_creator_and_invalid_requests_leave_state(self):
        self.client.force_login(self.owner.user)
        before = BillableConfirmation.objects.get(pk=self.row['id'])
        response = self.client.post(self.reverse_url, {
            'confirmation_id': str(self.row['id']), 'expected_version': '2',
            'request_token': str(uuid.uuid4()), 'reason': '自撤'})
        self.assertEqual(response.status_code, 302)
        after = BillableConfirmation.objects.get(pk=self.row['id'])
        self.assertEqual((after.status, after.version), (before.status, before.version))
        response = self.client.post(self.reverse_url, {
            'confirmation_id': str(self.row['id']), 'expected_version': '2',
            'request_token': str(uuid.uuid4()), 'reason': ''})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(BillableConfirmation.objects.get(pk=self.row['id']).status, 'confirmed')

    def test_csrf_disabled_module_and_no_partial_writes(self):
        from django.test import Client as ProtectedClient
        protected = ProtectedClient(enforce_csrf_checks=True)
        protected.force_login(self.reverser.user)
        self.assertEqual(protected.post(self.reverse_url, {
            'confirmation_id': str(self.row['id']), 'expected_version': '2',
            'request_token': str(uuid.uuid4()), 'reason': '无 token'}).status_code, 403)
        from core.plugin_runtime import get_registry, set_enabled
        for plugin_id in ('onboarding', 'time', 'management_revenue', 'settlement', 'delivery', 'contracts'):
            if plugin_id in get_registry().manifests:
                set_enabled(self.owner, plugin_id, False)
        before = (AuditEvent.objects.count(), DomainEvent.objects.count(),
                  BillableConfirmation.objects.get(pk=self.row['id']).status)
        self.client.force_login(self.auditor.user)
        self.assertEqual(self.client.get(self.detail()).status_code, 200)
        self.client.force_login(self.reverser.user)
        self.assertEqual(self.client.post(self.reverse_url, {
            'confirmation_id': str(self.row['id']), 'expected_version': '2',
            'request_token': str(uuid.uuid4()), 'reason': '停用后'}).status_code, 403)
        self.assertEqual(before, (AuditEvent.objects.count(), DomainEvent.objects.count(),
                                  BillableConfirmation.objects.get(pk=self.row['id']).status))

    def test_extra_client_fields_do_not_override_status_or_amounts(self):
        self.client.force_login(self.reverser.user)
        response = self.client.post(self.reverse_url, {
            'confirmation_id': str(self.row['id']), 'expected_version': '2',
            'request_token': str(uuid.uuid4()), 'reason': '客户撤回',
            'status': 'confirmed', 'confirmed_minutes': '999', 'income_cents': '1'})
        self.assertEqual(response.status_code, 302)
        row = BillableConfirmation.objects.get(pk=self.row['id'])
        self.assertEqual((row.status, row.version, row.confirmed_minutes),
                         ('reversed', 3, 360))
