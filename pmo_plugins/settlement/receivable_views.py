"""PH-224 customer settlement browser handlers; every write goes through receivable_services."""
import csv
import io
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import get_script_prefix
from django.utils import timezone

from core.access import require_member, require_project, visible_projects
from core.models import Membership, Project, SourceConsumption
from core.plugin_runtime import is_enabled

from . import receivable_services as services
from .models import Settlement
from .receivable_forms import (ActionForm, AgingFilterForm, ReasonForm, ReviewForm,
                               SettlementForm, SettlementListFilterForm, settlement_line_choices,
                               term_choices, term_key, yuan)

WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'pm', 'auditor'}
ACTIONS = {'submit': ('提交独立审核', ActionForm),
           'review': ('独立核对结算', ReviewForm),
           'withdraw': ('明确撤回', ReasonForm)}
TERM_KIND_LABELS = {'due': '到期款', 'retention': '质保款'}


def _url(page, **query):
    return get_script_prefix() + 'modules/settlement/' + page.replace('_', '-') + '/' + (
        '?' + urlencode({key: value for key, value in query.items() if value is not None})
        if query else '')


def _money(cents):
    return yuan(cents)


def _fresh(member, write=False):
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if member.role not in (WRITE_ROLES if write else READ_ROLES):
        raise PermissionDenied('无权读取或办理客户结算')
    if not is_enabled(member.organization, 'settlement'):
        raise PermissionDenied('客户结算模块已停用')
    if write and (not is_enabled(member.organization, 'contracts')
                  or not is_enabled(member.organization, 'delivery')):
        raise PermissionDenied('合同或履约模块已停用，不能办理客户结算')
    return member


def _base(member, nav_page='settlement', **values):
    return dict(nav='settlement', nav_page=nav_page,
                can_write=member.role in WRITE_ROLES,
                list_url=_url('settlement_list'), new_url=_url('settlement_form'),
                aging_url=_url('aging_list'), **values)


def _snapshot(member, settlement_id):
    try:
        return services.settlement_snapshot(member, settlement_id)
    except ValidationError:
        raise PermissionDenied('结算记录不存在或无权访问')


def _line_snapshots(member):
    from pmo_plugins.contracts.approval_services import approved_line_choices
    return {str(value['line_id']): value for value in approved_line_choices(member)}


def _line_context(member, snapshot):
    from pmo_plugins.contracts.approval_services import approved_terms_snapshot
    from pmo_plugins.delivery.services import confirmed_line_performance
    today = timezone.localdate()
    terms = approved_terms_snapshot(member, snapshot['line_ref'], today)
    try:
        performance = confirmed_line_performance(member, snapshot['line_ref'], today)
    except (ValidationError, PermissionDenied):
        performance = {'confirmed_net_cents': 0, 'sources': []}
    project = Project.objects.get(organization=member.organization, pk=snapshot['project_id'])
    settled_net = Settlement.objects.filter(
        organization=member.organization, contract_line_id=snapshot['line_id'],
        status='active').aggregate(total=Sum('net_cents'))['total'] or 0
    used = set(SourceConsumption.objects.filter(
        organization=member.organization, consumer='settlement',
        source_id=snapshot['line_id'], coverage_key__startswith='term:'
    ).values_list('coverage_key', flat=True))
    term_rows = []
    for term in terms['terms']:
        key = term_key(term)
        term_rows.append({
            'key': key, 'id': term['id'], 'condition_type': term['condition_type'],
            'kind_label': TERM_KIND_LABELS.get(term['kind'], term['kind']),
            'gross_display': _money(term['gross_cents']),
            'due_display': term.get('due_date') or '',
            'release_condition': term.get('release_condition') or '',
            'used': ('term:' + term['term_ref']['kind'] + ':' + term['id']) in used,
        })
    sources = [{'kind': item['kind'], 'ref': item['ref'], 'version': item['version'],
                'net_display': _money(item['net_cents']), 'confirmed_on': item['confirmed_on']}
               for item in performance['sources']]
    return {
        'snapshot': snapshot, 'terms': terms, 'term_rows': term_rows,
        'term_map': {term_key(term): term for term in terms['terms']},
        'line_snapshot': {
            'project': project.name,
            'contract_line': f"{snapshot['contract_code']} · 第 {snapshot['line_no']} 行",
            'counterparty': terms['line'].contract.party.display_name,
            'gross_display': _money(snapshot['gross_cents']),
            'net_display': _money(snapshot['net_cents']),
            'tax_display': _money(snapshot['tax_cents']),
        },
        'confirmed_net_display': _money(performance['confirmed_net_cents']),
        'settled_net_display': _money(settled_net),
        'remaining_net_display': _money(max(performance['confirmed_net_cents'] - settled_net, 0)),
        'sources': sources,
    }


def _explicit_project(member, payload, project_id):
    """A manually supplied project identifier must be authorized even if it is outside the filter choices."""
    requested = payload.get('project')
    if not requested or project_id:
        return
    try:
        project = Project.objects.get(organization=member.organization, pk=requested)
    except (Project.DoesNotExist, ValueError, TypeError):
        raise PermissionDenied('项目不存在或无权访问')
    require_project(member, project, finance=True)


def settlement_list(context, payload):
    member = _fresh(context.member)
    rows = Settlement.objects.filter(organization=member.organization).select_related(
        'project', 'contract_line__contract_version__contract', 'counterparty').order_by('-created_at')
    if member.role == 'pm':
        rows = rows.filter(project__manager=member)
    filter_form = SettlementListFilterForm(payload or None, member=member)
    project_id = status = ''
    if filter_form.is_valid():
        project_id = filter_form.cleaned_data.get('project') or ''
        status = filter_form.cleaned_data.get('status') or ''
    _explicit_project(member, payload, project_id)
    if project_id:
        rows = rows.filter(project_id=project_id)
    if status:
        rows = rows.filter(status=status)
    result = []
    for row in rows[:500]:
        selected = (row.terms_snapshot or {}).get('selected') or []
        result.append({
            'project': row.project.name,
            'contract_line': f"{row.contract_line.contract.contract_code} · 第 {row.contract_line.line_no} 行",
            'counterparty': row.counterparty.display_name,
            'settled_on': row.settled_on, 'due_date': row.due_date,
            'status': row.status, 'status_display': row.get_status_display(),
            'net_display': _money(row.net_cents), 'tax_display': _money(row.tax_cents),
            'gross_display': _money(row.gross_cents),
            'term_count': len(selected), 'item_count': row.items.count(),
            'detail_url': _url('settlement_detail', id=row.pk),
        })
    return render(context.request, 'settlement/receivable_list.html',
                  _base(member, rows=result, filter_form=filter_form))


def _render_form(context, member, *, snapshot, form, title, status=200):
    context_values = _line_context(member, snapshot)
    values = _base(
        member, chooser=False, form=form, title=title,
        explanation=('按批准条款行组合结算：只能选择完整、未使用的条款行；金额与到期日由服务端按批准'
                     '条款和已确认履约派生，页面不提供金额、方向、收款对象或未清分区输入。'),
        submit_url=_url('settlement_save'), back_url=_url('settlement_list'), **context_values)
    return render(context.request, 'settlement/receivable_form.html', values, status=status)


def settlement_form(context, payload):
    member = _fresh(context.member)
    action = payload.get('action', 'save')
    settlement_id = payload.get('id')
    row = _snapshot(member, settlement_id) if settlement_id else None
    if action in ACTIONS:
        if member.role not in WRITE_ROLES:
            raise PermissionDenied('仅公司负责人或财务可办理结算动作')
        if row is None:
            raise PermissionDenied('结算记录不存在或无权访问')
        title, form_class = ACTIONS[action]
        return render(context.request, 'settlement/receivable_form.html', _base(
            member, chooser=False, action_mode=True, title=title,
            form=form_class(initial={'expected_version': row['version']}),
            submit_url=_url('settlement_action', id=row['id'], action=action),
            back_url=_url('settlement_detail', id=row['id']),
            explanation='结算批准须由另一位公司负责人或财务独立办理；草稿/提交/退回/撤回使用普通审计，不生成未清项。'))
    if action not in {'save', 'edit'}:
        raise PermissionDenied('未知结算办理动作')
    if member.role not in WRITE_ROLES:
        raise PermissionDenied('项目经理与审阅账户只读结算，不能登记或修改')
    if row is not None and row['status'] != 'draft':
        raise PermissionDenied('仅可编辑草稿结算')
    choices = settlement_line_choices(member)
    line_id = payload.get('line') or (row['contract_line_id'] if row else None)
    if not line_id:
        return render(context.request, 'settlement/receivable_form.html', _base(
            member, chooser=True, title='选择已批准合同明细', line_choices=choices,
            back_url=_url('settlement_list')))
    snapshots = _line_snapshots(member)
    snapshot = snapshots.get(str(line_id))
    if snapshot is None or str(line_id) not in {value for value, label in choices}:
        raise PermissionDenied('合同明细不存在、无权访问或条款未覆盖全部含税额')
    context_values = _line_context(member, snapshot)
    selected = set()
    initial = {}
    if row is not None:
        selected = {('due' if term['kind'] == 'payment_term' else 'retention') + ':' + term['id']
                    for term in (row['terms_snapshot'] or {}).get('selected', [])}
        initial = {'settlement_id': row['id'], 'expected_version': row['version'],
                   'settled_on': row['settled_on']}
        references = [item.get('reference') for item in row.get('evidence', [])
                      if isinstance(item, dict) and item.get('reference')]
        if references:
            initial['evidence'] = '；'.join(references)
    for term in context_values['term_rows']:
        term['checked'] = term['key'] in selected
    form = SettlementForm(initial=initial, member=member, line_choices=choices,
                          term_choices=term_choices(context_values['terms']))
    form.fields['line'].initial = str(line_id)
    return _render_form(context, member, snapshot=snapshot, form=form,
                        title='修改结算草稿' if row else '登记客户结算')


def settlement_save(context, payload):
    member = _fresh(context.member, write=True)
    choices = settlement_line_choices(member)
    line_id = payload.get('line')
    snapshots = _line_snapshots(member)
    snapshot = snapshots.get(str(line_id))
    if snapshot is None or str(line_id) not in {value for value, label in choices}:
        raise PermissionDenied('合同明细不存在、无权访问或条款未覆盖全部含税额')
    context_values = _line_context(member, snapshot)
    form = SettlementForm(payload, member=member, line_choices=choices,
                          term_choices=term_choices(context_values['terms']))
    if form.is_valid():
        selected = [context_values['term_map'][key] for key in form.cleaned_data['terms']
                    if key in context_values['term_map']]
        evidence = [{'reference': form.cleaned_data['evidence']}]
        try:
            row = services.save_settlement(
                member, contract_line_ref=snapshot['line_ref'],
                term_refs=[term['term_ref'] for term in selected],
                settled_on=form.cleaned_data['settled_on'], evidence=evidence,
                request_token=form.cleaned_data['request_token'],
                settlement_id=form.cleaned_data.get('settlement_id'),
                expected_version=form.cleaned_data.get('expected_version'), dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            return redirect(_url('settlement_detail', id=row.pk))
    posted = payload.getlist('terms') if hasattr(payload, 'getlist') else list(payload.get('terms') or [])
    for term in context_values['term_rows']:
        term['checked'] = term['key'] in posted
    return _render_form(context, member, snapshot=snapshot, form=form,
                        title='修改结算草稿' if payload.get('settlement_id') else '登记客户结算',
                        status=400)


def settlement_detail(context, payload):
    member = _fresh(context.member)
    snapshot = _snapshot(member, payload.get('id'))
    row = snapshot['settlement']
    selected = (snapshot['terms_snapshot'] or {}).get('selected') or []
    terms = [{'kind_label': TERM_KIND_LABELS.get(
                  'due' if term['kind'] == 'payment_term' else 'retention', term['kind']),
              'id': term['id'], 'condition_type': term['condition_type'],
              'release_condition': term.get('release_condition') or '',
              'due_display': term.get('due_date') or '',
              'gross_display': _money(term['gross_cents'])} for term in selected]
    items = []
    for item in snapshot['items']:
        partition_kind = item['partition_kind']
        items.append({**item,
                      'partition_label': '质保未清' if partition_kind == 'retention' else '到期未清',
                      'term_label': f"{item['term_ref']['kind']} · {item['term_ref']['id']}",
                      'gross_display': _money(item['gross_cents']),
                      'tax_display': _money(item['tax_cents']),
                      'status_display': {'active': '有效'}.get(item['status'], item['status'])})
    sources = [{'kind': item['kind'], 'ref': item['ref'],
                'net_display': _money(item['net_cents'])} for item in snapshot['source_performance']]
    links = []
    if member.role in WRITE_ROLES and row.status in {'draft', 'submitted'}:
        if row.status == 'draft':
            links = [('edit', '修改草稿'), ('submit', '提交独立审核'), ('withdraw', '撤回草稿')]
        else:
            links = [('withdraw', '明确撤回')]
            if member.pk not in {row.created_by_id, row.submitted_by_id}:
                links.append(('review', '独立核对结算'))
    action_links = [(_url('settlement_form', id=row.pk, action=action), label)
                    for action, label in links]
    references = [item.get('reference') for item in row.evidence
                  if isinstance(item, dict) and item.get('reference')]
    return render(context.request, 'settlement/receivable_detail.html', _base(
        member, settlement=row, items=items, terms=terms, sources=sources,
        action_links=action_links, status_display=row.get_status_display(),
        net_display=_money(row.net_cents), tax_display=_money(row.tax_cents),
        gross_display=_money(row.gross_cents),
        contract_line=f'{row.contract_line.contract.contract_code} · 第 {row.contract_line.line_no} 行',
        counterparty=row.counterparty.display_name,
        evidence_display='；'.join(references)))


def settlement_action(context, payload):
    member = _fresh(context.member, write=True)
    settlement_id = context.request.GET.get('id')
    action = context.request.GET.get('action', '')
    if action not in ACTIONS:
        raise PermissionDenied('未知结算办理动作')
    if not settlement_id:
        raise PermissionDenied('结算记录不存在或无权访问')
    snapshot = _snapshot(member, settlement_id)
    row = snapshot['settlement']
    title, form_class = ACTIONS[action]
    allowed = set(form_class.base_fields)
    bound = {key: value for key, value in payload.items() if key in allowed}
    form = form_class(bound, initial={'expected_version': row.version})
    if form.is_valid():
        data = form.cleaned_data.copy()
        data['dry_run'] = False
        try:
            if action == 'submit':
                services.submit_settlement(member, row.pk, expected_version=data['expected_version'],
                                           request_token=data['request_token'], dry_run=False)
            elif action == 'review':
                services.review_settlement(member, row.pk, expected_version=data['expected_version'],
                                           request_token=data['request_token'],
                                           decision=data['decision'], note=data['reason'], dry_run=False)
            else:
                services.withdraw_settlement(member, row.pk, expected_version=data['expected_version'],
                                             request_token=data['request_token'],
                                             reason=data['reason'], dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '办理完成')
            return redirect(_url('settlement_detail', id=row.pk))
    return render(context.request, 'settlement/receivable_form.html', _base(
        member, chooser=False, action_mode=True, title=title, form=form,
        submit_url=_url('settlement_action', id=row.pk, action=action),
        back_url=_url('settlement_detail', id=row.pk)), status=400)


BUCKET_ORDER = ('not_due', '1_30', '31_60', '61_90', '90_plus')
AGING_CSV_HEADER = ['账龄基准日', '项目编号', '项目名称', '合同明细', '结算编号', '分区类型', '条款',
                    '释放条件', '到期或释放日', '逾期天数', '账龄桶', '含税（元）', '剩余（元）',
                    '税（元）', '状态', '未清项编号']


def aging_list(context, payload):
    member = _fresh(context.member)
    from . import aging_services
    filter_form = AgingFilterForm(payload or None, member=member)
    project_id = ''
    as_of = timezone.localdate()
    if filter_form.is_valid():
        project_id = filter_form.cleaned_data.get('project') or ''
        as_of = filter_form.cleaned_data.get('as_of') or as_of
    as_of = aging_services._as_of(as_of)
    _explicit_project(member, payload, project_id)
    if payload.get('format') == 'csv':
        rows = aging_services.aging_export_rows(member, project_id=project_id or None, as_of=as_of)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(AGING_CSV_HEADER)
        for row in rows:
            writer.writerow([row.get(label, '') for label in AGING_CSV_HEADER])
        response = HttpResponse(buffer.getvalue(), content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="settlement-aging.csv"'
        return response
    data = aging_services.open_item_aging(member, project_id=project_id or None, as_of=as_of)
    settlements = {str(value.pk): value for value in Settlement.objects.filter(
        organization=member.organization, pk__in={item['settlement_id'] for item in data['items']}
    ).select_related('counterparty')}
    rows, counts = [], {key: 0 for key in BUCKET_ORDER}
    for item in data['items']:
        settlement = settlements.get(item['settlement_id'])
        counts[item['bucket']] += 1
        rows.append({
            'project': item['project_name'],
            'counterparty': settlement.counterparty.display_name if settlement else '—',
            'settled_on': settlement.settled_on if settlement else None,
            'partition_kind': item['partition_kind'], 'partition_label': item['partition_label'],
            'term_label': item['term_label'], 'due_date': item['due_date'],
            'release_condition': item['release_condition'],
            'gross_display': item['gross_display'],
            'bucket_label': item['bucket_label'],
            'retention_not_due': (item['partition_kind'] == 'retention'
                                  and item['bucket'] == 'not_due'),
        })
    buckets = [{'key': key, 'label': data['bucket_labels'][key],
                'amount_display': _money(data['buckets'][key]), 'count': counts[key]}
               for key in BUCKET_ORDER]
    return render(context.request, 'settlement/aging_list.html', _base(
        member, nav_page='aging', filter_form=filter_form, buckets=buckets, rows=rows,
        total_display=_money(data['totals']['remaining_cents']), as_of=as_of,
        retention=data['retention'],
        csv_url=_url('aging_list', format='csv', as_of=as_of.isoformat(),
                     project=project_id or None)))
