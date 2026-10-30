# 本轮开发准备设计校核记录

日期：2026-10-27。范围：docs/v1.0-development以及上一轮1.0方案的开发准备入口链接。代码基线仍为119a049；没有恢复产品实现、调整任务状态或发布。

## 1. 实际执行的检查

| 检查 | 实际结果 | 能证明什么 |
| --- | --- | --- |
| `.venv/bin/python docs/v1.0-development/check-design.py` | pass；62工作包/7准备/55实施验证收口，137需求，18UAT，dispatch_authorized=false | 原文快照一致、每需求唯一主包、DAG无环、全部包纳入最终依赖闭包、UAT双向引用、本地链接/表格/围栏有效 |
| `.venv/bin/python scripts/projectctl.py check` | ok=true，task_count=45 | 既有任务契约检查通过；未增加或晋级1.0任务 |
| `.venv/bin/python scripts/projectctl.py next`、`brief PH-097` | 实际读取；PH-003/005/026/097仍ready，PH-097为规划入口 | 现有元数据尚未承接新1.0，不能由设计包直接开工 |
| `.venv/bin/python -m unittest tests.test_projectctl tests.test_governance -v` | 19项通过，2.382秒 | 现有治理回归通过，不代表新增设计已接入机器门禁 |
| 受保护原文件SHA256前后比对 | 五份内容一致 | 本轮未覆盖用户规范修改，也未改任务/manifest/发布门 |

结构检查另外核对：PRE包不以自身未来schema作为进入前提，跨域验证根据生产消费契约适用，全部设计包PH引用为空且不可派遣。没有把需求/UAT已映射写成业务测试已通过。

## 2. 独立审阅与修订

由独立上下文审阅者完成只读审查，另两名只读代理分别检查既有治理和跨域契约缺口。审查没有参与本轮文档写入。

初审发现并修正：

1. 准备包进入条件原来沿用实现包要求，导致政策/schema准备自我阻断。现明确只要求既有准备任务输入/范围/固定验收冻结，待产出政策/schema放在退出条件。
2. 跨域验证原来依据拥有域数量推断，误将外包、收入配对、期初和PG集成专项标false。现依据声明的生产消费契约，指定包及全目录规则已复查。

限定复查通过。只读设计校核脚本亦经独立检查并实际运行PASS，未发现写文件、联网、进程执行、派遣或授权旁路。此结论限于本轮准备设计，不证明任何产品功能、环境或客户结果。

## 3. 保留的原工作区内容

本轮开始即存在AGENTS.md及docs/engineering/agent-protocol.md未提交修改；两者保持逐字节不变。project/tasks.json、project/manifest.json、project/release-readiness.json保持逐字节不变。上一轮docs/v1.0-delivery-plan.md仅追加开发准备入口章节；其137需求/18场景未改，追踪快照已按新文档哈希绑定。原简化清单保留。

## 4. 明确未执行

没有创建/合并PR、修改远端保护或标签、执行产品代码实现/迁移、全量业务回归、新CLI派遣/取消演练、PG多连接、浏览器、Linux/TLS、附件恢复、负载、客户访谈或业务签收。当前未对远端最新SHA/CI重新作网络查询；基线引用来自本地冻结记录和Git状态，不新增远端运行声明。

当前完成的是G0设计交付；G1/G2所需的正式准备PH、政策/字段schema冻结、真实执行环境演练和可信BASE晋级仍按决策与开工条件执行。代码冻结与现有治理继续有效。

## 2026-10-28 PH-130准备设计入库

纳入1.0交付方案、简表和长程设计：137需求/62工作包/18UAT。实际只读结构检查、五种拒绝及19治理回归通过；独立复核修正临时加载缓存和政策状态后通过。设计覆盖不是产品实现或客户验收。PH131成为下一准备任务，当前PR的8项required CI仍须合入前核对；产品代码保持119a049，未发布/部署。

## PH-210 单一冲销引用与原现金消费（2026-10-30）

基线7a2cc17。只改canonical契约：AccountingEvent.reverses_event_ref为可选、server-only、readOnly，非空仅允许namespace=core/kind=accounting_event的SourceRef，保持additionalProperties=false；SourceConsumption.consumer追加cash并保留原七用途；relationships.event-reversal补只读桥投影left=原事件/right=冲销事件，桥id/version/created_at取自冲销事件；commands客户端append/reverse DTO不放开新字段；source-registry只加内部用途映射注解（objects身份清单不变）。README/摘要/反例同步，未新增对象/命令/事件/金额字段或弱化旧约束。

固定jsonschema4.26离线环境实际执行：prep 7测试5.158s全执行0skip（含新增边界6正10拒绝），原19正37拒绝、99正8负、137需求与全部契约/来源归属保留；validate_contracts、check_contract_cases、check_fixture通过；projectctl check与23项治理测试通过。fixture仅刷新5个实际canonical摘要；extension-freeze保留原base_sha/201补丁/input_sha256/旧审阅历史，追加PH210前后SHA与本次设计验证，不把旧pass改写为新SHA通过。

边界仅证明设计输入可校验：未运行来源resolver、批准/期间/模型事务、真实服务拒绝、数据库、PG并发、浏览器/客户/生产；现金/成本/收入口径未改，141及后续实现仍待。

独立S复核（fresh context）实际重跑全部6条BASE验收命令与严格环境prep（7 tests 0 skip），PASS 0阻断；按复核跟进更新freeze的independent字段并记录BASE已有可选readOnly字段、本片为收紧的措辞说明。


## PH-243 分类桥接 canonical 契约修正（2026-10-30）

基线 e91346a（PH-242 合入）。仅契约/测试/规划，无业务代码/迁移。events.schema.json 两处 classification 事件 posting_event_refs 改 1/1、items 绑定 core/accounting_event（reversed namespace cash→core）；其余 5 个 0/0 cash 事件与全部其它 cash 事件未动；entities SourceConsumption invariants[5] 逐字替换为冻结句（有效组/镜像冲销/重分类复用 claim/allocation-release 零账本/永不释放 claim）；catalog-completeness LedgerPosting 仅补注解；extension-freeze 仅更新 3 个受影响 canonical 摘要并显式 supersede cash-extension.plan.json#/service_semantics/classification_reverse/4，保 base_sha/201/input_sha256/历史；fixture 仅更新 3 个 contract_artifact_sha256。实际执行（严格 .agent-venv，jsonschema 4.26/referencing）：validate_contracts pass（schemas=9 refs=2977 events=47，old positive 19/rejected 37/fixture positive 99/negative 8）；check_contract_cases pass（19/37）；check_fixture pass（99/8，errors=[]，3 个 frozen hash 通过）；PH135_REQUIRE_SCHEMAS=1 unittest tests.test_v1_preparation → 8 tests OK 0 skipped（PH243 positive=3 rejected=9）。产品 venv 同一 prep 测试 8 tests OK skipped=5（schema 用例按设计跳过，不作通过证据）。后续：PH-239 已 ready（依赖 PH-210/PH-230/PH-243 done），其实现须生成 core 0009 并改造 verify_integrity 现金账断言；真实 PG/浏览器/生产未验证。

### PH-243 独立复核与最终证据（补记）

独立 S 复核（fresh context）PASS，0 阻断；复核以 6209321 为审查对象，逐项验证 events 仅两处改动、entities invariants[5] 与 spec 冻结句逐字相等、catalog 仅注解、fixture 仅 3 个摘要、extension-freeze 保留 base_sha/201/input_sha256/历史并含 supersede 记录、原 19/37 与 99/8 计数不变。最终证据 HEAD 复跑：`projectctl.py check` ok=true/task_count=169；`verify-change --base e91346a --task PH-243` ok=true、13 文件全在白名单；严格环境 `.agent-venv` + `PH135_REQUIRE_SCHEMAS=1` 的 tests.test_v1_preparation 仍为 8 tests OK 0 skipped（PH243 positive=3/rejected=9），validate_contracts/check_contract_cases/check_fixture 计数与上文一致。residual（非阻断）：PH-243 spec 正文“PH-247 晋级 PH-239”措辞与实际（PH-239 已 ready）不一致，由 PH-247 规划槽消解；handoff 已记录。

## PH-239 分类桥接运行时验证（2026-10-30）

基线 a760cf7（main 含 PH-240/244/246；PH-243 冻结契约）。实现落账：每个有效分类恰一 AccountingEvent（cash.classification，source=分类 ref，occurred_on=流水日期）+ 恰一 LedgerPosting（cash_in/out、funding_account scope、+gross）；撤分类追加镜像事件（reverses_event_ref）+ 负值 posting（reverses_posting_id），原记录不变；bank_effect claim 每流水唯一永久、净额归零后复用；canonical recorded/reversed posting_event_refs 恰 1 个 core/accounting_event；core 0009 以原名放宽 labor_posting_cost_scope（cost↔project/nonproject、cash_in/out↔funding_account/legacy_project，origin/current+金额边界保留）；cash 迁移因 PH-240 先合入顺延为 0005_cash_classification_guards。实际执行（最终 HEAD）：scoped 29 项（26 pass+3 PG-only skip，含复核阻断修复回归）；全量 822 项（752 pass+62 skip+8 项既有 macOS 环境失败，无新增）；manage.py check/makemigrations/verify_integrity 通过；projectctl check ok=true/task_count=169。未验证：PG 并发（CI）、浏览器、独立复核、生产/UAT。
