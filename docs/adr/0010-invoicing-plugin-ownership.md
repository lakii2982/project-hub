# ADR 0010：发票域归属——新建 pmo_plugins.invoicing 插件（推荐方案 A）

状态：PH-259 决策产出（推荐方案 A）；PH-252 实现前仍须规划 PR 复核并满足 PH-150/PH-155 done，本文不授权开工、不创建代码。日期：2026-10-31。

## 背景与约束（按实代码核对）

- 契约面已冻结且与归属无关：`docs/v1.0-development/contracts/` 已定义 `Invoice`/`InvoiceLine`/`InvoiceAllocation` 与 `invoicing.invoice.record/v1`、`invoicing.lines.allocate/v1`、`invoicing.invoice.reverse/v1` 及 `invoicing.recorded.v1`/`invoicing.lines_allocated.v1`/`invoicing.reversed.v1`。本 ADR 不改 contract schema。
- 当前插件运行时（`core/plugin_runtime.py` + `docs/plugin-architecture.md`）：manifest 恰好 8 字段（id/name/version/kernel_api/requires/default_enabled/capabilities/description），插件在 `settings.PMO_PLUGIN_MODULES` 固定清单中、随 Django app 一起迁移；**没有 URL/页面注册点**，现有插件页面由 `pmo_plugins/delivery/urls.py` 统一绑定；迁移随应用统一执行、无插件独立回滚链；capabilities 只是接口检查，不是授权引擎。
- 既有业务域插件先例：`contracts`、`costing`、`delivery`、`management_revenue`、`settlement`、`cash` 均为 `pmo_plugins.*` Django app + `plugin.py` manifest + `domain_metadata.py` + `delivery/urls.py` 绑定；新业务域按同一形态落地的成本最低、评审路径已验证。
- `docs/plugin-evolution.md`/WO-14 路线中，PH-001（SDK-1）、PH-002（SDK-2）与 PH-140（最小 SDK 注册/查询/全入口门控）**已交付**；但插件独立迁移链、URL/页面注册点、data namespace 与逐插件回滚仍**未交付**。任何方案都必须在当前冻结运行时内可运行，不能假设未实现能力。

## 选项

- **方案 A（推荐）：新建 `pmo_plugins.invoicing` 插件 app**，沿用 contracts/costing/management_revenue/settlement/cash 的既有形态；发票域自有 models/services/views/templates/migrations/domain_metadata/plugin.py。
- **方案 B1：并入 `management_revenue`**（复用其 app/迁移链）。
- **方案 B2：并入 `settlement`**（复用其 app/迁移链）。
- **方案 B3：并入 `contracts` 或 `delivery`**。
- **方案 C：暂缓发票域**，先完成 PH-150/PH-155/PH-162 等上游。

## 判据与逐项比较

| 判据 | A 新建 invoicing | B1 management_revenue | B2 settlement | B3 contracts/delivery | C 暂缓 |
| --- | --- | --- | --- | --- | --- |
| 域所有权/只读边界 | 发票文档+行+分配自有 owner，收入/结算只读 | 把发票文档混入收入确认 owner，两套事实耦合 | settlement 是未清/核销 owner，混入发票文档会扩张其语义 | contracts 是合同/收费明细 owner，delivery 是履约 owner，均非发票 | 不产生新事实 |
| 迁移隔离/回滚 | 自有迁移链（0001 起），发票表可独立回滚 | 与收入确认迁移耦合，回滚牵连收入 | 与未清/核销迁移耦合 | 与合同/履约迁移耦合 | — |
| manifest/requires 适配 | 8 字段可表达：requires contracts/settlement（若分配消费履约再加 delivery）；capabilities 空 | 需把 invoicing 能力塞进 management_revenue manifest，id 语义失真 | 同上 | 同上 | — |
| 路由/命令暴露 | 沿用现有模式：delivery/urls root 单写绑定 views；canonical `invoicing.*` 首片只走私有服务/浏览器，不新增 PAT/MCP | 同模式但 URL 归属错位 | 同左 | 同左 | — |
| SDK/插件演进兼容 | 不依赖未交付能力（无 URL 注册点、无独立迁移链也能落地）；未来 WO-14 交付可平滑升级 | 同 | 同 | 同 | — |
| 测试/部署/回滚成本 | 新 app 一次 bootstrap（settings/PMO_PLUGIN_MODULES/delivery urls root 单写），后续独立演进；停用保留历史 | 复用 app 少一次注册，但域耦合导致测试/回滚成本长期更高 | 同 | 同 | 无实现成本但阻塞 WP-INVOICE/收入配对 |
| core 膨胀/架构一致性 | 与既有业务域插件一致 | 违反 owner 边界，易把发票查询塞进收入读模型 | 违反 settlement 契约边界 | 违反 contracts/delivery 边界 | — |

## 决定

采用**方案 A：新建 `pmo_plugins.invoicing` 插件 app**，作为发票域的写 owner；契约 schema 不变，收入（management_revenue）、结算（settlement）、合同（contracts）与现金（cash）保持各自 owner，只通过具名只读接口被发票域消费。

实现约束（由 PH-252 实现 PR 落地，本 ADR 不建 scaffold）：

1. **app 形态**：`pmo_plugins/invoicing/{apps.py,plugin.py,domain_metadata.py,models.py,services.py,views.py,forms.py,templates/invoicing/*,migrations/0001_initial.py}`；`plugin.py` manifest 恰好 8 字段：`id='invoicing'`、`name='发票'`、`version='0.1.0'`、`kernel_api='>=1.0,<2'`、`requires={'contracts': '>=0.1.0', 'settlement': '>=0.1.0'}`（若分配消费履约来源再加 `delivery`）、`default_enabled=True`、`capabilities=[]`、`description` 简述发票登记/行/分配。
2. **注册**：`pmo/settings.py` 的 `INSTALLED_APPS` 与 `PMO_PLUGIN_MODULES` 各加一项；`pmo_plugins/delivery/urls.py` 绑定 views；上述三处为 root 单写（PH-252 白名单已列明）。
3. **迁移 owner**：invoicing app 自有迁移链（首号 `0001_initial`），随部署统一执行；不改其它 app 已应用迁移；回滚按 app 迁移单元执行；停用插件保留历史数据与事件队列（沿用现有 PluginState 语义）。
4. **命令暴露**：`invoicing.invoice.record/v1`、`invoicing.lines.allocate/v1`、`invoicing.invoice.reverse/v1` 首片仅私有服务/浏览器适配承载，不新增公开 PAT/MCP/支付/关期工具；若未来要暴露，另立规划决策，不在实现 PR 自我授权。
5. **依赖顺序**：PH-252 实现须等 PH-150（合同/收费明细）与 PH-155（客户结算/未清）done；PH-252 当前保持 proposed，本 ADR 不改变其状态。
6. **不预设**：本 ADR 不创建 app scaffold、不改 contract schema、不写业务代码/迁移；PH-252 的实现 PR 负责落地并按 tasks 契约白名单执行。

## 影响面

- PH-252：decision point 已引用本 ADR（方案 A）；tasks allowed_paths/spec 精确白名单已同步补入 bootstrap 文件 `pmo_plugins/invoicing/{__init__.py,apps.py,plugin.py}`（实现 PR 按本 ADR 创建 scaffold 与迁移 `0001_initial`），`pmo/settings.py`/`pmo/urls.py`/`pmo_plugins/delivery/urls.py` 等 root 单写项 PH-252 白名单已含；PH-252 状态仍为 proposed、范围不变。
- 其它 owner：contracts/settlement/delivery/management_revenue/cash 不改契约与代码；只读接口由 PH-252 按其白名单新增/消费。
- WO-14/SDK：本决定不新增 SDK 契约，不假设未交付能力；未来插件独立迁移链/URL 注册点交付后可平滑迁移。
- 风险：新 app 增加一次 bootstrap 注册成本；若未来决定发票与收入合并读模型，只允许读模型投影，不合并写 owner。

## 放弃的备选与理由

- **B1 management_revenue**：发票文档/行/分配是独立业务事实，与收入确认的确认时点/方法互斥规则不同；混入会使收入 owner 扩张为“收入+发票”双域，迁移/回滚/测试耦合，违反 ADR 0003 的 owner 读模型边界。
- **B2 settlement**：settlement 拥有未清/核销/账龄；发票是开票文档而非未清事实，混入会制造第二套“可开票余额/已开票”语义，违反其冻结契约。
- **B3 contracts/delivery**：contracts 是合同/收费明细，delivery 是履约/验收；发票文档与分配不是二者子域，混入会扩大冻结契约的写 owner。
- **C 暂缓**：会阻塞 WP-INVOICE 与后续收入/结算配对（PH-260）及 1.0 需求；且 PH-150/PH-155 是 PH-252 的既有依赖，不需要以暂缓实现来等待。

## 后续

- PH-252 按本 ADR 方案 A 实现首片（发票登记+行+逐行分配），并同步更新其 spec 的白名单/迁移号与实现报告。
- 若实现中发现 requests 需要额外 owner 只读或 requires 调整，报告披露并以新规划 PR 修订，不在实现 PR 自我扩权。
