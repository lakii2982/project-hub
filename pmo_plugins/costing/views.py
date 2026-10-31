"""PH-371 tax qualification -> costing cost adjustment pages."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.urls import get_script_prefix

from . import tax_adjustment_services as services
from .forms import TaxAdjustmentForm, TaxReversalForm


def _url(page, **query):
    from urllib.parse import urlencode
    suffix = '?' + urlencode({k: v for k, v in query.items() if v is not None}) if query else ''
    return get_script_prefix() + 'modules/costing/' + page.replace('_', '-') + '/' + suffix


def _base(member, **values):
    return {'nav': 'costing', 'can_write': member.role in services.WRITE_ROLES,
            'list_url': _url('tax_adjustment_page'),
            'execute_url': _url('tax_adjustment_execute'),
            'reverse_url': _url('tax_adjustment_reverse'), **values}


def tax_adjustment_page(context, payload):
    member = services._fresh(context.member)
    rows = services.tax_adjustment_list(member)
    form = TaxAdjustmentForm(payload or None)
    return render(context.request, 'costing/tax_adjustment.html', _base(
        member, rows=rows, form=form, mode='list'))


def tax_adjustment_execute(context, payload):
    member = services._fresh(context.member, write=True)
    form = TaxAdjustmentForm(payload)
    if form.is_valid():
        data = form.cleaned_data
        try:
            services.adjust_tax_cost(member, data['qualification'],
                                     request_token=data['request_token'], dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '税额成本调整已登记')
            return redirect(_url('tax_adjustment_page'))
    return render(context.request, 'costing/tax_adjustment.html', _base(
        member, rows=services.tax_adjustment_list(member), form=form, mode='form'),
        status=400)


def tax_adjustment_reverse(context, payload):
    member = services._fresh(context.member, write=True)
    form = TaxReversalForm(payload)
    if form.is_valid():
        data = form.cleaned_data
        try:
            services.reverse_tax_cost_adjustment(
                member, data['batch'], reason=data['reason'],
                request_token=data['request_token'], dry_run=False)
        except ValidationError as exc:
            form.add_error(None, '；'.join(exc.messages))
        else:
            messages.success(context.request, '税额成本调整已冲销')
            return redirect(_url('tax_adjustment_page'))
    return render(context.request, 'costing/tax_adjustment.html', _base(
        member, rows=services.tax_adjustment_list(member), form=form, mode='reverse'),
        status=400)
