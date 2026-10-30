"""PH-241 HTTP browser boundaries for advance offset creation and read-only pages."""
import uuid

from django.test import Client, TestCase

from core.models import (AccountingEvent, DomainEvent, LedgerEntry, LedgerPosting,
                         PluginState, SourceConsumption)
from pmo_plugins.cash import classification_services as classification
from pmo_plugins.settlement.models import Offset
from tests.test_v1_clearance_http import ClearanceHTTPFixture

OFFSET_LIST = '/modules/settlement/offset-list/'
OFFSET_SAVE = '/modules/settlement/offset-save/'


class OffsetHTTPFixture(ClearanceHTTPFixture):
    def setUp(self):
        super().setUp()
        # The fixture's posted incoming cash is the customer advance for this slice.
        classification.record_classification(
            self.owner, cash_transaction_id=self.cash.pk, classification='advance',
            evidence=[{'reference': '预收依据'}], request_token=uuid.uuid4(), dry_run=False)
        self.cash.refresh_from_db()

    def advance_ref(self):
        return f"{self.cash.pk}:{self.cash.version}"

    def save_data(self, **changes):
        data = {'open_item': str(self.open_item.pk), 'advance_ref': self.advance_ref(),
                'amount': '200.00', 'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def save(self, member=None, **changes):
        self.login(member or self.owner)
        return self.client.post(OFFSET_SAVE, self.save_data(**changes))

    def side_effect_counts(self):
        return tuple(model.objects.count() for model in (
            AccountingEvent, LedgerEntry, LedgerPosting, SourceConsumption))


class OffsetHTTPTests(OffsetHTTPFixture, TestCase):
    def test_read_page_and_selected_item_balances(self):
        self.login(self.owner)
        response = self.client.get(OFFSET_LIST)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '预收抵扣')
        response = self.client.get(OFFSET_LIST, {'open_item': str(self.open_item.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '未清项余额')

    def test_owner_creates_offset_without_ledger_or_income_side_effects(self):
        before = self.side_effect_counts()
        response = self.save()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Offset.objects.count(), 1)
        self.assertEqual(before, self.side_effect_counts())
        self.assertEqual(Offset.objects.get().offset_cents, 20000)

    def test_invalid_amount_returns_form_error(self):
        response = self.save(amount='0.00')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Offset.objects.exists())
        response = self.save(amount='600.00')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Offset.objects.exists())

    def test_unknown_field_rejected(self):
        response = self.save(available_cents='1')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Offset.objects.exists())

    def test_pm_and_member_write_denied_but_pm_can_read(self):
        self.login(self.pm)
        self.assertEqual(self.client.get(OFFSET_LIST).status_code, 200)
        self.assertEqual(self.client.post(OFFSET_SAVE, self.save_data()).status_code, 403)
        self.login(self.employee_member)
        self.assertEqual(self.client.get(OFFSET_LIST).status_code, 403)
        self.assertEqual(self.client.post(OFFSET_SAVE, self.save_data()).status_code, 403)

    def test_module_disabled_write_denied_history_still_readable(self):
        self.save()
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='settlement',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.login(self.owner)
        self.assertEqual(self.client.get(OFFSET_LIST).status_code, 200)
        self.assertEqual(self.client.post(OFFSET_SAVE, self.save_data()).status_code, 403)
        PluginState.objects.filter(organization=self.org, plugin_id='settlement').delete()
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.assertEqual(self.client.post(OFFSET_SAVE, self.save_data()).status_code, 403)
