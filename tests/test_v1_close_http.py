"""PH-322 close/reopen page and execution HTTP boundaries."""
import uuid

from django.test import Client, TestCase

from core.models import PluginState
from pmo_plugins.delivery.models import PeriodCloseRun, ProjectClose
from tests.test_v1_close import CloseFixture

PERIOD_URL = '/modules/delivery/period-close/'
PERIOD_EXEC = '/modules/delivery/period-close-execute/'
PROJECT_URL = '/modules/delivery/project-close/'
PROJECT_EXEC = '/modules/delivery/project-close-execute/'
PERIOD_REOPEN = '/modules/delivery/period-reopen/'


class CloseHTTPTests(CloseFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def test_owner_reads_period_precheck_and_closes(self):
        self.login(self.owner)
        page = self.client.get(PERIOD_URL, {'period': self.period})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '预检结果')
        self.assertContains(page, self.period)
        response = self.client.post(PERIOD_EXEC, {
            'period': self.period, 'expected_version': self.period_row.version,
            'request_token': str(uuid.uuid4()), 'reason': 'HTTP 关期'})
        self.assertEqual(response.status_code, 302)
        self.period_row.refresh_from_db()
        self.assertEqual(self.period_row.status, 'posted')
        self.assertEqual(PeriodCloseRun.objects.count(), 2)

    def test_project_page_and_close(self):
        self.login(self.owner)
        page = self.client.get(PROJECT_URL, {'project': str(self.project.pk)})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '预检结果')
        response = self.client.post(PROJECT_EXEC, {
            'project': str(self.project.pk), 'expected_version': self.project.version,
            'request_token': str(uuid.uuid4()), 'reason': 'HTTP 结项'})
        self.assertEqual(response.status_code, 302)
        self.project.refresh_from_db()
        self.assertEqual(self.project.stage, 'closed')
        self.assertEqual(ProjectClose.objects.count(), 2)

    def test_reopen_through_page(self):
        self.close_period()
        self.period_row.refresh_from_db()
        self.login(self.owner)
        response = self.client.post(PERIOD_REOPEN, {
            'period': self.period, 'expected_version': self.period_row.version,
            'request_token': str(uuid.uuid4()), 'reason': 'HTTP 重开'})
        self.assertEqual(response.status_code, 302)
        self.period_row.refresh_from_db()
        self.assertEqual(self.period_row.status, 'active')
        self.assertEqual(PeriodCloseRun.objects.order_by('sequence').last().state, 'reopened')

    def test_member_denied_auditor_read_only(self):
        self.login(self.plain)
        self.assertEqual(self.client.get(PERIOD_URL, {'period': self.period}).status_code, 403)
        self.assertEqual(self.client.post(PERIOD_EXEC, {}).status_code, 403)
        self.login(self.auditor)
        self.assertEqual(self.client.get(PERIOD_URL, {'period': self.period}).status_code, 200)
        self.assertEqual(self.client.post(PERIOD_EXEC, {}).status_code, 403)

    def test_module_disabled_read_kept_write_denied(self):
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='delivery',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.login(self.owner)
        self.assertEqual(self.client.get(PERIOD_URL, {'period': self.period}).status_code, 200)
        self.assertEqual(self.client.post(PERIOD_EXEC, {
            'period': self.period, 'expected_version': self.period_row.version,
            'request_token': str(uuid.uuid4())}).status_code, 403)
        self.assertEqual(PeriodCloseRun.objects.count(), 0)
