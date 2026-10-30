"""PH-281 read-only membership / project-scope / authorization audit view.

Composes already-delivered identity facts (core.Membership, ProjectMember,
AgentToken, AuditEvent, Employee binding) into read-only projections for
owner/finance/auditor operations and a pm project-scoped summary. No write,
audit, receipt, session persistence, migration or canonical event is produced.
"""
from datetime import date, datetime, time, timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Max, Q
from django.utils import timezone

from .access import require_member
from .models import AgentToken, AuditEvent, Employee, Membership, Project, ProjectMember

READ_ROLES = {'owner', 'finance', 'auditor'}
COMPANY_SCOPE_ROLES = {'owner', 'finance', 'auditor'}
AUDIT_ACTIONS = ('identity.membership.changed', 'agent_token.created', 'agent_token.revoked')
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50
AUDIT_LIMIT = 200


def _fresh(member):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist) as error:
        raise PermissionDenied('无效公司成员上下文') from error
    require_member(fresh)
    return fresh


def _reader(member):
    """Return (fresh member, scope) where scope is ``org`` or ``project`` (pm)."""
    fresh = _fresh(member)
    if fresh.role in READ_ROLES:
        return fresh, 'org'
    if fresh.role == 'pm':
        return fresh, 'project'
    raise PermissionDenied('无权查看成员与授权视图')


def _as_of(value):
    if value in (None, ''):
        return timezone.localdate()
    if isinstance(value, datetime):
        parsed = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        try:
            parsed = date.fromisoformat(str(value))
        except (TypeError, ValueError):
            raise ValidationError('快照日期须为有效的年月日')
        if parsed.isoformat() != str(value):
            raise ValidationError('快照日期须为有效的年月日')
    if parsed > timezone.localdate():
        raise ValidationError('快照日期不能在未来')
    return parsed


def _cutoff(as_of):
    return timezone.make_aware(datetime.combine(as_of, time.max))


def _since(value):
    if value in (None, ''):
        return None
    try:
        parsed = date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise ValidationError('起始日期须为有效的年月日')
    if parsed.isoformat() != str(value):
        raise ValidationError('起始日期须为有效的年月日')
    return parsed


def _page(value):
    try:
        page = 1 if value in (None, '') else int(value)
    except (TypeError, ValueError):
        raise ValidationError('页码无效')
    if page < 1:
        raise ValidationError('页码无效')
    return page


def _page_size(value):
    try:
        size = int(value or DEFAULT_PAGE_SIZE)
    except (TypeError, ValueError):
        raise ValidationError('每页条数无效')
    if not 1 <= size <= MAX_PAGE_SIZE:
        raise ValidationError('每页条数须在 1..200')
    return size


def _display(user):
    return user.get_full_name() or user.username


def _project_scope(memberships):
    memberships = list(memberships)
    ids = [row.pk for row in memberships]
    scope = {pk: [] for pk in ids}
    for row in ProjectMember.objects.filter(member_id__in=ids).select_related('project'):
        scope.setdefault(row.member_id, []).append({
            'project_id': str(row.project_id), 'code': row.project.code,
            'name': row.project.name})
    return scope


def _token_stats(memberships, cutoff):
    memberships = list(memberships)
    ids = [row.pk for row in memberships]
    result = {pk: {'total': 0, 'active': 0, 'revoked': 0, 'expired': 0,
                   'last_activity': None} for pk in ids}
    for token in AgentToken.objects.filter(member_id__in=ids):
        bucket = result[token.member_id]
        bucket['total'] += 1
        if token.revoked_at is not None:
            bucket['revoked'] += 1
        elif token.expires_at > cutoff:
            bucket['active'] += 1
        else:
            bucket['expired'] += 1
        activity = token.revoked_at or token.last_used_at or token.created_at
        if bucket['last_activity'] is None or activity > bucket['last_activity']:
            bucket['last_activity'] = activity
    return result


def _last_changes(memberships):
    memberships = list(memberships)
    ids = [str(row.pk) for row in memberships]
    rows = {}
    for event in AuditEvent.objects.filter(
            organization=memberships[0].organization, action='identity.membership.changed',
            entity_id__in=ids).order_by('created_at'):
        rows[event.entity_id] = {'action': event.action, 'created_at': event.created_at}
    return rows


def _visible_memberships(fresh, scope):
    qs = Membership.objects.filter(organization=fresh.organization).select_related('user')
    if scope == 'org':
        return qs
    projects = Project.objects.filter(organization=fresh.organization, manager=fresh)
    visible_ids = set(ProjectMember.objects.filter(project__in=projects).values_list('member_id', flat=True))
    visible_ids.add(fresh.pk)
    return qs.filter(pk__in=visible_ids)


def _member_entry(fresh, scope, row, as_of_cutoff, scope_map, token_map, changes,
                  managed_ids=None):
    entry = {
        'member_ref': str(row.pk),
        'display_name': _display(row.user),
        'role': row.role,
        'active': row.active,
        'version': row.version,
        'project_scope': [item for item in scope_map.get(row.pk, [])
                          if managed_ids is None
                          or int(item['project_id']) in managed_ids],
        'last_changed_at': (changes.get(str(row.pk)) or {}).get('created_at'),
        'last_changed_action': (changes.get(str(row.pk)) or {}).get('action'),
    }
    if scope == 'org':
        stats = token_map.get(row.pk, {})
        entry['tokens'] = {
            'total': stats.get('total', 0), 'active': stats.get('active', 0),
            'revoked': stats.get('revoked', 0), 'expired': stats.get('expired', 0),
            'last_activity_at': stats.get('last_activity'),
        }
    else:
        # pm summary: no other members' credential lifecycle details.
        entry['tokens'] = None
        entry['last_changed_at'] = None
        entry['last_changed_action'] = None
    return entry


def member_access_snapshot(member, *, as_of=None, page=1, page_size=DEFAULT_PAGE_SIZE):
    """Read-only member/role/project-scope/token summary; derived and paginated."""
    fresh, scope = _reader(member)
    as_of_date = _as_of(as_of)
    page_number, size = _page(page), _page_size(page_size)
    cutoff = _cutoff(as_of_date)
    rows = list(_visible_memberships(fresh, scope).order_by('pk'))
    scopes = _project_scope(rows)
    tokens = _token_stats(rows, cutoff) if scope == 'org' else {}
    changes = _last_changes(rows) if rows else {}
    managed_ids = None
    if scope == 'project':
        managed_ids = set(Project.objects.filter(
            organization=fresh.organization, manager=fresh).values_list('pk', flat=True))
    entries = [_member_entry(fresh, scope, row, cutoff, scopes, tokens, changes,
                             managed_ids)
               for row in rows]
    start = (page_number - 1) * size
    page_items = entries[start:start + size]
    return {'as_of': as_of_date.isoformat(), 'scope': scope, 'role': fresh.role,
            'page': page_number, 'page_size': size, 'total': len(entries),
            'truncated': len(entries) > start + size, 'members': page_items}


def member_access_audit(member, *, member_id=None, since=None, as_of=None, limit=AUDIT_LIMIT):
    """Read-only authorization audit summary; no payload secrets or token hashes."""
    fresh, scope = _reader(member)
    as_of_date = _as_of(as_of)
    since_date = _since(since)
    if limit is None:
        limit = AUDIT_LIMIT
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise ValidationError('审计条数无效')
    if not 1 <= limit <= AUDIT_LIMIT:
        raise ValidationError('审计条数须在 1..200')
    visible = _visible_memberships(fresh, scope)
    visible_ids = {str(pk) for pk in visible.values_list('pk', flat=True)}
    events = AuditEvent.objects.filter(
        organization=fresh.organization, action__in=AUDIT_ACTIONS)
    events = events.filter(created_at__lte=_cutoff(as_of_date))
    if scope == 'project':
        # pm must never see other projects' identity/token audit: always scope the
        # event stream to the managed-project member set, even without member_id.
        visible_token_ids = [str(pk) for pk in AgentToken.objects.filter(
            member_id__in=[int(pk) for pk in visible_ids]).values_list('pk', flat=True)]
        events = events.filter(
            Q(entity_id__in=visible_ids)
            | Q(payload__membership_id__in=visible_ids)
            | Q(payload__target__in=visible_ids)
            | Q(entity='AgentToken', entity_id__in=visible_token_ids))
    if since_date is not None:
        events = events.filter(created_at__gte=timezone.make_aware(datetime.combine(since_date, time.min)))
    if member_id:
        target = str(member_id)
        if target not in visible_ids:
            raise PermissionDenied('无权查看该成员审计')
        token_ids = [str(pk) for pk in AgentToken.objects.filter(
            member_id=int(target)).values_list('pk', flat=True)]
        events = events.filter(Q(entity_id=target)
                               | Q(payload__membership_id=target)
                               | Q(payload__target=target)
                               | Q(entity='AgentToken', entity_id__in=token_ids))
    events = list(events.order_by('-created_at', '-id')[:limit])
    result = []
    for event in events:
        payload = event.payload or {}
        safe = {key: payload.get(key) for key in
                ('changed_fields', 'role', 'active', 'project_scope_ids', 'name')
                if key in payload}
        result.append({
            'action': event.action, 'entity': event.entity, 'entity_id': event.entity_id,
            'created_at': event.created_at,
            'actor': _display(event.actor) if event.actor else None,
            'summary': safe,
        })
    return {'as_of': as_of_date.isoformat(), 'scope': scope, 'events': result,
            'total': len(result), 'truncated': len(events) == limit}


def member_access_issues(member, *, as_of=None):
    """Read-only issue list; no write, no issue resolution."""
    fresh, scope = _reader(member)
    as_of_date = _as_of(as_of)
    cutoff = _cutoff(as_of_date)
    rows = list(_visible_memberships(fresh, scope).order_by('pk'))
    ids = [row.pk for row in rows]
    issues = []
    inactive_active_tokens = AgentToken.objects.filter(
        member_id__in=ids, member__active=False, revoked_at__isnull=True,
        expires_at__gt=cutoff).select_related('member')
    if inactive_active_tokens:
        issues.append({'code': 'inactive_member_active_token',
                       'summary': f'{inactive_active_tokens.count()} 个停用成员仍持有效 Agent 令牌',
                       'refs': [str(row.member_id) for row in inactive_active_tokens[:5]]})
    expired_tokens = AgentToken.objects.filter(
        member_id__in=ids, revoked_at__isnull=True, expires_at__lte=cutoff)
    if expired_tokens:
        issues.append({'code': 'expired_token_not_revoked',
                       'summary': f'{expired_tokens.count()} 个过期 Agent 令牌未撤销',
                       'refs': [str(row.member_id) for row in expired_tokens[:5]]})
    company_roles_with_scope = ProjectMember.objects.filter(
        member_id__in=ids, member__role__in=COMPANY_SCOPE_ROLES).select_related('member')
    if company_roles_with_scope:
        issues.append({'code': 'role_scope_mismatch',
                       'summary': f'{company_roles_with_scope.count()} 条公司级角色的项目范围记录',
                       'refs': [str(row.member_id) for row in company_roles_with_scope[:5]]})
    cross_scope = ProjectMember.objects.filter(
        member_id__in=ids).exclude(project__organization=fresh.organization)
    if cross_scope:
        issues.append({'code': 'cross_org_project_scope',
                       'summary': f'{cross_scope.count()} 条项目范围跨公司错位',
                       'refs': [str(row.member_id) for row in cross_scope[:5]]})
    changed_ids = set(AuditEvent.objects.filter(
        organization=fresh.organization, action='identity.membership.changed',
        entity_id__in=[str(pk) for pk in ids]).values_list('entity_id', flat=True))
    unaudited = [row for row in rows if row.version > 1 and str(row.pk) not in changed_ids]
    if unaudited:
        issues.append({'code': 'audit_gap',
                       'summary': f'{len(unaudited)} 个成员版本变更缺少身份审计事件',
                       'refs': [str(row.pk) for row in unaudited[:5]]})
    duplicate_bindings = (AgentToken.objects.filter(member_id__in=ids, revoked_at__isnull=True,
                                                    expires_at__gt=cutoff)
                          .values('member_id', 'name').annotate(total=Max('id'))
                          .filter(total__isnull=False))
    # Duplicate active tokens with the same display name are the only duplicate binding
    # constructible under the frozen unique constraints (org+user / project+member).
    seen, duplicates = set(), 0
    for token in AgentToken.objects.filter(member_id__in=ids, revoked_at__isnull=True,
                                           expires_at__gt=cutoff):
        key = (token.member_id, token.name)
        if key in seen:
            duplicates += 1
        seen.add(key)
    if duplicates:
        issues.append({'code': 'duplicate_active_binding',
                       'summary': f'{duplicates} 个重复的同名有效令牌绑定',
                       'refs': []})
    return {'as_of': as_of_date.isoformat(), 'scope': scope, 'issues': issues,
            'issue_count': len(issues)}
