"""PH-284 revenue pairing browser boundaries."""
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase

from core.models import AuditEvent, DomainEvent, Project
from pmo_plugins.management_revenue.models import RevenueRecognition
from pmo_plugins.settlement.models import Settlement
from tests.test_v1_revenue_pairing_read import RevenuePairingFixture


class RevenuePairingHTTPTests(RevenuePairingFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.url = '/modules/management_revenue/revenue-pairing/'
        self.recognition(60000)
        settlement = self.settlement(40000)
        self.clearance(settlement, 30000)

    def test_page_renders_snapshot_and_issue_section(self):
        self.client.force_login(self.owner.user)
        page = self.client.get(self.url + '?project=' + str(self.project.pk))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '管理收入确认与结算/合同条款只读配对')
        self.assertContains(page, '收入/结算/合同配对（只读）')
        self.assertContains(page, '60000')
        self.assertContains(page, '40000')
        self.assertContains(page, '30000')
        self.assertContains(page, '配对异常（只读）')
        self.assertContains(page, '未发现收入/结算/合同条款配对异常')
        # Aggregate page shows the project row.
        aggregate = self.client.get(self.url)
        self.assertEqual(aggregate.status_code, 200)
        self.assertContains(aggregate, self.project.code)

    def test_denied_settlement_snapshot_renders_missing(self):
        from unittest import mock
        self.client.force_login(self.owner.user)
        with mock.patch(
                'pmo_plugins.management_revenue.pairing_read_services.'
                'receivable_services.settlement_snapshot',
                side_effect=PermissionDenied('结算快照不可读')):
            page = self.client.get(self.url + '?project=' + str(self.project.pk))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '缺失（结算读取被拒）')
        self.assertContains(page, 'settlement_unreadable')
        self.assertContains(page, '部分结算快照不可读')

    def test_roles_and_invalid_filters(self):
        self.client.force_login(self.auditor.user)
        self.assertEqual(self.client.get(self.url + '?project=' + str(self.project.pk)).status_code, 200)
        self.client.force_login(self.finance.user)
        self.assertEqual(self.client.get(self.url + '?project=' + str(self.project.pk)).status_code, 200)
        self.client.force_login(self.pm.user)
        self.assertEqual(self.client.get(self.url + '?project=' + str(self.project.pk)).status_code, 200)
        self.client.force_login(self.member('member').user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(self.owner.user)
        self.assertEqual(self.client.get(self.url + '?period=2026-13').status_code, 400)
        self.assertEqual(self.client.get(self.url + '?as_of=bad').status_code, 400)
        self.assertEqual(self.client.get(
            self.url + '?as_of=' + (self.today + timedelta(days=1)).isoformat()).status_code, 400)
        self.assertEqual(self.client.get(self.url + '?recognized_cents=1').status_code, 400)
        self.assertEqual(self.client.get(self.url + '?balance=1').status_code, 400)
        self.assertEqual(self.client.get(self.url + '?project=bogus').status_code, 400)

    def test_issue_rendering_disabled_module_and_no_writes(self):
        self.recognition(120000)
        before = (AuditEvent.objects.count(), DomainEvent.objects.count(),
                  RevenueRecognition.objects.count(), Settlement.objects.count())
        self.client.force_login(self.owner.user)
        page = self.client.get(self.url + '?project=' + str(self.project.pk))
        self.assertContains(page, 'recognized_over_contract_line')
        self.assertEqual(before, (AuditEvent.objects.count(), DomainEvent.objects.count(),
                                  RevenueRecognition.objects.count(), Settlement.objects.count()))
        from core.plugin_runtime import get_registry, set_enabled
        if 'management_revenue' in get_registry().manifests:
            set_enabled(self.owner, 'management_revenue', False)
        before = (AuditEvent.objects.count(), DomainEvent.objects.count(),
                  RevenueRecognition.objects.count(), Settlement.objects.count())
        self.client.force_login(self.auditor.user)
        page = self.client.get(self.url + '?project=' + str(self.project.pk))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '配对异常（只读）')
        response = self.client.post(self.url, {'recognized_cents': '1', 'balance': '1'})
        self.assertIn(response.status_code, (400, 403, 405))
        self.assertEqual(before, (AuditEvent.objects.count(), DomainEvent.objects.count(),
                                  RevenueRecognition.objects.count(), Settlement.objects.count()))

    def test_pm_other_project_scope_is_denied(self):
        other = Project.objects.create(organization=self.org, code='P284HTTP', name='越权项目',
                                       manager=self.owner, start_date=self.today, end_date=self.today)
        self.client.force_login(self.pm.user)
        self.assertEqual(self.client.get(self.url + '?project=' + str(other.pk)).status_code, 400)
