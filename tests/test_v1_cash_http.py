"""PH-228 HTTP browser boundaries for funding accounts, bank facts and party matching."""
import uuid
from datetime import timedelta

from django.test import Client, TestCase
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.models import (AccountingEvent, Employee, LedgerEntry, LedgerPosting,
                         Organization, SourceConsumption)
from pmo_plugins.cash import funding_services as funding
from pmo_plugins.cash import transaction_services as tx
from pmo_plugins.cash.models import CashPartyMatch, CashReceipt, CashTransaction, FundingAccount
from pmo_plugins.contracts.models import Counterparty
from pmo_plugins.contracts.party_services import create_party
from pmo_plugins.settlement.models import OpenItem, Settlement
from tests.test_v1_contracts import Fixture

ACCOUNTS = '/modules/cash/funding-account-list/'
ACCOUNT_FORM = '/modules/cash/funding-account-form/'
ACCOUNT_SAVE = '/modules/cash/funding-account-save/'
ACCOUNT_ACTIVATE = '/modules/cash/funding-account-activate/'
TX_LIST = '/modules/cash/cash-transaction-list/'
TX_FORM = '/modules/cash/cash-transaction-form/'
TX_SAVE = '/modules/cash/cash-transaction-save/'
TX_DETAIL = '/modules/cash/cash-transaction-detail/'
MATCH = '/modules/cash/cash-party-match/'


class CashHTTPFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.finance = self.member('finance')
        self.auditor = self.member('auditor')
        self.employee_member = self.member('member')

    def account(self, *, activate=True, name='基本户'):
        row = funding.save_funding_account(
            self.owner, name=name, masked_identifier='****1234',
            request_token=uuid.uuid4(), dry_run=False)
        if activate:
            row = funding.activate_funding_account(
                self.owner, row.pk, expected_version=row.version,
                request_token=uuid.uuid4(), dry_run=False)
        return row

    def transaction(self, account=None, **changes):
        account = account or self.account()
        values = {'funding_account_id': account.pk, 'transaction_date': self.today,
                  'direction': 'in', 'gross_cents': 12345, 'external_source_key': 'BANK-001',
                  'payee': {'kind': 'unidentified'}, 'evidence': [{'reference': '银行回单 R-001'}],
                  'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return tx.save_cash_transaction(self.owner, **values)

    def login(self, member):
        self.client.force_login(member.user)

    def account_data(self, **changes):
        data = {'name': '基本户', 'masked_identifier': '****1234',
                'account_id': '', 'expected_version': '', 'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def tx_data(self, account, **changes):
        data = {'funding_account': str(account.pk),
                'transaction_date': self.today.isoformat(), 'direction': 'in',
                'amount': '123.45', 'external_source_key': 'BANK-001',
                'payee_kind': 'unidentified', 'counterparty': '', 'own_account': '',
                'evidence': '银行回单 R-001', 'transaction_id': '', 'expected_version': '',
                'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def match_data(self, **changes):
        data = {'payee_kind': 'counterparty', 'counterparty': '', 'employee': '',
                'own_account': '', 'original_match_ref': '', 'reason': '',
                'evidence': '匹配证据 M-001', 'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def side_effect_counts(self):
        return tuple(model.objects.count() for model in (
            AccountingEvent, LedgerEntry, LedgerPosting, Settlement, OpenItem, SourceConsumption))


class CashHTTPTests(CashHTTPFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_full_http_chain_account_activate_transaction_identify_reverse(self):
        before = self.side_effect_counts()
        self.login(self.owner)
        response = self.client.get(ACCOUNTS)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '银行账户')
        self.assertContains(response, '银行流水')
        self.assertEqual(self.client.get(ACCOUNT_FORM).status_code, 200)
        response = self.client.post(ACCOUNT_SAVE, self.account_data())
        self.assertEqual(response.status_code, 302)
        account = FundingAccount.objects.get()
        self.assertEqual(account.status, 'draft')
        self.assertEqual(account.opening_cents, 0)
        self.assertEqual(account.opening_on, self.today)
        token = uuid.uuid4()
        response = self.client.post(f'{ACCOUNT_ACTIVATE}?id={account.pk}',
                                    {'expected_version': account.version,
                                     'request_token': str(token)})
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertEqual(account.status, 'active')
        page = self.client.get(TX_FORM)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '银行流水来源编号')
        response = self.client.post(TX_SAVE, self.tx_data(account))
        self.assertEqual(response.status_code, 302)
        row = CashTransaction.objects.get()
        self.assertEqual((row.direction, row.gross_cents, row.status),
                         ('in', 12345, 'draft'))
        self.assertEqual(row.payee.get('kind'), 'unidentified')
        self.assertTrue(row.evidence and row.evidence[0]['sha256'])
        detail = self.client.get(TX_DETAIL, {'id': row.pk})
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, '123.45')
        self.assertContains(detail, '未识别')
        self.assertContains(detail, '草稿')
        self.assertNotContains(detail, '&#x27;kind&#x27;')
        party = create_party(self.owner, display_name='现金往来客户', roles=['customer'],
                             active_from=self.today, request_token=uuid.uuid4())
        data = self.match_data(counterparty=str(party.pk))
        response = self.client.post(f'{MATCH}?id={row.pk}&action=identify', data)
        self.assertEqual(response.status_code, 302)
        identify = CashPartyMatch.objects.get(action='identify')
        self.assertEqual(identify.identified_payee, {'kind': 'counterparty', 'id': str(party.pk)})
        detail = self.client.get(TX_DETAIL, {'id': row.pk})
        self.assertContains(detail, '往来方')
        data = self.match_data(action='reverse', original_match_ref=str(identify.pk),
                               reason='原匹配对象登记错误', evidence='反向证据 R-001')
        response = self.client.post(f'{MATCH}?id={row.pk}&action=reverse', data)
        self.assertEqual(response.status_code, 302)
        reverse = CashPartyMatch.objects.get(action='reverse')
        self.assertEqual(reverse.original_match_id, identify.pk)
        detail = self.client.get(TX_DETAIL, {'id': row.pk})
        self.assertContains(detail, '反向匹配')
        self.assertEqual(before, self.side_effect_counts())

    def test_duplicate_external_key_idempotency_and_changed_input_rejected(self):
        account = self.account()
        self.login(self.owner)
        data = self.tx_data(account)
        response = self.client.post(TX_SAVE, data)
        self.assertEqual(response.status_code, 302)
        row = CashTransaction.objects.get()
        self.assertEqual(self.client.post(TX_SAVE, data).status_code, 302)
        self.assertEqual(CashTransaction.objects.count(), 1)
        same_token = dict(data, amount='99.99')
        response = self.client.post(TX_SAVE, same_token)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '请求标识', status_code=400)
        changed = dict(data, amount='99.99', request_token=str(uuid.uuid4()))
        response = self.client.post(TX_SAVE, changed)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '外部流水编号', status_code=400)
        self.assertEqual(CashTransaction.objects.count(), 1)
        # Same account/key different request token with same content replays the original row.
        replay = dict(data, request_token=str(uuid.uuid4()))
        self.assertEqual(self.client.post(TX_SAVE, replay).status_code, 302)
        self.assertEqual(CashTransaction.objects.count(), 1)

    def test_business_errors_future_locked_evidence_payee_and_no_half_rows(self):
        account = self.account()
        before = self.side_effect_counts()
        self.login(self.owner)
        response = self.client.post(TX_SAVE, self.tx_data(
            account, transaction_date=(self.today + timedelta(days=1)).isoformat()))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '不能在未来', status_code=400)
        Organization.objects.filter(pk=self.org.pk).update(locked_through=self.today)
        response = self.client.post(TX_SAVE, self.tx_data(account))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '锁账', status_code=400)
        Organization.objects.filter(pk=self.org.pk).update(locked_through=None)
        response = self.client.post(TX_SAVE, self.tx_data(account, evidence=''))
        self.assertEqual(response.status_code, 400)
        response = self.client.post(TX_SAVE, self.tx_data(account, gross_cents='1'))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CashTransaction.objects.exists())
        response = self.client.post(TX_SAVE, self.tx_data(
            account, funding_account=str(uuid.uuid4())))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CashTransaction.objects.exists())
        self.assertEqual(before, self.side_effect_counts())
        self.assertEqual(CashReceipt.objects.count(), 2)  # account save + activation receipts only
        # Invalid counterparty id on a real payee is rejected without a cash fact.
        data = self.tx_data(account, payee_kind='counterparty',
                            counterparty=str(uuid.uuid4()), external_source_key='BANK-002')
        response = self.client.post(TX_SAVE, data)
        self.assertIn(response.status_code, (400, 403))
        self.assertEqual(CashTransaction.objects.count(), 0)

    def test_roles_read_only_owner_activation_csrf_and_disabled_module(self):
        account = self.account(activate=False)
        self.login(self.finance)
        response = self.client.post(f'{ACCOUNT_ACTIVATE}?id={account.pk}',
                                    {'expected_version': account.version,
                                     'request_token': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 403)
        account.refresh_from_db()
        self.assertEqual(account.status, 'draft')
        self.login(self.owner)
        response = self.client.post(f'{ACCOUNT_ACTIVATE}?id={account.pk}',
                                    {'expected_version': account.version,
                                     'request_token': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        account.refresh_from_db()
        self.assertEqual(account.status, 'active')
        self.login(self.auditor)
        self.assertEqual(self.client.get(ACCOUNTS).status_code, 200)
        self.assertEqual(self.client.get(TX_LIST).status_code, 200)
        self.assertEqual(self.client.post(ACCOUNT_SAVE, self.account_data()).status_code, 403)
        self.login(self.pm)
        self.assertEqual(self.client.get(ACCOUNTS).status_code, 403)
        self.login(self.employee_member)
        self.assertEqual(self.client.get(ACCOUNTS).status_code, 403)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner.user)
        self.assertEqual(protected.post(ACCOUNT_SAVE, self.account_data()).status_code, 403)
        self.login(self.finance)
        row = self.transaction(account=account)
        self.assertEqual(self.client.get(TX_DETAIL, {'id': row.pk}).status_code, 200)
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'cash', False)
        self.login(self.auditor)
        self.assertEqual(self.client.get(ACCOUNTS).status_code, 403)
        self.assertEqual(len(funding.funding_account_list(self.owner)), 1)
        self.assertEqual(len(tx.cash_transaction_list(self.owner)), 1)

    def test_template_states_and_payee_display_do_not_leak_raw_json(self):
        account = self.account()
        self.login(self.owner)
        page = self.client.get(TX_FORM)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'id_payee_kind')
        self.assertContains(page, 'id_evidence')
        self.assertContains(page, 'name="amount"')
        self.assertNotContains(page, 'name="status"')
        row = self.transaction(account=account,
                               payee={'kind': 'own_account', 'id': str(account.pk)})
        detail = self.client.get(TX_DETAIL, {'id': row.pk})
        self.assertContains(detail, '自有账户')
        self.assertNotContains(detail, '&#x27;kind&#x27;')
        list_page = self.client.get(TX_LIST)
        self.assertContains(list_page, 'BANK-001')
        self.assertContains(list_page, str(self.today))
        self.assertContains(list_page, '收入')
        accounts_page = self.client.get(ACCOUNTS)
        self.assertContains(accounts_page, str(self.today))
        self.assertContains(accounts_page, '0.00')

    def test_match_semantics_inactive_objects_duplicate_identify_and_reidentify(self):
        account = self.account()
        row = self.transaction(account=account)
        self.login(self.owner)
        party = create_party(self.owner, display_name='匹配往来客户', roles=['customer'],
                             active_from=self.today, request_token=uuid.uuid4())
        # Inactive counterparty cannot be a bank payee or an identified party.
        Counterparty.objects.filter(pk=party.pk).update(status='inactive')
        response = self.client.post(TX_SAVE, self.tx_data(
            account, payee_kind='counterparty', counterparty=str(party.pk),
            external_source_key='BANK-INACTIVE-PARTY'))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '停用', status_code=400)
        self.assertEqual(CashTransaction.objects.filter(external_source_key='BANK-INACTIVE-PARTY').count(), 0)
        Counterparty.objects.filter(pk=party.pk).update(status='active')
        # A draft funding account cannot be used as the own-account payee.
        draft_account = funding.save_funding_account(
            self.owner, name='草稿账户', masked_identifier='****0002',
            request_token=uuid.uuid4(), dry_run=False)
        response = self.client.post(TX_SAVE, self.tx_data(
            account, payee_kind='own_account', own_account=str(draft_account.pk),
            external_source_key='BANK-DRAFT-ACCOUNT'))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CashTransaction.objects.filter(
            external_source_key='BANK-DRAFT-ACCOUNT').exists())
        # An inactive employee is not selectable/identifiable.
        inactive = Employee.objects.create(organization=self.org, name='停用员工', active=False)
        response = self.client.post(f'{MATCH}?id={row.pk}&action=identify', self.match_data(
            payee_kind='employee', employee=str(inactive.pk), counterparty=''))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CashPartyMatch.objects.exists())
        # Identify succeeds, a second unreversed identify is refused with the service message.
        response = self.client.post(f'{MATCH}?id={row.pk}&action=identify', self.match_data(
            counterparty=str(party.pk)))
        self.assertEqual(response.status_code, 302)
        first = CashPartyMatch.objects.get(action='identify')
        response = self.client.post(f'{MATCH}?id={row.pk}&action=identify', self.match_data(
            counterparty=str(party.pk)))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '未被反向', status_code=400)
        self.assertEqual(CashPartyMatch.objects.filter(action='identify').count(), 1)
        # Reverse the effective identify; a second reverse of the same match is refused.
        response = self.client.post(f'{MATCH}?id={row.pk}&action=reverse', self.match_data(
            action='reverse', original_match_ref=str(first.pk),
            reason='原识别有误', evidence='反向证据 R-002'))
        self.assertEqual(response.status_code, 302)
        response = self.client.post(f'{MATCH}?id={row.pk}&action=reverse', self.match_data(
            action='reverse', original_match_ref=str(first.pk),
            reason='重复反向', evidence='反向证据 R-003'))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '已被反向', status_code=400)
        # After an explicit reverse the same fact can be identified again.
        response = self.client.post(f'{MATCH}?id={row.pk}&action=identify', self.match_data(
            payee_kind='own_account', own_account=str(account.pk), counterparty=''))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CashPartyMatch.objects.filter(action='identify').count(), 2)
