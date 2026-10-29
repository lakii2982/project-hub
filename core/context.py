from .access import current_membership, visible_projects
from .models import Timesheet, Document, CashPlan
from django.db.models import Q

def workspace(request):
    member = current_membership(request)
    if not member: return {}
    projects = visible_projects(member)
    count = (Timesheet.objects.filter(Q(project__in=projects) | Q(organization=member.organization, project__isnull=True, submitted_by=member.user) | (Q(organization=member.organization, project__isnull=True) if member.role in {"owner", "finance"} else Q(pk__in=[])), status="submitted").count()
             + Document.objects.filter(project__in=projects, status="submitted").count()
             + CashPlan.objects.filter(project__in=projects, approved=False, rejected=False, active=True).count())
    from .plugin_runtime import plugin_catalog, is_enabled
    flags = {spec["id"]: is_enabled(member.organization, spec["id"]) for spec in plugin_catalog()}
    return {"plugin_flags": flags, "membership": member, "organization": member.organization, "pending_count": count,
            "is_manager": member.role in {"owner", "pm", "finance"},
            "is_finance": member.role in {"owner", "finance"}, "is_owner": member.role == "owner",
            "is_viewer": member.role == "auditor"}
