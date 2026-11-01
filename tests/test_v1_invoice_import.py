"""PH-338 sales-invoice batch import: frozen template, precheck and atomic commit."""
import csv
import hashlib
import io
import json
import uuid
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from core.imports import HEADERS, commit_rows
from core.models import (AccountingEvent, AuditEvent, DomainEvent, LedgerEntry,
                         Membership, Organization, PluginState, SourceConsumption)
from pmo_plugins.insights import precheck_services
from pmo_plugins.invoicing import services
from pmo_plugins.invoicing.models import (Invoice, InvoiceAllocation, InvoiceLine)
from pmo_plugins.onboarding import import_commit_services as commits
from pmo_plugins.onboarding import preview_services as previews
from pmo_plugins.onboarding.models import ImportPreview, ImportSubmissionReceipt
from tests.test_v1_invoice import InvoiceFixture
from tests.test_v1_revenue_pairing_read import RevenuePairingFixture


def canonical_digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
        default=str).encode()).hexdigest()


class InvoiceImportFixture(InvoiceFixture):
    def setUp(self):
        super().setUp()
        # The shared PH-354 company precheck must not be blocked by the settlement
        # fixture: recognize the full contract line before import commits run.
        self.recognition(60000)
        self.party_name = self.contract.party.display_name
        self.evidence = json.dumps(
            [{'kind': 'invoice_document', 'evidence_id': 'scan-1'}])

    # --- CSV / rows -------------------------------------------------------
    def source_ref(self, kind='contract_line'):
        if kind == 'open_item':
            return {'namespace': 'settlement', 'kind': 'open_item',
                    'id': str(self.open_item.pk), 'version': 1}
        return {'namespace': 'contracts', 'kind': 'contract_line',
                'id': str(self.line.pk), 'version': 1}

    def invoice_rows(self, number, *, lines=None, counterparty_name=None,
                     invoice_date=None, evidence_json=None, gross=None):
        """Return CSV row mappings for one invoice.

        ``lines`` is a list of ``(line_no, line_gross, [(source_kind, amount)])``.
        """
        lines = lines or [
            (1, 6000, [('contract_line', 6000)]),
            (2, 4000, [('open_item', 4000)]),
        ]
        header_gross = gross if gross is not None else sum(line[1] for line in lines)
        header_tax = header_gross // 10
        rows = []
        for line_no, line_gross, allocations in lines:
            line_tax = line_gross // 10
            line_net = line_gross - line_tax
            for source_kind, amount in allocations:
                ref = self.source_ref(source_kind)
                tax = amount // 10
                rows.append({
                    'source_id': f'{number}-{line_no}-{len(rows) + 1}',
                    'counterparty_name': counterparty_name or self.party_name,
                    'invoice_number': number,
                    'invoice_date': (invoice_date or self.today).isoformat(),
                    'gross_cents': str(header_gross),
                    'net_cents': str(header_gross - header_tax),
                    'tax_cents': str(header_tax),
                    'evidence_json': evidence_json or self.evidence,
                    'line_no': str(line_no),
                    'line_gross_cents': str(line_gross),
                    'line_net_cents': str(line_net),
                    'line_tax_cents': str(line_tax),
                    'allocation_namespace': ref['namespace'],
                    'allocation_kind': ref['kind'],
                    'allocation_id': ref['id'],
                    'allocation_version': str(ref['version']),
                    'allocation_gross_cents': str(amount),
                    'allocation_net_cents': str(amount - tax),
                    'allocation_tax_cents': str(tax),
                    'note': '演练备注',
                })
        return rows

    def csv_bytes(self, rows):
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=HEADERS['invoicing'])
        writer.writeheader()
        writer.writerows(rows)
        return buffer.getvalue().encode('utf-8')

    def preview(self, rows, *, member=None, kind='invoicing'):
        return previews.preview_import(
            member or self.owner, kind=kind, file_name='invoices.csv',
            content=self.csv_bytes(rows))

    def persist(self, rows, *, conflicts=(), member=None):
        return commits.persist_preview(
            member or self.owner, kind='invoicing', file_name='invoices.csv',
            rows=rows, conflicts=conflicts)

    def preview_and_persist(self, rows, *, conflicts=(), **kwargs):
        result = self.preview(rows, **kwargs)
        preview = self.persist(result['rows'],
                               conflicts=conflicts or result['conflicts'])
        return result, preview

    # --- commit -----------------------------------------------------------
    def snapshot(self, *, member=None, as_of=None, period=None):
        return precheck_services.period_precheck_snapshot(
            member or self.owner, scope='company',
            period=period or self.today.strftime('%Y-%m'),
            as_of=as_of or self.today)

    def commit_kwargs(self, preview, *, snapshot=None, **changes):
        snapshot = snapshot or self.snapshot()
        values = {
            'preview_ref': {'namespace': 'onboarding', 'kind': 'import_preview',
                            'id': str(preview.pk), 'version': preview.version},
            'rows_digest': preview.rows_digest,
            'precheck_digest': snapshot['precheck_digest'],
            'precheck_as_of_date': self.today,
            'period': self.today.strftime('%Y-%m'),
            'request_token': uuid.uuid4(),
            'expected_version': preview.version,
            'dry_run': False,
        }
        values.update(changes)
        return values

    def commit(self, preview, *, member=None, **changes):
        return commits.commit_import(
            member or self.owner, **self.commit_kwargs(preview, **changes))

    def assert_rejected(self, call, issue_code):
        with self.assertRaises(commits.ImportCommitRejected) as raised:
            call()
        codes = [entry['issue_code'] for entry in raised.exception.envelope['row_errors']]
        self.assertIn(issue_code, codes)
        return raised.exception.envelope

    def counts(self):
        return tuple(model.objects.count() for model in (
            Invoice, InvoiceLine, InvoiceAllocation, ImportSubmissionReceipt,
            SourceConsumption, LedgerEntry, AccountingEvent, AuditEvent,
            DomainEvent))

    def assert_zero_business(self):
        self.assertEqual(Invoice.objects.count(), 0)
        self.assertEqual(InvoiceLine.objects.count(), 0)
        self.assertEqual(InvoiceAllocation.objects.count(), 0)
        self.assertEqual(ImportSubmissionReceipt.objects.count(), 0)
        self.assertEqual(SourceConsumption.objects.count(), 0)
        self.assertEqual(LedgerEntry.objects.count(), 0)


class InvoiceTemplateAndPreviewTests(InvoiceImportFixture, TestCase):
    def test_frozen_template_columns_and_detection(self):
        self.assertEqual(HEADERS['invoicing'], [
            'source_id', 'counterparty_name', 'invoice_number', 'invoice_date',
            'gross_cents', 'net_cents', 'tax_cents', 'evidence_json', 'line_no',
            'line_gross_cents', 'line_net_cents', 'line_tax_cents',
            'allocation_namespace', 'allocation_kind', 'allocation_id',
            'allocation_version', 'allocation_gross_cents',
            'allocation_net_cents', 'allocation_tax_cents', 'note'])
        rows = self.invoice_rows('INV-T1')
        result = self.preview(rows)
        self.assertEqual(result['template'], 'invoicing')
        self.assertEqual(result['summary']['row_count'], 2)
        self.assertEqual(result['row_errors'], [])
        self.assertFalse(result['summary']['persisted'])
        self.assertEqual(result['mapped_fields'][0]['field'], 'source_id')

    def test_extra_columns_are_template_mismatch(self):
        rows = self.invoice_rows('INV-T2')
        content = self.csv_bytes(rows).decode().replace(
            '\n', ',extra\n', 1).encode()
        with self.assertRaises(ValidationError):
            previews.preview_import(self.owner, kind='invoicing',
                                    file_name='bad.csv', content=content)

    def test_duplicate_source_id_is_rejected(self):
        rows = self.invoice_rows('INV-SRC')
        rows[1]['source_id'] = rows[0]['source_id']
        result = self.preview(rows)
        self.assertIn('duplicate_source_id',
                      {error['code'] for error in result['row_errors']})

    def test_counterparty_must_be_unique_active_customer(self):
        rows = self.invoice_rows('INV-T3', counterparty_name='不存在客户')
        result = self.preview(rows)
        self.assertIn('counterparty_unresolved',
                      {error['code'] for error in result['row_errors']})

        from pmo_plugins.contracts.models import Counterparty
        Counterparty.objects.filter(pk=self.party.pk).update(status='inactive')
        result = self.preview(self.invoice_rows('INV-T4'))
        self.assertIn('counterparty_unresolved',
                      {error['code'] for error in result['row_errors']})

    def test_duplicate_invoice_number_and_totals(self):
        self.record(invoice_number='INV-DUP')
        result = self.preview(self.invoice_rows('INV-DUP'))
        self.assertIn('invoice_number_duplicate',
                      {error['code'] for error in result['row_errors']})

        bad = self.invoice_rows('INV-LINE')
        bad[0]['allocation_gross_cents'] = '5000'
        bad[0]['allocation_net_cents'] = '4500'
        bad[0]['allocation_tax_cents'] = '500'
        result = self.preview(bad)
        self.assertIn('line_total_mismatch',
                      {error['code'] for error in result['row_errors']})

        bad = self.invoice_rows('INV-HEAD', gross=12000)
        result = self.preview(bad)
        self.assertIn('invoice_total_mismatch',
                      {error['code'] for error in result['row_errors']})

    def test_source_quota_and_counterparty_consistency(self):
        small = RevenuePairingFixture.settlement(self, amount=100, items_total=100)
        self.open_item = small.items.first()
        result = self.preview(self.invoice_rows('INV-QUOTA'))
        self.assertIn('source_quota_exceeded',
                      {error['code'] for error in result['row_errors']})

        other = self.make_party(display_name='另一客户')
        rows = self.invoice_rows('INV-PARTY', counterparty_name=other.display_name)
        result = self.preview(rows)
        self.assertIn('row_source_unapproved',
                      {error['code'] for error in result['row_errors']})

        rows = (self.invoice_rows(
            'INV-Q1', lines=[(1, 30000, [('open_item', 30000)])])
            + self.invoice_rows(
                'INV-Q2', lines=[(1, 30000, [('open_item', 30000)])]))
        result = self.preview(rows)
        self.assertIn('source_quota_exceeded',
                      {error['code'] for error in result['row_errors']})

    def test_preview_is_read_only_and_deterministic(self):
        before = (Invoice.objects.count(), InvoiceLine.objects.count(),
                  InvoiceAllocation.objects.count(), LedgerEntry.objects.count())
        first = self.preview(self.invoice_rows('INV-P1'))
        second = self.preview(self.invoice_rows('INV-P1'))
        self.assertEqual(first['rows'], second['rows'])
        self.assertEqual(before, (Invoice.objects.count(), InvoiceLine.objects.count(),
                                  InvoiceAllocation.objects.count(),
                                  LedgerEntry.objects.count()))
        audit = AuditEvent.objects.filter(action='onboarding.import_previewed').latest('id')
        self.assertEqual(audit.payload['kind'], 'invoicing')

    def test_preview_permissions(self):
        rows = self.invoice_rows('INV-P2')
        self.assertEqual(self.preview(rows, member=self.finance)['template'],
                         'invoicing')
        self.assertEqual(self.preview(rows, member=self.auditor)['template'],
                         'invoicing')
        for member in (self.pm, self.plain):
            with self.assertRaises(PermissionDenied):
                self.preview(rows, member=member)
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='onboarding',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        with self.assertRaises(PermissionDenied):
            self.preview(rows)


class InvoiceImportCommitTests(InvoiceImportFixture, TestCase):
    def test_gold_multi_invoice_atomic_commit(self):
        rows = (self.invoice_rows('INV-A') + self.invoice_rows(
            'INV-B', lines=[(1, 5000, [('contract_line', 5000)]),
                            (2, 3000, [('open_item', 3000)])]))
        result, preview = self.preview_and_persist(rows)
        self.assertEqual(result['row_errors'], [])
        receipt = self.commit(preview)
        self.assertEqual(receipt['status'], 'committed')
        self.assertEqual(Invoice.objects.count(), 2)
        self.assertEqual(InvoiceLine.objects.count(), 4)
        self.assertEqual(InvoiceAllocation.objects.count(), 4)
        self.assertEqual(ImportSubmissionReceipt.objects.count(), 1)
        namespaces = {ref['namespace'] for ref in receipt['result_refs']}
        kinds = {ref['kind'] for ref in receipt['result_refs']}
        self.assertEqual(namespaces, {'invoicing'})
        self.assertEqual(kinds, {'invoice', 'invoice_line', 'invoice_allocation'})
        preview.refresh_from_db()
        self.assertEqual(preview.status, ImportPreview.STATUS_CONSUMED)
        # No revenue/cash/ledger fact is invented by the import layer.
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.assertEqual(AccountingEvent.objects.count(), 0)

    def test_same_token_same_input_replays_receipt(self):
        _result, preview = self.preview_and_persist(self.invoice_rows('INV-REPLAY'))
        token = uuid.uuid4()
        first = self.commit(preview, request_token=token)
        before = self.counts()
        second = self.commit(preview, request_token=token)
        self.assertEqual(first['receipt_id'], second['receipt_id'])
        self.assertTrue(second['replayed'])
        self.assertEqual(before, self.counts())
        self.assertEqual(Invoice.objects.count(), 1)

    def test_same_token_changed_input_conflicts(self):
        _result, preview = self.preview_and_persist(self.invoice_rows('INV-CONFLICT'))
        token = uuid.uuid4()
        self.commit(preview, request_token=token)
        before = self.counts()
        self.assert_rejected(
            lambda: self.commit(preview, request_token=token, period='2000-01'),
            'request_token_conflict')
        self.assertEqual(before, self.counts())

    def test_preview_stale_and_consumed(self):
        _result, preview = self.preview_and_persist(self.invoice_rows('INV-STALE'))
        before = self.counts()
        self.assert_rejected(
            lambda: self.commit(preview, rows_digest='0' * 64), 'preview_stale')
        self.assert_rejected(
            lambda: self.commit(preview, expected_version=preview.version + 1),
            'preview_stale')
        self.assertEqual(before, self.counts())
        self.commit(preview)
        before = self.counts()
        self.assert_rejected(lambda: self.commit(preview), 'preview_consumed')
        self.assertEqual(before, self.counts())

    def test_precheck_stale_and_blocked(self):
        _result, preview = self.preview_and_persist(self.invoice_rows('INV-PRECHECK'))
        before = self.counts()
        self.assert_rejected(
            lambda: self.commit(preview, precheck_digest='0' * 64),
            'precheck_stale')
        self.assertEqual(before, self.counts())
        from core.models import AccountingPeriod
        AccountingPeriod.objects.create(
            organization=self.org, period=self.today.strftime('%Y-%m'),
            status='posted', closed_through=self.today)
        envelope = self.assert_rejected(
            lambda: self.commit(preview), 'precheck_blocked')
        self.assertTrue(envelope['precheck']['blocked'])
        self.assert_zero_business()

    def test_unresolved_blocker_conflict(self):
        _result, preview = self.preview_and_persist(
            self.invoice_rows('INV-CF'), conflicts=[
                {'row_no': 2, 'field': 'gross_cents',
                 'conflict_code': 'amount_unbalanced', 'severity': 'blocker'}])
        before = self.counts()
        envelope = self.assert_rejected(
            lambda: self.commit(preview), 'import_conflict_unresolved')
        self.assertEqual(envelope['conflicts'][0]['conflict_code'],
                         'amount_unbalanced')
        self.assertEqual(before, self.counts())

    def test_duplicate_invoice_number_rejected_on_commit(self):
        self.record(invoice_number='INV-EXISTS')
        rows = self.invoice_rows('INV-EXISTS')
        preview = self.persist(rows, conflicts=[])
        before = self.counts()
        self.assert_rejected(lambda: self.commit(preview),
                             'invoice_number_duplicate')
        self.assertEqual(before, self.counts())

    def test_line_and_invoice_total_mismatch_rejected_on_commit(self):
        bad = self.invoice_rows('INV-LINE-BAD')
        bad[0]['allocation_gross_cents'] = '5000'
        bad[0]['allocation_net_cents'] = '4500'
        bad[0]['allocation_tax_cents'] = '500'
        preview = self.persist(bad, conflicts=[])
        before = self.counts()
        self.assert_rejected(lambda: self.commit(preview), 'line_total_mismatch')
        self.assertEqual(before, self.counts())

        bad = self.invoice_rows('INV-HEAD-BAD', gross=12000)
        preview = self.persist(bad, conflicts=[])
        before = self.counts()
        self.assert_rejected(lambda: self.commit(preview),
                             'invoice_total_mismatch')
        self.assertEqual(before, self.counts())

    def test_source_quota_and_counterparty_inconsistency_rejected(self):
        small = RevenuePairingFixture.settlement(self, amount=100, items_total=100)
        self.open_item = small.items.first()
        preview = self.persist(self.invoice_rows('INV-QUOTA-C'), conflicts=[])
        before = self.counts()
        self.assert_rejected(lambda: self.commit(preview), 'source_quota_exceeded')
        self.assertEqual(before, self.counts())

        other = self.make_party(display_name='另一客户')
        preview = self.persist(
            self.invoice_rows('INV-PARTY-C', counterparty_name=other.display_name),
            conflicts=[])
        before = self.counts()
        self.assert_rejected(lambda: self.commit(preview), 'row_source_unapproved')
        self.assertEqual(before, self.counts())

    def test_cross_org_preview_is_denied(self):
        other = Organization.objects.create(name='另一演练公司')
        other_owner = Membership.objects.create(
            organization=other,
            user=get_user_model().objects.create_user(
                username='cross-invoice-' + uuid.uuid4().hex), role='owner')
        preview = self.persist(self.invoice_rows('INV-CROSS'), member=other_owner)
        with self.assertRaises(commits.ImportCommitDenied) as raised:
            self.commit(preview)
        codes = [entry['issue_code']
                 for entry in raised.exception.envelope['row_errors']]
        self.assertIn('cross_org', codes)
        self.assertEqual(ImportSubmissionReceipt.objects.count(), 0)
        self.assertEqual(Invoice.objects.count(), 0)

    def test_unauthorized_roles_and_module_disable(self):
        _result, preview = self.preview_and_persist(self.invoice_rows('INV-ROLE'))
        for member in (self.auditor, self.plain, self.pm):
            with self.assertRaises(PermissionDenied):
                self.commit(preview, member=member)
        self.assertEqual(ImportSubmissionReceipt.objects.count(), 0)
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='onboarding',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        with self.assertRaises(commits.ImportCommitDenied) as raised:
            self.commit(preview)
        codes = [entry['issue_code']
                 for entry in raised.exception.envelope['row_errors']]
        self.assertIn('module_disabled', codes)
        self.assert_zero_business()

    def test_dry_run_zero_writes(self):
        _result, preview = self.preview_and_persist(self.invoice_rows('INV-DRY'))
        before = self.counts()
        result = self.commit(preview, dry_run=True)
        self.assertEqual(result['status'], 'dry_run')
        self.assertTrue(result['would_commit'])
        self.assertEqual(before, self.counts())
        preview.refresh_from_db()
        self.assertEqual(preview.status, ImportPreview.STATUS_READY)

    def test_owner_rejection_rolls_back_whole_batch(self):
        rows = self.invoice_rows('INV-OK1') + self.invoice_rows('INV-FAIL')
        _result, preview = self.preview_and_persist(rows)
        before = self.counts()
        original = services.record_invoice

        def flaky(member, **kwargs):
            if kwargs.get('invoice_number') == 'INV-FAIL':
                raise ValidationError('同一方向发票号码已存在')
            return original(member, **kwargs)

        with mock.patch('pmo_plugins.invoicing.services.record_invoice',
                        side_effect=flaky):
            envelope = self.assert_rejected(
                lambda: self.commit(preview), 'invoice_number_duplicate')
        self.assertEqual(envelope['status'], 'rejected')
        self.assertEqual(before, self.counts())
        self.assert_zero_business()
        preview.refresh_from_db()
        self.assertEqual(preview.status, ImportPreview.STATUS_READY)

    def test_duplicate_import_key_and_row_business_date(self):
        _result, preview = self.preview_and_persist(self.invoice_rows('INV-KEY'))
        self.commit(preview)
        ImportPreview.objects.filter(pk=preview.pk).update(status='ready')
        before = self.counts()
        self.assert_rejected(lambda: self.commit(preview), 'import_key_duplicate')
        self.assertEqual(before, self.counts())

        previous = self.today.replace(day=1) - timedelta(days=1)
        old_preview = self.persist(
            self.invoice_rows('INV-OLD', invoice_date=previous), conflicts=[])
        before = self.counts()
        self.assert_rejected(lambda: self.commit(old_preview), 'row_date_invalid')
        self.assertEqual(before, self.counts())

    def test_generic_commit_entry_rejects_invoicing(self):
        with self.assertRaises(ValidationError):
            commit_rows(self.owner, 'invoicing',
                        self.invoice_rows('INV-GENERIC'))
        self.assertEqual(Invoice.objects.count(), 0)
