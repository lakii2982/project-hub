from django.core.exceptions import PermissionDenied
from django.db.models import Q
from .models import Membership, Project

READ_FINANCE = {"owner", "finance", "auditor"}
WRITE_FINANCE = {"owner", "finance"}

def current_membership(request):
    if not request.user.is_authenticated:
        return None
    cached = getattr(request, "workspace_member", None)
    if cached is not None:
        return cached
    member = Membership.objects.select_related("organization", "user").filter(user=request.user, active=True).first()
    request.workspace_member = member
    return member

def require_member(member):
    if not member or not member.active or not member.user.is_active:
        raise PermissionDenied("此账户尚未加入有效公司工作区")

def visible_projects(member):
    require_member(member)
    qs = Project.objects.filter(organization=member.organization).select_related("manager__user", "organization")
    if member.role in READ_FINANCE:
        return qs
    return qs.filter(Q(manager=member) | Q(team__member=member)).distinct()

def can_manage(member, project):
    return member.organization_id == project.organization_id and (member.role == "owner" or (member.role == "pm" and project.manager_id == member.id))

def can_read_finance(member, project=None):
    if project and member.organization_id != project.organization_id:
        return False
    return member.role in READ_FINANCE or (member.role == "pm" and (project is None or project.manager_id == member.id))

def require_project(member, project, manage=False, finance=False):
    require_member(member)
    if not visible_projects(member).filter(pk=project.pk).exists():
        raise PermissionDenied("无权访问此项目")
    if manage and not can_manage(member, project):
        raise PermissionDenied("此操作需要项目经理或公司负责人")
    if finance and not can_read_finance(member, project):
        raise PermissionDenied("无权访问项目财务")

def require_write(member):
    require_member(member)
    if member.role == "auditor":
        raise PermissionDenied("审阅账户为只读权限")

def require_finance_write(member, project=None):
    require_write(member)
    if member.role not in WRITE_FINANCE and not (project and can_manage(member, project)):
        raise PermissionDenied("此操作需要项目财务或项目负责人")
