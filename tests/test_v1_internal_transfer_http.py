"""PH-245 HTTP browser boundaries for internal transfer link/reverse and read-only pages."""
import uuid

from django.test import Client, TestCase

from core.models import AccountingEvent, LedgerEntry, LedgerPosting, PluginState, SourceConsumption
from pmo_plugins.cash.models import InternalTransfer
from tests.test_v1_internal_transfer import TransferFixture

TRANSFER_LIST = '/modules/cash/cash-internal-transfer-list/'
TRANSFER_FORM = '/modules/cash/cash-internal-transfer-form/'
TRANSFER_SAVE = '/modules/cash/cash-internal-transfer-save/'
TRANSFER_REVERSE = '/modules/cash/cash-internal-transfer-reverse/'
TRANSFER_REVERSE_SAVE = '/modules/cash/cash-internal-transfer-reverse-save/'


class TransferHTTPFixture(TransferFixture):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def login(self, member):
        self.client.force_login(member.user)

    def save_data(self, **changes):
        data = {'out_cash': f"{self.out_cash.pk}:{self.out_cash.version}",
                'in_cash': f"{self.in_cash.pk}:{self.in_cash.version}",
                'evidence': '内部互转依据', 'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def reverse_data(self, transfer, **changes):
        data = {'transfer_id': str(transfer.pk), 'expected_version': transfer.version,
                'reason': '内部互转依据错误', 'evidence': '撤转依据',
                'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def side_effect_counts(self):
        return tuple(model.objects.count() for model in (
            AccountingEvent, LedgerEntry, LedgerPosting, SourceConsumption))


class TransferHTTPTests(TransferHTTPFixture, TestCase):
    def test_owner_links_transfer_and_company_net_stays_zero(self):
        self.login(self.owner)
        before = self.side_effect_counts()
        response = self.client.post(TRANSFER_SAVE, self.save_data())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(InternalTransfer.objects.count(), 1)
        # Two classification events/postings are expected; no other accounting write.
        self.assertEqual(self.side_effect_counts()[0], before[0] + 2)
        self.assertEqual(self.side_effect_counts()[2], before[2] + 2)

    def test_read_pages_and_selected_rows(self):
        self.login(self.owner)
        self.assertEqual(self.client.get(TRANSFER_LIST).status_code, 200)
        self.assertContains(self.client.get(TRANSFER_FORM), '登记内部互转')

    def test_invalid_same_cash_returns_form_error(self):
        self.login(self.owner)
        response = self.client.post(TRANSFER_SAVE, self.save_data(
            in_cash=f"{self.out_cash.pk}:{self.out_cash.version}"))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(InternalTransfer.objects.exists())

    def test_unknown_field_rejected(self):
        self.login(self.owner)
        response = self.client.post(TRANSFER_SAVE, self.save_data(status='reversed'))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(InternalTransfer.objects.exists())

    def test_pm_and_member_denied(self):
        self.login(self.pm)
        self.assertEqual(self.client.get(TRANSFER_LIST).status_code, 403)
        self.assertEqual(self.client.post(TRANSFER_SAVE, self.save_data()).status_code, 403)
        self.login(self.plain)
        self.assertEqual(self.client.get(TRANSFER_LIST).status_code, 403)
        self.assertEqual(self.client.post(TRANSFER_SAVE, self.save_data()).status_code, 403)

    def test_reverse_flow_and_readonly_after_reverse(self):
        transfer = self.link()
        self.login(self.owner)
        response = self.client.get(f'{TRANSFER_REVERSE}?id={transfer.pk}')
        self.assertEqual(response.status_code, 200)
        response = self.client.post(TRANSFER_REVERSE_SAVE,
                                    self.reverse_data(transfer))
        self.assertEqual(response.status_code, 302)
        transfer.refresh_from_db()
        self.assertEqual(transfer.status, 'reversed')
        self.assertEqual(self.client.get(f'{TRANSFER_REVERSE}?id={transfer.pk}').status_code, 403)

    def test_module_disabled_write_denied(self):
        self.login(self.owner)
        PluginState.objects.update_or_create(
            organization=self.org, plugin_id='cash',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.assertEqual(self.client.post(TRANSFER_SAVE, self.save_data()).status_code, 403)
        self.assertFalse(InternalTransfer.objects.exists())
