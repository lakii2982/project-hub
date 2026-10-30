"""PH-239 cash book bridge tests: AccountingEvent + LedgerPosting + permanent bank_effect claim.

SQLite runs the deterministic bridge tests; the three PostgreSQL-only races at the bottom are
skipped locally and executed by the PR CI job with real independent connections.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid
from unittest import skipUnless

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, close_old_connections
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase

from core.models import AccountingEvent, LedgerPosting, Membership, SourceConsumption
from pmo_plugins.cash import classification_services as cls
from pmo_plugins.cash.models import CashClassification, CashClassificationReversal
from tests.test_v1_cash_classification import ClassificationFixture

RECORDED_TOPIC = 'cash.classification_recorded.v1'
REVERSED_TOPIC = 'cash.classification_reversed.v1'


class CashBookBridgeTests(ClassificationFixture, TestCase):
    def verify(self):
        from io import StringIO
        import json
        from django.core.management import call_command
        out = StringIO()
        call_command('verify_integrity', '--json', stdout=out)
        return json.loads(out.getvalue())

    def test_classify_bridges_one_event_one_posting_and_one_claim(self):
        row = self.posted(gross_cents=53000)
        classification = self.classify(row)
        event = AccountingEvent.objects.get(source=classification.classification_ref)
        self.assertEqual((event.event_type, event.claim_effect_cents, event.occurred_on,
                          event.posting_period, event.labor_origin_ref),
                         ('cash.classification', 53000, row.transaction_date,
                          row.transaction_date.strftime('%Y-%m'), None))
        posting = event.postings.get()
        self.assertEqual((posting.book, posting.amount_cents, posting.scope_kind,
                          posting.scope_id, posting.origin, posting.reverses_posting_id),
                         ('cash_in', 53000, 'funding_account',
                          str(row.funding_account_id), 'current', None))
        claim = SourceConsumption.objects.get(consumer='cash', source_id=row.pk)
        self.assertEqual((claim.coverage_key, claim.amount_cents,
                          claim.events.aggregate(total=Sum('claim_effect_cents'))['total']),
                         ('bank_effect', 53000, 53000))
        coverage = cls.cash_book_coverage(self.owner, self.account.pk)
        self.assertEqual((coverage['from_ledger_cash_in_cents'],
                          coverage['from_ledger_cash_out_cents']), (53000, 0))
        canonical = cls.classification_snapshot(self.owner, classification.pk)
        self.assertEqual(canonical['posting_event_ref'],
                         {'namespace': 'core', 'kind': 'accounting_event',
                          'id': str(event.pk), 'version': event.version})
        self.assertTrue(self.verify()['ok'])

    def test_reversal_mirrors_and_reclassification_reuses_the_claim(self):
        row = self.posted(gross_cents=53000,
                          payee={'kind': 'counterparty', 'id': str(self.party.pk)})
        classification = self.classify(row)
        original = AccountingEvent.objects.get(source=classification.classification_ref)
        original_posting = original.postings.get()
        self.reverse(classification)
        reversal = CashClassificationReversal.objects.get()
        reversed_event = AccountingEvent.objects.get(source=reversal.reversal_ref)
        self.assertEqual((reversed_event.event_type, reversed_event.claim_effect_cents,
                          reversed_event.reverses_event_id),
                         ('cash.classification.reversed', -53000, original.pk))
        reversed_posting = reversed_event.postings.get()
        self.assertEqual((reversed_posting.book, reversed_posting.amount_cents,
                          reversed_posting.reverses_posting_id),
                         ('cash_in', -53000, original_posting.pk))
        claim = SourceConsumption.objects.get(consumer='cash')
        self.assertEqual(claim.events.aggregate(
            total=Sum('claim_effect_cents'))['total'], 0)
        self.assertEqual(cls.cash_book_coverage(
            self.owner, self.account.pk)['book_net_cents'], 0)
        replacement = self.classify(row, classification='advance')
        self.assertEqual(replacement.status, 'active')
        self.assertEqual(SourceConsumption.objects.filter(consumer='cash').count(), 1)
        self.assertEqual(cls.cash_book_coverage(
            self.owner, self.account.pk)['book_net_cents'], 53000)
        self.assertEqual(AccountingEvent.objects.filter(
            source__namespace='cash').count(), 3)
        self.assertTrue(self.verify()['ok'])

    def test_nonzero_bank_effect_claim_rejects_reposting_without_half_write(self):
        row = self.posted(gross_cents=10000)
        claim = SourceConsumption.objects.create(
            organization=self.org, consumer='cash', source_id=row.pk,
            coverage_key='bank_effect', source=row.transaction_ref, amount_cents=10000,
            event_ref={'namespace': 'core', 'kind': 'accounting_event',
                       'id': str(uuid.uuid4()), 'version': 1})
        AccountingEvent.objects.create(
            id=uuid.UUID(claim.event_ref['id']), organization=self.org,
            source=row.transaction_ref, event_type='cash.classification',
            occurred_on=row.transaction_date,
            posting_period=row.transaction_date.strftime('%Y-%m'),
            algorithm_version='forged', claim=claim, claim_effect_cents=10000)
        before_classifications = CashClassification.objects.count()
        with self.assertRaises(ValidationError):
            self.classify(row)
        self.assertEqual(CashClassification.objects.count(), before_classifications)
        self.assertFalse(CashClassification.objects.filter(cash_transaction=row).exists())

    def test_caller_supplied_balance_or_bridge_fields_are_rejected(self):
        row = self.posted()
        with self.assertRaises(TypeError):
            cls.record_classification(
                self.owner, cash_transaction_id=row.pk, classification='receipt',
                evidence=[{'reference': '依据'}], request_token=uuid.uuid4(),
                dry_run=False, refundable_cents=1)
        with self.assertRaises(TypeError):
            cls.record_classification(
                self.owner, cash_transaction_id=row.pk, classification='receipt',
                evidence=[{'reference': '依据'}], request_token=uuid.uuid4(),
                dry_run=False, accounting_event={})


@skipUnless(connection.vendor == 'postgresql',
            'Requires real PostgreSQL independent connections')
class CashBookConcurrencyTests(ClassificationFixture, TransactionTestCase):
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
                except (ValidationError, PermissionDenied):
                    return ('rejected', None)
            finally:
                close_old_connections()
                connection.close()
        with ThreadPoolExecutor(max_workers=len(actions)) as pool:
            return list(pool.map(call, actions))

    def owner_member(self):
        return Membership.objects.get(pk=self.owner.pk)

    def classify_action(self, row, kind='receipt'):
        def action():
            member = self.owner_member()
            return cls.record_classification(
                member, cash_transaction_id=row.pk, classification=kind,
                evidence=[{'reference': '并发分类依据'}], request_token=uuid.uuid4(),
                dry_run=False)
        return action

    def verify_ok(self):
        from io import StringIO
        import json
        from django.core.management import call_command
        out = StringIO()
        call_command('verify_integrity', '--json', stdout=out)
        return json.loads(out.getvalue())['ok']

    def test_same_cash_concurrent_classification_only_one_wins(self):
        row = self.posted(gross_cents=53000)
        results = self.run_race(self.classify_action(row), self.classify_action(row))
        self.assertEqual(sorted(status for status, _ in results), ['ok', 'rejected'])
        self.assertEqual(CashClassification.objects.filter(
            cash_transaction=row, status='active').count(), 1)
        self.assertEqual(AccountingEvent.objects.filter(
            source__namespace='cash').count(), 1)
        self.assertEqual(LedgerPosting.objects.count(), 1)
        self.assertEqual(SourceConsumption.objects.filter(consumer='cash').count(), 1)
        self.assertEqual(cls.cash_book_coverage(
            self.owner, self.account.pk)['book_net_cents'], 53000)
        self.assertTrue(self.verify_ok())

    def test_duplicate_reversal_only_one_wins(self):
        row = self.posted(gross_cents=53000)
        classification = self.classify(row)

        def reverse_action():
            member = self.owner_member()
            fresh = CashClassification.objects.get(pk=classification.pk)
            return cls.reverse_classification(
                member, classification_id=fresh.pk, reason='并发撤分类',
                evidence=[{'reference': '并发撤分类依据'}], request_token=uuid.uuid4(),
                expected_version=fresh.version, dry_run=False)
        results = self.run_race(reverse_action, reverse_action)
        self.assertEqual(sorted(status for status, _ in results), ['ok', 'rejected'])
        self.assertEqual(CashClassificationReversal.objects.count(), 1)
        self.assertEqual(AccountingEvent.objects.filter(
            source__namespace='cash').count(), 2)
        claim = SourceConsumption.objects.get(consumer='cash')
        self.assertEqual(claim.events.aggregate(
            total=Sum('claim_effect_cents'))['total'], 0)
        self.assertEqual(cls.cash_book_coverage(
            self.owner, self.account.pk)['book_net_cents'], 0)
        self.assertTrue(self.verify_ok())

    def test_interleaved_classify_and_reverse_keep_net_consistent(self):
        row = self.posted(gross_cents=53000)
        first = self.classify(row)

        def reverse_action():
            member = self.owner_member()
            fresh = CashClassification.objects.get(pk=first.pk)
            return cls.reverse_classification(
                member, classification_id=fresh.pk, reason='并发撤销原分类',
                evidence=[{'reference': '并发撤分类依据'}], request_token=uuid.uuid4(),
                expected_version=fresh.version, dry_run=False)
        results = self.run_race(reverse_action, self.classify_action(row))
        active = CashClassification.objects.filter(
            cash_transaction=row, status='active').count()
        self.assertIn(active, {0, 1})
        expected_net = 53000 if active == 1 else 0
        posting_net = LedgerPosting.objects.aggregate(
            total=Sum('amount_cents'))['total'] or 0
        self.assertEqual(posting_net, expected_net)
        claim = SourceConsumption.objects.get(consumer='cash')
        self.assertEqual(claim.events.aggregate(
            total=Sum('claim_effect_cents'))['total'], expected_net)
        self.assertTrue(self.verify_ok())
        self.assertIn('ok', [status for status, _ in results])
