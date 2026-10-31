# ADR 0017：项目关账/结项契约冻结（WP-PROJECT-CLOSE）

状态：PH-300 契约冻结产出（docs-only）；实现由后续 PH-322 按本 ADR 与实现前置复核落地，本 ADR 不授权开工、不创建代码/迁移。日期：2026-10-31。基线：15cf20c。

## 背景与已冻结事实（按实代码/契约核对）

- 项目/合同/结算/收入/成本/现金/时间 owner 均已交付：PH-212/214/215/265（合同与变更终止）、PH-224/232（结算/未清/核销）、PH-219/222（收入确认/配对）、PH-244/250（手续费/成本）、PH-230/239/240/245（现金/分类/退款/互转）、PH-025/217/269（工时/可计费）、PH-274（导入预览）、PH-267/ADR 0012（导入归属）。
- 只读核对能力已交付：PH-275 变更影响/终止阻断、PH-283 结算覆盖、PH-284 收入配对、PH-287 条款快照、PH-288 现金对账、PH-291 跨域总览、PH-309 工作台、ADR 0014 insights 读模型、ADR 0016 期间关期/预测覆盖。
- 项目结项写侧未实现；legacy PH-185 的只读结项核对由上述切片覆盖，写侧由 PH-322 承接。

## 决定

### 1. 结项状态机（冻结）

- 项目结项对象：`active`（正常）→ `closing`（结项核对通过、结项命令进行中）→ `closed`（已结项，业务写拒绝）→ `reopened`（仅经明确重新开项命令，append-only 记录，回到 active）；不允许 `closed → closed` 重复结项，不允许删除/改写结项历史。
- 结项命令：具名服务 + 浏览器承载，owner/finance 执行；`dry_run` 默认、`request_token` 收据、公司锁、同项目并发结项一胜、重复结项幂等拒绝；不新增 PAT/MCP/结项工具/审批捷径。
- 结项结论为 append-only 事实（项目 ref、结论、blockers/warnings 摘要、操作者、时间、request_token），只追加不修改；历史只读。

### 2. 结项前置核对清单（冻结）

owner/finance 触发只读结项核对，返回 blockers（必须清零）与 warnings（可带条件结项）：

- **合同/变更**：合同 active（非 terminated/closed）、无未决变更草稿/待决策、终止已归档；引用 PH-275 只读影响/阻断接口。
- **结算/未清**：无 active OpenItem 余额（或按项目策略全部核销/关闭）、无重复/超额核销、退款/退回覆盖一致；引用 PH-283 覆盖核对。
- **收入**：已确认收入与合同条款/结算一致、无终止后确认、无未配对确认；引用 PH-284 收入配对。
- **成本/手续费**：已批准手续费成本过账一致、无未批准成本、无重复成本事件；引用 PH-280 手续费配对。
- **现金**：posted 现金分类/桥接/退款/互转一致、无未分类/未过账/负余额；引用 PH-288 现金对账。
- **时间/导入**：工时已审批/可计费确认一致、导入预览无未提交冲突；引用 PH-269/PH-274。
- **期间**：项目相关期间已关期或按 ADR 0016 明确关期状态；未关期时结项命令拒绝或 warning（由实现片冻结）。
- 缺失能力显示 `null + missing`，不显示 0；不自动修正、不自动结项。

### 3. 未清项处理（冻结）

- 结项前 active OpenItem 必须清零：核销（PH-232）、退款/退回（PH-240）、或按合同/财务策略关闭/转营业外（后者未实现，须另立规划并核号）；不得直接改写/删除 OpenItem。
- 未清项处理只走既有 owner 命令（核销/退款/退回/撤核），结项命令不代写结算/未清事实。
- 结项后不得再产生新结算/核销/收入确认/成本/现金事实（owner 服务按项目状态拒绝）；历史只读。

### 4. 结项后写拒绝与更正（冻结）

- 结项后各 owner 写路径必须按项目状态拒绝新业务事实；关账/结项命令本身只更新项目状态与追加结论，不写账本/收入/成本/现金/未清。
- 更正路径：只允许既有负值冲销/红冲/撤核/撤转/撤分类，或明确重新开项后追加；不直接改历史行；更正必须在原 owner 服务内执行并保留来源。
- 归档证据只读引用（合同/验收/结算/收入/现金 refs），不复制业务表、不做文件归档（后续规划）。

### 5. 只读结项核对投影（冻结）

- 项目合同额/已确认收入/成本/现金/未清余额/变更终止状态/期间状态/归档证据 refs 的只读投影；缺失 null+missing；不重算口径、不建第二 owner。
- 页面走 history_read；模块停用历史只读；issue_code 与既有 owner 核对口径一致（PH-283/284/287/288/291）。
- 权限：owner/finance 触发/写结项；auditor 只读；pm 仅其管理项目摘要；member 拒绝。

### 6. 幂等/并发/审计（冻结）

- request_token 收据（org+token 唯一、input digest、result）；同 token 同输入幂等重放、改输入冲突；公司锁；同项目并发结项一胜；重复结项拒绝。
- 结项结论 append-only 审计（generic audit 或实现片 spec 冻结的审计方式），不新增 canonical topic/command 除非另立规划并核号。

## 与既有模型映射（PH-322 前置，必须落实）

ADR 冻结的 `active/closing/closed/reopened` 是**契约状态名**，不是既有 `core.Project.stage` 的枚举事实。现有 `Project.stage ∈ {initiation, planning, execution, acceptance, warranty, closed, on_hold, cancelled}`（另有 `previous_stage`），没有 `active/closing/reopened`。旧→新映射如下，实现片不得把不兼容枚举写成既有事实：

| 契约状态 | 既有事实映射 | 语义与约束 |
| --- | --- | --- |
| `active` | `Project.stage` ∈ {initiation, planning, execution, acceptance, warranty, on_hold}（即 stage ∉ {closed, cancelled}） | 既有事实；本 ADR 的 active 即"未结项" |
| `closed` | `Project.stage='closed'` | 既有事实；本 ADR 的 closed 即该状态 |
| `closing` | **无既有映射** | 未来 schema 变更；不得在现有 stage 枚举中伪称已存在 |
| `reopened` | **无既有映射** | 未来 schema 变更；不得在现有 stage 枚举中伪称已存在 |
| —（取消） | `Project.stage='cancelled'` | 既有独立终态，不是本 ADR 的结项；不得把取消当结项/重开 |

**迁移/回填/约束计划（PH-322 实现前置）**：

1. 在实现片按实际 BASE 二选一并写入 PH-322 spec/report：方案 A 扩展 `Project.stage` 允许 `closing/reopened`（需新迁移、放宽/替换 stage 约束、保留既有 stage 语义与 `previous_stage`）；方案 B 新增 append-only `ProjectClose`（项目 ref、状态迁移、blockers/warnings 摘要、结论、操作者、时间、request_token），`Project.stage` 保持既有枚举，closing/reopened 仅由 ProjectClose 表达（推荐，避免污染项目 stage 语义）。
2. 回填：既有 `stage='closed'` 回填为 closed 语义；其余未取消项目回填为 active 语义；不得改写历史项目行；若方案 A 改约束，须证明旧行仍满足新约束。
3. 约束/不变量：同一项目至多一个进行中的 closing；closed 后业务写拒绝由各 owner 项目/期间校验保证；reopened 必须 append-only 记录并保留原结项结论；`cancelled` 与 `closed` 互斥，取消不触发结项结论。
4. 未实现标记：本 ADR 交付时 `closing/reopened`、结项命令与结项结论表均未实现；PH-322 未落地前，只读投影只能显示 stage 映射并标注 `close_command_not_implemented`/`reopen_not_implemented`，不得显示为已结项或已重开。
5. 不得改已应用迁移、不得抢迁移号；迁移号由 PH-322 按实际叶申请。

## 非目标

结项写侧、项目归档文件/导出、通知、账本/收入/成本/现金/未清写、多币种、客户门户、自动业务修正、canonical schema 变更、PAT/MCP 结项工具。

## 后果与实现约束

- PH-322 实现前须按实际 BASE 复核本 ADR 与 PH-299/PH-300 spec；若需项目结项状态/结论表/迁移，由实现片在 tasks 白名单申请，不得抢号/改已应用迁移。
- 本 ADR 只冻结契约；不改变既有 owner 服务语义、门禁、验收命令或报告 schema。
- 与 ADR 0016 一致：期间关期/锁账边界由 ADR 0016 冻结，本 ADR 只引用；结项与关期是两个独立命令，不互相代写。
