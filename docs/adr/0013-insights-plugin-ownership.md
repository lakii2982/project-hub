# ADR 0013：insights 域归属与期间核对/读模型契约——新建 pmo_plugins.insights 插件（推荐方案 A）

状态：PH-270 决策产出（推荐方案 A）；期间核对/工作台读模型实现由后续规划槽按本 ADR 冻结（PH-272 等），本文不授权开工、不创建代码。日期：2026-10-31。

## 背景与已冻结契约（按实代码/契约核对）

- canonical 实体已冻结且 owner 明确：`ReconciliationCase`（`x-owner=insights`、`x-record-kind=owned_fact`；status 枚举 draft/pending/approved/rejected/superseded/closed，required=source_refs/difference_cents/owner_member_id/resolution_note，readOnly=case_key/rule_code/coverage_key/snapshot_watermark，additionalProperties=false）与 `MetricDefinition`（`x-owner=insights`、`x-record-kind=read_projection`）；读模型/指标/差异单只能在 insights 域内定义，业务事实仍属各 owner。
- canonical 命令/事件：`insights.reconciliation.refresh/v1`、`insights.reconciliation.case.update/v1`、`insights.export.create/v1`；事件 `insights.reconciliation_refreshed.v1`、`insights.reconciliation_case_updated.v1`、`insights.export_ready.v1`。本文不改 contract schema。
- 当前没有 insights 插件 App；现有 `pmo_plugins`：contracts/costing/delivery/management_revenue/settlement/cash 等。迁移当前叶：core 0010、contracts 0003、cash 0006、settlement 0005、costing 0004（新 app 迁移不得抢号）。
- 插件运行时（`core/plugin_runtime.py`/`docs/plugin-architecture.md`）：manifest 恰好 8 字段、插件固定列在 `settings.PMO_PLUGIN_MODULES`、无 URL/页面注册点（页面由 `pmo_plugins/delivery/urls.py` 绑定）、无插件独立迁移链；PH-001/PH-002/PH-140 SDK 注册/入口门控已交付。
- 只读来源 owner：cash（现金/分类桥接）、settlement（核销/未清/账龄）、costing（成本/人工池）、contracts（合同/收费模板）、management_revenue（收入确认）；期间状态 owner：`AccountingPeriod`/`Organization.locked_through`（core）。
- legacy 上游：PH-180（期间预检/锁定）、PH-181（四类核对/差额）、PH-182（经营指标/缺口）、PH-183（到期扫描/待办）仍 proposed；PH-257（期间核对）、PH-262（工作台读模型）、PH-272（实现首片）均 proposed。

## 选项

- **方案 A（推荐）：新建 `pmo_plugins.insights` 插件 app**，作为 insights 域写 owner（只读读模型快照 + 核对差异单），canonical x-owner=insights 一致。
- **方案 B：复用 `delivery`/`management_revenue` 等既有插件**承载读模型/核对。
- **方案 C：放进 core 只读服务**（kernel 内新增 insights 表/入口）。
- **方案 D：暂缓 insights 域**。

## 判据与逐项比较

| 判据 | A 新建 insights | B 复用既有插件 | C core | D 暂缓 |
| --- | --- | --- | --- | --- |
| canonical owner 一致性 | 与 x-owner=insights 一致 | owner 语义错位（delivery 履约、revenue 收入确认） | core 是 kernel，非读模型 owner | — |
| 迁移 isolation/抢号 | insights 自有 0001，不抢 core 0010/contracts 0003/cash 0006/settlement 0005/costing 0004 | 与承载插件迁移耦合 | 改 core 迁移链，风险高 | — |
| manifest/requires | 8 字段：requires={cash,settlement,costing,contracts}（只读消费） | 可表达但语义错位 | 不适用 | — |
| 入口门控/路由 | delivery/urls root 单写 + domain_metadata access；`insights.*` 具名服务/浏览器 | 同模式但入口归属错位 | kernel 入口膨胀 | — |
| 只读边界 | 读模型/差异单只追加，不写业务事实；period 只读 | 同左 | 同左 | 阻塞 WP-CLOSE/READ-MODEL |
| 与既有 owner 关系 | 只经具名只读接口派生，不复制业务表/不建可编辑余额 | 同左 | 同左 | — |
| 测试/部署/回滚 | 新 app 一次 bootstrap；停用保留历史 | 与承载插件发布耦合 | kernel 风险 | — |

## 决定

采用**方案 A：新建 `pmo_plugins.insights` 插件 app**，作为 insights 域写 owner（期间核对差异单/读模型/指标定义）；只读派生，不写业务事实，不改 contract schema，不实现业务代码。

实现约束（由后续规划槽冻结的实现片落地，本 ADR 不建 scaffold）：

1. **app 形态**：`pmo_plugins/insights/{__init__.py,apps.py,plugin.py,domain_metadata.py,models.py,reconciliation_services.py,read_model_services.py,views.py,forms.py,templates/insights/*,migrations/0001_initial.py}`；`plugin.py` manifest 恰好 8 字段：`id='insights'`、`name='洞察与核对'`、`version='0.1.0'`、`kernel_api='>=1.0,<2'`、`requires={'cash': '>=0.1.0', 'settlement': '>=0.1.0', 'costing': '>=0.1.0', 'contracts': '>=0.1.0'}`、`default_enabled=True`、`capabilities=[]`、`description` 简述只读读模型与期间核对。
2. **注册**：`pmo/settings.py`（INSTALLED_APPS + PMO_PLUGIN_MODULES）+ `pmo_plugins/delivery/urls.py` root 单写；domain_metadata 声明 `plugin_id`/access 元数据（owner/finance 写核对单；auditor 只读；pm 只读其项目；member 拒绝；模块停用写拒绝、历史读保留）。
3. **迁移 owner**：insights 自有迁移链首号 `0001_initial`；**不改** core 0010/contracts 0003/cash 0006/settlement 0005/costing 0004 等已应用迁移；不抢号；停用保留历史。
4. **入口/命令暴露**：`insights.reconciliation.refresh/v1`、`insights.reconciliation.case.update/v1` 首片仅具名服务/浏览器工作流；`insights.export.create/v1` 留后续（导出不在首片）；**不新增** PAT/MCP/个人 AI 审批/支付/关期工具；拒绝客户端传入差异金额/余额/证明。
5. **只读边界**：读模型（MetricDefinition/项目汇总/经营指标快照）与 ReconciliationCase 只追加，不改写业务事实，不建可编辑余额/第二套账；期间预检只读 `AccountingPeriod`/`Organization.locked_through` 与既有 owner 具名接口，不执行锁定/关期。
6. **与既有 owner 关系**：cash/settlement/costing/contracts/management_revenue 只读消费；不复制其表、不改其契约；差异金额服务端派生，差异单仅记录与跟踪，不自动更正/不做调整分录；不写账本/收入/成本/现金/未清。
7. **状态/幂等/并发**：ReconciliationCase 严格使用 canonical 字段与状态机 draft→pending→approved/rejected，approved 后可 closed，刷新取代旧快照时旧 case 置 superseded（不引入 open/assigned/resolved/dismissed 等非 canonical 状态/字段；additionalProperties=false）。业务操作映射：预检生成=draft、分派/待办=pending+owner_member_id、确认通过/驳回=approved/rejected+resolution_note、结案=closed、刷新替代=superseded；期间/口径通过服务端派生的 case_key/coverage_key/rule_code 与 source_refs 表达，不新增 period/scope/category/assignee/due_date/note 列。request_token 收据、公司锁、同 case_key 并发 refresh 只一胜；具体反例由 PH-272 实现片 DoR 冻结。
8. **PH-257/PH-262/PH-272**：owner 统一为新建 `pmo_plugins.insights`；PH-257/PH-262 的 decision point 与 blocker 指向本 ADR；PH-272 按本 ADR 填充 owner/白名单（其 spec 已列出 bootstrap/文件与 settings/urls）；三者仍 proposed，待 PH-180/PH-181/PH-182/PH-183 done 且规划 PR 复核后才可 ready。

## 影响面

- PH-257（期间核对）：decision point/blocker 改为引用 ADR 0013；spec 白名单与 tasks allowed_paths 同步补 `pmo_plugins/insights/__init__.py`、`apps.py`、`plugin.py`、`pmo/settings.py`、`pmo/urls.py`（bootstrap 可执行）；仍 proposed（缺 PH-180/PH-181）。
- PH-262（工作台读模型）：同样同步 owner/白名单 bootstrap；仍 proposed（缺 PH-182/PH-183）。
- PH-272（实现首片）：owner 引用统一为 insights 插件；白名单已含 bootstrap 与 settings/urls，无需扩大；仍 proposed（缺 PH-270 done 之外的 PH-180/181/182/183）。
- 现有 owner：cash/settlement/costing/contracts/management_revenue/core 契约与代码不变；只读接口由实现片按其白名单新增/消费。
- 风险：新 app 一次 bootstrap 注册成本；若未来读模型并入其它 app 需另立规划，不在实现 PR 自我授权。

## 放弃的备选与理由

- **B 复用 delivery/management_revenue**：delivery 是项目履约 owner，management_revenue 是收入确认 owner，读模型/核对是跨域只读聚合，混入会制造第二套 owner 与发布耦合。
- **C core**：kernel 已有基础业务，继续塞读模型/核对会膨胀 kernel 并迫使改 core 迁移链（与 core 0010 抢号）。
- **D 暂缓**：会阻塞 WP-CLOSE/WP-READ-MODEL 与 UAT 核对/工作台验收；且 PH-002/PH-024/PH-235 已 done，没有需要等待的新前置。

## 后续

- 下一规划槽按本 ADR 与 PH-272 spec 冻结 insights 实现片（期间预检/正确性差异单/项目汇总），先复核 PH-180/PH-181/PH-182/PH-183 是否 done；PH-257/PH-262/PH-272 按依赖事实晋级。
- 实现片开工前复核 ReconciliationCase/MetricDefinition 与 `insights.*` 命令仍为当前契约；契约漂移走独立规划 PR。
