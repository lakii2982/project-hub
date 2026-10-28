# PH-137 · S6/S7 正式薄片契约候选（第二次修订）

> 状态：**候选，未合入、未授权实施、未执行验收**。本文件只覆盖 `work-packages.json` 中阶段 S6/S7 的 12 个包，是 PH-137 授权内的规划输入；最终整合、tasks/trace 写入、独立复核和 PR 由主控完成。
>
> 权威 BASE：`f1115cfd043a9f3a9a7ffc05d6a88d772e324920`；契约摘要：`39d7119aedc68df2a269a95fc69b46f66680c82b5b362a46d42320c9edb0987f`。本文件不修改 `project/tasks.json`、`project/manifest.json`、`requirement-trace.json`、检查器、workflow 或发布门。
>
> 本文件不声称任何需求已实现、任何 UAT 已通过、任何环境已部署。所有切片一律为 `proposed` 候选；代码存在、自动化通过、浏览器检查、目标环境实证和客户签收分别记录，互不替代。
>
> **本次为验收后第二次修订**（原候选 SHA256 `1eac7061…`）。针对验收单 S6–S7 三项阻断与通用拆片要求：删除重复 SDK 冻结与改 PH-001 状态的动作，只保留现状核实并引用主控唯一 SDK 候选；容量基准改为“起测基线而非产品硬上限”，基准 +1 不得拒绝业务；按可验收行为合并空片，不再机械每包 M/SVC/UI/X 四 PR，确需拆分者逐片给出文件与接口；PHC 明确声明为占位符并补齐逐片白名单、验收与实证外门供最终登记。逐条回执与实际核对见第 24 节。所有 schema 指针已逐条实际解析（见 §24.2），canonical schema/fixture 未改。

## 0. 边界

- 只写本文件；不写产品代码、不建迁移、不改任务/契约夹具、不建 PR、不部署、不调用其他 agent、不读取凭据或真实客户数据。
- 不执行整仓测试、不安装依赖、不运行候选验收命令。文中所有命令均为**建议验收**，本轮全部 `not_run`。
- 不复制第二套可编辑字段。字段、必填、枚举、约束、`x-owner`、`x-exposure`、`x-service-invariants` 一律以既有 JSON Schema 指针为准；本文只记录指针、切片边界、执行文件和判定规则。
- 不预占真实 PH 编号、迁移编号、标签或版本号。`PHC-*` 只是候选占位符，合并时由主控另行分配。

## 1. 覆盖、计数与拆片原则

| 项 | 数量 | 说明 |
| --- | ---: | --- |
| S6 包 | 9 | `WP-IMPORT`、`WP-OPENING`、`WP-ONBOARDING`、`WP-INSTALL`、`WP-TLS`、`WP-UPGRADE-RESTORE`、`WP-PG-CONCURRENCY`、`WP-PERFORMANCE`、`WP-SUPPORT` |
| S7 包 | 3 | `WP-RELEASE-PACKAGE`、`WP-CUSTOMER-UAT`、`WP-FINAL-RELEASE` |
| 本文件覆盖 | 12 / 62 | 其余 50 包属 S0–S5，不在本子范围；由主控整合，本文不代写、不声称覆盖 |
| 本子范围唯一需求 | 17 | `V1-ORG-01`；`V1-ONB-01`～`08`；`V1-OPS-01`～`08` |
| 相关 UAT | 4 条直接 + 全量收口 | `UAT-01`、`UAT-12`、`UAT-14`、`UAT-16`；S7 另负责 18UAT 全量证据 |
| 候选切片 | 21 个可执行 + 1 个白名单未决 | 见各包；每个需求只指向一个主交付切片 |
| 拆片原则 | 按可交付行为 | 合并同一行为的模型/服务/入口；无浏览器入口的包不单列 UI PR；只有跨域证据或独立迁移边界才拆 |

拆片规则（本次修订的核心）：

1. 一个切片必须能独立验收一个可观察行为；“建模型”“加页面”“写测试”本身不构成切片。
2. 同一行为的实体、服务、页面、测试放在同一 PR；页面不是独立交付物时不单列。
3. 仅以下情形拆第二片：需要跨域端到端证据、需要独立迁移/兼容窗口、或文件写入必须串行且行为可分明验收。拆片时逐片写文件与接口。
4. 同一包内若两片触碰同一文件（如 `imports.py`），明确“串行：前片合入后才开工”，不得并行写。
5. `PHC-*` 只是候选占位符，**不是任务 ID、不占真实编号、不表示 ready**。正式编号、`allowed_paths`、`acceptance`、依赖必须由主控在最终规划写入可信 BASE 后才有开工效力。

需求唯一主包以 `requirement-trace.json` 的 `primary_package` 为准（已逐条核对，见 §24.2）。`WP-PERFORMANCE`、`WP-PG-CONCURRENCY` 第二片、`WP-CUSTOMER-UAT`、`WP-FINAL-RELEASE` 不新增需求主 owner，分别承担容量证据、补充竞争证据、18UAT 集成与最终证据收口；不能把 UAT 映射当成需求通过。

## 2. 唯一引用与命名

### 2.1 契约指针（只引用，不改写）

| 契约面 | 唯一文件/指针根 |
| --- | --- |
| 共享值类型 | `contracts/contract-bundle.schema.json#/$defs/`：`Id`、`Version`、`CnyCents`、`Minutes`、`PostingPeriod`、`SourceRef`、`EvidenceRef`、`PolicyVersion`、`AlgorithmVersion` |
| 实体/投影 | `contracts/entities.schema.json#/$defs/`；编辑 DTO 只在 `contracts/write-dtos.schema.json#/$defs/` |
| 具名命令 | `contracts/commands.schema.json#/$defs/`；回执在 `contracts/command-results.schema.json#/$defs/` |
| 查询 | `contracts/queries.schema.json#/$defs/`；结果在 `contracts/query-results.schema.json#/$defs/` |
| 事件 | `contracts/events.schema.json#/$defs/` |
| 目录/owner/消费者/桥 | `contracts/contract-catalog.json`、`contracts/source-registry.json`、`contracts/relationships.json` |
| 需求覆盖 | `contracts/requirements-coverage.json`；只证明映射，不证明执行 |
| 夹具 | `fixtures/fixture.json`（`PH134-SYNTHETIC-20261028` / `1.0`）、`fixtures/command-samples.json`、`fixtures/requirements-coverage.json`、`fixtures/verification.json`、`fixtures/sample-verification.json` |
| 政策与旧新切换 | `policy-decisions.md`、`legacy-cutover.md`、`architecture-contracts.md`、`docs/v1.0-delivery-plan.md#11.2` |

已核对的夹具事实（只读，未改）：`fixture_id=PH134-SYNTHETIC-20261028`、`version=1.0`、`status=preparation_fixture_only`、`contract_baseline=573ac46`（与本文 BASE `f1115cf` 不同，属未验证项）。本文件引用的具体文件哈希：`CSV-HIST-COST`=`11b26b0d870e6df7efafb6215ebbd87c813803b2c6e57be4132bb6386fc2bbea`、`CSV-AP-NORMAL`=`0292f26b653448be5241b5e0126ce49fb59af36b113df8c4c43574213d671ecb`、`CSV-AP-REORDERED`=`9a2666b2cb4acae0bfa4a95ffdb53a40aacf410ab19dac10907bf29570eac4ce`、`CSV-AP-CONFLICT`=`e5da815c6d5a8dffc35b132792a078cfc5d079b56cf2f32171cdfe6e95dbea61`、`ATT-RESTORE`=`39c0fcbfd54a682235cb81432e2219052d9676d9bb6cb2e9876972ee873c6347`。

JSON Pointer 中含 `/` 的键按 RFC 6901 转义为 `~1`，例如 `commands.schema.json#/$defs/onboarding.opening.commit~1v1`。文中省略公共前缀 `docs/v1.0-development/contracts/` 时，仍指该唯一文件。
下文简写约定：`<entity>` 指 `entities.schema.json#/$defs/<entity>`；`<command>~1v1` 指 `commands.schema.json#/$defs/<command>~1v1`；`<query>~1v1` 指 `queries.schema.json#/$defs/<query>~1v1`；`<event>.v1` 指 `events.schema.json#/$defs/<event>.v1`。出现 `#/$defs/...` 时一律以完整指针为准，不再复制字段。

### 2.2 命名与占位符声明

- 切片占位符：`PHC-<阶段>-<包简称>-<序号>`，如 `PHC-S6-IMPORT-1`。它是**候选命名空间**，不是 `project/tasks.json` 中的任务 ID，不抢真实编号，也不构成 ready/派工。
- 切片证据：`EV:<包>/<切片>@<candidate_sha>`，例如 `EV:S6-IMPORT/PHC-S6-IMPORT-1@f1115cf`。
- 需求证据：`REQ-EV:<requirement_id>@<切片占位符>`，每个需求只允许一个主引用；跨包生产者另记 `producer_ref`，不改变主 owner。
- UAT 证据：`UAT-EV:<uat_id>@<切片占位符>`，只能由 `requirement-trace.json` 的 `integration_owner_package` 汇总。
- 证据内容至少含：BASE/候选 SHA、契约摘要、夹具版本、环境/DB、实际命令与输出摘要、`pass|fail|not_run|blocked`、限制、`supersedes`。`pending` 不是 pass，缺输出不是默认成功。
- **SDK 不在本文件定义**：字段、`domain_metadata`、兼容规则、验收与 ready 顺序只以主控唯一 SDK 候选（验收单附文）及其最终 tasks/spec 为准；本文件只记录 PH-001 现状，不复制第二套字段，不自行放行。

## 3. 全局不变式（所有切片适用）

1. **金额/时间**：整数分、整数分钟；计算用 `Decimal`，`ROUND_HALF_UP`；禁止 float/bool；单笔上限 `10^12` 分。以 `contract-bundle.schema.json` 的 `CnyCents`/`Minutes` 为准。
2. **来源唯一**：业务消费键严格取 `fixtures/fixture.json#/source_consumption_key`（enterprise、purpose、original namespace/kind/stable id、detail、period、coverage）；`version`、`request_id`、`idempotency_key`、UI/CSV/API 渠道按同文件 `#/excluded_from_consumption_key` 不进入键。`SourceRef.version` 只用于读取快照，渠道、请求 key、版本变化不得重复消费。旧数字 PK 在适配层规范化，不因整数/字符串表示重复。
3. **账本/审计**：只追加；纠错用负值冲销并保留原来源；不关闭触发器、不直接改余额、不编辑历史哈希链、不为旧账伪造 `AccountingEvent`。旧 `LedgerEntry` 的 cost/cash_in/cash_out 原符号与原 PK 保留。
4. **锁序**：清洗 → 事务 → 公司锁 → 锁内重查成员/项目/期间/插件 → 期间/项目 → 来源 → 资金/未清/分配按固定类型与稳定 ID 排序 → 来源消费/幂等/版本/金额上限 → 事实/账本/审计/Outbox → 提交。跨域服务不自行 commit，不吞异常保存半笔。
5. **权限**：身份/企业/actor/可见项目/敏感字段从已认证上下文派生；客户端不提交组织、审批人、任意状态。本人业务/制单人不得自审；PM/员工不得读个人工资/费率；个人 AI/PAT 不获得审批、支付、锁期或客户签收能力。
6. **幂等**：所有具名命令 `idempotency_key` 长度与格式以指针为准；同 key + 同规范化输入返回原结果，输入变化冲突；`dry_run` 缺省由服务选 `true` 并规范化，显式 `false` 仍须普通服务授权；dry-run 不落事实/账本/Outbox/幂等记录。
7. **三模板**：一合同一项目；固定价只按已批准验收节点确认收入；确认工时只按客户确认的可计费分钟确认；固定服务期只按已确认期间覆盖确认。结算、开票、收款只产生/匹配未清、票据、现金事实，**不得再确认一次收入**。缺客户价格/分配/确认依据时只可草稿/试算，不用 0 或当前政策补齐。
8. **月度人工池**：新账方法 `monthly_comprehensive_capacity_v1`、算法 `monthly_group_half_up_largest_remainder_v1`；旧账按原快照、原逐行结果保留，不重算。合成反例 C=101 分、D=60 分钟，目标 A=1+1+1 分钟、B=31 分钟、未分配 26 分钟 → A=5、B=52、未分配=44，合计 101；旧账 2+2+2+52=58 不得改成 57。同一员工/月不得混用旧逐行与新月度池；目标月已有旧人工时，切换预检拒绝，改选后续完整月份。
9. **旧表/旧门**：旧表名、主键、账本、审计、附件、历史 URL/CSV/API 兼容层保留；旧 PH 只能显式承接，不能假 done、不能删除检查器、不能降低 `release_check.py` 的 9 项发布门。
10. **迁移**：只追加；已应用迁移不改；执行者不预占编号、不同时写共享接线；由主控按当前叶节点生成下一号，并在独立迁移链/兼容窗口内接线。
11. **容量基准不是产品硬上限**：`docs/v1.0-delivery-plan.md#11.2` 的 50 员工/200 项目/10 万记录/10 并发是**待测起测基准**，8GB/4 核是起测环境；列表/详情 P95 ≤ 2 秒、核心写 P95 ≤ 3 秒是设计目标。基准 +1（51 员工/201 项目/100,001 记录）不得被产品拒绝或降级；客户负载超出已验证范围时按实测重新定容量并记录环境，不把基准写成许可证或产品上限。

## 4. 就绪结论

- S6/S7 的 12 个包在 BASE 中均为 `design_candidate`、`dispatch_allowed=false`、`ph_task_ids=[]`，本文件不改变该事实。
- 依赖未 done：`WP-IMPORT` 依赖 S5 的 `WP-RECONCILE`/`WP-PROJECT-CLOSE`；`WP-INSTALL` 依赖 S1 的 `WP-SDK`/`WP-CORE`；其余 S6/S7 依赖链同理未闭合。因此 **S6/S7 本轮没有可置 ready 的包**；全部保持 `proposed`。
- “首批 ready”只能来自 S0–S5 中依赖已 done 且 DoR 完整者，由主控在同一 PH-137 整合 PR 中判定；本文不代替，也不把“设计输入齐全”写成“执行 DoR 齐全”。
- 每个包的就绪判据见第 7 节全局 DoR/DoD；未满足项一律 `blocked`，不以“文档已写”放行。

| 包 | 至少一个未 done 依赖 | 本轮结论 | 说明 |
| --- | --- | --- | --- |
| `WP-IMPORT` | `WP-RECONCILE`、`WP-PROJECT-CLOSE` | `proposed` | 导入合同/结算/现金映射未冻结前不 ready |
| `WP-OPENING` | `WP-IMPORT`、`WP-RECONCILE` | `proposed` | 期初类别/覆盖/旧新切换需先完成 |
| `WP-ONBOARDING` | `WP-OPENING`、`WP-UI-ACCEPTANCE` | `proposed` | 向导依赖开账与真实可访问验收 |
| `WP-INSTALL` | `WP-SDK`、`WP-CORE` | `proposed` | 安装可提前准备，但正式验收须绑定候选 |
| `WP-TLS` | `WP-INSTALL` | `proposed` | 需先有可安装候选 |
| `WP-UPGRADE-RESTORE` | `WP-INSTALL`、`WP-OPENING`、`WP-ONBOARDING` | `proposed` | 旧新映射/维护窗口未冻结 |
| `WP-PG-CONCURRENCY` | `WP-CLOSE`、`WP-OPENING`、`WP-REFUND`、`WP-SHARED` | `proposed` | 竞争域未完成，测试会空转 |
| `WP-PERFORMANCE` | `WP-ONBOARDING`、`WP-UI-ACCEPTANCE`、`WP-PG-CONCURRENCY` | `proposed` | 起测环境/基准/首用目标未实测，不能把设计目标当结果 |
| `WP-SUPPORT` | `WP-ONBOARDING`、`WP-UPGRADE-RESTORE` | `proposed` | 支持合同/退出流程未冻结 |
| `WP-RELEASE-PACKAGE` | `WP-TLS`、`WP-UPGRADE-RESTORE`、`WP-PG-CONCURRENCY`、`WP-PERFORMANCE`、`WP-SUPPORT`、`WP-INVOICE`、`WP-REVENUE-PAIR` | `proposed` | 9 门未过，不能发候选 |
| `WP-CUSTOMER-UAT` | `WP-RELEASE-PACKAGE`、`WP-PROJECT-CLOSE`、`WP-NOTIFY`、`WP-EXPORT`、`WP-EMPLOYEE-ADVANCE` | `proposed` | 无客户/候选版本，不能开 UAT |
| `WP-FINAL-RELEASE` | `WP-CUSTOMER-UAT` | `proposed` | 客户签收与 9 门未完成 |

## 5. PH-001 blocker 现状核实（只核实，不解除、不定义 SDK）

### 5.1 核实事实

- BASE `project/tasks.json`：`PH-001` 状态 `proposed`，`depends_on=["PH-000"]`（已 done），blocker 原文为“SDK字段、类型与兼容规则尚未冻结，先由PH-090规划PR明确契约，再将本任务置ready。”
- `PH-090` 虽为 done，但其 handoff 实际是“项目详情改版规划”，只登记了 `PH-022`，**没有冻结 SDK**。因此“PH-090 done”本身不构成 blocker 解除，不能据此把 PH-001 置 ready。
- 现有真实冻结面：`docs/plugin-architecture.md` 第 2 节明确当前 v1 manifest 恰好 8 字段，逐字段对应 `core/plugin_runtime.py::Registry.validate`；`core/plugin_sdk/`、`pmo_plugins/example_domain/` 尚不存在，`docs/adr/0002-domain-plugin-sdk.md` 也不存在。
- 结论：blocker 的问题成立且未按原文完成。**本文件不解除它**：不复制 SDK 字段表、不冻结兼容规则、不建议改 `PH-001` 状态、不指定 ADR 落点内容。

### 5.2 唯一 SDK 引用与边界

- SDK 的唯一候选是主控在验收单中附出的《SDK-1字段与兼容契约候选》及其唯一 schema（主控候选目录中的 `sdk1-metadata.schema.json`，不在本仓库）；字段、`domain_metadata`、命名空间/路由、兼容与拒绝规则、精确文件范围、验收和依赖顺序**一律以该候选与最终 tasks/spec 为准**。本文件不重述、不改写、不另立第二套。
- 该候选要求：先完成依赖安全薄片，再 SDK-1，再 SDK-2/3/CORE；`WP-SDK` 覆盖三张 PH。此顺序属最终规划内容，本文只作为 `WP-INSTALL`（依赖 `WP-SDK`）未就绪的原因引用，不排期、不派遣。
- `PH-001` 的解除、编号、状态与白名单变更由主控在最终规划按上述候选执行；本文件只提供事实核实，不构成开工或解除许可。`PH-002` 同理保持 `proposed`。

## 6. 旧 PH 承接、治理边界与发布门

### 6.1 旧 PH 承接（只记录，不删除、不假 done）

| 旧任务（BASE 状态） | 承接工作包 | 承接规则 |
| --- | --- | --- |
| PH-001（proposed） | `WP-SDK` | 只记录 blocker 现状（§5.1）；SDK 契约/字段/验收/状态只引用主控唯一候选，本文件不解除、不建议置 ready |
| PH-002（proposed） | `WP-SDK` | 保持 proposed；SDK-1 后的字段冻结与细分范围属最终规划 |
| PH-003（ready） | `WP-INSTALL` | 旧 ready 不因本文自动恢复执行；本包只承接其 Linux/Compose 安装与诊断，避免重复实现 |
| PH-004（proposed） | `WP-ONBOARDING` | 首次启用向导/重复安装保护承接；旧 spec 的 PH-015 限制由正式规划对齐，不能用本文补字绕过 |
| PH-005（ready） | `WP-WORKSPACES` | 信息/交互契约承接；本文件不改其范围 |
| PH-006（proposed） | `WP-CONTRACT`、`WP-CHANGE` | 合同/变更 epic 拆薄片；旧 WO 不能直接开工 |
| PH-007（proposed） | `WP-AR`、`WP-AP`、`WP-CLEARING` | 应收应付与核销；跨项目核销默认禁用 |
| PH-008（proposed） | `WP-BANK`、`WP-CLEARING`、`WP-REFUND` | 预收预付/退款/未分配收付 |
| PH-009（proposed） | `WP-SHARED` | 共享费用与重分配 |
| PH-010（proposed） | `WP-LABOR-POLICY`、`WP-LABOR-POST` | 月度综合池/组取整；旧账不重算 |
| PH-011（proposed） | 资源容量方向 | 后置扩展；不在 1.0 首批，不删除历史资产 |
| PH-012（proposed） | `WP-INVOICE` | 发票登记/分配/红冲；开票不造收入 |
| PH-013（proposed） | `WP-OPENING` | 存量期初/启用核对；与 `WP-IMPORT` 共用批次与来源 |
| PH-014（proposed） | `WP-NOTIFY` | 通知待办/事件幂等 |
| PH-015（proposed） | `WP-REVENUE-FIXED`、`WP-REVENUE-SERVICE`、`WP-REVENUE-PAIR` | 管理收入方法互斥/来源/调整；三模板分别验收 |
| PH-016（proposed） | `WP-WORKSPACES`、`WP-INSIGHT-METRICS` | 读模型/工作台 |
| PH-017（proposed） | AI 方向 | 后置；仅记录独立变更，不删发布 AI 门 |
| PH-018（proposed） | 个人客户端方向 | 后置；仅记录独立变更，不删 client 门 |
| PH-019（proposed） | `WP-PG-CONCURRENCY`、`WP-UPGRADE-RESTORE` | PG 并发/恢复/升级；不能以 SQLite 冒充 |
| PH-020（proposed） | `WP-RELEASE-PACKAGE`、`WP-FINAL-RELEASE` | 上线就绪/发布候选；旧门保持，替代须独立规划批准 |

规则：旧任务状态、spec、allowed_paths、acceptance、release-readiness 一律保持 BASE 原样。本文件只说明“由哪个新包承接”，不把任何旧任务标 done，不删旧依赖，不把旧门改成通过。PH-017/018/011 的 AI/客户端/资源方向若后置，必须另有显式变更任务与批准记录；不能由 S6/S7 实施者顺手删除。

### 6.2 PH-120～122 保持原范围

- PH-120（BASE `ready`）：可信 PR 交付契约、独立审查与自动仲裁；本文件不改其 spec、allowed_paths、acceptance、risk。
- PH-121（BASE `proposed`）：覆盖率、财务反例和发布边界；本文件不启用、不改门槛。
- PH-122（BASE `proposed`）：远端强制门禁、对抗演练与新会话交接；本文件不启用、不改 workflow。
- 本文件不修改 `.github/workflows/*`、`scripts/check_pr.py`、`scripts/release_check.py`、`project/release-readiness.json`，不新增/删除 required checks，不关闭保护，不伪造人类批准。

### 6.3 发布门（保持 9 项，全 pending）

`scripts/release_check.py` 对 `>=1.0` 要求以下 9 项均 `passed` 且有非空 evidence：`lifecycle`、`financial_rules`、`production_deployment`、`postgres_concurrency`、`backup_restore`、`agent_client`、`ai_evaluation`、`business_acceptance`、`licenses`。BASE 的 `project/release-readiness.json` 全部为 `pending`，本文件不填任何 pass。

S6/S7 只能生产候选证据，不能自行把门改成 passed；`WP-FINAL-RELEASE` 也只能在获得授权后汇总。AI/client 方向后置不等于删除 `ai_evaluation`/`agent_client` 门；如需调整，走独立规划变更。

## 7. 全局 DoR / DoD（逐包引用）

### 7.1 DoR（开工前必须同时满足）

| 编号 | 条件 |
| --- | --- |
| DOR-01 | 当前用户指令允许该工作；可信 BASE 上的 PH spec 已合入；本文只是规划输入，不是开工许可 |
| DOR-02 | `depends_on` 全部 done，且依赖契约在 BASE 兼容；未 done 即 blocked |
| DOR-03 | 所涉 C01–C12 字段/命令/查询/事件/政策/算法已冻结；只引用唯一 schema，无第二套可编辑字段 |
| DOR-04 | `allowed_paths` 与固定 acceptance argv 已写入可信 BASE 的 tasks；执行者不得扩白名单 |
| DOR-05 | 正例、反例、夹具 ID 与逐分/逐状态预期已冻结；缺项不得用 0、当前政策或现金代替 |
| DOR-06 | 隔离验收环境明确：SQLite 仅用于非并发单测；财务写/并发须真实 PG 多连接；运维须授权 Linux/PG；UI 须真实浏览器 |
| DOR-07 | 独立 S 级复核者已安排，且与执行者不同上下文；复核者不得是客户签收人 |
| DOR-08 | 迁移/共享接线由主控串行；执行者不预占迁移号、不改已应用迁移、不同时写共享文件 |
| DOR-09 | 旧表/PK/账本/审计/附件/旧入口保留方案明确；不删检查器、不降发布门、不伪造旧事件 |

### 7.2 DoD（完成时必须同时满足）

| 编号 | 条件 |
| --- | --- |
| DOD-01 | 代码/文档改动限定在白名单；实际命令、输出摘要、候选 SHA 与契约摘要绑定 |
| DOD-02 | 正例逐分/逐状态匹配；反例拒绝且账本/来源消费/桥/余额无副作用 |
| DOD-03 | 跨域黄金链按夹具阶段逐分比对；不允许模块各自绿而合起来错 |
| DOD-04 | 财务写/并发在真实 PG 多连接与确定性屏障下通过；SQLite 结果标 `not_run/blocked` |
| DOD-05 | UI/运维切片有真实浏览器或授权目标环境证据；配置存在、模板渲染、样张截图不算 |
| DOD-06 | 独立 S 级复核完成，发现关闭或保留 limitations；不隐藏失败/跳过 |
| DOD-07 | 当前 required CI 在同一候选通过，PR 经授权合入；未合入不得称完成 |
| DOD-08 | `project/reports/<PH>.json` 与 `project/handoffs/<PH>.md` 提交，证据引用唯一且可追溯 |
| DOD-09 | 未验证项、外部门、旧门状态显式列出；不以 `pending` 冒充 pass |

## 8. 包范围、文件与迁移接线（候选，不是 BASE 白名单）

§8 只是**包级上限**；可执行的最小写入面在 §9–§20 逐片列出。标“新建”的文件当前不存在，必须由最终 tasks 白名单显式列出后才能创建；现有文件才能修改。`tests/deployment/` 当前不存在，属拟新建目录。

| 包 | 执行者候选路径 | 仅主控接线/迁移 |
| --- | --- | --- |
| WP-IMPORT | `pmo_plugins/onboarding/*`、`pmo_plugins/onboarding/**/*`、`tests/test_v1_onboarding.py`（新建）、`core/imports.py`、`tests/test_imports_ai.py` | `pmo/settings.py`、`pmo/urls.py`、`templates/base.html`、`core/models.py`、`core/services.py`、`verify_integrity.py`、所有迁移、`project/tasks.json`、`project/manifest.json`、`docs/delivery-status.md` |
| WP-OPENING | 同上（不含 `core/imports.py`/`tests/test_imports_ai.py`） | 同上 |
| WP-ONBOARDING | `pmo_plugins/onboarding/*`、`pmo_plugins/onboarding/**/*`、`tests/test_v1_onboarding.py`、`tests/test_v1_workspace.py`（新建） | `pmo/settings.py`、`pmo/urls.py`、`templates/base.html`、`static/app.css`、`static/app.js`、`core/models.py`、`core/services.py`、`verify_integrity.py`、所有迁移、tasks/manifest、`docs/delivery-status.md` |
| WP-INSTALL | `scripts/install.sh`、`scripts/check-deployment.sh`、`scripts/backup.sh`、`compose.yaml`、`Dockerfile`、`Caddyfile`、`tests/deployment/*`、`tests/deployment/**/*` | `pmo/settings.py`、`pmo/urls.py`、`core/management/commands/verify_integrity.py`、所有迁移、tasks/manifest、发布/部署 workflow、`docs/delivery-status.md` |
| WP-TLS | 同 WP-INSTALL | 同上；TLS/Cookie/HSTS 设置与 DNS/证书授权由主控/环境责任人执行 |
| WP-UPGRADE-RESTORE | 同 WP-INSTALL | 同上；迁移叶节点、维护窗口、备份存储与恢复目标由主控/环境责任人执行 |
| WP-PG-CONCURRENCY | `tests/test_v1_integrations.py`、`tests/test_v1_concurrency.py`、`tests/deployment/*` | PG 环境、迁移叶、CI 命令、共享夹具接线由主控执行 |
| WP-PERFORMANCE | 同 WP-PG-CONCURRENCY | 目标硬件/授权环境、容量阈值、数据生成边界由主控冻结 |
| WP-SUPPORT | 同 WP-INSTALL | 支持合同/临时访问授权/数据交还窗口由主控与客户责任人执行 |
| WP-RELEASE-PACKAGE | `scripts/package.py`、`scripts/verify-source-package.py`、`tests/test_publish_release.py`（`scripts/release_check.py` 只读） | 版本号、标签、发布 workflow、发布环境、`project/release-readiness.json` 由主控在授权后执行 |
| WP-CUSTOMER-UAT | `docs/verification.md`、`docs/v1.0-development/*` | 真实客户、签收人、客户数据/周期由主控与客户责任人执行；代理不可代签 |
| WP-FINAL-RELEASE | `scripts/package.py`、`scripts/verify-source-package.py`、`tests/test_publish_release.py`、`project/specs/*`、`docs/v1.0-development/*`（`scripts/release_check.py` 只读） | tasks/manifest、报告/交接、release-readiness、标签/发布授权由主控执行 |

**迁移接线规则**：`WP-IMPORT/OPENING/ONBOARDING` 共用 `pmo_plugins/onboarding` 一条迁移链；三包还共享 `pmo_plugins/onboarding/{models,services}.py`，必须按 IMPORT→OPENING→ONBOARDING 串行合入，后包在前包合入后扩展，不得并行写同一文件。若新建 App，首号由主控按当前叶节点生成，执行者只写模型/服务/测试，不写 `pmo/settings.py`/`pmo/urls.py`/`templates/base.html`。运维包默认**不新增业务表**；C12 证据用现有报告/JSON/附件与既有 `Evidence`，如确需持久化，归 C12 owner 的独立 PH，不在 S6 擅自建第二套证据表。所有迁移只追加，旧表/PK/账本/触发器不动。

**已识别的范围缺口（不能靠实现 PR 自补）**：
- `WP-SUPPORT` 的候选路径只有运维脚本/部署测试，没有支持 runbook、客户导出/退出文档或支持专用页面的独立路径；只能只读引用现有 `docs/operations.md` 与既有导出页。若需新增 `docs/operations/support-*.md`、支持页或导出脚本，主控必须在 PH 契约中先补白名单。
- `WP-UPGRADE-RESTORE` 的升级/恢复需要明确脚本或 runbook 路径；候选只列现有 `scripts/backup.sh` 与 install/check 脚本，具体恢复入口由 PH 契约冻结，不在实现 PR 临时新增。
- `WP-CUSTOMER-UAT`/`WP-FINAL-RELEASE` 的报告/交接/客户证据文件由主控命名；本文件不预占文件名，避免与并行任务冲突。

## 9. WP-IMPORT · 全域模板、预览与幂等原子导入

`S6` · 需求 `V1-ONB-05`、`V1-ONB-06`（唯一主 owner：本包）· 依赖 `WP-RECONCILE`、`WP-PROJECT-CLOSE` · 合同 C01–C09、C11 · 风险 S。
交付：统一源命令映射；字段/单位/主数据冲突预览；行重排幂等、整批回滚与凭证更正。

### 9.1 切片（可交付行为）

| 切片 | 主需求 | 可验收行为 | 明确边界 |
| --- | --- | --- | --- |
| `PHC-S6-IMPORT-1` | `V1-ONB-05` | CSV 上传 → 模板识别 → 字段/单位/主数据映射 → 逐行冲突预览；预览零账本、零来源消费 | 只读预览；`ImportPreview.preview_only` 恒 true；不新增命令 |
| `PHC-S6-IMPORT-2` | `V1-ONB-06` | 批准预览 → 单事务按域调用既有命令 → 任一行失败整批回滚；重排/重试幂等；已过账走更正 | 不新增 `import.*` 命令；提交只编排既有具名命令 |

两片**串行**：都写 `pmo_plugins/onboarding/imports.py`，切片 1 合入后才开切片 2；不得并行。

### 9.2 契约锚点与接口

| 切片 | schema/命令/状态/权限 |
| --- | --- |
| 1 | `entities.schema.json#/$defs/ImportPreview`（`file_sha256`/`normalized_commands`/`conflict_ids`/`preview_only`）、`#/$defs/ImportConflict`（`source_key`/`row_number`/`code`/`message`）；两者 `status` 枚举 `draft/pending/approved/rejected/superseded/closed`。源用 `contract-bundle.schema.json#/$defs/SourceRef`；无编辑 DTO。 |
| 2 | 按导入种类调用既有命令：`masterdata.entity.upsert~1v1`、`contracts.contract.draft.save~1v1`/`contracts.version.submit~1v1`、`time.timesheet.submit~1v1`、`costing.cost.post~1v1`、`settlement.open_item.create~1v1`、`cash.transaction.record~1v1`、`management_revenue.recognize~1v1`；回执形状只取 `command-results.schema.json#/$defs/<同名命令>`；覆盖查询 `onboarding.coverage.get~1v1`。事件只在期初提交时由期初服务发 `onboarding.opening_committed.v1`，导入不另造事件。 |

权限：导入预览/提交需 owner 或 finance；PM 只可导入其可见项目且不得改财务政策；member 不获得批量财务导入；敏感字段读取留 `SensitiveReadAudit`；AI/PAT 不获得导入审批或批量写权限。
来源唯一：业务消费键不含文件名/行号/渠道；文件 SHA256 只标识预览输入。同 key 重排/重试返回原结果；同 key 改金额/期间/项目/覆盖为 `R-IDEMPOTENCY-CONFLICT`。预览不落账；提交整批单事务，任一行失败全部回滚。
锁序：清洗 → 单事务 → 公司锁 → 锁内重查成员/项目/期间/插件 → 按稳定 `source_key` 排序逐域调用 → 来源消费/幂等/版本/金额上限 → 事实/账本/审计/Outbox → 提交。禁止逐行 commit，禁止先写一半再报错。
状态：预览 `draft → pending → approved|rejected`，提交后 `closed`；冲突不因重试变成通过；已正式过账的回退走纠错/冲销，不删除原行。

### 9.3 执行文件与接线

| 切片 | 现有/新建文件 | 接线 |
| --- | --- | --- |
| 1 | 现有 `core/imports.py`（UTF-8 CSV、2MB、500 行基线，只读复用）；新建 `pmo_plugins/onboarding/imports.py`（解析/映射/冲突）、`models.py`（`ImportPreview`/`ImportConflict`）、`pmo_plugins/onboarding/templates/onboarding/import_preview.html`；扩展现有 `tests/test_imports_ai.py`；新建 `tests/test_v1_onboarding.py` 预览段 | App 注册、新表迁移、`pmo/urls.py`/插件路由、`templates/base.html`、`static/app.css` 改动均主控；执行者不写 |
| 2 | 新建/扩展 `pmo_plugins/onboarding/imports.py`（提交段）、`services.py`、`pmo_plugins/onboarding/templates/onboarding/import_result.html`、`tests/test_v1_onboarding.py` 提交段 | 同上；提交只编排既有服务，不新增根 URL 或迁移 |

迁移接线（root-only）：`pmo_plugins/onboarding` 一条新迁移链，首号由主控按当前叶节点生成；执行者只写模型/服务/模板/测试，不改已应用迁移、不改旧表。

### 9.4 正反例与逐分预期

| 用例 | 输入 | 预期 |
| --- | --- | --- |
| `POS-S6-IMPORT-01` | UAT-12-S02：`CSV-HIST-COST` 在 2026-08-31 已覆盖成本范围内预览/提交 | 成本仍 `200000` 分、应收 `50000` 分、现金 `0`、应付 `0`；`10000` 分历史费用只备查/引用，不再加成本；不生成新收入 |
| `POS-S6-IMPORT-02` | 同一 `source_key` 列重排后重试 | 返回原结果；不新增来源消费、账本、审计业务事件；金额不变 |
| `POS-S6-IMPORT-03` | 主数据先按稳定 ID 匹配，再提交合同/工时/费用 | 每行引用稳定 ID；预览不产生业务痕迹；提交全有或全无 |
| `NEG-S6-IMPORT-01` | 同 key 金额 `10000` 改 `10001` | `R-IDEMPOTENCY-CONFLICT`；不覆盖、不追加账本/审计业务事件 |
| `NEG-S6-IMPORT-02` | 引用他企业项目 | `R-CROSS-TENANT`；不披露对象、不落任何事实 |
| `NEG-S6-IMPORT-03` | 批量第 N 行非法（越期/缺项目/超上限） | 整批回滚；前 N-1 行也不得留下事实、来源消费或桥 |
| `NEG-S6-IMPORT-04` | 已正式过账后要求“回退导入” | 拒绝直接删除；改走纠错/负值冲销并保留原来源 |

夹具绑定（只读引用，不改）：`PH134-SYNTHETIC-20261028`/`1.0`；`CSV-HIST-COST`=`11b26b0d…c2bbea`，`CSV-AP-NORMAL`=`0292f26b…671ecb`，`CSV-AP-REORDERED`=`9a2666b2…0eac4ce`，`CSV-AP-CONFLICT`=`e5da815c…95dbea61`（完整哈希见 §2.1）。覆盖冲突同时受 `fixture.json#/boundary_counterexamples` 的 `BND-02` 约束。

### 9.5 固定验收、DoR/DoD、未验证

- 建议验收：`python scripts/projectctl.py check`；`python manage.py check`；`python manage.py makemigrations --check --dry-run`；`python manage.py test tests.test_imports_ai tests.test_v1_onboarding -v`；契约校核 `.agent-venv/bin/python -B docs/v1.0-development/contracts/validate_contracts.py` 与 `check_contract_cases.py`；夹具校核 `.agent-venv/bin/python -B docs/v1.0-development/fixtures/check_fixture.py`。本轮全部 `not_run`。
- DoR：DOR-01～09；额外要求 `V1-ONB-05/06` 的 `SPEC-*` 专项已映射，`CSV-HIST-COST`/重排/冲突样本已冻结。DoD：DOD-01～09；额外要求整批回滚、重排幂等、跨租户拒绝和覆盖冲突均有实际反例。
- 未验证外部门：真实浏览器导入页 1440/390、键盘/焦点；真实客户 CSV 变体；PG 下大批次与并发导入；附件/导出等价性。以上均不得以 schema 通过代替。

## 10. WP-OPENING · 期初承接、明细覆盖和开账核对

`S6` · 需求 `V1-ONB-02`、`03`、`04`、`07`（唯一主 owner：本包）· 依赖 `WP-IMPORT`、`WP-RECONCILE` · 合同 C02–C04、C06–C09、C11 · 风险 S。
交付：从零/存量范围清单；各域专用期初事件；汇总与明细互斥、未清不重记收入。

### 10.1 切片（可交付行为）

| 切片 | 主需求 | 可验收行为 | 明确边界 |
| --- | --- | --- | --- |
| `PHC-S6-OPEN-1` | `V1-ONB-02`、`V1-ONB-03` | 选择从零/存量与截止日 → 类别/项目/期间覆盖清单 → 明细重建或余额承接互相排斥的草稿与试算 | 只草稿/试算；零账本/现金/收入/义务副作用 |
| `PHC-S6-OPEN-2` | `V1-ONB-04` | 正式提交 → 按固定域顺序写各域期初事件与桥 → 覆盖冲突拒绝、金额只记一次 | 不另建核算规则；期初未清不再造收入/成本 |
| `PHC-S6-OPEN-3` | `V1-ONB-07` | 开账核对报告与责任人确认；UAT-12 全链：期初、收款、后续只核销 | 报告只呈现服务结果；缺项不得显示完整经营结果 |

三片**串行**：共享 `models.py`/`services.py`/`opening.py`，按 1→2→3 合入；不得并行写同一文件。

### 10.2 契约锚点与接口

| 切片 | schema/命令/状态/权限 |
| --- | --- |
| 1 | `entities.schema.json#/$defs/OpeningBatch`（`mode` = `rebuild_detail`/`carry_balance`；`status` = `draft/active/posted/reversed/superseded`）、`#/$defs/OpeningCoverage`（`coverage_category` 枚举 `cost/revenue/open_item/advance/cash`）、`#/$defs/OnboardingSession`（`required_parameter_gaps`/`coverage_confirmed`）；编辑 DTO 仅 `write-dtos.schema.json#/$defs/OpeningBatchDraft`；命令 `onboarding.opening_batch.draft.save~1v1`。 |
| 2 | 命令 `onboarding.opening.commit~1v1`（`payload` 必填 `opening_batch_id` + `expected_version`，见 `commands.schema.json#/$defs/onboarding.opening.commit~1v1`）；事件 `events.schema.json#/$defs/onboarding.opening_committed.v1`；回执 `command-results.schema.json#/$defs/onboarding.opening.commit~1v1`；实体 `OpeningEvent`、`OpeningCoverageBridge`。 |
| 3 | 查询 `onboarding.coverage.get~1v1`、`period.precheck.get~1v1`；无新命令、无新实体；报告引用上述指针，不复制金额字段。 |

权限：正式开账需 owner + finance 职责分离；提交人与批准人不得同一人；缺批准人/覆盖确认时只可试算；敏感期初金额读取留审计；AI/PAT 不获得开账/锁期能力。
来源唯一：批次 + 覆盖类别 + 区间 + 项目/池为唯一消费范围；同范围明细重建与余额承接互斥；不同类别可分别选择；版本/渠道变化不重复消费。重复提交同 `opening_batch_id+expected_version` 返回原结果；输入变化冲突。
锁序：公司锁 → 会话/批次 → 覆盖与期间预检 → 按固定域顺序（主数据→合同/项目→成本→结算/未清→现金/预付→收入/预测）写期初事件 → 来源消费/幂等/版本 → 账本/审计/Outbox → 提交。跨域不得各自 commit。
状态：`OnboardingSession draft→pending→approved→closed`；`OpeningBatch/OpenCoverage/OpenEvent draft→active→posted→reversed|superseded`；已 posted 只可反向/新批次，不原地改。

### 10.3 执行文件与接线

| 切片 | 现有/新建文件 | 接线 |
| --- | --- | --- |
| 1 | 新建 `pmo_plugins/onboarding/models.py`（`OpeningBatch`/`OpeningCoverage`/`OnboardingSession`）、`opening.py`（草稿/试算/覆盖互斥）、`pmo_plugins/onboarding/templates/onboarding/opening_*.html`；新建 `tests/test_v1_onboarding.py` 草稿段 | App 注册、迁移、共享导航/样式由主控 |
| 2 | 扩展 `opening.py`（提交）、`services.py`（锁序/事务/域顺序）、`models.py`（`OpeningEvent`/`OpeningCoverageBridge`）；扩展同测试文件提交段 | 主控生成迁移；`verify_integrity` 如需新增检查由主控 |
| 3 | 扩展 `services.py`（核对报告）、`pmo_plugins/onboarding/templates/onboarding/opening_report.html`；扩展同测试文件 UAT-12 段 | 报告导航主控；不改夹具 |

迁移接线（root-only）：同一 `pmo_plugins/onboarding` 迁移链；三片都不写 `pmo/settings.py`/`pmo/urls.py`/`templates/base.html`；旧 `LedgerEntry`/`Document`/旧迁移不动，不建第二套期初表。

### 10.4 正反例与逐分预期

| 用例 | 输入 | 预期 |
| --- | --- | --- |
| `POS-S6-OPEN-01` | UAT-12-S01 `opening.load` | 公司成本 `200000`、管理收入净额 `0`、应收 `50000`、应付 `0`、现金 `0`、预收 `0`、未耗预付 `0`、预测幅度 `50000`；声明账户余额 `100000` |
| `POS-S6-OPEN-02` | UAT-12-S03 收款 `50000` 并核销 | 应收 `0`、现金 `50000`、已声明账户余额 `150000`；收入仍 `0`，不因收款再造收入 |
| `POS-S6-OPEN-03` | 汇总承接与明细重建选择不同类别 | 各类别独立生效；公司成本/应收只按批准覆盖记一次；未验证项不能签“迁移正确” |
| `NEG-S6-OPEN-01` | 同项目/期间/类别与活动期初或履约覆盖重叠 | `R-COVERAGE`；无业务变更，不产生第二笔成本/收入 |
| `NEG-S6-OPEN-02` | 缺关键期初（银行/未清/项目/期间）却要求正式启用 | 只能试算或阻止正式启用；不得填 0、不得展示完整公司余额/利润 |
| `NEG-S6-OPEN-03` | 同员工/月已有旧逐行人工，仍切换到新月度池 | 预检拒绝该月；改选后续完整月份；旧 `58` 分保留，不改成 `57` |
| `NEG-S6-OPEN-04` | 期初未清后续收款又记收入/成本 | 拒绝；后续只核销未清 + 记现金，收入/成本不重复 |
| `NEG-S6-OPEN-05` | 提交人与批准人同一人 | `R-SAME-REVIEW`；无账本/审计业务事件 |

### 10.5 固定验收、DoR/DoD、未验证

- 建议验收：`python scripts/projectctl.py check`；`python manage.py check`；`python manage.py makemigrations --check --dry-run`；`python manage.py test tests.test_v1_onboarding -v`；契约/夹具校核同上；UAT-12 逐分链在真实 PG 上重跑。本轮全部 `not_run`。
- DoR：DOR-01～09；额外要求 `OPEN-20260831` 覆盖、模式互斥、客户开账参数（起账日/账户/非项目/税资格）已定义缺项行为。DoD：DOD-01～09；额外要求覆盖冲突、缺项、旧/新人工切换拒绝、收款不再造收入均有实际反例。
- 未验证外部门：客户真实起账日/银行账户/期初类别；真实旧库迁移；PG 多连接下的期初与锁期竞争；浏览器开账向导 1440/390；客户核对人签收。均 `not_run`。

## 11. WP-ONBOARDING · 首公司向导、首项目和自助手册

`S6` · 需求 `V1-ORG-01`、`V1-ONB-01`、`V1-ONB-08`（唯一主 owner：本包）· 依赖 `WP-OPENING`、`WP-UI-ACCEPTANCE` · 合同 C01、C03、C04、C11、C12 · 风险 S。
交付：安装后不可接管的启用；客户政策配置与缺项拒绝；可续接步骤/角色说明/数据导出。

### 11.1 切片（可交付行为）

| 切片 | 主需求 | 可验收行为 | 明确边界 |
| --- | --- | --- | --- |
| `PHC-S6-ONB-1` | `V1-ORG-01` | 首公司/起账日/币种/时区/核对负责人与角色 → 缺项清单 → 会话可续接；正式启用后不可重复接管 | 企业唯一租户；正式账后不直接改主体/本位币 |
| `PHC-S6-ONB-2` | `V1-ONB-01` | 首项目/首合同/收费模板（三模板分别配置）→ 一合同一项目；缺价格/分配/确认依据只草稿 | 不在向导内自建收入/成本规则 |
| `PHC-S6-ONB-3` | `V1-ONB-08` | 自助说明、样例与数据导出入口；角色说明与续接说明可读 | 示例/演练数据不得进入正式账 |

三片**串行**：共享 `models.py`/`services.py`/`wizard.py`；按 1→2→3 合入。

### 11.2 契约锚点与接口

| 切片 | schema/命令/状态/权限 |
| --- | --- |
| 1 | `entities.schema.json#/$defs/OnboardingSession`（`status` = `draft/pending/approved/rejected/superseded/closed`、`required_parameter_gaps`、`coverage_confirmed`）、`#/$defs/OrganizationProfile`；命令 `identity.membership.change~1v1`；查询 `identity.permissions.get~1v1`；无独立 `onboarding.*` 命令。 |
| 2 | 命令 `masterdata.entity.upsert~1v1`、`contracts.contract.draft.save~1v1`、`contracts.version.submit~1v1`；期初步骤只调用 `onboarding.opening_batch.draft.save~1v1`/`onboarding.opening.commit~1v1`；合同/项目实体指针以 `entities.schema.json#/$defs/Contract`、`#/$defs/Project` 为准。 |
| 3 | 查询 `insights.export.create~1v1`（导出申请，`dry_run` 默认 true）；`ExportManifest` 指针；无新命令、无新表；样例只读固定夹具。 |

权限：首公司启用/主体信息仅 owner；财务政策由 owner + finance 确认；成员只能完成本人资料；初始管理员凭据一次性，不写日志；重复安装不得接管已有公司；AI/PAT 不获得向导/审批/支付能力。
来源唯一：`OnboardingSession.id + version` 为会话幂等键；同一步骤重复提交返回原结果，内容变化冲突；首项目/首合同按稳定 ID 引用，不按名称猜。
锁序：公司锁 → 会话/步骤 → 锁内重查成员/角色 → 主数据 → 首项目/合同 → 期初编排 → 来源/幂等/版本 → 审计/Outbox → 提交。向导不得逐步骤提交半套财务事实；失败保留可续接步骤。
状态：会话 `draft→pending→approved→closed`；缺项未补齐不得 `approved`；正式启用后不可重新接管；示例/演练数据不得进入正式账范围。

### 11.3 执行文件与接线

| 切片 | 现有/新建文件 | 接线 |
| --- | --- | --- |
| 1 | 新建 `pmo_plugins/onboarding/models.py`（`OnboardingSession`）、`services.py`、`wizard.py`（步骤/缺口/续接）、`pmo_plugins/onboarding/templates/onboarding/step_*.html`；新建 `tests/test_v1_onboarding.py` 向导段 | `OrganizationProfile` 若落 `core/models.py` 由主控；App 注册/迁移/导航主控 |
| 2 | 扩展 `wizard.py`（首项目/首合同/三模板）、`services.py`；扩展同测试文件；复用 `pmo_plugins/onboarding/templates/onboarding/step_*.html` | 只编排既有服务；不改 `core/models.py` |
| 3 | 新建 `pmo_plugins/onboarding/templates/onboarding/manual.html`（或 `manual_*.html`）；新建 `tests/test_v1_workspace.py`（当前不存在）导出/说明段 | `templates/base.html`、`static/app.css`/`app.js`、根 URL 仅主控；如需新增 `docs/` 手册文件，白名单缺口见 §8 |

迁移接线（root-only）：同一 `pmo_plugins/onboarding` 迁移链；三片均不改 `pmo/settings.py`/`pmo/urls.py`/`templates/base.html`；不预占迁移号。

### 11.4 正反例与逐分预期

| 用例 | 输入 | 预期 |
| --- | --- | --- |
| `POS-S6-ONBOARD-01` | UAT-01-S01 建立 ENT-A、四职责 | 公司成本/收入/应收/应付/现金/预收/未耗预付均 `0`；ETC/预测 `null`；不伪造完整经营结果 |
| `POS-S6-ONBOARD-02` | UAT-01-S02 工资池 `2400000` 分 | 公司成本 `2400000`；管理收入 `0`；项目/非项目/未分配按夹具总和 `2400000`，不重复 |
| `POS-S6-ONBOARD-03` | 首项目 + 首合同，三模板分别选择 | 一合同一项目；三模板配置分别保存；缺价格/分配/确认依据时只草稿 |
| `NEG-S6-ONBOARD-01` | 已有正式账后重复安装/重新接管 | 拒绝；原主体/本位币/账本不变；不创建第二企业 |
| `NEG-S6-ONBOARD-02` | 缺起账日/币种/时区/核对负责人 | 阻止正式启用或只允许试算；不默认 0/当前政策 |
| `NEG-S6-ONBOARD-03` | 初始密码/令牌写入日志或页面 | 拒绝；凭据一次性、不写明文日志；安全审计可记拒绝但不含秘密 |
| `NEG-S6-ONBOARD-04` | 向导内自建收入/成本规则或跨项目合同 | 拒绝；合同版本/明细继承父项目，客户端不自报第二个项目 |

### 11.5 固定验收、DoR/DoD、未验证

- 建议验收：`python scripts/projectctl.py check`；`python manage.py check`；`python manage.py test tests.test_v1_onboarding -v`（`tests.test_v1_workspace` 由 ONB-3 新建后再加入）；契约/夹具校核同上；真实浏览器 UAT-01。本轮全部 `not_run`。
- DoR：DOR-01～09；额外要求客户政策配置 schema/缺项行为已冻结，UAT-01 基线与四职责就绪。DoD：DOD-01～09；额外要求重复接管、缺项拒绝、密码不落日志、三模板配置均有实际反例。
- 未验证外部门：真实客户政策（价格/税率/收入方法）；真实浏览器 1440/390、键盘/焦点；客户核对负责人签收；演练与正式数据隔离的现场证据。均 `not_run`。

## 12. WP-INSTALL · Linux 安装、失败诊断和健康可见

`S6` · 需求 `V1-OPS-01`、`V1-OPS-05`（唯一主 owner：本包）· 依赖 `WP-SDK`、`WP-CORE` · 合同 C01、C11 · 风险 S。
交付：Ubuntu/Compose 空安装；端口/磁盘/拉取/迁移失败；健康、worker、备份错误可见。

### 12.1 切片（可交付行为）

| 切片 | 主需求 | 可验收行为 | 明确边界 |
| --- | --- | --- | --- |
| `PHC-S6-INSTALL-1` | `V1-OPS-01` | Ubuntu 24.04 + Compose v2 空机安装；端口/磁盘/拉取/迁移失败可诊断、可安全重试 | 不覆盖既有 `.env`/卷；不静默装系统软件 |
| `PHC-S6-INSTALL-2` | `V1-OPS-05` | 健康、worker、备份状态与失败可见；重启后持久化；部署证据写入 manifest | 健康页只读脱敏；配置存在≠运行通过 |

两片**串行**：共享安装脚本与部署证据实体；1 合入后才写 2 的健康检查。

### 12.2 契约锚点与接口

| 切片 | schema/命令/状态/权限 |
| --- | --- |
| 1 | `entities.schema.json#/$defs/DeploymentManifest`（`source_commit`/`environment_reference`/`kind`/`checks`；`status` = `draft/pending/approved/rejected/superseded/closed`；`kind` 枚举 `isolated_ci/isolated_linux/customer_linux`）；脚本接口 `install.sh`（安装）、`check-deployment.sh --json`（诊断退出码）。 |
| 2 | 复用 `DeploymentManifest.checks` 记录健康/worker/备份检查；`entities.schema.json#/$defs/BackupManifest`（`database_sha256`/`attachment_manifest_sha256`/`attachment_count`/`storage_reference`）；无新实体；健康路由只读。 |

权限：安装/升级仅部署责任人；应用 owner 不自动获得主机 root；诊断页面需登录/授权，未认证只返回最小健康状态；日志脱敏；不把主机凭据写入仓库/报告。
来源唯一：`source_commit + environment_reference + kind + checks` 标识一次部署证据；同候选重复安装返回同一 manifest/追加检查，不覆盖旧证据；不同候选新建 manifest 并 `supersedes`。
锁序：运维窗口 → 单写者 → 备份/快照 → 迁移 → 启动 → 健康/完整性与 worker 检查 → 记录证据。部署锁不是业务公司锁的替代；迁移失败保留诊断并允许安全重试，不覆盖原数据。
状态：`DeploymentManifest/BackupManifest draft→pending→approved|rejected→superseded|closed`；健康失败不得标 approved；配置存在不等于运行通过。

### 12.3 执行文件与接线

| 切片 | 现有/新建文件 | 接线 |
| --- | --- | --- |
| 1 | 新建 `scripts/install.sh`、`scripts/check-deployment.sh`；现有 `compose.yaml`、`Dockerfile`、`Caddyfile`；新建 `tests/deployment/test_install.py` | 共享容器/入口/`pmo/settings.py` 由主控串行；不改已应用迁移 |
| 2 | 现有 `scripts/backup.sh`；新建健康检查片段（插件模板或既有页面片段）+ `tests/deployment/test_health.py`、`tests/deployment/test_backup_manifest.py` | `/healthz` 等 URI、`pmo/urls.py`、`templates/base.html` 仅主控；不新增业务表 |

迁移接线（root-only）：本包默认**不新增业务表**；部署/备份证据落 `DeploymentManifest`/`BackupManifest` 的 JSON 证据或既有 `Evidence`。如确需 DB 证据表，归 C12 owner 的独立 PH，不在 S6 自建。

### 12.4 正反例与逐分预期

| 用例 | 输入 | 预期 |
| --- | --- | --- |
| `POS-S6-INSTALL-01` | Ubuntu 24.04 + Compose v2 空机安装 | 应用/DB/卷独立；健康 200；worker 运行；`verify_integrity` 通过；重启后数据仍在 |
| `POS-S6-INSTALL-02` | 备份成功后读取 manifest | `database_sha256`/`attachment_manifest_sha256` 可核对；附件计数一致；权限最小化 |
| `POS-S6-INSTALL-03` | 迁移成功后重复执行安装 | 不覆盖 `.env`/卷；重复迁移安全；旧数据不变 |
| `NEG-S6-INSTALL-01` | 端口占用/磁盘不足/镜像拉取失败/迁移失败 | 明确失败、保留诊断、可安全重试；不覆盖原数据、不半启动 |
| `NEG-S6-INSTALL-02` | 应用或 DB 直接暴露公网 | 部署检查失败；不得以“配置存在”标通过 |
| `NEG-S6-INSTALL-03` | worker 停止/备份失败但页面仍显示健康 | 健康/告警必须识别并指向可操作诊断；不得绿屏掩盖 |
| `NEG-S6-INSTALL-04` | macOS/本机演示冒充 Linux 目标安装 | 标 `not_run/blocked`，不得声称已部署 |

### 12.5 固定验收、DoR/DoD、未验证

- 建议验收：`python scripts/projectctl.py check`；`python manage.py check`；`python -m unittest tests.test_backup_script -v`；目标环境 `bash scripts/check-deployment.sh`、`verify_integrity --json`；`tests/deployment/*`。本轮全部 `not_run`。
- DoR：DOR-01～09；额外要求 Ubuntu 24.04/Compose v2、独立密钥/DB/卷、失败分支清单已冻结。DoD：DOD-01～09；额外要求空机、重启持久化、四类失败、健康/worker/备份可见均有实际证据。
- 未验证外部门：授权 Linux 主机、真实 Docker/PG、DNS/端口/磁盘、目标环境访问；本文件不声称已安装或已部署。

## 13. WP-TLS · 真实域名 HTTPS 和安全边界

`S6` · 需求 `V1-OPS-02`（唯一主 owner：本包）· 依赖 `WP-INSTALL` · 合同 C01、C12 · 风险 S。
交付：实际域名/证书/续期；Cookie/HSTS/反向代理；错误配置拒绝及真实访问证据。

### 13.1 切片（可交付行为）

| 切片 | 主需求 | 可验收行为 | 明确边界 |
| --- | --- | --- | --- |
| `PHC-S6-TLS-1` | `V1-OPS-02` | 真实域名 HTTPS 登录/附件；HTTP→HTTPS；Secure/HttpOnly/SameSite；代理头不可伪造；证书续期；错误配置拒绝并留证据 | 无新增业务页面；配置存在或 `check --deploy` 绿不算 TLS |

单切片：TLS 的部署配置、证据记录、续期与错误配置属同一可验收行为，不拆独立 UI/模型 PR。确需拆分时只能按“真实公网证据”与“续期演练”拆，且必须由最终 tasks 冻结路径。

### 13.2 契约锚点与接口

| 项 | schema/命令/状态/权限 |
| --- | --- |
| 证据 | `entities.schema.json#/$defs/DeploymentManifest`（`kind`/`checks` 记录 TLS、HSTS、Cookie、代理、续期；`status` 同实体枚举）；不新建证书/域名业务表。 |
| 接口 | `Caddyfile`、`compose.yaml`、`scripts/install.sh`、`scripts/check-deployment.sh`；Django 安全设置（`SECURE_*`、Cookie、代理信任）由主控在 `pmo/settings.py` 接线。 |
| 入口 | 现有登录/附件页；真实域名 HTTPS、跳转、Secure Cookie；不加新页面、不改业务表单。 |

权限：TLS/代理配置仅部署责任人；证书私钥不入仓库/报告/日志；应用 owner 不自动获得主机权限；诊断页面脱敏；个人 AI/PAT 不获得证书或代理配置能力。
来源唯一：`source_commit + environment_reference + domain + certificate_serial/expiry` 标识一次 TLS 证据；续期追加新证据并 `supersedes`，不覆盖历史失败。
锁序：运维窗口 → 备份配置 → 更新代理/证书 → 重载 → 外部 TLS/登录/附件检查 → 记录证据；失败回退到上一可用配置，不改业务账。
状态：`DeploymentManifest draft→pending→approved|rejected`；配置存在、内部自签或 `check --deploy` 绿均不得标 approved；真实外部访问失败即 `rejected/blocked`。

### 13.3 执行文件与接线

| 现有/新建文件 | 接线 |
| --- | --- |
| 现有 `Caddyfile`、`compose.yaml`、`Dockerfile`；新建 `scripts/check-deployment.sh`（TLS 检查分支）；新建 `tests/deployment/test_tls.py`、`tests/deployment/test_proxy_headers.py` | `pmo/settings.py` 安全项仅主控；域名/证书/DNS 由环境责任人提供；根 URL/模板不改 |

迁移接线（root-only）：无迁移、无业务表。证书私钥、真实域名凭据不进入仓库/报告/日志，本文件不预占域名或证书序列号。

### 13.4 正反例与逐分预期

| 用例 | 输入 | 预期 |
| --- | --- | --- |
| `POS-S6-TLS-01` | 真实域名 + 有效证书访问登录/附件 | HTTPS 200；证书链/域名有效；HTTP 跳转 HTTPS；Cookie 为 Secure/HttpOnly/SameSite |
| `POS-S6-TLS-02` | 证书临近到期并续期 | 续期成功；旧连接不受影响；证据记录 serial/expiry；无业务账变化 |
| `POS-S6-TLS-03` | 反向代理转发真实客户端信息 | 仅信任已声明代理；不信任客户端伪造的 `X-Forwarded-*` |
| `NEG-S6-TLS-01` | 无效/过期/域名不匹配证书 | 拒绝或明确失败；不得标 approved；不得让用户绕过证书错误 |
| `NEG-S6-TLS-02` | 缺少 HSTS/安全 Cookie/HTTP 明文 | 部署检查失败；不得以“配置存在”放行 |
| `NEG-S6-TLS-03` | 仅在本机自签/局域网访问就声称公网 TLS | 标 `not_run/blocked`；真实公网证据另行记录 |

### 13.5 固定验收、DoR/DoD、未验证

- 建议验收：`python scripts/projectctl.py check`；`bash scripts/check-deployment.sh`；`tests/deployment/test_tls.py`；真实域名浏览器/TLS 扫描（授权环境）。本轮全部 `not_run`。
- DoR：DOR-01～09；额外要求域名/证书/续期责任人、代理边界、Cookie/HSTS 策略已冻结。DoD：DOD-01～09；额外要求真实 TLS、错误配置拒绝、续期、代理伪造拒绝均有证据。
- 未验证外部门：真实域名/DNS、CA/证书、公网入口、续期任务、外部扫描；本文件不声称已启用 HTTPS。

## 14. WP-UPGRADE-RESTORE · 基线升级与非空附件联合恢复

`S6` · 需求 `V1-OPS-03`、`V1-OPS-04`（唯一主 owner：本包）· 依赖 `WP-INSTALL`、`WP-OPENING`、`WP-ONBOARDING` · 合同 C02、C06–C09、C11 · 风险 S。
交付：旧库到候选迁移映射/切写；PG + 非空附件隔离恢复；真实登录报表权限/附件 hash 及 RPO/RTO。

### 14.1 切片（可交付行为）

| 切片 | 主需求 | 可验收行为 | 明确边界 |
| --- | --- | --- | --- |
| `PHC-S6-UPG-1` | `V1-OPS-03` | 冻结基线 `119a049…` 合成旧库升级到候选；旧 58 分/PK/审计/来源不变；重复升级与中断重试安全；仅结构兼容才回退 | 迁移只追加；不伪造旧 `AccountingEvent`、不倒放账本 |
| `PHC-S6-UPG-2` | `V1-OPS-04` | PG + 非空附件备份与隔离恢复；hash/计数/权限/报表核对；实测 RPO/RTO | 不以 SQLite 文件拷贝或 `check --deploy` 代替 |

两片**串行**：1 合入后才开 2 的恢复演练；共享 `scripts/backup.sh` 只读接口。

### 14.2 契约锚点与接口

| 切片 | schema/命令/状态/权限 |
| --- | --- |
| 1 | `entities.schema.json#/$defs/ReleaseManifest`、`#/$defs/BackupManifest`；升级脚本接口 `scripts/upgrade.sh --from <sha> --to <sha> --backup <manifest>`（**拟新建，白名单未决**）；完整性检查 `manage.py verify_integrity --json`。 |
| 2 | `entities.schema.json#/$defs/BackupManifest`（`database_sha256`/`attachment_manifest_sha256`/`attachment_count`/`storage_reference`）、`#/$defs/RestoreEvidence`（`database_integrity_passed`/`nonempty_attachment_count`/`attachment_content_match`/`business_snapshot_match`/`raw_log_sha256`）；恢复脚本接口 `scripts/restore.sh --backup <manifest> --target <isolated>`（**拟新建，白名单未决**）。 |

权限：备份/恢复仅授权运维；恢复目标使用最小权限账号；备份含客户数据，禁止进入源码包/外部代理；恢复后必须校验角色/停用/敏感字段可见性；客户数据交还/删除另由 `WP-SUPPORT` 承接。
来源唯一：`source_commit + backup_manifest + environment_reference` 标识一次恢复；同备份重复恢复返回同一证据/`existing`，不同字节/候选新建并 `supersedes`；附件以 SHA256 匹配，不以文件名猜。
锁序：维护窗口 → 停止受影响写入/排空 → 备份 DB+附件+配置引用 → 迁移（只追加）→ 启动 → 隔离恢复 → 校验完整性/权限/报表/附件 → 记录 RPO/RTO。不得关闭触发器、直接改余额、倒放账本；失败保留现场并前向修复。
状态：`BackupManifest/RestoreEvidence/ReleaseManifest draft→pending→approved|rejected`；完整性/附件/业务快照任一失败即 rejected/blocked，不得用部分成功签收。

### 14.3 执行文件与接线

| 切片 | 现有/新建文件 | 接线 |
| --- | --- | --- |
| 1 | 新建 `scripts/upgrade.sh`（白名单未决）；现有 `docs/v1.0-development/legacy-cutover.md`（只读依据）；新建 `tests/deployment/test_upgrade.py`；现有 `docs/v1.0-development/fixtures/verify_legacy.py` 只读复用 | 迁移叶、维护窗口、冻结基线归档 SHA 由主控；不预占迁移号 |
| 2 | 现有 `scripts/backup.sh`；新建 `scripts/restore.sh`（白名单未决）、`tests/deployment/test_restore.py`；现有 `tests/test_backup_script.py` 扩展 | 备份存储、隔离目标、PG 实例由环境责任人；不改旧表/主键 |

迁移接线（root-only）：升级只执行主控生成的追加迁移；执行者不改已应用迁移、不写 `pmo/settings.py`；恢复流程不新增业务表，证据写 `BackupManifest`/`RestoreEvidence`。

### 14.4 正反例与逐分预期

| 用例 | 输入 | 预期 |
| --- | --- | --- |
| `POS-S6-UPGRADE-01` | 冻结基线 `119a04916e3e96a5e4d86516a5f13e841faf2f67` 合成旧库升级到候选 | 旧账 `2+2+2+52=58` 分保留；原 PK/审计链/来源不变；迁移只追加；重复升级安全 |
| `POS-S6-UPGRADE-02` | 候选 PG + 非空附件备份，隔离恢复 | `database_integrity_passed=true`；`nonempty_attachment_count` 与清单一致；附件字节/hash 匹配；登录/报表/凭证可核对；记录实测 RPO/RTO |
| `POS-S6-UPGRADE-03` | 升级中断后重试 | 不覆盖原数据；可安全重试；失败现场保留；前向修复优先 |
| `NEG-S6-UPGRADE-01` | 恢复后附件 SHA256 与清单不符 | `R-HASH`；失败，不出具发布验收；不覆盖原附件 |
| `NEG-S6-UPGRADE-02` | 为旧账伪造 `AccountingEvent`/关闭触发器/改余额 | 拒绝；完整性失败；旧账保持 `origin=legacy`、`accounting_event_id=null` |
| `NEG-S6-UPGRADE-03` | 未证明结构兼容就回退旧代码 | 拒绝；只读保留现场；不倒放账本/不删除迁移 |
| `NEG-S6-UPGRADE-04` | 用 SQLite 文件拷贝或 `check --deploy` 冒充恢复 | 标 `not_run/blocked`；真实 PG/附件/RPO-RTO 另行执行 |

### 14.5 固定验收、DoR/DoD、未验证

- 建议验收：`python scripts/projectctl.py check`；`python -m unittest tests.test_backup_script -v`；`tests/deployment/test_upgrade_restore.py`；`python -B docs/v1.0-development/fixtures/verify_legacy.py --repo "$PWD"`；目标环境 `verify_integrity --json` + 登录/报表/附件核对。本轮全部 `not_run`。
- DoR：DOR-01～09；额外要求冻结基线归档 SHA、迁移叶、非空附件、维护窗口、RPO/RTO 记录口径已冻结。DoD：DOD-01～09；额外要求旧 58 分/PK/审计不变、重复升级、附件 hash、失败恢复、兼容回退均有证据。
- 未验证外部门：授权 Linux/PG 目标、非空附件存储、真实备份/恢复、RPO/RTO 实测、维护窗口与客户授权；本文件不声称升级或恢复已通过。

## 15. WP-PG-CONCURRENCY · 跨域 PG 竞争与最终完整性

`S6` · 需求 `V1-OPS-06`（唯一主 owner：本包）· 依赖 `WP-CLOSE`、`WP-OPENING`、`WP-REFUND`、`WP-SHARED` · 合同 C02、C06–C09、C11 · 风险 S。
交付：多连接审批/核销/退款/分配/期初/锁期；屏障和失败原子断言；候选绑定完整性回归。

### 15.1 切片（可交付行为）

| 切片 | 主需求 | 可验收行为 | 明确边界 |
| --- | --- | --- | --- |
| `PHC-S6-PG-1` | `V1-OPS-06`（主交付） | UAT-14 两连接：核销/退款/锁期竞争；确定性屏障；只一笔有效消费；证据 `backend=postgresql`、`all_skipped=false` | 不用 sleep 猜竞争；SQLite 不算 |
| `PHC-S6-PG-2` | 支持/验证（不改需求 owner） | 审批、分摊、期初竞争与“事实后、Outbox 前”故障注入回滚；跨域原子性 | 测试不改产品规则；不得绕过权限/锁期 |

两片**串行**：2 依赖 1 的 PG 双连接原语与证据 schema；不同测试文件可先后合入，但不得并行改共享夹具/CI 命令。

### 15.2 契约锚点与接口

| 切片 | schema/命令/状态/权限 |
| --- | --- |
| 1 | `entities.schema.json#/$defs/ConcurrencyTestEvidence`（`backend`/`independent_connections`/`synchronization_barrier_used`/`all_skipped`/`case_names`/`raw_log_sha256`；`status` = `draft/pending/approved/rejected/superseded/closed`）；命令 `settlement.clearance.create~1v1`、`cash.allocation.create~1v1`、`cash.refund.record~1v1`、`period.close~1v1`；回执只取 `command-results` 同名定义。 |
| 2 | 复用同一证据实体；命令 `costing.salary_pool.draft.save~1v1` + `costing.cost.post~1v1`、`costing.allocation.run~1v1`、`onboarding.opening.commit~1v1`；无新实体、无新产品规则。 |

权限：测试使用独立测试角色/最小权限；不得读取真实客户/工资；服务层仍按会话/PAT 推导权限；并发测试不能绕过权限/锁期；测试日志脱敏。
来源唯一：同命令 `idempotency_key` + 规范化输入唯一；同来源跨连接只允许一个有效消费；失败连接整体回滚；审计/Outbox 与事实同事务。
锁序：连接 A/B 各自清洗 → 事务 → **公司锁** → 锁内重查成员/项目/期间/插件 → 来源/资金/未清/分配按稳定顺序 → 来源消费/幂等/版本/上限 → 事实/账本/审计/Outbox → 提交。屏障只同步“准备就绪/抢锁”，不得用 sleep 或单连接模拟。
状态：证据 `draft→pending→approved|rejected`；任一案例 skip、`all_skipped=true`、SQLite 后端或缺少屏障即 rejected/blocked，不得写“并发通过”。

### 15.3 执行文件与接线

| 切片 | 现有/新建文件 | 接线 |
| --- | --- | --- |
| 1 | 新建 `tests/test_v1_concurrency.py`（UAT-14 核销/退款/锁期）；现有 `tests/test_v1_preparation_pg.py` 的 PG 双连接/公司行锁原语只读复用 | PG 实例、CI 命令、证据落盘由主控；不改产品代码 |
| 2 | 新建 `tests/test_v1_integrations.py`（审批/分摊/期初竞争、故障注入）；复用同原语 | 同上；不改夹具 UAT-14/`SE-SEP-10000`/`AR-U14` |

迁移接线（root-only）：无迁移、无业务表；证据写 `ConcurrencyTestEvidence` JSON，不新建证据表。执行者不写 `.github/workflows/*` 或 `pmo/settings.py`。

### 15.4 正反例与逐分预期

| 用例 | 输入 | 预期 |
| --- | --- | --- |
| `POS-S6-PG-01` | UAT-14-S01：已有现金 `10000`，未清 `10000`，两连接各争核销 `8000` | 仅一笔 `8000` 核销；未清余 `2000`；现金仍 `10000`；未分配现金 `2000`；不生成第二笔银行事实 |
| `POS-S6-PG-02` | UAT-14-S02：9 月关账 vs 9 月 `SE-SEP-10000` 过账，屏障让 close 先锁 | close 获胜；过账整体拒绝；无 9 月成本新增；关账 watermark 正确 |
| `POS-S6-PG-03` | 同一工资/来源两连接审批 | 只一个有效财务来源；公司成本 `2400000` 只一次；第二连接返回原结果/冲突 |
| `POS-S6-PG-04` | 注入“事实后、Outbox 前”失败 | 事实/账本/桥/Outbox 全部回滚；审计/完整性有效；重试前可查原幂等键 |
| `NEG-S6-PG-01` | 两笔 `8000` 争 `10000` 未清/现金 | `R-OVER-CLEAR` 或等值拒绝；不得负余额、不得超额核销 |
| `NEG-S6-PG-02` | 已锁 9 月继续过账 | `R-CLOSED-PERIOD`；无账本/审计业务事件；不移动计划到预测周 |
| `NEG-S6-PG-03` | 两笔退款 `8000` 对原预收 `10000` | 最多 `8000` 有效，余 `2000`；失败事务不留下跨域半笔 |
| `NEG-S6-PG-04` | SQLite/单连接跑 UAT-14 | `BND-06`：`not_run/blocked`，不得标 pass |

### 15.5 固定验收、DoR/DoD、未验证

- 建议验收：`python scripts/projectctl.py check`；`PH135_REQUIRE_PG=1 python manage.py test tests.test_v1_preparation_pg tests.test_v1_concurrency tests.test_v1_integrations -v`（真实 PG 环境）；`verify_integrity --json`。本轮全部 `not_run`。
- DoR：DOR-01～09；额外要求真实 PG、双连接、确定性屏障、UAT-14 案例/夹具、候选 SHA 绑定已冻结。DoD：DOD-01～09；额外要求审批/核销/退款/分摊/期初/锁期竞争和回滚均有实际输出，`all_skipped=false`、`backend=postgresql`。
- 未验证外部门：真实 PG 版本/双连接、CI 环境、并发时序稳定性、目标硬件；本文件不声称任何并发通过。

## 16. WP-PERFORMANCE · 容量与首用操作任务验证

`S6` · 无直接需求主 owner（不新增需求归属）· 依赖 `WP-ONBOARDING`、`WP-UI-ACCEPTANCE`、`WP-PG-CONCURRENCY` · 合同 C12 · 风险 S。
交付：在具名起测环境按 50 员工/200 项目/10 万记录/10 并发基准测 P95（目标列表/详情 ≤2 秒、核心写 ≤3 秒）；首用时间与长任务反馈；超出已验证负载时重新定容量。**基准不是产品硬上限。**

### 16.1 切片（可交付行为）

| 切片 | 需求归属 | 可验收行为 | 明确边界 |
| --- | --- | --- | --- |
| `PHC-S6-PERF-1` | 无直接需求；为 `V1-OPS-05` 提供容量/可见性证据 | 在具名起测环境按夹具基准测列表/详情/核心写 P95 与 10 并发；记录环境、数据规模、错误率、连接数 | 基准是待测起点，不是产品上限；超基准不拒绝业务 |
| `PHC-S6-PERF-2` | 无直接需求；验证首用可用性 | 首用路径（登录→向导→首项目→首合同→首条投入）与长任务进度/取消/失败可见；1440/390、键盘/焦点 | 不改业务规则；不隐藏慢/失败 |

两片**串行**：2 复用 1 的环境与数据生成器；不同文件，但同一证据实体，按 1→2 合入。

### 16.2 契约锚点与接口

| 切片 | schema/命令/状态/权限 |
| --- | --- |
| 1 | 复用 `entities.schema.json#/$defs/ConcurrencyTestEvidence`（`backend`/`independent_connections`/`synchronization_barrier_used`/`all_skipped`/`case_names`/`raw_log_sha256`）与 `#/$defs/DeploymentManifest`（`source_commit`/`environment_reference`/`kind`/`checks`）记录环境与结果；阈值只写证据与 PH 契约，不写 schema default。 |
| 2 | 复用 `DeploymentManifest.checks` 记录首用耗时与长任务状态；无新实体、无新命令；页面只呈现既有服务结果。 |

阈值与环境的唯一来源：`docs/v1.0-delivery-plan.md#11.2`——单企业 50 员工/200 项目/10 万条记录/10 并发为**建议基准夹具**，8GB 内存/4 核为**起测环境**，列表/详情 P95 ≤ 2 秒、核心写 P95 ≤ 3 秒为设计目标。不得把该规模写成产品硬上限或拒绝条件。

权限：容量测试只用合成企业/角色；不得读取真实客户/薪酬；测试账号最小权限；性能页面不泄露敏感字段；AI/PAT 不获得压测写权限。
来源唯一：`source_commit + environment_reference + fixture_version + dataset_scale + concurrency` 标识一次容量证据；同候选重复跑追加结果，不覆盖失败；不同硬件/候选新建并 `supersedes`。
锁序：与业务写相同（公司锁→…→提交）；压测不得绕过锁/权限/期间；长任务必须有进度/取消/失败可见，不以 sleep/前端假进度冒充。
状态：证据 `draft→pending→approved|rejected`；未达设计目标、缺少 P95/10 并发、`all_skipped` 或仅开发机结果即 blocked；超基准本身不是失败，容量数字不替代业务正确性。

### 16.3 执行文件与接线

| 切片 | 现有/新建文件 | 接线 |
| --- | --- | --- |
| 1 | 新建 `tests/deployment/test_capacity.py`（合成数据生成 + 基准 + P95 统计）；复用现有工作台/项目/导出查询 | 目标硬件/DB/数据生成边界由主控冻结；只读/写基准绑定同一候选 SHA |
| 2 | 新建 `tests/deployment/test_first_use.py`（首用路径、长任务状态、浏览器检查脚本） | 共享前端文件主控串行；真实浏览器设备/账号由验收环境提供 |

迁移接线（root-only）：无迁移、无业务表；只用合成企业/角色，测试数据不落正式账；证据写 `ConcurrencyTestEvidence`/`DeploymentManifest` JSON。

### 16.4 正反例与逐分预期

| 用例 | 输入 | 预期 |
| --- | --- | --- |
| `POS-S6-PERF-01` | 8GB/4 核起测环境，单企业 50 员工/200 项目/100,000 记录，10 并发只读工作台/项目列表 | 全部请求成功；记录实际列表/详情 P95（目标 ≤ 2 秒）、错误率、DB 连接数；环境、候选 SHA、夹具版本写入证据 |
| `POS-S6-PERF-02` | 同环境核心写操作（提交工时/费用等） | 核心写 P95 目标 ≤ 3 秒；逐命令记录，不合并成单一平均值；未达标标 fail，不调低目标 |
| `POS-S6-PERF-03` | 首用路径：登录→向导→首项目→首合同→首条投入 | 记录每步耗时；对照交付方案目标（首个基础项目 30 分钟内、员工日常填报约 1 分钟、PM 周更新 10–15 分钟）；无空白页/假进度 |
| `POS-S6-PERF-04` | 长任务（导入/导出/备份） | 有进度/取消/失败原因；worker 积压可见；完成后结果可核对 |
| `NEG-S6-PERF-01` | 基准 +1：51 员工/201 项目/100,001 记录 | **必须正常接受并成功处理**，不得拒绝、降级或提示“超容量”；只记录为超出起测基准的观察值。基准不是产品硬上限 |
| `NEG-S6-PERF-02` | 客户负载明显超过已验证范围 | 按实测重新定容量并更新证据/说明；不得静默丢数据、无限排队或以旧基准拒绝业务 |
| `NEG-S6-PERF-03` | worker 停止/磁盘近满/导出超时 | 健康/告警/失败可见并可办理；不得显示成功 |
| `NEG-S6-PERF-04` | 仅 macOS 开发机/单连接结果 | 标 `not_run/blocked`；不得冒充目标环境/10 并发 |
| `NEG-S6-PERF-05` | 为达标修改业务规则/跳过权限/锁期 | 拒绝；性能优化不得改变金额/权限/来源/账本 |

### 16.5 固定验收、DoR/DoD、未验证

- 建议验收：`python scripts/projectctl.py check`；`tests/deployment/test_capacity.py`；`tests/deployment/test_first_use.py`；真实浏览器 1440/390；目标环境 P95/10 并发。本轮全部 `not_run`。
- DoR：DOR-01～09；额外要求具名起测环境（8GB/4 核或经批准等价）、夹具基准、10 并发、P95/首用目标与合成数据生成边界已冻结；确认基准不作为产品拒绝阈值。DoD：DOD-01～09；额外要求 P95/10 并发、首用路径、长任务反馈、基准 +1 正常接受、超已验证负载重新定容量均有实际输出；目标不得事后下调。
- 未验证外部门：目标硬件、生产型数据量、真实浏览器、10 并发、网络/磁盘；本文件不声称任何性能达标。

## 17. WP-SUPPORT · 客户支持、数据退出和授权排障

`S6` · 需求 `V1-OPS-07`（唯一主 owner：本包）· 依赖 `WP-ONBOARDING`、`WP-UPGRADE-RESTORE` · 合同 C01、C12 · 风险 S。
交付：支持/响应/备份责任；临时授权排障/数据保管；客户可读导出附件和退出步骤。

### 17.1 切片（可交付行为）

| 切片 | 主需求 | 可验收行为 | 明确边界 |
| --- | --- | --- | --- |
| `PHC-S6-SUP-1` | `V1-OPS-07` | 客户授权人申请导出 → manifest/附件清单/hash 可核对；临时/个人令牌撤销立即生效；数据交还引用批准记录 | 只列授权范围；不显示个人费率/工资；不新增共享密钥 |
| `PHC-S6-SUP-2` | 支持/退出文档（无独立需求） | 支持期/响应/备份责任/临时访问期限/退出 runbook 文档化并演练 | **阻塞**：`docs/operations/support-*.md` 不在当前候选白名单；须主控先冻结路径 |

`SUP-2` 在本轮**不可执行**：不列入 ready，不自行创建文档目录；保留为 §8 的范围缺口，由最终规划补白名单后再切片。`SUP-1` 单切片即可验收，不拆模型/服务/页面 PR。

### 17.2 契约锚点与接口

| 项 | schema/命令/状态/权限 |
| --- | --- |
| 证据 | `entities.schema.json#/$defs/ExportManifest`（`query_contract`/`as_of_date`/`watermark_ref`；`status` = `draft/active/posted/reversed/superseded`）、`#/$defs/Evidence`（`sha256`/`original_name`/`uploaded_by_member_id`；同状态枚举）、`#/$defs/DeliveryReceipt`（`acceptance_ref`/`received_on`/`received_by_member_id`）；`AgentGrant`/`SensitiveReadAudit` 以 C01 指针为准。 |
| 接口 | 命令 `insights.export.create~1v1`（`dry_run` 默认 true）、`delivery.evidence.attach~1v1`、`identity.agent_grant.revoke~1v1`；查询 `identity.permissions.get~1v1`；无新命令。 |
| 入口 | 复用现有导出/设置页与附件清单；如需模板片段，主控先补 `templates/*` 白名单；不新增支持后门或客户专用页面。 |

权限：客户数据 owner 是客户；支持人员仅最小临时访问、有期限、可撤销、全程审计；不新增共享管理员密钥；导出/删除需客户授权人；个人 AI/PAT 不获得导出审批/删除/支持后门。
来源唯一：`query_contract + as_of_date + scope + watermark_ref` 标识一次导出；同请求重复返回原 manifest；附件按 SHA256 匹配；删除/交还必须引用批准记录与范围，不静默清库。
锁序：导出走只读快照/watermark，不阻塞业务写；撤销令牌先失效再审计；删除/交还在维护窗口内单写者执行，先备份/清单再操作；不得绕过账本/审计保留。
状态：`ExportManifest`/`DeliveryReceipt` 状态以实体枚举为准（`draft/active/posted/reversed/superseded`）；`Evidence` 同；未授权/过期/跨租户即拒绝，不因支持角色放宽。

### 17.3 执行文件与接线

| 切片 | 现有/新建文件 | 接线 |
| --- | --- | --- |
| 1 | 现有 `core/models.py::Evidence`、`scripts/backup.sh`（只读）、`docs/operations.md`（只读）；新建 `tests/deployment/test_support_export.py`、`tests/deployment/test_support_revoke.py`；复用现有导出/设置模板 | 导出/撤销命令已存在，不新增支持脚本；`pmo/urls.py`/模板片段仅主控；客户数据不进仓库 |
| 2 | （阻塞）拟新建 `docs/operations/support-*.md` 与退出 runbook | 路径白名单未决；主控冻结前不创建 |

迁移接线（root-only）：无迁移、无业务表；如需新增 C12 表，归 C12 owner 独立 PH。客户真实数据、合同、工资不进入本包测试或报告。

### 17.4 正反例与逐分预期

| 用例 | 输入 | 预期 |
| --- | --- | --- |
| `POS-S6-SUPPORT-01` | 客户授权人申请其范围导出 | `ExportManifest` 绑定 query/as_of/watermark；只含授权字段；附件清单/hash 可核对；无个人费率/工资 |
| `POS-S6-SUPPORT-02` | 撤销临时/个人令牌 | 立即失效；后续调用拒绝；历史账/审计/附件仍可按权限读取；有审计 |
| `POS-S6-SUPPORT-03` | 客户退出/交还 | 按约定导出数据+附件清单；删除/保留有批准与记录；不删除客户未授权的历史 |
| `NEG-S6-SUPPORT-01` | 未授权/过期/跨租户导出 | `permission_denied`/`not_found`；不披露对象/计数/附件 |
| `NEG-S6-SUPPORT-02` | 支持人员共享管理员密钥/长期后门 | 拒绝；仅限期、最小 scope、可撤销令牌；不新增共享密钥 |
| `NEG-S6-SUPPORT-03` | 未备份/无清单直接删除客户数据 | 拒绝；先备份/清单/批准；不得以“支持需要”绕过 |
| `NEG-S6-SUPPORT-04` | 代理代替客户签收/确认退出 | 无效；签收人必须是客户授权业务负责人 |

用例归属：`POS-01`、`POS-02`、`NEG-01`、`NEG-02` 属可执行切片 `SUP-1`；`POS-03`、`NEG-03`、`NEG-04` 依赖 `SUP-2` 的文档/退出路径，白名单未冻结前一律 `blocked`，不得以“文档已写”标通过。

### 17.5 固定验收、DoR/DoD、未验证

- 建议验收：`python scripts/projectctl.py check`；`python manage.py test tests.test_agent_api -v`；`tests/deployment/test_support_export.py`、`tests/deployment/test_support_revoke.py`；导出/附件/撤销实际演练（真实 PG）。`tests/deployment/test_support_exit.py` 属阻塞的 `SUP-2`，本轮 `blocked`。本轮全部 `not_run`。
- DoR：DOR-01～09；额外要求支持期/响应/备份责任、临时访问期限/scope、导出/交还/删除约定已冻结。DoD：DOD-01～09；额外要求授权导出、令牌撤销、退出/交还、越权拒绝均有实际文档与证据。
- 未验证外部门：真实客户支持合同/响应窗口、数据交还/删除的法律与客户授权、真实附件导出、备份责任分工；均 `not_run`。

## 18. WP-RELEASE-PACKAGE · 版本化发行资产与安全清单

`S7` · 需求 `V1-OPS-08`（唯一主 owner：本包）· 依赖 `WP-TLS`、`WP-UPGRADE-RESTORE`、`WP-PG-CONCURRENCY`、`WP-PERFORMANCE`、`WP-SUPPORT`、`WP-INVOICE`、`WP-REVENUE-PAIR` · 合同 C12 · 风险 S。
交付：源码/镜像/配置/许可证/摘要；敏感内容排除/同提交 CI；候选发布与旧门合规核对。

### 18.1 切片（可交付行为）

| 切片 | 主需求 | 可验收行为 | 明确边界 |
| --- | --- | --- | --- |
| `PHC-S7-REL-1` | `V1-OPS-08` | 候选源码/镜像/配置/许可证/摘要打包；敏感内容排除；同提交 CI；9 项旧门只读核对；`ReleaseManifest` 记录 | 只产出候选资产/清单；不发布、不打标签、不推送镜像 |

单切片：打包与门核对共享同一候选与同一批证据，不拆“模型/服务/UI/跨域”PR；本包无浏览器入口。

**外部前置（不属本切片）**：主控另有《首个正式薄片：依赖安全修复候选》（`djangorestframework` 3.16.1→3.18.2，独立 P0 安全 PH）。该候选的字段、白名单、验收与 ready 条件以其自身最终 tasks/spec 为准；本文件只把它记为发行前的外部依赖，不复制、不放行、不代替其证据。

### 18.2 契约锚点与接口

| 项 | schema/命令/状态/权限 |
| --- | --- |
| 证据 | `entities.schema.json#/$defs/ReleaseManifest`（`source_commit`/`version_label`/`artifact_sha256`/`required_evidence_ids`；`status` = `draft/pending/approved/rejected/superseded/closed`）；`EvidenceRef` 关联附件/验收；版本单一来源为 `pyproject.toml` + 标签一致性检查。 |
| 接口 | 现有 `scripts/release_check.py`（9 门只读校验）、`scripts/package.py`、`scripts/verify-source-package.py`、`scripts/public_hygiene.py`；现有 `tests/test_publish_release.py` 扩展；不新增发布依赖或网络上传。 |
| 入口 | 无新增页面；发布清单由报告/附件读取，不做管理页面。 |

权限：仅主控/授权发布责任人可打包/校验；执行者不发布、不打标签、不推送镜像；构建令牌最小权限；发布清单不含秘密；个人 AI/PAT 不获得发布权限。
来源唯一：`version_label + source_commit + artifact_sha256` 唯一；同候选重复打包返回同摘要/`existing`，字节变化即新 manifest 并 `supersedes`；标签与 `pyproject.toml` 版本必须一致。
锁序：构建/打包为不可变产物；先校验源码/摘要/敏感内容 → 同提交 CI → 旧门核对 → 记录 manifest。发布/部署是独立授权动作，不能由合入触发。
状态：`ReleaseManifest draft→pending→approved|rejected`；摘要不符、敏感内容、旧门未过、CI 非同一提交即 rejected/blocked；`approved` 不等于已发布。

### 18.3 执行文件与接线

| 现有/新建文件 | 接线 |
| --- | --- |
| 现有 `scripts/release_check.py`（只读）、`scripts/package.py`、`scripts/verify-source-package.py`、`scripts/public_hygiene.py`、`tests/test_publish_release.py` | 版本号/标签/workflow/发布环境仅主控授权后；执行者不改 `.github`、`project/release-readiness.json`、`scripts/release_check.py` |

迁移接线（root-only）：无迁移、无业务表；不新增版本/资产表。`project/release-readiness.json`、标签、镜像仓库、发布 workflow 由主控在授权后处理，本文件不改。

### 18.4 正反例与逐分预期

| 用例 | 输入 | 预期 |
| --- | --- | --- |
| `POS-S7-REL-01` | 候选源码打包 | 源码归档/镜像/配置/许可证/摘要齐全；`artifact_sha256` 可复算；同提交 CI 通过；`required_evidence_ids` 指向真实证据 |
| `POS-S7-REL-02` | 重复打包同候选 | 摘要一致；返回 existing/同 manifest；不生成第二套资产 |
| `POS-S7-REL-03` | 旧门核对 | 9 项 gate 均有状态与 evidence；本包只读核对，不把 pending 改 passed |
| `NEG-S7-REL-01` | 包含 runtime/.env/凭据/客户数据/虚拟环境/备份 | 拒绝打包；明确列出命中文件；不生成发布资产 |
| `NEG-S7-REL-02` | 摘要/标签/版本不一致 | `release_check.py` 失败；不生成 approved；不发布 |
| `NEG-S7-REL-03` | CI 非同一提交/旧结果/自报 pass | 拒绝；必须同提交 required CI 实际通过 |
| `NEG-S7-REL-04` | 候选修改 `release_check.py` 或 9 门清单自我放行 | 拒绝；检查器/门槛取可信 BASE，候选不得自改 |

### 18.5 固定验收、DoR/DoD、未验证

- 建议验收：`python scripts/projectctl.py check`；`python -m unittest tests.test_publish_release -v`；`python scripts/release_check.py`（不传 tag，仅候选元数据）；`python scripts/verify-source-package.py <archive>`；同提交 CI。本轮全部 `not_run`。
- DoR：DOR-01～09；额外要求版本单一来源、资产清单、敏感内容排除规则、9 门状态、同提交 CI 绑定已冻结。DoD：DOD-01～09；额外要求源码/镜像/配置/许可证/摘要一致、敏感内容拒绝、旧门未降级。
- 未验证外部门：发布授权、标签/镜像仓库、签名/摘要、发布环境、客户交付清单；本文件不发布、不打标签、不声称发行就绪。

## 19. WP-CUSTOMER-UAT · 三模板客户周期与独立使用验收

`S7` · 无直接需求主 owner；承担 UAT-01 集成 owner 与 18UAT 客户侧证据 · 依赖 `WP-RELEASE-PACKAGE`、`WP-PROJECT-CLOSE`、`WP-NOTIFY`、`WP-EXPORT`、`WP-EMPLOYEE-ADVANCE` · 合同 C12 · 风险 S。
交付：三家分批实际角色办理；完整月度核对/可追溯决策；客户负责人签收及限制，禁止代理代签。

### 19.1 切片（可交付行为）

| 切片 | 需求归属 | 可验收行为 | 明确边界 |
| --- | --- | --- | --- |
| `PHC-S7-CUST-1` | 无直接需求；UAT-01 集成 owner + 18UAT 客户侧证据 | 三家客户分批按 owner/finance/PM/member 真实办理约定周期；三模板分别验收（固定价验收/确认工时/确认期间）；完整月度核对、决策留痕、客户签收与限制 | 代理不可代签；无客户即 blocked；签收≠发布授权 |

单切片：客户执行、三模板证据与签收是同一交付行为，不拆模型/服务/UI PR；本包不新增产品页面或命令。

### 19.2 契约锚点与接口

| 项 | schema/命令/状态/权限 |
| --- | --- |
| 证据 | `entities.schema.json#/$defs/DeliveryReceipt`（`acceptance_ref`/`received_on`/`received_by_member_id`；`status` = `draft/active/posted/reversed/superseded`）、`#/$defs/Evidence`；签收人必须是客户授权业务负责人。 |
| 接口 | 不新增业务命令；复用已验收的工作台/合同/工时/结算/导出；客户证据文档路径（`docs/v1.0-development/customer-uat-*.md`）由主控命名后才能创建。 |
| 入口 | 真实浏览器工作台/项目/合同/工时/结算/现金/报表/导出；1440/390、键盘/焦点、空/错/加载/不可用。 |

权限：客户业务负责人签收；财务核对由客户 finance；PM/员工按项目/本人范围；支持人员不能代签；代理不得填客户角色；敏感工资/费率不对客户无关角色展示。
来源唯一：`customer + uat_id + candidate_sha + period` 唯一；同一客户/周期重复签收返回 existing 或新版本并 `supersedes`；签收后候选变化必须做差异验收，不把旧签收改写成新版本。
锁序：客户周期按自然月/约定周期串行；月度关账后不补写历史，开放期调整；签收前锁定证据版本；不得用代理批量生成“客户操作”。
状态：`DeliveryReceipt draft→active→posted→reversed|superseded`；未实际使用/缺证据/仅代理操作即 blocked；签收不等于发布授权。

### 19.3 执行文件与接线

| 现有/新建文件 | 接线 |
| --- | --- |
| 现有 `docs/verification.md`；新建 `docs/v1.0-development/customer-uat-<客户代号>.md`（名称由主控冻结）；客户证据摘要/引用，不新增表 | 客户/周期/签收人由主控与客户责任人；客户原始数据不入仓库；`docs/*` 路径需最终白名单 |

迁移接线（root-only）：无迁移、无业务表；不新增 UAT 通过状态到 `requirement-trace.json`；只引用实际证据。

### 19.4 正反例与逐分预期

| 用例 | 输入 | 预期 |
| --- | --- | --- |
| `POS-S7-CUST-01` | 三家客户分批，按 owner/finance/PM/member 实际办理 | 每家完成约定周期；三模板分别有验收/确认工时/确认期间证据；月度核对可追溯；签收人/日期/限制齐全 |
| `POS-S7-CUST-02` | 固定价客户验收节点 | 只按批准验收确认收入；质保在总价内；结算/开票/收款不再造收入 |
| `POS-S7-CUST-03` | 确认工时客户 | 只按客户确认计费分钟；不超可确认原分钟；不填第二份投入；累计差额 HALF_UP |
| `POS-S7-CUST-04` | 固定服务期客户 | 只按确认期间覆盖；无部分期间规则不按天猜；结算/开票/现金不替代履约 |
| `NEG-S7-CUST-01` | 代理/执行者代替客户签收 | 无效；签收人必须是客户授权业务负责人；记录拒绝 |
| `NEG-S7-CUST-02` | 只有一家客户/一个模板/一个周期 | 未完成三家分批与全 UAT；不得声称客户验收完成 |
| `NEG-S7-CUST-03` | 客户真实数据/工资进入外部代理或源码包 | 拒绝；只用合成/脱敏；违反即停止并报告 |
| `NEG-S7-CUST-04` | 候选变化后沿用旧签收 | 无效；做差异验收/新签收或明确限制 |

### 19.5 固定验收、DoR/DoD、未验证

- 建议验收：`python scripts/projectctl.py check`；`python -m unittest tests.test_projectctl tests.test_governance -v`；18UAT 实际执行记录；真实浏览器 1440/390；客户签收文件/限制。本轮全部 `not_run`。
- DoR：DOR-01～09；额外要求三家客户/角色/周期/模板、客户授权签收人、数据脱敏边界、全 UAT 脚本已冻结。DoD：DOD-01～09；额外要求三模板分别证据、完整月度核对、决策留痕、客户签收/限制；代理不可代签。
- 未验证外部门：三家真实客户、客户业务负责人、连续周期、客户环境/数据、客户签收；本文件不声称任何客户 UAT 已执行。

## 20. WP-FINAL-RELEASE · 全部需求证据核对与正式发行收口

`S7` · 无直接需求主 owner；承担 UAT-12/14/16 集成 owner 与 137 需求/18UAT 最终证据收口 · 依赖 `WP-CUSTOMER-UAT` · 合同 C12 · 风险 S。
交付：137 Must 及 18UAT 证据汇总；已批准 release-readiness 与业务接受；授权发行回执/报告/下一维护入口。

### 20.1 切片（可交付行为）

| 切片 | 需求归属 | 可验收行为 | 明确边界 |
| --- | --- | --- | --- |
| `PHC-S7-FIN-1` | 无直接需求；UAT-12/14/16 集成 owner + 137/18UAT 最终证据 | 只读汇总 137 Must、18UAT、9 门与 `ReleaseManifest`；逐项引用唯一证据；生成收口报告 | 只核对，不改门、不打标签、不发布；授权发行是独立动作 |

单切片：证据核对与收口报告是同一交付行为；本包无新增页面、命令或表。

### 20.2 契约锚点与接口

| 项 | schema/命令/状态/权限 |
| --- | --- |
| 证据 | `entities.schema.json#/$defs/ReleaseManifest`（`required_evidence_ids`/`artifact_sha256`/`source_commit`/`version_label`）、`#/$defs/DeliveryReceipt`、`#/$defs/Evidence`；状态枚举同实体指针。 |
| 接口 | 现有 `scripts/release_check.py`（9 门只读）、`scripts/verify-source-package.py`；只读汇总报告；不新增发布命令。 |
| 入口 | 无新增页面；业务接受读 `WP-CUSTOMER-UAT` 证据。 |

权限：仅主控/授权发布责任人可收口；客户签收不能由代理；发布/部署/打标签需单独授权；不得用本包修改 `release-readiness.json` 为 passed，除非有对应实际证据与授权。
来源唯一：`candidate_sha + contract_digest + evidence_id + uat_id + requirement_id` 唯一；同候选重复汇总返回 existing；候选变化新建并 `supersedes`，不得复用旧证据冒充新候选。
锁序：冻结候选 → 汇总 137/18UAT/9 门 → 独立核对 → 报告/回执 → 授权发布（独立动作）。核对不修改业务账；发布失败不回写历史证据。
状态：`ReleaseManifest draft→pending→approved|rejected`；任一 Must/UAT/gate 缺证据、`pending`、旧候选、自报 pass 即 blocked；`approved` 仍需授权发行回执。

### 20.3 执行文件与接线

| 现有/新建文件 | 接线 |
| --- | --- |
| 现有 `scripts/release_check.py`（只读）、`scripts/verify-source-package.py`；新建只读汇总报告（路径由主控冻结）；扩展 `tests/test_publish_release.py` 汇总断言 | 版本/标签/发布 workflow/`release-readiness.json` 由主控授权后；`project/reports/*`、`project/handoffs/*`、`docs/verification.md`、`docs/delivery-status.md` 由主控写 |

迁移接线（root-only）：无迁移、无业务表；不新增证据表，不改 `project/release-readiness.json` 为 passed。

### 20.4 正反例与逐分预期

| 用例 | 输入 | 预期 |
| --- | --- | --- |
| `POS-S7-FINAL-01` | 137 Must + 18UAT 均有唯一证据，9 门 passed 且有 evidence | `ReleaseManifest` 完整；候选 SHA/契约摘要/夹具版本一致；可生成授权发行回执 |
| `POS-S7-FINAL-02` | 客户签收 + 发布清单 + 维护入口 | 客户接受与研发候选分开记录；下一维护入口明确；旧门未被删除 |
| `POS-S7-FINAL-03` | 同候选重复核对 | 返回 existing；不重复生成证据/不覆盖失败历史 |
| `NEG-S7-FINAL-01` | 任一 Must/UAT/gate `pending`/缺 evidence | `release_check.py` 失败；不得 approved；不得发布 |
| `NEG-S7-FINAL-02` | 自报 pass/旧候选/过期 CI/伪造审查 | 拒绝；必须实际输出/当前候选/当前 CI |
| `NEG-S7-FINAL-03` | 删除 AI/client 门或把 9 门改成 6 门 | 拒绝；旧门保持，调整须独立规划批准 |
| `NEG-S7-FINAL-04` | 无授权就打标签/部署/发布 | 拒绝；发布/部署是独立授权动作，合入不等于发布 |

### 20.5 固定验收、DoR/DoD、未验证

- 建议验收：`python scripts/projectctl.py check`；`python scripts/release_check.py`；`python -m unittest tests.test_projectctl tests.test_governance tests.test_publish_release -v`；137/18UAT 证据汇总实际核对。本轮全部 `not_run`。
- DoR：DOR-01～09；额外要求 `WP-CUSTOMER-UAT` done、9 门证据与授权路径、候选/契约/夹具绑定、维护入口已冻结。DoD：DOD-01～09；额外要求 137/18UAT/9 门逐项有证据、客户接受、授权回执；缺一项保持 blocked。
- 未验证外部门：正式发布授权、标签/镜像/发布环境、客户业务接受、9 门实际通过；本文件不发布、不部署、不声称 1.0 就绪。

## 21. 唯一需求—PH—证据引用（候选）

下表每个需求只出现一次，主 owner 与 `requirement-trace.json` 的 `primary_package` 一致（已逐条核对）；`producer_ref` 只表示跨包生产者，不改变主 owner。`REQ-EV` 与 `PHC-*` 都是候选命名空间，未写入任何 trace/报告；正式 PH 编号登记见 §22.3。

| 需求 | 主包 | 主交付切片（占位） | 专项用例 | UAT | 唯一证据引用 | 跨包 producer_ref |
| --- | --- | --- | --- | --- | --- | --- |
| `V1-ORG-01` | `WP-ONBOARDING` | `PHC-S6-ONB-1` | `SPEC-V1-ORG-01` | UAT-01 | `REQ-EV:V1-ORG-01@PHC-S6-ONB-1` | `WP-OPENING` 提供开账范围 |
| `V1-ONB-01` | `WP-ONBOARDING` | `PHC-S6-ONB-2` | `SPEC-V1-ONB-01` | UAT-01 | `REQ-EV:V1-ONB-01@PHC-S6-ONB-2` | `WP-OPENING` 提供期初 |
| `V1-ONB-02` | `WP-OPENING` | `PHC-S6-OPEN-1` | `SPEC-V1-ONB-02` | UAT-01、12 | `REQ-EV:V1-ONB-02@PHC-S6-OPEN-1` | `WP-ONBOARDING` 选择从零/存量 |
| `V1-ONB-03` | `WP-OPENING` | `PHC-S6-OPEN-1` | `SPEC-V1-ONB-03` | UAT-12 | `REQ-EV:V1-ONB-03@PHC-S6-OPEN-1` | `WP-IMPORT` 提供明细覆盖 |
| `V1-ONB-04` | `WP-OPENING` | `PHC-S6-OPEN-2` | `SPEC-V1-ONB-04` | UAT-12 | `REQ-EV:V1-ONB-04@PHC-S6-OPEN-2` | 结算/现金/成本/收入域期初事件 |
| `V1-ONB-05` | `WP-IMPORT` | `PHC-S6-IMPORT-1` | `SPEC-V1-ONB-05` | UAT-12 | `REQ-EV:V1-ONB-05@PHC-S6-IMPORT-1` | `WP-OPENING` 提供覆盖范围 |
| `V1-ONB-06` | `WP-IMPORT` | `PHC-S6-IMPORT-2` | `SPEC-V1-ONB-06` | UAT-12 | `REQ-EV:V1-ONB-06@PHC-S6-IMPORT-2` | `WP-CORE` 提供来源/幂等原语 |
| `V1-ONB-07` | `WP-OPENING` | `PHC-S6-OPEN-3` | `SPEC-V1-ONB-07` | UAT-12 | `REQ-EV:V1-ONB-07@PHC-S6-OPEN-3` | `WP-IMPORT` 提供逐行对照 |
| `V1-ONB-08` | `WP-ONBOARDING` | `PHC-S6-ONB-3` | `SPEC-V1-ONB-08` | 无直接 UAT | `REQ-EV:V1-ONB-08@PHC-S6-ONB-3` | `WP-EXPORT` 提供导出能力 |
| `V1-OPS-01` | `WP-INSTALL` | `PHC-S6-INSTALL-1` | `SPEC-V1-OPS-01` | UAT-16 | `REQ-EV:V1-OPS-01@PHC-S6-INSTALL-1` | `WP-TLS` 提供 HTTPS 入口 |
| `V1-OPS-02` | `WP-TLS` | `PHC-S6-TLS-1` | `SPEC-V1-OPS-02` | UAT-16 | `REQ-EV:V1-OPS-02@PHC-S6-TLS-1` | `WP-INSTALL` 提供部署基线 |
| `V1-OPS-03` | `WP-UPGRADE-RESTORE` | `PHC-S6-UPG-1` | `SPEC-V1-OPS-03` | UAT-16 | `REQ-EV:V1-OPS-03@PHC-S6-UPG-1` | `WP-OPENING`/`WP-ONBOARDING` 提供新旧边界 |
| `V1-OPS-04` | `WP-UPGRADE-RESTORE` | `PHC-S6-UPG-2` | `SPEC-V1-OPS-04` | UAT-16 | `REQ-EV:V1-OPS-04@PHC-S6-UPG-2` | `WP-INSTALL` 提供备份/恢复脚本 |
| `V1-OPS-05` | `WP-INSTALL` | `PHC-S6-INSTALL-2` | `SPEC-V1-OPS-05` | 无直接 UAT | `REQ-EV:V1-OPS-05@PHC-S6-INSTALL-2` | `WP-PERFORMANCE` 提供容量证据 |
| `V1-OPS-06` | `WP-PG-CONCURRENCY` | `PHC-S6-PG-1` | `SPEC-V1-OPS-06` | UAT-14 | `REQ-EV:V1-OPS-06@PHC-S6-PG-1` | `WP-CLOSE`/`WP-OPENING`/`WP-REFUND`/`WP-SHARED` 提供竞争域 |
| `V1-OPS-07` | `WP-SUPPORT` | `PHC-S6-SUP-1` | `SPEC-V1-OPS-07` | 无直接 UAT | `REQ-EV:V1-OPS-07@PHC-S6-SUP-1` | `WP-EXPORT`/`WP-UPGRADE-RESTORE` |
| `V1-OPS-08` | `WP-RELEASE-PACKAGE` | `PHC-S7-REL-1` | `SPEC-V1-OPS-08` | 无直接 UAT | `REQ-EV:V1-OPS-08@PHC-S7-REL-1` | `WP-FINAL-RELEASE` 汇总 |

### 21.1 UAT 集成 owner 与证据

| UAT | 集成 owner（trace） | 候选证据 | 生产包 |
| --- | --- | --- | --- |
| `UAT-01` | `WP-CUSTOMER-UAT` | `UAT-EV:UAT-01@PHC-S7-CUST-1` | `WP-ONBOARDING`、`WP-OPENING`、`WP-IMPORT` |
| `UAT-12` | `WP-FINAL-RELEASE` | `UAT-EV:UAT-12@PHC-S7-FIN-1` | `WP-OPENING`、`WP-IMPORT`、结算/现金 |
| `UAT-14` | `WP-FINAL-RELEASE` | `UAT-EV:UAT-14@PHC-S7-FIN-1` | `WP-PG-CONCURRENCY` |
| `UAT-16` | `WP-FINAL-RELEASE` | `UAT-EV:UAT-16@PHC-S7-FIN-1` | `WP-UPGRADE-RESTORE`、`WP-INSTALL`、`WP-TLS` |
| 全量 `18UAT` | `WP-CUSTOMER-UAT` / `WP-FINAL-RELEASE` | `UAT-EV:<uat_id>@PHC-S7-CUST-1` 或 `@PHC-S7-FIN-1` | 各生产包；不得由一个 UAT 代签其他 UAT |

三模板在客户证据中必须分别出现：`fixed_milestone` 对应 `UAT-03`（验收）；`confirmed_time` 对应 `UAT-05`（客户确认计费分钟）；`fixed_service_period` 对应 `SPEC-SERVICE-REVENUE`（确认期间覆盖，**无直接 UAT**）；`UAT-18` 只证明年度预付服务耗用，不能替代固定服务期收入确认。结算/开票/收款证据不得作为收入确认证据。`V1-ONB-08`、`V1-OPS-05`、`V1-OPS-07`、`V1-OPS-08` 无直接 UAT，只能按其专项用例单独验收，不能贴 UAT 编号“全覆盖”。

## 22. 覆盖边界与未验证清单

### 22.1 本文件覆盖

- 12/62 包：S6 的 9 包 + S7 的 3 包。
- 21 个可执行候选切片 + 1 个白名单未决切片（`PHC-S6-SUP-2`），按可验收行为合并，不再每包固定四片。
- 17/137 需求：`V1-ORG-01`、`V1-ONB-01`～`08`、`V1-OPS-01`～`08`；每个需求一个主 owner、一个主交付切片、一个专项用例、一个证据引用。
- 4 条直接 UAT：`UAT-01`、`UAT-12`、`UAT-14`、`UAT-16`；S7 另负责 18UAT 全量证据，但本文件未执行。
- 旧 PH-001～020、PH-120～122 只做承接/边界记录；不改状态、不删门、不假 done。

### 22.2 明确未覆盖/未验证

- 其余 50 包（S0–S5）不在本子范围；本文件不写其 spec、不声称其 DoR/映射完整。
- **PHC 仍是占位符**：22 个候选切片（21 可执行 + 1 blocked）均未在 `project/tasks.json` 登记，无真实编号、无生效 `allowed_paths`、无生效 acceptance、无依赖状态；`proposed` 只是候选描述，不是任务状态。
- 未运行 `python scripts/projectctl.py check`；未运行 `python -m unittest tests.test_projectctl tests.test_governance -v`；两条权威验收均 `not_run`。
- 未运行任何产品测试、schema 校核、夹具校核、PG 多连接、浏览器、TLS、安装、升级/恢复、性能或发布检查。
- 未修改 tasks/manifest/trace，未创建 PR，未提交报告/交接，未打标签/发布/部署。
- 未验证：真实客户/工资/数据、真实 Linux/PG 目标、公网域名/证书、非空附件恢复、RPO/RTO、10 并发/P95、三家客户签收、9 项 release gates。
- 未验证：`docs/adr/0002-domain-plugin-sdk.md`、`core/plugin_sdk/`、`pmo_plugins/example_domain/` 是否落盘；不由本文件判断 PH-001 解除。
- 未验证：夹具 `contract_baseline=573ac46` 与本文 BASE `f1115cf` 的差异是否影响引用场景；只按文件哈希绑定，未声明业务等价。
- 未验证（目录/文件不存在）：`tests/deployment/`、`pmo_plugins/onboarding/`、`tests/test_v1_onboarding.py`、`tests/test_v1_workspace.py`、`tests/test_v1_concurrency.py`、`tests/test_v1_integrations.py`、`scripts/install.sh`、`scripts/check-deployment.sh`、`scripts/upgrade.sh`、`scripts/restore.sh`。

### 22.3 正式登记所需输入（主控最终规划执行，本文不代做）

下表是**登记输入草案**，供主控把每个切片写入可信 BASE；在写入前，任何切片都不得开工。白名单为最小建议，必须与包级上限（§8）一致；新增“新建”文件需显式列出。

| 占位符 | 包 | 主需求 | 拟执行者白名单（最小） | 拟固定验收（摘要） | 依赖/状态 |
| --- | --- | --- | --- | --- | --- |
| `PHC-S6-IMPORT-1` | WP-IMPORT | ONB-05 | `pmo_plugins/onboarding/{imports,models}.py`、`.../templates/onboarding/import_preview.html`、`tests/test_imports_ai.py`、`tests/test_v1_onboarding.py` | `manage.py test tests.test_imports_ai tests.test_v1_onboarding -v`；契约/夹具校核 | 依赖 WP-RECONCILE/PROJECT-CLOSE；proposed |
| `PHC-S6-IMPORT-2` | WP-IMPORT | ONB-06 | `pmo_plugins/onboarding/{imports,services}.py`、`.../import_result.html`、`tests/test_v1_onboarding.py` | 同上 + 原子回滚/重排幂等 | 串行于 IMPORT-1；proposed |
| `PHC-S6-OPEN-1` | WP-OPENING | ONB-02、03 | `pmo_plugins/onboarding/{models,opening}.py`、`.../opening_*.html`、`tests/test_v1_onboarding.py` | `manage.py test tests.test_v1_onboarding -v`；UAT-12 草稿段 | 依赖 WP-IMPORT；proposed |
| `PHC-S6-OPEN-2` | WP-OPENING | ONB-04 | `pmo_plugins/onboarding/{opening,services,models}.py`、`tests/test_v1_onboarding.py` | 同上 + 覆盖互斥/事件/锁序 | 串行于 OPEN-1；proposed |
| `PHC-S6-OPEN-3` | WP-OPENING | ONB-07 | `pmo_plugins/onboarding/{services}.py`、`.../opening_report.html`、`tests/test_v1_onboarding.py` | 同上 + UAT-12 全链（真实 PG） | 串行于 OPEN-2；proposed |
| `PHC-S6-ONB-1` | WP-ONBOARDING | ORG-01 | `pmo_plugins/onboarding/{models,services,wizard}.py`、`.../step_*.html`、`tests/test_v1_onboarding.py` | `manage.py test tests.test_v1_onboarding -v` | 依赖 WP-OPENING/UI-ACCEPTANCE；proposed |
| `PHC-S6-ONB-2` | WP-ONBOARDING | ONB-01 | `pmo_plugins/onboarding/{wizard,services}.py`、`.../step_*.html`、`tests/test_v1_onboarding.py` | 同上 + 一合同一项目/三模板 | 串行于 ONB-1；proposed |
| `PHC-S6-ONB-3` | WP-ONBOARDING | ONB-08 | `pmo_plugins/onboarding/templates/onboarding/manual.html`、`tests/test_v1_workspace.py`（新建） | 同上 + SPEC-V1-ONB-08 专项 | 串行于 ONB-2；proposed |
| `PHC-S6-INSTALL-1` | WP-INSTALL | OPS-01 | `scripts/install.sh`、`scripts/check-deployment.sh`、`compose.yaml`、`Dockerfile`、`Caddyfile`、`tests/deployment/test_install.py` | `bash scripts/check-deployment.sh`；`tests/deployment/test_install.py`；`verify_integrity --json` | 依赖 WP-SDK/CORE；proposed |
| `PHC-S6-INSTALL-2` | WP-INSTALL | OPS-05 | `scripts/backup.sh`、健康检查片段、`tests/deployment/test_health.py`、`tests/deployment/test_backup_manifest.py` | `python -m unittest tests.test_backup_script -v`；健康/worker/备份用例 | 串行于 INSTALL-1；proposed |
| `PHC-S6-TLS-1` | WP-TLS | OPS-02 | `Caddyfile`、`compose.yaml`、`Dockerfile`、`scripts/check-deployment.sh`、`tests/deployment/test_tls.py`、`tests/deployment/test_proxy_headers.py` | 真实域名 TLS/登录/附件；错误配置拒绝 | 依赖 WP-INSTALL；proposed |
| `PHC-S6-UPG-1` | WP-UPGRADE-RESTORE | OPS-03 | `scripts/upgrade.sh`（待批）、`tests/deployment/test_upgrade.py`、`docs/v1.0-development/legacy-cutover.md`（只读） | `tests/deployment/test_upgrade.py`；`fixtures/verify_legacy.py --repo "$PWD"` | 依赖 WP-INSTALL/OPENING/ONBOARDING；白名单待批 |
| `PHC-S6-UPG-2` | WP-UPGRADE-RESTORE | OPS-04 | `scripts/backup.sh`、`scripts/restore.sh`（待批）、`tests/deployment/test_restore.py`、`tests/test_backup_script.py` | 真实 PG+非空附件恢复；hash/RPO/RTO | 串行于 UPG-1；白名单待批 |
| `PHC-S6-PG-1` | WP-PG-CONCURRENCY | OPS-06 | `tests/test_v1_concurrency.py`、`tests/test_v1_preparation_pg.py`（只读原语） | `PH135_REQUIRE_PG=1 manage.py test tests.test_v1_concurrency -v` | 依赖 WP-CLOSE/OPENING/REFUND/SHARED；proposed |
| `PHC-S6-PG-2` | WP-PG-CONCURRENCY | 支持 OPS-06 | `tests/test_v1_integrations.py` | `PH135_REQUIRE_PG=1 manage.py test tests.test_v1_integrations -v` | 串行于 PG-1；proposed |
| `PHC-S6-PERF-1` | WP-PERFORMANCE | 支持 OPS-05 | `tests/deployment/test_capacity.py` | 8GB/4 核、基准 50/200/100k、10 并发、P95 证据 | 依赖 ONBOARDING/UI/PG；proposed |
| `PHC-S6-PERF-2` | WP-PERFORMANCE | 支持 OPS-05 | `tests/deployment/test_first_use.py` | 首用/长任务/1440/390 浏览器证据 | 串行于 PERF-1；proposed |
| `PHC-S6-SUP-1` | WP-SUPPORT | OPS-07 | `tests/deployment/test_support_export.py`、`tests/deployment/test_support_revoke.py` | 导出/附件/撤销；越权拒绝 | 依赖 ONBOARDING/UPG；proposed |
| `PHC-S6-SUP-2` | WP-SUPPORT | 支持/退出（无独立需求） | `docs/operations/support-*.md`（待批）、退出 runbook | 文档与演练证据；不得代签 | **blocked**：白名单未冻结，不得开工 |
| `PHC-S7-REL-1` | WP-RELEASE-PACKAGE | OPS-08 | `scripts/{package,verify-source-package,public_hygiene}.py`、`tests/test_publish_release.py`（`release_check.py` 只读） | `python -m unittest tests.test_publish_release -v`；`release_check.py`；同提交 CI | 依赖 S6 + 安全薄片；proposed |
| `PHC-S7-CUST-1` | WP-CUSTOMER-UAT | 集成/验证 | `docs/verification.md`、`docs/v1.0-development/customer-uat-*.md`（待定名） | 18UAT/三模板/签收证据；`projectctl`+治理测试 | 依赖 REL-1 等；proposed |
| `PHC-S7-FIN-1` | WP-FINAL-RELEASE | 集成/验证 | `scripts/verify-source-package.py`、`tests/test_publish_release.py`、收口报告（待定名）（`release_check.py` 只读） | `release_check.py`；137/18UAT/9 门核对 | 依赖 CUST-1；proposed |

**登记时必须同时完成**：

1. 真实 PH 编号、`allowed_paths`、`acceptance`、依赖、risk、状态写入 `project/tasks.json` 与 `project/specs/`；本文不预占编号。
2. 17 条需求/4 条 UAT 的唯一证据引用写入 `requirement-trace.json`/报告契约；不手工维护第二套 running/done。
3. 按当前迁移叶分配 `pmo_plugins/onboarding` 迁移号；确认 `pmo/settings.py`、`pmo/urls.py`、`templates/base.html` 串行接线。
4. 补 `SUP-2`、`UPG-1/2` 脚本、客户证据文档的路径白名单；未补前保持 blocked。
5. 为每个 S 切片安排不同上下文的独立复核；复核不得自评，不得代客户签收。
6. 用真实 BASE/CI 重跑所有“建议验收”；未实际输出的一律保持 `not_run/blocked`。
7. 若实际执行发现需改 core/API 或扩大白名单，先做规划变更，不得在实现 PR 自我授权。

### 22.4 实证外门（不得用文档/schema 代替）

- 真实 PG 多连接与确定性屏障（`PG-1/2`、`UPG-2`、`OPEN-3`）；SQLite 结果一律 `not_run`。
- 授权 Linux/PG 目标与真实 Docker/Compose（`INSTALL-1/2`、`TLS-1`、`UPG-1/2`）。
- 真实域名/证书/续期/公网访问（`TLS-1`）。
- 真实浏览器 1440/390、键盘/焦点、空/错/加载/不可用（`ONB-1/2/3`、`PERF-2`、`CUST-1`）。
- 客户真实操作、周期与签收（`CUST-1`、`FIN-1`）；代理不可代签。
- 9 项发布门与发行授权（`REL-1`、`FIN-1`）；`approved` 不等于已发布。

## 23. 独立 S 级复核清单

- [ ] 只覆盖 S6/S7 12 包；未把 S0–S5 或 62 包整体写成已完成。
- [ ] 切片按可交付行为合并（21 可执行 + 1 blocked），未机械每包四 PR；拆片均列具体文件与接口。
- [ ] 每个需求只出现一次，主包与 trace 一致；其余切片明确“支持/集成/验证”，无多个主 owner。
- [ ] 所有 schema 指针已实际解析（§24.2），含 `~1` 转义；未复制第二套可编辑字段/金额/状态机。
- [ ] 命令/查询/事件/回执均引用既有 55/25/28 目录；未发明 `import.*` 或新 API。
- [ ] 状态、权限、幂等、来源唯一（引用 `fixture.json#/source_consumption_key`）、锁序、事务边界逐片写明；未用 0/当前政策/现金补缺项。
- [ ] 正反例有逐分/逐状态预期；覆盖/重复/越权/锁期/跨租户/旧账保留均有拒绝路径。
- [ ] 旧表/PK/账本/审计/附件保留；旧 PH 只承接不假 done；9 项发布门未删除/降级。
- [ ] PH-001 只核实现状；SDK 字段/兼容/验收/顺序只引用主控唯一候选，未建议改状态。
- [ ] 容量基准明确为起测基线；基准 +1 必须正常接受；8GB/4 核、P95 ≤2s/≤3s 目标与超载重测写明。
- [ ] PH-120～122 的 spec/白名单/验收/门槛未被修改。
- [ ] 每片 DoR/DoD/未验证外部门齐全；所有命令标 `not_run`，无自报 pass。
- [ ] 迁移/共享接线标 root-only；执行者路径未越界；无真实客户/凭据/生产部署。
- [ ] PHC 未被冒充实任务/ready；正式登记输入已列，未声称已登记。

## 24. 第二次修订回执、实际核对与未完成

### 24.1 验收单逐条回执（本执行器负责的 S6–S7）

| # | 原阻断 | 本次处理 | 状态 |
| ---: | --- | --- | --- |
| 1 | 原 99–119 行重复冻结 SDK、建议只改 PH-001 状态 | §5 改为“只核实现状”：删除字段/兼容/不做清单与 `PH-001 → ready` JSON，明确只引用主控唯一 SDK 候选与最终 tasks/spec；§6.1 承接行同步；全文检索无第二套字段表/ready 动作 | 已修（文档层）；SDK 契约本身未验证 |
| 2 | 原 644–655 行把 50/200/10 万写成硬上限，基准 +1 被拒绝 | §3.11 与 §16 改为起测基准：8GB/4 核、列表/详情 P95 ≤2 秒、核心写 P95 ≤3 秒、绑定实际环境；`NEG-S6-PERF-01` 改为基准 +1 **必须正常接受**；超已验证负载重新定容量 | 已修；未实测 |
| 3 | 17 项映射正确但 PHC 仍是占位；正式登记/逐片白名单/实证外门未完成 | 保留 PHC 为占位并显式声明非任务；新增 §22.3 的 21 个可执行 + 1 个 blocked 逐片登记输入（最小白名单、拟验收、依赖）与 §22.4 实证外门；正式编号/白名单/验收仍须主控登记 | 部分完成；登记未做 |
| 4 | 通用要求：不得机械 M/SVC/UI/X 四 PR | 全部改为按可验收行为切片：共 21 个可执行 + 1 个白名单未决；无 UI 的包不单列页面；同文件写入标“串行”；拆分片逐片列文件与接口 | 已修 |
| 5 | 必须实际解析 schema 指针，不得改 canonical schema/fixture | 用 `python3 -c` 只读解析：15 个完整指针 + 10 个真实相对指针全部可解析，0 失败；未写 contracts/fixtures | 已核对 |
| 6 | 夹具已交付，不得整体写“待冻结” | §2.1 绑定 `PH134-SYNTHETIC-20261028`/`1.0` 与 5 个文件完整 SHA256；§9.4 再绑 CSV 场景；缺口另列（`contract_baseline=573ac46` ≠ BASE） | 已绑定 |
| 7 | 不写 tasks/其他文件、不称正式通过 | 仅改本文件；未改 tasks/manifest/trace/检查器/workflow；§24.3 列未完成；不称产品/PH137 通过 | 已遵守 |

### 24.2 实际核对（本轮真实执行，只读）

| 核对 | 命令/方式 | 结果 |
| --- | --- | --- |
| S6/S7 包与依赖 | `python3 -c` 读取 `work-packages.json` | 12 个包，依赖与 §4 表一致；均 `design_candidate`、`dispatch_allowed=false`、`ph_task_ids=[]` |
| 需求主 owner | 同上读取 `requirement-trace.json` | S6/S7 共 17 条；§21 表 17 行，无重复/缺失/多余，`primary_package` 0 处不符 |
| 完整 schema 指针 | 正则提取 `*.schema.json#/$defs/...` 后逐段解析 | 20 个唯一写法 = 15 个具体指针（全部解析，失败 0）+ 5 个 `<entity>`/`<command>`/`<query>`/`<event>`/`<同名命令>` 元变量（非指针） |
| 相对指针 | 提取 `#/$defs/...`，在 8 个 schema 的 `$defs` 中查找 | 10 个真实指针全部可解析；`#/$defs/...` 为文中元变量，非指针 |
| 实体/命令名 | 提取反引号内 CamelCase 与 8 个 schema 的 `$defs` 比对 | 仅 `Caddyfile`/`Dockerfile`/`Decimal`/`Document`/`LedgerEntry`/`S6`/`S7` 不在 schema，均为文件/既有模型/阶段名，非虚构实体 |
| 夹具版本与哈希 | 读取 `fixtures/fixture.json`，与 §2.1 的 5 组哈希比对 | `PH134-SYNTHETIC-20261028`/`1.0`；5/5 哈希一致；`status=preparation_fixture_only`、`contract_baseline=573ac46` 已记录 |
| 切片计数 | 提取 `PHC-S[67]-*` | 22 个占位符 = 21 可执行 + `SUP-2`（blocked） |
| 旧 PH 状态 | 读取 `project/tasks.json` | PH-001/002/121/122 `proposed`，PH-120 `ready`，PH-136 `done`，PH-137 `ready`；本文件未改 |
| PH-001/ADR 事实 | 目录与任务字段 | blocker 原文仍在；`docs/adr/0002-domain-plugin-sdk.md`、`core/plugin_sdk/`、`pmo_plugins/example_domain/` 均不存在 |
| 目录/文件事实 | `ls tests`、`ls scripts`、`ls pmo_plugins` | `tests/deployment/`、`pmo_plugins/onboarding/`、`tests/test_v1_workspace.py`、`scripts/install.sh`、`scripts/check-deployment.sh`、`scripts/upgrade.sh`、`scripts/restore.sh` 不存在；文中已标“新建/待批” |
| 变更范围 | 尝试 `git status --short` | Git 在沙箱不可用（`fatal: not a git repository`）；以本文件为唯一写入，未执行 commit/push/tag |

**未运行**：`python scripts/projectctl.py check`、`python -m unittest tests.test_projectctl tests.test_governance -v`、任何产品测试、schema 校核脚本 `validate_contracts.py`、夹具校核 `check_fixture.py`、PG、浏览器、TLS、安装/升级/恢复、性能或发布命令。以上全部 `not_run`/`unverified`，由主控在无凭据、断网环境执行。

### 24.3 未完成与剩余（不得当作通过）

1. **正式 PH 登记**：21 个切片均无真实编号/生效白名单/生效 acceptance；§22.3 只是登记输入，须主控写入可信 BASE 后才可派工。
2. **白名单缺口**：`SUP-2`（支持/退出 runbook）、`UPG-1/2` 的 `scripts/upgrade.sh`/`scripts/restore.sh`、客户证据文档与收口报告路径均未冻结；未补前保持 blocked。
3. **SDK/安全前置**：PH-001 blocker 未解除；SDK-1/2/3 与依赖安全薄片只按主控候选引用，未验证、未排期。
4. **夹具基线差异**：`contract_baseline=573ac46` 与 BASE `f1115cf` 不同，未证明业务等价；执行时须按实际 BASE 复核。
5. **全部实证外门**：真实 PG/浏览器/TLS/安装/恢复/容量/客户 UAT/9 门/发行授权均未执行（§22.4）。
6. **独立复核**：本文件未经第二上下文 S 级复核；主控须按 §23 清单复核后再整合。
7. **权威验收**：两条命令均未在本执行器运行，不能声称通过。

### 24.4 本次变更

- 变更文件：仅 `docs/v1.0-development/ph137-s6-s7-contract-candidate.md`（本文件，第二次修订）。
- 未写 `project/tasks.json`、`project/manifest.json`、`requirement-trace.json`、contracts/fixtures、检查器、workflow；未创建 PR、未部署、未读取凭据或真实数据、未调用其他 agent。
- 未执行 `git commit`/`push`/`tag`；Git 状态不可用，变更范围以工具写入记录与本文件内容为准。
- 本文件仍是候选：不表示 PH-137 完成，不表示任何 PHC 已登记或 ready，不表示 1.0 可开工。

