"""PH-224 customer settlement, frozen open items and read-only aging regressions."""
import uuid
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingEvent, AccountingPeriod, AuditEvent, DomainEvent, LedgerEntry,
                         LedgerPosting, Membership, Milestone, Organization, PluginState, Project,
                         SourceConsumption)
from pmo_plugins.contracts import approval_services as contracts
from pmo_plugins.contracts.party_services import create_contact
from pmo_plugins.delivery import services as delivery
from pmo_plugins.delivery.models import Acceptance
from pmo_plugins.management_revenue.models import RevenueRecognition
from pmo_plugins.settlement import aging_services as aging
from pmo_plugins.settlement import receivable_services as receivable
from pmo_plugins.settlement.models import OpenItem, Settlement, SettlementReceipt
from tests.test_v1_contracts import Fixture


class SettlementFixture(Fixture):
    """Real approved fixed_milestone contract, terms, confirmations and settlement workflow."""

    today = timezone.localdate()

    def setUp(self):
        super().setUp()
        self.finance = self.member('finance')
        self.auditor = self.member('auditor')
        self.plain = self.member('member')
        self.contact = create_contact(self.owner, party_id=self.party.pk, name='结算联系人',
                                      request_token=uuid.uuid4())
        self.milestone = Milestone.objects.create(project=self.project, name='客户交付',
                                                  due_date=self.today, criteria='客户确认')
        self.contract = self.make_contract()
        self.line = None

    # ---------- approved contract line and frozen terms ----------
    def golden_terms(self, release=None):
        release = release or (self.today + timedelta(days=300))
        payments = [
            {'key': 'p50', 'condition_type': 'unconditional', 'gross_cents': 53000,
             'due_date': (self.today - timedelta(days=5)).isoformat()},
            {'key': 'p40', 'condition_type': 'conditional', 'gross_cents': 42400,
             'due_date': (self.today - timedelta(days=40)).isoformat(),
             'release_condition': '交付确认后到期'},
            {'key': 'p10', 'condition_type': 'warranty', 'gross_cents': 10600,
             'release_condition': '质保期内无重大缺陷'},
        ]
        warranties = [{'payment_key': 'p10', 'gross_cents': 10600,
                       'release_condition': '质保期满无重大缺陷',
                       'release_on': release.isoformat()}]
        return payments, warranties

    def approve_golden(self, **changes):
        payments, warranties = self.golden_terms()
        return self.approve_line(payments=payments, warranties=warranties, **changes)

    def approve_line(self, *, net=100000, tax=6000, payments=None, warranties=None,
                     contract=None, memo='结算演练批准依据'):
        gross = net + tax
        pricing = {'template': 'fixed_milestone', 'net_cap_cents': net,
                   'milestones': [{'milestone_id': str(self.milestone.pk),
                                   'allocated_net_cents': net}]}
        value = {'line_no': 1, 'net_cents': net, 'tax_cents': tax, 'gross_cents': gross,
                 'pricing_configuration': pricing, 'payment_terms': payments or [],
                 'warranty_terms': warranties or []}
        contract = contract or self.contract
        version = contracts.save_version(self.owner, contract.pk, lines=[value],
            effective_on=self.today, contract_reference=memo, request_token=uuid.uuid4())
        version = contracts.submit_version(self.owner, version.pk,
            expected_version=version.version, request_token=uuid.uuid4())
        version = contracts.review_version(self.finance, version.pk,
            expected_version=version.version, request_token=uuid.uuid4(),
            decision='approve', review_note='独立核对合同价款与付款条款')
        self.line = version.lines.get()
        return self.line

    def line_ref(self, line=None):
        line = line or self.line
        return {'namespace': 'contracts', 'kind': 'contract_line', 'id': str(line.pk),
                'version': line.version}

    def terms(self, line=None):
        return contracts.approved_terms_snapshot(self.owner, self.line_ref(line), self.today)['terms']

    def term_value(self, term):
        return term['term_ref']['kind'] + ':' + term['id']

    def term_values(self, *kinds, line=None):
        wanted = set(kinds)
        return [self.term_value(term) for term in self.terms(line)
                if term['term_ref']['kind'] in wanted]

    # ---------- real confirmed performance ----------
    def acceptance(self, start, end, *, line=None, confirmed_on=None):
        line = line or self.line
        row = delivery.save_acceptance_with_evidence(self.owner,
            contract_line_ref=self.line_ref(line), contact_id=self.contact.pk,
            confirmed_on=confirmed_on or self.today, channel='邮件', reference='演练结算确认',
            statement='客户确认本次交付范围已完成', request_token=uuid.uuid4(),
            milestone_coverages=[{'milestone_id': str(self.milestone.pk),
                                  'from_basis_points': start, 'to_basis_points': end}],
            dry_run=False)
        row = delivery.submit_acceptance(self.owner, row.pk, expected_version=row.version,
                                        request_token=uuid.uuid4(), dry_run=False)
        return delivery.review_acceptance(self.finance, row.pk, expected_version=row.version,
            decision='approve', note='独立核对客户真实确认依据', request_token=uuid.uuid4(),
            dry_run=False)

    # ---------- settlement workflow ----------
    def save(self, term_refs, *, line=None, member=None, **changes):
        values = {'contract_line_ref': self.line_ref(line), 'term_refs': term_refs,
                  'settled_on': self.today,
                  'evidence': [{'reference': '按批准条款与已确认履约结算'}],
                  'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return receivable.save_settlement(member or self.owner, **values)

    def submit(self, row, *, member=None, **changes):
        values = {'expected_version': row.version, 'request_token': uuid.uuid4(), 'dry_run': False}
        values.update(changes)
        return receivable.submit_settlement(member or self.owner, row.pk, **values)

    def approve(self, row, *, member=None, **changes):
        values = {'expected_version': row.version, 'request_token': uuid.uuid4(),
                  'decision': 'approve', 'note': '独立核对批准条款与已确认履约', 'dry_run': False}
        values.update(changes)
        return receivable.review_settlement(member or self.finance, row.pk, **values)

    def settle(self, term_refs, **changes):
        return self.approve(self.submit(self.save(term_refs, **changes)))

    # ---------- facts ----------
    def facts(self):
        return tuple(model.objects.count() for model in (
            Settlement, OpenItem, SettlementReceipt, SourceConsumption, DomainEvent, AuditEvent,
            LedgerEntry, LedgerPosting, AccountingEvent, RevenueRecognition, Acceptance))

    def denied(self, call, exceptions=(ValidationError, PermissionDenied)):
        before = self.facts()
        with self.assertRaises(exceptions):
            call()
        self.assertEqual(before, self.facts())
        return True

    def assert_db_denied(self, call):
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                call()


class SettlementLifecycleTests(SettlementFixture, TestCase):
    def test_full_chain_half_then_due_then_retention_cumulative_and_conservation(self):
        self.approve_golden()
        no_cash = (LedgerEntry.objects.count(), LedgerPosting.objects.count(),
                   AccountingEvent.objects.count(), RevenueRecognition.objects.count())

        self.acceptance(0, 5000)
        first = self.settle(self.term_values('payment_term')[:1])
        self.assertEqual((first.status, first.version), ('active', 3))
        self.assertEqual((first.gross_cents, first.net_cents, first.tax_cents), (53000, 50000, 3000))
        self.assertEqual(first.due_date, self.today - timedelta(days=5))
        self.assertEqual(first.approval_audit.action, 'settlement.settlement.activated')
        self.assertFalse(first.approval_audit.action.startswith('plugin.'))
        self.assertEqual(first.approved_version, first.version)
        items = list(first.items.all())
        self.assertEqual(len(items), 1)
        term = self.terms()[0]
        self.assertEqual(items[0].partition_key, 'due:' + term['id'])
        self.assertEqual((items[0].gross_cents, items[0].tax_cents), (53000, 3000))
        self.assertEqual(items[0].due_date, self.today - timedelta(days=5))
        self.assertEqual(items[0].source, first.source_ref)
        self.assertEqual(items[0].payee_ref, {'kind': 'counterparty', 'id': str(self.party.pk)})
        self.assertEqual(items[0].scope, {'kind': 'project', 'id': str(self.project.pk)})
        claim = SourceConsumption.objects.get(consumer='settlement', coverage_key='term:due:' + term['id'])
        self.assertEqual((claim.source_id, claim.amount_cents), (self.line.pk, 53000))
        event = DomainEvent.objects.get(topic='settlement.open_item_created.v1')
        self.assertEqual(event.data['source'], items[0].item_ref)
        self.assertEqual(event.data['payload']['changed_refs'], [items[0].item_ref])
        self.assertEqual(event.data['payload']['posting_event_refs'], [])
        self.assertFalse(DomainEvent.objects.filter(topic='settlement.open_item.created.v1').exists())

        self.acceptance(5000, 9000)
        second = self.settle(self.term_values('payment_term')[1:2])
        self.assertEqual((second.gross_cents, second.net_cents, second.tax_cents), (42400, 40000, 2400))
        self.assertEqual(second.due_date, self.today - timedelta(days=40))

        self.acceptance(9000, 10000)
        retention = self.settle(self.term_values('warranty_term'))
        self.assertEqual((retention.gross_cents, retention.net_cents, retention.tax_cents),
                         (10600, 10000, 600))
        self.assertEqual(retention.due_date, self.today + timedelta(days=300))
        retention_item = retention.items.get()
        self.assertEqual(retention_item.partition_kind, 'retention')
        self.assertEqual(retention_item.partition_key, 'retention:' + self.terms()[2]['id'])
        self.assertEqual(retention_item.due_date, self.today + timedelta(days=300))

        rows = Settlement.objects.filter(contract_line=self.line, status='active')
        self.assertEqual(sum(row.net_cents for row in rows), 100000)
        self.assertEqual(sum(row.tax_cents for row in rows), 6000)
        self.assertEqual(sum(row.gross_cents for row in rows), 106000)
        self.assertEqual(sum(OpenItem.objects.values_list('gross_cents', flat=True)), 106000)
        self.assertEqual(sum(OpenItem.objects.values_list('tax_cents', flat=True)), 6000)
        self.assertEqual(SourceConsumption.objects.filter(consumer='settlement').count(), 3)
        self.assertEqual(DomainEvent.objects.filter(topic='settlement.open_item_created.v1').count(), 3)
        self.assertEqual(no_cash, (LedgerEntry.objects.count(), LedgerPosting.objects.count(),
                                   AccountingEvent.objects.count(), RevenueRecognition.objects.count()))

        snapshot = receivable.settlement_snapshot(self.pm, first.pk)
        self.assertEqual(snapshot['status'], 'active')
        self.assertTrue(snapshot['is_canonical'])
        self.assertEqual(snapshot['items'][0]['partition_key'], items[0].partition_key)
        self.assertEqual(snapshot['items'][0]['source_display'], snapshot['source_display'])
        self.assertEqual(snapshot['source_display'],
                         'delivery · acceptance · ' + first.source_ref['id'] + ' · v'
                         + str(first.source_ref['version']))
        read_items = receivable.open_items_for_settlement(self.auditor, first.pk)
        self.assertEqual(len(read_items), 1)
        self.assertEqual(read_items[0]['source_display'], snapshot['source_display'])

    def test_draft_only_registers_and_submit_approve_state_machine(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        term_refs = self.term_values('payment_term')[:1]
        draft = self.save(term_refs)
        self.assertEqual(draft.status, 'draft')
        self.assertEqual(OpenItem.objects.count(), 0)
        self.assertEqual(SourceConsumption.objects.count(), 0)
        self.assertEqual(receivable.open_items_for_settlement(self.owner, draft.pk), [])
        submitted = self.submit(draft)
        self.assertEqual((submitted.status, submitted.submitted_by_id), ('submitted', self.owner.pk))
        self.assertEqual(OpenItem.objects.count(), 0)
        active = self.approve(submitted)
        self.assertEqual(active.status, 'active')
        self.assertEqual(OpenItem.objects.count(), 1)
        self.assertEqual(SettlementReceipt.objects.filter(action='review').count(), 1)
        topics = set(DomainEvent.objects.values_list('topic', flat=True))
        self.assertIn('settlement.settlement.saved.v1', topics)
        self.assertIn('settlement.settlement.submitted.v1', topics)
        self.assertIn('settlement.settlement.activated.v1', topics)
        self.assertIn('settlement.open_item_created.v1', topics)

    def test_withdraw_and_terminal_states(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        term_refs = self.term_values('payment_term')[:1]
        draft = self.save(term_refs)
        withdrawn = receivable.withdraw_settlement(self.owner, draft.pk, expected_version=draft.version,
            request_token=uuid.uuid4(), reason='客户条款选择错误，撤回重登', dry_run=False)
        self.assertEqual(withdrawn.status, 'withdrawn')
        self.assertEqual(withdrawn.review_note, '客户条款选择错误，撤回重登')
        self.denied(lambda: self.submit(withdrawn))
        self.denied(lambda: receivable.withdraw_settlement(self.owner, withdrawn.pk,
            expected_version=withdrawn.version, request_token=uuid.uuid4(), reason='再次撤回', dry_run=False))
        submitted = self.submit(self.save(term_refs))
        withdrawn2 = receivable.withdraw_settlement(self.owner, submitted.pk,
            expected_version=submitted.version, request_token=uuid.uuid4(), reason='待审阶段撤回', dry_run=False)
        self.assertEqual(withdrawn2.status, 'withdrawn')
        active = self.settle(term_refs)
        self.denied(lambda: receivable.withdraw_settlement(self.finance, active.pk,
            expected_version=active.version, request_token=uuid.uuid4(), reason='生效后撤回', dry_run=False))

    def test_return_and_reject_keep_draft_and_allow_resubmit(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        term_refs = self.term_values('payment_term')[:1]
        row = self.submit(self.save(term_refs))
        returned = receivable.review_settlement(self.finance, row.pk, expected_version=row.version,
            request_token=uuid.uuid4(), decision='return', note='请补充客户结算依据',
            dry_run=False)
        self.assertEqual(returned.status, 'draft')
        resubmitted = self.submit(returned)
        rejected = receivable.review_settlement(self.finance, resubmitted.pk,
            expected_version=resubmitted.version, request_token=uuid.uuid4(),
            decision='reject', note='条款选择与客户确认不一致', dry_run=False)
        self.assertEqual(rejected.status, 'draft')
        active = self.approve(self.submit(rejected))
        self.assertEqual(active.status, 'active')
        self.assertEqual(OpenItem.objects.count(), 1)
        self.denied(lambda: receivable.review_settlement(self.finance, row.pk,
            expected_version=row.version, request_token=uuid.uuid4(), decision='approve',
            note='过期版本', dry_run=False))

    def test_self_review_roles_and_independence(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        term_refs = self.term_values('payment_term')[:1]
        for actor in (self.plain, self.pm, self.auditor):
            self.denied(lambda actor=actor: self.save(term_refs, member=actor))
        row = self.submit(self.save(term_refs))
        self.denied(lambda: self.approve(row, member=self.owner))
        for actor in (self.plain, self.pm, self.auditor):
            self.denied(lambda actor=actor: self.approve(row, member=actor))
        self.assertEqual(self.approve(row, member=self.finance).status, 'active')
        for actor in (self.owner, self.finance, self.pm, self.auditor):
            self.assertTrue(receivable.settlement_snapshot(actor, row.pk)['id'])
        self.denied(lambda: receivable.settlement_snapshot(self.plain, row.pk))
        self.denied(lambda: receivable.open_items_for_settlement(self.plain, row.pk))

    def test_evidence_text_is_persisted_and_derived_refs_are_merged(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        draft = self.save(self.term_values('payment_term')[:1],
                          evidence=[{'reference': '客户邮件确认 2026-10 结算 50% 到期款'}])
        note = draft.terms_snapshot['evidence_note']
        self.assertEqual(note, '客户邮件确认 2026-10 结算 50% 到期款')
        self.assertEqual({ref['kind'] for ref in draft.evidence},
                         {'approval', 'customer_confirmation'})
        self.assertTrue(all(set(ref) <= {'evidence_id', 'sha256', 'kind'}
                            for ref in draft.evidence))
        snapshot = receivable.settlement_snapshot(self.owner, draft.pk)
        self.assertEqual(snapshot['evidence'], draft.evidence)
        self.assertEqual(snapshot['evidence_note'], note)
        self.assertEqual(snapshot['evidence_display'], note)
        text = self.save(self.term_values('payment_term')[:1], evidence='纯文本结算依据')
        self.assertEqual(text.terms_snapshot['evidence_note'], '纯文本结算依据')
        empty = self.save(self.term_values('payment_term')[:1], evidence=[])
        self.assertEqual(empty.terms_snapshot['evidence_note'], '')
        self.assertEqual({ref['kind'] for ref in empty.evidence},
                         {'approval', 'customer_confirmation'})
        self.assertTrue(all(set(ref) <= {'evidence_id', 'sha256', 'kind'}
                            for ref in empty.evidence))
        self.denied(lambda: self.save(self.term_values('payment_term')[:1], evidence=[{'note': 'x'}]))


class SettlementRefusalTests(SettlementFixture, TestCase):
    def test_amount_direction_payee_scope_and_date_cannot_be_overridden(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        term_refs = self.term_values('payment_term')[:1]
        for field, value in [('gross_cents', 1), ('net_cents', 1), ('tax_cents', 1),
                             ('gross_cents', True), ('gross_cents', 53000.0),
                             ('net_cents', 50000.0), ('gross_cents', -1),
                             ('direction', 'payable'), ('due_date', self.today),
                             ('payee_ref', {'kind': 'counterparty', 'id': str(self.owner.pk)}),
                             ('scope', {'kind': 'nonproject', 'id': str(self.project.pk)})]:
            self.denied(lambda field=field, value=value: self.save(term_refs, **{field: value}))
        self.denied(lambda: self.save([self.term_values('payment_term')[0],
                                       self.term_values('payment_term')[0]]))
        accepted = self.save(term_refs, direction='receivable',
            payee_ref={'kind': 'counterparty', 'id': str(self.party.pk)},
            scope={'kind': 'project', 'id': str(self.project.pk)},
            gross_cents=53000, net_cents=50000, tax_cents=3000,
            due_date=self.today - timedelta(days=5))
        self.assertEqual((accepted.gross_cents, accepted.net_cents), (53000, 50000))

    def test_no_performance_and_over_confirmed_are_rejected(self):
        self.approve_golden()
        term_refs = self.term_values('payment_term')
        self.denied(lambda: self.save([term_refs[0]]))
        self.acceptance(0, 5000)
        self.denied(lambda: self.save(term_refs))
        active = self.settle([term_refs[0]])
        self.assertEqual(active.net_cents, 50000)
        self.denied(lambda: self.save([term_refs[1]]))
        self.acceptance(5000, 9000)
        self.assertEqual(self.settle([term_refs[1]]).net_cents, 40000)

    def test_terms_not_covering_full_gross_reject_whole_line(self):
        payments = [{'key': 'half', 'condition_type': 'unconditional', 'gross_cents': 53000,
                     'due_date': self.today.isoformat()}]
        self.approve_line(payments=payments, warranties=[])
        self.denied(lambda: contracts.approved_terms_snapshot(
            self.owner, self.line_ref(), self.today))
        self.denied(lambda: self.save(['payment_term:' + str(uuid.uuid4())]))
        self.denied(lambda: self.save([]))
        self.assertEqual(Settlement.objects.count(), 0)

    def test_consumed_term_and_duplicate_settlement_rejected(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        term_refs = self.term_values('payment_term')
        first = self.settle([term_refs[0]])
        self.denied(lambda: self.save([term_refs[0]]))
        self.denied(lambda: self.settle([term_refs[0]]))
        self.assertEqual(first.items.count(), 1)
        self.assertEqual(SourceConsumption.objects.filter(consumer='settlement').count(), 1)

    def test_future_locked_and_closed_period_reject_without_half_write(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        term_refs = self.term_values('payment_term')[:1]
        self.denied(lambda: self.save(term_refs, settled_on=self.today + timedelta(days=1)))
        self.denied(lambda: self.save(term_refs, settled_on='2026-1-1'))
        self.org.locked_through = self.today
        self.org.save()
        self.denied(lambda: self.save(term_refs))
        self.org.locked_through = None
        self.org.save()
        AccountingPeriod.objects.update_or_create(organization=self.org,
            period=self.today.strftime('%Y-%m'),
            defaults={'status': 'posted', 'closed_through': self.today})
        self.denied(lambda: self.save(term_refs))
        AccountingPeriod.objects.filter(organization=self.org,
            period=self.today.strftime('%Y-%m')).delete()
        row = self.save(term_refs)
        AccountingPeriod.objects.update_or_create(organization=self.org,
            period=self.today.strftime('%Y-%m'),
            defaults={'status': 'posted', 'closed_through': self.today})
        self.denied(lambda: self.approve(self.submit(row)))

    def test_duplicate_request_replays_same_result_and_changed_input_conflicts(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        term_refs = self.term_values('payment_term')[:1]
        token = uuid.uuid4()
        first = self.save(term_refs, request_token=token)
        before = self.facts()
        replay = self.save(term_refs, request_token=token)
        self.assertEqual(replay.pk, first.pk)
        self.assertEqual(before, self.facts())
        self.denied(lambda: self.save(self.term_values('payment_term')[1:], request_token=token))
        token2 = uuid.uuid4()
        submitted = receivable.submit_settlement(self.owner, first.pk,
            expected_version=first.version, request_token=token2, dry_run=False)
        self.assertEqual(submitted.status, 'submitted')
        again = receivable.submit_settlement(self.owner, first.pk,
            expected_version=first.version, request_token=token2, dry_run=False)
        self.assertEqual(again.pk, submitted.pk)
        self.denied(lambda: receivable.submit_settlement(self.owner, first.pk,
            expected_version=first.version + 1, request_token=token2, dry_run=False))

    def test_same_token_save_replays_after_period_lock_and_default_date(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        term_refs = self.term_values('payment_term')[:1]
        token = uuid.uuid4()
        first = self.save(term_refs, request_token=token)
        explicit_token = uuid.uuid4()
        explicit = self.save(term_refs, settled_on=self.today, request_token=explicit_token)
        self.assertEqual(Settlement.objects.count(), 2)
        self.org.locked_through = self.today
        self.org.save()
        replay = self.save(term_refs, request_token=token)
        self.assertEqual(replay.pk, first.pk)
        explicit_replay = self.save(term_refs, settled_on=self.today.isoformat(),
                                    request_token=explicit_token)
        self.assertEqual(explicit_replay.pk, explicit.pk)
        self.denied(lambda: self.save(term_refs, settled_on=self.today - timedelta(days=1),
                                      request_token=explicit_token))
        self.assertEqual(Settlement.objects.count(), 2)
        self.org.locked_through = None
        self.org.save()

    def test_bool_and_float_arguments_are_rejected_everywhere(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        row = self.save(self.term_values('payment_term')[:1])
        for value in (0, 1, 1.0, 'false', None, [], {}):
            self.denied(lambda value=value: receivable.save_settlement(self.owner,
                contract_line_ref=self.line_ref(), term_refs=self.term_values('payment_term')[:1],
                settled_on=self.today, request_token=uuid.uuid4(), dry_run=value))
            self.denied(lambda value=value: receivable.submit_settlement(self.owner, row.pk,
                expected_version=row.version, request_token=uuid.uuid4(), dry_run=value))
            self.denied(lambda value=value: receivable.review_settlement(self.finance, row.pk,
                expected_version=row.version, request_token=uuid.uuid4(), decision='approve',
                note='核对', dry_run=value))
            self.denied(lambda value=value: receivable.withdraw_settlement(self.owner, row.pk,
                expected_version=row.version, request_token=uuid.uuid4(),
                reason='原因', dry_run=value))
        for value in (True, 1.0, 0, -1, '1'):
            self.denied(lambda value=value: self.submit(row, expected_version=value))

    def test_dry_run_all_entries_have_no_durable_effect(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        term_refs = self.term_values('payment_term')[:1]
        before = self.facts()
        preview = receivable.save_settlement(self.owner, contract_line_ref=self.line_ref(),
            term_refs=term_refs, settled_on=self.today, request_token=uuid.uuid4())
        self.assertTrue(preview['dry_run'] if isinstance(preview, dict) else True)
        self.assertEqual(before, self.facts())
        row = self.save(term_refs)
        before = self.facts()
        receivable.submit_settlement(self.owner, row.pk,
            expected_version=row.version, request_token=uuid.uuid4())
        self.assertEqual(before, self.facts())
        submitted = self.submit(row)
        before = self.facts()
        receivable.review_settlement(self.finance, submitted.pk,
            expected_version=submitted.version, request_token=uuid.uuid4(),
            decision='approve', note='预览批准')
        receivable.withdraw_settlement(self.owner, submitted.pk,
            expected_version=submitted.version, request_token=uuid.uuid4(), reason='预览撤回')
        self.assertEqual(before, self.facts())
        submitted.refresh_from_db()
        self.assertEqual((submitted.status, submitted.version), ('submitted', 2))
        self.assertEqual(OpenItem.objects.count(), 0)

    def test_module_disable_keeps_history_readable_and_refuses_writes(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        active = self.settle(self.term_values('payment_term')[:1])
        PluginState.objects.update_or_create(organization=self.org, plugin_id='settlement',
            defaults={'enabled': False, 'changed_by': self.owner.user})
        self.assertEqual(receivable.settlement_snapshot(self.auditor, active.pk)['status'], 'active')
        self.assertEqual(len(receivable.open_items_for_settlement(self.owner, active.pk)), 1)
        self.assertIn('items', aging.open_item_aging(self.auditor))
        self.denied(lambda: self.save(self.term_values('payment_term')[1:]))
        self.denied(lambda: self.approve(active))
        self.denied(lambda: receivable.withdraw_settlement(self.owner, active.pk,
            expected_version=active.version, request_token=uuid.uuid4(), reason='停用撤回'))

    def test_membership_revocation_and_cross_project_refused(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        row = self.settle(self.term_values('payment_term')[:1])
        other_pm = self.member('pm')
        self.denied(lambda: receivable.settlement_snapshot(other_pm, row.pk))
        from django.contrib.auth import get_user_model
        other_org = Organization.objects.create(name='其他公司')
        other_owner = Membership.objects.create(organization=other_org, role='owner',
            user=get_user_model().objects.create_user(username='other' + uuid.uuid4().hex))
        self.denied(lambda: receivable.settlement_snapshot(other_owner, row.pk))
        self.denied(lambda: receivable.open_items_for_settlement(other_owner, row.pk))
        self.denied(lambda: receivable.withdraw_settlement(other_owner, row.pk,
            expected_version=row.version, request_token=uuid.uuid4(), reason='跨企业撤回'))
        Membership.objects.filter(pk=self.owner.pk).update(active=False)
        self.denied(lambda: self.save(self.term_values('payment_term')[1:]))
        Membership.objects.filter(pk=self.owner.pk).update(active=True)
        other_project = Project.objects.create(organization=self.org, code='P2', name='其他项目',
            manager=other_pm, start_date=self.today, end_date=self.today)
        self.denied(lambda: aging.open_item_aging(self.pm, project_id=other_project.pk))


class SettlementOpenItemIntegrityTests(SettlementFixture, TestCase):
    def approved_settlement(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        return self.settle(self.term_values('payment_term')[:1])

    def test_active_settlement_and_open_item_are_immutable(self):
        row = self.approved_settlement()
        row.gross_cents = row.gross_cents + 1
        with self.assertRaises(ValidationError):
            row.save()
        with self.assertRaises(ValidationError):
            row.delete()
        row.refresh_from_db()
        self.assertEqual(row.status, 'active')
        item = row.items.get()
        item.gross_cents = item.gross_cents + 1
        with self.assertRaises(ValidationError):
            item.save()
        with self.assertRaises(ValidationError):
            item.delete()
        self.assert_db_denied(lambda: Settlement.objects.filter(pk=row.pk).update(
            review_note='直接改库'))
        self.assert_db_denied(lambda: OpenItem.objects.filter(pk=item.pk).update(gross_cents=1))
        self.assert_db_denied(lambda: Settlement.objects.filter(pk=row.pk).delete())
        self.assert_db_denied(lambda: OpenItem.objects.filter(pk=item.pk).delete())
        self.assert_db_denied(lambda: SettlementReceipt.objects.filter(
            pk=SettlementReceipt.objects.first().pk).update(action='其他'))

    def test_contract_drift_between_draft_and_approval_is_rejected(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        row = self.submit(self.save(self.term_values('payment_term')[:1]))
        before = self.facts()
        from pmo_plugins.contracts.models import ContractLine
        ContractLine.objects.filter(pk=self.line.pk).update(net_cents=99999)
        try:
            with self.assertRaises((ValidationError, PermissionDenied)):
                self.approve(row)
        finally:
            ContractLine.objects.filter(pk=self.line.pk).update(net_cents=100000)
        self.assertEqual(before, self.facts())
        row.refresh_from_db()
        self.assertEqual(row.status, 'submitted')
        self.assertEqual(OpenItem.objects.count(), 0)

    def test_second_draft_recomputes_against_current_cumulative(self):
        payments = [
            {'key': 'a', 'condition_type': 'unconditional', 'gross_cents': 33,
             'due_date': (self.today - timedelta(days=1)).isoformat()},
            {'key': 'b', 'condition_type': 'unconditional', 'gross_cents': 34,
             'due_date': (self.today - timedelta(days=2)).isoformat()},
            {'key': 'c', 'condition_type': 'unconditional', 'gross_cents': 34,
             'due_date': (self.today - timedelta(days=3)).isoformat()},
        ]
        self.approve_line(net=100, tax=1, payments=payments, warranties=[])
        self.acceptance(0, 10000)
        refs = self.term_values('payment_term')
        first = self.submit(self.save([refs[0]]))
        second = self.submit(self.save([refs[1]]))
        self.assertEqual((first.net_cents, second.net_cents), (33, 34))
        self.approve(first)
        before = self.facts()
        with self.assertRaises(ValidationError):
            self.approve(second)
        self.assertEqual(before, self.facts())
        second.refresh_from_db()
        self.assertEqual((second.status, second.net_cents), ('submitted', 34))
        returned = receivable.review_settlement(self.finance, second.pk,
            expected_version=second.version, request_token=uuid.uuid4(),
            decision='return', note='按当前累计重新保存', dry_run=False)
        resaved = self.save([refs[1]], settlement_id=returned.pk,
                            expected_version=returned.version)
        self.assertEqual(resaved.net_cents, 33)
        self.assertEqual(self.approve(self.submit(resaved)).status, 'active')
        self.assertEqual(sum(OpenItem.objects.values_list('gross_cents', flat=True)), 67)

    def test_open_items_are_only_created_at_import_approval_and_sum_to_parent(self):
        row = self.approved_settlement()
        self.assertEqual(row.items.count(), 1)
        self.assertEqual(sum(row.items.values_list('gross_cents', flat=True)), row.gross_cents)
        item = row.items.get()
        self.assert_db_denied(lambda: OpenItem.objects.create(
            organization=self.org, project=self.project, settlement=row, direction='receivable',
            partition_kind='due', partition_key='due:manual', term_ref=item.term_ref,
            counterparty_id=item.counterparty_id, payee_ref=item.payee_ref, scope=item.scope,
            source=item.source, gross_cents=1, tax_cents=0, due_date=self.today,
            occurred_on=self.today, release_condition='', status='active', version=1))


class SettlementAgingTests(SettlementFixture, TestCase):
    def settle_golden_all(self):
        self.approve_golden()
        self.acceptance(0, 5000)
        self.settle(self.term_values('payment_term')[:1])
        self.acceptance(5000, 9000)
        self.settle(self.term_values('payment_term')[1:2])
        self.acceptance(9000, 10000)
        return self.settle(self.term_values('warranty_term'))

    def five_bucket_line(self):
        contract = self.make_contract(contract_code='C-AGE')
        payments = [{'key': 't%d' % index, 'condition_type': 'unconditional',
                     'gross_cents': 21200,
                     'due_date': (self.today + timedelta(days=days)).isoformat()}
                    for index, days in enumerate((-10, -40, -70, -100, 10))]
        self.approve_line(net=100000, tax=6000, payments=payments, warranties=[],
                          contract=contract)
        line = self.line
        self.acceptance(0, 10000, line=line)
        return line

    def test_age_buckets_inclusive_endpoint_and_no_forecast_write(self):
        line = self.five_bucket_line()
        for value in self.term_values('payment_term', line=line):
            self.settle([value], line=line)
        self.assertEqual((Settlement.objects.count(), OpenItem.objects.count(),
                          SettlementReceipt.objects.count()), (5, 5, 15))
        before = self.facts()
        data = aging.open_item_aging(self.owner, project_id=self.project.pk)
        self.assertEqual(data['as_of'], self.today.isoformat())
        self.assertEqual(data['buckets'], {'not_due': 21200, '1_30': 21200, '31_60': 21200,
                                           '61_90': 21200, '90_plus': 21200})
        self.assertEqual(data['totals'], {'count': 5, 'gross_cents': 106000,
                                          'remaining_cents': 106000,
                                          'overdue_cents': 84800, 'overdue_count': 4})
        self.assertEqual(data['retention']['count'], 0)
        self.assertEqual(data['unallocated_receipt_refs'], [])
        self.assertTrue(all(item['remaining_cents'] == item['gross_cents']
                            for item in data['items']))
        oldest = min(data['items'], key=lambda item: item['due_date'])
        self.assertEqual((oldest['bucket'], oldest['overdue_days']), ('90_plus', 100))
        self.assertTrue(all(item['release_display'] == item['due_display'] for item in data['items']))
        boundary = aging.open_item_aging(self.owner, as_of=oldest['due_date'])['items']
        chosen = next(item for item in boundary if item['id'] == oldest['id'])
        self.assertEqual((chosen['bucket'], chosen['overdue_days']), ('not_due', 0))
        from datetime import date as _date
        next_day = aging.open_item_aging(
            self.owner, as_of=_date.fromisoformat(oldest['due_date']) + timedelta(days=1)
        )['items']
        chosen = next(item for item in next_day if item['id'] == oldest['id'])
        self.assertEqual((chosen['bucket'], chosen['overdue_days']), ('1_30', 1))
        self.assertEqual(before, self.facts())

    def test_retention_is_not_overdue_before_release_date(self):
        retention = self.settle_golden_all()
        item = retention.items.get()
        release = item.due_date
        self.assertEqual(release, self.today + timedelta(days=300))
        data = aging.open_item_aging(self.owner)
        retained = next(row for row in data['items'] if row['id'] == str(item.pk))
        self.assertEqual((retained['partition_kind'], retained['partition_label'],
                          retained['bucket'], retained['overdue_days']),
                         ('retention', '质保款', 'not_due', 0))
        self.assertEqual(retained['release_display'], retained['due_display'])
        self.assertEqual(data['buckets'], {'not_due': 10600, '1_30': 53000, '31_60': 42400,
                                           '61_90': 0, '90_plus': 0})
        self.assertEqual(data['retention'], {'count': 1, 'gross_cents': 10600,
                                             'released_cents': 0,
                                             'unreleased_cents': 10600, 'overdue_cents': 0})
        after_release = aging.open_item_aging(self.owner, as_of=release + timedelta(days=1))
        overdue = next(row for row in after_release['items'] if row['id'] == str(item.pk))
        self.assertEqual((overdue['bucket'], overdue['overdue_days']), ('1_30', 1))
        self.assertEqual(after_release['retention']['released_cents'], 10600)
        self.assertEqual(after_release['retention']['unreleased_cents'], 0)

    def test_export_rows_are_read_only_and_pm_scoped(self):
        self.settle_golden_all()
        before = self.facts()
        rows = aging.aging_export_rows(self.owner, project_id=self.project.pk)
        self.assertEqual(len(rows), 3)
        self.assertEqual(set(rows[0]), {'账龄基准日', '项目编号', '项目名称', '合同明细',
                                        '结算编号', '分区类型', '条款', '释放条件',
                                        '到期或释放日', '逾期天数', '账龄桶', '含税（元）',
                                        '剩余（元）', '税（元）', '状态', '未清项编号'})
        self.assertEqual(len(aging.aging_export_rows(self.pm)), 3)
        other_pm = self.member('pm')
        self.assertEqual(aging.open_item_aging(other_pm)['items'], [])
        self.assertEqual(aging.aging_export_rows(other_pm), [])
        self.denied(lambda: aging.open_item_aging(other_pm, project_id=self.project.pk))
        self.denied(lambda: aging.aging_export_rows(self.plain))
        self.denied(lambda: aging.open_item_aging(self.plain))
        self.denied(lambda: aging.open_item_aging(self.owner, as_of='2026-1-1'))
        self.denied(lambda: aging.open_item_aging(self.owner, as_of=True))
        future = aging.open_item_aging(self.owner, as_of=self.today + timedelta(days=400))
        self.assertEqual(future['totals']['gross_cents'], 106000)
        self.assertEqual(before, self.facts())
        self.assertEqual(OpenItem.objects.filter(status='active').count(), 3)

    def test_settlement_snapshot_and_open_items_as_of(self):
        retention = self.settle_golden_all()
        self.assertEqual(len(receivable.open_items_for_settlement(
            self.owner, retention.pk, as_of=self.today)), 1)
        self.assertEqual(receivable.open_items_for_settlement(
            self.owner, retention.pk, as_of=self.today - timedelta(days=1)), [])
        snapshot = receivable.settlement_snapshot(self.auditor, retention.pk,
                                                  as_of=self.today)
        self.assertEqual(snapshot['items'][0]['partition_kind'], 'retention')
        self.assertIsNone(receivable.settlement_snapshot(self.owner, retention.pk)['as_of'])
