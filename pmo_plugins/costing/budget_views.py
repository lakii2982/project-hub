"""PH-330 read-only budget/ETC snapshot page."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render
from django.urls import get_script_prefix

from core.models import Project
from . import budget_services as services


def _url(page):
    return get_script_prefix() + 'modules/costing/' + page + '/'


def budget_etc_page(context, payload):
    member = services._fresh(context.member)
    allowed = {'project', 'period', 'as_of'}
    error = ''
    snapshot = issues = None
    project_id = payload.get('project') or None
    if set(payload) - allowed:
        error = '包含不允许的查询参数，请刷新后重试'
    else:
        try:
            if project_id:
                snapshot = services.budget_etc_snapshot(
                    member, project_id=project_id, period=payload.get('period') or None,
                    as_of=payload.get('as_of') or None)
            issues = services.budget_etc_issues(member, as_of=payload.get('as_of') or None)
        except (PermissionDenied, ValidationError) as exc:
            error = '；'.join(getattr(exc, 'messages', [str(exc)]))
    projects = Project.objects.filter(organization=member.organization)
    if member.role == 'pm':
        projects = projects.filter(manager=member)
    return render(context.request, 'costing/budget_etc.html', {
        'nav': 'costing', 'snapshot': snapshot, 'issues': issues or [],
        'projects': projects.order_by('code'), 'error': error, 'project_id': project_id or '',
        'budget_url': _url('budget-etc'), 'enabled': member.role in services.WRITE_ROLES,
    }, status=400 if error else 200)
