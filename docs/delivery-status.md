# 交付状态：现状头

口径：本头只做能力概览；逐任务条目与完整历史快照在 [delivery-status-archive.md](delivery-status-archive.md)（append-only，不改写），新条目只进归档，本头仅在能力变化时重写。验证明细见 [verification.md](verification.md) 与 `project/reports/`；`product-requirements.md`、`accounting-rules.md` 是目标设计，多数未实现。任务状态唯一来源是 `project/tasks.json`（用 `projectctl brief <id>` 查询）。

## 当前能力（2026-10-30，依据最新main与当次CI）

- 版本：0.1.0 工程验证版已发布；main 在 v0.2 基础产品化与 1.0 现金/应收薄片迭代。
- 已实现：项目工作流（8阶段状态机、蓝图、关键任务）、成本/现金分账（工时与六类单据审批入账、费率快照、幂等token、负值冲销）、审批审计、导入导出、个人API/MCP、插件运行时与三叶插件；前端六页签与统一蓝白视觉（PH-022至025）；四CLI交付闭环演练（PH-100至108）。现金/应收薄片：现金事实、posted过账、客户结算与未清/账龄、服务期与管理收入已合入，核销首片（部分核销/撤核，PH-232）已实现。
- 未实现：全站界面统一与经营趋势（PH-026）、正式合同、应收应付全量、预收预付/退款、发票、多币种、共享分摊、月度人工池、期初、通知中心；core 未完全插件化。

## 验证边界

CI 已覆盖 SQLite/PG 回归、迁移、append-only、MCP、容器恢复、CodeQL、治理门禁；未验证真实TLS、锁账PG并发、非空附件恢复后检查、真实模型与个人客户端联调。CI通过不等于生产验收。

## 交付效率改造（2026-10-30 起）

会话开场 `projectctl orient`、任务上下文 `brief <id>`、报告起草 `draft-report <id>`；本文件、`project/tasks.json`、`docs/v1.0-development/work-packages.json` 禁止整文件读入。量化基线与复测口径见归档 PH-233 条目。
