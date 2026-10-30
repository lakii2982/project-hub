"""PH-222 service-period confirmations to month-owned management revenue."""
import uuid
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from core.models import (AccountingPeriod, DomainEvent, LedgerEntry, LedgerPosting,
                         PluginState, SourceConsumption)
from pmo_plugins.contracts import approval_services as contracts
from pmo_plugins.contracts.party_services import create_contact
from pmo_plugins.delivery import service_period_services as service_period
from pmo_plugins.delivery.models import (ServiceConfirmation, ServiceConfirmationMonth,
                                         ServiceConfirmationReversal)
from pmo_plugins.management_revenue import services as revenue
from pmo_plugins.management_revenue.models import (RevenueAdjustment, RevenueRecognition,
                                                   RevenueReceipt)
from tests.test_v1_contracts import Fixture


class ServiceRevenueFixture(Fixture):
    """Real approved fixed_service_period contract, evidence and independent confirmation."""

    today = timezone.localdate()

    def setUp(self):
        super().setUp()
        self.finance = self.member('finance')
        self.auditor = self.member('auditor')
        self.member_user = self.member('member')
        self.contact = create_contact(self.owner, party_id=self.party.pk, name='服务验收联系人',
            request_token=uuid.uuid4())
        self.contract = self.make_contract()
        self.line = None

    # ---------- contract approval ----------
    def approved_service_line(self, *, net=101, start=None, end=None, period_unit='calendar_day',
            partial_rule='calendar_days', schedule=None):
        start = start or date(2026, 1, 1)
        end = end or date(2026, 1, 31)
        schedule = schedule or [{'coverage_start': start.isoformat(), 'coverage_end': end.isoformat(),
                                 'allocated_net_cents': net}]
        pricing = {'template': 'fixed_service_period', 'net_cap_cents': net,
                   'service_start': start.isoformat(), 'service_end': end.isoformat(),
                   'period_unit': period_unit, 'partial_period_rule': partial_rule,
                   'service_schedule': schedule}
        line = {'line_no': 1, 'net_cents': net, 'tax_cents': 0, 'gross_cents': net,
                'pricing_configuration': pricing}
        version = contracts.save_version(self.owner, self.contract.pk, lines=[line],
            effective_on=start, contract_reference='服务期批准价格依据', request_token=uuid.uuid4())
        version = contracts.submit_version(self.owner, version.pk, expected_version=version.version,
            request_token=uuid.uuid4())
        version = contracts.review_version(self.finance, version.pk, expected_version=version.version,
            request_token=uuid.uuid4(), decision='approve', review_note='独立核对服务期分配')
        self.line = version.lines.get()
        return self.line

    def line_ref(self):
        return {'namespace': 'contracts', 'kind': 'contract_line', 'id': str(self.line.pk),
                'version': self.line.version}

    # ---------- confirmation workflow ----------
    def confirmation(self, *, start, end, confirmed_on=None, confirmed=True, replacement=None,
            net=None, period_unit='calendar_day', partial_rule='calendar_days', schedule=None):
        if self.line is None:
            self.approved_service_line(net=net or 101, start=start, end=end,
                period_unit=period_unit, partial_rule=partial_rule, schedule=schedule)
        row = service_period.save_service_confirmation_with_evidence(self.owner,
            contract_line_ref=self.line_ref(), contact_id=self.contact.pk,
            coverage_start=start, coverage_end=end,
            confirmed_on=confirmed_on or end, channel='邮件', reference='演练服务确认编号',
            statement='客户确认该服务范围已经完成', request_token=uuid.uuid4(),
            replaces_confirmation_ref=replacement, dry_run=False)
        if confirmed:
            row = service_period.submit_service_confirmation(self.owner, row.pk,
                expected_version=row.version, request_token=uuid.uuid4(), dry_run=False)
            row = service_period.review_service_confirmation(self.finance, row.pk,
                expected_version=row.version, request_token=uuid.uuid4(),
                decision='approve', note='独立核对客户依据与服务覆盖', dry_run=False)
        return row

    # ---------- income ----------
    def recognize(self, row, month, **changes):
        values = {'expected_version': row.confirmed_version, 'request_token': uuid.uuid4(),
                  'dry_run': False}
        values.update(changes)
        return revenue.recognize_service_month(self.owner, row.pk, service_month=month, **values)

    def reverse(self, recognition, **changes):
        values = {'reason': '原事实登记错误，完整冲销', 'request_token': uuid.uuid4(),
                  'dry_run': False}
        values.update(changes)
        return revenue.reverse_recognition(self.owner, recognition.pk, **values)

    def reverse_parent(self, row, **changes):
        values = {'reason': '服务确认依据撤销，逐月收入已反', 'request_token': uuid.uuid4(),
                  'dry_run': False}
        values.update(changes)
        return service_period.reverse_service_confirmation(self.finance, row.pk,
            expected_version=row.version, **values)

    # ---------- snapshots / facts ----------
    def facts(self):
        return tuple(model.objects.count() for model in (
            ServiceConfirmation, ServiceConfirmationMonth, ServiceConfirmationReversal,
            RevenueRecognition, RevenueAdjustment, RevenueReceipt, AccountingPeriod,
            SourceConsumption, LedgerEntry, LedgerPosting))

    def denied(self, call):
        before = self.facts()
        with self.assertRaises((ValidationError, PermissionDenied)):
            call()
        self.assertEqual(before, self.facts())

    def assert_db_denied(self, call):
        with self.assertRaises((IntegrityError, ValidationError)):
            with transaction.atomic():
                call()


class ServiceConfirmationMonthTests(ServiceRevenueFixture, TestCase):
    def test_draft_derives_months_without_persisting_and_dry_run_has_no_side_effect(self):
        self.approved_service_line(net=101, start=date(2026, 1, 31), end=date(2026, 2, 2))
        before = self.facts()
        draft = service_period.save_service_confirmation_with_evidence(self.owner,
            contract_line_ref=self.line_ref(), contact_id=self.contact.pk,
            coverage_start=date(2026, 1, 31), coverage_end=date(2026, 2, 2),
            confirmed_on=date(2026, 2, 10), channel='邮件', reference='草稿依据',
            statement='仅登记草稿', request_token=uuid.uuid4())
        self.assertEqual(draft.status, 'draft')
        self.assertIsNone(draft.confirmed_version)
        self.assertEqual(ServiceConfirmationMonth.objects.count(), 0)
        self.assertEqual(before, self.facts())

    def test_confirm_freezes_real_month_rows_and_permanent_version(self):
        row = self.confirmation(start=date(2026, 1, 31), end=date(2026, 2, 2),
            confirmed_on=date(2026, 2, 10))
        self.assertEqual(row.status, 'confirmed')
        self.assertEqual(row.confirmed_net_cents, 101)
        self.assertIsNotNone(row.confirmed_version)
        months = list(row.months.order_by('service_month'))
        self.assertEqual([m.service_month for m in months], ['2026-01', '2026-02'])
        self.assertEqual([m.allocated_net_cents for m in months], [34, 67])
        self.assertEqual(sum(m.allocated_net_cents for m in months), 101)
        snapshot = service_period.confirmed_service_snapshot(self.owner, row.pk, for_recognition=False)
        self.assertEqual(snapshot['version'], row.confirmed_version)
        self.assertEqual([m['service_month'] for m in snapshot['months']], ['2026-01', '2026-02'])
        self.assertEqual(LedgerEntry.objects.count(), 0)
        self.assertEqual(LedgerPosting.objects.count(), 0)

    def test_no_partial_single_month_must_cover_whole_approved_segment(self):
        self.approved_service_line(net=101, start=date(2026, 1, 1), end=date(2026, 1, 31),
            period_unit='calendar_month', partial_rule='no_partial')
        self.denied(lambda: service_period.save_service_confirmation_with_evidence(self.owner,
            contract_line_ref=self.line_ref(), contact_id=self.contact.pk,
            coverage_start=date(2026, 1, 10), coverage_end=date(2026, 1, 20),
            confirmed_on=date(2026, 1, 25), channel='邮件', reference='部分月份',
            statement='不允许部分期间', request_token=uuid.uuid4(), dry_run=False))
        row = self.confirmation(start=date(2026, 1, 1), end=date(2026, 1, 31),
            confirmed_on=date(2026, 2, 2), period_unit='calendar_month', partial_rule='no_partial')
        self.line = None
        recognition = self.recognize(row, '2026-01')
        self.assertEqual(recognition.recognized_net_cents, 101)

    def test_cross_month_no_partial_is_rejected_at_formal_submit(self):
        self.approved_service_line(net=101, start=date(2026, 1, 31), end=date(2026, 2, 2),
            period_unit='calendar_day', partial_rule='no_partial')
        draft = service_period.save_service_confirmation_with_evidence(self.owner,
            contract_line_ref=self.line_ref(), contact_id=self.contact.pk,
            coverage_start=date(2026, 1, 31), coverage_end=date(2026, 2, 2),
            confirmed_on=date(2026, 2, 10), channel='邮件', reference='跨月不许部分',
            statement='缺少批准月价', request_token=uuid.uuid4(), dry_run=False)
        before = self.facts()
        with self.assertRaises(ValidationError):
            service_period.submit_service_confirmation(self.owner, draft.pk,
                expected_version=draft.version, request_token=uuid.uuid4(), dry_run=False)
        self.assertEqual(before, self.facts())


class ServiceMonthRevenueTests(ServiceRevenueFixture, TestCase):
    def test_cross_month_recognitions_use_each_month_allocated_amount(self):
        row = self.confirmation(start=date(2026, 1, 31), end=date(2026, 2, 2),
            confirmed_on=date(2026, 2, 10))
        jan = self.recognize(row, '2026-01')
        feb = self.recognize(row, '2026-02')
        self.assertEqual((jan.recognized_net_cents, feb.recognized_net_cents), (34, 67))
        self.assertEqual((jan.occurred_on, feb.occurred_on), (date(2026, 1, 31), date(2026, 2, 2)))
        self.assertEqual((jan.posting_period, feb.posting_period), ('2026-01', '2026-02'))
        self.assertEqual(jan.algorithm_version, 'fixed_service_period_cumulative_half_up_v1')
        self.assertEqual(jan.accounting_event.event_type, 'management_revenue.service_recognized')
        self.assertEqual(jan.accounting_event.claim.consumer, 'management_revenue')
        self.assertEqual(jan.accounting_event.claim.source_id, row.pk)
        self.assertEqual(jan.accounting_event.claim.coverage_key, 'service_month:2026-01')
        self.assertEqual(LedgerPosting.objects.count(), 0)

    def test_same_month_three_confirmations_34_33_34_and_summary_not_parent_short_cut(self):
        self.approved_service_line(net=101, start=date(2026, 1, 1), end=date(2026, 1, 3))
        rows = [self.confirmation(start=date(2026, 1, day), end=date(2026, 1, day),
            confirmed_on=date(2026, 2, 1)) for day in (1, 2, 3)]
        amounts = [self.recognize(row, '2026-01').recognized_net_cents for row in rows]
        self.assertEqual(amounts, [34, 33, 34])
        summary = revenue.project_revenue_summary(self.owner, self.project.pk)
        self.assertEqual(summary['management_revenue_cents'], 101)
        self.assertEqual(summary['unrecognized_service_month_count'], 0)
        self.assertEqual(revenue.revenue_export_rows(self.owner, self.project.pk)[0]['service_month'], '2026-01')

    def test_zero_amount_month_still_consumes_and_must_be_reversed(self):
        self.contract = self.make_contract(contract_code='C-zero')
        self.line = None
        row = self.confirmation(start=date(2026, 1, 31), end=date(2026, 2, 2),
            confirmed_on=date(2026, 2, 10), net=1)
        jan = self.recognize(row, '2026-01')
        self.assertEqual(jan.recognized_net_cents, 0)
        self.assertTrue(SourceConsumption.objects.filter(source_id=row.pk,
            coverage_key='service_month:2026-01').exists())
        self.denied(lambda: self.recognize(row, '2026-01'))
        adjustment = self.reverse(jan)
        self.assertEqual(adjustment.adjustment_cents, 0)
        self.denied(lambda: self.recognize(row, '2026-01'))
        state = service_period.service_confirmation_income_state(self.owner, row.pk)
        jan_state = [m for m in state['months'] if m['service_month'] == '2026-01'][0]
        self.assertTrue(jan_state['is_reversed'])

    def test_customer_next_month_cannot_move_income_period(self):
        row = self.confirmation(start=date(2026, 1, 10), end=date(2026, 1, 20),
            confirmed_on=date(2026, 2, 10))
        recognition = self.recognize(row, '2026-01')
        self.assertEqual(recognition.occurred_on, date(2026, 1, 20))
        self.assertEqual(recognition.posting_period, '2026-01')
        self.assertNotEqual(recognition.posting_period, '2026-02')

    def test_month_lock_and_closed_period_reject_income_and_reversal(self):
        row = self.confirmation(start=date(2026, 1, 10), end=date(2026, 1, 20),
            confirmed_on=date(2026, 1, 25))
        self.org.locked_through = date(2026, 1, 15)
        self.org.save()
        self.denied(lambda: self.recognize(row, '2026-01'))
        self.org.locked_through = None
        self.org.save()
        recognition = self.recognize(row, '2026-01')
        period = AccountingPeriod.objects.get(organization=self.org, period='2026-01')
        period.status, period.closed_through = 'posted', date(2026, 1, 20)
        period.save()
        self.denied(lambda: self.reverse(recognition))
        self.denied(lambda: self.recognize(row, '2026-01'))

    def test_dry_run_receipt_and_token_variants(self):
        row = self.confirmation(start=date(2026, 1, 10), end=date(2026, 1, 20),
            confirmed_on=date(2026, 1, 25))
        before = self.facts()
        preview = self.recognize(row, '2026-01', dry_run=True)
        self.assertTrue(preview['dry_run'])
        self.assertEqual(preview['posting_period'], '2026-01')
        self.assertEqual(before, self.facts())
        token = uuid.uuid4()
        first = self.recognize(row, '2026-01', request_token=token)
        before = self.facts()
        self.assertEqual(self.recognize(row, '2026-01', request_token=token).pk, first.pk)
        self.assertEqual(before, self.facts())
        self.denied(lambda: self.recognize(row, '2026-01', request_token=token,
            expected_version=row.confirmed_version + 1))
        for variant in ['2026-1', '2026-01 ', '2026-13', 202601, True]:
            self.denied(lambda: self.recognize(row, variant))
        self.denied(lambda: self.recognize(row, '2026-01', expected_version=row.version + 1))

    def test_roles_and_disabled_module_keep_facts_readable(self):
        row = self.confirmation(start=date(2026, 1, 10), end=date(2026, 1, 20),
            confirmed_on=date(2026, 1, 25))
        recognition = self.recognize(row, '2026-01')
        for actor in (self.pm, self.member_user, self.auditor):
            self.denied(lambda: revenue.recognize_service_month(actor, row.pk,
                service_month='2026-01', expected_version=row.confirmed_version,
                request_token=uuid.uuid4(), dry_run=False))
        PluginState.objects.update_or_create(organization=self.org,
            plugin_id='management_revenue', defaults={'enabled': False, 'changed_by': self.owner.user})
        state = service_period.service_confirmation_income_state(self.owner, row.pk)
        self.assertTrue(state['any_posted'])
        self.assertEqual(revenue.project_revenue_summary(self.owner)['management_revenue_cents'], 101)
        self.denied(lambda: self.reverse(recognition))
        self.assertTrue(revenue.recognition_for_service_month(row.pk, '2026-01'))

    def test_xor_and_service_source_db_guard(self):
        row = self.confirmation(start=date(2026, 1, 10), end=date(2026, 1, 20),
            confirmed_on=date(2026, 1, 25))
        month = row.months.get()
        fields = dict(organization=self.org, project=self.project, contract_line=self.line,
            created_by=self.owner, contract_line_ref=self.line_ref(),
            performance_ref=service_period.confirmed_service_snapshot(self.owner, row.pk,
                for_recognition=False)['source_ref'],
            policy_version=row.policy_version, pricing_snapshot=row.pricing_configuration,
            accepted_snapshot={'fake': True}, recognized_net_cents=month.allocated_net_cents,
            occurred_on=month.coverage_end, posting_period=month.service_month,
            algorithm_version='fixed_service_period_cumulative_half_up_v1')
        # XOR: no source at all, and a parent without its required month, are both rejected.
        self.assert_db_denied(lambda: RevenueRecognition.objects.create(**fields))
        self.assert_db_denied(lambda: RevenueRecognition.objects.create(
            service_confirmation=row, **fields))
        # Month must belong to the same parent, and its amounts/dates must match.
        self.contract = self.make_contract(contract_code='C-other')
        self.line = None
        other = self.confirmation(start=date(2026, 3, 1), end=date(2026, 3, 2),
            confirmed_on=date(2026, 3, 3))
        other_month = other.months.get()
        self.assert_db_denied(lambda: RevenueRecognition.objects.create(
            service_confirmation=row, service_confirmation_month=other_month,
            **{k: v for k, v in fields.items() if k not in
               ('recognized_net_cents', 'occurred_on', 'posting_period')},
            recognized_net_cents=other_month.allocated_net_cents,
            occurred_on=other_month.coverage_end, posting_period=other_month.service_month))
        # Source identity columns are immutable once the row exists.
        draft = RevenueRecognition.objects.create(service_confirmation=row,
            service_confirmation_month=month, **fields)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                RevenueRecognition.objects.filter(pk=draft.pk).update(service_confirmation=other)
        self.assertEqual(RevenueRecognition.objects.filter(pk=draft.pk).first().service_confirmation_id, row.pk)


class ServiceMonthReversalTests(ServiceRevenueFixture, TestCase):
    def test_reverse_copies_original_date_and_period_per_month(self):
        row = self.confirmation(start=date(2026, 1, 31), end=date(2026, 2, 2),
            confirmed_on=date(2026, 2, 10))
        jan = self.recognize(row, '2026-01')
        feb = self.recognize(row, '2026-02')
        adjustment = self.reverse(jan)
        self.assertEqual(adjustment.adjustment_cents, -34)
        self.assertEqual(adjustment.occurred_on, jan.occurred_on)
        self.assertEqual(adjustment.posting_period, '2026-01')
        self.assertEqual(adjustment.accounting_event.reverses_event_id, jan.accounting_event_id)
        self.assertEqual(adjustment.accounting_event.event_type, 'management_revenue.service_reversed')
        self.assertEqual(adjustment.accounting_event.claim_id, jan.accounting_event.claim_id)
        self.denied(lambda: self.reverse(jan))
        # Other month stays intact until it is explicitly reversed too.
        self.assertFalse(feb.is_reversed)

    def test_parent_reversal_requires_every_month_reversed_first(self):
        row = self.confirmation(start=date(2026, 1, 31), end=date(2026, 2, 2),
            confirmed_on=date(2026, 2, 10))
        jan = self.recognize(row, '2026-01')
        feb = self.recognize(row, '2026-02')
        self.denied(lambda: self.reverse_parent(row))
        self.reverse(jan)
        self.denied(lambda: self.reverse_parent(row))
        self.reverse(feb)
        state = service_period.service_confirmation_income_state(self.owner, row.pk)
        self.assertTrue(state['all_reversed'])
        parent = self.reverse_parent(row)
        self.assertEqual(parent.status, 'reversed')
        self.assertEqual(ServiceConfirmationReversal.objects.filter(confirmation=parent).count(), 1)
        self.denied(lambda: self.recognize(row, '2026-01'))
        history = service_period.confirmed_service_snapshot(self.owner, row.pk, for_recognition=False)
        self.assertEqual(history['version'], row.confirmed_version)
        with self.assertRaises(ValidationError):
            service_period.confirmed_service_snapshot(self.owner, row.pk, for_recognition=True)

    def test_replacement_chain_after_full_month_reversal(self):
        row = self.confirmation(start=date(2026, 1, 10), end=date(2026, 1, 20),
            confirmed_on=date(2026, 1, 25))
        recognition = self.recognize(row, '2026-01')
        self.reverse(recognition)
        parent = self.reverse_parent(row)
        old_ref = {'namespace': 'delivery', 'kind': 'service_confirmation',
                   'id': str(parent.pk), 'version': parent.confirmed_version}
        new = self.confirmation(start=date(2026, 1, 10), end=date(2026, 1, 20),
            confirmed_on=date(2026, 1, 28), replacement=old_ref)
        self.assertEqual(new.replaces_confirmation_id, parent.pk)
        self.assertEqual(self.recognize(new, '2026-01').recognized_net_cents, 101)

    def test_income_reversal_requires_original_month_not_locked(self):
        row = self.confirmation(start=date(2026, 1, 10), end=date(2026, 1, 20),
            confirmed_on=date(2026, 1, 25))
        recognition = self.recognize(row, '2026-01')
        self.org.locked_through = date(2026, 1, 15)
        self.org.save()
        self.denied(lambda: self.reverse(recognition))


class ServiceMonthSummaryIntegrityTests(ServiceRevenueFixture, TestCase):
    def test_summary_recognizes_each_month_even_when_parent_has_one_income(self):
        row = self.confirmation(start=date(2026, 1, 31), end=date(2026, 2, 2),
            confirmed_on=date(2026, 2, 10))
        self.assertEqual(revenue.project_revenue_summary(self.owner)['unrecognized_service_month_count'], 2)
        jan = self.recognize(row, '2026-01')
        summary = revenue.project_revenue_summary(self.owner)
        self.assertEqual(summary['unrecognized_service_month_count'], 1)
        self.assertEqual(summary['pending_service_confirmation_count'], 0)
        self.assertIn('unrecognized_acceptance_count', summary)
        self.reverse(jan)
        self.assertEqual(revenue.project_revenue_summary(self.owner)['reversed_pending_service_month_count'], 1)

    def test_export_exposes_service_fk_month_customer_date_and_reversal_chain(self):
        row = self.confirmation(start=date(2026, 1, 31), end=date(2026, 2, 2),
            confirmed_on=date(2026, 2, 10))
        jan = self.recognize(row, '2026-01')
        adjustment = self.reverse(jan)
        export = revenue.revenue_export_rows(self.finance)
        recognition_row = next(item for item in export if item['kind'] == 'revenue_recognition')
        self.assertEqual(recognition_row['service_confirmation_id'], str(row.pk))
        self.assertEqual(recognition_row['service_month'], '2026-01')
        self.assertEqual(recognition_row['customer_confirmed_on'], '2026-02-10')
        self.assertIsNone(recognition_row['acceptance_id'])
        self.assertEqual(recognition_row['contract_version_id'], str(self.line.contract_version_id))
        reverse_row = next(item for item in export if item['kind'] == 'revenue_adjustment')
        self.assertEqual(reverse_row['recognition_id'], str(jan.pk))
        self.assertEqual(reverse_row['reverses_event_id'], str(adjustment.accounting_event.reverses_event_id))

    def test_integrity_accepts_valid_service_chain(self):
        from core.revenue_accounting import revenue_integrity_errors
        row = self.confirmation(start=date(2026, 1, 31), end=date(2026, 2, 2),
            confirmed_on=date(2026, 2, 10))
        self.recognize(row, '2026-01')
        self.recognize(row, '2026-02')
        self.assertEqual(revenue_integrity_errors(), [])

    def test_invalid_month_argument_and_wrong_version_have_no_side_effects(self):
        row = self.confirmation(start=date(2026, 1, 10), end=date(2026, 1, 20),
            confirmed_on=date(2026, 1, 25))
        before = self.facts()
        self.denied(lambda: self.recognize(row, '2026-03'))
        self.denied(lambda: self.recognize(row, '2026-01', expected_version=row.confirmed_version + 1))
        self.denied(lambda: self.recognize(row, None))
        self.assertEqual(before, self.facts())
