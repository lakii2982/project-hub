from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.db.models import Q, Sum
from django.utils import timezone
from .charts import line_chart
from .models import LedgerEntry, Timesheet
from .services import plan_remaining


def _key_tasks(project):
    """Only milestone/cash-plan linked tasks drive key-task statistics."""
    return project.tasks.filter(Q(milestone__isnull=False) | Q(cash_plan__isnull=False))


def project_summary(project):
    totals = {"cost": 0, "cash_in": 0, "cash_out": 0}
    for row in project.entries.values("book").annotate(n=Sum("amount_cents")):
        totals[row["book"]] = row["n"]
    linked = _key_tasks(project)
    count = linked.count()
    done = linked.filter(status="done").count()
    overdue = linked.filter(due_date__lt=timezone.localdate()).exclude(status="done").count()
    receivable = payable = 0
    for plan in project.cash_plans.filter(approved=True, active=True):
        if plan.direction == "in": receivable += plan_remaining(plan)
        else: payable += plan_remaining(plan)
    eac = totals["cost"] + project.etc_cents
    over = project.budget_approved and eac > project.budget_cents
    health = "high" if over or overdue else "medium" if not project.budget_approved else "good"
    return {"project": project, "cost_cents": totals["cost"], "budget_cents": project.budget_cents,
            "remaining_cents": project.budget_cents - totals["cost"], "cash_in_cents": totals["cash_in"],
            "cash_out_cents": totals["cash_out"], "cash_net_cents": totals["cash_in"] - totals["cash_out"],
            "receivable_cents": receivable, "payable_cents": payable, "eac_cents": eac,
            "progress": round(done * 100 / count) if count else 0, "overdue_tasks": overdue,
            "health": health, "health_label": "需要干预" if health == "high" else "待完善" if health == "medium" else "推进正常",
            "total_minutes": project.timesheets.filter(status="approved").aggregate(n=Sum("minutes"))["n"] or 0}


def project_cost_burndown(project, today=None):
    """Budget-balance burndown from approved budget and cost ledger only.

    The whole curve is restated against the *current* approved budget; no
    historical budget version is reconstructed.  Cash entries and pending
    documents never become cost.  Callers must have already authorised the
    project for finance viewing.
    """
    today = today or timezone.localdate()
    budget = int(project.budget_cents)
    if not project.budget_approved:
        reason = "预算尚未批准，暂不显示成本预算余额图。"
        return {"available": False, "reason": reason, "as_of": today,
                "budget_cents": budget, "actual_cost_cents": 0, "remaining_cents": 0,
                "note": reason, "rows": [], "chart": None}

    daily = defaultdict(int)
    for occurred_on, amount in project.entries.filter(
            book=LedgerEntry.Book.COST).values_list("occurred_on", "amount_cents"):
        daily[occurred_on] += int(amount)

    start, end = project.start_date, project.end_date
    actual_cost = sum(amount for day, amount in daily.items() if day <= today)
    remaining = budget - actual_cost

    days = set(daily) | {start, end, today}

    def plan_cents(day):
        if start == end:
            return budget if day < start else 0
        if day <= start:
            return budget
        if day >= end:
            return 0
        share = Decimal((end - day).days) / Decimal((end - start).days)
        return int((Decimal(budget) * share).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    cost_days = sorted(day for day in daily if day <= today)
    balance_at, cursor, cumulative = {}, 0, 0
    rows = []
    for day in sorted(days):
        while cursor < len(cost_days) and cost_days[cursor] <= day:
            cumulative += daily[cost_days[cursor]]
            cursor += 1
        actual_balance = budget - cumulative if day <= today else None
        if day <= today:
            balance_at[day] = actual_balance
        rows.append({"date": day, "actual_cents": actual_balance,
                     "plan_cents": plan_cents(day)})

    label_format = "%Y-%m" if (end - start).days > 366 else "%m.%d"
    chart_rows = [{"date": row["date"], "label": row["date"].strftime(label_format),
                   "actual": row["actual_cents"], "plan": row["plan_cents"]} for row in rows]
    chart = line_chart(chart_rows, [
        {"key": "actual", "label": "实际预算余额", "reference": False},
        {"key": "plan", "label": "计划参考", "reference": True},
    ], divisor=100)
    return {"available": True, "reason": "", "as_of": today, "budget_cents": budget,
            "actual_cost_cents": actual_cost, "remaining_cents": remaining,
            "note": f"按当前批准预算计算；实际线截至 {today:%Y-%m-%d}，计划线仅为参考。",
            "rows": rows, "chart": chart}


def portfolio_metrics(projects):
    cards = [project_summary(p) for p in projects]
    metrics = {"active_projects": sum(c["project"].stage not in {"closed", "cancelled"} for c in cards)}
    for key in ["cost_cents", "cash_in_cents", "cash_out_cents", "receivable_cents", "payable_cents"]:
        metrics[key] = sum(c[key] for c in cards)
    metrics["net_cash_cents"] = metrics["cash_in_cents"] - metrics["cash_out_cents"]
    return metrics, cards


def cash_forecast(projects, weeks=13):
    """Deterministic dated plan. Past-due items require reforecast; never invent a date."""
    from .models import CashPlan
    project_ids = [p.id for p in projects]
    balance = 0
    for row in LedgerEntry.objects.filter(project_id__in=project_ids).values("book").annotate(n=Sum("amount_cents")):
        if row["book"] == "cash_in": balance += row["n"]
        if row["book"] == "cash_out": balance -= row["n"]
    today = timezone.localdate()
    buckets = defaultdict(lambda: {"in": 0, "out": 0})
    for plan in CashPlan.objects.filter(project_id__in=project_ids, approved=True, active=True):
        days = (plan.due_date - today).days
        if days < 0:
            continue
        week = days // 7
        if week < weeks:
            buckets[week][plan.direction] += max(0, plan_remaining(plan))
    result = []
    for week in range(weeks):
        start = today + timedelta(days=7 * week)
        inc, out = buckets[week]["in"], buckets[week]["out"]
        balance += inc - out
        result.append({"period": f"{start:%m.%d}–{start + timedelta(days=6):%m.%d}", "in_cents": inc,
                       "out_cents": out, "balance_cents": balance})
    return result


def alerts_for(projects, include_finance=True):
    from .plugin_runtime import collect_insights
    projects = list(projects)
    if not projects: return []
    org = projects[0].organization
    snapshots = []
    today = timezone.localdate()
    for project in projects:
        if project.organization_id != org.pk: raise ValueError("Plugin snapshots cannot mix organizations")
        summary = project_summary(project)
        snapshot = {"id": project.pk, "name": project.name, "url": f"/projects/{project.pk}/", "stage": project.stage,
            "budget_approved": project.budget_approved if include_finance else None,
            "budget_cents": project.budget_cents if include_finance else None,
            "cost_cents": summary["cost_cents"] if include_finance else None,
            "eac_cents": summary["eac_cents"] if include_finance else None,
            "etc_cents": project.etc_cents if include_finance else None,
            "has_open_tasks": _key_tasks(project).exclude(status="done").exists(), "overdue_tasks": summary["overdue_tasks"],
            "overdue_milestones": [{"name": m.name, "due_date": str(m.due_date)} for m in project.milestones.filter(accepted_at__isnull=True, due_date__lt=today)],
            "upcoming_milestones": [{"name": m.name, "due_date": str(m.due_date), "days_remaining": (m.due_date - today).days}
                for m in project.milestones.filter(accepted_at__isnull=True, due_date__gte=today, due_date__lte=today + timedelta(days=7))],
            "overdue_plans": []}
        if include_finance:
            snapshot["overdue_plans"] = [{"direction": p.direction, "title": p.title, "due_date": str(p.due_date)}
                for p in project.cash_plans.filter(approved=True, active=True, due_date__lt=today) if plan_remaining(p) > 0]
        snapshots.append(snapshot)
    return collect_insights(org, snapshots, include_finance=include_finance)


def overdue_plan_totals(projects):
    from .models import CashPlan
    result = {"count": 0, "in_cents": 0, "out_cents": 0}
    for plan in CashPlan.objects.filter(project__in=projects, approved=True, active=True, due_date__lt=timezone.localdate()):
        remaining = plan_remaining(plan)
        if remaining > 0:
            result["count"] += 1
            result["in_cents" if plan.direction == "in" else "out_cents"] += remaining
    return result
