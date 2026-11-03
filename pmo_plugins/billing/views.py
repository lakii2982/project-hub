"""Q-37 开票与收款浏览器入口：登记发票、选择/新建资金账户、登记收款、作废与退款。

页面与导出只读同一服务层聚合；写操作统一走 ``services`` 的租户/项目/角色/期间/
来源检查、幂等与审计，不在这里重算金额。
"""
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from core.access import current_membership

from . import forms as ui
from . import services


def _member(request):
    member = current_membership(request)
    return services.billing_member(member)


def _error_text(error):
    if isinstance(error, ValidationError):
        return "；".join(error.messages)
    return "记录冲突或已更新，请刷新并核对后重试"


def _page(request, member, *, invoice_form=None, receipt_form=None,
          account_form=None, status=200):
    snapshot = services.billing_snapshot(member)
    for row in snapshot["invoice_rows"]:
        if row.reverses_id is None and not row.is_voided:
            row.void_token = uuid.uuid4()
    for row in snapshot["receipt_rows"]:
        if row.reverses_id is None and row.refundable_cents > 0:
            row.refund_token = uuid.uuid4()
    return render(request, "billing/billing.html", {
        "nav": "billing",
        "snapshot": snapshot,
        "invoice_form": invoice_form or ui.InvoiceForm(member=member),
        "receipt_form": receipt_form or ui.ReceiptForm(member=member),
        "account_form": account_form or ui.AccountForm(member=member),
        "can_manage_accounts": member.role == "owner",
    }, status=status)


@login_required
@require_GET
def billing(request):
    return _page(request, _member(request))


@login_required
@require_POST
def account_create(request):
    member = _member(request)
    form = ui.AccountForm(request.POST, member=member)
    if not form.is_valid():
        return _page(request, member, account_form=form, status=400)
    try:
        services.register_account(
            member, name=form.cleaned_data["name"], kind=form.cleaned_data["kind"],
            opening_balance_cents=services.yuan_to_cents(
                form.cleaned_data.get("opening_balance") or 0, allow_negative=True),
            note=form.cleaned_data["note"])
        messages.success(request, "资金账户已建立；余额由期初余额与收付款派生")
    except (ValidationError, IntegrityError) as error:
        form.add_error(None, _error_text(error))
        return _page(request, member, account_form=form, status=400)
    return redirect("billing")


@login_required
@require_POST
def invoice_create(request):
    member = _member(request)
    form = ui.InvoiceForm(request.POST, member=member)
    if not form.is_valid():
        return _page(request, member, invoice_form=form, status=400)
    try:
        services.register_invoice(
            member, project=form.cleaned_data["project"],
            number=form.cleaned_data["number"], amount_cents=ui.amount_cents(form),
            tax_rate_bp=form.cleaned_data["tax_rate"],
            issued_on=form.cleaned_data["issued_on"],
            contract_reference=form.cleaned_data["contract_reference"],
            note=form.cleaned_data["note"], token=form.token())
        messages.success(request, "发票已登记；未收款 = 已开票 − 已收款，自动计算")
    except (ValidationError, IntegrityError) as error:
        form.add_error(None, _error_text(error))
        return _page(request, member, invoice_form=form, status=400)
    return redirect("billing")


@login_required
@require_POST
def invoice_void(request, pk):
    member = _member(request)
    form = ui.VoidForm(request.POST)
    if not form.is_valid():
        messages.error(request, "；".join(
            message for errors in form.errors.values() for message in errors))
        return redirect("billing")
    try:
        services.void_invoice(member, pk, form.cleaned_data["reason"], form.token())
        messages.success(request, "已追加作废记录并保留原发票；未收款随之重算")
    except ObjectDoesNotExist:
        messages.error(request, "发票不存在或无权访问")
    except (ValidationError, IntegrityError) as error:
        messages.error(request, _error_text(error))
    return redirect("billing")


@login_required
@require_POST
def receipt_create(request):
    member = _member(request)
    form = ui.ReceiptForm(request.POST, member=member)
    if not form.is_valid():
        return _page(request, member, receipt_form=form, status=400)
    try:
        services.register_receipt(
            member, project=form.cleaned_data["project"],
            non_project=form.cleaned_data["non_project"],
            account=form.cleaned_data["account"],
            invoice=form.cleaned_data["invoice"],
            amount_cents=ui.amount_cents(form),
            received_on=form.cleaned_data["received_on"],
            counterparty=form.cleaned_data["counterparty"],
            reference=form.cleaned_data["reference"],
            note=form.cleaned_data["note"], token=form.token())
        messages.success(request, "收款已登记；同一来源编号只消费一次，不会重复入账")
    except (ValidationError, IntegrityError) as error:
        form.add_error(None, _error_text(error))
        return _page(request, member, receipt_form=form, status=400)
    return redirect("billing")


@login_required
@require_POST
def receipt_refund(request, pk):
    member = _member(request)
    form = ui.RefundForm(request.POST)
    if not form.is_valid():
        messages.error(request, "；".join(
            message for errors in form.errors.values() for message in errors))
        return redirect("billing")
    try:
        services.refund_receipt(member, pk, services.yuan_to_cents(form.cleaned_data["amount"]),
                                form.cleaned_data["reason"], form.token())
        messages.success(request, "已追加退款负值记录并保留原收款")
    except ObjectDoesNotExist:
        messages.error(request, "收款不存在或无权访问")
    except (ValidationError, IntegrityError) as error:
        messages.error(request, _error_text(error))
    return redirect("billing")
