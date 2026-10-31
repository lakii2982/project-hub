"""PH-284 read-only management-revenue pairing browser handlers."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render
from django.urls import get_script_prefix

from . import pairing_read_services as pairing
from .forms import RevenuePairingFilterForm


def _url(page):
    return get_script_prefix() + 'modules/management_revenue/' + page + '/'


def revenue_pairing_snapshot_page(context, payload):
    member = pairing._reader(context.member)
    allowed = {'project', 'period', 'as_of'}
    form = RevenuePairingFilterForm(payload or None, member=member)
    error = ''
    if set(payload) - allowed:
        error = '包含不允许的查询参数，请刷新后重试'
        snapshot, issues = None, []
    else:
        try:
            as_of = payload.get('as_of') or None
            project_id = payload.get('project') or None
            snapshot = pairing.revenue_pairing_snapshot(
                member, project_id=project_id, period=payload.get('period') or None,
                as_of=as_of)
            issues = pairing.revenue_pairing_issues(member, as_of=as_of)
            if project_id:
                issues = [issue for issue in issues if issue['project_id'] == str(project_id)]
        except (PermissionDenied, ValidationError) as exc:
            snapshot, issues = None, []
            error = '；'.join(getattr(exc, 'messages', [str(exc)]))
    return render(context.request,
                  'management_revenue/revenue_pairing_snapshot.html',
                  {'nav': 'management_revenue', 'can_write': False,
                   'snapshot': snapshot, 'issues': issues, 'filter_form': form,
                   'error': error, 'pairing_url': _url('revenue-pairing')},
                  status=400 if error else 200)
