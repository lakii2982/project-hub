"""PH-355 reminder page HTTP boundaries and read-only behavior."""
from django.test import Client, TestCase

from core.models import AuditEvent, DomainEvent, PluginState
from tests.test_v1_reminders import ReminderFixture

URL = '/reminders/'


class ReminderHTTPTests(ReminderFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def test_roles_and_empty_state(self):
        for member in (self.owner, self.finance, self.auditor, self.pm, self.plain):
            self.login(member)
            response = self.client.get(URL)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '到期提醒')
            self.assertContains(response, '外部发送 0')
            if member == self.plain:
                self.assertContains(response, '当前范围没有活动提醒')
            else:
                self.assertContains(response, '未清项逾期')

    def test_repeat_get_has_no_writes(self):
        self.login(self.owner)
        before = (AuditEvent.objects.count(), DomainEvent.objects.count())
        first, second = self.client.get(URL), self.client.get(URL)
        self.assertContains(first, first.context['snapshot']['scan_digest'])
        self.assertEqual(first.context['snapshot']['scan_digest'],
                         second.context['snapshot']['scan_digest'])
        self.assertEqual(before, (AuditEvent.objects.count(), DomainEvent.objects.count()))

    def test_invalid_filters_unknown_parameters_and_post(self):
        self.login(self.owner)
        for params in ({'as_of': 'bad'}, {'as_of': '2099-01-01'},
                       {'period': '2026-13'}, {'unexpected': '1'}):
            response = self.client.get(URL, params)
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, 'role="alert"', status_code=400)
        self.assertEqual(self.client.post(URL, {}).status_code, 405)
        self.assertEqual(Client().get(URL).status_code, 302)

    def test_disabled_source_shows_missing_not_new_reminder(self):
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='settlement',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.login(self.owner)
        response = self.client.get(URL)
        self.assertContains(response, 'module_disabled_history')
        self.assertNotContains(response, '未清项逾期')
