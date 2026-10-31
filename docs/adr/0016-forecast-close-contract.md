# ADR 0016：期间关期与预测覆盖契约冻结（WP-FORECAST/CLOSE）

状态：PH-299 契约冻结产出（docs-only）；实现由后续 PH-322 按本 ADR 与实现前置复核落地，本 ADR 不授权开工、不创建代码/迁移。日期：2026-10-31。基线：15cf20c。

## 背景与已冻结事实（按实代码/契约核对）

- 期间/锁期已存在：`core.AccountingPeriod`（period YYYY-MM、status active/posted、closed_through、org 唯一）与各 owner 服务的期间/锁期/未来日校验（现金 PH-230/239/240/245、结算 PH-224/232、收入 PH-219/222、time PH-269、合同 PH-214/265）。
- 只读核对能力已交付：PH-291 跨域总览、PH-283 结算覆盖、PH-288 现金对账、PH-280 手续费配对、PH-281 成员/授权、PH-279 现金桥接、PH-277 insights 读模型契约、PH-274 导入预览、PH-309 工作台聚合。
- 关期写侧与预测覆盖均未实现；legacy PH-180/181/182/185 的只读能力由上述切片覆盖，写侧由 PH-322 承接。
- 权限矩阵既有：owner/finance 写，auditor 只读，pm 仅管理项目摘要，member 拒绝；模块停用历史只读。

## 决定

### 1. 关期状态机（冻结）

- 期间对象状态：`open`（可写）→ `closing`（预检通过、关期进行中，仅关期命令可写）→ `closed`（关期完成，业务写拒绝）→ `reopened`（仅经明确重新开期命令，append-only 记录，回到 open）；不允许 `closed → closed` 重复关期，不允许删除/改写关期历史。
- 关期命令：具名服务 + 浏览器承载，owner/finance 执行；`dry_run` 默认、`request_token` 收据、公司锁、同期间并发关期一胜、重复关期幂等拒绝；不新增 PAT/MCP/关期工具/审批捷径。
- 关期结论为 append-only 事实（期间、结论、预检摘要、操作者、时间、request_token），只追加不修改；历史只读。

### 2. 锁账边界（冻结）

- 锁账以 `AccountingPeriod.closed_through` 为唯一服务端边界；关期后任何业务写（现金/结算/收入/成本/时间/合同/导入/期初）必须按既有 owner 期间校验拒绝，不得由关期命令代写业务事实。
- 关期命令本身只更新期间状态与追加结论，不写账本/收入/成本/现金/未清；关期不自动生成调整分录。
- 更正路径：只允许既有负值冲销/红冲/撤核/撤转/撤分类或明确重新开期后追加，不直接改历史行；更正必须在原 owner 服务内执行并保留来源。

### 3. 预测覆盖（冻结）

- 预测批次（forecast batch）：`period`（YYYY-MM）、`version`（正整数，同期间/范围递增）、`scope`（company 或 project）、`status`（draft/active/superseded）、`coverage`（covered/partial/uncovered）、`covered_amount_cents`/`gap_cents`、`source_refs`、`as_of`；只读投影，不建可编辑余额/第二套账。
- 覆盖由服务端派生：`covered = Σ 既有 owner 已确认/已过账事实（收入确认/结算/现金/成本/时间）`；`gap = 目标 - covered`，目标缺失时 `null + missing`，不显示 0；客户端不得传覆盖值/金额/目标覆盖服务端派生。
- forecast 版本：同期间/范围 `version+1`，上一 active 置 superseded（append-only 状态迁移），历史版本只读；预测算法/机器学习不在本契约。
- 只读读模型：公司/项目预测覆盖汇总（按期间/范围/版本），引用既有 owner 事实，不写业务事实；若需 canonical topic/command，须另立规划并核号，不在本 ADR 冻结。

### 4. 跨 owner 边界（冻结）

- 关期/预测只读聚合：delivery 层只读服务组合各 owner 只读接口（PH-291/283/288/280/281/279/277/274），不复制 owner 事实、不建第二 owner、不重算业务口径。
- 写侧：关期命令只属于期间 owner（core/delivery 具名服务），预测批次/覆盖若需持久化由后续实现片按实际 BASE 申请模型/迁移；任何 owner 不得借关期/预测写其业务事实。
- 关期后各 owner 写拒绝由 owner 服务自身期间校验保证；关期服务不代理业务写。

### 5. 只读核对（冻结）

- 期间预检 blockers/warnings：未审批单据/工时、未过账现金、未核销/未对账、锁期外写请求、预测覆盖缺口；全部只读派生，不自动修正。
- 关期/结项/预测核对页面走 history_read，模块停用历史只读；缺失 null+missing，不显示 0。
- issue_code 与既有 owner 口径一致（PH-283/288/291），不新增第二套核对口径。

### 6. 权限/门控/幂等（冻结）

- owner/finance 写关期/预测；auditor 只读；pm 仅其管理项目摘要；member 拒绝；模块停用历史只读。
- request_token 收据（org+token 唯一、dataset/input digest、result）；同 token 同输入幂等重放、改输入冲突；公司锁；同期间并发关期一胜；重复关期拒绝。
- 不写 generic audit 之外的隐式副作用；关期/预测结论的审计按实现片 spec 冻结。

## 与既有模型映射（PH-322 前置，必须落实）

ADR 冻结的 `open/closing/closed/reopened` 是**契约状态名**，不是既有 `core.AccountingPeriod` 的枚举事实。现有模型只有 `status ∈ {active, posted}`，并由 CheckConstraint 约束 `active ↔ closed_through IS NULL`、`posted ↔ closed_through IS NOT NULL`；没有 `closing/reopened`。旧→新映射如下，实现片不得把不兼容枚举写成既有事实：

| 契约状态 | 既有事实映射 | 语义与约束 |
| --- | --- | --- |
| `open` | `AccountingPeriod.status='active'`（`closed_through IS NULL`） | 既有事实；本 ADR 的 open 即该状态 |
| `closed` | `AccountingPeriod.status='posted'`（`closed_through IS NOT NULL`） | 既有事实；本 ADR 的 closed 即该状态 |
| `closing` | **无既有映射** | 未来 schema 变更；不得在现有枚举/约束中伪称已存在 |
| `reopened` | **无既有映射** | 未来 schema 变更；不得在现有枚举/约束中伪称已存在 |

**迁移/回填/约束计划（PH-322 实现前置）**：

1. 在实现片按实际 BASE 二选一并写入 PH-322 spec/report：方案 A 扩展 `AccountingPeriod.status` 允许 `closing/reopened`（需新迁移、放宽/替换 CheckConstraint、保留 active/posted 历史语义）；方案 B 新增 append-only `PeriodCloseRun`（期间、状态迁移、blockers 摘要、操作者、时间、request_token），`AccountingPeriod` 保持 active/posted 不变，closing/reopened 仅由 CloseRun 表达。
2. 回填：既有 `active` 回填为 open 语义、`posted` 回填为 closed 语义；不得改写历史期间行；若方案 A 改约束，须证明旧行仍满足新约束。
3. 约束/不变量：同一期间至多一个进行中的 closing；closed 后业务写拒绝由各 owner 期间校验保证；reopened 必须 append-only 记录且保留原关期结论。
4. 未实现标记：本 ADR 交付时 `closing/reopened` 与关期命令均未实现；PH-322 未落地前，对外只读投影只能显示 active/posted 映射并标注 `close_command_not_implemented`/`reopen_not_implemented`，不得显示为已关期或已重开。
5. 不得改已应用迁移、不得抢迁移号；迁移号由 PH-322 按实际叶申请。

## 非目标

关期实现、期间锁定写侧、通知/导出、预测算法/机器学习、客户门户、canonical schema 变更、自动业务修正、账本/收入/成本/现金/未清写、PAT/MCP 关期工具。

## 后果与实现约束

- PH-322 实现前须按实际 BASE 复核本 ADR 与 PH-299/PH-300 spec；若需内部状态表/迁移，由实现片在 tasks 白名单申请，不得抢号/改已应用迁移。
- 本 ADR 只冻结契约；不改变既有 owner 服务语义、门禁、验收命令或报告 schema。
- 与 ADR 0014（insights 读模型）一致：读模型只读、缺失 null+missing；与 ADR 0013（insights 归属）不冲突。
