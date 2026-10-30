"""PostgreSQL multi-connection races for PH-245 internal transfer link/reverse.

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
from pmo_plugins.cash import internal_transfer_services as internal
from core.models import AccountingEvent, LedgerPosting
from pmo_plugins.cash.models import InternalTransfer, InternalTransferReversal
from tests.test_v1_internal_transfer import TransferFixture


@skipUnless(connection.vendor == 'postgresql',
            'Requires real PostgreSQL independent connections')
class InternalTransferConcurrencyTests(TransferFixture, TransactionTestCase):
    @classmethod
    def tearDownClass(cls):
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

    def link(self, member, out_cash, in_cash):
        return internal.link_internal_transfer(
            member, out_cash_transaction_id=out_cash.pk,
            in_cash_transaction_id=in_cash.pk,
            evidence=[{'reference': '并发互转依据'}],
            request_token=uuid.uuid4(), dry_run=False)

    def reverse(self, member, transfer):
        fresh = InternalTransfer.objects.get(pk=transfer.pk)
        return internal.reverse_internal_transfer(
            member, fresh.pk, reason='并发撤转', evidence=[{'reference': '并发撤转依据'}],
            request_token=uuid.uuid4(), expected_version=fresh.version, dry_run=False)

    def test_same_out_cash_concurrent_link_only_one_wins(self):
        second_in = self.post(self.in_account, 'in', 50000, 'IN-RACE-2')
        results = self.run_race(
            lambda: self.link(self.owner_member(), self.out_cash, self.in_cash),
            lambda: self.link(self.owner_member(), self.out_cash, second_in))
        self.assertEqual(sorted(status for status, _ in results), ['ok', 'rejected'])
        self.assertEqual(InternalTransfer.objects.count(), 1)
        self.assertEqual(CashClassification_count(), 2)

    def test_duplicate_reverse_only_one_wins(self):
        transfer = self.link(self.owner, self.out_cash, self.in_cash)
        results = self.run_race(
            lambda: self.reverse(self.owner_member(), transfer),
            lambda: self.reverse(self.owner_member(), transfer))
        self.assertEqual(sorted(status for status, _ in results), ['ok', 'rejected'])
        self.assertEqual(InternalTransferReversal.objects.count(), 1)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, 'reversed')

    def test_link_and_reverse_interleave_keep_company_net_zero(self):
        first = self.link(self.owner, self.out_cash, self.in_cash)
        out2 = self.post(self.out_account, 'out', 50000, 'OUT-RACE-2')
        in2 = self.post(self.in_account, 'in', 50000, 'IN-RACE-3')
        results = self.run_race(
            lambda: self.reverse(self.owner_member(), first),
            lambda: self.link(self.owner_member(), out2, in2))
        self.assertIn('ok', [status for status, _ in results])
        postings = LedgerPosting.objects.values('book').annotate(
            total=Sum('amount_cents'))
        by_book = {row['book']: row['total'] for row in postings}
        self.assertEqual(by_book.get('cash_in', 0) - by_book.get('cash_out', 0), 0)


def CashClassification_count():
    from pmo_plugins.cash.models import CashClassification
    return CashClassification.objects.filter(
        classification='transfer', status='active').count()
