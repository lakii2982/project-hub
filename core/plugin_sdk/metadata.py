"""SDK-1 领域插件元数据校验（PH-001 / PHC-SDK-1）。

结构校验与冻结的 ``docs/v1.0-development/promotion-draft/sdk1-metadata.schema.json``
等价；语义校验覆盖 plugin_id 一致、AppConfig 匹配、契约 ID 类别与迁移依赖声明。
纯函数：不 import 元数据字符串指向的模块、不访问网络、不写数据库/审计/事件；
``routes[].access`` 只是声明，本模块从不声称访问控制已经执行。

调用者提供受控上下文（全部来自发行包，不接受 URL/上传/请求参数）：

- ``manifest``：该插件现有且恰好 8 字段的旧 manifest 字典，由
  ``core.plugin_runtime.Registry.validate`` 复用校验版本/内核/依赖。
- ``catalog``：规范契约目录，即解析后的 ``contract-catalog.json`` 字典或其
  ``contract_catalog`` 列表；缺失上下文明确拒绝。
- ``installed_apps``：``label -> module path`` 或 ``label -> {module, plugin_id}``
  的受控部署索引（字符串值以label作为旧依赖身份），或暴露 ``label``/``name``
  属性的 AppConfig 可迭代序列；缺失上下文明确拒绝。

校验通过返回元数据的深拷贝副本；失败抛出 :class:`MetadataError`，
``codes`` 只含固定类别代码，不携带输入值、环境变量或凭据。
"""
import copy
import re

from core.plugin_runtime import Registry

SCHEMA_VERSION = "1"
PLUGIN_ID = re.compile(r"[a-z][a-z0-9_]{1,79}")
APP_MODULE = re.compile(r"pmo_plugins\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*")
APP_LABEL = re.compile(r"[a-z][a-z0-9_]*")
ROUTE_PATH = re.compile(r"(?:[a-z][a-z0-9_-]*/)*")
ROUTE_ACCESS = ("business_read", "business_write", "history_read")
CONTRACT_GROUPS = ("commands", "queries", "events")
CONTRACT_ID_MIN, CONTRACT_ID_MAX = 1, 200
MAX_CONTRACTS, MAX_ROUTES, MAX_MIGRATION_DEPENDENCIES = 128, 64, 64
# 受控共享App：迁移依赖可直接指向它们；业务App迁移依赖必须同时在 manifest.requires 声明。
SHARED_APPS = frozenset({
    "core", "auth", "contenttypes", "admin", "sessions", "messages", "staticfiles",
})
TOP_LEVEL_FIELDS = frozenset({
    "schema_version", "plugin_id", "django_app", "contracts", "routes", "migration_dependencies",
})


class MetadataError(ValueError):
    """元数据被拒绝；``codes`` 为固定类别代码列表（脱敏，不含输入值）。"""

    def __init__(self, codes):
        self.codes = tuple(codes)
        super().__init__("; ".join(self.codes))


def normalize_catalog(catalog):
    """把规范目录归一化为 ``类别 -> ID 集合``；``None`` 原样返回。"""
    if catalog is None:
        return None
    entries = catalog.get("contract_catalog") if isinstance(catalog, dict) else catalog
    ids = {group: set() for group in CONTRACT_GROUPS}
    for entry in entries or ():
        for group in CONTRACT_GROUPS:
            ids[group].update(entry.get(group) or ())
    return ids


def normalize_installed_apps(installed_apps):
    """归一化受控部署索引为label -> {module, plugin_id}；不导入模块。"""
    if installed_apps is None:
        return {}
    source = installed_apps if isinstance(installed_apps, dict) else {
        app.label: app.name for app in installed_apps
    }
    result = {}
    for label, value in source.items():
        if type(label) is not str or not APP_LABEL.fullmatch(label):
            raise ValueError("invalid installed app label")
        if type(value) is str:
            result[label] = {"module": value, "plugin_id": label}
        elif (type(value) is dict and set(value) == {"module", "plugin_id"}
              and type(value["module"]) is str
              and type(value["plugin_id"]) is str
              and PLUGIN_ID.fullmatch(value["plugin_id"])):
            result[label] = dict(value)
        else:
            raise ValueError("invalid installed app identity")
    return result


def validate_domain_metadata(metadata, manifest, catalog=None, installed_apps=None):
    """校验领域元数据并返回深拷贝副本；拒绝原因只含类别代码。"""
    if type(metadata) is not dict:
        raise MetadataError(["metadata.type"])
    codes: list[str] = []
    if set(metadata) != TOP_LEVEL_FIELDS:
        codes.append("metadata.fields")
    else:
        _check_schema_version(metadata["schema_version"], codes)
        _check_plugin_id(metadata["plugin_id"], codes)
        _check_django_app(metadata["django_app"], codes)
        _check_contracts(metadata["contracts"], codes)
        _check_routes(metadata["routes"], codes)
        _check_migration_dependencies(metadata["migration_dependencies"], codes)
    if codes:
        raise MetadataError(codes)

    if catalog is None or installed_apps is None:
        raise MetadataError(["metadata.context.required"])
    try:
        _check_semantics(metadata, manifest, catalog, installed_apps, codes)
    except Exception:
        raise MetadataError(["metadata.context.invalid"]) from None
    if codes:
        raise MetadataError(codes)
    return copy.deepcopy(metadata)


def _check_semantics(metadata, manifest, catalog, installed_apps, codes):
    try:
        spec = Registry.validate(manifest)
    except Exception:
        codes.append("manifest.invalid")
        return
    if metadata["plugin_id"] != spec["id"]:
        codes.append("metadata.plugin_id.mismatch")
    catalog_ids = normalize_catalog(catalog)
    if catalog_ids is not None:
        for group in CONTRACT_GROUPS:
            for contract_id in metadata["contracts"][group]:
                if contract_id not in catalog_ids[group]:
                    codes.append(f"metadata.contracts.{group}.unknown")
    if installed_apps is not None:
        apps = normalize_installed_apps(installed_apps)
        label = metadata["django_app"]["label"]
        module = metadata["django_app"]["module"]
        if label not in apps:
            codes.append("metadata.django_app.label.not_installed")
        elif apps[label]["module"] != module:
            codes.append("metadata.django_app.module.mismatch")
        for dependency in metadata["migration_dependencies"]:
            if dependency == label:
                codes.append("metadata.migration_dependencies.self")
            elif dependency not in apps:
                codes.append("metadata.migration_dependencies.not_installed")
            elif dependency not in SHARED_APPS and apps[dependency]["plugin_id"] not in spec["requires"]:
                codes.append("metadata.migration_dependencies.undeclared")


def _check_schema_version(value, codes):
    # schema_version 是严格字符串 "1"；整数/bool（True 是 int 实例）都不接受。
    if type(value) is not str or value != SCHEMA_VERSION:
        codes.append("metadata.schema_version")


def _check_plugin_id(value, codes):
    if type(value) is not str or not PLUGIN_ID.fullmatch(value):
        codes.append("metadata.plugin_id")


def _check_django_app(value, codes):
    if type(value) is not dict or set(value) != {"module", "label"}:
        codes.append("metadata.django_app.fields")
        return
    if type(value["module"]) is not str or not APP_MODULE.fullmatch(value["module"]):
        codes.append("metadata.django_app.module")
    if type(value["label"]) is not str or not APP_LABEL.fullmatch(value["label"]):
        codes.append("metadata.django_app.label")


def _check_contracts(value, codes):
    if type(value) is not dict or set(value) != set(CONTRACT_GROUPS):
        codes.append("metadata.contracts.fields")
        return
    for group in CONTRACT_GROUPS:
        items = value[group]
        if type(items) is not list:
            codes.append(f"metadata.contracts.{group}.type")
            continue
        if len(items) > MAX_CONTRACTS or len({repr(item) for item in items}) != len(items):
            codes.append(f"metadata.contracts.{group}.unique")
            continue
        if any(
            type(item) is not str or not CONTRACT_ID_MIN <= len(item) <= CONTRACT_ID_MAX
            for item in items
        ):
            codes.append(f"metadata.contracts.{group}.items")


def _check_routes(value, codes):
    if type(value) is not list:
        codes.append("metadata.routes.type")
        return
    if len(value) > MAX_ROUTES:
        codes.append("metadata.routes.limit")
        return
    for route in value:
        if type(route) is not dict or set(route) != {"name", "path", "access"}:
            codes.append("metadata.routes.fields")
            continue
        if type(route["name"]) is not str or not APP_LABEL.fullmatch(route["name"]):
            codes.append("metadata.routes.name")
        # path 是静态相对段（可为空）；本薄片不支持 converter、正则或可执行 handler。
        if type(route["path"]) is not str or not ROUTE_PATH.fullmatch(route["path"]):
            codes.append("metadata.routes.path")
        if route["access"] not in ROUTE_ACCESS or type(route["access"]) is not str:
            codes.append("metadata.routes.access")


def _check_migration_dependencies(value, codes):
    if type(value) is not list:
        codes.append("metadata.migration_dependencies.type")
        return
    if any(type(item) is not str or not APP_LABEL.fullmatch(item) for item in value):
        codes.append("metadata.migration_dependencies.items")
        return
    if len(value) > MAX_MIGRATION_DEPENDENCIES or len(set(value)) != len(value):
        codes.append("metadata.migration_dependencies.unique")
