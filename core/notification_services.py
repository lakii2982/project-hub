"""Internal per-member notification snapshots and read/ignore state (PH-340)."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from .access import require_member
from .models import Membership, Notification, Project
from .plugin_runtime import is_enabled
from .reminder_services import scan_reminders
from .services import lock_company


def _fresh(member):
    try:
        row = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(row)
    return row


def sync_notifications(member, *, as_of=None, period=None):
    """Materialize only the current member's PH-355 items; no external side effects."""
    member = _fresh(member)
    if member.role == 'auditor':
        raise PermissionDenied('审阅账户只能读取，不能同步通知')
    with transaction.atomic():
        lock_company(member)
        result = scan_reminders(member, as_of=as_of, period=period)
        created = 0
        for item in result['reminders']:
            _, was_created = Notification.objects.get_or_create(
                member=member, reminder_key=item['reminder_key'],
                defaults={
                    'organization': member.organization,
                    'reminder_ref': item['reminder_ref'], 'source_ref': item['source_ref'],
                    'project_ref': item['project_ref'], 'issue_code': item['issue_code'],
                    'severity': item['severity'], 'title': item['title'],
                    'summary': item['summary'], 'due_on': item['due_on'],
                    'as_of': item['as_of'], 'deeplink': item['deeplink'],
                })
            created += int(was_created)
        total = Notification.objects.filter(member=member, ignored_at__isnull=True).count()
    return {'created': created, 'total': total, 'scan_key': result['scan_key'],
            'scan_digest': result['scan_digest'], 'missing': result['missing'],
            'notifications_sent': 0}


def _source_available(member, row):
    namespace = row.source_ref.get('namespace')
    if row.issue_code == 'close_precheck_blocker':
        return (is_enabled(member.organization, 'delivery')
                and is_enabled(member.organization, 'insights'))
    if row.issue_code == 'cash_plan_overdue':
        return is_enabled(member.organization, 'cash')
    if namespace == 'core':
        return True
    return is_enabled(member.organization, namespace)


def _scope_rows(member, rows):
    """Apply current role/project scope to both reading and state changes."""
    if member.role == 'member':
        rows = [row for row in rows if row.issue_code == 'agent_token_expiring']
    rows = list(rows)
    if member.role == 'pm':
        managed = {str(pk) for pk in Project.objects.filter(
            organization=member.organization, manager=member).values_list('pk', flat=True)}
        rows = [row for row in rows if row.project_ref
                and str(row.project_ref.get('id')) in managed]
    return rows


def notification_snapshot(member, *, status=None, include_ignored=False):
    """Read-time role/scope and source-state checks over personal history."""
    member = _fresh(member)
    allowed = {None, '', 'active', 'unread', 'read', 'ignored', 'all'}
    if status not in allowed:
        raise ValidationError('通知状态筛选无效')
    rows = Notification.objects.filter(
        organization=member.organization, member=member).order_by('-created_at', '-id')
    if status == 'ignored':
        rows = rows.filter(ignored_at__isnull=False)
    elif status == 'read':
        rows = rows.filter(read_at__isnull=False, ignored_at__isnull=True)
    elif status == 'unread':
        rows = rows.filter(read_at__isnull=True, ignored_at__isnull=True)
    elif not include_ignored and status != 'all':
        rows = rows.filter(ignored_at__isnull=True)

    rows = _scope_rows(member, rows)

    active_keys = {row['reminder_key'] for row in scan_reminders(member)['reminders']}
    for row in rows:
        row.source_available = _source_available(member, row)
        row.source_active = row.source_available and row.reminder_key in active_keys
    if status in {None, '', 'active', 'unread'} and not include_ignored:
        # Disabled sources remain explicitly historical; resolved enabled facts leave the
        # current/unread list but stay visible under read/ignored/all history filters.
        rows = [row for row in rows if row.source_active or not row.source_available]
    return rows


def mark_notification(member, notification_id, *, action):
    """Mark only a member's own notification; never changes owner business facts."""
    member = _fresh(member)
    if member.role == 'auditor':
        raise PermissionDenied('审阅账户不能修改通知状态')
    if action not in {'read', 'ignore'}:
        raise ValidationError('通知操作仅支持已读或忽略')
    with transaction.atomic():
        lock_company(member)
        row = Notification.objects.filter(
            pk=notification_id, member=member, organization=member.organization).first()
        if row is None or not _scope_rows(member, [row]):
            raise PermissionDenied('通知不存在、已超出当前范围或无权操作')
        field = 'read_at' if action == 'read' else 'ignored_at'
        if getattr(row, field) is None:
            setattr(row, field, timezone.now())
            row.save(update_fields=[field])
        return row
