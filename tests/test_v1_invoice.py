"""PH-252 invoicing record/allocate/reverse invariants."""
import uuid
from datetime import timedelta
from io import StringIO

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AuditEvent, DomainEvent, LedgerEntry,
                         LedgerPosting, SourceConsumption)
from pmo_plugins.invoicing import services
from pmo_plugins.invoicing.models import (Invoice, InvoiceAllocation, InvoiceLine,
                                          InvoiceReversal)
from pmo_plugins.management_revenue.models import RevenueRecognition
from tests.test_v1_revenue_pairing_read import RevenuePairingFixture


class InvoiceFixture(RevenuePairingFixture):
    def setUp(self):
        super().setUp()
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'invoicing', True)
        self.finance = self.member('finance')
        self.plain = self.member('member')
        self.settlement = self.settlement(amount=40000)
        self.open_item = self.settlement.items.first()

    def record(self, member=None, **changes):
        values = dict(direction='sales', counterparty_id=self.contract.party_id,
                      invoice_number='INV-' + uuid.uuid4().hex[:8].upper(),
                      invoice_date=self.today, gross_cents=10000, net_cents=9000,
                      tax_cents=1000,
                      evidence=[{'kind': 'invoice_document', 'evidence_id': 'scan-1'}],
                      request_token=uuid.uuid4(), dry_run=False)
        values.update(changes)
        return services.record_invoice(member or self.owner, **values)

    def lines(self, contract_amount=6000, item_amount=4000):
        return [
            {'line_no': 1, 'gross_cents': contract_amount,
             'net_cents': contract_amount - contract_amount // 10,
             'tax_cents': contract_amount // 10,
             'allocations': [{'business_source_ref': {
                 'namespace': 'contracts', 'kind': 'contract_line',
                 'id': str(self.line.pk), 'version': 1},
                 'gross_cents': contract_amount,
                 'net_cents': contract_amount - contract_amount // 10,
                 'tax_cents': contract_amount // 10}]},
            {'line_no': 2, 'gross_cents': item_amount,
             'net_cents': item_amount - item_amount // 10,
             'tax_cents': item_amount // 10,
             'allocations': [{'business_source_ref': {
                 'namespace': 'settlement', 'kind': 'open_item',
                 'id': str(self.open_item.pk), 'version': 1},
                 'gross_cents': item_amount,
                 'net_cents': item_amount - item_amount // 10,
                 'tax_cents': item_amount // 10}]},
        ]

    def allocated(self, invoice=None):
        invoice = invoice or self.record()
        result = services.allocate_invoice_lines(
            self.owner, invoice_id=invoice['invoice_ref']['id'], lines=self.lines(),
            expected_version=1, request_token=uuid.uuid4(), dry_run=False)
        return invoice, result


class InvoiceRecordTests(InvoiceFixture, TestCase):
    def test_record_active_and_canonical_event_without_financial_write(self):
        before = (AccountingEvent.objects.count(), LedgerEntry.objects.count(),
                  RevenueRecognition.objects.count())
        result = self.record()
        self.assertEqual(result['status'], 'active')
        row = Invoice.objects.get(pk=result['invoice_ref']['id'])
        self.assertEqual(row.invoice_ref['kind'], 'invoice')
        event = DomainEvent.objects.get(topic='invoicing.recorded.v1', organization=self.org)
        self.assertEqual(event.data['source']['kind'], 'invoice')
        self.assertEqual(event.data['payload']['changed_refs'][0]['id'], str(row.pk))
        self.assertEqual((AccountingEvent.objects.count(), LedgerEntry.objects.count(),
                          RevenueRecognition.objects.count()), before)

    def test_duplicate_number_and_tax_split_rejected(self):
        first = self.record()
        with self.assertRaises(ValidationError):
            self.record(invoice_number=Invoice.objects.get(pk=first['invoice_ref']['id']).invoice_number)
        with self.assertRaises(ValidationError):
            self.record(gross_cents=10001)
        with self.assertRaises(ValidationError):
            self.record(gross_cents=0, net_cents=0, tax_cents=0)
        self.assertEqual(Invoice.objects.count(), 1)

    def test_future_locked_and_closed_period_rejected(self):
        with self.assertRaises(ValidationError):
            self.record(invoice_date=self.today + timedelta(days=1))
        from core.models import Organization
        Organization.objects.filter(pk=self.org.pk).update(locked_through=self.today)
        with self.assertRaises(ValidationError):
            self.record()
        Organization.objects.filter(pk=self.org.pk).update(locked_through=None)
        from core.models import AccountingPeriod
        AccountingPeriod.objects.create(organization=self.org,
                                        period=self.today.strftime('%Y-%m'),
                                        status='posted', closed_through=self.today)
        with self.assertRaises(ValidationError):
            self.record()

    def test_member_denied_and_auditor_read_only(self):
        with self.assertRaises(PermissionDenied):
            self.record(member=self.plain)
        with self.assertRaises(PermissionDenied):
            self.record(member=self.auditor)
        self.assertEqual(services.invoice_list(self.auditor), [])

    def test_token_replay_and_changed_input(self):
        token = uuid.uuid4()
        number = 'REPLAY-' + uuid.uuid4().hex[:8].upper()
        first = self.record(request_token=token, invoice_number=number)
        again = self.record(request_token=token, invoice_number=number)
        self.assertEqual(again['invoice_ref'], first['invoice_ref'])
        self.assertEqual(Invoice.objects.count(), 1)
        with self.assertRaises(ValidationError):
            self.record(request_token=token, invoice_number=number, gross_cents=12000,
                        net_cents=10800, tax_cents=1200)
        self.assertEqual(Invoice.objects.count(), 1)


class InvoiceAllocationTests(InvoiceFixture, TestCase):
    def test_allocate_lines_and_allocations(self):
        invoice, result = self.allocated()
        row = Invoice.objects.get(pk=invoice['invoice_ref']['id'])
        self.assertEqual(row.lines.count(), 2)
        self.assertEqual(sum(line.gross_cents for line in row.lines.all()), 10000)
        self.assertEqual(InvoiceAllocation.objects.filter(
            invoice_line__invoice=row).count(), 2)
        event = DomainEvent.objects.get(topic='invoicing.lines_allocated.v1')
        self.assertEqual(len(event.data['payload']['changed_refs']), 5)

    def test_line_total_and_over_allocation_rejected(self):
        invoice = self.record()
        bad = self.lines()
        bad[0]['gross_cents'] = 5000
        with self.assertRaises(ValidationError):
            services.allocate_invoice_lines(
                self.owner, invoice_id=invoice['invoice_ref']['id'], lines=bad,
                expected_version=1, request_token=uuid.uuid4(), dry_run=False)
        self.assertEqual(InvoiceLine.objects.count(), 0)
        invoice2 = self.record()
        over = self.lines()
        over[1]['allocations'][0]['gross_cents'] = 999999
        over[1]['allocations'][0]['net_cents'] = 899999
        over[1]['allocations'][0]['tax_cents'] = 100000
        with self.assertRaises(ValidationError):
            services.allocate_invoice_lines(
                self.owner, invoice_id=invoice2['invoice_ref']['id'], lines=over,
                expected_version=1, request_token=uuid.uuid4(), dry_run=False)
        self.assertEqual(InvoiceAllocation.objects.count(), 0)

    def test_allocation_idempotent_and_no_double_allocate(self):
        token = uuid.uuid4()
        invoice = self.record()
        first = services.allocate_invoice_lines(
            self.owner, invoice_id=invoice['invoice_ref']['id'], lines=self.lines(),
            expected_version=1, request_token=token, dry_run=False)
        second = services.allocate_invoice_lines(
            self.owner, invoice_id=invoice['invoice_ref']['id'], lines=self.lines(),
            expected_version=1, request_token=token, dry_run=False)
        self.assertEqual(second['line_refs'], first['line_refs'])
        self.assertEqual(InvoiceLine.objects.count(), 2)
        with self.assertRaises(ValidationError):
            services.allocate_invoice_lines(
                self.owner, invoice_id=invoice['invoice_ref']['id'], lines=self.lines(),
                expected_version=1, request_token=uuid.uuid4(), dry_run=False)
        self.assertEqual(InvoiceLine.objects.count(), 2)


class InvoiceReversalTests(InvoiceFixture, TestCase):
    def test_partial_reversal_keeps_active_and_full_reverses(self):
        invoice, _ = self.allocated()
        invoice_id = invoice['invoice_ref']['id']
        row = Invoice.objects.get(pk=invoice_id)
        line = row.lines.get(line_no=1)
        allocation = line.allocations.first()
        partial = services.reverse_invoice(
            self.owner, invoice_id=invoice_id, reason='部分红冲', reversal_date=self.today,
            line_reversals=[{'original_line_ref': line.line_ref, 'gross_cents': -3000,
                             'net_cents': -2700, 'tax_cents': -300}],
            allocation_reversals=[{'original_allocation_ref': allocation.allocation_ref,
                                   'original_line_ref': line.line_ref,
                                   'business_source_ref': allocation.business_source_ref,
                                   'gross_cents': -3000, 'net_cents': -2700,
                                   'tax_cents': -300}],
            expected_version=1, request_token=uuid.uuid4(), dry_run=False)
        self.assertFalse(partial['full'])
        self.assertEqual(Invoice.objects.get(pk=invoice_id).status, 'active')
        full = services.reverse_invoice(
            self.owner, invoice_id=invoice_id, reason='全额红冲', reversal_date=self.today,
            expected_version=1, request_token=uuid.uuid4(), dry_run=False)
        self.assertTrue(full['full'])
        self.assertEqual(Invoice.objects.get(pk=invoice_id).status, 'reversed')
        self.assertEqual(InvoiceReversal.objects.filter(invoice_id=invoice_id).count(), 2)
        with self.assertRaises(ValidationError):
            services.reverse_invoice(
                self.owner, invoice_id=invoice_id, reason='再次', reversal_date=self.today,
                expected_version=1, request_token=uuid.uuid4(), dry_run=False)

    def test_partial_reversal_over_line_and_foreign_allocation_rejected(self):
        invoice, _ = self.allocated()
        invoice_id = invoice['invoice_ref']['id']
        row = Invoice.objects.get(pk=invoice_id)
        line = row.lines.get(line_no=1)
        with self.assertRaises(ValidationError):
            services.reverse_invoice(
                self.owner, invoice_id=invoice_id, reason='超行', reversal_date=self.today,
                line_reversals=[{'original_line_ref': line.line_ref, 'gross_cents': -7000,
                                 'net_cents': -6300, 'tax_cents': -700}],
                allocation_reversals=[], expected_version=1,
                request_token=uuid.uuid4(), dry_run=False)
        with self.assertRaises(ValidationError):
            services.reverse_invoice(
                self.owner, invoice_id=invoice_id, reason='外来分配', reversal_date=self.today,
                line_reversals=[{'original_line_ref': line.line_ref, 'gross_cents': -3000,
                                 'net_cents': -2700, 'tax_cents': -300}],
                allocation_reversals=[{
                    'original_allocation_ref': {'namespace': 'invoicing',
                                                'kind': 'invoice_allocation',
                                                'id': str(uuid.uuid4()), 'version': 1},
                    'original_line_ref': line.line_ref, 'business_source_ref': {},
                    'gross_cents': -3000, 'net_cents': -2700, 'tax_cents': -300}],
                expected_version=1, request_token=uuid.uuid4(), dry_run=False)
        self.assertEqual(InvoiceReversal.objects.count(), 0)

    def test_tax_qualification_trigger_guard(self):
        from pmo_plugins.invoicing.models import TaxQualification
        row = TaxQualification.objects.create(
            organization=self.org, source_ref={'kind': 'invoice', 'id': 'x'},
            tax_cents=100, status='pending', qualification_status='unknown')
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                TaxQualification.objects.filter(pk=row.pk).update(qualification_status='recoverable')
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                TaxQualification.objects.filter(pk=row.pk).delete()
        with self.assertRaises(ValidationError):
            row.delete()

    def test_verify_integrity_probe_catches_bad_reversal_detail(self):
        invoice, _ = self.allocated()
        row = Invoice.objects.get(pk=invoice['invoice_ref']['id'])
        InvoiceReversal.objects.create(
            organization=self.org, invoice=row, reason='坏明细', reversal_date=self.today,
            line_reversals=[], allocation_reversals=[], gross_cents=-1, net_cents=-1,
            tax_cents=0, created_by=self.owner)
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_integrity', '--json', stdout=output)
        self.assertIn('invoice_reversal:', output.getvalue())

    def test_verify_integrity_probe_catches_malformed_invoice(self):
        Invoice.objects.create(
            organization=self.org, direction='sales', counterparty=self.contract.party,
            invoice_number='BROKEN-' + uuid.uuid4().hex[:6], invoice_date=self.today,
            gross_cents=10000, net_cents=9000, tax_cents=1000, created_by=self.owner)
        output = StringIO()
        with self.assertRaises(CommandError):
            call_command('verify_integrity', '--json', stdout=output)
        self.assertIn('invoice:', output.getvalue())

    def test_append_only_history_cannot_be_updated_or_deleted(self):
        invoice, _ = self.allocated()
        row = Invoice.objects.get(pk=invoice['invoice_ref']['id'])
        line = row.lines.first()
        for operation in (
                lambda: Invoice.objects.filter(pk=row.pk).update(invoice_number='HACK'),
                lambda: InvoiceLine.objects.filter(pk=line.pk).update(line_no=99),
                lambda: InvoiceAllocation.objects.filter(invoice_line=line).delete()):
            with transaction.atomic():
                with self.assertRaises(IntegrityError):
                    operation()
        with self.assertRaises(ValidationError):
            line.delete()
