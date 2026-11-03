"""Q-38 现金页只读聚合：资金账户派生余额、收付款流水与未来 8 周只读计划。

口径（docs/product-scope.md §4.4）：
* 账户余额 = 期初余额 + 已收款（退款负值） − 已付款（Q-36 关联账户的付款），派生值；
* 8 周计划 = 未收收款计划（core CashPlan 收款方向，来源键 ``payment_plan:<id>``）
  + 已确认未付支出（``expense:<id>``，按 expected_pay_on，未填按发生日期）；
* 两类来源按 (source_kind, source_id) 去重，已结清移出；过期单列，不自动挪周；
* 预计余额 = 账户余额 + 8 周内计划收款 − 8 周内计划付款，为负时提示缺口；
* 本模块只读：不写账本、核销、审计或业务事实。账户维护写操作在 billing 服务里。

简化口径：Q-37 收款不绑定收款计划，因此现金页把项目已收款按到期日先后只读抵减
该项目未结清收款计划（旧流程已核销金额先扣减）；不落核销事实、不生成应收应付。
"""
from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.utils import timezone

from core.access import require_member, visible_projects
from core.models import CashPlan, Document, LedgerEntry
from pmo_plugins.billing.models import Account, Receipt
from pmo_plugins.billing.services import account_balance_cents, account_rows
from pmo_plugins.expenses.models import ExpensePayment
from pmo_plugins.expenses.services import cash_plan as expense_cash_plan

CASH_ROLES = frozenset({"owner", "pm"})
EIGHT_WEEKS = 8


def cash_member(member):
    require_member(member)
    if member.role not in CASH_ROLES:
        raise PermissionDenied("现金页仅向公司负责人和项目经理开放")
    return member


def cash_projects(member):
    """Owner sees every company project; PM only the projects they manage."""
    cash_member(member)
    rows = visible_projects(member)
    if member.role != "owner":
        rows = rows.filter(manager=member)
    return rows


def _sum(rows, field="amount_cents"):
    return int(rows.aggregate(total=Sum(field))["total"] or 0)


def _account_flows(member, limit=120):
    """Combined cash flows on funding accounts: Q-37 receipts/refunds + Q-36 payments."""
    org = member.organization
    receipts = (Receipt.objects.filter(organization=org)
                .select_related("account", "project", "invoice", "reverses")
                .order_by("-received_on", "-id")[:limit])
    payments = (ExpensePayment.objects.filter(organization=org, account__isnull=False)
                .select_related("account", "expense__project", "payment_document")
                .order_by("-paid_on", "-id")[:limit])
    rows = []
    for row in receipts:
        rows.append({
            "date": row.received_on, "kind": "refund" if row.reverses_id else "receipt",
            "label": "退款" if row.reverses_id else "收款", "direction": "in",
            "account": row.account, "project": row.project,
            "title": (f"发票 {row.invoice.number}" if row.invoice_id
                      else (row.counterparty or "项目收款")),
            "counterparty": row.counterparty,
            "reference": row.reference,
            "amount_cents": int(row.amount_cents),
            "signed_cents": int(row.amount_cents),
            "source": f"receipt:{row.pk}",
        })
    for row in payments:
        rows.append({
            "date": row.paid_on, "kind": "payment", "label": "付款", "direction": "out",
            "account": row.account, "project": row.expense.project,
            "title": row.expense.title, "counterparty": row.expense.counterparty,
            "reference": row.payment_document.reference,
            "amount_cents": int(row.amount_cents),
            "signed_cents": -int(row.amount_cents),
            "source": f"expense_payment:{row.pk}",
        })
    rows.sort(key=lambda item: (item["date"], item["source"]), reverse=True)
    return rows


def _receivable_plan_items(member, projects, receipts_by_project, today):
    """Unsettled core receivable plans, after legacy settlement and FIFO simplified receipts."""
    # Available simplified receipts are computed before the empty-plan branch: a project
    # with receipts but zero enabled/approved receivable plans must surface the whole
    # received amount as an unmatched-receipt gap, never a fake zero.
    available = {project_id: max(amount, 0)
                 for project_id, amount in receipts_by_project.items()}
    plans = list(CashPlan.objects.filter(
        project__in=projects, direction=CashPlan.Direction.IN, approved=True, active=True)
        .select_related("project").order_by("due_date", "id"))
    if not plans:
        return [], sum(available.values())
    settled = {row["document__plan_id"]: int(row["total"] or 0) for row in
               LedgerEntry.objects.filter(document__plan__in=plans, book=LedgerEntry.Book.CASH_IN)
               .values("document__plan_id").annotate(total=Sum("amount_cents"))}
    items = []
    for plan in plans:
        amount = int(plan.amount_cents)
        legacy_settled = max(int(settled.get(plan.pk, 0)), 0)
        core_outstanding = max(amount - legacy_settled, 0)
        if core_outstanding <= 0:
            continue
        applied = min(available.get(plan.project_id, 0), core_outstanding)
        available[plan.project_id] = available.get(plan.project_id, 0) - applied
        outstanding = core_outstanding - applied
        if outstanding <= 0:
            continue
        days = (plan.due_date - today).days
        items.append({
            "source_kind": "payment_plan", "source_id": f"payment_plan:{plan.pk}",
            "direction": "in", "due_on": plan.due_date, "amount_cents": outstanding,
            "settled_cents": legacy_settled + applied,
            "legacy_settled_cents": legacy_settled, "applied_receipt_cents": applied,
            "total_cents": amount, "project": plan.project,
            "title": plan.title, "counterparty": plan.counterparty,
            "overdue": days < 0, "days": days,
        })
    unfunded = sum(amount for amount in available.values() if amount > 0)
    return items, unfunded


def _expense_plan_items(member):
    """Reuse Q-36's read-only unpaid-expense plan so cost/cash rules stay in one owner."""
    plan = expense_cash_plan(member)
    items = []
    for row in plan["items"]:
        items.append({
            "source_kind": "expense", "source_id": row["source_id"], "direction": "out",
            "due_on": row["due_on"], "amount_cents": int(row["amount_cents"]),
            "settled_cents": int(row["settled_cents"]), "total_cents": None,
            "expense_id": row.get("expense_id"), "project": row["project"],
            "title": row["title"], "category_label": row.get("category_label", ""),
            "overdue": row["days"] < 0, "days": row["days"],
        })
    return items, plan


def cash_snapshot(member, today=None, weeks=EIGHT_WEEKS):
    """Read-only Q-38 cash page data. Never writes a business row or audit event."""
    member = cash_member(member)
    today = today or timezone.localdate()
    projects = list(cash_projects(member).select_related("manager"))
    project_ids = [project.pk for project in projects]
    accounts = account_rows(member.organization)
    account_total = sum(account.balance_cents for account in accounts)

    receipts_by_project = {row["project_id"]: int(row["total"] or 0) for row in
                           Receipt.objects.filter(
                               organization=member.organization,
                               project_id__in=project_ids)
                           .values("project_id").annotate(total=Sum("amount_cents"))}
    plan_items, unplanned_receipts = _receivable_plan_items(
        member, projects, receipts_by_project, today)
    expense_items, expense_plan = _expense_plan_items(member)
    seen, items = set(), []
    for item in plan_items + expense_items:
        key = (item["source_kind"], item["source_id"])
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    if not plan_items and not items and not projects:
        items = items  # keep empty plan explicit

    buckets = [{"index": index, "in_cents": 0, "out_cents": 0, "items": []}
               for index in range(weeks)]
    overdue, beyond = [], []
    for item in items:
        days = (item["due_on"] - today).days
        item["due_label"] = item["due_on"].strftime("%Y.%m.%d")
        if days < 0:
            overdue.append(item)
            continue
        week_index = days // 7
        if week_index >= weeks:
            beyond.append(item)
            continue
        bucket = buckets[week_index]
        bucket["items"].append(item)
        if item["direction"] == "in":
            bucket["in_cents"] += item["amount_cents"]
        else:
            bucket["out_cents"] += item["amount_cents"]
    projected = account_total
    negative_week = None
    week_rows = []
    for bucket in buckets:
        start = today + timedelta(days=7 * bucket["index"])
        end = start + timedelta(days=6)
        projected += bucket["in_cents"] - bucket["out_cents"]
        if projected < 0 and negative_week is None:
            negative_week = bucket["index"]
        week_rows.append({
            "index": bucket["index"], "label": f"{start:%m.%d}–{end:%m.%d}",
            "in_cents": bucket["in_cents"], "out_cents": bucket["out_cents"],
            "net_cents": bucket["in_cents"] - bucket["out_cents"],
            "projected_cents": projected, "items": bucket["items"],
        })
    within_in = sum(row["in_cents"] for row in week_rows)
    within_out = sum(row["out_cents"] for row in week_rows)
    projected_end = account_total + within_in - within_out

    org = member.organization
    unlinked_payments = ExpensePayment.objects.filter(organization=org, account__isnull=True)
    legacy_receipts = Document.objects.filter(
        project_id__in=project_ids, kind=Document.Kind.RECEIPT,
        status__in=[Document.Status.SUBMITTED, Document.Status.APPROVED]).count()
    legacy_payments = Document.objects.filter(
        project_id__in=project_ids, kind=Document.Kind.PAYMENT,
        status__in=[Document.Status.SUBMITTED, Document.Status.APPROVED],
        expense_payment_link__isnull=True).count()
    pending_expenses = Document.objects.filter(
        project_id__in=project_ids, kind=Document.Kind.EXPENSE,
        status=Document.Status.SUBMITTED, expense_detail__isnull=False)
    legacy_payable_plans = list(CashPlan.objects.filter(
        project_id__in=project_ids, direction=CashPlan.Direction.OUT,
        approved=True, active=True))
    from core.services import plan_remaining
    legacy_payable_plan_cents = sum(
        max(int(plan_remaining(plan)), 0) for plan in legacy_payable_plans)

    gaps = {
        "no_accounts": not accounts,
        "unlinked_payment_count": unlinked_payments.count(),
        "unlinked_payment_cents": _sum(unlinked_payments),
        "legacy_receipt_count": legacy_receipts,
        "legacy_payment_count": legacy_payments,
        "legacy_payable_plan_count": len(legacy_payable_plans),
        "legacy_payable_plan_cents": legacy_payable_plan_cents,
        "unplanned_receipt_cents": unplanned_receipts,
        "pending_expense_count": pending_expenses.count(),
        "pending_expense_cents": _sum(pending_expenses),
    }
    return {
        "today": today,
        "accounts": accounts,
        "account_total_cents": account_total,
        "flows": _account_flows(member),
        "plan": {
            "items": items,
            "weeks": week_rows,
            "overdue": overdue,
            "beyond": beyond,
            "within_in_cents": within_in,
            "within_out_cents": within_out,
            "within_net_cents": within_in - within_out,
            "account_total_cents": account_total,
            "projected_end_cents": projected_end,
            "negative": negative_week is not None or projected_end < 0,
            "negative_week": negative_week,
            "overdue_count": len(overdue),
            "overdue_in_cents": sum(item["amount_cents"] for item in overdue
                                    if item["direction"] == "in"),
            "overdue_out_cents": sum(item["amount_cents"] for item in overdue
                                     if item["direction"] == "out"),
            "beyond_count": len(beyond),
            "beyond_cents": sum(item["amount_cents"] for item in beyond),
            "source_count": len(items),
            "payment_plan_count": len(plan_items),
            "expense_count": len(expense_items),
            "expense_plan": expense_plan,
        },
        "gaps": gaps,
        "can_manage_accounts": member.role == "owner",
    }
