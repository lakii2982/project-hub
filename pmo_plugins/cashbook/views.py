"""Q-38 现金页浏览器入口：账户派生余额、收付款流水与只读 8 周计划。

页面本身零写：余额与计划全部由 Q-37 收款、Q-36 付款和 core 收款计划派生。
账户新建/维护写操作复用 billing 服务（租户/角色/审计/版本检查在那里）。
"""
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from core.access import current_membership
from pmo_plugins.billing import forms as billing_forms
from pmo_plugins.billing import services as billing_services

from . import forms as ui
from . import services


def _member(request):
    return services.cash_member(current_membership(request))


def _error_text(error):
    if isinstance(error, ValidationError):
        return "；".join(error.messages)
    return "记录冲突或已更新，请刷新并核对后重试"


def _yuan(cents):
    return format(Decimal(int(cents)) / Decimal(100), ".2f")


def _page(request, member, *, account_form=None, status=200):
    snapshot = services.cash_snapshot(member)
    for account in snapshot["accounts"]:
        account.opening_balance_yuan = _yuan(account.opening_balance_cents)
    return render(request, "cashbook/cash.html", {
        "nav": "cash",
        "snapshot": snapshot,
        "account_form": account_form or billing_forms.AccountForm(member=member),
        "can_manage_accounts": member.role == "owner",
    }, status=status)


@login_required
@require_GET
def cash(request):
    return _page(request, _member(request))


@login_required
@require_POST
def account_create(request):
    member = _member(request)
    form = billing_forms.AccountForm(request.POST, member=member)
    if not form.is_valid():
        return _page(request, member, account_form=form, status=400)
    try:
        billing_services.register_account(
            member, name=form.cleaned_data["name"], kind=form.cleaned_data["kind"],
            opening_balance_cents=billing_services.yuan_to_cents(
                form.cleaned_data.get("opening_balance") or 0, allow_negative=True),
            note=form.cleaned_data["note"])
        messages.success(request, "资金账户已建立；余额 = 期初 + 收款 − 付款（派生值）")
    except (ValidationError, IntegrityError) as error:
        form.add_error(None, _error_text(error))
        return _page(request, member, account_form=form, status=400)
    return redirect("cash")


@login_required
@require_POST
def account_update(request, pk):
    member = _member(request)
    form = ui.AccountUpdateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "没有保存账户维护：" + "；".join(
            message for errors in form.errors.values() for message in errors))
        return redirect("cash")
    try:
        billing_services.update_account(
            member, pk,
            opening_balance_cents=billing_services.yuan_to_cents(
                form.cleaned_data["opening_balance"], allow_negative=True),
            active=bool(form.cleaned_data["active"]),
            version=form.cleaned_data["version"])
        messages.success(request, "资金账户已更新；余额仍由收付款派生，历史流水不变")
    except ObjectDoesNotExist:
        messages.error(request, "资金账户不存在或无权访问")
    except (ValidationError, IntegrityError) as error:
        messages.error(request, _error_text(error))
    return redirect("cash")
