"""PH-280 fee approval/cost/net read-only pairing regressions."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AuditEvent, DomainEvent, LedgerPosting,
                         SourceConsumption)
from pmo_plugins.cash.models import CashTransaction, FundingAccount
from pmo_plugins.costing import bank_charge_pairing_services as pairing
from pmo_plugins.costing import bank_charge_services as charges
from pmo_plugins.costing.models import BankCharge
from tests.test_v1_contracts import Fixture


class FeePairingFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.auditor = self.member('auditor')
        self.finance = self.member('finance')
        self.other_project = self.project  # project manager runs cost scope

    def cash(self, gross=9800, *, direction='in', status='posted', org=None, suffix=None):
        org = org or self.org
        account = FundingAccount.objects.create(
            organization=org, name='手续费账户' + uuid.uuid4().hex, opening_on=self.today,
            status='active', created_by=self.owner if org == self.org else self.owner)
        return CashTransaction.objects.create(
            organization=org, funding_account=account, transaction_date=self.today,
            direction=direction, gross_cents=gross,
            external_source_key='fee-' + (suffix or uuid.uuid4().hex),
            payee={'kind': 'text', 'name': '客户'}, evidence=[], status=status,
            version=1, created_by=self.owner)

    def cash_ref(self, cash):
        return {'namespace': 'cash', 'kind': 'cash_transaction', 'id': str(cash.pk),
                'version': cash.version}

    def draft(self, cash, gross=200, scope=None, **changes):
        values = dict(cash_transaction_ref=self.cash_ref(cash), gross_cents=gross,
                      tax_cents=None, scope_kind='project', scope_id=str((scope or self.project).pk),
                      evidence='银行手续费真实依据',
                      occurred_on=self.today, posting_period=self.today.strftime('%Y-%m'),
                      request_token=uuid.uuid4(), dry_run=False)
        values.update(changes)
        return charges.save_bank_charge_draft(self.owner, **values)

    def approved(self, cash, gross=200):
        charge = self.draft(cash, gross=gross)
        charges.submit_bank_charge(self.owner, charge.pk, request_token=uuid.uuid4(),
                                   expected_version=charge.version, dry_run=False)
        charge.refresh_from_db()
        charges.decide_bank_charge(self.finance, charge.pk, decision='approve',
                                   request_token=uuid.uuid4(), expected_version=charge.version,
                                   dry_run=False)
        charge.refresh_from_db()
        return charge

    def drop_charge_guards(self):
        with connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                cursor.execute('DROP TRIGGER IF EXISTS costing_bankcharge_guard '
                               'ON costing_bankcharge')
            else:
                for name in ('costing_bankcharge_insert_guard',
                             'costing_bankcharge_update_guard',
                             'costing_bankcharge_no_delete'):
                    cursor.execute(f'DROP TRIGGER IF EXISTS {name}')

    def synthetic(self, cash, *, status='approved', gross=200, scope=None, **changes):
        self.drop_charge_guards()
        values = dict(
            organization=self.org, cash_transaction_id=cash.pk,
            cash_transaction_ref=self.cash_ref(cash), gross_cents=gross, tax_cents=None,
            occurred_on=self.today, posting_period=self.today.strftime('%Y-%m'),
            scope_kind='project', scope_id=str((scope or self.project).pk), fee_bearer='company',
            evidence=[{'kind': 'bank_proof', 'evidence_id': 'synthetic'}],
            status=status, version=2 if status == 'approved' else 1, created_by=self.owner)
        if status == 'approved':
            values.update(submitted_by=self.owner, submitted_at=timezone.now(),
                          approved_by=self.finance, approved_at=timezone.now())
        values.update(changes)
        return BankCharge.objects.create(**values)

    def add_cost(self, charge, *, claim_amount=None, posting_amount=None, extra_event=False,
                 scope_id=None):
        claim = SourceConsumption.objects.create(
            organization=charge.organization, source=charge.source_ref, source_id=charge.pk,
            consumer='cost', coverage_key=f'bank_charge:{charge.pk}',
            amount_cents=claim_amount if claim_amount is not None else charge.gross_cents,
            event_ref={'namespace': 'core', 'kind': 'accounting_event', 'id': str(uuid.uuid4()),
                       'version': 1})
        event = AccountingEvent.objects.create(
            organization=charge.organization, source=charge.source_ref,
            event_type='costing.bank_charge.cost', occurred_on=self.today,
            posting_period=self.today.strftime('%Y-%m'), algorithm_version='bank_fee_cost_v1',
            claim=claim, claim_effect_cents=claim.amount_cents)
        LedgerPosting.objects.create(
            organization=charge.organization, accounting_event=event, book='cost',
            amount_cents=posting_amount if posting_amount is not None else charge.gross_cents,
            scope_kind='project', scope_id=scope_id or charge.scope_id, origin='current',
            line_index=1)
        if extra_event:
            AccountingEvent.objects.create(
                organization=charge.organization, source=charge.source_ref,
                event_type='costing.bank_charge.cost', occurred_on=self.today,
                posting_period=self.today.strftime('%Y-%m'), algorithm_version='bank_fee_cost_v1',
                claim=claim, claim_effect_cents=claim.amount_cents)
        return claim, event

    def codes(self, **filters):
        return {issue['issue_code'] for issue in
                pairing.bank_charge_pairing_issues(self.owner, **filters)}


class BankChargePairingServiceTests(FeePairingFixture, TestCase):
    def test_gold_approved_charge_is_posted_with_no_issues(self):
        charge = self.approved(self.cash(9800), gross=200)
        before = (AuditEvent.objects.count(), DomainEvent.objects.count(),
                  LedgerPosting.objects.count(), SourceConsumption.objects.count())
        rows = pairing.bank_charge_pairing(self.owner)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['charge_ref']['id'], str(charge.pk))
        self.assertEqual(row['status'], 'approved')
        self.assertEqual(row['fee_gross_cents'], 200)
        self.assertEqual(row['posted_net_cents'], 9800)
        self.assertEqual(row['net_fee']['implied_gross_cents'], 10000)
        self.assertEqual(row['cost']['cost_amount_cents'], 200)
        self.assertEqual(row['cost']['claim_count'], 1)
        self.assertEqual(row['cost']['claim_refs'][0]['coverage_key'], f'bank_charge:{charge.pk}')
        self.assertEqual(row['refund_return']['total_cents'], 0)
        self.assertEqual(row['pairing_status'], 'posted')
        self.assertEqual(row['issues'], [])
        self.assertFalse(row['tax_split_implemented'])
        self.assertEqual(row['ph251']['receipt_fee_settlement'], 'not_implemented')
        self.assertEqual(before, (AuditEvent.objects.count(), DomainEvent.objects.count(),
                                  LedgerPosting.objects.count(), SourceConsumption.objects.count()))
        self.assertEqual(pairing.bank_charge_pairing_issues(self.owner), [])

    def test_filters_and_permissions(self):
        charge = self.approved(self.cash(), gross=200)
        self.assertEqual(len(pairing.bank_charge_pairing(self.owner, status='approved')), 1)
        self.assertEqual(len(pairing.bank_charge_pairing(
            self.owner, cash_transaction_id=str(charge.cash_transaction_id))), 1)
        self.assertEqual(len(pairing.bank_charge_pairing(self.owner, status='draft')), 0)
        self.assertEqual(len(pairing.bank_charge_pairing(
            self.auditor, as_of=self.today.isoformat())), 1)
        with self.assertRaises(ValidationError):
            pairing.bank_charge_pairing(self.owner, as_of=(self.today + timedelta(days=1)).isoformat())
        with self.assertRaises(ValidationError):
            pairing.bank_charge_pairing(self.owner, status='bogus')
        with self.assertRaises((PermissionDenied, ValidationError)):
            pairing.bank_charge_pairing(self.member('member'))
        pm_rows = pairing.bank_charge_pairing(self.pm)
        self.assertEqual(len(pm_rows), 1)
        other = self.project.__class__.objects.create(
            organization=self.org, code='P280X', name='另一个项目', manager=self.owner,
            start_date=self.today, end_date=self.today)
        self.synthetic(self.cash(suffix='pm-other'), scope=other)
        self.assertEqual(len(pairing.bank_charge_pairing(self.pm)), 1)
        from core.plugin_runtime import get_registry, set_enabled
        for plugin_id in ('onboarding', 'time', 'management_revenue', 'settlement', 'delivery',
                          'contracts', 'costing'):
            if plugin_id in get_registry().manifests:
                set_enabled(self.owner, plugin_id, False)
        rows = pairing.bank_charge_pairing(self.auditor)
        self.assertIn(str(charge.pk), {row['charge_ref']['id'] for row in rows})

    def test_approved_without_cost_and_duplicate_event_issues(self):
        missing = self.synthetic(self.cash(suffix='missing'))
        row = pairing.bank_charge_pairing(self.owner, cash_transaction_id=str(missing.cash_transaction_id))[0]
        self.assertEqual(row['pairing_status'], 'approved_unposted')
        self.assertIn('approved_without_cost', row['issue_codes'])
        good = self.approved(self.cash(suffix='dup'), gross=200)
        claim = SourceConsumption.objects.get(source_id=good.pk)
        self.add_cost(good, extra_event=False) if False else None
        AccountingEvent.objects.create(
            organization=self.org, source=good.source_ref, event_type='costing.bank_charge.cost',
            occurred_on=self.today, posting_period=self.today.strftime('%Y-%m'),
            algorithm_version='bank_fee_cost_v1', claim=claim,
            claim_effect_cents=good.gross_cents)
        codes = self.codes()
        self.assertIn('cost_event_duplicate', codes)

    def test_cost_amount_mismatch_and_fee_over_cash_issues(self):
        mismatch = self.synthetic(self.cash(suffix='mismatch'))
        self.add_cost(mismatch, posting_amount=100)
        codes = self.codes()
        self.assertIn('cost_posting_amount_mismatch', codes)
        self.assertIn('partial', {row['pairing_status'] for row in
                                  pairing.bank_charge_pairing(self.owner)
                                  if row['charge_ref']['id'] == str(mismatch.pk)})
        over = self.synthetic(self.cash(9000, suffix='over'), gross=12000)
        row = pairing.bank_charge_pairing(self.owner,
                                          cash_transaction_id=str(over.cash_transaction_id))[0]
        self.assertIn('fee_exceeds_cash_gross', row['issue_codes'])

    def test_unapproved_cost_cash_and_cross_scope_issues(self):
        draft = self.synthetic(self.cash(suffix='draft'), status='draft')
        self.add_cost(draft)
        codes = self.codes()
        self.assertIn('unapproved_cost_posted', codes)
        not_posted = self.synthetic(self.cash(status='draft', suffix='draft-cash'))
        codes = self.codes()
        self.assertIn('cash_not_posted_in', codes)
        other_project = self.project.__class__.objects.create(
            organization=self.org, code='P280Y', name='错位项目', manager=self.owner,
            start_date=self.today, end_date=self.today)
        wrong = self.synthetic(self.cash(suffix='scope'))
        claim, event = self.add_cost(wrong, scope_id=str(other_project.pk))
        self.assertIn('cross_project_reference', self.codes())
