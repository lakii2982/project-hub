# 候选验收清单（一页）

更新：2026-11-03。每个候选版本（每两周一次）用本文件验收一次；只写真实结果，
未执行项写 `not_run` 或 `blocked` 并写清前提，禁止用文档/配置/本地 SQLite 冒充通过。
1.0 范围见 [docs/product-scope.md](../docs/product-scope.md)（小微企业简化版：项目收益 + 现金流），
队列与顺序见 [QUEUE.md](QUEUE.md)，现状见 [STATUS.md](STATUS.md)。

## 模板（复制到"当前候选"段使用）

```text
候选：<版本/分支> · SHA：<sha> · 环境：<真实环境名> · 日期：<YYYY-MM-DD> · 复核人：<name>

| # | 验收项（连到 Q-nn） | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | <用户可完成的动作> | pass/not_run/blocked | <截图/命令/CI run URL/步骤> |

必需检查：9 项全绿 <是/否 + run URL>
独立复核：<通过/未通过 + HEAD SHA>
未验证：<逐项写前提与解除条件>
剩余风险：<风险与需要谁决定>
```

判定规则：

- `pass` 只在有真实执行证据时写；浏览器项必须有 1440/390 截图或录屏与步骤；命令项必须有命令、输出与退出码。
- L3（CI/PostgreSQL）、L4（浏览器）、L5（真实环境/客户）分层记录，低层不冒充高层；SQLite 不冒充 PostgreSQL 并发。
- 缺项显示缺口；不得把 `not_run` 写成 pass，不得以"配置存在"替代运行证据。
- 执行人不得自批；候选验收结论与独立复核分开记录。小微版不要求客户 UAT/三方签批作为发布前置（DECISIONS D-12）。

## 当前候选

候选：小微版 `0.2.x` 预发布（尚未建立）。SHA：待定。环境：待定。日期：待定。

当前状态（按 [QUEUE.md](QUEUE.md) W1–W3，沿用 STATUS.md 的边界，不重复推断）：

| # | 验收项 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 范围/设计/任务与队列重排（Q-31） | not_run | 本 PR 的必需 CI 与独立复核（合并后回填 run URL） |
| 2 | 小微版入口收口（Q-32） | not_run | 待实现 |
| 3 | 老板工作台：收益 + 现金一屏（Q-33） | not_run | 待实现 |
| 4 | 项目页：合同额→开票→收款→成本→毛利（Q-34） | not_run | 待实现 |
| 5 | 工时与自动人工成本（Q-35） | pass | `python manage.py test tests.test_weekly_time`（13 tests OK，费率快照/更正/幂等/缺费率/权限/锁期/1440 分钟反例）；`python manage.py verify_integrity --json` exit 0（演练库 47 条账目）；`manage.py seed_demo` 后 Chromium 1440/390 浏览器证据 39/39 通过：周填报→更正→重复提交幂等、失败保留已填数据、缺费率进入工作台缺口清单、补录费率后自动定价；截图 `docs/screenshots/q35-weekly-1440.png`、`q35-weekly-390.png`、`q35-gap-1440.png`、`q35-gap-390.png`、`q35-project-cost-1440.png` |
| 6 | 费用/外包成本确认（Q-36） | not_run | 待实现 |
| 7 | 极简开票与收款归集（Q-37） | not_run | 待实现 |
| 8 | 现金账户与 8 周计划（Q-38） | not_run | 待实现 |
| 9 | 一条命令演示环境（Q-39） | not_run | `scripts/demo.py` 未实现 |
| 10 | 首次启用向导（Q-40） | not_run | 待实现 |
| 11 | 简化导出与提醒（Q-41） | not_run | 待实现 |
| 12 | 浏览器端到端验收（Q-42） | not_run | 待实现 |
| 13 | 安装与备份恢复底线（Q-43） | not_run | 待实测 |
| 14 | 候选与发布包（Q-44） | not_run | 未建立候选 |
| 15 | L5 真实环境走查 | blocked | 真实域名/PG 环境未就绪（P-02） |

未验证：L3 CI/PG、L4 浏览器走查、L5 真实环境均未执行；下一候选建立后逐项回填真实结果。

剩余风险：发布资格仍 blocked；备份恢复、真实域名 HTTPS 与浏览器验收未实测；P-01/P-02 待确认。
小微版口径已由负责人确认（DECISIONS D-11）。
