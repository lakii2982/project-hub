"""PH-244 bank-fee draft browser workflow; writes go through the costing owner service."""
from decimal import Decimal
import uuid
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import get_script_prefix

from core.plugin_runtime import is_enabled
from pmo_plugins.cash.transaction_services import cash_transaction_snapshot

from . import bank_charge_pairing_services as pairing_services
from . import bank_charge_services as services
from .bank_charge_forms import (BankChargeDecisionForm, BankChargeForm, BankChargePairingFilterForm, BankChargeSubmitForm)

WRITE_ROLES = {'owner', 'finance'}


def url(page, **query):
    return get_script_prefix() + 'modules/costing/' + page.replace('_', '-') + '/' + (
        '?' + urlencode({key: value for key, value in query.items() if value is not None})
        if query else '')


def money(cents):
    return format(Decimal(cents) / Decimal(100), '.2f')


def base(member, **values):
    return dict(nav='costing', can_write=member.role in WRITE_ROLES,
                list_url=url('bank_charge_list'), new_url=url('bank_charge_form'), **values)


def _require_writer(member):
    if member.role not in WRITE_ROLES:
        raise PermissionDenied('仅公司负责人或财务可以登记银行手续费草稿')


def bank_charge_list(context, payload):
    member = services.bank_charge_reader(context.member)
    rows, selected, reconciliation, approval, error = [], None, None, None, ''
    try:
        rows = services.list_bank_charges(member)
        for row in rows:
            row['amount_display'] = money(row['gross_cents'])
            row['detail_url'] = url('bank_charge_list', id=row['id'])
        charge_id = payload.get('id')
        if charge_id:
            selected = services.get_bank_charge(member, charge_id)
            selected['amount_display'] = money(selected['gross_cents'])
            selected['tax_display'] = money(selected['tax_cents']) if selected['tax_cents'] is not None else ''
            selected['edit_url'] = url('bank_charge_form', id=selected['id'])
            reconciliation = services.net_fee_reconciliation(member, selected['cash_transaction_id'])
            for key in ('posted_net_cents', 'approved_fee_cents', 'implied_gross_cents', 'draft_fee_cents'):
                reconciliation[key + '_display'] = money(reconciliation[key])
            approval = services.bank_charge_cost_snapshot(member, selected['id'])
            approval['amount_display'] = money(approval['cost_amount_cents']) if approval['cost_amount_cents'] is not None else ''
            can_decide = (member.role in WRITE_ROLES
                          and str(member.pk) not in {str(selected['created_by']),
                                                     str(approval['submitted_by'])})
            approval.update(can_decide=can_decide,
                            submit_url=url('bank_charge_action', id=selected['id'], action='submit'),
                            approve_url=url('bank_charge_action', id=selected['id'], action='approve'),
                            reject_url=url('bank_charge_action', id=selected['id'], action='reject'),
                            submit_token=uuid.uuid4(), decide_token=uuid.uuid4())
    except ValidationError as exc:
        rows, selected, reconciliation, approval = [], None, None, None
        error = '；'.join(exc.messages)
    return render(context.request, 'costing/bank_charge_list.html', base(
        member, rows=rows, selected=selected, reconciliation=reconciliation,
        approval=approval, error=error,
        enabled=is_enabled(member.organization, 'costing')), status=400 if error else 200)


def bank_charge_form(context, payload):
    member = services.bank_charge_reader(context.member)
    _require_writer(member)
    charge_id = payload.get('id')
    charge = services.get_bank_charge(member, charge_id) if charge_id else None
    initial = {}
    if charge:
        initial = {
            'charge_id': charge['id'],
            'cash_transaction_id': charge['cash_transaction_id'],
            'gross': Decimal(charge['gross_cents']) / Decimal(100),
            'tax': Decimal(charge['tax_cents']) / Decimal(100) if charge['tax_cents'] is not None else None,
            'counterparty': charge['counterparty_id'],
            'project': charge['scope_id'],
            'occurred_on': charge['occurred_on'],
            'posting_period': charge['posting_period'],
            'expected_version': charge['version'],
            'evidence': '；'.join(item.get('evidence_id', '') for item in charge['evidence']),
        }
    form = BankChargeForm(initial=initial, member=member)
    return render(context.request, 'costing/bank_charge_form.html', base(
        member, form=form, charge=charge, title='修改手续费草稿' if charge else '登记银行手续费草稿',
        notice='本片只登记未批准手续费草稿；不提交、不批准、不产生成本/账本/会计事件/未清。',
        submit_url=url('bank_charge_save'),
        back_url=url('bank_charge_list', id=charge['id']) if charge else url('bank_charge_list')), status=200)


def bank_charge_save(context, payload):
    member = services.bank_charge_reader(context.member)
    _require_writer(member)
    form = BankChargeForm(payload, member=member)
    charge = None
    if form.is_valid():
        data = form.cleaned_data
        try:
            snapshot = cash_transaction_snapshot(member, data['cash_transaction_id'])
            cash_ref = {'namespace': 'cash', 'kind': 'cash_transaction',
                        'id': str(data['cash_transaction_id']), 'version': snapshot['version']}
            charge = services.save_bank_charge_draft(
                member, cash_transaction_ref=cash_ref,
                gross_cents=BankChargeForm.amount_cents(data['gross']),
                tax_cents=BankChargeForm.amount_cents(data['tax']) if data.get('tax') is not None else None,
                counterparty_id=data.get('counterparty').pk if data.get('counterparty') else None,
                scope_kind='project', scope_id=data['project'].pk, evidence=data['evidence'],
                occurred_on=data.get('occurred_on'), posting_period=data.get('posting_period'),
                request_token=data['request_token'], expected_version=data.get('expected_version'),
                dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '手续费草稿已保存；仍未批准、未过账成本、未核销未清。')
            return redirect(url('bank_charge_list', id=charge.pk))
    existing_id = payload.get('charge_id')
    existing = services.get_bank_charge(member, existing_id) if existing_id else None
    return render(context.request, 'costing/bank_charge_form.html', base(
        member, form=form, charge=existing, title='修改手续费草稿' if existing else '登记银行手续费草稿',
        notice='本片只登记未批准手续费草稿；不提交、不批准、不产生成本/账本/会计事件/未清。',
        submit_url=url('bank_charge_save'),
        back_url=url('bank_charge_list', id=existing['id']) if existing else url('bank_charge_list')),
        status=400)


def bank_charge_action(context, payload):
    """Browser submission of a draft, or an independent approve/reject of a pending charge."""
    member = services.bank_charge_reader(context.member)
    _require_writer(member)
    action = context.request.GET.get('action', '')
    charge_id = context.request.GET.get('id') or payload.get('charge_id')
    if action not in {'submit', 'approve', 'reject'}:
        raise PermissionDenied('未知手续费办理动作')
    charge = services.get_bank_charge(member, charge_id)
    from core.services import decide_document
    try:
        if action == 'submit':
            form = BankChargeSubmitForm(payload)
            if not form.is_valid():
                raise ValidationError('；'.join(sum(form.errors.values(), [])))
            decide_document(member, source_ref=charge['source_ref'],
                            expected_version=form.cleaned_data['expected_version'],
                            decision='submit',
                            request_token=form.cleaned_data['request_token'], dry_run=False)
            messages.success(context.request, '手续费已提交独立审批；尚未批准、未过账成本、未核销未清。')
        else:
            form = BankChargeDecisionForm(payload)
            if not form.is_valid():
                raise ValidationError('；'.join(sum(form.errors.values(), [])))
            decide_document(member, source_ref=charge['source_ref'],
                            expected_version=form.cleaned_data['expected_version'],
                            decision=form.cleaned_data['decision'],
                            note=form.cleaned_data.get('note') or '',
                            request_token=form.cleaned_data['request_token'], dry_run=False)
            messages.success(context.request, '手续费审批已办理；批准时一次性公司成本已过账。')
    except ValidationError as exc:
        messages.error(context.request, '；'.join(exc.messages))
        return redirect(url('bank_charge_list', id=charge_id))
    return redirect(url('bank_charge_list', id=charge_id))


def bank_charge_pairing(context, payload):
    """Read-only fee approval/cost/net pairing page; no write route or side effect."""
    member = pairing_services._reader(context.member)
    allowed = {'status', 'as_of', 'cash_transaction_id'}
    form = BankChargePairingFilterForm(payload or None)
    error = ''
    if set(payload) - allowed:
        error = '包含不允许的查询参数，请刷新后重试'
        rows, issues = [], []
    else:
        try:
            rows = pairing_services.bank_charge_pairing(
                member, status=payload.get('status') or None,
                cash_transaction_id=payload.get('cash_transaction_id') or None,
                as_of=payload.get('as_of') or None)
            all_issues = pairing_services.bank_charge_pairing_issues(
                member, as_of=payload.get('as_of') or None)
            visible = {row['charge_ref']['id'] for row in rows}
            issues = (all_issues if (payload.get('status') or payload.get('cash_transaction_id')
                                     or payload.get('as_of'))
                      else all_issues)
            issues = [issue for issue in issues
                      if not visible or issue['charge_ref']['id'] in visible]
        except (PermissionDenied, ValidationError) as exc:
            rows, issues = [], []
            error = '；'.join(getattr(exc, 'messages', [str(exc)]))
    for row in rows:
        row['fee_display'] = money(row['fee_gross_cents'])
        row['net_display'] = (money(row['posted_net_cents'])
                              if row['posted_net_cents'] is not None else '缺失')
        row['cost_display'] = (money(row['cost']['cost_amount_cents'])
                               if row['cost']['cost_amount_cents'] is not None else '缺失')
        row['refund_display'] = (money(row['refund_return']['total_cents'])
                                 if row['refund_return']['total_cents'] is not None else '缺失')
    return render(context.request, 'costing/bank_charge_pairing.html', base(
        member, rows=rows, issues=issues, filter_form=form, error=error,
        pairing_url=url('bank_charge_pairing'),
        enabled=is_enabled(member.organization, 'costing')), status=400 if error else 200)
