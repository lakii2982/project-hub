"""Core-owned, append-only postings for the monthly labor business only."""
import uuid
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .access import require_member
from .models import (AccountingEvent, AccountingPeriod, LedgerPosting, Membership,
                     NonProjectDestination, Project, SourceConsumption, LaborCutover)
from .services import lock_company

ALGORITHM = 'monthly_group_half_up_largest_remainder_v1'


def fresh_labor_member(member, *, finance=False):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist) as error:
        raise PermissionDenied('无效成员') from error
    require_member(fresh)
    from .plugin_runtime import is_enabled
    if not is_enabled(fresh.organization, 'costing'):
        raise PermissionDenied('月度人工模块已停用，不能退回旧算法记账')
    if finance and fresh.role not in {'owner', 'finance'}:
        raise PermissionDenied('只有公司负责人或财务可办理人工成本')
    return fresh


def period_start(period):
    if not isinstance(period, str) or len(period) != 7:
        raise ValidationError('期间须为YYYY-MM')
    try:
        result = date.fromisoformat(period + '-01')
    except ValueError as error:
        raise ValidationError('期间须为YYYY-MM') from error
    if result.strftime('%Y-%m') != period:
        raise ValidationError('期间须为YYYY-MM')
    return result


def check_labor_period(org, period, covered_through):
    start = period_start(period)
    if type(covered_through) is not date or covered_through < start or covered_through.strftime('%Y-%m') != period:
        raise ValidationError('发生覆盖日必须在该月')
    if covered_through > timezone.localdate():
        raise ValidationError('实际人工发生日期不能在未来')
    if org.locked_through and start <= org.locked_through:
        raise ValidationError('月度覆盖与已锁期间相交，不能换发生日或开放月绕过')
    cutover = LaborCutover.objects.filter(organization=org, status='approved').first()
    if not cutover or start < cutover.effective_from:
        raise ValidationError('该月尚未启用月度人工口径')
    existing = AccountingPeriod.objects.filter(organization=org, period=period).first()
    if existing and (existing.status != 'active' or existing.closed_through is not None):
        raise ValidationError('原会计期间已关闭')


def ensure_period(org, period):
    # Called only inside a company-locked authorized business transaction.
    start = period_start(period)
    cutover = LaborCutover.objects.filter(organization=org, status='approved').first()
    if not cutover or start < cutover.effective_from:
        raise ValidationError('未批准的月度人工期间')
    result, _ = AccountingPeriod.objects.get_or_create(organization=org, period=period)
    if result.status != 'active' or result.closed_through is not None:
        raise ValidationError('原期间已关闭')
    return result


def _amount(value):
    if type(value) is not int or not 0 <= value <= 10**12:
        raise ValidationError('人工金额须为非负整数分且不超过100亿元')
    return value


def _pool(member, pool):
    if pool._meta.label_lower != 'costing.salarypool' or pool.organization_id != member.organization_id:
        raise PermissionDenied('人工来源必须属于本公司')
    if pool.status not in {'approved', 'reconciled'} or not pool.approved_salary_snapshot or pool.reversed_event_id:
        raise ValidationError('工资来源未独立确认或已终态冲销')
    check_labor_period(member.organization, pool.period, pool.covered_through)
    ensure_period(member.organization, pool.period)
    return _amount(pool.total_comprehensive_cost_cents)


def _claim(org, pool, purpose, event_id, amount):
    key = f'{purpose}:{pool.employee_id}/{pool.period}'
    claim, created = SourceConsumption.objects.get_or_create(
        organization=org, consumer='cost', source_id=pool.pk, coverage_key=key,
        defaults={'source': pool.source_ref, 'amount_cents': amount,
                  'event_ref': {'namespace': 'core', 'kind': 'accounting_event', 'id': str(event_id), 'version': 1}})
    if not created:
        if purpose == 'recognition':
            raise ValidationError('同一工资来源只能确认一次，包括零金额与已冲销来源')
        if claim.amount_cents != amount or claim.source['id'] != str(pool.pk):
            raise ValidationError('原分配来源金额不可替换')
        if claim.events.filter(reverses_event__isnull=True, reversal__isnull=True).exists():
            raise ValidationError('原分配覆盖尚未释放，包括零金额')
        if (claim.events.aggregate(total=Sum('claim_effect_cents'))['total'] or 0) != 0:
            raise ValidationError('原分配消费尚未释放')
    return claim


def _event(member, pool, source, purpose, amount):
    event_id = uuid.uuid4()
    claim = _claim(member.organization, pool, purpose, event_id, amount)
    return AccountingEvent.objects.create(id=event_id, organization=member.organization,
        source=source, event_type='labor.' + purpose, occurred_on=pool.covered_through,
        posting_period=pool.period, algorithm_version=ALGORITHM, claim=claim,
        claim_effect_cents=amount, labor_origin_ref=pool.source_ref)


def _line(event, index, amount, kind, target, reverse=None):
    if type(amount) is not int or abs(amount) > 10**12:
        raise ValidationError('成本行金额超出范围')
    return LedgerPosting.objects.create(organization=event.organization, accounting_event=event,
        line_index=index, amount_cents=amount, scope_kind=kind, scope_id=str(target), reverses_posting=reverse)


@transaction.atomic
def post_salary_confirmation(member, pool):
    lock_company(member)
    member = fresh_labor_member(member, finance=True)
    amount = _pool(member, pool)
    if pool.recognition_event_id:
        raise ValidationError('工资已确认，不得重复记成本')
    destination = NonProjectDestination.objects.get(organization=member.organization, code='UNALLOCATED')
    event = _event(member, pool, pool.source_ref, 'recognition', amount)
    _line(event, 0, amount, 'nonproject', destination.pk)
    return event


@transaction.atomic
def post_allocation(member, pool, run, targets):
    lock_company(member)
    member = fresh_labor_member(member, finance=True)
    amount = _pool(member, pool)
    if not pool.recognition_event_id or pool.recognition_event.reverses_event_id or AccountingEvent.objects.filter(reverses_event_id=pool.recognition_event_id).exists():
        raise ValidationError('原工资确认不可用')
    if run._meta.label_lower != 'costing.allocationrun' or run.salary_pool_id != pool.pk or run.organization_id != member.organization_id:
        raise ValidationError('分配必须属于原工资来源')
    if run.accounting_event_id or pool.allocation_runs.filter(status='posted').exclude(pk=run.pk).exists():
        raise ValidationError('同一工资来源已有有效分配')
    if sum(_amount(t['allocated_cents']) for t in targets) != amount:
        raise ValidationError('分配结果必须逐分守恒')
    checked = []
    for target in targets:
        kind = target['target_kind']
        if kind == 'project':
            valid = Project.objects.filter(pk=target['target_id'], organization=member.organization).exists()
            scope = 'project'
        elif kind == 'non_project_pool':
            valid = NonProjectDestination.objects.filter(pk=target['target_id'], organization=member.organization, active=True).exists()
            scope = 'nonproject'
        else:
            raise ValidationError('不支持的人工分配去向')
        if not valid:
            raise ValidationError('分配去向不属于本公司或已停用')
        checked.append((scope, target['target_id'], target['allocated_cents']))
    destination = NonProjectDestination.objects.get(organization=member.organization, code='UNALLOCATED')
    event = _event(member, pool, run.source_ref, 'distribution', amount)
    _line(event, 0, -amount, 'nonproject', destination.pk)
    for index, (kind, target, value) in enumerate(checked, 1):
        _line(event, index, value, kind, target)
    return event


@transaction.atomic
def reverse_labor_event(member, event, reason):
    lock_company(member)
    member = fresh_labor_member(member, finance=True)
    event = AccountingEvent.objects.select_for_update().get(pk=event.pk, organization=member.organization)
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError('必须填写冲销原因')
    if event.reverses_event_id or AccountingEvent.objects.filter(reverses_event=event).exists():
        raise ValidationError('该事件已冲销或本身是冲销事件')
    if event.source.get('namespace') != 'costing' or event.source.get('kind') not in {'salary_pool', 'allocation_run'}:
        raise ValidationError('本服务只支持月度人工事件')
    from pmo_plugins.costing.models import SalaryPool
    pool = SalaryPool.objects.get(pk=event.labor_origin_ref['id'], organization=member.organization)
    check_labor_period(member.organization, event.posting_period, event.occurred_on)
    if event.source['kind'] == 'salary_pool' and pool.allocation_runs.filter(status='posted').exists():
        raise ValidationError('先明确撤销有效分配，不能自动级联')
    reversed_event = AccountingEvent.objects.create(organization=member.organization, source=event.source,
        event_type=event.event_type + '.reversed', occurred_on=event.occurred_on,
        posting_period=event.posting_period, algorithm_version=event.algorithm_version,
        reverses_event=event, claim=event.claim, claim_effect_cents=-event.claim_effect_cents,
        labor_origin_ref=event.labor_origin_ref)
    for index, original in enumerate(event.postings.order_by('line_index')):
        _line(reversed_event, index, -original.amount_cents, original.scope_kind, original.scope_id, original)
    return reversed_event
