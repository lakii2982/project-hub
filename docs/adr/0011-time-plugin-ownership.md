# ADR 0011：time 域归属与可计费确认契约——新建 pmo_plugins.time 插件（推荐方案 A）

状态：PH-266 决策产出（推荐方案 A）；可计费确认实现片由后续规划槽按本 ADR 冻结（预计 PH-269+），本文不授权开工、不创建代码。日期：2026-10-31。

## 背景与已冻结契约（按实代码核对）

- canonical 实体 `BillableConfirmation` 已冻结：`x-owner=time`、`x-record-kind=owned_fact`，status 枚举 draft/confirmed/reversed，必填 timesheet_ref(kind=timesheet)/contract_line_ref(kind=contract_line)/confirmed_minutes/confirmed_on，evidence≥1；其余字段与约束以 `docs/v1.0-development/contracts/entities.schema.json` 为准。
- canonical 命令/事件已冻结：`time.timesheet.draft.save/v1`、`time.timesheet.submit/v1`、`time.billable.confirm/v1`；事件 `time.approved.v1`、`time.billable_confirmed.v1`。本 ADR 不改 contract schema。
- 当前工时事实与能力在 core：`core.Timesheet`、`core.services` 的提交/审批（PH-025/PH-217 done，月度人工池 `core.labor_projection`）。`pmo_plugins` 下**没有** time app/迁移 owner；若新建 app，迁移随 Django app 统一执行。
- 插件运行时（`core/plugin_runtime.py`/`docs/plugin-architecture.md`）：manifest 恰好 8 字段、插件固定列在 `settings.PMO_PLUGIN_MODULES`、无 URL/页面注册点（页面由 `pmo_plugins/delivery/urls.py` 绑定）、无插件独立迁移链；PH-001/PH-002/PH-140 已交付的 SDK 注册/门控可用，但独立迁移链/URL 注册点/data namespace 仍未交付。
- 相关下游：合同 `confirmed_time` 收费模板（PH-214 done）、结算未清（PH-224 done）、管理收入（PH-219/PH-222 done）。本决策不改变这些 owner 的契约。

## 选项

- **方案 A（推荐）：新建 `pmo_plugins.time` 插件 app**，作为 time 域写 owner，首片只承载 `BillableConfirmation`。
- **方案 B：把 `BillableConfirmation` 放进 core**（kernel 内新增 time 业务表与写服务）。
- **方案 C：放进 contracts**（由合同 owner 承载客户确认）。
- **方案 D：暂缓 time 域**，先不落客户确认。

## 判据与逐项比较

| 判据 | A 新建 time 插件 | B core | C contracts | D 暂缓 |
| --- | --- | --- | --- | --- |
| 契约 owner 一致性 | 与 `x-owner=time` 一致 | core 是 kernel，与冻结 owner 不符 | contracts 是合同/模板 owner，非客户确认事实 owner | — |
| kernel 不膨胀/边界 | time 业务事实独立演进 | 继续把业务纵线塞进 core，违反插件化方向 | 合同 owner 扩张为合同+客户确认双域 | — |
| 迁移 owner/抢号 | time app 自有 0001 起，不碰 core 0009/contracts 0002/settlement 0005 | 改 core 迁移链，与 PH-264 core 0010 抢号风险 | 与 PH-265 contracts 0003 抢号风险 | — |
| manifest/requires 适配 | 8 字段可表达：requires={contracts}（contract_line_ref 只读）；capabilities=[] | 不适用 | 不适用 | — |
| 路由/命令暴露 | delivery/urls root 单写绑定；`time.billable.confirm/v1` 仅具名服务/浏览器 | 同模式但业务入口混入 kernel | 同左 | — |
| 与 labor/costing 边界 | 只记录客户确认分钟，不写人工池/成本/收入 | 易被误当工时/成本 owner | 同左 | — |
| 测试/部署/回滚 | 新 app 一次 bootstrap；停用保留历史；实现片可薄 | 改 kernel 风险高 | 与合同发布耦合 | 阻塞 WP-BILLABLE/UAT-05 |
| 未来 PH-152/153 抽离 | time app 可逐步承接后续工时域能力 | 继续 kernel 化 | 同左 | — |

## 决定

采用**方案 A：新建 `pmo_plugins.time` 插件 app**，作为 time 域写 owner；首片只实现 `BillableConfirmation`（客户确认可计费分钟）及其 canonical 命令/事件/审计，**不改 contract schema、不搬 core.Timesheet、不实现 PH-152/153/154 业务代码**。

实现约束（由后续规划槽冻结的实现片落地，本 ADR 不建 scaffold）：

1. **app 形态**：`pmo_plugins/time/{__init__.py,apps.py,plugin.py,domain_metadata.py,models.py,services.py,views.py,forms.py,templates/time/*,migrations/0001_initial.py}`；`plugin.py` manifest 恰好 8 字段：`id='time'`、`name='工时与可计费'`、`version='0.1.0'`、`kernel_api='>=1.0,<2'`、`requires={'contracts': '>=0.1.0'}`、`default_enabled=True`、`capabilities=[]`、`description` 简述客户可计费确认。
2. **注册**：`pmo/settings.py` 的 `INSTALLED_APPS` 与 `PMO_PLUGIN_MODULES` 各加 `pmo_plugins.time`；`pmo_plugins/delivery/urls.py` 绑定 views；上述为 root 单写（实现片白名单列明）。
3. **迁移 owner**：time app 自有迁移链首号 `0001_initial`；**不新增/不改 core、contracts、settlement 迁移**（避免与 PH-264 core 0010、PH-265 contracts 0003、settlement 0005 抢号）；停用插件保留历史数据/事件队列（沿用 PluginState 语义）。
4. **与 core 只读关系**：`core.Timesheet`/审批事实仍是 kernel 事实，time 插件只经具名只读接口读取（如 `approved_timesheet_for_billable`，由 core 侧 root 单写新增），**不复制工时/审批表**，不改变 PH-025/PH-217 的工时与月度人工池语义。
5. **命令暴露**：`time.billable.confirm/v1` 首片仅由具名服务/浏览器工作流承载；**不接入** PAT/MCP/个人 AI 审批/支付/锁账能力，也不新增公开命令胶囊；若未来要接入须另立决策。`time.timesheet.draft.save/v1`/`time.timesheet.submit/v1` 继续由既有 core 服务承载，本片不迁移。
6. **权限/门控**：写 owner/finance（客户确认由财务/负责人办理，自审按既有禁止自审底线）；读 owner/finance/auditor 与 PM（仅其管理项目）；member 拒绝；模块停用写拒绝、历史只读。
7. **下游边界**：`BillableConfirmation` 只记录客户确认事实（timesheet_ref/contract_line_ref/confirmed_minutes/confirmed_on/evidence），**不写收入/账本/现金/未清/合同模板**；合同 `confirmed_time` 模板、结算 OpenItem、管理收入只经具名只读消费已确认分钟（PH-154 实现片负责接线），不反向改写确认事实。
8. **状态与纠错**：draft→confirmed 的确认路径、confirmed→reversed 的追加式撤销（保留原记录与审计）；不物理删除；幂等 request_token、期间/锁期/未来日期按既有规则拒绝；具体字段/状态机/反例由 PH-266 spec 的实现片 DoR 骨架冻结。

## 影响面

- PH-154（客户确认可计费分钟）：由 proposed 保持；其实现片按本 ADR + PH-266 spec 的 DoR 骨架由下一规划槽冻结为 PH-269+，不改 PH-154 的冻结行为范围；PH-154 spec 增加 owner/边界注记。
- PH-152/153：工时填报/审批能力已由 PH-025/PH-217 在 core 交付，但 PH-152/153 仍缺其声明依赖（PH-145/PH-147/PH-149/PH-110、PH-153）；本 ADR 不晋级它们，只更新 blockers 说明。
- contracts/settlement/management_revenue/costing/cash：契约与代码不变；只读接口由实现片按其白名单新增/消费。
- 风险：新 app 一次 bootstrap 注册成本；若未来把工时域整体抽离 time 插件，需另立规划，不在本决策自我授权。

## 放弃的备选与理由

- **B core**：`BillableConfirmation` 是业务 owned_fact（x-owner=time），塞入 kernel 会让 core 继续膨胀并混淆工时事实与业务确认；还会迫使改 core 迁移链（与 PH-264 抢号）。
- **C contracts**：合同 owner 已有模板/版本/条款职责，客户确认是可计费事实而非合同文本，混入会制造第二套“已确认分钟/合同可计费”语义。
- **D 暂缓**：会阻塞 WP-BILLABLE（UAT-05）与后续结算/收入配对；且 PH-025/PH-214/PH-217/PH-224 已 done，没有需要“等待”的新前置。

## 后续

- 下一规划槽（PH-263 已登记的规划入口）按 PH-266 spec 的“可计费确认实现片候选 DoR 骨架”冻结 PH-269+ 实现片（含精确白名单/bootstrap 文件/迁移 0001/测试/反例），再按依赖事实登记 ready。
- 实现片开工前须复核 `core.Timesheet` 只读接口与 contracts 条款只读接口仍为当前契约；任何契约漂移走独立规划 PR。
