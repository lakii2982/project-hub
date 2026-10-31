# 关期/结项写侧验收与运维 runbook（PH-327 冻结）

- 产出：PH-327（docs-only；**不实现**、不改迁移/路由/schema/门禁）。实现：PH-322（done，delivery append-only run）。
- 对齐：`docs/engineering/ops-runbook.md`（PH-345 envelope/退出码/角色/失败处置）、`docs/v1.0-development/batch-c-runbook.md`（PH-352 执行模板）、`docs/v1.0-development/batch-c-release-evidence-contract.md`（PH-366 证据回填/发布 gate）。
- 状态：本 runbook 提供**操作/验收方法**；真实关期/结项/重开/PG/浏览器演练在本片**未执行**，相关项标 `not_run`（见 §11），不得据此宣称验收通过。

## 1. 范围与角色

| 角色 | 权限 |
| --- | --- |
| owner / finance | 执行关期/结项/重开（business_write）；可查看全部预检 |
| auditor | 只读历史/预检；不可执行 |
| pm | 仅其管理项目的结项摘要；**period 预检/页面拒绝**；不可关期 |
| member | 拒绝 |
| 独立复核人 | 四眼复核 evidence/审计，不执行 |

- 所有执行走服务层 + 公司锁（PostgreSQL 生产）；HTTP 页面需 `business_write`，预检页 `history_read`；模块停用后历史只读。
- **四眼原则**：关期/结项/重开为高影响操作，执行人（owner/finance）与复核人（财务负责人/审计/独立验收人）不得同一人；执行前确认 dry_run 预检、期间、备份与回退点；执行后复核审计/事件/verify_integrity。
- 不写账本/收入/成本/现金/未清；不开自动修复/直接 SQL 后门。

## 2. 对象与状态（冻结）

| 对象 | 状态/字段 | 说明 |
| --- | --- | --- |
| `AccountingPeriod` | status、locked_through/closed_through、version | 期间；close 后不可新写（依 PH-322 门控） |
| `PeriodCloseRun` | action=close/reopen、state=closing/closed/reopened、sequence、closed_through、precheck_digest、blockers/warnings、actor、request_token | append-only；唯一序列；closing→closed 成对；重开链 reopen→… |
| `ProjectClose` | action=close/reopen、state、sequence、project_id、reason、actor、request_token | append-only；项目关/重开链 |
| `DeliveryReceipt` | action、request_token、digest、result_id | 幂等收据；同 token 不同输入拒绝 |

- 状态机由服务保证；`verify_integrity` 校验序列连续、closing→closed 成对、latest 与当前状态一致、无 dangling closing、重开链合法、cancelled 无 run、收据引用可用。
- 命令签名（服务层）：`close_period(member, period, request_token, expected_version, dry_run=True, reason='')`、`reopen_period(...)`、`close_project(member, project_id, ..., allow_open_period=False)`、`reopen_project(...)`；预检：`period_close_precheck`、`reopen_period_precheck`、`project_close_precheck`、`reopen_project_precheck`。
- canonical 路由（delivery）：`period-close/`、`period-close-execute/`、`period-reopen/`、`project-close/`、`project-close-execute/`、`project-reopen/`。

## 3. 预检 issue_code 与处置（冻结）

### 3.1 期间关期 blockers / warnings

| issue_code | 级别 | 含义 | 处置 |
| --- | --- | --- | --- |
| period_missing | warning | 无 AccountingPeriod 行 | 先建期间/补资料；不可硬关 |
| period_already_closed | blocker | 期间已 posted/closed | 不重复关期；如需更正走开放期/重开流程 |
| future_dated_fact | blocker | 存在 as_of 之后/未来日期事实 | 更正/等待业务日；不可提前关期 |
| settlement_coverage_issue | blocker | 结算覆盖异常（超核销/remaining 不守恒等） | 按结算 owner 处置后重跑预检 |
| revenue_pairing_issue | blocker | 收入配对异常（超额确认/重复等） | 按收入 owner 处置 |
| fee_pairing_issue | blocker | 成本/手续费配对异常 | 按 costing owner 处置 |
| cash_unposted | blocker | 现金未入账/未分类等 | 按 cash owner 处置 |
| historical_close_without_run | warning | 历史 close 无 run | 记录历史缺口；新关期走 run |
| forecast_gap 等 ops warning | warning | 运营核对 warning | 记录并确认可接受（四眼） |

### 3.2 项目结项 blockers / warnings

| issue_code | 级别 | 含义 | 处置 |
| --- | --- | --- | --- |
| contract_terminated_pending_archive | blocker | 合同终止未归档 | 完成合同归档流程 |
| contract_not_active | blocker | 项目合同非 active | 修正合同状态 |
| change_draft_pending | blocker | 未批准变更草稿 | 批准/撤销变更 |
| open_item_balance | blocker | 未清项未处理 | 结算/核销/调整后重跑 |
| settlement_coverage_issue / revenue_pairing_issue / fee_pairing_issue / cash_reconciliation_issue | blocker | 各 owner 核对异常 | 对应 owner 处置 |
| period_not_closed | warning/blocker | 期间未关期；默认 warning，`allow_open_period=False` 时 blocker | 先关期或显式 `allow_open_period=True`（四眼批准） |
| archive_evidence_missing | warning | 归档证据缺失 | 补证据/记录缺口 |
| historical_close_without_run | warning | 历史项目结项无 run | 记录；新结项走 run |

- 预检返回 `blockers/warnings/missing/precheck_digest`；执行时服务端重算，blocker 非空即拒绝；warning 不自动阻塞但必须记录并四眼确认。
- `precheck_digest` 绑定 scope/期间/project/expected_version/latest_run 等；执行前 digest 变化 → 重新预检，不得沿用旧预检。

## 4. 关期操作步骤（Period close）

1. **准备**：确认开放期、期间结束日、备份/回退点、执行人+复核人；`python manage.py verify_integrity --json` 当前 ok。
2. **预检**：`period_close_precheck(member, period)` 或页面 `modules/delivery/period-close/`；读取 blockers/warnings/missing/digest；有 blocker 停止并转 owner 处置。
3. **dry_run**：`close_period(..., request_token=<新UUID>, expected_version=<period_version>, dry_run=True)`；确认零写、blockers/warnings、planned_state=closed。
4. **执行**：`dry_run=False` 同 token（或新 token；同 token 同输入重放返回 replay）；服务公司锁 + expected_version 校验；写入 closing→closed 成对 run + DeliveryReceipt + plugin. 审计。
5. **复核**：`verify_integrity --json` 无 `period_close:` 错误；查 `PeriodCloseRun` 最新 sequence/state=closed、AccountingPeriod 状态/closed_through；审计/事件/收据引用。
6. **后置**：关期后新写被拒；通知利益相关方；记录 evidence。

## 5. 期间重开（Period reopen）

1. 仅**已关期（closed）**期间可重开；预检 `reopen_period_precheck`（pm 拒绝；需 owner/finance）。
2. dry_run → execute（新 request_token、expected_version、reason）；写 reopen run（state=reopened）+ receipt + plugin. 审计。
3. `verify_integrity` 校验重开链与 latest 状态；重开后在开放期补录/更正，再按 §4 重新关期（新 sequence）。
4. 禁止直接改 AccountingPeriod 状态或删除 run；重开不等于修改历史账。

## 6. 项目结项操作（Project close）

1. 预检 `project_close_precheck(project_id, allow_open_period=False)`：合同/变更/未清/各 owner 核对/期间；blockers 逐项处置。
2. dry_run → execute（request_token/expected_version/reason）；写 ProjectClose run（closed）+ receipt + plugin. 审计；项目 stage→closed（服务内部）。
3. `verify_integrity` 无 `project_close:` 错误；结算/收入/现金/成本**不自动改动**，按 owner 既有流程处理。
4. 归档证据/迟到款：项目结项不等于财务归档；迟到款只走开放期受限后续（不重开普通报工）。

## 7. 项目重开（Project reopen）

1. 仅 `ProjectClose` latest=closed 可重开；`reopen_project_precheck`（allow_open_period=True 复用项目预检）。
2. dry_run → execute（新 token/expected_version/reason）；写 reopen run + receipt + 审计；项目 stage 回退按服务状态机。
3. `verify_integrity` 重开链/序列；重开后按 §6 重新结项。

## 8. 异常处置（冻结）

| verify_integrity 错误码 | 含义 | 处置 |
| --- | --- | --- |
| `period_close:<org>:<period>:sequence` | 序列不连续/重复 | 停止；保留证据；升级工程按 append-only 方案修复（不得手改/删行） |
| `period_close:<id>:action_state` / `closed_date` | action/state 组合或 closed_through 非法 | 同上；不得直接 SQL 修 |
| `period_close:<id>:missing_closing_pair` | closing 后未成对 closed | 视为 dangling；停止关期；保留现场；升级工程 |
| `period_close:<id>:dangling_closing` | 存在未完成 closing | 同上；不得删除 closing |
| `period_close:<id>:close_transition` / `reopen_transition` / `reopen_state` | 非法状态迁移 | 停止；审计取证；工程修复 |
| `period_close:<org>:<period>:latest_closing` / `latest_state` / `reopened_state` | latest 与 AccountingPeriod 不一致 | 停止；不得强改状态；恢复/修复按工程 runbook |
| `project_close:<project>:sequence` / `action_state` / `reopen_stage` 等 | 项目 run/链异常 | 停止；保留证据；升级工程 |
| `historical_close_without_run` | 历史 close 无 run | 记录为历史缺口；新操作走 run；不补造历史 |
| `stale_version` / `period_already_closed` / `project_already_closed` | 并发/重复执行 | 刷新版本；不要复用旧 token；并发冲突重试一次 |
| 预检 blocker | 业务未就绪 | 按 §3 处置后重跑，不得绕过 |

- **禁止**：删除/覆盖/手改 PeriodCloseRun/ProjectClose/DeliveryReceipt、关闭 trigger、直接改 AccountingPeriod/Project 状态、用 SQL 修 dangling。
- **失败处置**：保留日志/收据/审计/证据摘要；按 PH-345 exit 码语义（0/2/3/4/5）记录；破坏性步骤失败不得盲目重试；恢复/回滚按 `upgrade-recovery.md` 与工程支持。
- **通知/支持话术**：对业务说明“关期/结项失败，已停止新写，正在处置”；不承诺修复时间；不泄露客户/薪资数据。

## 9. 浏览器验收清单（1440/390，not_run）

- 角色：owner/finance 执行、auditor 只读、pm 仅项目摘要、member 拒绝。
- 页面：`period-close/`、`period-close-execute/`、`period-reopen/`、`project-close/`、`project-close-execute/`、`project-reopen/`。
- 检查：1440/390 布局、键盘可达/焦点、空态/错误/加载/停用态、blocker 列表/警告确认、dry_run 与执行按钮状态、权限拒绝提示、停用模块 history_read。
- 证据：截图/录屏路径+步骤+角色；**本片未执行浏览器走查** → `not_run`。

## 10. PostgreSQL 并发验收（not_run）

- 场景：两连接同时关同一期间/结项同一项目；期望一胜一拒（公司锁/唯一序列），无握手丢失；失败方 `stale_version`/`period_already_closed` 等明确错误。
- 场景：关期与写账竞争（关期后写拒绝）、重开与关期交错、project close 与 open item 写入竞争。
- 命令（真实 PG，CI/部署环境）：既有 scoped 测试 + PH-349 `ops_pg_concurrency`/PH-334 汇总证据；SQLite 不冒充 PG。
- 证据：PG 版本/连接数/时序日志/`verify_integrity`；本片未执行 → `not_run`。

## 11. 证据回填模板（对齐 PH-366）

```markdown
## 关期/结项演练 <period|project>（YYYY-MM-DD，BASE <sha>）

- 候选 SHA：<40-hex>；环境/profile：rehearsal|staging|production；执行角色：<owner/finance>；复核角色：<auditor/财务负责人/独立验收人>
- 预检：scope=<period|project>；blockers=<n>；warnings=<n>；missing=<n>；precheck_digest=<sha256>
- 命令/服务：close_period|reopen_period|close_project|reopen_project，mode=dry_run|execute，request_token=<redacted>，expected_version=<n>
- 结果：exit/status=<pass|not_run|fail|blocked>；checks=<...>；run_ref=<id>；sequence=<n>；state=<closing|closed|reopened>
- 证据 artifacts：<路径/CI URL/sha256>；verify_integrity=<ok|errors>
- 证据层级：L1 <有/无>；L2 <有/无（scoped 测试计数）>；L3 <CI PG/无>；L4 <浏览器截图/无>；L5 <生产/试点/无>
- 缺口/未执行：<not_run/blocked 项与原因、责任人、处置>
- 判定：<pass|fail|blocked>；四眼签名：<role + signed_at + evidence_digest>
```

- 按 PH-366：证据绑定候选 SHA/环境/时效（14 天），演练不冒充 L5；未执行项不得写 pass；发布 gate 以 PH-366 清单为准。

## 12. 当前状态（BASE 1c2c6ca，2026-10-31）

- **实现（PH-322）已合入**：PeriodCloseRun/ProjectClose 模型/迁移、4 个命令、4 个预检、关/重开页面、verify_integrity 五类分支、23 项 scoped 测试（L1+L2 证据见 PH-322 report/verification）。
- **本片（PH-327）**：runbook/验收清单文档（L1）；**未执行**真实关期/结项/重开演练、浏览器 1440/390、真实 PG 并发、生产/客户验收 → `not_run`。
- **不得**以本 runbook 或 PH-322 scoped 测试宣称关期/结项已通过发布验收；需按本文执行并回填证据，发布 gate 依 PH-366。

## 13. 反例（一律拒绝）

- 不以预检通过代替 execute；不跳过 dry_run/备份/四眼复核。
- 不手改/删除 close run、收据、审计，不关闭触发器，不用 SQL 修 dangling。
- 不把历史缺口补造为 run；不把 warning 当 blocker 或反之。
- 不录制/保存真实客户、薪资、凭据；证据不脱敏即无效。
- 不以 SQLite 并发冒充 PG；不以演练冒充 L5。
