# ADR 0009：客户结算首片采用条款行组合结算

状态：PH-223 独立规划候选，未实现、未验证。

PH-155/156 要求从履约/验收与条款发起客户结算、拆分到期与质保未清、按基准日只读账龄。当前已具备批准合同（含 PaymentTerm/WarrantyTerm 真实行与 1000 项上限校验）、固定节点验收、服务期月收入与锁期规则；缺少 settlement 写 owner。

选择：一次结算覆盖 1..n 个**完整、未使用过**的批准条款行（PaymentTerm 到期款、WarrantyTerm 质保款），金额只由条款 gross 派生，客户端不得传金额；net/tax 按整行累计比例 HALF_UP（net_cum(gross_cum)=HALF_UP(line.net*gross_cum/line.gross)，本笔=新增累计−既有累计，tax=gross−net），尾差自然归最后一笔；条款行只能整行使用，不能取条款金额的一部分。可结算性门槛：该明细已有真实已确认履约，且本次净额≤已确认履约净额−已结算净额；未达条件仅草稿。批准时一次性建全部分区 OpenItem 并每分区发一次已登记 canonical settlement.open_item_created.v1。

理由：fixture 金样例 UAT-03-S03 明确 50%（net50000/gross53000）、UAT-10 明确 50%+40%+10%（90000/95400+质保10000/10600），"整行完整结算"会直接否掉这些验收；而让客户端填金额或按比例猜分会制造第二套可编辑余额。条款行组合既保证金额可核对（金额来自批准条款）、又支持真实分期，同时条款一次性消费天然防重复结算。

放弃的备选：整行完整结算（否掉 UAT-03/部分结算验收）；按履约来源金额结算（需未冻结的分区分配口径）；客户端传金额/比例（任意分配，违反 open_item.create 继承约束）；把账龄写成 AgingCase 余额表（会变成第二份可编辑余额，本片改为 OpenItem 只读派生）；本片提供生效结算反冲（无核销模型时会产生不可解释的负未清，纠错留待核销/折让薄片并在 spec 披露）。

只读跨 owner：contracts 仅新增 approved_terms_snapshot、delivery 仅新增 confirmed_line_performance，均为具名只读，不在本片复用可变调用方输入；两处均为 root 单写并在白名单中列明（旧 PH-155/156 白名单缺少它们与并发测试文件，本片显式修订，不自我扩权）。应收不生成管理收入、成本、现金或账本事实；锁期/关期沿用公司锁与期间检查。

公共 schema 不改：Settlement/OpenItem/AgingCase 与 settlement 命令/查询/事件已冻结，open_item.create 为 internal_service_only；本片只实现服务端校验与已登记 canonical open_item_created 事件。AgingCase 不写。

后续：核销/抵扣、折让/坏账、应付、发票、争议延期、生效结算反冲与期初未清由后续规划登记；本文不授权 proposed 原任务，不声称生产或完整 1.0 完成。
