from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.contrib import messages
from django.shortcuts import render, redirect
from .access import current_membership, require_member
from .models import DomainEvent, EventDelivery
from .plugin_runtime import plugin_catalog, is_enabled, set_enabled, get_registry

@login_required
def plugin_settings(request):
    member = current_membership(request)
    require_member(member)
    if member.role not in {"owner", "finance", "auditor"}: raise PermissionDenied()
    if request.method == "POST":
        if member.role != "owner": raise PermissionDenied()
        if request.POST.get("enabled") not in {"0", "1"}: raise PermissionDenied()
        try:
            set_enabled(member, request.POST.get("plugin_id"), request.POST["enabled"] == "1")
            messages.success(request, "插件状态已更新，原始业务数据完整保留")
        except ValidationError as error: messages.error(request, "；".join(error.messages))
        return redirect("plugin-settings")
    capability_names = {"project:read_snapshot": "读取授权项目快照", "insights:emit": "生成项目提醒", "personal_agent_api": "连接个人办公助手"}
    plugins = []
    for spec in plugin_catalog():
        plugins.append({**spec, "enabled": is_enabled(member.organization, spec["id"]), "capability_labels": [capability_names.get(c, c) for c in spec["capabilities"]]})
    rows = EventDelivery.objects.filter(event__organization=member.organization)
    return render(request, "plugins.html", {"nav": "plugins", "plugins": plugins, "plugin_errors": get_registry().errors,
        "unexpanded_count": DomainEvent.objects.filter(organization=member.organization, expanded_at__isnull=True).count(),
        "failed_count": rows.filter(status="failed").count(), "blocked_count": rows.filter(status="blocked").count(),
        "pending_events": rows.filter(status__in=["pending", "retry"]).count()})
