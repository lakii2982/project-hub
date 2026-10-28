"""SDK-1 独立示例插件：恰好 8 字段旧 manifest，默认停用，无 hook、无业务表。

示例单独升级版本时不改变 domain_metadata 的 schema_version；
仅当已有 PluginState 且 version 与新 manifest 不同才按旧规则停用。
"""
class Plugin:
    manifest = {
        "id": "example_domain",
        "name": "示例领域",
        "version": "0.1.0",
        "kernel_api": ">=1.0,<2",
        "requires": {},
        "default_enabled": False,
        "capabilities": [],
        "description": "SDK-1 独立示例插件：仅演示领域元数据，无业务表与路由接线。",
    }
