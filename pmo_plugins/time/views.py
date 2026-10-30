"""PH-269 time billable-confirmation browser handlers; writes go through the time owner service."""
import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import get_script_prefix

from . import services
from .forms import BillableConfirmationForm, ConfirmForm, ReverseForm


def _url(page, **query):
    return get_script_prefix() + 'modules/time/' + page.replace('_', '-') + '/' + (
        '?' + urlencode({key: value for key, value in query.items() if value is not None})
        if query else '')


def _fresh(member, write=False):
    return services._fresh(member, write=write)


def _base(member, **values):
    return dict(nav='time', can_write=member.role in services.WRITE_ROLES,
                list_url=_url('billable_confirmation_list'),
                form_url=_url('billable_confirmation_form'),
                save_url=_url('billable_confirmation_save'),
                confirm_url=_url('billable_confirmation_confirm'),
                detail_url=_url('billable_confirmation_detail'),
                reverse_url=_url('billable_confirmation_reverse'), **values)


def billable_confirmation_list(context, payload):
    member = _fresh(context.member)
    rows = list(services.list_billable_confirmations(member))
    for row in rows:
        row.edit_url = _url('billable_confirmation_form', id=row.pk) if (
            row.status == 'draft' and row.created_by_member_id == member.pk) else ''
        row.can_confirm = row.status == 'draft' and row.created_by_member_id != member.pk
        row.confirm_token = uuid.uuid4()
        row.detail_url = _url('billable_confirmation_detail', id=row.pk)
    return render(context.request, 'time/billable_confirmation_list.html', _base(
        member, rows=rows, error=''))


def billable_confirmation_form(context, payload):
    member = _fresh(context.member, write=True)
    row = services.get_billable_confirmation(member, payload.get('id')) if payload.get('id') else None
    if row is not None and (row.status != 'draft' or row.created_by_member_id != member.pk):
        return redirect(_url('billable_confirmation_list'))
    initial = {}
    if row is not None:
        initial = {'confirmation_id': row.pk, 'expected_version': row.version,
                   'timesheet_id': str(row.timesheet_ref['id']),
                   'contract_line_id': row.contract_line_ref['id'],
                   'confirmed_minutes': row.confirmed_minutes,
                   'confirmed_on': row.confirmed_on, 'evidence': row.evidence,
                   'request_token': uuid.uuid4()}
    return render(context.request, 'time/billable_confirmation_form.html', _base(
        member, row=row, form=BillableConfirmationForm(member=member, initial=initial), error=''))


def billable_confirmation_save(context, payload):
    member = _fresh(context.member, write=True)
    form = BillableConfirmationForm({k: payload[k] for k in payload
                                     if k in BillableConfirmationForm.base_fields or k == 'csrfmiddlewaretoken'},
                                    member=member)
    row = None
    if payload.get('confirmation_id'):
        try:
            row = services.get_billable_confirmation(member, payload['confirmation_id'])
        except (PermissionDenied, ValueError):
            row = None
    if form.is_valid():
        try:
            data = form.cleaned_data
            services.save_billable_confirmation_draft(
                member,
                timesheet_ref={'namespace': 'time', 'kind': 'timesheet',
                               'id': data['timesheet_id'], 'version': _timesheet_version(member, data['timesheet_id'])},
                contract_line_ref={'namespace': 'contracts', 'kind': 'contract_line',
                                   'id': data['contract_line_id'], 'version': 1},
                confirmed_minutes=data['confirmed_minutes'], confirmed_on=data['confirmed_on'],
                evidence=data['evidence'], request_token=data['request_token'],
                confirmation_id=data.get('confirmation_id'),
                expected_version=data.get('expected_version'), dry_run=False)
        except ValidationError as error:
            form.add_error(None, '；'.join(error.messages))
        else:
            messages.success(context.request, '可计费确认草稿已保存；确认前不产生收入或账本事实')
            return redirect(_url('billable_confirmation_list'))
    return render(context.request, 'time/billable_confirmation_form.html', _base(
        member, row=row, form=form,
        error='；'.join(str(item) for errors in form.errors.values() for item in errors)), status=400)


def _timesheet_version(member, timesheet_id):
    for row in services.approved_timesheet_choices(member):
        if str(row.pk) == str(timesheet_id):
            return row.version
    raise ValidationError('工时不存在或不可确认')


def billable_confirmation_confirm(context, payload):
    member = _fresh(context.member, write=True)
    form = ConfirmForm(payload)
    if form.is_valid():
        data = form.cleaned_data
        try:
            services.confirm_billable(member, data['confirmation_id'],
                                      expected_version=data['expected_version'],
                                      request_token=data['request_token'], dry_run=False)
        except ValidationError as error:
            messages.error(context.request, '；'.join(error.messages))
            return redirect(_url('billable_confirmation_list'))
        messages.success(context.request, '客户可计费分钟已确认；事件已记录，未生成收入/账本/现金/未清')
        return redirect(_url('billable_confirmation_list'))
    messages.error(context.request, '；'.join(str(item) for errors in form.errors.values() for item in errors))
    return redirect(_url('billable_confirmation_list'))


def billable_confirmation_detail(context, payload):
    member = _fresh(context.member)
    row = services.get_billable_confirmation(member, payload.get('id'))
    pairing = services.billable_confirmation_pairing(member, row.pk)
    can_reverse = (member.role in services.WRITE_ROLES and row.status == 'confirmed'
                   and row.created_by_member_id != member.pk)
    form = ReverseForm(initial={
        'confirmation_id': row.pk, 'expected_version': row.version,
        'request_token': uuid.uuid4()}) if can_reverse else None
    return render(context.request, 'time/billable_confirmation_detail.html', _base(
        member, row=row, pairing=pairing, form=form, can_reverse=can_reverse, error=''))


def billable_confirmation_reverse(context, payload):
    member = _fresh(context.member, write=True)
    form = ReverseForm(payload)
    if form.is_valid():
        data = form.cleaned_data
        try:
            services.reverse_billable_confirmation(
                member, data['confirmation_id'], reason=data['reason'],
                request_token=data['request_token'],
                expected_version=data['expected_version'], dry_run=False)
        except ValidationError as error:
            messages.error(context.request, '；'.join(error.messages))
            return redirect(_url('billable_confirmation_detail', id=data['confirmation_id']))
        messages.success(context.request, '客户可计费确认已撤销；历史保留，不重算收入/账本/现金/未清')
        return redirect(_url('billable_confirmation_list'))
    messages.error(context.request, '；'.join(str(item) for errors in form.errors.values() for item in errors))
    return redirect(_url('billable_confirmation_list'))
