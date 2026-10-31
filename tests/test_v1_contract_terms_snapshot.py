"""PH-287 read-only approved terms snapshot and settlement reconciliation regressions."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import AuditEvent, DomainEvent, Project
from pmo_plugins.contracts import change_services, terms_snapshot_services
from pmo_plugins.contracts.models import ContractLine
from pmo_plugins.settlement.models import OpenItem, Settlement
from tests.test_v1_contract_change import ContractChangeFixture


class TermsSnapshotFixture(ContractChangeFixture):
    def setUp(self):
        super().setUp()
        self.auditor = self.member('auditor')
        self.approved = self.approved_contract(effective_on=self.today - timedelta(days=5))
        self.approved_line = self.approved.lines.get()
        self.selection = self.approved_line.policy_selection

    def settlement(self, amount=50000, *, items_total=None, project=None, version=None,
                   line=None, settled_on=None, status='submitted', with_items=True):
        line = line or self.approved_line
        version = version or self.approved
        settlement = Settlement.objects.create(
            organization=self.org, project=project or self.project, contract_line=line,
            contract_version=version, selection=line.policy_selection,
            counterparty=self.contract.party,
            contract_line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                               'id': str(line.pk), 'version': 1},
            source_ref={}, source_performance=[], policy_version={}, pricing_configuration={},
            terms_snapshot={}, evidence=[], settled_on=settled_on or self.today,
            due_date=self.today, gross_cents=amount, net_cents=amount, tax_cents=0,
            status='submitted' if with_items else 'active', created_by=self.owner)
        if with_items:
            OpenItem.objects.create(
                organization=self.org, project=settlement.project, settlement=settlement,
                partition_kind='due', partition_key='ph287:' + uuid.uuid4().hex, term_ref={},
                counterparty=self.contract.party,
                payee_ref={'kind': 'counterparty', 'id': str(self.contract.party_id)},
                scope={'kind': 'project', 'id': str(settlement.project_id)}, source={},
                gross_cents=items_total if items_total is not None else amount, tax_cents=0,
                due_date=self.today, occurred_on=self.today, status='active')
        if with_items and status == 'active':
            Settlement.objects.filter(pk=settlement.pk).update(status='active', version=2)
        settlement.refresh_from_db()
        return settlement

    def facts(self):
        return (ContractLine.objects.count(), Settlement.objects.count(),
                OpenItem.objects.count(), AuditEvent.objects.count(), DomainEvent.objects.count())

    def codes(self, **kwargs):
        return {issue['issue_code'] for issue in
                terms_snapshot_services.terms_settlement_issues(self.owner, **kwargs)}


class TermsSnapshotServiceTests(TermsSnapshotFixture, TestCase):
    def test_gold_snapshot_and_clean_issues(self):
        settlement = self.settlement(50000)
        before = self.facts()
        snapshot = terms_snapshot_services.approved_terms_snapshot(
            self.owner, contract_id=self.contract.pk)
        issues = terms_snapshot_services.terms_settlement_issues(self.owner)
        self.assertEqual(before, self.facts())
        self.assertEqual(snapshot['contract_ref']['id'], str(self.contract.pk))
        self.assertEqual(snapshot['version_ref']['id'], str(self.approved.pk))
        self.assertEqual(snapshot['pricing_templates'], ['confirmed_time'])
        self.assertTrue(snapshot['lines'][0]['readable'])
        self.assertEqual(snapshot['lines'][0]['terms_total_cents'], self.approved_line.gross_cents)
        self.assertFalse(snapshot['terminated'])
        self.assertEqual(issues, [])
        # as_of before the approved effective date falls back to the latest approved snapshot.
        historical = terms_snapshot_services.approved_terms_snapshot(
            self.owner, contract_id=self.contract.pk,
            as_of=(self.today - timedelta(days=3)).isoformat())
        self.assertEqual(historical['version_ref']['id'], str(self.approved.pk))
        self.assertEqual(terms_snapshot_services.terms_settlement_issues(
            self.owner, as_of=self.today.isoformat()), [])
        with self.assertRaises(ValidationError):
            terms_snapshot_services.approved_terms_snapshot(
                self.owner, contract_id=self.contract.pk,
                as_of=(self.today - timedelta(days=10)).isoformat())

    def test_terms_amount_and_partition_anomalies(self):
        ContractLine.objects.filter(pk=self.approved_line.pk).update(gross_cents=100000)
        self.assertIn('terms_amount_mismatch', self.codes())
        self.settlement(50000, with_items=False, status='active')
        codes = self.codes()
        self.assertIn('settlement_partition_mismatch', codes)

    def test_repeated_consumption_and_cross_project_references(self):
        self.settlement(10000, with_items=False, status='active')
        self.settlement(20000, with_items=False, status='active')
        other = Project.objects.create(organization=self.org, code='P287', name='另一项目',
                                       manager=self.pm, start_date=self.today, end_date=self.today)
        self.settlement(30000, project=other, with_items=True, status='active')
        codes = self.codes()
        self.assertIn('line_repeated_consumption', codes)
        self.assertIn('cross_project_reference', codes)

    def test_termination_marks_snapshot_and_flags_late_settlement(self):
        change = self.draft_change(change_type='termination')
        self.decide(change)
        self.contract.refresh_from_db()
        snapshot = terms_snapshot_services.approved_terms_snapshot(
            self.owner, contract_id=self.contract.pk)
        self.assertTrue(snapshot['terminated'])
        self.assertEqual(snapshot['terminated_on'], self.today.isoformat())
        self.assertFalse(snapshot['settlement_guard']['readable'])
        self.settlement(10000, settled_on=self.today, with_items=False, status='active')
        self.assertIn('terminated_still_settled', self.codes())
        from pmo_plugins.settlement import receivable_services
        with self.assertRaises(ValidationError):
            receivable_services.save_settlement(
                self.owner, contract_line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                                               'id': str(self.approved_line.pk), 'version': 1},
                term_refs=[], request_token=uuid.uuid4(), settled_on=self.today, dry_run=False)

    def test_stale_version_and_old_terms_still_active(self):
        change = self.draft_change(lines=[self.line(unit_price=11000)])
        self.decide(change)
        old_line = self.approved_line
        self.contract.refresh_from_db()
        self.settlement(10000, line=old_line, version=self.approved, with_items=False, status='active')
        codes = self.codes()
        self.assertIn('old_terms_still_active', codes)
        self.assertIn('stale_version_referenced', codes)

    def test_permissions_scope_and_read_only(self):
        snapshot = terms_snapshot_services.approved_terms_snapshot(
            self.auditor, contract_id=self.contract.pk)
        self.assertFalse(snapshot.get('summary_only'))
        pm_snapshot = terms_snapshot_services.approved_terms_snapshot(
            self.pm, contract_id=self.contract.pk)
        self.assertTrue(pm_snapshot['summary_only'])
        with self.assertRaises((PermissionDenied, ValidationError)):
            terms_snapshot_services.approved_terms_snapshot(
                self.member('member'), contract_id=self.contract.pk)
        with self.assertRaises((PermissionDenied, ValidationError)):
            terms_snapshot_services.terms_settlement_issues(self.member('member'))
        other = Project.objects.create(organization=self.org, code='P287X', name='越权项目',
                                       manager=self.owner, start_date=self.today, end_date=self.today)
        other_contract = self.make_contract(project_id=other.pk, contract_code='C287X')
        with self.assertRaises((PermissionDenied, ValidationError)):
            terms_snapshot_services.approved_terms_snapshot(self.pm, contract_id=other_contract.pk)
        from core.plugin_runtime import get_registry, set_enabled
        set_enabled(self.owner, 'invoicing', False)
        for plugin_id in ('onboarding', 'time', 'management_revenue', 'settlement', 'delivery', 'contracts'):
            if plugin_id in get_registry().manifests:
                set_enabled(self.owner, plugin_id, False)
        before = self.facts()
        snapshot = terms_snapshot_services.approved_terms_snapshot(
            self.auditor, contract_id=self.contract.pk)
        self.assertEqual(snapshot['contract_ref']['id'], str(self.contract.pk))
        self.assertEqual(before, self.facts())
