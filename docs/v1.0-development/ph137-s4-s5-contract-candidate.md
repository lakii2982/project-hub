# PH-137 · S4/S5 工作包逐包正式薄片契约候选（第二次修订）

状态：**候选（candidate），未晋级、未独立复核、未执行产品验收**。本文只是 PH-137 授权内的规划输入，不是 BASE 契约、开工许可或派遣授权。正式 PH 编号、`project/tasks.json` 状态/依赖/白名单/固定验收及需求追踪回填均由主控在独立规划 PR 中冻结。本文不写产品代码，不解除任何任务 blocker。

- 权威 BASE：`f1115cfd043a9f3a9a7ffc05d6a88d772e324920`；PH-137 契约摘要：`39d7119aedc68df2a269a95fc69b46f66680c82b5b362a46d42320c9edb0987f`。
- 覆盖：`work-packages.json` 的 S4 八包、S5 十一包，共 19 包、49 条需求。每包只设一个主交付占位 `PHC-S4-nn` / `PHC-S5-nn`；其他 PH 只能标支持、集成或验证，不能集合作主。
- 拆分原则：按可验收行为合并模型、业务服务、浏览器入口和跨域验证，不机械生成 M/S/U/X 四张 PR。本批 19 包各形成一个完整行为片；空模型片、空服务片和只写“集成”的片均不设立。确需新增 schema 的能力先列为契约缺口，不以实现 PR 自扩。

## 1. 唯一输入、版本与哈希

以下路径是唯一可编辑来源；本文只引用，不复制第二套字段定义。

| 输入 | 绑定 |
| --- | --- |
| 包/依赖/候选范围 | `docs/v1.0-development/work-packages.json`，`artifact_kind=development_design_not_task_authority` |
| 需求唯一主 owner/专项/UAT | `docs/v1.0-development/requirement-trace.json`；正式 PH 仍回填各需求原有 `ph_task_ids`/`evidence_refs` |
| 字段/命令/查询/事件 | `docs/v1.0-development/contracts/` 的 PH-132 canonical 文件，禁止修改 schema 迎合本文 |
| 政策/切写 | `policy-decisions.md`、`legacy-cutover.md`：一合同一项目、三模板分别按验收/确认计费分钟/确认期间确认收入；新月度人工池组取整，旧账不重算 |
| SDK/安全 | 只引用主控附后的“SDK-1字段与兼容契约候选”和“首个正式薄片：依赖安全修复候选”；顺序为 PH137 规划合入→P0 安全片→SDK-1→SDK-2→SDK-3→WP-CORE |
| 旧任务 | `project/specs/PH-001.md`—`PH-020.md` 与 BASE `project/tasks.json`；只记录承接，不宣称 done |

黄金夹具已经交付，不再写“待冻结”：`fixture.json#/fixture_id=PH134-SYNTHETIC-20261028`、`#/version=1.0`、`#/status=preparation_fixture_only`。

- `fixture.json` SHA256：`9cb2d8726ef212dda8966671e82f31e3ebfb5a617e874f5198c035b25342b318`。
- `command-samples.json` SHA256：`575e0f77def7ee1ce770fa5be11e0b9ed4aa2b51ea1cae9de3a71cdb49fa0ad5`。
- `requirements-coverage.json` SHA256：`7f8c1049e02e946ff44142d331723b4704fe2ee3d290e37212aebc6f5cc888af`。
- 既有结构检查记录 `sample-verification.json` 只证明 99 正例、8 拒绝例、92 来源、18 UAT、137 需求的结构/哈希/算术/schema 检查；`product_commands_executed=false`、`product_uat_passed=false`。本文没有重跑该检查，也不把它写成产品通过。
- schema 哈希绑定在 `fixture.json#/contract_artifact_sha256`；本文实际解析时以当前 canonical 文件为准，若最终规划时哈希变化必须重审全部引用。

指针别名（下文均为真实 JSON Pointer，`/` 在定义名中转义为 `~1`）：

- `CMD`=`contracts/commands.schema.json`，`CRES`=`contracts/command-results.schema.json`；`DTO`=`contracts/write-dtos.schema.json`。
- `ENT`=`contracts/entities.schema.json`，`QRY`=`contracts/queries.schema.json`，`QRES`=`contracts/query-results.schema.json`，`EVT`=`contracts/events.schema.json`。
- `FX`=`fixtures/fixture.json`，`RT`=`requirement-trace.json`。例如 `CMD#/$defs/cash.transaction.record~1v1/properties/payload` 可定位到命令 payload，不是具名命令的口头占位。

## 2. 所有行为片的共同契约

### 2.1 请求、返回、状态、权限与错误

- 所有写命令沿用 `CMD` 既有信封：`contract/request_id/idempotency_key/payload` 必填；`source/expected_version/occurred_on/posting_period/dry_run` 只按各定义使用。缺 `dry_run` 由服务端选 true；显式 false 不增加 PAT 审批、支付或锁账权限。
- 所有命令返回只引用对应 `CRES`：`result_type∈{created,existing,dry_run}`，并含 `entity_refs/event_ids`；dry_run 的 `event_ids=[]`，且无事实、账本、审计业务事件、Outbox 或幂等持久副作用。查询返回只引用对应 `QRES` 的 `contract/as_of_date/watermark/data`。
- 持久 `status` 只能使用 `ENT`/`DTO` 已声明枚举。银行待匹配/待认领是 `CashClassification.classification=unmatched` 与归属/核销关系派生状态，**不得改写 `CashTransaction.status`**。业务“已锁期”由 `AccountingPeriod.closed_through`/`PeriodClose` 表达，也不发明 open/closed 枚举。
- actor、企业、角色、项目范围和敏感字段权限由认证上下文在服务端推导；越企业/不可见项目按隐藏策略返回 `not_found` 或 `permission_denied`，且不泄露对象。owner/finance 才可读资金账户敏感信息和个人工资池；PM/员工响应体不得含个人工资/费率。本人/制单人不得自审。
- 固定错误锚点使用既有目录：`FX#/rejection_catalog/R-CROSS-TENANT`、`R-UNASSIGNED`、`R-DISABLED`、`R-DUP-SOURCE`、`R-IDEMPOTENCY-CONFLICT`、`R-OVER-CLEAR`、`R-DUP-RETURN`、`R-CLOSED-PERIOD`、`R-DUP-CASH`、`R-FUTURE-DATE`、`R-FLOAT`、`R-COVERAGE`、`R-POLICY`、`R-STALE-VERSION`。每个拒绝均保持零业务副作用；安全拒绝审计不冒充财务事实。

### 2.2 来源唯一、事务与锁序

- 银行事实来源唯一严格使用 `FX#/source_consumption_key`：企业＋消费用途＋原始 namespace/kind/stable id＋明细/期间/覆盖范围；`external_source_key` 只在所属资金账户和原始银行来源命名空间内解析稳定身份。**金额、银行日、请求 key、渠道和 SourceRef.version 均不进入来源唯一键**。改金额或换请求 key 不得再消费同一银行事实；相同规范化输入返回原结果，内容变化走 `R-IDEMPOTENCY-CONFLICT`/冲正更正。
- 所有金融写按：输入清洗→事务→公司锁→锁内重查成员/项目/期间/插件→期间/项目→来源→资金/未清/分配（类型＋稳定 ID 排序）→来源消费/幂等/版本/上限→事实/账本/审计/Outbox→提交。跨域服务不自行 commit；失败整体回滚，结果未知先用原 key 对账。
- `settlement.clearance.*`、`settlement.offset.create` 是 `internal_cash_orchestration_only`。cash 验证资金并在同公司锁/同事务构造服务器只读 `FundingAvailabilityProof`；不得从 JSON 接收可用余额，不让 settlement 反向 import cash 模型。
- 金额整数分、计算 Decimal＋ROUND_HALF_UP；账本/审计只追加，旧表、旧 PK、`Book=cost/cash_in/cash_out`、原符号、哈希链与历史触发器保留。结算、开票、收款不再次造收入；付款不再造成本。

### 2.3 文件、迁移、DoR/DoD 与固定验收

- 下文“执行文件”是逐片候选白名单，必须原样进入正式 PH 后才生效；不再用整域 `**` 代替具体文件。共享 `pmo/urls.py`、`pmo/settings.py`、`templates/base.html`、`core/models.py`、`core/services.py`、`verify_integrity.py` 由主控接线。
- 每片 `domain_metadata.py` 只实现主控 SDK-1 候选规定的描述，不复制字段或 ready 条件。新增迁移统一写“root-only：对应 app `migrations/` 下一号”，由集成者按当前叶生成；执行者不预占序号、不改已应用迁移。
- **共同 DoR**：用户明确允许该片；正式 PH 已合入可信 BASE；所有直接 WP 对应 PH done；canonical/schema/夹具/政策哈希一致；契约缺口已独立关闭；隔离写范围及 PG/浏览器环境可用。19 片当前全部建议 **proposed**，没有首批 ready。
- **共同 DoD**：实际专项＋共同命令绑定最终候选；S 级不同上下文独立复核；当前 required CI 与正常 PR 合入；报告/交接/追踪一致；未执行项写入 unverified。代码存在、schema 通过或 SQLite 通过都不是完成证据。
- 每片固定共同 argv（本轮未运行）：

```sh
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test tests --verbosity 1
.venv/bin/python manage.py verify_integrity --json
```

专项 argv 在各片列出；金融写必须真实 PostgreSQL 多连接，浏览器行为必须真实浏览器。外部 PG、Linux/恢复、TLS、客户签收与发行授权继续保持未验证外门。

## 3. S4：八个完整行为片

### 3.1 WP-BANK → `PHC-S4-01`（唯一主交付：账户、银行事实、归属与待匹配）

- **主需求/依赖**：V1-MD-05、V1-CSH-01/02/12；依赖 WP-AR、WP-AP、WP-IDENTITY。契约 C02/C03/C07/C08。
- **模型/接口**：`ENT#/$defs/FundingAccount`、`CashTransaction`、`CashClassification`、`CashAllocation`；可写字段仅 `DTO#/$defs/FundingAccountDraft`、`CashTransaction`、`CashClassification`、`CashAllocation`。命令为 `CMD#/$defs/cash.funding_account.draft.save~1v1`、`cash.transaction.record~1v1`、`cash.allocation.create~1v1`，返回取同名 `CRES`；查询为 `QRY#/$defs/cash.transaction.get~1v1`、`cash.account_coverage.get~1v1` 及同名 `QRES`；事件只取 `EVT#/$defs/cash.transaction_recorded.v1`。
- **服务/状态**：`CashTransaction.status` 保持既有 `draft/active/posted/reversed/superseded`；`unmatched` 只存在 `CashClassification.classification`，认领由有效 allocation/clearance 关系派生。余额由 `FundingAccount.opening_cents/opening_on`＋有效交易派生；draft 不包含 opening/current balance，可写 DTO 不含组织/状态/余额。真实银行日取 `transaction_date`，入账期独立取命令 `posting_period`；更正追加反向事实，不删流水。
- **浏览器/跨域**：账户草稿、流水登记/CSV、待认领与归属页全部调用上述服务；遮蔽标识只在 owner/finance 响应出现，敏感读取写 `SensitiveReadAudit`。与 C07/C08 验证同一交易归属合计不超 `gross_cents`，核销不新增银行事实。
- **执行文件**：`pmo_plugins/cash/{__init__.py,apps.py,plugin.py,domain_metadata.py,models.py,services.py,forms.py,views.py,urls.py}`、`pmo_plugins/cash/templates/cash/{account_form.html,transaction_list.html,transaction_form.html,allocation_form.html}`、`tests/test_v1_cash.py`。root-only：`pmo_plugins/cash/migrations/<next>.py`、`pmo/urls.py`、`pmo/settings.py`、`templates/base.html`。
- **正反例/夹具**：`FX#/uat_sequences/12/steps/0` 毛额10000、手续费100、净现金9900、成本100；`steps/2` 未分类700后净现金10600且仍可见待认领。`additional_cases/0` 同一流入10000分归属6000＋4000，只一笔现金。重复外部来源改金额、银行日、version、渠道或请求 key→原结果或冲突，第二现金/账本/审计业务事件为0；未来日、锁期、越企业、越权归属和分配10001→拒绝且零业务副作用。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_cash --verbosity 1`＋共同 argv＋PG 同来源双连接＋真实浏览器账户/流水/权限。DoR 另需 WP-ONBOARDING 提供已批准期初引用。当前产品/PG/浏览器/迁移全部未验证；目标环境账户覆盖与客户确认仍是外门。

### 3.2 WP-CLEARING → `PHC-S4-02`（唯一主交付：核销、预收预付抵扣与撤核）

- **主需求/依赖**：V1-SET-05/06/10、V1-CSH-03/04；依赖 WP-BANK、WP-AR、WP-AP。契约 C02/C07/C08。
- **模型/接口**：`ENT#/$defs/Clearance`、`Offset`、`AdvanceBalance`；DTO 只取同名 `DTO#/$defs/Clearance`、`Offset`。命令/返回为 `CMD`/`CRES#/$defs/settlement.clearance.create~1v1`、`settlement.clearance.reverse~1v1`、`settlement.offset.create~1v1`；事件 `EVT#/$defs/settlement.cleared.v1`、`settlement.clearance_reversed.v1`。查询沿用 `QRY#/$defs/settlement.open_item.get~1v1`、`cash.transaction.get~1v1` 及同名 `QRES`。
- **服务/状态**：现金域为编排 owner；创建按现金→未清稳定 ID 加锁，撤销只锁原 clearance。有效 clearance/offset 决定剩余未清与 `AdvanceBalance.available_cents`，不把关系状态塞入 `CashTransaction.status`。跨项目/跨收付对象需同企业及已批准例外；无批准拒绝。抵扣、核销、撤核均不新增现金、收入或成本。
- **浏览器/跨域**：核销/抵扣页只显示服务器返回的可用额，提交原来源引用；撤核显示原关系与原因。验证 cash＋settlement 同事务、Outbox 同源、失败无半张关系；与 WP-FORECAST 只共享来源覆盖，不复制余额。
- **执行文件**：`pmo_plugins/cash/{services.py,forms.py,views.py,urls.py}`、`pmo_plugins/cash/templates/cash/{clearance_form.html,offset_form.html}`、`pmo_plugins/settlement/{__init__.py,apps.py,plugin.py,domain_metadata.py,models.py,services.py}`、`tests/test_v1_cash.py`、`tests/test_v1_settlement.py`。root-only：两 app 各自 `migrations/<next>.py`、共享 URL/settings/base 接线。
- **正反例/夹具**：`FX#/uat_sequences/13/steps/0` 两连接各核销8000，只有一笔8000成功，现金仍10000、未清2000、未分配现金2000。`FX#/uat_sequences/2/steps/3` 预收抵扣31800后应收21200、预收0、现金仍31800；`steps/4` 再收21200后累计现金53000、应收0。余额6000核销7000→`R-OVER-CLEAR`；跨项目无批准/跨企业→拒绝；同 key 同输入→原 receipt；重复撤核/抵扣再记现金或收入→拒绝/零新增。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_cash tests.test_v1_settlement --verbosity 1`＋共同 argv＋UAT-14 PG 屏障＋浏览器。DoR 需 WP-BANK 的来源证明及 AR/AP 未清服务 done。当前产品/PG/浏览器未验证；客户跨项目批准配置是外部门。

### 3.3 WP-REFUND → `PHC-S4-03`（唯一主交付：退款、银行退回与恢复）

- **主需求/依赖**：V1-SET-07、V1-CSH-05；依赖 WP-CLEARING、WP-CREDIT、WP-SPEND-CORRECTION。契约 C02/C07/C08。
- **模型/接口**：`ENT`/`DTO#/$defs/Refund`、`BankReturn`；命令及同名返回 `cash.refund.record/v1`、`cash.return.record/v1`；事件 `EVT#/$defs/cash.refunded.v1`、`cash.returned.v1`。原现金选择器只接受 `kind=cash_transaction`；恢复关系继续由 `settlement.clearance.reverse/v1` 完成，不能把撤核当退款。
- **服务/状态**：公司锁→期间→原现金/原预收预付→既有退款/退回（稳定 ID）→新事实；可退上限为原有效事实减已退/已退回。部分退款可多次至0；退回只恢复对应未清一次；费用/人工成本更正由 WP-SPEND-CORRECTION 独立追加，不自动反向成本。
- **浏览器/跨域**：退款/退回页显示原事实、已退累计与服务器可退额；锁期禁用并给原因。跨域验证退款产生真实现金，撤核只恢复关系，银行退回与恢复未清原子；失败两侧均不变。
- **执行文件**：`pmo_plugins/cash/{models.py,services.py,forms.py,views.py,urls.py}`、`pmo_plugins/cash/templates/cash/{refund_form.html,return_form.html}`、`pmo_plugins/settlement/services.py`、`tests/test_v1_cash.py`、`tests/test_v1_settlement.py`。root-only：cash/settlement 迁移下一号及共享接线。
- **正反例/夹具**：`FX#/uat_sequences/8/steps/0` 预收31800、退款10000后净现金21800、预收21800；`steps/1` 原收10000退回3000后累计净现金28800、应收3000。`FX#/uat_sequences/13/additional_cases/2` 两连接各退8000，对原预收10000最多8000有效、余2000，失败方跨域事实全回滚。重复退回→`R-DUP-RETURN`；超退/锁期/未来日→拒绝；撤核新增现金或退款自动反成本→0。
- **专项 argv/DoD**：现金/结算专项 argv 同 PHC-S4-02＋共同 argv＋PG 退款竞争＋浏览器。DoR 需原预收预付、费用更正接口 done。当前全部未执行；真实银行退回凭据与客户批准是外部门。

### 3.4 WP-PAYROLL-CASH → `PHC-S4-04`（唯一主交付：工资社保税费现金）

- **主需求/依赖**：V1-CSH-06、V1-CST-11；依赖 WP-BANK、WP-LABOR-POST。契约 C06/C08。
- **模型/接口**：银行现金仍用 `ENT`/`DTO#/$defs/CashTransaction`，分类只读定义取 `ENT#/$defs/CashClassification`（既有 `payroll/tax`），人工成本只取 `ENT#/$defs/SalaryPool`、`CostEvent`。现金命令/返回用 `CMD`/`CRES#/$defs/cash.transaction.record~1v1`；查询用 `QRY`/`QRES#/$defs/cash.transaction.get~1v1`、`costing.reconciliation.get~1v1`。
- **服务/状态**：公司锁→期间→同企业/员工/月的已批准 SalaryPool 来源→现金事实；公司成本只由月度人工池确认一次，分配不新增公司成本，付款只新增现金。旧人工仍按原快照，不用新月度方法重算；工资、社保、税费去向分别核对，不与项目费用重复汇总。
- **契约缺口 GAP-PAYROLL-CASH**：当前 `cash.transaction.record/v1` payload 没有 SalaryPool 引用或 `CashClassification` 写接口，不能把 envelope `source` 同时冒充银行来源和工资池来源。正式实现前须由独立契约变更换新/扩展 canonical 命令、结果和反例；本片不得自创 `PayrollPayment` DTO，也不得在 DoR 未闭合时 ready。
- **浏览器/跨域**：工资付款页展示银行事实、同月池引用及权限裁剪后的去向汇总；PM/员工不可读个人池明细。costing＋cash 逐分核对池2400000、项目/非项目/未分配守恒，现金1200000不再确认成本。
- **执行文件**：`pmo_plugins/cash/{models.py,services.py,forms.py,views.py,urls.py}`、`pmo_plugins/cash/templates/cash/payroll_payment_form.html`、`pmo_plugins/costing/services.py`、`tests/test_v1_cash.py`、`tests/test_v1_costing.py`。root-only：cash 迁移下一号、共享接线；schema 变更不在本执行片白名单。
- **正反例/夹具**：`FX#/uat_sequences/3/steps/0`、`FX#/uat_sequences/3/steps/1`、`FX#/uat_sequences/3/steps/2` 依次为池成本2400000/现金0，分配后成本仍2400000/现金0，付款1200000后成本仍2400000/现金-1200000。同银行事实改请求 key→不重复现金；同池换政策/方法版本→不得再确认一份全额成本；未批准池、跨企业员工/月、锁期→拒绝；付款产生第二个 `costing.cost_posted.v1`→失败。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_cash tests.test_v1_costing --verbosity 1`＋共同 argv＋PG 同源竞争＋浏览器权限。DoR 明确包含 GAP-PAYROLL-CASH 关闭；当前全部未验证，客户批准工资组成/支付凭据是外部门。

### 3.5 WP-FEE-TRANSFER → `PHC-S4-05`（唯一主交付：手续费与内部互转）

- **主需求/依赖**：V1-CSH-07/08；依赖 WP-BANK。契约 C02/C08。
- **模型/接口**：现有字段只引用 `ENT`/`DTO#/$defs/CashTransaction`、`CashClassification`、`InternalTransfer`；银行腿命令为 `CMD#/$defs/cash.transaction.record~1v1`，费用成本只能走 `CMD#/$defs/costing.cost.post~1v1` 的合法来源。查询用 `QRY`/`QRES#/$defs/cash.transaction.get~1v1`、`cash.account_coverage.get~1v1`。
- **服务/状态**：毛额、净到账和手续费是同一银行事实的核对关系，不得既计净收款又重复扣费；手续费成本来源只能确认一次。互转必须同企业、同币种、两腿方向相反且金额相等，公司层抵销，账户层分别保留；单边只进入待核对，不删腿、不造收入/成本。
- **契约缺口 GAP-FEE-TRANSFER**：canonical 虽有 `InternalTransfer` 和 `CashClassification`，却没有创建互转对、关联手续费或写分类的命令；`cash.transaction.record/v1` 也不能表达两腿原子关系。PH132 README 已明确“两条转账腿样本不能代替同事务 InternalTransfer”。须先独立冻结命令/返回/来源键和失败语义，不能在本片发明 `InternalTransferCommand` 或用两笔普通收款替代。
- **浏览器/跨域**：净额核对页与互转详情只读服务器关联结果；缺口关闭后才开放提交。跨域验证 cash＋costing 原子、账户/公司两层守恒和单边异常可见。
- **执行文件**：`pmo_plugins/cash/{models.py,services.py,forms.py,views.py,urls.py}`、`pmo_plugins/cash/templates/cash/{fee_reconciliation.html,transfer_form.html}`、`pmo_plugins/costing/services.py`、`tests/test_v1_cash.py`、`tests/test_v1_costing.py`。root-only：cash 迁移下一号及共享接线；canonical 变更独立规划。
- **正反例/夹具**：`FX#/uat_sequences/12/steps/0` 毛10000/费100/净9900，成本100、公司净现金9900；`steps/1` 互转25000后公司净现金仍9900；`additional_cases/1` 两腿-25000/+25000、公司净0。净额之外再扣100、同一手续费二次过账、互转计收入/成本→拒绝/零新增；缺腿、跨企业、金额不等→拒绝或明确待核对且不计公司流量。
- **专项 argv/DoD**：现金/成本专项 argv 同 PHC-S4-04＋共同 argv＋PG 双腿故障注入＋浏览器。DoR 明确包含 GAP-FEE-TRANSFER 关闭；当前全部未验证，真实银行手续费与账户覆盖确认是外部门。

### 3.6 WP-EMPLOYEE-ADVANCE → `PHC-S4-06`（唯一主交付：员工借支与报销抵扣）

- **主需求/依赖**：V1-SPD-07；依赖 WP-CLEARING、WP-EXPENSE、WP-PEOPLE。契约 C03/C06/C07/C08。
- **模型/接口**：`ENT#/$defs/AdvanceBalance` 是只读投影，不可直接写；现金事实用 `CashTransaction`，员工身份用 `PayeeRef kind=employee`，不为付款复制供应商。抵扣用 `ENT`/`DTO#/$defs/Offset` 和 `CMD`/`CRES#/$defs/settlement.offset.create~1v1`；报销成本继续由 WP-EXPENSE 的 `costing.cost.post/v1` 产生。
- **服务/状态**：借支只产生现金及员工往来，报销只确认一次成本，抵扣原子减少借支与应付；剩余借支保留。锁序为公司→期间→员工/原借支→报销未清→offset；同来源/覆盖范围只抵扣一次，员工与企业从原记录解析，客户端不能改组织或审批人。
- **浏览器/跨域**：本人可读本人借支/报销状态，代录显示制单人；财务办理抵扣，PM 无个人费率/工资字段。验证 cash＋costing＋settlement 原子及权限收回后不可读。
- **执行文件**：`pmo_plugins/cash/{models.py,services.py,forms.py,views.py,urls.py}`、`pmo_plugins/cash/templates/cash/{advance_list.html,advance_offset_form.html}`、`pmo_plugins/costing/services.py`、`pmo_plugins/settlement/services.py`、`tests/test_v1_cash.py`、`tests/test_v1_costing.py`、`tests/test_v1_settlement.py`。root-only：相关 app 迁移下一号及共享接线。
- **正反例/夹具**：`FX#/specialized_not_directly_uat/1`（SPEC-ADVANCE）四阶段：借支10000/成本0/现金-10000；报销后借支2000/成本8000/应付8000/现金仍-10000；抵扣后借支2000/应付0/现金仍-10000；偿还余款后借支0/现金-8000。重复来源→`R-DUP-SOURCE`；借支记成本、抵扣再记现金、剩余2000自动清零、并发抵扣超余额→拒绝/零新增。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_cash tests.test_v1_costing tests.test_v1_settlement --verbosity 1`＋共同 argv＋PG 抵扣竞争＋浏览器本人/代录权限。当前全部未验证；员工绑定与报销批准证据是外部 DoR。

### 3.7 WP-PREPAID-SERVICE → `PHC-S4-07`（唯一主交付：跨期预付服务耗用）

- **主需求/依赖**：V1-SPD-10；依赖 WP-CLEARING、WP-SPEND。契约 C04/C06/C07/C08。
- **模型/接口**：`ENT#/$defs/PrepaidService`、`CostEvent`；草稿字段只取 `DTO#/$defs/PrepaidServiceDraft`，含 `counterparty_id/gross_cents/service_start/service_end/consumption_method/scope`，方法枚举仅 `monthly_straight_line/actual_usage`。命令/返回用 `costing.prepaid_service.draft.save/v1` 与内部 `costing.cost.post/v1`；后者 `cost_source_ref.kind=prepaid_service`，成本种类为 `prepaid_consumption`。
- **服务/状态**：付款只记现金；每期服务根据批准方法和适用期间计算耗用，再调用内部成本过账。`remaining_cents` 由服务派生，草稿和成本命令均不接收客户端剩余额；`PrepaidService.status` 只用 `active/fully_consumed/cancelled`。同期同覆盖只消费一次，剩余 ETC 与未来应付是不同查询/来源。
- **浏览器/跨域**：台账显示服务起止、方法、已耗用/待耗用；缺方法可留草稿，正式耗用拒绝。与 WP-ETC 按原服务＋期间覆盖释放，与 WP-CLEARING 的预付抵扣分开，不互造现金或成本。
- **执行文件**：`pmo_plugins/costing/{models.py,services.py,forms.py,views.py,urls.py}`、`pmo_plugins/costing/templates/costing/{prepaid_service_form.html,prepaid_service_detail.html}`、`pmo_plugins/cash/services.py`、`pmo_plugins/settlement/services.py`、`tests/test_v1_costing.py`、`tests/test_v1_cash.py`、`tests/test_v1_settlement.py`。root-only：costing 迁移下一号及共享接线。
- **正反例/夹具**：`FX#/uat_sequences/17/steps/0` 付款120000后成本0、未耗用120000、ETC120000、现金-120000；`steps/1` 2026-10耗用10000后成本10000、未耗用/ETC110000、现金仍-120000。`additional_cases/0` 12期各10000，首期余110000、末期累计成本120000余0、未来应付0。同期重复、超余额、无方法整单费用化→拒绝；剩余成本写入未来应付→查询断言失败。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_costing tests.test_v1_cash tests.test_v1_settlement --verbosity 1`＋共同 argv＋PG 同期竞争＋浏览器跨期边界。当前全部未验证；客户批准服务期间/部分期间政策是外部门。

### 3.8 WP-INVOICE → `PHC-S4-08`（唯一主交付：发票、分配与红冲）

- **主需求/依赖**：V1-REV-01/02/03；依赖 WP-AR、WP-AP、WP-SPEND-CORRECTION。契约 C03/C06/C07/C09。
- **模型/接口**：`ENT`/`DTO#/$defs/Invoice`、`InvoiceLine`、`InvoiceAllocation`、`InvoiceReversal`；命令/返回用 `invoicing.invoice.record/v1`、`invoicing.invoice.reverse/v1`；查询用 `QRY`/`QRES#/$defs/invoicing.unallocated.get~1v1`；事件只引用已有的 `EVT#/$defs/invoicing.recorded.v1`。record payload 固定为 `direction/counterparty_id/invoice_number/invoice_date/gross_cents/net_cents/tax_cents/evidence`。
- **服务/状态**：方向仅 `sales/purchase`；号码＋原始来源查重，`gross=net+tax` 逐分校验。开票不确认收入/成本义务，红冲只追加原发票反向事实，不当退款、不删结算。`management_revenue.match/v1` 只建履约/结算覆盖，三模板收入仍只由各自履约确认产生。
- **契约缺口 GAP-INVOICE-ALLOCATION**：现有 record 命令没有 InvoiceLine/InvoiceAllocation 输入，canonical 也没有独立分配命令；不能将只读实体当可写 DTO 或把分配字段偷塞进 record。一票多项、分次开票实现前须独立冻结分配命令、守恒/累计上限与返回；缺口未关闭则 V1-REV-02 DoR 不完整。
- **浏览器/跨域**：登记/红冲页调用既有命令；分配页只在缺口关闭后启用。跨域核对 settlement 可分配额、invoicing 覆盖与 management_revenue 不重复；权限裁剪对手和税额资料。
- **执行文件**：`pmo_plugins/invoicing/{__init__.py,apps.py,plugin.py,domain_metadata.py,models.py,services.py,forms.py,views.py,urls.py}`、`pmo_plugins/invoicing/templates/invoicing/{invoice_form.html,invoice_detail.html,invoice_reverse_form.html}`、`tests/test_v1_invoicing.py`。root-only：invoicing 迁移下一号及共享接线；schema 变更独立规划。
- **正反例/夹具**：`FX#/uat_sequences/10/steps/2` 发票及红冲后管理收入仍20000、应收21200、现金0；`additional_cases/1` match 后 matched net20000，不新增收入/应收，票据不造退款。重复号码/来源、税额不守恒、超额分配→拒绝；红冲自动退款/删结算或直接产生 `management_revenue.recognized.v1`→0。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_invoicing --verbosity 1`＋共同 argv＋PG 分次分配竞争＋浏览器。DoR 包含 GAP-INVOICE-ALLOCATION 关闭；当前全部未验证，客户税务资格/税率由授权负责人确认。

## 4. S5：十一个完整行为片

### 4.1 WP-BUDGET → `PHC-S5-01`（唯一主交付：预算版本与批准）

- **主需求/依赖**：V1-PLN-01/02；依赖 WP-CONTRACT、WP-SPEND、WP-LABOR-POLICY。契约 C04/C06/C10。
- **模型/接口**：`ENT#/$defs/BudgetVersion`、`BudgetLine`；草稿只取 `DTO#/$defs/BudgetVersionDraft`（project_id/version_name）和 `BudgetLineDraft`（budget_version_id/category_id/planned_cents）。三个命令及同名返回为 `planning.budget_version.draft.save/v1`、`planning.budget_line.draft.save/v1`、`planning.budget.approve/v1`；事件 `EVT#/$defs/planning.budget_approved.v1`。
- **服务/状态**：B0/Bn 由 `version_name` 标识，批准时服务端写 `approved_on/approved_by_member_id`；草稿不得覆盖已批准版本，历史只追加。公司锁→项目→版本→预算行；批准要求非本人、有效合同/类别与版本一致。预算不产生实际成本，Project 旧 `budget_cents` 只读适配。
- **浏览器/跨域**：编制/批准页按版本展示分项；Project 页面只读当前批准值。与 WP-ETC 只共享批准版本引用，不与合同额、实际成本双写。
- **执行文件**：`pmo_plugins/costing/{models.py,services.py,forms.py,views.py,urls.py,planning.py}`、`pmo_plugins/costing/templates/costing/{budget_edit.html,budget_approve.html}`、`tests/test_v1_planning.py`、`tests/test_v1_costing.py`。root-only：costing 迁移下一号及共享接线。
- **正反例/夹具**：现有夹具没有预算直接 UAT 或批准金额，不得编造逐分数值；`RT#/requirements/95`、`96` 是专项锚点，**GAP-BUDGET-FIXTURE** 必须在 ready 前独立补充批准版本及金额预期。可确定反例：草稿覆盖B0、无批准人/自审、预算生成成本、Project 直接编辑均拒绝/零新增。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_planning tests.test_v1_costing --verbosity 1`＋共同 argv＋真实浏览器版本回查。DoR 包含 GAP-BUDGET-FIXTURE 关闭；当前全部未验证，客户批准基线是外部门。

### 4.2 WP-ETC → `PHC-S5-02`（唯一主交付：剩余预计与实际释放）

- **主需求/依赖**：V1-PLN-03/04/05/07/08；依赖 WP-BUDGET、WP-SPEND-CORRECTION、WP-LABOR-POST、WP-PREPAID-SERVICE。契约 C04/C05/C06/C10。
- **模型/接口**：`ENT#/$defs/ETCVersion`、`ETCItem`、`EstimateRelease`；草稿取同名 `DTO` 的 `ETCVersionDraft/ETCItemDraft`，类别只可为 `own_effort/commitment/other_cost/unposted/risk`。命令/返回用 `planning.e_t_c_version.draft.save/v1`、`planning.e_t_c_item.draft.save/v1`、`planning.etc.publish/v1`；查询 `QRY`/`QRES#/$defs/planning.eac.get~1v1`；事件 `planning.etc_published.v1`。
- **服务/状态**：发布只接受版本 ID＋expected_version，服务端锁定项目/版本/行并记录批准人。实际确认通过内部服务追加 `EstimateRelease(etc_item_ref,actual_source_ref,released_cents)`，不提供浏览器直接写投影；按来源/覆盖释放而非同金额盲删。计划费率与实际费率分离，未批变化留 risk，缺项使 EAC 为 null 并列 `gap_codes`。
- **浏览器/跨域**：PM 周更新、版本差异及缺项提示；与 WP-PREPAID-SERVICE、WP-SPEND-CORRECTION、WP-LABOR-POST 验证 AC/ETC 互斥，部分验收只释放对应覆盖。
- **执行文件**：`pmo_plugins/costing/{models.py,services.py,forms.py,views.py,urls.py,planning.py}`、`pmo_plugins/costing/templates/costing/{etc_edit.html,etc_diff.html}`、`tests/test_v1_planning.py`、`tests/test_v1_costing.py`。root-only：costing 迁移下一号及共享接线。
- **正反例/夹具**：`FX#/uat_sequences/5/steps/0..3` ETC依次100000→100000→60000→60000；部分验收40000后成本/应付40000、ETC60000，抵扣20000＋付款20000后现金累计-40000、ETC仍60000。`FX#/uat_sequences/17/steps/1` 预付耗用10000后ETC110000。重复覆盖、实际过账不释放、未批变化并入基准、实际费率覆盖计划费率→拒绝/断言失败；缺费用不得把EAC填0。
- **专项 argv/DoD**：planning/costing 专项 argv 同 PHC-S5-01＋共同 argv＋PG 实际/发布竞争＋浏览器差异。当前全部未验证；无直接UAT的 V1-PLN-07/08 专项证据和客户批准假设是外部门。

### 4.3 WP-FORECAST → `PHC-S5-03`（唯一主交付：来源升级与13周预测）

- **主需求/依赖**：V1-CSH-09/10/11；依赖 WP-CLEARING、WP-REFUND、WP-PAYROLL-CASH、WP-FEE-TRANSFER。契约 C04/C07/C08/C10。
- **模型/接口**：`ENT#/$defs/CashForecastItem`、`ForecastSource` 是只读投影，`ForecastCoverage` 是唯一覆盖事实；DTO 只取 `DTO#/$defs/ForecastCoverage`。命令/返回为 `cash.forecast.replace/v1`（lower_source_ref/higher_source_ref/replaced_cents），查询为 `QRY`/`QRES#/$defs/cash.forecast.13week~1v1`、`cash.account_coverage.get~1v1`；事件 `cash.forecast_replaced.v1`。
- **服务/状态**：公司锁后来源按稳定 ID 排序，覆盖金额不得超过低层剩余；升级/部分替代只追加 ForecastCoverage。合同计划与应收按覆盖去重，预收抵扣后仅算未清部分，退款/退回撤下或恢复相应计划；过期来源进入 `overdue_source_refs`，无日期进入 `undated_source_refs`，不自动移入当周。
- **契约缺口 GAP-FORECAST-MANUAL**：replace 不接受预计日期或情景，`CashForecastItem` 又是只读投影；UAT-08-S01 也明确条件预测初建没有冻结命令。若实现人工调整日期/情景，须先独立冻结命令与来源边界；不得把日期塞进 replace 或直写投影。
- **浏览器/跨域**：13周视图固定13桶并显示水位、覆盖完整性；覆盖不全不得称“公司银行可用余额”，净流入不得称利润。与核销、退款、工资和互转按来源重放验证不重复。
- **执行文件**：`pmo_plugins/cash/{models.py,services.py,forms.py,views.py,urls.py}`、`pmo_plugins/cash/templates/cash/{forecast_13week.html,account_coverage.html}`、`tests/test_v1_cash.py`。root-only：cash 迁移下一号及共享接线；手工预测 schema 独立规划。
- **正反例/夹具**：`FX#/uat_sequences/7/steps/0..2` 条件计划10000；形成应收6000且替代计划6000时预测幅度仍10000（不是16000）；收款2000后应收4000、现金2000、预测8000。`FX#/uat_sequences/2/additional_cases/0` 本期已结53000与后半程条件530000? **纠错边界**：canonical 值固定为后半程53000，组合未来流入53000，不得写成106000双计。过期自动顺延、直改正式未清金额、覆盖不全仍称银行余额→拒绝。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_cash --verbosity 1`＋共同 argv＋PG 覆盖竞争＋浏览器13周/文案。DoR 包含 GAP-FORECAST-MANUAL 关闭或正式缩减该子行为并经主控批准；当前全部未验证，账户覆盖声明是外部门。

### 4.4 WP-CLOSE → `PHC-S5-04`（唯一主交付：期间预检、只前移锁定与调整）

- **主需求/依赖**：V1-REC-05/06/07/09；依赖 WP-CORE、WP-LABOR-POST、WP-SHARED、WP-REFUND、WP-REVENUE-PAIR、WP-INVOICE。契约 C02/C06/C07/C08/C09/C11。
- **模型/接口**：`ENT#/$defs/AccountingPeriod`、`PeriodClose`、`PeriodPrecheck`、`AdjustmentRequest`；DTO 只取 `PeriodClose/AdjustmentRequest`。命令/返回为 `period.close/v1`（period/expected_last_event_id）和 `period.adjustment.post/v1`（original_event_ref/reason/posting_period）；查询 `QRY`/`QRES#/$defs/period.precheck.get~1v1`；事件 `period.closed.v1`。
- **服务/状态**：不发明 AccountingPeriod open/closed 状态；`closed_through` 与 PeriodClose 表达锁定。预检 `blocking_codes/can_close/last_event_ref` 决定能否关账，允许未分配不等于忽略不平衡。公司锁内重查 last_event，随后写 PeriodClose/事件摘要；调整在开放入账期追加原币值反向/差额，保留原业务日，不编辑账本、不关闭触发器、无解锁命令。
- **浏览器/跨域**：预检页区分警告/硬阻断并列责任人与来源；锁期页要求 expected_last_event_id；调整页显示原日和入账期。与 `verify_integrity`、所有金融写共用 guard，硬错误阻断交付和锁账。
- **执行文件**：`core/plugin_sdk/{periods.py,period_guards.py}`、`pmo_plugins/insights/reconciliation.py`、`tests/test_v1_kernel.py`、`tests/test_v1_reconciliation.py`。root-only：`core/models.py`、`core/services.py`、`core/migrations/<next>.py`、共享 URL/settings/base 与 integrity 接线。
- **正反例/夹具**：`FX#/uat_sequences/13/steps/1` close wins 时9月10000成本整体拒绝，公司成本保持0；`additional_cases/4` post wins 时10000先提交并进入关账水位，或 close wins 后整体拒绝，不存在关账后写入。硬阻断仍关账、重复冲销、旧水位 close、锁期普通新增→拒绝；`R-CLOSED-PERIOD` 零锁期财务副作用。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_kernel tests.test_v1_reconciliation --verbosity 1`＋共同 argv＋真实 PG 两连接屏障的 close/post 两分支＋浏览器。当前全部未验证；真实 PG、迁移、恢复与客户关账确认是外部门。

### 4.5 WP-RECONCILE → `PHC-S5-05`（唯一主交付：四类核对与差额追踪）

- **主需求/依赖**：V1-REC-01/02/03/04；依赖 WP-CLOSE、WP-FORECAST、WP-EMPLOYEE-ADVANCE、WP-PREPAID-SERVICE。契约 C06/C07/C08/C09/C10/C11。
- **模型/接口**：`ENT#/$defs/ReconciliationCase`、`DataGap`；成本查询用 `QRY`/`QRES#/$defs/costing.reconciliation.get~1v1`，其 `source_refs/total_source_cents/project_cents/nonproject_cents/unallocated_cents/difference_cents` 为唯一返回。银行/收入核对组合 `cash.transaction.get/v1`、`cash.account_coverage.get/v1`、`settlement.open_item.get/v1`、`invoicing.unallocated.get/v1`、`management_revenue.contract_line.get/v1`，不复制金额字段。
- **服务/状态**：工资池=项目＋非项目＋未分配＋差额；资金事实、项目归属、核销三层分别守恒；合同/履约/结算/发票按覆盖区分差异。发现未入账、重复确认、只付款无来源时列原 SourceRef；未分配差额显式保留，办理跳转各域标准入口，不通过造单消差。
- **契约缺口 GAP-RECONCILE-CASE**：`ReconciliationCase` 虽含 owner/resolution_note，但 canonical 没有分派责任人或办理结论命令。若需持久办理记录，先独立冻结；本片不得把该实体当可写DTO或用“改余额”接口替代。
- **浏览器/跨域**：核对工作台只读汇总并下钻原事实；无权来源在查询前过滤。与 WP-CLOSE 共享预检规则/水位，不重复实现另一套核算。
- **执行文件**：`pmo_plugins/insights/{reconciliation.py,views.py,urls.py}`、`pmo_plugins/insights/templates/insights/reconciliation.html`、`tests/test_v1_reconciliation.py`。root-only：`pmo/urls.py`、`templates/base.html`；若需持久Case迁移，另由root-only规划，不在本读模型片暗增。
- **正反例/夹具**：`FX#/uat_sequences/3/steps/0`、`FX#/uat_sequences/3/steps/1`、`FX#/uat_sequences/3/steps/2` 工资池2400000=项目150000＋150000＋非项目30000＋未分配2070000，差额0；`FX#/uat_sequences/13/steps/0` 核销后现金10000、应收/未分配各2000；`FX#/uat_sequences/10/steps/2` 收入20000、应收21200，发票不消差。伪造收款消差、未分配摊入项目、缺来源付款不报警→失败并列明来源/层级。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_reconciliation --verbosity 1`＋共同 argv＋真实浏览器四类下钻。DoR 若包含Case办理则必须先关闭 GAP-RECONCILE-CASE；当前全部未验证，真实差异责任人与客户解释是外部门。

### 4.6 WP-INSIGHT-METRICS → `PHC-S5-06`（唯一主交付：经营指标、缺口与复盘草稿）

- **主需求/依赖**：V1-PLN-06/09、V1-INS-06/07；依赖 WP-RECONCILE、WP-ETC、WP-REVENUE-PAIR。契约 C04/C06/C07/C08/C09/C10/C12。
- **模型/接口**：`ENT#/$defs/MetricDefinition`、`ReadModelWatermark`、`DataGap` 均为读投影，不提供直接写。查询/返回用 `insights.metric.get/v1`、`insights.data_gaps.get/v1`、`planning.eac.get/v1`；指标枚举仅 `recognized_management_revenue/confirmed_cost/management_contribution/net_cash_flow/budget_remaining/estimate_at_completion/forecast_result`，返回保留 `value_cents=null`、`basis/coverage_complete/gap_codes/source_refs`。
- **服务/状态**：同基准日、范围、调整口径和水位计算页面/API/导出；预计结余、实际管理贡献、净现金流分别命名，毛利不称净利润，付款/到账/开票不改已确认收入。复盘报价只调用既有 `CMD#/$defs/contracts.contract.draft.save~1v1` 创建草稿，历史实际只作只读参考，不复制成新事实。
- **浏览器/跨域**：项目/客户/公司汇总与穿透均显示来源、口径和缺口；与 WP-EXPORT 使用同一查询服务，跨企业/角色在服务端拒绝。缺成本、费率、证据、政策或日期不得假零或以评分代替。
- **执行文件**：`pmo_plugins/insights/{__init__.py,apps.py,plugin.py,domain_metadata.py,metrics.py,reconciliation.py,views.py,urls.py}`、`pmo_plugins/insights/templates/insights/{metrics.html,metric_detail.html}`、`tests/test_v1_insights.py`。root-only：共享 URL/settings/base；读模型迁移下一号如确需持久化由集成者独立确认。
- **正反例/夹具**：`FX#/uat_sequences/5/steps/2` confirmed_cost=40000、ETC=60000，二者不得混为实际；`FX#/uat_sequences/10/steps/2` recognized_management_revenue=20000，开票后不变；`FX#/uat_sequences/13/steps/0` 现金10000、未清2000，净现金流不等于利润。缺成本显示0、两入口差1分、跨企业读取、复盘直接生成正式事实→失败/拒绝。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_insights --verbosity 1`＋共同 argv＋页面/API/导出逐分一致＋真实浏览器三级穿透。当前全部未验证；完整数据水位与客户经营口径确认是外部门。

### 4.7 WP-NOTIFY → `PHC-S5-07`（唯一主交付：到期扫描、幂等待办与停用保留）

- **主需求/依赖**：V1-INS-05；依赖 WP-FORECAST、WP-RECONCILE、WP-CHANGE。契约 C01/C02/C07/C10。
- **模型/接口**：不新造 Notification DTO 或第二套事件；只引用 `ENT#/$defs/OutboxEvent`、`IdempotencyRecord`、`SourceConsumption` 与 `QRY`/`QRES#/$defs/insights.workspace.get~1v1` 的 `pending_refs/gap_codes`。来源事实仍由 forecast/reconciliation/contracts 各自 owner 提供。
- **服务/状态**：金融事实与 Outbox 已在原事务提交；通知消费者按事件ID＋消费者用途做 SourceConsumption，重试返回原投递结果。通知失败只重试通知，不回滚或重放核算；扫描只生成来源引用，负责人/权限实时解析。模块停用保留历史与队列并统一门控，权限收回后旧 pending_ref 不可读。
- **浏览器/跨域**：站内待办按负责人/到期显示原因、来源和下一步；点击回所属域，无财务直写。跨域验证同一Outbox重复投递、消费者故障、模块停用和权限撤销，不要求空模型或空命令PR。
- **执行文件**：`pmo_plugins/notifications/{__init__.py,apps.py,plugin.py,domain_metadata.py,consumers.py,services.py,views.py,urls.py}`、`pmo_plugins/notifications/templates/notifications/todo_list.html`、`tests/test_v1_notifications.py`。root-only：共享URL/settings/base；如现有Outbox不足以持久消费水位，迁移须另列root-only并先冻结，不得暗加字段。
- **正反例/夹具**：无直接UAT金额场景，数值不属于本行为验收；`RT#/requirements/117` 为专项锚点。固定计数预期：同一事件投递2次只有1条有效待办；通知失败前后财务事实/账本/收入/现金数量与金额完全不变；停用后历史/队列计数不变；权限收回后响应 pending_refs 不含旧条目。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_notifications --verbosity 1`＋共同 argv＋真实消费者重复/故障注入＋浏览器权限。当前全部未验证；后台worker、真实队列保留和模块停用演练是外部门。

### 4.8 WP-WORKSPACES → `PHC-S5-08`（唯一主交付：四职责工作台）

- **主需求/依赖**：V1-INS-01/02/03/04；依赖 WP-INSIGHT-METRICS、WP-NOTIFY。契约 C01/C12。
- **模型/接口**：不新增业务表或可编辑余额；唯一工作台查询为 `QRY`/`QRES#/$defs/insights.workspace.get~1v1`，`workspace∈{owner,pm,member,finance}`，返回仅 `metrics/pending_refs/gap_codes`。指标继续引用 `insights.metric.get/v1`，不另建卡片金额DTO。
- **服务/状态**：owner 显示预计亏损/成本漏项/回款/资金风险；PM 仅授权项目且无个人工资/费率；member 仅本人待补/待审/费用；finance 显示跨模块异常。每条带原因、来源、责任人、下一步，空值/缺项不假零；裁剪在服务端响应前完成。
- **浏览器/跨域**：四职责页面与跨模块导航复用PH-023令牌/共享模式；覆盖1440/390、键盘/焦点、默认/空/错/载/无权。网络响应体断言敏感字段不存在，与 WP-EXPORT 同水位逐分一致，工作台无“改余额”表格。
- **执行文件**：`pmo_plugins/insights/{metrics.py,consumers.py,views.py,urls.py}` 中实际存在的目标文件仅保留 `metrics.py,views.py,urls.py`，以及 `pmo_plugins/insights/templates/insights/{workspace_owner.html,workspace_pm.html,workspace_member.html,workspace_finance.html}`、`templates/partials/workspace_cards.html`、`static/app.css`、`static/app.js`、`tests/test_v1_workspace.py`、`tests/test_v1_insights.py`。root-only：`pmo/urls.py`、`templates/base.html`。
- **正反例/夹具**：复用 `FX#/uat_sequences/13/steps/0`，owner/finance 卡片现金10000、未清2000且来源可穿透；同指标到 WP-EXPORT 不得差1分。PM响应含个人工资/费率、员工越项目、无来源装饰结论、旧权限待办可见→失败；不存在直改余额入口。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_workspace tests.test_v1_insights --verbosity 1`＋共同 argv＋真实浏览器四角色/两宽度/全状态。当前全部未验证；四角色真实账号与可访问性走查是外部门。

### 4.9 WP-PROJECT-CLOSE → `PHC-S5-09`（唯一主交付：项目聚合、结项与归档后迟到款）

- **主需求/依赖**：V1-PRO-08/09/10；依赖 WP-WORKSPACES、WP-CLOSE、WP-CLEARING、WP-ETC。契约 C03/C05/C07/C08/C10/C11/C12。
- **模型/接口**：`ENT#/$defs/DeliveryClose` 只作为既有实体定义引用，**不在 `write-dtos.schema.json`，不能冒充可写DTO**。聚合查询组合 `contracts.project_summary.get/v1`、`planning.eac.get/v1`、`cash.account_coverage.get/v1`、`settlement.open_item.get/v1`、`costing.reconciliation.get/v1` 及同名 `QRES`；附件只用既有 `delivery.evidence.attach/v1`。
- **服务/状态**：业务结项检查剩余工作；财务归档检查未清、预收预付、待审、未归属与风险，不能强制清零/删除。归档后迟到款由授权者在开放期追加现金/核销及“归档后处理”审计，不重开普通报工；原归档记录只追加。
- **契约缺口 GAP-PROJECT-CLOSE**：canonical 没有 DeliveryClose 写DTO、业务结项/财务归档命令或对应结果/事件；现有 evidence.attach 不能替代结项授权。须先独立冻结命令、状态、权限、返回和反例，不能临场发明 `DeliveryCloseCommand`，也不能据本文将该片 ready。
- **浏览器/跨域**：聚合详情只读并跳转各域；结项/归档按钮在缺口关闭后接唯一服务。与 WP-CLOSE 锁期、WP-CLEARING 未清、WP-ETC 剩余联合预检；迟到款保留原银行日和归档后审计。
- **执行文件**：`pmo_plugins/delivery/{__init__.py,apps.py,plugin.py,domain_metadata.py,close_services.py,views.py,urls.py}`、`pmo_plugins/delivery/templates/delivery/{project_summary.html,project_close.html}`、`tests/test_v1_delivery.py`。root-only：delivery 模型/迁移下一号及共享接线；canonical 变更独立规划。
- **正反例/夹具**：`FX#/uat_sequences/9/additional_cases/0` 主收95400、质保10600且2026-10-31未到期，允许业务结项但财务保留10600；`additional_cases/1` 2027-10-31迟到款10600后累计现金106000、应收0、收入仍100000，原归档只追加。有未清强制归档、归档后普通报工、无授权迟到款、聚合页直改余额→拒绝。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_delivery --verbosity 1`＋共同 argv＋真实浏览器归档/迟到款＋PG 锁期联动。DoR 包含 GAP-PROJECT-CLOSE 关闭；当前全部未验证，客户结项/归档授权与迟到款凭据是外部门。

### 4.10 WP-EXPORT → `PHC-S5-10`（唯一主交付：授权导出与核对底稿）

- **主需求/依赖**：V1-REC-08；依赖 WP-RECONCILE、WP-INSIGHT-METRICS。契约 C01/C12。
- **模型/接口**：`ENT`/`DTO#/$defs/ExportManifest`；命令/返回 `insights.export.create/v1`，payload 只含 `query_contract/as_of_date/scope/project_id`；事件 `EVT#/$defs/insights.export_ready.v1`。金额来自 `insights.metric.get/v1` 与 `costing.reconciliation.get/v1` 同水位结果，不在导出任务中维护第二份金额。
- **服务/状态**：公司锁内解析权限、查询、范围和 `watermark_ref`；导出列期间、水位、来源和口径，无需用户再写公式。CSV 对 `= + - @` 开头单元格按既定转义，文件生成前再次裁剪字段；重复同水位请求返回原manifest或明确新水位，不混用数据。
- **浏览器/跨域**：发起/下载页展示授权后的字段清单与水位；页面/API/文件字节三方逐分一致，越权字段在文件字节层也不存在。
- **执行文件**：`pmo_plugins/insights/{exports.py,metrics.py,reconciliation.py,views.py,urls.py}`、`pmo_plugins/insights/templates/insights/{export_form.html,export_detail.html}`、`tests/test_v1_insights.py`。root-only：共享URL/base；导出迁移下一号如需要由集成者确认。
- **正反例/夹具**：以 `FX#/uat_sequences/10/steps/2` 作支持场景，页面/API/CSV 管理收入均20000、应收21200；以 `FX#/uat_sequences/3/steps/2` 作成本核对，公司成本均2400000、净现金-1200000。公式前缀未转义、含PM无权工资字段、同水位三方差1分→失败；重复请求不得产生口径混杂的新文件。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_insights --verbosity 1`＋共同 argv＋真实下载字节检查＋浏览器权限。无直接UAT不降低专项要求；当前全部未验证，真实导出文件与客户数据外发授权是外部门。

### 4.11 WP-UI-ACCEPTANCE → `PHC-S5-11`（唯一主交付：全站统一与真实可访问办理）

- **主需求/依赖**：V1-INS-08；依赖 WP-WORKSPACES、WP-PROJECT-CLOSE、WP-EXPORT。契约 C01/C12。
- **模型/接口**：不新增业务表、命令或查询；复用各域唯一服务。无独立模型/服务PR，浏览器统一与跨域可访问验证合并为一个真实交付片。发现业务缺陷回对应域PH，不在本片跨域修代码。
- **浏览器/跨域**：按 `docs/engineering/frontend-standards.md`、`visual-system.md`、`ui-tokens.css`、`ui-specimen.html` 核验共享布局/令牌；覆盖1440/390及连续宽度、键盘/焦点、默认/空/错误/加载/无权、无横向溢出。真实浏览器逐入口检查服务器权限，无权页面不得200展示受保护内容。
- **执行文件**：`static/app.css`、`static/app.js`、`tests/test_v1_workspace.py`。root-only：`templates/base.html`；领域模板由对应域PH修改。若需新增共享模式，先由独立规划把 `docs/engineering/*` 规范/样张纳入范围，本片不自行扩大白名单。
- **正反例/夹具**：不适用金额夹具，固定走查获 `RT#/requirements/120`；所有金额显示同时回查所属域夹具，不允许为对齐页面改计算。只换色不统一布局、390溢出、焦点丢失、空/错/载/无权缺态→缺陷且不得验收；不得以代码存在代替真实浏览器记录。
- **专项 argv/DoD**：`.venv/bin/python manage.py test tests.test_v1_workspace --verbosity 1`＋共同 argv＋真实浏览器全站矩阵与键盘/焦点记录。当前全部未验证；真实浏览器、辅助技术、客户可访问性签收是外部门。

## 5. 契约缺口与晋级约束汇总

| 缺口 | 阻断包/需求 | 关闭方式 |
| --- | --- | --- |
| GAP-PAYROLL-CASH | WP-PAYROLL-CASH；工资池与银行事实关联/分类 | 独立 canonical 契约变更，不发明第二套DTO |
| GAP-FEE-TRANSFER | WP-FEE-TRANSFER；手续费关系/双腿原子互转 | 独立冻结命令、返回、来源键与故障语义 |
| GAP-INVOICE-ALLOCATION | WP-INVOICE / V1-REV-02 | 独立冻结InvoiceLine/Allocation写命令及累计上限 |
| GAP-BUDGET-FIXTURE | WP-BUDGET | 独立补充批准版本和逐分夹具；不修改现有预期迎合实现 |
| GAP-FORECAST-MANUAL | WP-FORECAST / V1-CSH-09 | 独立冻结人工日期/情景命令，或经主控正式调整范围 |
| GAP-RECONCILE-CASE | WP-RECONCILE 的持久办理记录（如纳入） | 独立冻结责任人/结论命令；只读核对不因此造假 |
| GAP-PROJECT-CLOSE | WP-PROJECT-CLOSE | 独立冻结DeliveryClose写命令/状态/权限/结果/事件 |

这些缺口不是实现PR的扩权清单。关闭前相应DoR不完整，全部19片仍建议 proposed；即使缺口关闭，也须依赖PH done、正式任务合入、独立S复核及当前CI，不能由本文直接置ready。

## 6. 需求 → 唯一主交付PHC → 设计证据锚点

本表每行只有一个主交付PHC；“支持/集成/验证”不是共同主责。`RT`索引是实际数组指针。正式晋级只回填 `requirement-trace.json` 原有 `ph_task_ids`；实现done后再把 `project/reports/<正式PH>.json`、独立复核和实际CI加入原有 `evidence_refs`。当前下列均是设计锚点，不是运行证据，`evidence_refs` 不得预先写成通过。

| 需求及RT指针 | 唯一主交付 | 支持/集成/验证 | 设计证据锚点 |
| --- | --- | --- | --- |
| V1-MD-05 `RT#/requirements/12` | PHC-S4-01 | WP-IDENTITY | `FX#/uat_sequences/12` |
| V1-CSH-01 `RT#/requirements/73` | PHC-S4-01 | WP-IMPORT | `FX#/uat_sequences/12`、R-DUP-CASH |
| V1-CSH-02 `RT#/requirements/74` | PHC-S4-01 | WP-IDENTITY | `FX#/uat_sequences/12/steps/2` |
| V1-CSH-12 `RT#/requirements/84` | PHC-S4-01 | PHC-S4-03支持 | `FX#/uat_sequences/8`、`12` |
| V1-SET-05 `RT#/requirements/67` | PHC-S4-02 | PHC-S4-01、PH-019验证 | `FX#/uat_sequences/13/steps/0` |
| V1-SET-06 `RT#/requirements/68` | PHC-S4-02 | PHC-S5-03集成 | `FX#/uat_sequences/2/steps/3`、`5/steps/3` |
| V1-SET-10 `RT#/requirements/72` | PHC-S4-02 | WP-IDENTITY | `FX#/uat_sequences/14`、R-CROSS-TENANT |
| V1-CSH-03 `RT#/requirements/75` | PHC-S4-02 | PHC-S4-01支持 | `FX#/uat_sequences/12` |
| V1-CSH-04 `RT#/requirements/76` | PHC-S4-02 | WP-AR/AP支持 | `FX#/uat_sequences/2/steps/0`、`5/steps/1` |
| V1-SET-07 `RT#/requirements/69` | PHC-S4-03 | PHC-S4-02支持 | `FX#/uat_sequences/8` |
| V1-CSH-05 `RT#/requirements/77` | PHC-S4-03 | PH-019 PG验证 | `FX#/uat_sequences/8`、`13/additional_cases/2` |
| V1-CST-11 `RT#/requirements/51` | PHC-S4-04 | WP-LABOR-POST支持 | `FX#/uat_sequences/3/steps/0`、`FX#/uat_sequences/3/steps/1`、`FX#/uat_sequences/3/steps/2` |
| V1-CSH-06 `RT#/requirements/78` | PHC-S4-04 | PHC-S5-05验证 | `FX#/uat_sequences/3/steps/2`、`12` |
| V1-CSH-07 `RT#/requirements/79` | PHC-S4-05 | WP-EXPENSE支持 | `FX#/uat_sequences/12/steps/0` |
| V1-CSH-08 `RT#/requirements/80` | PHC-S4-05 | PHC-S4-01支持 | `FX#/uat_sequences/12/steps/1`、`additional_cases/1` |
| V1-SPD-07 `RT#/requirements/59` | PHC-S4-06 | WP-EXPENSE/PEOPLE支持 | `FX#/specialized_not_directly_uat/1` |
| V1-SPD-10 `RT#/requirements/62` | PHC-S4-07 | PHC-S5-02集成 | `FX#/uat_sequences/17` |
| V1-REV-01 `RT#/requirements/85` | PHC-S4-08 | WP-REVENUE-PAIR支持 | `FX#/uat_sequences/10/steps/2` |
| V1-REV-02 `RT#/requirements/86` | PHC-S4-08 | WP-AR/AP支持；GAP阻断 | `FX#/uat_sequences/10`＋待补分配专项 |
| V1-REV-03 `RT#/requirements/87` | PHC-S4-08 | PHC-S4-03支持 | `FX#/uat_sequences/10/steps/2`、`additional_cases/1` |

| 需求及RT指针 | 唯一主交付 | 支持/集成/验证 | 设计证据锚点 |
| --- | --- | --- | --- |
| V1-PLN-01 `RT#/requirements/95` | PHC-S5-01 | WP-CONTRACT/SPEND支持 | RT专项＋GAP-BUDGET-FIXTURE |
| V1-PLN-02 `RT#/requirements/96` | PHC-S5-01 | WP-IDENTITY验证 | RT专项＋GAP-BUDGET-FIXTURE |
| V1-PLN-03 `RT#/requirements/97` | PHC-S5-02 | WP-LABOR-POST支持 | `FX#/uat_sequences/5`、`17` |
| V1-PLN-04 `RT#/requirements/98` | PHC-S5-02 | WP-SPEND-CORRECTION支持 | `FX#/uat_sequences/5/steps/2` |
| V1-PLN-05 `RT#/requirements/99` | PHC-S5-02 | PHC-S4-07集成 | `FX#/uat_sequences/5/steps/2`、`17/steps/1` |
| V1-PLN-07 `RT#/requirements/101` | PHC-S5-02 | WP-CHANGE支持 | RT专项，无直接UAT |
| V1-PLN-08 `RT#/requirements/102` | PHC-S5-02 | PHC-S5-06验证 | RT专项，无直接UAT |
| V1-CSH-09 `RT#/requirements/81` | PHC-S5-03 | WP-CONTRACT支持；GAP阻断 | `FX#/uat_sequences/7` |
| V1-CSH-10 `RT#/requirements/82` | PHC-S5-03 | PHC-S4-02/03支持 | `FX#/uat_sequences/7/steps/1`、`steps/2` |
| V1-CSH-11 `RT#/requirements/83` | PHC-S5-03 | PHC-S4-01支持 | `FX#/uat_sequences/12`、`QRES#/$defs/cash.account_coverage.get~1v1` |
| V1-REC-05 `RT#/requirements/108` | PHC-S5-04 | PHC-S5-05集成 | `FX#/uat_sequences/13` |
| V1-REC-06 `RT#/requirements/109` | PHC-S5-04 | PH-019 PG验证 | `FX#/uat_sequences/13/steps/1`、`additional_cases/4` |
| V1-REC-07 `RT#/requirements/110` | PHC-S5-04 | WP-CORE支持 | `FX#/uat_sequences/13`、R-CLOSED-PERIOD |
| V1-REC-09 `RT#/requirements/112` | PHC-S5-04 | PHC-S5-05支持 | `FX#/uat_sequences/13` |
| V1-REC-01 `RT#/requirements/104` | PHC-S5-05 | WP-LABOR-POST支持 | `FX#/uat_sequences/3/steps/2` |
| V1-REC-02 `RT#/requirements/105` | PHC-S5-05 | WP-SPEND-CORRECTION支持 | `FX#/uat_sequences/5`、`16`、`17` |
| V1-REC-03 `RT#/requirements/106` | PHC-S5-05 | PHC-S4-01/02支持 | `FX#/uat_sequences/12` |
| V1-REC-04 `RT#/requirements/107` | PHC-S5-05 | WP-REVENUE-PAIR支持 | `FX#/uat_sequences/10` |
| V1-PLN-06 `RT#/requirements/100` | PHC-S5-06 | PHC-S5-02支持 | `FX#/uat_sequences/5`、`17` |
| V1-PLN-09 `RT#/requirements/103` | PHC-S5-06 | WP-CONTRACT草稿集成 | RT专项，无直接UAT |
| V1-INS-06 `RT#/requirements/118` | PHC-S5-06 | PHC-S5-10验证 | `FX#/uat_sequences/1` |
| V1-INS-07 `RT#/requirements/119` | PHC-S5-06 | WP-ONBOARDING支持 | `FX#/uat_sequences/0` |
| V1-INS-05 `RT#/requirements/117` | PHC-S5-07 | PHC-S5-03/05、WP-CHANGE支持 | RT专项；Outbox重复/故障计数 |
| V1-INS-01 `RT#/requirements/113` | PHC-S5-08 | PHC-S5-06/07支持 | RT专项＋角色响应矩阵 |
| V1-INS-02 `RT#/requirements/114` | PHC-S5-08 | WP-IDENTITY验证 | RT专项＋敏感字段字节断言 |
| V1-INS-03 `RT#/requirements/115` | PHC-S5-08 | WP-DELIVERY/TIME支持 | RT专项＋真实浏览器 |
| V1-INS-04 `RT#/requirements/116` | PHC-S5-08 | PHC-S5-05支持 | RT专项＋原事实跳转 |
| V1-PRO-08 `RT#/requirements/22` | PHC-S5-09 | PHC-S5-08支持 | `FX#/uat_sequences/1` |
| V1-PRO-09 `RT#/requirements/23` | PHC-S5-09 | PHC-S4-02、PHC-S5-02支持 | `FX#/uat_sequences/9/additional_cases/0` |
| V1-PRO-10 `RT#/requirements/24` | PHC-S5-09 | PHC-S4-01/02支持 | `FX#/uat_sequences/9/additional_cases/1` |
| V1-REC-08 `RT#/requirements/111` | PHC-S5-10 | PHC-S5-05/06支持 | RT专项；`FX#/uat_sequences/10/steps/2`仅作支持场景 |
| V1-INS-08 `RT#/requirements/120` | PHC-S5-11 | 各域PH验证，不共同主责 | RT专项＋1440/390真实浏览器矩阵 |

## 7. PH-001、安全片、旧任务与并行治理边界

- **PH-001只核实，不自行放行**：BASE 中 PH-001 仍为 proposed，原 blocker 是SDK字段/兼容规则需规划明确。主控SDK-1候选已给出8字段旧manifest与独立 `domain_metadata` 的最小契约，但仍未独立审阅/合入，不能视为 blocker 已解除。唯一候选顺序为：PH137规划合入→主控P0依赖安全片→PH-001 SDK-1→PH-002统一门控/静态路由→新SDK-3迁移与重放→WP-CORE。WP-SDK必须覆盖SDK-1/2/3，PH-001单独done不等于SDK完成。
- 本文不定义SDK字段、示例App、启动顺序之外的ready动作，不改 PH-001/002 状态；正式解除只能由主控在最终tasks/spec中逐项核对安全前置、DoR和依赖后实施。
- **PH-006—020承接**：这些旧epic/规格保持BASE状态、白名单和验收；S4/S5新片承接其领域意图，不宣布旧任务done，不删除 `scripts/projectctl.py`、治理测试、AI/资源前置、PG/恢复或发布门。PH-017/018的AI与真实客户端要求、PH-019的真实PG/恢复/规模要求、PH-020发布候选/客户验收均需独立变更和实际证据。
- **PH-120/121/122**：并行治理改造保持原spec范围、白名单与验收，本文不修改、不预占、不以其结果替代本批业务验证。
- 无产品实现、版本发布、真实客户/生产部署；AI不自动购买额度或转用未知计费API，不向外部模型提供凭据、真实客户或工资数据。

## 8. 第二次修订整改回执（不称为正式PH137验收通过）

| 验收阻断 | 本次整改 | 仍需主控/独立复核 |
| --- | --- | --- |
| 银行来源键含金额/日期 | 第2.2节改为既有企业＋用途＋原始namespace/kind/stable id＋明细/期间/覆盖；金额、日期、version、渠道、请求key均排除，变化走冲突/冲正 | 在最终PH与PG竞争用例中验证同事实改金额/换key不重复消费 |
| 待匹配替换CashTransaction.status | 第2.1及PHC-S4-01明确 `unmatched` 只属于CashClassification并由关系派生，CashTransaction保留既有五态 | 核对数据库实现不新增伪状态 |
| 19×4机械拆分、55/76计数矛盾 | 改为19个完整行为片；模型/服务/浏览器/跨域验证在片内合并，无空模型/服务PR | 主控最终登记正式PH编号与逐片白名单 |
| 只有命令名、指针/返回/错误不具体 | 每片列出CMD/CRES/DTO/ENT/QRY/QRES/EVT指针、权限、状态、锁序、来源和固定拒绝码；缺失能力单列GAP | 对最终候选重新解析全部指针，并审核缺口关闭契约 |
| 夹具整体误写待冻结 | 绑定PH134-SYNTHETIC-20261028 / 1.0、三份SHA和具体UAT/SPEC场景；只有明确标注的预算/分配等缺口待补 | 最终规划时复核哈希；结构记录不得冒充产品UAT |
| 多片共同主责需求 | 第6节49条需求各只有一行、一个主交付PHC，其余均标支持/集成/验证 | 主控只回填原有ph_task_ids/evidence_refs，不建第二套追踪 |
| SDK重复定义/自行放行 | 第1、7节只引用主控SDK-1与安全候选，顺序固定在安全片后SDK1/2/3；本文不放行PH001 | 主控独立核实并正式解除blocker |

## 9. 实际核对、未执行与未完成

### 9.1 本执行器实际做过的只读核对

- 读取了PH-137规格、`work-packages.json`、`requirement-trace.json`、PH132 contracts/fixtures、policy/legacy-cutover及既有PH-001—020规格；解析得到S4/S5为19包、49条需求，不是55或76个交付片。
- 解析canonical目录确认：55命令/55命令返回、25查询/25查询返回、140实体/投影定义、97写DTO、28事件；实际查看了本批使用的payload、状态枚举、返回字段和 `x-exposure/x-service-invariants`。
- 实际解析夹具ID/版本、UAT数组索引、SPEC-ADVANCE、rejection_catalog及逐分预期；用 `shasum -a 256` 计算三份夹具哈希，与 `sample-verification.json` 记录一致。
- 本文写入后另做文档指针解析和覆盖核对，结果以本次最终修订回执为准；该只读核对不是候选产品测试或授权验收命令。

### 9.2 明确未执行

- 未运行 `python scripts/projectctl.py check`、`python -m unittest tests.test_projectctl tests.test_governance -v`，也未运行整仓测试、候选专项、安装依赖、产品命令、迁移、SQLite/PG、浏览器、UAT或fixture验证脚本。
- 未实现产品业务、未改tasks/manifest/schema/fixture、未创建PR/部署、未读取凭据或真实数据，未调用其他agent。本文全部产品正反例和专项argv仍是**未验证设计候选**。
- 既有 `sample-verification.json`/`verification.json` 是历史准备资产证据，不是本次执行结果；真实PG、Linux/恢复、TLS、客户签收、发行授权继续未验证。

### 9.3 尚未完成/交主控

- 第5节七项GAP及预算/分配等专项夹具缺口尚未关闭；相应DoR不完整。19片均只建议proposed，没有首批ready。
- PHC全部是占位符；正式PH编号、依赖、精确白名单、固定验收和状态须主控登记合入，需求追踪和实际报告/交接尚未回填。
- 本候选尚未经新的独立S级只读复核，须按修订后新SHA重验；不能继承原始候选的执行完成或内容返工结论，也不声称产品、PH137整体或正式开工基线已通过。
