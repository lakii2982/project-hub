"""Q-34 read-only project 收益/现金 aggregation (1.0 micro basis).

The numbers here are the shared basis Q-33 should reuse for the boss dashboard:

* contract amount is a commercial commitment, **not revenue**; the latest appended
  ``ProjectContractChange`` wins and the initiation estimate remains historical;
* revenue defaults to **invoiced net of tax** (product-scope §4.1); a project-level
  ``revenue_basis`` field, when present, may switch it to receipt basis;
* gross margin = revenue − (labor cost + direct confirmed expense cost);
* project cash net = 1.0 receipts − 1.0 expense payments; it is not the margin and not
  the company bank balance.  Historical professional cash stays in the legacy
  ``project_summary`` cash book and is surfaced separately, never mixed into this basis;
* gaps (missing labor rate, unconfirmed expense, uninvoiced contract) are returned with
  counts/amounts and never rendered as a fake zero.

This module performs reads only; it never writes a business row or audit event.
"""
from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum

from .contract_changes import contract_amount_cents, contract_history
from .models import Document, LedgerEntry, Timesheet

INVOICE_BASIS = "invoice"
RECEIPT_BASIS = "receipt"
BASIS_NOTE = ("口径：合同额不是收入；收入按项目口径确认（默认开票不含税）；"
              "毛利 = 收入 − 人工成本 − 直接费用；项目现金净额 = 已收款 − 已付款；"
              "毛利与现金净额不可混用。")
LEGACY_NOTE = "历史专业流程现金（项目账）单列，不混入上述 1.0 简化口径。"


def revenue_basis(project):
    basis = getattr(project, "revenue_basis", INVOICE_BASIS)
    return basis if basis in {INVOICE_BASIS, RECEIPT_BASIS} else INVOICE_BASIS


def revenue_basis_label(basis):
    return "按收款确认收入" if basis == RECEIPT_BASIS else "按开票确认收入（不含税）"


def _sum(queryset, field="amount_cents"):
    return int(queryset.aggregate(total=Sum(field))["total"] or 0)


def labor_cost_cents(project):
    """All labor facts: legacy approved timesheets + monthly-pool postings + Q-35 rows."""
    from .labor_projection import project_monthly_cost
    from .weekly_time import simplified_labor_cost
    legacy = _sum(LedgerEntry.objects.filter(project=project, book=LedgerEntry.Book.COST,
                                            category="labor"))
    return int(legacy) + int(project_monthly_cost(project)) + int(simplified_labor_cost(project))


def project_finance(project):
    """Return the read-only project收益/现金 snapshot used by the project page (and Q-33)."""
    from .reporting import project_summary
    from pmo_plugins.billing.models import Invoice, Receipt
    from pmo_plugins.expenses.models import ExpensePayment
    from .weekly_time import simplified_gap_count

    summary = project_summary(project)
    invoices = Invoice.objects.filter(project=project)
    receipts = Receipt.objects.filter(project=project)
    invoiced_gross = _sum(invoices)
    invoiced_tax = _sum(invoices, "tax_cents")
    invoiced_net = invoiced_gross - invoiced_tax
    received = _sum(receipts)
    paid = int(ExpensePayment.objects.filter(expense__project=project)
               .aggregate(total=Sum("amount_cents"))["total"] or 0)
    contract = int(contract_amount_cents(project))
    basis = revenue_basis(project)
    revenue = received if basis == RECEIPT_BASIS else invoiced_net
    cost_total = int(summary["cost_cents"])
    labor = labor_cost_cents(project)
    direct_expenses = cost_total - labor
    margin = revenue - cost_total
    to_invoice = max(contract - invoiced_gross, 0)
    over_invoiced = max(invoiced_gross - contract, 0)
    outstanding = invoiced_gross - received
    cash_net = received - paid
    legacy_cash_net = int(summary["cash_net_cents"])
    margin_rate = None
    margin_rate_percent = None
    if revenue > 0:
        margin_rate = Decimal(margin) / Decimal(revenue)
        margin_rate_percent = str((margin_rate * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))

    submitted_time = Timesheet.objects.filter(project=project, status=Timesheet.Status.SUBMITTED).count()
    labor_gaps = int(simplified_gap_count(project)) + int(submitted_time)
    pending_expenses = Document.objects.filter(
        project=project, kind=Document.Kind.EXPENSE, status=Document.Status.SUBMITTED,
        expense_detail__isnull=False)
    expense_pending_count = pending_expenses.count()
    expense_pending_cents = _sum(pending_expenses)
    gaps = []
    if labor_gaps:
        gaps.append({"code": "labor_rate", "label": "缺费率/待入账工时", "count": labor_gaps,
                     "amount_cents": None, "severity": "warning",
                     "detail": "这些工时尚未计入项目人工成本，不显示假零。"})
    if expense_pending_count:
        gaps.append({"code": "expense_pending", "label": "待确认费用", "count": expense_pending_count,
                     "amount_cents": expense_pending_cents, "severity": "warning",
                     "detail": "确认后才计入成本；未确认金额单列，不冒充已发生成本。"})
    if contract == 0:
        gaps.append({"code": "contract_missing", "label": "合同额未配置", "count": 1,
                     "amount_cents": None, "severity": "warning",
                     "detail": "尚未登记合同额，待开票与合同进度无法计算。"})
    elif to_invoice:
        gaps.append({"code": "uninvoiced", "label": "未开票合同额", "count": 1,
                     "amount_cents": to_invoice, "severity": "info",
                     "detail": "合同额 − 已开票价税合计；未开票不计收入。"})
    if invoiced_gross > 0 and received == 0:
        gaps.append({"code": "no_receipt", "label": "已开票未收款", "count": 1,
                     "amount_cents": invoiced_gross, "severity": "warning",
                     "detail": "未收款 = 已开票 − 已收款。"})

    invoice_rows = list(invoices.select_related("reversal").order_by("-issued_on", "-id")[:8])
    for row in invoice_rows:
        row.reversal_record = row.reverses_id is not None
        row.voided_by_reversal = row.reverses_id is None and getattr(row, "reversal", None) is not None
    receipt_rows = list(receipts.select_related("account", "invoice")
                        .order_by("-received_on", "-id")[:8])
    for row in receipt_rows:
        row.refund_record = row.reverses_id is not None

    return {
        "contract_cents": contract,
        "contract_original_cents": project.contract_cents,
        "contract_changes": contract_history(project),
        "contract_change_count": project.contract_changes.count(),
        "invoiced_gross_cents": invoiced_gross,
        "invoiced_tax_cents": invoiced_tax,
        "invoiced_net_cents": invoiced_net,
        "to_invoice_cents": to_invoice,
        "over_invoiced_cents": over_invoiced,
        "received_cents": received,
        "outstanding_cents": outstanding,
        "paid_cents": paid,
        "labor_cost_cents": labor,
        "expense_cost_cents": direct_expenses,
        "cost_total_cents": cost_total,
        "revenue_cents": revenue,
        "gross_margin_cents": margin,
        "margin_rate_percent": margin_rate_percent,
        "cash_net_cents": cash_net,
        "legacy_cash_net_cents": legacy_cash_net,
        "revenue_basis": basis,
        "revenue_basis_label": revenue_basis_label(basis),
        "basis_note": BASIS_NOTE,
        "legacy_note": LEGACY_NOTE,
        "labor_gap_count": labor_gaps,
        "expense_pending_count": expense_pending_count,
        "expense_pending_cents": expense_pending_cents,
        "gaps": gaps,
        "invoice_rows": invoice_rows,
        "receipt_rows": receipt_rows,
    }
