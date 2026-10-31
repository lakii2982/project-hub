"""PH-304 authorized read-only reconciliation CSV export service."""
import csv
import io
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from core.models import (AccountingEvent, AuditEvent, DomainEvent, LedgerEntry, LedgerPosting,
                         SourceConsumption)
from pmo_plugins.delivery import operations_export_services as export
from tests.test_v1_bank_charge_pairing import FeePairingFixture
from tests.test_v1_cash_bridge_read import BridgeFixture
from tests.test_v1_ops_reconciliation_overview import OpsOverviewFixture
from tests.test_v1_settlement_coverage_read import CoverageFixture


def parse_csv(text):
    rows = list(csv.reader(io.StringIO(text.lstrip('\ufeff'))))
    header = rows[0]
    checksum = next(row for row in rows[1:] if row and row[0] == '#checksum')
    data = [row for row in rows[1:] if row and row[0] != '#checksum']
    return header, data, checksum


class ExportOpsOverviewTests(OpsOverviewFixture, TestCase):
    def export(self, member=None, **changes):
        values = {'dataset': 'ops_overview', 'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return export.export_reconciliation_worksheets(member or self.owner, **values)

    def test_dry_run_plan_and_csv(self):
        plan = self.export(dry_run=True)
        self.assertTrue(plan['dry_run'])
        self.assertNotIn('csv', plan)
        self.assertEqual(plan['columns'], export.COLUMNS['ops_overview'])
        self.assertGreaterEqual(plan['row_count'], 1)
        result = self.export()
        self.assertFalse(result['dry_run'])
        self.assertTrue(result['csv'].startswith('\ufeff'))
        header, rows, checksum = parse_csv(result['csv'])
        self.assertEqual(header, export.COLUMNS['ops_overview'])
        self.assertEqual(len(rows), result['row_count'])
        self.assertEqual(checksum[0], '#checksum')
        self.assertEqual(checksum[2], 'ops_overview')
        self.assertEqual(int(checksum[8]), result['row_count'])
        self.assertEqual(len(checksum[10]), 64)
        self.assertEqual(result['content_id'], checksum[10])

    def test_token_replay_and_changed_input_conflict(self):
        token = uuid.uuid4()
        first = self.export(request_token=token)
        replay = self.export(request_token=token)
        self.assertEqual(first['content_id'], replay['content_id'])
        with self.assertRaises(ValidationError):
            self.export(request_token=token, as_of='2000-01-01')

    def test_dry_run_then_real_same_token(self):
        token = uuid.uuid4()
        plan = self.export(request_token=token, dry_run=True)
        self.assertTrue(plan['dry_run'])
        self.assertNotIn('csv', plan)
        real = self.export(request_token=token, dry_run=False)
        self.assertFalse(real['dry_run'])
        self.assertTrue(real['csv'].startswith('\ufeff'))
        self.assertEqual(len(real['content_id']), 64)
        self.assertEqual(real['row_count'], plan['row_count'])

    def test_real_then_dry_run_same_token(self):
        token = uuid.uuid4()
        real = self.export(request_token=token, dry_run=False)
        plan = self.export(request_token=token, dry_run=True)
        self.assertTrue(plan['dry_run'])
        self.assertNotIn('csv', plan)
        self.assertIsNone(plan['content_id'])
        self.assertEqual(plan['row_count'], real['row_count'])
        self.assertEqual(plan['token'], str(token))

    def test_invalid_dataset_and_dates(self):
        with self.assertRaises(ValidationError):
            self.export(dataset='unknown')
        with self.assertRaises(ValidationError):
            self.export(as_of='bad-date')
        with self.assertRaises(ValidationError):
            self.export(period='2026-13')
        with self.assertRaises(ValidationError):
            self.export(request_token='not-a-token')

    def test_read_only_fact_counts(self):
        before = (AccountingEvent.objects.count(), LedgerPosting.objects.count(),
                  SourceConsumption.objects.count(), AuditEvent.objects.count(),
                  DomainEvent.objects.count())
        self.export()
        after = (AccountingEvent.objects.count(), LedgerPosting.objects.count(),
                 SourceConsumption.objects.count(), AuditEvent.objects.count(),
                 DomainEvent.objects.count())
        self.assertEqual(before, after)


class ExportSettlementTests(CoverageFixture, TestCase):
    def export(self, member=None, **changes):
        values = {'dataset': 'settlement_coverage', 'request_token': uuid.uuid4(),
                  'dry_run': False}
        values.update(changes)
        return export.export_reconciliation_worksheets(member or self.owner, **values)

    def test_settlement_rows_and_derived_values(self):
        self.clear(amount=30000)
        result = self.export()
        header, rows, checksum = parse_csv(result['csv'])
        self.assertEqual(header, export.COLUMNS['settlement_coverage'])
        self.assertGreaterEqual(len(rows), 1)
        row = next(value for value in rows if int(value[5]) == 30000)
        self.assertEqual(row[0], 'settlement_coverage')
        self.assertEqual(row[3], 'due')
        self.assertEqual(int(row[4]), self.item.gross_cents)
        self.assertEqual(int(row[6]), 0)  # offset not implemented
        self.assertEqual(int(row[9]), self.item.gross_cents - 30000)
        self.assertEqual(int(checksum[8]), len(rows))

    def test_member_denied(self):
        with self.assertRaises(PermissionDenied):
            self.export(member=self.plain)


class ExportCashTests(BridgeFixture, TestCase):
    def export(self, member=None, **changes):
        values = {'dataset': 'cash_bridge', 'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return export.export_reconciliation_worksheets(member or self.owner, **values)

    def test_cash_rows_and_pm_denied(self):
        row = self.posted(gross_cents=1000)
        self.classify(row, classification='receipt')
        result = self.export()
        header, rows, checksum = parse_csv(result['csv'])
        self.assertEqual(header, export.COLUMNS['cash_bridge'])
        self.assertGreaterEqual(len(rows), 1)
        item = next(value for value in rows if value[1] == str(row.pk))
        self.assertEqual(int(item[4]), 1000)
        self.assertEqual(item[6], 'receipt')
        self.assertEqual(int(checksum[8]), len(rows))
        with self.assertRaises(PermissionDenied):
            self.export(member=self.pm)


class ExportFeePairingTests(FeePairingFixture, TestCase):
    def export(self, member=None, **changes):
        values = {'dataset': 'fee_pairing', 'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return export.export_reconciliation_worksheets(member or self.owner, **values)

    def test_fee_rows(self):
        cash = self.cash()
        self.approved(cash)
        result = self.export()
        header, rows, checksum = parse_csv(result['csv'])
        self.assertEqual(header, export.COLUMNS['fee_pairing'])
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 'fee_pairing')
        self.assertEqual(int(checksum[8]), len(rows))
