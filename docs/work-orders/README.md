# 工作单总览与依赖

任务ID：PMO-ENGINEERING-01
日期：2026-10-22
规则：工作单的字段与验收遵循 `../development-contract.md`。WO-01 至 WO-09 已列出领域规则与反例，尚须在 WO-14 之后冻结插件文件范围和接口，才能发给执行者。共享接线文件由主代理集成，执行者不改。含迁移的工作单串行集成。不做虚估排期，只给依赖与先后。

## Backlog

| 编号 | 目标 | 级别 | 依赖 | 状态 |
| --- | --- | --- | --- | --- |
| WO-01 | 正式合同、合同明细与变更单 | S | 无 | 待执行 |
| WO-02 | 结算项、应收应付未清项与核销 | S | WO-01 | 待执行 |
| WO-03 | 预收预付与退款 | S | WO-02 | 待执行 |
| WO-04 | 共享费用池与 A04 最大余数分摊 | S | 无 | 待执行 |
| WO-05 | 月度人工成本池、8 位费率快照与组守恒 | S | WO-04 | 待执行 |
| WO-06 | 资源周工时 | B | 无 | 待执行 |
| WO-07 | 发票登记、分配与红冲 | B（税额分配按 S 复核） | WO-02 | 待执行 |
| WO-08 | 期初数据与启用向导 | S | WO-02、WO-03 | 待执行 |
| WO-09 | 通知与后台任务 | A | 无 | 待执行 |
| WO-10 | 生产 PG 并发、锁账与备份恢复验证 | S | WO-02、WO-03、WO-05 | 待执行 |
| WO-11 | api/v1 与个人令牌 | S | — | 已完成：core/api.py、core/agent_auth.py、迁移 0003，tests/test_agent_api.py 17 项通过（SQLite，2026-10-22） |
| WO-12 | MCP stdio 适配器 | S | WO-11 | 已完成：integrations/mcp/，test_server.py 8 项由主代理跑通；未做真实客户端联调 |
| WO-13 | 插件 kernel、outbox 与叶插件 | S | 无 | 已完成（骨架）：core/plugin_runtime.py（pluggy）、迁移 0004、pmo_plugins/ 三个叶插件，tests/test_plugins.py 13 项通过；core 业务模块未全部插件化 |
| WO-14 | 业务插件 SDK、统一入口 guard 与渐进抽离 | S | 现有插件骨架 | 待执行；先于 WO-01 至 WO-09 的新业务实现，见 [工作单](WO-14-domain-plugin-sdk.md) |

## 执行顺序

1. 先执行 WO-14，将文件所有权与入口契约冻结到独立业务插件；WO-01 至 WO-09 当前的 core 文件范围是验证版接线位置，开工前按 [迁移设计](../plugin-evolution.md) 转为各插件目录，不继续扩张 core。
2. SDK 通过后，WO-01、WO-04、WO-06、WO-09 可在互斥目录并行。迁移串行集成，编号按实际叶节点生成，不预占编号。
3. 主线财务链：WO-01 → WO-02 → WO-03 → WO-08；WO-04 → WO-05；WO-02 → WO-07。
4. WO-10 在最后，依赖主线财务对象就位后做 PG 并发与恢复验证；现有版本的 PG 基线可提前验证。

## 交付 JSON schema（每张工作单统一）

```json
{
  "work_order_id": "WO-XX",
  "status": "done | blocked",
  "files_changed": ["相对路径"],
  "tests_added": ["tests/test_x.py::ClassName::test_method"],
  "acceptance": [{"cmd": "完整命令", "result": "pass | fail", "output_tail": "末尾输出摘录"}],
  "deviations": ["与工作单或契约的偏差及原因，无则为空数组"],
  "unverified": ["未验证事项，无则为空数组"],
  "blocker": "status 为 blocked 时填写具体阻塞"
}
```

主代理先校验 schema 字段与类型，再核对文件差异与测试真实性；acceptance 必须真实运行，虚构输出按 development-contract 第 4 节处理。

