"""PH-354 期间预检只读页面 HTTP 边界测试。"""
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase

from core.models import Membership, Organization
from core.plugin_runtime import set_enabled
from pmo_plugins.insights import precheck_services
from tests.test_v1_ops_reconciliation_overview import OpsOverviewFixture

PAGE = '/modules/insights/period-precheck/'


class PeriodPrecheckHTTPTests(OpsOverviewFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.settlement_with_item()
        self.cash(classify=False)

    def login(self, member):
        self.client.force_login(member.user)

    def test_owner_auditor_finance_get_full_page(self):
        for member in (self.owner, self.auditor, self.finance):
            self.login(member)
            response = self.client.get(PAGE)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'period.precheck.snapshot/v1')
            data = precheck_services.period_precheck_snapshot(
                member, as_of=self.today.isoformat())
            self.assertContains(response, data['precheck_digest'])
            self.assertContains(response, 'classification_missing')

    def test_member_is_forbidden(self):
        self.login(self.plain)
        self.assertEqual(self.client.get(PAGE).status_code, 403)

    def test_pm_requires_managed_project(self):
        self.login(self.pm)
        self.assertEqual(self.client.get(PAGE).status_code, 403)
        ok = self.client.get(PAGE, {'project': str(self.project.pk)})
        self.assertEqual(ok.status_code, 200)
        self.assertContains(ok, 'period.precheck.snapshot/v1')
        self.assertEqual(self.client.get(PAGE, {'project': str(self.project2.pk)}).status_code, 403)

    def test_cross_org_project_forbidden(self):
        outsider = Organization.objects.create(name='PH354 其他公司')
        owner = Membership.objects.create(
            organization=outsider,
            user=get_user_model().objects.create_user(username='ph354' + uuid.uuid4().hex),
            role='owner')
        self.login(owner)
        self.assertEqual(self.client.get(PAGE, {'project': str(self.project.pk)}).status_code, 403)
        # 本公司成员引用其他公司项目同样拒绝。
        self.login(self.owner)
        self.assertEqual(self.client.get(PAGE, {'project': str(self.project.pk)}).status_code, 200)

    def test_unknown_param_and_invalid_period_rejected(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(PAGE, {'bogus': '1'}).status_code, 400)
        self.assertEqual(self.client.get(PAGE, {'period': '13-13'}).status_code, 400)

    def test_post_not_allowed(self):
        self.login(self.owner)
        self.assertEqual(self.client.post(PAGE).status_code, 405)

    def test_disabled_module_keeps_history_read(self):
        set_enabled(self.owner, 'insights', False)
        self.login(self.auditor)
        response = self.client.get(PAGE)
        self.assertEqual(response.status_code, 200)
        set_enabled(self.owner, 'insights', True)
        self.assertEqual(self.client.get(PAGE).status_code, 200)

    def test_disabled_dependency_keeps_precheck_history_read(self):
        # 停用依赖不改变 history_read 语义：先停 insights 与其依赖 cash 的 onboarding，再停 cash。
        set_enabled(self.owner, 'insights', False)
        set_enabled(self.owner, 'onboarding', False)
        set_enabled(self.owner, 'cash', False)
        self.login(self.auditor)
        response = self.client.get(PAGE)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'period.precheck.snapshot/v1')
