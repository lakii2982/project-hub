"""PH-228 cash facts: bank accounts, bank transactions and party matching (no posting)."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, DomainEvent, Employee, LedgerEntry,
                         LedgerPosting, Membership, Organization, PluginState, SourceConsumption)
from pmo_plugins.cash import funding_services as funding
from pmo_plugins.cash import transaction_services as cash
from pmo_plugins.cash.models import (CashPartyMatch, CashReceipt, CashTransaction, FundingAccount)
from pmo_plugins.contracts.models import Counterparty
from pmo_plugins.settlement.models import OpenItem, Settlement
from tests.test_v1_contracts import Fixture


class CashFixture(Fixture):
    """Real organization, counterparty, employee and funding accounts through named services."""

    today = timezone.localdate()

    def setUp(self):
        super().setUp()
        self.finance = self.member('finance')
        self.auditor = self.member('auditor')
        self.plain = self.member('member')
        self.employee = Employee.objects.create(organization=self.org, name='演练员工')
        self.account = self.active_account(name='默认基本户')

    # ---------- funding accounts ----------
    def save_account(self, *, name='基本户', masked='****1234', member=None, **changes):
        values = {'name': name, 'masked_identifier': masked, 'request_token': uuid.uuid4(),
                  'dry_run': False}
        values.update(changes)
        return funding.save_funding_account(member or self.owner, **values)

    def draft_account(self, **changes):
        return self.save_account(**changes)

    def activate(self, row, *, member=None, **changes):
        values = {'expected_version': row.version, 'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return funding.activate_funding_account(member or self.owner, row.pk, **values)

    def active_account(self, **changes):
        return self.activate(self.draft_account(**changes))

    # ---------- cash transactions ----------
    def tx(self, *, member=None, account=None, **changes):
        values = {'funding_account_id': (account or self.account).pk,
                  'transaction_date': self.today, 'direction': 'in', 'gross_cents': 101,
                  'external_source_key': 'BANK-' + uuid.uuid4().hex[:8],
                  'payee': {'kind': 'unidentified', 'bank_label': '客户回款'},
                  'evidence': [{'reference': '银行回单 001'}],
                  'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return cash.save_cash_transaction(member or self.owner, **values)

    def identify(self, row, payee, *, member=None, **changes):
        values = {'action': 'identify', 'identified_payee': payee,
                  'evidence': [{'reference': '匹配依据'}], 'expected_version': row.version,
                  'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return cash.match_party(member or self.owner, row.pk, **values)

    def reverse(self, row, original, *, member=None, **changes):
        values = {'action': 'reverse', 'match_ref': original.match_ref,
                  'reason': '原识别有误，反向重识别',
                  'evidence': [{'reference': '反向匹配依据'}],
                  'expected_version': row.version, 'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return cash.match_party(member or self.owner, row.pk, **values)

    # ---------- facts ----------
    def facts(self):
        return tuple(model.objects.count() for model in (
            FundingAccount, CashTransaction, CashPartyMatch, CashReceipt, AccountingEvent,
            LedgerPosting, LedgerEntry, Settlement, OpenItem, SourceConsumption))

    def denied(self, call, exceptions=(ValidationError, PermissionDenied)):
        before = self.facts()
        with self.assertRaises(exceptions):
            call()
        self.assertEqual(before, self.facts())
        return True


class FundingAccountTests(CashFixture, TestCase):
    def test_draft_create_update_and_private_projection(self):
        before = self.facts()
        preview = funding.save_funding_account(self.owner, name='预览户', request_token=uuid.uuid4())
        self.assertEqual(preview.status, 'draft')
        self.assertEqual(before, self.facts())
        row = self.draft_account(name='结算专户')
        self.assertEqual((row.status, row.version, row.account_owner), ('draft', 1, 'organization'))
        self.assertEqual((row.opening_cents, row.opening_on), (0, self.today))
        updated = funding.save_funding_account(self.owner, name='结算专户二',
            masked_identifier='****1234', account_id=row.pk,
            expected_version=row.version, request_token=uuid.uuid4(), dry_run=False)
        self.assertEqual((updated.version, updated.name), (2, '结算专户二'))
        self.denied(lambda: funding.save_funding_account(self.owner, name='再次', account_id=row.pk,
            expected_version=1, request_token=uuid.uuid4(), dry_run=False))
        snapshot = funding.funding_account_snapshot(self.finance, row.pk)
        self.assertEqual((snapshot['status'], snapshot['is_canonical']), ('draft', False))
        self.assertEqual(snapshot['masked_identifier'], '****1234')
        for actor in (self.owner, self.finance, self.auditor):
            self.assertEqual(len(funding.funding_account_list(actor)), 2)
        for actor in (self.pm, self.plain):
            self.denied(lambda actor=actor: funding.funding_account_list(actor))
            self.denied(lambda actor=actor: funding.funding_account_snapshot(actor, row.pk))

    def test_duplicate_name_validation_and_bool_dry_run(self):
        self.draft_account(name='唯一户')
        self.denied(lambda: self.draft_account(name='唯一户'))
        for changes in ({'name': ''}, {'name': 'x' * 121}, {'masked': 'x' * 65}):
            self.denied(lambda changes=changes: self.draft_account(**changes))
        for value in (0, 1, 1.0, None, 'false'):
            self.denied(lambda value=value: funding.save_funding_account(
                self.owner, name='布尔户', request_token=uuid.uuid4(), dry_run=value))

    def test_only_owner_activates_and_active_account_is_immutable(self):
        draft = self.draft_account()
        for actor in (self.finance, self.pm, self.auditor, self.plain):
            self.denied(lambda actor=actor: self.activate(draft, member=actor))
        active = self.activate(draft)
        self.assertEqual((active.status, active.version), ('active', 2))
        self.assertTrue(funding.funding_account_snapshot(self.owner, draft.pk)['is_canonical'])
        self.denied(lambda: self.activate(active))
        self.denied(lambda: funding.save_funding_account(self.owner, name='改名',
            account_id=active.pk, expected_version=active.version,
            request_token=uuid.uuid4(), dry_run=False))
        active.name = '直接改名'
        with self.assertRaises(ValidationError):
            active.save()

    def test_activation_receipt_replay_and_stale_version(self):
        draft = self.draft_account()
        token = uuid.uuid4()
        first = funding.activate_funding_account(self.owner, draft.pk, expected_version=1,
            request_token=token, dry_run=False)
        again = funding.activate_funding_account(self.owner, draft.pk, expected_version=1,
            request_token=token, dry_run=False)
        self.assertEqual(again.pk, first.pk)
        self.denied(lambda: funding.activate_funding_account(self.owner, draft.pk,
            expected_version=2, request_token=token, dry_run=False))
        other = self.draft_account(name='竞争户')
        self.denied(lambda: self.activate(other, expected_version=other.version + 1))
        for value in (True, 1.0, 0, -1, '1'):
            self.denied(lambda value=value: self.activate(other, expected_version=value))

    def test_opening_balance_and_opening_date_are_frozen(self):
        draft = self.draft_account(name='冻结户')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FundingAccount.objects.filter(pk=draft.pk).update(opening_cents=100)
        row = FundingAccount.objects.get(pk=draft.pk)
        row.opening_cents = 100
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                FundingAccount.objects.filter(pk=draft.pk).update(
                    opening_on=self.today - timedelta(days=1))
        row = FundingAccount.objects.get(pk=draft.pk)
        row.opening_on = self.today - timedelta(days=1)
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            FundingAccount.objects.create(organization=self.org, name='非法期初',
                opening_cents=100, opening_on=self.today, created_by=self.owner)
        updated = funding.save_funding_account(self.owner, name='冻结户二',
            masked_identifier='****0001', account_id=draft.pk, expected_version=draft.version,
            request_token=uuid.uuid4(), dry_run=False)
        self.assertEqual(self.activate(updated).status, 'active')

    def test_module_disable_keeps_history_readable_but_writes_refuse(self):
        active = self.active_account()
        PluginState.objects.update_or_create(organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.assertEqual(funding.funding_account_snapshot(self.auditor, active.pk)['status'], 'active')
        self.assertEqual(len(funding.funding_account_list(self.finance)), 2)
        self.denied(lambda: self.draft_account(name='停用户'))
        self.denied(lambda: self.activate(self.draft_account(name='停用激活')))


class CashTransactionTests(CashFixture, TestCase):
    def test_draft_variants_and_derived_counterparty(self):
        unidentified = self.tx(payee={'kind': 'unidentified', 'bank_label': '客户 A'})
        self.assertIsNone(unidentified.counterparty_id)
        self.assertEqual(unidentified.payee, {'kind': 'unidentified', 'bank_label': '客户 A'})
        external = self.tx(payee={'kind': 'counterparty', 'id': str(self.party.pk)}, direction='out')
        self.assertEqual(external.counterparty_id, self.party.pk)
        own = self.tx(payee={'kind': 'own_account', 'id': str(self.account.pk)})
        self.assertIsNone(own.counterparty_id)
        snapshot = cash.cash_transaction_snapshot(self.finance, external.pk)
        self.assertEqual(snapshot['counterparty_id'], str(self.party.pk))
        self.assertEqual(snapshot['direction_display'], '支出')
        self.assertNotIn('allocated_cents', snapshot)
        self.assertNotIn('available_cents', snapshot)
        self.assertEqual(len(cash.cash_transaction_list(self.auditor)), 3)
        self.assertEqual(len(cash.cash_transaction_list(self.auditor, as_of=self.today)), 3)
        self.assertEqual(len(cash.cash_transaction_list(self.auditor,
                                                        as_of=self.today - timedelta(days=1))), 0)

    def test_validation_rejections_have_no_half_write(self):
        for changes in ({'transaction_date': self.today + timedelta(days=1)},
                        {'gross_cents': True}, {'gross_cents': False}, {'gross_cents': 101.0},
                        {'gross_cents': 0}, {'gross_cents': -1}, {'gross_cents': 10**12 + 1},
                        {'direction': 'inout'}, {'direction': ['in']},
                        {'external_source_key': ''}, {'external_source_key': 'x' * 201},
                        {'evidence': []}, {'payee': {'kind': 'employee', 'id': self.employee.pk}},
                        {'payee': {'kind': 'unidentified', 'extra': 1}},
                        {'payee': {'kind': 'counterparty', 'id': str(uuid.uuid4())}},
                        {'payee': {'kind': 'own_account', 'id': str(uuid.uuid4())}},
                        {'payee': {'kind': 'nope'}}, {'payee': 'bank'}):
            self.denied(lambda changes=changes: self.tx(**changes))
        draft = self.draft_account(name='草稿账户')
        self.denied(lambda: self.tx(account=draft))
        self.denied(lambda: self.tx(funding_account_id=uuid.uuid4()))
        self.org.locked_through = self.today
        self.org.save()
        self.denied(lambda: self.tx())
        self.org.locked_through = self.today - timedelta(days=1)
        self.org.save()
        self.tx()
        AccountingPeriod.objects.update_or_create(organization=self.org,
            period=self.today.strftime('%Y-%m'),
            defaults={'status': 'posted', 'closed_through': self.today})
        self.denied(lambda: self.tx())

    def test_payee_and_matching_require_active_party_window_and_account(self):
        inactive = Counterparty.objects.create(organization=self.org, display_name='停用客户',
            roles=['customer'], active_from=self.today, status='inactive')
        expired = Counterparty.objects.create(organization=self.org, display_name='过期客户',
            roles=['customer'], active_from=self.today - timedelta(days=10),
            inactive_on=self.today - timedelta(days=1))
        future = Counterparty.objects.create(organization=self.org, display_name='未来客户',
            roles=['customer'], active_from=self.today + timedelta(days=1))
        for party in (inactive, expired, future):
            self.denied(lambda party=party: self.tx(
                payee={'kind': 'counterparty', 'id': str(party.pk)}))
            row = self.tx()
            self.denied(lambda party=party, row=row: self.identify(
                row, {'kind': 'counterparty', 'id': str(party.pk)}))
        self.denied(lambda: self.tx(payee={'kind': 'counterparty', 'id': str(self.party.pk)},
                                    transaction_date=self.today + timedelta(days=1)))
        Employee.objects.filter(pk=self.employee.pk).update(active=False)
        row = self.tx()
        self.denied(lambda: self.identify(row, {'kind': 'employee', 'id': self.employee.pk}))
        draft_account = self.draft_account(name='草稿自有户')
        self.denied(lambda: self.tx(payee={'kind': 'own_account',
                                           'id': str(draft_account.pk)}))
        self.denied(lambda: self.identify(row, {'kind': 'own_account',
                                                'id': str(draft_account.pk)}))
        self.assertEqual(CashPartyMatch.objects.count(), 0)

    def test_cross_organization_account_and_payee_are_refused(self):
        other = Organization.objects.create(name='其他公司')
        foreign = FundingAccount.objects.create(organization=other, name='外部户',
            opening_on=self.today, created_by=self.owner)
        self.denied(lambda: self.tx(funding_account_id=foreign.pk))
        foreign_party = Counterparty.objects.create(organization=other, display_name='外部客户',
            roles=['customer'], active_from=self.today)
        self.denied(lambda: self.tx(payee={'kind': 'counterparty', 'id': str(foreign_party.pk)}))

    def test_duplicate_external_key_replays_same_input_and_conflicts_on_change(self):
        token = uuid.uuid4()
        first = self.tx(external_source_key='KEY-1', request_token=token)
        before = self.facts()
        replay = self.tx(external_source_key='KEY-1')
        self.assertEqual(replay.pk, first.pk)
        self.assertEqual(before, self.facts())
        same_token = self.tx(external_source_key='KEY-1', request_token=token)
        self.assertEqual(same_token.pk, first.pk)
        for changes in ({'gross_cents': 202}, {'direction': 'out'},
                        {'transaction_date': self.today - timedelta(days=1)},
                        {'payee': {'kind': 'unidentified', 'bank_label': '别人'}},
                        {'evidence': [{'reference': '另一依据'}]}):
            self.denied(lambda changes=changes: self.tx(external_source_key='KEY-1', **changes))
        second = self.tx(external_source_key='KEY-2')
        self.denied(lambda: self.tx(transaction_id=second.pk, external_source_key='KEY-1',
                                    expected_version=second.version))

    def test_replay_survives_later_party_deactivation(self):
        token = uuid.uuid4()
        first = self.tx(payee={'kind': 'counterparty', 'id': str(self.party.pk)},
                        external_source_key='LATE-1', request_token=token)
        other = self.tx(external_source_key='LATE-2')
        match_token = uuid.uuid4()
        match = self.identify(other, {'kind': 'employee', 'id': self.employee.pk},
                              request_token=match_token)
        Counterparty.objects.filter(pk=self.party.pk).update(status='inactive')
        Employee.objects.filter(pk=self.employee.pk).update(active=False)
        before = self.facts()
        replay = self.tx(payee={'kind': 'counterparty', 'id': str(self.party.pk)},
                         external_source_key='LATE-1')
        self.assertEqual(replay.pk, first.pk)
        same_token = self.tx(payee={'kind': 'counterparty', 'id': str(self.party.pk)},
                             external_source_key='LATE-1', request_token=token)
        self.assertEqual(same_token.pk, first.pk)
        match_replay = cash.match_party(self.owner, other.pk, action='identify',
            identified_payee={'kind': 'employee', 'id': self.employee.pk},
            evidence=[{'reference': '匹配依据'}], expected_version=other.version,
            request_token=match_token, dry_run=False)
        self.assertEqual(match_replay.pk, match.pk)
        self.assertEqual(before, self.facts())
        self.denied(lambda: self.tx(payee={'kind': 'counterparty', 'id': str(self.party.pk)},
                                    external_source_key='LATE-1', gross_cents=999))
        self.denied(lambda: self.tx(payee={'kind': 'counterparty', 'id': str(self.party.pk)}))
        fresh = self.tx()
        self.denied(lambda: self.identify(fresh, {'kind': 'employee',
                                                  'id': self.employee.pk}))

    def test_draft_edit_version_and_receipt_replay(self):
        row = self.tx()
        updated = self.tx(transaction_id=row.pk, expected_version=row.version, gross_cents=202)
        self.assertEqual((updated.version, updated.gross_cents), (2, 202))
        self.denied(lambda: self.tx(transaction_id=row.pk, expected_version=1, gross_cents=303))
        token = uuid.uuid4()
        created = self.tx(external_source_key='REPLAY', request_token=token)
        replay = self.tx(external_source_key='REPLAY', request_token=token)
        self.assertEqual(replay.pk, created.pk)
        self.denied(lambda: self.tx(external_source_key='REPLAY', gross_cents=999,
                                    request_token=token))

    def test_non_draft_rows_are_guarded(self):
        row = self.tx()
        CashTransaction.objects.filter(pk=row.pk).update(status='posted')
        stale = CashTransaction.objects.get(pk=row.pk)
        stale.gross_cents = 999
        with self.assertRaises(ValidationError):
            stale.save()
        with self.assertRaises(ValidationError):
            stale.delete()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                CashTransaction.objects.filter(pk=row.pk).update(gross_cents=1)

    def test_roles_revocation_and_module_disable(self):
        row = self.tx()
        for actor in (self.pm, self.plain):
            self.denied(lambda actor=actor: cash.cash_transaction_snapshot(actor, row.pk))
            self.denied(lambda actor=actor: cash.cash_transaction_list(actor))
            self.denied(lambda actor=actor: self.tx(member=actor))
            self.denied(lambda actor=actor: self.identify(row, {'kind': 'employee',
                                                                'id': self.employee.pk},
                                                          member=actor))
        Membership.objects.filter(pk=self.owner.pk).update(active=False)
        self.denied(lambda: self.tx())
        Membership.objects.filter(pk=self.owner.pk).update(active=True)
        PluginState.objects.update_or_create(organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.assertEqual(cash.cash_transaction_snapshot(self.auditor, row.pk)['status'], 'draft')
        self.assertEqual(len(cash.cash_transaction_list(self.finance)), 1)
        self.denied(lambda: self.tx())
        self.denied(lambda: self.identify(row, {'kind': 'employee', 'id': self.employee.pk}))

    def test_cash_facts_never_touch_ledger_accounting_or_settlement(self):
        before = self.facts()
        row = self.tx(payee={'kind': 'counterparty', 'id': str(self.party.pk)})
        identified = self.identify(row, {'kind': 'counterparty', 'id': str(self.party.pk)})
        self.reverse(row, identified)
        after = self.facts()
        self.assertEqual((after[0], after[1], after[2], after[3]),
                         (before[0], before[1] + 1, before[2] + 2, before[3] + 3))
        self.assertEqual(before[4:], after[4:])
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.assertEqual(LedgerPosting.objects.count(), 0)
        self.assertEqual(AccountingEvent.objects.count(), 0)
        self.assertFalse(DomainEvent.objects.filter(topic='cash.transaction_recorded.v1').exists())
        self.assertTrue(DomainEvent.objects.filter(topic='cash.cash_transaction.saved.v1').exists())


class CashPartyMatchTests(CashFixture, TestCase):
    def test_identify_counterparty_employee_and_own_account(self):
        external = self.tx(payee={'kind': 'unidentified'})
        match = self.identify(external, {'kind': 'counterparty', 'id': str(self.party.pk)})
        self.assertEqual((match.action, match.identified_payee, match.reason),
                         ('identify', {'kind': 'counterparty', 'id': str(self.party.pk)}, None))
        self.assertIsNone(match.original_match_id)
        employee_match = self.identify(self.tx(), {'kind': 'employee', 'id': self.employee.pk})
        self.assertEqual(employee_match.identified_payee,
                         {'kind': 'employee', 'id': self.employee.pk})
        own_match = self.identify(self.tx(), {'kind': 'own_account', 'id': str(self.account.pk)})
        self.assertEqual(own_match.identified_payee['kind'], 'own_account')
        snapshot = cash.cash_transaction_snapshot(self.owner, external.pk)
        self.assertEqual(len(snapshot['matches']), 1)
        self.assertEqual(snapshot['matches'][0]['identified_payee_display'],
                         '往来方 · ' + str(self.party.pk))
        self.assertEqual(funding.funding_account_list(self.owner)[0]['transaction_count'], 3)

    def test_identify_rejections_have_no_half_write(self):
        row = self.tx()
        other_tx = self.tx()
        invalid_payees = [
            {'kind': 'counterparty', 'id': str(uuid.uuid4())},
            {'kind': 'employee', 'id': 999999},
            {'kind': 'employee', 'id': True},
            {'kind': 'own_account', 'id': str(uuid.uuid4())},
            {'kind': 'employee'},
            {'kind': 'employee', 'id': self.employee.pk, 'extra': 1},
            {'kind': 'nope', 'id': 1},
            None, 'counterparty',
        ]
        for payee in invalid_payees:
            self.denied(lambda payee=payee: self.identify(row, payee))
        self.denied(lambda: self.identify(row, {'kind': 'employee', 'id': self.employee.pk},
                                          evidence=[]))
        self.denied(lambda: self.identify(row, {'kind': 'employee', 'id': self.employee.pk},
                                          reason='不允许原因'))
        self.denied(lambda: self.identify(row, {'kind': 'employee', 'id': self.employee.pk},
                                          match_ref={'namespace': 'cash',
                                                     'kind': 'cash_party_match',
                                                     'id': str(uuid.uuid4()), 'version': 1}))
        self.denied(lambda: self.identify(row, {'kind': 'employee', 'id': self.employee.pk},
                                          expected_version=row.version + 1))
        self.denied(lambda: self.identify(other_tx, {'kind': 'employee', 'id': self.employee.pk},
                                          evidence=''))
        self.assertEqual(CashPartyMatch.objects.count(), 0)

    def test_reverse_requires_original_match_and_cannot_repeat(self):
        row = self.tx()
        original = self.identify(row, {'kind': 'counterparty', 'id': str(self.party.pk)})
        before = cash.cash_transaction_snapshot(self.owner, row.pk)
        reverse = self.reverse(row, original)
        self.assertEqual((reverse.action, reverse.identified_payee, reverse.original_match_id),
                         ('reverse', None, original.pk))
        self.assertEqual(reverse.reason, '原识别有误，反向重识别')
        self.assertEqual(reverse.original_match.match_ref, original.match_ref)
        after = cash.cash_transaction_snapshot(self.owner, row.pk)
        self.assertEqual((after['gross_cents'], after['direction'], after['transaction_date'],
                          after['payee'], after['version']),
                         (before['gross_cents'], before['direction'], before['transaction_date'],
                          before['payee'], before['version']))
        self.denied(lambda: self.reverse(row, original))
        self.denied(lambda: self.reverse(row, reverse))
        other = self.tx()
        foreign_original = self.identify(other, {'kind': 'employee', 'id': self.employee.pk})
        self.denied(lambda: self.reverse(row, foreign_original))
        self.denied(lambda: self.reverse(row, original, reason=''))
        for ref in ({'namespace': 'cash', 'kind': 'cash_transaction',
                     'id': str(original.pk), 'version': original.version},
                    {'namespace': 'contracts', 'kind': 'cash_party_match',
                     'id': str(original.pk), 'version': original.version},
                    {'namespace': 'cash', 'kind': 'cash_party_match', 'id': str(uuid.uuid4()),
                     'version': 1},
                    {'namespace': 'cash', 'kind': 'cash_party_match', 'id': str(original.pk),
                     'version': original.version + 1}):
            self.denied(lambda ref=ref: cash.match_party(self.owner, row.pk, action='reverse',
                match_ref=ref, reason='反向', evidence=[{'reference': '依据'}],
                expected_version=row.version, request_token=uuid.uuid4(), dry_run=False))
        self.assertFalse(CashPartyMatch.objects.filter(action='reverse',
                                                       original_match__isnull=True).exists())

    def test_identify_is_frozen_to_the_current_effective_match(self):
        row = self.tx()
        first = self.identify(row, {'kind': 'employee', 'id': self.employee.pk})
        self.denied(lambda: self.identify(row, {'kind': 'counterparty',
                                                'id': str(self.party.pk)}))
        first_reverse = self.reverse(row, first)
        self.denied(lambda: self.reverse(row, first))
        second = self.identify(row, {'kind': 'counterparty', 'id': str(self.party.pk)})
        self.denied(lambda: self.identify(row, {'kind': 'employee', 'id': self.employee.pk}))
        self.denied(lambda: self.reverse(row, first))
        second_reverse = self.reverse(row, second)
        third = self.identify(row, {'kind': 'own_account', 'id': str(self.account.pk)})
        actions = list(row.matches.order_by('created_at', 'id').values_list('action', flat=True))
        self.assertEqual(actions, ['identify', 'reverse', 'identify', 'reverse', 'identify'])
        self.assertEqual((first_reverse.original_match_id, second_reverse.original_match_id),
                         (first.pk, second.pk))
        self.assertEqual(third.identified_payee, {'kind': 'own_account',
                                                  'id': str(self.account.pk)})
        self.denied(lambda: self.reverse(row, third, reason=''))

    def test_match_dry_run_and_receipt_replay(self):
        row = self.tx()
        before = self.facts()
        preview = cash.match_party(self.owner, row.pk, action='identify',
            identified_payee={'kind': 'employee', 'id': self.employee.pk},
            evidence=[{'reference': '匹配依据'}], expected_version=row.version,
            request_token=uuid.uuid4())
        self.assertEqual(preview.action, 'identify')
        self.assertEqual(before, self.facts())
        token = uuid.uuid4()
        first = cash.match_party(self.owner, row.pk, action='identify',
            identified_payee={'kind': 'employee', 'id': self.employee.pk},
            evidence=[{'reference': '匹配依据'}], expected_version=row.version,
            request_token=token, dry_run=False)
        replay = cash.match_party(self.owner, row.pk, action='identify',
            identified_payee={'kind': 'employee', 'id': self.employee.pk},
            evidence=[{'reference': '匹配依据'}], expected_version=row.version,
            request_token=token, dry_run=False)
        self.assertEqual(replay.pk, first.pk)
        self.denied(lambda: cash.match_party(self.owner, row.pk, action='identify',
            identified_payee={'kind': 'own_account', 'id': str(self.account.pk)},
            evidence=[{'reference': '匹配依据'}], expected_version=row.version,
            request_token=token, dry_run=False))
        for value in (0, 1, 1.0, None, 'false'):
            self.denied(lambda value=value: cash.match_party(self.owner, row.pk,
                action='identify', identified_payee={'kind': 'employee', 'id': self.employee.pk},
                evidence=[{'reference': '匹配依据'}], expected_version=row.version,
                request_token=uuid.uuid4(), dry_run=value))

    def test_match_roles_and_append_only_guards(self):
        row = self.tx()
        for actor in (self.pm, self.plain, self.auditor):
            self.denied(lambda actor=actor: self.identify(
                row, {'kind': 'employee', 'id': self.employee.pk}, member=actor))
        match = self.identify(row, {'kind': 'employee', 'id': self.employee.pk})
        match.reason = '直接修改'
        with self.assertRaises(ValidationError):
            match.save()
        with self.assertRaises(ValidationError):
            match.delete()
        receipt = CashReceipt.objects.first()
        receipt.action = 'other'
        with self.assertRaises(ValidationError):
            receipt.save()
        with self.assertRaises(ValidationError):
            receipt.delete()
        PluginState.objects.update_or_create(organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.assertEqual(len(cash.cash_transaction_snapshot(self.auditor, row.pk)['matches']), 1)
        self.denied(lambda: self.identify(row, {'kind': 'employee', 'id': self.employee.pk}))
