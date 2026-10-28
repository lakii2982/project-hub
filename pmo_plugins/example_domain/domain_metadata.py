"""SDK-1 领域元数据：与 docs/v1.0-development/promotion-draft/sdk1-metadata.example.json 一致。

只引用既定目录契约；本示例暂不声明命令/查询/事件与迁移依赖。
routes[].access 只是声明，实际访问控制由 SDK-2 统一门控执行。
"""
DOMAIN_METADATA = {
    "schema_version": "1",
    "plugin_id": "example_domain",
    "django_app": {
        "module": "pmo_plugins.example_domain",
        "label": "example_domain",
    },
    "contracts": {"commands": [], "queries": [], "events": []},
    "routes": [{"name": "overview", "path": "", "access": "business_read"}],
    "migration_dependencies": [],
}
