"""Authorised view context for master-data maintenance.

No route is registered here yet: PH-026 wires this context to the Kimi
templates.  The context must never carry employee cost rates for an account
that is not allowed to maintain people, so restricted tabs fail before any
query is evaluated.
"""
from django.core.exceptions import PermissionDenied

from .access import WRITE_FINANCE, require_member, visible_projects
from .models import CostRate, Employee

TABS = ("projects", "employees", "rates")


def masterdata_context(member, tab="projects"):
    """Return the small, permission-filtered context for one master-data tab.

    Unknown tabs fall back to ``projects``.  The ``employees`` and ``rates``
    tabs are restricted to owners and finance; everyone else is rejected
    instead of receiving empty or partially filtered sensitive rows.
    """
    require_member(member)
    tab = tab if tab in TABS else "projects"
    can_write_people = member.role in WRITE_FINANCE
    can_write_projects = member.role in {"owner", "pm"}
    if tab != "projects" and not can_write_people:
        raise PermissionDenied("只有公司负责人和项目财务可以维护员工与费率")

    projects = list(visible_projects(member))
    employees = []
    rates = []
    if tab != "projects":
        employees = list(
            Employee.objects.filter(organization=member.organization)
            .select_related("member__user")
            .order_by("name", "id")
        )
        if tab == "rates":
            rates = list(
                CostRate.objects.filter(employee__organization=member.organization)
                .select_related("employee")
                .order_by("-effective_from", "-id")
            )

    return {
        "tab": tab,
        "projects": projects,
        "employees": employees,
        "rates": rates,
        "can_write_projects": can_write_projects,
        "can_write_people": can_write_people,
    }
