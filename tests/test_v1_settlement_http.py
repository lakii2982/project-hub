"""PH-224 HTTP browser boundaries for customer settlement and read-only aging."""
import csv
import io
import uuid
from datetime import timedelta

from django.test import Client, TestCase
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.models import (AccountingEvent, LedgerEntry, LedgerPosting, Milestone,
                         Organization, PluginState)
from pmo_plugins.contracts import approval_services as contracts
from pmo_plugins.contracts.party_services import create_contact
from pmo_plugins.delivery import services as delivery
from pmo_plugins.settlement import aging_services
from pmo_plugins.settlement import receivable_services as services
from pmo_plugins.settlement.models import OpenItem, Settlement, SettlementReceipt
from tests.test_v1_contracts import Fixture

LIST = '/modules/settlement/settlement-list/'
DETAIL = '/modules/settlement/settlement-detail/'
FORM = '/modules/settlement/settlement-form/'
SAVE = '/modules/settlement/settlement-save/'
ACTION = '/modules/settlement/settlement-action/'
AGING = '/modules/settlement/aging-list/'


class SettlementHTTPFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.today = timezone.localdate()
        self.finance = self.member('finance')
        self.auditor = self.member('auditor')
        self.employee_member = self.member('member')
        self.contact = create_contact(self.owner, party_id=self.party.pk, name='结算联系人',
                                      request_token=uuid.uuid4())
        self.milestone = Milestone.objects.create(project=self.project, name='结算交付',
                                                  due_date=self.today, criteria='客户确认')
        self.contract = self.make_contract(contract_code='SET-1')
        self.line = self.approved_line()
        self.confirm_performance()

    def line_ref(self):
        return {'namespace': 'contracts', 'kind': 'contract_line',
                'id': str(self.line.pk), 'version': self.line.version}

    def approved_line(self):
        value = {
            'line_no': 1, 'net_cents': 100000, 'tax_cents': 6000, 'gross_cents': 106000,
            'pricing_configuration': {
                'template': 'fixed_milestone', 'net_cap_cents': 100000,
                'milestones': [{'milestone_id': str(self.milestone.pk),
                                'allocated_net_cents': 100000}]},
            'payment_terms': [
                {'key': 'half', 'condition_type': 'unconditional', 'gross_cents': 53000,
                 'due_date': (self.today - timedelta(days=40)).isoformat()},
                {'key': 'second', 'condition_type': 'unconditional', 'gross_cents': 42400,
                 'due_date': (self.today - timedelta(days=5)).isoformat()},
                {'key': 'retention', 'condition_type': 'warranty', 'gross_cents': 10600,
                 'due_date': (self.today + timedelta(days=365)).isoformat(),
                 'release_condition': '质保期届满'},
            ],
            'warranty_terms': [
                {'payment_key': 'retention', 'gross_cents': 10600,
                 'release_condition': '质保期届满',
                 'release_on': (self.today + timedelta(days=365)).isoformat()},
            ],
        }
        version = contracts.save_version(
            self.owner, self.contract.pk, lines=[value], effective_on=self.today,
            contract_reference='合成结算合同批准依据', request_token=uuid.uuid4())
        version = contracts.submit_version(self.owner, version.pk,
                                           expected_version=version.version,
                                           request_token=uuid.uuid4())
        version = contracts.review_version(
            self.finance, version.pk, expected_version=version.version,
            request_token=uuid.uuid4(), decision='approve', review_note='独立核对条款与含税额')
        return version.lines.get()

    def confirm_performance(self):
        row = delivery.save_acceptance_with_evidence(
            self.owner, contract_line_ref=self.line_ref(), contact_id=self.contact.pk,
            confirmed_on=self.today, channel='客户邮件', reference='SETTLEMENT-PERF-001',
            statement='客户确认本次交付范围',
            milestone_coverages=[{'milestone_id': str(self.milestone.pk),
                                  'from_basis_points': 0, 'to_basis_points': 10000}],
            request_token=uuid.uuid4(), dry_run=False)
        row = delivery.submit_acceptance(self.owner, row.pk, expected_version=row.version,
                                         request_token=uuid.uuid4(), dry_run=False)
        delivery.review_acceptance(self.finance, row.pk, expected_version=row.version,
                                   decision='approve', note='独立核对客户真实确认依据',
                                   request_token=uuid.uuid4(), dry_run=False)

    def terms(self):
        return contracts.approved_terms_snapshot(self.owner, self.line_ref(), self.today)

    def term_key(self, term):
        return f"{term['kind']}:{term['id']}"

    def keys(self, *names):
        by_condition = {}
        for term in self.terms()['terms']:
            by_condition.setdefault(term['condition_type'], []).append(term)
        result = []
        for name in names:
            if name == 'half':
                result.append(self.term_key(by_condition['unconditional'][0]))
            elif name == 'second':
                result.append(self.term_key(by_condition['unconditional'][1]))
            elif name == 'retention':
                result.append(self.term_key(by_condition['warranty'][0]))
        return result

    def login(self, member):
        self.client.force_login(member.user)

    def save_data(self, keys, **changes):
        data = {'line': str(self.line.pk), 'terms': list(keys),
                'settled_on': self.today.isoformat(), 'evidence': '合成结算依据 SET-EVID-001',
                'settlement_id': '', 'expected_version': '', 'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def save(self, member=None, **changes):
        self.login(member or self.owner)
        return self.client.post(SAVE, self.save_data(self.keys('half'), **changes))

    def action(self, member, row, action, **extra):
        self.login(member)
        data = {'expected_version': row.version, 'request_token': str(uuid.uuid4()), **extra}
        return self.client.post(f'{ACTION}?id={row.pk}&action={action}', data)

    def create_settlement(self, names, *, approve=True, settled_on=None):
        selected = [term for term in self.terms()['terms']
                    if self.term_key(term) in set(self.keys(*names))]
        row = services.save_settlement(
            self.owner, contract_line_ref=self.line_ref(),
            term_refs=[term['term_ref'] for term in selected],
            settled_on=settled_on or self.today, evidence=[{'reference': '合成结算依据'}],
            request_token=uuid.uuid4(), dry_run=False)
        if approve:
            row = services.submit_settlement(self.owner, row.pk, expected_version=row.version,
                                             request_token=uuid.uuid4(), dry_run=False)
            row = services.review_settlement(
                self.finance, row.pk, expected_version=row.version,
                request_token=uuid.uuid4(), decision='approve',
                note='独立核对批准条款与已确认履约', dry_run=False)
        return row


class SettlementHTTPTests(SettlementHTTPFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_full_http_chain_terms_no_amount_inputs_open_items_and_aging(self):
        before_events = AccountingEvent.objects.count()
        before_ledger = (LedgerEntry.objects.count(), LedgerPosting.objects.count())
        self.login(self.owner)
        response = self.client.get(LIST)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '客户结算')
        self.assertContains(response, '账龄与质保未清')
        chooser = self.client.get(FORM)
        self.assertEqual(chooser.status_code, 200)
        self.assertContains(chooser, self.contract.contract_code)
        page = self.client.get(FORM, {'line': str(self.line.pk)})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '到期款')
        self.assertContains(page, '质保款')
        self.assertContains(page, '质保期届满')
        self.assertNotContains(page, 'name="gross_cents"')
        self.assertNotContains(page, 'name="net_cents"')
        self.assertNotContains(page, 'name="tax_cents"')
        self.assertNotContains(page, 'name="direction"')
        self.assertNotContains(page, 'type="number"')
        response = self.save()
        self.assertEqual(response.status_code, 302)
        row = Settlement.objects.get()
        self.assertEqual(row.status, 'draft')
        self.assertEqual((row.gross_cents, row.net_cents, row.tax_cents), (53000, 50000, 3000))
        self.assertEqual(OpenItem.objects.count(), 0)
        detail = self.client.get(DETAIL, {'id': row.pk})
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, '草稿')
        self.assertContains(detail, '到期款')
        self.assertContains(detail, '尚未生成未清项')
        self.assertEqual(self.action(self.owner, row, 'submit').status_code, 302)
        row.refresh_from_db()
        self.assertEqual(self.action(self.owner, row, 'review', decision='approve',
                                     reason='自审').status_code, 403)
        self.assertEqual(self.action(self.finance, row, 'review', decision='approve',
                                     reason='独立核对条款与履约').status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.status, 'active')
        items = list(OpenItem.objects.order_by('due_date'))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].gross_cents, 53000)
        self.assertTrue(items[0].partition_kind == 'due')
        detail = self.client.get(DETAIL, {'id': row.pk})
        self.assertContains(detail, '到期未清')
        self.assertContains(detail, 'delivery · acceptance')
        self.assertNotContains(detail, '&#x27;namespace&#x27;')
        self.assertEqual(AccountingEvent.objects.count(), before_events)
        self.assertEqual((LedgerEntry.objects.count(), LedgerPosting.objects.count()), before_ledger)
        aging = self.client.get(AGING, {'as_of': self.today.isoformat()})
        self.assertEqual(aging.status_code, 200)
        self.assertContains(aging, '逾期31-60天')
        self.assertContains(aging, (self.today - timedelta(days=40)).isoformat())
        self.assertContains(aging, '客户结算')
        csv_response = self.client.get(AGING, {'format': 'csv', 'as_of': self.today.isoformat()})
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn('账龄桶', csv_response.content.decode('utf-8-sig'))
        self.assertIn('settlement-aging.csv', csv_response['Content-Disposition'])
        # The remaining approved terms can be settled later without reusing the first term.
        self.login(self.owner)
        response = self.client.post(SAVE, self.save_data(self.keys('second', 'retention')))
        self.assertEqual(response.status_code, 302)
        second = Settlement.objects.exclude(pk=row.pk).get()
        self.assertEqual((second.gross_cents, second.net_cents, second.tax_cents),
                         (53000, 50000, 3000))
        # Editing a draft must pre-check the terms already selected (due/retention vs raw kind regression).
        edit = self.client.get(FORM, {'id': second.pk})
        self.assertEqual(edit.status_code, 200)
        for term in second.terms_snapshot['selected']:
            kind = term.get('kind') if term.get('kind') in ('due', 'retention') else (
                'due' if (term.get('term_ref') or {}).get('kind') == 'payment_term' else 'retention')
            expected = f'value="{kind}:{term["id"]}" checked'
            self.assertIn(expected, edit.content.decode('utf-8'))
        self.assertEqual(self.action(self.owner, second, 'submit').status_code, 302)
        second.refresh_from_db()
        self.assertEqual(self.action(self.finance, second, 'review', decision='approve',
                                     reason='独立核对结算').status_code, 302)
        self.assertEqual(OpenItem.objects.count(), 3)
        self.assertEqual(sum(item.gross_cents for item in OpenItem.objects.all()), 106000)
        aging = self.client.get(AGING, {'as_of': self.today.isoformat()})
        self.assertContains(aging, '质保未清')
        self.assertContains(aging, '未到释放日')
        self.assertContains(aging, '未到期')
        self.assertContains(aging, (self.today - timedelta(days=5)).isoformat())
        self.assertContains(aging, (self.today + timedelta(days=365)).isoformat())

    def test_no_amount_or_partial_terms_injection_duplicate_and_idempotency(self):
        self.login(self.owner)
        injection = self.save_data(self.keys('half'), gross_cents='1')
        response = self.client.post(SAVE, injection)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Settlement.objects.exists())
        duplicated = self.save_data(self.keys('half') * 2)
        response = self.client.post(SAVE, duplicated)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Settlement.objects.exists())
        token = uuid.uuid4()
        data = self.save_data(self.keys('half'), request_token=str(token))
        first = self.client.post(SAVE, data)
        self.assertEqual(first.status_code, 302)
        row = Settlement.objects.get()
        self.assertEqual(self.client.post(SAVE, data).status_code, 302)
        self.assertEqual(Settlement.objects.count(), 1)
        changed = dict(data, terms=self.keys('second'))
        response = self.client.post(SAVE, changed)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '请求标识', status_code=400)
        self.assertEqual(Settlement.objects.count(), 1)
        self.assertEqual(self.action(self.owner, row, 'submit').status_code, 302)
        row.refresh_from_db()
        self.assertEqual(self.action(self.finance, row, 'review', decision='approve',
                                     reason='独立核对').status_code, 302)
        self.login(self.owner)
        reused = self.client.post(SAVE, self.save_data(self.keys('half')))
        self.assertEqual(reused.status_code, 400)
        self.assertContains(reused, '已被其他结算使用', status_code=400)
        self.assertEqual(Settlement.objects.count(), 1)
        self.assertEqual(OpenItem.objects.count(), 1)

    def test_business_errors_future_locked_roles_self_review_and_disabled_module(self):
        self.login(self.owner)
        response = self.client.post(SAVE, self.save_data(
            self.keys('half'), settled_on=(self.today + timedelta(days=1)).isoformat()))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '结算日期不能在未来', status_code=400)
        self.assertFalse(Settlement.objects.exists())
        Organization.objects.filter(pk=self.org.pk).update(locked_through=self.today)
        response = self.client.post(SAVE, self.save_data(self.keys('half')))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '已锁', status_code=400)
        self.assertFalse(Settlement.objects.exists())
        Organization.objects.filter(pk=self.org.pk).update(locked_through=None)
        self.login(self.employee_member)
        self.assertEqual(self.client.get(LIST).status_code, 403)
        self.assertEqual(self.client.get(FORM).status_code, 403)
        self.login(self.pm)
        self.assertEqual(self.client.get(LIST).status_code, 200)
        self.assertEqual(self.client.get(FORM, {'line': str(self.line.pk)}).status_code, 403)
        self.assertEqual(self.client.post(SAVE, self.save_data(self.keys('half'))).status_code, 403)
        outsider = self.member('pm')
        self.login(outsider)
        self.assertEqual(self.client.get(LIST).status_code, 200)
        self.login(self.auditor)
        self.assertEqual(self.client.get(LIST).status_code, 200)
        self.assertEqual(self.client.post(SAVE, self.save_data(self.keys('half'))).status_code, 403)
        self.login(self.owner)
        self.assertEqual(self.client.post(SAVE, self.save_data(self.keys('half'))).status_code, 302)
        row = Settlement.objects.get()
        self.assertEqual(self.action(self.owner, row, 'submit').status_code, 302)
        row.refresh_from_db()
        self.assertEqual(self.action(self.owner, row, 'review', decision='approve',
                                     reason='自审').status_code, 403)
        self.assertEqual(self.action(self.finance, row, 'review', decision='approve',
                                     reason='独立核对').status_code, 302)
        self.login(self.pm)
        self.assertEqual(self.client.get(DETAIL, {'id': row.pk}).status_code, 200)
        self.login(self.auditor)
        self.assertEqual(self.client.get(DETAIL, {'id': row.pk}).status_code, 200)
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'settlement', False)
        self.login(self.auditor)
        self.assertEqual(self.client.get(LIST).status_code, 403)
        data = aging_services.open_item_aging(self.owner, as_of=self.today)
        self.assertEqual(data['totals']['count'], 1)

    def test_aging_buckets_retention_as_of_and_read_only(self):
        first = self.create_settlement(('half',))
        second = self.create_settlement(('second', 'retention'))
        before = (Settlement.objects.count(), OpenItem.objects.count(),
                  SettlementReceipt.objects.count(), AccountingEvent.objects.count())
        self.login(self.owner)
        aging = self.client.get(AGING, {'as_of': self.today.isoformat()})
        self.assertEqual(aging.status_code, 200)
        self.assertContains(aging, '逾期31-60天')
        self.assertContains(aging, '逾期1-30天')
        self.assertContains(aging, '未到期')
        self.assertContains(aging, '未到释放日')
        self.assertContains(aging, '质保未清')
        far = self.client.get(AGING, {'as_of': (self.today + timedelta(days=800)).isoformat()})
        self.assertContains(far, '逾期90天以上')
        csv_response = self.client.get(AGING, {'format': 'csv', 'as_of': self.today.isoformat()})
        rows = list(csv.DictReader(io.StringIO(csv_response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows), 3)
        self.assertEqual({row['分区类型'] for row in rows}, {'到期款', '质保款'})
        self.assertEqual(sum(int(row['含税（元）'].replace('.', '')) for row in rows), 106000)
        self.assertEqual(before, (Settlement.objects.count(), OpenItem.objects.count(),
                                  SettlementReceipt.objects.count(), AccountingEvent.objects.count()))
        self.login(self.pm)
        self.assertEqual(self.client.get(AGING, {'project': str(self.project.pk)}).status_code, 200)
        outsider = self.member('pm')
        self.login(outsider)
        self.assertEqual(self.client.get(AGING, {'project': str(self.project.pk)}).status_code, 403)
        self.login(self.employee_member)
        self.assertEqual(self.client.get(AGING).status_code, 403)
