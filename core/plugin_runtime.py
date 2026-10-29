"""Trusted installed plugins, versioned hooks and per-company lifecycle. Not a code sandbox."""
import copy
import importlib
import json
import logging
import re
from datetime import timedelta
from functools import lru_cache
import pluggy
from packaging.version import Version, InvalidVersion
from packaging.specifiers import SpecifierSet, InvalidSpecifier
from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone
from .models import Organization, PluginState, DomainEvent, EventDelivery
from .services import lock_company, audit

logger = logging.getLogger(__name__)
KERNEL_API = "1.0"
DEFAULT_MODULES = ("pmo_plugins.project_health.plugin", "pmo_plugins.delivery_watch.plugin", "pmo_plugins.agent_gateway.plugin")
hookspec = pluggy.HookspecMarker("project_hub")

class HookSpecs:
    @hookspec
    def project_insights(self, snapshot, include_finance):
        """Return bounded insights from an already-authorized read snapshot."""
    @hookspec
    def event_received(self, event):
        """Return a namespaced artifact. Must not change business records."""

class Registry:
    def __init__(self, modules=None, instances=None):
        self.manager = pluggy.PluginManager("project_hub")
        self.manager.add_hookspecs(HookSpecs)
        self.plugins, self.manifests, self.errors = {}, {}, {}
        if instances is None:
            instances = []
            for path in (DEFAULT_MODULES if modules is None else modules):
                try: instances.append(importlib.import_module(path).Plugin())
                except Exception as error:
                    self.errors[path] = type(error).__name__
                    logger.error("插件加载失败: %s (%s)", path, type(error).__name__)
        for plugin in instances:
            try:
                spec = self.validate(plugin.manifest)
                if spec["id"] in self.plugins: raise ValueError("duplicate plugin id")
                self.manager.register(plugin, name=spec["id"])
                self.manager.check_pending()
                self.plugins[spec["id"]] = plugin
                self.manifests[spec["id"]] = spec
            except Exception as error:
                if self.manager.is_registered(plugin): self.manager.unregister(plugin)
                self.errors[getattr(plugin, "manifest", {}).get("id", type(plugin).__name__)] = type(error).__name__
        self.manager.check_pending()
        # Refuse only broken plugins and their dependents; unrelated modules remain available.
        for key in list(self.manifests):
            try:
                self.validate_graph(key)
            except Exception as error:
                self.errors[key] = str(error)[:200]
                self.manager.unregister(self.plugins.pop(key))
                self.manifests.pop(key)

    @staticmethod
    def validate(spec):
        keys = {"id", "name", "version", "kernel_api", "requires", "default_enabled", "capabilities", "description"}
        if not isinstance(spec, dict) or set(spec) != keys: raise ValueError("manifest fields")
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,79}", spec["id"]): raise ValueError("plugin id")
        if any(not isinstance(spec[k], str) or not spec[k] for k in ["name", "version", "kernel_api", "description"]): raise ValueError("manifest types")
        Version(spec["version"])
        if Version(KERNEL_API) not in SpecifierSet(spec["kernel_api"]): raise ValueError("incompatible kernel API")
        if not isinstance(spec["requires"], dict) or type(spec["default_enabled"]) is not bool: raise ValueError("manifest types")
        if not isinstance(spec["capabilities"], list) or any(not isinstance(c, str) for c in spec["capabilities"]): raise ValueError("capabilities")
        for constraint in spec["requires"].values(): SpecifierSet(constraint)
        return dict(spec)

    def validate_graph(self, plugin_id=None):
        done, visiting = set(), set()
        def visit(plugin_id):
            if plugin_id in visiting: raise ValueError("插件依赖循环")
            if plugin_id in done: return
            visiting.add(plugin_id)
            for dependency, constraint in self.manifests[plugin_id]["requires"].items():
                if dependency not in self.manifests: raise ValueError("插件依赖未安装")
                if Version(self.manifests[dependency]["version"]) not in SpecifierSet(constraint): raise ValueError("插件依赖版本不兼容")
                visit(dependency)
            visiting.remove(plugin_id); done.add(plugin_id)
        for key in ([plugin_id] if plugin_id else self.manifests): visit(key)

    def call_one(self, plugin_id, hook, *, organization, **kwargs):
        if organization is None or plugin_id not in self.plugins or not is_enabled(organization, plugin_id, registry=self):
            raise PermissionDenied("插件旧入口缺少有效公司上下文或已停用")
        plugin = self.plugins[plugin_id]
        others = [p for key, p in self.plugins.items() if key != plugin_id]
        caller = self.manager.subset_hook_caller(hook, remove_plugins=others)
        return caller(**kwargs)

@lru_cache(maxsize=1)
def get_registry():
    return Registry(modules=tuple(getattr(settings, "PMO_PLUGIN_MODULES", DEFAULT_MODULES)))

def plugin_catalog():
    registry = get_registry()
    return [dict(spec) for spec in registry.manifests.values()]

def is_enabled(organization, plugin_id, _seen=None, *, registry=None):
    registry = registry if registry is not None else get_registry()
    spec = registry.manifests.get(plugin_id)
    if not spec: return False
    seen = set(_seen or ())
    if plugin_id in seen: return False
    seen.add(plugin_id)
    state = PluginState.objects.filter(organization=organization, plugin_id=plugin_id).first()
    if state and state.version != spec["version"]: return False
    enabled = state.enabled if state else spec["default_enabled"]
    return enabled and all(is_enabled(organization, dependency, seen, registry=registry) for dependency in spec["requires"])

@transaction.atomic
def set_enabled(member, plugin_id, enabled):
    lock_company(member)
    if member.role != "owner": raise PermissionDenied("只有公司负责人可以启停插件")
    if type(enabled) is not bool: raise ValidationError("插件启停值无效")
    registry = get_registry()
    spec = registry.manifests.get(plugin_id)
    if not spec: raise ValidationError("插件未安装或不兼容，请先检查部署包")
    if enabled:
        for dependency in spec["requires"]:
            if not is_enabled(member.organization, dependency): raise ValidationError("请先启用依赖插件：" + registry.manifests[dependency]["name"])
    else:
        for key, other in registry.manifests.items():
            if plugin_id in other["requires"] and is_enabled(member.organization, key): raise ValidationError("请先停用依赖此插件的模块：" + other["name"])
    state, unused = PluginState.objects.update_or_create(organization=member.organization, plugin_id=plugin_id,
        defaults={"enabled": enabled, "version": spec["version"], "changed_by": member.user})
    if enabled:
        EventDelivery.objects.filter(event__organization=member.organization, plugin_id=plugin_id, status="blocked").update(next_retry_at=timezone.now())
    audit(member, "plugin.enabled" if enabled else "plugin.disabled", state, {"plugin_id": plugin_id, "version": spec["version"]})


def collect_insights(organization, snapshots, include_finance=True):
    registry = get_registry()
    results = []
    for plugin_id, spec in registry.manifests.items():
        if "insights:emit" not in spec["capabilities"] or not is_enabled(organization, plugin_id): continue
        for snapshot in snapshots:
            try:
                groups = registry.call_one(plugin_id, "project_insights", organization=organization, snapshot=copy.deepcopy(snapshot), include_finance=include_finance)
                validated = []
                for group in groups:
                    if not isinstance(group, list) or len(group) > 100: raise ValueError("invalid insights")
                    for item in group:
                        if not isinstance(item, dict) or set(item) != {"severity", "title", "detail", "url"}: raise ValueError("invalid insight")
                        if item["severity"] not in {"high", "medium", "info"}: raise ValueError("severity")
                        if any(not isinstance(item[k], str) for k in ["title", "detail", "url"]): raise ValueError("types")
                        if not 1 <= len(item["title"]) <= 160 or len(item["detail"]) > 1000: raise ValueError("length")
                        if not re.fullmatch(re.escape(snapshot["url"]) + r"(#[a-zA-Z0-9_-]{1,40})?", item["url"]): raise ValueError("source URL")
                        validated.append(item)
                results.extend(validated)
            except Exception as error:
                logger.error("插件提醒失败: %s (%s)", plugin_id, type(error).__name__)
                # Visible failure marker, scoped to an already-authorized project.
                results.append({"severity": "medium", "title": "项目检查暂时未完成", "detail": f"{spec['name']}执行失败，请在插件管理中检查状态。", "url": snapshot["url"]})
    return results[:500]


def dispatch_pending(limit=50):
    limit = max(1, min(int(limit), 500))
    counts = {"expanded": 0, "processed": 0, "retried": 0, "failed": 0, "blocked": 0}
    registry = get_registry()
    candidates = list(DomainEvent.objects.filter(expanded_at__isnull=True).order_by("id").values_list("id", "organization_id")[:limit])
    for event_id, org_id in candidates:
        with transaction.atomic():
            org = Organization.objects.select_for_update().get(pk=org_id)
            event = DomainEvent.objects.select_for_update().get(pk=event_id)
            if event.expanded_at: continue
            for plugin_id, plugin in registry.plugins.items():
                if hasattr(plugin, "event_received"):
                    EventDelivery.objects.get_or_create(event=event, plugin_id=plugin_id,
                        defaults={"status": "pending" if is_enabled(org, plugin_id) else "blocked"})
            event.expanded_at = timezone.now(); event.save(update_fields=["expanded_at"])
            counts["expanded"] += 1
    due = list(EventDelivery.objects.filter(status__in=["pending", "retry", "blocked"], next_retry_at__lte=timezone.now())
               .order_by("next_retry_at", "id").values_list("id", "event__organization_id")[:limit])
    for delivery_id, org_id in due:
        with transaction.atomic():
            org = Organization.objects.select_for_update().get(pk=org_id)
            delivery = EventDelivery.objects.select_for_update().select_related("event").get(pk=delivery_id)
            if delivery.status in {"done", "failed"} or delivery.next_retry_at > timezone.now(): continue
            if not is_enabled(org, delivery.plugin_id):
                delivery.status = "blocked"; delivery.next_retry_at = timezone.now() + timedelta(seconds=60)
                delivery.save(update_fields=["status", "next_retry_at"]); counts["blocked"] += 1
                continue
            delivery.attempts += 1
            try:
                event = {"event_id": str(delivery.event.event_id), "organization_id": org.pk,
                         "topic": delivery.event.topic, "data": delivery.event.data}
                groups = registry.call_one(delivery.plugin_id, "event_received", organization=org, event=event)
                result = groups[0] if groups else {"message": "事件已接收"}
                if not isinstance(result, dict) or not set(result) <= {"message", "topic", "event_id"} or len(json.dumps(result, ensure_ascii=False)) > 4000: raise ValueError("invalid event result")
                delivery.result = result
                delivery.status = "done"; delivery.processed_at = timezone.now(); delivery.last_error = ""
                counts["processed"] += 1
            except Exception as error:
                delivery.last_error = type(error).__name__
                delivery.status = "failed" if delivery.attempts >= 3 else "retry"
                delivery.next_retry_at = timezone.now() + timedelta(seconds=[60, 300, 1800][min(delivery.attempts - 1, 2)])
                counts["failed" if delivery.status == "failed" else "retried"] += 1
                logger.error("插件事件处理失败: %s (%s)", delivery.plugin_id, type(error).__name__)
            delivery.save(update_fields=["attempts", "result", "status", "processed_at", "last_error", "next_retry_at"])
    return counts
