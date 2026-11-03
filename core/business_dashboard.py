"""Q-33 老板工作台只读聚合：本月开票/收款/支出、现金余额、未收款与项目毛利排行。

口径（docs/product-scope.md §4）：
* 收入默认按开票确认（不含税）；可切换按收款确认；未开票合同额不计收入。
* 成本复用 core 项目账费用 + Q-35 简化人工成本（``project_summary``）。
* 毛利 = 收入 − 成本；收入为 0 时毛利率无定义，不显示假 0。
* 未收款 = 已开票 − 已收款（仅项目收款，不含显式非项目收款）。
* 资金账户余额 = 期初 + 已收款 − 退款（Q-37 派生）；已确认支出的付款记录暂未
  关联资金账户（Q-38 接入前），单列为缺口而不是假装已从账户扣除。
* 所有函数只读：不写账本/审计，不修改业务事实。
"""
from calendar import monthrange
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.utils import timezone

from .access import require_member, visible_projects
from .models import Document, LedgerEntry
from .reporting import project_summary
from .weekly_time import simplified_labor_cost

DASHBOARD_ROLES = frozenset({"owner", "pm"})
BASIS_INVOICE = "invoice"
BASIS_RECEIPT = "receipt"
BASIS_CHOICES = ((BASIS_INVOICE, "按开票确认收入"), (BASIS_RECEIPT, "按收款确认收入"))


def resolve_basis(value):
    """Unknown/missing basis selects the product default (invoice), never an error page."""
    return value if value in {BASIS_INVOICE, BASIS_RECEIPT} else BASIS_INVOICE


def _month_start(today):
    return today.replace(day=1)


def _month_bounds(today):
    start = _month_start(today)
    last = monthrange(start.year, start.month)[1]
    return start, start.replace(day=last) + timedelta(days=1)


def _sum(queryset, field="amount_cents"):
    return int(queryset.aggregate(total=Sum(field))["total"] or 0)


def _project_payments(payments, project):
    return _sum(payments.filter(expense__project=project))


def _simplified_cost(project):
    """1.0 cost = Q-35 simplified labor + Q-36 confirmed direct expenses only."""
    q36 = (LedgerEntry.objects.filter(project=project, book=LedgerEntry.Book.COST,
                                      document__expense_detail__isnull=False)
           .aggregate(total=Sum("amount_cents"))["total"] or 0)
    return int(simplified_labor_cost(project)) + int(q36)


def _taxed_invoice_totals(queryset):
    """Gross and net (tax-excluded) invoiced amounts; void reversals are negative rows."""
    totals = queryset.aggregate(gross=Sum("amount_cents"), tax=Sum("tax_cents"))
    gross = int(totals["gross"] or 0)
    return gross, gross - int(totals["tax"] or 0)


def _margin_bp(revenue, gross):
    if revenue <= 0:
        return None
    return int((Decimal(gross) * 10000 / Decimal(revenue)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP))


def dashboard_snapshot(member, basis=None, today=None):
    """Read-only Q-33 dashboard facts for the current member's visible projects."""
    require_member(member)
    if member.role not in DASHBOARD_ROLES:
        raise PermissionDenied("项目收益与现金工作台仅向公司负责人和项目经理开放")
    basis = resolve_basis(basis)
    today = today or timezone.localdate()
    month_start, month_end = _month_bounds(today)

    from pmo_plugins.billing.models import Account, Invoice, Receipt
    from pmo_plugins.billing.services import account_balance_cents, project_totals
    from pmo_plugins.expenses.models import ExpensePayment
    from pmo_plugins.expenses.services import dashboard_expense_facts, legacy_expense_count

    projects = list(visible_projects(member).select_related("manager"))
    if member.role == "pm":
        projects = [project for project in projects if project.manager_id == member.pk]
    project_ids = [project.pk for project in projects]
    is_owner = member.role == "owner"

    invoice_scope = Invoice.objects.filter(organization=member.organization,
                                           project_id__in=project_ids)
    receipt_scope = Receipt.objects.filter(organization=member.organization,
                                           project_id__in=project_ids)
    payment_scope = ExpensePayment.objects.filter(
        organization=member.organization, expense__project_id__in=project_ids)
    # 老板的“本月收款/支出”是公司现金口径，包含显式非项目收款；项目/未收款口径
    # 始终只含项目收款，避免用非项目收款冲抵项目未收款。
    cash_receipts = (Receipt.objects.filter(organization=member.organization)
                     if is_owner else receipt_scope)
    cash_payments = (ExpensePayment.objects.filter(organization=member.organization)
                     if is_owner else payment_scope)
    non_project_receipts = (Receipt.objects.filter(organization=member.organization,
                                                  project__isnull=True)
                            if is_owner else Receipt.objects.none())

    month_filters = {"issued_on__gte": month_start, "issued_on__lt": month_end}
    month_invoiced_gross, month_invoiced = _taxed_invoice_totals(
        invoice_scope.filter(**month_filters))
    month_received = _sum(cash_receipts.filter(received_on__gte=month_start,
                                               received_on__lt=month_end))
    month_spent = _sum(cash_payments.filter(paid_on__gte=month_start,
                                            paid_on__lt=month_end))

    invoiced_gross_total, invoiced_net_total = _taxed_invoice_totals(invoice_scope)
    received_total = _sum(receipt_scope)
    # 未收款是现金口径（价税合计），不能用不含税收入冲抵；收入口径只影响毛利。
    outstanding_total = invoiced_gross_total - received_total

    # /billing/ shows the same company account balances to owner and PM, so Q-33 aligns.
    accounts = list(Account.objects.filter(organization=member.organization))
    account_total = sum(account_balance_cents(account) for account in accounts)
    payment_unlinked_count = cash_payments.count()
    payment_unlinked_amount = _sum(cash_payments)

    expense_facts = dashboard_expense_facts(member) or {}
    legacy_expenses = legacy_expense_count(member)
    legacy_receipts = Document.objects.filter(
        project_id__in=project_ids, kind=Document.Kind.RECEIPT,
        status__in=[Document.Status.SUBMITTED, Document.Status.APPROVED]).count()
    # 历史专业付款只统计旧流程单据；Q-36 简化付款有 expense_payment_link，不能冒充历史。
    legacy_payments = Document.objects.filter(
        project_id__in=project_ids, kind=Document.Kind.PAYMENT,
        status__in=[Document.Status.SUBMITTED, Document.Status.APPROVED],
        expense_payment_link__isnull=True).count()

    rows = []
    historical_cost_total = 0
    historical_cost_projects = 0
    for project in projects:
        totals = project_totals(project)
        summary = project_summary(project)
        received = totals["received_cents"]
        paid = _project_payments(payment_scope, project)
        invoiced_gross, invoiced_net = _taxed_invoice_totals(
            Invoice.objects.filter(project=project))
        # §4.1：按开票确认收入时项目收入 = 已开票金额（不含税）；按收款时用实际现金收款。
        revenue = invoiced_net if basis == BASIS_INVOICE else received
        simplified_cost = _simplified_cost(project)
        historical_cost = int(summary["cost_cents"]) - simplified_cost
        if historical_cost > 0:
            historical_cost_total += historical_cost
            historical_cost_projects += 1
        else:
            historical_cost = 0
        gross = revenue - simplified_cost
        rows.append({
            "project": project,
            "revenue_cents": revenue,
            "invoiced_gross_cents": invoiced_gross,
            "invoiced_net_cents": invoiced_net,
            "received_cents": received,
            "outstanding_cents": invoiced_gross - received,
            "cash_net_cents": received - paid,
            "cost_cents": simplified_cost,
            "historical_cost_cents": historical_cost,
            "gross_cents": gross,
            "margin_bp": _margin_bp(revenue, gross),
            "margin_undefined": revenue <= 0,
            "labor_gap_count": int(summary["labor_gap_count"]),
            "uninvoiced": invoiced_net == 0,
            "no_receipt": received == 0,
        })
    ranking = sorted(rows, key=lambda row: (-row["gross_cents"], row["project"].code))
    gaps = {
        "uninvoiced_count": sum(1 for row in rows if row["uninvoiced"]),
        "no_receipt_count": sum(1 for row in rows if row["no_receipt"]),
        "labor_gap_count": sum(row["labor_gap_count"] for row in rows),
        "payment_unlinked_count": payment_unlinked_count,
        "payment_unlinked_cents": payment_unlinked_amount,
        "legacy_expense_count": legacy_expenses,
        "legacy_receipt_count": legacy_receipts,
        "legacy_payment_count": legacy_payments,
        "historical_cost_cents": historical_cost_total,
        "historical_cost_projects": historical_cost_projects,
        "non_project_receipt_count": non_project_receipts.count(),
        "non_project_receipt_cents": _sum(non_project_receipts),
        "pending_expense_count": int(expense_facts.get("pending_count") or 0),
        "pending_expense_cents": int(expense_facts.get("pending_amount_cents") or 0),
    }
    return {
        "basis": basis,
        "basis_label": dict(BASIS_CHOICES)[basis],
        "month_label": f"{today.year}年{today.month}月",
        "month": {"invoiced_cents": month_invoiced,
                  "invoiced_gross_cents": month_invoiced_gross,
                  "received_cents": month_received, "spent_cents": month_spent},
        "outstanding": {"invoiced_cents": invoiced_gross_total, "received_cents": received_total,
                        "outstanding_cents": outstanding_total},
        "cash": {"account_total_cents": account_total, "account_count": len(accounts),
                 "payment_unlinked_count": payment_unlinked_count,
                 "payment_unlinked_cents": payment_unlinked_amount},
        "rows": rows,
        "ranking": ranking,
        "gaps": gaps,
    }
