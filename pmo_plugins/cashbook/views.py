"""Q-38 现金页浏览器入口：账户派生余额、收付款流水与只读 8 周计划。

页面本身零写：余额与计划全部由 Q-37 收款、Q-36 付款和 core 收款计划派生。
账户新建/维护写操作复用 billing 服务（租户/角色/审计/版本检查在那里）。
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import IntegrityError
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from core.access import current_membership
from pmo_plugins.billing import forms as billing_forms
from pmo_plugins.billing import services as billing_services
from pmo_plugins.cashflow_page import render_cashflow

from . import forms as ui
from . import services


def _member(request):
    return services.cash_member(current_membership(request))


def _error_text(error):
    if isinstance(error, ValidationError):
        return "；".join(error.messages)
    return "记录冲突或已更新，请刷新并核对后重试"


def _tab_redirect(tab):
    return redirect(f"{reverse('cashflow')}?tab={tab}")


def _page(request, member, *, account_form=None, status=200, default_tab="accounts"):
    """Cash entry renders the merged cash-flow page on the accounts tab."""
    return render_cashflow(request, member, default_tab=default_tab,
                           account_form=account_form, status=status)


@login_required
@require_GET
def cashflow(request):
    """PH-026 阶段④ 模块入口：现金流（概览默认）。"""
    return _page(request, _member(request), default_tab="overview")


@login_required
@require_GET
def cash(request):
    return _page(request, _member(request), default_tab="accounts")


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
    return _tab_redirect("accounts")


@login_required
@require_POST
def account_update(request, pk):
    member = _member(request)
    form = ui.AccountUpdateForm(request.POST)
    if not form.is_valid():
        messages.error(request, "没有保存账户维护：" + "；".join(
            message for errors in form.errors.values() for message in errors))
        return _tab_redirect("accounts")
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
    return _tab_redirect("accounts")
