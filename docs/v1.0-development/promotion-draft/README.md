# PH-137规划收口材料

本目录保留SDK元数据规格、范围补齐的设计补丁、结构样例及固定哈希。正式任务许可来自合入后的 `project/tasks.json`，不得凭目录存在执行任务。

- `sdk1-freeze-candidate.md`和对应schema/example明确SDK1六字段domain_metadata与既有八字段manifest的边界；SDK1/2分片，复用既有插件资产。
- `security-first-candidate.md`对应首个P0安全兼容薄片PH139；其实施仍须可信BASE ready。
- `*-extension.plan.json`是本轮设计补齐与服务不变式；canonical schemas为字段唯一入口，`extension-freeze.json`校核二者一致。JSON样例不是数据库行为测试。
- `slice-inventory.json`及正式 `../implementation-plan.json`记录来源和PH映射。需求主责以 `../requirement-trace.json`关联可信tasks，不存在另一套运行调度状态。
- 原生渠道曾产出未通过验收及超时草稿，`native-acceptance.md`与`salvage-record.json`记录限制。候选草稿只能作来源，错误或GAP描述不能覆盖冻结字段和正式spec。历史产物未被当作成功实现。

当前仍是规划审查候选；产品代码、运行门控、生产并发、新财务结果与客户验收均未因此通过。独立复核、当前CI与正常合入是本任务收口条件。
