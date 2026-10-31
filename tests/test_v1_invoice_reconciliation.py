"""PH-325 read-only invoice/settlement/revenue/contract-terms pairing."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AuditEvent, DomainEvent, LedgerEntry,
                         Project, SourceConsumption)
from pmo_plugins.invoicing import invoice_reconciliation_services as reconciliation
from pmo_plugins.invoicing.models import Invoice, InvoiceAllocation, TaxQualification
from tests.test_v1_invoice import InvoiceFixture
from tests.test_v1_revenue_pairing_read import RevenuePairingFixture


class ReconciliationFixture(InvoiceFixture):
    def setUp(self):
        super().setUp()
        self.auditor = self.member('auditor')
        self.pm = self.member('pm')
        self.plain = self.member('member')
        Project.objects.filter(pk=self.project.pk).update(manager=self.pm)


class InvoiceReconciliationReadTests(ReconciliationFixture, TestCase):
    def detail(self, invoice=None, **kwargs):
        invoice = invoice or self.record()
        result = reconciliation.invoice_pairing(
            kwargs.pop('member', self.owner), invoice_id=invoice['invoice_ref']['id'], **kwargs)
        return result

    def test_owner_finance_auditor_read_and_member_denied(self):
        invoice, _ = self.allocated()
        for reader in (self.owner, self.finance, self.auditor):
            snapshot = reconciliation.invoice_pairing(
                reader, invoice_id=invoice['invoice_ref']['id'])
            self.assertEqual(snapshot['scope']['kind'], 'invoice')
            self.assertEqual(snapshot['invoice']['invoice_number'],
                             Invoice.objects.get(pk=invoice['invoice_ref']['id']).invoice_number)
            self.assertEqual(snapshot['invoice']['gross_cents'], 10000)
        with self.assertRaises(PermissionDenied):
            reconciliation.invoice_pairing(self.plain, invoice_id=invoice['invoice_ref']['id'])

    def test_pm_project_scope_and_cross_project_filtering(self):
        invoice, _ = self.allocated()
        snapshot = reconciliation.invoice_pairing(self.pm)
        self.assertEqual([row['invoice_number'] for row in snapshot['invoices']],
                         [Invoice.objects.get(pk=invoice['invoice_ref']['id']).invoice_number])
        Project.objects.filter(pk=self.project.pk).update(manager=self.owner)
        snapshot = reconciliation.invoice_pairing(self.pm)
        self.assertEqual(snapshot['invoices'], [])
        with self.assertRaises(PermissionDenied):
            reconciliation.invoice_pairing(self.pm, invoice_id=invoice['invoice_ref']['id'])

    def test_settlement_and_contract_sources_and_totals(self):
        invoice, _ = self.allocated()
        snapshot = self.detail(invoice)
        rows = snapshot['invoice']['allocations']
        kinds = sorted((row['business_source_ref']['namespace'],
                        row['business_source_ref']['kind']) for row in rows)
        self.assertEqual(kinds, [('contracts', 'contract_line'), ('settlement', 'open_item')])
        self.assertTrue(all(row['project_id'] == str(self.project.pk) for row in rows))
        open_item = next(row for row in rows
                         if row['business_source_ref']['kind'] == 'open_item')
        self.assertEqual(open_item['source']['remaining_cents'], 40000)
        self.assertEqual(snapshot['invoice']['allocated']['gross_cents'], 10000)
        self.assertEqual(snapshot['invoice']['unallocated_cents'], 0)
        codes = {issue['code'] for issue in snapshot['invoice']['issues']}
        self.assertNotIn('unallocated_amount', codes)
        self.assertNotIn('over_allocated', codes)
        self.assertNotIn('source_missing', codes)

    def test_unallocated_invoice_and_tax_missing_then_pending(self):
        invoice = self.record()
        snapshot = self.detail(invoice)
        codes = {issue['code'] for issue in snapshot['invoice']['issues']}
        self.assertIn('unallocated_amount', codes)
        self.assertIn('tax_qualification_missing',
                      [item['code'] for item in snapshot['invoice']['missing']])
        row = Invoice.objects.get(pk=invoice['invoice_ref']['id'])
        source_ref = {'namespace': 'invoicing', 'kind': 'invoice',
                      'id': str(row.pk), 'version': 1}
        from pmo_plugins.invoicing import services
        opened = services.open_tax_qualification(
            self.owner, source_ref=source_ref, tax_cents=1000,
            evidence=[{'kind': 'tax_invoice', 'reference': 'pending'}],
            request_token=uuid.uuid4(), dry_run=False)
        self.assertEqual(opened['status'], 'pending')
        snapshot = self.detail(invoice)
        self.assertIn('tax_unconfirmed', {issue['code'] for issue in snapshot['invoice']['issues']})
        confirmed = self.record(gross_cents=2000, net_cents=1800, tax_cents=200)
        confirmed_row = Invoice.objects.get(pk=confirmed['invoice_ref']['id'])
        confirmed_source = {'namespace': 'invoicing', 'kind': 'invoice',
                            'id': str(confirmed_row.pk), 'version': 1}
        opened_confirmed = services.open_tax_qualification(
            self.owner, source_ref=confirmed_source, tax_cents=200,
            evidence=[{'kind': 'tax_invoice', 'reference': 'confirmed'}],
            request_token=uuid.uuid4(), dry_run=False)
        services.confirm_tax_qualification(
            self.owner, opened_confirmed['qualification_ref']['id'],
            qualification_status='recoverable', decided_on=self.today,
            evidence=[{'kind': 'tax_decision', 'reference': '可抵扣'}],
            request_token=uuid.uuid4(), expected_version=1, dry_run=False)
        confirmed_snapshot = self.detail(confirmed)
        self.assertNotIn('tax_unconfirmed',
                         {issue['code'] for issue in confirmed_snapshot['invoice']['issues']})
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TaxQualification.objects.create(
                    organization=self.org, source_ref=source_ref, tax_cents=1000,
                    status='confirmed', qualification_status='recoverable',
                    decided_on=self.today)

    def test_missing_source_is_null_plus_missing_not_zero(self):
        from pmo_plugins.invoicing.models import InvoiceAllocation, InvoiceLine
        row = Invoice.objects.create(
            organization=self.org, direction='sales', counterparty=self.contract.party,
            invoice_number='INV-MISSING-' + uuid.uuid4().hex[:6].upper(),
            invoice_date=self.today, gross_cents=10000, net_cents=9000, tax_cents=1000,
            created_by=self.owner)
        line = InvoiceLine.objects.create(
            organization=self.org, invoice=row, line_no=1, gross_cents=10000,
            net_cents=9000, tax_cents=1000)
        InvoiceAllocation.objects.create(
            organization=self.org, invoice_line=line, business_source_ref={
                'namespace': 'contracts', 'kind': 'contract_line',
                'id': str(uuid.uuid4()), 'version': 1},
            gross_cents=10000, net_cents=9000, tax_cents=1000, created_by=self.owner)
        snapshot = reconciliation.invoice_pairing(self.owner, invoice_id=row.pk)
        self.assertIsNone(snapshot['invoice']['allocations'][0]['source'])
        self.assertIn('contract_line_missing',
                      [item['code'] for item in snapshot['invoice']['missing']])

    def test_duplicate_pairing_detected_across_invoices(self):
        invoice, _ = self.allocated()
        second = self.record(gross_cents=4000, net_cents=3600, tax_cents=400)
        from pmo_plugins.invoicing import services
        services.allocate_invoice_lines(
            self.owner, invoice_id=second['invoice_ref']['id'], lines=[{
                'line_no': 1, 'gross_cents': 4000, 'net_cents': 3600, 'tax_cents': 400,
                'allocations': [{'business_source_ref': {
                    'namespace': 'settlement', 'kind': 'open_item',
                    'id': str(self.open_item.pk), 'version': 1},
                    'gross_cents': 4000, 'net_cents': 3600, 'tax_cents': 400}]}],
            expected_version=1, request_token=uuid.uuid4(), dry_run=False)
        snapshot = self.detail(second)
        issues = ({issue['code'] for issue in snapshot['invoice']['issues']}
                  | {issue['code'] for issue in snapshot['issues']})
        self.assertIn('duplicate_pairing', issues)

    def test_read_only_no_audit_event_or_financial_write(self):
        invoice, _ = self.allocated()
        before = (Invoice.objects.count(), InvoiceAllocation.objects.count(),
                  AuditEvent.objects.count(), DomainEvent.objects.count(),
                  AccountingEvent.objects.count(), LedgerEntry.objects.count())
        reconciliation.invoice_pairing(self.owner)
        reconciliation.invoice_pairing(self.owner, invoice_id=invoice['invoice_ref']['id'])
        after = (Invoice.objects.count(), InvoiceAllocation.objects.count(),
                 AuditEvent.objects.count(), DomainEvent.objects.count(),
                 AccountingEvent.objects.count(), LedgerEntry.objects.count())
        self.assertEqual(before, after)

    def test_org_scope_aggregates_and_project_filter(self):
        invoice, _ = self.allocated()
        snapshot = reconciliation.invoice_pairing(self.owner)
        self.assertEqual(snapshot['scope']['kind'], 'organization')
        self.assertEqual(len(snapshot['invoices']), 1)
        self.assertEqual(snapshot['totals']['invoice_gross_cents'], 10000)
        self.assertEqual(snapshot['totals']['allocated_gross_cents'], 10000)
        filtered = reconciliation.invoice_pairing(self.owner, project_id=self.project.pk)
        self.assertEqual(filtered['scope']['kind'], 'project')
        self.assertEqual(len(filtered['invoices']), 1)
        other = Project.objects.create(organization=self.org, name='另项目',
                                       client='客户', code='P-OTHER-' + uuid.uuid4().hex[:6],
                                       manager=self.owner, start_date=self.today,
                                       end_date=self.today + timedelta(days=30))
        empty = reconciliation.invoice_pairing(self.owner, project_id=other.pk)
        self.assertEqual(empty['invoices'], [])


class InvoiceReconciliationRegressionTests(ReconciliationFixture, TestCase):
    def _invoice_allocated_to(self, allocations, total=10000):
        from pmo_plugins.invoicing import services
        invoice = self.record(gross_cents=total, net_cents=total - total // 10,
                              tax_cents=total // 10)
        services.allocate_invoice_lines(
            self.owner, invoice_id=invoice['invoice_ref']['id'],
            lines=[{'line_no': 1, 'gross_cents': total,
                    'net_cents': total - total // 10, 'tax_cents': total // 10,
                    'allocations': allocations}],
            expected_version=1, request_token=uuid.uuid4(), dry_run=False)
        return invoice

    def test_no_posted_revenue_is_null_plus_missing(self):
        invoice, _ = self.allocated()
        snapshot = reconciliation.invoice_pairing(
            self.owner, invoice_id=invoice['invoice_ref']['id'])
        self.assertIsNone(snapshot['invoice']['recognized_net_cents'])
        self.assertIn('revenue_missing',
                      [item['code'] for item in snapshot['invoice']['missing']])
        self.assertNotIn('revenue_invoice_mismatch',
                         [issue['code'] for issue in snapshot['invoice']['issues']])

    def test_as_of_bounds_future_revenue_adjustment(self):
        from pmo_plugins.invoicing import services
        invoice = self.record(gross_cents=6000, net_cents=5400, tax_cents=600)
        services.allocate_invoice_lines(
            self.owner, invoice_id=invoice['invoice_ref']['id'],
            lines=[{'line_no': 1, 'gross_cents': 6000, 'net_cents': 5400, 'tax_cents': 600,
                    'allocations': [{'business_source_ref': {
                        'namespace': 'contracts', 'kind': 'contract_line',
                        'id': str(self.line.pk), 'version': 1},
                        'gross_cents': 6000, 'net_cents': 5400, 'tax_cents': 600}]}],
            expected_version=1, request_token=uuid.uuid4(), dry_run=False)
        recognition = self.recognition(amount=5400)
        from pmo_plugins.management_revenue.models import RevenueAdjustment
        RevenueAdjustment.objects.create(
            organization=self.org, project=self.project, recognition=recognition,
            adjustment_cents=-1000, reason='未来调整', occurred_on=self.today + timedelta(days=10),
            posting_period=(self.today + timedelta(days=10)).strftime('%Y-%m'),
            status='posted', created_by=self.owner)
        today_snapshot = reconciliation.invoice_pairing(
            self.owner, invoice_id=invoice['invoice_ref']['id'])
        self.assertEqual(today_snapshot['invoice']['recognized_by_project'][str(self.project.pk)],
                         5400)
        future_snapshot = reconciliation.invoice_pairing(
            self.owner, invoice_id=invoice['invoice_ref']['id'],
            as_of=self.today + timedelta(days=30))
        self.assertEqual(future_snapshot['invoice']['recognized_by_project'][str(self.project.pk)],
                         4400)

    def test_multi_project_invoice_compares_per_project(self):
        from core.models import Project
        other = Project.objects.create(
            organization=self.org, name='跨项目', client='客户',
            code='P-MULTI-' + uuid.uuid4().hex[:6], manager=self.owner,
            start_date=self.today, end_date=self.today + timedelta(days=30))
        other_settlement = RevenuePairingFixture.settlement(
            self, amount=4000, project=other)
        other_item = other_settlement.items.first()
        invoice = self._invoice_allocated_to([
            {'business_source_ref': {'namespace': 'contracts', 'kind': 'contract_line',
                                     'id': str(self.line.pk), 'version': 1},
             'gross_cents': 6000, 'net_cents': 5400, 'tax_cents': 600},
            {'business_source_ref': {'namespace': 'settlement', 'kind': 'open_item',
                                     'id': str(other_item.pk), 'version': 1},
             'gross_cents': 4000, 'net_cents': 3600, 'tax_cents': 400},
        ])
        self.recognition(amount=5400)
        snapshot = reconciliation.invoice_pairing(
            self.owner, invoice_id=invoice['invoice_ref']['id'])
        self.assertEqual(snapshot['invoice']['project_ids'],
                         sorted([str(self.project.pk), str(other.pk)]))
        self.assertNotIn('revenue_invoice_mismatch',
                         [issue['code'] for issue in snapshot['invoice']['issues']])
        self.assertIn(str(other.pk), snapshot['invoice']['recognized_by_project'])
        self.assertIsNone(snapshot['invoice']['recognized_by_project'][str(other.pk)])
        self.assertIn('revenue_missing',
                      [item['code'] for item in snapshot['invoice']['missing']])

    def test_project_filter_mismatch_denied(self):
        from core.models import Project
        invoice, _ = self.allocated()
        other = Project.objects.create(
            organization=self.org, name='错项目', client='客户',
            code='P-DENY-' + uuid.uuid4().hex[:6], manager=self.owner,
            start_date=self.today, end_date=self.today + timedelta(days=30))
        with self.assertRaises(PermissionDenied):
            reconciliation.invoice_pairing(
                self.owner, invoice_id=invoice['invoice_ref']['id'], project_id=other.pk)
