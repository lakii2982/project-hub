"""Read-only reminder item and scan-envelope contract (PH-355/PH-364).

No persistence, delivery or business authorization lives here. All references and
recipient scopes are derived server-side from the current organization and role.
"""
from datetime import date
import hashlib
import json
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from .access import require_member, visible_projects
from .models import Membership

CONTRACT = 'core.reminders.read/v1'
CONTRACT_VERSION = 1
MAX_REMINDERS = 50
SEVERITIES = {
    'settlement_open_item_overdue': 'warning',
    'settlement_clearance_pending': 'info',
    'cash_plan_overdue': 'warning',
    'contract_change_pending': 'warning',
    'contract_terminated': 'blocker',
    'billable_confirmation_pending': 'warning',
    'tax_qualification_pending': 'warning',
    'budget_missing': 'warning',
    'budget_overrun': 'blocker',
    'close_precheck_blocker': 'blocker',
    'import_conflict_pending': 'warning',
    'agent_token_expiring': 'info',
    'read_model_missing': 'info',
    'module_disabled_history': 'info',
}
OWNERS = {'contracts', 'settlement', 'cash', 'time', 'invoicing', 'costing',
          'delivery', 'onboarding', 'core'}
NAMESPACES = OWNERS | {'insights', 'management_revenue'}


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def scan_digest(envelope):
    """Hash the complete envelope except volatile generated_at and this field."""
    payload = dict(envelope)
    payload.pop('generated_at', None)
    payload.pop('scan_digest', None)
    return _digest(payload)


def _day(value, *, allow_future=False):
    if value is None:
        result = timezone.localdate()
        allow_future = True
    elif isinstance(value, date):
        result = value
    else:
        try:
            result = date.fromisoformat(str(value))
        except (TypeError, ValueError):
            raise ValidationError('as_of 须为YYYY-MM-DD日期')
    if not allow_future and result > timezone.localdate():
        raise ValidationError('as_of 不能在未来')
    return result


def _period(value):
    if value in (None, ''):
        return None
    try:
        parsed = date.fromisoformat(str(value) + '-01')
    except (TypeError, ValueError):
        raise ValidationError('period 须为YYYY-MM')
    if parsed.strftime('%Y-%m') != str(value):
        raise ValidationError('period 须为YYYY-MM')
    return str(value)


def _ref(value, *, allow_null=False):
    if value is None and allow_null:
        return None
    if not isinstance(value, dict) or set(value) != {'namespace', 'kind', 'id', 'version'}:
        raise ValidationError('来源引用形状无效')
    namespace, kind = value['namespace'], value['kind']
    if namespace not in NAMESPACES or not isinstance(kind, str) or not kind:
        raise ValidationError('来源引用类型无效')
    if not str(value['id']) or type(value['version']) is not int or value['version'] < 1:
        raise ValidationError('来源引用标识或版本无效')
    return {'namespace': namespace, 'kind': kind, 'id': str(value['id']),
            'version': value['version']}


def _scope(value):
    if not isinstance(value, dict) or set(value) != {'kind', 'id'}:
        raise ValidationError('收件范围形状无效')
    if value['kind'] not in {'member', 'role', 'project'} or not str(value['id']):
        raise ValidationError('收件范围无效')
    return {'kind': value['kind'], 'id': str(value['id'])}


def build_reminder(member, *, issue_code, source_ref, source_owner, project_ref,
                   recipient_scope, title, summary, due_on, as_of, deeplink,
                   refs=(), missing=()):
    """Build one deterministic item; no argument is accepted as a trusted client input."""
    if issue_code not in SEVERITIES or source_owner not in OWNERS:
        raise ValidationError('提醒类型或来源 owner 未注册')
    source_ref = _ref(source_ref)
    project_ref = _ref(project_ref, allow_null=True)
    if project_ref is not None and (project_ref['namespace'] != 'core'
                                    or project_ref['kind'] != 'project'):
        raise ValidationError('项目引用无效')
    recipient_scope = _scope(recipient_scope)
    refs = [_ref(ref) for ref in refs][:MAX_REMINDERS]
    due = _day(due_on, allow_future=True) if due_on is not None else None
    day = _day(as_of, allow_future=True)
    key = _digest({
        'organization_id': str(member.organization_id),
        'source_ref': source_ref,
        'issue_code': issue_code,
        'recipient_scope': recipient_scope,
        'source_state': 'active',
    })
    return {
        'reminder_key': key,
        'reminder_ref': {'namespace': 'core', 'kind': 'reminder_ref',
                         'id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'pmo:reminder:' + key)),
                         'version': 1},
        'issue_code': issue_code,
        'severity': SEVERITIES[issue_code],
        'source_ref': source_ref,
        'source_owner': source_owner,
        'project_ref': project_ref,
        'recipient_scope': recipient_scope,
        'refs': refs,
        'title': str(title).strip()[:160],
        'summary': str(summary).strip()[:500],
        'due_on': due.isoformat() if due else None,
        'as_of': day.isoformat(),
        'missing': sorted(set(missing)),
        'deeplink': str(deeplink),
    }


def _reader(member):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(fresh)
    if fresh.role not in {'owner', 'finance', 'auditor', 'pm', 'member'}:
        raise PermissionDenied('角色无权读取提醒')
    return fresh


def _visible_item(item, member, managed_project_ids):
    scope = item['recipient_scope']
    if member.role in {'owner', 'finance', 'auditor'}:
        return scope['kind'] in {'role', 'project'}
    if member.role == 'pm':
        return scope['kind'] == 'project' and scope['id'] in managed_project_ids
    return scope['kind'] == 'member' and scope['id'] == str(member.pk)


def scan_reminders(member, *, as_of=None, period=None):
    """Return the frozen read-only scan envelope, with read-time authorization."""
    member = _reader(member)
    day, month = _day(as_of), _period(period)
    if member.role == 'member':
        projects = []
    else:
        projects = list(visible_projects(member))
        if member.role == 'pm':
            projects = [project for project in projects if project.manager_id == member.pk]
    managed_project_ids = {str(project.pk) for project in projects}

    from . import reminder_scan
    raw = reminder_scan.scan(member, as_of=day, period=month, projects=projects)
    rows = [row for row in raw['reminders']
            if _visible_item(row, member, managed_project_ids)]
    unique = {row['reminder_key']: row for row in rows}
    rows = sorted(unique.values(), key=lambda row: (
        row['issue_code'], row['due_on'] or '', row['source_ref']['namespace'],
        row['source_ref']['kind'], row['source_ref']['id'],
        row['recipient_scope']['kind'], row['recipient_scope']['id']))
    truncated = len(rows) > MAX_REMINDERS
    rows = rows[:MAX_REMINDERS]
    issues = []
    seen_issues = set()
    for issue in raw['issues']:
        normalized = {
            'issue_code': issue['issue_code'],
            'refs': [_ref(ref) for ref in issue.get('refs', [])][:MAX_REMINDERS],
            'expected': issue.get('expected'),
            'actual': issue.get('actual'),
        }
        marker = _canonical(normalized)
        if marker not in seen_issues:
            seen_issues.add(marker)
            issues.append(normalized)
    scope_kind = ('member' if member.role == 'member'
                  else 'managed_projects' if member.role == 'pm' else 'organization')
    scope_id = str(member.pk) if scope_kind != 'organization' else str(member.organization_id)
    scan_key = _digest({
        'organization_id': str(member.organization_id), 'as_of': day.isoformat(),
        'period': month, 'role': member.role, 'scope_kind': scope_kind,
        'scope_id': scope_id, 'contract_version': CONTRACT_VERSION,
    })
    envelope = {
        'contract_version': CONTRACT_VERSION,
        'contract': CONTRACT,
        'scan_key': scan_key,
        'as_of': day.isoformat(),
        'period': month,
        'role': member.role,
        'read_only': True,
        'notifications_sent': 0,
        'reminders': rows,
        'missing': sorted(set(raw['missing'])),
        'issues': issues,
        'truncated': truncated,
    }
    envelope['scan_digest'] = scan_digest(envelope)
    envelope['generated_at'] = timezone.now().isoformat()
    return envelope
