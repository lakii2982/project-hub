"""Actual PG connections; SQLite skips cannot establish these guarantees."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Thread
import time
import uuid
from unittest import skipUnless

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, close_old_connections, transaction
from django.test import TransactionTestCase
from core.models import Organization, Membership, AccountingEvent, LedgerPosting, Timesheet
from pmo_plugins.costing import services as service
from pmo_plugins.costing.models import AllocationRun
from tests import test_monthly_labor as business


@skipUnless(connection.vendor == 'postgresql', 'Requires real PostgreSQL multi-connection locking')
class MonthlyLaborConcurrencyTests(TransactionTestCase):
    setUp = business.MonthlyLaborBusinessTests.setUp
    member = business.MonthlyLaborBusinessTests.member
    save_pool = business.MonthlyLaborBusinessTests.save_pool
    confirmed = business.MonthlyLaborBusinessTests.confirmed
    capacity = business.MonthlyLaborBusinessTests.capacity
    time = business.MonthlyLaborBusinessTests.time
    allocate = business.MonthlyLaborBusinessTests.allocate
    facts = business.MonthlyLaborBusinessTests.facts

    def race(self, action):
        barrier = Barrier(2)
        def worker():
            close_old_connections()
            try:
                barrier.wait(10)
                try:
                    action()
                    return 'posted'
                except ValidationError:
                    return 'refused'
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: worker(), range(2)))
        self.assertCountEqual(results, ['posted', 'refused'])

    def test_same_zero_salary_confirmed_once(self):
        pool = self.save_pool(0)
        pool = service.submit_salary_pool(self.owner, pool.pk, expected_version=pool.version,
            request_token=uuid.uuid4(), dry_run=False)
        self.race(lambda: service.confirm_salary_pool(self.finance, pool.pk, expected_version=pool.version,
            request_token=uuid.uuid4(), review_note='独立确认零工资实际依据', dry_run=False))
        self.assertEqual(AccountingEvent.objects.count(), 1)
        self.assertEqual(LedgerPosting.objects.count(), 1)

    def test_same_zero_pool_one_active_allocation(self):
        pool, policy = self.capacity(self.confirmed(0))
        self.race(lambda: self.allocate(pool, policy))
        self.assertEqual(AllocationRun.objects.filter(status='posted').count(), 1)
        self.assertEqual(AccountingEvent.objects.count(), 2)

    def test_two_reversals_append_once(self):
        pool, policy = self.capacity(self.confirmed())
        run = self.allocate(pool, policy)
        self.race(lambda: service.reverse_allocation(self.owner, run.pk,
            request_token=uuid.uuid4(), reason='并发独立撤销同一分配', dry_run=False))
        self.assertEqual(AccountingEvent.objects.filter(reverses_event_id=run.accounting_event_id).count(), 1)

    def test_allocation_and_input_reversal_serialize(self):
        from core.services import reverse
        row = self.time(10, self.project)
        pool, policy = self.capacity(self.confirmed())
        barrier = Barrier(2)
        def worker(allocate):
            close_old_connections()
            try:
                barrier.wait(10)
                try:
                    if allocate:
                        self.allocate(pool, policy)
                    else:
                        reverse(self.finance, 'time', row.pk, '核对后撤销投入')
                    return 'posted'
                except ValidationError:
                    return 'refused'
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(worker, [True, False]))
        self.assertEqual(results[0], 'posted')
        run = AllocationRun.objects.get(status='posted')
        row.refresh_from_db()
        covered = [item['timesheet_ref']['id'] for item in run.approved_inputs_snapshot]
        if row.status == 'reversed':
            self.assertNotIn(str(row.pk), covered)
        else:
            self.assertEqual(results[1], 'refused')
            self.assertIn(str(row.pk), covered)

    def blocked_allocation(self, change, expected_error):
        pool, policy = self.capacity(self.confirmed())
        ready, pids, results = Event(), {}, []
        def worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids['worker'] = cursor.fetchone()[0]
                ready.set()
                try:
                    self.allocate(pool, policy)
                    results.append('unexpected')
                except expected_error:
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
        self.assertEqual(results, ['refused'])
        self.assertEqual(before, self.facts())

    def test_allocation_rereads_membership_after_actual_wait(self):
        self.blocked_allocation(lambda: Membership.objects.filter(pk=self.owner.pk).update(active=False), PermissionDenied)

    def test_allocation_rereads_module_after_actual_wait(self):
        from core.plugin_runtime import set_enabled
        self.blocked_allocation(lambda: set_enabled(self.owner, 'costing', False), PermissionDenied)

    def test_allocation_rereads_period_after_actual_wait(self):
        self.blocked_allocation(lambda: Organization.objects.filter(pk=self.org.pk).update(locked_through=self.today), ValidationError)
