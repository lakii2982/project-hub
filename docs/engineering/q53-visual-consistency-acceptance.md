# Q-53 前端视觉一致性回归验收记录（按钮、重叠与错位）

- 日期：2026-11-09（Asia/Shanghai）
- 分支：`feat/Q-53-visual/agent/ui-consistency-0b380dc1fecd`；真实 base：`origin/main = 80d9681b1da852a37d6ce56a1eac4c01243f150c`（PH-026 阶段⑤ 与 Q-51/Q-52 合并后）
- 演示数据：用户正在使用的本地演练库副本（`seed_demo` 演练组织「行远技术 · 演练工作区」，4 个项目、6 个账号）；owner（陈予安/公司负责人）；`PMO_SCOPE=micro`、`PMO_ENV=development`、SQLite，仅 127.0.0.1
- 依据：`docs/engineering/frontend-standards.md` §3.1/§3.4/§4.1/§7/§10；`docs/engineering/visual-system.md` §2.1/§2.4/§2.6/§5.3/§8.1；`project/QUEUE.md` Q-53
- 用户反馈原文（2026-11-09）：「多个页面存在按钮样式不统一、页面元素重叠、错位的情况，前端需要一次规范」

## 1. 修复清单（逐项可复核）

| # | 缺陷（修复前可观察） | 根因 | 修复 |
| --- | --- | --- | --- |
| 1 | **1024px 整页内容被 240px 固定侧栏覆盖**（页标题、页签、指标卡左半被遮住） | `@media (max-width:1024px)` 把 `.app-shell` 左边距清零；`@media (min-width:1024px)` 只恢复侧栏显示，未恢复主区几何，1024px 恰好同时命中两段 | 在 `min-width:1024px` 段同时恢复 `.app-shell` 左边距、顶栏与主区内边距 |
| 2 | 手机宽度指标金额被拆行（如「205,811.3 / 2」「130,00 / 0.00」） | ≤640px 对 `.metric > strong` 设 `white-space: normal; overflow-wrap: anywhere` | 删除该拆行规则（金额保持 `nowrap` + `tabular-nums`）；≤480px 指标卡改单列，空间不足时先减列不拆数 |
| 3 | 设置（员工档案/账户与权限/成本费率/审计日志）、审计、项目财务（明细/收付款计划/账簿）、导入预览、缺费率缺口等窄屏表格被压成逐字竖排 | 这些通用表只有桌面样式，≤768px 仍按表格列宽压缩 | 新增共享 `.table-rows` 行卡模式（字段名 + 值），给上述表加 `data-label`；沿用项目/现金流行卡的同一阅读顺序 |
| 4 | 行卡内第二段值掉进字段名列、徽章被拉伸成整行（如「收款」占满整行、仪表盘缺口徽章全宽） | 行卡 `td` 为 grid 容器，多子元素被自动放置到字段名列；徽章作为 grid item 默认 stretch | 行卡 `td` 的直接子元素统一落在值列（`grid-column:2`），徽章 `justify-self:start`；仪表盘缺口单元格改 flex-wrap |
| 5 | 项目详情页头把「推进正常 / 执行」状态徽章与操作按钮并排，看起来像两个按钮 | 状态徽章放在 `.actions` 操作组内 | 徽章移入标题下 `.heading-status` 独立成行，操作组只保留按钮 |
| 6 | 简化导出页四个并列蓝色主按钮 | 四个导出链接均用 `.button primary` | 降为次要按钮（`accent` 不滥用于多个并列按钮） |
| 7 | 系统状态页导航高亮「设置」而非「系统状态」 | `system_status_page` 的 `nav` 上下文写为 `settings` | 改为 `status` |
| 8 | 320px 首次启用向导步骤胶囊被挤成椭圆、文字逐字换行 | ≤768px 步骤为两列，320px 单元格放不下编号+标签+「已完成」 | ≤480px 步骤改单列 |
| 9 | 工作台「默认开票」在 320px 被裁掉 | `.section-nav` 单行 nowrap 且溢出裁切 | `.section-nav` 允许换行，不再裁切 |
| 10 | 设置页「保存设置 / 保存成员变更」按钮横贯整列，与其他页内容宽度按钮不一致 | `.stack-form` 纵向拉伸直接子按钮 | `.stack-form > .button` 按内容宽度（`align-self:flex-start`），`full-width` 显式类不受影响 |

## 2. 自动化命令（真实命令与退出码，cwd=仓库根）

| 命令 | 结果 |
| --- | --- |
| `python manage.py check` | exit 0（127 项既有告警，无新增错误） |
| `python manage.py makemigrations --check --dry-run` | exit 0（No changes detected） |
| `python manage.py verify_integrity --json` | exit 0，`{"ok": true, "errors": [], "organizations": 1, "entries": 53}` |
| `python manage.py test tests.test_ph026_chart_forecast -v 1` | 8 tests OK（模板加 `data-label` 后同步更新该测试的逐行 HTML 断言） |
| `python manage.py test tests --verbosity 1`（全量，最终 HEAD） | 见 §5 |
| `python -m unittest tests.test_projectctl tests.test_governance tests.test_delivery_policy` | 64 tests OK，exit 0 |
| `python scripts/projectctl.py check` / `queue` | `ok=true` / `ok=true, issues=[]`（Q-53 已定义） |
| `node --check static/app.js` | exit 0 |
| `diff docs/engineering/ui-tokens.css static/ui/ui-tokens.css` | 输出为空（令牌同源，本次未改令牌） |
| CSS 门禁：v1 变量、裸 hex 色值、`font-size` < 12px 字面量 | 0 命中 |
| `python scripts/package.py` + `python scripts/check_candidate_metadata.py` | 重建候选源码包元数据后 `ok=true` |

## 3. 浏览器检查（独立于命令结果）

- 浏览器：Chromium（Playwright），视口 **1440×900 / 1024×800 / 768×900 / 390×844 / 320×640**，owner 登录，light 主题；路由 22 条（工作台三页签、项目列表/详情/编辑、工时、费用、现金流三页签、资金账户、设置三页签、系统状态、审计、到期提醒、简化导出、首次启用）。
- 结果（`evidence/verify_visual.py`，**110/110 checks PASS**）：
  - 每个路由每个视口：`documentElement.scrollWidth <= clientWidth` 且 `body` 无横向滚动；console error = 0、page error = 0、HTTP ≥500 = 0。
  - **1024px 壳层**：侧栏可见且宽 240px，`.app-shell` 起点 x=240，主区不再被覆盖。
- 未改页面回归对比：对 1440px 全部路由做 Before/After 像素差分，除本记录 §1 明确变更的 4 个页面（导出、项目详情、设置、系统状态）与开发态 404 页 URL 文本外，其余页面逐像素一致；390/320 的差异均落在 §1 清单对应页面。
- 专项复测：
  - 390px 设置/审计/项目财务/导入预览行卡宽度 356px、字段名与值同列；导入预览以真实 CSV 上传渲染 8 列行卡。
  - 390px 项目详情页头仅剩 3 个操作按钮，状态徽章独立成行。
  - 390px 简化导出按钮为次要样式、高 38px（与其他页按钮一致）。
  - 390px 系统状态页导航高亮「系统状态」。
  - 专业范围（`PMO_SCOPE=full` 同库）：`/assistant/`、`/connections/` 空态渲染正常；`/finance/` 13 周现金预测表转行卡（预计流入/预计流出/预计累计净流入），0 横向滚动、0 console error。

## 4. 证据截图（`docs/screenshots/q53-*`，同场景、同视口、同主题、同演示库）

- **Before**：`origin/main = 80d9681` + 用户正在使用的演示库，owner 登录，light。
- **After**：本 PR HEAD，同库副本、同账号、同视口、同主题。
- 成对文件（before/after 同名后缀）：
  - `q53-{before,after}-1024-dashboard.png`（侧栏覆盖主区 → 恢复 240px 主区）
  - `q53-{before,after}-390-dashboard.png`、`q53-{before,after}-320-dashboard.png`（金额拆行、缺口徽章拉伸、缺费率缺口竖排 → 单列指标、内容宽度徽章、行卡）
  - `q53-{before,after}-390-settings.png`（表格逐字竖排、整列主按钮 → 行卡、内容宽度按钮）
  - `q53-{before,after}-390-audit-viewport.png`（审计表挤压 → 行卡；同视口 390×1400 裁切）
  - `q53-{before,after}-390-project-finance.png`（明细/收付款计划/账簿挤压 → 行卡；390×1600 裁切）
  - `q53-{before,after}-390-cash.png`（收款/付款徽章拉伸、说明第二段错位 → 内容宽度徽章、值列对齐）
  - `q53-{before,after}-390-projects.png`（毛利徽章掉进字段名列 → 值列对齐）
  - `q53-{before,after}-1440-project-detail.png`（状态徽章混入操作区 → 独立成行）
  - `q53-{before,after}-1440-exports.png`（四个主按钮、39px 按钮 → 次要按钮、38px）
  - `q53-{before,after}-1440-status.png`（导航高亮「设置」→「系统状态」）
  - `q53-{before,after}-320-setup.png`（步骤椭圆 → 单列胶囊）

## 5. 全量测试与 CI

- 本机全量 `manage.py test tests`（最终 HEAD）：**1929 tests，2 failures + 76 skips**。两项失败均为既有 macOS 本机环境基线（在 pristine `origin/main = 80d9681` 上逐项复现，非本切片引入）：
  1. `tests.test_delivery_execution.ExecutorIntegrationTests.test_adapter_sandbox_callbacks_and_persisted_lifecycle`（macOS `/private/var` 与 `/var` 符号链接前缀差异）
  2. `tests.test_delivery_execution_sandbox.SandboxConstructionTests.test_partial_home_components_and_intra_path_quantifiers_are_rejected`（macOS 沙箱 `IsolationError` 断言差异）
- CI：9 项必需检查随 PR 运行，以 CI 双域（SQLite/PostgreSQL）结果为准；独立复核作用于最终 HEAD。

## 6. 独立复核与修复

- 待复核记录（复核人、HEAD SHA、结论、finding、PR 评论链接）在复核完成后补入本节；作者不自审。

## 7. 未执行项与边界

- `not_run`：真实 PostgreSQL 并发（本切片无业务写路径变化）；200% 文字缩放专项（本切片未改字号/控件高度与壳层结构，沿用 Q-50 已记录的 720 CSS px 等价代理结论）；reduced-motion 专项（未新增动画）；Safari/Firefox 浏览器（本机仅有 Chromium）；L5 真实环境。
- 全站范围说明：`/assistant/`、`/connections/`、`/finance/` 13 周预测表在微版演示中不可达（微版停用/专业范围），本次只做同一行卡模式的模板改造，并以专业范围同库服务验证空态/行卡渲染；其数据行外观未纳入微版 Before/After 截图。
- 已知限制：390px 审计页在 207 条日志下按行卡展开会显著变长（行卡模式的固有取舍，分页不在本切片范围）；仪表盘毛利排行表在 1024px 仍有既有局部横滚（`min-width: 820px`，属允许的表格容器内滚动，本切片未改其信息结构）。
- 未改变：业务路由、金额/工时/权限/期间口径、页签结构与默认项、令牌文件；`core/views.py` 仅修正 `nav` 上下文键值。
