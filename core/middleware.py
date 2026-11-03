"""Request gate for the 1.0 micro scope.

Kept entries stay reachable; pages moved out of the 1.0 scope (complex finance,
plugin management, AI assistant, approvals, personal agent) return the disabled
page for direct URLs. Hiding the menu alone is not a gate.
"""
from django.shortcuts import render

from .access import current_membership
from .scope import is_micro_disabled_path, is_micro_scope


class MicroScopeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if (is_micro_scope() and request.user.is_authenticated
                and is_micro_disabled_path(request.path)):
            member = current_membership(request)
            if member is not None:
                return render(request, "scope_disabled.html", {
                    "nav": "",
                    "membership": member,
                    "organization": member.organization,
                    "can_read_audit": member.role in {"owner", "finance", "auditor"},
                }, status=403)
        return self.get_response(request)
