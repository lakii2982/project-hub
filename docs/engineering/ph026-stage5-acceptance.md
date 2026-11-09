# PH-026 阶段⑤ 验收记录（设置、审计与图表）

- 日期：2026-11-09（Asia/Shanghai）
- 分支：`feat/Q-50-stage5/agent/frontend-iteration-2-ddc35edd8f6d`；真实 base：`origin/main = beab1f1`（阶段①–④ 与 Q-51/Q-52 均已合并）
- 演示数据：`runtime/pmo.sqlite3`（`seed_demo` 演练组织，行远技术 · 演练工作区），owner（陈予安/公司负责人）；微版默认 `PMO_SCOPE=micro`，图表页走 `PMO_SCOPE=full`（现金预测与成本燃尽在专业范围页面）
- 依据：`docs/engineering/visual-system.md` §4.6、§5.5、§5.1；`frontend-standards.md` §4.2/§4.3/§4.4；`project/QUEUE.md` Q-50

## 1. 自动化命令（真实命令与退出码，cwd=仓库根）

| 命令 | 结果 |
| --- | --- |
| `python manage.py test tests.test_ph026_settings_tabs tests.test_ph026_chart_forecast -v 1` | 30 tests OK，exit 0 |
| `python manage.py test tests.test_ph026_settings_tabs tests.test_ph026_chart_forecast tests.test_http tests.test_ph026_dashboard_tabs tests.test_q34_project_finance tests.test_ph026_time_tabs tests.test_ph026_expense_tabs tests.test_ph026_cashflow_tabs tests.test_governance tests.test_q41_exports_reminders tests.test_q36_expenses tests.test_q37_billing tests.test_q38_cash tests.test_q42_e2e tests.test_micro_scope tests.test_q51_import -v 1` | 247 tests OK（skipped=1），exit 0 |
| `python manage.py test tests --verbosity 1`（全量，最终 HEAD） | 见 §5 |
| `python manage.py check` | exit 0（127 项既有告警，无新增错误） |
| `node --check static/app.js` | exit 0 |
| `grep` 门禁：`static/app.css` v1 变量、裸 hex 色值、`font-size` < 12px 字面量 | 0 命中 |
| `diff docs/engineering/ui-tokens.css static/ui/ui-tokens.css` | 输出为空（令牌同源） |
| `python scripts/check_candidate_metadata.py` | ok=true |

## 2. 浏览器检查（独立于命令结果）

- 浏览器：Chromium（Playwright），视口 1440×900 / 390×844 / 320×700，`device_scale_factor=2`，`reduced_motion=reduce`；微版与专业范围各起一个服务同库同账号
- 结果（`evidence/verify_stage5.py`，**73/73 checks PASS**）：
  - **设置三页签**：`/settings/` 默认「成员」；`?tab=members|rates|audit` 服务端渲染唯一可见面板（其余 `hidden`），非法 `?tab=` 回退成员；真实 `?tab=` 链接 + `data-link-tabs`；无 JS 深链正确；←/→/End 自动激活、URL 同步、浏览器后退恢复页签。
  - **审计**：`/audit/` 对 owner/finance 302 到 `/settings/?tab=audit`（单一维护入口），auditor 保留独立只读审计页；审计面板无任何 POST/变更表单，筛选为只读 GET（对象/操作/操作者），筛选空态保留条件与清除，日志链不可用时显示明确错误而非“暂无日志”。
  - **图表（专业范围）**：`/finance/` 未来 13 周现金预测图为原生 SVG，`role="img"` + `aria-labelledby`/`title`/`desc`，图例以实心柱/斜线纹理柱/虚线菱形点区分三序列，单位/区间/口径写在图旁，真实零基线；相邻精确值表为口径来源；无计划时显示空态且不画全零曲线。项目财务页成本燃尽图保持 title/desc、图例、等价数据表与不可用原因。
  - 3 视口下设置/审计/图表页面：document 横向滚动 = 0、console error = 0、失败请求 = 0（图表容器内部允许局部横滚，不撑宽页面）。
  - **200% 缩放代理**：1440 宽屏幕在 200% 页面缩放下等价 720 CSS px 视口（页面缩放同时触发响应式断点），以 720×450 视口 + DPR2 复现：设置/审计/工作台/财务页 0 横向滚动；截图 `ph026-stage5-after-zoom200-*`。`prefers-reduced-motion: reduce` 上下文截图见 `-dashboard-reduced-motion`。
  - 微版工作台按设计不渲染财务预测图（图表属于专业范围页面），脚本显式记录该缺席，避免把范围差异误当缺陷。

## 3. 证据截图（`docs/screenshots/ph026-stage5-*`，同场景同视口同主题）

- Before 基线：`origin/main = beab1f1`（阶段④ 与 Q-51/Q-52 合并后、本切片前）+ 同一演示库，owner 登录：
  - 微版 1440/390/320：`ph026-stage5-before-{1440,390,320}-{settings,audit,dashboard}.png`
  - 专业范围 1440/390/320：`ph026-stage5-before-full-{1440,390,320}-{finance,project-finance}.png`
- After（本 PR HEAD）：
  - 设置：`ph026-stage5-after-1440-settings.png`、`-settings-rates.png`、`-settings-audit.png`、`-audit-alias.png`；390/320：`-{390,320}-settings.png`、`-settings-audit.png`
  - 工作台：1440/390/320 `-dashboard.png`；无 JS `-dashboard-nojs.png`（全部浏览器检查上下文均以 `reduced_motion="reduce"` 运行）
  - 图表（专业范围）：1440/390/320 `-full-{1440,390,320}-finance.png`（现金预测）、`-full-{1440,390,320}-project-finance.png`（成本燃尽）
  - 200% 缩放：`ph026-stage5-after-zoom200-{settings,settings-audit,dashboard,full-finance}.png`

## 4. 实现边界（本切片未改变）

- 设置现有维护操作、校验、`dry_run`/`request_token`/版本冲突与消息提示保持不变；失败 POST 固定回成员页签并保留输入。
- 权限模型不变：设置仍限 owner/finance；`/audit/` 对 owner/finance 改为跳转设置审计页（两者原本都可访问，无权限扩张），auditor 仍只能看独立只读审计页；pm/member 仍 403。
- 审计仍为追加式只读，不提供修改/删除；筛选仅暴露 `AuditEvent` 真实字段（对象/操作/操作者），不虚构“来源”或时间范围控件。
- 图表数值全部来自服务端既有行（`cashflow` 的 period/in_cents/out_cents/balance_cents、燃尽图既有 `cost_burndown`），前端只做几何映射，不重算财务口径；不引入图表库/远程脚本/canvas/渐变/动画。
- 金额、工时、现金、权限口径与既有路由不变；阶段①②③④ 页签协议与业务服务未回归。

## 5. 全量测试与 CI

- 本机全量 `manage.py test tests`（最终 HEAD）：**1929 tests，2 failures + 76 skips**。两项失败均为既有 macOS 本机环境基线（与 main 一致，非本切片引入）：
  1. `tests.test_delivery_execution.ExecutorIntegrationTests.test_adapter_sandbox_callbacks_and_persisted_lifecycle`（macOS `/private/var` 与 `/var` 符号链接前缀差异）
  2. `tests.test_delivery_execution_sandbox.SandboxConstructionTests.test_partial_home_components_and_intra_path_quantifiers_are_rejected`（macOS 沙箱 IsolationError 断言差异）
- CI：9 项必需检查 + CodeQL 随 PR 运行，以 CI 双域结果为准；独立复核作用于最终 HEAD。

## 6. 独立复核与修复

- 独立复核（reviewer，最终 HEAD `676cddb`，PR 评论）：**approve**，无 blocking finding；4 项非阻塞观察见下。
  - 复核独立复现：30 项新测试 OK、定向 16 模块 247 tests OK（skipped=1）、全量 1929 tests（2 项既有 macOS 基线失败 + 76 skips）、浏览器 73/73 PASS（微版 + 专业范围各自起服务）、6/6 变异检查可检出、截图 SHA-256 与 ticket 捕获一致、`check_pr`（唯一 Queue: Q-50）/候选元数据/前端门禁通过。
  - 复核评论：`https://github.com/lakii2982/project-hub/pull/344#issuecomment-6734429257`。
- 非阻塞观察与处理：
  1. `_audit_context` 在每次设置 GET（含成员/费率页签）都执行 `verify_audit` 全链扫描，量大有性能风险 → 记为已知限制（§7），本切片不改变审计链校验语义。
  2. `/audit/` 对 owner/finance 的 302 丢弃原 query 筛选参数 → 记为已知限制；筛选在设置页审计页签完整可用。
  3. 减少动态效果证据文件命名曾指向 720 代理上下文 → 已删除冗余重名文件，并在 §2/§3 说明全部检查上下文均为 `reduced_motion=reduce`。
  4. 本节占位已按复核结果补全。

## 7. 未执行项与边界

- `not_run`：真实浏览器 200% 缩放（以 720 CSS px 视口等价代理，已在 §2 说明方法与近似性）；真实 PostgreSQL 并发（本切片无业务写路径变化）。
- 微版工作台不显示财务预测图属既有范围设计（阶段①② 已定），本切片未改变；图表证据取自专业范围页面并在 PR 中说明。
- 审计“来源”筛选未实现：`AuditEvent` 无 source 字段，按契约只暴露后端真实支持的条件。
- 已知限制：设置页每次 GET 都执行 `verify_audit`（与旧 `/audit/` 同成本，但设置页访问更频繁）；`/audit/` 的 302 跳转会丢弃原查询筛选参数（筛选在设置审计页签可用）。两者均不改变审计数据与权限语义。
