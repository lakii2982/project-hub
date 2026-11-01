# 发票批量/导入实现前置契约（PH-342 冻结）

- 产出：PH-342（docs-only；**不实现**、不建表/迁移/路由/产品代码）。
- 依赖（done）：PH-252 发票登记/分配/红冲、PH-274 导入预览、PH-320 税额资格/跨 owner 编排、PH-353 onboarding 原子导入首片。
- 消费方：PH-338 发票批量/导入实现首片；PH-378/383 为其后续。
- 权威：canonical schema 与 PH-252/PH-353 既有服务高于本文；本文只冻结**批量导入编排层**的模板、输入、幂等、原子性与错误语义，不新增 canonical 实体/topic/command。

## 1. 范围与总原则

1. **本片仅 `sales` 发票**：PH-252 当前只允许 `sales` 发票做业务来源分配（`_source_quota` 对非 sales 明确拒绝）。采购发票批量导入及其分配属于 PH-252 扩展，需另立规划，不在本片。
2. **发票唯一写者**：发票/行/分配/红冲只经 `pmo_plugins.invoicing.services.record_invoice / allocate_invoice_lines / reverse_invoice` 写入；导入层只做模板/字段映射/批次编排与收据，不直接写 invoicing/core/accounting/cash/settlement 表。
3. **复用导入基础设施（唯一路径）**：发票 kind 必须经 PH-353 的 onboarding 预览/提交链路。PH-338 的前置规划 PR 必须扩展其 allowed_paths，包含 `core/imports.py`（新增 `HEADERS["invoicing"]` 与 `apply_rows` 的 invoicing 分支，仅调用 PH-252 服务）以及 `pmo_plugins/onboarding/preview_services.py`、`pmo_plugins/onboarding/import_commit_services.py`（新增 invoicing 模板识别与 `invoice_date` 期间映射）。不接受在 invoicing 侧另写第二套 CSV 解析/预览；本契约冻结的是该扩展后的接口形状，PH-338 不得跳过规划扩展自行改这些文件。
4. **全批原子**：一个批次在公司锁 + 单数据库事务内执行；任一行/owner 服务失败整批回滚，零发票、零行、零分配、零来源消费、零账本/审计半笔。
5. **不产生第二套财务事实**：不自动生成收入、现金、应收应付、账本或税额资格；红冲走 PH-252 单票命令；税额资格确认走 PH-320。
6. **不猜不修**：重复发票号、行合计不守恒、来源额度不足、期间关闭/锁定、跨 org/项目一律拒绝；不自动改数据、不跳过重复。

## 2. 模板与字段映射（对齐 PH-252）

### 2.1 批量文件

- UTF-8（可选 BOM）CSV，单文件 ≤ 1 MiB（与 PH-274 预览一致）、单批 ≤ 500 行；超出 `file_too_large` / `row_limit_exceeded`。
- 列集合必须精确匹配冻结模板；缺列/多列/未知列 `template_mismatch`。
- 每行 `source_id` 必填、≤100 字符、批内唯一；重复 `duplicate_source_id`（与 PH-274 命名一致）。

### 2.2 冻结模板（sales 发票批量导入）

| 列 | 语义 | 必填 |
| --- | --- | --- |
| `source_id` | 导入来源稳定编号（每行） | 是 |
| `counterparty_name` | 同公司 active 且角色为客户的往来方名称；按 `display_name`/`normalized_name` 唯一匹配，歧义/不存在/停用则拒绝 | 是 |
| `invoice_number` | 发票号码 1–80 字符 | 是 |
| `invoice_date` | 业务日期 YYYY-MM-DD | 是 |
| `gross_cents` / `net_cents` / `tax_cents` | 整数分，gross=net+tax | 是 |
| `evidence_json` | 客户/合同证据 JSON（脱敏，≤2000 字符），原样传给 `record_invoice.evidence`；同票多行必须逐字一致 | 是 |
| `line_no` | 发票行号，同票内唯一 | 是 |
| `line_gross_cents` / `line_net_cents` / `line_tax_cents` | 行金额整数分，行合计=票面 | 是 |
| `allocation_namespace` / `allocation_kind` / `allocation_id` / `allocation_version` | 业务来源引用，组合为 `business_source_ref`；kind 仅 `contracts/contract_line`、`settlement/open_item`、`settlement/settlement` | 是 |
| `allocation_gross_cents` / `allocation_net_cents` / `allocation_tax_cents` | 分配金额整数分，行内合计=行金额 | 是 |
| `note` | 脱敏说明（可选，≤500） | 否 |

- 同票多行/多分配以 `invoice_number + line_no` 归组；跨行合计必须守恒；`allocation_version` 为整数且必须等于来源实体当前版本。
- 客户端不得携带 actor、审批人、状态、税额资格结论、余额或账本字段。
- `counterparty_name` 无法唯一匹配时拒绝 `counterparty_unresolved`，不自动创建往来方。

### 2.3 提交请求（invoicing 批次编排，复用 PH-353 形状）

```
commit_invoice_import(member, *, preview_ref, rows_digest, precheck_digest,
                      precheck_as_of_date, period, request_token,
                      expected_version, dry_run=True)
```

- `preview_ref`：PH-353 `ImportPreview` 引用，其中 `ImportPreview.kind="invoicing"`（`preview_ref.kind` 仍为 `import_preview`）；必须同 org、`draft/ready`、未消费、版本匹配。
- `precheck_digest` / `precheck_as_of_date`：PH-354 期间预检摘要；服务端重跑后比较，不一致 `precheck_stale`，blocker `precheck_blocked`。
- `period`：必须与每行 `invoice_date` 同月，否则 `row_date_invalid`。
- `request_token`：批次级幂等；批次内每张发票/行分配使用确定性派生 token（见 §4），同 token 重放返回原收据。
- `dry_run`：默认 true；true 覆盖预览/预检/冲突/行格式/行合计/期间校验，**不覆盖** owner 来源额度与往来方一致性（PH-252 当前 dry_run 在 `_source_quota` 前返回）；额度/一致性在真实提交时由 PH-252 检查。dry_run 不写收据、不写发票、不消费来源。

## 3. 逐行校验与 issue_code（对齐 PH-362/PH-353）

| issue_code | 严重级 | 触发 |
| --- | --- | --- |
| template_mismatch / file_too_large / row_limit_exceeded | blocker | 模板/大小/行数 |
| duplicate_source_id / row_duplicate | blocker | 来源编号或发票行重复 |
| row_missing_required / row_type_invalid / row_date_invalid / row_amount_invalid | blocker | 字段/日期/金额格式 |
| counterparty_unresolved | blocker | 往来方名称缺失/歧义 |
| invoice_number_duplicate | blocker | 同方向发票号已存在（PH-252 唯一口径） |
| line_total_mismatch / invoice_total_mismatch | blocker | 行合计≠票面、分配合计≠行 |
| allocation_ref_invalid / source_quota_exceeded / row_source_unapproved | blocker | 来源引用/额度/往来方与来源不一致（透传 PH-252） |
| period_closed / period_locked / future_date | blocker | 期间/锁期/未来日期 |
| cross_org / cross_project / unauthorized / module_disabled | blocker | 门控/权限/归属 |
| preview_not_found / preview_stale / preview_consumed | blocker | 预览不存在/被修改/已消费（PH-362） |
| precheck_stale / precheck_blocked | blocker | 预检摘要或阻塞项 |
| import_conflict_unresolved | blocker | 存在未消解 blocker 冲突（PH-362） |
| request_token_conflict / import_key_duplicate | blocker | 幂等冲突 |
| owner_validation_failed / owner_idempotency_conflict | blocker | PH-252 具名服务拒绝/收据冲突（透传） |
| precheck_warning / result_truncated | warning | 非阻塞预检/截断 |

- **owner 异常到 issue_code 的映射（冻结）**：PH-252 抛出的 `ValidationError` 文本按前缀/关键词归入 `source_quota_exceeded`（来源额度/覆盖/可消费量不足）、`row_source_unapproved`（发票/来源往来方不一致或来源未批准）、`invoice_number_duplicate`（同方向票号已存在）、`line_total_mismatch`/`invoice_total_mismatch`（合计不等）；无法归类的统一为 `owner_validation_failed` 并透传脱敏消息。实现片必须提供该映射的测试，不得把 owner 文本直接当 issue_code。
- 错误 envelope 复用 PH-353：`status`、`row_errors[]`（`row_no/field/issue_code/original_digest/normalized_value/source_ref/message`）、`precheck`、`conflicts`、`missing`、`truncated`；单次最多 200 行错误/1000 issue。

## 4. 幂等、收据与重复提交

- 稳定 `import_key = sha256(canonical_json({organization_id, kind:'invoicing', preview_id, rows_digest, source_batch_ref}))`；同 org + import_key 至多一个成功提交，重复 `import_key_duplicate`。
- 批次收据复用 PH-353 `ImportSubmissionReceipt`（append-only）：`request_token` 唯一、`input_digest`、`result_refs`（发票/行/分配引用）、`period`、`precheck_digest/as_of`、`conflicts_resolved`、`warnings`、`missing`。
- **owner token 派生（冻结公式）**：
  - 发票：`uuid5(NAMESPACE_URL, f"project-hub:{org_id}:invoicing:invoice:{direction}:{invoice_number}")`
  - 分配：`uuid5(NAMESPACE_URL, f"project-hub:{org_id}:invoicing:allocate:{direction}:{invoice_number}:{invoice.version}")`
  - 批次收据使用请求 `request_token`。
  - 同方向同票号重放得到相同 owner token；PH-252 收据按 action+token 查重，因此重复提交返回原收据而非输入冲突。
- 同 token 同输入重放返回原收据；改输入 `request_token_conflict`；未知结果先查收据再重试，不盲重放。
- `dry_run` 不写收据、不消费预览、不写发票；明确 dry_run 覆盖预览/预检/冲突/行/来源额度/往来方一致性，但不替代真实 owner 写入。

## 5. 事务、锁与失败回滚

1. 公司锁 → 权限/模块 → 期间/预检 → 预览/摘要/版本 → 冲突 → 行校验 → owner 服务。
2. 单事务：逐票 `record_invoice` → 逐行 `allocate_invoice_lines`；任一步失败整批回滚。
3. 不部分成功；不写补偿批次；失败返回 `status=rejected` 与逐行错误。
4. 并发：公司锁 + 收据唯一 + PH-252 发票号唯一；SQLite 不作生产并发证据，PG 双连接交 CI。

## 6. 权限、门控与期间

- 写：owner/finance；auditor 只读提交历史/收据；pm/member 拒绝。
- 模块停用：提交/写路径拒绝；历史收据/预览只读 `history_read`。
- 跨 org/项目/往来方/来源拒绝；预览与提交 scope 必须一致。
- 期间：仅开放期；`AccountingPeriod` 关闭/锁定、`PeriodCloseRun/ProjectClose` 已关闭 → blocker。

## 7. 实现边界与白名单扩展

- PH-338 需要新建 invoicing 侧批次编排服务（如 `pmo_plugins/invoicing/batch_import_services.py`）与必要测试，并复用 onboarding 的 `ImportPreview`/`ImportSubmissionReceipt` 与 PH-354 预检接口。
- 若需要 onboarding 暴露 kind=`invoicing` 的持久预览/收据接口，或需要修改 `core/imports.py`，必须由规划槽扩展 PH-338 的 allowed_paths；实现片不得自行扩权，也不得复制 PH-353 的提交实现。
- 本文不授权实现；PH-338 仍 proposed。

## 8. 反例（实现片与评审须拒绝）

- 跳过持久预览直接写发票；第二套 CSV 解析/金额口径；客户端金额/审批人/状态；
- 用 non-sales 发票走分配；复制 PH-353 提交实现或修改 `core/imports.py` 而未获规划授权；
- 部分提交；失败后保留半批；自动生成收入/现金/账本/税额资格；
- 重复发票号静默跳过；往来方歧义时自动创建；越权/跨 org/跨项目/关闭期间写入；
- 同 token 重放/改输入、import_key_duplicate、预览 stale/consumed、未消解 blocker conflict、跨期行、往来方不一致未拒绝；
- 修改已应用迁移、把本契约当实现片提交产品代码。

## 9. 实现片验收（PH-338 按实际 BASE）

```sh
python scripts/projectctl.py check
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test tests --verbosity 1
python manage.py verify_integrity --json
```

- 金样例：合法预览 + 最新预检 → 多票多行全批提交一次；同 token 重放返回同一收据；发票/行/分配各一次。
- 反例：重复发票号、行合计不守恒、来源额度不足、往来方歧义、期间关闭、跨 org、越权、预检 stale/blocked、owner 拒绝；全部零半笔。
- 必须用 PH-252 真实服务夹具；PG 并发交 CI；浏览器 1440/390 仅页面前提。

## 10. 变更控制

- 模板列、issue_code、收据、幂等键、owner token 派生规则变更需新规划槽；实现片不得静默削弱零半笔、预检重算或 owner 归属。
- 本文为契约冻结：实跑 `projectctl check` 与 `tests.test_projectctl tests.test_governance`；无实现/迁移/表/路由/产品代码；PH-338 未解除 proposed。
