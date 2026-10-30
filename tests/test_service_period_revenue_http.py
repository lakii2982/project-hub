"""HTTP browser boundaries for PH-222 service-period confirmation and monthly revenue."""
import csv
import html
import io
import re
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client, TestCase
from django.utils import timezone

from core.models import Membership, Organization, PluginState, Project
from pmo_plugins.contracts import approval_services as contracts
from pmo_plugins.contracts.party_services import create_contact, create_party
from pmo_plugins.contracts.services import create_contract_draft
from pmo_plugins.delivery import service_period_services as services
from pmo_plugins.delivery.models import (CustomerConfirmationEvidence, ServiceConfirmation,
                                         ServiceConfirmationMonth)
from pmo_plugins.management_revenue.models import RevenueAdjustment, RevenueRecognition

LIST = '/modules/delivery/service-confirmation-list/'
DETAIL = '/modules/delivery/service-confirmation-detail/'
FORM = '/modules/delivery/service-confirmation-form/'
SAVE = '/modules/delivery/service-confirmation-save/'
ACTION = '/modules/delivery/service-confirmation-action/'


class ServicePeriodHTTPFixture:
    def setUp(self):
        self.today = timezone.localdate()
        first = self.today.replace(day=1)
        self.coverage_start = first - timedelta(days=1)
        self.coverage_end = first
        self.org = Organization.objects.create(name='服务期演练公司')
        self.owner = self.member('owner')
        self.finance = self.member('finance')
        self.pm = self.member('pm')
        self.auditor = self.member('auditor')
        self.employee_member = self.member('member')
        self.project = Project.objects.create(
            organization=self.org, code='SP1', name='服务期演练项目', client='服务客户',
            manager=self.pm, start_date=self.coverage_start, end_date=self.today)
        self.party = create_party(self.owner, display_name='服务客户', roles=['customer'],
                                  active_from=self.coverage_start, request_token=uuid.uuid4())
        self.contact = create_contact(self.owner, party_id=self.party.pk, name='服务确认联系人',
                                      request_token=uuid.uuid4())
        self.contract = create_contract_draft(
            self.owner, project_id=self.project.pk, party_id=self.party.pk,
            contract_code='SVC-1', request_token=uuid.uuid4())
        self.line = self.approved_line()

    def member(self, role):
        user = get_user_model().objects.create_user(username=role + uuid.uuid4().hex)
        return Membership.objects.create(organization=self.org, user=user, role=role)

    def line_ref(self):
        return {'namespace': 'contracts', 'kind': 'contract_line',
                'id': str(self.line.pk), 'version': self.line.version}

    def approved_line(self, *, start=None, end=None, net_cents=10100,
                      partial_period_rule='calendar_days', period_unit='calendar_day',
                      contract=None):
        start = start or self.coverage_start
        end = end or self.coverage_end
        pricing = {'template': 'fixed_service_period', 'net_cap_cents': net_cents,
                   'service_start': start.isoformat(), 'service_end': end.isoformat(),
                   'period_unit': period_unit, 'partial_period_rule': partial_period_rule,
                   'service_schedule': [{'coverage_start': start.isoformat(),
                                         'coverage_end': end.isoformat(),
                                         'allocated_net_cents': net_cents}]}
        value = {'line_no': 1, 'net_cents': net_cents, 'tax_cents': 0, 'gross_cents': net_cents,
                 'pricing_configuration': pricing}
        version = contracts.save_version(
            self.owner, (contract or self.contract).pk, lines=[value], effective_on=start,
            contract_reference='合成服务期合同批准依据', request_token=uuid.uuid4())
        version = contracts.submit_version(self.owner, version.pk, expected_version=version.version,
                                           request_token=uuid.uuid4())
        version = contracts.review_version(
            self.finance, version.pk, expected_version=version.version,
            request_token=uuid.uuid4(), decision='approve', review_note='独立核对服务期合同')
        return version.lines.get()

    def extra_project_line(self, *, code='SVC-2', partial_period_rule='calendar_days',
                           start=None, end=None):
        project = Project.objects.create(
            organization=self.org, code=code, name='服务期隔离项目', client='服务客户',
            manager=self.pm, start_date=self.coverage_start, end_date=self.today)
        contract = create_contract_draft(
            self.owner, project_id=project.pk, party_id=self.party.pk,
            contract_code=code, request_token=uuid.uuid4())
        return self.approved_line(start=start, end=end,
                                  partial_period_rule=partial_period_rule, contract=contract)

    def login(self, member):
        self.client.force_login(member.user)

    def form_data(self, **changes):
        data = {'line': str(self.line.pk), 'contact': str(self.contact.pk),
                'confirmed_on': self.today.isoformat(),
                'coverage_start': self.coverage_start.isoformat(),
                'coverage_end': self.coverage_end.isoformat(),
                'channel': '客户邮件', 'reference': 'SERVICE-EVIDENCE-001',
                'content': '客户确认真实服务已完成', 'request_token': str(uuid.uuid4())}
        data.update(changes)
        return data

    def save(self, member=None, **changes):
        self.login(member or self.owner)
        return self.client.post(SAVE, self.form_data(**changes))

    def confirmation(self, *, submit=True, confirm=True):
        row = services.save_service_confirmation_with_evidence(
            self.owner, contract_line_ref=self.line_ref(), contact_id=self.contact.pk,
            coverage_start=self.coverage_start, coverage_end=self.coverage_end,
            confirmed_on=self.today, channel='客户邮件', reference='SERVICE-EVIDENCE-SETUP',
            statement='客户确认真实服务已完成', request_token=uuid.uuid4(), dry_run=False)
        if submit:
            row = services.submit_service_confirmation(
                self.owner, row.pk, expected_version=row.version,
                request_token=uuid.uuid4(), dry_run=False)
        if submit and confirm:
            row = services.review_service_confirmation(
                self.finance, row.pk, expected_version=row.version,
                request_token=uuid.uuid4(), decision='approve', note='独立核对客户依据',
                dry_run=False)
        return row

    def action(self, member, row, action, month=None, **extra):
        self.login(member)
        query = f'?id={row.pk}&action={action}' + (f'&month={month}' if month else '')
        data = {'expected_version': row.confirmed_version or row.version,
                'request_token': str(uuid.uuid4()), **extra}
        return self.client.post(ACTION + query, data)


class ServicePeriodHTTPTests(ServicePeriodHTTPFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def months(self, row):
        return list(row.months.order_by('service_month'))

    def test_full_chain_customer_date_service_months_and_per_month_income(self):
        self.login(self.owner)
        response = self.client.get(LIST)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '服务期确认')
        self.assertContains(response, '固定节点验收')
        self.assertContains(response, 'modules/delivery/acceptance-list/')
        page = self.client.get(FORM, {'line': str(self.line.pk)})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, '客户确认日期')
        self.assertContains(page, '服务覆盖起始日')
        self.assertNotContains(page, 'name="accepted_net_cents"')
        self.assertNotContains(page, 'name="service_month"')
        response = self.save()
        self.assertEqual(response.status_code, 302)
        row = ServiceConfirmation.objects.get()
        self.assertEqual(row.status, 'draft')
        self.assertEqual(row.confirmed_net_cents, 10100)
        self.assertFalse(ServiceConfirmationMonth.objects.exists())
        self.assertEqual(self.action(self.owner, row, 'submit').status_code, 302)
        row.refresh_from_db()
        self.assertEqual(self.action(self.owner, row, 'review', decision='approve', reason='自审').status_code, 403)
        self.assertEqual(self.action(self.finance, row, 'review', decision='approve', reason='独立核对').status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.status, 'confirmed')
        months = self.months(row)
        self.assertEqual(len(months), 2)
        self.assertEqual(sum(item.allocated_net_cents for item in months), 10100)
        self.login(self.auditor)
        detail = self.client.get(DETAIL, {'id': row.pk})
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, '客户确认日期')
        self.assertContains(detail, '服务月')
        self.assertContains(detail, '待收入')
        self.assertContains(detail, months[0].service_month)
        self.assertNotContains(detail, '&#x27;namespace&#x27;')
        first = months[0]
        self.assertEqual(self.action(self.owner, row, 'recognize', month=first.service_month).status_code, 302)
        self.assertEqual(RevenueRecognition.objects.count(), 1)
        recognition = RevenueRecognition.objects.get()
        self.assertEqual(recognition.service_confirmation_month_id, first.pk)
        self.assertEqual(recognition.occurred_on, first.coverage_end)
        self.assertEqual(recognition.posting_period, first.service_month)
        self.login(self.auditor)
        detail = self.client.get(DETAIL, {'id': row.pk})
        self.assertContains(detail, '已收入')
        self.assertContains(detail, '待收入')
        self.assertEqual(self.action(self.finance, row, 'reverse', reason='尚未逐月反收入 | 不允许').status_code, 400)
        for month in months[1:]:
            self.assertEqual(self.action(self.owner, row, 'recognize', month=month.service_month).status_code, 302)
        for month in months:
            detail = self.client.get(DETAIL, {'id': row.pk})
            self.assertContains(detail, '已收入')
            self.assertEqual(self.action(self.owner, row, 'reverse_income', month=month.service_month,
                                         reason='原额完整冲销该月管理收入').status_code, 302)
        self.assertEqual(RevenueRecognition.objects.count(), 2)
        self.assertEqual(RevenueAdjustment.objects.count(), 2)
        self.assertEqual(self.action(self.finance, row, 'reverse', reason='核对后反冲已确认服务').status_code, 302)
        row.refresh_from_db()
        self.assertEqual(row.status, 'reversed')
        self.assertEqual(row.version, row.confirmed_version)
        self.login(self.auditor)
        detail = self.client.get(DETAIL, {'id': row.pk})
        self.assertContains(detail, '已反冲')
        self.login(self.owner)
        detail = self.client.get(DETAIL, {'id': row.pk})
        self.assertContains(detail, '以新客户依据登记替代确认')
        replacement_form = self.client.get(FORM, {'id': row.pk, 'action': 'replace'})
        self.assertEqual(replacement_form.status_code, 200)
        self.assertContains(replacement_form, 'name="replaces_confirmation_id"')
        replacement = self.save(replaces_confirmation_id=str(row.pk),
                                reference='SERVICE-EVIDENCE-NEW',
                                content='客户新依据确认服务范围')
        self.assertEqual(replacement.status_code, 302)
        replacement_row = ServiceConfirmation.objects.exclude(pk=row.pk).get()
        self.assertEqual(replacement_row.replaces_confirmation_id, row.pk)
        self.assertEqual(replacement_row.status, 'draft')

    def test_failed_save_rolls_back_evidence_rejects_injection_and_csrf(self):
        self.login(self.owner)
        before = CustomerConfirmationEvidence.objects.count()
        response = self.client.post(SAVE, self.form_data(
            **{'coverage_start': (self.coverage_start - timedelta(days=10)).isoformat()}))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, 'SERVICE-EVIDENCE-001', status_code=400)
        self.assertEqual(before, CustomerConfirmationEvidence.objects.count())
        self.assertFalse(ServiceConfirmation.objects.exists())
        injected = self.form_data()
        injected.update({'accepted_net_cents': '1', 'service_month': '2026-01',
                         'allocated_net_cents': '1'})
        response = self.client.post(SAVE, injected)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ServiceConfirmation.objects.exists())
        self.assertFalse(CustomerConfirmationEvidence.objects.exists())
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.owner.user)
        self.assertEqual(protected.post(SAVE, self.form_data()).status_code, 403)
        self.assertFalse(ServiceConfirmation.objects.exists())
        # A valid form rejected by the owner service must roll back the new evidence too.
        self.confirmation()
        before = CustomerConfirmationEvidence.objects.count()
        response = self.client.post(SAVE, self.form_data(reference='SERVICE-EVIDENCE-OVERLAP'))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(before, CustomerConfirmationEvidence.objects.count())

    def test_roles_cross_project_self_review_and_no_partial_cross_month(self):
        row = self.confirmation(submit=False)
        self.assertEqual(self.action(self.owner, row, 'submit').status_code, 302)
        row.refresh_from_db()
        self.assertEqual(self.action(self.owner, row, 'review', decision='approve', reason='自审').status_code, 403)
        self.assertEqual(self.action(self.pm, row, 'review', decision='approve', reason='PM自审').status_code, 403)
        for member in (self.owner, self.finance, self.pm, self.auditor):
            self.login(member)
            self.assertEqual(self.client.get(DETAIL, {'id': row.pk}).status_code, 200)
        self.login(self.employee_member)
        self.assertEqual(self.client.get(LIST).status_code, 403)
        self.assertEqual(self.client.get(DETAIL, {'id': row.pk}).status_code, 403)
        self.assertEqual(self.save(member=self.employee_member).status_code, 403)
        for member in (self.auditor, self.pm):
            self.assertEqual(self.action(member, row, 'review', decision='approve', reason='越权').status_code, 403)
        outsider = self.member('pm')
        self.login(outsider)
        self.assertEqual(self.client.get(DETAIL, {'id': row.pk}).status_code, 403)
        # Cross-month no_partial: draft is allowed, formal submit is refused with the frozen message.
        no_partial_line = self.extra_project_line(code='SVC-NP',
                                                  partial_period_rule='no_partial')
        self.login(self.owner)
        response = self.client.post(SAVE, {
            'line': str(no_partial_line.pk), 'contact': str(self.contact.pk),
            'confirmed_on': self.today.isoformat(),
            'coverage_start': self.coverage_start.isoformat(),
            'coverage_end': self.coverage_end.isoformat(), 'channel': '客户邮件',
            'reference': 'SERVICE-EVIDENCE-NP', 'content': '客户确认整段服务',
            'request_token': str(uuid.uuid4())})
        self.assertEqual(response.status_code, 302)
        pending = ServiceConfirmation.objects.get(replaces_confirmation__isnull=True,
                                                  contract_line=no_partial_line)
        rejected = self.action(self.owner, pending, 'submit')
        self.assertEqual(rejected.status_code, 400)
        self.assertContains(rejected, '缺明确月价', status_code=400)
        self.assertContains(rejected, '正式合同变更', status_code=400)
        # A creator may withdraw an unconfirmed draft with an explicit reason; it becomes terminal.
        self.login(self.owner)
        created = self.client.post(SAVE, self.form_data(reference='SERVICE-EVIDENCE-WITHDRAW'))
        self.assertEqual(created.status_code, 302)
        draft = ServiceConfirmation.objects.get(contract_line=self.line, status='draft')
        self.assertEqual(self.action(self.owner, draft, 'withdraw', reason='客户依据登记口径调整').status_code, 302)
        draft.refresh_from_db()
        self.assertEqual(draft.status, 'withdrawn')
        self.assertEqual(self.action(self.owner, draft, 'submit').status_code, 400)

    def test_browser_checkpoints_and_locked_month_rejection(self):
        row = self.confirmation()
        self.login(self.owner)
        detail = self.client.get(DETAIL, {'id': row.pk})
        self.assertEqual(detail.status_code, 200)
        self.assertContains(detail, '客户确认日期')
        self.assertContains(detail, '服务月')
        self.assertContains(detail, '任一月收入不代表整个父来源已处理')
        self.assertContains(detail, 'aria-label="服务月分配与收入状态"')
        self.assertContains(detail, 'aria-label="验收与服务期确认"')
        self.assertContains(detail, 'service-confirmation-list/')
        self.assertContains(detail, 'acceptance-list/')
        self.assertNotContains(detail, 'name="accepted_net_cents"')
        form_page = self.client.get(FORM, {'line': str(self.line.pk)})
        self.assertEqual(form_page.status_code, 200)
        self.assertContains(form_page, 'type="date"')
        self.assertNotContains(form_page, 'name="accepted_net_cents"')
        self.assertNotContains(form_page, 'name="service_month"')
        # A mid-coverage company lock refuses formal submission without hiding the customer date.
        locked_line = self.extra_project_line(code='SVC-LOCK')
        self.login(self.owner)
        created = self.client.post(SAVE, self.form_data(line=str(locked_line.pk)))
        self.assertEqual(created.status_code, 302)
        locked = ServiceConfirmation.objects.get(contract_line=locked_line)
        Organization.objects.filter(pk=self.org.pk).update(locked_through=self.coverage_start)
        response = self.action(self.owner, locked, 'submit')
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '服务覆盖与原锁账期间相交', status_code=400)
        Organization.objects.filter(pk=self.org.pk).update(locked_through=None)

    def test_finance_project_pages_and_csv_expose_service_months(self):
        row = self.confirmation()
        months = self.months(row)
        self.assertEqual(self.action(self.owner, row, 'recognize', month=months[0].service_month).status_code, 302)
        for member in (self.owner, self.finance, self.auditor):
            self.login(member)
            finance = self.client.get(f'/finance/?project={self.project.pk}')
            self.assertEqual(finance.status_code, 200)
            project = self.client.get(f'/projects/{self.project.pk}/')
            self.assertEqual(project.status_code, 200)
            response = self.client.get(f'/export/management-revenue/?project={self.project.pk}')
            self.assertEqual(response.status_code, 200)
            self.assertIn('服务确认', response.content.decode('utf-8-sig'))
            self.assertIn('服务月', response.content.decode('utf-8-sig'))
            values = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
            self.assertEqual(len(values), 1)
            self.assertEqual(values[0]['服务确认'], str(row.pk))
            self.assertEqual(values[0]['服务月'], months[0].service_month)
            self.assertEqual(values[0]['客户确认日'], self.today.isoformat())
            self.assertEqual(int(values[0]['管理收入净额_分']), months[0].allocated_net_cents)
        self.login(self.pm)
        self.assertEqual(self.client.get(f'/export/management-revenue/?project={self.project.pk}').status_code, 403)
        self.login(self.employee_member)
        self.assertEqual(self.client.get(f'/finance/?project={self.project.pk}').status_code, 403)

    def test_module_disabled_history_facts_still_readable_but_routes_refuse(self):
        row = self.confirmation()
        from core.plugin_runtime import set_enabled
        set_enabled(self.owner, 'management_revenue', False)
        # PH-226: disable the newer settlement dependant before its delivery/contracts dependencies.
        from core.plugin_runtime import plugin_catalog
        set_enabled(self.owner, 'onboarding', False)
        if any(spec['id'] == 'settlement' for spec in plugin_catalog()):
            set_enabled(self.owner, 'settlement', False)
        set_enabled(self.owner, 'delivery', False)
        self.login(self.auditor)
        self.assertEqual(self.client.get(LIST).status_code, 403)
        self.assertEqual(self.client.get(DETAIL, {'id': row.pk}).status_code, 403)
        state = services.service_confirmation_income_state(self.owner, row.pk)
        self.assertEqual(len(state['months']), 2)
        self.assertFalse(state['any_posted'])
        snapshot = services.confirmed_service_snapshot(self.owner, services._source_ref(row),
                                                       for_recognition=False)
        self.assertEqual(snapshot['version'], row.confirmed_version)

    @staticmethod
    def _action_form(page_html):
        match = re.search(
            r'<form method="post" action="([^"]*service-confirmation-action[^"]*)">(.*?)</form>',
            page_html, re.S)
        assert match, 'rendered service confirmation action form missing'
        action, body = html.unescape(match.group(1)), match.group(2)
        values = {}
        for tag in re.findall(r'<input[^>]*>', body):
            if 'type="hidden"' not in tag:
                continue
            name = re.search(r'name="([^"]*)"', tag)
            value = re.search(r'value="([^"]*)"', tag)
            if name:
                values[html.unescape(name.group(1))] = html.unescape(value.group(1)) if value else ''
        return action, values

    def test_detail_page_click_submits_month_and_expected_version(self):
        row = self.confirmation()
        month = self.months(row)[0].service_month
        self.login(self.owner)
        detail = self.client.get(DETAIL, {'id': row.pk})
        detail_html = detail.content.decode()
        # The detail page must link a month-bearing recognize action, not id/action only.
        self.assertIn(f'action=recognize&amp;month={month}', detail_html)
        form_url = html.unescape(re.search(r'href="([^"]*action=recognize[^"]*)"', detail_html).group(1))
        page = self.client.get(form_url)
        self.assertEqual(page.status_code, 200)
        action, fields = self._action_form(page.content.decode())
        self.assertIn(f'month={month}', action)
        self.assertIn('expected_version', fields)
        self.assertEqual(fields['service_month'], month)
        self.assertEqual(int(fields['expected_version']), row.confirmed_version)
        # Browser-equivalent submit of the rendered form succeeds.
        self.assertEqual(self.client.post(action, fields).status_code, 302)
        recognition = RevenueRecognition.objects.get(service_confirmation_month__service_month=month)
        # The same detail page now exposes the month-bearing reversal form with expected_version.
        detail = self.client.get(DETAIL, {'id': row.pk})
        detail_html = detail.content.decode()
        self.assertIn(f'action=reverse_income&amp;month={month}', detail_html)
        form_url = html.unescape(re.search(r'href="([^"]*action=reverse_income[^"]*)"', detail_html).group(1))
        page = self.client.get(form_url)
        self.assertEqual(page.status_code, 200)
        action, fields = self._action_form(page.content.decode())
        self.assertIn(f'month={month}', action)
        self.assertIn('recognition_id', fields)
        self.assertIn('expected_version', fields)
        fields['reason'] = '原额完整冲销该月管理收入'
        self.assertEqual(self.client.post(action, fields).status_code, 302)
        self.assertTrue(RevenueAdjustment.objects.filter(recognition=recognition, status='posted').exists())
        # Repeating the same month income action is a business 400 with a readable reason.
        repeated = self.client.post(action.replace('action=reverse_income', 'action=recognize'), {
            'expected_version': row.confirmed_version, 'service_month': month,
            'request_token': str(uuid.uuid4())})
        self.assertEqual(repeated.status_code, 400)
        self.assertContains(repeated, '已产生管理收入', status_code=400)
