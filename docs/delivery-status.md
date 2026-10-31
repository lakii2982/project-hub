# 交付状态：现状头

口径：本头只做能力概览；逐任务条目与完整历史快照在 [delivery-status-archive.md](delivery-status-archive.md)（append-only，不改写），新条目只进归档，本头仅在能力变化时重写。验证明细见 [verification.md](verification.md) 与 `project/reports/`；`product-requirements.md`、`accounting-rules.md` 是目标设计，多数未实现。任务状态唯一来源是 `project/tasks.json`（用 `projectctl brief <id>` 查询）。

## 当前能力（2026-10-30，依据最新main与当次CI）

- 版本：0.1.0 工程验证版已发布；main 在 v0.2 基础产品化与 1.0 现金/应收薄片迭代。
- 已实现：项目工作流（8阶段状态机、蓝图、关键任务）、成本/现金分账（工时与六类单据审批入账、费率快照、幂等token、负值冲销）、审批审计、导入导出、个人API/MCP、插件运行时与三叶插件；前端六页签与统一蓝白视觉（PH-022至025）；四CLI交付闭环演练（PH-100至108）。现金/应收薄片：现金事实、posted过账、客户结算与未清/账龄、服务期与管理收入已合入，核销首片（部分核销/撤核，PH-232）已实现；银行手续费草稿与净额/手续费对账首片（PH-244，仅未批准草稿，无成本/账本/未清写入）已实现；手续费提交/独立批准/驳回与批准时一次性公司成本过账（PH-250，成本只走 cost 账，无现金流水/未清核销写入）已实现。合同变更草稿/独立决策/生效新版本与终止（PH-265：终止保留历史并经既有只读条款接口阻断新结算；变更不写账本/现金/收入/未清）已实现。合同变更影响只读追溯（PH-275：变更前后批准版本/条款/行与已生效结算/未清/核销/管理收入只读聚合、终止后新结算/变更阻断深化；无迁移/无写入口/不重算历史）已实现。合同收费模板/生效条款只读快照与结算核对（PH-287：批准版本/模板/条款行/变更历史/终止标记只读快照 + 条款金额/结算分区/重复消费/跨项目/旧版本引用异常清单；无迁移/无写入口/不改写路径）已实现。客户可计费确认首片（PH-269：新建 time 插件，记录客户确认分钟并与已批准工时/合同条款对齐；只写确认事实与 canonical 事件，不写收入/账本/现金/未清）已实现。
- 跨域只读核对总览（PH-291：合同待审变更/终止条款、结算未清/超额核销/退款覆盖、管理收入确认与结算差额、time 可计费 pending/待审工时、导入预览缺失、现金过账净额/缺分类的统一只读看板；delivery 层只读聚合各 owner 既有事实，无迁移/无写入口/无审计、不建 insights 第二 owner；缺失上游以 null+missing 展示而非 0）已实现。
- 成员账号/项目范围与授权审计只读运营视图（PH-281：成员/角色/停用状态/项目范围/AgentToken 生命周期计数/身份审计摘要的统一只读聚合；canonical `identity-access` history_read 路由；无写/审计/迁移；pm 仅管理项目摘要、member 拒绝、脱敏不含令牌密文/密码/费率）已实现。
- 发票税额资格确认与跨 owner 编排（PH-320）：复用 PH-252 发票事实，新增 pending→confirmed 税额资格确认/取代（append-only 状态守护）与跨 owner 引用链登记（管理收入反冲/cash 退款/银行退回/结算调整，经 owner 事实存在性校验，不写其它 owner）；只读资格/编排引用链页面；无 canonical/新 owner 写/contract schema 变更。
- 多币种/汇率首片（PH-321，contracts-only）：`FxRateSnapshot` append-only 汇率事实（精确业务日、版本化、Decimal、来源、无最近汇率回退）+ `record_fx_rate`；合同首次批准/变更生效写入路径接受 `currency/original_*_cents/fx_rate_ref`，经冻结汇率一次性 ROUND_HALF_UP 换算并保存原币；无汇率拒绝非 CNY；合同行原币/记账币只读核对；无 core 迁移；跨 owner（结算/收入/现金/发票）写入与 verify_integrity FX 分支按 Lead 裁决不在本片。
- 管理收入确认与结算/合同条款只读配对（PH-284：RevenueRecognition 已确认/冲销、active Settlement/OpenItem/核销与批准条款快照只读聚合 + 确认超合同/终止后确认/结算无确认/金额与跨项目异常清单；无迁移/无写入口/零审计副作用）已实现。
- 未实现：全站界面统一与经营趋势（PH-026）、正式合同、应收应付全量、预收预付/退款、发票、多币种、共享分摊、月度人工池、期初、通知中心；core 未完全插件化。

## 验证边界

CI 已覆盖 SQLite/PG 回归、迁移、append-only、MCP、容器恢复、CodeQL、治理门禁；未验证真实TLS、锁账PG并发、非空附件恢复后检查、真实模型与个人客户端联调。CI通过不等于生产验收。

## 交付效率改造（2026-10-30 起）

会话开场 `projectctl orient`、任务上下文 `brief <id>`、报告起草 `draft-report <id>`；本文件、`project/tasks.json`、`docs/v1.0-development/work-packages.json` 禁止整文件读入。量化基线与复测口径见归档 PH-233 条目。

## PH-240 退款与银行退回（2026-10-30）

在PH-230已过账现金与PH-232核销覆盖之上实现“原现金覆盖内真实退款/银行退回”：可退额=posted gross−active核销−active退款−active退回（服务端派生、非负），退款生成反方向posted支出与Refund事实+`cash.refunded.v1`，银行退回按原方向取反生成新现金与BankReturn+`cash.returned.v1`，幂等收据与期间/锁期校验；新现金沿用PH-230过账事件/收据不变式。覆盖对称化：settlement新增只读clearance_coverage_for_cash，核销剩余与现金候选可用额扣除退款/退回，保证cleared+refunded+returned≤gross（Lead裁定A；PH-232 spec文字未同步，PH-247登记小修）。明确不写账本/收入/成本/应收应付/未清，不做Offset/手续费/转账/更正/导入/期初；PH-171不解除。见`docs/verification.md`与`project/reports/PH-240.json`。

## PH-239 现金分类与账本桥接首片（2026-10-30）

在PH-230已过账现金之上实现分类到现金账的闭环：posted现金分类为receipt/payment/advance/tax（payroll命令层拒绝），每流水至多一条有效分类、撤分类append-only且可重分类；每条有效分类经core具名桥接恰写一个`cash.classification` AccountingEvent与一条cash_in/cash_out、funding_account scope的LedgerPosting，撤分类追加镜像事件/负值posting（原记录不改不删），bank_effect claim每流水唯一永久、撤后重分类净额复用；canonical recorded/reversed事件posting_event_refs恰1个core/accounting_event（PH-243冻结）；core放宽`labor_posting_cost_scope`（core 0009）。cash迁移因PH-240先合入顺延为0005。verify_integrity七项现金账不变式（与PH-240退款provenance守卫合并保留）。独立S复核round 2 PASS（3阻断消除）。明确不做工资分类/项目分配/Offset/退款/手续费/互转/更正/导入/期初，不解除PH-169/170/171 proposed。见`docs/verification.md`与`project/reports/PH-239.json`。

## PH-241 预收/预付抵扣 Offset 首片（2026-10-31）

在PH-239 advance分类与PH-232未清核销之上实现预收抵扣闭环：owner/finance选择同企业/同往来方、已过账且有效advance分类的客户预收现金，部分抵扣active应收OpenItem；append-only Offset事实（settlement 0005），支持同一对多次部分抵扣，余额由服务端按advance可用额与OpenItem剩余额派生；无Offset撤销、无canonical事件、无账本/会计事件/收入/成本/应收应付写。按Lead裁决方案a修正PH-232服务行为：advance可用扣active cleared+offsets、核销候选/上限排除advance分类现金、同一现金不可同时clearance+offset；verify_integrity新增offset分支且不加全局禁令。未验证：PG并发（CI）、浏览器1440/390、独立复核、生产/UAT；PH-232 spec文字待PH-249同步。见`docs/verification.md`与`project/reports/PH-241.json`。

## PH-245 内部账户互转首片（2026-10-31）

在PH-239分类桥接之上实现内部账户互转：同企业、反方向、同额、同日的两条已过账现金成对关联为append-only InternalTransfer（cash 0006），经PH-239非路由内部服务为两腿各写一条transfer分类与cash_out/cash_in posting（funding_account scope），公司净现金恒为零；撤转append-only追加两条撤分类+冲销posting、InternalTransferReversal与canonical reversed事件；Unique(out/in_cash_transaction)无条件终身一次；两腿不同request_token；服务层先owner/finance fresh校验；canonical linked/reversed的posting_event_refs为0/0、changed_refs恰3、classification_reversal_refs=2；verify_integrity新增internal_transfer分支（批量、无全局禁令）。无新增canonical topic、无公开transfer通道；不做手续费/Offset/退款/更正/导入/期初，不写成本/收入/应收/应付/未清。未验证：PG并发（CI）、浏览器1440/390、独立复核、生产/UAT。见`docs/verification.md`与`project/reports/PH-245.json`。

## PH-264 成员账号生命周期与项目范围首片（2026-10-31）

实现成员账号最小生命周期闭环：owner 变更公司内成员角色（owner/pm/finance/member/auditor）、以 ProjectMember 差集维护可见项目范围、停用/恢复成员；停用要求先处理在职员工绑定与有效项目经理交接，同事务撤销全部未撤销 AgentToken、清空项目范围并写审计；最后 active owner 不可降级/停用、不可自停用、停用成员不可再改（除恢复）；公司级角色不接受项目范围；Membership.version 乐观版本（core 0010）+ generic audit identity.membership.changed（无新 canonical topic/无新 business 表）；同 token 重放、dry_run 零副作用；finance/auditor 只读全部成员，pm/member 仅读自己；settings 页面 owner 表单承载。未验证：浏览器1440/390、独立复核、生产/UAT。见`docs/verification.md`与`project/reports/PH-264.json`。

## PH-274 onboarding 导入预览实现前置首片（2026-10-31）

新建 pmo_plugins.onboarding 插件（ADR 0012，manifest requires={contracts,settlement,cash}）并落地只读导入预览：CSV 模板识别（time/expense/plan）、字段映射与未映射列、200 行内预览、逐行格式/主数据预检与内存态 ephemeral 冲突投影；零业务事实/账本/来源消费/规范事件（仅一条 generic audit），无表无迁移、无提交/期初/冲突持久化。权限 owner/finance/auditor 可读（auditor 经 history_read GET 只读预览，文件上传走 business_write），pm/member 拒绝、模块与依赖停用拒绝；7 个既有禁用 loop 测试按 PH-282 白名单先停用 onboarding。未验证：浏览器 1440/390、独立复核、PG、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-274.json`。
## PH-279 现金链路只读核对快照首片（2026-10-31）

新增现金只读核对：posted 现金 ↔ 有效分类 ↔ 账本事件/posting/bank_effect claim ↔ 退款/退回 ↔ 内部互转成对，输出逐流水快照、账户现金账净额（含冲销，非银行余额）与 issue_code 异常清单；offset_implemented=false 占位（PH-241 未合入）。owner/finance/auditor 只读，pm/member 拒绝；无迁移/无写入口/无审计写/不写账本事实。偏差：pm 因现金事实无项目归属而拒绝；两条路由 history_read，cash 停用后历史 GET 仍 200 可读；无审计写。未验证：浏览器 1440/390、独立复核、PG、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-279.json`。

## PH-288 银行账户/现金余额只读对账汇总首片（2026-10-31）

新增银行账户只读对账：按账户/期间派生 posted 现金 in/out 与净额、退款/退回、内部互转 in/out 与抵销、分类桥接现金账净额、期初/期末余额（closing=opening+posted_in-posted_out，不建余额表）与 issue_code 异常清单（无 posted 事实/未分类现金/账本净额不符/跨账户跨 org/同账户互转/负余额/关期后 future 事实）；offset_implemented=false 占位（PH-241 未合入）。owner/finance/auditor 只读，pm/member 拒绝（无项目归属）；两条 history_read 路由，cash 停用后历史 GET 仍 200；无迁移/写入口/审计写。偏差：pm 拒绝、Offset 未实现、余额含退款/互转实际现金腿、反例 append-only 兼容。未验证：浏览器 1440/390、独立复核、PG、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-288.json`。

## PH-304 运营核对底稿授权只读 CSV 导出首片（2026-10-31）

新增授权只读 CSV 导出：组合 PH-291 跨域总览、PH-283 结算覆盖、PH-279 现金桥接、PH-280 手续费配对四类只读数据集，输出 UTF-8+BOM CSV（表头/refs/as_of/生成时间/行数/#checksum sha256），dry_run 计划零文件、request_token 进程内幂等；canonical domain_metadata 声明 `ops-export/` history_read、urls 仅导入，无 POST 写入口。owner/finance/auditor 全量，pm 仅有项目归属的 3 类，member 拒绝；无迁移/写/审计/零事实变化。偏差：receipt 进程内非持久、pm cash_bridge 拒绝、单路由页面+下载、仅组合既有只读服务。未验证：浏览器 1440/390、独立复核、PG、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-304.json`。

## PH-309 运营核对工作台聚合首片（2026-10-31）

新增只读运营核对工作台：聚合 PH-291 跨域总览、PH-283 结算覆盖、PH-279 现金桥接、PH-280 手续费配对、PH-281 成员/授权五个分区的 issue 计数、关键合计、missing 与下钻链接；缺失 null+missing 不显示 0，不重算口径、不建第二 owner。canonical domain_metadata 声明 `ops-workbench/` history_read、urls 仅导入；owner/finance/auditor 全量、pm 管理项目摘要（cash_bridge 无项目归属不可用）、member 拒绝；无迁移/写/审计。偏差：五分区按 spec、合同/收入/银行余额经 PH-291 间接聚合、pm cash 分区不可用、missing 语义。未验证：浏览器 1440/390、独立复核、PG、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-309.json`。

## PH-316 运营核对工作台导出联动首片（2026-10-31）

工作台分区联动 PH-304 底稿导出：ops_overview/settlement_coverage/cash_bridge/fee_pairing 分区带 dataset/as_of/period 导出链接，复用既有 ops-export/ 服务与 history_read 路由；identity_access 无导出（PH-304 数据集不含身份敏感明细，export_dataset_not_implemented），pm cash_bridge 不可导出（无项目归属）。无新写通道/元数据副本/迁移/表/审计，零写、事实计数不变。未验证：浏览器 1440/390、独立复核、PG、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-316.json`。

## PH-252 发票实现首片（invoicing 插件）（2026-10-31）

新建 invoicing 插件：Invoice/InvoiceLine/InvoiceAllocation/InvoiceReversal/TaxQualification + 自有迁移 0001 + vendor-aware append-only 触发器；record/allocate/reverse 服务（公司锁、request_token 幂等、期间/锁期、整数分价税分离、来源额度只读派生、部分/全额红冲、append-only 负值冲销、红票≠退款），不写收入/现金/账本/未清；浏览器表单/列表/详情、verify_integrity 发票分支、generic audit + canonical events。插件默认停用（opt-in，避免反向依赖阻断模块停用）；分配首片仅 sales 合同明细/结算未清项/结算。未验证：浏览器 1440/390、独立复核、PG 触发器/并发、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-252.json`。

## PH-322 关期/结项实现首片（2026-10-31）

delivery append-only run：PeriodCloseRun/ProjectClose + 迁移 0005（vendor-aware append-only 守护）；close/reopen period/project 命令（公司锁、expected_version、DeliveryReceipt 幂等、dry_run 零写、closing+closed 成对、plugin. 审计，仅更新既有期间/项目当前行，不写业务事实）；只读预检复用既有 owner 接口（issue_code/reminder_ref/precheck_digest/missing）；close 页面/执行/重开表单；verify_integrity 五类 close 分支。偏差：PH-336 已补 models/migrations/verify_integrity/test 白名单；query-count 阈值按实测 84→88（4 条固定 bulk 查询）。未验证：reviewer-b 复审、PG trigger/并发、浏览器 1440/390、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-322.json`。
- 分摊/预算 ETC 实现首片（PH-330）：costing 0005/0006 落地共享费用分摊批次（净零重归属、末项吸收、append-only）与项目预算版本/四眼批准/budget_residual_v1 ETC 只读派生；只读核对页 + verifier 分支；无 canonical/schema/core migration/其它 owner 写。
