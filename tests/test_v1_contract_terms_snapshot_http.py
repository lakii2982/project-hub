"""PH-287 read-only terms snapshot browser boundaries."""
import uuid
from datetime import timedelta

from django.test import Client, TestCase

from core.models import AuditEvent, DomainEvent, Project
from tests.test_v1_contract_terms_snapshot import TermsSnapshotFixture


class TermsSnapshotHTTPTests(TermsSnapshotFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.snapshot_url = '/modules/contracts/contract-terms-snapshot/'

    def url(self, **params):
        query = '&'.join(f'{key}={value}' for key, value in params.items() if value is not None)
        return self.snapshot_url + ('?' + query if query else '')

    def test_snapshot_page_renders_terms_changes_and_issues(self):
        self.settlement(50000)
        self.client.force_login(self.owner.user)
        page = self.client.get(self.url(contract=self.contract.pk))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '合同条款快照与结算核对')
        self.assertContains(page, self.contract.contract_code)
        self.assertContains(page, '批准版本')
        self.assertContains(page, 'confirmed_time')
        self.assertContains(page, '结算核对异常')
        self.assertContains(page, '未发现条款/结算核对异常')
        # as_of filter is read-only and keeps the approved snapshot.
        history = self.client.get(self.url(contract=self.contract.pk,
                                           as_of=(self.today - timedelta(days=3)).isoformat()))
        self.assertEqual(history.status_code, 200)

    def test_roles_scope_and_invalid_filters(self):
        self.client.force_login(self.auditor.user)
        self.assertEqual(self.client.get(self.url(contract=self.contract.pk)).status_code, 200)
        self.client.force_login(self.pm.user)
        self.assertEqual(self.client.get(self.url(contract=self.contract.pk)).status_code, 200)
        other = Project.objects.create(organization=self.org, code='P287HTTP', name='越权项目',
                                       manager=self.owner, start_date=self.today, end_date=self.today)
        other_contract = self.make_contract(project_id=other.pk, contract_code='C287HTTP')
        self.assertEqual(self.client.get(self.url(contract=other_contract.pk)).status_code, 400)
        self.client.force_login(self.member('member').user)
        self.assertEqual(self.client.get(self.url(contract=self.contract.pk)).status_code, 403)
        self.client.force_login(self.owner.user)
        self.assertEqual(self.client.get(self.url(contract=self.contract.pk,
                                                  as_of=(self.today + timedelta(days=1)).isoformat())).status_code, 400)
        self.assertEqual(self.client.get(self.url(contract=self.contract.pk, as_of='not-a-date')).status_code, 400)
        self.assertEqual(self.client.get(self.url(contract=uuid.uuid4())).status_code, 400)
        self.assertEqual(self.client.get(self.url(contract=self.contract.pk, balance='1')).status_code, 400)
        self.assertEqual(self.client.get(self.url(contract=self.contract.pk, terms='fabricated')).status_code, 400)

    def test_issues_render_and_disabled_module_history_read(self):
        self.settlement(10000, with_items=False, status='active')
        self.settlement(20000, with_items=False, status='active')
        self.client.force_login(self.owner.user)
        page = self.client.get(self.url(contract=self.contract.pk))
        self.assertContains(page, 'line_repeated_consumption')
        self.assertContains(page, 'settlement_partition_mismatch')
        from core.plugin_runtime import get_registry, set_enabled
        for plugin_id in ('onboarding', 'time', 'management_revenue', 'settlement', 'delivery', 'contracts'):
            if plugin_id in get_registry().manifests:
                set_enabled(self.owner, plugin_id, False)
        self.client.force_login(self.auditor.user)
        page = self.client.get(self.url(contract=self.contract.pk))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '合同条款快照与结算核对')

    def test_post_and_client_amounts_do_not_write_facts(self):
        self.client.force_login(self.owner.user)
        before = (AuditEvent.objects.count(), DomainEvent.objects.count(),
                  self.facts()[1], self.facts()[2])
        response = self.client.post(self.snapshot_url, {'contract': str(self.contract.pk),
                                                        'terms': 'fabricated', 'balance': '1'})
        self.assertIn(response.status_code, (400, 403, 405))
        self.assertEqual(before, (AuditEvent.objects.count(), DomainEvent.objects.count(),
                                  self.facts()[1], self.facts()[2]))
