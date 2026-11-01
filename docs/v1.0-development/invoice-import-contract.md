# 发票批量/导入实现前置契约（PH-342 冻结）

- 产出：PH-342（docs-only；**不实现**、不建表/迁移/路由/产品代码）。
- 依赖（done）：PH-252 发票登记/分配/红冲、PH-274 导入预览、PH-320 税额资格/跨 owner 编排、PH-353 onboarding 原子导入首片。
- 消费方：PH-338 发票批量/导入实现首片；PH-378/383 为其后续。
- 权威：canonical schema 与 PH-252/PH-353 既有服务高于本文；本文只冻结**批量导入编排层**的输入、幂等、原子性与错误语义，不新增 canonical 实体/topic/command，不改既有门禁/验收命令。

## 1. 范围与总原则

1. **只经既有预览/提交**：批量导入必须先产生 PH-274 预览并经 PH-353 持久预览，提交只引用 `preview_ref`；不新增第二套 CSV 解析、模板识别或行映射口径。
2. **owner 唯一写者**：发票、发票行、分配与红冲只经 `pmo_plugins.invoicing.services.record_invoice / allocate_invoice_lines / reverse_invoice` 写入；导入层只做模板/字段映射/冲突/批次编排与收据，不直接写 invoicing/core/accounting/cash/settlement 表。
3. **全批原子**：一个批次在公司锁 + 单数据库事务内执行；任一行/owner 服务失败整批回滚，零发票、零行、零分配、零来源消费、零账本/审计半笔。
4. **不产生第二套财务事实**：导入不自动生成收入、现金、应收应付、账本或税额资格；红冲仍走 PH-252 单票命令；税额资格确认仍走 PH-320 既有路径。
5. **不猜不修**：重复发票号、行合计不守恒、来源额度不足、期间关闭/锁定、跨 org/项目一律拒绝；不自动改数据、不跳过重复。
6. **无外部副作用**：不发送邮件/Webhook，不触发审批/付款/关期/结项，不调用 PAT/MCP/AI 授权。

## 2. 输入契约

### 2.1 批量文件

- 仅接受 UTF-8（可选 BOM）CSV，单文件 ≤ 2 MiB、单批 ≤ 500 行；超出拒绝 `file_too_large` / `row_limit_exceeded`。
- 列集合必须精确匹配冻结模板；缺列/多列/未知列拒绝 `template_mismatch`。
- 首行 `source_id` 必填、≤100 字符、批内唯一；重复 `source_id_duplicate`。

### 2.2 冻结模板（invoicing 批量导入）

| 列 | 语义 | 必填 |
| --- | --- | --- |
| `source_id` | 导入来源稳定编号 | 是 |
| `direction` | `sales` / `purchase` | 是 |
| `counterparty_code` | 同公司往来方编号 | 是 |
| `invoice_number` | 发票号码 1–80 字符 | 是 |
| `invoice_date` | 业务日期 YYYY-MM-DD | 是 |
| `gross_cents` / `net_cents` / `tax_cents` | 整数分，gross=net+tax | 是 |
| `line_no` | 发票行号，同票内唯一 | 是 |
| `line_gross_cents` / `line_net_cents` / `line_tax_cents` | 行金额整数分，行合计=票面 | 是 |
| `allocation_kind` | `contract_line` / `settlement_open_item` / `settlement` | 是 |
| `allocation_ref` | 对应 owner 实体 id | 是 |
| `allocation_gross_cents` / `allocation_net_cents` / `allocation_tax_cents` | 分配金额整数分，行内合计=行金额 | 是 |
| `note` | 脱敏说明（可选，≤500） | 否 |

- 同票多行/多分配以 `invoice_number + line_no` 归组；跨行合计必须守恒。
- 客户端不得携带 actor、审批人、状态、税额资格结论、余额或账本字段。

### 2.3 提交请求（内部服务，复用 PH-353 形状）

```
commit_invoice_import(member, *, preview_ref, rows_digest, precheck_digest,
                      precheck_as_of_date, period, request_token,
                      expected_version, dry_run=True)
```

- `preview_ref`：PH-353 `ImportPreview` 引用；必须同 org、`draft/ready`、未消费、版本匹配。
- `precheck_digest` / `precheck_as_of_date`：PH-354 期间预检摘要；服务端重跑后比较，不一致 `precheck_stale`，存在 blocker `precheck_blocked`。
- `period`：必须与每个行业务日期同月（PH-353 已实现逐行期间绑定），否则 `row_date_invalid`。
- `request_token`：同 token 同输入返回原收据，改输入 `request_token_conflict`。
- `dry_run`：默认 true；true 只做预览/预检/冲突/行校验，不写收据、不写发票、不消费来源。

## 3. 逐行校验与 issue_code

| issue_code | 严重级 | 触发 |
| --- | --- | --- |
| template_mismatch / file_too_large / row_limit_exceeded | blocker | 模板/大小/行数 |
| source_id_duplicate / row_duplicate | blocker | 来源编号或发票行重复 |
| row_missing_required / row_type_invalid / row_date_invalid / row_amount_invalid | blocker | 字段/日期/金额格式 |
| invoice_number_duplicate | blocker | 同方向发票号已存在（PH-252 唯一口径） |
| line_total_mismatch / invoice_total_mismatch | blocker | 行合计≠票面、分配合计≠行 |
| allocation_ref_missing / allocation_ref_invalid / source_quota_exceeded | blocker | 分配来源不存在/越权/额度不足 |
| period_closed / period_locked / future_date | blocker | 期间/锁期/未来日期 |
| cross_org / cross_project / unauthorized / module_disabled | blocker | 门控/权限/归属 |
| precheck_stale / precheck_blocked | blocker | 预检摘要或阻塞项 |
| request_token_conflict / import_key_duplicate | blocker | 幂等冲突 |
| owner_validation_failed | blocker | PH-252 具名服务拒绝（透传其错误） |
| precheck_warning / result_truncated | warning | 非阻塞预检/截断 |

- 错误 envelope 复用 PH-353：`status`、`row_errors[]`（`row_no/field/issue_code/original_digest/source_ref/message`）、`precheck`、`conflicts`、`missing`、`truncated`；单次最多 200 行错误/1000 issue。

## 4. 幂等、收据与重复提交

- 稳定 `import_key = sha256(canonical_json({organization_id, kind:'invoicing', preview_id, rows_digest, source_batch_ref}))`；同 org + import_key 至多一个成功提交，重复 `import_key_duplicate`。
- 收据复用 PH-353 `ImportSubmissionReceipt`（append-only）：`request_token` 唯一、`input_digest`、`result_refs`（发票/行/分配引用）、`period`、`precheck_digest/as_of`、`conflicts_resolved`、`warnings`、`missing`。
- 同 token 同输入重放返回原收据；改输入 `request_token_conflict`；未知结果先查收据再重试，不盲重放。
- `dry_run` 不写收据、不消费预览、不写发票。

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

## 7. 与 PH-252/PH-274/PH-353 的边界

- **PH-274**：只读预览/模板/映射；本契约不修改预览规则。
- **PH-353**：提供持久预览、原子提交、收据、预检绑定与逐行期间校验；发票批量导入复用其服务形状，不复制其实现。
- **PH-252**：发票/行/分配/红冲的唯一写者；导入不得直接创建 Invoice/InvoiceLine/InvoiceAllocation。
- **PH-320**：税额资格确认与跨 owner 编排独立于导入；导入不自动确认税额资格。
- **PH-338/378/383**：实现片按实际 BASE 复核白名单；本文不授权实现。

## 8. 反例（实现片与评审须拒绝）

- 跳过预览直接写发票；第二套 CSV 解析/金额口径；客户端金额/审批人/状态；
- 部分提交；失败后保留半批；自动生成收入/现金/账本/税额资格；
- 重复发票号静默跳过；越权/跨 org/跨项目/关闭期间写入；
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
- 反例：重复发票号、行合计不守恒、来源额度不足、期间关闭、跨 org、越权、预检 stale/blocked、owner 拒绝；全部零半笔。
- 必须用 PH-252 真实服务夹具；PG 并发交 CI；浏览器 1440/390 仅页面前提。

## 10. 变更控制

- 模板列、issue_code、收据、幂等键变更需新规划槽；实现片不得静默削弱零半笔、预检重算或 owner 归属。
- 本文为契约冻结：实跑 `projectctl check` 与 `tests.test_projectctl tests.test_governance`；无实现/迁移/表/路由/产品代码；PH-338 未解除 proposed。
