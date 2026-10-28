# 1.0 开发计划与容量估算

这是PH136静态规划，沿用6dfb7f8可信基线和62包依赖，不提供开工权限。数字是主控按复杂度给出的暂定净工作区间；没有真实产品开发速度样本，不能换算成承诺发布日期。正式PH及首批ready仍由PH137独立规划冻结。

## 可执行顺序

1. PH137收口：冻结SDK1字段/兼容、需求→PH→证据、精确范围/状态/反例；只晋级真实DoR首批。现有PH001的proposed/blocker尚未解除。
2. 首批基础：已有安全告警列P0修复候选；SDK1元数据/示例→SDK2门控→来源/期间/过账基础，再进入身份、往来方与项目。旧表和金额不重算。
3. 业务闭环：交付/工时/合同并行薄片，之后分别交付结算、成本、管理收入，再整合现金/核销/退款/票据；每个子命令保留唯一来源和事务入口。
4. 经营办理：预算/ETC/13周预测→核对/月结→统一指标→四职责工作台/项目结项→导入/期初/从零开账。界面沿PH110/PH026已有依赖处理，不让服务端权限靠按钮隐藏。
5. 环境与交付：安装可独立提前研究；真正TLS、升级/非空附件恢复、PG业务并发、容量/客户任务和发行门按实际依赖收口。没有目标/客户授权时明确待外部条件。

依赖是门而不是仅排序提示。SDK/CORE/身份链先完成；同时最多两份互斥写范围，root独占迁移、共享接线、元数据与合件。当前最长非加权实现链24包（不含PRE），有权重的路径见JSON逐情景输出。

## 估算依据与边界

实际观测：文档探针56/106/123秒；成功原8项CI106/111/113秒；新增准备CI48秒；两次449回归93.505/97.640秒且11环境跳过。它们仅说明渠道和流水线成本。原生PG候选562秒因验证缺口取消接管；本次Claude484秒、多次计算命令被本机CLI规则拒绝、无产物后安全取消，均不是交付速度或PASS。本计划由root计算，没有扩大工具权限。

估算以整数专注分钟分开执行、验证、独立审阅、整合，分类范围如下；每个包有独立行及原始需求/模块/依赖和产物引用，见schedule.json。

| 类别 | 编写 | 验证 | 独立复核 | 整合 | 理由 |
| --- | ---: | ---: | ---: | ---: | --- |
| foundation | 90–240 | 60–180 | 30–90 | 30–90 | 现有表与接口兼容、元数据/身份/项目基础；接口及接线需要复核 |
| finance | 180–480 | 120–360 | 60–180 | 60–180 | 金额、来源、锁期/自审/幂等、跨域守恒及反例；至少独立财务复核 |
| interface | 120–360 | 90–240 | 45–120 | 45–120 | 已有服务与UI/API/导入的同源办理、1440/390键盘及权限状态 |
| qualification | 90–240 | 180–480 | 60–180 | 60–180 | 目标环境/恢复/容量/客户任务和发行收口，实际演练比代码生成占比更高 |
| planning | 90–240 | 30–90 | 60–120 | 30–60 | PH137字段/任务映射与首批DoR规划，不改产品 |

6个已交付或本次交付的PRE输入不重复估算剩余工作，1个PRE-GOVERNANCE及55个实施/资格/交付包进入余量。低/高值是情景假设，不是统计置信区间。每包另留CI5–20分钟队列/重跑区间，严守当前BASE；曾有合成key误报和真实财务预期返修，故资源窗口另加25%/50%返工缓冲。

## 关键路径与资源窗口

| 情景 | 合计净工作小时（各角色相加） | 依赖关键路径小时 | 资源/依赖理论下界小时 | 静态容量表小时 | 加缓冲主动窗口小时 |
| --- | ---: | ---: | ---: | ---: | ---: |
| low | 341.0 | 139.6 | 139.6 | 226.6 | 283.2 |
| high | 964.5 | 394.8 | 394.8 | 632.2 | 948.2 |

容量表采用固定优先序的保守离线排表，按剩余依赖路径优先、固定ID打破并列；不是最优排程证明，不是运行调度器。验证预算前2/3用于初候选、后1/3用于合件后复验，复核前后各半；合件后仍须最终独立复核。表中各资源无重叠且前序CI结束才进入后序，已实际程序校核；纯工作时钟0点是所有外部条件已具备的假定起点。小时不能直接当自然日；未计算额度/客户周期/目标授权等未知等待，因此不能把末行解释为G3将于某日完成。

低情景关键链：PRE-GOVERNANCE → WP-SDK → WP-CORE → WP-IDENTITY → WP-PEOPLE → WP-PROJECT → WP-DELIVERY → WP-TIME-INPUT → WP-TIME-REVIEW → WP-LABOR-POLICY → WP-LABOR-POST → WP-REVENUE-PAIR → WP-CLOSE → WP-RECONCILE → WP-INSIGHT-METRICS → WP-WORKSPACES → WP-PROJECT-CLOSE → WP-IMPORT → WP-OPENING → WP-ONBOARDING → WP-UPGRADE-RESTORE → WP-SUPPORT → WP-RELEASE-PACKAGE → WP-CUSTOMER-UAT → WP-FINAL-RELEASE。

高情景关键链：PRE-GOVERNANCE → WP-SDK → WP-CORE → WP-IDENTITY → WP-PEOPLE → WP-PROJECT → WP-DELIVERY → WP-TIME-INPUT → WP-TIME-REVIEW → WP-LABOR-POLICY → WP-LABOR-POST → WP-REVENUE-PAIR → WP-CLOSE → WP-RECONCILE → WP-INSIGHT-METRICS → WP-WORKSPACES → WP-PROJECT-CLOSE → WP-IMPORT → WP-OPENING → WP-ONBOARDING → WP-UPGRADE-RESTORE → WP-SUPPORT → WP-RELEASE-PACKAGE → WP-CUSTOMER-UAT → WP-FINAL-RELEASE。

## 全包执行表

| 工作包 | 阶段/类别 | 涉及模块 | 直接依赖 | 主需求数 | 净工作分钟（低–高） |
| --- | --- | --- | --- | ---: | ---: |
| PRE-BASELINE 基线、授权和当轮执行/环境能力 | S0/completed_preparation | governance, execution | — | 0 | 0–0 |
| PRE-POLICY 产品政策、客户配置与范围决策 | S0/completed_preparation | governance, policy | PRE-BASELINE | 0 | 0–0 |
| PRE-CONTRACTS 跨域字段、接口、锁和迁移契约 | S0/completed_preparation | governance, contracts, costing, settlement, cash | PRE-BASELINE、PRE-POLICY | 0 | 0–0 |
| PRE-UX 角色路径、三模板与界面契约 | S0/completed_preparation | ux | PRE-POLICY、PRE-CONTRACTS | 0 | 0–0 |
| PRE-FIXTURES 黄金企业夹具、旧库构建与预期 | S0/completed_preparation | verification | PRE-CONTRACTS | 0 | 0–0 |
| PRE-ESTIMATE 关键路径、验证容量与工期区间 | S0/completed_preparation | governance | PRE-BASELINE、PRE-CONTRACTS、PRE-UX、PRE-FIXTURES | 0 | 0–0 |
| PRE-GOVERNANCE 将准备和实施拆成正式PH基线 | S0/planning | governance | PRE-ESTIMATE | 0 | 210–510 |
| WP-SDK 最小SDK注册、契约查询与全入口门控 | S1/foundation | sdk | PRE-GOVERNANCE | 0 | 210–600 |
| WP-CORE 来源、期间与过账统一基础 | S1/foundation | kernel | WP-SDK | 0 | 210–600 |
| WP-IDENTITY 账号生命周期、授权与敏感字段 | S1/foundation | identity | WP-CORE | 6 | 210–600 |
| WP-PEOPLE 员工、账号绑定与共同字典 | S1/foundation | masterdata | WP-IDENTITY | 3 | 210–600 |
| WP-PARTY 统一往来方、联系人和去重 | S1/foundation | contracts, masterdata | WP-CORE | 3 | 210–600 |
| WP-PROJECT 项目主档、成员与基础状态 | S2/foundation | masterdata, delivery | WP-PEOPLE、WP-PARTY | 5 | 210–600 |
| WP-DELIVERY 交付节点、履约证据与风险 | S2/interface | delivery | WP-PROJECT | 3 | 300–840 |
| WP-CONTRACT 合同、收费明细与生效方法 | S2/finance | contracts | WP-PROJECT、WP-PARTY | 6 | 420–1200 |
| WP-CHANGE 合同变化、终止和关联视图 | S2/finance | contracts | WP-CONTRACT、WP-DELIVERY | 2 | 420–1200 |
| WP-TIME-INPUT 项目/非项目投入与周填报 | S2/interface | time | WP-PEOPLE、WP-PROJECT、WP-DELIVERY | 4 | 300–840 |
| WP-TIME-REVIEW 投入审批与缺项处理 | S2/interface | time | WP-TIME-INPUT | 3 | 300–840 |
| WP-BILLABLE 客户可计费投入确认 | S2/interface | time, contracts | WP-TIME-REVIEW、WP-CONTRACT | 1 | 300–840 |
| WP-AR 客户结算、未清、账龄与质保 | S3/finance | settlement | WP-CONTRACT、WP-DELIVERY、WP-CORE | 4 | 420–1200 |
| WP-AP 供应商及员工费用义务接口 | S3/finance | settlement | WP-CONTRACT、WP-PEOPLE、WP-CORE | 1 | 420–1200 |
| WP-CREDIT 折让、减免、坏账与信用更正 | S3/finance | settlement | WP-AR、WP-AP | 1 | 420–1200 |
| WP-LABOR-POLICY 工资池、有效产能与费率版本 | S3/finance | costing | WP-TIME-REVIEW、WP-CORE | 4 | 420–1200 |
| WP-LABOR-POST 人工分配、差额和开放期调整 | S3/finance | costing | WP-LABOR-POLICY | 3 | 420–1200 |
| WP-EXPENSE 报销费用、发生确认与待定税额 | S3/finance | costing | WP-AP、WP-CORE | 3 | 420–1200 |
| WP-SPEND 外包采购承诺与验收成本 | S3/finance | costing | WP-AP、WP-CONTRACT、WP-TIME-INPUT | 3 | 420–1200 |
| WP-SPEND-CORRECTION 暂估转正式、退货折让和支出更正 | S3/finance | costing | WP-EXPENSE、WP-SPEND、WP-CREDIT | 2 | 420–1200 |
| WP-SHARED 共享费用分配与重分配 | S3/finance | costing | WP-EXPENSE、WP-TIME-REVIEW | 4 | 420–1200 |
| WP-REVENUE-FIXED 固定价履约收入和累计调整 | S3/finance | management_revenue | WP-CONTRACT、WP-DELIVERY、WP-CORE | 3 | 420–1200 |
| WP-REVENUE-SERVICE 工时及固定服务期收入 | S3/finance | management_revenue | WP-REVENUE-FIXED、WP-BILLABLE | 1 | 420–1200 |
| WP-REVENUE-PAIR 收入和结算配对及结果查询 | S3/finance | management_revenue | WP-REVENUE-SERVICE、WP-AR、WP-EXPENSE、WP-LABOR-POST | 3 | 420–1200 |
| WP-BANK 账户、银行事实和待匹配 | S4/finance | cash | WP-AR、WP-AP、WP-IDENTITY | 4 | 420–1200 |
| WP-CLEARING 现金核销、预收预付和抵扣 | S4/finance | cash, settlement | WP-BANK、WP-AR、WP-AP | 5 | 420–1200 |
| WP-REFUND 退款、退票与撤核恢复 | S4/finance | cash, settlement | WP-CLEARING、WP-CREDIT、WP-SPEND-CORRECTION | 2 | 420–1200 |
| WP-PAYROLL-CASH 工资社保税费和公司现金 | S4/finance | cash, costing | WP-BANK、WP-LABOR-POST | 2 | 420–1200 |
| WP-FEE-TRANSFER 手续费和账户内部互转 | S4/finance | cash | WP-BANK | 2 | 420–1200 |
| WP-EMPLOYEE-ADVANCE 员工借支及报销抵扣 | S4/finance | cash, costing, settlement | WP-CLEARING、WP-EXPENSE、WP-PEOPLE | 1 | 420–1200 |
| WP-PREPAID-SERVICE 跨期预付服务逐期耗用 | S4/finance | costing, cash, settlement | WP-CLEARING、WP-SPEND | 1 | 420–1200 |
| WP-INVOICE 发票、分配、红冲与税额 | S4/finance | invoicing | WP-AR、WP-AP、WP-SPEND-CORRECTION | 3 | 420–1200 |
| WP-BUDGET 预算编制及批准版本 | S5/finance | costing, planning | WP-CONTRACT、WP-SPEND、WP-LABOR-POLICY | 2 | 420–1200 |
| WP-ETC 剩余预计与实际覆盖释放 | S5/finance | costing, planning | WP-BUDGET、WP-SPEND-CORRECTION、WP-LABOR-POST、WP-PREPAID-SERVICE | 5 | 420–1200 |
| WP-FORECAST 来源升级、13周与资金覆盖 | S5/finance | cash | WP-CLEARING、WP-REFUND、WP-PAYROLL-CASH、WP-FEE-TRANSFER | 3 | 420–1200 |
| WP-CLOSE 期间预检、锁定与更正 | S5/finance | kernel, reconciliation | WP-CORE、WP-LABOR-POST、WP-SHARED、WP-REFUND、WP-REVENUE-PAIR、WP-INVOICE | 4 | 420–1200 |
| WP-RECONCILE 工资、成本、银行和收入核对 | S5/finance | reconciliation | WP-CLOSE、WP-FORECAST、WP-EMPLOYEE-ADVANCE、WP-PREPAID-SERVICE | 4 | 420–1200 |
| WP-INSIGHT-METRICS 统一经营指标和完工结果 | S5/finance | insights | WP-RECONCILE、WP-ETC、WP-REVENUE-PAIR | 4 | 420–1200 |
| WP-NOTIFY 到期扫描与站内待办 | S5/interface | notifications | WP-FORECAST、WP-RECONCILE、WP-CHANGE | 1 | 300–840 |
| WP-WORKSPACES 四职责工作台和跨模块导航 | S5/interface | insights, ux | WP-INSIGHT-METRICS、WP-NOTIFY | 4 | 300–840 |
| WP-PROJECT-CLOSE 项目聚合、结项归档及迟到款 | S5/interface | delivery | WP-WORKSPACES、WP-CLOSE、WP-CLEARING、WP-ETC | 3 | 300–840 |
| WP-EXPORT 授权导出与核对底稿 | S5/interface | insights | WP-RECONCILE、WP-INSIGHT-METRICS | 1 | 300–840 |
| WP-UI-ACCEPTANCE 全站统一及真实可访问办理 | S5/interface | ux | WP-WORKSPACES、WP-PROJECT-CLOSE、WP-EXPORT | 1 | 300–840 |
| WP-IMPORT 全域模板、预览与幂等原子导入 | S6/interface | onboarding, imports | WP-RECONCILE、WP-PROJECT-CLOSE | 2 | 300–840 |
| WP-OPENING 期初承接、明细覆盖和开账核对 | S6/finance | onboarding | WP-IMPORT、WP-RECONCILE | 4 | 420–1200 |
| WP-ONBOARDING 首公司向导、首项目和自助手册 | S6/interface | onboarding, ux | WP-OPENING、WP-UI-ACCEPTANCE | 3 | 300–840 |
| WP-INSTALL Linux安装、失败诊断和健康可见 | S6/qualification | operations | WP-SDK、WP-CORE | 2 | 390–1080 |
| WP-TLS 真实域名HTTPS和安全边界 | S6/qualification | operations | WP-INSTALL | 1 | 390–1080 |
| WP-UPGRADE-RESTORE 基线升级与非空附件联合恢复 | S6/qualification | operations, migrations | WP-INSTALL、WP-OPENING、WP-ONBOARDING | 2 | 390–1080 |
| WP-PG-CONCURRENCY 跨域PG竞争与最终完整性 | S6/qualification | verification | WP-CLOSE、WP-OPENING、WP-REFUND、WP-SHARED | 1 | 390–1080 |
| WP-PERFORMANCE 容量与首用操作任务验证 | S6/qualification | verification | WP-ONBOARDING、WP-UI-ACCEPTANCE、WP-PG-CONCURRENCY | 0 | 390–1080 |
| WP-SUPPORT 客户支持、数据退出和授权排障 | S6/qualification | operations | WP-ONBOARDING、WP-UPGRADE-RESTORE | 1 | 390–1080 |
| WP-RELEASE-PACKAGE 版本化发行资产与安全清单 | S7/qualification | release | WP-TLS、WP-UPGRADE-RESTORE、WP-PG-CONCURRENCY、WP-PERFORMANCE、WP-SUPPORT、WP-INVOICE、WP-REVENUE-PAIR | 1 | 390–1080 |
| WP-CUSTOMER-UAT 三模板客户周期与独立使用验收 | S7/qualification | acceptance | WP-RELEASE-PACKAGE、WP-PROJECT-CLOSE、WP-NOTIFY、WP-EXPORT、WP-EMPLOYEE-ADVANCE | 0 | 390–1080 |
| WP-FINAL-RELEASE 全部需求证据核对与正式发行收口 | S7/qualification | release, governance | WP-CUSTOMER-UAT | 0 | 390–1080 |

## 调度、完成定义与重估

- 两个执行者只处理互斥的已ready薄片；复核者独立上下文；root掌握技术取舍、迁移号、全局接线、合件和最终CI。原生渠道有命令限制时记录并接管，不放宽权限或把进程结束当任务完成；provider不是必需依赖。
- 每片必须有BASE契约、反例、实际产物、同候选验收和独立复核、当前PR/CI及正常合入后才算完成。角色/月份/来源/旧新差异等财务反例未过，不能转入下游。
- 首3个正式薄片及首个跨域财务薄片合入后分别重新量测执行、失败/取消、复核/返工、CI与迁移瓶颈，再更新区间；任何新增范围先独立规划。计划不是自动把proposed改ready的规则。
- 依赖安全P0：当前DRF3.16.1有5项open告警(3高/2中)，PH137将从现有安全发行包拆早期修复/兼容薄片，预留180–480分钟已含该包范围；不能只换版本或忽略API/PAT/解析行为。未开始依赖修改。
- 目标Linux/TLS/备份恢复、三客户业务周期和签收、发布授权、额度恢复等待均为未知，不填0假装完成；其包即使代码完成，现场门仍未通过。正式发布/生产部署/客户联络尚未授权。

实际交付物：本说明、62包估算和依赖/资源时钟JSON、观测证据及区间假设、外部门与重估机制。DoD是原图/源SHA一致、62唯一行、金额单位不混用、分角色求和一致、无环/资源无重叠/先后约束可复核、独立审阅与本次CI通过；不是产品已开发。

## PH137正式任务映射

62包保留原依赖与主观区间，55个正式实施/资格包细分为72个PH薄片，另从既有安全发行包提前拆出PH139，总计73个正式薄片。对应137条需求的唯一主责及辅助证据路径见requirement-trace.json；任务、前置、白名单和验收的执行许可只取可信BASE tasks。新增69个任务，复用PH001/002/003/026。最先合入规划基线，再完成PH139安全兼容，再按SDK、共同底座、财务闭环、工作台与开账、目标环境资格的依赖推进。

该拆分没有新增第63个产品包，原估算没有被宣称为73片的实测速率。细分会增加PR/CI与审阅次数，首三片及首个跨域财务片后按实测重估；额度和外部门等待仍未知。旧006–020、AI/资源和发行门、PH120–122保持原契约；新产品需求映射不自动完成这些旧任务。目标环境和三客户签收需要相应外部条件，不用合成演练替代客户验收。
