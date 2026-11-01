"""PH-353 onboarding import preview persistence and atomic submission services."""
import hashlib
import json
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingPeriod, AuditEvent, Document, LedgerEntry,
                         Membership, Organization, PluginState, SourceConsumption,
                         Timesheet)
from pmo_plugins.insights import precheck_services
from pmo_plugins.onboarding import import_commit_services as commits
from pmo_plugins.onboarding.models import (ImportConflict, ImportPreview,
                                           ImportSubmissionReceipt)
from tests.test_v1_import_preview import ImportPreviewFixture


def canonical_digest(value):
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(',', ':'),
        default=str).encode()).hexdigest()


class ImportSubmissionFixture(ImportPreviewFixture):
    def expense_row(self, source_id='E-1', reference='INV-1', **changes):
        row = {
            'source_id': source_id, 'project_code': 'P1', 'title': '差旅费',
            'amount': '120.50', 'tax': '6.00', 'recoverable_tax': '1',
            'occurred_on': self.today.isoformat(), 'counterparty': '示例客户',
            'reference': reference, 'note': '演练备注',
        }
        row.update(changes)
        return row

    def rows(self):
        return [self.expense_row('E-1', 'INV-1'),
                self.expense_row('E-2', 'INV-2', amount='88.00', tax='0.00',
                                 recoverable_tax='0')]

    def persist(self, *, rows=None, conflicts=(), kind='expense', member=None):
        return commits.persist_preview(
            member or self.owner, kind=kind, file_name='demo.csv',
            rows=self.rows() if rows is None else rows, conflicts=conflicts)

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
            Document, Timesheet, LedgerEntry, SourceConsumption, AuditEvent,
            ImportSubmissionReceipt))


class ImportPreviewPersistenceTests(ImportSubmissionFixture, TestCase):
    def test_preview_persists_canonical_rows_and_digest_without_business_facts(self):
        before = self.counts()
        preview = self.persist(conflicts=[
            {'row_no': 2, 'field': 'amount', 'conflict_code': 'amount_unbalanced',
             'severity': 'blocker'}])
        self.assertEqual(preview.status, ImportPreview.STATUS_READY)
        self.assertEqual(preview.kind, 'expense')
        self.assertEqual(preview.version, 1)
        self.assertEqual(preview.rows_json, self.rows())
        self.assertEqual(preview.rows_digest, canonical_digest(self.rows()))
        self.assertEqual(preview.created_by_member_id, self.owner.pk)
        conflict = preview.conflicts.get()
        self.assertEqual(conflict.conflict_code, 'amount_unbalanced')
        self.assertEqual(conflict.severity, ImportConflict.SEVERITY_BLOCKER)
        self.assertIsNone(conflict.resolution)
        self.assertEqual(conflict.conflict_key, canonical_digest({
            'organization_id': str(self.org.pk), 'kind': 'expense',
            'preview_id': str(preview.pk), 'row_no': 2, 'field': 'amount',
            'source_ref': {'namespace': 'onboarding', 'kind': 'import_preview',
                           'id': str(preview.pk), 'version': 1},
            'conflict_code': 'amount_unbalanced'}))
        self.assertEqual(before, self.counts())

    def test_duplicate_conflict_keys_are_deduplicated(self):
        conflict = {'row_no': 2, 'field': 'amount',
                    'conflict_code': 'amount_unbalanced', 'severity': 'error'}
        preview = self.persist(conflicts=[conflict, dict(conflict)])
        self.assertEqual(preview.conflicts.count(), 1)

    def test_persist_accepts_preview_service_rows(self):
        result = super().preview(self.expense_csv())
        preview = commits.persist_preview(
            self.owner, kind='expense', file_name='demo.csv',
            rows=result['rows'], conflicts=result['conflicts'])
        self.assertEqual(preview.rows_json[0]['source_id'], 'E-1')
        self.assertEqual(preview.rows_json[0]['amount'], '120.50')
        self.assertEqual(preview.conflicts.count(), 0)

    def test_persist_roles_module_and_org(self):
        with self.assertRaises(PermissionDenied):
            self.persist(member=self.auditor)
        with self.assertRaises(PermissionDenied):
            self.persist(member=self.plain)
        self.assertEqual(self.persist(member=self.finance).kind, 'expense')
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='onboarding',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        with self.assertRaises(PermissionDenied):
            self.persist()

    def test_conflict_resolution_is_one_way_and_history_is_retained(self):
        preview = self.persist(conflicts=[
            {'row_no': 2, 'field': 'amount', 'conflict_code': 'amount_unbalanced',
             'severity': 'blocker'}])
        conflict = preview.conflicts.get()
        resolved = commits.resolve_conflict(
            self.finance, conflict_id=conflict.pk, resolution='accept')
        self.assertEqual(resolved.resolution, 'accept')
        self.assertEqual(resolved.resolved_by_member_id, self.finance.pk)
        self.assertIsNotNone(resolved.resolved_at)
        with self.assertRaises(ValidationError):
            commits.resolve_conflict(
                self.owner, conflict_id=conflict.pk, resolution='reject')
        conflict.refresh_from_db()
        self.assertEqual(conflict.resolution, 'accept')
        self.assertEqual(preview.conflicts.count(), 1)


class ImportCommitTests(ImportSubmissionFixture, TestCase):
    def test_gold_preview_to_atomic_commit(self):
        preview = self.persist()
        result = self.commit(preview)
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(result['row_count'], 2)
        self.assertEqual(len(result['result_refs']), 2)
        self.assertEqual(result['preview_ref']['id'], str(preview.pk))
        self.assertEqual(Document.objects.count(), 2)
        self.assertEqual(ImportSubmissionReceipt.objects.count(), 1)
        preview.refresh_from_db()
        self.assertEqual(preview.status, ImportPreview.STATUS_CONSUMED)
        receipt = ImportSubmissionReceipt.objects.get()
        self.assertEqual(receipt.status, 'committed')
        self.assertEqual(receipt.result_refs, result['result_refs'])
        self.assertEqual(receipt.status, ImportSubmissionReceipt.STATUS_COMMITTED)

    def test_same_token_same_input_replays_original_receipt(self):
        preview = self.persist()
        token = uuid.uuid4()
        first = self.commit(preview, request_token=token)
        before = self.counts()
        second = self.commit(preview, request_token=token)
        self.assertEqual(first['receipt_id'], second['receipt_id'])
        self.assertTrue(second['replayed'])
        self.assertEqual(Document.objects.count(), 2)
        self.assertEqual(before, self.counts())

    def test_same_token_changed_input_conflicts(self):
        preview = self.persist()
        token = uuid.uuid4()
        self.commit(preview, request_token=token)
        before = self.counts()
        self.assert_rejected(
            lambda: self.commit(preview, request_token=token, period='2000-01'),
            'request_token_conflict')
        self.assertEqual(before, self.counts())

    def test_expected_version_mismatch_is_stale(self):
        preview = self.persist()
        self.assert_rejected(
            lambda: self.commit(preview, expected_version=preview.version + 1),
            'preview_stale')

    def test_rows_digest_mismatch_is_preview_stale(self):
        preview = self.persist()
        before = self.counts()
        self.assert_rejected(
            lambda: self.commit(preview, rows_digest='0' * 64), 'preview_stale')
        self.assertEqual(before, self.counts())
        preview.refresh_from_db()
        self.assertEqual(preview.status, ImportPreview.STATUS_READY)

    def test_precheck_digest_mismatch_is_precheck_stale(self):
        preview = self.persist()
        before = self.counts()
        envelope = self.assert_rejected(
            lambda: self.commit(preview, precheck_digest='0' * 64), 'precheck_stale')
        self.assertFalse(envelope['precheck']['blocked'])
        self.assertIsNotNone(envelope['precheck']['digest'])
        self.assertEqual(before, self.counts())

    def test_period_and_as_of_must_share_month(self):
        preview = self.persist()
        before = self.counts()
        self.assert_rejected(
            lambda: self.commit(preview, period='2000-01'), 'precheck_stale')
        self.assertEqual(before, self.counts())

    def test_future_as_of_is_rejected(self):
        preview = self.persist()
        before = self.counts()
        self.assert_rejected(
            lambda: self.commit(
                preview, precheck_as_of_date=self.today + timedelta(days=1)),
            'future_date')
        self.assertEqual(before, self.counts())

    def test_closed_and_locked_periods_are_precheck_blocked(self):
        preview = self.persist()
        AccountingPeriod.objects.create(
            organization=self.org, period=self.today.strftime('%Y-%m'),
            status='posted', closed_through=self.today)
        blocked = self.assert_rejected(
            lambda: self.commit(preview), 'precheck_blocked')
        self.assertTrue(blocked['precheck']['blocked'])
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(ImportSubmissionReceipt.objects.count(), 0)

        AccountingPeriod.objects.filter(organization=self.org).delete()
        self.org.locked_through = self.today
        self.org.save(update_fields=['locked_through'])
        self.assert_rejected(lambda: self.commit(preview), 'precheck_blocked')
        self.assertEqual(Document.objects.count(), 0)

    def test_unresolved_blocker_conflict_rejects(self):
        preview = self.persist(conflicts=[
            {'row_no': 2, 'field': 'amount', 'conflict_code': 'amount_unbalanced',
             'severity': 'blocker'}])
        before = self.counts()
        envelope = self.assert_rejected(
            lambda: self.commit(preview), 'import_conflict_unresolved')
        self.assertEqual(envelope['conflicts'][0]['conflict_code'],
                         'amount_unbalanced')
        self.assertEqual(before, self.counts())

    def test_resolved_blocker_conflict_allows_commit(self):
        preview = self.persist(conflicts=[
            {'row_no': 2, 'field': 'amount', 'conflict_code': 'amount_unbalanced',
             'severity': 'blocker'}])
        commits.resolve_conflict(
            self.owner, conflict_id=preview.conflicts.get().pk, resolution='merge')
        result = self.commit(preview)
        self.assertEqual(result['status'], 'committed')
        self.assertEqual(result['conflicts_resolved'][0]['resolution'], 'merge')
        self.assertEqual(Document.objects.count(), 2)

    def test_warning_conflict_does_not_block_and_is_recorded(self):
        preview = self.persist(conflicts=[
            {'row_no': 2, 'field': 'note',
             'conflict_code': 'precheck_warning', 'severity': 'warning'}])
        result = self.commit(preview)
        self.assertEqual(result['status'], 'committed')
        receipt = ImportSubmissionReceipt.objects.get()
        self.assertTrue(any(item.get('source') == 'conflict'
                            for item in receipt.warnings))
        self.assertTrue(any(str(item.get('source')).startswith('precheck')
                            for item in receipt.warnings))

    def test_cross_org_preview_is_denied(self):
        other = Organization.objects.create(name='另一演练公司')
        other_owner = Membership.objects.create(
            organization=other,
            user=get_user_model().objects.create_user(
                username='cross-' + uuid.uuid4().hex),
            role='owner')
        other_preview = commits.persist_preview(
            other_owner, kind='expense', file_name='other.csv',
            rows=[self.expense_row()], conflicts=[])
        with self.assertRaises(commits.ImportCommitDenied) as raised:
            self.commit(other_preview)
        codes = [entry['issue_code']
                 for entry in raised.exception.envelope['row_errors']]
        self.assertIn('cross_org', codes)
        self.assertEqual(ImportSubmissionReceipt.objects.count(), 0)

    def test_unauthorized_roles_and_module_disable(self):
        preview = self.persist()
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
        self.assertEqual(Document.objects.count(), 0)

    def test_dry_run_zero_writes(self):
        preview = self.persist()
        before = self.counts()
        result = self.commit(preview, dry_run=True)
        self.assertEqual(result['status'], 'dry_run')
        self.assertTrue(result['would_commit'])
        self.assertEqual(before, self.counts())
        preview.refresh_from_db()
        self.assertEqual(preview.status, ImportPreview.STATUS_READY)

    def test_owner_rejection_rolls_back_the_whole_batch(self):
        rows = self.rows()
        rows[1]['project_code'] = 'P9'
        preview = self.persist(rows=rows)
        before = self.counts()
        envelope = self.assert_rejected(
            lambda: self.commit(preview), 'owner_validation_failed')
        self.assertEqual(envelope['row_errors'][0]['row_no'], 3)
        self.assertEqual(before, self.counts())
        self.assertEqual(Document.objects.count(), 0)
        self.assertEqual(ImportSubmissionReceipt.objects.count(), 0)
        preview.refresh_from_db()
        self.assertEqual(preview.status, ImportPreview.STATUS_READY)

    def test_duplicate_import_key_is_rejected(self):
        preview = self.persist()
        self.commit(preview)
        ImportPreview.objects.filter(pk=preview.pk).update(status='ready')
        before = self.counts()
        self.assert_rejected(lambda: self.commit(preview), 'import_key_duplicate')
        self.assertEqual(before, self.counts())

    def test_consumed_preview_cannot_be_recommitted(self):
        preview = self.persist()
        self.commit(preview)
        before = self.counts()
        self.assert_rejected(lambda: self.commit(preview), 'preview_consumed')
        self.assertEqual(before, self.counts())

    def test_preview_not_found(self):
        self.assert_rejected(
            lambda: commits.commit_import(
                self.owner,
                **self.commit_kwargs(self.persist(), preview_ref=uuid.uuid4())),
            'preview_not_found')
