"""PH-340 internal notification snapshots, idempotency and member boundaries."""
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from core import notification_services as notifications
from core.models import AuditEvent, DomainEvent, LedgerEntry, Notification, PluginState
from tests.test_v1_reminders import ReminderFixture


class NotificationServiceTests(ReminderFixture, TestCase):
    def test_sync_is_idempotent_and_has_no_business_or_external_writes(self):
        before = (AuditEvent.objects.count(), DomainEvent.objects.count(),
                  LedgerEntry.objects.count())
        first = notifications.sync_notifications(self.owner)
        initial_count = Notification.objects.count()
        second = notifications.sync_notifications(self.owner)
        self.assertGreater(initial_count, 0)
        self.assertEqual(first['total'], initial_count)
        self.assertEqual(second['created'], 0)
        self.assertEqual(second['total'], initial_count)
        self.assertEqual(before, (AuditEvent.objects.count(), DomainEvent.objects.count(),
                                  LedgerEntry.objects.count()))
        with mock.patch('core.plugin_runtime.dispatch_pending') as dispatch:
            notifications.sync_notifications(self.owner)
        dispatch.assert_not_called()

    def test_read_and_ignore_are_idempotent_personal_state(self):
        notifications.sync_notifications(self.owner)
        row = Notification.objects.filter(member=self.owner).first()
        read = notifications.mark_notification(self.owner, row.pk, action='read')
        self.assertIsNotNone(read.read_at)
        self.assertEqual(read.read_at, notifications.mark_notification(
            self.owner, row.pk, action='read').read_at)
        ignored = notifications.mark_notification(self.owner, row.pk, action='ignore')
        self.assertIsNotNone(ignored.ignored_at)
        self.assertEqual(ignored.ignored_at, notifications.mark_notification(
            self.owner, row.pk, action='ignore').ignored_at)
        default_ids = {item.pk for item in notifications.notification_snapshot(self.owner)}
        self.assertNotIn(row.pk, default_ids)
        history_ids = {item.pk for item in notifications.notification_snapshot(
            self.owner, include_ignored=True)}
        self.assertIn(row.pk, history_ids)

    def test_history_survives_fact_resolution_without_resync_duplicate(self):
        notifications.sync_notifications(self.owner)
        initial = Notification.objects.count()
        initial_keys = set(Notification.objects.values_list('reminder_key', flat=True))
        from pmo_plugins.settlement.models import Clearance
        Clearance.objects.create(
            organization=self.org, open_item=self.open_item,
            cash_transaction_id=__import__('uuid').uuid4(), cash_transaction_ref={},
            cleared_cents=self.open_item.gross_cents, occurred_on=self.today,
            status='active', created_by=self.owner)
        notifications.sync_notifications(self.owner)
        final_keys = list(Notification.objects.values_list('reminder_key', flat=True))
        self.assertEqual(len(final_keys), len(set(final_keys)))
        self.assertTrue(initial_keys <= set(final_keys))
        self.assertTrue(notifications.notification_snapshot(self.owner))

    def test_disabled_source_keeps_history_but_adds_no_new_snapshot(self):
        notifications.sync_notifications(self.owner)
        initial = Notification.objects.count()
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='settlement',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        result = notifications.sync_notifications(self.owner)
        self.assertEqual(result['created'], 0)
        self.assertEqual(Notification.objects.count(), initial)
        self.assertTrue(notifications.notification_snapshot(self.owner))

    def test_revoked_member_and_cross_member_state_are_denied(self):
        notifications.sync_notifications(self.owner)
        row = Notification.objects.filter(member=self.owner).first()
        self.owner.active = False
        self.owner.save()
        with self.assertRaises(PermissionDenied):
            notifications.sync_notifications(self.owner)
        with self.assertRaises(PermissionDenied):
            notifications.notification_snapshot(self.owner)
        self.owner.active = True
        self.owner.save()
        with self.assertRaises(PermissionDenied):
            notifications.mark_notification(self.finance, row.pk, action='read')

    def test_invalid_action_and_client_supplied_key_are_not_accepted(self):
        notifications.sync_notifications(self.owner)
        row = Notification.objects.filter(member=self.owner).first()
        with self.assertRaises(Exception):
            notifications.mark_notification(self.owner, row.pk, action='approve')
        row.refresh_from_db()
        self.assertIsNone(row.read_at)
        with self.assertRaises(Exception):
            notifications.mark_notification(
                self.owner, row.pk, action='read', reminder_key='client-forged')

    def test_scan_due_sync_is_idempotent(self):
        from io import StringIO
        import json
        from django.core.management import call_command
        first, second = StringIO(), StringIO()
        call_command('scan_due', member=self.owner.pk, sync=True, stdout=first)
        call_command('scan_due', member=self.owner.pk, sync=True, stdout=second)
        first_result, second_result = json.loads(first.getvalue()), json.loads(second.getvalue())
        self.assertEqual(first_result['notifications_sent'], 0)
        self.assertGreater(first_result['total'], 0)
        self.assertEqual(second_result['created'], 0)
        self.assertEqual(first_result['total'], second_result['total'])


from django.db import close_old_connections, connection
from django.test import TransactionTestCase
from threading import Barrier, Thread
from unittest import skipUnless


@skipUnless(connection.vendor == 'postgresql',
            'Requires PostgreSQL independent connections; SQLite cannot prove locking')
class NotificationConcurrencyTests(ReminderFixture, TransactionTestCase):
    def test_concurrent_sync_keeps_one_row_per_member_and_key(self):
        barrier = Barrier(2)
        results = [None, None]

        def run(index):
            close_old_connections()
            try:
                from core.models import Membership
                member = Membership.objects.get(pk=self.owner.pk)
                barrier.wait(timeout=10)
                notifications.sync_notifications(member)
                results[index] = 'ok'
            except Exception as error:
                results[index] = type(error).__name__
            finally:
                close_old_connections()

        threads = [Thread(target=run, args=(index,)) for index in (0, 1)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(results, ['ok', 'ok'])
        keys = list(Notification.objects.filter(member=self.owner)
                    .values_list('reminder_key', flat=True))
        self.assertEqual(len(keys), len(set(keys)))


class NotificationReadScopeRegressionTests(ReminderFixture, TestCase):
    def test_role_and_pm_revocation_hide_existing_history(self):
        notifications.sync_notifications(self.owner)
        owner_row = Notification.objects.filter(member=self.owner).first()
        self.owner.role = 'member'
        self.owner.save()
        self.assertEqual(list(notifications.notification_snapshot(self.owner)), [])
        with self.assertRaises(PermissionDenied):
            notifications.mark_notification(self.owner, owner_row.pk, action='read')

        notifications.sync_notifications(self.pm)
        pm_row = Notification.objects.filter(member=self.pm).first()
        from core.models import Project
        Project.objects.filter(pk=self.project.pk).update(manager=self.owner)
        self.assertEqual(list(notifications.notification_snapshot(self.pm)), [])
        with self.assertRaises(PermissionDenied):
            notifications.mark_notification(self.pm, pm_row.pk, action='ignore')

    def test_auditor_cannot_sync_or_mark(self):
        with self.assertRaises(PermissionDenied):
            notifications.sync_notifications(self.auditor)
        notifications.sync_notifications(self.owner)
        row = Notification.objects.filter(member=self.owner).first()
        with self.assertRaises(PermissionDenied):
            notifications.mark_notification(self.auditor, row.pk, action='read')
        row.refresh_from_db()
        self.assertIsNone(row.read_at)

    def test_resolved_fact_leaves_unread_list_but_remains_history(self):
        notifications.sync_notifications(self.owner)
        row = Notification.objects.filter(
            member=self.owner, issue_code='settlement_open_item_overdue').first()
        from pmo_plugins.settlement.models import Clearance
        Clearance.objects.create(
            organization=self.org, open_item=self.open_item,
            cash_transaction_id=__import__('uuid').uuid4(), cash_transaction_ref={},
            cleared_cents=self.open_item.gross_cents, occurred_on=self.today,
            status='active', created_by=self.owner)
        current = list(notifications.notification_snapshot(self.owner))
        self.assertNotIn(row.pk, {item.pk for item in current})
        history = list(notifications.notification_snapshot(self.owner, include_ignored=True))
        resolved = next(item for item in history if item.pk == row.pk)
        self.assertFalse(resolved.source_active)
        self.assertTrue(resolved.source_available)

    def test_notification_database_rejects_cross_org_member(self):
        from django.db import DatabaseError
        other = ReminderFixture()
        other.setUp()
        with self.assertRaises(DatabaseError):
            Notification.objects.create(
                organization=other.org, member=self.owner, reminder_key='e' * 64,
                reminder_ref={}, source_ref={}, project_ref=None,
                issue_code='budget_missing', severity='warning', title='跨组织',
                summary='拒绝', due_on=None, as_of=self.today, deeplink='/')
