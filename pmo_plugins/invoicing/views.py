"""PH-252 invoicing browser handlers; every write goes through the named services."""
from urllib.parse import urlencode
import uuid

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import get_script_prefix

from . import services
from .forms import AllocateForm, InvoiceForm, ReverseForm, TaxConfirmForm, TaxLinkForm


def _url(page, **query):
    return get_script_prefix() + 'modules/invoicing/' + page.replace('_', '-') + '/' + (
        '?' + urlencode({key: value for key, value in query.items() if value is not None})
        if query else '')


def _base(member, **values):
    return {'nav': 'invoicing', 'can_write': member.role in services.WRITE_ROLES,
            'list_url': _url('invoice_list'), 'form_url': _url('invoice_form'), **values}


def invoice_list(context, payload):
    member = services._fresh(context.member)
    rows = services.invoice_list(member)
    return render(context.request, 'invoicing/invoice_list.html', _base(
        member, rows=rows, detail_url=_url('invoice_detail'), mode='list'))


def invoice_detail(context, payload):
    member = services._fresh(context.member)
    invoice_id = payload.get('id') or context.request.GET.get('id')
    snapshot = services.invoice_snapshot(member, invoice_id)
    return render(context.request, 'invoicing/invoice_list.html', _base(
        member, invoice=snapshot, detail_url=_url('invoice_detail'), mode='detail',
        allocate_url=_url('invoice_allocate'), reverse_url=_url('invoice_reverse')))


def invoice_form(context, payload):
    member = services._fresh(context.member, write=True)
    return render(context.request, 'invoicing/invoice_form.html', _base(
        member, form=InvoiceForm(member=member), submit_url=_url('invoice_record')))


def invoice_record(context, payload):
    member = services._fresh(context.member, write=True)
    form = InvoiceForm(payload, member=member)
    if form.is_valid():
        data = form.cleaned_data
        try:
            result = services.record_invoice(
                member, direction=data['direction'],
                counterparty_id=data['counterparty'].pk,
                invoice_number=data['invoice_number'], invoice_date=data['invoice_date'],
                gross_cents=data['gross_cents'], net_cents=data['net_cents'],
                tax_cents=data['tax_cents'], evidence=data['evidence'],
                request_token=payload.get('request_token'), dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '发票已登记')
            return redirect(_url('invoice_detail', id=result['invoice_ref']['id']))
    return render(context.request, 'invoicing/invoice_form.html', _base(
        member, form=form, submit_url=_url('invoice_record')), status=400)


def invoice_allocate(context, payload):
    member = services._fresh(context.member, write=True)
    form = AllocateForm(payload)
    if form.is_valid():
        data = form.cleaned_data
        try:
            services.allocate_invoice_lines(
                member, invoice_id=data['invoice_id'], lines=data['lines'],
                expected_version=data['expected_version'],
                request_token=payload.get('request_token'), dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '发票行与分配已登记')
            return redirect(_url('invoice_detail', id=data['invoice_id']))
    return render(context.request, 'invoicing/invoice_form.html', _base(
        member, form=form, submit_url=_url('invoice_allocate'),
        title='发票行分配'), status=400)


def invoice_reverse(context, payload):
    member = services._fresh(context.member, write=True)
    form = ReverseForm(payload)
    if form.is_valid():
        data = form.cleaned_data
        try:
            services.reverse_invoice(
                member, invoice_id=data['invoice_id'], reason=data['reason'],
                reversal_date=data['reversal_date'],
                line_reversals=data.get('line_reversals'),
                allocation_reversals=data.get('allocation_reversals'),
                expected_version=data['expected_version'],
                request_token=payload.get('request_token'), dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '发票已红冲')
            return redirect(_url('invoice_detail', id=data['invoice_id']))
    return render(context.request, 'invoicing/invoice_form.html', _base(
        member, form=form, submit_url=_url('invoice_reverse'), title='发票红冲'),
        status=400)


def invoice_reconciliation(context, payload):
    """PH-325 read-only invoice/settlement/revenue/terms pairing page."""
    from . import invoice_reconciliation_services as reconciliation
    from .forms import InvoiceReconciliationFilterForm
    from django.core.exceptions import PermissionDenied
    member = reconciliation._reader(context.member)
    allowed = {'project', 'as_of', 'invoice'}
    form = InvoiceReconciliationFilterForm(payload or None, member=member)
    error = ''
    snapshot = None
    if set(payload) - allowed:
        error = '包含不允许的查询参数，请刷新后重试'
    else:
        try:
            snapshot = reconciliation.invoice_pairing(
                member, invoice_id=payload.get('invoice') or None,
                project_id=(payload.get('project') or None),
                as_of=payload.get('as_of') or None)
        except (PermissionDenied, ValueError) as exc:
            error = '；'.join(getattr(exc, 'messages', [str(exc)]))
    return render(context.request, 'invoicing/invoice_reconciliation.html',
                  {'nav': 'invoicing', 'can_write': False, 'snapshot': snapshot,
                   'issues': snapshot['issues'] if snapshot else [],
                   'missing': snapshot['missing'] if snapshot else [],
                   'filter_form': form, 'error': error,
                   'reconciliation_url': _url('invoice_reconciliation')},
                  status=400 if error else 200)


def _invoice_tax_page(context, member, *, invoice_id=None, form=None, link_form=None,
                      error='', status=200):
    snapshot = services.invoice_tax_snapshot(member, invoice_id) if invoice_id else None
    rows = services.invoice_tax_list(member) if invoice_id is None else []
    values = _base(
        member, rows=rows, snapshot=snapshot, error=error,
        confirm_form=form or (TaxConfirmForm() if member.role in services.WRITE_ROLES else None),
        link_form=link_form or (TaxLinkForm() if member.role in services.WRITE_ROLES else None),
        detail_url=_url('invoice_tax'), confirm_url=_url('invoice_tax_confirm'),
        link_url=_url('invoice_tax_link'))
    return render(context.request, 'invoicing/invoice_tax.html', values, status=status)


def invoice_tax(context, payload):
    member = services._fresh(context.member)
    invoice_id = payload.get('id') or context.request.GET.get('id')
    return _invoice_tax_page(context, member, invoice_id=invoice_id)


def invoice_tax_confirm(context, payload):
    member = services._fresh(context.member, write=True)
    form = TaxConfirmForm(payload)
    invoice_id = payload.get('invoice_id') or context.request.GET.get('invoice_id')
    if form.is_valid():
        try:
            services.confirm_tax_qualification(
                member, form.cleaned_data['qualification_id'],
                qualification_status=form.cleaned_data['qualification_status'],
                decided_on=form.cleaned_data['decided_on'],
                evidence=form.cleaned_data['evidence'],
                request_token=(form.cleaned_data.get('request_token')
                               or payload.get('request_token') or uuid.uuid4()),
                expected_version=form.cleaned_data['expected_version'], dry_run=False)
        except ValidationError as exc:
            return _invoice_tax_page(context, member, invoice_id=invoice_id, form=form,
                                     error='；'.join(exc.messages), status=400)
        messages.success(context.request, '税额资格已确认')
        return redirect(_url('invoice_tax', id=invoice_id))
    return _invoice_tax_page(context, member, invoice_id=invoice_id, form=form,
                             error='请核对确认字段', status=400)


def invoice_tax_link(context, payload):
    member = services._fresh(context.member, write=True)
    form = TaxLinkForm(payload)
    invoice_id = payload.get('invoice_id')
    if form.is_valid():
        try:
            services.link_invoice_orchestration(
                member, invoice_id=form.cleaned_data['invoice_id'],
                link_kind=form.cleaned_data['link_kind'],
                owner_ref=form.cleaned_data['owner_ref'],
                evidence=form.cleaned_data['evidence'],
                request_token=(form.cleaned_data.get('request_token')
                               or payload.get('request_token') or uuid.uuid4()),
                expected_version=form.cleaned_data['expected_version'],
                reversal_id=form.cleaned_data.get('reversal_id'), dry_run=False)
        except ValidationError as exc:
            return _invoice_tax_page(context, member, invoice_id=invoice_id, link_form=form,
                                     error='；'.join(exc.messages), status=400)
        messages.success(context.request, '跨 owner 编排引用已登记')
        return redirect(_url('invoice_tax', id=form.cleaned_data['invoice_id']))
    return _invoice_tax_page(context, member, invoice_id=invoice_id, link_form=form,
                             error='请核对编排字段', status=400)
