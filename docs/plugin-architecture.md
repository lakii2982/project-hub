# 插件架构设计

任务ID：PMO-ENGINEERING-01
日期：2026-10-22
地位：已确认需求。目标是稳定 kernel 与可替换 business plugin：某部分需求变更或扩展只重构该插件，不动 kernel 与其他插件。形态参考 deepseek harness（github.com/deepseek-ai/deepseek-harness，2026-10-22 核验）的受控能力注册与 Atlassian 插件框架（developer.atlassian.com/server/framework/atlassian-sdk/plugin-framework/，同日核验）的清单、启停与版本兼容模式，不复制其源码。本文以 core/plugin_runtime.py 实代码为准区分「当前冻结契约」与「未实现目标」，不混写。

## 1. 当前已交付（实代码核对）

- 运行时：core/plugin_runtime.py，基于 pluggy（pluggy==1.7.0）。入口只有 `settings.PMO_PLUGIN_MODULES` 固定模块列表（默认 pmo_plugins 下三个模块），每个模块提供无参 `Plugin()` 实例与 `manifest` 字典；进程启动时经 `get_registry()` 注册，加载失败的模块记入 `registry.errors` 并隔离，不影响无关插件。
- 钩子：`project_insights(snapshot, include_finance)` 与 `event_received(event)` 两个 hookspec。
- 管理：/plugins/ 页面（core/plugin_views.py）；`is_enabled(organization, plugin_id)`、`set_enabled(member, plugin_id, enabled)`（仅 owner，依赖强制，写审计）、`collect_insights(...)`、`dispatch_pending(limit)`。
- 每公司启停：PluginState（迁移 0004，另含 DomainEvent、EventDelivery）。
- 叶插件：project_health、delivery_watch、agent_gateway（停用后阻断 api/v1 与新令牌签发、允许撤销）。
- 证据：tests/test_plugins.py 12 项全部通过（方法名见 delivery-status 2.8）；真实 outbox 命令运行一次（expanded 1、processed 1、无失败）。

## 2. 当前冻结的 v1 manifest 契约

逐字段对应 `Registry.validate`（core/plugin_runtime.py）。manifest 是插件实例上的字典，不是 plugin.json 文件；字段必须恰好 8 个，多一个少一个都拒绝：

| 字段 | 类型与校验 |
| --- | --- |
| id | 字符串，正则 `^[a-z][a-z0-9_]{1,79}$`，全注册表唯一 |
| name | 非空字符串 |
| version | 非空字符串，必须可被 packaging.Version 解析 |
| kernel_api | 非空字符串，SpecifierSet；当前 kernel `KERNEL_API = "1.0"` 必须落在其中（即插件须声明兼容 `>=1.0,<2` 一类范围），不兼容拒绝注册 |
| requires | 字典，键为其他插件 id、值为 SpecifierSet 字符串；`validate_graph` 检查依赖已安装、版本落在约束内、无循环；违例插件及其依赖方被摘除，无关插件不受影响 |
| default_enabled | 必须严格为 bool（公司无 PluginState 行时的默认启停值） |
| capabilities | 字符串列表；当前实际使用的取值：`project:read_snapshot`、`insights:emit`、`personal_agent_api` |
| description | 非空字符串 |

冻结语义：

- capabilities 是声明与接口检查清单：`collect_insights` 只调用声明 `insights:emit` 的插件，agent_gateway 的启停门控针对 `personal_agent_api`。它不是逐项运行时强制的授权引擎，插件代码仍是同进程可信 Python。
- 启停判定（`is_enabled`）：无状态行取 default_enabled；状态行 version 与当前 manifest version 不符视为停用，直到 owner 再启用确认该版本；依赖递归检查（带循环保护）。
- 启停动作（`set_enabled`）：仅 owner；启用前要求全部依赖已启用；停用前要求无已启用的依赖方；重新启用时把该插件 blocked 的 EventDelivery 重新排期；全程公司行锁加审计事件。
- 安装与升级是部署流程：代码随部署包进入 `PMO_PLUGIN_MODULES`，迁移随应用迁移执行，需重启进程。不存在自动安装、自动卸载或 Web 上传。uninstall 概念当前不存在，只有停用。

## 3. 事件与 outbox（真实语义）

- 产生：`services.audit()` 在同一业务事务内写入 DomainEvent，`topic = 审计 action + ".v1"`（例如 `document.approved.v1`），业务回滚则事件同时消失（有测试）。
- 展开：`dispatch_pending` 对未展开事件按注册插件建 EventDelivery（get_or_create 幂等），插件当时停用则状态为 blocked 但保留队列（有测试）。
- 投递：到期投递调用 `event_received`，输入只含 event_id、organization_id、topic、data 的安全字典；返回值只接受 `{"message", "topic", "event_id"}` 子集且序列化后不超过 4000 字符，存入 EventDelivery.result。它是一张有界回执，不是通知发送器，处理器不得改账。
- 重试：失败按 60/300/1800 秒退避，最多 3 次后 failed（有测试）；投递幂等去重（有测试）。
- 洞察通道：`collect_insights` 传入深拷贝的已授权快照，逐条校验 severity/title/detail/url，url 必须属于该项目快照地址（可加锚点），失败插件被隔离并在该项目下显示一条可见失败标记（有测试）。

## 4. 边界声明（不宣称）

1. 同进程可信插件共享解释器与数据库连接。当前机制是受控安装（固定模块清单）加接口检查（manifest、钩子签名、输出校验），不是沙箱，也不是完整授权引擎；不宣称 capabilities 逐项运行时强制。
2. 当前不存在：plugin.json 文件及其扩展字段、data namespace 与表名前缀强制、插件独立迁移链、迁移静态扫描器、URL/页面注册点、PluginRecord 模型、`require_plugin` 服务层函数、CI 双版本兼容 runner、自动安装/卸载。这些是目标能力，见第 5 节，均未交付。
3. 启停（enable/disable）免重启即刻生效；安装与升级需部署加重启，不伪称热装。
4. 卸载不删除历史数据；当前也无卸载动作，业务表、账本、审计、事件一律保留。
5. 任意第三方插件不允许 Web 上传执行。第三方只能走隔离服务（独立进程/机器）经受限通道（api/v1 加 PAT）交互。
6. core 业务纵线（项目、工时、单据、账本）仍在 kernel 内，未拆分为插件；不宣称全部业务已可拔插。

## 5. 未实现目标（与当前契约分离）

以下为方向性设计，任何一项落地前不写入验收：

具体模块目录、接口所有权、启停升级语义、历史数据保留与渐进抽离顺序见 [业务插件化落地设计](plugin-evolution.md)，实施前置单为 [WO-14](work-orders/WO-14-domain-plugin-sdk.md)。

| 目标 | 与现状的差距 |
| --- | --- |
| plugin.json 文件与扩展字段（depends、scopes 枚举、migrations、data_namespace、per_company_toggle） | 当前只有实例 manifest 字典 8 字段 |
| scopes 逐项运行时强制 | 当前 capabilities 只做接口检查 |
| 数据 namespace（表名前缀、迁移不得碰 kernel 表）与静态扫描 | 无机制，靠评审 |
| 插件独立迁移链与逐插件回滚 | 迁移随应用统一执行 |
| 页面/API/settings 注册点 | 插件不能注册 URL，页面由 kernel 提供 |
| CI 兼容矩阵（kernel × 插件最近两版本跑契约测试） | 无 runner |
| 事件主题命名空间与 payload 版本化治理 | 当前 topic 为审计 action 加 .v1 单版本 |
| core 业务插件化抽离 | 未开始，随 WO-01/02/07/09 评估 |

## 6. 可拔插验收现状

已有测试覆盖（test_plugins.py）：公司隔离与默认值、owner 启停与依赖强制、版本不匹配停用待确认、kernel 不兼容与依赖循环隔离、洞察快照裁剪、失败/改数据插件隔离、不可信 URL 拒绝、业务回滚事件消失、投递幂等与停用保队列、重试上限、agent_gateway 停用阻断。

尚未覆盖（随第 5 节目标补）：停用插件后核心全页面无 500 的整站巡检、事件跨版本重放治理、迁移逐插件回滚、CI 兼容矩阵。这些通过前，不说「可任意拔插」。



## PH-001 · 领域元数据SDK-1

格式已由PH137冻结，最小实现和受控descriptor上下文见[ADR0002](adr/0002-domain-plugin-sdk.md)。旧八字段manifest与新六字段domain_metadata分别校验，namespace与表前缀从唯一身份派生，契约引用canonical目录。注册快照不可变，冲突双方及坏依赖链拒绝；旧Registry/叶插件/数据库不改。示例默认停用且不接生产settings/URL。本片没有实现统一入口权限、动态路由、迁移扫描或财务模块；这些仍按SDK-2/3和后续领域薄片交付。
