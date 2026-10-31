"""PH-371 tax qualification -> costing open-period cost adjustment."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, LedgerEntry, Organization)
from pmo_plugins.costing import tax_adjustment_services as services
from pmo_plugins.costing.models import SharedCostAllocationBatch, SharedCostAllocationReceipt
from pmo_plugins.invoicing import services as invoicing
from tests.test_v1_invoice import InvoiceFixture


class TaxAdjustmentFixture(InvoiceFixture):
    def setUp(self):
        super().setUp()
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'invoicing', True)
        set_enabled(self.owner, 'costing', True)
        self.invoice = self.record()
        self.invoice_ref = self.invoice['invoice_ref']
        self.qualification = invoicing.open_tax_qualification(
            self.owner, source_ref=self.invoice_ref, tax_cents=1000,
            evidence=[{'kind': 'tax_invoice', 'evidence_id': 'vat-1'}],
            request_token=uuid.uuid4(), dry_run=False)

    def confirm(self, status='not_recoverable', decided_on=None, member=None):
        qualification_id = self.qualification['qualification_ref']['id']
        from pmo_plugins.invoicing.models import TaxQualification
        row = TaxQualification.objects.get(pk=qualification_id)
        return invoicing.confirm_tax_qualification(
            member or self.owner, qualification_id, qualification_status=status,
            decided_on=decided_on or self.today, evidence=[{'kind': 'decision'}],
            request_token=uuid.uuid4(), expected_version=row.version, dry_run=False)

    def adjust(self, member=None, **changes):
        values = dict(request_token=uuid.uuid4(), dry_run=False)
        values.update(changes)
        qualification_id = values.pop('qualification_id',
                                      self.qualification['qualification_ref']['id'])
        return services.adjust_tax_cost(member or self.owner, qualification_id, **values)

    def facts(self):
        from pmo_plugins.cash.models import CashTransaction
        from pmo_plugins.management_revenue.models import RevenueRecognition
        from pmo_plugins.settlement.models import OpenItem
        return (LedgerEntry.objects.count(), AccountingEvent.objects.count(),
                SharedCostAllocationBatch.objects.count(),
                SharedCostAllocationReceipt.objects.count(),
                RevenueRecognition.objects.count(), CashTransaction.objects.count(),
                OpenItem.objects.count())


class TaxCostAdjustmentTests(TaxAdjustmentFixture, TestCase):
    def test_not_recoverable_creates_append_only_cost_batch(self):
        invoicing.allocate_invoice_lines(
            self.owner, invoice_id=self.invoice_ref['id'], lines=self.lines(),
            expected_version=1, request_token=uuid.uuid4(), dry_run=False)
        self.confirm('not_recoverable')
        before = self.facts()
        result = self.adjust()
        self.assertEqual(result['status'], 'posted')
        batch = SharedCostAllocationBatch.objects.get(pk=result['batch_ref']['id'])
        self.assertEqual(batch.pool_kind, 'tax_not_recoverable')
        self.assertEqual(batch.amount_cents, 1000)
        self.assertEqual(batch.status, 'posted')
        self.assertIn('tax_qualification', [ref['kind'] for ref in batch.source_refs])
        self.assertEqual(batch.total_basis_points, 10000)
        self.assertEqual(sum(line.basis_points for line in batch.lines.all()), 10000)
        self.assertEqual(sum(line.allocated_cents for line in batch.lines.all()),
                         batch.amount_cents)
        self.assertEqual(batch.amount_cents, 1000)
        self.assertEqual(self.facts()[0], before[0])
        self.assertEqual(self.facts()[1], before[1])
        self.assertEqual(self.facts()[4:], before[4:])

    def test_recoverable_and_pending_do_not_create_cost(self):
        self.confirm('recoverable')
        with self.assertRaises(ValidationError):
            self.adjust()
        self.assertEqual(SharedCostAllocationBatch.objects.count(), 0)

    def test_pending_qualification_rejected(self):
        with self.assertRaises(ValidationError):
            self.adjust()
        self.assertEqual(SharedCostAllocationBatch.objects.count(), 0)

    def test_closed_period_and_locked_period_rejected(self):
        self.confirm('not_recoverable')
        AccountingPeriod.objects.create(
            organization=self.org, period=self.today.strftime('%Y-%m'),
            status='posted', closed_through=self.today)
        with self.assertRaises(ValidationError):
            self.adjust()
        AccountingPeriod.objects.filter(
            organization=self.org, period=self.today.strftime('%Y-%m')).delete()
        Organization.objects.filter(pk=self.org.pk).update(locked_through=self.today)
        with self.assertRaises(ValidationError):
            self.adjust()
        self.assertEqual(SharedCostAllocationBatch.objects.count(), 0)

    def test_idempotency_replay_changed_input_and_duplicate(self):
        self.confirm('not_recoverable')
        token = uuid.uuid4()
        first = self.adjust(request_token=token)
        replay = self.adjust(request_token=token)
        self.assertEqual(replay['batch_ref'], first['batch_ref'])
        with self.assertRaises((ValidationError, PermissionDenied)):
            self.adjust(request_token=token, qualification_id=str(uuid.uuid4()))
        with self.assertRaises(ValidationError):
            self.adjust()
        self.assertEqual(SharedCostAllocationBatch.objects.count(), 1)

    def test_permissions_cross_org_and_module_disabled(self):
        self.confirm('not_recoverable')
        with self.assertRaises(PermissionDenied):
            self.adjust(member=self.plain)
        with self.assertRaises(PermissionDenied):
            self.adjust(member=self.auditor)
        other = Organization.objects.create(name='PH371 其他公司')
        from core.models import Membership
        from django.contrib.auth import get_user_model
        outsider = Membership.objects.create(
            organization=other, user=get_user_model().objects.create_user(
                username='ph371' + uuid.uuid4().hex), role='owner')
        with self.assertRaises(PermissionDenied):
            self.adjust(member=outsider)
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'costing', False)
        with self.assertRaises(PermissionDenied):
            self.adjust()

    def test_multi_project_weights_and_basis_sum(self):
        from unittest.mock import patch
        from pmo_plugins.costing import tax_adjustment_services as svc
        class FakeAlloc:
            def __init__(self, gross, pid):
                self.gross_cents = gross
                self.business_source_ref = {'namespace': 'contracts', 'kind': 'contract_line',
                                            'id': pid, 'version': 1}
        class FakeLine:
            def __init__(self, pid):
                self.contract_version = type('V', (), {'contract': type('C', (), {'project_id': pid})()})()
        invoice = type('I', (), {'pk': uuid.uuid4()})()
        with patch.object(svc.InvoiceAllocation.objects, 'filter',
                          return_value=[FakeAlloc(6000, 'p1'), FakeAlloc(4000, 'p2')]):
            with patch('pmo_plugins.contracts.models.ContractLine.objects.filter') as fake:
                fake.side_effect = lambda pk: type('Q', (), {'select_related': lambda self, *a: type('Q2', (), {'first': lambda self: FakeLine(pk)})()})()
                weights, allocated = svc._project_weights(invoice, 1000)
        self.assertEqual(sum(weights.values()), 10000)
        self.assertEqual(sum(cents for _, cents, _ in allocated), 1000)
        self.assertEqual({key for key, _, _ in allocated}, {'p1', 'p2'})

    def test_company_level_without_project_attribution_has_no_lines(self):
        self.confirm('not_recoverable')
        result = self.adjust()
        batch = SharedCostAllocationBatch.objects.get(pk=result['batch_ref']['id'])
        self.assertEqual(batch.lines.count(), 0)
        self.assertEqual(batch.amount_cents, 1000)

    def test_future_dated_qualification_rejected(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        fake = SimpleNamespace(
            pk=uuid.uuid4(), status='confirmed', qualification_status='not_recoverable',
            tax_cents=1000, source_ref=self.invoice_ref, version=1,
            decided_on=self.today + timedelta(days=3))
        with patch.object(services, '_qualification', return_value=fake):
            with self.assertRaises(ValidationError):
                self.adjust()
        self.assertEqual(SharedCostAllocationBatch.objects.count(), 0)

    def test_reversal_is_negative_append_only_and_once(self):
        self.confirm('not_recoverable')
        result = self.adjust()
        token = uuid.uuid4()
        reversal = services.reverse_tax_cost_adjustment(
            self.owner, result['batch_ref']['id'], reason='资格更正',
            request_token=token, dry_run=False)
        self.assertEqual(reversal['amount_cents'], -1000)
        batch = SharedCostAllocationBatch.objects.get(pk=reversal['reversal_ref']['id'])
        self.assertEqual(batch.kind, 'reversal')
        self.assertEqual(batch.status, 'posted')
        original = SharedCostAllocationBatch.objects.get(pk=result['batch_ref']['id'])
        self.assertEqual(batch.lines.count(), original.lines.count())
        for line in batch.lines.all():
            self.assertLess(line.allocated_cents, 0)
        replay = services.reverse_tax_cost_adjustment(
            self.owner, result['batch_ref']['id'], reason='资格更正',
            request_token=token, dry_run=False)
        self.assertEqual(replay['reversal_ref'], reversal['reversal_ref'])
        with self.assertRaises(ValidationError):
            services.reverse_tax_cost_adjustment(
                self.owner, result['batch_ref']['id'], reason='重复',
                request_token=uuid.uuid4(), dry_run=False)
