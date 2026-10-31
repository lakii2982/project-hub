"""PH-322 close/reopen pages; writes only through close_services named commands."""
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import get_script_prefix

from . import close_services as services
from .close_forms import PeriodCloseForm, ProjectCloseForm, ReopenForm


def _url(page, **query):
    suffix = '?' + urlencode({k: v for k, v in query.items() if v is not None}) if query else ''
    return get_script_prefix() + 'modules/delivery/' + page.replace('_', '-') + '/' + suffix


def _base(member, **values):
    return {'nav': 'delivery', 'can_write': member.role in services.WRITE_ROLES,
            'period_url': _url('period_close_page'), 'project_url': _url('project_close_page'),
            'period_execute_url': _url('period_close_execute'),
            'project_execute_url': _url('project_close_execute'),
            'period_reopen_url': _url('period_reopen'), 'project_reopen_url': _url('project_reopen'),
            **values}


def _period_page(context, payload):
    member = services._fresh(context.member)
    form = PeriodCloseForm(payload or None)
    precheck, error = None, None
    period = (payload.get('period') or context.request.GET.get('period') or
              context.request.GET.get('id'))
    if period:
        try:
            precheck = services.period_close_precheck(member, period)
        except ValidationError as exc:
            error = '；'.join(exc.messages)
    return render(context.request, 'delivery/close.html', _base(
        member, scope='period', precheck=precheck, form=form, error=error,
        period=period), status=400 if error else 200)


def period_close_page(context, payload):
    return _period_page(context, payload)


def period_close_execute(context, payload):
    member = services._fresh(context.member, write=True)
    form = PeriodCloseForm(payload)
    if form.is_valid():
        data = form.cleaned_data
        try:
            result = services.close_period(
                member, data['period'], request_token=data['request_token'],
                expected_version=data['expected_version'], dry_run=False,
                reason=data['reason'])
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '期间已关期')
            return redirect(_url('period_close_page', period=data['period']))
    precheck = None
    if form.data.get('period'):
        try:
            precheck = services.period_close_precheck(member, form.data['period'])
        except (ValidationError, PermissionDenied):
            precheck = None
    return render(context.request, 'delivery/close.html', _base(
        member, scope='period', precheck=precheck, form=form,
        period=form.data.get('period')), status=400)


def _project_page(context, payload):
    member = services._fresh(context.member)
    form = ProjectCloseForm(payload or None)
    precheck, error = None, None
    project_id = (payload.get('project') or context.request.GET.get('project') or
                  context.request.GET.get('id'))
    allow_open = payload.get('allow_open_period') in ('1', 'on', 'true', True)
    if project_id:
        try:
            precheck = services.project_close_precheck(
                member, project_id, allow_open_period=allow_open)
        except ValidationError as exc:
            error = '；'.join(exc.messages)
    return render(context.request, 'delivery/close.html', _base(
        member, scope='project', precheck=precheck, form=form, error=error,
        project_id=project_id, allow_open=allow_open), status=400 if error else 200)


def project_close_page(context, payload):
    return _project_page(context, payload)


def project_close_execute(context, payload):
    member = services._fresh(context.member, write=True)
    form = ProjectCloseForm(payload)
    if form.is_valid():
        data = form.cleaned_data
        try:
            services.close_project(
                member, data['project'], request_token=data['request_token'],
                expected_version=data['expected_version'], dry_run=False,
                reason=data['reason'], allow_open_period=data['allow_open_period'])
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '项目已结项')
            return redirect(_url('project_close_page', project=data['project']))
    precheck = None
    if form.data.get('project'):
        try:
            precheck = services.project_close_precheck(member, form.data['project'])
        except (ValidationError, PermissionDenied):
            precheck = None
    return render(context.request, 'delivery/close.html', _base(
        member, scope='project', precheck=precheck, form=form,
        project_id=form.data.get('project'),
        allow_open=form.data.get('allow_open_period') in ('1', 'on', 'true', True)), status=400)


def period_reopen(context, payload):
    member = services._fresh(context.member, write=True)
    form = ReopenForm(payload)
    if form.is_valid():
        data = form.cleaned_data
        try:
            services.reopen_period(
                member, data['period'], request_token=data['request_token'],
                expected_version=data['expected_version'], dry_run=False,
                reason=data['reason'])
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '期间已重开')
            return redirect(_url('period_close_page', period=data['period']))
    return render(context.request, 'delivery/close.html', _base(
        member, scope='period', form=form, reopen=True,
        period=form.data.get('period')), status=400)


def project_reopen(context, payload):
    member = services._fresh(context.member, write=True)
    form = ReopenForm(payload)
    if form.is_valid():
        data = form.cleaned_data
        try:
            services.reopen_project(
                member, data['project'], request_token=data['request_token'],
                expected_version=data['expected_version'], dry_run=False,
                reason=data['reason'])
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '项目已重开')
            return redirect(_url('project_close_page', project=data['project']))
    return render(context.request, 'delivery/close.html', _base(
        member, scope='project', form=form, reopen=True,
        project_id=form.data.get('project')), status=400)
