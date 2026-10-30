# ADR 0008：完整服务确认与归属月份收入

状态：PH-221 独立规划候选，未实现、未验证。产品负责人已决定收入属于服务月份、客户确认日期单列；锁月拒自动补记，旧账不重算。

优先补 fixed_service_period，复用 PH-214 真实批准价格和 PH-219 收入owner/原分反冲链。工时法还需要客户计费分钟与真实工时来源，保留原PH-154/166；原PH-148/149/150/167状态和依赖不改。拟新PH-222依赖214/219/221，需规划合入可信BASE后另行执行。

现合同规则允许 no_partial 的批准schedule跨多月。若强迫按月独立确认服务，就会未经客户批准把整段不得部分确认变成允许部分确认。因此保留一个真实ServiceConfirmation父事实：no_partial仅支持单自然月完整原段；跨月 no_partial 缺少批准月价，只可展示/草稿，正式办理拒绝并提示须等待后续正式合同变更能力独立批准月度分配；本片没有既有合同变更能力，首次批准服务拒绝已有 approved 合同，不能称可直接原审批修订，不能伪造新合同绕过。不得按天/等月猜分价。calendar_days可确认原段内连续子区间。批准时冻结各自然月交集为真实私有月分配子行；它们不冒充额外客户确认，也不重复客户证据或审批。每次显式为一个月行确认收入，UI显示父履约及各月办理状态。

每月金额沿原整个schedule天数累计HALF_UP差额计算，不在月初重置；确认覆盖末日是各月收入发生日，客户真实confirmed_on独立保存。原月锁定拒绝，不能以次月确认日转月或静默自动补记。no_partial既约束确认范围，又未授权跨月按天定价，因此本片不支持跨月no_partial收入；不能宣称已覆盖所有服务期合同。服务尚未结束或证据未确认时不自动形成收入。

Delivery唯一拥有父确认、真实月分配和依据快照；management_revenue继续唯一拥有收入与signedAdjustment。Recognition新增真实服务父/月FK，与现Acceptance来源实施DB XOR，不能假造Acceptance或新收入余额。公共performance_ref沿既有service_confirmation父永久版本，稳定claim键为父ID+service_month，真实财务子ref用既有Recognition source_ref；私有月行无需注册新公共SourceRef。零元也占用唯一消费和反冲终态。

反父前逐月显式反全部原收入，原日期期间原分不变；锁原月拒绝，开放期差额另规划。重用覆盖需要已全反原来源的替代链、新客户依据和独立确认。模块停用、关期预检、项目汇总与CSV都必须识别服务月明细，不能因父已有一笔收入就漏掉其他月。

正式批准confirmed时沿ADR-0007规则以固定plugin.* audit抑制简略outbox，并在同一atomic内发一次已登记canonical `delivery.service_confirmation_changed.v1`（source=父确认永久版本source_ref，posting_event_refs为空，不新建topic、不改events/registry）；草稿/依据/提交/撤回/反确认用generic audit。已登记的 `delivery.service_confirmation.draft.save/v1` 命令与PH-149记录的 core.document.decide handler 属命令/PAT接线与PH-149范围，本片只实现浏览器与服务层路径，不实现也不伪造该命令语义。

只新增具名service-period服务及现owner分支，不引通用执行框架/SDK/部署。私有冻结快照不扩公共契约；仅替代来源字段需要ServiceConfirmation/ServiceConfirmationDraft窄补及对应摘要同步。精确白名单、日期/锁期/幂等/权限和待执行验收见PH-222。本文不授权proposed原任务，不声称生产或完整1.0完成。


公共投影边界：ServiceConfirmation 的 confirmed_by_member_id 为必填且非nullable，因此 draft/submitted/withdrawn 只作为私有工作流记录展示，不输出为 canonical ServiceConfirmation；只有 confirmed/reversed 才输出该 canonical 实体并保留真实独立确认人。不得给草稿填假确认人或放松schema。

摘要写权限仅 extension-freeze.json 以及 docs/v1.0-development/fixtures/fixture.json.contract_artifact_sha256 中 entities.schema.json/write-dtos.schema.json 的实际摘要（check_fixture.py:84），不包括 sdk1-metadata.example.json、fixture业务值或oracle。
