"""PH-026 阶段④：现金流合并页（Q-49：发票、收款、资金账户与 8 周计划）。

服务端按 ``?tab=`` 渲染唯一可见面板（其余面板带 ``hidden``），无 JavaScript 可用；
写操作仍走 Q-37/Q-38 各自的 POST 入口，成功后回到对应页签。页面数据全部来自
``billing_snapshot`` / ``cash_snapshot`` 只读聚合，不在这里重算金额或权限。

``/cashflow/`` 是合并后的模块入口；``/billing/`` 与 ``/cash/`` 保留为同一页面的
兼容入口（默认页签分别为发票与资金账户），既有书签与测试不失效。
"""
import uuid
from decimal import Decimal

from django.http import Http404
from django.shortcuts import get_object_or_404, render

from .billing import forms as billing_forms
from .billing import services as billing_services
from .cashbook import services as cash_services

CASHFLOW_TABS = ("overview", "invoices", "receipts", "accounts", "plan")


def selected_project(request, member):
    """Optional project drill-down filter, scoped to the caller's billing projects."""
    value = request.GET.get("project", "")
    if not value:
        return None
    if not value.isdigit() or len(value) > 18:
        raise Http404()
    return get_object_or_404(billing_services.billing_projects(member), pk=int(value))


def _yuan(cents):
    return format(Decimal(int(cents)) / Decimal(100), ".2f")


def _billing_snapshot(member, project):
    snapshot = billing_services.billing_snapshot(member)
    if project is not None:
        # Project drill-down from Q-34: aggregate KPIs stay company-wide, detail lists filter.
        snapshot["project_rows"] = [row for row in snapshot["project_rows"]
                                    if row["project"].pk == project.pk]
        snapshot["invoice_rows"] = [row for row in snapshot["invoice_rows"]
                                    if row.project_id == project.pk]
        snapshot["receipt_rows"] = [row for row in snapshot["receipt_rows"]
                                    if row.project_id == project.pk]
    for row in snapshot["invoice_rows"]:
        if row.reverses_id is None and not row.is_voided:
            row.void_token = uuid.uuid4()
    for row in snapshot["receipt_rows"]:
        if row.reverses_id is None and row.refundable_cents > 0:
            row.refund_token = uuid.uuid4()
    return snapshot


def render_cashflow(request, member, *, default_tab="overview", invoice_form=None,
                    receipt_form=None, account_form=None, status=200):
    member = billing_services.billing_member(member)
    cash_services.cash_member(member)
    project = selected_project(request, member)
    billing = _billing_snapshot(member, project)
    cash = cash_services.cash_snapshot(member)
    for account in cash["accounts"]:
        account.opening_balance_yuan = _yuan(account.opening_balance_cents)
    active_tab = request.GET.get("tab")
    if active_tab not in CASHFLOW_TABS:
        active_tab = default_tab
    return render(request, "cashflow.html", {
        "nav": "cashflow",
        "active_tab": active_tab,
        "snapshot": billing,
        "cash": cash,
        "invoice_form": invoice_form or billing_forms.InvoiceForm(member=member),
        "receipt_form": receipt_form or billing_forms.ReceiptForm(member=member),
        "account_form": account_form or billing_forms.AccountForm(member=member),
        "can_manage_accounts": member.role == "owner",
        "selected_project": project,
    }, status=status)
