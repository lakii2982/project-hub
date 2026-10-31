"""PH-284 read-only management revenue / settlement / contract pairing regressions."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import AuditEvent, DomainEvent, Project
from pmo_plugins.contracts.models import ContractLine
from pmo_plugins.delivery.models import Acceptance
from pmo_plugins.management_revenue import pairing_read_services as pairing
from pmo_plugins.management_revenue.models import RevenueRecognition
from pmo_plugins.settlement.models import Clearance, OpenItem, Settlement
from tests.test_v1_contract_change import ContractChangeFixture


class RevenuePairingFixture(ContractChangeFixture):
    def setUp(self):
        super().setUp()
        self.auditor = self.member('auditor')
        self.approved = self.approved_contract()
        self.line = self.approved.lines.get()
        self.selection = self.line.policy_selection

    def cash_account(self):
        from pmo_plugins.cash.models import FundingAccount
        return FundingAccount.objects.create(
            organization=self.org, name='收入配对账户' + uuid.uuid4().hex,
            opening_on=self.today, status='active', created_by=self.owner)

    def recognition(self, amount=60000, **changes):
        values = dict(organization=self.org, project=self.project, contract_line=self.line,
                      created_by=self.owner, contract_line_ref={
                          'namespace': 'contracts', 'kind': 'contract_line',
                          'id': str(self.line.pk), 'version': 1},
                      performance_ref={}, policy_version={}, pricing_snapshot={},
                      accepted_snapshot={}, recognized_net_cents=amount,
                      occurred_on=self.today, posting_period=self.today.strftime('%Y-%m'),
                      status='posted')
        values.update(changes)
        acceptance = values.pop('acceptance', None)
        if acceptance is None:
            acceptance = Acceptance.objects.create(
                organization=self.org, project=values['project'], contract_line=values['contract_line'],
                contract_version=self.approved, selection=self.selection,
                line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                          'id': str(values['contract_line'].pk), 'version': 1},
                policy_version={}, pricing_configuration={}, approved_digest='a' * 64,
                evidence=[{'kind': 'acceptance', 'reference': '客户验收'}],
                confirmed_on=self.today, accepted_net_cents=amount, status='confirmed',
                created_by_member=self.owner)
        values['acceptance'] = acceptance
        return RevenueRecognition.objects.create(**values)

    def settlement(self, amount=40000, *, status='active', items_total=None, line=None,
                   settled_on=None, project=None):
        line = line or self.line
        settlement = Settlement.objects.create(
            organization=self.org, project=project or self.project, contract_line=line,
            contract_version=line.contract_version, selection=line.policy_selection,
            counterparty=self.contract.party,
            contract_line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                               'id': str(line.pk), 'version': 1},
            source_ref={}, source_performance=[], policy_version={}, pricing_configuration={},
            terms_snapshot={}, evidence=[], settled_on=settled_on or self.today,
            due_date=self.today, gross_cents=amount, net_cents=amount, tax_cents=0,
            status='submitted', created_by=self.owner)
        OpenItem.objects.create(
            organization=self.org, project=settlement.project, settlement=settlement,
            partition_kind='due', partition_key='ph284:' + uuid.uuid4().hex, term_ref={},
            counterparty=self.contract.party,
            payee_ref={'kind': 'counterparty', 'id': str(self.contract.party_id)},
            scope={'kind': 'project', 'id': str(settlement.project_id)}, source={},
            gross_cents=items_total if items_total is not None else amount, tax_cents=0,
            due_date=self.today, occurred_on=self.today, status='active')
        if status == 'active':
            Settlement.objects.filter(pk=settlement.pk).update(status='active', version=2)
        settlement.refresh_from_db()
        return settlement

    def clearance(self, settlement, amount=30000):
        item = settlement.items.first()
        return Clearance.objects.create(
            organization=self.org, open_item=item, cash_transaction_id=uuid.uuid4(),
            cash_transaction_ref={}, cleared_cents=amount, occurred_on=self.today,
            status='active', created_by=self.owner)

    def codes(self, **filters):
        return {issue['issue_code'] for issue in pairing.revenue_pairing_issues(
            self.owner, **filters)}


class RevenuePairingServiceTests(RevenuePairingFixture, TestCase):
    def test_gold_snapshot_derives_values_and_clean_issues(self):
        self.recognition(60000)
        settlement = self.settlement(40000)
        self.clearance(settlement, 30000)
        before = (AuditEvent.objects.count(), DomainEvent.objects.count(),
                  RevenueRecognition.objects.count(), Settlement.objects.count())
        snapshot = pairing.revenue_pairing_snapshot(self.owner, project_id=self.project.pk)
        self.assertEqual(snapshot['contract_value_cents'], 100000)
        self.assertEqual(snapshot['recognized_cents'], 60000)
        self.assertEqual(snapshot['settled_cents'], 40000)
        self.assertEqual(snapshot['unsettled_cents'], 10000)
        self.assertEqual(snapshot['cash_coverage_cents'], 30000)
        self.assertEqual(snapshot['difference_cents'], 20000)
        self.assertEqual(snapshot['contract_variance_cents'], 40000)
        self.assertEqual(snapshot['issues'], [])
        self.assertEqual(pairing.revenue_pairing_issues(self.owner), [])
        self.assertEqual(before, (AuditEvent.objects.count(), DomainEvent.objects.count(),
                                  RevenueRecognition.objects.count(), Settlement.objects.count()))
        aggregate = pairing.revenue_pairing_snapshot(self.owner)
        self.assertEqual(aggregate['recognized_cents'], 60000)
        self.assertEqual(len(aggregate['projects']), 1)

    def test_period_and_as_of_filters_are_read_only(self):
        self.recognition(60000)
        self.settlement(40000)
        self.assertEqual(pairing.revenue_pairing_snapshot(
            self.owner, project_id=self.project.pk, period=self.today.strftime('%Y-%m'))[
            'recognized_cents'], 60000)
        self.assertEqual(pairing.revenue_pairing_snapshot(
            self.owner, project_id=self.project.pk, period='2000-01')[
            'recognized_cents'], 0)
        history = pairing.revenue_pairing_snapshot(
            self.owner, project_id=self.project.pk,
            as_of=(self.today - timedelta(days=1)).isoformat())
        self.assertEqual(history['recognized_cents'], 0)
        self.assertEqual(history['settled_cents'], 0)
        with self.assertRaises(ValidationError):
            pairing.revenue_pairing_snapshot(
                self.owner, project_id=self.project.pk,
                as_of=(self.today + timedelta(days=1)).isoformat())
        with self.assertRaises(ValidationError):
            pairing.revenue_pairing_snapshot(self.owner, project_id=self.project.pk, period='2026-13')

    def test_recognition_over_cap_and_settlement_without_recognition(self):
        self.recognition(120000)
        self.assertIn('recognized_over_contract_line', self.codes())
        line = ContractLine.objects.create(
            organization=self.org, contract_version=self.approved, line_no=2,
            net_cents=50000, tax_cents=0, gross_cents=50000, status='approved')
        from pmo_plugins.contracts.models import MethodPolicySelection
        selection = MethodPolicySelection.objects.create(
            organization=self.org, contract_line=line, selected_on=self.today,
            approved_by_member=self.owner, approval_audit=self.approved.approval_audit,
            pricing_configuration={'template': 'confirmed_time', 'net_cap_cents': 50000,
                                   'time_unit': 'hour', 'unit_minutes': 60,
                                   'unit_price_cents': 10000, 'currency': 'CNY',
                                   'confirmed_by_customer_required': True,
                                   'policy_ref': {'policy_id': 'x', 'version': 1}})
        self.settlement(20000, line=line)
        self.assertIn('settlement_without_recognition', self.codes())

    def test_termination_and_cross_project_issues(self):
        change = self.draft_change(change_type='termination')
        self.decide(change)
        self.contract.refresh_from_db()
        self.recognition(10000, occurred_on=self.today, project=self.project)
        codes = self.codes()
        self.assertIn('recognized_after_termination', codes)
        self.settlement(10000)
        self.assertIn('settlement_after_termination', self.codes())
        other = Project.objects.create(organization=self.org, code='P284', name='错位项目',
                                       manager=self.pm, start_date=self.today, end_date=self.today)
        self.recognition(5000, project=other)
        self.assertIn('cross_project_reference', self.codes())

    def test_permissions_scope_and_disabled_module(self):
        self.recognition(10000)
        self.assertEqual(pairing.revenue_pairing_snapshot(
            self.auditor, project_id=self.project.pk)['recognized_cents'], 10000)
        self.assertEqual(pairing.revenue_pairing_snapshot(
            self.finance, project_id=self.project.pk)['recognized_cents'], 10000)
        pm_snapshot = pairing.revenue_pairing_snapshot(self.pm, project_id=self.project.pk)
        self.assertEqual(pm_snapshot['recognized_cents'], 10000)
        with self.assertRaises((PermissionDenied, ValidationError)):
            pairing.revenue_pairing_snapshot(self.member('member'), project_id=self.project.pk)
        with self.assertRaises((PermissionDenied, ValidationError)):
            pairing.revenue_pairing_issues(self.member('member'))
        other = Project.objects.create(organization=self.org, code='P284X', name='越权项目',
                                       manager=self.owner, start_date=self.today, end_date=self.today)
        with self.assertRaises((PermissionDenied, ValidationError)):
            pairing.revenue_pairing_snapshot(self.pm, project_id=other.pk)
        from core.plugin_runtime import get_registry, set_enabled
        if 'management_revenue' in get_registry().manifests:
            set_enabled(self.owner, 'management_revenue', False)
        snapshot = pairing.revenue_pairing_snapshot(self.auditor, project_id=self.project.pk)
        self.assertEqual(snapshot['recognized_cents'], 10000)
