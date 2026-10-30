"""Service-period confirmation browser handlers; all business writes use owner services."""
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import get_script_prefix

from pmo_plugins.contracts.models import ContractLine
from pmo_plugins.management_revenue import services as revenue

from . import service_period_services as services
from .forms import ActionForm, ReasonForm, ReviewForm
from .service_period_forms import RecognizeMonthForm, ReverseIncomeForm, ServiceConfirmationForm


def url(page, **query):
    return get_script_prefix() + 'modules/delivery/' + page.replace('_', '-') + '/' + (
        '?' + urlencode(query) if query else '')


def money(cents):
    return format(Decimal(cents) / 100, '.2f')


def base(member, **values):
    return dict(nav='delivery', can_write=member.role in {'owner', 'finance', 'pm'},
                list_url=url('service_confirmation_list'), new_url=url('service_confirmation_form'),
                acceptance_url=url('acceptance_list'), service_url=url('service_confirmation_list'), **values)


ACTIONS = {
    'submit': ('提交独立确认', ActionForm),
    'review': ('独立核对服务确认', ReviewForm),
    'withdraw': ('明确撤回', ReasonForm),
    'reverse': ('反冲已确认服务', ReasonForm),
    'recognize': ('确认该服务月管理收入', RecognizeMonthForm),
    'reverse_income': ('完整反冲该月管理收入', ReverseIncomeForm),
}


def _month_rows(member, row, detail=None):
    detail = detail or services.service_confirmation_detail(member, row.pk)
    stored = {item['service_month']: item for item in detail['income']['months']}
    rows = []
    for item in detail['months']:
        month = item['service_month']
        state = stored.get(month)
        if state and state['recognition_id'] and state['is_reversed']:
            status, badge = '已反冲', 'neutral'
        elif state and state['recognition_id']:
            status, badge = '已收入', 'success'
        elif row.status == 'confirmed':
            status, badge = '待收入', 'warning'
        elif row.status == 'reversed':
            status, badge = '已反冲', 'neutral'
        else:
            status, badge = '确认后冻结', 'neutral'
        rows.append({**item, 'amount_display': money(item['allocated_net_cents']),
                     'state_label': status, 'state_badge': badge,
                     'recognition_id': state['recognition_id'] if state else item.get('recognition_id')})
    return rows


def service_confirmation_list(context, payload):
    rows = list(services.list_service_confirmations(context.member))
    for row in rows:
        row.amount_display = money(row.confirmed_net_cents)
        row.detail_url = url('service_confirmation_detail', id=row.pk)
        try:
            detail = services.service_confirmation_detail(context.member, row.pk)
            months = _month_rows(context.member, row, detail)
            row.month_count = len(months)
            row.pending_month_count = sum(1 for item in months if item['state_label'] == '待收入')
            row.recognized_month_count = sum(1 for item in months if item['state_label'] == '已收入')
            row.reversed_month_count = sum(1 for item in months if item['state_label'] == '已反冲')
        except ValidationError:
            row.month_count = 0
            row.pending_month_count = row.recognized_month_count = row.reversed_month_count = 0
    return render(context.request, 'delivery/service_confirmation_list.html',
                  base(context.member, rows=rows))


def service_confirmation_detail(context, payload):
    detail = services.service_confirmation_detail(context.member, payload.get('id'))
    row = detail['confirmation']
    member = services._fresh(context.member)
    months = _month_rows(member, row, detail)
    income = detail['income']
    links = []
    if member.role in {'owner', 'finance', 'pm'}:
        if row.status == 'draft':
            links = [('edit', '修改草稿'), ('submit', '提交独立确认'), ('withdraw', '撤回草稿')]
        elif row.status == 'submitted':
            links = [('withdraw', '明确撤回')]
            if member.role in {'owner', 'finance'} and member.pk not in {row.created_by_id, row.submitted_by_id}:
                links.append(('review', '独立核对服务确认'))
        elif row.status == 'confirmed' and member.role in {'owner', 'finance'}:
            for item in months:
                if not item['recognition_id']:
                    links.append((f"recognize:{item['service_month']}", f"确认 {item['service_month']} 管理收入"))
                elif item['state_label'] == '已收入':
                    links.append((f"reverse_income:{item['service_month']}", f"反冲 {item['service_month']} 管理收入"))
            if income['all_reversed'] and member.pk not in {row.created_by_id, row.submitted_by_id}:
                links.append(('reverse', '反冲已确认服务'))
        elif row.status == 'reversed':
            links = [('replace', '以新客户依据登记替代确认')]
    action_links = []
    for action, label in links:
        if ':' in action:
            kind, month = action.split(':', 1)
            action_links.append((url('service_confirmation_form', id=row.pk, action=kind, month=month), label))
        else:
            action_links.append((url('service_confirmation_form', id=row.pk, action=action), label))
    evidence_ids = []
    for item in row.evidence or []:
        if isinstance(item, dict) and item.get('evidence_id'):
            evidence_ids.append(item['evidence_id'])
    values = {**detail, 'months': months, 'month_rows': months}
    return render(context.request, 'delivery/service_confirmation_detail.html', base(
        member, **values, amount_display=money(row.confirmed_net_cents), action_links=action_links,
        replaced_detail_url=url('service_confirmation_detail', id=row.replaces_confirmation_id)
        if row.replaces_confirmation_id else ''))


def _line(member, line_id):
    candidates = ServiceConfirmationForm(member=member).fields['line'].queryset
    try:
        return candidates.get(pk=line_id)
    except (ContractLine.DoesNotExist, ValueError, ValidationError):
        raise PermissionDenied('合同明细不存在或无权访问')


def _save_form(context, member, payload=None, source=None):
    line_id = payload.get('line') if payload is not None else context.request.GET.get('line')
    if source and not line_id:
        line_id = source.contract_line_id
    if not line_id:
        chooser = ServiceConfirmationForm(member=member)
        return render(context.request, 'delivery/service_confirmation_form.html', base(
            member, title='选择已批准固定服务期合同', chooser=True,
            line_choices=chooser.fields['line'].choices, back_url=url('service_confirmation_list')))
    line = _line(member, line_id)
    initial = {'line': line.pk}
    if source:
        if source.status == 'reversed':
            initial['replaces_confirmation_id'] = source.pk
        else:
            initial.update(confirmation_id=source.pk, expected_version=source.version,
                           confirmed_on=source.confirmed_on, coverage_start=source.coverage_start,
                           coverage_end=source.coverage_end)
            evidence = services.service_confirmation_detail(member, source.pk)['evidence'].first()
            if evidence:
                initial.update(contact=evidence.contact_id, channel=evidence.channel,
                               reference=evidence.reference, content=evidence.statement)
    parent = {key: payload[key] for key in payload} if payload is not None else None
    form = ServiceConfirmationForm(parent, member=member, line=line, initial=initial)
    if payload is not None and form.is_valid():
        data = form.cleaned_data
        ref = {'namespace': 'contracts', 'kind': 'contract_line',
               'id': str(line.pk), 'version': line.version}
        replacement = data.get('replaces_confirmation_id')
        try:
            previous = services.get_service_confirmation(member, replacement) if replacement else None
            row = services.save_service_confirmation_with_evidence(
                member, contract_line_ref=ref, contact_id=data['contact'].pk,
                coverage_start=data['coverage_start'], coverage_end=data['coverage_end'],
                confirmed_on=data['confirmed_on'], channel=data['channel'], reference=data['reference'],
                statement=data['content'], request_token=data['request_token'],
                confirmation_id=data.get('confirmation_id'), expected_version=data.get('expected_version'),
                replaces_confirmation_ref=services._source_ref(previous) if previous else None,
                dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            return redirect(url('service_confirmation_detail', id=row.pk))
    replacement_source = source if source and source.status == 'reversed' else None
    replacement_id = form.cleaned_data.get('replaces_confirmation_id') if payload is not None \
        else form['replaces_confirmation_id'].value()
    if replacement_id and replacement_source is None:
        try:
            replacement_source = services.get_service_confirmation(member, replacement_id)
        except PermissionDenied:
            replacement_source = None
    return render(context.request, 'delivery/service_confirmation_form.html', base(
        member, title='登记客户服务期确认依据', form=form, replacement_source=replacement_source,
        replacement_amount=money(replacement_source.confirmed_net_cents) if replacement_source else '',
        back_url=url('service_confirmation_list'), submit_url=url('service_confirmation_save'),
        explanation='按真实客户确认记录填写：客户确认日期独立保留，服务覆盖须落在同一已批准分配段内；'
                    '金额与所属月份由系统按原批准服务期计算。'),
        status=400 if payload is not None else 200)


def _action_initial(member, row, action, month):
    if action == 'recognize':
        if not month:
            raise PermissionDenied('请选择真实服务月')
        return {'service_month': month, 'expected_version': row.confirmed_version}
    if action == 'reverse_income':
        if not month:
            raise PermissionDenied('请选择真实服务月')
        detail = services.service_confirmation_detail(member, row.pk)
        item = next((value for value in detail['months']
                     if value['service_month'] == month and value['recognition_id']), None)
        if item is None:
            raise ValidationError('该服务月尚无可反冲管理收入')
        return {'recognition_id': item['recognition_id']}
    return {'expected_version': row.version}


def service_confirmation_form(context, payload):
    member = services._fresh(context.member, True)
    action = payload.get('action', 'save')
    row = services.get_service_confirmation(member, payload['id']) if payload.get('id') else None
    if action in {'save', 'edit', 'replace'}:
        return _save_form(context, member, source=row)
    if action not in ACTIONS or row is None:
        raise PermissionDenied('未知服务期办理动作')
    title, cls = ACTIONS[action]
    month = payload.get('month')
    initial = _action_initial(member, row, action, month)
    submit_url = url('service_confirmation_action', id=row.pk, action=action)
    return render(context.request, 'delivery/service_confirmation_form.html', base(
        member, title=title, form=cls(initial=initial), submit_url=submit_url,
        back_url=url('service_confirmation_detail', id=row.pk),
        explanation='独立确认与反冲由另一位公司负责人或财务办理；每月管理收入须逐月显式确认，'
                    '不得把任一月收入当作整个父来源已处理。'))


def service_confirmation_save(context, payload):
    member = services._fresh(context.member, True)
    return _save_form(context, member, payload)


def service_confirmation_action(context, payload):
    member = services._fresh(context.member, True)
    row = services.get_service_confirmation(member, context.request.GET.get('id'))
    action = context.request.GET.get('action', '')
    if action not in ACTIONS:
        raise PermissionDenied('未知服务期办理动作')
    title, cls = ACTIONS[action]
    month = context.request.GET.get('month')
    initial = _action_initial(member, row, action, month)
    bound = {key: value for key, value in payload.items()}
    for key, value in initial.items():
        bound.setdefault(key, value)
    form = cls(bound, initial=initial)
    if form.is_valid():
        data = form.cleaned_data.copy()
        data['dry_run'] = False
        try:
            if action == 'recognize':
                revenue.recognize_service_month(
                    member, row.pk, service_month=data['service_month'],
                    expected_version=data['expected_version'],
                    request_token=data['request_token'], dry_run=False)
            elif action == 'reverse_income':
                data.pop('expected_version')
                revenue.reverse_recognition(member, data.pop('recognition_id'), **data)
            else:
                if action == 'review':
                    data['note'] = data.pop('reason')
                function = {
                    'submit': services.submit_service_confirmation,
                    'review': services.review_service_confirmation,
                    'withdraw': services.withdraw_service_confirmation,
                    'reverse': services.reverse_service_confirmation,
                }[action]
                function(member, row.pk, **data)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '办理完成')
            return redirect(url('service_confirmation_detail', id=row.pk))
    return render(context.request, 'delivery/service_confirmation_form.html', base(
        member, title=title, form=form,
        submit_url=url('service_confirmation_action', id=row.pk, action=action),
        back_url=url('service_confirmation_detail', id=row.pk)), status=400)
