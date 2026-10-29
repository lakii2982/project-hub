"""First contract approval business/immutable evidence/zero posting regressions."""
from copy import deepcopy
from datetime import timedelta
import uuid
import importlib.util
from unittest import mock, skipUnless
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from core.models import AuditEvent, DomainEvent, LedgerEntry, CashPlan, Milestone, Membership, PluginState
from pmo_plugins.contracts.models import ContractVersion, ContractLine, MethodPolicySelection, PaymentTerm, WarrantyTerm
from pmo_plugins.contracts import approval_services as service
from pmo_plugins.contracts.services import update_contract_draft
from tests.test_v1_contracts import Fixture


class ContractApprovalTests(Fixture, TestCase):
    def setUp(self):
        super().setUp()
        self.finance = self.member('finance')
        self.contract = self.make_contract()
        self.milestone = Milestone.objects.create(project=self.project, name='交付节点', due_date=self.today, criteria='项目确认')

    def line(self, template='confirmed_time'):
        pricing = {'template': template, 'net_cap_cents': 100000}
        if template == 'confirmed_time':
            pricing.update(time_unit='hour', unit_minutes=60, unit_price_cents=10000)
        elif template == 'fixed_milestone':
            pricing['milestones'] = [{'milestone_id': str(self.milestone.pk), 'allocated_net_cents': 100000}]
        else:
            pricing.update(service_start=self.today.isoformat(), service_end=self.today.isoformat(), period_unit='calendar_day',
                partial_period_rule='no_partial', service_schedule=[{'coverage_start': self.today.isoformat(),
                    'coverage_end': self.today.isoformat(), 'allocated_net_cents': 100000}])
        return {'line_no': 1, 'net_cents': 100000, 'tax_cents': 6000, 'gross_cents': 106000,
                'pricing_configuration': pricing,
                'payment_terms': [{'key': 'retention', 'condition_type': 'warranty', 'gross_cents': 6000, 'release_condition': '质保结束'}],
                'warranty_terms': [{'payment_key': 'retention', 'gross_cents': 6000, 'release_condition': '质保结束'}]}

    def save(self, lines=None, **changes):
        values = dict(lines=lines or [self.line()], effective_on=self.today, contract_reference='实际合同经办依据', request_token=uuid.uuid4())
        values.update(changes)
        return service.save_version(self.owner, self.contract.pk, **values)

    def submit(self, version):
        return service.submit_version(self.owner, version.pk, expected_version=version.version, request_token=uuid.uuid4())

    def approve(self, version, **changes):
        values = dict(expected_version=version.version, request_token=uuid.uuid4(), decision='approve', review_note='独立核对合同与规则')
        values.update(changes)
        return service.review_version(self.finance, version.pk, **values)

    def facts(self):
        return tuple(model.objects.count() for model in (ContractVersion, ContractLine, MethodPolicySelection, PaymentTerm, WarrantyTerm,
                                                        AuditEvent, DomainEvent, LedgerEntry, CashPlan))

    def denied(self, call):
        before = self.facts()
        with self.assertRaises((PermissionDenied, ValidationError, TypeError)):
            call()
        self.assertEqual(before, self.facts())

    def test_three_templates_first_approval_and_canonical_event(self):
        for index, template in enumerate(('confirmed_time', 'fixed_milestone', 'fixed_service_period')):
            if index:
                self.contract = self.make_contract(contract_code='T' + str(index))
            version = self.save([self.line(template)])
            self.assertFalse(ContractLine.objects.filter(contract_version=version).exists())
            self.assertIsNone(version.approved_by_member_id)
            version = self.submit(version)
            money_before = (LedgerEntry.objects.count(), CashPlan.objects.count(), self.project.contract_cents)
            version = self.approve(version)
            self.assertEqual(version.status, 'approved')
            self.contract.refresh_from_db()
            self.assertEqual(self.contract.status, 'active')
            self.assertEqual(money_before, (LedgerEntry.objects.count(), CashPlan.objects.count(), self.project.contract_cents))
            line = version.lines.get()
            self.assertEqual(line.pricing_template, template)
            self.assertEqual(line.pricing_configuration['policy_ref']['policy_id'], str(line.policy_selection.pk))
            self.assertEqual(service.resolve_policy(self.finance, line.pricing_configuration['policy_ref'], line_id=line.pk, as_of=self.today), line.pricing_configuration)
            event = DomainEvent.objects.get(source_id=version.approval_audit_id)
            self.assertEqual(event.data['event_id'], str(event.event_id))
            self.assertEqual(event.data['payload']['posting_event_refs'], [])
            self.assertEqual(version.evidence[0]['sha256'], version.approval_audit.event_hash)
            self.assertEqual(line.method_policy['evidence_ref']['kind'], 'audit_record')
        totals = service.approved_contract_totals(self.finance, self.project.pk)
        self.assertEqual((totals['net_cents'], totals['tax_cents'], totals['gross_cents']), (300000, 18000, 318000))

    @skipUnless(importlib.util.find_spec('jsonschema') is not None, 'Strict schema tool dependency is not a product dependency')
    def test_strict_contract_schema_states_relations_and_real_approval_event(self):
        import runpy
        from pathlib import Path
        from jsonschema import ValidationError as SchemaError
        api = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'docs/v1.0-development/contracts/validate_contracts.py'))
        schemas, registry = api['load']()
        def validate(name, value, file='entities.schema.json'):
            api['validator']({'$ref': schemas[file]['$id'] + '#/$defs/' + name}, registry).validate(value)
        draft = {'id': 'version', 'organization_id': self.org.pk, 'version': 1, 'status': 'draft',
                 'created_at': '2026-10-29T01:00:00Z', 'contract_id': str(self.contract.pk),
                 'version_no': 1, 'effective_on': self.today.isoformat(), 'created_by_member_id': self.owner.pk, 'evidence': []}
        validate('ContractVersion', draft)
        for bad in [dict(draft, approved_by_member_id=self.finance.pk), dict(draft, status='approved'),
                    {k:v for k,v in draft.items() if k != 'created_by_member_id'}, dict(draft, status='submitted')]:
            with self.assertRaises(SchemaError): validate('ContractVersion', bad)
        approved = self.approve(self.submit(self.save()))
        value = dict(draft, id=str(approved.pk), version=approved.version, status='approved',
                     submitted_by_member_id=approved.submitted_by_member_id,
                     approved_by_member_id=approved.approved_by_member_id,
                     approved_on=approved.approved_on.isoformat(), evidence=approved.evidence)
        validate('ContractVersion', value)
        validate('ContractVersion', dict(value, status='superseded'))
        event = DomainEvent.objects.get(source_id=approved.approval_audit_id)
        validate('contracts.version_approved.v1', event.data, 'events.schema.json')
        line = approved.lines.get()
        line_data = {'id':str(line.pk), 'organization_id':self.org.pk, 'version':1, 'status':'approved',
            'created_at':line.created_at.isoformat(), 'contract_id':str(self.contract.pk),
            'contract_version_id':str(approved.pk), 'line_no':1, 'project_id':self.project.pk,
            'counterparty_id':str(self.party.pk), 'pricing_template':line.pricing_template,
            'net_cents':line.net_cents,'tax_cents':line.tax_cents,'gross_cents':line.gross_cents,
            'method_policy':line.method_policy,'pricing_configuration':line.pricing_configuration}
        validate('ContractLine', line_data)
        selection=line.policy_selection
        policy={'id':str(selection.pk),'organization_id':self.org.pk,'version':1,'status':'approved',
            'created_at':selection.created_at.isoformat(),'contract_line_id':str(line.pk),
            'policy_version':selection.policy_version,'pricing_configuration':selection.pricing_configuration,
            'selected_on':selection.selected_on.isoformat(),'approved_by_member_id':selection.approved_by_member_id}
        validate('MethodPolicySelection',policy)
        bad_policy=dict(policy);bad_policy.pop('pricing_configuration')
        with self.assertRaises(SchemaError):validate('MethodPolicySelection',bad_policy)
        bad = dict(line_data); bad.pop('contract_version_id')
        with self.assertRaises(SchemaError): validate('ContractLine',bad)
        for amount in [True, 100000.0, -1]:
            with self.assertRaises(SchemaError): validate('ContractLine',dict(line_data,net_cents=amount))
        warranty = approved.lines.get().warranty_terms.get()
        dto = {'contract_line_id':str(line.pk),'payment_term_id':str(warranty.payment_term_id),
               'release_condition':warranty.release_condition,'gross_cents':warranty.gross_cents}
        validate('WarrantyTerm',dto,'write-dtos.schema.json')
        bad=dict(dto);bad.pop('payment_term_id')
        with self.assertRaises(SchemaError):validate('WarrantyTerm',bad,'write-dtos.schema.json')
        with self.assertRaises(SchemaError):validate('ContractVersion',{'contract_id':str(self.contract.pk),
            'version_no':1,'effective_on':self.today.isoformat(),'approved_by_member_id':self.finance.pk},'write-dtos.schema.json')

    def test_save_submit_review_replay_and_old_version(self):
        token = uuid.uuid4()
        version = self.save(request_token=token)
        self.assertEqual(self.save(request_token=token).pk, version.pk)
        self.denied(lambda: self.save(request_token=token, contract_reference='改变依据'))
        version = self.submit(version)
        self.denied(lambda: self.save(version_id=version.pk, expected_version=version.version))
        self.denied(lambda: update_contract_draft(self.owner, self.contract.pk, expected_version=1,
            project_id=self.project.pk, party_id=self.party.pk, contract_code='偷偷修改'))
        self.denied(lambda: service.review_version(self.owner, version.pk, expected_version=version.version,
            request_token=uuid.uuid4(), decision='approve', review_note='自审'))
        token = uuid.uuid4()
        approved = self.approve(version, request_token=token)
        self.assertEqual(self.approve(version, request_token=token).pk, approved.pk)
        self.denied(lambda: self.approve(version))
        self.denied(lambda: self.save())
        self.denied(lambda: service.resolve_policy(self.finance, approved.lines.get().pricing_configuration['policy_ref'],
            line_id=approved.lines.get().pk, as_of=self.today - timedelta(days=1)))

    def test_rejection_terminal_new_version_and_snapshot_guard(self):
        version = self.submit(self.save())
        rejected = self.approve(version, decision='reject')
        self.assertEqual(rejected.status, 'rejected')
        self.denied(lambda: self.submit(rejected))
        fresh = self.save()
        self.assertEqual(fresh.version_no, 2)
        fresh = self.submit(fresh)
        ContractVersion.objects.filter(pk=fresh.pk).update(contract_reference='篡改冻结依据')
        self.denied(lambda: self.approve(fresh))

    def test_invalid_candidates_no_side_effects(self):
        for field, value in [('net_cents', -1), ('tax_cents', True), ('gross_cents', 1.5)]:
            line = self.line(); line[field] = value
            self.denied(lambda: self.save([line]))
        line = self.line(); line['pricing_configuration']['policy_ref'] = {'policy_id': 'fake', 'version': 1}
        self.denied(lambda: self.save([line]))
        line = self.line(); line['warranty_terms'][0]['gross_cents'] = 6001
        self.denied(lambda: self.save([line]))
        line = self.line(); line['payment_terms'][0]['gross_cents'] = 106001
        self.denied(lambda: self.save([line]))
        line = self.line('fixed_milestone'); line['pricing_configuration']['milestones'][0]['allocated_net_cents'] = 1
        self.denied(lambda: self.save([line]))
        self.denied(lambda: self.save(effective_on=self.today + timedelta(days=1)))
        line = self.line('fixed_service_period'); line['pricing_configuration']['service_schedule'] *= 2
        self.denied(lambda: self.save([line]))
        self.denied(lambda: self.save(contract_reference=''))

    def test_module_revocation_and_outbox_rollback(self):
        version = self.submit(self.save())
        before = self.facts()
        with mock.patch('pmo_plugins.contracts.approval_services.DomainEvent.objects.create', side_effect=RuntimeError('event failure')):
            with self.assertRaises(RuntimeError):
                self.approve(version)
        self.assertEqual(before, self.facts())
        version.refresh_from_db(); self.assertEqual(version.status, 'submitted')
        Membership.objects.filter(pk=self.finance.pk).update(active=False)
        self.denied(lambda: self.approve(version))
        Membership.objects.filter(pk=self.finance.pk).update(active=True)
        PluginState.objects.create(organization=self.org, plugin_id='contracts', enabled=False, version='0.1.0', changed_by=self.owner.user)
        self.denied(lambda: self.approve(version))
        self.denied(lambda: service.approved_contract_totals(self.owner, self.project.pk))

    def test_browser_configuration_routes(self):
        self.client.force_login(self.owner.user)
        response = self.client.get('/modules/contracts/version/?contract=' + str(self.contract.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '人工合同依据')
        self.assertContains(response, self.today.isoformat())
        for route in ('version-save', 'version-submit', 'version-review'):
            self.assertEqual(self.client.get('/modules/contracts/' + route + '/').status_code, 405)
        version = self.submit(self.save())
        self.client.force_login(self.finance.user)
        response = self.client.get('/modules/contracts/version/?id=' + str(version.pk))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '独立审批')
        response = self.client.post('/modules/contracts/version-review/', dict(version_id=str(version.pk), expected_version=version.version,
            request_token=str(uuid.uuid4()), decision='approve', review_note='浏览器核对完整合同'))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.get(response['Location']).status_code, 200)

    def test_browser_normal_forms_save_candidate_and_keep_error_input(self):
        self.client.force_login(self.owner.user)
        payload = dict(contract_id=str(self.contract.pk), request_token=str(uuid.uuid4()), effective_on=self.today.isoformat(),
            contract_reference='浏览器真实合同依据', **{'lines-TOTAL_FORMS': '1', 'lines-INITIAL_FORMS': '0',
            'lines-0-line_no': '1', 'lines-0-template': 'confirmed_time', 'lines-0-net_cents': '1000.00',
            'lines-0-tax_cents': '60.00', 'lines-0-gross_cents': '1060.00', 'lines-0-net_cap_cents': '1000.00',
            'lines-0-time_unit': 'hour', 'lines-0-unit_minutes': '60', 'lines-0-unit_price_cents': '100.00'})
        for prefix in ('milestones', 'schedules', 'payments', 'warranties'):
            payload[prefix + '-TOTAL_FORMS'] = '0'; payload[prefix + '-INITIAL_FORMS'] = '0'
        response = self.client.post('/modules/contracts/version-save/', payload)
        self.assertEqual(response.status_code, 302, response.content.decode())
        version = ContractVersion.objects.get(contract=self.contract)
        self.assertEqual(version.contract_reference, '浏览器真实合同依据')
        response = self.client.get(response['Location'])
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1000')
        payload['request_token'] = str(uuid.uuid4()); payload['lines-0-gross_cents'] = '1'
        before = self.facts()
        response = self.client.post('/modules/contracts/version-save/', payload)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '浏览器真实合同依据', status_code=400)
        self.assertEqual(before, self.facts())


    def test_cross_contract_service_overlap_refused_without_side_effects(self):
        self.approve(self.submit(self.save([self.line('fixed_service_period')])))
        self.contract = self.make_contract(contract_code='SECOND-SERVICE')
        self.denied(lambda: self.save([self.line('fixed_service_period')]))

    def test_rendered_blank_extra_rows_submit_and_decimal_yuan_roundtrip(self):
        from html.parser import HTMLParser
        class BrowserForm(HTMLParser):
            def __init__(self):
                super().__init__(); self.inside = False; self.values = {}; self.select = None
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == 'form':
                    self.inside = 'version-save/' in attrs.get('action', '')
                if not self.inside:
                    return
                if tag == 'input' and attrs.get('type') not in {'checkbox', 'radio', 'submit'}:
                    self.values[attrs['name']] = attrs.get('value', '')
                if tag == 'select':
                    self.select = attrs['name']; self.values[self.select] = None
                if tag == 'option' and self.select and (self.values[self.select] is None or 'selected' in attrs):
                    self.values[self.select] = attrs.get('value', '')
            def handle_endtag(self, tag):
                if tag == 'form': self.inside = False
                if tag == 'select': self.select = None
        self.client.force_login(self.owner.user)
        response = self.client.get('/modules/contracts/version/?contract=' + str(self.contract.pk))
        parser = BrowserForm(); parser.feed(response.content.decode()); payload = parser.values
        self.assertEqual(payload['lines-1-template'], '')
        self.assertEqual(payload['payments-0-condition_type'], '')
        payload.update(contract_reference='保留空白扩展行', **{'lines-0-line_no': '1', 'lines-0-template': 'confirmed_time',
            'lines-0-net_cents': '1000.01', 'lines-0-tax_cents': '60.00', 'lines-0-gross_cents': '1060.01',
            'lines-0-net_cap_cents': '1000.01', 'lines-0-time_unit': 'hour',
            'lines-0-unit_minutes': '60', 'lines-0-unit_price_cents': '100.01'})
        response = self.client.post('/modules/contracts/version-save/', payload)
        self.assertEqual(response.status_code, 302, response.content.decode())
        version = ContractVersion.objects.get(contract=self.contract)
        self.assertEqual(version.draft_content['lines'][0]['net_cents'], 100001)
        self.assertEqual(version.draft_content['lines'][0]['pricing_configuration']['unit_price_cents'], 10001)
        response = self.client.get(response['Location'])
        self.assertContains(response, 'value="1000.01"')

    def test_review_error_preserves_decision_and_note(self):
        version = self.submit(self.save())
        self.client.force_login(self.finance.user)
        response = self.client.post('/modules/contracts/version-review/', dict(version_id=str(version.pk), expected_version=999,
            request_token=str(uuid.uuid4()), decision='reject', review_note='仍需核对的实际意见'))
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, '仍需核对的实际意见', status_code=400)
        self.assertContains(response, 'value="reject" selected', status_code=400)

    def test_save_replay_returns_original_after_other_contract_coverage_approval(self):
        token=uuid.uuid4(); original_contract=self.contract
        first=self.save([self.line('fixed_service_period')],request_token=token)
        self.contract=self.make_contract(contract_code='OTHER-COVERAGE')
        self.approve(self.submit(self.save([self.line('fixed_service_period')])))
        self.contract=original_contract
        before=self.facts()
        replay=self.save([self.line('fixed_service_period')],request_token=token)
        self.assertEqual(replay.pk,first.pk)
        self.assertEqual(before,self.facts())
        self.denied(lambda:self.save([self.line('fixed_service_period')]))
        self.denied(lambda:self.save([self.line('fixed_service_period')],request_token=token,contract_reference='改变原输入'))

    def test_rejected_version_copy_prefills_new_form_without_writing_on_get(self):
        version=self.submit(self.save())
        service.review_version(self.finance,version.pk,expected_version=version.version,
            request_token=uuid.uuid4(),decision='reject',review_note='请核对实际单价')
        self.client.force_login(self.owner.user)
        before=self.facts()
        response=self.client.get('/modules/contracts/version/?copy='+str(version.pk))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,'value="1000"')
        self.assertContains(response,'value="实际合同经办依据"')
        self.assertEqual(self.facts(),before)
        self.assertNotContains(response,'name="version_id" value="'+str(version.pk)+'"')
        version.refresh_from_db();self.assertEqual(version.status,'rejected')

    def test_reject_submitted_version_after_other_contract_coverage_wins(self):
        first=self.submit(self.save([self.line('fixed_service_period')]))
        self.contract=self.make_contract(contract_code='PENDING-OVERLAP')
        second=self.submit(self.save([self.line('fixed_service_period')]))
        self.approve(first)
        rejected=self.approve(second,decision='reject',review_note='重复服务覆盖，请修正')
        self.assertEqual(rejected.status,'rejected')
        self.assertEqual(ContractLine.objects.count(),1)
        self.assertEqual(MethodPolicySelection.objects.count(),1)
        self.assertFalse(LedgerEntry.objects.exists())

    def test_reject_submitted_version_with_inactive_customer(self):
        version=self.submit(self.save())
        from pmo_plugins.contracts.models import Counterparty
        Counterparty.objects.filter(pk=self.party.pk).update(status='inactive')
        rejected=self.approve(version,decision='reject',review_note='客户状态失效，保留原版本')
        self.assertEqual(rejected.status,'rejected')
        self.assertFalse(ContractLine.objects.exists())
        self.assertFalse(LedgerEntry.objects.exists())

    def test_contract_detail_title_contains_only_contract_code(self):
        import re
        self.client.force_login(self.owner.user)
        response=self.client.get('/modules/contracts/contract/?id='+str(self.contract.pk))
        title=re.search(r'<title>(.*?)</title>',response.content.decode(),re.S).group(1)
        self.assertEqual(title,self.contract.contract_code+' · 项目中枢')
        self.assertEqual(response.content.decode().count('收费配置与版本'),1)


from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Thread, Event
import time
from unittest import skipUnless
from django.db import connection, close_old_connections, transaction
from django.test import TransactionTestCase


@skipUnless(connection.vendor == 'postgresql', 'Real approval locking requires PostgreSQL connections')
class ContractApprovalConcurrencyTests(Fixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        # Isolate the original contract lifecycle scenarios from newer dependants.
        from core.plugin_runtime import get_registry, set_enabled
        for plugin_id in ('management_revenue', 'delivery'):
            if plugin_id in get_registry().manifests:
                set_enabled(self.owner, plugin_id, False)
        self.finance = self.member('finance')
        self.contract = self.make_contract()
        self.version = service.save_version(self.owner, self.contract.pk, lines=[{
            'line_no': 1, 'net_cents': 100000, 'tax_cents': 6000, 'gross_cents': 106000,
            'pricing_configuration': {'template': 'confirmed_time', 'net_cap_cents': 100000,
                'time_unit': 'hour', 'unit_minutes': 60, 'unit_price_cents': 10000}}],
            effective_on=self.today, contract_reference='PG合同依据', request_token=uuid.uuid4())
        self.version = service.submit_version(self.owner, self.version.pk, expected_version=self.version.version, request_token=uuid.uuid4())

    def review(self):
        return service.review_version(self.finance, self.version.pk, expected_version=self.version.version,
            request_token=uuid.uuid4(), decision='approve', review_note='PG独立审批')

    def counts(self):
        return tuple(model.objects.count() for model in (ContractLine, MethodPolicySelection, PaymentTerm,
            WarrantyTerm, AuditEvent, DomainEvent, LedgerEntry, CashPlan))

    def test_two_connections_approve_exactly_once(self):
        barrier = Barrier(2)
        def worker():
            close_old_connections()
            try:
                barrier.wait(10)
                try:
                    self.review()
                    return 'approved'
                except ValidationError:
                    return 'conflict'
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(lambda _: worker(), range(2)))
        self.assertCountEqual(result, ['approved', 'conflict'])
        self.assertEqual(self.version.lines.count(), 1)
        self.assertEqual(MethodPolicySelection.objects.count(), 1)
        self.assertEqual(DomainEvent.objects.filter(topic='contracts.version_approved.v1').count(), 1)
        self.assertFalse(LedgerEntry.objects.exists())

    def waiting_approval(self, revoke):
        from core.plugin_runtime import set_enabled
        ready, pids, results = Event(), {}, []
        def worker():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()'); pids['queued'] = cursor.fetchone()[0]
                ready.set()
                try:
                    self.review(); results.append('unexpected approval')
                except PermissionDenied:
                    results.append('refused')
            except BaseException as error:
                results.append(repr(error))
            finally:
                connection.close()
        thread = Thread(target=worker)
        try:
            with transaction.atomic():
                from core.models import Organization
                Organization.objects.select_for_update().get(pk=self.org.pk)
                with connection.cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()'); holder = cursor.fetchone()[0]
                thread.start(); self.assertTrue(ready.wait(5))
                deadline, blockers = time.monotonic() + 5, []
                while time.monotonic() < deadline:
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_blocking_pids(%s)', [pids['queued']]); blockers = cursor.fetchone()[0]
                    if holder in blockers:
                        break
                    time.sleep(0.01)
                self.assertIn(holder, blockers)
                if revoke:
                    Membership.objects.filter(pk=self.finance.pk).update(active=False)
                else:
                    set_enabled(self.owner, 'contracts', False)
                before = self.counts()
        finally:
            if thread.ident:
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(results, ['refused'])
        self.assertEqual(before, self.counts())
        self.version.refresh_from_db(); self.assertEqual(self.version.status, 'submitted')

    def test_approval_wait_rechecks_disable(self):
        self.waiting_approval(False)

    def test_approval_wait_rechecks_member_revocation(self):
        self.waiting_approval(True)
