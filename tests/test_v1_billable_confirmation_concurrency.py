"""PH-269 PG-only concurrent confirmation check (SQLite cannot prove locking)."""
import uuid
from threading import Barrier, Thread
from unittest import skipUnless

from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.test import TransactionTestCase

from pmo_plugins.time import services
from pmo_plugins.time.models import BillableConfirmation
from tests.test_v1_billable_confirmation import TimeBillableFixture


@skipUnless(connection.vendor == 'postgresql',
            'Requires PostgreSQL independent connections; SQLite cannot prove locking')
class BillableConfirmationConcurrencyTests(TimeBillableFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.row = self.draft()
        self.barrier = Barrier(2)

    def _confirm(self, results, index):
        close_old_connections()
        try:
            from core.models import Membership
            member = Membership.objects.get(pk=self.finance.pk)
            self.barrier.wait(timeout=10)
            services.confirm_billable(member, self.row['id'], expected_version=1,
                                      request_token=uuid.uuid4(), dry_run=False)
            results[index] = 'ok'
        except (ValidationError, Exception) as error:  # one loser is expected
            results[index] = type(error).__name__
        finally:
            close_old_connections()

    def test_only_one_concurrent_confirmation_wins(self):
        results = [None, None]
        threads = [Thread(target=self._confirm, args=(results, index)) for index in (0, 1)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertEqual(sorted(results), ['ValidationError', 'ok'])
        self.assertEqual(BillableConfirmation.objects.filter(status='confirmed').count(), 1)
