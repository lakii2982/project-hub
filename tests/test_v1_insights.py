"""PH-339 insights read models: reconciliation cases and project workbench."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.test import TestCase

from core.models import (AuditEvent, DomainEvent, LedgerEntry, LedgerPosting,
                         Organization, SourceConsumption)
from core.plugin_runtime import set_enabled
from pmo_plugins.contracts import approval_services as contracts
from pmo_plugins.insights import precheck_services, services
from pmo_plugins.insights.models import (MetricSnapshot, ReconciliationCase,
                                         ReconciliationRefresh)
from tests.test_v1_ops_reconciliation_overview import OpsOverviewFixture

CANONICAL_CASE_KEYS = {
    'id', 'organization_id', 'version', 'status', 'source_refs',
    'difference_cents', 'owner_member_id', 'resolution_note', 'case_key',
    'rule_code', 'coverage_key', 'snapshot_watermark',
}
BUSINESS_MODELS = (AuditEvent, DomainEvent, LedgerEntry, LedgerPosting,
                   SourceConsumption)


class InsightsServiceTests(OpsOverviewFixture, TestCase):
    def period(self):
        return self.today.strftime('%Y-%m')

    def refresh(self, member=None, *, token=None, dry_run=False, period=None, as_of=None):
        return services.refresh_reconciliation_cases(
            member or self.owner, period=period or self.period(),
            as_of=as_of or self.today.isoformat(),
            request_token=token or uuid.uuid4(), dry_run=dry_run)

    def case_for(self, data, rule_code='classification_missing'):
        return next(row for row in data['cases'] if row['rule_code'] == rule_code)

    def business_counts(self):
        return tuple(model.objects.count() for model in BUSINESS_MODELS)

    def insights_counts(self):
        return (ReconciliationCase.objects.count(), MetricSnapshot.objects.count(),
                ReconciliationRefresh.objects.count())

    def update(self, case, decision='pending', *, note='分派处理', version=None,
               token=None, dry_run=False, member=None):
        return services.update_reconciliation_case(
            member or self.owner, case_id=case['id'], decision=decision, note=note,
            expected_version=version if version is not None else case['version'],
            request_token=token or uuid.uuid4(), dry_run=dry_run)

    def test_refresh_gold_sample_canonical_fields_and_zero_business_writes(self):
        cash = self.cash(classify=False)
        before = self.business_counts()
        data = self.refresh()
        self.assertEqual(before, self.business_counts())
        self.assertEqual(data['contract'], 'insights.reconciliation.refresh/v1')
        self.assertFalse(data['dry_run'])
        case = self.case_for(data)
        self.assertEqual(set(case), CANONICAL_CASE_KEYS)
        self.assertEqual(case['organization_id'], str(self.org.pk))
        self.assertEqual(case['owner_member_id'], str(self.owner.pk))
        self.assertEqual(case['status'], 'draft')
        self.assertEqual(case['version'], 1)
        self.assertIsInstance(case['difference_cents'], int)
        self.assertTrue(case['case_key'])
        self.assertTrue(case['coverage_key'].startswith(self.period() + '|cash|'))
        self.assertTrue(any(ref['kind'] == 'cash_transaction' and
                            ref['id'] == str(cash.pk) for ref in case['source_refs']))
        self.assertEqual(ReconciliationCase.objects.count(), len(data['created']))
        self.assertEqual(ReconciliationRefresh.objects.count(), 1)
        # No non-canonical columns such as period/scope/category/assignee/due_date/note.
        field_names = {field.name for field in ReconciliationCase._meta.fields}
        self.assertFalse({'period', 'scope', 'category', 'assignee', 'due_date',
                          'note'} & field_names)

    def test_dry_run_previews_without_writing(self):
        self.cash(classify=False)
        before = self.business_counts()
        data = self.refresh(dry_run=True)
        self.assertTrue(data['dry_run'])
        self.assertTrue(data['created'])
        self.assertIsNone(data['created'][0]['id'])
        self.assertEqual(self.business_counts(), before)
        self.assertEqual(self.insights_counts(), (0, 0, 0))

    def test_refresh_replay_is_idempotent_and_conflicting_input_is_rejected(self):
        self.cash(classify=False)
        token = uuid.uuid4()
        first = self.refresh(token=token)
        second = self.refresh(token=token)
        self.assertEqual(first['cases'], second['cases'])
        self.assertEqual(ReconciliationRefresh.objects.count(), 1)
        self.assertEqual(ReconciliationCase.objects.count(), len(first['created']))
        with self.assertRaises(ValidationError):
            self.refresh(token=token, period='2020-01', as_of='2020-01-10')
        self.assertEqual(ReconciliationRefresh.objects.count(), 1)

    def test_refresh_supersedes_old_case_and_keeps_history(self):
        self.cash(classify=False)
        first = self.refresh()
        old = self.case_for(first)
        # Same rule and same source identity; a newer as_of replaces the snapshot.
        second = self.refresh(as_of=(self.today + timedelta(days=1)).isoformat())
        new = self.case_for(second)
        self.assertNotEqual(new['id'], old['id'])
        old_row = ReconciliationCase.objects.get(pk=old['id'])
        self.assertEqual(old_row.status, 'superseded')
        self.assertEqual(old_row.version, 2)
        self.assertEqual(ReconciliationCase.objects.get(pk=new['id']).status, 'draft')
        self.assertEqual(ReconciliationCase.objects.count(), 2)
        self.assertEqual(second['superseded'][0]['id'], old['id'])

    def test_refresh_unchanged_facts_do_not_churn_cases(self):
        self.cash(classify=False)
        first = self.refresh()
        second = self.refresh()
        self.assertEqual(second['created'], [])
        self.assertEqual(second['superseded'], [])
        self.assertTrue(second['unchanged'])
        self.assertEqual(ReconciliationCase.objects.count(), len(first['created']))

    def test_case_state_machine_draft_pending_approved_closed(self):
        self.cash(classify=False)
        case = self.case_for(self.refresh())
        pending = self.update(case, 'pending')
        self.assertEqual(pending['case']['status'], 'pending')
        self.assertEqual(pending['case']['version'], 2)
        approved = self.update(pending['case'], 'approved', note='独立核对通过')
        self.assertEqual(approved['case']['status'], 'approved')
        self.assertEqual(approved['case']['version'], 3)
        closed = self.update(approved['case'], 'closed', note='')
        self.assertEqual(closed['case']['status'], 'closed')
        self.assertEqual(closed['case']['version'], 4)
        with self.assertRaises(ValidationError):
            self.update(closed['case'], 'approved', note='不可复活')
        self.assertEqual(ReconciliationCase.objects.get(pk=case['id']).status, 'closed')

    def test_rejected_is_terminal_and_skips_illegal_transitions(self):
        self.cash(classify=False)
        case = self.case_for(self.refresh())
        with self.assertRaises(ValidationError):
            self.update(case, 'approved', note='不能跳过待确认')
        pending = self.update(case, 'pending')
        rejected = self.update(pending['case'], 'rejected', note='证据不足')
        self.assertEqual(rejected['case']['status'], 'rejected')
        with self.assertRaises(ValidationError):
            self.update(rejected['case'], 'approved', note='终态不可复活')
        with self.assertRaises(ValidationError):
            self.update(rejected['case'], 'pending')

    def test_update_requires_note_for_approval_and_rejection(self):
        self.cash(classify=False)
        pending = self.update(self.case_for(self.refresh()), 'pending')
        with self.assertRaises(ValidationError):
            self.update(pending['case'], 'approved', note='   ')
        with self.assertRaises(ValidationError):
            self.update(pending['case'], 'rejected', note='')

    def test_update_idempotency_and_version_conflict(self):
        self.cash(classify=False)
        case = self.case_for(self.refresh())
        baseline = ReconciliationRefresh.objects.count()
        token = uuid.uuid4()
        first = self.update(case, 'pending', token=token)
        second = self.update(case, 'pending', token=token)
        self.assertEqual(first['case'], second['case'])
        self.assertEqual(ReconciliationRefresh.objects.count(), baseline + 1)
        with self.assertRaises(ValidationError):
            self.update(case, 'pending', note='不同输入', token=token)
        with self.assertRaises(ValidationError):
            self.update(first['case'], 'approved', note='版本过期', version=1)

    def test_update_dry_run_writes_nothing(self):
        self.cash(classify=False)
        case = self.case_for(self.refresh())
        before = self.insights_counts()
        preview = self.update(case, 'pending', dry_run=True)
        self.assertTrue(preview['dry_run'])
        self.assertEqual(preview['case']['status'], 'pending')
        self.assertEqual(self.insights_counts(), before)
        self.assertEqual(ReconciliationCase.objects.get(pk=case['id']).status, 'draft')

    def test_update_does_not_write_business_facts(self):
        self.cash(classify=False)
        case = self.case_for(self.refresh())
        pending = self.update(case, 'pending')
        before = self.business_counts()
        self.update(pending['case'], 'approved', note='核对通过')
        self.assertEqual(before, self.business_counts())

    def test_member_denied_auditor_read_only_write_roles(self):
        self.cash(classify=False)
        self.assertRaises(PermissionDenied, lambda: services.reconciliation_case_list(self.plain))
        self.assertRaises(PermissionDenied, lambda: self.refresh(self.plain))
        self.assertRaises(PermissionDenied, lambda: self.refresh(self.auditor))
        self.assertRaises(PermissionDenied, lambda: self.refresh(self.pm))
        self.assertRaises(PermissionDenied,
                          lambda: services.project_workbench_summary(self.plain, self.project.pk))
        self.assertTrue(services.reconciliation_case_list(self.auditor)['cases'] is not None)
        self.assertEqual(services.project_workbench_summary(
            self.auditor, self.project.pk)['project_id'], str(self.project.pk))

    def test_pm_workbench_only_managed_project(self):
        self.assertEqual(services.project_workbench_summary(
            self.pm, self.project.pk)['scope'], 'project')
        self.assertRaises(PermissionDenied,
                          lambda: services.project_workbench_summary(self.pm, self.project2.pk))

    def test_cross_org_project_and_case_are_denied(self):
        self.cash(classify=False)
        case = self.case_for(self.refresh())
        outsider = Organization.objects.create(name='PH339 其他公司')
        from django.contrib.auth import get_user_model
        from core.models import Membership
        outsider_owner = Membership.objects.create(
            organization=outsider,
            user=get_user_model().objects.create_user(username='ph339' + uuid.uuid4().hex),
            role='owner')
        self.assertRaises(PermissionDenied, lambda: services.project_workbench_summary(
            outsider_owner, self.project.pk))
        self.assertRaises(PermissionDenied, lambda: services.reconciliation_case_snapshot(
            outsider_owner, case['id']))

    def test_disabled_module_keeps_reads_and_blocks_writes(self):
        self.cash(classify=False)
        self.case_for(self.refresh())
        set_enabled(self.owner, 'insights', False)
        try:
            self.assertRaises(PermissionDenied, lambda: self.refresh())
            self.assertIsInstance(services.reconciliation_case_list(self.owner)['cases'], list)
            self.assertEqual(services.project_workbench_summary(
                self.owner, self.project.pk)['project_id'], str(self.project.pk))
        finally:
            set_enabled(self.owner, 'insights', True)

    def test_period_must_match_as_of_and_token_must_be_uuid(self):
        with self.assertRaises(ValidationError):
            self.refresh(period='2020-02')
        with self.assertRaises(ValidationError):
            services.refresh_reconciliation_cases(
                self.owner, period=self.period(), as_of=self.today.isoformat(),
                request_token='not-a-token', dry_run=True)

    def test_future_as_of_is_recorded_as_warning(self):
        future = (self.today + timedelta(days=3)).isoformat()
        data = self.refresh(as_of=future)
        self.assertEqual(data['warnings'][0]['issue_code'], 'future_as_of')

    def test_case_list_filters_and_pm_scope_is_hidden(self):
        self.cash(classify=False)
        data = self.refresh()
        listing = services.reconciliation_case_list(self.owner, period=self.period())
        self.assertEqual(listing['scope'], 'company')
        self.assertEqual(len(listing['cases']), len(data['cases']))
        drafts = services.reconciliation_case_list(self.owner, status='draft')
        self.assertTrue(all(row['status'] == 'draft' for row in drafts['cases']))
        with self.assertRaises(ValidationError):
            services.reconciliation_case_list(self.owner, status='open')
        self.assertEqual(services.reconciliation_case_list(self.pm)['cases'], [])
        self.assertEqual(services.reconciliation_case_list(
            self.owner, period='2020-01')['cases'], [])

    def test_case_snapshot_canonical_and_pm_denied(self):
        self.cash(classify=False)
        case = self.case_for(self.refresh())
        snapshot = services.reconciliation_case_snapshot(self.owner, case['id'])
        self.assertEqual(set(snapshot), CANONICAL_CASE_KEYS)
        self.assertEqual(snapshot['case_key'], case['case_key'])
        self.assertRaises(PermissionDenied,
                          lambda: services.reconciliation_case_snapshot(self.pm, case['id']))
        self.assertRaises(PermissionDenied,
                          lambda: services.reconciliation_case_snapshot(self.owner, uuid.uuid4()))

    def test_workbench_missing_is_null_plus_missing_and_zero_write(self):
        before_business = self.business_counts()
        before_insights = self.insights_counts()
        data = services.project_workbench_summary(
            self.owner, self.project.pk, as_of=self.today.isoformat())
        self.assertEqual(before_business, self.business_counts())
        self.assertEqual(before_insights, self.insights_counts())
        self.assertIsNone(data['cash_net_cents'])
        self.assertIsNone(data['cost_cents'])
        self.assertIsNone(data['budget_etc_cents'])
        codes = {entry['missing_code'] for entry in data['missing']}
        self.assertIn('cash_project_scope_unavailable', codes)
        self.assertIn('incurred_cost_missing', codes)
        self.assertIn('approved_budget_missing', codes)
        mapping = {
            'contract_value': 'contract_value_cents',
            'recognized_revenue': 'recognized_revenue_cents',
            'cost': 'cost_cents',
            'cash_net': 'cash_net_cents',
            'open_item_remaining': 'open_item_remaining_cents',
            'budget_etc': 'budget_etc_cents',
        }
        for entry in data['missing']:
            base = entry['missing_code'].replace('_missing', '').replace(
                '_unavailable', '').replace('_scope', '_net')
            if base in mapping:
                self.assertIsNone(data[mapping[base]], entry)
        # Metric snapshots are an in-memory projection only; no internal snapshot row.
        self.assertEqual(MetricSnapshot.objects.count(), 0)
        self.assertEqual(len(data['metric_snapshots']), 6)

    def test_workbench_derives_contract_revenue_and_open_items(self):
        settlement = self.settlement_with_item(amount=50000)
        item = settlement.items.get()
        cash = self.cash(amount=30000)
        self.clearance(item, cash.pk, amount=30000)
        self.recognition(amount=40000)
        data = services.project_workbench_summary(
            self.owner, self.project.pk, as_of=self.today.isoformat())
        totals = contracts.approved_contract_totals(self.owner, self.project.pk)
        self.assertEqual(data['contract_value_cents'], totals['net_cents'])
        self.assertEqual(data['recognized_revenue_cents'], 40000)
        self.assertEqual(data['open_item_remaining_cents'], 20000)
        values = {row['metric_id']: row['value_cents'] for row in data['metric_snapshots']}
        self.assertEqual(values['recognized_revenue'], 40000)
        self.assertEqual(values['open_item_remaining'], 20000)
        self.assertIsNone(values['cash_net'])

    def test_workbench_future_as_of_warns_and_truncates_facts(self):
        future = (self.today + timedelta(days=3)).isoformat()
        data = services.project_workbench_summary(self.owner, self.project.pk, as_of=future)
        self.assertEqual(data['as_of'], future)
        self.assertEqual(data['facts_as_of'], self.today.isoformat())
        self.assertEqual(data['warnings'][0]['issue_code'], 'future_as_of')

    def test_period_precheck_is_the_ph354_snapshot(self):
        direct = precheck_services.period_precheck_snapshot(
            self.owner, period=self.period(), as_of=self.today.isoformat())
        reused = services.period_precheck(
            self.owner, period=self.period(), as_of=self.today.isoformat())
        self.assertEqual(direct['precheck_digest'], reused['precheck_digest'])

    def test_model_guards_append_only_and_terminal(self):
        self.cash(classify=False)
        case = self.case_for(self.refresh())
        row = ReconciliationCase.objects.get(pk=case['id'])
        row.rule_code = 'tampered'
        self.assertRaises(ValidationError, row.save)
        self.assertRaises(ValidationError, row.delete)
        snapshot = MetricSnapshot.objects.create(
            organization=self.org, metric_key='contract_value', period=self.period(),
            project=self.project, as_of=self.today, value_cents=1, source_version='v1')
        snapshot.value_cents = 2
        self.assertRaises(ValidationError, snapshot.save)
        self.assertRaises(ValidationError, snapshot.delete)
        receipt = ReconciliationRefresh.objects.first()
        receipt.input_digest = 'x' * 64
        self.assertRaises(ValidationError, receipt.save)
        self.assertRaises(ValidationError, receipt.delete)
        stale = ReconciliationCase.objects.get(pk=case['id'])
        stale.status = 'approved'
        self.assertRaises(ValidationError, stale.save)

    def test_database_triggers_block_direct_case_and_readmodel_mutation(self):
        self.cash(classify=False)
        case = self.case_for(self.refresh())
        row = ReconciliationCase.objects.get(pk=case['id'])
        snapshot = MetricSnapshot.objects.create(
            organization=self.org, metric_key='contract_value', period=self.period(),
            project=self.project, as_of=self.today, value_cents=1, source_version='v1')
        case_id, snapshot_id = row.pk.hex, snapshot.pk.hex

        def raw(sql, params):
            with self.assertRaises(Exception):
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute(sql, params)

        raw("UPDATE insights_reconciliationcase SET rule_code = 'tampered' WHERE id = %s", [case_id])
        raw("UPDATE insights_reconciliationcase SET snapshot_as_of = NULL WHERE id = %s", [case_id])
        raw("DELETE FROM insights_reconciliationcase WHERE id = %s", [case_id])
        raw("UPDATE insights_metricsnapshot SET value_cents = 2 WHERE id = %s", [snapshot_id])
        raw("DELETE FROM insights_metricsnapshot WHERE id = %s", [snapshot_id])
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("UPDATE insights_reconciliationcase SET status = 'pending' WHERE id = %s", [case_id])
        self.assertEqual(ReconciliationCase.objects.get(pk=row.pk).status, 'pending')

    def test_update_rejects_non_canonical_decisions_and_bad_case_id(self):
        self.cash(classify=False)
        case = self.case_for(self.refresh())
        with self.assertRaises(ValidationError):
            self.update(case, 'resolved')
        with self.assertRaises(ValidationError):
            services.update_reconciliation_case(
                self.owner, case_id='bogus', decision='pending', note='',
                expected_version=1, request_token=uuid.uuid4(), dry_run=True)
