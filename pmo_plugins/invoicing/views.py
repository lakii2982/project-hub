"""PH-252 invoicing browser handlers; every write goes through the named services."""
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import get_script_prefix

from . import services
from .forms import AllocateForm, InvoiceForm, ReverseForm


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
