"""Q-41 owner-facing CSV exports for the 1.0 micro pages.

Only the simplified micro facts are exported, with the same numbers and permissions as
the corresponding pages (Q-33 dashboard / Q-34 project page / Q-35 time / Q-36 expenses /
Q-37 billing).  Personal hourly rates and wages are deliberately not part of any export;
legacy professional ledgers, contracts, settlements or other moved-out data are not read.

Output is deterministic: the same data and business date produce the same bytes and file
name.  The download view writes one audit event per successful export.
"""
import csv
import io
from datetime import date

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from .access import require_member, visible_projects

EXPORTS = (
    {"kind": "project-finance", "label": "项目收益与现金",
     "description": "合同额、开票、收款、1.0 人工/费用、毛利、现金净额与缺口（与项目页同口径）"},
    {"kind": "time", "label": "工时",
     "description": "项目工时明细与分钟数；不含个人费率/工资与成本金额"},
    {"kind": "expenses", "label": "费用",
     "description": "Q-36 简化费用登记、确认状态、已付/未付；不含历史专业费用底稿"},
    {"kind": "billing", "label": "开票与收款",
     "description": "发票与收款的价税合计、税额、不含税、已收/未收；含作废/退款追加记录"},
)
EXPORT_KINDS = tuple(item["kind"] for item in EXPORTS)


def csv_safe(value):
    """Guard spreadsheet formula injection for every textual cell."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + value
    return value


def _project_rows(member):
    from .project_finance import project_finance

    headers = ["项目编号", "项目名称", "收入口径", "合同额_分", "已开票价税合计_分",
               "其中税额_分", "已开票不含税_分", "待开票_分", "已收款_分", "未收款_分",
               "1.0人工成本_分", "1.0直接费用_分", "1.0成本合计_分", "毛利_分",
               "毛利率_百分比", "已付款_分", "项目现金净额_分", "历史专业成本_分", "缺口"]
    rows = []
    for project in visible_projects(member).order_by("code", "pk"):
        finance = project_finance(project)
        gaps = "; ".join(
            f"{gap['label']}:{gap['count']}" + (f"（{gap['amount_cents']}分）" if gap["amount_cents"] else "")
            for gap in finance["gaps"])
        rows.append([
            project.code, project.name, finance["revenue_basis_label"],
            finance["contract_cents"], finance["invoiced_gross_cents"], finance["invoiced_tax_cents"],
            finance["invoiced_net_cents"], finance["to_invoice_cents"], finance["received_cents"],
            finance["outstanding_cents"], finance["labor_cost_cents"], finance["expense_cost_cents"],
            finance["cost_total_cents"], finance["gross_margin_cents"],
            finance["margin_rate_percent"] or "", finance["paid_cents"], finance["cash_net_cents"],
            finance["historical_cost_cents"], gaps,
        ])
    return headers, rows


def _time_rows(member):
    from .models import Timesheet

    headers = ["项目编号", "项目名称", "业务日期", "员工", "分钟", "核算模式", "状态", "说明"]
    mode_labels = {"simplified": "简化周填报", "monthly_pool": "月池", "legacy": "单条/历史"}
    rows = []
    for row in (Timesheet.objects.filter(project__in=visible_projects(member))
                .select_related("project", "employee").order_by("project__code", "work_date", "id")):
        rows.append([
            row.project.code, row.project.name, row.work_date.isoformat(), row.employee.name,
            row.minutes, mode_labels.get(row.accounting_mode, row.accounting_mode),
            row.get_status_display(), row.note,
        ])
    # Owner time page also shows company non-project rows; export them without project.
    for row in (Timesheet.objects.filter(organization=member.organization, project__isnull=True)
                .select_related("employee").order_by("work_date", "id")):
        rows.append(["公司非项目", "", row.work_date.isoformat(), row.employee.name, row.minutes,
                     mode_labels.get(row.accounting_mode, row.accounting_mode),
                     row.get_status_display(), row.note])
    rows.sort(key=lambda item: (item[0], item[2], item[3], item[4]))
    return headers, rows


def _expense_rows(member):
    from pmo_plugins.expenses.services import expense_snapshot

    headers = ["项目编号", "项目名称", "发生日期", "类别", "金额_分", "状态",
               "已付_分", "未付_分", "预计付款日", "供应商/往来方", "说明"]
    rows = []
    for row in expense_snapshot(member, limit=100000)["rows"]:
        document = row["document"]
        rows.append([
            document.project.code, document.project.name, document.occurred_on.isoformat(),
            row["category_label"], row["amount_cents"],
            "已确认" if row["confirmed"] else "待确认", row["paid_cents"], row["unpaid_cents"],
            row["expected_pay_on"].isoformat() if row["expected_pay_on"] else "",
            document.counterparty, document.note,
        ])
    rows.sort(key=lambda item: (item[0], item[2], item[3]))
    return headers, rows


def _billing_rows(member):
    from pmo_plugins.billing.services import invoice_rows, receipt_rows

    headers = ["类型", "项目编号", "项目名称", "日期", "编号/来源", "价税合计_分", "其中税额_分",
               "不含税_分", "已收或退款_分", "未收_分", "资金账户", "状态", "说明"]
    rows = []
    for row in invoice_rows(member, limit=100000):
        rows.append([
            "发票", row.project.code, row.project.name, row.issued_on.isoformat(), row.number,
            row.amount_cents, row.tax_cents, row.amount_cents - row.tax_cents,
            row.received_cents, row.outstanding_cents, "", "作废" if row.is_voided else "有效",
            row.note,
        ])
    for row in receipt_rows(member, limit=100000):
        rows.append([
            "收款", row.project.code if row.project_id else "非项目", row.project.name if row.project_id else "",
            row.received_on.isoformat(), row.reference,
            row.amount_cents, "", "", row.amount_cents, "",
            row.account.name, "退款" if row.is_refund else "有效", row.note,
        ])
    rows.sort(key=lambda item: (item[1], item[3], item[0], item[4]))
    return headers, rows


BUILDERS = {
    "project-finance": _project_rows,
    "time": _time_rows,
    "expenses": _expense_rows,
    "billing": _billing_rows,
}


def build_export(member, kind, today=None):
    """Return deterministic CSV headers/rows/name for an authorized owner export."""
    require_member(member)
    if member.role != "owner":
        raise PermissionDenied("简化导出仅向公司负责人开放")
    if kind not in EXPORT_KINDS:
        raise ValueError("unknown export kind")
    today = today or timezone.localdate()
    headers, rows = BUILDERS[kind](member)
    filename = f"project-hub-micro-{kind}-{member.organization_id}-{today.isoformat()}.csv"
    return {"kind": kind, "headers": headers, "rows": rows, "filename": filename,
            "row_count": len(rows), "as_of": today}


def export_csv_text(data):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([csv_safe(item) for item in data["headers"]])
    writer.writerows([[csv_safe(item) for item in row] for row in data["rows"]])
    return "\ufeff" + output.getvalue()
