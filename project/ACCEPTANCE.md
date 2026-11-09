# 候选验收清单（一页）

更新：2026-11-04。每个候选版本（每两周一次）用本文件验收一次；只写真实结果，
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

候选：`0.2.0-rc.1`（小微版预发布候选；版本来源 `pyproject.toml` = `0.2.0`）。
SHA：代码基线 `e642ec2`（Q-43 合并后的 main）；Q-44 发布元数据 PR 的 HEAD 是 9 项必需检查与独立复核的绑定对象，最终候选 SHA 以合并提交为准。
环境：L3 = GitHub Actions SQLite/PostgreSQL/容器/协议/治理（同一 PR HEAD）；L4 = macOS 本机 Chromium headless 1440×900/390×844 + SQLite 空库（非 seed_demo）。日期：2026-11-04。复核人：待独立复核。
（候选包、构建/校验命令、逐文件摘要与未验证项见 [docs/candidates/0.2.0/README.md](../docs/candidates/0.2.0/README.md)；本段不代表已发布、已打标签或 L5 通过。）

W6 的 Q-51（客户/项目/合同 CSV 导入）与 Q-52（演练库→正式库一键切换）以 [QUEUE.md](QUEUE.md) 与 [STATUS.md](STATUS.md) 为唯一跟踪；本候选表不逐条重复。Q-52 额外证据：`python manage.py test tests.test_q52_go_live`（12 tests OK，含 target 有员工/账本/审计行但无公司记录时 exit 3 且数据库 SHA-256 不变）、`scripts/go-live.py --check` 只读、空库迁移后无演练组织并打印 `/setup/`。

当前状态（按 [QUEUE.md](QUEUE.md) W1–W3，沿用 STATUS.md 的边界，不重复推断）：

| # | 验收项 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | 范围/设计/任务与队列重排（Q-31） | pass | Q-31 已合并（`80752e6` / PR #315）：1.0 范围重定义为小微企业简化版、队列 W1–W3 与轻交付协议落地；证据为 `docs/product-scope.md`、`project/QUEUE.md`、`docs/engineering/light-delivery.md` 与 main 历史；后续 Q-32–Q-44 均按该范围/队列交付，未出现范围回退。 |
| 2 | 小微版入口收口（Q-32） | pass | Q-32 已合并（`f671f05` / PR #316）：`PMO_SCOPE=micro` 导航白名单、停用页与写/导出/导入门控；`python manage.py test tests.test_micro_scope` 19 tests OK；浏览器 Before/After 截图 `docs/screenshots/q32-before-*`、`q32-owner-1440.png`、`q32-disabled-1440.png`；Q-42 走查再次验证 `/finance/` 403“不在小微版入口内”。 |
| 3 | 老板工作台：收益 + 现金一屏（Q-33） | pass | `python manage.py test tests.test_q33_dashboard`（17 tests OK：含税发票净额回归、净额为 0 的未开票边界、Q-36 付款不计入历史专业付款、简化成本/历史专业成本缺口、开票/收款口径切换、未收款=已开票−已收款、账户余额=期初+已收−退款、毛利排行与无定义毛利率、缺费率/未开票/无收款缺口、PM 账户余额与 /billing/ 一致、成员/finance/auditor 403、跨租户隔离、只读零写）；`seed_demo` 后 Chromium 1440/390/键盘证据：本月开票不含税 ¥205,811.32（价税合计 ¥216,000）、已收 ¥91,000、已支出 ¥7,000（仅 Q-36 付款）、未收款 ¥130,000（含税现金）、账户余额 ¥171,000（累计付款 ¥17,000 未关联账户缺口）、历史专业成本 ¥67,560/3 个项目只读未计入 1.0 毛利，历史专业收款 2 笔/付款 3 笔只读；排行与 billing/expenses 聚合逐项一致；截图 `docs/screenshots/q33-after-owner-1440.png`、`q33-after-owner-390.png`、`q33-after-owner-390-nav.png`、`q33-after-owner-receipt-1440.png`，Before 对照 `q33-before-*`（main 9daa3fc） |
| 4 | 项目页：合同额→开票→收款→成本→毛利（Q-34） | pass | `python manage.py test tests.test_q34_project_finance`（15 tests OK：与 Q-33 `business_dashboard` 同项目收入/1.0 成本/毛利/未收款/现金净额/历史成本缺口逐项一致，seed_demo 后 PH-26001 毛利 ¥84,527.55=收入 ¥113,207.55−Q-35/Q-36 成本 ¥28,680，历史专业成本 ¥19,280 单列；合同额追加保留历史与立项预估、缺费率/待确认费用/历史成本/未开票缺口有数量且只读 GET 零写、成员仅见本人项目、跨租户/非项目成员 404、非法金额/短说明/非法日期/同 token 改值/同金额/零合同额/缺 token/跨租户拒绝、项目卡片最新合同额、工时与开票下钻过滤）；`seed_demo` 后 Chromium 1440/390 浏览器证据：同一 Q-33 seed 下 Before（Q-33 合并后的 main）无 Q-34 面板 → After 一屏显示合同额/已开票不含税/待开票/已收款/未收款（价税合计现金口径）/1.0 人工/1.0 直接费用/毛利/项目现金净额与历史专业成本缺口，追加合同额变更保留历史并下钻工时/费用/开票（`?project=` 过滤）与收款明细，项目卡片显示最新合同额，390 无横向溢出；截图 `docs/screenshots/q34-before-project-finance-1440.png`、`q34-before-project-finance-390.png`、`q34-after-project-finance-1440.png`、`q34-after-contract-change-1440.png`、`q34-after-project-finance-390.png`、`q34-after-project-list-1440.png` |

| 5 | 工时与自动人工成本（Q-35） | pass | `python manage.py test tests.test_weekly_time`（18 tests OK：费率快照/更正/幂等/缺费率/权限/锁期/1440 分钟反例，含真实渲染 token 重复提交、锁期费率回填拒绝、极端 ?week、旧审批入口拒绝）；`python manage.py verify_integrity --json` exit 0（演练库 47 条账目）；`manage.py seed_demo` 后 Chromium 1440/390 浏览器证据：周填报→更正→重复提交幂等、失败保留已填数据、缺费率进入工作台缺口清单、补录费率后自动定价；截图 `docs/screenshots/q35-weekly-1440.png`、`q35-weekly-390.png`、`q35-after-dashboard-gap-1440.png`、`q35-after-dashboard-gap-390.png`、`q35-project-cost-1440.png` |
| 6 | 费用/外包成本确认（Q-36） | pass | `python manage.py test tests.test_q36_expenses`（37 tests OK：登记/确认/更正审计留痕、整数分 Decimal/ROUND_HALF_UP 且拒绝 float、确认→多次更正后 `verify_integrity` 仍 ok、成本=最新值且仅 1 条 active 成本分录、重复确认与重复登记/付款 token 幂等（附件参与幂等）、分次付款不重复计成本、未付进入 8 周计划且按来源去重、逾期单列、超额付款/锁期/未来日期/跨租户/成员-finance-auditor 角色拒绝、历史专业费用只读友好提示与写拒绝、附件类型校验；PostgreSQL-only FOR UPDATE 外连接回归测试在 PG 域执行）；`python manage.py verify_integrity --json` exit 0（seed 后 51 条，浏览器更正链后 56 条）；`seed_demo` 后 Chromium 1440/390 浏览器证据：登记→确认→分次付款→更正（追加冲销重述成本）→项目成本/现金/8 周缺口，截图 `docs/screenshots/q36-after-expenses-1440.png`、`q36-after-expenses-paid-390.png`、`q36-after-expenses-corrected-1440.png`、`q36-after-project-expense-1440.png`、`q36-after-project-expense-390.png`、`q36-after-dashboard-gap-1440.png`，Before 对照 `q36-before-*-1440.png`（main 5e4ae8b/Q-37 合并后、无 Q-36 入口/页面/项目面板） |
| 7 | 极简开票与收款归集（Q-37） | pass | Q-37 已合并（`5e4ae8b` / PR #318）：极简发票/收款/资金账户基础、退款与作废追加、未收款=已开票−已收款；`python manage.py test tests.test_q37_billing` 27 tests OK（含权限/租户/幂等/部分收款/`PMO_SCOPE=micro` HTTP）；后续 Q-38 现金页与 Q-42 浏览器链路复用 `/billing/` 并逐分核对（`docs/screenshots/q42-after-billing-1440.png`）。 |
| 8 | 现金账户与 8 周计划（Q-38） | pass | `python manage.py test tests.test_q38_cash`（22 tests OK：账户余额=期初+收款−付款含退款负值；流水按账户/项目/来源编号对账；8 周计划两类来源 payment_plan:<id>/expense:<id> 按 source key 去重、已结清移出、逾期单列不挪周、8 周外单列；只读计划零写入（Document/LedgerEntry/AuditEvent/ExpensePayment/Receipt 行数不变）；付款必须关联本公司启用账户（无/停用/跨租户账户拒绝）；账户期初/启停维护 owner-only、版本冲突拒绝并审计；成员/finance/auditor 403、PM 仅见本人项目计划；HTTP 新建/维护账户与页面渲染；评审回归：无收款计划或计划全部停用/未批准时不再返回假零（probe 期初 ¥10,000 + 收款 ¥4,000 无计划 → `unplanned_receipt_cents=400000`），模板「未匹配收款计划」仅在 >0 时显示、「本期无已知缺口」仅在全部缺口为零且预计余额非负时显示（预计余额为负不再显示无缺口）、历史付款计划显示剩余金额、PM 页披露公司现金口径与本人项目计划范围）；`python manage.py test tests.test_q33_dashboard tests.test_q34_project_finance tests.test_q36_expenses tests.test_q37_billing`（118 tests OK，含 Q-36 付款关联账户与 Q-33 账户余额/缺口回归）；`python manage.py verify_integrity --json` exit 0（seed 后 52 条账目）；`seed_demo` 后 Chromium 1440/390 浏览器证据：账户余额 ¥154,000.00 = 期初 ¥80,000 + 已收 ¥86,000 − 已付 ¥17,000（2 个账户），流水含收款/付款与来源编号，8 周计划 9 个去重来源（收款计划 7 + 未付支出 2）、逾期 2 项单列、8 周内计划收款 ¥434,000 / 付款 ¥7,000 / 预计余额 ¥581,000；新建「Q38 演示账户」期初 ¥5,000 → 从费用页选择该账户付款 ¥100 → 余额 ¥158,900、计划付款 ¥6,900，流水对账；Before main 56bbdec 无现金入口（`/cash/` 404）、工作台显示 3 笔 ¥17,000 付款未关联账户缺口（Q-38 接入），After 缺口收口；截图 `docs/screenshots/q38-after-cash-1440.png`、`q38-after-cash-accounts-1440.png`、`q38-after-cash-flows-1440.png`、`q38-after-cash-plan-details-1440.png`、`q38-after-cash-gaps-1440.png`、`q38-after-cash-new-account-1440.png`、`q38-after-expense-payment-1440.png`、`q38-after-cash-after-payment-1440.png`、`q38-after-cash-390.png`、`q38-after-cash-plan-390.png`、`q38-after-cash-accounts-390.png`、`q38-after-390-nav.png`，Before 对照 `q38-before-nav-1440.png`、`q38-before-cash-404-1440.png`、`q38-before-expense-payment-1440.png`、`q38-before-390-nav.png`（main 56bbdec） |
| 9 | 一条命令演示环境（Q-39） | pass | 空环境 `python scripts/demo.py --slice micro` exit 0，打印 `http://127.0.0.1:8765/`、owner/finance/pm/member/auditor/newbie 账号与 5 步脚本；随机密码只写凭据文件（0600）不打印；重复执行 exit 0 且输出「已存在，复用（未重复造数、未迁移）」「已有库不迁移、不覆盖」，数据库 SHA-256 与行数不变；同一命令复用已运行服务，`--stop` 停止；0 字节文件/空 SQLite/非项目库/真实公司库/旧结构演练库均在迁移前 exit 3 且文件哈希不变；服务状态文件（repo/端口/库）不匹配时 `--stop` exit 3 不误杀；外部 /health/ 服务不被复用。`python manage.py test tests.test_demo_script`（15 tests OK：空环境打印、重复幂等且 DB SHA-256 不变、0 字节/空 SQLite/非项目/真实公司/旧结构拒绝且不写、服务启动/复用/停止、状态不匹配拒绝停止、外部 health 不复用、production 在 mkdir 前拒绝、README/STATUS 一致性、seed_demo 运行时目录与 in-process 覆盖）；`python manage.py check`、`makemigrations --check`、`projectctl check/queue` 通过；演练数据覆盖 客户/项目/合同额、工时、费用（含 Q-36 分次付款）、开票、收款与工作台收益/现金；README/STATUS 入口与命令一致。scratch 副本中 `python3 scripts/demo.py --slice micro` 两次运行输出见 PR 证据（URL/账号/5 步、复用与 DB 哈希不变、`--stop`）；浏览器按打印脚本走查 5 步截图 `docs/screenshots/q39-demo-step1..5-1440.png`（无 UI 变更，不设 Before/After）。 |
| 10 | 首次启用向导（Q-40） | pass | `python manage.py test tests.test_q40_onboarding`（15 tests OK，含 Q-42 追加的回填费率与 auth 页脚本回归、PostgreSQL-only 双连接并发测试：空库五步完成并落库公司/owner/员工/客户/项目/合同额/工时，无预算基线/单据/账本/演示数据；整数分与 0.01 元边界；重复公司创建 409 且行数不变、`start_company` 二次调用拒绝、PostgreSQL 双连接并发只创建一个工作区；中断后续接（登出重登回到缺项）、重复项目/工时/合同提交不重复；成员/finance/auditor 与非首个工作区 403、匿名有公司重定向登录、空库步骤重定向公司；就绪检查在未完成时列出缺项与续接链接；设置改公司名不改启用日期/币种）；空库 migrate 后 Chromium 1440/390/键盘证据：Before main 490e7fb `/setup/` 404 且登录页无入口 → After 五步完成公司（启用日期/CNY/Asia/Shanghai）→ 客户 → 项目 → 合同额 ¥1,234.56 → 首个工时（费率 ¥300/h，成本 ¥300）→ 就绪检查（客户/项目/合同额/工时/缺资金账户/未开票/单账号缺口），随后工作台/项目页/开票页可用且无控制台错误；中断后工作台出现“继续启用”并能回到缺项；390 各步无横向溢出；截图 `docs/screenshots/q40-after-company-1440.png`、`q40-after-company-390.png`、`q40-after-company-error-1440.png`、`q40-after-customer-1440.png`、`q40-after-customer-390.png`、`q40-after-project-1440.png`、`q40-after-project-390.png`、`q40-after-contract-1440.png`、`q40-after-time-1440.png`、`q40-after-time-390.png`、`q40-after-readiness-1440.png`、`q40-after-readiness-390.png`、`q40-after-dashboard-1440.png`、`q40-after-dashboard-390.png`、`q40-after-dashboard-resume-1440.png`、`q40-after-resume-customer-1440.png`、`q40-after-project-detail-1440.png`、`q40-after-billing-1440.png`，Before 对照 `q40-before-login-1440.png`、`q40-before-login-390.png`、`q40-before-setup-404-1440.png`（main 490e7fb） |
| 11 | 简化导出与提醒（Q-41） | pass | `python manage.py test tests.test_q41_exports_reminders`（14 tests OK：四类 CSV 与项目页/工作台/费用/开票页聚合逐项一致，时间/费用/开票导出不含个人费率/工资、公式注入被前缀保护、同一天重复下载字节与文件名一致、导出写 `data.exported` 审计、非 owner/跨租户/全量 scope 拒绝；站内提醒按发票/费用/工时事实去重、重复扫描 key 与顺序一致且业务表零写、无 mail/外部副作用，PM 仅见本人项目、成员/finance/auditor 403、全量 scope 404）；`seed_demo` 后 Chromium 1440/390 证据：Before main `490e7fb` 工作台无到期提醒/导出入口、`/due/` 与 `/exports/` 404；After 工作台显示未收发票 2 / 逾期未付费用 1 / 缺费率工时 1（合计 4）并可下钻，`/due/` 三类来源链接与键盘 Enter 可用，`/exports/` 下载四类 CSV（项目收益 4 行、工时 38 行、费用 3 行、开票收款 6 行；项目页 PH-26001 毛利 84,527.55 与 CSV 一致）且 390 无横向溢出；截图 `docs/screenshots/q41-before-dashboard-1440.png`、`q41-before-dashboard-390.png`、`q41-after-dashboard-1440.png`、`q41-after-dashboard-390.png`、`q41-after-due-1440.png`、`q41-after-due-390.png`、`q41-after-exports-1440.png`、`q41-after-exports-390.png` |
| 12 | 浏览器端到端验收（Q-42） | pass | L4 本机 Chromium（SQLite 空库，非 seed_demo）：一次会话从 `/setup/` 公司→客户→项目→合同额→首个工时（昨天 120 分钟 + ¥300/h）→重新登录→空态/停用态→资金账户→费用（¥2,000 确认 + ¥500 付款）→发票 `Q42-INV-001` ¥8,000→收款 `Q42-RCPT-001` ¥3,000→工作台/项目页/现金→四类 CSV→到期提醒→1440/390→键盘→成员/跨租户拒绝；页面与导出数字逐项一致（本月开票 8,000 / 已收 3,000 / 已支出 500 / 未收 5,000 / 账户余额 7,500；项目毛利 5,400、现金净额 2,500；8 周计划付款 1,500、预计 6,000；导出分毫不差、重复导出字节一致、审计 5 次；到期仅未收发票 5,000、无缺费率/逾期费用），正常链路 console error=0/pageerror=0，390 横向溢出=0，Tab 焦点环/回车可达，成员访问 billing/expenses/exports/settings=403、跨租户项目 404；`python manage.py test tests.test_q42_e2e tests.test_q40_onboarding` 通过（4+15 tests）；步骤与截图索引见 [docs/q42-browser-acceptance.md](../docs/q42-browser-acceptance.md) 与 `docs/screenshots/q42-*.png`，Before 对照 `q42-before-*.png`（main 69eef07）与 `docs/q42-evidence/*`。缺陷修复：Q-40 回填首个工时费率未生效（已修复+回归测试）、项目财务页简化付款重复计入“历史专业现金”（已修复+回归测试）、启用向导首页未加载 `app.js` 导致提交中/防重复提交不生效（已修复+测试）。 |
| 13 | 安装与备份恢复底线（Q-43） | pass | 干净 SQLite 本地安装（scratch 目录，`PMO_PYTHON=python3.12 sh scripts/local.sh`）exit 0；重复同命令 exit 0 且 `runtime/pmo.sqlite3` SHA-256 不变；`scripts/install.sh` 的 `.env` 生成/0600、已有 `.env` 绝不覆盖、占位值拒绝、compose 配置/`up` 失败保留 `.env` 与卷并打印诊断、健康超时诊断、失败后重试成功由 `tests.test_q43_install` 8 项 fake-docker 回归覆盖（含 docker 存在但 compose 不可用→3）（本机无 Docker，真实 compose 安装未执行）。`manage.py backup_local` 备份数据库+Evidence 附件：`verify-local-backup.py` 返回 `{"ok": true, "entries": 52, "attachments": 1}`；`manage.py restore_local` 恢复到全新隔离目录 exit 0，恢复库 SHA-256 与备份一致（`2a78926f…0154`），`verify_integrity --json` 为 `ok=true/errors=[]`、1 组织、52 条账目。浏览器源库/恢复库（Chromium 1440 owner）工作台、项目列表、项目收益与现金、开票与收款、现金五页归一化文本全部一致（equal true），member 权限矩阵一致（billing/cash/settings 403、status 200），owner 控制台 0 错误；`/status/` 只读显示版本/sqlite/0 待迁移/最近备份清单存在（2.9MB、151B、1 附件）/附件缺失 0，无密钥或主机路径；Before main 设置页无 `系统状态` 入口，After 导航出现。`python manage.py test tests.test_q43_install tests.test_q43_restore tests.test_q43_status` 25 项通过（安装 8、恢复 9、状态 8）；全量 1806 项仅 2 条既有 macOS `/private` 路径测试失败（与 Q-43 无关）；`projectctl check/queue`、`manage.py check`、`makemigrations --check` 通过。证据见 `docs/verification.md` 2026-11-04 段与截图 `docs/screenshots/q43-before-settings-1440.png`、`q43-after-settings-1440.png`、`q43-after-status-1440.png`、`q43-restored-*-1440.png`。**缺口**：本机无 Docker/pg_dump/pg_restore/真实 PostgreSQL，真实 Compose 安装、生产 PG 备份恢复、TLS/域名与非 SQLite 跨机恢复未执行，`not_run`；不以 SQLite 文件拷贝冒充生产恢复。 |
| 14 | 候选与发布包（Q-44） | pass | 建立 `0.2.0-rc.1` 预发布候选：版本 `pyproject.toml`=0.2.0，`CHANGELOG.md` 有 0.2.0 段；源码包由 `python scripts/package.py` 生成 `releases/project-hub-0.2.0-source.zip`（逐文件 `SHA256MANIFEST.json` + 归档 `.sha256`；确定性条目时间/权限），`python scripts/verify-source-package.py` 复算 manifest/摘要/排除策略通过；包不含 `runtime/`、`.env`（仅 `.env.example`）、SQLite/附件/备份/虚拟环境/缓存/发布产物，保留 `LICENSE`/`NOTICE`/`THIRD_PARTY_NOTICES.md`。候选清单见 `docs/candidates/0.2.0/`（README、candidate.json、source.zip.sha256；逐文件 `SHA256MANIFEST.json` 在源码包内、不提交）并列出 not_run 项；`python scripts/check_candidate_metadata.py` 在 Public hygiene 与 release workflow 中重建包后比对提交摘要，防止摘要过期；9 项必需 CI 与独立复核绑定 Q-44 同一 HEAD；**未打标签、未发布 GitHub Release、未部署、未上传镜像**。 |
| 15 | L5 真实环境走查 | blocked | 真实域名/PG 环境未就绪（P-02） |

候选走查清单（引用既有真实证据，不重复 screenshot）：

1. **安装/首用**：空库 `python scripts/demo.py --slice micro`（Q-39）或 `/setup/` 五步向导（Q-40）；
   `/status/` 只读版本/迁移/备份状态与 SQLite 备份+隔离恢复（Q-43，`docs/verification.md` 2026-11-04）。
2. **核心链路**：Q-42 浏览器会话（`docs/q42-browser-acceptance.md`，72/72 断言、console/pageerror=0）：
   登录/启用→客户→项目→合同额 ¥12,000→回填首个工时昨天 120 分钟 @ ¥300/h（¥600 已计价）→
   费用 ¥2,000 确认 + 付款 ¥500→发票 ¥8,000→收款 ¥3,000→工作台/项目页/现金同数
   （开票 8,000、已收 3,000、已支出 500、未收 5,000、账户 7,500、毛利 5,400、现金净额 2,500）。
3. **现金/导出/提醒**：Q-38 现金页（余额=期初+收−付、流水、8 周计划去重/逾期单列/预计余额缺口）、
   Q-41 四类 CSV（与页面同口径、重复导出字节一致、审计 5 次）与 `/due/` 未收发票 ¥5,000 提醒；
   Q-42 逐分核对页面=CSV=提醒。
4. **非功能**：Q-42 1440/390/键盘/空错载停用四态、成员 billing/expenses/exports/settings 403、
   跨租户项目 404；Q-43 SQLite 安装/备份/恢复实测；Q-32 停用页、Q-39 demo 幂等。
5. **候选包**：见 `docs/candidates/0.2.0/`；构建与校验命令、摘要、包内容/排除项与许可证均可在评审中复算。

未验证 / 剩余风险：
- L5 真实环境/客户走查 `blocked`（真实域名/PG 未就绪，P-02）；真实 Docker/Compose 安装、生产 PostgreSQL 备份恢复、
  TLS/域名/反向代理均 `not_run`（本机无 Docker/pg_dump/pg_restore；SQLite 恢复不冒充 PG 恢复）。
- L3 以 Q-44 PR 的 9 项必需检查为准；L4 为本机 Chromium+SQLite（非 staging），不得升级为 L5。
- 独立复核待做；客户 UAT/三方签批**不是**本次预发布前置（DECISIONS D-12）。1.0 门槛仍未满足，本候选不代表生产可用。
- 候选不包含专业财务能力（合同版本/审批、应收结算核销、管理收入、预算 ETC、工资税、多币种等），默认停用/历史只读。
