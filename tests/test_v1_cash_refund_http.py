"""PH-240 HTTP boundaries for refund and bank return."""
import uuid
from datetime import timedelta

from django.test import Client, TestCase

from core.models import (AccountingEvent, LedgerEntry, LedgerPosting, Organization, PluginState,
                         SourceConsumption)
from pmo_plugins.cash import refund_services as refunds
from pmo_plugins.cash import transaction_services as tx
from pmo_plugins.cash.models import BankReturn, CashReceipt, CashTransaction, Refund
from tests.test_v1_cash_http import CashHTTPFixture

REFUND_LIST = '/modules/cash/cash-refund-list/'
REFUND_FORM = '/modules/cash/cash-refund-form/'
REFUND_SAVE = '/modules/cash/cash-refund-save/'
RETURN_FORM = '/modules/cash/cash-bank-return-form/'
RETURN_SAVE = '/modules/cash/cash-bank-return-save/'
TX_DETAIL = '/modules/cash/cash-transaction-detail/'


class RefundHTTPFixture(CashHTTPFixture):
    def posted(self, account=None, **changes):
        values = {'direction': 'in', 'gross_cents': 53000}
        values.update(changes)
        row = self.transaction(account, **values)
        return tx.record_cash_transaction(
            self.owner, row.pk, expected_version=row.version,
            request_token=uuid.uuid4(), dry_run=False)

    def refund_data(self, row, **changes):
        data = {'original_cash_transaction_id': str(row.pk),
                'refund_date': self.today.isoformat(), 'amount': '200.00',
                'reason': '客户取消订单退款', 'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def return_data(self, row, **changes):
        data = {'original_cash_transaction_id': str(row.pk),
                'return_date': self.today.isoformat(), 'amount': '100.00',
                'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def side_effect_counts(self):
        return tuple(model.objects.count() for model in (
            AccountingEvent, LedgerEntry, LedgerPosting, SourceConsumption, CashReceipt))


class RefundHTTPTests(RefundHTTPFixture, TestCase):
    def test_refund_then_bank_return_flow_and_pages(self):
        self.login(self.owner)
        row = self.posted()
        before = self.side_effect_counts()
        detail = self.client.get(TX_DETAIL, {'id': row.pk})
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, '退款与银行退回')
        self.assertContains(detail, '530.00')
        self.assertContains(detail, '登记退款')
        form_page = self.client.get(REFUND_FORM, {'id': row.pk})
        self.assertEqual(form_page.status_code, 200)
        self.assertContains(form_page, '可退额')
        response = self.client.post(REFUND_SAVE, self.refund_data(row))
        self.assertEqual(response.status_code, 302)
        refund = Refund.objects.get()
        self.assertEqual((refund.gross_cents, refund.refund_cash_transaction.direction),
                         (20000, 'out'))
        self.assertEqual(self.client.get(REFUND_LIST).status_code, 200)
        listing = self.client.get(REFUND_LIST)
        self.assertContains(listing, '200.00')
        self.assertContains(listing, '退款记录')
        produced = refund.refund_cash_transaction
        produced_detail = self.client.get(TX_DETAIL, {'id': produced.pk})
        self.assertContains(produced_detail, '本现金由退款生成')
        return_page = self.client.get(RETURN_FORM, {'id': produced.pk})
        self.assertEqual(return_page.status_code, 200)
        response = self.client.post(RETURN_SAVE, self.return_data(produced))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(BankReturn.objects.count(), 1)
        self.assertEqual(BankReturn.objects.get().return_cash_transaction.direction, 'in')
        self.assertEqual(before[0:4], self.side_effect_counts()[0:4])

    def test_roles_csrf_validation_and_module_disable(self):
        self.login(self.owner)
        row = self.posted()
        for actor in (self.pm, self.employee_member):
            self.login(actor)
            self.assertEqual(self.client.get(REFUND_LIST).status_code, 403)
            self.assertEqual(self.client.get(REFUND_FORM, {'id': row.pk}).status_code, 403)
        self.login(self.auditor)
        self.assertEqual(self.client.get(REFUND_LIST).status_code, 200)
        self.assertEqual(self.client.get(REFUND_FORM, {'id': row.pk}).status_code, 403)
        self.login(self.owner)
        over = self.client.post(REFUND_SAVE,
                                self.refund_data(row, amount='600.00'))
        self.assertEqual(over.status_code, 400)
        self.assertContains(over, '超过', status_code=400)
        empty_reason = self.client.post(REFUND_SAVE, self.refund_data(row, reason=''))
        self.assertEqual(empty_reason.status_code, 400)
        future = self.client.post(REFUND_SAVE, self.refund_data(
            row, refund_date=(self.today + timedelta(days=1)).isoformat()))
        self.assertEqual(future.status_code, 400)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner.user)
        self.assertEqual(protected.post(REFUND_SAVE, self.refund_data(row)).status_code, 403)
        PluginState.objects.update_or_create(organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.assertEqual(self.client.get(REFUND_LIST).status_code, 403)
        self.assertEqual(self.client.post(REFUND_SAVE, self.refund_data(row)).status_code, 403)
        self.assertEqual(refunds.cash_refund_coverage(
            self.auditor, row.pk)['posted_gross_cents'], 53000)
