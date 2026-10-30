"""Read-only aging of active receivable open items (no AgingCase rows, no forecast changes).

Amounts come only from frozen receivable partitions; remaining equals the active open item gross
because this slice has no clearance model. Retention partitions use their release date as due date,
so an unreleased warranty row is never overdue.
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.access import require_member, require_project, visible_projects
from core.models import Membership, Project

from .models import OpenItem

READ_ROLES = {'owner', 'finance', 'pm', 'auditor'}
BUCKETS = ('not_due', '1_30', '31_60', '61_90', '90_plus')
BUCKET_LABELS = {'not_due': '未到期', '1_30': '逾期1-30天', '31_60': '逾期31-60天',
                 '61_90': '逾期61-90天', '90_plus': '逾期90天以上'}
PARTITION_LABELS = {'due': '到期款', 'retention': '质保款'}


def _fail(message):
    raise ValidationError(message)


def _fresh(member):
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if member.role not in READ_ROLES:
        raise PermissionDenied('无权查看应收账龄')
    # Historical aging stays readable after the settlement module is disabled.
    return member


def _yuan(cents):
    return format(Decimal(cents) / 100, '.2f')


def _as_of(value):
    if value is None:
        return timezone.localdate()
    if type(value) is date:
        return value
    if isinstance(value, str):
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() == value:
                return parsed
        except ValueError:
            pass
    _fail('账龄基准日须为有效的年月日')


def _aged(due_date, as_of):
    days = (as_of - due_date).days
    if days <= 0:
        return 0, 'not_due'
    if days <= 30:
        return days, '1_30'
    if days <= 60:
        return days, '31_60'
    if days <= 90:
        return days, '61_90'
    return days, '90_plus'


def _projects(member, project_id):
    projects = visible_projects(member)
    if member.role == 'pm':
        projects = projects.filter(manager=member)
    if project_id is None:
        return projects, None
    try:
        project = Project.objects.get(organization=member.organization, pk=project_id)
    except (Project.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('项目不存在或无权访问')
    require_project(member, project)
    if member.role == 'pm' and project.manager_id != member.pk:
        raise PermissionDenied('项目经理只能查看自己管理的项目账龄')
    return projects.filter(pk=project.pk), project


def _item_projection(item, as_of):
    overdue_days, bucket = _aged(item.due_date, as_of)
    settlement = item.settlement
    line_ref = settlement.contract_line_ref or {}
    return {
        'id': str(item.pk), 'open_item_ref': item.item_ref, 'version': item.version,
        'organization_id': str(item.organization_id), 'project_id': str(item.project_id),
        'project_code': item.project.code, 'project_name': item.project.name,
        'settlement_id': str(item.settlement_id), 'settlement_ref': settlement.settlement_ref,
        'contract_line_ref': line_ref,
        'contract_line_label': str(line_ref.get('id', '')),
        'partition_kind': item.partition_kind,
        'partition_label': PARTITION_LABELS.get(item.partition_kind, item.partition_kind),
        'partition_key': item.partition_key, 'term_ref': item.term_ref,
        'term_label': f"{item.term_ref.get('kind')} · {item.term_ref.get('id')}",
        'payee': item.payee_ref, 'scope': item.scope, 'source': item.source,
        'direction': item.direction, 'status': item.status, 'status_display': '有效',
        'gross_cents': item.gross_cents, 'remaining_cents': item.gross_cents,
        'tax_cents': item.tax_cents,
        'gross_display': _yuan(item.gross_cents), 'remaining_display': _yuan(item.gross_cents),
        'tax_display': _yuan(item.tax_cents),
        'due_date': item.due_date.isoformat(), 'due_display': item.due_date.isoformat(),
        'release_display': item.due_date.isoformat(),
        'occurred_on': item.occurred_on.isoformat(), 'release_condition': item.release_condition,
        'overdue_days': overdue_days, 'bucket': bucket,
        'bucket_label': BUCKET_LABELS[bucket],
    }


def open_item_aging(member, *, project_id=None, as_of=None):
    """Active receivable partitions bucketed by the inclusive Asia/Shanghai business date."""
    member = _fresh(member)
    basis = _as_of(as_of)
    projects, project = _projects(member, project_id)
    rows = OpenItem.objects.filter(organization=member.organization, project__in=projects,
                                   status='active').select_related('project', 'settlement')
    items = [_item_projection(row, basis) for row in rows.order_by('due_date', 'id')]
    buckets = {key: 0 for key in BUCKETS}
    for item in items:
        buckets[item['bucket']] += item['remaining_cents']
    retention = [item for item in items if item['partition_kind'] == 'retention']
    released = sum(item['remaining_cents'] for item in retention
                   if item['due_date'] <= basis.isoformat())
    overdue_cents = sum(buckets[key] for key in BUCKETS if key != 'not_due')
    return {
        'as_of': basis.isoformat(), 'organization_id': str(member.organization_id),
        'project_id': str(project.pk) if project else None,
        'items': items, 'buckets': buckets, 'bucket_labels': dict(BUCKET_LABELS),
        'retention': {
            'count': len(retention),
            'gross_cents': sum(item['remaining_cents'] for item in retention),
            'released_cents': released,
            'unreleased_cents': sum(item['remaining_cents'] for item in retention) - released,
            'overdue_cents': sum(item['remaining_cents'] for item in retention
                                 if item['bucket'] != 'not_due'),
        },
        'totals': {
            'count': len(items),
            'gross_cents': sum(item['remaining_cents'] for item in items),
            'remaining_cents': sum(item['remaining_cents'] for item in items),
            'overdue_cents': overdue_cents,
            'overdue_count': sum(1 for item in items if item['bucket'] != 'not_due'),
        },
        'unallocated_receipt_refs': [],
    }


def aging_export_rows(member, *, project_id=None, as_of=None):
    """Flattened read-only rows for CSV; same authorization and bucket rules as aging."""
    data = open_item_aging(member, project_id=project_id, as_of=as_of)
    return [{
        '账龄基准日': data['as_of'], '项目编号': item['project_code'], '项目名称': item['project_name'],
        '合同明细': item['contract_line_label'], '结算编号': item['settlement_id'],
        '分区类型': item['partition_label'], '条款': item['term_label'],
        '释放条件': item['release_condition'], '到期或释放日': item['due_date'],
        '逾期天数': item['overdue_days'], '账龄桶': item['bucket_label'],
        '含税（元）': item['gross_display'], '剩余（元）': item['remaining_display'],
        '税（元）': item['tax_display'], '状态': item['status_display'],
        '未清项编号': item['id'],
    } for item in data['items']]
