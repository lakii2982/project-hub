# 1.0 工作包与开发顺序索引

状态：设计候选；所有包均不可直接派遣。正式PH及其字段/白名单/固定验收须经规划冻结。详见[入口](README.md)、[结构化工作包](work-packages.json)和[需求追踪](requirement-trace.json)。

下表按依赖拓扑排序；阶段是读图标签，实际派遣还须满足政策、环境、可信BASE及运行范围。7个PRE包先设计产物、再正式冻结，55个其他包覆盖实施、验证与交付；不表示62次现成编码调用。

## S0

| 工作包 | 目标 | 主需求数 | 直接依赖 |
| --- | --- | --- | --- |
| PRE-BASELINE | 基线、授权和当轮执行/环境能力 | 0 | 外部前提：正式授权准备任务 |
| PRE-POLICY | 产品政策、客户配置与范围决策 | 0 | PRE-BASELINE |
| PRE-CONTRACTS | 跨域字段、接口、锁和迁移契约 | 0 | PRE-BASELINE、PRE-POLICY |
| PRE-UX | 角色路径、三模板与界面契约 | 0 | PRE-POLICY、PRE-CONTRACTS |
| PRE-FIXTURES | 黄金企业夹具、旧库构建与预期 | 0 | PRE-CONTRACTS |
| PRE-ESTIMATE | 关键路径、验证容量与工期区间 | 0 | PRE-BASELINE、PRE-CONTRACTS、PRE-UX、PRE-FIXTURES |
| PRE-GOVERNANCE | 将准备和实施拆成正式PH基线 | 0 | PRE-ESTIMATE |

## S1

| 工作包 | 目标 | 主需求数 | 直接依赖 |
| --- | --- | --- | --- |
| WP-SDK | 最小SDK注册、契约查询与全入口门控 | 0 | PRE-GOVERNANCE |
| WP-CORE | 来源、期间与过账统一基础 | 0 | WP-SDK |
| WP-IDENTITY | 账号生命周期、授权与敏感字段 | 6 | WP-CORE |
| WP-PEOPLE | 员工、账号绑定与共同字典 | 3 | WP-IDENTITY |
| WP-PARTY | 统一往来方、联系人和去重 | 3 | WP-CORE |

## S2

| 工作包 | 目标 | 主需求数 | 直接依赖 |
| --- | --- | --- | --- |
| WP-PROJECT | 项目主档、成员与基础状态 | 5 | WP-PEOPLE、WP-PARTY |
| WP-DELIVERY | 交付节点、履约证据与风险 | 3 | WP-PROJECT |
| WP-CONTRACT | 合同、收费明细与生效方法 | 6 | WP-PROJECT、WP-PARTY |
| WP-CHANGE | 合同变化、终止和关联视图 | 2 | WP-CONTRACT、WP-DELIVERY |
| WP-TIME-INPUT | 项目/非项目投入与周填报 | 4 | WP-PEOPLE、WP-PROJECT、WP-DELIVERY |
| WP-TIME-REVIEW | 投入审批与缺项处理 | 3 | WP-TIME-INPUT |
| WP-BILLABLE | 客户可计费投入确认 | 1 | WP-TIME-REVIEW、WP-CONTRACT |

## S3

| 工作包 | 目标 | 主需求数 | 直接依赖 |
| --- | --- | --- | --- |
| WP-AR | 客户结算、未清、账龄与质保 | 4 | WP-CONTRACT、WP-DELIVERY、WP-CORE |
| WP-AP | 供应商及员工费用义务接口 | 1 | WP-CONTRACT、WP-PEOPLE、WP-CORE |
| WP-CREDIT | 折让、减免、坏账与信用更正 | 1 | WP-AR、WP-AP |
| WP-LABOR-POLICY | 工资池、有效产能与费率版本 | 4 | WP-TIME-REVIEW、WP-CORE |
| WP-LABOR-POST | 人工分配、差额和开放期调整 | 3 | WP-LABOR-POLICY |
| WP-EXPENSE | 报销费用、发生确认与待定税额 | 3 | WP-AP、WP-CORE |
| WP-SPEND | 外包采购承诺与验收成本 | 3 | WP-AP、WP-CONTRACT、WP-TIME-INPUT |
| WP-SPEND-CORRECTION | 暂估转正式、退货折让和支出更正 | 2 | WP-EXPENSE、WP-SPEND、WP-CREDIT |
| WP-SHARED | 共享费用分配与重分配 | 4 | WP-EXPENSE、WP-TIME-REVIEW |
| WP-REVENUE-FIXED | 固定价履约收入和累计调整 | 3 | WP-CONTRACT、WP-DELIVERY、WP-CORE |
| WP-REVENUE-SERVICE | 工时及固定服务期收入 | 1 | WP-REVENUE-FIXED、WP-BILLABLE |
| WP-REVENUE-PAIR | 收入和结算配对及结果查询 | 3 | WP-REVENUE-SERVICE、WP-AR、WP-EXPENSE、WP-LABOR-POST |

## S4

| 工作包 | 目标 | 主需求数 | 直接依赖 |
| --- | --- | --- | --- |
| WP-BANK | 账户、银行事实和待匹配 | 4 | WP-AR、WP-AP、WP-IDENTITY |
| WP-CLEARING | 现金核销、预收预付和抵扣 | 5 | WP-BANK、WP-AR、WP-AP |
| WP-REFUND | 退款、退票与撤核恢复 | 2 | WP-CLEARING、WP-CREDIT、WP-SPEND-CORRECTION |
| WP-PAYROLL-CASH | 工资社保税费和公司现金 | 2 | WP-BANK、WP-LABOR-POST |
| WP-FEE-TRANSFER | 手续费和账户内部互转 | 2 | WP-BANK |
| WP-EMPLOYEE-ADVANCE | 员工借支及报销抵扣 | 1 | WP-CLEARING、WP-EXPENSE、WP-PEOPLE |
| WP-PREPAID-SERVICE | 跨期预付服务逐期耗用 | 1 | WP-CLEARING、WP-SPEND |
| WP-INVOICE | 发票、分配、红冲与税额 | 3 | WP-AR、WP-AP、WP-SPEND-CORRECTION |

## S5

| 工作包 | 目标 | 主需求数 | 直接依赖 |
| --- | --- | --- | --- |
| WP-BUDGET | 预算编制及批准版本 | 2 | WP-CONTRACT、WP-SPEND、WP-LABOR-POLICY |
| WP-ETC | 剩余预计与实际覆盖释放 | 5 | WP-BUDGET、WP-SPEND-CORRECTION、WP-LABOR-POST、WP-PREPAID-SERVICE |
| WP-FORECAST | 来源升级、13周与资金覆盖 | 3 | WP-CLEARING、WP-REFUND、WP-PAYROLL-CASH、WP-FEE-TRANSFER |
| WP-CLOSE | 期间预检、锁定与更正 | 4 | WP-CORE、WP-LABOR-POST、WP-SHARED、WP-REFUND、WP-REVENUE-PAIR、WP-INVOICE |
| WP-RECONCILE | 工资、成本、银行和收入核对 | 4 | WP-CLOSE、WP-FORECAST、WP-EMPLOYEE-ADVANCE、WP-PREPAID-SERVICE |
| WP-INSIGHT-METRICS | 统一经营指标和完工结果 | 4 | WP-RECONCILE、WP-ETC、WP-REVENUE-PAIR |
| WP-NOTIFY | 到期扫描与站内待办 | 1 | WP-FORECAST、WP-RECONCILE、WP-CHANGE |
| WP-WORKSPACES | 四职责工作台和跨模块导航 | 4 | WP-INSIGHT-METRICS、WP-NOTIFY |
| WP-PROJECT-CLOSE | 项目聚合、结项归档及迟到款 | 3 | WP-WORKSPACES、WP-CLOSE、WP-CLEARING、WP-ETC |
| WP-EXPORT | 授权导出与核对底稿 | 1 | WP-RECONCILE、WP-INSIGHT-METRICS |
| WP-UI-ACCEPTANCE | 全站统一及真实可访问办理 | 1 | WP-WORKSPACES、WP-PROJECT-CLOSE、WP-EXPORT |

## S6

| 工作包 | 目标 | 主需求数 | 直接依赖 |
| --- | --- | --- | --- |
| WP-IMPORT | 全域模板、预览与幂等原子导入 | 2 | WP-RECONCILE、WP-PROJECT-CLOSE |
| WP-OPENING | 期初承接、明细覆盖和开账核对 | 4 | WP-IMPORT、WP-RECONCILE |
| WP-ONBOARDING | 首公司向导、首项目和自助手册 | 3 | WP-OPENING、WP-UI-ACCEPTANCE |
| WP-INSTALL | Linux安装、失败诊断和健康可见 | 2 | WP-SDK、WP-CORE |
| WP-TLS | 真实域名HTTPS和安全边界 | 1 | WP-INSTALL |
| WP-UPGRADE-RESTORE | 基线升级与非空附件联合恢复 | 2 | WP-INSTALL、WP-OPENING、WP-ONBOARDING |
| WP-PG-CONCURRENCY | 跨域PG竞争与最终完整性 | 1 | WP-CLOSE、WP-OPENING、WP-REFUND、WP-SHARED |
| WP-PERFORMANCE | 容量与首用操作任务验证 | 0 | WP-ONBOARDING、WP-UI-ACCEPTANCE、WP-PG-CONCURRENCY |
| WP-SUPPORT | 客户支持、数据退出和授权排障 | 1 | WP-ONBOARDING、WP-UPGRADE-RESTORE |

## S7

| 工作包 | 目标 | 主需求数 | 直接依赖 |
| --- | --- | --- | --- |
| WP-RELEASE-PACKAGE | 版本化发行资产与安全清单 | 1 | WP-TLS、WP-UPGRADE-RESTORE、WP-PG-CONCURRENCY、WP-PERFORMANCE、WP-SUPPORT、WP-INVOICE、WP-REVENUE-PAIR |
| WP-CUSTOMER-UAT | 三模板客户周期与独立使用验收 | 0 | WP-RELEASE-PACKAGE、WP-PROJECT-CLOSE、WP-NOTIFY、WP-EXPORT、WP-EMPLOYEE-ADVANCE |
| WP-FINAL-RELEASE | 全部需求证据核对与正式发行收口 | 0 | WP-CUSTOMER-UAT |

## 拆为PH后的派遣要求

每个包由主控根据模型/服务/入口和迁移成本拆成薄片PH，一个PR对应一个PH，禁止将多域大包整份交给执行者临场决定schema。来源/事务/锁/政策与旧新映射在契约中先冻结。共用域或schema的包即使文件不同，也按语义互斥组串行集成。

PRE-GOVERNANCE是准备结果的正式发布/任务晋级收口。准备工作的最初授权由PH-123（PR69）独立规划承担，见入口和决策条件；不能等PRE包都执行完才补授权，也不能把PRE-GOVERNANCE当作已存在的PH。
