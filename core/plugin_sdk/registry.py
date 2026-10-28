"""SDK-1 领域注册快照构建（PH-001 / PHC-SDK-1）。

``build_domain_registry(installed_descriptors)`` 先做全量图校验（重复
namespace/App label/路由、迁移依赖缺失/循环/影响链），再一次性构建
deep-immutable 快照；失败不留半注册，坏链上的插件不可用而无关项保留。
重复构建同一输入结果一致；同一冲突（namespace/app label）双方都拒绝，
不按加载顺序悄悄保留第一个。

descriptor 属于普通技术选择，契约在此冻结：``installed_descriptors`` 的每一项
是映射（或暴露同名属性的对象），键为：

- ``plugin_id``：领域命名空间，必须与 ``metadata["plugin_id"]`` 一致。
- ``metadata``：SDK-1 元数据字典（应已通过 ``validate_domain_metadata``）。
- ``manifest``：该插件恰好 8 字段的旧 manifest 字典。
- ``plugin``：不读取或保留活实例，快照仅含声明事实。
- ``catalog`` / ``installed_apps``：必填受控上下文，原样转交
  ``validate_domain_metadata`` 做语义复检；缺失明确拒绝。
  installed_apps可为label到模块字符串或严格{module, plugin_id}的部署索引。
  当前descriptor仅在已安装label/module匹配时补充身份，不将声明当安装证据。

纯函数：不 import descriptor 字符串、不访问网络、不写数据库/审计/事件。
错误按插件分组且脱敏，只含插件 ID 与固定类别代码。
"""
import copy
from types import MappingProxyType

from .metadata import MetadataError, PLUGIN_ID, normalize_installed_apps, validate_domain_metadata
from core.plugin_runtime import Registry


class DomainRegistry:
    """不可变领域注册快照：``domains`` 为可用项，``errors`` 按插件分组。"""

    __slots__ = ("_domains", "_errors")

    def __setattr__(self, name, value):
        raise AttributeError("immutable registry snapshot")

    @property
    def domains(self):
        return self._domains

    @property
    def errors(self):
        return self._errors

    def __init__(self, domains, errors):
        object.__setattr__(self, "_domains", _freeze(dict(sorted(domains.items()))))
        object.__setattr__(self, "_errors", MappingProxyType({
            key: tuple(sorted(set(codes))) for key, codes in sorted(errors.items())
        }))

    def is_available(self, plugin_id):
        return plugin_id in self.domains

    def routes(self, plugin_id):
        entry = self.domains.get(plugin_id)
        return entry["metadata"]["routes"] if entry else ()


def build_domain_registry(installed_descriptors):
    """校验全部 descriptor 并构建不可变注册快照；失败项进入 ``errors``。"""
    descriptors = list(installed_descriptors or ())
    candidates, errors, identities = [], {}, []
    for descriptor in descriptors:
        plugin_id = _field(descriptor, "plugin_id", None)
        key = plugin_id if type(plugin_id) is str and PLUGIN_ID.fullmatch(plugin_id) else "invalid_descriptor"
        raw = _field(descriptor, "metadata", None)
        label = raw.get("django_app", {}).get("label") if type(raw) is dict and type(raw.get("django_app")) is dict else None
        identities.append({"plugin_id": key, "label": label,
                           "module": raw.get("django_app", {}).get("module") if type(raw) is dict and type(raw.get("django_app")) is dict else None})
        try:
            metadata = validate_domain_metadata(
                _field(descriptor, "metadata", None),
                _field(descriptor, "manifest", None),
                _field(descriptor, "catalog", None),
                _deployment_context(descriptor, descriptors),
            )
            if metadata["plugin_id"] != plugin_id:
                raise MetadataError(["descriptor.plugin_id.mismatch"])
        except MetadataError as error:
            errors.setdefault(key, []).extend(error.codes)
            continue
        except Exception:
            errors.setdefault(key, []).append("descriptor.invalid")
            continue
        candidates.append({
            "plugin_id": plugin_id,
            "label": metadata["django_app"]["label"],
            "metadata": metadata,
            "manifest": copy.deepcopy(_field(descriptor, "manifest", None)),
        })

    available = {entry["plugin_id"]: entry for entry in candidates}
    _reject_duplicates(identities, available, errors)
    _check_route_uniqueness(available, errors)
    _check_migration_graph(available, candidates, errors)
    _check_manifest_graph(available, errors)
    return DomainRegistry(
        {plugin_id: _freeze(entry) for plugin_id, entry in available.items()}, errors
    )


def _freeze(value):
    """递归冻结：dict -> MappingProxyType，list -> tuple；快照不可被外部篡改。"""
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _field(descriptor, name, default):
    if isinstance(descriptor, dict):
        return descriptor.get(name, default)
    return getattr(descriptor, name, default)


def _reject_duplicates(candidates, available, errors):
    # 命名空间与 App label 冲突：双方都拒绝，不保留第一个。
    by_id = {}
    for entry in candidates:
        by_id.setdefault(entry["plugin_id"], []).append(entry)
    for plugin_id, entries in by_id.items():
        if len(entries) > 1:
            available.pop(plugin_id, None)
            errors.setdefault(plugin_id, []).append("duplicate_plugin_id")
    by_label = {}
    for entry in candidates:
        if type(entry["label"]) is str:
            by_label.setdefault(entry["label"], []).append(entry["plugin_id"])
    by_module = {}
    for entry in candidates:
        if type(entry["module"]) is str:
            by_module.setdefault(entry["module"], []).append(entry["plugin_id"])
    for groups, code in ((by_label, "duplicate_app_label"), (by_module, "duplicate_app_module")):
        for owners in groups.values():
            if len(owners) > 1:
                for plugin_id in owners:
                    available.pop(plugin_id, None)
                    errors.setdefault(plugin_id, []).append(code)


def _deployment_context(descriptor, descriptors):
    original = _field(descriptor, "installed_apps", None)
    if original is None:
        return None
    context = normalize_installed_apps(original)
    for peer in descriptors:
        metadata = _field(peer, "metadata", None)
        if type(metadata) is not dict or type(metadata.get("django_app")) is not dict:
            continue
        app = metadata["django_app"]
        label, module = app.get("label"), app.get("module")
        plugin_id = _field(peer, "plugin_id", None)
        if (type(label) is str and label in context and context[label]["module"] == module
                and type(plugin_id) is str and PLUGIN_ID.fullmatch(plugin_id)):
            # Explicit deployment identities are authoritative; infer only legacy string entries.
            if isinstance(original, dict) and type(original.get(label)) is dict:
                continue
            context[label] = {"module": module, "plugin_id": plugin_id}
    return context


def _check_route_uniqueness(available, errors):
    for plugin_id in list(available):
        names, paths, conflict = set(), set(), False
        for route in available[plugin_id]["metadata"]["routes"]:
            if route["name"] in names:
                conflict = True
            names.add(route["name"])
            # 挂载前缀由 plugin_id 派生；同域同 path 即使 name/access 不同也拒绝。
            mounted = f"{plugin_id}/{route['path']}"
            if mounted in paths:
                conflict = True
            paths.add(mounted)
        if conflict:
            available.pop(plugin_id)
            errors.setdefault(plugin_id, []).append("duplicate_route")


def _check_migration_graph(available, candidates, errors):
    by_label = {entry["label"]: entry["plugin_id"] for entry in candidates}
    declared = set(by_label)
    edges = {
        plugin_id: [by_label[d] for d in entry["metadata"]["migration_dependencies"]
                    if d in by_label and by_label[d] in available]
        for plugin_id, entry in available.items()
    }
    # 先找迁移依赖环：环上领域全部拒绝。
    cycle_nodes, color, path, stack = set(), dict.fromkeys(edges, 0), [], []
    for start in edges:
        if color[start]:
            continue
        color[start] = 1
        stack.append((start, iter(edges[start])))
        path.append(start)
        while stack:
            node, iterator = stack[-1]
            for dependency in iterator:
                if dependency not in edges:
                    continue
                if color[dependency] == 1:
                    cycle_nodes.update(path[path.index(dependency):])
                elif color[dependency] == 0:
                    color[dependency] = 1
                    stack.append((dependency, iter(edges[dependency])))
                    path.append(dependency)
                break
            else:
                color[node] = 2
                stack.pop()
                path.pop()
    for plugin_id in cycle_nodes:
        available.pop(plugin_id, None)
        errors.setdefault(plugin_id, []).append("migration_dependency_cycle")
    # 再按影响链收敛：依赖本批输入中已不可用领域的，一并拒绝。
    while True:
        dropped = {
            plugin_id
            for plugin_id, entry in available.items()
            if any(
                dependency in declared and by_label[dependency] not in available
                for dependency in entry["metadata"]["migration_dependencies"]
            )
        }
        if not dropped:
            return
        for plugin_id in dropped:
            available.pop(plugin_id)
            errors.setdefault(plugin_id, []).append("migration_dependency_missing")


def _check_manifest_graph(available, errors):
    """Reuse legacy version/graph rules without loading plugin code."""
    controlled = object.__new__(Registry)
    controlled.manifests = {key: entry["manifest"] for key, entry in available.items()}
    rejected = []
    for plugin_id in sorted(available):
        try:
            controlled.validate_graph(plugin_id)
        except Exception:
            rejected.append(plugin_id)
    for plugin_id in rejected:
        available.pop(plugin_id, None)
        errors.setdefault(plugin_id, []).append("manifest.dependency_graph")
