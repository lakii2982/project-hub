"""PH-275 contract change read-only impact trace and termination-blocking regressions."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import AuditEvent, DomainEvent
from pmo_plugins.contracts import approval_services, change_services
from pmo_plugins.contracts.models import ContractLine, ContractVersion
from pmo_plugins.delivery.models import Acceptance
from pmo_plugins.management_revenue.models import RevenueRecognition
from pmo_plugins.settlement import receivable_services
from pmo_plugins.settlement.models import Clearance, OpenItem, Settlement
from tests.test_v1_contract_change import ContractChangeFixture


class ImpactFixture(ContractChangeFixture):
    def setUp(self):
        super().setUp()
        self.auditor = self.member('auditor')
        self.approved = self.approved_contract(effective_on=self.today - timedelta(days=5))
        self.approved_line = self.approved.lines.get()
        self.selection = self.approved_line.policy_selection
        self.counterparty = self.contract.party

    def make_facts(self):
        acceptance = Acceptance.objects.create(
            organization=self.org, project=self.project, contract_line=self.approved_line,
            contract_version=self.approved, selection=self.selection,
            line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                      'id': str(self.approved_line.pk), 'version': 1},
            policy_version={}, pricing_configuration={}, approved_digest='a' * 64,
            evidence=[{'kind': 'acceptance', 'reference': '客户确认'}],
            confirmed_on=self.today, accepted_net_cents=40000, status='confirmed',
            created_by_member=self.owner)
        settlement = Settlement.objects.create(
            organization=self.org, project=self.project, contract_line=self.approved_line,
            contract_version=self.approved, selection=self.selection, counterparty=self.counterparty,
            contract_line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                               'id': str(self.approved_line.pk), 'version': 1},
            source_ref={}, source_performance=[], policy_version={}, pricing_configuration={},
            terms_snapshot={}, evidence=[], settled_on=self.today, due_date=self.today,
            gross_cents=100000, net_cents=100000, tax_cents=0, status='submitted',
            created_by=self.owner)
        common = dict(organization=self.org, project=self.project, settlement=settlement,
                      counterparty=self.counterparty,
                      payee_ref={'kind': 'counterparty', 'id': str(self.counterparty.pk)},
                      scope={'kind': 'project', 'id': str(self.project.pk)}, source={},
                      tax_cents=0, due_date=self.today, occurred_on=self.today, status='active')
        OpenItem.objects.create(partition_kind='due', partition_key='ph275:' + uuid.uuid4().hex,
                                term_ref={}, gross_cents=60000, **common)
        item = OpenItem.objects.create(partition_kind='retention',
                                       partition_key='ph275:' + uuid.uuid4().hex,
                                       term_ref={}, gross_cents=40000, **common)
        Settlement.objects.filter(pk=settlement.pk).update(status='active', version=2)
        settlement.refresh_from_db()
        clearance = Clearance.objects.create(
            organization=self.org, open_item=item, cash_transaction_id=uuid.uuid4(),
            cash_transaction_ref={}, cleared_cents=20000, occurred_on=self.today,
            status='active', created_by=self.owner)
        revenue = RevenueRecognition.objects.create(
            organization=self.org, project=self.project, acceptance=acceptance,
            contract_line=self.approved_line, created_by=self.owner, contract_line_ref={},
            performance_ref={}, policy_version={}, pricing_snapshot={}, accepted_snapshot={},
            recognized_net_cents=40000, occurred_on=self.today,
            posting_period=self.today.strftime('%Y-%m'), status='posted')
        return {'acceptance': acceptance, 'settlement': settlement, 'open_item': item,
                'clearance': clearance, 'revenue': revenue}

    def facts(self):
        return tuple(model.objects.count() for model in (
            ContractVersion, ContractLine, Settlement, OpenItem, Clearance, RevenueRecognition,
            AuditEvent, DomainEvent))


class ContractChangeImpactServiceTests(ImpactFixture, TestCase):
    def test_draft_impact_is_before_version_only_and_read_only(self):
        self.make_facts()
        change = self.draft_change()
        before = self.facts()
        impact = approval_services.contract_change_impact(self.owner, change.pk)
        self.assertEqual(before, self.facts())
        self.assertEqual(impact['status'], 'draft')
        self.assertFalse(impact['terminated'])
        self.assertEqual(impact['before_version_ref']['version_no'], self.approved.version_no)
        self.assertIsNone(impact['after_version_ref'])
        self.assertEqual([row['id'] for row in impact['contract_line_refs']], [str(self.approved_line.pk)])
        self.assertEqual(impact['settlements'], {'count': 1, 'gross_cents': 100000})
        self.assertEqual(impact['open_items'], {'count': 2, 'gross_cents': 100000})
        self.assertEqual(impact['clearances'], {'count': 1, 'cleared_cents': 20000})
        self.assertEqual(impact['revenue_recognitions'], {'count': 1, 'net_cents': 40000})

    def test_approved_price_change_lists_before_after_versions_and_affected_lines(self):
        self.make_facts()
        change = self.draft_change(lines=[self.line(unit_price=11000)])
        decided = self.decide(change)
        new_version = ContractVersion.objects.get(contract=self.contract, status='approved')
        new_line = new_version.lines.get()
        impact = approval_services.contract_change_impact(self.auditor, decided.pk)
        self.assertEqual(impact['after_version_ref']['id'], str(new_version.pk))
        self.assertEqual(impact['before_version_ref']['id'], str(self.approved.pk))
        refs = {row['id'] for row in impact['contract_line_refs']}
        self.assertEqual(refs, {str(self.approved_line.pk), str(new_line.pk)})
        self.assertEqual(impact['settlements']['gross_cents'], 100000)
        self.assertEqual(impact['revenue_recognitions']['net_cents'], 40000)
        self.assertEqual(impact['deltas']['gross_cents'], 10600)
        # Listing by contract returns the same read-only summary.
        listed = approval_services.contract_change_impact_list(self.owner, contract_id=self.contract.pk)
        self.assertEqual([row['change_ref']['id'] for row in listed], [str(decided.pk)])

    def test_termination_impact_and_deepened_blocking(self):
        change = self.draft_change(change_type='termination')
        decided = self.decide(change)
        impact = approval_services.contract_change_impact(self.owner, decided.pk)
        self.assertTrue(impact['terminated'])
        self.assertEqual(impact['terminated_on'], self.today.isoformat())
        self.assertIsNone(impact['after_version_ref'])
        self.assertEqual(impact['before_version_ref']['id'], str(self.approved.pk))
        self.assertEqual(impact['settlements'], {'count': 0, 'gross_cents': 0})
        # A new change draft is refused after termination with zero half-writes.
        before = self.facts()
        with self.assertRaises(ValidationError):
            self.draft_change()
        self.assertEqual(before, self.facts())
        # A new settlement is refused by the existing approved_terms_snapshot guard.
        before = self.facts()
        with self.assertRaises(ValidationError):
            receivable_services.save_settlement(
                self.owner, contract_line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                                               'id': str(self.approved_line.pk), 'version': 1},
                term_refs=[], request_token=uuid.uuid4(), settled_on=self.today, dry_run=False)
        self.assertEqual(before, self.facts())
        # Historical pairing reads terminate explicitly instead of silently reading.
        terms = approval_services.approved_terms_snapshot(
            self.owner, {'namespace': 'contracts', 'kind': 'contract_line',
                         'id': str(self.approved_line.pk), 'version': 1},
            (self.today - timedelta(days=1)).isoformat())
        self.assertTrue(terms['terminated'])
        self.assertEqual(terms['terminated_on'], self.today.isoformat())

    def test_permissions_module_disable_and_no_audit(self):
        change = self.draft_change()
        self.denied(lambda: approval_services.contract_change_impact(
            self.member('member'), change.pk))
        self.denied(lambda: approval_services.contract_change_impact_list(
            self.member('member')))
        pm_impact = approval_services.contract_change_impact(self.pm, change.pk)
        self.assertTrue(pm_impact['summary_only'])
        from core.plugin_runtime import get_registry, set_enabled
        for plugin_id in ('onboarding', 'time', 'management_revenue', 'settlement', 'delivery', 'contracts'):
            if plugin_id in get_registry().manifests:
                set_enabled(self.owner, plugin_id, False)
        self.assertFalse(__import__('core.plugin_runtime', fromlist=['is_enabled']).is_enabled(self.org, 'contracts'))
        before = (AuditEvent.objects.count(), DomainEvent.objects.count())
        impact = approval_services.contract_change_impact(self.auditor, change.pk)
        self.assertEqual(impact['change_ref']['id'], str(change.pk))
        self.assertEqual(before, (AuditEvent.objects.count(), DomainEvent.objects.count()))

    def test_cross_tenant_and_unknown_change_are_denied(self):
        change = self.draft_change()
        from core.models import Organization, Membership
        from django.contrib.auth import get_user_model
        other_org = Organization.objects.create(name='其他公司')
        user = get_user_model().objects.create_user(username='ph275' + uuid.uuid4().hex)
        outsider = Membership.objects.create(organization=other_org, user=user, role='owner')
        self.denied(lambda: approval_services.contract_change_impact(outsider, change.pk))
        self.denied(lambda: approval_services.contract_change_impact(self.owner, uuid.uuid4()))
