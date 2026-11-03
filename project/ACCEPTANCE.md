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
| 3 | 老板工作台：收益 + 现金一屏（Q-33） | pass | `python manage.py test tests.test_q33_dashboard`（17 tests OK：含税发票净额回归、净额为 0 的未开票边界、Q-36 付款不计入历史专业付款、简化成本/历史专业成本缺口、开票/收款口径切换、未收款=已开票−已收款、账户余额=期初+已收−退款、毛利排行与无定义毛利率、缺费率/未开票/无收款缺口、PM 账户余额与 /billing/ 一致、成员/finance/auditor 403、跨租户隔离、只读零写）；`seed_demo` 后 Chromium 1440/390/键盘证据：本月开票不含税 ¥205,811.32（价税合计 ¥216,000）、已收 ¥91,000、已支出 ¥7,000（仅 Q-36 付款）、未收款 ¥130,000（含税现金）、账户余额 ¥171,000（累计付款 ¥17,000 未关联账户缺口）、历史专业成本 ¥67,560/3 个项目只读未计入 1.0 毛利，历史专业收款 2 笔/付款 3 笔只读；排行与 billing/expenses 聚合逐项一致；截图 `docs/screenshots/q33-after-owner-1440.png`、`q33-after-owner-390.png`、`q33-after-owner-390-nav.png`、`q33-after-owner-receipt-1440.png`，Before 对照 `q33-before-*`（main 9daa3fc） |
| 4 | 项目页：合同额→开票→收款→成本→毛利（Q-34） | pass | `python manage.py test tests.test_q34_project_finance`（15 tests OK：与 Q-33 `business_dashboard` 同项目收入/1.0 成本/毛利/未收款/现金净额/历史成本缺口逐项一致，seed_demo 后 PH-26001 毛利 ¥84,527.55=收入 ¥113,207.55−Q-35/Q-36 成本 ¥28,680，历史专业成本 ¥19,280 单列；合同额追加保留历史与立项预估、缺费率/待确认费用/历史成本/未开票缺口有数量且只读 GET 零写、成员仅见本人项目、跨租户/非项目成员 404、非法金额/短说明/非法日期/同 token 改值/同金额/零合同额/缺 token/跨租户拒绝、项目卡片最新合同额、工时与开票下钻过滤）；`seed_demo` 后 Chromium 1440/390 浏览器证据：同一 Q-33 seed 下 Before（Q-33 合并后的 main）无 Q-34 面板 → After 一屏显示合同额/已开票不含税/待开票/已收款/未收款（价税合计现金口径）/1.0 人工/1.0 直接费用/毛利/项目现金净额与历史专业成本缺口，追加合同额变更保留历史并下钻工时/费用/开票（`?project=` 过滤）与收款明细，项目卡片显示最新合同额，390 无横向溢出；截图 `docs/screenshots/q34-before-project-finance-1440.png`、`q34-before-project-finance-390.png`、`q34-after-project-finance-1440.png`、`q34-after-contract-change-1440.png`、`q34-after-project-finance-390.png`、`q34-after-project-list-1440.png` |

| 5 | 工时与自动人工成本（Q-35） | pass | `python manage.py test tests.test_weekly_time`（18 tests OK：费率快照/更正/幂等/缺费率/权限/锁期/1440 分钟反例，含真实渲染 token 重复提交、锁期费率回填拒绝、极端 ?week、旧审批入口拒绝）；`python manage.py verify_integrity --json` exit 0（演练库 47 条账目）；`manage.py seed_demo` 后 Chromium 1440/390 浏览器证据：周填报→更正→重复提交幂等、失败保留已填数据、缺费率进入工作台缺口清单、补录费率后自动定价；截图 `docs/screenshots/q35-weekly-1440.png`、`q35-weekly-390.png`、`q35-after-dashboard-gap-1440.png`、`q35-after-dashboard-gap-390.png`、`q35-project-cost-1440.png` |
| 6 | 费用/外包成本确认（Q-36） | pass | `python manage.py test tests.test_q36_expenses`（37 tests OK：登记/确认/更正审计留痕、整数分 Decimal/ROUND_HALF_UP 且拒绝 float、确认→多次更正后 `verify_integrity` 仍 ok、成本=最新值且仅 1 条 active 成本分录、重复确认与重复登记/付款 token 幂等（附件参与幂等）、分次付款不重复计成本、未付进入 8 周计划且按来源去重、逾期单列、超额付款/锁期/未来日期/跨租户/成员-finance-auditor 角色拒绝、历史专业费用只读友好提示与写拒绝、附件类型校验；PostgreSQL-only FOR UPDATE 外连接回归测试在 PG 域执行）；`python manage.py verify_integrity --json` exit 0（seed 后 51 条，浏览器更正链后 56 条）；`seed_demo` 后 Chromium 1440/390 浏览器证据：登记→确认→分次付款→更正（追加冲销重述成本）→项目成本/现金/8 周缺口，截图 `docs/screenshots/q36-after-expenses-1440.png`、`q36-after-expenses-paid-390.png`、`q36-after-expenses-corrected-1440.png`、`q36-after-project-expense-1440.png`、`q36-after-project-expense-390.png`、`q36-after-dashboard-gap-1440.png`，Before 对照 `q36-before-*-1440.png`（main 5e4ae8b/Q-37 合并后、无 Q-36 入口/页面/项目面板） |
| 7 | 极简开票与收款归集（Q-37） | not_run | 待实现 |
| 8 | 现金账户与 8 周计划（Q-38） | not_run | 待实现 |
| 9 | 一条命令演示环境（Q-39） | pass | 空环境 `python scripts/demo.py --slice micro` exit 0，打印 `http://127.0.0.1:8765/`、owner/finance/pm/member/auditor/newbie 账号与 5 步脚本；随机密码只写凭据文件（0600）不打印；重复执行 exit 0 且输出「已存在，复用（未重复造数、未迁移）」「已有库不迁移、不覆盖」，数据库 SHA-256 与行数不变；同一命令复用已运行服务，`--stop` 停止；0 字节文件/空 SQLite/非项目库/真实公司库/旧结构演练库均在迁移前 exit 3 且文件哈希不变；服务状态文件（repo/端口/库）不匹配时 `--stop` exit 3 不误杀；外部 /health/ 服务不被复用。`python manage.py test tests.test_demo_script`（15 tests OK：空环境打印、重复幂等且 DB SHA-256 不变、0 字节/空 SQLite/非项目/真实公司/旧结构拒绝且不写、服务启动/复用/停止、状态不匹配拒绝停止、外部 health 不复用、production 在 mkdir 前拒绝、README/STATUS 一致性、seed_demo 运行时目录与 in-process 覆盖）；`python manage.py check`、`makemigrations --check`、`projectctl check/queue` 通过；演练数据覆盖 客户/项目/合同额、工时、费用（含 Q-36 分次付款）、开票、收款与工作台收益/现金；README/STATUS 入口与命令一致。scratch 副本中 `python3 scripts/demo.py --slice micro` 两次运行输出见 PR 证据（URL/账号/5 步、复用与 DB 哈希不变、`--stop`）；浏览器按打印脚本走查 5 步截图 `docs/screenshots/q39-demo-step1..5-1440.png`（无 UI 变更，不设 Before/After）。 |
| 10 | 首次启用向导（Q-40） | not_run | 待实现 |
| 11 | 简化导出与提醒（Q-41） | not_run | 待实现 |
| 12 | 浏览器端到端验收（Q-42） | not_run | 待实现 |
| 13 | 安装与备份恢复底线（Q-43） | not_run | 待实测 |
| 14 | 候选与发布包（Q-44） | not_run | 未建立候选 |
| 15 | L5 真实环境走查 | blocked | 真实域名/PG 环境未就绪（P-02） |

未验证：L3 CI/PG、L4 浏览器走查、L5 真实环境均未执行；下一候选建立后逐项回填真实结果。

剩余风险：发布资格仍 blocked；备份恢复、真实域名 HTTPS 与浏览器验收未实测；P-01/P-02 待确认。
小微版口径已由负责人确认（DECISIONS D-11）。
