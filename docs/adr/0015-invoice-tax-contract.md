# ADR 0015：发票/价税分离与红冲契约冻结（invoicing owner）

状态：PH-293 契约冻结产出（docs-only；owner 已由 ADR 0010/PH-259 决定为新建 `pmo_plugins.invoicing`）；实现片由 PH-252 按本 ADR 落地。本文不授权开工、不创建代码/迁移，也不改 contract schema。日期：2026-10-31。编号核验：`docs/adr/` 已有 0001–0014（0010 invoicing 归属、0011 time、0012 importing、0013/0014 insights），**0015 空闲**；文件名按 BASE 白名单 `docs/adr/0015-invoice-tax-contract.md`。

## 背景与既有事实（按实契约/已交付切片）

- canonical 契约已冻结（PH-137 候选，与归属无关）：
  - `Invoice`：required `id/organization_id/version/status/created_at/direction/counterparty_id/invoice_number/gross_cents/net_cents/tax_cents/invoice_date`；`direction` ∈ {sales, purchase}；`status` ∈ {draft, active, posted, reversed, superseded}；金额为 `CnyCents`（整数分）。
  - `InvoiceLine`：`invoice_id/line_no/gross_cents/net_cents/tax_cents`（同 status 枚举）。
  - `InvoiceAllocation`：`invoice_line_ref/business_source_ref/gross_cents/net_cents/tax_cents`（含税/税额分配守恒，见 A11/T28）。
  - `InvoiceReversal`：`original_invoice_ref/reason/reversal_date/line_reversals/allocation_reversals/gross_cents/net_cents/tax_cents`（SignedCnyCents，负值冲销）。
  - `TaxQualification`：`source_ref/tax_cents/qualification_status`；`status` ∈ {pending, confirmed, superseded}；`qualification_status` ∈ {unknown, recoverable, not_recoverable}；`evidence[]`。
  - 命令：`invoicing.invoice.record/v1`、`invoicing.lines.allocate/v1`、`invoicing.invoice.reverse/v1`；事件 `invoicing.recorded.v1`、`invoicing.lines_allocated.v1`、`invoicing.reversed.v1`。
- 已交付上游（本 ADR 的依赖证据，均 done）：contracts 三模板批准/条款与变更终止（PH-214/PH-265/PH-287）、settlement 结算/未清/核销（PH-224/PH-232）、management_revenue 收入确认与配对（PH-284）、costing 成本与手续费（PH-244/PH-250）、core 期间/锁/审计/outbox。
- PH-252（发票实现首片，proposed）spec 的 draft→active 登记口径与本 ADR 一致：私有 draft 工作流不输出 canonical，登记成功即 canonical `active`。
- 业务口径：A11（税与发票）、T07/T08（进项抵扣与无票费用）、T28（含税分配守恒）、WO-07（登记/分配/红冲）。发票登记不自动产生收入、成本、应收应付或现金。

## 冻结决策

### 1) 实体与状态机

- **发票事实一旦登记即不可变**：`Invoice`/`InvoiceLine`/`InvoiceAllocation` 的金额、对方、日期、号码、方向登记后不可编辑；纠错只能追加 `InvoiceReversal`（见 3）。
- canonical status 语义（首片）：
  - `draft`：仅私有 UI/导入暂存，**不输出 canonical**；未登记前无 canonical 行。
  - `active`：`invoicing.invoice.record/v1` 成功登记后的唯一起始 canonical 状态（发票事实已记录，不代表收入/应收/成本/现金，也不表示已入账）。
  - `posted`：预留给后续“发票入账/税务申报”等会计动作（经另立规划与账本 owner）；PH-252 首片不产生。
  - `reversed`：经 `invoicing.invoice.reverse/v1` **全额**冲销后，由 reversal 服务把原票 status 置 `reversed`（仅状态位变化，金额/行/分配永不修改；原始行保留）。
  - `superseded`：仅当同一业务关系被明确替代（如导入重复纠正）时由 owner/finance 具名服务置位；首片不产生，需后续规划。
- `InvoiceLine.status` 与所属 Invoice 同步；分配不新建行。
- 红字发票：首片以 **`InvoiceReversal` 表达红冲事实**（关联原票、行/分配冲销；红票本身不代表已退款）；若业务必须保留独立红字票号实体，属 canonical 扩展，需另立规划（ADR 0015 不改 schema）。

### 2) 金额、价税分离与税额资格

- 金额一律整数分（`CnyCents`）；`gross_cents = net_cents + tax_cents` 恒等；行和 = 单据总额；分配含税/税额不超原票未分配余额（A11/T28）。
- 税率/税种是**业务输入/快照**，不从行业名称推断；税率快照保留在发票行证据/元数据，不重算历史。
- 含税→不含税/税额推导：按行以 Decimal 计算，`ROUND_HALF_UP` 到整数分；末行/末项吸收舍入差保证恒等；不接受客户端提交的税额/不含税覆盖。
- `TaxQualification`：`pending(unknown)` 的进项税默认按含税暂计成本（owner=costing 的既定口径）；后续确认 `recoverable`/`not_recoverable` 时，在开放期由 costing owner 做成本/税额差额更正（A11/T08），invoicing 只记录资格事实，不直接改成本/账本。
- 销项税不进入管理收入；发票金额不自动生成收入/结算/应收应付。

### 3) 红冲/负值冲销（append-only）

- `invoicing.invoice.reverse/v1` 创建 `InvoiceReversal`：负值 signed 金额、`original_invoice_ref`、行/分配冲销明细、原因与日期；冲销金额 ≤ 原票未冲销余额；同一行/分配不可重复超冲。
- 原票与原始行/分配**永不修改或删除**（除全额冲销后的 status=reversed 状态位）；历史期间锁定后不得补开红冲跨期改写历史，只能按 open-period 规则在开放期追加。
- 部分冲销：原票保持 `active`（status 不变），由 append-only `InvoiceReversal` 与只读派生 remaining 表达已冲部分；全额冲销：原票 `active → reversed`（仅状态位变化），reversal 仍为独立 append-only 事实。
- 状态转移表（首片冻结）：

  | 当前状态 | 动作 | 结果状态 | 说明 |
  | --- | --- | --- | --- |
  | 无 | `invoicing.invoice.record/v1` | `active` | 私有 draft 不输出 canonical；金额/行/分配登记即固化 |
  | `active` | `invoicing.invoice.reverse/v1`（部分） | `active` | status 不变；追加负值 reversal，remaining 只读派生 |
  | `active` | `invoicing.invoice.reverse/v1`（全额） | `reversed` | 仅状态位变化，原票金额/行/分配不改 |
  | `reversed` | 再次 reverse | 拒绝 | 已全额冲销不可重复冲销 |
  | `posted` | — | — | 预留后续入账/税务动作，首片不产生、不进入本表 |

  `superseded` 同理为首片不产生的后续规划状态；任何未列出的转移一律拒绝。
- 红冲与收入/现金的边界：收入冲销只能走 management_revenue owner 的冲销命令；退款/银行退回只能走 cash owner；invoicing 不直接写收入/现金/账本/未清。

### 4) 命令/服务与幂等

- 具名服务（PH-252 实现）：`record_invoice(...)`、`allocate_invoice_lines(...)`、`reverse_invoice(...)`；浏览器/API/导入共用同一服务；不新增 canonical topic/command。
- 幂等：`request_token` 收据（org+token 唯一、输入 digest）；同 token 重放原结果、改输入冲突；`invoice_number` 同 org 内查重（只作查重，不作全局 ID）；公司锁串行化写；dry_run 零副作用。
- 审计：业务写经既有 audit/outbox；失败不产生半笔（发票/行/分配/reversal/资格一致）。

### 5) 角色与门控（首片）

| 操作 | owner | finance | auditor | pm | member |
| --- | --- | --- | --- | --- | --- |
| 登记/分配/红冲/税额资格确认 | ✓ | ✓ | 只读 | 只读其项目摘要 | 拒绝 |
| 发票列表/详情/分配/冲销/资格只读 | ✓ | ✓ | ✓ | 其管理项目摘要 | 拒绝 |

- 模块停用：发票历史只读（history_read 页面）；写入口守卫；不新增共享管理员密钥/审批工具。

### 6) 与 PH-252 的关系及只读核对接入点

- PH-252 是**唯一写侧实现片**（保持 proposed，依赖 PH-150/PH-155 的 legacy 契约边界由本 ADR + 已交付 contracts/settlement 能力覆盖的部分在 PH-252 规划时复核；不得在本 ADR 内假装实现完成）。
- PH-252 按 ADR 0010 新建 `pmo_plugins.invoicing`（manifest 8 字段 requires={contracts,settlement,cash,management_revenue,costing}、自有迁移 0001、settings/PMO_PLUGIN_MODULES/delivery-urls root 单写、canonical route 先入 domain_metadata）。
- 只读核对接入点（后续为 PH-291/PH-283/PH-284/PH-304 工作台/导出提供数据，不复制表）：发票列表/详情（active/reversed/remaining）、按结算/收入引用反查分配、税额资格与待确定清单、红冲链、发票-结算/收入只读配对。
- 精确实现白名单/反例由 PH-252 spec 按实际 BASE 冻结；本 ADR 只给边界。

## 明确不做

- 不实现、不建 scaffold、不写代码/迁移、不改 canonical schema/topic/command。
- 不接税务查验/PDF/邮件/客户门户；不做多币种/汇兑；不做自动收入/应收/成本/现金；不做税额自动抵扣申报。
- 不将红字票新建为独立 Invoice 实体（如需，另立规划）。

## 未决/后续

- 红字票号/全电票标识的独立实体是否必要（PH-252 实现时按真实客户要求复核，必要时新规划）。
- 税额抵扣报表/税务申报导出留后续；多币种见 PH-308/PH-321；发票写侧见 PH-252/PH-320。
- 本 ADR 为契约冻结，不含运行时/浏览器/PG 验证。
