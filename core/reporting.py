from collections import defaultdict
from datetime import date, timedelta
from django.db.models import Sum
from django.utils import timezone
from .models import LedgerEntry, Timesheet
from .services import plan_remaining


def project_summary(project):
    totals = {"cost": 0, "cash_in": 0, "cash_out": 0}
    for row in project.entries.values("book").annotate(n=Sum("amount_cents")):
        totals[row["book"]] = row["n"]
    count = project.tasks.count()
    done = project.tasks.filter(status="done").count()
    overdue = project.tasks.filter(due_date__lt=timezone.localdate()).exclude(status="done").count()
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
            "has_open_tasks": project.tasks.exclude(status="done").exists(), "overdue_tasks": summary["overdue_tasks"],
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
