# ADR 0014：insights 读模型契约冻结——期间预检/核对差异单/项目工作台只读投影

状态：PH-277 契约冻结产出（docs-only；owner 已由 ADR 0013/PH-270 决定为新建 `pmo_plugins.insights`）；实现片由后续规划槽按本 ADR 冻结（PH-272），本文不授权开工、不创建代码。日期：2026-10-31。编号核验：`docs/adr/` 已有 0001–0013，**0014 空闲**（不占 0010 invoicing / 0011 time / 0012 importing / 0013 insights 归属）。

## 背景与约束（按实代码/契约核对）

- ADR 0013（PH-270 done）已决定：新建 `pmo_plugins.insights` 插件作为 insights 域写 owner；manifest 8 字段 requires={cash,settlement,costing,contracts}；自有迁移 0001；settings/PMO_PLUGIN_MODULES/delivery-urls root 单写；首片只读投影/核对，不写业务事实；export 延后。
- canonical 契约事实：
  - `ReconciliationCase`（`x-owner=insights`、`x-record-kind=owned_fact`；additionalProperties=false）：required `id/organization_id/version/status/source_refs/difference_cents/owner_member_id/resolution_note`；status 枚举 `draft/pending/approved/rejected/superseded/closed`；readOnly `case_key/rule_code/coverage_key/snapshot_watermark`。
  - `MetricDefinition`（`x-owner=insights`、`x-record-kind=read_projection`）：required `id/organization_id/version/status/created_at/metric_id/name/formula_version/as_of_date/scope`；status 枚举 `draft/active/posted/reversed/superseded`；scope 枚举 `organization/project/counterparty`。
  - 命令/事件：`insights.reconciliation.refresh/v1`、`insights.reconciliation.case.update/v1`、`insights.export.create/v1`；事件 `insights.reconciliation_refreshed.v1`、`insights.reconciliation_case_updated.v1`、`insights.export_ready.v1`。
- 只读来源 owner 具名接口（不得复制表/不得写入）：contracts（批准版本/条款/终止）、settlement（OpenItem/Clearance/覆盖）、cash（posted 现金/分类桥接/退款/退回/互转）、costing（成本/人工池）、management_revenue（收入确认）、core（AccountingPeriod/locked_through）。
- 读模型内部表（**非 canonical**）：`MetricSnapshot`（PH-262 spec 定义的 insights 内部快照，仅追加、metric_key/period/project/值/来源版本；不是 contract entity）与 `ReconciliationRefresh`（内部刷新收据）。本 ADR 明确它们不得被当作 canonical 实体对外暴露。

## 冻结的只读接口（首片）

### 1) `period_precheck_snapshot(member, *, period)`

- 输出：`as_of`、`period`、`blockers[]`/`warnings[]`（每条 `code/severity/summary/source_refs[]/owner`），以及汇总计数。blockers 口径（按来源 owner 只读派生，不重算账）：
  - contracts：未批准/待决策合同变更、已终止合同在期间内的结算/变更/收入确认读取；
  - time：未审批工时、可计费确认 pending；
  - cash：未过账现金、分类/桥接缺失、退款/退回/互转异常；
  - settlement：未清/核销/覆盖为负、退款/退回超覆盖；
  - costing：成本/人工池未过账项；
  - core：AccountingPeriod 状态/锁定冲突（只读 `AccountingPeriod`/`Organization.locked_through`）。
- 零写入（除可选 generic audit `insights.precheck.viewed`）；不执行锁定/关期；`as_of` 仅只读过滤。
- 分页/上限：返回当前期间汇总（≤500 blockers/warnings，超出截断并标记 truncated）。

### 2) `reconciliation_case_list/snapshot(member, *, period=None, status=None, owner_member_id=None, project_id=None, as_of=None)`

- 输出严格为 canonical `ReconciliationCase` 字段：`source_refs[]`、`difference_cents`（SignedCnyCents）、`owner_member_id`、`resolution_note`、`status`（draft/pending/approved/rejected/superseded/closed）、readOnly `case_key/rule_code/coverage_key/snapshot_watermark`；分页上限 200。
- 刷新语义（`insights.reconciliation.refresh/v1`）：生成/刷新 draft/pending case；同 `case_key` 至多一条非 closed/superseded 有效 case；并发 refresh 公司锁只一胜；旧快照被新刷新替代时旧 case → superseded（append-only 历史）。
- case 更新语义（`insights.reconciliation.case.update/v1`）：仅允许 draft→pending（写 owner_member_id）、pending→approved/rejected（写 resolution_note）、approved→closed；不允许 open/assigned/resolved/dismissed 等非 canonical 状态/字段；不改任何业务事实。
- 幂等：request_token 收据（org+token 唯一、输入 digest），同 token 重放原结果、改输入冲突；重复 case.update 幂等。

### 3) `project_workbench_summary(member, project_id, *, as_of)`

- 输出（全部服务端派生、整数分、`as_of` 标注）：`contract_value_cents`、`recognized_revenue_cents`、`cost_cents`、`cash_net_cents`、`open_item_remaining_cents`、`budget_etc_cents`（若上游未实现则 null + `missing[]`，不得显示 0）、`metric_snapshots[]`（MetricDefinition refs：metric_id/name/formula_version/as_of_date/scope + 值）、`missing[]/truncated`。
- 口径：合同额/条款→contracts；收入→management_revenue；成本→costing；现金→cash；未清→settlement；预算/ETC→planning（未实现时 null）；全部经具名只读接口派生，不复制业务表、不建可编辑余额/第二套账。
- 分页：指标快照 ≤200；项目汇总单项目。

## 角色矩阵与门控

| 接口 | owner | finance | auditor | pm | member |
| --- | --- | --- | --- | --- | --- |
| period_precheck_snapshot | 全量 | 全量 | 全量 | 仅管理项目摘要 | 拒绝 |
| reconciliation_case_list/snapshot | 全量 | 全量 | 全量 | 仅管理项目摘要 | 拒绝 |
| project_workbench_summary | 全量 | 全量 | 全量 | 仅管理项目 | 拒绝 |

- 模块停用后历史只读、写路由拒绝；未登录 403；不返回他人薪资/费率/token 密文/密码；不新增写权限。

## 写事实边界

- 所有接口仅读既有 owner 事实并写 insights 读模型（ReconciliationCase/内部 MetricSnapshot/Refresh）与 canonical `insights.reconciliation_*` 事件/generic audit；**不写** SourceConsumption/AccountingEvent/LedgerPosting/现金/未清/收入/成本/合同/期间锁定。
- 不新增 canonical schema/topic/command；export（`insights.export.create/v1`）首片不实现，仅在后续片按契约接通。
- `MetricSnapshot` 是内部读模型（非 canonical），不得作为对外 canonical 事实；本 ADR 不新增 contract entity。

## 后续实现片白名单（供规划槽冻结 PH-272，非本片执行）

- `pmo_plugins/insights/{__init__.py,apps.py,plugin.py,domain_metadata.py,models.py,reconciliation_services.py,read_model_services.py,views.py,forms.py,urls.py}`、`pmo_plugins/insights/templates/insights/{reconciliation_list.html,reconciliation_form.html,dashboard.html}`、`pmo_plugins/insights/migrations/*.py`、`pmo/settings.py`、`pmo/urls.py`、`pmo_plugins/delivery/urls.py`、`core/management/commands/verify_integrity.py`、`tests/test_v1_reconciliation.py`、`tests/test_v1_reconciliation_http.py`、`tests/test_v1_dashboard_read_model.py`、治理文件。
- 实现片须按 ADR 0013 的 bootstrap 与 migration 0001 落地；不得改已应用迁移、不得写业务事实、不得新增写入口。

## 反例（实现片与评审须拒绝）

- 借本契约实现写入口/改账/锁期；把 `MetricSnapshot` 当 canonical 实体；新增 period/scope/category/assignee/due_date/note 等非 canonical ReconciliationCase 字段；使用 open/assigned/resolved/dismissed 状态；客户端传差异金额/余额/证明；把缺失指标显示为 0；把 insights 读模型写成业务事实 owner；未核号占用 0010–0013。

## 影响与同步

- PH-257（期间核对）与 PH-262（工作台读模型）：decision point/风险段落引用本 ADR 0014；两者仍 proposed（缺 PH-180/PH-181/PH-182/PH-183），不虚假晋级。
- PH-272（insights 实现首片）：tasks blockers 更新为“待 PH-277 done + PH-180/181/182/183 done”；因 PH-272 spec 不在本任务 BASE 的 allowed_paths，本片只更新 tasks blocker 并在报告披露，spec 文字由 PH-272 自己的实现/规划 PR 同步。
- 不改 contract schema、不建 scaffold、不写业务代码/迁移/测试逻辑；不解除任何 proposed。
