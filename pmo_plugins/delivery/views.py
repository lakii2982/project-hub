"""Customer acceptance browser handlers; all business writes use owner services."""
from decimal import Decimal
from urllib.parse import urlencode
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import render, redirect
from django.urls import get_script_prefix
from core.models import Milestone
from pmo_plugins.contracts.models import ContractLine
from pmo_plugins.management_revenue import services as revenue
from . import services
from .forms import AcceptanceForm, CoverageFormSet, ActionForm, ReasonForm, ReviewForm


def url(page, **query):
    return get_script_prefix() + 'modules/delivery/' + page.replace('_','-') + '/' + ('?' + urlencode(query) if query else '')


def money(cents):
    return format(Decimal(cents)/100, '.2f')


def base(member, **values):
    return dict(nav='delivery', can_write=member.role in {'owner','finance','pm'}, list_url=url('acceptance_list'), new_url=url('acceptance_form'), **values)


def acceptance_list(context, payload):
    rows=list(services.list_acceptances(context.member))
    for row in rows:
        row.amount_display=money(row.accepted_net_cents)
        row.detail_url=url('acceptance_detail', id=row.pk)
    return render(context.request,'delivery/acceptance_list.html',base(context.member,rows=rows))


def acceptance_detail(context,payload):
    detail=services.acceptance_detail(context.member,payload.get('id'))
    row=detail['acceptance']; member=services._fresh(context.member)
    links=[]
    if member.role in {'owner','finance','pm'}:
        if row.status=='draft':links=[('edit','修改草稿'),('submit','提交独立确认'),('withdraw','撤回草稿')]
        elif row.status=='submitted':
            links=[('withdraw','明确撤回')]
            if member.role in {'owner','finance'}:links.append(('review','独立核对验收'))
        elif row.status=='confirmed' and member.role in {'owner','finance'}:
            if not detail['income']['has_recognition']:links=[('recognize','确认管理收入'),('reverse','反冲验收')]
            elif not detail['income']['is_reversed']:links=[('reverse_income','先反冲管理收入')]
            else:links=[('reverse','反冲验收')]
        elif row.status=='reversed':links=[('replace','以新客户依据登记替代验收')]
    covers=list(detail['coverages'])
    for cover in covers:
        cover.from_display=format(Decimal(cover.from_basis_points)/100,'.2f')
        cover.to_display=format(Decimal(cover.to_basis_points)/100,'.2f')
        cover.amount_display=money(cover.accepted_net_cents)
    recognition=getattr(row,'revenue_recognition',None)
    return render(context.request,'delivery/acceptance_detail.html',base(member,**detail,coverage_rows=covers,
        amount_display=money(row.accepted_net_cents),recognition=recognition,replaced_detail_url=url('acceptance_detail',id=row.replaces_acceptance_id) if row.replaces_acceptance_id else '',
        revenue_display=money(recognition.recognized_net_cents) if recognition else '',
        action_links=[(url('acceptance_form',id=row.pk,action=action),label) for action,label in links]))


def _line(member,line_id):
    candidates=AcceptanceForm(member=member).fields['line'].queryset
    try:return candidates.get(pk=line_id)
    except (ContractLine.DoesNotExist,ValueError,ValidationError):raise PermissionDenied('合同节点不存在或无权访问')


def _coverages(member,line,payload=None,initial=None):
    snapshots=services.form_choices(member)
    snapshot=next((x for x in snapshots if str(x['line_id'])==str(line.pk)),None)
    if snapshot is None:raise PermissionDenied('合同配置不可用，请核对批准版本')
    milestones=snapshot['pricing_configuration']['milestones']
    if initial is None and payload is None:
        initial=[{'milestone_id':str(x['milestone_id']),'from_percent':0,'to_percent':100} for x in milestones]
    group=CoverageFormSet(payload,prefix='coverage',initial=initial)
    choices=[(str(x['milestone_id']), Milestone.objects.get(pk=x['milestone_id'],project_id=line.project_id).name) for x in milestones]
    for form in group:
        form.fields['milestone_id'].choices=choices
        form.fields['DELETE'].label='本次不包含该节点'
    return group


def _save_form(context,member,payload=None,source=None):
    line_id=payload.get('line') if payload is not None else context.request.GET.get('line')
    if source and not line_id:line_id=source.contract_line_id
    if not line_id:
        chooser=AcceptanceForm(member=member)
        return render(context.request,'delivery/acceptance_form.html',base(member,title='选择批准合同节点',chooser=True,line_choices=chooser.fields['line'].choices,back_url=url('acceptance_list')))
    line=_line(member,line_id)
    initial={'line':line.pk}
    if source:
        initial.update(replaces_acceptance_id=source.pk) if source.status=='reversed' else initial.update(acceptance_id=source.pk,expected_version=source.version)
    parent={k:payload[k] for k in payload if not k.startswith('coverage-')} if payload is not None else None
    if source and source.status != 'reversed':
        evidence=services.acceptance_detail(member,source.pk)['evidence'].first()
        if evidence:initial.update(contact=evidence.contact_id,confirmed_on=evidence.confirmed_on,channel=evidence.channel,reference=evidence.reference,content=evidence.statement)
    form=AcceptanceForm(parent,member=member,line=line,initial=initial)
    group=_coverages(member,line,payload,initial=[{'milestone_id':str(c.milestone_id),'from_percent':Decimal(c.from_basis_points)/100,'to_percent':Decimal(c.to_basis_points)/100} for c in source.coverages.all()] if source else None)
    if payload is not None:
        valid=form.is_valid();valid=group.is_valid() and valid
        allowed=set(form.fields)|{'csrfmiddlewaretoken'}|{'coverage-'+k for k in group.management_form.fields}
        for i in range(group.total_form_count()):allowed.update(f'coverage-{i}-{k}' for k in set(group.form.base_fields)|{'DELETE'})
        # Parent sees no formset keys; reject everything else explicitly.
        if set(payload)-allowed:form.add_error(None,'包含不允许的字段');valid=False
        if valid:
            data=form.cleaned_data
            ref={'namespace':'contracts','kind':'contract_line','id':str(line.pk),'version':line.version}
            replacement=data.get('replaces_acceptance_id')
            try:
                previous=services.get_acceptance(member,replacement) if replacement else None
                row=services.save_acceptance_with_evidence(member,contract_line_ref=ref,contact_id=data['contact'].pk,confirmed_on=data['confirmed_on'],channel=data['channel'],reference=data['reference'],statement=data['content'],
                    milestone_coverages=[{'milestone_id':f.cleaned_data['milestone_id'],'from_basis_points':int(f.cleaned_data['from_percent']*100),'to_basis_points':int(f.cleaned_data['to_percent']*100)} for f in group if f.cleaned_data and not f.cleaned_data.get('DELETE')],
                    request_token=data['request_token'],acceptance_id=data.get('acceptance_id'),expected_version=data.get('expected_version'),replaces_acceptance_ref=services.source_ref(previous) if previous else None,dry_run=False)
            except ValidationError as exc:form.add_error(None,'；'.join(exc.messages))
            else:return redirect(url('acceptance_detail',id=row.pk))
    replacement_source = source if source and source.status == 'reversed' else None
    replacement_id = form.cleaned_data.get('replaces_acceptance_id') if payload is not None else form['replaces_acceptance_id'].value()
    if replacement_id and replacement_source is None:
        replacement_source = services.get_acceptance(member, replacement_id)
    return render(context.request,'delivery/acceptance_form.html',base(member,title='登记客户确认依据与节点覆盖',form=form,coverages=group,replacement_source=replacement_source,replacement_amount=money(replacement_source.accepted_net_cents) if replacement_source else '',back_url=url('acceptance_list'),submit_url=url('acceptance_save'),explanation='按真实客户确认记录填写，核对同一客户联系人、认可节点范围和确认日。'),status=400 if payload is not None else 200)


ACTIONS={'submit':('提交独立确认',ActionForm),'review':('独立核对验收',ReviewForm),'withdraw':('明确撤回',ReasonForm),'reverse':('反冲验收',ReasonForm),'recognize':('确认管理收入',ActionForm),'reverse_income':('完整反冲管理收入',ReasonForm)}


def acceptance_form(context,payload):
    member=services._fresh(context.member,True)
    action=payload.get('action','save')
    row=services.get_acceptance(member,payload['id']) if payload.get('id') else None
    if action in {'save','edit','replace'}:return _save_form(context,member,source=row)
    if action not in ACTIONS or row is None:raise PermissionDenied('未知验收办理动作')
    title,cls=ACTIONS[action]
    return render(context.request,'delivery/acceptance_form.html',base(member,title=title,form=cls(initial={'expected_version':row.confirmed_version if action=='recognize' else row.version}),submit_url=url('acceptance_action',id=row.pk,action=action),back_url=url('acceptance_detail',id=row.pk),explanation='核对原始客户依据、批准合同及覆盖金额。管理收入不代表收款或利润；纠错须先反收入，再由另一位负责人反验收。'))


def acceptance_save(context,payload):
    member=services._fresh(context.member,True)
    return _save_form(context,member,payload)


def acceptance_action(context,payload):
    member=services._fresh(context.member,True)
    row=services.get_acceptance(member,context.request.GET.get('id'))
    action=context.request.GET.get('action','')
    if action not in ACTIONS:raise PermissionDenied('未知验收办理动作')
    title,cls=ACTIONS[action];form=cls(payload)
    if form.is_valid():
        data=form.cleaned_data.copy();data['dry_run']=False
        try:
            if action=='recognize':revenue.recognize_acceptance(member,row.pk,**data)
            elif action=='reverse_income':
                data.pop('expected_version')
                recognition=getattr(row,'revenue_recognition',None)
                if recognition is None:raise ValidationError('尚无可反冲管理收入')
                revenue.reverse_recognition(member,recognition.pk,**data)
            else:
                if action=='review':data['note']=data.pop('reason')
                getattr(services,{'submit':'submit_acceptance','review':'review_acceptance','withdraw':'withdraw_acceptance','reverse':'reverse_acceptance'}[action])(member,row.pk,**data)
        except ValidationError as exc:form.add_error(None,'；'.join(exc.messages))
        else:
            messages.success(context.request,'办理完成')
            return redirect(url('acceptance_detail',id=row.pk))
    return render(context.request,'delivery/acceptance_form.html',base(member,title=title,form=form,submit_url=url('acceptance_action',id=row.pk,action=action),back_url=url('acceptance_detail',id=row.pk)),status=400)


def ops_reconciliation_overview(context, payload):
    """PH-291 read-only cross-domain operations overview page (history_read)."""
    from django.shortcuts import render
    from django.urls import get_script_prefix
    from . import operations_read_services as overview
    from .forms import OpsOverviewFilterForm
    member = context.member
    allowed = {'as_of', 'period'}
    form = OpsOverviewFilterForm(payload)
    error = None
    if set(payload) - allowed:
        error = '包含不允许的查询参数，请刷新后重试'
    elif not form.is_valid():
        error = '；'.join(form.errors.get('__all__', [])) or '筛选条件无效'
    data = None
    if error is None:
        try:
            data = overview.ops_reconciliation_overview(
                member, as_of=form.cleaned_data.get('as_of'),
                period=form.cleaned_data.get('period'))
        except ValidationError as invalid:
            error = '；'.join(invalid.messages)
        except PermissionDenied:
            raise
    return render(context.request, 'delivery/ops_reconciliation_overview.html', {
        'nav': 'delivery',
        'overview': data,
        'form': form,
        'error': error,
        'page_url': get_script_prefix() + 'modules/delivery/ops-reconciliation-overview/',
        'delivery_url': get_script_prefix() + 'modules/delivery/acceptance-list/',
    }, status=400 if error else 200)


def ops_export(context, payload):
    """PH-304 authorized read-only CSV export page/download (history_read)."""
    import uuid
    from django.http import HttpResponse
    from django.shortcuts import render
    from django.urls import get_script_prefix
    from django.utils import timezone
    from . import operations_export_services as export
    from .forms import OpsExportFilterForm
    member = context.member
    allowed = {'dataset', 'as_of', 'period', 'download', 'token'}
    form = OpsExportFilterForm(payload)
    error = None
    plan = None
    if set(payload) - allowed:
        error = '包含不允许的查询参数，请刷新后重试'
    elif not form.is_valid():
        error = '；'.join(form.errors.get('__all__', [])) or '筛选条件无效'
    if error is None:
        data = form.cleaned_data
        token = data.get('token') or uuid.uuid4()
        try:
            if data.get('download'):
                result = export.export_reconciliation_worksheets(
                    member, dataset=data['dataset'], as_of=data.get('as_of'),
                    period=data.get('period'), request_token=token, dry_run=False)
                response = HttpResponse(
                    result['csv'], content_type='text/csv; charset=utf-8')
                response['Content-Disposition'] = (
                    f"attachment; filename=\"{result['filename']}\"")
                response['X-Content-SHA256'] = result['content_id']
                return response
            plan = export.export_reconciliation_worksheets(
                member, dataset=data['dataset'], as_of=data.get('as_of'),
                period=data.get('period'), request_token=token, dry_run=True)
        except PermissionDenied:
            raise
        except ValidationError as invalid:
            error = '；'.join(invalid.messages)
    base = get_script_prefix() + 'modules/delivery/ops-export/'
    download_query = ''
    if plan is not None:
        from urllib.parse import urlencode
        download_query = '?' + urlencode({
            'dataset': plan['dataset'], 'as_of': plan['as_of'] or '',
            'period': plan['period'] or '', 'download': '1',
            'token': plan['token']})
    return render(context.request, 'delivery/ops_export.html', {
        'nav': 'delivery', 'form': form, 'plan': plan, 'error': error,
        'page_url': base, 'download_url': base + download_query,
        'datasets': export.export_dataset_options(),
        'overview_url': get_script_prefix() + 'modules/delivery/ops-reconciliation-overview/',
        'generated_at': timezone.now().isoformat(),
    }, status=400 if error else 200)
