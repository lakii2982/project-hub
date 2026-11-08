# PH-026 阶段①② 验收记录（壳层 + 工作台页签）

- 日期：2026-11-08/09（Asia/Shanghai）
- 分支：`agent/frontend-design-spec-91cefe33de6d-ph026`（基于 PR #333 分支，规范令牌与参考包来自该分支）
- 演示数据：`manage.py seed_demo` 演练组织（行远技术 · 演练工作区），账号 owner（陈予安/公司负责人），本地 SQLite
- 依据：`docs/engineering/visual-system.md` §2–§4.1、`frontend-standards.md` §3–§4、§7、§8、§11、§13.2 阶段①②

## 1. 自动化命令（真实命令与退出码）

| 命令（cwd=仓库根） | 结果 |
| --- | --- |
| `…/q37-invoicing/repo/.venv/bin/python manage.py test tests --verbosity 1` | 全量套件通过（详见 §5） |
| `…/q37-invoicing/repo/.venv/bin/python manage.py test tests.test_ph026_dashboard_tabs tests.test_q33_dashboard tests.test_governance` | 34 tests OK，exit 0 |
| `…/q37-invoicing/repo/.venv/bin/python manage.py test tests.test_ph026_dashboard_tabs tests.test_q41_exports_reminders` | 22 tests OK，exit 0（含 member 空态断言修正后的 q41 回归） |
| `…/q37-invoicing/repo/.venv/bin/python manage.py check` | exit 0（127 条均为存量 W003/W342 警告，与本变更无关） |
| `…/q37-invoicing/repo/.venv/bin/python manage.py makemigrations --check --dry-run` | 无变更（本 PR 无模型改动） |
| `diff docs/engineering/ui-tokens.css static/ui/ui-tokens.css` | 输出为空（令牌同源） |
| `node --check static/app.js` | exit 0 |
| grep 门禁：`static/app.css` 中 v1 变量（--green/--muted/--ink/--line/--bg/--surface/--orange/--radius/--shadow/--sidebar/--text）、裸 hex/rgb 颜色、`font-size` < 12px 字面量 | 全部 0 命中 |

## 2. 浏览器检查（独立于命令结果）

- 浏览器：Chromium（Playwright），视口 1440×900 / 390×844 / 320×700，`device_scale_factor=2`
- 路由：`/login/`、`/`、`/projects/`、`/projects/4/`、`/time/`、`/expenses/`、`/billing/`、`/cash/`、`/settings/`、`/audit/`、`/status/`
- 结果：
  - 10 路由 × 3 视口 console error = 0、失败请求 = 0、`body`/document 横向滚动 = 0（发现 `.summary-band` 在 390/320 溢出一次，已修复并复测归零）
  - 壳层：≥1024 白色 240px 侧栏 + 32px 主区留白 + 内容最大 1440px；<1024 侧栏隐藏、菜单按钮出现、留白 16px；顶栏白色 60px 非 sticky；画布 `rgb(247,249,253)`
  - 页签（JS）：`/?tab=due` 直达 due 面板且其余面板带 `hidden`；焦点在页签上按 ←/→ 自动激活（URL 同步 `?tab=activity` 等）；Home/End 首尾；前进/后退恢复正确页签、面板与 `aria-selected`；键盘焦点保留在页签
  - 页签（无 JS）：三个页签为真实链接，`/?tab=activity`、`/?tab=due`、`/?tab=bogus`（回退 overview）服务端各渲染唯一可见面板
  - 抽屉（390）：菜单按钮打开模态 `dialog`，焦点进入抽屉不逃逸；Esc、关闭按钮、遮罩点击均可关闭且焦点回到菜单按钮；点击导航链接关闭并导航
  - 抽屉（无 JS 390）：抽屉以静态面板渲染在页首，导航可达；关闭按钮隐藏
  - 跳至主要内容：首个 Tab 聚焦 skip-link，Enter 后焦点到 `#main`
  - 收入口径切换 `.section-nav` 当前项 = 品牌蓝文字 + 2px 底部指示条
- 交互断言脚本累计 33 项检查全部 PASS（22 + 11，含两次修正后的复跑）。

## 3. 证据截图（docs/screenshots/ph026-*，同场景同视口同主题）

- Before 基线：05a8f78（=PR #333 HEAD，规范已入但产品页未重构）+ seed_demo，owner 登录
- After：本分支 HEAD + 同库同账号
- 覆盖：dashboard/projects/project-detail/time/expenses/billing/cash/settings（1440×8）、dashboard-due 页签（1440）、drawer-open（390）、dashboard/time/billing（390）、dashboard/time（320）及对应 Before（1440×8、390/320 dashboard）

## 4. 状态覆盖（七态）

- 本切片为布局与壳层迁移：面板数据全部沿用既有服务端渲染（默认/空/筛选空/错误/不可用文案与缺口徽章未改动，见 Q-33/36/37/38/41 既有测试与页面）。
- 新增状态面：页签面板 `hidden`（非当前）；到期提醒页签对无权限角色渲染「暂无到期事项」空态（区别于数据为空的「当前没有到期提醒」）。
- 加载态：无新增异步区域，`not_run`（无新增异步加载）。

## 5. 全量测试与 CI

- 本机全量 `manage.py test tests`：1819 tests，2 failures + 76 skips，两次独立运行结果一致；失败项为 `tests.test_delivery_execution` 与 `tests.test_delivery_execution_sandbox` 各 1 项（macOS `/private/var` 符号链接前缀比较、沙箱 IsolationError 断言），在 main（8f0ed2c）同样复现且 CI 通过，属本机环境差异（与 PR #333 记录一致），与本前端变更无关；其余 1817 项全部通过。
- CI：9 项必需检查 + CodeQL 随 PR 运行，以 CI 双域结果为准；独立复核作用于最终 HEAD。

## 6. 未执行项与边界

- `not_run`：真实 PostgreSQL 并发场景（本 PR 无业务写路径变化）；200% 文字缩放逐页走查（规范字阶落地后抽查 dashboard/projects 无遮挡，完整矩阵留待阶段⑤统一走查）；`prefers-reduced-motion`（新动效仅 150–200ms 过渡与 0ms 降级令牌，无循环动画；专项走查留待阶段⑤）。
- 阶段③–⑤（项目与详情页签、工时/费用/现金流页签、设置与图表）不在本切片；项目详情沿用现有 hash 页签实现。
