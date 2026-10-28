# PH-137 S1–S3 正式薄片契约候选（第二次修订）

状态：仅供主控整合和独立复核的规划候选，不是可信 BASE 任务、开工许可或完成声明。覆盖 `work-packages.json` 的 S1/S2/S3 全部 24 包，按可验收行为合并为 **31 个建议片**；`PHC-*` 均为占位符，不占用真实 PH 编号，当前全部建议 `proposed`。本文未改 tasks/manifest、canonical schema/fixture、产品代码或发布门，未执行产品实现、浏览器、PG、部署或客户验收。

## 1. 唯一引用、夹具与共同完成条件

权威顺序为 BASE `project/tasks.json`、正式 PH spec、PH132 契约、PH134 夹具。需求唯一主 owner 取 `requirement-trace.json#/requirements/*` 的 `primary_package`；每包仅标“主”的一片承接该包全部需求，其余片为支持/集成/验证，不共同主责。证据只取实际 PH report/handoff、PR/CI 和受控回执，当前均未生成。

下列别名只缩短仓库内引用，展开后必须是可解析 JSON Pointer；命令/查询 ID 中的 `/` 按 RFC 6901 写成 `~1`：
- `E`= `docs/v1.0-development/contracts/entities.schema.json`；`D`= `.../write-dtos.schema.json`；`B`= `.../contract-bundle.schema.json`。
- `C`= `.../commands.schema.json`；`CR`= `.../command-results.schema.json`；`Q`= `.../queries.schema.json`；`QR`= `.../query-results.schema.json`。
- `R`= `.../relationships.json#/relationships`；`S`= `.../source-registry.json#/objects`；`F`= `docs/v1.0-development/fixtures/fixture.json`。

字段只沿上述 canonical 定义实现，不复制第二套可编辑字段。实体、客户端 DTO、命令回执和查询结果分别引用，禁止把只读实体当写 DTO。夹具统一绑定 **PH134-SYNTHETIC-20261028 / 1.0**（`F#/fixture_id`、`F#/version`）及 `F#/contract_artifact_sha256` 下逐项记录的实际哈希；场景尚未产品执行，不能写“待冻结”或“已通过”。

共同规则：身份、企业、actor、项目范围由服务端会话/PAT 推导，先授权再查询；制单/业务本人不得自审，PM/成员不得读个人工资费率。草稿零账本、现金、收入、义务副作用。所有浏览器/API/CSV/后台入口共用 owner 服务；同 key 同规范化内容返回原回执，改内容 `idempotency_conflict`。业务来源键固定为企业+用途+原 namespace/kind/stable id+明细+期间+覆盖（`F#/source_consumption_key`），排除快照 version、request/idempotency key 和渠道（`F#/excluded_from_consumption_key`）。适用锁序为 **company→period→project→source→cash_transaction→open_item→allocation**，同类稳定 ID 升序；锁内重查租户/项目/期间/插件/版本，事实、账本、桥、审计、Outbox 原子提交，dry_run/失败全回滚。SQLite 不证明 PG 并发。

每片只写其列出的执行文件。`R0` 为主控单写的 `pmo/urls.py`、`pmo/settings.py`、`templates/base.html`、`core/models.py`、`core/services.py`、`core/management/commands/verify_integrity.py`、`project/tasks.json`、manifest、交付状态，以及各 App 当前叶节点之后的迁移；执行者不预占迁移号、不改已应用迁移。主控完成模型/迁移/URL/settings/nav 接线后，必须按最终合件重验。

每个实现 PH 固定验收 `A0`：
```sh
python scripts/projectctl.py check
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test tests --verbosity 1
python manage.py verify_integrity --json
```
每片另跑列出的定向命令；涉及浏览器另留 1440/390、键盘/焦点、溢出、空/错/加载/不可用证据，涉及财务并发另留真实 PG 双连接证据。共同 DoR=PH137 最终规划合入、前列依赖 done、canonical 版本匹配、精确白名单/反例/验收已进入可信 BASE。共同 DoD=A0 与定向实测通过、独立 S 复核、当前 CI、正常 PR 合入、report/handoff/需求证据齐全；Linux/TLS、数据库+非空附件恢复、真实客户签收和发行授权始终为外门，未做写 unverified。

## 2. PH-001、首批顺序与旧任务承接

PH-001 在 BASE 仍为 `proposed`，blocker 是“SDK字段、类型与兼容规则尚未冻结”，无 report/handoff，不能写 done。本文只引用主控《SDK-1字段与兼容契约候选》和《首个正式薄片：依赖安全修复候选》，不另定义 SDK 字段或 ready 条件。唯一顺序为 **PH137 最终规划合入→P0 依赖安全薄片→PH-001 SDK-1→PH-002 SDK-2→新 SDK-3→WP-CORE**。PH137 合入后仅安全薄片可由其正式任务契约定 ready；安全 done 且 PH-001 最终 spec/白名单/验收合入后，原字段 blocker 才可由主控明确解除，本文不自行放行 PH-001。

PH-006→WP-CONTRACT/CHANGE；007→WP-AR/AP/CREDIT 及后续现金核销；008→后续 S4；009→WP-SHARED；010→WP-LABOR-POLICY/POST；011→WP-TIME-INPUT/REVIEW 与后续资源包；012→后续 S4；013→后续 S6；014→后续通知包；015→WP-REVENUE-*；016→后续读模型。PH017/018 保持独立 AI/真实客户端前置，PH019 保持 PG/恢复/升级外门，PH020 保持发布评审。旧 epic 仅承接、不假 done、不删检查器；AI、资源、agent_client、ai_evaluation、business_acceptance 等发布门只能独立规划变更。PH120–122 契约不在本文修改。

## 3. S1 共享基础

### WP-SDK — 最小 SDK 注册、契约查询与全入口门控（3片）
无直接主需求；owner=sdk。拆分仅因元数据注册、运行门控、迁移重放有不同验收边界，不再另造空 UI 片。

- `PHC-SDK-1`（依赖安全薄片；支持）：严格实现主控 SDK-1 候选，不复制其字段。执行 `core/plugin_sdk/__init__.py`、`metadata.py`、`registry.py`，独立示例 App 的 `__init__.py`、`apps.py`、`plugin.py`、`domain_metadata.py`，`tests/test_plugin_sdk.py`；生产 settings/URL 不接线。接口只取主控候选的 `validate_domain_metadata` / `build_domain_registry`。正反例为主控候选整张固定表，并断言旧 8 字段 manifest、旧 URL/表/账本不变。验收=A0+`python manage.py test tests.test_plugin_sdk tests.test_plugins -v 2`+`verify_integrity`、schema/Python一致及示例升级源码哈希比较。外门：真实插件重放未做。
- `PHC-SDK-2`（依赖 SDK-1；支持）：统一 guard、契约查询和静态命名空间路由，不扩 SDK-1 元数据。执行 `core/plugin_sdk/runtime.py`、`core/plugin_runtime.py`、`core/plugin_views.py`、`tests/test_plugin_sdk.py`；R0 接 `pmo/urls.py`、settings、base nav。身份用 `E#/$defs/CommandContext`，页面/API/内部命令/后台/旧直连同 guard；停用保留历史/队列，依赖缺失拒绝新写。正例启用后允许授权路由；反例停用直连、依赖断裂、同 path 冲突均零业务写入。验收=A0+上述两测试+`tests.test_v1_identity` 全入口用例；真实浏览器未验证。
- `PHC-SDK-3`（依赖 SDK-2；支持）：独立 App 迁移依赖扫描、失败原子回滚和重放。执行 `core/plugin_sdk/migrations.py`、`tests/test_plugin_sdk.py`；R0 仅主控生成实际叶后迁移并接 settings。正例新旧空库迁移/重放一致；缺/循环/跨 App 错依赖、半注册均拒绝且旧表/PK/账本哈希不变。验收=A0+上述两测试+makemigrations check+真实 PG 空库/旧库重放。外门：PG、旧库升级、恢复均未执行。

### WP-CORE — 来源、期间与过账统一基础（2片）
无直接主需求；owner=kernel；依赖 SDK-3。保留旧账本与触发器，不建余额编辑器。

- `PHC-CORE-KERNEL`（主）：字段 `E#/$defs/SourceRef`、`SourceConsumption`、`IdempotencyRecord`、`AccountingEvent`、`LedgerPosting`、`AuditRecord`、`OutboxEvent`、`AccountingPeriod`、`PeriodClose`；命令 `C#/$defs/core.posting.append~1v1`、`core.posting.reverse~1v1`、`period.adjustment.post~1v1`，回执取 `CR` 同 ID；查询 `Q#/$defs/core.source.trace~1v1`、`core.event.get~1v1`、`period.precheck.get~1v1`；关系 `R#/source-event`、`event-reversal`、`period-event-snapshot`。执行 `core/plugin_sdk/sources.py`、`posting.py`、`periods.py`、`tests/test_v1_kernel.py`，R0 接 core models/services/integrity 与下一迁移。AccountingPeriod.status 只用 `draft/active/posted/reversed/superseded`；业务锁定由 PeriodClose/closed_through/precheck 表达，不发明 open/closed。事件 posted→reversed 只追加。正例10000分追加、-10000严格冲销且原行不变；重复来源换版本/request key、未来实际日、锁期直写、半事务均拒绝且零新账。验收=A0+`python manage.py test tests.test_v1_kernel -v 2`+PG 双连接锁期竞争；PG/旧账逐分与哈希链未验证。
- `PHC-CORE-ADAPTERS`（依赖 KERNEL；支持）：浏览器/API/CSV/后台旧入口适配同一来源服务。执行 `core/plugin_sdk/adapters.py`、`tests/test_v1_kernel.py`，R0 仅 `core/services.py` 和 verify 接线。正例同来源三入口只一条消费；反例改金额同 key 返回 `idempotency_conflict`，绕过服务直写由完整性检查拒绝。验收=A0+kernel 测试+旧 API/CSV 回归及旧 LedgerEntry 符号/ origin=legacy 断言；真实旧库升级和全入口浏览器未验证。

### WP-IDENTITY — 账号生命周期、授权与敏感字段（2片）
主需求 `V1-ORG-02/03/05/06/07/08`；依赖 CORE-ADAPTERS。拆分因账号生命周期与全入口字段授权可分别验收。

- `PHC-IDENTITY-LIFECYCLE`（主）：字段 `E#/$defs/Membership`、`MembershipScope`、`AgentGrant`、`CommandContext`；Membership 无 DTO，只能经 `C#/$defs/identity.membership.change~1v1`、`identity.agent_grant.revoke~1v1` 及 `CR` 同 ID 改变，查询 `Q#/$defs/identity.context.get~1v1`。执行 `core/access.py`、`core/agent_auth.py`、`tests/test_v1_identity.py`；R0 接现有 views/services，不新增 PAT 审批/支付/锁账工具。`Membership.active` 保持布尔值，role 只取 `owner/finance/pm/member/auditor`，不得写 active/inactive/suspended 状态；一次性激活/恢复凭据 pending→consumed/expired，撤销立即生效。来源键=企业+membership/grant 原 stable id+有效期覆盖，版本/request key 不重置；锁 company→membership→grant。正例激活一次、撤权后旧会话/PAT/后台写拒绝；过期/重复使用/账号枚举、自授敏感权限、移除最后负责人均拒绝且无凭据日志。验收=A0+`python manage.py test tests.test_v1_identity tests.test_agent_api -v 2`+`F#/uat_sequences/14` 角色撤销反例；真实邮件/客户端/浏览器未验证。
- `PHC-IDENTITY-ENFORCEMENT`（依赖 LIFECYCLE；支持）：字段 `E#/$defs/MembershipScope`、`SensitiveReadAudit`，查询 `Q#/$defs/identity.permissions.get~1v1` 与 `QR` 同 ID，关系 `R#/member-project-scope`。执行 `core/access.py`、`core/agent_auth.py`、`tests/test_v1_identity.py`，R0 接 core services 的页面/API/CSV/导出/附件权限。锁 company→membership→scope→sensitive audit，所有调用重授权。正例同账号多角色按授权项目访问且不能规避自审；跨企业/项目读、写、导出、附件按隐藏策略 `permission_denied` 或 `not_found`，PM/成员个人费率字段裁剪，财务零变化。验收=A0+identity/agent_api/masterdata 测试+`F#/uat_sequences/14` 全入口；真实浏览器和附件权限未验证。

### WP-PEOPLE — 员工、账号绑定与共同字典（1片）
`PHC-PEOPLE-DIRECTORY`（主：`V1-ORG-04`、`V1-MD-02/04`；依赖 IDENTITY-ENFORCEMENT）：实体 `E#/$defs/Employee`、`EmployeeBinding`、`CostCategory`、`NonProjectDestination`，写 DTO 仅 `D#/$defs/Employee`、`CostCategory`、`NonProjectDestination`；EmployeeBinding 无 DTO，由服务派生。命令 `C#/$defs/masterdata.entity.upsert~1v1`、回执 `CR#/$defs/masterdata.entity.upsert~1v1`；查询 `Q#/$defs/masterdata.entity.get~1v1`、`masterdata.lookup.search~1v1`；关系 `R#/membership-employee-binding`；来源 `S#/Employee`、`CostCategory`、`NonProjectDestination`。执行 `core/masterdata.py`、`masterdata_forms.py`、`masterdata_views.py`、`tests/test_masterdata.py`，R0 接 core models/services/下一迁移。Employee/字典 active↔inactive；Binding 只用 `draft/pending/approved/rejected/superseded/closed`，历史不随改绑重写。管理者维护、本人只读自身、代录不取得审批权；唯一键=企业+员工原 PK/字典 code，绑定另含有效期覆盖，锁 company→employee→membership。正例无账号员工可被代录且 recorder 留痕，分钟/分单位明确；同名员工不自动合并，重复有效绑定、跨企业账号、停用日新事实、解绑绕审拒绝。验收=A0+`python manage.py test tests.test_masterdata tests.test_v1_identity -v 2`+`F#/uat_sequences/0` 与 `F#/uat_sequences/14`；真实浏览器、旧绑定迁移未验证。

### WP-PARTY — 统一往来方、联系人和去重（1片）
`PHC-PARTY-REGISTRY`（主：`V1-MD-03/06/07`；依赖 CORE-ADAPTERS）：`E#/$defs/Counterparty`、`Contact`、`MergeMap` 与 `D` 同名 DTO；命令 `C#/$defs/masterdata.entity.upsert~1v1`、`masterdata.entity.merge~1v1` 及 `CR` 同 ID；关系 `R#/counterparty-role`、`masterdata-merge`；来源 `S#/Counterparty`、`Contact`、`MergeMap`。执行 `pmo_plugins/contracts/models.py`、`party_services.py`、`party_forms.py`、`party_views.py`、`templates/contracts/party_list.html`、`party_detail.html`，以及 `core/masterdata.py`、`masterdata_forms.py`、`masterdata_views.py`、`tests/test_masterdata.py`、`tests/test_v1_contracts.py`；R0 接 contracts 下一迁移和 root URL/nav。Counterparty active/inactive/merged，Contact active/inactive，MergeMap 仅 effective；合并不删源 PK、不同名不自动合并。管理者维护，快捷新增不放大权限；唯一键=企业+原 entity 类型+原 PK，合并键另含 source→target，锁 company→source/target ID 升序。正例同一档案兼具客户/供应商且合同快捷新增只一笔；重复/环/跨企业合并、无权快捷新增、停用后新事实拒绝。验收=A0+`python manage.py test tests.test_masterdata tests.test_v1_contracts -v 2`+`F#/uat_sequences/1` 稳定 ID 穿透；旧引用全量迁移、真实浏览器未验证。

## 4. S2 业务来源

### WP-PROJECT — 项目主档、成员与基础状态（1片）
`PHC-PROJECT-REGISTRY`（主：`V1-MD-01`、`V1-PRO-01/02/06/07`；依赖 PEOPLE-DIRECTORY、PARTY-REGISTRY）：`E#/$defs/Project`、`MembershipScope` 与 `D#/$defs/Project`；命令 `C#/$defs/masterdata.entity.upsert~1v1`，查询 `Q#/$defs/masterdata.entity.get~1v1`、`identity.permissions.get~1v1`；关系 `R#/member-project-scope`。执行 `core/masterdata.py`、`masterdata_forms.py`、`masterdata_views.py`、`tests/test_masterdata.py`，`pmo_plugins/delivery/project_services.py`、`project_views.py`、`templates/delivery/project_detail.html`、`tests/test_v1_delivery.py`；R0 接 core/delivery 模型迁移和 root URL/nav。Project.status 仅 `active/on_hold/cancelled/closed`，delivery_status 与 financial_archive_status 各用 canonical enum；交付关闭不清财务未清。管理者/授权 PM 维护，成员范围只引用 identity；唯一键=企业+project code/原 PK，锁 company→project→membership scope。正例不要求任务树即可建项，移出成员后历史可查；跨企业成员、暂停后普通新投入、重复 code、伪造角色拒绝，合法实际收付/纠错仍可走所属服务。验收=A0+`python manage.py test tests.test_masterdata tests.test_v1_delivery -v 2`+`F#/uat_sequences/1`、`F#/uat_sequences/9`；旧 Project 财务字段只读迁移、浏览器未验证。

### WP-DELIVERY — 交付节点、履约证据与风险（2片）
主需求 `V1-PRO-03/04/05`；依赖 PROJECT-REGISTRY。拆分因普通交付办理与可计费履约确认具有不同审批/来源边界。

- `PHC-DELIVERY-WORK`（主）：`E#/$defs/DeliveryMilestone`、`WorkItem`、`Evidence`、`DeliveryClose` 与 `D` 中仅 DeliveryMilestone/WorkItem/Evidence；DeliveryClose 是只读结果，无 DTO/直写命令。命令 `C#/$defs/delivery.evidence.attach~1v1` 及 `CR` 同 ID；关系 `R#/evidence-entity`。执行 `pmo_plugins/delivery/models.py`、`services.py`、`forms.py`、`views.py`、`templates/delivery/work_list.html`、`work_detail.html`、`tests/test_v1_delivery.py`；R0 接下一迁移/root URL。Milestone open/accepted/cancelled，WorkItem todo/doing/blocked/done/cancelled；授权 PM 维护，风险关闭留原因。唯一键=企业+项目+原 Task/PK，锁 company→project→work item→evidence。正例跨项目/循环依赖拒绝且历史 Task 保留；日期到期、任务完成、DeliveryClose 均新增收入/应收=0，未批风险变更不增合同额。验收=A0+`python manage.py test tests.test_v1_delivery -v 2`+旧 Task/CashPlan 兼容；非空附件、真实浏览器未验证。
- `PHC-DELIVERY-PERFORMANCE`（依赖 WORK、CONTRACT-APPROVAL；支持）：`E#/$defs/Acceptance`、`ServiceConfirmation`，DTO `D#/$defs/AcceptanceDraft`、`ServiceConfirmationDraft`；命令 `C#/$defs/delivery.acceptance.draft.save~1v1`、`delivery.acceptance.confirm~1v1`、`delivery.service_confirmation.draft.save~1v1` 及对应 `CR`；查询 `Q#/$defs/delivery.performance.available~1v1`；关系 `R#/service-confirmation-contract-line`。执行 `pmo_plugins/delivery/performance_services.py`、`performance_forms.py`、`performance_views.py`、`templates/delivery/performance_confirm.html`、`tests/test_v1_delivery.py`；R0 接 root services/URL。两者均 draft→confirmed→reversed，仅追加更正；客户/授权 PM 确认且制单不自审。消费键=企业+合同明细+原履约 stable id+节点/服务覆盖，锁 company→period→project→source。正例固定价验收可供收入引用；重复/重叠覆盖、未批合同、未来确认、跨项目证据拒绝且零收入/结算。验收=A0+delivery/contracts 测试+`F#/uat_sequences/2`。**契约缺口**：`core.document.decide/v1` 的 kind 不含 service_confirmation，当前无服务期确认命令；须独立 additive PH132 契约变更并合入后才能完成该片，不得伪造 status 或复用 acceptance 命令。真实客户确认、浏览器未验证。

### WP-CONTRACT — 合同、收费明细与生效方法（1片）
`PHC-CONTRACT-APPROVAL`（主：`V1-CON-01/02/03/04/05/07`；依赖 PROJECT-REGISTRY、PARTY-REGISTRY）：实体 `E#/$defs/Contract`、`ContractVersion`、`ContractLine`、`PaymentTerm`、`WarrantyTerm`、`MethodPolicySelection`，DTO `D#/$defs/ContractDraft`、`ContractLine`、`PaymentTerm`、`WarrantyTerm`、`MethodPolicySelection`；价格只引 `B#/$defs/PricingConfiguration`。命令 `C#/$defs/contracts.contract.draft.save~1v1`、`contracts.version.submit~1v1`、`contracts.version.approve~1v1` 及 `CR` 同 ID；查询 `Q#/$defs/contracts.effective_line.get~1v1`、`contracts.project_summary.get~1v1`；关系 `R#/contract-line-policy`、`contract-line-payment-term`。执行 `pmo_plugins/contracts/models.py`、`services.py`、`forms.py`、`views.py`、`templates/contracts/contract_form.html`、`contract_detail.html`、`tests/test_v1_contracts.py`；R0 接下一迁移/root URL/nav，旧 Project 合同字段只读。Contract/Version/Line/Method 状态严格取 E enum；一合同一项目，业务制单与另一人批准，三模板分别验收。唯一键=企业+合同+version_no+line_no，锁 company→project→contract/version；方法/价格须批准，禁止第二编辑方法字段。正例批准净额100000、税额6000、价税106000，批准本身收入/现金=0；跨项目/对手/币种、自审、明细分配超总价、同服务双方法、缺价格或未知税额猜值拒绝。验收=A0+`python manage.py test tests.test_v1_contracts -v 2`+三模板 `F#/contracts` 与 `F#/confirmed_contract_measurement_policy`；真实客户税率/价格、PG 修订竞争、浏览器未验证。

### WP-CHANGE — 合同变化、终止和关联视图（1片）
`PHC-CONTRACT-CHANGE`（主：`V1-CON-06/08`；依赖 CONTRACT-APPROVAL、DELIVERY-WORK）：`E#/$defs/ContractChange`、`ContractVersion`、`MethodPolicySelection`，DTO `D#/$defs/ContractChangeDraft`；命令 `C#/$defs/contracts.contract_change.draft.save~1v1`、`contracts.change.decide~1v1` 及 `CR` 同 ID；查询 `Q#/$defs/contracts.project_summary.get~1v1`。执行 `pmo_plugins/contracts/change_services.py`、`change_forms.py`、`change_views.py`、`templates/contracts/change_form.html`、`change_detail.html`、`tests/test_v1_contracts.py`；R0 接 models/下一迁移/root URL。Change 仅 `draft/pending_customer/approved/rejected/withdrawn`，批准变化使旧 Version superseded 而不改历史。业务申请、独立批准；消费键=企业+合同+change 原 stable id+覆盖区间，锁 company→project→contract/version→source。正例 `F#/uat_sequences/1`：草稿+10000不改批准100000，独立批准+20000后 net120000/tax7200/gross127200，收入/现金=0；未批变化、终止低于累计消费、重复覆盖、跨项目增减价拒绝，结算/开票/收款不代替应收或收入。验收=A0+contracts/delivery 测试+PG 过期版本竞争；跨域累计金额、真实浏览器未验证。

### WP-TIME-INPUT — 项目/非项目投入与周填报（1片）
`PHC-TIME-CAPTURE`（主：`V1-TIM-01/02/03/04`；依赖 PEOPLE-DIRECTORY、PROJECT-REGISTRY、DELIVERY-WORK）：`E#/$defs/Timesheet`、`NonProjectWork`、`WorkforceCoverage`；写 DTO 仅 `D#/$defs/TimesheetDraft`、`WorkforceCoverage`，NonProjectWork 由 Timesheet 的 nonproject 目标派生，不能直写。命令 `C#/$defs/time.timesheet.draft.save~1v1`、`time.timesheet.submit~1v1` 及 `CR` 同 ID；查询 `Q#/$defs/masterdata.lookup.search~1v1`；关系 `R#/vendor-workforce-exclusion`。执行 `pmo_plugins/time/models.py`、`services.py`、`forms.py`、`views.py`、`adapters.py`、`templates/time/timesheet_form.html`、`timesheet_week.html`、`tests/test_v1_time.py`；R0 接下一迁移/root URL，旧 CSV/API/task_id 只走适配层。Timesheet 本片仅 draft→submitted，整数分钟、项目/非项目互斥；本人或留痕代录，PM 不越项目。唯一键=企业+员工+原外部 ID/原 PK+工作覆盖，锁 company→period→project→timesheet。正例项目60+非项目30分钟提交，未填不伪造0；bool/float 分钟、跨项目日总量超限、未来日期、锁期、停用员工、供应商工时套员工路径均拒绝且零成本。验收=A0+`python manage.py test tests.test_v1_time -v 2`+旧 CSV/API 等价及 `F#/uat_sequences/3`；真实浏览器、PG 重复提交未验证。

### WP-TIME-REVIEW — 投入审批与缺项处理（1片）
`PHC-TIME-APPROVAL`（主：`V1-TIM-05/06/08`；依赖 TIME-CAPTURE）：`E#/$defs/Timesheet`、`SourceConsumption`、`AccountingEvent`；命令 `C#/$defs/time.timesheet.submit~1v1`、`core.document.decide~1v1`（kind=timesheet）及对应 `CR`；查询 `Q#/$defs/time.approved.list~1v1` 与 `QR` 同 ID。执行 `pmo_plugins/time/review_services.py`、`review_views.py`、`templates/time/review_list.html`、`review_detail.html`、`tests/test_v1_time.py`；R0 接 root services/URL。状态 submitted→approved/rejected；approved→posted/reversed 仅后续服务追加，编辑生成新草稿并使旧审批失效。PM 审授权项目、员工不得自审；审批消费键=企业+timesheet 原 PK+用途+submitted 覆盖，版本/request key 不重置，锁 company→period→project→timesheet。正例60分钟批准可查，缺费率仍 approved 且 cost_state=unpriced、缺项非0；双审批只一笔，退回不计成本，改绑/换版本/自审/锁期改原行拒绝。验收=A0+time/masterdata 测试+页面/导出/API 分钟合计一致+PG 双审批；真实 PG、导出权限和浏览器未验证。

### WP-BILLABLE — 客户可计费投入确认（1片）
`PHC-BILLABLE-CONFIRM`（主：`V1-TIM-07`；依赖 TIME-APPROVAL、CONTRACT-APPROVAL）：`E#/$defs/BillableConfirmation` 与 `D` 同名 DTO；命令 `C#/$defs/time.billable.confirm~1v1` 及 `CR` 同 ID；查询 `Q#/$defs/time.approved.list~1v1`、`contracts.effective_line.get~1v1`；关系 `R#/timesheet-billable-confirmation`。执行 `pmo_plugins/time/billable_services.py`、`billable_forms.py`、`billable_views.py`、`templates/time/billable_confirm.html`、`tests/test_v1_time.py`、`tests/test_v1_contracts.py`；R0 接下一迁移/root URL。状态 draft→confirmed→reversed；客户确认授权人与制单分离，员工不能自报售价。消费键=企业+timesheet 原 PK+contract_line+分钟覆盖，版本变化不重置累计，锁 company→period→project→source。正例 `F#/uat_sequences/4` 批准600分钟仅确认480，成本分钟仍600且收入/结算在确认时=0；确认601、第二明细重复分钟、未批准工时、跨合同项目均拒绝。验收=A0+time/contracts 测试+PG 累计竞争；真实客户确认、PG、浏览器未验证。

## 5. S3 经营核算

### WP-AR — 客户结算、未清、账龄与质保（2片）
主需求 `V1-SET-01/03/04/09`；依赖 CONTRACT-APPROVAL、DELIVERY-PERFORMANCE、CORE-ADAPTERS。拆分因结算权利生成与账龄投影可分别验收。

- `PHC-AR-SETTLEMENT`（主）：`E#/$defs/Settlement`、`OpenItem`、`PaymentTerm`、`WarrantyTerm`，DTO `D#/$defs/SettlementDraft`、`OpenItem`；命令 `C#/$defs/settlement.settlement.draft.save~1v1`、`settlement.open_item.create~1v1` 及 `CR` 同 ID；查询 `Q#/$defs/settlement.open_item.get~1v1`；来源 `S#/Settlement`、`OpenItem`。执行 `pmo_plugins/settlement/models.py`、`receivable_services.py`、`receivable_forms.py`、`receivable_views.py`、`templates/settlement/receivable_form.html`、`open_item_detail.html`、`tests/test_v1_settlement.py`；R0 接下一迁移/root URL。Settlement/OpenItem 只用 canonical `draft/active/posted/reversed/superseded`；PM 准备、财务独立确认，未达实质条件只留计划。消费键=企业+合同明细+履约/结算覆盖+receivable，共同锁序。正例 `F#/uat_sequences/9` 主90000+质保10000的净额仍100000、价税106000，收入不新增；重复履约、超合同、手改余额/方向、未到期质保算逾期均拒绝。验收=A0+`python manage.py test tests.test_v1_settlement -v 2`+PG 部分结算竞争；后续核销、真实浏览器未验证。
- `PHC-AR-AGING`（依赖 SETTLEMENT 及后续核销接口；支持）：`E#/$defs/AgingCase` 为只读投影，无 DTO/直写命令；查询 `Q#/$defs/settlement.aging.get~1v1` 与 `QR` 同 ID，OpenItem 仍由 `S#/OpenItem` 唯一所有。执行 `pmo_plugins/settlement/aging_services.py`、`aging_views.py`、`templates/settlement/aging_list.html`、`tests/test_v1_settlement.py`；R0 接 root URL。账龄从 OpenItem、剩余核销和原到期日派生，争议保留原到期日并留负责人/下一步/证据；锁 company→open_item→projection。正例部分核销后仅剩余计账龄；直接写 AgingCase、改余额、删除争议证据均拒绝。验收=A0+settlement 测试+`F#/uat_sequences/2` 与 `F#/uat_sequences/9`；后续现金核销和真实浏览器未验证。

### WP-AP — 供应商及员工费用义务接口（1片）
`PHC-AP-OBLIGATION`（主：`V1-SET-02`；依赖 CONTRACT-APPROVAL、PEOPLE-DIRECTORY、CORE-ADAPTERS）：`E#/$defs/Settlement`、`OpenItem`、`CostEvent`、`Employee`，DTO 仅 `D#/$defs/SettlementDraft`、`OpenItem`；命令 `C#/$defs/settlement.open_item.create~1v1` 及 `CR` 同 ID；查询 `Q#/$defs/settlement.open_item.get~1v1`；关系 `R#/source-event`。执行 `pmo_plugins/settlement/payable_services.py`、`payable_views.py`、`templates/settlement/payable_detail.html`、`tests/test_v1_settlement.py`；R0 接下一迁移/root services，costing 只经内部接口调用。状态沿 Settlement/OpenItem enum；暂估→正式只追加差额并引用原成本。财务确认，员工仅见本人非敏感义务；消费键=企业+原 cost source stable id+payable+覆盖，锁 company→period→project→cost source→open_item。正例已发生40000分产生成本/应付各40000，后续付款只减现金/核销；未发生来源、重复应付、Employee 错作 Counterparty、成本成功而义务失败均拒绝/整体回滚。验收=A0+settlement/costing 测试+`F#/uat_sequences/5` 与 `F#/uat_sequences/16` 原子性；真实 PG 跨域回滚、浏览器未验证。

### WP-CREDIT — 折让、减免、坏账与信用更正（1片）
`PHC-CREDIT-ADJUSTMENT`（主：`V1-SET-08`；依赖 AR-SETTLEMENT、AP-OBLIGATION）：`E#/$defs/CreditAdjustment`、`BadDebtDisposition`、`PerformanceSettlementMatch`，DTO `D#/$defs/CreditAdjustmentDraft`、`BadDebtDispositionDraft`；命令 `C#/$defs/settlement.credit_adjustment.draft.save~1v1`、`settlement.bad_debt_disposition.draft.save~1v1`、`core.document.decide~1v1` 及对应 `CR`；查询 `Q#/$defs/settlement.open_item.get~1v1`。执行 `pmo_plugins/settlement/credit_services.py`、`credit_forms.py`、`credit_views.py`、`templates/settlement/credit_form.html`、`credit_detail.html`、`tests/test_v1_settlement.py`；R0 接下一迁移/root URL。两类状态均 `draft/active/posted/reversed/superseded`，adjustment_type 仅 `discount/waiver/return/tax_adjustment`；财务申请、独立批准。消费键=企业+open_item+adjustment 原 stable id+批准覆盖，共同锁序。正例未清6000、折让2000后4000，管理收入只按政策另调，坏账不自动冲交付收入；折让7000、已核销未先撤/更正、重复批准、伪造收款或退款当信用均拒绝且现金不变。验收=A0+settlement/management_revenue 测试+`F#/uat_sequences/10` 发票/信用不造退款；真实核销联动、PG、浏览器未验证。

### WP-LABOR-POLICY — 工资池、有效产能与费率版本（1片）
`PHC-LABOR-POLICY`（主：`V1-CST-01/02/03/04`；依赖 TIME-APPROVAL、CORE-ADAPTERS）：`E#/$defs/SalaryPool`、`RateSnapshot`、`CostPolicy`；DTO 仅 `D#/$defs/SalaryPoolDraft`、`CostPolicy`，RateSnapshot 是服务生成的只读快照，无 DTO/直写命令。输入只引 `B#/$defs/MonthlyLaborPolicyInput`、`MonthlyLaborInputs`；命令 `C#/$defs/costing.salary_pool.draft.save~1v1`、`core.document.decide~1v1`（kind=salary_pool）及 `CR` 同 ID；查询 `Q#/$defs/time.approved.list~1v1`、`costing.source.available~1v1`。执行 `pmo_plugins/costing/models.py`、`labor_policy_services.py`、`labor_policy_forms.py`、`labor_policy_views.py`、`templates/costing/labor_policy.html`、`tests/test_v1_costing.py`；R0 接下一迁移/root URL。SalaryPool 仅 `draft/approved/reconciled/superseded`，RateSnapshot 仅 `estimated/reconciled/superseded`，CostPolicy 仅 `approved/retired`；method/algorithm/rounding_group 只取 B 的 const。仅 owner/finance 与独立复核可见个人池。

原始工资来源唯一键固定 **企业+员工+自然月**，方法、政策、费率快照版本均不得加入该原始来源键；版本只选择不可变输入快照，同源更正另建引用原员工月的差额记录。锁 company→period→employee/month→policy。正例 `F#/facts/labor` 池2400000、D=9600可批准；D 缺失/0、T>D、启用月已有旧人工、PM 读个人池均阻止正式分配并保留工资事实。反例：同员工月仅切换 method/policy/snapshot 后再提交全额2400000，新增公司成本必须为0；合法更正2400000→2400101也只能后续追加101，旧58分绝不重算。验收=A0+`python manage.py test tests.test_v1_costing -v 2`+`F#/uat_sequences/3` capacity/privacy/additional cases；真实薪酬、客户参数、浏览器未验证。

### WP-LABOR-POST — 人工分配、差额和开放期调整（2片）
主需求 `V1-CST-05/06/10`；依赖 LABOR-POLICY。拆分因公司成本确认与月度组分配有不同事务/守恒验收。

- `PHC-LABOR-COST`（主）：`E#/$defs/CostEvent`、`SalaryPool`、`SourceConsumption`，命令 `C#/$defs/costing.cost.post~1v1` 及 `CR` 同 ID；查询 `Q#/$defs/costing.reconciliation.get~1v1`；关系 `R#/source-event`。执行 `pmo_plugins/costing/labor_posting_services.py`、`tests/test_v1_costing.py`；R0 接 costing 下一迁移、core services/integrity。CostEvent 仅 pending/posted/reversed；财务内部过账，工资付款不调用成本服务。原始来源仍为企业+员工+自然月，消费记录另含 cost 用途/期间/覆盖，锁 company→period→source。正例池2400000只确认一次公司成本；换政策/方法/请求key/渠道重复全额新增=0，更正只追加101；缺来源、未来发生日、重复池、工资付款再记成本拒绝。验收=A0+costing/settlement 测试+`F#/uat_sequences/3` S01/S03+PG 双过账；真实 PG/月结未验证。
- `PHC-LABOR-ALLOCATION`（依赖 LABOR-COST；支持）：`E#/$defs/AllocationRun`、`AllocationTarget`、`CostEvent`、`SalaryPool` 与 `D` 中 AllocationRun/Target；命令 `C#/$defs/costing.allocation.run~1v1`、`costing.allocation.reverse~1v1` 及 `CR` 同 ID；查询 `Q#/$defs/costing.reconciliation.get~1v1`；关系 `R#/cost-source-allocation`。执行 `pmo_plugins/costing/labor_allocation_services.py`、`labor_allocation_views.py`、`templates/costing/labor_allocation.html`、`tests/test_v1_costing.py`；R0 接下一迁移/root URL。Run draft/posted/reversed，Target posted/reversed；算法只取 `B#/$defs/MonthlyLaborInputs`，Decimal+HALF_UP+最大余数，敏感明细遮蔽。锁 company→period→source→allocation。正例 C=101、D=60，A三条各1分钟、B=31、未分配=26，逐分为 **A=5、B=52、未分配=44，合计101**，公司成本仍101；旧逐行2/2/2/52=58保留。`F#/uat_sequences/3` 更正后目标150007/150006/30001/2070087，合计2400101。缺分母、T>D、分配再增公司成本、重算冲销均拒绝；冲销取原存储分币。验收=A0+costing 测试+PG 双分配；真实月结、浏览器未验证。

### WP-EXPENSE — 报销费用、发生确认与待定税额（1片）
`PHC-EXPENSE-POST`（主：`V1-SPD-01/04/06`；依赖 AP-OBLIGATION、CORE-ADAPTERS）：`E#/$defs/ExpenseClaim`、`CostEvent`、`TaxQualification`、`Evidence`，DTO `D#/$defs/ExpenseClaimDraft`、`TaxQualification`、`Evidence`；命令 `C#/$defs/costing.expense_claim.draft.save~1v1`、`core.document.decide~1v1`（kind=expense_claim）、`costing.cost.post~1v1` 及对应 `CR`；查询 `Q#/$defs/costing.source.available~1v1`；关系 `R#/source-event`。执行 `pmo_plugins/costing/expense_services.py`、`expense_forms.py`、`expense_views.py`、`templates/costing/expense_form.html`、`expense_detail.html`、`tests/test_v1_costing.py`；R0 接下一迁移/root URL、旧 Document 适配。ExpenseClaim 状态严格取 E enum，TaxQualification 为 pending/confirmed/superseded；本人制单、业务与财务独立复核，未知抵扣含税暂计。消费键=企业+原 Document/claim PK+cost/payable 用途，共同锁序。正例 `F#/uat_sequences/16` 未知税额时成本/应付各10600，后续追加成本-600/税+600，有效成本10000、应付10600、现金0；自审、缺凭据、未来发生日、重复导入、半提交均拒绝/回滚。验收=A0+costing/settlement 测试+PG 原子回滚；非空附件、真实 PG、浏览器未验证。

### WP-SPEND — 外包采购承诺与验收成本（1片）
`PHC-SPEND-ACCEPTANCE`（主：`V1-SPD-02/03`、`V1-CST-12`；依赖 AP-OBLIGATION、CONTRACT-APPROVAL、TIME-CAPTURE）：`E#/$defs/SpendCommitment`、`SpendAcceptance`、`WorkforceCoverage`、`CostEvent`，DTO `D#/$defs/SpendCommitmentDraft`、`SpendAcceptanceDraft`、`WorkforceCoverage`；命令 `C#/$defs/costing.spend_commitment.draft.save~1v1`、`costing.spend_acceptance.draft.save~1v1`、`core.document.decide~1v1`（仅 kind=spend_acceptance）、`costing.cost.post~1v1` 及对应 `CR`；关系 `R#/commitment-consumption`、`vendor-workforce-exclusion`。执行 `pmo_plugins/costing/spend_services.py`、`spend_forms.py`、`spend_views.py`、`templates/costing/spend_detail.html`、`tests/test_v1_costing.py`；R0 接下一迁移/root URL。Commitment/Acceptance 状态严格取 E enum；授权 PM 验收、财务复核，预付款不自动转成本。消费键=企业+commitment/acceptance 原 stable id+验收覆盖，共同锁序。正例 `F#/uat_sequences/5` 承诺100000、验收40000则成本/应付40000、剩余承诺60000；验收100001、重复验收、供应商分钟套员工费率、半账拒绝。**契约缺口**：现有 decide kind 不含 spend_commitment，承诺批准须独立 additive 契约变更；未合入前本片保持 proposed，不得直接改 status。验收=A0+costing/time/settlement 测试+PG 部分验收竞争；真实 PG、浏览器未验证。

### WP-SPEND-CORRECTION — 暂估转正式、退货折让和支出更正（1片）
`PHC-SPEND-CORRECTION`（主：`V1-SPD-05/08`；依赖 EXPENSE-POST、SPEND-ACCEPTANCE、CREDIT-ADJUSTMENT）：`E#/$defs/CostEvent`、`ExpenseClaim`、`SpendAcceptance`、`TaxQualification`、`CreditAdjustment`；命令 `C#/$defs/costing.cost.post~1v1`、`core.posting.reverse~1v1`、`period.adjustment.post~1v1` 及 `CR` 同 ID；查询 `Q#/$defs/costing.reconciliation.get~1v1`、`settlement.open_item.get~1v1`；关系 `R#/source-event`、`commitment-consumption`。执行 `pmo_plugins/costing/correction_services.py`、`correction_forms.py`、`correction_views.py`、`templates/costing/correction_detail.html`、`tests/test_v1_costing.py`；R0 接 root services/URL。状态沿用原实体，只追加 pending/posted/reversed；财务申请独立批准。更正键=企业+原成本来源+更正记录 stable id+类型/覆盖，共同锁序。正例暂估40000、正式35000则追加成本/应付-5000，原40000保留；补发票/付款新增成本=0，重复更正、超原额退货、已核销未先处理、银行退款直接冲成本均拒绝且现金不变。验收=A0+costing/settlement 测试+`F#/uat_sequences/5` invoice-later 与 `F#/uat_sequences/16`；发票/核销联动、PG、浏览器未验证。

### WP-SHARED — 共享费用分配与重分配（1片）
`PHC-SHARED-ALLOCATION`（主：`V1-CST-07/08/09`、`V1-SPD-09`；依赖 EXPENSE-POST、TIME-APPROVAL）：`E#/$defs/AllocationRun`、`AllocationTarget`、`CostEvent` 与 `D` 中 AllocationRun/Target；命令 `C#/$defs/costing.allocation.run~1v1`、`costing.allocation.reverse~1v1` 及 `CR` 同 ID；查询 `Q#/$defs/costing.reconciliation.get~1v1`；关系 `R#/cost-source-allocation`。执行 `pmo_plugins/costing/shared_allocation_services.py`、`shared_allocation_forms.py`、`shared_allocation_views.py`、`templates/costing/shared_allocation.html`、`tests/test_v1_costing.py`；R0 接下一迁移/root URL。Run draft/posted/reversed，Target posted/reversed；敏感财务批准，权重取客户版本，原费用不得重录。来源键=企业+原费用+批准分配 stable id+目标覆盖，共同锁序。正例 `F#/facts/shared` 10000按1/1/1分为3334/3333/3333，整运行冲销后按1/2/1为2500/5000/2500，公司成本始终10000；10001等额分为3334/3334/3333。超来源、零权重偷平摊、部分改历史、未整冲销重分配均拒绝。验收=A0+costing 测试+`F#/uat_sequences/6`+PG 竞争；真实浏览器未验证。

### WP-REVENUE-FIXED — 固定价履约收入和累计调整（1片）
`PHC-REVENUE-FIXED`（主：`V1-REV-04/05/07`；依赖 CONTRACT-APPROVAL、DELIVERY-PERFORMANCE、CORE-ADAPTERS）：`E#/$defs/RevenueRecognition`、`RevenueAdjustment` 与 `D` 同名 DTO，但 recognized/derived total 只能由服务生成；命令 `C#/$defs/management_revenue.recognize~1v1`（performance_ref.kind=acceptance）及 `CR` 同 ID；查询 `Q#/$defs/management_revenue.contract_line.get~1v1`；关系 `R#/performance-settlement-match`、`source-event`。执行 `pmo_plugins/management_revenue/models.py`、`fixed_services.py`、`revenue_forms.py`、`revenue_views.py`、`templates/management_revenue/fixed_detail.html`、`tests/test_v1_management_revenue.py`；R0 接下一迁移/root URL。状态仅 `draft/active/posted/reversed/superseded`；批准合同明细+confirmed acceptance+政策版本才可确认，财务核对、PM 不能确认收入。消费键=企业+contract_line+acceptance 原 stable id+覆盖，政策/算法版本只作快照，不重置消费；共同锁序。正例批准不含税120000，验收覆盖50%累计60000；同覆盖重跑差额0，有效覆盖增至75%时累计90000、**本次新增30000而非15000**。超额/重复履约、未批变化、直接输入 recognized、日期/成本/任务完成率或结算/开票/收款触发均拒绝或新增收入0；锁期只开放期调整，历史不改。验收=A0+`python manage.py test tests.test_v1_management_revenue -v 2`+`F#/uat_sequences/2`、`F#/uat_sequences/10` 及三模板黄金链+PG；真实 PG/浏览器未验证。

### WP-REVENUE-SERVICE — 工时及固定服务期收入（1片）
`PHC-REVENUE-SERVICE`（主：`V1-REV-06`；依赖 REVENUE-FIXED、BILLABLE-CONFIRM、DELIVERY-PERFORMANCE）：`E#/$defs/RevenueRecognition`、`RevenueAdjustment`、`BillableConfirmation`、`ServiceConfirmation`、`MethodPolicySelection`；命令 `C#/$defs/management_revenue.recognize~1v1`（performance_ref.kind=billable_confirmation 或 service_confirmation）及 `CR` 同 ID；查询 `Q#/$defs/management_revenue.contract_line.get~1v1`、`delivery.performance.available~1v1`；价格只引 `B#/$defs/PricingConfiguration`。执行 `pmo_plugins/management_revenue/service_services.py`、`service_views.py`、`templates/management_revenue/service_detail.html`、`tests/test_v1_management_revenue.py`；R0 接 root services/URL。状态同 RevenueRecognition；计时按确认分钟累计后 HALF_UP，服务期只按批准 schedule/period 规则，无部分规则不按天猜。消费键=企业+contract_line+billable/service 原 stable id+覆盖，版本不重置，共同锁序。正例每小时25000分、确认40分钟累计16667分；`F#/uat_sequences/4` 确认480分钟收入200000分；固定服务期价款30000且整期确认则30000。重复分钟/期间、未确认事实、成本分钟替代计费分钟、现金替代履约、同服务双方法、缺部分期间规则均拒绝。验收=A0+management_revenue/time/delivery 测试+三模板黄金链+PG；服务期确认仍受 DELIVERY-PERFORMANCE 的 additive 契约缺口阻塞，真实客户确认/PG/浏览器未验证。

### WP-REVENUE-PAIR — 收入和结算配对及结果查询（1片）
`PHC-REVENUE-PAIR`（主：`V1-REV-08/09/10`；依赖 REVENUE-SERVICE、AR-SETTLEMENT、EXPENSE-POST、LABOR-ALLOCATION）：`E#/$defs/PerformanceSettlementMatch`、`RevenueRecognition`、`Settlement`、`CostEvent` 与 `D#/$defs/PerformanceSettlementMatch`；命令 `C#/$defs/management_revenue.match~1v1` 及 `CR` 同 ID；查询 `Q#/$defs/management_revenue.contract_line.get~1v1`、`settlement.open_item.get~1v1`、`costing.reconciliation.get~1v1`；关系 `R#/performance-settlement-match`。执行 `pmo_plugins/management_revenue/match_services.py`、`match_views.py`、`result_services.py`、`templates/management_revenue/result_detail.html`、`tests/test_v1_management_revenue.py`；R0 接下一迁移/root URL。Match 仅 `draft/active/posted/reversed/superseded`；同明细部分配对，unmatch 后新建，不确认收入。财务办理，PM 只见授权项目且成本敏感字段遮蔽；唯一键=企业+contract_line+performance 原 stable id/覆盖+settlement 原 stable id/覆盖，共同锁序。正例 `F#/uat_sequences/10` 两个不重叠净额10000履约分别与结算配对，matched=20000、新增收入/应收=0，发票/信用/现金不改变收入；跨明细、超额/重复覆盖、把毛利叫净利润或把净现金流当利润均拒绝。缺成本/费率/期间时结果显示缺项而非完整可信实际。验收=A0+management_revenue/settlement/costing 测试+完整经营结果+PG；真实浏览器未验证。

## 6. 唯一需求—PH—证据映射与晋级

下表是每包唯一主交付占位符；支持片只提供依赖或验证，不分散主 owner。正式整合时替换为真实 PH，并回填 tasks/spec、work-packages 与 requirement-trace；“计划证据”不是已有证据，实际 report/handoff、PR/CI 合入后才可登记。

| 包 | 主需求 | 唯一主 PHC | 计划需求证据/夹具指针 |
| --- | --- | --- | --- |
| WP-SDK | 无直接主需求 | 无主需求；SDK-1/2/3依次支持 | 主控SDK候选反例、旧插件回归 |
| WP-CORE | 无直接主需求 | 无主需求；CORE-KERNEL主责基础 | 来源/锁期/旧账完整性 |
| WP-IDENTITY | V1-ORG-02、V1-ORG-03、V1-ORG-05、V1-ORG-06、V1-ORG-07、V1-ORG-08 | PHC-IDENTITY-LIFECYCLE | `F#/uat_sequences/14` |
| WP-PEOPLE | V1-ORG-04、V1-MD-02、V1-MD-04 | PHC-PEOPLE-DIRECTORY | `F#/uat_sequences/0`、`F#/uat_sequences/14` |
| WP-PARTY | V1-MD-03、V1-MD-06、V1-MD-07 | PHC-PARTY-REGISTRY | `F#/uat_sequences/1` |
| WP-PROJECT | V1-MD-01、V1-PRO-01、V1-PRO-02、V1-PRO-06、V1-PRO-07 | PHC-PROJECT-REGISTRY | `F#/uat_sequences/1`、`F#/uat_sequences/9` |
| WP-DELIVERY | V1-PRO-03、V1-PRO-04、V1-PRO-05 | PHC-DELIVERY-WORK | `F#/uat_sequences/2` |
| WP-CONTRACT | V1-CON-01、V1-CON-02、V1-CON-03、V1-CON-04、V1-CON-05、V1-CON-07 | PHC-CONTRACT-APPROVAL | `F#/contracts` |
| WP-CHANGE | V1-CON-06、V1-CON-08 | PHC-CONTRACT-CHANGE | `F#/uat_sequences/1` |
| WP-TIME-INPUT | V1-TIM-01、V1-TIM-02、V1-TIM-03、V1-TIM-04 | PHC-TIME-CAPTURE | `F#/uat_sequences/3` |
| WP-TIME-REVIEW | V1-TIM-05、V1-TIM-06、V1-TIM-08 | PHC-TIME-APPROVAL | `F#/uat_sequences/0`、`F#/uat_sequences/14` |
| WP-BILLABLE | V1-TIM-07 | PHC-BILLABLE-CONFIRM | `F#/uat_sequences/4` |
| WP-AR | V1-SET-01、V1-SET-03、V1-SET-04、V1-SET-09 | PHC-AR-SETTLEMENT | `F#/uat_sequences/2`、`F#/uat_sequences/9` |
| WP-AP | V1-SET-02 | PHC-AP-OBLIGATION | `F#/uat_sequences/5`、`F#/uat_sequences/16` |
| WP-CREDIT | V1-SET-08 | PHC-CREDIT-ADJUSTMENT | `F#/uat_sequences/10` |
| WP-LABOR-POLICY | V1-CST-01、V1-CST-02、V1-CST-03、V1-CST-04 | PHC-LABOR-POLICY | `F#/facts/labor`、`F#/uat_sequences/3` |
| WP-LABOR-POST | V1-CST-05、V1-CST-06、V1-CST-10 | PHC-LABOR-COST | `F#/uat_sequences/3` |
| WP-EXPENSE | V1-SPD-01、V1-SPD-04、V1-SPD-06 | PHC-EXPENSE-POST | `F#/uat_sequences/16` |
| WP-SPEND | V1-SPD-02、V1-SPD-03、V1-CST-12 | PHC-SPEND-ACCEPTANCE | `F#/uat_sequences/5` |
| WP-SPEND-CORRECTION | V1-SPD-05、V1-SPD-08 | PHC-SPEND-CORRECTION | `F#/uat_sequences/5`、`F#/uat_sequences/16` |
| WP-SHARED | V1-CST-07、V1-CST-08、V1-CST-09、V1-SPD-09 | PHC-SHARED-ALLOCATION | `F#/facts/shared`、`F#/uat_sequences/6` |
| WP-REVENUE-FIXED | V1-REV-04、V1-REV-05、V1-REV-07 | PHC-REVENUE-FIXED | `F#/uat_sequences/2`、`F#/uat_sequences/10` |
| WP-REVENUE-SERVICE | V1-REV-06 | PHC-REVENUE-SERVICE | `F#/uat_sequences/4` |
| WP-REVENUE-PAIR | V1-REV-08、V1-REV-09、V1-REV-10 | PHC-REVENUE-PAIR | `F#/uat_sequences/10` |

状态建议：31片当前均 proposed。只有主控安全候选在 PH137 最终规划合入后可按正式任务契约定 ready；安全 done 后才审 PH-001 DoR，随后逐片晋级，不按阶段批量 ready。每片 report 必须记录需求 ID、实际命令/退出码、独立复核、偏差、unverified 和最终 SHA；共享 R0/迁移合件后重跑 A0 与定向验收。

共同未验证：31片均未实现；未跑产品测试、真实 PG 财务竞争、真实浏览器、旧 URL/CSV/API/MCP 全量兼容、目标 Linux/TLS、数据库+非空附件恢复、客户政策确认或签收。PH135 的 PG 基础设施资格不代替业务证据。DRF 安全修复只走主控独立薄片；旧 AI/资源/客户端、PH019/020 与 release-readiness 不降低。无版本发布、生产部署、真实客户数据或外联授权。

## 7. 第二次整改回执、实际核对与未完成

1. **收入算术**：已把120000分从50%到75%的累计确认改为60000→90000，本次新增30000；同覆盖重跑0，未改冻结财务口径。
2. **工资原始来源**：已改为同企业+员工+自然月；方法/政策/RateSnapshot版本只作快照，同源更正另链原员工月。换版本全额重提新增公司成本0，2400000→2400101只追加101。
3. **真实实体/DTO**：DeliveryClose、NonProjectWork、AgingCase、RateSnapshot均只引用 `E`，分别作为只读结果、Timesheet派生、OpenItem派生和服务快照；未再写成 `D`。ServiceConfirmation与SpendCommitment的现有命令覆盖缺口已单列，未伪造命令。
4. **持久状态**：AccountingPeriod保留 canonical 五态并以PeriodClose/closed_through表达锁定；Membership.active保持布尔值，未发明open/closed或active/inactive/suspended替代字段。
5. **SDK与首批**：只引用主控SDK/security候选及最终规划，顺序为安全→SDK1/2/3→CORE；本文未解除PH-001 blocker、未将任何业务片置ready。
6. **非机械拆分**：24包由96片合并为31片；仅SDK、CORE、IDENTITY、DELIVERY、AR、LABOR-POST保留有独立交付/迁移/跨域验收边界的多片，每片均列具体文件、接口、依赖、反例、验收和外门。

实际核对：`PENDING_FINAL_READONLY_CHECK`。

未完成：本候选尚未经主控最终整合、独立S复核、可信BASE规划PR/当前CI合入，未形成正式PH或证据；两个additive命令缺口未解除；全部产品实现、A0/定向测试、PG/迁移/浏览器/UAT、真实客户/生产验证均未执行。本文不声称产品或正式PH137通过。
