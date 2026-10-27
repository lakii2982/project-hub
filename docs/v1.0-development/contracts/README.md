# C01–C12字段与服务契约（PH-132候选）

本目录是目标规格，尚待本次独立复核、PR/当前CI合入冻结；不是产品功能已实现。原临时草案曾因URI失效、身份边界、来源种类和缺查询/事件定义被拒绝，主控保留原草案后接管修订。

## 唯一职责与入口

- `contract-catalog.json`：C01–12生产者/消费者、具名命令/查询/事件和概念关系目录；领域分组不等于每个对象同一App。
- `source-registry.json`：每个逻辑来源唯一namespace/kind/owner；`relationships.json`把每种关系指向唯一事实或投影，不授权再建一套桥表。
- `contract-bundle.schema.json`：整数分/分钟/日期、SourceRef、PolicyRef、客户计价和月度人工输入/快照等共享值类型。
- `entities.schema.json`：服务返回/正式实体与只读投影。`write-dtos.schema.json`为客户端业务字段；组织、审批人、状态及派生总额不能混入可编辑DTO。别名仅表达同一关系，不产生新表。
- `commands.schema.json` / `command-results.schema.json`：55个具名逻辑命令与闭合收据，含草稿保存和既有提交/复核适配原语；内部过账、核销编排和普通浏览器工作流的暴露边界各自声明。
- `queries.schema.json` / `query-results.schema.json`：25个跨域查询的具体选择器、结果、日期/分页与字段权限规格。域内Django表单/页面读取所属域实体与DTO；不允许其他域直接维护该表。
- `events.schema.json`：28个事件的具体topic、来源类型和不可变版本引用。事件不承载另一套可编辑金额，消费者查唯一owner；金融事实、审计、Outbox同事务，通知不补半笔账。
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

第一条实际执行9份schema的metaschema、所有本地$ref解析、137需求/对象与具名目录一致性；解析器禁止网络取schema。第二条用真实实例校核主要正例及31种拒绝，包括嵌套身份、错误来源、raw收入金额、float/bool、旧桥任意状态、缺查询选择器、未知账本/错误入账范围/伪造旧核算事件；并区分工资事实确认与正分母的正式项目分配。校核脚本在内存读取，不改仓库、不联网、不执行业务命令。最终数字和独立复核以本任务报告为准。
