"""PostgreSQL multi-connection races for settlement approval; SQLite is explicitly not evidence."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Thread
import time
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, close_old_connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone
from unittest import skipUnless

from core.models import (AccountingPeriod, DomainEvent, Membership, Organization, PluginState,
                         SourceConsumption)
from core.services import lock_company
from pmo_plugins.settlement import receivable_services as settlement
from pmo_plugins.settlement.models import OpenItem, Settlement, SettlementReceipt
from tests.test_v1_settlement import SettlementFixture


@skipUnless(connection.vendor == 'postgresql', 'Requires real PostgreSQL independent connections')
class SettlementConcurrencyTests(SettlementFixture, TransactionTestCase):
    @classmethod
    def tearDownClass(cls):
        # Worker threads keep thread-local psycopg connections; close them before destroy_test_db.
        import gc
        from django.db import connections
        connections.close_all()
        gc.collect()
        super().tearDownClass()

    def run_race(self, *actions):
        barrier = Barrier(len(actions))

        def call(action):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                try:
                    return ('ok', action())
                except (ValidationError, PermissionDenied) as error:
                    return ('rejected', type(error).__name__)
            finally:
                close_old_connections()
                connection.close()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return list(pool.map(call, actions))

    def finance_member(self):
        return Membership.objects.get(pk=self.finance.pk)

    def approver(self, row, *, token=None, note='并发独立核对'):
        def action():
            return settlement.review_settlement(self.finance_member(), row.pk,
                expected_version=row.version, request_token=token or uuid.uuid4(),
                decision='approve', note=note, dry_run=False).pk
        return action

    def test_two_approvals_same_line_serialize_and_conserve(self):
        self.approve_golden()
        self.acceptance(0, 9000)
        first = self.submit(self.save(self.term_values('payment_term')[:1]))
        second = self.submit(self.save(self.term_values('payment_term')[1:2]))
        results = self.run_race(self.approver(first), self.approver(second))
        self.assertEqual([item[0] for item in results], ['ok', 'ok'], results)
        active = Settlement.objects.filter(status='active')
        self.assertEqual(active.count(), 2)
        self.assertEqual(sum(active.values_list('net_cents', flat=True)), 90000)
        self.assertEqual(OpenItem.objects.count(), 2)
        self.assertEqual(SourceConsumption.objects.filter(consumer='settlement').count(), 2)
        self.assertEqual(DomainEvent.objects.filter(
            topic='settlement.open_item_created.v1').count(), 2)

    def test_same_term_two_drafts_consumes_once(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        term = self.term_values('payment_term')[:1]
        first = self.submit(self.save(term))
        second = self.submit(self.save(term))
        results = self.run_race(self.approver(first), self.approver(second))
        self.assertEqual(sum(item[0] == 'ok' for item in results), 1, results)
        self.assertEqual(Settlement.objects.filter(status='active').count(), 1)
        self.assertEqual(OpenItem.objects.count(), 1)
        self.assertEqual(SourceConsumption.objects.filter(consumer='settlement').count(), 1)
        self.assertEqual(Settlement.objects.filter(status='submitted').count(), 1)

    def test_same_request_token_approves_once(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        row = self.submit(self.save(self.term_values('payment_term')[:1]))
        token = uuid.uuid4()
        results = self.run_race(self.approver(row, token=token),
                                self.approver(row, token=token))
        self.assertEqual([item[0] for item in results], ['ok', 'ok'], results)
        self.assertEqual(SettlementReceipt.objects.filter(action='review').count(), 1)
        self.assertEqual(Settlement.objects.filter(status='active').count(), 1)
        self.assertEqual(OpenItem.objects.count(), 1)

    def blocked_approval(self, change, expected='refused'):
        from threading import Event, Thread
        self.approve_golden()
        self.acceptance(0, 5000)
        row = self.submit(self.save(self.term_values('payment_term')[:1]))
        ready, pids, results = Event(), {}, []

        def worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids['worker'] = cursor.fetchone()[0]
                ready.set()
                try:
                    settlement.review_settlement(self.finance_member(), row.pk,
                        expected_version=row.version, request_token=uuid.uuid4(),
                        decision='approve', note='公司锁后重读', dry_run=False)
                    results.append('unexpected')
                except (PermissionDenied, ValidationError) as error:
                    results.append('refused')
                except BaseException as error:
                    results.append(repr(error))
            finally:
                connection.close()
        thread = Thread(target=worker)
        try:
            with transaction.atomic():
                Organization.objects.select_for_update().get(pk=self.org.pk)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    holder = cursor.fetchone()[0]
                thread.start()
                self.assertTrue(ready.wait(5))
                deadline, blockers = time.monotonic() + 5, []
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_blocking_pids(%s)', [pids['worker']])
                        blockers = cursor.fetchone()[0]
                    if holder in blockers:
                        break
                    time.sleep(.01)
                self.assertIn(holder, blockers)
                change()
                before = self.facts()
        finally:
            if thread.ident:
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, [expected])
        self.assertEqual(before, self.facts())
        self.assertEqual(OpenItem.objects.count(), 0)
        self.assertEqual(Settlement.objects.get(pk=row.pk).status, 'submitted')

    def test_approval_rereads_revoked_membership_after_actual_company_wait(self):
        self.blocked_approval(lambda: Membership.objects.filter(pk=self.finance.pk).update(active=False))

    def test_approval_rereads_disabled_module_after_actual_company_wait(self):
        self.blocked_approval(lambda: PluginState.objects.update_or_create(
            organization=self.org, plugin_id='settlement',
            defaults={'enabled': False, 'changed_by': self.owner.user}))

    def test_approval_competes_with_company_locked_period_close(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        row = self.submit(self.save(self.term_values('payment_term')[:1]))
        month = self.today.strftime('%Y-%m')

        def approve(_unused=None):
            return settlement.review_settlement(self.finance_member(), row.pk,
                expected_version=row.version, request_token=uuid.uuid4(),
                decision='approve', note='并发核对期间', dry_run=False).pk

        def close(_unused=None):
            with transaction.atomic():
                org = lock_company(Membership.objects.get(pk=self.owner.pk))
                AccountingPeriod.objects.update_or_create(
                    organization=org, period=month,
                    defaults={'status': 'posted', 'closed_through': self.today})
                return 'closed'

        results = self.run_race(approve, close)
        self.assertEqual(sum(item[0] == 'ok' for item in results), 1, results)
        if results[0][0] == 'ok':
            self.assertEqual(Settlement.objects.get(pk=row.pk).status, 'active')
            self.assertEqual(OpenItem.objects.count(), 1)
        else:
            self.assertEqual(Settlement.objects.get(pk=row.pk).status, 'submitted')
            self.assertEqual(OpenItem.objects.count(), 0)
        self.assertEqual(SourceConsumption.objects.filter(consumer='settlement').count(),
                         1 if results[0][0] == 'ok' else 0)
