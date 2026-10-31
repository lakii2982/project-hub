"""PH-388 FX history reverse lookup and cross-owner reconciliation."""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from pmo_plugins.contracts import fx_services
from pmo_plugins.contracts.models import ContractLine
from pmo_plugins.invoicing.models import Invoice
from tests.test_v1_fx import FxFixture


class FxHistoryFixture(FxFixture):
    def non_cny_line(self, rate_ref, **changes):
        values = dict(organization=self.org, contract_version=self.approved, line_no=99,
                      net_cents=72000, tax_cents=0, gross_cents=72000, currency='USD',
                      original_net_cents=10000, original_tax_cents=0,
                      original_gross_cents=10000, fx_rate_ref=rate_ref)
        values.update(changes)
        return ContractLine.objects.create(**values)

    def invoice(self):
        return Invoice.objects.create(
            organization=self.org, direction='sales', counterparty=self.party,
            invoice_number='INV-388-%s' % uuid.uuid4().hex[:6], invoice_date=self.today,
            gross_cents=10600, net_cents=10000, tax_cents=600, status='active',
            created_by=self.owner)


class FxRateHistoryTests(FxHistoryFixture, TestCase):
    def test_exact_date_returns_all_versions_and_never_falls_back(self):
        first = self.record(rate='7.20000000')
        second = self.record(rate='7.30000000')
        history = fx_services.fx_rate_history(
            self.owner, quote_currency='USD', effective_on=self.today.isoformat())
        self.assertEqual([row['version'] for row in history['rows']], [1, 2])
        self.assertEqual(history['missing'], [])
        self.assertNotEqual(first['ref']['id'], second['ref']['id'])
        other = (self.today - timedelta(days=30)).isoformat()
        missing = fx_services.fx_rate_history(
            self.owner, quote_currency='USD', effective_on=other)
        self.assertEqual(missing['rows'], [])
        self.assertEqual(missing['missing'], ['fx_rate_exact_date_missing'])
        self.assertTrue(missing['no_fallback'])

    def test_future_rate_is_rejected_for_history_and_reconciliation(self):
        future = self.today + timedelta(days=10)
        with self.assertRaises(ValidationError):
            fx_services.fx_rate_history(self.owner, quote_currency='USD',
                                        effective_on=future.isoformat())
        self.record(effective_on=future)
        with self.assertRaises(ValidationError):
            fx_services.fx_reconciliation(self.owner, as_of=future.isoformat())
        # A future rate cannot reconcile a historical fact, even if a bad ref points at it.
        future_ref = self.record(effective_on=future)['ref']
        self.non_cny_line(future_ref)
        recon = fx_services.fx_reconciliation(self.owner, as_of=self.today.isoformat())
        row = next(item for item in recon['contracts'] if item['line_no'] == 99)
        self.assertIn('fx_rate_ref', row['missing'])
        self.assertIn('fx_rate_invalid_or_late', [issue['issue_code'] for issue in row['issues']])

    def test_permissions_and_history_read_when_module_disabled(self):
        self.record()
        self.assertEqual(len(fx_services.fx_rate_history(self.auditor)['rows']), 1)
        with self.assertRaises(PermissionDenied):
            fx_services.fx_rate_history(self.member('member'))
        from core.plugin_runtime import get_registry, set_enabled
        for _ in range(len(get_registry().manifests) + 1):
            for plugin_id in get_registry().manifests:
                if plugin_id == 'contracts':
                    continue
                try:
                    set_enabled(self.owner, plugin_id, False)
                except ValidationError:
                    pass
        set_enabled(self.owner, 'contracts', False)
        self.assertEqual(len(fx_services.fx_rate_history(self.auditor)['rows']), 1)


class FxReconciliationTests(FxHistoryFixture, TestCase):
    def test_contract_conversion_consistent_and_invoice_missing_is_explicit(self):
        rate = self.record()
        self.non_cny_line(rate['ref'])
        self.invoice()
        result = fx_services.fx_reconciliation(self.owner, as_of=self.today.isoformat())
        usd = next(item for item in result['contracts'] if item['line_no'] == 99)
        self.assertEqual(usd['missing'], [])
        self.assertEqual(usd['issues'], [])
        self.assertEqual(result['summary']['contract_inconsistent'], 0)
        self.assertEqual(result['summary']['invoice_fx_unsupported'], 1)
        invoice = result['invoices'][0]
        self.assertIn('invoice_original_currency_fields_missing', invoice['missing'])
        self.assertIn('invoice_fx_fields_not_implemented',
                      [issue['issue_code'] for issue in invoice['issues']])
        self.assertEqual(invoice['book_net_cents'], 10000)

    def test_conversion_mismatch_is_reported_without_writing(self):
        rate = self.record()
        self.non_cny_line(rate['ref'], net_cents=70000, gross_cents=70000)
        before = ContractLine.objects.count()
        result = fx_services.fx_reconciliation(self.owner, as_of=self.today.isoformat())
        row = next(item for item in result['contracts'] if item['line_no'] == 99)
        self.assertIn('fx_conversion_mismatch', row['missing'])
        self.assertEqual(ContractLine.objects.count(), before)
        self.assertEqual(result['summary']['contract_inconsistent'], 1)

    def test_pm_scope_and_cross_org_are_limited(self):
        rate = self.record()
        self.non_cny_line(rate['ref'])
        pm = fx_services.fx_reconciliation(self.pm, as_of=self.today.isoformat())
        self.assertTrue(any(item['line_no'] == 99 for item in pm['contracts']))
        self.assertEqual(pm['invoices'], [])
        with self.assertRaises(PermissionDenied):
            fx_services.fx_reconciliation(self.member('member'))


from tests.test_v1_settlement import SettlementFixture


class FxSettlementReconciliationTests(SettlementFixture, TestCase):
    def test_active_cny_settlement_is_included_consistently(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        settlement = self.settle(self.term_values('payment_term')[:1])
        result = fx_services.fx_reconciliation(
            self.owner, as_of=self.today.isoformat())
        row = next(item for item in result['settlements']
                   if item['source_ref']['id'] == str(settlement.pk))
        self.assertEqual(row['currency'], 'CNY')
        self.assertEqual(row['missing'], [])
