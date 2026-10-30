"""PH-288 read-only bank-account balance reconciliation browser handlers."""
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import get_script_prefix

from core.access import require_member
from core.models import Membership

from . import bank_balance_services as bank
from .models import FundingAccount


def _url(page, **query):
    return get_script_prefix() + 'modules/cash/' + page.replace('_', '-') + '/' + (
        '?' + urlencode({key: value for key, value in query.items() if value is not None})
        if query else '')


def _fresh(member):
    try:
        fresh = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(fresh)
    if fresh.role not in bank.READ_ROLES:
        raise PermissionDenied('仅公司负责人、财务或审阅账户可以查看银行账户余额对账')
    return fresh


def _account_options(member):
    return [(str(row.pk), f'{row.name}'
             + (f' · {row.masked_identifier}' if row.masked_identifier else ''))
            for row in FundingAccount.objects.filter(
                organization_id=member.organization_id).order_by('name')]


def _base(member, nav_page, **values):
    return dict(nav='cash', nav_page=nav_page, can_write=member.role in {'owner', 'finance'},
                accounts_url=_url('funding_account_list'),
                snapshot_url=_url('bank_balance_snapshot'),
                issues_url=_url('bank_balance_issues'), **values)


def _filters(payload):
    payload = payload or {}
    return (payload.get('as_of') or None, payload.get('period') or None,
            payload.get('funding_account') or None)


def bank_balance_snapshot(context, payload):
    member = _fresh(context.member)
    as_of, period, account_id = _filters(payload)
    snapshot = bank.bank_account_balance_snapshot(
        member, funding_account_id=account_id, as_of=as_of, period=period)
    return render(context.request, 'cash/bank_balance_snapshot.html', _base(
        member, 'bank_balance', snapshot=snapshot, issues=snapshot['issues'],
        mode='snapshot', as_of=snapshot['as_of'], period=snapshot['period'] or '',
        selected_account=account_id or '', account_options=_account_options(member)))


def bank_balance_issues(context, payload):
    member = _fresh(context.member)
    as_of, period, account_id = _filters(payload)
    result = bank.bank_account_issues(member, as_of=as_of, period=period)
    return render(context.request, 'cash/bank_balance_snapshot.html', _base(
        member, 'bank_balance', snapshot=result, issues=result['issues'],
        mode='issues', as_of=result['as_of'], period=result['period'] or '',
        selected_account=account_id or '', account_options=_account_options(member)))
