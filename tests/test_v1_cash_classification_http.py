"""PH-239 HTTP boundaries for cash classification and append-only reversal."""
from decimal import Decimal
import uuid

from django.test import Client, TestCase

from core.models import (AccountingEvent, LedgerEntry, LedgerPosting, Organization, PluginState,
                         SourceConsumption)
from pmo_plugins.cash import classification_services as cls
from pmo_plugins.cash import transaction_services as tx
from pmo_plugins.cash.models import (CashClassification, CashClassificationReversal, CashReceipt,
                                     CashTransaction)
from tests.test_v1_cash_http import CashHTTPFixture

CLASSIFICATION_LIST = '/modules/cash/cash-classification-list/'
CLASSIFICATION_FORM = '/modules/cash/cash-classification-form/'
CLASSIFICATION_SAVE = '/modules/cash/cash-classification-save/'
CLASSIFICATION_REVERSE = '/modules/cash/cash-classification-reverse/'
CLASSIFICATION_REVERSE_SAVE = '/modules/cash/cash-classification-reverse-save/'
TX_DETAIL = '/modules/cash/cash-transaction-detail/'


class ClassificationHTTPFixture(CashHTTPFixture):
    def posted(self, account=None, **changes):
        row = self.transaction(account, **changes)
        return tx.record_cash_transaction(
            self.owner, row.pk, expected_version=row.version,
            request_token=uuid.uuid4(), dry_run=False)

    def classify_data(self, row, **changes):
        data = {'transaction_id': str(row.pk), 'classification': 'receipt',
                'evidence': '分类依据 B-001 · 银行回单', 'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def reverse_data(self, classification, **changes):
        data = {'classification_id': str(classification.pk),
                'expected_version': str(classification.version),
                'reason': '分类归属录错，撤分类重做',
                'evidence': '撤分类依据 R-001 · 复核记录',
                'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def side_effect_counts(self):
        return tuple(model.objects.count() for model in (
            AccountingEvent, LedgerEntry, LedgerPosting, SourceConsumption, CashReceipt,
            CashClassificationReversal))


class ClassificationHTTPTests(ClassificationHTTPFixture, TestCase):
    def test_classify_then_reverse_flow_and_read_page(self):
        self.login(self.owner)
        row = self.posted()
        before = self.side_effect_counts()
        detail = self.client.get(TX_DETAIL, {'id': row.pk})
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, '现金分类')
        self.assertContains(detail, '登记现金分类')
        form_page = self.client.get(CLASSIFICATION_FORM, {'id': row.pk})
        self.assertEqual(form_page.status_code, 200)
        self.assertContains(form_page, '分类依据')
        response = self.client.post(CLASSIFICATION_SAVE, self.classify_data(row))
        self.assertEqual(response.status_code, 302)
        classification = CashClassification.objects.get()
        self.assertEqual((classification.classification, classification.status),
                         ('receipt', 'active'))
        self.assertEqual(CashReceipt.objects.filter(
            action='cash_classification.record').count(), 1)
        listing = self.client.get(CLASSIFICATION_LIST)
        self.assertEqual(listing.status_code, 200)
        self.assertContains(listing, '收款')
        self.assertContains(listing, '现金账净额')
        detail = self.client.get(TX_DETAIL, {'id': row.pk})
        self.assertContains(detail, '有效')
        self.assertContains(detail, '撤分类')
        reverse_page = self.client.get(CLASSIFICATION_REVERSE, {'id': classification.pk})
        self.assertEqual(reverse_page.status_code, 200)
        self.assertContains(reverse_page, '撤分类原因')
        reversed_response = self.client.post(CLASSIFICATION_REVERSE_SAVE,
                                             self.reverse_data(classification))
        self.assertEqual(reversed_response.status_code, 302)
        classification.refresh_from_db()
        self.assertEqual(classification.status, 'reversed')
        self.assertEqual(CashClassificationReversal.objects.count(), 1)
        repeated = self.client.post(CLASSIFICATION_REVERSE_SAVE,
                                    self.reverse_data(classification))
        self.assertEqual(repeated.status_code, 400)
        self.assertContains(repeated, '撤分类', status_code=400)
        listing = self.client.get(CLASSIFICATION_LIST)
        self.assertContains(listing, '已撤分类')
        after = self.side_effect_counts()
        # PH-239 bridge: one AccountingEvent/LedgerPosting per effective classification action
        # and one permanent bank_effect SourceConsumption for the whole bank fact.
        self.assertEqual(after[0], before[0] + 2)
        self.assertEqual(after[1], before[1])
        self.assertEqual(after[2], before[2] + 2)
        self.assertEqual(after[3], before[3] + 1)

    def test_roles_csrf_validation_and_module_disable(self):
        self.login(self.owner)
        account = self.account()
        row = self.posted(account)
        classification = cls.record_classification(
            self.owner, cash_transaction_id=row.pk, classification='receipt',
            evidence=[{'reference': '分类依据'}], request_token=uuid.uuid4(), dry_run=False)
        for actor in (self.pm, self.employee_member):
            self.login(actor)
            self.assertEqual(self.client.get(CLASSIFICATION_LIST).status_code, 403)
            self.assertEqual(self.client.get(CLASSIFICATION_FORM,
                                             {'id': row.pk}).status_code, 403)
        self.login(self.auditor)
        self.assertEqual(self.client.get(CLASSIFICATION_LIST).status_code, 200)
        self.assertEqual(self.client.get(CLASSIFICATION_FORM,
                                         {'id': row.pk}).status_code, 403)
        self.login(self.finance)
        self.assertEqual(self.client.get(CLASSIFICATION_LIST).status_code, 200)
        self.login(self.owner)
        tax_row = self.posted(account, direction='out', gross_cents=1000,
                              external_source_key='OUT-1')
        invalid = self.client.post(CLASSIFICATION_SAVE,
                                   self.classify_data(tax_row, classification='receipt'))
        self.assertEqual(invalid.status_code, 400)
        self.assertContains(invalid, '方向', status_code=400)
        empty = self.client.post(CLASSIFICATION_SAVE,
                                 self.classify_data(tax_row, classification=''))
        self.assertEqual(empty.status_code, 400)
        duplicate = self.client.get(CLASSIFICATION_FORM, {'id': row.pk})
        self.assertEqual(duplicate.status_code, 403)
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner.user)
        csrf = protected.post(CLASSIFICATION_SAVE, self.classify_data(tax_row,
                                                                     classification='payment'))
        self.assertEqual(csrf.status_code, 403)
        PluginState.objects.update_or_create(organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.assertEqual(self.client.get(CLASSIFICATION_LIST).status_code, 403)
        self.assertEqual(self.client.post(CLASSIFICATION_SAVE,
                                          self.classify_data(tax_row,
                                                             classification='payment')).status_code,
                         403)
        self.assertEqual(cls.classification_snapshot(self.auditor, classification.pk)['status'],
                         'active')
