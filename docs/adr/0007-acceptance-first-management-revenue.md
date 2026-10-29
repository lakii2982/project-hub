# ADR 0007：先交付固定节点客户验收与管理收入

状态：PH-218 规划决策，随规划 PR 合入后约束 PH-219；尚无实现或验证结论。

客户已有三模板合同与月度人工业务，但固定节点合同还不能走完客户确认、收入、纠错与追溯。原 PH-149/166 同时覆盖更多履约方法且依赖链未齐，不应把它们直接标完成。本决策登记独立首片 PH-219，只做真实批准 fixed_milestone 合同。

采用 delivery 管客户验收，management_revenue 管收入与签名冲销。复用 core.Milestone，旧内部节点完成不作外部验收。contracts 的具名只读 approved_line_snapshot 验证批准 digest、冻结候选明细与 Selection 配置，提供唯一批准价格，不读 Project.contract_cents 猜金额。客户依据为 delivery-owned 不可变人工登记及 hash，页面明确由业务人员核对，不冒充附件上传或客户电子签名。

验收按节点覆盖基点累计 ROUND_HALF_UP 差额定价。active confirmed 覆盖以组织、项目、真实节点跨合同/line/version互斥，避免合同重复引用节点导致重复收入；零额同样占用。批准后保留原配置与每段金额。另一 owner/finance 独立确认后，财务可确定性生成原额收入，不再加第三审批层。工时、服务期间、自由调整、结算、现金、开票和外部客户账号留后续。

RevenueRecognition 加 RevenueAdjustment 是收入唯一事实。core 仅具名桥接 AccountingEvent/SourceConsumption 与审计/outbox；不把收入装进现 cost LedgerPosting。AccountingEvent.labor_origin_ref 新迁移允许 null，收入填 None，劳动仍要求真实 pool，旧人工字段/数据保持。完整性按 source.namespace 分支查实际收入 FK、金额和 claim，杜绝假劳动来源或第二收入账本。

来源消费稳定键排除版本、请求键、渠道，零额也检查唯一事实与终态。收入发生日及期间固定取客户确认日；org lock 和 AccountingPeriod closed 同时约束，期间可在公司锁及独立确认来源下具名建立，与工资切换无关。先显式反收入再反验收；全额负原分、原日期期间、一对一逆转，原期锁定拒绝。原来源不能换 key 重做收入；释放验收覆盖后新正确来源可再确认，但重用覆盖必须显式 replaces_acceptance_ref 指同组织项目 line 的已完全反冲原验收，私有 FK 保留替代链。开放期累计差额留原后续任务。

此选择给客户一条完整可纠错业务路径，代价是首片不覆盖所有合同方法或法定财务会计。项目财务分别显示管理收入与已记录成本，数据不齐不称利润。公共 canonical 已有 EvidenceRef customer_confirmation、验收/收入实体与事件，private 快照和 FK 不扩大 schema；唯一公共补充为 Acceptance/AcceptanceDraft 的 optional nullable replaces_acceptance_ref，root 仅同步对应摘要；无 SDK、执行器、CI、部署改动。详尽字段、状态、权限、精确白名单和验收矩阵以 PH-219 为准。原 PH-148/149/166 及相关依赖、状态保持原样。


## 补充冻结：依据登记、事件与关账待办

客户联系人必须引用现 contracts.Contact：登记时有效、同 org 且 party 为所选 line 的合同客户；证据保留当时显示 snapshot，后续改名/停用不改历史。可先在现合同联系人页面建立，不维护第二联系人主档。手工依据字段只是本次业务记录。单浏览器保存可在 outer atomic 内调用唯一 owner 的 register_customer_evidence 再 save_acceptance，生成真实 EvidenceRef；失败一同回滚，dry_run 零副作用。

草稿/依据登记无适用 accepted canonical 事件，沿现 audit 普通 action 自动 generic outbox，不冒 canonical，不发 delivery.accepted、不创建未注册 SourceRef 或伪上传 Evidence 事件。正式 Acceptance 确认、收入及适用财务反冲使用固定 plugin.* audit 抑制简略 outbox，再同 atomic 发一次已有适用 canonical。验收反冲无适用 canonical topic 时用普通 generic 追加事件，不扩 events.schema。前文“唯一 canonical outbox”仅适用于具有相应正式 topic 的操作。

草稿允许本人或 owner/finance 带原因明确 withdraw，追加审计，内部 workflow_state=withdrawn、canonical 仍 draft；终态禁止确认及收入、保留原记录，不能改真实 customer date 逃锁。confirmed 只能原反冲路径。

root_only 另含 core/forms.py，且只准现 OrgForm 调具名 check_revenue_close(org,cutoff) 的本片接线。现 settings 已在 companylock 内重新绑定 Form，沿用该锁；预检拒绝 confirmed_on <= cutoff 的已知 draft/submitted 验收（排除 withdrawn），以及 confirmed 但未收入且未反验收记录被静默锁掉。零额 Recognition 也是消费；已反收入但 Acceptance 未反仍为终态，不能当未消费重认。不扩通用月结、CI或框架。

待执行验证补充：Contact 跨客户/停用拒绝，后续改名停用历史 snapshot 不变；浏览器单保存失败依据/草稿全部回滚；generic/canonical 类型和次数准确；withdrawn 权限、原因审计及终态；真实月中及截止 pending Acceptance 导致 OrgForm 拒锁；PostgreSQL 多连接收入与关期竞争，无半笔或漏关账检查。上述均为验收要求，不是通过结论。

草稿/待审/withdrawn验收仅为私有UI工作流，不伪造confirmed_by输出canonical实体；确认后才输出规范Acceptance。收入owner先建私有unposted候选，再由core具名helper核真实行并建claim/event，最后同atomic定稿posted；定稿后DB完全追加保护，冲销状态由追加调整派生。
