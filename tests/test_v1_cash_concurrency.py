"""PostgreSQL multi-connection races for cash facts; SQLite is explicitly not evidence."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, close_old_connections
from django.test import TransactionTestCase
from unittest import skipUnless

from core.models import Membership
from pmo_plugins.cash import funding_services as funding
from pmo_plugins.cash import transaction_services as cash
from pmo_plugins.cash.models import CashReceipt, CashTransaction
from tests.test_v1_cash import CashFixture


@skipUnless(connection.vendor == 'postgresql', 'Requires real PostgreSQL independent connections')
class CashConcurrencyTests(CashFixture, TransactionTestCase):
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

    def transaction_values(self, **changes):
        values = {'funding_account_id': self.account.pk, 'transaction_date': self.today,
                  'direction': 'in', 'gross_cents': 101, 'external_source_key': 'RACE-1',
                  'payee': {'kind': 'unidentified', 'bank_label': '并发客户'},
                  'evidence': [{'reference': '银行回单 并发'}], 'dry_run': False}
        values.update(changes)
        return values

    def test_same_external_key_two_connections_records_once(self):
        payload = self.transaction_values()

        def record(_unused=None):
            return cash.save_cash_transaction(self.owner_member(), request_token=uuid.uuid4(),
                                              **payload).pk
        results = self.run_race(record, record)
        self.assertEqual([item[0] for item in results], ['ok', 'ok'], results)
        self.assertEqual(len({item[1] for item in results}), 1, results)
        self.assertEqual(CashTransaction.objects.count(), 1)
        self.assertEqual(CashReceipt.objects.filter(action='cash_transaction.save').count(), 1)

    def test_same_request_token_two_connections_records_once(self):
        token = uuid.uuid4()
        payload = self.transaction_values(external_source_key='RACE-TOKEN')

        def record(_unused=None):
            return cash.save_cash_transaction(self.owner_member(), request_token=token,
                                              **payload).pk
        results = self.run_race(record, record)
        self.assertEqual([item[0] for item in results], ['ok', 'ok'], results)
        self.assertEqual(CashTransaction.objects.count(), 1)
        self.assertEqual(CashReceipt.objects.filter(action='cash_transaction.save').count(), 1)

    def test_account_activation_race_activates_once(self):
        draft = self.draft_account(name='并发激活户')

        def activate(_unused=None):
            return funding.activate_funding_account(self.owner_member(), draft.pk,
                expected_version=draft.version, request_token=uuid.uuid4(), dry_run=False).pk
        results = self.run_race(activate, activate)
        self.assertEqual(sum(item[0] == 'ok' for item in results), 1, results)
        self.assertEqual(funding.funding_account_snapshot(self.owner, draft.pk)['version'], 2)
        self.assertEqual(funding.funding_account_snapshot(self.owner, draft.pk)['status'], 'active')
        self.assertEqual(CashReceipt.objects.filter(action='funding_account.activate').count(), 1)
