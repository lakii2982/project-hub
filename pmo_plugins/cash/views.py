"""PH-228 cash browser handlers; writes go through funding/transaction owner services."""
from urllib.parse import urlencode
from decimal import Decimal
import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import get_script_prefix
from django.utils import timezone

from core.access import require_member
from core.models import Employee, Membership
from core.plugin_runtime import is_enabled

from . import transaction_services as tx
from . import funding_services as funding
from .forms import (CashTransactionForm, CashTransactionListFilterForm,
                    FundingAccountForm, FundingAccountListFilterForm, IdentifyPartyForm,
                    ReversePartyForm, amount_cents)

WRITE_ROLES = {'owner', 'finance'}
READ_ROLES = {'owner', 'finance', 'auditor'}


def _url(page, **query):
    return get_script_prefix() + 'modules/cash/' + page.replace('_', '-') + '/' + (
        '?' + urlencode({key: value for key, value in query.items() if value is not None})
        if query else '')


def _fresh(member, write=False):
    try:
        member = Membership.objects.select_related('organization', 'user').get(pk=member.pk)
    except (AttributeError, Membership.DoesNotExist):
        raise PermissionDenied('公司成员上下文无效')
    require_member(member)
    if member.role not in (WRITE_ROLES if write else READ_ROLES):
        raise PermissionDenied('无权办理或查看公司银行事实')
    if write and not is_enabled(member.organization, 'cash'):
        raise PermissionDenied('现金事实模块已停用')
    return member


def _base(member, nav_page, **values):
    return dict(nav='cash', nav_page=nav_page, can_write=member.role in WRITE_ROLES,
                can_activate=member.role == 'owner',
                accounts_url=_url('funding_account_list'),
                transactions_url=_url('cash_transaction_list'), **values)


def _account_options(member, *, active_only=False):
    rows = funding.funding_account_list(member)
    if active_only:
        rows = [row for row in rows if row['status'] == 'active']
    return [(row['id'], f"{row['name']}"
             + (f" · {row['masked_identifier']}" if row['masked_identifier'] else ''))
            for row in rows]


def _counterparty_options(member):
    from pmo_plugins.contracts.party_services import list_parties
    return [(str(party.pk), party.display_name) for party in list_parties(member)]


def _employee_options(member):
    return [(str(employee.pk), employee.name) for employee in Employee.objects.filter(
        organization=member.organization, active=True).order_by('name', 'id')]


def _evidence_text(evidence):
    return [str(item.get('kind', 'attachment')) + ' · ' + str(item.get('evidence_id', ''))[:8]
            for item in (evidence or []) if isinstance(item, dict)]


def _evidence_refs(text):
    return [{'reference': text}]


def funding_account_list(context, payload):
    member = _fresh(context.member)
    rows = funding.funding_account_list(member)
    filter_form = FundingAccountListFilterForm(payload or None)
    status = filter_form.cleaned_data.get('status') if filter_form.is_valid() else ''
    result = []
    for row in rows:
        if status and row['status'] != status:
            continue
        result.append({**row,
                       'opening_display': format(Decimal(row['opening_cents']) / 100, '.2f'),
                       'account_owner_display': '本公司',
                       'edit_url': _url('funding_account_form', id=row['id']),
                       'activate_url': _url('funding_account_activate', id=row['id']),
                       'activate_token': uuid.uuid4(),
                       'transactions_url': _url('cash_transaction_list', account=row['id'])})
    return render(context.request, 'cash/funding_account_list.html', _base(
        member, 'accounts', rows=result, filter_form=filter_form,
        new_url=_url('funding_account_form')))


def funding_account_form(context, payload):
    member = _fresh(context.member, write=True)
    account_id = payload.get('id')
    snapshot = funding.funding_account_snapshot(member, account_id) if account_id else None
    if snapshot and snapshot['status'] != 'draft':
        raise PermissionDenied('已启用银行账户不可修改')
    initial = {}
    if snapshot:
        initial = {'account_id': snapshot['id'], 'expected_version': snapshot['version'],
                   'name': snapshot['name'], 'masked_identifier': snapshot['masked_identifier']}
    return render(context.request, 'cash/funding_account_form.html', _base(
        member, 'accounts', form=FundingAccountForm(initial=initial),
        title='修改银行账户草稿' if snapshot else '登记银行账户',
        submit_url=_url('funding_account_save'), back_url=_url('funding_account_list'),
        opening_on=snapshot['opening_on'] if snapshot else timezone.localdate().isoformat(),
        explanation='登记公司自有银行账户；草稿可修改，owner 激活后不可改。'
                    '期初余额固定 0，本片不记现金账。'))


def funding_account_save(context, payload):
    member = _fresh(context.member, write=True)
    form = FundingAccountForm(payload)
    if form.is_valid():
        data = form.cleaned_data
        try:
            funding.save_funding_account(
                member, name=data['name'], masked_identifier=data['masked_identifier'],
                request_token=data['request_token'], account_id=data.get('account_id'),
                expected_version=data.get('expected_version'), dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '银行账户已保存')
            return redirect(_url('funding_account_list'))
    return render(context.request, 'cash/funding_account_form.html', _base(
        member, 'accounts', form=form, title='登记银行账户',
        submit_url=_url('funding_account_save'), back_url=_url('funding_account_list'),
        opening_on=timezone.localdate().isoformat(),
        explanation='登记公司自有银行账户；草稿可修改，owner 激活后不可改。'
                    '期初余额固定 0，本片不记现金账。'), status=400)


def funding_account_activate(context, payload):
    member = _fresh(context.member, write=True)
    account_id = context.request.GET.get('id')
    if not account_id:
        raise PermissionDenied('银行账户不存在或无权访问')
    try:
        try:
            expected_version = int(payload.get('expected_version'))
        except (TypeError, ValueError):
            raise ValidationError('银行账户版本无效，请刷新后重试')
        funding.activate_funding_account(
            member, account_id, expected_version=expected_version,
            request_token=payload.get('request_token'), dry_run=False)
    except ValidationError as exc:
        messages.error(context.request, '；'.join(exc.messages))
        return redirect(_url('funding_account_list'))
    messages.success(context.request, '银行账户已启用')
    return redirect(_url('funding_account_list'))


def cash_transaction_list(context, payload):
    member = _fresh(context.member)
    account_options = _account_options(member)
    filter_form = CashTransactionListFilterForm(payload or None, accounts=account_options)
    account_id = direction = ''
    as_of = None
    if filter_form.is_valid():
        account_id = filter_form.cleaned_data.get('funding_account') or ''
        direction = filter_form.cleaned_data.get('direction') or ''
        as_of = filter_form.cleaned_data.get('as_of')
    rows = tx.cash_transaction_list(member, as_of=as_of)
    result = []
    for row in rows:
        if account_id and row['funding_account_id'] != account_id:
            continue
        if direction and row['direction'] != direction:
            continue
        reversed_ids = {item['original_match_ref']['id'] for item in row['matches']
                        if item['action'] == 'reverse' and item.get('original_match_ref')}
        active = [item for item in row['matches']
                  if item['action'] == 'identify' and item['id'] not in reversed_ids]
        result.append({**row, 'account_name': row['funding_account_name'],
                       'amount_display': row['gross_display'],
                       'match_display': active[-1]['identified_payee_display'] if active else '未匹配',
                       'detail_url': _url('cash_transaction_detail', id=row['id'])})
    return render(context.request, 'cash/cash_transaction_list.html', _base(
        member, 'transactions', rows=result, filter_form=filter_form,
        new_url=_url('cash_transaction_form')))


def cash_transaction_form(context, payload):
    member = _fresh(context.member, write=True)
    transaction_id = payload.get('id')
    snapshot = tx.cash_transaction_snapshot(member, transaction_id) if transaction_id else None
    if snapshot and snapshot['status'] != 'draft':
        raise PermissionDenied('非草稿银行流水不可修改')
    initial = {}
    if snapshot:
        payee = snapshot['payee'] or {}
        initial = {'transaction_id': snapshot['id'], 'expected_version': snapshot['version'],
                   'funding_account': snapshot['funding_account_id'],
                   'transaction_date': snapshot['transaction_date'],
                   'direction': snapshot['direction'],
                   'amount': Decimal(snapshot['gross_cents']) / 100,
                   'external_source_key': snapshot['external_source_key'],
                   'payee_kind': payee.get('kind', 'unidentified'),
                   'counterparty': payee.get('id') if payee.get('kind') == 'counterparty' else '',
                   'own_account': payee.get('id') if payee.get('kind') == 'own_account' else ''}
    accounts = _account_options(member, active_only=True)
    counterparties = _counterparty_options(member)
    form = CashTransactionForm(initial=initial, accounts=accounts, counterparties=counterparties)
    return render(context.request, 'cash/cash_transaction_form.html', _base(
        member, 'transactions', form=form,
        title='修改银行流水草稿' if snapshot else '登记银行流水',
        submit_url=_url('cash_transaction_save'), back_url=_url('cash_transaction_list'),
        explanation='登记真实银行流水事实；流水草稿不产生现金账、收入、成本或未清项。'
                    '同一账户内来源编号重复提交同输入返回原记录。'))


def _payee_value(data):
    kind = data['payee_kind']
    if kind == 'unidentified':
        return {'kind': 'unidentified'}
    if kind == 'counterparty':
        return {'kind': 'counterparty', 'id': data['counterparty']}
    return {'kind': 'own_account', 'id': data['own_account']}


def cash_transaction_save(context, payload):
    member = _fresh(context.member, write=True)
    accounts = _account_options(member, active_only=True)
    counterparties = _counterparty_options(member)
    form = CashTransactionForm(payload, accounts=accounts, counterparties=counterparties)
    if form.is_valid():
        data = form.cleaned_data
        try:
            row = tx.save_cash_transaction(
                member, funding_account_id=data['funding_account'],
                transaction_date=data['transaction_date'], direction=data['direction'],
                gross_cents=amount_cents(data['amount']),
                external_source_key=data['external_source_key'], payee=_payee_value(data),
                evidence=_evidence_refs(data['evidence']), request_token=data['request_token'],
                transaction_id=data.get('transaction_id'),
                expected_version=data.get('expected_version'), dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '银行流水已保存')
            return redirect(_url('cash_transaction_detail', id=row.pk))
    return render(context.request, 'cash/cash_transaction_form.html', _base(
        member, 'transactions', form=form, title='登记银行流水',
        submit_url=_url('cash_transaction_save'), back_url=_url('cash_transaction_list'),
        explanation='登记真实银行流水事实；流水草稿不产生现金账、收入、成本或未清项。'),
        status=400)


def _reverse_choices(snapshot):
    reversed_ids = {item['original_match_ref']['id'] for item in snapshot['matches']
                    if item['action'] == 'reverse' and item.get('original_match_ref')}
    return [(item['id'], f"{item['action_display']} · {item['identified_payee_display']} "
                         f"(v{item['version']})")
            for item in snapshot['matches']
            if item['action'] == 'identify' and item['id'] not in reversed_ids]


def _detail_context(member, snapshot, *, identify_form=None, reverse_form=None):
    matches = []
    for item in snapshot['matches']:
        original = item.get('original_match_ref') or {}
        matches.append({**item,
                        'identified_display': item.get('identified_payee_display', '—'),
                        'original_match_display': (str(original.get('kind', '')) + ' · '
                                                   + str(original.get('id', ''))[:8])
                        if original else '—',
                        'evidence_display': '；'.join(_evidence_text(item.get('evidence'))) or '—',
                        'actor_display': str(item.get('created_by_member_id', ''))[:8],
                        'created_display': str(item.get('created_at', ''))[:16].replace('T', ' ')})
    choices = _reverse_choices(snapshot)
    identify_form = identify_form or IdentifyPartyForm(
        initial={'transaction_id': snapshot['id'], 'expected_version': snapshot['version']},
        counterparties=_counterparty_options(member), employees=_employee_options(member),
        accounts=_account_options(member, active_only=True))
    reverse_form = reverse_form or ReversePartyForm(
        initial={'transaction_id': snapshot['id'], 'expected_version': snapshot['version']},
        matches=choices)
    return _base(
        member, 'transactions', transaction=snapshot,
        transaction_account=snapshot['funding_account_name'],
        direction_display=snapshot['direction_display'],
        transaction_date=snapshot['transaction_date'],
        status_display=snapshot['status_display'], amount_display=snapshot['gross_display'],
        payee_display=snapshot['payee_display'],
        evidence_rows=_evidence_text(snapshot.get('evidence')),
        matches=matches, identify_form=identify_form, reverse_form=reverse_form,
        identify_url=_url('cash_party_match', id=snapshot['id'], action='identify'),
        reverse_url=_url('cash_party_match', id=snapshot['id'], action='reverse'),
        back_url=_url('cash_transaction_list'))


def cash_transaction_detail(context, payload):
    member = _fresh(context.member)
    snapshot = tx.cash_transaction_snapshot(member, payload.get('id'))
    return render(context.request, 'cash/cash_transaction_detail.html',
                  _detail_context(member, snapshot))


def cash_party_match(context, payload):
    member = _fresh(context.member, write=True)
    transaction_id = context.request.GET.get('id')
    action = context.request.GET.get('action', '')
    if action not in {'identify', 'reverse'}:
        raise PermissionDenied('未知往来方匹配动作')
    snapshot = tx.cash_transaction_snapshot(member, transaction_id)
    if action == 'identify':
        allowed = set(IdentifyPartyForm.base_fields)
        bound = {key: value for key, value in payload.items() if key in allowed}
        bound['transaction_id'] = snapshot['id']
        bound['expected_version'] = snapshot['version']
        bound['action'] = 'identify'
        form = IdentifyPartyForm(bound, counterparties=_counterparty_options(member),
                                 employees=_employee_options(member),
                                 accounts=_account_options(member, active_only=True))
        if form.is_valid():
            data = form.cleaned_data
            kind = data['payee_kind']
            try:
                tx.match_party(member, snapshot['id'], action='identify',
                               identified_payee={'kind': kind, 'id': data[kind]},
                               evidence=_evidence_refs(data['evidence']),
                               request_token=data['request_token'],
                               expected_version=snapshot['version'], dry_run=False)
            except ValidationError as exc:
                form.add_error(None, '；'.join(exc.messages))
            else:
                messages.success(context.request, '往来方已识别')
                return redirect(_url('cash_transaction_detail', id=snapshot['id']))
        return render(context.request, 'cash/cash_transaction_detail.html',
                      _detail_context(member, snapshot, identify_form=form), status=400)
    choices = _reverse_choices(snapshot)
    allowed = set(ReversePartyForm.base_fields)
    bound = {key: value for key, value in payload.items() if key in allowed}
    bound['transaction_id'] = snapshot['id']
    bound['expected_version'] = snapshot['version']
    bound['action'] = 'reverse'
    form = ReversePartyForm(bound, matches=choices)
    if form.is_valid():
        data = form.cleaned_data
        original = next((item for item in snapshot['matches']
                         if item['id'] == data['original_match_ref']), None)
        if original is None:
            form.add_error('original_match_ref', '原匹配不属于该银行流水')
        else:
            try:
                tx.match_party(member, snapshot['id'], action='reverse',
                               match_ref=original['match_ref'], reason=data['reason'],
                               evidence=_evidence_refs(data['evidence']),
                               request_token=data['request_token'],
                               expected_version=snapshot['version'], dry_run=False)
            except ValidationError as exc:
                form.add_error(None, '；'.join(exc.messages))
            else:
                messages.success(context.request, '已反向匹配')
                return redirect(_url('cash_transaction_detail', id=snapshot['id']))
    return render(context.request, 'cash/cash_transaction_detail.html',
                  _detail_context(member, snapshot, reverse_form=form), status=400)
