"""Actual contract → customer acceptance → deterministic revenue/reversal chain."""
from copy import deepcopy
from datetime import timedelta
import uuid
import importlib.util
from unittest import mock, skipUnless

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from core.models import (Milestone, AuditEvent, DomainEvent, LedgerEntry, LedgerPosting,
                         AccountingEvent, AccountingPeriod, SourceConsumption, PluginState)
from pmo_plugins.contracts import approval_services as contracts
from pmo_plugins.contracts.party_services import create_contact
from pmo_plugins.delivery import services as delivery
from pmo_plugins.delivery.models import Acceptance, CustomerConfirmationEvidence
from pmo_plugins.management_revenue import services as revenue
from pmo_plugins.management_revenue.models import RevenueRecognition, RevenueAdjustment, RevenueReceipt
from tests.test_v1_contracts import Fixture


class RevenueFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.finance = self.member('finance')
        self.contact = create_contact(self.owner, party_id=self.party.pk, name='验收联系人', request_token=uuid.uuid4())
        self.contract = self.make_contract()
        self.milestone = Milestone.objects.create(project=self.project, name='客户交付', due_date=self.today, criteria='客户确认')
        self.line = self.approved_line(101)

    def approved_line(self, cents):
        value = {'line_no': 1, 'net_cents': cents, 'tax_cents': 0, 'gross_cents': cents,
            'pricing_configuration': {'template': 'fixed_milestone', 'net_cap_cents': cents,
                'milestones': [{'milestone_id': str(self.milestone.pk), 'allocated_net_cents': cents}]}}
        version = contracts.save_version(self.owner, self.contract.pk, lines=[value], effective_on=self.today,
            contract_reference='合成合同批准依据', request_token=uuid.uuid4())
        version = contracts.submit_version(self.owner, version.pk, expected_version=version.version, request_token=uuid.uuid4())
        version = contracts.review_version(self.finance, version.pk, expected_version=version.version,
            request_token=uuid.uuid4(), decision='approve', review_note='独立核对原合同')
        return version.lines.get()

    def line_ref(self):
        return {'namespace': 'contracts', 'kind': 'contract_line', 'id': str(self.line.pk), 'version': self.line.version}

    def acceptance(self, start=0, end=10000, *, confirmed=True, replacement=None):
        row = delivery.save_acceptance_with_evidence(self.owner, contract_line_ref=self.line_ref(),
            contact_id=self.contact.pk, confirmed_on=self.today, channel='邮件', reference='演练确认编号',
            statement='客户确认本次交付范围', milestone_coverages=[{'milestone_id': str(self.milestone.pk),
                'from_basis_points': start, 'to_basis_points': end}], request_token=uuid.uuid4(),
            replaces_acceptance_ref=replacement, dry_run=False)
        if confirmed:
            row = delivery.submit_acceptance(self.owner, row.pk, expected_version=row.version, request_token=uuid.uuid4(), dry_run=False)
            row = delivery.review_acceptance(self.finance, row.pk, expected_version=row.version,
                decision='approve', note='核对客户真实确认依据', request_token=uuid.uuid4(), dry_run=False)
        return row

    def recognize(self, row, **changes):
        values = {'expected_version': row.version, 'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return revenue.recognize_acceptance(self.owner, row.pk, **values)

    def reverse(self, row, **changes):
        values = {'reason': '原事实登记错误，完整冲销', 'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return revenue.reverse_recognition(self.owner, row.pk, **values)

    def facts(self):
        return tuple(model.objects.count() for model in (Acceptance, CustomerConfirmationEvidence,
            RevenueRecognition, RevenueAdjustment, RevenueReceipt, AccountingEvent, SourceConsumption,
            AccountingPeriod, AuditEvent, DomainEvent, LedgerEntry, LedgerPosting))

    def denied(self, call):
        before = self.facts()
        with self.assertRaises((ValidationError, PermissionDenied)):
            call()
        self.assertEqual(before, self.facts())


class AcceptanceRevenueTests(RevenueFixture, TestCase):
    def test_full_chain_rounding_one_owner_no_cost_cash(self):
        before = LedgerEntry.objects.count(), LedgerPosting.objects.count()
        recognitions = [self.recognize(self.acceptance(a, b)) for a,b in [(0,3333),(3333,6667),(6667,10000)]]
        self.assertEqual([r.recognized_net_cents for r in recognitions], [34,33,34])
        self.assertEqual(revenue.project_revenue_summary(self.pm,self.project.pk)['management_revenue_cents'],101)
        self.assertEqual(before, (LedgerEntry.objects.count(), LedgerPosting.objects.count()))
        for row in recognitions:
            event = row.accounting_event
            self.assertEqual(event.claim.source['id'],str(row.acceptance_id))
            self.assertEqual(event.claim.consumer,'management_revenue')
            self.assertIsNone(event.labor_origin_ref)
            self.assertEqual(event.claim_effect_cents,row.recognized_net_cents)
            self.assertEqual(row.occurred_on,self.today)
            self.assertEqual(row.posting_period,self.today.strftime('%Y-%m'))
        self.assertEqual(len(revenue.revenue_export_rows(self.pm,self.project.pk)),3)

    def test_dry_run_no_period_receipt_or_outbox(self):
        source = self.acceptance()
        before = self.facts()
        result = self.recognize(source,dry_run=True)
        self.assertTrue(result['dry_run'])
        self.assertEqual(before,self.facts())
        result = revenue.recognize_acceptance(self.owner,source.pk,expected_version=source.version,request_token=uuid.uuid4())
        self.assertTrue(result['dry_run'])
        self.assertEqual(before,self.facts())

    def test_idempotency_terminal_and_hash_conflict(self):
        source=self.acceptance(); token=uuid.uuid4()
        row=self.recognize(source,request_token=token)
        before=self.facts()
        self.assertEqual(self.recognize(source,request_token=token).pk,row.pk)
        self.assertEqual(before,self.facts())
        self.denied(lambda:self.recognize(source))
        self.denied(lambda:self.recognize(source,expected_version=source.version+1,request_token=token))
        reverse_token=uuid.uuid4(); adjustment=self.reverse(row,request_token=reverse_token)
        self.assertEqual(self.reverse(row,request_token=reverse_token).pk,adjustment.pk)
        self.denied(lambda:self.reverse(row,reason='改变输入',request_token=reverse_token))
        self.denied(lambda:self.recognize(source))
        self.denied(lambda:self.reverse(row))
        row.refresh_from_db();self.assertEqual(row.status,'posted');self.assertTrue(row.is_reversed)
        self.assertEqual(revenue.project_revenue_summary(self.owner,self.project.pk)['management_revenue_cents'],0)

    def test_roles_and_module_state_does_not_hide_dependency(self):
        source=self.acceptance()
        for member in [self.pm,self.member('member'),self.member('auditor')]:
            self.denied(lambda:revenue.recognize_acceptance(member,source.pk,expected_version=source.version,request_token=uuid.uuid4(),dry_run=False))
        row=self.recognize(source)
        PluginState.objects.update_or_create(organization=self.org,plugin_id='management_revenue',defaults={'enabled':False,'changed_by':self.owner.user})
        self.assertTrue(revenue.acceptance_income_state(self.owner,source.pk)['has_recognition'])
        self.denied(lambda:self.reverse(row))

    def test_draft_bad_versions_and_bool_inputs(self):
        draft=self.acceptance(confirmed=False)
        self.denied(lambda:self.recognize(draft))
        source=self.acceptance()
        for value in [True,1.0,0,-1]:
            self.denied(lambda:self.recognize(source,expected_version=value))
        for value in [1,0,'false',None]:
            self.denied(lambda:self.recognize(source,dry_run=value))
        for value in [True,1.5,-1,10**12+1]:
            with self.assertRaises(ValidationError):revenue._amount(value)

    def test_locking_and_reverse_original_period(self):
        source=self.acceptance();row=self.recognize(source)
        before=self.facts(); preview=self.reverse(row,dry_run=True)
        self.assertEqual(preview['occurred_on'],self.today.isoformat());self.assertEqual(before,self.facts())
        period=AccountingPeriod.objects.get(organization=self.org,period=row.posting_period)
        period.status='posted';period.closed_through=self.today;period.save()
        self.denied(lambda:self.reverse(row))

    def test_org_lock_rejects_new_income_without_half_write(self):
        source=self.acceptance();self.org.locked_through=self.today;self.org.save()
        self.denied(lambda:self.recognize(source))

    def test_atomic_bridge_failure_rolls_back_candidate(self):
        source=self.acceptance()
        before=self.facts()
        with mock.patch('core.revenue_accounting.post_revenue_recognition',side_effect=ValidationError('模拟桥失败')):
            with self.assertRaises(ValidationError):self.recognize(source)
        self.assertEqual(before,self.facts())

    def test_zero_amount_is_consumed_and_terminal(self):
        # Cumulative rounding can produce an actual zero-valued confirmed coverage.
        source=self.acceptance(0,1);self.assertEqual(source.accepted_net_cents,0)
        row=self.recognize(source);self.assertEqual(row.recognized_net_cents,0)
        self.denied(lambda:self.recognize(source));adjustment=self.reverse(row)
        self.assertEqual(adjustment.adjustment_cents,0)
        self.denied(lambda:self.recognize(source));self.denied(lambda:self.reverse(row))

    def test_replacement_after_explicit_reversal_retains_chain(self):
        source=self.acceptance();row=self.recognize(source)
        self.denied(lambda:delivery.reverse_acceptance(self.finance,source.pk,expected_version=source.version,
            reason='纠错',request_token=uuid.uuid4(),dry_run=False))
        self.reverse(row)
        oldref=delivery.source_ref(source)
        source=delivery.reverse_acceptance(self.finance,source.pk,expected_version=source.version,
            reason='已先反收入',request_token=uuid.uuid4(),dry_run=False)
        # Reference version is historical current terminal source for replacement validation.
        new=self.acceptance(replacement=delivery.source_ref(source))
        self.assertEqual(new.replaces_acceptance_id,source.pk)
        self.assertEqual(self.recognize(new).recognized_net_cents,101)
        self.denied(lambda:self.recognize(source))

    def test_posted_model_is_immutable_and_summary_hides_unassigned_pm(self):
        row=self.recognize(self.acceptance());row.recognized_net_cents=102
        with self.assertRaises(ValidationError):row.save()
        other=self.member('pm')
        self.denied(lambda:revenue.project_revenue_summary(other,self.project.pk))

    def test_canonical_event_envelopes_follow_real_posting_once(self):
        source=self.acceptance(); row=self.recognize(source); adjustment=self.reverse(row)
        for fact,topic,event_source in [(row,'management_revenue.recognized.v1',row.source_ref),
                (adjustment,'core.posting_reversed.v1',{'namespace':'core','kind':'accounting_event',
                    'id':str(adjustment.accounting_event_id),'version':1})]:
            events=DomainEvent.objects.filter(source__entity_id=str(fact.pk))
            self.assertEqual(events.count(),1)
            event=events.get()
            self.assertEqual(event.topic,topic)
            self.assertEqual(event.data['event_id'],str(event.event_id))
            self.assertEqual(event.data['organization_id'],str(self.org.pk))
            self.assertEqual(event.data['schema_version'],1)
            self.assertEqual(event.data['source'],event_source)
            self.assertEqual(event.data['payload']['changed_refs'],[fact.source_ref])
            self.assertEqual(event.data['payload']['posting_event_refs'],[{'namespace':'core',
                'kind':'accounting_event','id':str(fact.accounting_event_id),'version':1}])
            self.assertEqual(fact.accounting_event.source,fact.source_ref)
            self.assertEqual(fact.accounting_event.claim.source['id'],str(source.pk))

    @skipUnless(importlib.util.find_spec('jsonschema') is not None and importlib.util.find_spec('referencing') is not None,
                'Strict schema tooling is optional in the business runtime; financial-chain tests remain unconditional')
    def test_canonical_events_validate_strictly(self):
        import runpy
        from pathlib import Path
        api=runpy.run_path(str(Path(__file__).resolve().parents[1]/'docs/v1.0-development/contracts/validate_contracts.py'))
        schemas,registry=api['load']()
        row=self.recognize(self.acceptance());self.reverse(row)
        for topic in ['management_revenue.recognized.v1','core.posting_reversed.v1']:
            event=DomainEvent.objects.filter(topic=topic).get()
            api['validator']({'$ref':schemas['events.schema.json']['$id']+'#/$defs/'+topic},registry).validate(event.data)

    def test_future_and_mutated_contract_price_are_rejected(self):
        source=self.acceptance()
        # A corrupt current contract line must never silently change the approved basis.
        from pmo_plugins.contracts.models import ContractLine
        ContractLine.objects.filter(pk=self.line.pk).update(net_cents=102,gross_cents=102)
        self.denied(lambda:self.recognize(source))

    def test_reversal_retains_original_actor_date_amount_and_source(self):
        source=self.acceptance(); row=self.recognize(source)
        original=deepcopy(row.performance_ref)
        adjustment=self.reverse(row)
        self.assertEqual(adjustment.adjustment_cents,-101)
        self.assertEqual(adjustment.occurred_on,row.occurred_on)
        self.assertEqual(adjustment.posting_period,row.posting_period)
        self.assertEqual(adjustment.accounting_event.reverses_event_id,row.accounting_event_id)
        self.assertEqual(adjustment.accounting_event.claim_id,row.accounting_event.claim_id)
        self.assertEqual(adjustment.accounting_event.claim_effect_cents,-101)
        row.refresh_from_db();self.assertEqual(row.performance_ref,original)
        self.assertEqual(row.created_by_id,self.owner.pk)

    def test_all_read_roles_and_member_deny(self):
        source=self.acceptance();self.recognize(source)
        for actor in [self.owner,self.finance,self.pm,self.member('auditor')]:
            self.assertTrue(revenue.acceptance_income_state(actor,source.pk)['has_recognition'])
            self.assertEqual(revenue.project_revenue_summary(actor,self.project.pk)['management_revenue_cents'],101)
        member=self.member('member')
        self.denied(lambda:revenue.acceptance_income_state(member,source.pk))
        self.denied(lambda:revenue.revenue_export_rows(member,self.project.pk))

    def test_explicit_default_dry_run_hash_canonical_json(self):
        a={'accepted_net_cents':101,'policy':{'id':'x','version':1}}
        b={'policy':{'version':1,'id':'x'},'accepted_net_cents':101}
        self.assertEqual(revenue._digest(a),revenue._digest(b))
        self.assertNotEqual(revenue._digest(a),revenue._digest(dict(a,accepted_net_cents=102)))

    def test_company_pending_counts_zero_recognition_and_export_trace(self):
        draft=self.acceptance(0,1,confirmed=False)
        summary=revenue.project_revenue_summary(self.owner)
        self.assertIsNone(summary['project_id'])
        self.assertEqual(summary['pending_acceptance_count'],1)
        # Withdraw this overlapping draft before creating actual confirmed coverage.
        delivery.withdraw_acceptance(self.owner,draft.pk,expected_version=draft.version,
            reason='重复演练草稿',request_token=uuid.uuid4(),dry_run=False)
        source=self.acceptance(0,1)
        self.assertEqual(revenue.project_revenue_summary(self.owner)['unrecognized_acceptance_count'],1)
        row=self.recognize(source)
        self.assertEqual(row.recognized_net_cents,0)
        self.assertEqual(revenue.project_revenue_summary(self.owner)['unrecognized_acceptance_count'],0)
        adjustment=self.reverse(row)
        summary=revenue.project_revenue_summary(self.pm)
        self.assertEqual(summary['reversed_pending_acceptance_count'],1)
        export=revenue.revenue_export_rows(self.finance)
        self.assertEqual(len(export),2)
        reverse=next(item for item in export if item['kind']=='revenue_adjustment')
        self.assertEqual(reverse['recognition_id'],str(row.pk))
        self.assertEqual(reverse['acceptance_id'],str(source.pk))
        self.assertEqual(reverse['contract_version_id'],str(self.line.contract_version_id))
        self.assertEqual(reverse['contract_line_id'],str(self.line.pk))
        self.assertEqual(reverse['accounting_event_id'],str(adjustment.accounting_event_id))
        self.assertEqual(reverse['reverses_event_id'],str(row.accounting_event_id))

    def test_delivery_non_bool_dry_run_all_mutation_boundaries(self):
        from pmo_plugins.delivery.models import AcceptanceCoverage, DeliveryReceipt, AcceptanceReversal
        def all_facts():
            return self.facts() + tuple(model.objects.count() for model in
                (AcceptanceCoverage,DeliveryReceipt,AcceptanceReversal))
        evidence_args={'contract_line_ref':self.line_ref(),'contact_id':self.contact.pk,
            'confirmed_on':self.today,'channel':'邮件','reference':'演练严格布尔',
            'statement':'客户明确确认本次范围','request_token':uuid.uuid4()}
        evidence=delivery.register_customer_evidence(self.owner,**evidence_args,dry_run=False)
        coverage=[{'milestone_id':str(self.milestone.pk),'from_basis_points':0,'to_basis_points':10000}]
        save_args={'contract_line_ref':self.line_ref(),'confirmed_on':self.today,
            'evidence':[delivery.evidence_ref(evidence)],'milestone_coverages':coverage,
            'request_token':uuid.uuid4()}
        draft=delivery.save_acceptance(self.owner,**save_args,dry_run=False)
        wrapper_args={**evidence_args,'milestone_coverages':coverage}
        for value in [0,0.0,None,'',[],{},1,1.0,'false']:
            calls=[lambda:delivery.register_customer_evidence(self.owner,**dict(evidence_args,request_token=uuid.uuid4()),dry_run=value),
                lambda:delivery.save_acceptance(self.owner,**dict(save_args,request_token=uuid.uuid4()),dry_run=value),
                lambda:delivery.submit_acceptance(self.owner,draft.pk,expected_version=draft.version,
                    request_token=uuid.uuid4(),dry_run=value),
                lambda:delivery.save_acceptance_with_evidence(self.owner,**dict(wrapper_args,request_token=uuid.uuid4()),dry_run=value)]
            for call in calls:
                before=all_facts()
                with self.assertRaises(ValidationError):call()
                self.assertEqual(before,all_facts(),repr(value))
        submitted=delivery.submit_acceptance(self.owner,draft.pk,expected_version=draft.version,
            request_token=uuid.uuid4(),dry_run=False)
        for value in [0,0.0,None]:
            before=all_facts()
            with self.assertRaises(ValidationError):
                delivery.review_acceptance(self.finance,submitted.pk,expected_version=submitted.version,
                    request_token=uuid.uuid4(),decision='approve',note='独立核对',dry_run=value)
            self.assertEqual(before,all_facts())

    def test_delivery_default_preview_chain_including_wrapper_has_zero_durable_effect(self):
        from pmo_plugins.delivery.models import AcceptanceCoverage, DeliveryReceipt
        def all_facts():
            return self.facts()+(AcceptanceCoverage.objects.count(),DeliveryReceipt.objects.count())
        evidence_args={'contract_line_ref':self.line_ref(),'contact_id':self.contact.pk,
            'confirmed_on':self.today,'channel':'邮件','reference':'预览依据',
            'statement':'仅预览明确确认事实','request_token':uuid.uuid4()}
        before=all_facts()
        delivery.register_customer_evidence(self.owner,**evidence_args)
        self.assertEqual(before,all_facts())
        coverage=[{'milestone_id':str(self.milestone.pk),'from_basis_points':0,'to_basis_points':10000}]
        delivery.save_acceptance_with_evidence(self.owner,**dict(evidence_args,request_token=uuid.uuid4()),milestone_coverages=coverage)
        self.assertEqual(before,all_facts())
        # Each subsequent preview starts from a real authorized source but adds no durable fact.
        evidence=delivery.register_customer_evidence(self.owner,**dict(evidence_args,request_token=uuid.uuid4()),dry_run=False)
        save_args={'contract_line_ref':self.line_ref(),'confirmed_on':self.today,
            'evidence':[delivery.evidence_ref(evidence)],'milestone_coverages':coverage,'request_token':uuid.uuid4()}
        before=all_facts();delivery.save_acceptance(self.owner,**save_args)
        self.assertEqual(before,all_facts())
        draft=delivery.save_acceptance(self.owner,**dict(save_args,request_token=uuid.uuid4()),dry_run=False)
        before=all_facts();delivery.submit_acceptance(self.owner,draft.pk,expected_version=draft.version,request_token=uuid.uuid4())
        self.assertEqual(before,all_facts());draft.refresh_from_db();self.assertEqual(draft.status,'draft')
        submitted=delivery.submit_acceptance(self.owner,draft.pk,expected_version=draft.version,request_token=uuid.uuid4(),dry_run=False)
        before=all_facts();delivery.review_acceptance(self.finance,submitted.pk,expected_version=submitted.version,
            request_token=uuid.uuid4(),decision='approve',note='独立核对预览')
        self.assertEqual(before,all_facts());submitted.refresh_from_db();self.assertEqual(submitted.status,'submitted')


    def test_new_business_dependency_guard_blocks_contract_disable_without_half_write(self):
        from core.plugin_runtime import set_enabled, is_enabled
        set_enabled(self.owner, 'invoicing', False)
        self.assertTrue(is_enabled(self.org,'delivery'))
        self.assertTrue(is_enabled(self.org,'management_revenue'))
        before=self.facts()+(PluginState.objects.count(),)
        with self.assertRaises(ValidationError):set_enabled(self.owner,'contracts',False)
        self.assertEqual(before,self.facts()+(PluginState.objects.count(),))
        self.assertTrue(is_enabled(self.org,'contracts'))
        set_enabled(self.owner,'management_revenue',False)
        before=self.facts()+(PluginState.objects.count(),)
        with self.assertRaises(ValidationError):set_enabled(self.owner,'contracts',False)
        self.assertEqual(before,self.facts()+(PluginState.objects.count(),))
        self.assertTrue(is_enabled(self.org,'contracts'))
        # PH-226: disable the newer settlement dependant before its delivery/contracts dependencies.
        from core.plugin_runtime import plugin_catalog
        set_enabled(self.owner, 'onboarding', False)
        if any(spec['id'] == 'settlement' for spec in plugin_catalog()):
            set_enabled(self.owner, 'settlement', False)
        if any(spec['id'] == 'time' for spec in plugin_catalog()):
            set_enabled(self.owner, 'time', False)
        set_enabled(self.owner,'delivery',False)
        set_enabled(self.owner,'contracts',False)
        self.assertFalse(is_enabled(self.org,'contracts'))
