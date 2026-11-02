"""PH-340 notification inbox HTTP boundaries."""
from django.test import Client, TestCase
from django.urls import set_script_prefix

from core import notification_services as notifications
from core.models import Notification, PluginState
from tests.test_v1_reminders import ReminderFixture

URL = '/notifications/'


class NotificationHTTPTests(ReminderFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def test_roles_read_personal_inbox_and_member_empty(self):
        for member in (self.owner, self.finance, self.auditor, self.pm, self.plain):
            self.login(member)
            response = self.client.get(URL)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '通知中心')
            self.assertContains(response, '外部发送 0')
            if member == self.plain:
                self.assertContains(response, '当前没有通知')

    def test_read_ignore_filters_and_idempotent_post(self):
        self.login(self.owner)
        self.client.post('/notifications/sync/')
        self.client.get(URL)
        row = Notification.objects.filter(member=self.owner).first()
        mark_url = f'/notifications/{row.pk}/read/'
        first = self.client.post(mark_url)
        second = self.client.post(mark_url)
        self.assertEqual((first.status_code, second.status_code), (302, 302))
        row.refresh_from_db()
        self.assertIsNotNone(row.read_at)
        self.assertContains(self.client.get(URL + '?status=read'), '已读')
        ignored = self.client.post(f'/notifications/{row.pk}/ignore/')
        self.assertEqual(ignored.status_code, 302)
        self.assertNotContains(self.client.get(URL), row.reminder_key)
        self.assertContains(self.client.get(URL + '?status=ignored'), row.title)

    def test_invalid_action_unknown_fields_get_mark_and_anonymous(self):
        self.login(self.owner)
        self.client.post('/notifications/sync/')
        self.client.get(URL)
        row = Notification.objects.filter(member=self.owner).first()
        self.assertEqual(self.client.get(f'/notifications/{row.pk}/read/').status_code, 405)
        self.assertEqual(self.client.post(f'/notifications/{row.pk}/approve/').status_code, 400)
        response = self.client.post(
            f'/notifications/{row.pk}/read/', {'source_ref': 'client-forged'})
        self.assertEqual(response.status_code, 400)
        row.refresh_from_db()
        self.assertIsNone(row.read_at)
        self.assertEqual(Client().get(URL).status_code, 302)

    def test_cross_member_post_is_denied(self):
        self.login(self.owner)
        self.client.post('/notifications/sync/')
        self.client.get(URL)
        row = Notification.objects.filter(member=self.owner).first()
        self.login(self.finance)
        response = self.client.post(f'/notifications/{row.pk}/read/')
        self.assertEqual(response.status_code, 403)
        row.refresh_from_db()
        self.assertIsNone(row.read_at)

    def test_revoked_scope_cannot_mark_hidden_notification(self):
        self.login(self.owner)
        self.client.post('/notifications/sync/')
        row = Notification.objects.filter(member=self.owner).first()
        self.owner.role = 'member'
        self.owner.save()
        response = self.client.post(f'/notifications/{row.pk}/read/')
        self.assertEqual(response.status_code, 403)
        row.refresh_from_db()
        self.assertIsNone(row.read_at)

    def test_auditor_read_only(self):
        self.login(self.owner)
        self.client.post('/notifications/sync/')
        row = Notification.objects.filter(member=self.owner).first()
        self.login(self.auditor)
        self.assertEqual(self.client.get(URL).status_code, 200)
        self.assertEqual(self.client.post('/notifications/sync/').status_code, 403)
        self.assertEqual(self.client.post(f'/notifications/{row.pk}/read/').status_code, 403)

    def test_disabled_source_keeps_history_and_prefix_links(self):
        self.login(self.owner)
        self.client.post('/notifications/sync/')
        self.client.get(URL)
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='settlement',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        response = self.client.get(URL)
        self.assertContains(response, '未清项逾期')
        set_script_prefix('/pmo/')
        self.addCleanup(set_script_prefix, '/')
        response = self.client.get(URL)
        self.assertContains(response, 'action="/pmo/notifications/"')
        self.assertContains(response, 'href="/pmo/modules/settlement/settlement-coverage-issues/"')
