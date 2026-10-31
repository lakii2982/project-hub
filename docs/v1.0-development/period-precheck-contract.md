# 期间预检 / 四类核对 / 差额只读契约（PH-363 冻结）

- 产出：PH-363（docs-only；**不实现**、不建表/迁移/路由/产品代码）。
- 依赖（均 done）：PH-291 跨域总览、PH-283 结算覆盖、PH-284 收入配对、PH-288 现金对账、PH-280 手续费配对；实现承接 PH-181（期间预检/锁定）、PH-185（四类核对/差额/复盘）后续片；**消费方：PH-353 导入提交**（预检 digest 绑定）。
- 权威：canonical schema 高于本文；本文只冻结**只读投影/服务契约**，不新增 canonical 实体/topic/command，不改变既有门禁/验收命令。若未来公开该投影为 query，须走 canonical version bump，不在实现 PR 自我授权。

## 1. 范围与总原则

1. **只读派生**：所有值按请求从 owner 事实派生；不写业务事实/账本/收入/现金/成本/未清，不建第二套核对规则，不自动修正差额。
2. **复用既有只读接口**：四类核对与预检直接调用既有 owner 只读服务（见 §4），issue_code/口径与 owner 一致；本文只定义聚合 envelope、差额公式、period/missing 语义与 digest。
3. **null/missing 不为 0**：上游缺失或不可判定 → 字段 `null` + `missing[]` code；`0` 只表示真实零值。差额任一侧缺失 → 差额 `null`，不得以 0 参与守恒断言。
4. **期间/锁期**：任何跨期/未来/已锁/已关闭事实产生明确 issue；引用既有 AccountingPeriod/PeriodCloseRun/ProjectClose 只读事实，不复制状态机。
5. **权限**：owner/finance/auditor 全量；pm 仅管理项目摘要；member 拒绝；模块停用 `history_read` 只读；跨 org 一律拒绝。只读不写 audit/receipt。

## 2. 输入契约

只读服务签名（与既有服务命名/参数风格一致，供 PH-181/185/353 实现复用）：

```
period_precheck_snapshot(member, *, scope='company'|'project', project_id=None,
                         period=None, as_of=None, sections=None)
```

| 参数 | 类型 | 语义 |
| --- | --- | --- |
| scope | enum | company（默认）或 project；pm 强制 project 且仅其管理项目 |
| project_id | Id/null | scope=project 时必填；越权/跨 org → 拒绝 |
| period | PostingPeriod（YYYY-MM）/null | 核对期间；默认按 as_of 当月；与事实 posting_period 比较 |
| as_of | BusinessDate/null | 截止业务日（含）；默认今天；未来日期产生 warning |
| sections | array<enum>/null | 取 `cash`,`settlement`,`revenue`,`cost` 子集；默认全部；未知项拒绝 |

- 请求参数不得携带金额/差额/状态；服务端从 owner 事实派生。
- canonical 对应：`query-results.schema.json#/$defs/period.precheck.get/v1`（`contract=period.precheck.get/v1`, `as_of_date`, `watermark`, `data.precheck`）；本投影的扩展字段（§3）必须由后续 canonical 规划决定是否进入公共 query。

## 3. 输出 envelope（只读投影 `period.precheck.snapshot/v1`）

```json
{
  "contract": "period.precheck.snapshot/v1",
  "scope": {"kind": "company|project", "project_id": "<id|null>"},
  "period": "YYYY-MM",
  "as_of_date": "YYYY-MM-DD",
  "watermark": {"namespace": "<owner>", "kind": "<kind>", "id": "<id>", "version": 1},
  "purpose": "period_close|import_submit|reconciliation_review",
  "precheck": {
    "can_close": false,
    "blocking_codes": ["period_closed", "settlement_remaining_mismatch"],
    "warning_codes": ["cash_unclassified", "tax_qualification_pending"],
    "status": "pending|approved|closed"
  },
  "checks": [{
    "domain": "cash|settlement|revenue|cost",
    "source_refs": [{"namespace": "...", "kind": "...", "id": "...", "version": 1}],
    "expected_cents": 100000,
    "actual_cents": 99000,
    "difference_cents": 1000,
    "counts": {"items": 12, "issues": 2, "missing": 0},
    "issues": [],
    "missing": []
  }],
  "differences": [
    {"rule_code": "cash_source_vs_classified", "domain": "cash",
     "expected_cents": 100000, "actual_cents": 99000, "difference_cents": 1000,
     "source_refs": [], "owner": "cash"}
  ],
  "issues": [],
  "missing": [],
  "summary": {
    "issue_count": 3, "blocking_count": 2, "warning_count": 1,
    "missing_count": 1, "domains": ["cash", "settlement", "revenue", "cost"]
  },
  "precheck_digest": "<sha256>",
  "estimate_label": "只读期间预检；derived values are not an editable balance"
}
```

- `precheck` 字段与 canonical `PeriodPrecheck` 对齐（status/can_close/blocking_codes/last_event_ref 可映射）；扩展 `checks/differences/issues/missing` 为只读派生，**不落库**。
- 返回行数上限与其他只读服务一致（≤200 明细、≤1000 issue）；超出标 `truncated=true` 并给 `missing` code `result_truncated`。

## 4. 四类核对与差额口径（复用既有 owner 接口）

| 域 | owner 只读接口（唯一口径来源） | 核对恒等式/差额 | 复用 issue_code（示例） |
| --- | --- | --- | --- |
| 现金 cash | `pmo_plugins.cash.reconciliation_services.cash_bridge_snapshot` / `cash_bridge_issues` | `posted_gross = classified + unclassified`；`difference_cents = posted_gross - classified`（真实 0 才为 0）；退款/退票/互转按 owner 既有覆盖校验 | classification_missing, reversal_event_missing, posting_amount_mismatch, claim_missing_or_mismatch, claim_net_mismatch, refund_over_gross, transfer_amount_or_date_mismatch |
| 结算 settlement | `pmo_plugins.settlement.coverage_read_services.open_item_coverage_snapshot` / `open_item_coverage_issues` | `gross = cleared + offset + remaining`（offset 未实现时按 owner 的 `offset_implemented=false` 标明，offset=null 而非 0）；`difference_cents = gross - cleared - offset - remaining` | cleared_over_gross, remaining_negative, remaining_mismatch, clearance_duplicate_active, clearance_cash_missing, clearance_cash_not_posted, cash_coverage_over_gross |
| 收入 revenue | `pmo_plugins.management_revenue.pairing_read_services.revenue_pairing_snapshot` / `revenue_pairing_issues` | `difference_cents = recognized - settled`；`contract_variance_cents = contract_value - recognized`；null 任一侧 → null | recognized_over_contract_line, recognized_after_termination, settled_exceeds_recognized, settlement_without_recognition, terms_without_recognition, duplicate_recognition, settlement_unreadable |
| 成本/手续费 cost | `pmo_plugins.costing.bank_charge_pairing_services.bank_charge_pairing` / `bank_charge_pairing_issues` | `difference_cents = charge_gross - approved_cost_claim - posted_cost`；未批准/未入账分别 issue，不合并成 0 | fee_exceeds_cash_gross, approved_without_cost, unapproved_cost_posted, cost_claim_amount_mismatch, cost_posting_amount_mismatch, cost_claim_duplicate, cost_event_duplicate |

- 跨域总览（PH-291 `delivery.operations_read_services.ops_reconciliation_overview`）用于汇总计数与 sections；四类核对以各 owner 服务为准，不重复实现规则。
- 期间预检在四类核对之上只做 **聚合与期间/锁期判定**，不改写 owner issue_code/severity。

## 5. issue / missing schema 与严重级

```json
{"issue_code": "settlement_remaining_mismatch", "severity": "blocker|warning",
 "domain": "cash|settlement|revenue|cost|period",
 "source_refs": [{"namespace":"settlement","kind":"open_item","id":"...","version":1}],
 "owner": "settlement", "expected": {"cents": 100}, "actual": {"cents": 90},
 "difference_cents": 10, "message": "只读派生说明", "remediation_hint": "open_period_adjustment",
 "missing_code": null}
```

```json
{"missing_code": "cash_bridge_unavailable|revenue_pairing_unavailable|bank_charge_pairing_unavailable|period_not_found|tax_qualification_missing|offset_not_implemented|result_truncated",
 "domain": "cash|settlement|revenue|cost|period",
 "source_ref": {"namespace": "...", "kind": "...", "id": "...", "version": 1},
 "reason": "上游未实现/不可读/未配置",
 "severity": "blocker|warning"}
```

- **severity 映射（冻结）**：
  - `blocker`：期间已关闭/已锁、未来日期、跨 org、结算 remaining 不守恒、收入超额确认、成本重复入账、缺少必需期间。
  - `warning`：未分类现金、offset 未实现、缺税额资格、缺费率、截止日截断、非阻塞的 source 不可读。
- `expected/actual/difference` 在缺失时为 `null`；禁止用 0 表示缺失。0 只在真实零值时出现（如真实无未清项）。
- 同一 issue 以 `issue_code + 排序后的 source_refs` 去重；不按消息文本去重。

## 6. 期间 / 锁期 / 关期边界

1. `as_of` 之后的事实不得参与（未来日期请求本身产生 `future_as_of` warning；`occurred_on > as_of` 的事实排除）。
2. 期间状态引用既有 `AccountingPeriod`（status/locked_through/closed_through）：期间 `posted/closed` 或 `locked_through >= occurred_on` → `period_closed`/`period_locked` blocker。
3. 关期/结项引用既有 delivery `PeriodCloseRun`/`ProjectClose` 只读事实：已关闭期间只允许查历史（history_read），不可用于新提交；重开链只读校验，不复制状态。
4. 项目结项/归档不自动阻塞历史期间查询；但新导入/提交必须走开放期（PH-353 依此拒绝）。
5. 未来业务日、跨期 `posting_period` 与事实日期不一致 → blocker/warning 按上表。

## 7. 跨 owner 只读引用

- 只读引用 owner 事实，不复制表、不写 owner；`source_refs` 采用 source-registry 的 namespace/kind/id/version。
- 覆盖域：cash（CashTransaction/Refund/BankReturn/InternalTransfer/Classification）、settlement（Settlement/OpenItem/Clearance/Offset）、revenue（RevenueRecognition/RevenueAdjustment/Acceptance/ServiceConfirmation）、cost（BankCharge/CostClaim/CostPosting）、periods（AccountingPeriod/PeriodCloseRun/ProjectClose）。
- 任一引用不可读/跨 org → `missing` 或 `blocker`，不得静默按 0 处理；模块停用仍可 `history_read` 读取其历史只读投影。

## 8. `precheck_digest`（供 PH-353 导入提交）

- 定义：`sha256(canonical_json({contract, scope, period, as_of_date, watermark, issues, differences, missing}))`，canonical_json 按 key 排序、金额整数分、日期 ISO、refs 按 namespace/kind/id 排序；排除 `precheck_digest` 自身。
- PH-353 提交时：请求携带 `precheck_as_of_date` + `precheck_digest`；服务端重跑分享只读预检，digest 不一致或出现新 blocker → 拒绝提交（`precheck_stale`/`precheck_blocked`），不得以客户端 digest 代替服务端重算。
- digest 不是金额授权，仅绑定“提交前预检快照”；重跑仍受期间/锁期/权限/公司锁约束。

## 9. 权限与只读纯度

- owner/finance/auditor：全量；pm：仅 `scope=project` 且管理项目摘要（无全公司/跨项目明细）；member：拒绝；跨 org：拒绝。
- 只读：不写 audit/receipt/session/outbox，不建持久预检表；`precheck_digest` 由调用方（PH-353）随提交持久化，本契约不新增表。
- 停用模块：`history_read` 可查看历史只读结果；写/提交路径仍被停用门控拒绝。

## 10. 证据与验收命令（实现片按实际 BASE 复核）

```sh
python scripts/projectctl.py check
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test tests --verbosity 1
python manage.py verify_integrity --json
```

- 金样例（未来 PH-181/185/353 实现片）：四类核对各一条守恒/差额 + null/missing 反例；期间关闭/锁定/未来日期拒绝；pm/member/跨 org 拒绝；`precheck_digest` 稳定/变更检测；只读零写计数。
- 反例：缺数据按 0 通过、客户端金额、复制 owner 规则、写业务事实、跨 org 泄露、跳过预检直接提交。
- 本文为契约，**无实现/测试/运行结果**；当前状态：not implemented（PH-181/185 后续；PH-353 消费）。

## 11. 与既有任务/文档的边界

- PH-181/PH-185 为遗留规划片（proposed）：实现须按实际 BASE 复核白名单与 DoR，不得以本文直接开工。
- PH-353 导入提交：只消费本文 envelope/digest，不复制差额公式；其预检失败拒绝路径属实现片。
- PH-291/283/284/288/280 的既有只读接口/issue_code 为唯一口径；本文不新增第二套核对规则，也不改其行为。
- 变更控制：若需公开 query/字段，先做 canonical 契约规划（version bump），不得在实现 PR 自我授权。

## 12. 未验证

- 四类核对在期间预检聚合下的端到端实现、预检 digest 与 PH-353 提交的联动、浏览器/CI/生产证据均未执行（本片 docs-only）。
- 现金 offset（PH-241）、insights 读模型（PH-272）等未交付能力以 owner 的 `offset_implemented=false`/missing 语义呈现，不假设已实现。
