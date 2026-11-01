"""PH-339 insights HTTP boundaries: history_read pages and business_write actions."""
import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from core.models import Membership, Organization
from core.plugin_runtime import set_enabled
from pmo_plugins.insights import services
from pmo_plugins.insights.models import ReconciliationCase
from tests.test_v1_ops_reconciliation_overview import OpsOverviewFixture

RECONCILIATION = '/modules/insights/reconciliation/'
REFRESH = '/modules/insights/reconciliation-refresh/'
UPDATE = '/modules/insights/reconciliation-update/'
WORKBENCH = '/modules/insights/workbench/'
PRECHECK = '/modules/insights/period-precheck/'


class InsightsHTTPTests(OpsOverviewFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.cash(classify=False)
        self.period = self.today.strftime('%Y-%m')

    def login(self, member):
        self.client.force_login(member.user)

    def make_case(self):
        data = services.refresh_reconciliation_cases(
            self.owner, period=self.period, as_of=self.today.isoformat(),
            request_token=uuid.uuid4(), dry_run=False)
        return next(row for row in data['cases']
                    if row['rule_code'] == 'classification_missing')

    def test_owner_finance_auditor_get_history_pages(self):
        for member in (self.owner, self.finance, self.auditor):
            self.login(member)
            self.assertEqual(self.client.get(RECONCILIATION).status_code, 200)
            response = self.client.get(WORKBENCH, {'project': str(self.project.pk)})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'insights.project_workbench_summary/v1')
            self.assertContains(response, 'cash_project_scope_unavailable')
            self.assertContains(response, '缺失')
            self.assertEqual(self.client.get(PRECHECK).status_code, 200)

    def test_member_is_forbidden_everywhere(self):
        self.login(self.plain)
        self.assertEqual(self.client.get(RECONCILIATION).status_code, 403)
        self.assertEqual(self.client.get(WORKBENCH, {'project': str(self.project.pk)}).status_code, 403)
        self.assertEqual(self.client.get(PRECHECK).status_code, 403)

    def test_pm_only_managed_project(self):
        case = self.make_case()
        self.login(self.pm)
        listing = self.client.get(RECONCILIATION)
        self.assertEqual(listing.status_code, 200)
        self.assertNotContains(listing, case['case_key'])
        self.assertEqual(self.client.get(
            WORKBENCH, {'project': str(self.project.pk)}).status_code, 200)
        self.assertEqual(self.client.get(
            WORKBENCH, {'project': str(self.project2.pk)}).status_code, 403)

    def test_workbench_without_project_prompts_and_invalid_project_forbidden(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(WORKBENCH).status_code, 200)
        self.assertContains(self.client.get(WORKBENCH), '项目 ID')
        self.assertEqual(self.client.get(
            WORKBENCH, {'project': str(self.project2.pk)}).status_code, 200)

    def test_cross_org_project_forbidden(self):
        outsider = Organization.objects.create(name='PH339 HTTP 其他公司')
        owner = Membership.objects.create(
            organization=outsider,
            user=get_user_model().objects.create_user(username='ph339http' + uuid.uuid4().hex),
            role='owner')
        self.login(owner)
        self.assertEqual(self.client.get(
            WORKBENCH, {'project': str(self.project.pk)}).status_code, 403)

    def test_unknown_params_and_methods(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(RECONCILIATION, {'bogus': '1'}).status_code, 400)
        self.assertEqual(self.client.get(WORKBENCH, {'bogus': '1'}).status_code, 400)
        self.assertEqual(self.client.get(PRECHECK, {'bogus': '1'}).status_code, 400)
        self.assertEqual(self.client.get(REFRESH).status_code, 405)
        self.assertEqual(self.client.get(UPDATE).status_code, 405)
        self.assertEqual(self.client.post(RECONCILIATION).status_code, 405)

    def test_refresh_dry_run_then_confirm(self):
        self.login(self.owner)
        token = uuid.uuid4()
        preview = self.client.post(REFRESH, {
            'period': self.period, 'as_of': self.today.isoformat(),
            'request_token': str(token)})
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, '预演')
        self.assertEqual(ReconciliationCase.objects.count(), 0)
        written = self.client.post(REFRESH, {
            'period': self.period, 'as_of': self.today.isoformat(),
            'request_token': str(token), 'confirm': '1'}, follow=True)
        self.assertEqual(written.status_code, 200)
        case = ReconciliationCase.objects.get(rule_code='classification_missing')
        listing = self.client.get(RECONCILIATION, {'period': self.period})
        self.assertContains(listing, case.case_key)
        self.assertContains(listing, 'classification_missing')

    def test_update_case_through_post(self):
        case = self.make_case()
        self.login(self.finance)
        response = self.client.post(UPDATE, {
            'case_id': case['id'], 'decision': 'pending', 'note': '分派核对',
            'expected_version': case['version'], 'request_token': str(uuid.uuid4()),
            'confirm': '1'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(ReconciliationCase.objects.get(pk=case['id']).status,
                         'pending')

    def test_write_routes_reject_non_finance_roles(self):
        case = self.make_case()
        for member in (self.auditor, self.pm, self.plain):
            self.login(member)
            self.assertEqual(self.client.post(REFRESH, {
                'period': self.period, 'request_token': str(uuid.uuid4()),
                'confirm': '1'}).status_code, 403)
            self.assertEqual(self.client.post(UPDATE, {
                'case_id': case['id'], 'decision': 'pending', 'note': 'x',
                'expected_version': 1, 'request_token': str(uuid.uuid4()),
                'confirm': '1'}).status_code, 403)

    def test_invalid_refresh_and_update_payloads_are_400(self):
        case = self.make_case()
        self.login(self.owner)
        self.assertEqual(self.client.post(REFRESH, {
            'period': '13-13', 'request_token': str(uuid.uuid4())}).status_code, 400)
        self.assertEqual(self.client.post(REFRESH, {
            'period': self.period, 'request_token': 'bad'}).status_code, 400)
        self.assertEqual(self.client.post(UPDATE, {
            'case_id': case['id'], 'decision': 'resolved', 'note': 'x',
            'expected_version': 1, 'request_token': str(uuid.uuid4()),
            'confirm': '1'}).status_code, 400)
        self.assertEqual(self.client.post(UPDATE, {
            'case_id': case['id'], 'decision': 'approved', 'note': '',
            'expected_version': 1, 'request_token': str(uuid.uuid4()),
            'confirm': '1'}).status_code, 400)

    def test_disabled_module_keeps_history_read_and_blocks_writes(self):
        set_enabled(self.owner, 'insights', False)
        try:
            self.login(self.auditor)
            self.assertEqual(self.client.get(RECONCILIATION).status_code, 200)
            self.assertEqual(self.client.get(
                WORKBENCH, {'project': str(self.project.pk)}).status_code, 200)
            self.login(self.owner)
            self.assertEqual(self.client.post(REFRESH, {
                'period': self.period, 'request_token': str(uuid.uuid4()),
                'confirm': '1'}).status_code, 403)
        finally:
            set_enabled(self.owner, 'insights', True)
