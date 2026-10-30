# C01–C12字段与服务契约（PH-132基础与PH-137补齐）

本目录是目标规格，尚待本次独立复核、PR/当前CI合入冻结；不是产品功能已实现。原临时草案曾因URI失效、身份边界、来源种类和缺查询/事件定义被拒绝，主控保留原草案后接管修订。

## 唯一职责与入口

- `contract-catalog.json`：C01–12生产者/消费者、具名命令/查询/事件和概念关系目录；领域分组不等于每个对象同一App。
- `source-registry.json`：每个逻辑来源唯一namespace/kind/owner；`relationships.json`把每种关系指向唯一事实或投影，不授权再建一套桥表。
- `contract-bundle.schema.json`：整数分/分钟/日期、SourceRef、PolicyRef、客户计价和月度人工输入/快照等共享值类型。
- `entities.schema.json`：服务返回/正式实体与只读投影。`write-dtos.schema.json`为客户端业务字段；组织、审批人、状态及派生总额不能混入可编辑DTO。别名仅表达同一关系，不产生新表。
- `commands.schema.json` / `command-results.schema.json`：具名逻辑命令（准确数量由校核器输出）与闭合收据，含草稿保存和既有提交/复核适配原语；内部过账、核销编排和普通浏览器工作流的暴露边界各自声明。
- `queries.schema.json` / `query-results.schema.json`：25个跨域查询的具体选择器、结果、日期/分页与字段权限规格。域内Django表单/页面读取所属域实体与DTO；不允许其他域直接维护该表。
- `events.schema.json`：具名事件（准确数量由校核器输出）的具体topic、来源类型和不可变版本引用。事件不承载另一套可编辑金额，消费者查唯一owner；金融事实、审计、Outbox同事务，通知不补半笔账。
- `requirements-coverage.json`：137需求到实际具名实体/契约，标记待实现；产品方案SHA绑定，不把“有映射”当测试通过。

这些名称是逻辑服务契约，不自动新增HTTP/PAT/MCP工具、Django App、微服务或JSON Schema生产依赖。继续使用Django单体、固定依赖和已有资料/账本资产；运行实现可用现有表单/服务做等价确定性校验。首次SDK元数据与兼容薄片另由PH137规划，不把本目录当开工许可。

## 不可省略的服务边界

身份来自已认证上下文；所有来源先解析成同企业/项目/原始稳定ID与不可变版本，再用于规范化幂等摘要。旧数字ID选择器在适配层规范化到原PK，不因整数/字符串表示不同重复消费。SourceRef版本用于读取快照，业务消费键独立排除可变版本/调用渠道/请求key。

JSON Schema的default只是注释。缺dry_run由服务器明确选true再规范化；显式false仍须合法权限和业务状态。普通浏览器动作的审批与个人AI权限分开，不能因契约存在生成审批/支付/锁期工具。

使用本包StrictValidator对Python输入严格区分整数、bool和float，包括101.0也拒绝。标准JSON Schema对“整数值浮点”的定义不同；生产服务必须显式等价校验，不能只依赖Django/Python隐式数值转换。金额绝对值上限10^12分，分钟是整数；计算用Decimal、HALF_UP与批准组取整算法。

实体/草稿/投影区分：事实在所属域维护；读投影和派生余额没有直接写命令。草稿未完成的值保留缺项，不为凑正式实体schema填0；批准/过账前按所属域完整性和客户政策检查。改批准版本另起草稿；来源、账本、消费、核销、审计与冲销只追加有效历史。

价格、人工分配、角色/原子编排及升级的业务规格见[政策决定](../policy-decisions.md)、[旧新切写](../legacy-cutover.md)。其中服务不变式和x元数据是实现义务；schema通过不能证明数据库权限、累计上限、锁序、历史保真或生产并发。

## 实际校核方式

使用已有MCP固定环境（jsonschema4.26、referencing0.37），不新增产品依赖：

```sh
.agent-venv/bin/python -B docs/v1.0-development/contracts/validate_contracts.py
.agent-venv/bin/python -B docs/v1.0-development/contracts/check_contract_cases.py
```

第一条实际执行9份schema的metaschema、所有本地$ref解析、137需求/对象与具名目录一致性；解析器禁止网络取schema。第二条用真实实例校核主要正例及37种拒绝，包括嵌套身份、错误来源、raw收入金额、float/bool、旧桥任意状态、缺查询选择器、未知账本/错误入账范围/伪造旧核算事件；并区分工资事实确认与正分母的正式项目分配。校核脚本在内存读取，不改仓库、不联网、不执行业务命令。最终数字和独立复核以本任务报告为准。

## PH-137补齐与唯一数据归属

补充契约覆盖已存在产品需求中的现金草稿与更正、人工付款分类、账户间转账关联、银行手续费及净额到账结清、手工现金计划、发票逐行分配、交付确认和核对工作台。具体字段与消息是本目录schema，服务约束与设计反例见 `../promotion-draft/*-extension.plan.json`；这些文件不代表服务已经实现。`extension-freeze.json`绑定已物化schema与补丁输入，校核器会拒绝不匹配的哈希。

ManualCashPlan由cash唯一维护，与旧core.CashPlan是切写适配关系；不能出现两份可编辑计划。BankCharge由costing维护；复用已入账费用必须引用原来源并证明没有同源未清应付或另一次实际付款，未知覆盖阻断。ReceiptFeeSettlement由settlement记录非现金结清，现金只记录银行真实净到账；银行实际9800加非现金手续费结清200形成应收10000的清偿，成本200最多一次，收入不因结清重复产生。

发票分配与结算单、费用及银行费共享原始经济来源的额度，改变引用入口不能重复使用额度。票面税额与可抵扣税额分别检查，未知票面税保持未知；不可抵扣不等于票面税为0。核对查询只读，刷新与状态变更使用独立写命令；水位变化保留同一问题与历史差额，不能靠换来源版本删除未解决差异。

新增服务仍须通过公司锁、业务来源唯一键、期间/权限检查、幂等、追加冲销、审计与事件原子事务。结构正反例通过仅证明设计输入可校验，数据库并发、浏览器办理、真实目标环境与客户验收另行取证。正式开发的许可仅来自已合入可信BASE的tasks，而非本README或补丁文件。

## PH-210 单一冲销引用与原现金消费

AccountingEvent.reverses_event_ref是服务生成的单一可空原事件引用，非空只允许namespace=core/kind=accounting_event的不可变SourceRef；该字段server-only、readOnly，客户端命令与写DTO不接受。原事件不更新，只有新的完整冲销事件持有该引用。relationships.event-reversal仍归core/AccountingEvent/embedded_reference，其只读投影left=原事件、right=冲销事件，桥id/version/created_at从冲销事件派生，不新增第二个可编辑关系。同企业/授权/原版本/期间/依赖/完整冲销唯一性由后续服务与持久约束检查，readOnly与schema通过不等于服务已强制。旧账无AccountingEvent时走legacy adapter与原冲销服务，不合成历史事件。

SourceConsumption.consumer新增cash内部用途，原七用途保留。稳定来源是cash owner的原始CashTransaction：organization+consumer=cash+source.namespace/kind/id+coverage_key=bank_effect唯一，source.version只读快照，不含请求键/渠道/分类ID；该用途不是clearance核销，项目分配不能重记银行流。原bank_effect claim永久保留，分类/分配及其撤销不释放它、也不产生银行流或会计事件。真实现金更正遵守bank-entry-extension.plan.json#/service_semantics/correction：先拒绝有效依赖、由各owner先撤销且不级联，反向全部当前有效posting并保留book/account/source，再追加完整正额replacement（两个零成本/收入事件）；原CashTransaction ID/原行/external_source_key/银行provenance不变且更正链不分支，按批准请求与有效predecessor唯一消费，不提供仅写差额或释放原防重再普通过账。

以上仅为设计与严格schema反例；没有来源resolver、批准/期间/模型事务或真实服务拒绝，不改变现金/成本/收入口径，也不是客户或生产验收。旧99正8负夹具、原19正37负契约案例与137需求保持，PH210新增边界案例单列。
