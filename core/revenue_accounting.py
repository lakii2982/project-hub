"""Named core accounting bridge for approved acceptance and service-month income, never cash/cost."""
import uuid
from copy import deepcopy
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .access import require_member
from .models import (Membership, AccountingPeriod, AccountingEvent, SourceConsumption,
                     LedgerPosting)
from .services import lock_company

ALGORITHM = 'fixed_milestone_cumulative_half_up_v1'
SERVICE_ALGORITHM = 'fixed_service_period_cumulative_half_up_v1'


def _fresh(member):
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist) as error:
        raise PermissionDenied('无效财务成员') from error
    require_member(member)
    from .plugin_runtime import is_enabled
    if member.role not in {'owner', 'finance'} or not is_enabled(member.organization, 'management_revenue'):
        raise PermissionDenied('没有管理收入办理权限或模块已停用')
    return member


def check_revenue_period(org, occurred_on, *, allow_create=False):
    if type(occurred_on) is not date or occurred_on > timezone.localdate():
        raise ValidationError('客户确认实际日期须为有效日期且不得未来')
    if org.locked_through and occurred_on <= org.locked_through:
        raise ValidationError('客户确认日所在原期间已锁，不能换开放月绕过')
    period = occurred_on.strftime('%Y-%m')
    row = AccountingPeriod.objects.filter(organization=org, period=period).first()
    if row and (row.status != 'active' or row.closed_through is not None):
        raise ValidationError('客户确认日所在会计期间已关闭')
    if row is None and allow_create:
        # Only a company-locked formal recognition with a confirmed source calls this.
        AccountingPeriod.objects.create(organization=org, period=period)
    return period


def check_service_revenue_period(org, coverage_start, coverage_end, *, allow_create=False):
    """Service-month income posts to the service month, never the customer date month."""
    if type(coverage_start) is not date or type(coverage_end) is not date:
        raise ValidationError('服务覆盖首尾须为有效日期')
    if coverage_start.strftime('%Y-%m') != coverage_end.strftime('%Y-%m'):
        raise ValidationError('单个服务月行必须落在同一自然月')
    service_month = coverage_start.strftime('%Y-%m')
    check_revenue_period(org, coverage_start)
    check_revenue_period(org, coverage_end)
    period = AccountingPeriod.objects.filter(organization=org, period=service_month).first()
    if period and (period.status != 'active' or period.closed_through is not None):
        raise ValidationError('服务月份会计期间已关闭')
    if period is None and allow_create:
        # Only a company-locked formal recognition with an independently confirmed month calls this.
        AccountingPeriod.objects.create(organization=org, period=service_month)
    return service_month


def _amount(value):
    if type(value) is not int or not 0 <= value <= 10**12:
        raise ValidationError('管理收入必须为0至100亿元的整数分')
    return value


def _accepted_snapshot(source):
    row = source['acceptance']
    return {'acceptance_id': str(row.pk), 'version': source['version'],
            'accepted_net_cents': source['accepted_net_cents'],
            'confirmed_on': source['confirmed_on'].isoformat(),
            'approved_digest': source['approved_digest'], 'evidence': source['evidence'],
            'milestone_coverages': [
                {'milestone_id': str(c.milestone_id), 'from_basis_points': c.from_basis_points,
                 'to_basis_points': c.to_basis_points, 'accepted_net_cents': c.accepted_net_cents}
                for c in row.coverages.order_by('milestone_id', 'from_basis_points')]}


def service_recognition_snapshot(source, month):
    """Private immutable snapshot of one frozen service month; not a second public source."""
    return {'source_kind': 'service_confirmation_month',
            'service_confirmation_id': str(source['confirmation'].pk),
            'service_confirmation_version': source['version'],
            'service_confirmation_month_id': str(month['id']),
            'service_month': month['service_month'],
            'coverage_start': month['coverage_start'].isoformat(),
            'coverage_end': month['coverage_end'].isoformat(),
            'allocated_net_cents': month['allocated_net_cents'],
            'schedule_index': source['schedule_index'],
            'schedule': deepcopy(source['schedule']),
            'confirmed_on': source['confirmed_on'].isoformat(),
            'approved_digest': source['approved_digest'],
            'evidence': deepcopy(source['evidence'])}


def _service_month(snapshot, month_id):
    for month in snapshot['months']:
        if str(month['id']) == str(month_id):
            return month
    raise ValidationError('服务月行与真实确认不一致')


@transaction.atomic
def post_service_revenue_recognition(member, recognition):
    """Named bridge for one explicitly chosen frozen service month, never cost/cash."""
    member = _fresh(member)
    lock_company(member)
    member = _fresh(member)
    from pmo_plugins.management_revenue.models import RevenueRecognition
    from pmo_plugins.delivery.service_period_services import confirmed_service_snapshot
    row = RevenueRecognition.objects.select_for_update(of=('self',)).get(
        pk=recognition.pk, organization=member.organization)
    if row.status != 'draft' or row.accounting_event_id or row.created_by_id != member.pk:
        raise ValidationError('只可为真实未过账服务收入候选建立核算桥')
    if row.acceptance_id or not row.service_confirmation_id or not row.service_confirmation_month_id:
        raise ValidationError('服务收入候选来源不完整或混用固定节点来源')
    snapshot = confirmed_service_snapshot(member, row.service_confirmation_id, for_recognition=True)
    month = _service_month(snapshot, row.service_confirmation_month_id)
    amount = _amount(month['allocated_net_cents'])
    if (row.performance_ref != snapshot['source_ref'] or row.project_id != snapshot['project'].pk
            or row.contract_line_id != snapshot['contract_line'].pk
            or row.contract_line_ref != snapshot['contract_line_ref']
            or row.policy_version != snapshot['policy_version']
            or row.pricing_snapshot != snapshot['pricing_configuration']
            or row.accepted_snapshot != service_recognition_snapshot(snapshot, month)
            or row.recognized_net_cents != amount or row.occurred_on != month['coverage_end']
            or row.posting_period != month['service_month']
            or row.algorithm_version != SERVICE_ALGORITHM):
        raise ValidationError('服务收入候选与真实冻结月行或原批准快照不一致')
    check_service_revenue_period(member.organization, month['coverage_start'], month['coverage_end'])
    key = 'service_month:' + month['service_month']
    if SourceConsumption.objects.filter(organization=member.organization, consumer='management_revenue',
            source_id=row.service_confirmation_id, coverage_key=key).exists():
        raise ValidationError('该服务月已消费，包括零金额与已冲销来源')
    check_service_revenue_period(member.organization, month['coverage_start'], month['coverage_end'],
        allow_create=True)
    event_id = uuid.uuid4()
    claim = SourceConsumption.objects.create(organization=member.organization, consumer='management_revenue',
        source=snapshot['source_ref'], source_id=row.service_confirmation_id, coverage_key=key,
        amount_cents=amount,
        event_ref={'namespace': 'core', 'kind': 'accounting_event', 'id': str(event_id), 'version': 1})
    return AccountingEvent.objects.create(id=event_id, organization=member.organization,
        source=row.source_ref, event_type='management_revenue.service_recognized',
        occurred_on=row.occurred_on, posting_period=month['service_month'],
        algorithm_version=SERVICE_ALGORITHM, claim=claim, claim_effect_cents=amount,
        labor_origin_ref=None)


@transaction.atomic
def post_revenue_recognition(member, recognition):
    member = _fresh(member)
    lock_company(member)
    member = _fresh(member)
    from pmo_plugins.management_revenue.models import RevenueRecognition
    from pmo_plugins.delivery.services import confirmed_acceptance_snapshot
    row = RevenueRecognition.objects.select_for_update(of=('self',)).get(
        pk=recognition.pk, organization=member.organization)
    if row.status != 'draft' or row.accounting_event_id or row.created_by_id != member.pk:
        raise ValidationError('只可为真实未过账收入候选建立核算桥')
    source = confirmed_acceptance_snapshot(member, row.performance_ref, for_recognition=True)
    amount = _amount(source['accepted_net_cents'])
    if (row.acceptance_id != source['acceptance'].pk or row.project_id != source['project'].pk
            or row.contract_line_id != source['contract_line'].pk
            or row.contract_line_ref != source['contract_line_ref']
            or row.policy_version != source['policy_version']
            or row.pricing_snapshot != source['pricing_configuration']
            or row.accepted_snapshot != _accepted_snapshot(source)
            or row.recognized_net_cents != amount or row.occurred_on != source['confirmed_on']
            or row.posting_period != row.occurred_on.strftime('%Y-%m')
            or row.algorithm_version != ALGORITHM):
        raise ValidationError('收入候选与原始独立批准验收不一致')
    period = check_revenue_period(member.organization, row.occurred_on)
    key = 'recognition:acceptance'
    if SourceConsumption.objects.filter(organization=member.organization, consumer='management_revenue',
            source_id=row.acceptance_id, coverage_key=key).exists():
        raise ValidationError('原始验收已消费，包括零金额与已冲销来源')
    check_revenue_period(member.organization, row.occurred_on, allow_create=True)
    event_id = uuid.uuid4()
    claim = SourceConsumption.objects.create(organization=member.organization, consumer='management_revenue',
        source=source['source'], source_id=row.acceptance_id, coverage_key=key, amount_cents=amount,
        event_ref={'namespace': 'core', 'kind': 'accounting_event', 'id': str(event_id), 'version': 1})
    return AccountingEvent.objects.create(id=event_id, organization=member.organization, source=row.source_ref,
        event_type='management_revenue.recognized', occurred_on=row.occurred_on, posting_period=period,
        algorithm_version=ALGORITHM, claim=claim, claim_effect_cents=amount, labor_origin_ref=None)


@transaction.atomic
def reverse_revenue_recognition(member, adjustment):
    member = _fresh(member)
    lock_company(member)
    member = _fresh(member)
    from pmo_plugins.management_revenue.models import RevenueAdjustment
    row = RevenueAdjustment.objects.select_for_update(of=('self',)).select_related('recognition').get(
        pk=adjustment.pk, organization=member.organization)
    original = row.recognition
    if row.status != 'draft' or row.accounting_event_id or row.created_by_id != member.pk:
        raise ValidationError('冲销必须有真实未过账调整候选')
    if original.status != 'posted' or not original.accounting_event_id or original.organization_id != member.organization_id:
        raise ValidationError('原收入确认不可用')
    amount = _amount(original.recognized_net_cents)
    if original.acceptance_id:
        from pmo_plugins.delivery.services import confirmed_acceptance_snapshot
        source = confirmed_acceptance_snapshot(member, original.performance_ref, for_recognition=False)
        if (original.accepted_snapshot != _accepted_snapshot(source)
                or source['accepted_net_cents'] != amount or row.adjustment_cents != -amount
                or type(row.adjustment_cents) is not int or row.project_id != original.project_id
                or row.occurred_on != original.occurred_on or row.posting_period != original.posting_period
                or not row.reason.strip()):
            raise ValidationError('只能按原始确认金额、日期和期间完整冲销')
        check_revenue_period(member.organization, original.occurred_on)
        claim_source = original.performance_ref
        claim_source_id = original.acceptance_id
        claim_key = 'recognition:acceptance'
        event_type = 'management_revenue.reversed'
    else:
        from pmo_plugins.delivery.service_period_services import confirmed_service_snapshot
        if not original.service_confirmation_id or not original.service_confirmation_month_id:
            raise ValidationError('原服务收入来源不完整')
        snapshot = confirmed_service_snapshot(member, original.service_confirmation_id,
            for_recognition=False)
        month = _service_month(snapshot, original.service_confirmation_month_id)
        if (original.accepted_snapshot != service_recognition_snapshot(snapshot, month)
                or month['allocated_net_cents'] != amount or row.adjustment_cents != -amount
                or type(row.adjustment_cents) is not int or row.project_id != original.project_id
                or row.occurred_on != original.occurred_on or row.posting_period != original.posting_period
                or not row.reason.strip()):
            raise ValidationError('只能按原始确认金额、日期和期间完整冲销')
        check_service_revenue_period(member.organization, month['coverage_start'], month['coverage_end'])
        claim_source = original.performance_ref
        claim_source_id = original.service_confirmation_id
        claim_key = 'service_month:' + original.posting_period
        event_type = 'management_revenue.service_reversed'
    event = AccountingEvent.objects.select_for_update().select_related('claim').get(pk=original.accounting_event_id)
    if (event.organization_id != member.organization_id or event.source != original.source_ref
            or event.claim.consumer != 'management_revenue' or event.claim.source != claim_source
            or event.claim.source_id != claim_source_id or event.claim.coverage_key != claim_key
            or event.claim_effect_cents != amount
            or event.reverses_event_id or event.postings.exists()
            or AccountingEvent.objects.filter(reverses_event=event).exists()):
        raise ValidationError('原核算来源不符或已有完整冲销')
    return AccountingEvent.objects.create(organization=member.organization, source=row.source_ref,
        event_type=event_type, occurred_on=event.occurred_on,
        posting_period=event.posting_period, algorithm_version=event.algorithm_version,
        reverses_event=event, claim=event.claim, claim_effect_cents=-amount, labor_origin_ref=None)


def check_revenue_close(org, cutoff):
    """Existing date-lock preflight must not strand known unfinished acceptance or service income."""
    from pmo_plugins.delivery.models import Acceptance, ServiceConfirmation, ServiceConfirmationMonth
    from pmo_plugins.management_revenue.models import RevenueRecognition, RevenueAdjustment
    rows = Acceptance.objects.filter(organization=org, confirmed_on__lte=cutoff)
    if rows.filter(status__in=['draft', 'submitted']).exists():
        raise ValidationError('锁账范围有待处理客户验收，请确认或明确撤回草稿')
    for row in rows.filter(status='confirmed'):
        income = RevenueRecognition.objects.filter(acceptance=row, status='posted').first()
        if income is None:
            raise ValidationError('锁账范围有已验收但尚未确认的管理收入')
        if income.is_reversed:
            raise ValidationError('管理收入已冲销，请先明确反冲验收，不能静默锁住更正来源')
    # Service confirmations intersect the cutoff by real coverage, not by customer confirmation date.
    pending = ServiceConfirmation.objects.filter(organization=org, coverage_start__lte=cutoff,
        status__in=['draft', 'submitted'])
    if pending.exists():
        raise ValidationError('锁账范围有待处理服务期确认，请确认或明确撤回草稿')
    months = ServiceConfirmationMonth.objects.filter(service_confirmation__organization=org,
        service_confirmation__status='confirmed', coverage_start__lte=cutoff)
    posted = set(RevenueRecognition.objects.filter(organization=org, status='posted',
        service_confirmation_month__in=months).values_list('service_confirmation_month_id', flat=True))
    for month in months:
        if month.pk not in posted:
            raise ValidationError('锁账范围有已确认服务但尚未确认的管理收入')
    reversed_months = RevenueAdjustment.objects.filter(organization=org, status='posted',
        recognition__service_confirmation_month__in=months).values_list(
        'recognition__service_confirmation_month_id', flat=True)
    if reversed_months:
        unfinished = ServiceConfirmationMonth.objects.filter(pk__in=set(reversed_months)).exclude(
            service_confirmation__status='reversed').exists()
        if unfinished:
            raise ValidationError('服务期管理收入已冲销，请先明确反确认，不能静默锁住更正来源')


def revenue_integrity_errors():
    """Internal read-only audit; does not depend on modules or members staying active."""
    from pmo_plugins.management_revenue.models import RevenueRecognition, RevenueAdjustment
    errors = []
    for cls in (RevenueRecognition, RevenueAdjustment):
        for row in cls.objects.all():
            if row.status != 'posted' or not row.accounting_event_id:
                errors.append(f'revenue:{row.pk}:unposted_candidate')
                continue
            event = row.accounting_event
            original = row if cls is RevenueRecognition else row.recognition
            amount = row.recognized_net_cents if cls is RevenueRecognition else row.adjustment_cents
            if original.acceptance_id:
                source = original.acceptance
                source_ref = {'namespace': 'delivery', 'kind': 'acceptance', 'id': str(source.pk), 'version': source.confirmed_version}
                if (original.performance_ref != source_ref or event.claim.source != source_ref
                        or event.claim.source_id != source.pk
                        or event.claim.coverage_key != 'recognition:acceptance'
                        or original.recognized_net_cents != source.accepted_net_cents
                        or original.occurred_on != source.confirmed_on):
                    errors.append(f'revenue:{row.pk}:source_amount_or_scope')
            else:
                month = original.service_confirmation_month
                parent = original.service_confirmation
                if month is None or parent is None or month.service_confirmation_id != parent.pk:
                    errors.append(f'revenue:{row.pk}:service_month_parent')
                    continue
                source_ref = {'namespace': 'delivery', 'kind': 'service_confirmation',
                              'id': str(parent.pk), 'version': parent.confirmed_version}
                if (original.performance_ref != source_ref or event.claim.source != source_ref
                        or event.claim.source_id != parent.pk
                        or event.claim.coverage_key != 'service_month:' + month.service_month
                        or month.allocated_net_cents != original.recognized_net_cents
                        or original.occurred_on != month.coverage_end
                        or original.posting_period != month.service_month
                        or original.contract_line_id != parent.contract_line_id
                        or original.project_id != parent.project_id
                        or parent.status not in {'confirmed', 'reversed'}
                        or original.algorithm_version != SERVICE_ALGORITHM):
                    errors.append(f'revenue:{row.pk}:service_source_amount_or_scope')
            if (event.source != row.source_ref or event.organization_id != row.organization_id
                    or row.organization_id != original.organization_id or row.project_id != original.project_id
                    or event.claim.organization_id != row.organization_id
                    or event.claim.consumer != 'management_revenue'
                    or event.claim.amount_cents != original.recognized_net_cents
                    or event.claim_effect_cents != amount or event.labor_origin_ref is not None
                    or event.occurred_on != original.occurred_on or event.posting_period != original.posting_period
                    or event.postings.exists()):
                errors.append(f'revenue:{row.pk}:event_scope')
            if cls is RevenueAdjustment:
                if amount != -original.recognized_net_cents or event.reverses_event_id != original.accounting_event_id:
                    errors.append(f'revenue:{row.pk}:exact_reversal')
            elif event.reverses_event_id:
                errors.append(f'revenue:{row.pk}:unexpected_reversal')
    for event in AccountingEvent.objects.filter(source__namespace='management_revenue'):
        kind = event.source.get('kind')
        cls = RevenueRecognition if kind == 'revenue_recognition' else RevenueAdjustment if kind == 'revenue_adjustment' else None
        if cls is None or not cls.objects.filter(pk=event.source.get('id'), accounting_event=event, status='posted').exists():
            errors.append(f'revenue_event:{event.pk}:missing_fact')
    return errors
