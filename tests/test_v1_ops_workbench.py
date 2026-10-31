"""PH-309 read-only operations reconciliation workbench aggregation."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from core import identity_read_services as identity
from core.models import AccountingEvent, AuditEvent, DomainEvent, LedgerEntry, LedgerPosting, SourceConsumption
from pmo_plugins.cash import reconciliation_services as cash_recon
from pmo_plugins.costing import bank_charge_pairing_services as fee_pairing
from pmo_plugins.delivery import ops_workbench_services as workbench
from pmo_plugins.delivery import operations_read_services as overview
from pmo_plugins.settlement import coverage_read_services as coverage
from tests.test_v1_ops_reconciliation_overview import OpsOverviewFixture

PARTITION_KEYS = ['ops_overview', 'settlement_coverage', 'cash_bridge',
                  'fee_pairing', 'identity_access']


class OpsWorkbenchTests(OpsOverviewFixture, TestCase):
    def summary(self, *, member=None, **changes):
        return workbench.ops_workbench_summary(member or self.owner, **changes)

    def partitions(self, result):
        return {item['key']: item for item in result['partitions']}

    def test_partition_summary_matches_delivered_reads(self):
        result = self.summary()
        self.assertEqual([item['key'] for item in result['partitions']], PARTITION_KEYS)
        parts = self.partitions(result)
        overview_data = overview.ops_reconciliation_overview(self.owner)
        expected_overview = overview_data['issue_count']
        expected_settlement = coverage.open_item_coverage_issues(self.owner)['issue_count']
        expected_cash = cash_recon.cash_bridge_issues(self.owner)['issue_count']
        expected_fee = len(fee_pairing.bank_charge_pairing_issues(self.owner))
        expected_identity = len(identity.member_access_issues(self.owner))
        self.assertEqual(parts['ops_overview']['issue_count'], expected_overview)
        self.assertEqual(parts['settlement_coverage']['issue_count'], expected_settlement)
        self.assertEqual(parts['cash_bridge']['issue_count'], expected_cash)
        self.assertEqual(parts['fee_pairing']['issue_count'], expected_fee)
        self.assertEqual(parts['identity_access']['issue_count'], expected_identity)
        self.assertEqual(result['issue_count'],
                         sum(parts[key]['issue_count'] for key in PARTITION_KEYS))
        for key in PARTITION_KEYS:
            self.assertEqual(parts[key]['drill_url'],
                             workbench.DRILL_URLS[key])
            self.assertTrue(parts[key]['available'])
        self.assertEqual(parts['ops_overview']['missing'], overview_data['missing'])
        for key in ('settlement_coverage', 'cash_bridge', 'fee_pairing',
                    'identity_access'):
            self.assertEqual(parts[key]['missing'], [])

    def test_missing_and_scope_for_pm(self):
        result = self.summary(member=self.pm)
        self.assertEqual(result['scope'], 'managed_projects')
        parts = self.partitions(result)
        self.assertFalse(parts['cash_bridge']['available'])
        self.assertIsNone(parts['cash_bridge']['issue_count'])
        self.assertEqual(parts['cash_bridge']['missing'], ['permission'])
        self.assertIn('permission', result['missing'])
        for key in ('ops_overview', 'settlement_coverage', 'fee_pairing',
                    'identity_access'):
            self.assertTrue(parts[key]['available'])

    def test_member_denied(self):
        with self.assertRaises(PermissionDenied):
            self.summary(member=self.plain)

    def test_as_of_period_and_validation(self):
        result = self.summary(as_of=self.today.isoformat(),
                              period=self.today.strftime('%Y-%m'))
        self.assertEqual(result['as_of'], self.today.isoformat())
        self.assertEqual(result['period'], self.today.strftime('%Y-%m'))
        with self.assertRaises(ValidationError):
            self.summary(as_of='bad-date')
        with self.assertRaises(ValidationError):
            self.summary(period='2026-13')

    def test_read_only_fact_counts(self):
        before = (AccountingEvent.objects.count(), LedgerPosting.objects.count(),
                  SourceConsumption.objects.count(), AuditEvent.objects.count(),
                  DomainEvent.objects.count(), LedgerEntry.objects.count())
        self.summary()
        after = (AccountingEvent.objects.count(), LedgerPosting.objects.count(),
                 SourceConsumption.objects.count(), AuditEvent.objects.count(),
                 DomainEvent.objects.count(), LedgerEntry.objects.count())
        self.assertEqual(before, after)
