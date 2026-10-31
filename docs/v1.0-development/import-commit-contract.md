# 导入提交实现前置契约（PH-362 冻结）

- 产出：PH-362（docs-only；**不实现**、不建表/迁移/路由/产品代码）。
- 依赖（done）：PH-274 导入预览、PH-363 期间预检/四类核对/差额契约（`docs/v1.0-development/period-precheck-contract.md`）。
- 消费方：PH-289（ImportPreview/ImportConflict 持久化与逐域提交编排，proposed）、PH-353（`onboarding` 原子提交首片，proposed）、PH-338（发票批量/导入，proposed）。
- 权威：canonical schema 高于本文；本文只冻结**内部提交服务/收据/失败语义**，不新增 canonical 实体/topic/command/query，不改既有门禁/验收命令。实现片若需公开命令，须另立 canonical 规划。

## 1. 范围与总原则

1. **提交只经预览**：提交必须引用 PH-274/PH-289 产生的持久预览（`preview_ref`）；无预览、预览过期/已消费、预览与提交行不一致 → 拒绝。客户端不得直接提交业务事实/`normalized_commands`/冲突裁决结果。
2. **全有或全无**：一个提交批次在一个公司锁 + 一个数据库事务内执行；任一行/owner 服务失败 → 整批回滚，零业务事实、零来源消费、零账本/审计半笔。
3. **服务端重算预检**：提交前服务端按 PH-363 重跑期间预检（`purpose=import_submit`），重算 `precheck_digest`；客户端携带的 digest/as_of 只作提示，不作为授权。
4. **owner 唯一写者**：行级提交只调用既有 owner 具名服务（onboarding 不直接写 core/accounting/cash/settlement/invoicing/costing 表）；导入只做模板/字段映射/冲突/批次编排与收据。
5. **幂等/可追溯**：`request_token` + 稳定 `import_key`；同 token 同输入返回原收据，改输入冲突；批次/预览/冲突/来源消费均可追溯。
6. **不自动修数据**：失败不自动改业务事实/不重试写半批/不覆盖已提交批次；冲突必须显式消解后重提。
7. **无外部副作用**：不发送邮件/Webhook、不触发审批/付款/关期/结项，不调用 PAT/MCP/AI 授权。

## 2. 输入契约（内部服务）

```
commit_import(member, *, preview_ref, rows_digest, precheck_digest,
              precheck_as_of_date, period, request_token, expected_version,
              dry_run=True)
```

| 参数 | 类型 | 语义 |
| --- | --- | --- |
| preview_ref | SourceRef-like | `{namespace:'onboarding', kind:'import_preview', id, version}`；必须同 org、状态 `draft/ready` 且未被消费 |
| rows_digest | sha256 | 预览持久化的规范化行 digest（PH-289 冻结）；必须与服务端重算一致 |
| precheck_digest | sha256 | PH-363 预检 digest；服务端重算后比较 |
| precheck_as_of_date | BusinessDate | 与重跑预检 as_of 必须一致；不得未来 |
| period | PostingPeriod | 导入批次期间；必须开放、未锁、与行业务日期一致 |
| request_token | UUID | 幂等键；同 token 同输入返回收据，改输入冲突 |
| expected_version | int | 预览/批次版本；并发修改拒绝 |
| dry_run | bool | 默认 true；true 只预演并返回将写入摘要，不落任何业务事实/收据/来源消费 |

- 请求不得携带金额/状态/审批人/账本/所有权字段；金额/日期/税额/成本等由行映射与 owner 服务派生/校验。
- `preview_ref` 版本升级后旧版本不得提交；预览被修订必须产生新版本，客户端重新确认。

## 3. 预检与预览绑定（冻结）

### 3.1 预检重算

提交时服务端必须：

1. 按预览的 `scope/period/as_of` 调用 PH-363 `period_precheck_snapshot`（`purpose/import_submit` 语义），至少包含与导入 kind 相关的 sections。
2. 重算 `precheck_digest = sha256(canonical_json({contract, scope, period, as_of_date, watermark, issues, differences, missing}))`，canonical 规则与 PH-363 §8 完全一致（排除 digest 自身）。
3. 比较请求 `precheck_digest`/`precheck_as_of_date`：
   - 不一致 → 拒绝 `precheck_stale`（要求重新预览/重新确认）。
   - 存在 blocker 或 `precheck.can_close=false` → 拒绝 `precheck_blocked`。
   - 预检 `missing` 中属于 blocker 的项 → 拒绝 `precheck_blocked`；warning 不单独阻断，但必须写入提交收据。
4. 期间/锁期：`period` 必须为 PH-363 定义的开放期，未锁/未关/未跨期；已关闭/锁定/未来 → `period_closed`/`period_locked`/`future_date`。

### 3.2 预览一致性

- 服务端重算预览行 digest，必须等于 `rows_digest`；预览行/字段/冲突在预览确认后被修改 → `preview_stale`。
- 预览已提交过（任一行被消费）→ `preview_consumed`；同预览仅允许一个成功提交。
- 客户端提交行必须与预览行逐行一致（行号、原值 digest、映射结果）；不得新增/删改行绕过预览。

## 4. 冲突消解（PH-289/PH-353）

- `ImportConflict` 以 `conflict_key = sha256(org|kind|preview_id|row_no|field|source_ref|conflict_code)` 去重；同一 key 至多一条有效未消解冲突。
- 提交前所有 `blocker` 冲突必须已显式消解（accept/reject/merge 决策+operator+时间+evidence），仍存在未消解 blocker → `import_conflict_unresolved`，零写入。
- `warning` 冲突不阻断提交，但必须写入收据 `warnings[]`；不得静默丢弃。
- 冲突消解决策 append-only；不得覆盖历史；消解后重新预检/重新 digest，旧 digest 提交 → `precheck_stale`。
- 客户端不得直接提交冲突裁决结果；裁决经 onboarding 冲突服务落库并返回新预览版本后才能提交。

## 5. 幂等、收据与重复提交

### 5.1 import_key（稳定）

```
import_key = sha256(canonical_json({
  "organization_id": "<org>",
  "kind": "<import kind>",
  "preview_id": "<preview uuid>",
  "rows_digest": "<sha256>",
  "source_batch_ref": "<owner batch/source ref or null>"
}))
```

- 排除 `request_token`、渠道、重试次数、展示文案。
- 同 org + import_key 至多一个成功提交；重复 import_key → `import_key_duplicate`，不重复消费来源/不重复写业务事实。

### 5.2 收据（内部表，非 canonical）

PH-289/PH-353 可新增 onboarding 内部 `ImportSubmissionReceipt`（或等价 append-only 收据）字段：

```
id, organization, request_token, action='import.commit', input_digest,
import_key, preview_ref, period, status='committed|failed|dry_run',
precheck_digest, precheck_as_of_date, result_refs, conflicts_resolved, warnings,
missing, created_by, created_at
```

- 唯一约束 `(organization, request_token)` 与 `(organization, import_key where status='committed')`；append-only，UPDATE/DELETE 拒绝。
- 收据不保存客户敏感原文；行级原值只存脱敏摘要/哈希，错误 envelope 例外字段按最小必要。
- 同 token 同 `input_digest` 重放返回原收据；同 token 改输入 → `request_token_conflict`；未知结果先查收据再重试，不盲重放。
- `dry_run` 不写收据/不消费来源/不写业务事实；可返回预览将通过/拒绝摘要。

## 6. 行级错误与 issue_code

### 6.1 错误 envelope

```json
{
  "status": "rejected|committed",
  "row_errors": [{
    "row_no": 3, "field": "amount_cents", "issue_code": "row_amount_invalid",
    "original_digest": "<sha256>", "normalized_value": null,
    "source_ref": {"namespace": "onboarding", "kind": "import_preview_row", "id": "<uuid>", "version": 1},
    "message": "脱敏说明"
  }],
  "precheck": {"digest": "<sha256>", "as_of_date": "YYYY-MM-DD", "blocked": false},
  "conflicts": [],
  "missing": [],
  "truncated": false
}
```

- 行级错误逐行返回，不因一条错误丢失其它错误；单次最多 200 行错误/1000 issue，超出 `truncated=true` + `result_truncated`。
- 失败提交零业务事实；错误可重试/修正后重提，不自动重试。

### 6.2 issue_code 注册表（冻结）

| issue_code | 严重级 | 触发 |
| --- | --- | --- |
| precheck_stale | blocker | digest/as_of 与服务端重算不一致 |
| precheck_blocked | blocker | 预检存在 blocker 或 can_close=false |
| preview_not_found / preview_stale / preview_consumed | blocker | 预览不存在/被修改/已提交 |
| period_closed / period_locked / future_date | blocker | 期间关闭/锁定/未来业务日 |
| import_conflict_unresolved | blocker | 存在未消解 blocker 冲突 |
| import_key_duplicate | blocker | 同 import_key 已成功提交 |
| request_token_conflict | blocker | 同 token 改输入 |
| row_missing_required / row_type_invalid / row_date_invalid / row_amount_invalid / row_duplicate / row_source_unapproved | blocker | 行级校验失败 |
| owner_validation_failed | blocker | owner 具名服务拒绝（引用其 message/issue_code） |
| owner_idempotency_conflict | blocker | owner 收据冲突/来源已被其它导入消费 |
| source_quota_exceeded | blocker | 来源额度/覆盖/可消费量不足 |
| module_disabled / unauthorized / cross_org / cross_project | blocker | 门控/权限/归属拒绝 |
| result_truncated | warning | 错误/明细超上限 |
| precheck_warning | warning | 预检 warning（不阻断，写入收据） |

- severity 仅 blocker/warning；blocker 一律零写；warning 允许提交但必须可追溯。
- 行级错误码按 owner/预览既有口径优先复用；本表为提交编排层补充，不另建第二套金额/期间规则。

## 7. 来源额度消费与一致性

- 每行提交若创建/引用 owner 来源，必须经 owner 具名服务做来源消费（`SourceConsumption`/owner 唯一约束）；onboarding 不直接写 `SourceConsumption`。
- 同一来源/覆盖 key 只能被一个成功提交消费；不同预览/批次同来源 → `source_quota_exceeded`/`owner_idempotency_conflict`，零半笔。
- 提交成功后来源额度以 owner 事实为准；onboarding 收据只存 `result_refs`/消费摘要，不复制余额/额度。
- 部分行失败/owner 拒绝 → 整批回滚，来源消费不保留。
- 期初/重复检测：同 org + kind + 业务键/来源 key 已存在 → `import_key_duplicate` 或 owner 唯一冲突；不覆盖、不跳过去重。
- 不写账本/收入/成本/现金/未清；所有财务事实由 owner 服务产生。

## 8. 事务、锁与失败回滚

1. 提交开始时 `lock_company(member)`，再校验权限/模块/期间/预览/预检/冲突/令牌；锁顺序固定公司→期间→批次/预览→行/来源，避免并发半笔。
2. 单事务：创建收据（pending）→ 逐行 owner 服务 → 更新收据 committed；任一步异常 → 事务回滚，收据/业务事实/来源消费均不保留。
3. 失败返回 `status=rejected` + `row_errors`/owner message；不自动“修数据”、不部分成功、不写补偿批次。
4. 无法在单事务覆盖的外部系统：本契约禁止外部写；email/Webhook/第三方 API 均非目标，故不引入分布式补偿。
5. 并发：公司锁 + 唯一 import_key/收据保证一胜一拒；SQLite 不作生产并发证据，PG 双连接交 CI。

## 9. 权限、门控与期间

- 写：owner/finance；auditor 只读提交历史/收据；pm/member 拒绝（不因导入放宽项目摘要）。
- 模块停用：提交/写路径拒绝；历史收据/预览只读 `history_read`。
- 跨 org/项目/预览/来源拒绝；预览与提交 scope 必须一致。
- 期间：仅开放期；`AccountingPeriod` 关闭/锁定、`PeriodCloseRun`/`ProjectClose` 已关闭 → blocker；重开链只读引用，不复制状态。
- `expected_version`、公司锁、`request_token` 必须沿用既有模式；客户端不得指定 actor/org/状态/审批人。

## 10. 与既有契约/任务的边界

- **PH-274**：只读预览/模板/字段映射；本契约不修改预览规则，只要求提交引用持久预览（PH-289）。
- **PH-289**：负责 ImportPreview/ImportConflict 持久化与逐域提交编排（无业务写），本契约提供收据/幂等/预检绑定/失败语义。
- **PH-353**：实现 onboarding 原子提交首片，必须消费本契约；不得复制 PH-363 预检公式或 owner 校验规则。
- **PH-338**：发票批量/导入提交复用本契约，但 invoice owner 服务是唯一写者；发票导入不得直接写 Invoice。
- **PH-363**：唯一预检/digest 口径来源；本契约只引用，不重定义。
- 不新增 canonical 实体/topic/command/query；内部表/收据由 PH-289/353 按实际 BASE 申请迁移号。

## 11. 实现片验收（PH-289/PH-353/PH-338 按实际 BASE）

```sh
python scripts/projectctl.py check
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test tests --verbosity 1
python manage.py verify_integrity --json
python manage.py test tests.test_v1_import_submission tests.test_v1_import_submission_http --verbosity 1
```

- 金样例：合法预览 + 最新预检 → 提交成功一次；同 token 重放返回同一收据；预览行/业务事实/来源消费各一次；预检/冲突/期间/权限任一 blocker → 零写。
- 反例：跳过预览、旧 digest/as_of、预检 blocker、未消解冲突、同 import_key 重复、同 token 改输入、跨 org/项目、关闭/锁定期间、owner 拒绝、外部发送/自动修数据；全部拒绝且零半笔。
- 必须用 owner 服务真实 fixture；PG 并发交 CI；浏览器 1440/390 仅页面前提，不代替提交证据。
- verify_integrity 不因本契约新增业务不变式；但提交后账本/来源/审计链必须仍通过。

## 12. 证据与状态

- 本片为契约冻结：实际执行 `projectctl check` 与 `tests.test_projectctl tests.test_governance`；无实现/迁移/表/路由/产品代码。
- 未执行：PH-289/PH-353/PH-338 实现、真实导入、PG 并发、浏览器、生产；均 not_run。
- 变更控制：字段/issue_code/收据/幂等键变更需新规划槽；实现片不得静默削弱预检重算、零半笔或 owner 归属。
