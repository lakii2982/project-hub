"""PH-330 read-only shared-cost reconciliation page."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render
from django.urls import get_script_prefix

from . import shared_cost_services as services


def _url(page):
    return get_script_prefix() + 'modules/costing/' + page + '/'


def shared_cost_reconciliation(context, payload):
    member = services._fresh(context.member)
    period = payload.get('period') or __import__('datetime').date.today().strftime('%Y-%m')
    pool_kind = payload.get('pool_kind') or None
    allowed = {'period', 'pool_kind'}
    error = ''
    snapshot = None
    if set(payload) - allowed:
        error = '包含不允许的查询参数，请刷新后重试'
    else:
        try:
            snapshot = services.shared_cost_reconciliation(
                member, period=period, pool_kind=pool_kind)
        except (PermissionDenied, ValidationError) as exc:
            error = '；'.join(getattr(exc, 'messages', [str(exc)]))
    return render(context.request, 'costing/shared_cost_reconciliation.html', {
        'nav': 'costing', 'snapshot': snapshot, 'error': error,
        'enabled': member.role in services.WRITE_ROLES,
        'reconciliation_url': _url('shared-cost-reconciliation'),
        'period': period, 'pool_kind': pool_kind or '',
    }, status=400 if error else 200)
