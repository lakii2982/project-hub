# WO-14 业务插件 SDK 与渐进抽离

- 级别：S；状态：待执行；依赖：现有插件骨架；先于 WO-01 至 WO-09 的新业务实现。

## 目标与输入

依照 ../plugin-evolution.md 第3至5节，把现有叶插件机制扩展为有独立 App、路由与服务入口的业务插件机制。输入：core/plugin_runtime.py、core/access.py、pmo/settings.py、pmo/urls.py、tests/test_plugins.py 和当前插件 manifest 契约。现有 8 字段 manifest 的变化必须显式版本化；不得把未来字段偷偷混入 1.0。

## 分为四张小单串行验收

1. SDK-1：冻结命令/查询/路由/迁移元数据 schema，建立独立示例 App；先写非法 schema、重复命名空间、缺依赖反例。
2. SDK-2：统一启停 guard；页面、API、CSV、MCP、后台和内部服务直接调用均受控。停用不影响撤销令牌、账本与历史凭证读取。
3. SDK-3：迁移检查、包版本兼容、历史来源摘要和事件重放契约；禁止插件迁移修改其他 App/内核表。
4. SDK-4：只抽离 delivery 的策略/页面为试点，保留旧表与主键、旧 URL 适配；新旧流程和停用巡检通过后再切换 time。不得在此单同时改核算口径。

## 文件所有权与禁止范围

主代理先冻结 SDK 契约与接线点；执行者分别拥有 core/plugin_sdk/、pmo_plugins/example_domain/、pmo_plugins/delivery/ 与各自 tests，不共享写范围。pmo/settings.py、pmo/urls.py 和导航由集成者接线。禁止删除旧表、重写历史金额、绕过 append-only、放宽既有权限或测试。迁移由集成者按当前叶节点生成，禁止预占编号。

## 验收

全量测试与迁移漂移检查通过；增加停用后入口直接调用和整站巡检；启用前后及停用后 verify_integrity 一致；示例插件可独立升级且另一插件的源码哈希不变。PostgreSQL 迁移和并发验证在有环境后单列证据。

交付采用 README.md 的 JSON schema。此单是领域工作单的前置条件，不能仅更新设计文档后标为完成。
