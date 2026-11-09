# PH-026 阶段④ 验收记录（工时、费用与现金流页签）

- 日期：2026-11-09（Asia/Shanghai）
- 分支：`feat/Q-49-stage4/agent/frontend-iteration-2-ddc35edd8f6d`；真实 base：`origin/main = d33c59e`（阶段③ #340 + Q-51 客户/项目/合同 CSV 导入 #342 合并后；本切片已 rebase 到该基线，Q-51 不涉及本切片页面行为）
- 演示数据：`manage.py seed_demo` 演练组织（行远技术 · 演练工作区），账号 owner（陈予安/公司负责人），本地 SQLite
- 依据：`docs/engineering/visual-system.md` §4.3–§4.5、§5.3、§8 与 `frontend-standards.md` §3–§4、§13.2 阶段④；范围见 `project/QUEUE.md` Q-49

## 1. 自动化命令（真实命令与退出码，cwd=仓库根）

| 命令 | 结果 |
| --- | --- |
| `.venv(py3.12)/python manage.py test tests.test_ph026_time_tabs tests.test_weekly_time tests.test_q34_project_finance tests.test_q36_expenses tests.test_q37_billing tests.test_q38_cash tests.test_q41_exports_reminders tests.test_micro_scope tests.test_q40_onboarding tests.test_ph026_dashboard_tabs tests.test_ph026_cashflow_tabs tests.test_ph026_expense_tabs tests.test_q42_e2e tests.test_governance -v 1` | 223 tests OK，exit 0 |
| `…/python manage.py test tests --verbosity 1`（全量，最终 HEAD） | 1872 tests，2 failures + 76 skips；失败项与 main 既有 macOS 环境基线一致（见 §5） |
| `node --check static/app.js` | exit 0 |
| `grep` 门禁：`static/app.css` v1 变量、裸 hex 色值、`font-size` < 12px 字面量 | 0 命中 |
| `diff docs/engineering/ui-tokens.css static/ui/ui-tokens.css` | 输出为空（令牌同源） |
| `python scripts/check_candidate_metadata.py` | ok=true |
| `GITHUB_EVENT_PATH=… python3 scripts/check_pr.py`（PR body 含唯一 `Queue: Q-49`） | ok=true mode=queue |

## 2. 浏览器检查（独立于命令结果）

- 浏览器：Chromium（Playwright），视口 1440×900 / 390×844 / 320×700，`device_scale_factor=2`，`reduced_motion=reduce`
- 路由：`/time/`、`/time/week/`、`/time/?tab=detail|summary`、`/expenses/`（4 页签）、`/cashflow/`（5 页签）、`/billing/`、`/cash/`
- 结果（`evidence/verify_stage4.py`，126 项检查全部 PASS）：
  - 3 视口 × 15 页面：`body`/document 横向滚动 = 0、console error = 0、失败请求 = 0
  - 工时：`/time/` 默认「按周填报」；`?tab=week|detail|summary` 服务端渲染唯一可见面板（其余 `hidden`），`/time/week/` 渲染同一页面 week 面板；保存成功跳 `/time/?tab=week&week=…`，失败保留输入并显示错误
  - 费用：默认「登记费用」；`?tab=register|pending|plan|all` 唯一可见面板；待确认计数徽章与空态正确
  - 现金流：`/cashflow/` 默认「概览」；`?tab=overview|invoices|receipts|accounts|plan` 唯一可见面板；`/billing/`、`/cash/` 为同页别名（默认发票/资金账户），浏览器与无 JS 均正确
  - 键盘/历史：←/→/Home/End 自动激活、URL 同步、焦点保留在页签；浏览器前进/后退恢复面板；旧入口（无 `?tab=`）在 JS 下保留服务端默认页签（`[data-link-tabs]` 以服务端 `aria-selected` 兜底）
  - 别名回退：`/billing/`、`/cash/`（无 `?tab=`）在 JS 下展示服务端默认页签（发票/资金账户）；点击其他页签后浏览器 Back 回到别名 URL 仍恢复服务端默认页签（不残留上一次点击的页签）
  - 无 JS：三类模块页签均为真实 `?tab=` 链接，服务端渲染目标面板；`/billing/`、`/cash/` 别名默认面板正确
- 成员/权限：`/expenses/`、`/cashflow/`、`/billing/`、`/cash/` 对 member/auditor 仍 403（Q-36/Q-37/Q-38 既有测试）。

## 3. 证据截图（`docs/screenshots/ph026-stage4-*`，同场景同视口同主题）

- Before 基线：`origin/main = d33c59e`（阶段③ + Q-51 合并后、本切片前）+ seed_demo，owner 登录；实际入库：
  - 1440：`ph026-stage4-before-1440-{time,time-week,expenses,billing,cash}.png`
  - 390：`ph026-stage4-before-390-{time,time-week,expenses,billing,cash}.png`
  - 320：`ph026-stage4-before-320-{time,expenses}.png`
- After（本 PR HEAD，同库同账号同视口同主题；实际入库）：
  - 1440：`ph026-stage4-after-1440-time.png`、`-time-detail.png`、`-time-summary.png`、`-expenses.png`、`-expenses-pending.png`、`-expenses-plan.png`、`-expenses-all.png`、`-cashflow.png`、`-cashflow-invoices.png`、`-cashflow-receipts.png`、`-cashflow-accounts.png`、`-cashflow-plan.png`、`-billing-alias.png`、`-cash-alias.png`、无 JS `-cashflow-nojs-plan.png`
  - 390：`ph026-stage4-after-390-{time,expenses,cashflow,cashflow-accounts,cashflow-plan}.png`
  - 320：`ph026-stage4-after-320-{time,expenses,cashflow}.png`

## 4. 实现边界（本切片未改变）

- 写路径、金额口径、权限与幂等：工时仍按小时输入、整数分钟落账（Q-35 服务不变）；费用确认成本、付款确认现金、未付进 8 周计划（Q-36 服务不变）；开票/收款/作废/退款/账户派生余额（Q-37/Q-38 服务不变）。
- 路由：`/time/`、`/time/week/`、`/expenses/` 路由保留；新增 `/cashflow/` 模块入口，`/billing/`、`/cash/` 保留为同页别名（旧链接/书签不失效）。
- 导航：小微信导航把「开票与收款」「现金」合并为单一「现金流」（owner/pm），与视觉规范 §4.5 一致；专业 `PMO_SCOPE=full` 导航未改。
- `static/ui/ui-tokens.css` 未修改；设置/审计/图表留给 Q-50。

## 5. 全量测试与 CI

- 本机全量 `manage.py test tests`（最终 HEAD）：**1872 tests，2 failures + 76 skips**。失败项为既有 macOS 本机环境基线：
  1. `tests.test_delivery_execution.ExecutorIntegrationTests.test_adapter_sandbox_callbacks_and_persisted_lifecycle`（macOS `/private/var` 与 `/var` 符号链接前缀差异，main 同样复现）
  2. `tests.test_delivery_execution_sandbox.SandboxConstructionTests.test_partial_home_components_and_intra_path_quantifiers_are_rejected`（macOS 沙箱 IsolationError 断言差异，main 同样复现）
- 过程记录：一次并行负载较高的全量运行中 `tests.test_demo_script` 的演练服务器健康检查曾超时（隔离重跑通过），最终全量运行未复现，属本机启动时序波动。
- CI：9 项必需检查 + CodeQL 随 PR 运行，以 CI 双域结果为准；独立复核作用于最终 HEAD。

## 6. 独立复核与修复

- 独立复核（reviewer，HEAD `e921ae4`，PR 评论）：request-changes。
  - B1（blocking）：项目财务「费用明细」钻取仍为 `/expenses/?project=`，落到默认「登记费用」而非「全部记录」。
  - B2（blocking）：`static/app.js` 的服务端默认页签兜底读取可变 `aria-selected`——在 `/billing/`、`/cash/` 切换页签后浏览器 Back 到无 `?tab=` 的别名 URL，会残留上一次点击的页签，而非服务端默认页签。
  - N1（non-blocking）：验收记录/PR 的截图索引超出了实际入库文件。
  - N2（non-blocking）：费用「POST 失败固定回登记页签」测试较同义（未构造冲突 `?tab=`）。
  - N3（non-blocking）：现金流页签链接未保留 `project` 筛选；旧 `/time/?project=` 书签回退到按周填报。
- 修复：`project_finance_summary` 钻取改 `?tab=all&project=…`；`app.js` 在初始化时捕获服务端默认页签并用其兜底（新增别名 Back 浏览器断言）；费用失败 POST 固定回登记页签新增冲突 `?tab=all` 测试；现金流页签链接保留 `project`，`/time/?project=` 无 tab 时回退明细；截图索引按实际文件校正。
- 修复后 HEAD 重新复核；结论见 PR #343 评论。

## 7. 未执行项与边界

- `not_run`：真实 PostgreSQL 并发场景（本切片无业务写路径变化）；200% 文字缩放与 reduced-motion 逐页矩阵（阶段⑤统一走查，本切片浏览器以 `reduced_motion=reduce` 截图）。
- 旧模板 `pmo_plugins/billing/templates/billing/billing.html`、`pmo_plugins/cashbook/templates/cashbook/cash.html` 由合并页 `templates/cashflow.html` 取代后删除；`billing/field.html` 仍被合并页复用。
