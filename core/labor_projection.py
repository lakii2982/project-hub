"""Existing time approval and read projections; never a second cost owner."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.utils import timezone

from .access import can_manage, require_project
from .labor_accounting import fresh_labor_member
from .models import AccountingEvent, LedgerPosting, Timesheet
from .services import audit, check_open, check_period


def _authorize_time(member, row, *, review=False):
    member = fresh_labor_member(member)
    if row.organization_id != member.organization_id:
        raise PermissionDenied('无权处理其他公司的投入')
    if row.project_id:
        require_project(member, row.project)
        if review and not can_manage(member, row.project) and member.role not in {'owner', 'finance'}:
            raise PermissionDenied('没有项目投入审批权限')
    elif member.role not in {'owner', 'finance'}:
        raise PermissionDenied('公司非项目投入由负责人或财务处理')
    return member


def _allocated(row):
    from pmo_plugins.costing.models import AllocationRun
    return AllocationRun.objects.filter(organization_id=row.organization_id,
        salary_pool__employee_id=row.employee_id, salary_pool__period=row.work_date.strftime('%Y-%m'),
        salary_pool__covered_through__gte=row.work_date, status='posted').exists()


def review_monthly_time(member, row, decision, note):
    member = _authorize_time(member, row, review=True)
    employee_user = row.employee.member.user_id if row.employee.member_id else None
    if member.user_id in {row.submitted_by_id, employee_user}:
        raise ValidationError('提交人与真实绑定业务本人不能自审')
    if row.status != 'submitted':
        if row.status == 'approved' and decision == 'approve':
            return row
        raise ValidationError('记录已处理，请刷新')
    check_period(member.organization, row.work_date)
    if row.project_id:
        check_open(row.project)
    if decision == 'reject' and not note.strip():
        raise ValidationError('退回必须填写原因')
    if decision == 'approve' and _allocated(row):
        raise ValidationError('覆盖期已有有效分配，先撤分配再批准补充投入')
    row.status = 'approved' if decision == 'approve' else 'rejected'
    row.reviewed_by, row.reviewed_at, row.review_note = member.user, timezone.now(), note[:300]
    row.version += 1
    if decision == 'approve':
        row.approved_input_snapshot = {'id': str(row.pk), 'version': row.version,
            'employee_id': str(row.employee_id), 'work_date': row.work_date.isoformat(),
            'minutes': row.minutes, 'project_id': str(row.project_id) if row.project_id else None,
            'non_project_destination_id': str(row.non_project_destination_id) if row.non_project_destination_id else None, 'target_kind': 'project' if row.project_id else 'non_project_pool',
            'target_id': str(row.project_id or row.non_project_destination_id),
            'submitted_by': str(row.submitted_by_id), 'reviewed_by': str(member.user_id)}
    row.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note', 'version', 'approved_input_snapshot'])
    audit(member, 'plugin.costing.time_' + row.status, row, {'minutes': row.minutes, 'version': row.version})
    return row


def reverse_monthly_time(member, row, note):
    member = _authorize_time(member, row)
    if member.role not in {'owner', 'finance'}:
        raise PermissionDenied('只有负责人或财务可撤销已批投入')
    check_period(member.organization, row.work_date)
    if _allocated(row):
        raise ValidationError('投入已被有效分配使用，先明确撤分配')
    if row.status == 'reversed':
        return row
    if row.status != 'approved':
        raise ValidationError('只能撤销已批投入')
    row.status = 'reversed'
    row.version += 1
    row.save(update_fields=['status', 'version'])
    audit(member, 'plugin.costing.time_reversed', row, {'reason': note})
    return row


def project_monthly_cost(project):
    return LedgerPosting.objects.filter(organization_id=project.organization_id, book='cost',
        scope_kind='project', scope_id=str(project.pk)).aggregate(total=Sum('amount_cents'))['total'] or 0


def project_cost_days(project):
    return LedgerPosting.objects.filter(organization_id=project.organization_id, book='cost',
        scope_kind='project', scope_id=str(project.pk)).values_list('accounting_event__occurred_on', 'amount_cents')


def project_labor_trace(member, project):
    require_project(member, project, finance=True)
    # PM receives only project aggregate facts, never per-person wages/minutes/rates.
    return {'cost_cents': project_monthly_cost(project),
            'label': '月度人工分配（仅本项目）',
            'source_count': LedgerPosting.objects.filter(organization_id=project.organization_id,
                scope_kind='project', scope_id=str(project.pk)).values('accounting_event').distinct().count()}
