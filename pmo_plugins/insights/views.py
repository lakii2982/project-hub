"""Insights browser handlers: precheck, reconciliation cases and project workbench.

GET routes stay available while the module is disabled (history_read); POST routes
are business_write and only owner/finance pass the service-level check. Every
handler delegates to ``services``/``precheck_services``; no business fact is
written here.
"""
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import get_script_prefix
from django.utils import timezone

from . import precheck_services, services
from .forms import (PeriodPrecheckFilterForm, ReconciliationFilterForm,
                    ReconciliationRefreshForm, ReconciliationUpdateForm,
                    WorkbenchFilterForm, new_token)

STATUS_LABELS = {'draft': '草稿', 'pending': '待确认', 'approved': '已确认',
                 'rejected': '已驳回', 'superseded': '已替代', 'closed': '已结案'}


def _url(page, **query):
    suffix = '?' + urlencode({key: value for key, value in query.items()
                              if value is not None}) if query else ''
    return get_script_prefix() + f'modules/insights/{page}/' + suffix


def _base(member, **values):
    return dict(
        nav='insights', role=member.role, can_write=member.role in services.WRITE_ROLES,
        precheck_url=_url('period-precheck'), reconciliation_url=_url('reconciliation'),
        workbench_url=_url('workbench'), refresh_url=_url('reconciliation-refresh'),
        update_url=_url('reconciliation-update'),
        today=timezone.localdate().isoformat(),
        default_period=timezone.localdate().strftime('%Y-%m'), **values)


def _unknown(payload, allowed):
    return bool(set(payload or ()) - set(allowed))


def _messages(form):
    return '；'.join(
        message for errors in form.errors.values() for message in errors) or '筛选条件无效'


def period_precheck_page(context, payload):
    member = context.member
    allowed = {'as_of', 'period', 'project'}
    form = PeriodPrecheckFilterForm(payload)
    error = None
    if _unknown(payload, allowed):
        error = '包含不允许的查询参数，请刷新后重试'
    elif not form.is_valid():
        error = _messages(form)
    data = None
    if error is None:
        try:
            data = precheck_services.period_precheck_snapshot(
                member, project_id=form.cleaned_data.get('project'),
                period=form.cleaned_data.get('period'),
                as_of=form.cleaned_data.get('as_of'))
        except ValidationError as invalid:
            error = '；'.join(invalid.messages)
        except PermissionDenied:
            raise
    return render(context.request, 'insights/precheck.html', _base(
        member, mode='precheck', form=form, data=data, error=error,
        page_url=_url('period-precheck')),
        status=400 if error else 200)


def _reconciliation_context(member, payload, *, data=None, error=None, preview=None):
    allowed = {'period', 'status', 'owner', 'project', 'as_of'}
    form = ReconciliationFilterForm(payload)
    if error is None and _unknown(payload, allowed):
        error = '包含不允许的查询参数，请刷新后重试'
    if error is None and not form.is_valid():
        error = _messages(form)
    if error is None and data is None:
        try:
            data = services.reconciliation_case_list(
                member, period=form.cleaned_data.get('period'),
                status=form.cleaned_data.get('status'),
                owner_member_id=form.cleaned_data.get('owner'),
                project_id=form.cleaned_data.get('project'),
                as_of=form.cleaned_data.get('as_of'))
        except ValidationError as invalid:
            error = '；'.join(invalid.messages)
    rows = []
    for case in (data or {}).get('cases') or ():
        update_form = ReconciliationUpdateForm(initial={
            'case_id': case['id'], 'expected_version': case['version'],
            'request_token': new_token(), 'decision': 'pending'})
        suffix = case['id'][:12]
        for name in ('decision', 'note', 'confirm'):
            update_form.fields[name].widget.attrs['id'] = f'{name}-{suffix}'
        rows.append({'case': {**case, 'status_label': STATUS_LABELS.get(
            case['status'], case['status'])}, 'form': update_form, 'suffix': suffix})
    refresh_form = ReconciliationRefreshForm(initial={
        'period': (data or {}).get('period') or timezone.localdate().strftime('%Y-%m'),
        'as_of': (data or {}).get('as_of'), 'request_token': new_token()})
    return _base(member, mode='reconciliation', form=form, data=data, error=error,
                 rows=rows, refresh_form=refresh_form, preview=preview,
                 page_url=_url('reconciliation'))


def reconciliation_list_page(context, payload):
    page = _reconciliation_context(context.member, payload)
    return render(context.request, 'insights/dashboard.html', page,
                  status=400 if page['error'] else 200)


def reconciliation_refresh_page(context, payload):
    member = context.member
    form = ReconciliationRefreshForm(payload)
    allowed = {'period', 'as_of', 'request_token', 'confirm'}
    error = None
    data = None
    if _unknown(payload, allowed):
        error = '包含不允许的查询参数，请刷新后重试'
    elif not form.is_valid():
        error = _messages(form)
    else:
        try:
            data = services.refresh_reconciliation_cases(
                member, period=form.cleaned_data['period'],
                as_of=form.cleaned_data.get('as_of'),
                request_token=form.cleaned_data['request_token'],
                dry_run=not form.cleaned_data.get('confirm'))
        except ValidationError as invalid:
            error = '；'.join(invalid.messages)
    if error is None and data is not None and data['dry_run']:
        page = _reconciliation_context(member, {'period': data['period']},
                                       preview=data)
        return render(context.request, 'insights/dashboard.html', page)
    if error is not None:
        page = _reconciliation_context(member, {'period': payload.get('period')},
                                       error=error)
        return render(context.request, 'insights/dashboard.html', page, status=400)
    messages.success(context.request,
                     f'已刷新差异单：新建 {len(data["created"])}，替代 {len(data["superseded"])}。')
    return redirect(_url('reconciliation', period=data['period']))


def reconciliation_update_page(context, payload):
    member = context.member
    form = ReconciliationUpdateForm(payload)
    allowed = {'case_id', 'decision', 'note', 'expected_version', 'request_token', 'confirm'}
    error = None
    data = None
    if _unknown(payload, allowed):
        error = '包含不允许的查询参数，请刷新后重试'
    elif not form.is_valid():
        error = _messages(form)
    else:
        try:
            data = services.update_reconciliation_case(
                member, case_id=form.cleaned_data['case_id'],
                decision=form.cleaned_data['decision'], note=form.cleaned_data.get('note'),
                expected_version=form.cleaned_data['expected_version'],
                request_token=form.cleaned_data['request_token'],
                dry_run=not form.cleaned_data.get('confirm'))
        except ValidationError as invalid:
            error = '；'.join(invalid.messages)
    if error is None and data is not None and data['dry_run']:
        page = _reconciliation_context(member, {}, preview=data['case'])
        return render(context.request, 'insights/dashboard.html', page)
    if error is not None:
        return render(context.request, 'insights/dashboard.html',
                      _reconciliation_context(member, {}, error=error), status=400)
    messages.success(context.request, '差异单状态已更新（仅内部读模型，未改业务事实）。')
    return redirect(_url('reconciliation'))


def workbench_page(context, payload):
    member = context.member
    form = WorkbenchFilterForm(payload)
    allowed = {'project', 'as_of'}
    error = None
    data = None
    if _unknown(payload, allowed):
        error = '包含不允许的查询参数，请刷新后重试'
    elif payload and not form.is_valid():
        error = _messages(form)
    elif form.is_valid():
        try:
            data = services.project_workbench_summary(
                member, form.cleaned_data['project'],
                as_of=form.cleaned_data.get('as_of'))
        except ValidationError as invalid:
            error = '；'.join(invalid.messages)
    return render(context.request, 'insights/dashboard.html', _base(
        member, mode='workbench', form=form, data=data, error=error,
        page_url=_url('workbench')), status=400 if error else 200)
