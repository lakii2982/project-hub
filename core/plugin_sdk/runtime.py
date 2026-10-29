from dataclasses import dataclass
from types import MappingProxyType

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import HttpRequest

from core.access import require_member, require_project, require_write
from core.models import Membership
from core import plugin_runtime
from core.services import lock_company


@dataclass(frozen=True)
class EntryContext:
    member: Membership
    organization: object
    project: object
    plugin_id: str
    route_name: str
    access: str
    channel: str
    request: HttpRequest | None = None


class PluginEntryPoint:
    """Trusted bindings only. Access labels classify routes, not financial rights.

    A handler must call its existing owner service for payload project resolution,
    financial role/period/source checks and field privacy. Only an explicitly
    supplied project is checked here; arbitrary payload IDs are never authority.
    """
    CHANNELS = frozenset({"http", "service", "import", "background", "legacy"})

    def __init__(self, domain_registry, handlers, reversal_handlers=None):
        self.domain_registry = domain_registry
        self._routes = {}
        self._mounted_paths = set()
        for plugin_id, domain in domain_registry.domains.items():
            for route in domain["metadata"]["routes"]:
                key = (plugin_id, route["name"])
                mounted = f"modules/{plugin_id}/{route['path']}"
                if mounted in self._mounted_paths:
                    raise ValueError("duplicate plugin route")
                self._mounted_paths.add(mounted)
                self._routes[key] = route
        self.handlers = self._bindings(handlers, "handler")
        self.reversal_handlers = self._bindings(reversal_handlers or {}, "reversal handler")

    def _bindings(self, bindings, label):
        result = {}
        for plugin_id, named in bindings.items():
            if not isinstance(named, dict):
                raise TypeError(f"{label} bindings must be a mapping")
            for route_name, handler in named.items():
                key = (plugin_id, route_name)
                if key not in self._routes:
                    raise ValueError("binding does not match an installed route")
                if not callable(handler):
                    raise TypeError(f"{label} must be callable")
                result[key] = handler
        return MappingProxyType(result)

    def route_access(self, plugin_id, route_name):
        try:
            return self._routes[(plugin_id, route_name)]["access"]
        except KeyError as error:
            raise PermissionDenied("插件路由不可用") from error

    def urlpatterns(self, view):
        from django.urls import path

        return [
            path(
                f"modules/{plugin_id}/{route['path']}",
                view,
                {"plugin_id": plugin_id, "route_name": route_name},
                name=f"plugin:{plugin_id}:{route_name}",
            )
            for (plugin_id, route_name), route in self._routes.items()
        ]

    def _snapshot_enabled(self, organization, plugin_id):
        # Bind authorization to the exact installed execution snapshot. Checking
        # only the process registry lets a new handler borrow an old approval.
        registry = plugin_runtime.get_registry()
        seen = set()

        def consistent(key):
            if key in seen:
                return False
            seen.add(key)
            live = registry.manifests.get(key)
            if live is None:
                return False
            domain = self.domain_registry.domains.get(key)
            if domain is not None:
                frozen = domain["manifest"]
                if any(frozen[field] != live[field] for field in
                       ("version", "requires", "default_enabled", "kernel_api")):
                    return False
            result = all(consistent(dependency) for dependency in live["requires"])
            seen.remove(key)
            return result

        return consistent(plugin_id) and plugin_runtime.is_enabled(organization, plugin_id)

    def _authorize(self, member, plugin_id, route_name, project, organization, channel,
                   request, allow_disabled, reversal):
        route_access = self.route_access(plugin_id, route_name)
        if channel not in self.CHANNELS:
            raise PermissionDenied("未知插件入口")
        try:
            fresh = Membership.objects.select_related("organization", "user").get(pk=member.pk)
        except (AttributeError, Membership.DoesNotExist) as error:
            raise PermissionDenied("无效公司成员上下文") from error
        require_member(fresh)
        if organization is not None and organization.pk != fresh.organization_id:
            raise PermissionDenied("跨公司插件入口")
        if project is not None:
            require_project(fresh, project)
        if reversal:
            if route_access != "business_write":
                raise PermissionDenied("撤销必须绑定既有业务写入口")
            require_write(fresh)
        elif route_access == "business_write":
            require_write(fresh)
        if not allow_disabled and not self._snapshot_enabled(fresh.organization, plugin_id):
            raise PermissionDenied("插件未启用、依赖不可用或升级未确认")
        context = EntryContext(
            member=fresh,
            organization=fresh.organization,
            project=project,
            plugin_id=plugin_id,
            route_name=route_name,
            access=route_access,
            channel=channel,
            request=request,
        )
        return context

    def dispatch(self, member, plugin_id, route_name, *, payload=None, project=None,
                 organization=None, channel="service", request=None):
        key = (plugin_id, route_name)
        handler = self.handlers.get(key)
        if handler is None:
            raise PermissionDenied("插件处理器未注册")
        history = self.route_access(plugin_id, route_name) == "history_read"
        context = self._authorize(member, plugin_id, route_name, project, organization,
                                  channel, request, history, False)
        if context.access != "business_write":
            return handler(context, payload)
        with transaction.atomic():
            lock_company(context.member)
            context = self._authorize(member, plugin_id, route_name, project, organization,
                                      channel, request, history, False)
            return handler(context, payload)

    def reverse(self, member, plugin_id, route_name, *, payload=None, project=None,
                organization=None, channel="service"):
        key = (plugin_id, route_name)
        handler = self.reversal_handlers.get(key)
        if handler is None:
            raise PermissionDenied("插件撤销处理器未注册")
        context = self._authorize(member, plugin_id, route_name, project, organization,
                                  channel, None, True, True)
        with transaction.atomic():
            lock_company(context.member)
            context = self._authorize(member, plugin_id, route_name, project, organization,
                                      channel, None, True, True)
            return handler(context, payload)
