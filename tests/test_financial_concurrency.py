"""PH-121 PostgreSQL financial concurrency: company lock and append-only audit chain.

Real independent connections only; SQLite skips are explicit and never claimed
as concurrency evidence. No production data is used.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Thread
import time
import uuid
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.db import connection, close_old_connections, transaction
from django.test import TransactionTestCase

from core.models import AuditEvent, Membership, Organization
from core.services import audit, lock_company, verify_audit
from tests.test_v1_contracts import Fixture


@skipUnless(connection.vendor == 'postgresql',
            'Requires real PostgreSQL independent connections')
class FinancialConcurrencyTests(Fixture, TransactionTestCase):
    @classmethod
    def tearDownClass(cls):
        import gc
        from django.db import connections
        connections.close_all()
        gc.collect()
        super().tearDownClass()

    def second_company(self):
        org = Organization.objects.create(name='并发隔离公司')
        user = get_user_model().objects.create_user(username='race' + uuid.uuid4().hex)
        return org, Membership.objects.create(organization=org, user=user, role='owner')

    def test_company_lock_serializes_second_connection_on_same_company(self):
        ready, pids, results = Event(), {}, []

        def worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    pids['worker'] = cursor.fetchone()[0]
                ready.set()
                with transaction.atomic():
                    lock_company(Membership.objects.get(pk=self.owner.pk))
                    results.append('locked')
            finally:
                connection.close()

        thread = Thread(target=worker)
        try:
            with transaction.atomic():
                lock_company(self.owner)
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
                # While the holder's transaction is open the second connection must wait.
                self.assertIn(holder, blockers)
                self.assertEqual(results, [])
        finally:
            thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ['locked'])

    def test_different_companies_do_not_block_each_other(self):
        _org, other = self.second_company()
        ready, results = Event(), []

        def worker():
            close_old_connections()
            try:
                ready.set()
                with transaction.atomic():
                    lock_company(Membership.objects.get(pk=other.pk))
                    results.append('locked')
            finally:
                connection.close()

        thread = Thread(target=worker)
        try:
            with transaction.atomic():
                lock_company(self.owner)
                thread.start()
                self.assertTrue(ready.wait(5))
                thread.join(5)
                # Different company rows never contend on the same lock.
                self.assertEqual(results, ['locked'])
        finally:
            thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_concurrent_audit_events_keep_append_only_chain_valid(self):
        barrier = Barrier(4)

        def worker(index):
            close_old_connections()
            try:
                barrier.wait(timeout=15)
                member = Membership.objects.get(pk=self.owner.pk)
                audit(member, 'finance.concurrent.v1', self.project, {'index': index})
                return 'ok'
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worker, range(4)))
        self.assertEqual(results, ['ok'] * 4)
        rows = AuditEvent.objects.filter(action='finance.concurrent.v1')
        self.assertEqual(rows.count(), 4)
        self.assertEqual(rows.values('previous_hash').distinct().count(), 4)
        self.assertTrue(verify_audit(Organization.objects.get(pk=self.org.pk)))
