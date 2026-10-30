"""PH-232 HTTP browser boundaries for posted-cash clearance and reversal."""
import uuid

from django.test import Client, TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, DomainEvent, LedgerEntry,
                         LedgerPosting, Organization, SourceConsumption)
from pmo_plugins.cash import funding_services as funding
from pmo_plugins.cash import transaction_services as cash_tx
from pmo_plugins.contracts.party_services import create_party
from pmo_plugins.settlement import clearance_services
from pmo_plugins.settlement.models import Clearance, ClearanceReceipt, OpenItem, Settlement
from tests.test_v1_settlement_http import SettlementHTTPFixture

CLEARANCE_LIST = '/modules/settlement/clearance-list/'
CLEARANCE_CREATE = '/modules/settlement/clearance-create/'
CLEARANCE_REVERSE = '/modules/settlement/clearance-reverse/'


class ClearanceHTTPFixture(SettlementHTTPFixture):
    def setUp(self):
        super().setUp()
        self.client = Client()
        self.settlement = self.create_settlement(('half',))
        self.open_item = self.settlement.items.get()
        self.cash = self.make_cash(amount=53000, counterparty=self.party,
                                   key='CLEAR-CASH-1')

    def make_cash(self, *, amount=53000, counterparty=None, posted=True, key='CLEAR-CASH-1',
                  direction='in', on=None):
        masked = '尾号' + '9001'
        account = funding.save_funding_account(
            self.owner, name='核销账户-' + key, masked_identifier=masked,
            request_token=uuid.uuid4(), dry_run=False)
        account = funding.activate_funding_account(
            self.owner, account.pk, expected_version=account.version,
            request_token=uuid.uuid4(), dry_run=False)
        payee = ({'kind': 'counterparty', 'id': str(counterparty.pk)}
                 if counterparty is not None else {'kind': 'unidentified'})
        row = cash_tx.save_cash_transaction(
            self.owner, funding_account_id=account.pk,
            transaction_date=on or self.today, direction=direction, gross_cents=amount,
            external_source_key=key, payee=payee,
            evidence=[{'reference': '银行回单 ' + key}],
            request_token=uuid.uuid4(), dry_run=False)
        if posted:
            row = cash_tx.record_cash_transaction(
                self.owner, row.pk, request_token=uuid.uuid4(),
                expected_version=row.version, dry_run=False)
        return row

    def cash_ref(self, cash):
        return f"{cash.pk}:{cash.version}"

    def create_data(self, cash=None, amount='300.00', **changes):
        data = {'open_item': str(self.open_item.pk),
                'cash_ref': self.cash_ref(cash or self.cash),
                'amount': amount, 'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def create(self, member=None, **changes):
        self.login(member or self.owner)
        return self.client.post(CLEARANCE_CREATE, self.create_data(**changes))

    def reverse_data(self, clearance, **changes):
        data = {'clearance_id': str(clearance.pk), 'expected_version': clearance.version,
                'reason': '原核销登记有误', 'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def reverse(self, member, clearance, **changes):
        self.login(member)
        return self.client.post(f'{CLEARANCE_REVERSE}?id={clearance.pk}',
                                self.reverse_data(clearance, **changes))

    def side_effect_counts(self):
        return tuple(model.objects.count() for model in (
            AccountingEvent, LedgerEntry, LedgerPosting, SourceConsumption))


class ClearanceHTTPTests(ClearanceHTTPFixture, TestCase):
    def test_partial_clearance_settle_reverse_and_balances(self):
        before = self.side_effect_counts()
        self.login(self.owner)
        page = self.client.get(CLEARANCE_LIST, {'open_item': str(self.open_item.pk)})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '未清项余额')
        self.assertContains(page, '530.00')
        self.assertContains(page, '登记核销')
        detail = self.client.get(f'/modules/settlement/settlement-detail/?id={self.settlement.pk}')
        self.assertContains(detail, '已核销')
        self.assertContains(detail, '剩余')
        self.assertContains(detail, 'clearance-list/')
        aging = self.client.get('/modules/settlement/aging-list/')
        self.assertContains(aging, '剩余')
        self.assertContains(aging, 'clearance-list/')
        response = self.create()
        self.assertEqual(response.status_code, 302)
        first = Clearance.objects.get()
        self.assertEqual((first.cleared_cents, first.status), (30000, 'active'))
        self.assertEqual(DomainEvent.objects.filter(topic='settlement.cleared.v1').count(), 1)
        balances = clearance_services.open_item_balances(self.owner, self.open_item.pk)
        self.assertEqual((balances['cleared_cents'], balances['remaining_cents']),
                         (30000, 23000))
        detail = self.client.get(f'/modules/settlement/settlement-detail/?id={self.settlement.pk}')
        self.assertContains(detail, '300.00')
        self.assertContains(detail, '230.00')
        aging = self.client.get('/modules/settlement/aging-list/')
        self.assertContains(aging, '300.00')
        self.assertContains(aging, '230.00')
        response = self.create(amount='230.00')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Clearance.objects.filter(status='active').count(), 2)
        balances = clearance_services.open_item_balances(self.owner, self.open_item.pk)
        self.assertEqual(balances['remaining_cents'], 0)
        self.assertEqual(self.reverse(self.owner, first).status_code, 302)
        first.refresh_from_db()
        self.assertEqual(first.status, 'reversed')
        self.assertEqual(DomainEvent.objects.filter(
            topic='settlement.clearance_reversed.v1').count(), 1)
        balances = clearance_services.open_item_balances(self.owner, self.open_item.pk)
        self.assertEqual((balances['cleared_cents'], balances['remaining_cents']),
                         (23000, 30000))
        repeated = self.reverse(self.owner, first)
        self.assertEqual(repeated.status_code, 400)
        self.assertEqual(DomainEvent.objects.filter(
            topic='settlement.clearance_reversed.v1').count(), 1)
        token = uuid.uuid4()
        data = self.create_data(amount='100.00', request_token=str(token))
        self.assertEqual(self.client.post(CLEARANCE_CREATE, data).status_code, 302)
        self.assertEqual(self.client.post(CLEARANCE_CREATE, data).status_code, 302)
        self.assertEqual(Clearance.objects.count(), 3)
        changed = dict(data, amount='200.00')
        conflict = self.client.post(CLEARANCE_CREATE, changed)
        self.assertEqual(conflict.status_code, 400)
        self.assertContains(conflict, '请求标识', status_code=400)
        self.assertEqual(Clearance.objects.count(), 3)
        self.assertEqual(before, self.side_effect_counts())
        self.assertEqual(AccountingEvent.objects.count(), 0)
        self.assertEqual(Settlement.objects.count(), 1)
        self.assertEqual(OpenItem.objects.count(), 1)

    def test_errors_roles_csrf_and_disabled_settlement(self):
        before = self.side_effect_counts()
        self.assertEqual(self.create(amount='300.00').status_code, 302)
        over = self.create(amount='300.00')
        self.assertEqual(over.status_code, 400)
        self.assertContains(over, '超过', status_code=400)
        self.assertEqual(Clearance.objects.count(), 1)
        other_party = create_party(self.owner, display_name='其他往来客户', roles=['customer'],
                                   active_from=self.today, request_token=uuid.uuid4())
        other_cash = self.make_cash(counterparty=other_party, key='CLEAR-CROSS-1')
        cross = self.create(cash=other_cash, amount='10.00')
        self.assertEqual(cross.status_code, 400)
        self.assertContains(cross, '选项', status_code=400)
        self.assertEqual(Clearance.objects.count(), 1)
        draft_cash = self.make_cash(posted=False, key='CLEAR-DRAFT-1')
        draft = self.client.post(CLEARANCE_CREATE, self.create_data(cash=draft_cash, amount='10.00'))
        self.assertEqual(draft.status_code, 400)
        self.assertEqual(Clearance.objects.count(), 1)
        Organization.objects.filter(pk=self.org.pk).update(locked_through=self.today)
        locked = self.create(amount='10.00')
        self.assertEqual(locked.status_code, 400)
        self.assertContains(locked, '锁账', status_code=400)
        Organization.objects.filter(pk=self.org.pk).update(locked_through=None)
        period = AccountingPeriod.objects.create(
            organization=self.org, period=self.today.strftime('%Y-%m'),
            status='posted', closed_through=self.today)
        closed = self.create(amount='10.00')
        self.assertEqual(closed.status_code, 400)
        self.assertContains(closed, '会计期间', status_code=400)
        period.delete()
        self.login(self.auditor)
        self.assertEqual(self.client.get(CLEARANCE_LIST).status_code, 200)
        self.assertEqual(self.create(member=self.auditor, amount='10.00').status_code, 403)
        clearance = Clearance.objects.get()
        self.assertEqual(self.reverse(self.auditor, clearance).status_code, 403)
        self.login(self.pm)
        self.assertEqual(self.client.get(CLEARANCE_LIST).status_code, 200)
        self.assertEqual(self.create(member=self.pm, amount='10.00').status_code, 403)
        self.login(self.employee_member)
        self.assertEqual(self.client.get(CLEARANCE_LIST).status_code, 403)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner.user)
        self.assertEqual(protected.post(CLEARANCE_CREATE,
                                       self.create_data(amount='10.00')).status_code, 403)
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'settlement', False)
        self.login(self.auditor)
        self.assertEqual(self.client.get(CLEARANCE_LIST).status_code, 403)
        self.assertEqual(len(clearance_services.clearance_list(self.owner)), 1)
        self.assertEqual(self.side_effect_counts(), before)

    def test_pm_own_project_read_page_with_selected_open_item(self):
        # Regression for the browser defect: selecting an open item used to render cash
        # candidates for PM and leak a 403 for the whole page (cash stays owner/finance/auditor).
        self.assertEqual(self.create(amount='300.00').status_code, 302)
        self.login(self.pm)
        page = self.client.get(CLEARANCE_LIST, {'open_item': str(self.open_item.pk)})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '未清项余额')
        self.assertContains(page, '300.00')
        self.assertContains(page, '230.00')
        self.assertContains(page, '只读账户')
        self.assertNotContains(page, '登记核销')
        self.assertEqual(self.create(member=self.pm, amount='10.00').status_code, 403)
