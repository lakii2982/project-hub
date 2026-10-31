"""PH-370 orchestration execution: owner calls, links, idempotency and safety."""
import uuid
from types import SimpleNamespace
from unittest.mock import patch, Mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from core.models import AuditEvent
from pmo_plugins.cash.models import CashTransaction, FundingAccount, Refund
from pmo_plugins.invoicing import orchestration_services as orch
from pmo_plugins.invoicing.models import Invoice, InvoiceReversal
from tests.test_v1_invoice_tax import TaxFixture


class OrchFixture(TaxFixture):
    def setUp(self):
        super().setUp()
        self.invoice, _ = self.allocated()
        self.invoice_row = Invoice.objects.get(pk=self.invoice['invoice_ref']['id'])
        self.reversal = InvoiceReversal.objects.create(
            organization=self.org, invoice=self.invoice_row, reason='编排红冲',
            reversal_date=self.today, line_reversals=[], allocation_reversals=[],
            gross_cents=-self.invoice_row.gross_cents,
            net_cents=-self.invoice_row.net_cents,
            tax_cents=-self.invoice_row.tax_cents, created_by=self.owner)
        self.account = FundingAccount.objects.create(
            organization=self.org, name='演练账户', opening_on=self.today,
            created_by=self.owner, version=1)
        self.cash = CashTransaction.objects.create(
            organization=self.org, funding_account=self.account,
            transaction_date=self.today, direction='in', gross_cents=5000,
            external_source_key='PH370-1', payee={'kind': 'counterparty',
                                                  'id': str(self.invoice_row.counterparty_id)},
            counterparty_id=self.invoice_row.counterparty_id, evidence=[], status='posted',
            version=1, created_by=self.owner)

    def execute(self, **changes):
        values = dict(invoice_id=self.invoice_row.pk, reversal_id=self.reversal.pk,
                      action='cash_refund',
                      payload={'original_cash_transaction_id': str(self.cash.pk),
                               'refund_date': self.today.isoformat(), 'gross_cents': 1000,
                               'reason': '编排退款', 'evidence': [{'kind': 'owner_fact'}]},
                      request_token=uuid.uuid4(),
                      expected_version=self.invoice_row.version, dry_run=False)
        values.update(changes)
        return orch.execute_invoice_orchestration(self.owner, **values)


class OrchestrationTests(OrchFixture, TestCase):
    def test_cash_refund_executes_owner_and_records_link_once(self):
        result = self.execute()
        self.assertEqual(result['status'], 'executed')
        self.assertEqual(result['action'], 'cash_refund')
        self.assertEqual(result['link_kind'], 'cash_refund')
        self.assertFalse(result['single_transaction_claim'])
        self.assertEqual(Refund.objects.count(), 1)
        refund = Refund.objects.get()
        self.assertEqual(result['owner_ref']['id'], str(refund.pk))
        self.assertEqual(result['owner_ref']['kind'], 'cash_refund')
        self.assertEqual(AuditEvent.objects.filter(
            action='plugin.invoicing.orchestration_executed').count(), 1)
        self.assertEqual(AuditEvent.objects.filter(
            action='plugin.invoicing.orchestration_linked').count(), 1)
        snapshot = orch.orchestration_snapshot(self.owner, self.invoice_row.pk)
        self.assertEqual(len(snapshot['executions']), 1)
        self.assertEqual(snapshot['executions'][0]['status'], 'executed')

    def test_token_replay_returns_receipt_no_duplicate_owner_fact(self):
        token = uuid.uuid4()
        first = self.execute(request_token=token)
        replay = self.execute(request_token=token)
        self.assertEqual(first, replay)
        self.assertEqual(Refund.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(
            action='plugin.invoicing.orchestration_executed').count(), 1)
        with self.assertRaises(ValidationError):
            self.execute(request_token=token, payload={
                'original_cash_transaction_id': str(self.cash.pk),
                'refund_date': self.today.isoformat(), 'gross_cents': 2000,
                'reason': '改输入'})

    def test_failed_owner_action_leaves_no_execution_or_link(self):
        with self.assertRaises(ValidationError):
            self.execute(payload={
                'original_cash_transaction_id': str(self.cash.pk),
                'refund_date': self.today.isoformat(), 'gross_cents': 999999,
                'reason': '超额'})
        self.assertEqual(Refund.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.filter(
            action='plugin.invoicing.orchestration_executed').count(), 0)
        self.assertEqual(AuditEvent.objects.filter(
            action='plugin.invoicing.orchestration_link_pending').count(), 0)

    def test_permissions_and_invalid_action(self):
        with self.assertRaises(PermissionDenied):
            orch.execute_invoice_orchestration(
                self.auditor, invoice_id=self.invoice_row.pk, reversal_id=self.reversal.pk,
                action='cash_refund', payload={}, request_token=uuid.uuid4(),
                expected_version=self.invoice_row.version)
        with self.assertRaises(ValidationError):
            self.execute(action='bogus')
        with self.assertRaises(ValidationError):
            self.execute(expected_version=99)

    def test_cross_org_and_attribution_rejections(self):
        with self.assertRaises(PermissionDenied):
            self.execute(invoice_id=uuid.uuid4())
        other_cash = CashTransaction.objects.create(
            organization=self.org, funding_account=self.account,
            transaction_date=self.today, direction='in', gross_cents=500,
            external_source_key='PH370-OTHER', payee={'kind': 'counterparty',
                                                       'id': str(uuid.uuid4())},
            counterparty_id=uuid.uuid4(), evidence=[], status='posted', version=1,
            created_by=self.owner)
        with self.assertRaises(ValidationError):
            self.execute(payload={'original_cash_transaction_id': str(other_cash.pk),
                                  'refund_date': self.today.isoformat(),
                                  'gross_cents': 100, 'reason': '错往来方'})
        self.assertEqual(Refund.objects.count(), 0)

    def test_revenue_and_settlement_dispatch_use_named_services(self):
        adjustment = SimpleNamespace(pk=uuid.uuid4(), version=1,
                                     source_ref={'namespace': 'management_revenue',
                                                 'kind': 'revenue_adjustment',
                                                 'id': str(uuid.uuid4()), 'version': 1})
        with patch.object(orch.revenue_services, 'reverse_recognition',
                          return_value=adjustment) as revenue, \
             patch.object(orch.invoice_services, 'link_invoice_orchestration',
                          return_value={'status': 'linked'}) as link:
            result = self.execute(action='revenue_reversal', payload={
                'recognition_id': str(uuid.uuid4()), 'reason': '收入反冲'})
            self.assertEqual(result['link_kind'], 'revenue_reversal')
            revenue.assert_called_once()
            self.assertEqual(revenue.call_args.kwargs['reason'], '收入反冲')
            link.assert_called_once()
        clearance = SimpleNamespace(pk=uuid.uuid4(), version=1,
                                    clearance_ref={'namespace': 'settlement',
                                                   'kind': 'clearance',
                                                   'id': str(uuid.uuid4()), 'version': 1})
        with patch.object(orch.settlement_services, 'create_clearance',
                          return_value=clearance) as settle, \
             patch.object(orch.invoice_services, 'link_invoice_orchestration',
                          return_value={'status': 'linked'}):
            result = self.execute(action='settlement_clearance', payload={
                'open_item_id': str(uuid.uuid4()),
                'cash_transaction_ref': {'namespace': 'cash', 'kind': 'cash_transaction',
                                         'id': str(uuid.uuid4()), 'version': 1},
                'cleared_cents': 1000})
            self.assertEqual(result['link_kind'], 'settlement_adjustment')
            settle.assert_called_once()
