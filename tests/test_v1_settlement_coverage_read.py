"""PH-283 read-only settlement coverage reconciliation: open-item remaining vs clearances/refunds."""
import uuid

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from core.models import (AccountingEvent, AuditEvent, DomainEvent, LedgerEntry, LedgerPosting,
                         PluginState, SourceConsumption)
from pmo_plugins.cash import refund_services as refunds
from pmo_plugins.settlement import coverage_read_services as coverage_read
from pmo_plugins.settlement.models import Clearance, OpenItem
from tests.test_v1_clearance import ClearanceFixture


class CoverageFixture(ClearanceFixture):
    def snapshot(self, *, member=None, as_of=None, project=None):
        return coverage_read.open_item_coverage_snapshot(
            member or self.owner, as_of=as_of, project_id=project)

    def issues(self, *, member=None, as_of=None, project=None):
        return coverage_read.open_item_coverage_issues(
            member or self.owner, as_of=as_of, project_id=project)

    def codes(self, *, member=None):
        return [item['issue_code'] for item in self.issues(member=member)['issues']]

    def refund(self, amount=5000, *, cash_row=None):
        return refunds.record_refund(
            self.owner, original_cash_transaction_id=(cash_row or self.cash_row).pk,
            refund_date=self.today, gross_cents=amount, reason='客户退款',
            request_token=uuid.uuid4(), dry_run=False)

    def item_row(self, result=None):
        result = result or self.snapshot()
        return result['items'][0]

    def fact_counts(self):
        return tuple(model.objects.count() for model in (
            Clearance, OpenItem, AuditEvent, DomainEvent, AccountingEvent, LedgerPosting,
            LedgerEntry, SourceConsumption))


class SettlementCoverageServiceTests(CoverageFixture, TestCase):
    def test_golden_clearance_refund_and_remaining(self):
        self.clear(amount=30000)
        self.refund(5000)
        result = self.snapshot()
        self.assertFalse(result['offset_implemented'])
        self.assertEqual(result['summary']['open_item_count'], 1)
        item = self.item_row(result)
        self.assertEqual(item['cleared_cents'], 30000)
        self.assertEqual(item['refunded_coverage_cents'], 5000)
        self.assertEqual(item['returned_coverage_cents'], 0)
        self.assertEqual(item['offset_cents'], 0)
        self.assertFalse(item['offset_implemented'])
        self.assertEqual(item['remaining_cents'], item['gross_cents'] - 30000)
        self.assertEqual(len(item['clearances']), 1)
        self.assertEqual(self.issues()['issue_count'], 0)
        self.assertEqual(self.issues()['issues'], [])

    def test_unclassified_missing_clearance_is_not_issue(self):
        result = self.snapshot()
        item = self.item_row(result)
        self.assertEqual(item['cleared_cents'], 0)
        self.assertEqual(item['remaining_cents'], item['gross_cents'])
        self.assertEqual(self.issues()['issue_count'], 0)

    def test_append_only_inserted_over_clearance_is_issue(self):
        first = self.clear(amount=30000)
        # append-only compatible tamper: INSERT an extra active clearance row instead of
        # UPDATE/DELETE (PG append-only triggers reject mutations of historical facts).
        Clearance.objects.create(
            organization=self.org, open_item=self.item,
            cash_transaction_id=first.cash_transaction_id,
            cash_transaction_ref=first.cash_transaction_ref,
            cleared_cents=40000, occurred_on=first.occurred_on, status='active',
            version=1, created_by=self.owner)
        codes = self.codes()
        self.assertIn('cleared_over_gross', codes)
        self.assertIn('remaining_negative', codes)
        self.assertIn('cash_coverage_over_gross', codes)

    def test_duplicate_active_clearance_is_issue(self):
        first = self.clear(amount=10000)
        Clearance.objects.create(
            organization=self.org, open_item=self.item,
            cash_transaction_id=first.cash_transaction_id,
            cash_transaction_ref=first.cash_transaction_ref,
            cleared_cents=first.cleared_cents, occurred_on=first.occurred_on,
            status='active', version=1, created_by=self.owner)
        self.assertIn('clearance_duplicate_active', self.codes())

    def test_missing_or_cross_org_cash_reference_is_issue(self):
        first = self.clear(amount=10000)
        # append-only compatible tamper: extra clearance referencing an unknown cash fact.
        Clearance.objects.create(
            organization=self.org, open_item=self.item,
            cash_transaction_id=uuid.uuid4(),
            cash_transaction_ref={'namespace': 'cash', 'kind': 'cash_transaction',
                                  'id': str(uuid.uuid4()), 'version': 1},
            cleared_cents=1000, occurred_on=first.occurred_on, status='active',
            version=1, created_by=self.owner)
        codes = self.codes()
        self.assertIn('clearance_cash_missing', codes)

    def test_as_of_filters_items(self):
        self.clear(amount=10000)
        self.assertEqual(self.snapshot(as_of=self.today.isoformat())['summary'][
            'open_item_count'], 1)
        self.assertEqual(self.snapshot(as_of='2000-01-01')['summary'][
            'open_item_count'], 0)
        self.assertEqual(self.issues(as_of='2000-01-01')['issue_count'], 0)

    def test_permissions_and_managed_project_scope(self):
        self.clear(amount=10000)
        for member in (self.finance, self.auditor):
            self.assertEqual(self.snapshot(member=member)['scope'], 'company')
        pm_result = self.snapshot(member=self.pm)
        self.assertEqual(pm_result['scope'], 'managed_projects')
        self.assertGreaterEqual(pm_result['summary']['open_item_count'], 1)
        with self.assertRaises(PermissionDenied):
            self.snapshot(member=self.plain)
        with self.assertRaises(PermissionDenied):
            self.issues(member=self.plain)

    def test_read_only_and_module_disabled_history_readable(self):
        self.clear(amount=10000)
        before = self.fact_counts()
        self.snapshot()
        self.issues()
        self.assertEqual(before, self.fact_counts())
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='settlement',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.assertEqual(self.snapshot()['summary']['open_item_count'], 1)
        self.assertEqual(self.issues()['issue_count'], 0)

    def test_derived_fields_not_input_values(self):
        self.clear(amount=12345)
        item = self.item_row()
        self.assertEqual(item['remaining_cents'], item['gross_cents'] - 12345)
        self.assertEqual(item['cleared_cents'], 12345)
        self.assertEqual(item['offset_cents'], 0)
        self.assertFalse(item['offset_implemented'])
        self.assertEqual(item['gross_cents'] - item['cleared_cents'],
                         item['remaining_cents'])
