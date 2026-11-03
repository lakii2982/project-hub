# Q-42 小微版浏览器端到端验收（L4）

日期：2026-11-04 · 环境：macOS 本机、SQLite（`runtime/q42.sqlite3`，空库 migrate）、
Chromium headless 1440×900 / 390×844、`PMO_SCOPE=micro`。未使用 `seed_demo` 或演示数据；
客户/项目/合同/首个工时全部由真实 `/setup/` 向导在浏览器里建立。

证据：本目录 `docs/screenshots/q42-*.png`（After）与 `q42-before-*.png`（Before，main `69eef07`）；
`docs/q42-evidence/` 保存四类导出 CSV、重复导出一致性样本与浏览器断言 JSON。
录屏未入库，L4 证据以 1440/390 截图 + 下列可复跑步骤/断言为准（ACCEPTANCE 允许“截图或录屏”）。

## 走查步骤与实时数字

1. **首次启用（空库）**：`/setup/` → 公司「青禾软件」、负责人 `q42owner`；
   密码不一致时提交 → 错误提示“两次输入的密码不一致”（`q42-after-setup-error-1440.png`）；
   提交中按钮 `disabled`/`aria-busy="true"`/“处理中…”（`q42-after-loading-setup-1440.png`）。
2. **客户 → 项目 → 合同 → 首个工时**：客户「星河零售」；项目 `Q42-001 · 零售数据平台`
   开始日=昨天、计划交付=今天+30 天；合同额 ¥12,000.00；首个工时**昨天 120 分钟**、
   负责人小时成本 **¥300.00**。就绪检查显示 **¥600.00** 人工成本、无“缺费率”缺口
   （`q42-after-setup-ready-1440.png`）。**Before（main 69eef07）同一输入**显示
   `2026-11-03 · 120 分钟 缺费率`、工作台缺口“缺费率工时 1 条”、人工成本 ¥0.00
   （`q42-before-setup-ready-1440.png`、`q42-before-dashboard-1440.png`、`q42-before-time-week-1440.png`）。
3. **重新登录**（清 cookie → `/login/`）进入工作台；空态：未开票 1 个 / 无收款 1 个 /
   缺费率 0 条、毛利率“无定义”，不使用假零（`q42-after-dashboard-empty-1440.png`）。
   费用空态、开票收款空态、现金无账户缺口分别见 `q42-after-expenses-empty-1440.png`、
   `q42-after-billing-empty-1440.png`、`q42-after-cash-empty-1440.png`。
   停用态：直接访问 `/finance/` → 403 “该功能不在小微版入口内 / 已停用”
   （`q42-after-disabled-finance-1440.png`）。
4. **资金账户**：新建「Q42 基本户」期初 ¥5,000.00。
5. **费用**：登记外包支出 ¥2,000.00（发生今天、预计付款今天+10 天）→ 确认计成本 →
   从基本户付款 ¥500.00（`q42-after-expenses-1440.png`）。
6. **开票与收款**：发票 `Q42-INV-001` ¥8,000.00（未填税率，不猜税额）；
   收款 `Q42-RCPT-001` ¥3,000.00 从基本户、关联该发票（`q42-after-billing-1440.png`）。
7. **页面一致**：工作台本月开票 ¥8,000.00 / 已收 ¥3,000.00 / 已支出 ¥500.00 /
   未收款 ¥5,000.00 / 账户余额 ¥7,500.00；项目排行收入 ¥8,000.00、成本 ¥2,600.00、
   毛利 ¥5,400.00（67.50%）。项目页财务页签：合同额 ¥12,000.00、已开票 ¥8,000.00、
   已收 ¥3,000.00、未收 ¥5,000.00、1.0 人工 ¥600.00 + 直接费用 ¥2,000.00、毛利 ¥5,400.00、
   项目现金净额 ¥2,500.00（`q42-after-project-1440.png`）。现金页：余额 ¥7,500.00、
   8 周内计划付款 ¥1,500.00、预计余额 ¥6,000.00（`q42-after-cash-1440.png`）。
8. **Q-41 导出**：`/exports/` 下载四类 CSV（`docs/q42-evidence/*.csv`）：项目收益与现金
   合同额 1,200,000 分 / 已开票 800,000 / 待开票 400,000 / 已收 300,000 / 未收 500,000 /
   人工 60,000 / 费用 200,000 / 成本 260,000 / 毛利 540,000 / 已付 50,000 / 现金净额 250,000；
   工时 120 分钟且无费率/成本列；费用 200,000/已付 50,000/未付 150,000；开票收款
   800,000/税额 0/不含税 800,000/已收 300,000/未收 500,000 与收款 300,000。
   同一天重复导出字节与文件名一致（`project-finance-again.csv`）；`AuditEvent(action=data.exported)`
   记录 5 次（owner，含重复），导出没有个人费率/工资。
9. **Q-41 提醒**：`/due/` 仅 1 项“未收发票 Q42-INV-001 ¥5,000.00”，无逾期费用、无缺费率工时
   （`q42-after-due-1440.png`），与开票页未收金额一致。
10. **1440/390 与键盘**：列出各页 390 截图（`q42-after-*-390.png`），页面级横向溢出均为 0；
    键盘 Tab 有可见焦点环、可回车进入项目页，工时周表单控件可达（`q42-after-time-week-1440.png`）。
11. **成员与跨租户拒绝**：成员登录访问 `/billing/`、`/expenses/`、`/exports/`、`/settings/` → 403
    （`q42-after-member-denied-billing-1440.png`）；成员可访问 `/time/week/`。第二租户登录后
    看不到 Q42 项目，直接访问本项目 URL → 404（`q42-after-cross-tenant-dashboard-1440.png`、
    `q42-after-cross-tenant-project-404-1440.png`）。
12. **控制台**：正常链路（登录→向导→费用→开票→收款→工作台→导出→提醒→390→键盘）无 console
    error、无 pageerror；仅“故意错误/停用/越权”探针产生预期 400/403 网络响应（已隔离，不计入正常链路）。

## 验收中发现的缺陷与本 PR 修复（最小范围）

1. **Q-40 回填首个工时费率不生效（阻断本链路数字）**：`record_first_time` 创建费率时固定
   `effective_from=today`，而首个工时允许早于今天（项目可昨天开始）。表现：输入 ¥300/h 后
   昨天 120 分钟仍显示“缺费率”，人工成本不进项目成本/工作台（Before 截图）。
   修复：费率生效日取 `min(work_date, today)`，回填业务日期当天即可计价；新增回归测试
   `test_backdated_first_time_with_supplied_rate_is_priced`（¥300/h × 1h = 30,000 分，
   `cost_state=priced`）。
2. **项目财务页把 Q-36 简化付款重复显示为“历史专业现金”**：Q-36 付款同时写 core `cash_out`
   账本，项目页 `legacy_cash_net_cents` 原先把这条也计入“历史专业流程现金（项目账）”，
   于是简化现金净额 ¥2,500 旁边出现“历史现金净额 ¥-500”，同一笔付款被两个口径重复展示。
   修复：`project_finance` 计算历史现金净额时排除 `document__expense_payment_link` 的简化付款，
   页面历史现金净额回到 ¥0.00；新增 `test_project_page_does_not_double_count_simplified_payment_as_legacy_cash`。
3. **启用向导首页未加载 `app.js`（加载态/防重复提交缺失）**：`auth_base.html` 未引入
   `app.js`，而“建立公司”表单带 `data-submit-once`；实际点击不会禁用按钮/显示“处理中…”，
   双击可能重复提交。修复：auth 页加载 `app.js`（脚本各 IIFE 均有空引用保护）；新增测试
   `test_company_page_loads_submit_once_script`，浏览器证据 `q42-after-loading-setup-1440.png`
   （Before 页面源码 `app.js` 引用数为 0）。

## 未验证 / 边界

- L4 本机 Chromium + SQLite，不是 staging/真实域名/HTTPS，不等同 L5；PostgreSQL 证据只来自 CI。
- 未提交录屏文件（体积）；截图与步骤满足 ACCEPTANCE 的“截图或录屏”要求。
- 跨租户通过第二租户浏览器登录 + 404 验证；数据隔离的完整反例矩阵仍由自动化测试覆盖。
- 客户/合同没有独立列表入口（Q-40 首个客户记在项目 `Project.client`，合同额为追加记录），
  与 STATUS 已披露的范围一致。
