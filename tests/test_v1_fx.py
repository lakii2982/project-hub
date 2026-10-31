"""PH-321 contracts-only FX rate snapshots, conversion and read-only display."""
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from pmo_plugins.contracts import fx_services
from pmo_plugins.contracts.models import ContractLine
from tests.test_v1_contract_change import ContractChangeFixture


class FxFixture(ContractChangeFixture):
    def setUp(self):
        super().setUp()
        self.auditor = self.member('auditor')
        self.approved = self.approved_contract()
        self.line = self.approved.lines.first()

    def record(self, member=None, **changes):
        values = dict(quote_currency='USD', rate='7.20000000', effective_on=self.today,
                      source='manual', reference='银行牌价')
        values.update(changes)
        return fx_services.record_fx_rate(member or self.owner, **values)


class FxRateSnapshotTests(FxFixture, TestCase):
    def test_record_appends_versions_and_exact_date_resolution(self):
        first = self.record()
        second = self.record(self.finance, rate='7.30000000')
        self.assertEqual(first['version'], 1)
        self.assertEqual(second['version'], 2)
        self.assertEqual(fx_services.resolve_fx_rate(
            self.auditor, quote_currency='USD', effective_on=self.today)['rate'], '7.30000000')
        self.assertEqual(len(fx_services.fx_rate_list(self.owner)), 2)
        yesterday = (self.today - timedelta(days=1)).isoformat()
        with self.assertRaises(ValidationError):
            fx_services.resolve_fx_rate(self.owner, quote_currency='USD',
                                        effective_on=yesterday)
        with self.assertRaises(ValidationError):
            self.record(quote_currency='CNY')
        with self.assertRaises(ValidationError):
            self.record(rate='0')
        with self.assertRaises(ValidationError):
            self.record(effective_on=(self.today + timedelta(days=1)).isoformat())
        with self.assertRaises(ValidationError):
            self.record(source='unknown')
        before = fx_services.FxRateSnapshot.objects.count()
        plan = self.record(dry_run=True)
        self.assertTrue(plan['dry_run'])
        self.assertEqual(fx_services.FxRateSnapshot.objects.count(), before)

    def test_permissions_and_disabled_module_history_read(self):
        self.record()
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.record(self.auditor)
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.record(self.member('member'))
        with self.assertRaises((PermissionDenied, ValidationError)):
            fx_services.fx_rate_list(self.pm)
        with self.assertRaises((PermissionDenied, ValidationError)):
            fx_services.contract_line_fx_snapshots(self.member('member'))
        from core.plugin_runtime import get_registry, set_enabled
        manifests = get_registry().manifests
        for _ in range(len(manifests) + 1):
            for plugin_id in manifests:
                if plugin_id == 'contracts':
                    continue
                try:
                    set_enabled(self.owner, plugin_id, False)
                except Exception:
                    pass
        set_enabled(self.owner, 'contracts', False)
        self.assertEqual(len(fx_services.fx_rate_list(self.auditor)), 1)
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.record()

    def test_conversion_is_one_time_half_up_and_gross_derived(self):
        result = fx_services.convert_original_amounts(
            original_net_cents=10000, original_tax_cents=600,
            currency='USD', rate=Decimal('7.20000000'))
        self.assertEqual(result['net_cents'], 72000)
        self.assertEqual(result['tax_cents'], 4320)
        self.assertEqual(result['gross_cents'], 76320)
        self.assertEqual(result['algorithm_version'], 'fx_once_half_up_v1')
        self.assertEqual(fx_services.convert_cents(1, Decimal('0.5')), 1)
        self.assertEqual(fx_services.convert_cents(1, Decimal('0.49')), 0)
        with self.assertRaises(ValidationError):
            fx_services.convert_original_amounts(original_net_cents=-1, currency='USD',
                                                 rate=Decimal('7.2'))

    def test_append_only_model_and_database_guards(self):
        row = self.record()
        snapshot = fx_services.FxRateSnapshot.objects.get(pk=row['ref']['id'])
        snapshot.rate = Decimal('9.00000000')
        with self.assertRaises(ValidationError):
            snapshot.save()
        with self.assertRaises(ValidationError):
            snapshot.delete()
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                with connection.cursor() as cursor:
                    cursor.execute('UPDATE contracts_fxratesnapshot SET reference=%s WHERE id=%s',
                                   ['hacked', row['ref']['id'].replace('-', '')])
        with transaction.atomic():
            with self.assertRaises(IntegrityError):
                with connection.cursor() as cursor:
                    cursor.execute('DELETE FROM contracts_fxratesnapshot WHERE id=%s',
                                   [row['ref']['id'].replace('-', '')])

    def test_contract_line_read_only_original_book_and_missing(self):
        row = self.record()
        lines = fx_services.contract_line_fx_snapshots(self.owner)
        cny = next(item for item in lines if item['line_ref']['id'] == str(self.line.pk))
        self.assertEqual(cny['currency'], 'CNY')
        self.assertEqual(cny['missing'], [])
        self.assertEqual(cny['conversion']['algorithm_version'], 'legacy_cny_v1')
        ContractLine.objects.create(
            organization=self.org, contract_version=self.approved, line_no=99,
            net_cents=72000, tax_cents=0, gross_cents=72000, currency='USD',
            original_net_cents=10000, original_tax_cents=0, original_gross_cents=10000,
            fx_rate_ref=row['ref'])
        lines = fx_services.contract_line_fx_snapshots(self.owner)
        usd = next(item for item in lines if item['line_no'] == 99)
        self.assertEqual(usd['currency'], 'USD')
        self.assertEqual(usd['conversion']['rate'], '7.20000000')
        self.assertEqual(usd['missing'], [])
        pm_lines = fx_services.contract_line_fx_snapshots(self.pm)
        self.assertTrue(any(item['line_no'] == 99 for item in pm_lines))
        ContractLine.objects.create(
            organization=self.org, contract_version=self.approved, line_no=100,
            net_cents=500, tax_cents=0, gross_cents=500, currency='USD',
            original_net_cents=100, original_tax_cents=0, original_gross_cents=100,
            fx_rate_ref={'namespace': 'contracts', 'kind': 'fx_rate_snapshot',
                         'id': '00000000-0000-0000-0000-000000000000', 'version': 1})
        missing = next(item for item in fx_services.contract_line_fx_snapshots(self.owner)
                       if item['line_no'] == 100)
        self.assertEqual(missing['missing'], ['fx_rate'])
        self.assertEqual(missing['issues'][0]['issue_code'], 'fx_rate_missing')
        self.assertIsNone(missing['conversion'])


class FxWritePreparationTests(FxFixture, TestCase):
    def test_cny_keeps_legacy_amounts_and_non_cny_requires_exact_rate(self):
        cny = fx_services.prepare_contract_line_fx(
            self.owner, currency='CNY', original_net_cents=10000,
            original_tax_cents=600, effective_on=self.today)
        self.assertEqual((cny['net_cents'], cny['tax_cents'], cny['gross_cents']),
                         (10000, 600, 10600))
        self.assertIsNone(cny['fx_rate_ref'])
        with self.assertRaises(ValidationError):
            fx_services.prepare_contract_line_fx(
                self.owner, currency='USD', original_net_cents=10000,
                effective_on=self.today)
        row = self.record()
        usd = fx_services.prepare_contract_line_fx(
            self.owner, currency='USD', original_net_cents=10000,
            original_tax_cents=600, effective_on=self.today, fx_rate_ref=row['ref'])
        self.assertEqual((usd['net_cents'], usd['tax_cents'], usd['gross_cents']),
                         (72000, 4320, 76320))
        self.assertEqual(usd['fx_rate_ref'], row['ref'])
        self.assertEqual(usd['algorithm_version'], 'fx_once_half_up_v1')
        with self.assertRaises(ValidationError):
            fx_services.prepare_contract_line_fx(
                self.owner, currency='EUR', original_net_cents=10000,
                effective_on=self.today)
