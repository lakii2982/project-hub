"""Month-start, explicitly approved routing of the existing time fact."""
import uuid
from datetime import date

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .labor_accounting import fresh_labor_member
from .models import LaborCutover, Timesheet, AccountingPeriod, LedgerEntry
from .services import audit, lock_company


def _date(value):
    if type(value) is date:
        return value
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValidationError('启用日期须为真实月首')


def _check(org, effective_from):
    if effective_from.day != 1:
        raise ValidationError('月度人工必须从月首启用')
    if org.locked_through and effective_from <= org.locked_through:
        raise ValidationError('启用月已涉及锁账')
    if AccountingPeriod.objects.filter(organization=org, period=effective_from.strftime('%Y-%m')).exclude(status='active', closed_through=None).exists():
        raise ValidationError('启用会计期间已关闭')
    if LedgerEntry.objects.filter(project__organization=org, timesheet__work_date__gte=effective_from, book="cost").exists():
        raise ValidationError("启用月已有旧人工账来源，包括已冲销来源；请选择干净未来月")
    if Timesheet.objects.filter(project__organization=org, work_date__gte=effective_from,
            accounting_mode='legacy_snapshot', status__in=['submitted', 'approved']).exists():
        raise ValidationError('月首起已有旧口径待批或已批工时；不得重写历史或改变真实工作日期')


@transaction.atomic
def propose_cutover(member, effective_from, request_token):
    org = lock_company(member)
    member = fresh_labor_member(member, finance=True)
    if member.role != 'owner':
        raise PermissionDenied('公司负责人提出人工口径启用')
    effective_from = _date(effective_from)
    try:
        token = uuid.UUID(str(request_token))
    except (ValueError, TypeError):
        raise ValidationError('请求编号无效')
    existing = LaborCutover.objects.filter(organization=org).first()
    if existing:
        if existing.request_token == token and existing.created_by_id == member.user_id and existing.effective_from == effective_from:
            return existing
        raise ValidationError('公司已有启用申请或已启用，不能另建口径覆盖原记录')
    _check(org, effective_from)
    row = LaborCutover.objects.create(organization=org, effective_from=effective_from,
        created_by=member.user, request_token=token)
    audit(member, 'plugin.costing.cutover_proposed', row, {'effective_from': effective_from})
    return row


@transaction.atomic
def approve_cutover(member, cutover_id, expected_version, request_token):
    org = lock_company(member)
    member = fresh_labor_member(member, finance=True)
    try:
        uuid.UUID(str(request_token))
    except (ValueError, TypeError):
        raise ValidationError('请求编号无效')
    row = LaborCutover.objects.select_for_update().get(pk=cutover_id, organization=org)
    if row.created_by_id == member.user_id:
        raise ValidationError('提出人不能审批自己的启用申请')
    if row.status == 'approved':
        if row.approved_by_id == member.user_id:
            return row
        raise ValidationError('启用申请已处理')
    if type(expected_version) is not int or row.version != expected_version:
        raise ValidationError('启用申请已更新，请刷新')
    _check(org, row.effective_from)
    from .masterdata import ensure_labor_destinations
    ensure_labor_destinations(member)
    row.status, row.approved_by, row.approved_at = 'approved', member.user, timezone.now()
    row.version += 1
    row.approval_audit = audit(member, 'plugin.costing.cutover_approved', row,
        {'effective_from': row.effective_from, 'proposed_by': row.created_by_id})
    row.save(update_fields=['status', 'version', 'approved_by', 'approved_at', 'approval_audit'])
    return row


def monthly_mode(org, work_date):
    # Mode remains authoritative even when costing is disabled: never fall back.
    return LaborCutover.objects.filter(organization=org, status='approved', effective_from__lte=work_date).exists()


def check_monthly_close(org, cutoff):
    """Keep existing date lock aware of unresolved monthly labor facts."""
    from pmo_plugins.costing.models import SalaryPool
    monthly = Timesheet.objects.filter(organization=org, accounting_mode='monthly_pool', work_date__lte=cutoff)
    if monthly.filter(status='submitted').exists():
        raise ValidationError('锁账范围仍有待审批月度投入（含非项目）')
    for row in monthly.filter(status='approved').select_related('employee'):
        pool = SalaryPool.objects.filter(organization=org, employee=row.employee,
            period=row.work_date.strftime('%Y-%m'), status__in=['approved', 'reconciled'],
            covered_through__gte=row.work_date, allocation_runs__status='posted').first()
        if not pool:
            raise ValidationError('锁账范围有未确认工资或未完成分配的投入')
    for pool in SalaryPool.objects.filter(organization=org, period__lte=cutoff.strftime('%Y-%m')).exclude(status='superseded'):
        if pool.status not in {'approved', 'reconciled'} or not pool.allocation_runs.filter(status='posted').exists():
            raise ValidationError('锁账范围有工资/容量/分配缺项')
        from .models import LedgerPosting, NonProjectDestination
        dest = NonProjectDestination.objects.get(organization=org, code='UNALLOCATED')
        from django.db.models import Sum
        balance = LedgerPosting.objects.filter(organization=org, accounting_event__labor_origin_ref__id=str(pool.pk),
            scope_kind='nonproject', scope_id=str(dest.pk)).aggregate(total=Sum('amount_cents'))['total'] or 0
        if balance:
            raise ValidationError('锁账范围仍有未分配人工，须补齐实际依据')
