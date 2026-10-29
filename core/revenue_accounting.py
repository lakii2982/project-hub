"""Named core accounting bridge for approved acceptance income, never cash/cost."""
import uuid
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .access import require_member
from .models import (Membership, AccountingPeriod, AccountingEvent, SourceConsumption,
                     LedgerPosting)
from .services import lock_company

ALGORITHM = 'fixed_milestone_cumulative_half_up_v1'


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
    from pmo_plugins.delivery.services import confirmed_acceptance_snapshot
    row = RevenueAdjustment.objects.select_for_update(of=('self',)).select_related('recognition').get(
        pk=adjustment.pk, organization=member.organization)
    original = row.recognition
    if row.status != 'draft' or row.accounting_event_id or row.created_by_id != member.pk:
        raise ValidationError('冲销必须有真实未过账调整候选')
    if original.status != 'posted' or not original.accounting_event_id or original.organization_id != member.organization_id:
        raise ValidationError('原收入确认不可用')
    source = confirmed_acceptance_snapshot(member, original.performance_ref, for_recognition=False)
    amount = _amount(original.recognized_net_cents)
    if (original.accepted_snapshot != _accepted_snapshot(source)
            or source['accepted_net_cents'] != amount or row.adjustment_cents != -amount
            or type(row.adjustment_cents) is not int or row.project_id != original.project_id
            or row.occurred_on != original.occurred_on or row.posting_period != original.posting_period
            or not row.reason.strip()):
        raise ValidationError('只能按原始确认金额、日期和期间完整冲销')
    check_revenue_period(member.organization, original.occurred_on)
    event = AccountingEvent.objects.select_for_update().select_related('claim').get(pk=original.accounting_event_id)
    if (event.organization_id != member.organization_id or event.source != original.source_ref
            or event.claim.consumer != 'management_revenue' or event.claim.source != original.performance_ref
            or event.claim.source_id != original.acceptance_id or event.claim_effect_cents != amount
            or event.reverses_event_id or event.postings.exists()
            or AccountingEvent.objects.filter(reverses_event=event).exists()):
        raise ValidationError('原核算来源不符或已有完整冲销')
    return AccountingEvent.objects.create(organization=member.organization, source=row.source_ref,
        event_type='management_revenue.reversed', occurred_on=event.occurred_on,
        posting_period=event.posting_period, algorithm_version=event.algorithm_version,
        reverses_event=event, claim=event.claim, claim_effect_cents=-amount, labor_origin_ref=None)


def check_revenue_close(org, cutoff):
    """Existing date-lock preflight must not strand known unfinished acceptance."""
    from pmo_plugins.delivery.models import Acceptance
    from pmo_plugins.management_revenue.models import RevenueRecognition
    rows = Acceptance.objects.filter(organization=org, confirmed_on__lte=cutoff)
    if rows.filter(status__in=['draft', 'submitted']).exists():
        raise ValidationError('锁账范围有待处理客户验收，请确认或明确撤回草稿')
    for row in rows.filter(status='confirmed'):
        income = RevenueRecognition.objects.filter(acceptance=row, status='posted').first()
        if income is None:
            raise ValidationError('锁账范围有已验收但尚未确认的管理收入')
        if income.is_reversed:
            raise ValidationError('管理收入已冲销，请先明确反冲验收，不能静默锁住更正来源')


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
            source = original.acceptance
            amount = row.recognized_net_cents if cls is RevenueRecognition else row.adjustment_cents
            source_ref = {'namespace': 'delivery', 'kind': 'acceptance', 'id': str(source.pk), 'version': source.confirmed_version}
            if (event.source != row.source_ref or event.organization_id != row.organization_id
                    or row.organization_id != original.organization_id or row.project_id != original.project_id
                    or original.project_id != source.project_id or original.organization_id != source.organization_id
                    or original.performance_ref != source_ref or event.claim.source != source_ref
                    or event.claim.source_id != source.pk or event.claim.organization_id != row.organization_id
                    or event.claim.consumer != 'management_revenue' or event.claim.amount_cents != original.recognized_net_cents
                    or original.recognized_net_cents != source.accepted_net_cents
                    or event.claim_effect_cents != amount or event.labor_origin_ref is not None
                    or event.occurred_on != original.occurred_on or event.posting_period != original.posting_period
                    or original.occurred_on != source.confirmed_on or event.postings.exists()):
                errors.append(f'revenue:{row.pk}:source_amount_or_scope')
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
