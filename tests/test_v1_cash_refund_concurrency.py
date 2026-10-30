"""PostgreSQL multi-connection races for PH-240 refunds and joint clearance coverage.

SQLite cannot prove production concurrency, so every test here is explicitly skipped outside
PostgreSQL and executed by the PR CI job.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid
from unittest import skipUnless

from django.db import connection, close_old_connections
from django.test import TransactionTestCase

from core.models import Membership
from pmo_plugins.cash import refund_services as refunds
from pmo_plugins.cash.models import BankReturn, Refund
from pmo_plugins.settlement.models import Clearance
from tests.test_v1_clearance import ClearanceFixture


@skipUnless(connection.vendor == 'postgresql',
            'Requires real PostgreSQL independent connections')
class RefundConcurrencyTests(ClearanceFixture, TransactionTestCase):
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
                except Exception as error:  # business rejection is the expected loser
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

    def refund(self, member, amount, **changes):
        values = {'original_cash_transaction_id': self.cash_row.pk,
                  'refund_date': self.today, 'gross_cents': amount,
                  'reason': '并发退款', 'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return refunds.record_refund(member, **values)

    def clear(self, member, amount):
        from pmo_plugins.settlement import clearance_services as clearance
        return clearance.create_clearance(
            member, open_item_id=self.item.pk, cash_transaction_ref=self.cash_row.transaction_ref,
            cleared_cents=amount, request_token=uuid.uuid4(), dry_run=False)

    def test_same_cash_concurrent_refunds_only_one_wins(self):
        results = self.run_race(
            lambda: self.refund(self.owner_member(), 40000),
            lambda: self.refund(self.owner_member(), 40000))
        self.assertEqual(sorted(status for status, _ in results), ['ok', 'rejected'])
        self.assertEqual(Refund.objects.count(), 1)
        refunded = sum(row.gross_cents for row in Refund.objects.all())
        self.assertLessEqual(refunded, self.cash_row.gross_cents)
        coverage = refunds.cash_refund_coverage(self.owner, self.cash_row.pk)
        self.assertGreaterEqual(coverage['refundable_cents'], 0)

    def test_refund_and_clearance_interleave_keep_joint_coverage(self):
        results = self.run_race(
            lambda: self.refund(self.owner_member(), 30000),
            lambda: self.clear(self.owner_member(), 30000))
        statuses = [status for status, _ in results]
        self.assertIn('ok', statuses)
        from django.db.models import Sum
        cleared = Clearance.objects.filter(
            organization=self.org, cash_transaction_id=self.cash_row.pk,
            status='active').aggregate(total=Sum('cleared_cents'))['total'] or 0
        refunded = sum(row.gross_cents for row in Refund.objects.all())
        returned = sum(row.gross_cents for row in BankReturn.objects.all())
        self.assertLessEqual(cleared + refunded + returned, self.cash_row.gross_cents)
        coverage = refunds.cash_refund_coverage(self.owner, self.cash_row.pk)
        self.assertGreaterEqual(coverage['refundable_cents'], 0)
        from io import StringIO
        import json
        from django.core.management import call_command
        out = StringIO()
        call_command('verify_integrity', '--json', stdout=out)
        self.assertTrue(json.loads(out.getvalue())['ok'])

    def test_same_cash_concurrent_bank_returns_only_one_wins(self):
        def bank_return(member):
            return refunds.record_bank_return(
                member, original_cash_transaction_id=self.cash_row.pk,
                return_date=self.today, gross_cents=40000, request_token=uuid.uuid4(),
                dry_run=False)
        results = self.run_race(
            lambda: bank_return(self.owner_member()),
            lambda: bank_return(self.owner_member()))
        self.assertEqual(sorted(status for status, _ in results), ['ok', 'rejected'])
        self.assertEqual(BankReturn.objects.count(), 1)
        coverage = refunds.cash_refund_coverage(self.owner, self.cash_row.pk)
        self.assertGreaterEqual(coverage['refundable_cents'], 0)
