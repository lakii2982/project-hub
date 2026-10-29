"""Ordinary browser workflow; services own authorization and accounting."""
import uuid
from decimal import Decimal
from urllib.parse import urlencode
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render, redirect
from django.urls import get_script_prefix
from core.models import LaborCutover, LedgerPosting, Project, NonProjectDestination, Timesheet
from . import services
from .labor_forms import (ActionForm, ReasonForm, SalaryForm, ComponentFormSet,
                         CapacityForm, CutoverForm, ReviewForm, AllocateForm)


def url(page, **query):
    return get_script_prefix() + 'modules/costing/' + page.replace('_', '-') + '/' + ('?' + urlencode(query) if query else '')


def money(cents):
    return format(Decimal(cents) / Decimal(100), '.2f')


def base(member, **values):
    return dict(nav='costing', can_write=member.role in {'owner', 'finance'}, list_url=url('labor_list'),
                new_url=url('labor_form'), cutover_url=url('labor_form', action='cutover'), **values)


def labor_list(context, payload):
    member = services.labor_reader(context.member)
    period = payload.get('period', '')
    error = ''
    try:
        rows = list(services.list_salary_pools(member, period=period))
    except ValidationError as exc:
        rows = []
        error = '；'.join(exc.messages)
    for row in rows:
        row.amount_display = money(row.total_comprehensive_cost_cents)
        row.detail_url = url('labor_detail', id=row.pk)
    cutover = LaborCutover.objects.filter(organization=member.organization).first()
    form = None
    if cutover and cutover.status == 'submitted' and member.role in {'owner', 'finance'} and cutover.created_by_id != member.user_id:
        form = ActionForm(initial={'expected_version': cutover.version})
    return render(context.request, 'costing/labor_list.html', base(member, rows=rows, period=period,
        cutover=cutover, cutover_form=form, error=error, cutover_approve_url=url('labor_action', action='approve_cutover')), status=400 if error else 200)


def labor_detail(context, payload):
    member = services.labor_reader(context.member)
    detail = services.get_labor_detail(member, payload.get('id'))
    pool = detail['pool']
    components = [{**c, 'amount_display': money(c['amount_cents'])} for c in pool.salary_components]
    targets, inputs = [], []
    totals = {'project': 0, 'nonproject': 0, 'unallocated': pool.total_comprehensive_cost_cents if pool.recognition_event_id and not pool.reversed_event_id else 0}
    if detail['active_run']:
        totals['unallocated'] = 0
    for run in detail['runs']:
        for target in run.targets.all():
            if target.target_kind == 'project':
                obj = Project.objects.filter(organization=member.organization, pk=target.target_id).first()
                label = str(obj) if obj else target.target_id
                bucket = 'project'
            else:
                obj = NonProjectDestination.objects.filter(organization=member.organization, pk=target.target_id).first()
                label = obj.name if obj else target.target_id
                bucket = 'unallocated' if obj and obj.code == 'UNALLOCATED' else 'nonproject'
            targets.append(dict(run=run, target=target, label=label, amount_display=money(target.allocated_cents)))
            if run == detail['active_run']:
                if bucket == 'unallocated':
                    totals['unallocated'] = target.allocated_cents
                else:
                    totals[bucket] += target.allocated_cents
        for item in run.approved_inputs_snapshot:
            ref = item['timesheet_ref']
            source = Timesheet.objects.filter(organization=member.organization, pk=ref['id']).first()
            snapshot = source.approved_input_snapshot if source else {}
            if item['target_kind'] == 'project':
                target = Project.objects.filter(organization=member.organization, pk=item['target_id']).first()
            else:
                target = NonProjectDestination.objects.filter(organization=member.organization, pk=item['target_id']).first()
            inputs.append({**item, 'work_date': snapshot.get('work_date', ''), 'target_label': str(target) if target else item['target_id']})
    for event in detail['events']:
        event.ui_postings = list(LedgerPosting.objects.filter(organization=member.organization, accounting_event=event.core_accounting_event))
        for posting in event.ui_postings:
            posting.amount_display = money(posting.amount_cents)
    action_links = []
    if member.role in {'owner', 'finance'}:
        if pool.status == 'draft':
            action_links = [('save', '修改未提交构成'), ('submit', '提交独立确认')] if not pool.submitted_at else [('confirm', '独立确认政策与已发生工资')]
        elif pool.status in {'approved', 'reconciled'}:
            action_links = [('capacity', '提出覆盖期容量'), ('confirm_capacity', '独立批准容量'), ('allocate', '按全部已批投入分配')]
            if detail['active_run']:
                action_links = [('unallocate', '完整撤销分配')]
            else:
                action_links.append(('reverse', '完整冲销工资来源'))
    return render(context.request, 'costing/labor_detail.html', base(member, **detail,
        components=components, targets=targets, inputs=inputs, totals=[('项目', money(totals['project'])), ('普通非项目', money(totals['nonproject'])), ('未分配', money(totals['unallocated']))], total_display=money(pool.total_comprehensive_cost_cents),
        action_links=[(url('labor_form', id=pool.pk, action=a), label) for a, label in action_links]))


ACTIONS = {'submit': ('提交独立确认', ActionForm), 'confirm': ('独立确认政策与已发生工资', ReviewForm),
    'capacity': ('提出覆盖期容量', CapacityForm), 'confirm_capacity': ('独立批准容量', ReviewForm),
    'allocate': ('按全部已批投入分配', AllocateForm), 'unallocate': ('完整撤销分配', ReasonForm),
    'reverse': ('完整冲销工资来源', ReasonForm), 'cutover': ('提出人工启用安排', CutoverForm)}


def _render_form(context, member, action, pool=None, form=None, components=None, status=200):
    if action not in ACTIONS and action != 'save':
        raise PermissionDenied('未知人工办理动作')
    title, cls = ('登记已发生工资', SalaryForm) if action == 'save' else ACTIONS[action]
    if form is None:
        kwargs = {'member': member} if action == 'save' else {}
        form = cls(initial={'expected_version': pool.version} if pool else {}, **kwargs)
    if action not in {'save', 'cutover'} and pool is None:
        raise PermissionDenied('请从真实人工来源详情办理')
    if action == 'allocate':
        form.fields['capacity_policy_id'].widget.choices = [(str(p.pk), f'{p.denominator_minutes} 分钟 · {p.approved_at:%Y-%m-%d %H:%M}') for p in pool.policies.filter(capacity_confirmed=True)]
    return render(context.request, 'costing/labor_form.html', base(member, pool=pool, title=title,
        form=form, components=components, action=action, submit_url=url('labor_save') if action == 'save' else url('labor_action', action=action, id=pool.pk if pool else ''),
        back_url=url('labor_detail', id=pool.pk) if pool else url('labor_list')), status=status)


def labor_form(context, payload):
    member = services.labor_reader(context.member)
    if member.role not in {'owner', 'finance'}:
        raise PermissionDenied('仅公司负责人或财务可办理')
    action = payload.get('action', 'save')
    pool = services.get_salary_pool(member, payload.get('id')) if payload.get('id') else None
    if action == 'save' and pool:
        form = SalaryForm(member=member, initial={'pool_id': pool.pk, 'expected_version': pool.version, 'employee': pool.employee_id, 'period': pool.period, 'covered_through': pool.covered_through, 'valuation_state': pool.valuation_state, 'source_evidence': pool.reference})
        group = ComponentFormSet(prefix='components', initial=[{'code': c['component_code'], 'amount': Decimal(c['amount_cents'])/100} for c in pool.draft_components])
        return _render_form(context, member, action, pool, form=form, components=group)
    return _render_form(context, member, action, pool, components=ComponentFormSet(prefix='components') if action == 'save' else None)


def labor_save(context, payload):
    member = services.labor_reader(context.member)
    parent = {k: payload[k] for k in payload if not k.startswith('components-')}
    form = SalaryForm(parent, member=member)
    components = ComponentFormSet(payload, prefix='components')
    valid = form.is_valid()
    valid = components.is_valid() and valid
    allowed = {'components-' + k for k in components.management_form.fields}
    for i in range(components.total_form_count()):
        allowed.update(f'components-{i}-{k}' for k in components.form.base_fields)
    if any(k.startswith('components-') and k not in allowed for k in payload):
        form.add_error(None, '工资构成包含不允许的字段')
        valid = False
    if valid:
        data = form.cleaned_data
        try:
            pool = services.save_salary_pool(member, employee_id=data['employee'].pk, period=data['period'],
                valuation_state=data['valuation_state'], covered_through=data['covered_through'],
                components=[{'component_code': c.cleaned_data['code'], 'amount_cents': int(c.cleaned_data['amount'] * 100)} for c in components if c.cleaned_data],
                reference=data['source_evidence'], request_token=data['request_token'], pool_id=data.get('pool_id'), expected_version=data.get('expected_version'), dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '工资来源已保存，请核对并提交独立确认')
            return redirect(url('labor_detail', id=pool.pk))
    pool = services.get_salary_pool(member, parent['pool_id']) if parent.get('pool_id') else None
    return _render_form(context, member, 'save', pool, form=form, components=components, status=400)


def labor_action(context, payload):
    member = services.labor_reader(context.member)
    action = context.request.GET.get('action', '')
    if action == 'approve_cutover':
        from core.labor_cutover import approve_cutover
        form = ActionForm(payload)
        if form.is_valid():
            cutover = LaborCutover.objects.filter(organization=member.organization).first()
            try:
                if cutover is None:
                    raise ValidationError('公司尚未提出人工启用安排，请先由公司负责人提出')
                approve_cutover(member, cutover.pk, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, '；'.join(exc.messages))
            else:
                return redirect(url('labor_list'))
        return render(context.request, 'costing/labor_form.html', base(member, title='独立批准人工启用', form=form, action='approve_cutover', submit_url=url('labor_action', action='approve_cutover'), back_url=url('labor_list')), status=400)
    if action not in ACTIONS:
        raise PermissionDenied('未知人工办理动作')
    pool_id = context.request.GET.get('id')
    pool = services.get_salary_pool(member, pool_id) if pool_id else None
    if action != 'cutover' and pool is None:
        raise PermissionDenied('请从真实人工来源详情办理')
    form = ACTIONS[action][1](payload)
    if form.is_valid():
        data = form.cleaned_data.copy()
        data.pop('confirmed', None)
        try:
            if action == 'cutover':
                from core.labor_cutover import propose_cutover
                propose_cutover(member, **data)
            else:
                data['dry_run'] = False
                funcs = {'submit': services.submit_salary_pool, 'confirm': services.confirm_salary_pool,
                    'capacity': services.propose_capacity, 'confirm_capacity': services.confirm_capacity,
                    'allocate': services.allocate_pool, 'reverse': services.reverse_salary_pool,
                    'unallocate': services.reverse_allocation}
                object_id = pool.pk
                if action == 'capacity':
                    data['capacity_minutes'] = data.pop('denominator_minutes')
                    data['calendar_basis'] = data.pop('evidence')
                if action == 'unallocate':
                    active = services.get_labor_detail(member, pool.pk)['active_run']
                    if active is None:
                        raise ValidationError('没有可撤销的有效分配')
                    object_id = active.pk
                    data.pop('expected_version', None)
                funcs[action](member, object_id, **data)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '办理完成')
            return redirect(url('labor_detail', id=pool.pk) if pool else url('labor_list'))
    return _render_form(context, member, action, pool, form=form, status=400)
