"""PostgreSQL multi-connection races for PH-241 advance offset and PH-232 clearance.

SQLite cannot prove production concurrency, so every test here is explicitly skipped outside
PostgreSQL and executed by the PR CI job.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid
from unittest import skipUnless

from django.db import connection, close_old_connections
from django.db.models import Sum
from django.test import TransactionTestCase

from core.models import Membership
from pmo_plugins.settlement import clearance_services as clearance
from pmo_plugins.settlement import offset_services as offsets
from pmo_plugins.settlement.models import Clearance, Offset
from tests.test_v1_offset import OffsetFixture


@skipUnless(connection.vendor == 'postgresql',
            'Requires real PostgreSQL independent connections')
class OffsetConcurrencyTests(OffsetFixture, TransactionTestCase):
    @classmethod
    def tearDownClass(cls):
        # Worker threads keep thread-local psycopg connections; close them before destroy.
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
                except Exception as error:
                    from django.core.exceptions import PermissionDenied, ValidationError
                    if isinstance(error, (ValidationError, PermissionDenied)):
                        return ('rejected', type(error).__name__)
                    raise
            finally:
                close_old_connections()
                connection.close()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return list(pool.map(call, actions))

    def owner_member(self):
        return Membership.objects.get(pk=self.owner.pk)

    def offset(self, member, advance, amount, open_item=None):
        return offsets.create_offset(
            member, open_item_id=(open_item or self.item).pk,
            advance_ref=advance.transaction_ref, offset_cents=amount,
            request_token=uuid.uuid4(), dry_run=False)

    def clear(self, member, amount, cash=None):
        return clearance.create_clearance(
            member, open_item_id=self.item.pk,
            cash_transaction_ref=(cash or self.cash_row).transaction_ref,
            cleared_cents=amount, request_token=uuid.uuid4(), dry_run=False)

    def test_same_advance_concurrent_offsets_never_exceed_available(self):
        results = self.run_race(
            lambda: self.offset(self.owner_member(), self.advance, 20000),
            lambda: self.offset(self.owner_member(), self.advance, 20000))
        statuses = sorted(status for status, _ in results)
        self.assertEqual(statuses, ['ok', 'rejected'])
        total = Offset.objects.aggregate(total=Sum('offset_cents'))['total'] or 0
        self.assertLessEqual(total, self.advance.gross_cents)
        self.assertEqual(offsets.advance_offset_snapshot(
            self.owner, self.advance.transaction_ref)['available_cents'],
            self.advance.gross_cents - total)

    def test_same_open_item_concurrent_offsets_never_exceed_remaining(self):
        second_advance = self.make_advance(gross_cents=30000)
        results = self.run_race(
            lambda: self.offset(self.owner_member(), self.advance, 30000),
            lambda: self.offset(self.owner_member(), second_advance, 30000))
        self.assertIn('ok', [status for status, _ in results])
        total = Offset.objects.aggregate(total=Sum('offset_cents'))['total'] or 0
        self.assertLessEqual(total, self.item.gross_cents)
        balances = offsets.open_item_offset_snapshot(self.owner, self.item.pk)
        self.assertGreaterEqual(balances['remaining_cents'], 0)

    def test_offset_and_clearance_interleave_keep_joint_balance(self):
        results = self.run_race(
            lambda: self.offset(self.owner_member(), self.advance, 30000),
            lambda: self.clear(self.owner_member(), 30000))
        self.assertIn('ok', [status for status, _ in results])
        offset_total = Offset.objects.aggregate(total=Sum('offset_cents'))['total'] or 0
        cleared = Clearance.objects.filter(
            organization=self.org, status='active').aggregate(
            total=Sum('cleared_cents'))['total'] or 0
        self.assertLessEqual(offset_total + cleared, self.item.gross_cents)
        balances = offsets.open_item_offset_snapshot(self.owner, self.item.pk)
        self.assertGreaterEqual(balances['remaining_cents'], 0)
