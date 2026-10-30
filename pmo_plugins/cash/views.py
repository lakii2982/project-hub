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
from . import internal_transfer_services as internal
from . import refund_services as refunds
from . import classification_services as cls
from . import funding_services as funding
from .forms import (BankReturnForm, CashClassificationForm, CashClassificationListFilterForm,
                    CashTransactionForm, CashTransactionListFilterForm,
                    FundingAccountForm, FundingAccountListFilterForm, IdentifyPartyForm,
                    InternalTransferForm, InternalTransferListFilterForm,
                    PostCashTransactionForm, RefundForm, RefundListFilterForm,
                    ReverseClassificationForm, ReverseInternalTransferForm,
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
                transactions_url=_url('cash_transaction_list'),
                internal_transfer_url=_url('cash_internal_transfer_list'), **values)


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
        coverage = None
        try:
            coverage = funding.cash_account_coverage(member, row['id'])
        except (ValidationError, PermissionDenied):
            coverage = None
        result.append({**row,
                       'opening_display': format(Decimal(row['opening_cents']) / 100, '.2f'),
                       'account_owner_display': '本公司',
                       'coverage': coverage,
                       'coverage_net_display': format(Decimal(
                           coverage['posted_net_estimate_cents']) / 100, '.2f') if coverage else '0.00',
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
    account_id = direction = status = ''
    as_of = None
    if filter_form.is_valid():
        account_id = filter_form.cleaned_data.get('funding_account') or ''
        direction = filter_form.cleaned_data.get('direction') or ''
        status = filter_form.cleaned_data.get('status') or ''
        as_of = filter_form.cleaned_data.get('as_of')
    try:
        rows = tx.cash_transaction_list(member, as_of=as_of, status=status or None)
    except (ValidationError, TypeError):
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
                       'can_post': bool(row.get('can_post', row.get('status') == 'draft')),
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
    # Reversed identifies stay selectable so an explicit reverse attempt reaches the owner
    # service and shows its business message instead of a generic choice error.
    reversed_ids = {item['original_match_ref']['id'] for item in snapshot['matches']
                    if item['action'] == 'reverse' and item.get('original_match_ref')}
    return [(item['id'], f"{item['action_display']} · {item['identified_payee_display']} "
                         f"(v{item['version']})"
             + ('（已反向）' if item['id'] in reversed_ids else ''))
            for item in snapshot['matches'] if item['action'] == 'identify']


def _detail_context(member, snapshot, *, identify_form=None, reverse_form=None, post_form=None,
                    classification_form=None, reversal_form=None):
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
    post_form = post_form or PostCashTransactionForm(
        initial={'transaction_id': snapshot['id'], 'expected_version': snapshot['version']})
    can_post = bool(snapshot.get('can_post', snapshot.get('status') == 'draft'))
    coverage = None
    refund_rows = []
    return_rows = []
    if snapshot['status'] == 'posted':
        try:
            coverage = refunds.cash_refund_coverage(member, snapshot['id'])
        except (ValidationError, PermissionDenied):
            coverage = None
        refund_rows = [_refund_row_view(row) for row in refunds.refund_list(
            member, original_cash_transaction_id=snapshot['id'])]
        return_rows = [_return_row_view(row) for row in refunds.bank_return_list(
            member, original_cash_transaction_id=snapshot['id'])]
    provenance = refunds.provenance_for_cash(member, snapshot['id'])
    can_refund = bool(member.role in WRITE_ROLES and snapshot['status'] == 'posted'
                      and coverage and coverage['refundable_cents'] > 0)
    refund_form = RefundForm(initial={'original_cash_transaction_id': snapshot['id']})
    return_form = BankReturnForm(initial={'original_cash_transaction_id': snapshot['id']})
    internal_transfer = internal.internal_transfer_for_cash(member, snapshot['id'])
    classifications = cls.classification_list(member, cash_transaction_id=snapshot['id'])
    active_classification = next(
        (row for row in classifications if row['status'] == 'active'), None)
    can_classify = (member.role in WRITE_ROLES and snapshot['status'] == 'posted'
                    and active_classification is None)
    classification_form = classification_form or CashClassificationForm(
        initial={'transaction_id': snapshot['id']})
    reversal_form = reversal_form or (
        ReverseClassificationForm(initial={'classification_id': active_classification['id'],
                                           'expected_version': active_classification['version']})
        if active_classification else None)
    classification_rows = []
    for item in classifications:
        reversal = item.get('reversal_ref') or {}
        classification_rows.append({
            **item,
            'created_display': str(item.get('created_at', ''))[:16].replace('T', ' '),
            'reversal_display': (str(reversal.get('id', ''))[:8] if reversal
                                 else '—'),
            'bridge_display': ('已桥接 · ' + str(item['posting_event_ref']['id'])[:8]
                               if item.get('posting_event_ref') else '待桥接（PH-243）'),
            'can_reverse': member.role in WRITE_ROLES and item['status'] == 'active',
        })
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
    post_form = post_form or PostCashTransactionForm(
        initial={'transaction_id': snapshot['id'], 'expected_version': snapshot['version']})
    can_post = bool(snapshot.get('can_post', snapshot.get('status') == 'draft'))
    return _base(
        member, 'transactions', transaction=snapshot, can_post=can_post,
        posted_display=(snapshot.get('posted_at') or '')[:16].replace('T', ' '),
        post_form=post_form, post_url=_url('cash_transaction_record', id=snapshot['id']),
        transaction_account=snapshot['funding_account_name'],
        direction_display=snapshot['direction_display'],
        transaction_date=snapshot['transaction_date'],
        status_display=snapshot['status_display'], amount_display=snapshot['gross_display'],
        payee_display=snapshot['payee_display'],
        evidence_rows=_evidence_text(snapshot.get('evidence')),
        matches=matches, identify_form=identify_form, reverse_form=reverse_form,
        identify_url=_url('cash_party_match', id=snapshot['id'], action='identify'),
        reverse_url=_url('cash_party_match', id=snapshot['id'], action='reverse'),
        coverage=coverage, refund_rows=refund_rows, return_rows=return_rows,
        provenance=provenance, can_refund=can_refund,
        refunds=refund_rows, bank_returns=return_rows,
        refund_form=refund_form, return_form=return_form,
        refund_form_url=_url('cash_refund_form', id=snapshot['id']),
        return_form_url=_url('cash_bank_return_form', id=snapshot['id']),
        refunds_url=_url('cash_refund_list'),
        internal_transfer=internal_transfer,
        classifications=classification_rows, active_classification=active_classification,
        can_classify=can_classify, classification_form=classification_form,
        reversal_form=reversal_form,
        classify_url=_url('cash_classification_form', id=snapshot['id']),
        classify_reverse_url=(_url('cash_classification_reverse',
                                   id=active_classification['id'])
                              if active_classification else None),
        classification_list_url=_url('cash_classification_list'),
        back_url=_url('cash_transaction_list'))


def cash_transaction_detail(context, payload):
    member = _fresh(context.member)
    snapshot = tx.cash_transaction_snapshot(member, payload.get('id'))
    return render(context.request, 'cash/cash_transaction_detail.html',
                  _detail_context(member, snapshot))


def cash_transaction_record(context, payload):
    member = _fresh(context.member, write=True)
    transaction_id = context.request.GET.get('id')
    if not transaction_id:
        raise PermissionDenied('银行流水不存在或无权访问')
    allowed = set(PostCashTransactionForm.base_fields)
    bound = {key: value for key, value in payload.items() if key in allowed}
    bound['transaction_id'] = transaction_id
    form = PostCashTransactionForm(bound)
    if form.is_valid():
        data = form.cleaned_data
        try:
            tx.record_cash_transaction(
                member, data['transaction_id'], request_token=data['request_token'],
                expected_version=data['expected_version'], dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '银行流水已过账，现金事实不可再修改')
            return redirect(_url('cash_transaction_detail', id=data['transaction_id']))
    snapshot = tx.cash_transaction_snapshot(member, transaction_id)
    return render(context.request, 'cash/cash_transaction_detail.html',
                  _detail_context(member, snapshot, post_form=form), status=400)


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


# ---------- PH-240 refund / bank return ----------
def _refund_row_view(row):
    return {**row,
            'created_display': str(row.get('created_at', ''))[:16].replace('T', ' '),
            'original_url': _url('cash_transaction_detail',
                                 id=row['original_cash_transaction_id']),
            'cash_url': _url('cash_transaction_detail', id=row['refund_cash_transaction_id'])}


def _return_row_view(row):
    return {**row,
            'created_display': str(row.get('created_at', ''))[:16].replace('T', ' '),
            'original_url': _url('cash_transaction_detail',
                                 id=row['original_cash_transaction_id']),
            'cash_url': _url('cash_transaction_detail', id=row['return_cash_transaction_id'])}


def cash_refund_list(context, payload):
    member = _fresh(context.member)
    account_options = _account_options(member)
    filter_form = RefundListFilterForm(payload or None, accounts=account_options)
    account_id = ''
    as_of = None
    if filter_form.is_valid():
        account_id = filter_form.cleaned_data.get('funding_account') or ''
        as_of = filter_form.cleaned_data.get('as_of')
    refund_rows = refunds.refund_list(member, funding_account_id=account_id or None,
                                      as_of=as_of)
    return_rows = refunds.bank_return_list(member, funding_account_id=account_id or None,
                                            as_of=as_of)
    return render(context.request, 'cash/refund_list.html', _base(
        member, 'refunds', refund_rows=[_refund_row_view(row) for row in refund_rows],
        return_rows=[_return_row_view(row) for row in return_rows], filter_form=filter_form,
        refunds_url=_url('cash_refund_list')))


def cash_refund_form(context, payload):
    member = _fresh(context.member, write=True)
    original_id = context.request.GET.get('id')
    if not original_id:
        raise PermissionDenied('原现金流水不存在或无权访问')
    snapshot = tx.cash_transaction_snapshot(member, original_id)
    if snapshot['status'] != 'posted' or snapshot['direction'] != 'in':
        raise PermissionDenied('仅已过账收入类现金可以退款')
    coverage = refunds.cash_refund_coverage(member, snapshot['id'])
    if coverage['refundable_cents'] <= 0:
        raise PermissionDenied('该现金已无可退余额')
    form = RefundForm(initial={'original_cash_transaction_id': snapshot['id']})
    return render(context.request, 'cash/refund_form.html', _base(
        member, 'refunds', transaction=snapshot, coverage=coverage, form=form,
        submit_url=_url('cash_refund_save'),
        back_url=_url('cash_transaction_detail', id=snapshot['id']),
        refunds_url=_url('cash_refund_list'),
        explanation='退款会生成一条真实的反方向已过账现金流水；可退额由服务端从核销/退款/退回派生，不接受调用方余额或证明。'))


def cash_refund_save(context, payload):
    member = _fresh(context.member, write=True)
    form = RefundForm(payload)
    snapshot = None
    if form.is_valid():
        data = form.cleaned_data
        snapshot = tx.cash_transaction_snapshot(
            member, data['original_cash_transaction_id'])
        try:
            refunds.record_refund(
                member, original_cash_transaction_id=data['original_cash_transaction_id'],
                refund_date=data['refund_date'], gross_cents=amount_cents(data['amount']),
                reason=data['reason'], request_token=data['request_token'], dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '退款已登记并生成反方向现金流水')
            return redirect(_url('cash_transaction_detail',
                                 id=data['original_cash_transaction_id']))
    if snapshot is None:
        original_id = payload.get('original_cash_transaction_id')
        if not original_id:
            raise PermissionDenied('原现金流水不存在或无权访问')
        snapshot = tx.cash_transaction_snapshot(member, original_id)
    coverage = refunds.cash_refund_coverage(member, snapshot['id'])
    return render(context.request, 'cash/refund_form.html', _base(
        member, 'refunds', transaction=snapshot, coverage=coverage, form=form,
        submit_url=_url('cash_refund_save'),
        back_url=_url('cash_transaction_detail', id=snapshot['id']),
        refunds_url=_url('cash_refund_list'),
        explanation='退款会生成一条真实的反方向已过账现金流水；可退额由服务端从核销/退款/退回派生，不接受调用方余额或证明。'),
        status=400)


def cash_bank_return_form(context, payload):
    member = _fresh(context.member, write=True)
    original_id = context.request.GET.get('id')
    if not original_id:
        raise PermissionDenied('原现金流水不存在或无权访问')
    snapshot = tx.cash_transaction_snapshot(member, original_id)
    if snapshot['status'] != 'posted':
        raise PermissionDenied('仅已过账现金可以登记银行退回')
    coverage = refunds.cash_refund_coverage(member, snapshot['id'])
    if coverage['refundable_cents'] <= 0:
        raise PermissionDenied('该现金已无可退回余额')
    form = BankReturnForm(initial={'original_cash_transaction_id': snapshot['id']})
    return render(context.request, 'cash/bank_return_form.html', _base(
        member, 'refunds', transaction=snapshot, coverage=coverage, form=form,
        submit_url=_url('cash_bank_return_save'),
        back_url=_url('cash_transaction_detail', id=snapshot['id']),
        refunds_url=_url('cash_refund_list'),
        explanation='银行退回按原方向取反生成新的已过账现金流水；可退回额与退款共用服务端派生覆盖。'))


def cash_bank_return_save(context, payload):
    member = _fresh(context.member, write=True)
    form = BankReturnForm(payload)
    snapshot = None
    if form.is_valid():
        data = form.cleaned_data
        snapshot = tx.cash_transaction_snapshot(
            member, data['original_cash_transaction_id'])
        try:
            refunds.record_bank_return(
                member, original_cash_transaction_id=data['original_cash_transaction_id'],
                return_date=data['return_date'], gross_cents=amount_cents(data['amount']),
                request_token=data['request_token'], dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '银行退回已登记并生成反方向现金流水')
            return redirect(_url('cash_transaction_detail',
                                 id=data['original_cash_transaction_id']))
    if snapshot is None:
        original_id = payload.get('original_cash_transaction_id')
        if not original_id:
            raise PermissionDenied('原现金流水不存在或无权访问')
        snapshot = tx.cash_transaction_snapshot(member, original_id)
    coverage = refunds.cash_refund_coverage(member, snapshot['id'])
    return render(context.request, 'cash/bank_return_form.html', _base(
        member, 'refunds', transaction=snapshot, coverage=coverage, form=form,
        submit_url=_url('cash_bank_return_save'),
        back_url=_url('cash_transaction_detail', id=snapshot['id']),
        refunds_url=_url('cash_refund_list'),
        explanation='银行退回按原方向取反生成新的已过账现金流水；可退回额与退款共用服务端派生覆盖。'),
        status=400)


# ---------- PH-239 classification ----------
def _classification_row_view(member, row):
    reversal = row.get('reversal_ref') or {}
    return {
        **row,
        'created_display': str(row.get('created_at', ''))[:16].replace('T', ' '),
        'reversal_display': str(reversal.get('id', ''))[:8] if reversal else '—',
        'bridge_display': ('已桥接 · ' + str(row['posting_event_ref']['id'])[:8]
                           if row.get('posting_event_ref') else '待桥接（PH-243）'),
        'transaction_url': _url('cash_transaction_detail', id=row['cash_transaction_id']),
        'reverse_url': _url('cash_classification_reverse', id=row['id']),
    }


def cash_classification_list(context, payload):
    member = _fresh(context.member)
    account_options = _account_options(member)
    filter_form = CashClassificationListFilterForm(payload or None, accounts=account_options)
    account_id = status = ''
    if filter_form.is_valid():
        account_id = filter_form.cleaned_data.get('funding_account') or ''
        status = filter_form.cleaned_data.get('status') or ''
    result = []
    for row in cls.classification_list(member):
        if account_id and row['funding_account_id'] != account_id:
            continue
        if status and row['status'] != status:
            continue
        item = _classification_row_view(member, row)
        item['can_reverse'] = member.role in WRITE_ROLES and row['status'] == 'active'
        result.append(item)
    coverage_rows = []
    for value, label in account_options:
        try:
            coverage = cls.cash_book_coverage(member, value)
        except (ValidationError, PermissionDenied):
            continue
        coverage_rows.append({
            'label': label, **coverage,
            'cash_in_display': format(Decimal(coverage['from_ledger_cash_in_cents']) / 100, '.2f'),
            'cash_out_display': format(Decimal(coverage['from_ledger_cash_out_cents']) / 100, '.2f'),
            'book_net_display': format(Decimal(coverage['book_net_cents']) / 100, '.2f')})
    return render(context.request, 'cash/cash_classification_list.html', _base(
        member, 'classifications', rows=result, coverage_rows=coverage_rows,
        filter_form=filter_form,
        classification_list_url=_url('cash_classification_list')))


def cash_classification_form(context, payload):
    member = _fresh(context.member, write=True)
    transaction_id = context.request.GET.get('id') or payload.get('transaction_id')
    if not transaction_id:
        raise PermissionDenied('银行流水不存在或无权访问')
    snapshot = tx.cash_transaction_snapshot(member, transaction_id)
    if snapshot['status'] != 'posted':
        raise PermissionDenied('仅已过账银行流水可以办理现金分类')
    if [row for row in cls.classification_list(
            member, cash_transaction_id=snapshot['id']) if row['status'] == 'active']:
        raise PermissionDenied('该银行流水已有有效分类；如需重新分类请先撤分类')
    form = CashClassificationForm(initial={'transaction_id': snapshot['id']})
    return render(context.request, 'cash/cash_classification_form.html', _base(
        member, 'classifications', transaction=snapshot, form=form,
        submit_url=_url('cash_classification_save'),
        back_url=_url('cash_transaction_detail', id=snapshot['id']),
        classification_list_url=_url('cash_classification_list'),
        explanation='分类只记录归属，不改金额/日期/方向/账户/往来方；每条流水同时只能有一条有效分类。'))


def cash_classification_save(context, payload):
    member = _fresh(context.member, write=True)
    form = CashClassificationForm(payload)
    snapshot = None
    if form.is_valid():
        data = form.cleaned_data
        snapshot = tx.cash_transaction_snapshot(member, data['transaction_id'])
        try:
            cls.record_classification(
                member, cash_transaction_id=data['transaction_id'],
                classification=data['classification'],
                evidence=_evidence_refs(data['evidence']),
                request_token=data['request_token'], dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '现金分类已登记')
            return redirect(_url('cash_transaction_detail', id=data['transaction_id']))
    if snapshot is None:
        transaction_id = payload.get('transaction_id')
        if transaction_id:
            snapshot = tx.cash_transaction_snapshot(member, transaction_id)
        else:
            raise PermissionDenied('银行流水不存在或无权访问')
    return render(context.request, 'cash/cash_classification_form.html', _base(
        member, 'classifications', transaction=snapshot, form=form,
        submit_url=_url('cash_classification_save'),
        back_url=_url('cash_transaction_detail', id=snapshot['id']),
        classification_list_url=_url('cash_classification_list'),
        explanation='分类只记录归属，不改金额/日期/方向/账户/往来方；每条流水同时只能有一条有效分类。'),
        status=400)


def _reverse_classification_page(context, member, snapshot, form, status=200):
    return render(context.request, 'cash/cash_classification_form.html', _base(
        member, 'classifications', transaction=snapshot, form=form, reverse_mode=True,
        submit_url=_url('cash_classification_reverse_save'),
        back_url=_url('cash_transaction_detail', id=snapshot['cash_transaction_id']),
        classification_list_url=_url('cash_classification_list'),
        explanation='撤分类采用追加撤分类记录，不改原分类；撤后可用同一流水重新分类。'),
        status=status)


def cash_classification_reverse(context, payload):
    member = _fresh(context.member, write=True)
    classification_id = context.request.GET.get('id')
    if not classification_id:
        raise PermissionDenied('现金分类不存在或无权访问')
    snapshot = cls.classification_snapshot(member, classification_id)
    if snapshot['status'] != 'active':
        raise PermissionDenied('仅有效现金分类可以撤分类')
    form = ReverseClassificationForm(initial={
        'classification_id': snapshot['id'], 'expected_version': snapshot['version']})
    return _reverse_classification_page(context, member, snapshot, form)


def cash_classification_reverse_save(context, payload):
    member = _fresh(context.member, write=True)
    form = ReverseClassificationForm(payload)
    snapshot = None
    if form.is_valid():
        data = form.cleaned_data
        snapshot = cls.classification_snapshot(member, data['classification_id'])
        try:
            cls.reverse_classification(
                member, classification_id=data['classification_id'], reason=data['reason'],
                evidence=_evidence_refs(data['evidence']),
                request_token=data['request_token'],
                expected_version=data['expected_version'], dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '现金分类已撤分类')
            return redirect(_url('cash_transaction_detail',
                                 id=snapshot['cash_transaction_id']))
    if snapshot is None:
        classification_id = payload.get('classification_id')
        if not classification_id:
            raise PermissionDenied('现金分类不存在或无权访问')
        snapshot = cls.classification_snapshot(member, classification_id)
    return _reverse_classification_page(context, member, snapshot, form, status=400)


# ---------- PH-245 internal transfer ----------
def _transfer_row_view(row):
    return {**row,
            'created_display': str(row.get('created_at', ''))[:16].replace('T', ' '),
            'out_url': _url('cash_transaction_detail', id=row['out_cash_transaction_id']),
            'in_url': _url('cash_transaction_detail', id=row['in_cash_transaction_id']),
            'reverse_url': _url('cash_internal_transfer_reverse', id=row['id']),
            'reverse_token': uuid.uuid4()}


def _transfer_choices(member, direction):
    rows = internal.internal_transfer_candidates(member, direction)
    return [(f"{row['transaction_ref']['id']}:{row['transaction_ref']['version']}",
             f"{row['transaction_date']} · {row['funding_account_name']} · "
             f"{row['gross_display']} 元 · 流水 {row['id'][:8]}")
            for row in rows]


def cash_internal_transfer_list(context, payload):
    member = _fresh(context.member)
    account_options = _account_options(member)
    filter_form = InternalTransferListFilterForm(payload or None, accounts=account_options)
    account_id = status = ''
    if filter_form.is_valid():
        account_id = filter_form.cleaned_data.get('funding_account') or ''
        status = filter_form.cleaned_data.get('status') or ''
    rows = internal.internal_transfer_list(member, funding_account_id=account_id or None,
                                           status=status or None)
    return render(context.request, 'cash/internal_transfer_list.html', _base(
        member, 'internal_transfers',
        transfer_rows=[_transfer_row_view(row) for row in rows],
        filter_form=filter_form,
        new_url=_url('cash_internal_transfer_form')))


def cash_internal_transfer_form(context, payload):
    member = _fresh(context.member, write=True)
    form = InternalTransferForm(out_candidates=_transfer_choices(member, 'out'),
                                in_candidates=_transfer_choices(member, 'in'))
    return render(context.request, 'cash/internal_transfer_form.html', _base(
        member, 'internal_transfers', form=form,
        submit_url=_url('cash_internal_transfer_save'),
        back_url=_url('cash_internal_transfer_list'),
        explanation='内部互转把同企业、反方向、同额、同日的两条已过账现金成对关联；'
                    '两条现金各写一条内部 transfer 分类与现金账 posting，公司净现金为 0，'
                    '不写成本/收入/应收/应付/未清。'))


def cash_internal_transfer_save(context, payload):
    member = _fresh(context.member, write=True)
    out_choices = _transfer_choices(member, 'out')
    in_choices = _transfer_choices(member, 'in')
    form = InternalTransferForm(payload, out_candidates=out_choices,
                                in_candidates=in_choices)
    if form.is_valid():
        data = form.cleaned_data
        out_id = data['out_cash'].split(':', 1)[0]
        in_id = data['in_cash'].split(':', 1)[0]
        try:
            internal.link_internal_transfer(
                member, out_cash_transaction_id=out_id,
                in_cash_transaction_id=in_id,
                evidence=_evidence_refs(data['evidence']),
                request_token=data['request_token'], dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '内部互转已登记')
            return redirect(_url('cash_internal_transfer_list'))
    return render(context.request, 'cash/internal_transfer_form.html', _base(
        member, 'internal_transfers', form=form,
        submit_url=_url('cash_internal_transfer_save'),
        back_url=_url('cash_internal_transfer_list'),
        explanation='内部互转把同企业、反方向、同额、同日的两条已过账现金成对关联；'
                    '两条现金各写一条内部 transfer 分类与现金账 posting，公司净现金为 0，'
                    '不写成本/收入/应收/应付/未清。'), status=400)


def cash_internal_transfer_reverse(context, payload):
    member = _fresh(context.member, write=True)
    transfer_id = context.request.GET.get('id')
    if not transfer_id:
        raise PermissionDenied('内部互转不存在或无权访问')
    snapshot = internal.internal_transfer_snapshot(member, transfer_id)
    if snapshot['status'] != 'active':
        raise PermissionDenied('仅有效内部互转可以撤转')
    form = ReverseInternalTransferForm(initial={
        'transfer_id': snapshot['id'], 'expected_version': snapshot['version']})
    return render(context.request, 'cash/internal_transfer_form.html', _base(
        member, 'internal_transfers', reverse=True, transfer=snapshot, form=form,
        submit_url=_url('cash_internal_transfer_reverse_save'),
        back_url=_url('cash_internal_transfer_list'),
        explanation='撤转会为两条现金各追加一条撤分类记录与冲销 posting，'
                    '再把内部互转标记为已撤转；原事实不改不删，公司净现金仍为 0。'))


def cash_internal_transfer_reverse_save(context, payload):
    member = _fresh(context.member, write=True)
    transfer_id = payload.get('transfer_id')
    if not transfer_id:
        raise PermissionDenied('内部互转不存在或无权访问')
    try:
        snapshot = internal.internal_transfer_snapshot(member, transfer_id)
    except (ValidationError, PermissionDenied):
        raise PermissionDenied('内部互转不存在或无权访问')
    form = ReverseInternalTransferForm(payload)
    if form.is_valid():
        data = form.cleaned_data
        try:
            internal.reverse_internal_transfer(
                member, data['transfer_id'], reason=data['reason'],
                evidence=_evidence_refs(data['evidence']),
                expected_version=data['expected_version'],
                request_token=data['request_token'], dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '内部互转已撤转')
            return redirect(_url('cash_internal_transfer_list'))
    return render(context.request, 'cash/internal_transfer_form.html', _base(
        member, 'internal_transfers', reverse=True, transfer=snapshot, form=form,
        submit_url=_url('cash_internal_transfer_reverse_save'),
        back_url=_url('cash_internal_transfer_list'),
        explanation='撤转会为两条现金各追加一条撤分类记录与冲销 posting，'
                    '再把内部互转标记为已撤转；原事实不改不删，公司净现金仍为 0。'), status=400)


def _bridge_filters(member, payload):
    as_of = (payload or {}).get('as_of') or None
    account_id = (payload or {}).get('funding_account') or None
    return as_of, account_id or None


def cash_bridge_snapshot(context, payload):
    member = _fresh(context.member)
    from . import reconciliation_services as recon
    as_of, account_id = _bridge_filters(member, payload)
    snapshot = recon.cash_bridge_snapshot(
        member, as_of=as_of, funding_account_id=account_id)
    return render(context.request, 'cash/bridge_snapshot.html', _base(
        member, 'bridge', snapshot=snapshot, issues=snapshot['issues'],
        mode='snapshot', account_options=_account_options(member),
        as_of=snapshot['as_of'], selected_account=account_id or '',
        issues_url=_url('cash_bridge_issues'),
        snapshot_url=_url('cash_bridge_snapshot')))


def cash_bridge_issues(context, payload):
    member = _fresh(context.member)
    from . import reconciliation_services as recon
    as_of, account_id = _bridge_filters(member, payload)
    result = recon.cash_bridge_issues(
        member, as_of=as_of, funding_account_id=account_id)
    return render(context.request, 'cash/bridge_snapshot.html', _base(
        member, 'bridge', snapshot=result, issues=result['issues'],
        mode='issues', account_options=_account_options(member),
        as_of=result['as_of'], selected_account=account_id or '',
        issues_url=_url('cash_bridge_issues'),
        snapshot_url=_url('cash_bridge_snapshot')))
