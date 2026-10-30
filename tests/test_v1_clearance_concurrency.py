"""PostgreSQL multi-connection races for clearance; SQLite is explicitly not evidence."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, close_old_connections
from django.test import TransactionTestCase
from unittest import skipUnless

from core.models import DomainEvent, Membership
from pmo_plugins.settlement import clearance_services as clearance
from pmo_plugins.settlement.models import Clearance
from tests.test_v1_clearance import ClearanceFixture


@skipUnless(connection.vendor == 'postgresql', 'Requires real PostgreSQL independent connections')
class ClearanceConcurrencyTests(ClearanceFixture, TransactionTestCase):
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

    def owner_member(self):
        return Membership.objects.get(pk=self.owner.pk)

    def active_cleared(self):
        return sum(Clearance.objects.filter(status='active').values_list('cleared_cents',
                                                                        flat=True))

    def test_same_cash_double_clear_only_one_wins(self):
        baseline_events = DomainEvent.objects.filter(
            topic='settlement.cleared.v1').count()

        def create(_unused=None):
            return clearance.create_clearance(self.owner_member(),
                open_item_id=self.item.pk,
                cash_transaction_ref=self.cash_row.transaction_ref,
                cleared_cents=30000, request_token=uuid.uuid4(), dry_run=False).pk
        results = self.run_race(create, create)
        self.assertEqual(sum(item[0] == 'ok' for item in results), 1, results)
        self.assertEqual(Clearance.objects.filter(status='active').count(), 1)
        self.assertEqual(self.active_cleared(), 30000)
        self.assertLessEqual(self.active_cleared(), self.cash_row.gross_cents)
        self.assertLessEqual(self.active_cleared(), self.item.gross_cents)
        self.assertEqual(clearance.open_item_balances(
            self.owner, self.item.pk)['remaining_cents'], 23000)
        self.assertEqual(DomainEvent.objects.filter(
            topic='settlement.cleared.v1').count(), baseline_events + 1)

    def test_same_open_item_two_cash_double_clear_only_one_wins(self):
        second_cash = self.post_cash(gross_cents=53000)

        def create(cash_row):
            def action(_unused=None):
                return clearance.create_clearance(self.owner_member(),
                    open_item_id=self.item.pk,
                    cash_transaction_ref=cash_row.transaction_ref,
                    cleared_cents=30000, request_token=uuid.uuid4(), dry_run=False).pk
            return action
        results = self.run_race(create(self.cash_row), create(second_cash))
        self.assertEqual(sum(item[0] == 'ok' for item in results), 1, results)
        self.assertEqual(Clearance.objects.filter(status='active').count(), 1)
        self.assertEqual(clearance.open_item_balances(
            self.owner, self.item.pk)['remaining_cents'], 23000)
        self.assertLessEqual(self.active_cleared(), self.item.gross_cents)

    def test_duplicate_reverse_only_one_wins(self):
        row = self.clear(amount=30000)
        baseline_events = DomainEvent.objects.filter(
            topic='settlement.clearance_reversed.v1').count()

        def reverse(_unused=None):
            return clearance.reverse_clearance(self.owner_member(), row.pk,
                reason='并发撤核', request_token=uuid.uuid4(),
                expected_version=row.version, dry_run=False).pk
        results = self.run_race(reverse, reverse)
        self.assertEqual(sum(item[0] == 'ok' for item in results), 1, results)
        self.assertEqual(Clearance.objects.get(pk=row.pk).status, 'reversed')
        self.assertEqual(clearance.open_item_balances(
            self.owner, self.item.pk)['remaining_cents'], 53000)
        self.assertEqual(DomainEvent.objects.filter(
            topic='settlement.clearance_reversed.v1').count(), baseline_events + 1)
