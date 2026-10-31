"""PH-316 workbench-to-export linkage: partition export descriptors and content consistency."""
import csv
import io
import uuid

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from core.models import AccountingEvent, AuditEvent, DomainEvent, LedgerEntry, LedgerPosting, SourceConsumption
from pmo_plugins.delivery import ops_workbench_services as workbench
from pmo_plugins.delivery import operations_export_services as export
from tests.test_v1_ops_reconciliation_overview import OpsOverviewFixture


def parse_csv(text):
    rows = list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))
    header = rows[0]
    checksum = next(row for row in rows[1:] if row and row[0] == '#checksum')
    data = [row for row in rows[1:] if row and row[0] != '#checksum']
    return header, data, checksum


class WorkbenchExportTests(OpsOverviewFixture, TestCase):
    def summary(self, *, member=None, **changes):
        return workbench.ops_workbench_summary(member or self.owner, **changes)

    def parts(self, result):
        return {item['key']: item for item in result['partitions']}

    def test_partition_export_datasets_and_filters(self):
        result = self.summary(as_of=self.today.isoformat(),
                              period=self.today.strftime('%Y-%m'))
        parts = self.parts(result)
        expected = {
            'ops_overview': 'ops_overview',
            'settlement_coverage': 'settlement_coverage',
            'cash_bridge': 'cash_bridge',
            'fee_pairing': 'fee_pairing',
        }
        for key, dataset in expected.items():
            self.assertIsNotNone(parts[key]['export'])
            self.assertEqual(parts[key]['export']['dataset'], dataset)
            self.assertEqual(parts[key]['export']['as_of'], self.today.isoformat())
            self.assertEqual(parts[key]['export']['period'],
                             self.today.strftime('%Y-%m'))
            self.assertEqual(parts[key]['export_missing'], [])
        self.assertIsNone(parts['identity_access']['export'])
        self.assertIn('export_dataset_not_implemented',
                      parts['identity_access']['export_missing'])

    def test_pm_cash_export_unavailable(self):
        result = self.summary(member=self.pm)
        parts = self.parts(result)
        self.assertIsNone(parts['cash_bridge']['export'])
        self.assertEqual(parts['cash_bridge']['export_missing'], ['permission'])
        self.assertEqual(parts['settlement_coverage']['export']['dataset'],
                         'settlement_coverage')

    def test_export_content_consistent_with_partition_metrics(self):
        result = self.summary(as_of=self.today.isoformat())
        settlement = self.parts(result)['settlement_coverage']
        exported = export.export_reconciliation_worksheets(
            self.owner, dataset=settlement['export']['dataset'],
            as_of=settlement['export']['as_of'], period=settlement['export']['period'],
            request_token=uuid.uuid4(), dry_run=False)
        header, rows, checksum = parse_csv(exported['csv'])
        self.assertEqual(checksum[2], 'settlement_coverage')
        self.assertEqual(int(checksum[8]), len(rows))
        self.assertEqual(len(rows), settlement['metrics']['open_item_count'])

    def test_read_only_fact_counts(self):
        before = (AccountingEvent.objects.count(), LedgerPosting.objects.count(),
                  SourceConsumption.objects.count(), AuditEvent.objects.count(),
                  DomainEvent.objects.count(), LedgerEntry.objects.count())
        result = self.summary()
        settlement = self.parts(result)['settlement_coverage']
        export.export_reconciliation_worksheets(
            self.owner, dataset=settlement['export']['dataset'],
            as_of=settlement['export']['as_of'], request_token=uuid.uuid4(),
            dry_run=True)
        after = (AccountingEvent.objects.count(), LedgerPosting.objects.count(),
                 SourceConsumption.objects.count(), AuditEvent.objects.count(),
                 DomainEvent.objects.count(), LedgerEntry.objects.count())
        self.assertEqual(before, after)

    def test_member_denied(self):
        with self.assertRaises(PermissionDenied):
            self.summary(member=self.plain)
