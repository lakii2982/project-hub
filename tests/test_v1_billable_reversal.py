"""PH-276 time confirmed reversal, append-only guards and read-only pairing regressions."""
import uuid
from datetime import timedelta
from io import StringIO

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError, IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AuditEvent, DomainEvent, LedgerEntry, Project,
                         SourceConsumption)
from pmo_plugins.management_revenue.models import RevenueRecognition
from pmo_plugins.settlement.models import OpenItem, Settlement
from pmo_plugins.time import services
from pmo_plugins.time.models import BillableConfirmation, BillableConfirmationReceipt
from tests.test_v1_billable_confirmation import TimeBillableFixture


class ReversalFixture(TimeBillableFixture):
    def setUp(self):
        super().setUp()
        self.reverser = self.member('finance')

    def confirmed(self):
        return self.confirm(self.draft())

    def reverse(self, confirmation, member=None, **changes):
        values = dict(reason='客户撤回确认', request_token=uuid.uuid4(),
                      expected_version=confirmation['version'], dry_run=False)
        values.update(changes)
        return services.reverse_billable_confirmation(
            member or self.reverser, confirmation['id'], **values)

    def pair_facts(self, amount_cents=60000):
        from pmo_plugins.delivery.models import Acceptance
        line = self._line
        acceptance = Acceptance.objects.create(
            organization=self.org, project=self.project, contract_line=line,
            contract_version=self._version, selection=line.policy_selection,
            line_ref={'namespace': 'contracts', 'kind': 'contract_line', 'id': str(line.pk), 'version': 1},
            policy_version={}, pricing_configuration={}, approved_digest='a' * 64,
            evidence=[{'kind': 'acceptance', 'reference': '客户验收'}],
            confirmed_on=self.today, accepted_net_cents=amount_cents, status='confirmed',
            created_by_member=self.owner)
        settlement = Settlement.objects.create(
            organization=self.org, project=self.project, contract_line=line,
            contract_version=self._version, selection=line.policy_selection,
            counterparty=self.contract.party,
            contract_line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                               'id': str(line.pk), 'version': 1},
            source_ref={}, source_performance=[], policy_version={}, pricing_configuration={},
            terms_snapshot={}, evidence=[], settled_on=self.today, due_date=self.today,
            gross_cents=amount_cents, net_cents=amount_cents, tax_cents=0, status='submitted',
            created_by=self.owner)
        OpenItem.objects.create(
            organization=self.org, project=self.project, settlement=settlement,
            partition_kind='due', partition_key='ph276:' + uuid.uuid4().hex, term_ref={},
            counterparty=self.contract.party,
            payee_ref={'kind': 'counterparty', 'id': str(self.contract.party_id)},
            scope={'kind': 'project', 'id': str(self.project.pk)}, source={},
            gross_cents=amount_cents, tax_cents=0, due_date=self.today, occurred_on=self.today,
            status='active')
        Settlement.objects.filter(pk=settlement.pk).update(status='active', version=2)
        RevenueRecognition.objects.create(
            organization=self.org, project=self.project, acceptance=acceptance,
            contract_line=line, created_by=self.owner, contract_line_ref={}, performance_ref={},
            policy_version={}, pricing_snapshot={}, accepted_snapshot={},
            recognized_net_cents=amount_cents, occurred_on=self.today,
            posting_period=self.today.strftime('%Y-%m'), status='posted')
        return settlement

    @property
    def _line(self):
        from pmo_plugins.contracts.models import ContractLine
        return ContractLine.objects.get(pk=self.line_ref['id'])

    @property
    def _version(self):
        return self._line.contract_version


class BillableReversalServiceTests(ReversalFixture, TestCase):
    def test_gold_reversal_is_append_only_without_canonical_topic(self):
        row = self.confirmed()
        before = self.facts()
        reversed_row = self.reverse(row)
        after = self.facts()
        stored = BillableConfirmation.objects.get(pk=row['id'])
        self.assertEqual((stored.status, stored.version), ('reversed', 3))
        self.assertEqual(reversed_row['status'], 'reversed')
        self.assertEqual(AuditEvent.objects.filter(
            action='time.billable_confirmation.reversed', entity_id=str(stored.pk)).count(), 1)
        self.assertEqual(BillableConfirmationReceipt.objects.filter(
            action='reverse', result_ref__id=str(stored.pk)).count(), 1)
        self.assertEqual(DomainEvent.objects.filter(topic='time.billable_confirmed.v1').count(), 1)
        self.assertFalse(DomainEvent.objects.filter(topic='time.billable_reversed.v1').exists())
        self.assertEqual((after[4], after[5], after[6]), (before[4], before[5], before[6]))
        self.assertEqual(self.verify_errors(), [])

    def test_reversal_boundaries_and_idempotency(self):
        row = self.confirmed()
        draft = self.draft()
        self.denied(lambda: self.reverse(draft))
        self.denied(lambda: self.reverse(row, member=self.owner))
        self.denied(lambda: self.reverse(row, expected_version=1))
        self.denied(lambda: self.reverse(row, reason=''))
        self.denied(lambda: self.reverse(row, reason='x' * 501))
        preview = services.reverse_billable_confirmation(
            self.reverser, row['id'], reason='预检', request_token=uuid.uuid4(),
            expected_version=row['version'], dry_run=True)
        self.assertEqual(preview['status'], 'reversed')
        self.assertEqual(BillableConfirmation.objects.get(pk=row['id']).status, 'confirmed')
        token = uuid.uuid4()
        first = self.reverse(row, request_token=token)
        replay = self.reverse(row, request_token=token)
        self.assertEqual(replay['id'], first['id'])
        self.denied(lambda: self.reverse(row, request_token=token, reason='另一原因'))
        self.denied(lambda: self.reverse(row))
        self.assertEqual(BillableConfirmationReceipt.objects.filter(action='reverse').count(), 1)

    def test_triggers_allow_only_the_single_reverse_transition(self):
        row = self.confirmed()
        pk = row['id']
        def denied(operation):
            with transaction.atomic():
                with self.assertRaises((DatabaseError, IntegrityError)):
                    operation()
        denied(lambda: BillableConfirmation.objects.filter(pk=pk).update(
            status='draft', version=3))
        denied(lambda: BillableConfirmation.objects.filter(pk=pk).update(
            status='reversed', version=3, confirmed_minutes=1))
        denied(lambda: BillableConfirmation.objects.filter(pk=pk).delete())
        BillableConfirmation.objects.filter(pk=pk).update(status='reversed', version=3)
        self.assertEqual(BillableConfirmation.objects.get(pk=pk).status, 'reversed')
        denied(lambda: BillableConfirmation.objects.filter(pk=pk).update(
            status='confirmed', version=4))
        denied(lambda: BillableConfirmationReceipt.objects.create(
            organization=self.org, action='bogus', request_token=uuid.uuid4(),
            input_digest='x', result_ref={}, created_by=self.owner))

    def test_verify_integrity_detects_reversed_half_writes_and_topic(self):
        row = self.confirmed()
        BillableConfirmation.objects.filter(pk=row['id']).update(status='reversed', version=3)
        errors = self.verify_errors()
        self.assertTrue(any(':reversal_audit_count' in error for error in errors), errors)
        self.assertTrue(any(':reversal_receipt_count' in error for error in errors), errors)
        # A forbidden canonical reversed topic is reported even for a valid reversal.
        good = self.confirmed()
        self.reverse(good)
        from core.services import audit as audit_event
        good_row = BillableConfirmation.objects.get(pk=good['id'])
        source = audit_event(self.owner, 'plugin.ph276.synthetic', good_row, {})
        event_id = uuid.uuid4()
        DomainEvent.objects.create(
            event_id=event_id, organization=self.org, source=source,
            topic='time.billable_reversed.v1',
            data={'event_id': str(event_id), 'topic': 'time.billable_reversed.v1',
                  'schema_version': 1, 'organization_id': str(self.org.pk)})
        errors = self.verify_errors()
        self.assertIn('billable_confirmation:unexpected_canonical_reversed_topic', errors)

    def test_pairing_is_read_only_and_reports_paired_partial_unpaired(self):
        row = self.confirmed()
        before = self.facts()
        pairing = services.billable_confirmation_pairing(self.owner, row['id'])
        self.assertEqual(pairing['pairing_status'], 'unpaired')
        self.assertEqual(pairing['expected_value_cents'], 60000)
        self.assertEqual(before, self.facts())
        self.pair_facts(60000)
        after_facts = self.facts()
        pairing = services.billable_confirmation_pairing(self.owner, row['id'])
        self.assertEqual(pairing['pairing_status'], 'paired')
        self.assertEqual(pairing['revenue']['net_cents'], 60000)
        self.assertEqual(pairing['settlements']['gross_cents'], 60000)
        self.assertEqual(pairing['difference_cents'], -60000)
        self.assertEqual(after_facts, self.facts())
        listed = services.billable_confirmation_pairing_list(self.owner)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['pairing_status'], 'paired')

    def test_roles_module_disable_and_cross_tenant(self):
        row = self.confirmed()
        self.denied(lambda: services.reverse_billable_confirmation(
            self.member('member'), row['id'], reason='x', request_token=uuid.uuid4(),
            expected_version=row['version'], dry_run=False))
        self.denied(lambda: services.billable_confirmation_pairing(self.member('member'), row['id']))
        pm_pairing = services.billable_confirmation_pairing(self.pm, row['id'])
        self.assertEqual(pm_pairing['confirmation_ref']['id'], row['id'])
        from core.models import Membership, Organization
        from django.contrib.auth import get_user_model
        outsider = Membership.objects.create(
            organization=Organization.objects.create(name='PH276 其他公司'),
            user=get_user_model().objects.create_user(username='ph276' + uuid.uuid4().hex),
            role='owner')
        self.denied(lambda: services.billable_confirmation_pairing(outsider, row['id']))
        from core.plugin_runtime import get_registry, set_enabled
        set_enabled(self.owner, 'invoicing', False)
        for plugin_id in ('onboarding', 'time', 'management_revenue', 'settlement', 'delivery', 'contracts'):
            if plugin_id in get_registry().manifests:
                set_enabled(self.owner, plugin_id, False)
        self.denied(lambda: services.reverse_billable_confirmation(
            self.reverser, row['id'], reason='x', request_token=uuid.uuid4(),
            expected_version=row['version'], dry_run=False))
        pairing = services.billable_confirmation_pairing(self.auditor, row['id'])
        self.assertEqual(pairing['status'], 'confirmed')
