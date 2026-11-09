"""Q-41 read-only in-app due reminders (1.0 micro scope).

Reminders are derived from the same business facts and 1.0 basis as the pages:

* ``unpaid_invoice``: an issued, non-voided invoice whose linked receipts are short;
* ``overdue_expense``: a Q-36 confirmed expense past its expected pay date and not paid;
* ``missing_rate``: a Q-35 simplified timesheet row without a cost-rate snapshot.

One item exists per business fact (dedupe key is the source row), repeated scans are
idempotent, and nothing is persisted, sent, queued or posted.  No email/SMS/Webhook is
touched.  The module is read-only and scoped to the member's authorized projects.
"""
from datetime import date

from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.utils import timezone

from .access import require_member, visible_projects
from .models import Timesheet
from .weekly_time import MODE as SIMPLIFIED_MODE
from .weekly_time import STATUS as SIMPLIFIED_STATUS

DUE_ROLES = frozenset({"owner", "pm"})
KIND_LABELS = {
    "unpaid_invoice": "未收发票",
    "overdue_expense": "逾期未付费用",
    "missing_rate": "缺费率工时",
}


def _authorized_projects(member):
    projects = visible_projects(member)
    if member.role == "pm":
        # PM reminders only cover projects whose finance the PM may read (manager scope).
        projects = projects.filter(manager=member)
    return projects


def _invoice_items(member, projects, today):
    from pmo_plugins.billing.models import Invoice, Receipt

    invoices = (Invoice.objects.filter(organization=member.organization, project__in=projects)
                .select_related("project", "reversal").order_by("project__code", "issued_on", "id"))
    received_by_invoice = {}
    for row in (Receipt.objects.filter(invoice__in=invoices).values("invoice_id")
                .annotate(total=Sum("amount_cents"))):
        received_by_invoice[row["invoice_id"]] = int(row["total"] or 0)
    items = []
    for invoice in invoices:
        if invoice.reverses_id is not None or getattr(invoice, "reversal", None) is not None:
            continue  # void reversal row or an original already voided
        received = received_by_invoice.get(invoice.pk, 0)
        outstanding = int(invoice.amount_cents) - received
        if outstanding <= 0:
            continue
        items.append({
            "key": f"invoice:{invoice.pk}",
            "kind": "unpaid_invoice",
            "kind_label": KIND_LABELS["unpaid_invoice"],
            "project": invoice.project,
            "title": f"未收发票 {invoice.number}",
            "summary": f"{invoice.project.code} · 开票 {invoice.issued_on:%Y.%m.%d} · 未收 ¥{outstanding / 100:,.2f}",
            "due_on": invoice.issued_on,
            "overdue": invoice.issued_on < today,
            "amount_cents": outstanding,
            "href": f"/cashflow/?tab=invoices&project={invoice.project_id}",
            "source": f"invoice:{invoice.pk}",
        })
    return items


def _expense_items(member, today):
    from pmo_plugins.expenses.services import expense_snapshot

    snapshot = expense_snapshot(member, limit=100000)
    items = []
    for row in snapshot["rows"]:
        if not row["confirmed"] or row["unpaid_cents"] <= 0 or row["due_on"] >= today:
            continue
        document = row["document"]
        items.append({
            "key": f"expense:{document.pk}",
            "kind": "overdue_expense",
            "kind_label": KIND_LABELS["overdue_expense"],
            "project": document.project,
            "title": f"逾期未付费用 {document.title}",
            "summary": (f"{document.project.code} · 应付 {row['due_on']:%Y.%m.%d} · "
                        f"未付 ¥{row['unpaid_cents'] / 100:,.2f}"),
            "due_on": row["due_on"],
            "overdue": True,
            "amount_cents": int(row["unpaid_cents"]),
            "href": f"/expenses/?tab=all&project={document.project_id}",
            "source": f"expense:{document.pk}",
        })
    return items


def _missing_rate_items(member, projects, today):
    rows = (Timesheet.objects.filter(
        organization=member.organization, accounting_mode=SIMPLIFIED_MODE,
        status=SIMPLIFIED_STATUS, project__in=projects, project__isnull=False,
        cost_cents__isnull=True)
        .select_related("project", "employee")
        .order_by("project__code", "work_date", "id"))
    items = []
    for row in rows:
        items.append({
            "key": f"timesheet:{row.pk}",
            "kind": "missing_rate",
            "kind_label": KIND_LABELS["missing_rate"],
            "project": row.project,
            "title": f"缺费率工时 {row.employee.name}",
            "summary": (f"{row.project.code} · {row.work_date:%Y.%m.%d} · {row.minutes} 分钟，"
                        "尚未计入人工成本"),
            "due_on": row.work_date,
            "overdue": row.work_date < today,
            "amount_cents": None,
            "href": f"/time/?tab=detail&project={row.project_id}",
            "source": f"timesheet:{row.pk}",
        })
    return items


def due_reminders(member, today=None):
    """Return deduped, deterministic reminders for the authorized member; read-only."""
    require_member(member)
    if member.role not in DUE_ROLES:
        raise PermissionDenied("到期提醒仅向公司负责人和项目经理开放")
    today = today or timezone.localdate()
    projects = _authorized_projects(member)
    items = (_invoice_items(member, projects, today)
             + _expense_items(member, today)
             + _missing_rate_items(member, projects, today))
    deduped = {}
    for item in items:
        deduped[item["key"]] = item
    items = sorted(deduped.values(), key=lambda item: (
        not item["overdue"], item["due_on"] or date.max, item["kind"], item["key"]))
    counts = {kind: sum(1 for item in items if item["kind"] == kind) for kind in KIND_LABELS}
    return {
        "as_of": today,
        "items": items,
        "count": len(items),
        "counts": counts,
        "overdue_count": sum(1 for item in items if item["overdue"]),
        "kinds": KIND_LABELS,
    }
