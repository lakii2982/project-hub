"""PH-291 cross-domain read-only operations reconciliation overview (service)."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from core.models import (AuditEvent, DomainEvent, LedgerEntry, LedgerPosting, Project,
                         SourceConsumption)
from pmo_plugins.delivery.models import Acceptance
from pmo_plugins.cash.models import (CashClassification, CashTransaction, FundingAccount)
from pmo_plugins.contracts.models import Contract, ContractChange
from pmo_plugins.delivery.operations_read_services import ops_reconciliation_overview
from pmo_plugins.management_revenue.models import RevenueRecognition
from pmo_plugins.settlement.models import Clearance, OpenItem, Settlement
from pmo_plugins.time.models import BillableConfirmation
from tests.test_v1_contract_terms_snapshot import TermsSnapshotFixture


class OpsOverviewFixture(TermsSnapshotFixture):
    def setUp(self):
        super().setUp()
        self.plain = self.member('member')
        self.project2 = Project.objects.create(
            organization=self.org, code='P2', name='另一项目', client='另一客户',
            manager=self.finance, start_date=self.today, end_date=self.today)

    def settlement_with_item(self, amount=50000, *, project=None, line=None, version=None,
                             status='active', occurred_on=None):
        line = line or self.approved_line
        version = version or self.approved
        project = project or self.project
        settlement = Settlement.objects.create(
            organization=self.org, project=project, contract_line=line,
            contract_version=version, selection=line.policy_selection,
            counterparty=self.contract.party,
            contract_line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                               'id': str(line.pk), 'version': 1},
            source_ref={}, source_performance=[], policy_version={}, pricing_configuration={},
            terms_snapshot={}, evidence=[], settled_on=occurred_on or self.today,
            due_date=self.today, gross_cents=amount, net_cents=amount, tax_cents=0,
            status='submitted', created_by=self.owner)
        OpenItem.objects.create(
            organization=self.org, project=project, settlement=settlement,
            partition_kind='due', partition_key='ph291:' + uuid.uuid4().hex, term_ref={},
            counterparty=self.contract.party,
            payee_ref={'kind': 'counterparty', 'id': str(self.contract.party_id)},
            scope={'kind': 'project', 'id': str(project.pk)}, source={},
            gross_cents=amount, tax_cents=0, due_date=self.today,
            occurred_on=occurred_on or self.today, status='active')
        Settlement.objects.filter(pk=settlement.pk).update(status='active', version=2)
        settlement.refresh_from_db()
        return settlement

    def clearance(self, open_item, cash_id, amount=30000, *, occurred_on=None):
        return Clearance.objects.create(
            organization=self.org, open_item=open_item, cash_transaction_id=cash_id,
            cash_transaction_ref={'namespace': 'cash', 'kind': 'cash_transaction',
                                  'id': str(cash_id), 'version': 1},
            cleared_cents=amount, occurred_on=occurred_on or self.today,
            status='active', created_by=self.owner)

    def cash(self, *, direction='in', amount=30000, date=None, status='posted',
             classify=True):
        account = getattr(self, '_account', None)
        if account is None:
            account = FundingAccount.objects.create(
                organization=self.org, name='主账户' + uuid.uuid4().hex[:6],
                opening_on=self.today, status='active', created_by=self.owner)
            self._account = account
        transaction = CashTransaction.objects.create(
            organization=self.org, funding_account=account,
            transaction_date=date or self.today, direction=direction,
            gross_cents=amount, external_source_key=uuid.uuid4().hex,
            payee={'kind': 'counterparty', 'id': str(self.contract.party_id)},
            status=status, created_by=self.owner)
        if classify:
            CashClassification.objects.create(
                organization=self.org, cash_transaction=transaction,
                classification='receipt' if direction == 'in' else 'payment',
                created_by=self.owner)
        return transaction

    def billable(self, *, status='draft', minutes=120, date=None):
        confirmation = BillableConfirmation.objects.create(
            organization=self.org, status='draft',
            timesheet_ref={'namespace': 'core', 'kind': 'timesheet',
                           'id': str(uuid.uuid4()), 'version': 1},
            timesheet_id=uuid.uuid4(),
            contract_line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                               'id': str(self.approved_line.pk), 'version': 1},
            contract_line_id=self.approved_line.pk, confirmed_minutes=minutes,
            confirmed_on=date or self.today, created_by_member=self.owner)
        if status == 'confirmed':
            BillableConfirmation.objects.filter(pk=confirmation.pk).update(
                status='confirmed', version=2, confirmed_by_member=self.finance,
                confirmed_at=timezone.now())
            confirmation.refresh_from_db()
        return confirmation

    def recognition(self, *, amount=40000, status='posted', date=None, line=None):
        line = line or self.approved_line
        acceptance = Acceptance.objects.create(
            organization=self.org, project=self.project, contract_line=line,
            contract_version=self.approved, selection=line.policy_selection,
            line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                      'id': str(line.pk), 'version': 1},
            policy_version={}, pricing_configuration={}, approved_digest='d' * 64,
            evidence=[], confirmed_on=date or self.today, accepted_net_cents=amount,
            status='confirmed', created_by_member=self.owner)
        return RevenueRecognition.objects.create(
            organization=self.org, project=acceptance.project, acceptance=acceptance,
            contract_line=line, status=status, created_by=self.owner,
            contract_line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                               'id': str(line.pk), 'version': 1},
            performance_ref={'kind': 'acceptance', 'id': str(acceptance.pk)},
            policy_version={}, pricing_snapshot={}, accepted_snapshot={},
            recognized_net_cents=amount, occurred_on=date or self.today,
            posting_period=(date or self.today).strftime('%Y-%m'))

    def direct_contract(self, code, *, project=None):
        contract = Contract.objects.create(
            organization=self.org, project=project or self.project, party=self.party,
            contract_code=code, status='draft')
        Contract.objects.filter(pk=contract.pk).update(status='active')
        contract.refresh_from_db()
        return contract

    def pending_change(self, *, contract=None):
        contract = contract or self.contract
        return ContractChange.objects.create(
            organization=self.org, contract=contract, status='draft',
            change_type='price_change', requested_on=self.today,
            net_delta_cents=1000, tax_delta_cents=60, gross_delta_cents=1060,
            created_by_member=self.owner)

    def overview(self, member=None, **kwargs):
        return ops_reconciliation_overview(self.owner if member is None else member, **kwargs)

    def sections_by_key(self, member=None, **kwargs):
        return {row['key']: row for row in self.overview(member, **kwargs)['sections']}

    def facts(self):
        models = (AuditEvent, DomainEvent, LedgerEntry, LedgerPosting, SourceConsumption,
                  ContractChange, Settlement, OpenItem, Clearance, RevenueRecognition,
                  BillableConfirmation, CashTransaction, CashClassification)
        return tuple(model.objects.count() for model in models)


class OpsOverviewServiceTests(OpsOverviewFixture, TestCase):
    def test_gold_sections_and_derived_amounts(self):
        self.pending_change()
        settlement = self.settlement_with_item(50000)
        item = settlement.items.get()
        cash = self.cash(amount=30000)
        self.clearance(item, cash.pk, 30000)
        self.recognition(amount=40000)
        self.billable(status='draft')
        self.billable(status='confirmed', minutes=90)
        self.cash(direction='out', amount=5000)
        before = self.facts()
        overview = self.overview()
        self.assertEqual(before, self.facts())
        sections = {row['key']: row for row in overview['sections']}
        self.assertEqual(list(sections), ['contracts', 'settlement', 'management_revenue',
                                          'time', 'onboarding', 'cash'])
        contracts = sections['contracts']
        self.assertEqual(contracts['counts']['pending_changes'], 1)
        self.assertEqual(contracts['totals_cents']['pending_gross_delta_cents'], 1060)
        settlement_section = sections['settlement']
        self.assertEqual(settlement_section['counts']['active_open_items'], 1)
        # remaining is derived from gross-cleared, not echoed from the input amount.
        self.assertEqual(settlement_section['totals_cents']['remaining_cents'], 20000)
        revenue = sections['management_revenue']
        self.assertEqual(revenue['counts']['posted_recognitions'], 1)
        self.assertEqual(revenue['totals_cents']['recognized_net_cents'], 40000)
        self.assertEqual(revenue['totals_cents']['settled_net_cents'], 50000)
        self.assertEqual(revenue['totals_cents']['difference_cents'], -10000)
        time_section = sections['time']
        self.assertEqual(time_section['counts']['pending_confirmations'], 1)
        self.assertEqual(time_section['counts']['confirmed_confirmations'], 1)
        self.assertEqual(time_section['minutes']['confirmed_minutes'], 90)
        cash_section = sections['cash']
        self.assertEqual(cash_section['counts']['posted_transactions'], 2)
        self.assertEqual(cash_section['counts']['missing_classification'], 0)
        self.assertEqual(cash_section['totals_cents']['net_cents'], 25000)
        self.assertEqual(sections['onboarding']['counts'], None)
        self.assertIn('import_preview_persistence_not_implemented',
                      sections['onboarding']['missing'])

    def test_missing_upstream_is_null_not_zero(self):
        overview = self.overview(self.owner)
        onboarding = {row['key']: row for row in overview['sections']}['onboarding']
        self.assertIsNone(onboarding['counts'])
        self.assertIsNone(onboarding['totals_cents'])
        self.assertIn('import_preview_persistence_not_implemented', overview['missing'])
        pm_overview = self.overview(self.pm)
        pm_cash = {row['key']: row for row in pm_overview['sections']}['cash']
        self.assertIsNone(pm_cash['counts'])
        self.assertIn('cash_not_scoped_to_project', pm_cash['missing'])

    def test_issue_codes_for_abnormal_facts(self):
        self.pending_change()
        settlement = self.settlement_with_item(10000)
        item = settlement.items.get()
        self.clearance(item, uuid.uuid4(), 12000)
        Contract.objects.filter(pk=self.contract.pk).update(
            status='terminated', terminated_on=self.today - timedelta(days=1))
        codes = {issue['code'] for row in self.overview()['sections']
                 for issue in row['issues']}
        self.assertIn('pending_change_approval', codes)
        self.assertIn('over_cleared_open_item', codes)
        self.assertIn('terminated_still_active_terms', codes)
        self.assertEqual(self.overview()['issue_count'], len(codes))

    def test_period_filter_limits_sections(self):
        old = self.today - timedelta(days=40)
        self.cash(amount=10000, date=old)
        self.cash(amount=20000, date=self.today)
        current = {row['key']: row for row in self.overview(
            self.owner, period=self.today.strftime('%Y-%m'))['sections']}['cash']
        self.assertEqual(current['counts']['posted_transactions'], 1)
        self.assertEqual(current['totals_cents']['posted_in_cents'], 20000)
        previous = {row['key']: row for row in self.overview(
            self.owner, period=old.strftime('%Y-%m'))['sections']}['cash']
        self.assertEqual(previous['counts']['posted_transactions'], 1)
        self.assertEqual(previous['totals_cents']['posted_in_cents'], 10000)

    def test_input_validation(self):
        future = (self.today + timedelta(days=1)).isoformat()
        with self.assertRaises(ValidationError):
            self.overview(self.owner, as_of=future)
        for bad in ('2026-13', '2026-1', 'abc', '2026/01'):
            with self.assertRaises(ValidationError):
                self.overview(self.owner, period=bad)

    def test_permissions_and_org_isolation(self):
        for member in (self.owner, self.finance, self.auditor, self.pm):
            self.assertEqual(self.overview(member)['role'], member.role)
        with self.assertRaises(PermissionDenied):
            self.overview(self.plain)
        with self.assertRaises(PermissionDenied):
            ops_reconciliation_overview(None)
        other_org = Project.objects.create(
            organization=self.org, code='P9', name='隔离项目', client='客户',
            manager=self.pm, start_date=self.today, end_date=self.today)
        other_contract = self.direct_contract('C9', project=other_org)
        ContractChange.objects.create(
            organization=self.org, contract=other_contract, status='draft',
            change_type='price_change', requested_on=self.today, net_delta_cents=1,
            tax_delta_cents=0, gross_delta_cents=1, created_by_member=self.owner)
        # pm manages both projects here, so both are visible; a project managed by finance
        # must not leak into the pm scope.
        finance_project = Project.objects.create(
            organization=self.org, code='P8', name='财务项目', client='客户',
            manager=self.finance, start_date=self.today, end_date=self.today)
        hidden_contract = self.direct_contract('C8', project=finance_project)
        ContractChange.objects.create(
            organization=self.org, contract=hidden_contract, status='draft',
            change_type='price_change', requested_on=self.today, net_delta_cents=2,
            tax_delta_cents=0, gross_delta_cents=2, created_by_member=self.owner)
        pm_counts = {row['key']: row for row in self.overview(self.pm)['sections']}[
            'contracts']['counts']
        owner_counts = {row['key']: row for row in self.overview(self.owner)['sections']}[
            'contracts']['counts']
        self.assertEqual(pm_counts['pending_changes'], 1)
        self.assertEqual(owner_counts["pending_changes"], 2)

    def test_read_only_purity_no_write_sql(self):
        self.pending_change()
        self.settlement_with_item(50000)
        self.billable()
        self.cash()
        before = self.facts()
        with CaptureQueriesContext(connection) as captured:
            self.overview(self.owner)
        statements = [query['sql'].lstrip().upper() for query in captured.captured_queries]
        self.assertFalse([sql for sql in statements if sql.startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.assertEqual(before, self.facts())

    def test_refs_capped_at_fifty(self):
        for index in range(55):
            project = Project.objects.create(
                organization=self.org, code=f'R{index}', name=f'项目{index}', client='客户',
                manager=self.pm, start_date=self.today, end_date=self.today)
            contract = self.direct_contract(f'CR{index}', project=project)
            ContractChange.objects.create(
                organization=self.org, contract=contract, status='draft',
                change_type='price_change', requested_on=self.today, net_delta_cents=1,
                tax_delta_cents=0, gross_delta_cents=1, created_by_member=self.owner)
        section = {row['key']: row for row in self.overview(self.owner)['sections']}['contracts']
        self.assertEqual(section['counts']['pending_changes'], 55)
        self.assertEqual(len(section['refs']), 50)
        self.assertTrue(section['truncated'])
