"""PH-320 tax qualification confirmation, owner-link orchestration and read model."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, connection, transaction
from django.test import TestCase

from core.models import AccountingEvent, AuditEvent, DomainEvent, LedgerEntry
from pmo_plugins.invoicing import services
from pmo_plugins.invoicing.models import TaxQualification
from tests.test_v1_invoice import InvoiceFixture


class TaxFixture(InvoiceFixture):
    def setUp(self):
        super().setUp()
        self.invoice = self.record()
        self.invoice_id = self.invoice['invoice_ref']['id']
        self.invoice_row = __import__('pmo_plugins.invoicing.models', fromlist=['Invoice']).Invoice.objects.get(
            pk=self.invoice_id)

    def open(self, **changes):
        values = dict(source_ref=self.invoice_row.invoice_ref, tax_cents=1000,
                      evidence=[{'kind': 'tax_invoice', 'evidence_id': 'tax-1'}],
                      request_token=uuid.uuid4(), dry_run=False)
        values.update(changes)
        return services.open_tax_qualification(self.owner, **values)

    def confirm(self, opened, **changes):
        values = dict(qualification_status='recoverable', decided_on=self.today,
                      evidence=[{'kind': 'tax_decision', 'evidence_id': 'd1'}],
                      request_token=uuid.uuid4(), expected_version=1, dry_run=False)
        values.update(changes)
        return services.confirm_tax_qualification(
            self.finance, opened['qualification_ref']['id'], **values)


class TaxQualificationTests(TaxFixture, TestCase):
    def test_pending_to_confirmed_append_only_and_no_owner_facts(self):
        before = (AccountingEvent.objects.count(), LedgerEntry.objects.count(),
                  DomainEvent.objects.count())
        opened = self.open()
        row = TaxQualification.objects.get(pk=opened['qualification_ref']['id'])
        self.assertEqual((row.status, row.qualification_status), ('pending', 'unknown'))
        confirmed = self.confirm(opened)
        row.refresh_from_db()
        self.assertEqual(row.status, 'confirmed')
        self.assertEqual(row.qualification_status, 'recoverable')
        self.assertEqual(row.decided_on, self.today)
        self.assertEqual(row.version, 2)
        self.assertEqual(confirmed['qualification_ref'], row.__class__ and {
            'namespace': 'invoicing', 'kind': 'tax_qualification',
            'id': str(row.pk), 'version': 2})
        self.assertEqual(before, (AccountingEvent.objects.count(), LedgerEntry.objects.count(),
                                  DomainEvent.objects.count()))
        self.assertTrue(AuditEvent.objects.filter(
            action='plugin.invoicing.tax_qualification_confirmed',
            organization=self.org).exists())
        snapshot = services.invoice_tax_snapshot(self.owner, self.invoice_id)
        self.assertEqual(snapshot['effective_qualification']['qualification_status'], 'recoverable')
        self.assertEqual(snapshot['issues'], [])

    def test_duplicate_confirmation_and_supersede(self):
        opened = self.open()
        self.confirm(opened)
        with self.assertRaises(ValidationError):
            self.confirm(opened)
        with self.assertRaises(ValidationError):
            self.open()
        replacement = services.supersede_tax_qualification(
            self.finance, opened['qualification_ref']['id'],
            qualification_status='not_recoverable', decided_on=self.today,
            evidence=[{'kind': 'tax_decision', 'evidence_id': 'd2'}],
            request_token=uuid.uuid4(), expected_version=2, dry_run=False)
        old = TaxQualification.objects.get(pk=opened['qualification_ref']['id'])
        new = TaxQualification.objects.get(pk=replacement['qualification_ref']['id'])
        self.assertEqual(old.status, 'superseded')
        self.assertEqual((new.status, new.qualification_status), ('confirmed', 'not_recoverable'))
        snapshot = services.invoice_tax_snapshot(self.owner, self.invoice_id)
        self.assertEqual(snapshot['effective_qualification']['qualification_status'],
                         'not_recoverable')
        self.assertIn('not_recoverable_costing_review_required',
                      [issue['issue_code'] for issue in snapshot['issues']])

    def test_permissions_period_and_append_only_guards(self):
        opened = self.open()
        with self.assertRaises(PermissionDenied):
            services.confirm_tax_qualification(
                self.plain, opened['qualification_ref']['id'], qualification_status='recoverable',
                decided_on=self.today, evidence=[{'kind': 'x', 'evidence_id': '1'}],
                request_token=uuid.uuid4(), expected_version=1, dry_run=False)
        with self.assertRaises(PermissionDenied):
            services.invoice_tax_snapshot(self.pm, self.invoice_id)
        with self.assertRaises(ValidationError):
            self.confirm(opened, decided_on=(self.today + timedelta(days=1)))
        with self.assertRaises(ValidationError):
            self.confirm(opened, evidence=[])
        with self.assertRaises(ValidationError):
            self.confirm(opened, expected_version=99)
        # direct UPDATE of tax facts is blocked by the vendor-aware trigger
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('UPDATE invoicing_taxqualification SET tax_cents=%s WHERE id=%s',
                                   [999, opened['qualification_ref']['id'].replace('-', '')])
        # direct DELETE is blocked
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute('DELETE FROM invoicing_taxqualification WHERE id=%s',
                                   [opened['qualification_ref']['id'].replace('-', '')])

    def test_orchestration_reference_chain(self):
        opened = self.open()
        self.confirm(opened)
        link = services.link_invoice_orchestration(
            self.owner, invoice_id=self.invoice_id, link_kind='settlement_adjustment',
            owner_ref={'namespace': 'settlement', 'kind': 'settlement',
                       'id': str(self.settlement.pk), 'version': self.settlement.version},
            evidence=[{'kind': 'owner_fact', 'evidence_id': 's1'}],
            request_token=uuid.uuid4(), expected_version=1, dry_run=False)
        self.assertEqual(link['link_kind'], 'settlement_adjustment')
        snapshot = services.invoice_tax_snapshot(self.owner, self.invoice_id)
        self.assertEqual(len(snapshot['orchestration_links']), 1)
        self.assertTrue(snapshot['orchestration_links'][0]['exists'])
        with self.assertRaises(ValidationError):
            services.link_invoice_orchestration(
                self.owner, invoice_id=self.invoice_id, link_kind='settlement_adjustment',
                owner_ref={'namespace': 'settlement', 'kind': 'settlement',
                           'id': str(self.settlement.pk), 'version': self.settlement.version},
                evidence=[{'kind': 'owner_fact', 'evidence_id': 's1'}],
                request_token=uuid.uuid4(), expected_version=1, dry_run=False)
        with self.assertRaises(ValidationError):
            services.link_invoice_orchestration(
                self.owner, invoice_id=self.invoice_id, link_kind='cash_refund',
                owner_ref={'namespace': 'cash', 'kind': 'cash_refund',
                           'id': str(uuid.uuid4()), 'version': 1},
                evidence=[{'kind': 'owner_fact', 'evidence_id': 'c1'}],
                request_token=uuid.uuid4(), expected_version=1, dry_run=False)
        rows = services.invoice_tax_list(self.auditor)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['orchestration_link_count'], 1)


class TaxOrchestrationAfterReversalTests(TaxFixture, TestCase):
    def test_reversal_reference_chain_is_read_only_link(self):
        invoice, _ = self.allocated()
        invoice_id = invoice['invoice_ref']['id']
        from pmo_plugins.invoicing.models import Invoice
        row = Invoice.objects.get(pk=invoice_id)
        services.reverse_invoice(
            self.owner, invoice_id=invoice_id, reason='全额红冲', reversal_date=self.today,
            expected_version=1, request_token=uuid.uuid4(), dry_run=False)
        reversal = row.reversals.first()
        opened = services.open_tax_qualification(
            self.owner, source_ref=row.invoice_ref, tax_cents=row.tax_cents,
            evidence=[{'kind': 'tax_invoice', 'evidence_id': 'tax-rev'}],
            request_token=uuid.uuid4(), dry_run=False)
        services.confirm_tax_qualification(
            self.finance, opened['qualification_ref']['id'],
            qualification_status='recoverable', decided_on=self.today,
            evidence=[{'kind': 'tax_decision', 'evidence_id': 'rev-d'}],
            request_token=uuid.uuid4(), expected_version=1, dry_run=False)
        services.link_invoice_orchestration(
            self.owner, invoice_id=invoice_id, link_kind='settlement_adjustment',
            owner_ref={'namespace': 'settlement', 'kind': 'settlement',
                       'id': str(self.settlement.pk), 'version': self.settlement.version},
            evidence=[{'kind': 'owner_fact', 'evidence_id': 'rev-s'}],
            request_token=uuid.uuid4(), expected_version=1,
            reversal_id=reversal.pk, dry_run=False)
        snapshot = services.invoice_tax_snapshot(self.owner, invoice_id)
        self.assertEqual(snapshot['invoice']['status'], 'reversed')
        self.assertEqual(snapshot['orchestration_links'][0]['reversal_ref']['id'],
                         str(reversal.pk))
        self.assertEqual(snapshot['issues'], [])
