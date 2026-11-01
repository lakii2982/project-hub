# insights 读模型实现前置契约（PH-343 冻结）

- 产出：PH-343（docs-only；**不实现**、不建表/迁移/路由/产品代码）。
- 依赖（done）：PH-277 ADR 0014、PH-354 期间预检只读首片、PH-291/304/309 只读来源。
- 消费方：PH-339 insights 读模型实现首片；PH-354 为其期间预检切片；PH-379/384 为后续。
- 权威：canonical schema、ADR 0013/0014 与 PH-363 期间预检契约高于本文；本文只冻结**insights 实现片**的只读投影、刷新边界、口径、missing 语义与权限，不新增 canonical 实体/topic/command。

## 1. 范围与总原则

1. **只读派生**：所有指标按请求从 owner 事实派生；不写账本/现金/收入/成本/未清，不建第二套账，不自动修正差额。
2. **owner 唯一事实来源**：contracts→合同/条款/变更/终止；settlement→OpenItem/Clearance/Offset；cash→posted 现金/分类/退款/退回/互转；costing→成本/人工池/分摊/预算；management_revenue→收入确认/冲销；time→工时/可计费确认；core→AccountingPeriod/locked_through。insights 不复制表、不重算 owner 规则。
3. **复用既有只读接口**：PH-291 跨域总览、PH-283 结算覆盖、PH-284 收入配对、PH-288 现金对账、PH-280 手续费配对、PH-309 工作台聚合、PH-354 期间预检；本片只做聚合/投影，不新增第二套口径。
4. **缺失不为 0**：上游缺失/不可判定 → `null` + `missing[]`；`0` 只表示真实零值；差额任一侧缺失 → `null`。
5. **刷新不产生业务事实**：刷新/快照只更新 insights 内部读模型（若实现片申请），不写 canonical 事件、不写账本/审计业务事实。
6. **无外部副作用**：不发送邮件/Webhook、不触发审批/付款/关期/结项、不调用 PAT/MCP/AI 授权。

## 2. 冻结的只读接口（复用 ADR 0014）

### 2.1 `period_precheck_snapshot(member, *, scope, project_id, period, as_of, sections=None)`

- 已由 PH-354 实现；本文不重定义 envelope/digest，只确认实现片必须复用它而非复制公式。
- 期间必须与 as_of 同月；跨月拒绝；未来 as_of 拒绝。

### 2.2 `reconciliation_case_list/snapshot(member, *, period, status, owner_member_id, project_id, as_of)`

- 输出严格为 canonical `ReconciliationCase` 字段（`source_refs/difference_cents/owner_member_id/resolution_note/status` + 只读 `case_key/rule_code/coverage_key/snapshot_watermark`）。
- 刷新语义（`insights.reconciliation.refresh/v1`）：生成/刷新 draft/pending case；同 `case_key` 至多一条非 closed/superseded 有效 case；并发 refresh 公司锁只一胜；旧快照被替代 → superseded（append-only 历史）。
- case 更新（`insights.reconciliation.case.update/v1`）：仅 draft→pending→approved/rejected→closed；不改业务事实。
- 幂等：request_token 收据（org+token 唯一、输入 digest）。

### 2.3 `project_workbench_summary(member, project_id, *, as_of)`

- 输出：`contract_value_cents`、`recognized_revenue_cents`、`cost_cents`、`cash_net_cents`、`open_item_remaining_cents`、`budget_etc_cents`、`metric_snapshots[]`、`missing[]/truncated`。
- 各字段经具名只读接口派生；上游未实现 → `null` + `missing`，不显示 0。
- 指标快照 ≤200；单项目汇总。

## 3. as_of / period 过滤

- `as_of` 为业务日（含）；未来日期拒绝或按契约产生 warning，不纳入未来事实。
- `period` 为 YYYY-MM；与 as_of 不一致时按 PH-354 口径拒绝（跨月分别查询）。
- 期间/锁期引用 AccountingPeriod/Organization.locked_through/PeriodCloseRun/ProjectClose 只读事实，不复制状态机。

## 4. missing / null 语义

- 上游不可读/未实现/未配置 → `missing_code` + `domain` + `source_ref` + `reason` + `severity`；禁止以 0 冒充。
- 差额公式任一侧缺失 → 差额 `null`，不参与守恒断言。
- 同一 issue 以 `issue_code + 排序 source_refs` 去重；截断标 `truncated=true` + `result_truncated`。

## 5. 权限与门控

| 接口 | owner | finance | auditor | pm | member |
| --- | --- | --- | --- | --- | --- |
| period_precheck_snapshot | 全量 | 全量 | 全量 | 仅管理项目摘要 | 拒绝 |
| reconciliation_case_list/snapshot | 全量 | 全量 | 全量 | 仅管理项目摘要 | 拒绝 |
| project_workbench_summary | 全量 | 全量 | 全量 | 仅管理项目 | 拒绝 |

- 模块停用后历史只读（`history_read`），写路由拒绝；未登录 403。
- 不返回他人薪资/费率/token 密文/密码；不新增写权限。
- project scope 下无项目归属的域（cash/costing/合同总览）显式 `missing`，不混入全公司其他项目。

## 6. 内部读模型与迁移边界

- 若实现片需要 `MetricSnapshot`/`ReconciliationRefresh` 等内部表，必须按实际 BASE 申请独立迁移号并复核白名单；它们是内部读模型，不是 canonical 实体，不得对外暴露为业务事实。
- 不新增 canonical schema/topic/command；`export`（`insights.export.create/v1`）首片不实现。

## 7. 反例（实现片与评审须拒绝）

- 借本契约实现写入口/改账/锁期；把 `MetricSnapshot` 当 canonical 实体；新增非 canonical 的 `ReconciliationCase` 字段或 `open/assigned/resolved/dismissed` 状态；
- 客户端传差异金额/余额/证明；缺失指标显示 0；insights 写成业务事实 owner；
- 跨月 period 静默混入、未来事实参与、project scope 混入其他项目；未核号占用迁移。

## 8. 实现片验收（PH-339 按实际 BASE）

```sh
python scripts/projectctl.py check
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test tests --verbosity 1
python manage.py verify_integrity --json
```

- 金样例：期间预检 blocker 与来源一致、missing 不显示 0、digest 稳定、差异单状态机合法、项目工作台字段经 owner 派生。
- 反例：member/pm 越权、跨 org、缺失按 0、写业务事实/第二套账、跨月/未来事实、未先停 insights 导致依赖停用语义变化。

## 9. 变更控制

- 接口字段、状态机、missing_code、内部表结构变更需新规划槽；实现片不得静默削弱只读纯度、owner 归属或权限。
- 本文为契约冻结：实跑 `projectctl check` 与 `tests.test_projectctl tests.test_governance`；无实现/迁移/表/路由/产品代码；PH-339 未解除 proposed。
