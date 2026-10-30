"""PH-265 contract change draft / independent decision / effective version service regressions."""
import uuid
from datetime import timedelta
from io import StringIO

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from django.utils import timezone

from core.models import AuditEvent, CashPlan, DomainEvent, LedgerEntry, Milestone, PluginState
from pmo_plugins.contracts import approval_services, change_services
from pmo_plugins.contracts.models import (ContractChange, ContractChangeReceipt, ContractLine,
                                         ContractVersion, MethodPolicySelection, PaymentTerm,
                                         WarrantyTerm)
from tests.test_v1_contracts import Fixture


class ContractChangeFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.finance = self.member('finance')
        self.contract = self.make_contract()
        self.milestone = Milestone.objects.create(project=self.project, name='交付节点',
                                                  due_date=self.today, criteria='项目确认')

    def line(self, unit_price=10000):
        pricing = {'template': 'confirmed_time', 'net_cap_cents': 100000,
                   'time_unit': 'hour', 'unit_minutes': 60, 'unit_price_cents': unit_price}
        net = unit_price * 10
        return {'line_no': 1, 'net_cents': net, 'tax_cents': net * 6 // 100,
                'gross_cents': net + net * 6 // 100, 'pricing_configuration': pricing,
                'payment_terms': [{'key': 'due', 'condition_type': 'unconditional',
                                   'gross_cents': net},
                                  {'key': 'retention', 'condition_type': 'warranty',
                                   'gross_cents': 6000, 'release_condition': '质保结束'}],
                'warranty_terms': [{'payment_key': 'retention', 'gross_cents': 6000,
                                    'release_condition': '质保结束'}]}

    def save_version(self, lines=None, effective_on=None, **changes):
        values = dict(lines=lines or [self.line()], effective_on=effective_on or self.today,
                      contract_reference='实际合同经办依据', request_token=uuid.uuid4())
        values.update(changes)
        return approval_services.save_version(self.owner, self.contract.pk, **values)

    def submitted(self, **changes):
        version = self.save_version(**changes)
        return approval_services.submit_version(self.owner, version.pk,
                                                expected_version=version.version,
                                                request_token=uuid.uuid4())

    def approved_contract(self, effective_on=None):
        version = self.submitted(effective_on=effective_on or self.today)
        approved = approval_services.review_version(
            self.finance, version.pk, expected_version=version.version,
            request_token=uuid.uuid4(), decision='approve', review_note='独立核对合同与规则')
        self.contract.refresh_from_db()
        return approved

    def draft_change(self, *, change_type='price_change', lines=None, evidence=None, **changes):
        values = dict(contract_id=self.contract.pk, change_type=change_type,
                      requested_on=self.today.isoformat(),
                      draft_content={} if change_type == 'termination' else {'lines': lines or [self.line(11000)]},
                      evidence=evidence or [], request_token=uuid.uuid4(), dry_run=False)
        values.update(changes)
        return change_services.save_contract_change_draft(self.owner, **values)

    def decide(self, change, *, decision='approved', member=None, note='', **changes):
        values = dict(request_token=uuid.uuid4(), expected_version=change.version, dry_run=False)
        values.update(changes)
        return change_services.decide_contract_change(member or self.finance, change.pk,
                                                      decision=decision, note=note, **values)

    def facts(self):
        return tuple(model.objects.count() for model in (
            ContractVersion, ContractLine, MethodPolicySelection, PaymentTerm, WarrantyTerm,
            ContractChange, ContractChangeReceipt, AuditEvent, DomainEvent, LedgerEntry, CashPlan))

    def denied(self, call, exceptions=(ValidationError, PermissionDenied, TypeError)):
        before = self.facts()
        with self.assertRaises(exceptions):
            call()
        self.assertEqual(before, self.facts())


class ContractChangeServiceTests(ContractChangeFixture, TestCase):
    def test_draft_derives_deltas_without_version_or_canonical_event(self):
        approved = self.approved_contract()
        before = self.facts()
        change = self.draft_change()
        after = self.facts()
        self.assertEqual((change.status, change.version), ('draft', 1))
        self.assertEqual((change.net_delta_cents, change.tax_delta_cents,
                          change.gross_delta_cents), (10000, 600, 10600))
        self.assertEqual(after[0], before[0])   # no new ContractVersion
        self.assertEqual(after[1], before[1])   # no new ContractLine
        self.assertEqual(after[5], before[5] + 1)   # ContractChange
        self.assertEqual(after[6], before[6] + 1)   # receipt
        self.assertFalse(DomainEvent.objects.filter(topic='contracts.change_effective.v1').exists())
        self.assertEqual(approved.status, 'approved')
        self.assertEqual(ContractVersion.objects.filter(contract=self.contract, status='approved').count(), 1)
        self.assertEqual(LedgerEntry.objects.count(), 0)

    def test_draft_replay_duplicate_and_input_conflict(self):
        self.approved_contract()
        token = uuid.uuid4()
        change = self.draft_change(request_token=token)
        replay = self.draft_change(request_token=token)
        self.assertEqual(replay.pk, change.pk)
        self.denied(lambda: self.draft_change(request_token=token, lines=[self.line(12000)]))
        self.denied(lambda: self.draft_change())
        self.denied(lambda: self.draft_change(provided_deltas={'net_delta_cents': 1,
                                                              'tax_delta_cents': 0,
                                                              'gross_delta_cents': 1}))
        self.denied(lambda: self.draft_change(requested_on=(self.today + timedelta(days=1)).isoformat()))

    def test_self_review_rejected_and_independent_approval_creates_version(self):
        old = self.approved_contract()
        change = self.draft_change()
        self.denied(lambda: self.decide(change, member=self.owner, note='自审'))
        approved = self.decide(change, note='价格调整经客户确认')
        self.contract.refresh_from_db()
        old.refresh_from_db()
        self.assertEqual((approved.status, approved.version), ('approved', 2))
        self.assertEqual((self.contract.status, self.contract.version), ('active', 3))
        self.assertEqual((old.status, old.version), ('superseded', 4))
        approved_versions = ContractVersion.objects.filter(contract=self.contract, status='approved')
        self.assertEqual(approved_versions.count(), 1)
        new = approved_versions.get()
        self.assertEqual((new.version_no, new.lines.get().net_cents), (2, 110000))
        self.assertEqual(new.approved_by_member_id, self.finance.pk)
        self.assertEqual(new.draft_content['lines'][0]['net_cents'], 110000)
        self.assertEqual(new.lines.get().gross_cents, 116600)
        events = DomainEvent.objects.filter(topic__in=['contracts.version_approved.v1',
                                                       'contracts.change_effective.v1'])
        self.assertEqual(events.filter(topic='contracts.change_effective.v1').count(), 1)
        self.assertEqual(events.filter(topic='contracts.version_approved.v1').count(), 2)
        version_event = events.get(topic='contracts.version_approved.v1',
                                   data__source__id=str(new.pk))
        self.assertEqual(version_event.data['source'], {'namespace': 'contracts', 'kind': 'contract_version',
                                                        'id': str(new.pk), 'version': new.version})
        self.assertEqual(version_event.data['payload']['changed_refs'],
                         [{'namespace': 'contracts', 'kind': 'contract_version', 'id': str(new.pk),
                           'version': new.version},
                          {'namespace': 'contracts', 'kind': 'contract_change', 'id': str(change.pk),
                           'version': 2}])
        self.assertEqual(version_event.data['payload']['posting_event_refs'], [])
        change_event = events.get(topic='contracts.change_effective.v1')
        self.assertEqual(change_event.data['source'], {'namespace': 'contracts', 'kind': 'contract_change',
                                                       'id': str(change.pk), 'version': 2})
        line = new.lines.get()
        snapshot = approval_services.approved_line_snapshot(
            self.finance, {'namespace': 'contracts', 'kind': 'contract_line', 'id': str(line.pk), 'version': 1},
            self.today)
        self.assertEqual(snapshot['gross_cents'], 116600)
        self.assertEqual(LedgerEntry.objects.count(), 0)

    def test_reject_and_withdraw_are_terminal_without_effect(self):
        self.approved_contract()
        change = self.draft_change()
        self.denied(lambda: self.decide(change, decision='rejected', note=' '))
        rejected = self.decide(change, decision='rejected', note='价格依据不足')
        self.assertEqual((rejected.status, rejected.decision_note), ('rejected', '价格依据不足'))
        self.assertFalse(DomainEvent.objects.filter(topic='contracts.change_effective.v1').exists())
        self.denied(lambda: self.decide(change, member=self.owner, note='重复决策'))
        other = self.draft_change()
        withdrawn = self.decide(other, decision='withdrawn', note='客户取消')
        self.assertEqual(withdrawn.status, 'withdrawn')
        self.assertFalse(DomainEvent.objects.filter(topic='contracts.change_effective.v1').exists())

    def test_termination_sets_date_emits_event_and_blocks_new_settlement(self):
        old = self.approved_contract(effective_on=self.today - timedelta(days=10))
        line = old.lines.get()
        ref = {'namespace': 'contracts', 'kind': 'contract_line', 'id': str(line.pk), 'version': 1}
        self.assertTrue(approval_services.approved_terms_snapshot(self.finance, ref, self.today - timedelta(days=1)))
        change = self.draft_change(change_type='termination', lines=[])
        self.assertEqual((change.change_type, change.net_delta_cents), ('termination', 0))
        approved = self.decide(change, note='客户终止合同')
        self.contract.refresh_from_db()
        self.assertEqual((self.contract.status, self.contract.terminated_on),
                         ('terminated', self.today))
        self.assertEqual(ContractVersion.objects.filter(contract=self.contract, status='approved').count(), 1)
        self.assertEqual(ContractVersion.objects.filter(contract=self.contract).count(), 1)
        self.assertEqual(approved.status, 'approved')
        event = DomainEvent.objects.get(topic='contracts.change_effective.v1')
        self.assertEqual(event.data['source'], {'namespace': 'contracts', 'kind': 'contract_change',
                                                'id': str(change.pk), 'version': 2})
        self.assertEqual(event.data['payload']['changed_refs'],
                         [{'namespace': 'contracts', 'kind': 'contract_change', 'id': str(change.pk),
                           'version': 2},
                          {'namespace': 'contracts', 'kind': 'contract', 'id': str(self.contract.pk),
                           'version': self.contract.version}])
        # past business date before termination still reads; today/after is rejected
        self.assertTrue(approval_services.approved_terms_snapshot(self.finance, ref, self.today - timedelta(days=1)))
        self.denied(lambda: approval_services.approved_terms_snapshot(self.finance, ref, self.today))
        self.denied(lambda: self.draft_change())

    def test_termination_rejects_new_version_content_and_missing_contracts(self):
        self.approved_contract()
        self.denied(lambda: self.draft_change(change_type='termination',
                                              draft_content={'lines': [self.line()]}))
        other = self.make_contract(contract_code='C2')
        self.denied(lambda: self.draft_change(contract_id=other.pk))  # not active
        self.denied(lambda: change_services.save_contract_change_draft(
            self.pm, contract_id=self.contract.pk, change_type='price_change',
            requested_on=self.today.isoformat(), draft_content={'lines': [self.line(11000)]},
            evidence=[], request_token=uuid.uuid4(), dry_run=False))

    def test_roles_module_disable_replay_and_zero_half_writes(self):
        self.approved_contract()
        auditor, member = self.member('auditor'), self.member('member')
        self.denied(lambda: change_services.save_contract_change_draft(
            auditor, contract_id=self.contract.pk, change_type='price_change',
            requested_on=self.today.isoformat(), draft_content={'lines': [self.line(11000)]},
            evidence=[], request_token=uuid.uuid4(), dry_run=False))
        change = self.draft_change()
        token = uuid.uuid4()
        decided = self.decide(change, request_token=token, note='确认')
        replay = self.decide(change, request_token=token, note='确认')
        self.assertEqual(replay.pk, decided.pk)
        self.denied(lambda: self.decide(change, member=member))
        from core.plugin_runtime import set_enabled
        for key in ('management_revenue', 'settlement', 'delivery', 'contracts'):
            set_enabled(self.owner, key, False)
        self.denied(lambda: self.draft_change())
