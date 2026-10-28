"""业务插件 SDK 最小薄片（SDK-1，PH-001）。

只冻结两个接口：领域元数据校验与领域注册快照构建。
运行时门控、静态路由接线与迁移扫描分别由 SDK-2/3 承接。
"""
from .metadata import SCHEMA_VERSION, MetadataError, validate_domain_metadata
from .registry import DomainRegistry, build_domain_registry

__all__ = [
    "SCHEMA_VERSION",
    "MetadataError",
    "validate_domain_metadata",
    "DomainRegistry",
    "build_domain_registry",
]
