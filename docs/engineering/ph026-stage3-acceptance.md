# PH-026 阶段③ 验收记录（项目与项目详情页签）

- 日期：2026-11-09（Asia/Shanghai）
- 分支：`feat/Q-48-ph026-stage3`；真实 base：`origin/main = 448b7b3`（PR #335 阶段①② 合并后）
- 演示数据：`manage.py seed_demo` 演练组织（行远技术 · 演练工作区），账号 owner（陈予安/公司负责人），本地 SQLite `runtime/pmo.sqlite3`
- 依据：`docs/engineering/visual-system.md` §4.2、§5.1–5.4、§9 与 `frontend-standards.md` §3–§4、§13.2 阶段③；范围见 `project/QUEUE.md` Q-48

## 1. 自动化命令（真实命令与退出码，cwd=仓库根）

| 命令 | 结果 |
| --- | --- |
| `.venv(py3.12)/python manage.py test tests.test_ph026_project_tabs tests.test_ph026_project_list tests.test_project_detail tests.test_q34_project_finance -v 1` | 54 tests OK，exit 0 |
| `…/python manage.py test tests.test_plugins -v 1` | 15 tests OK，exit 0（含 2 项新增：插件提醒 URL 接受已知 `?tab=`、拒绝任意 query） |
| `…/python manage.py test tests.test_governance -v 1` | 9 tests OK，exit 0（队列 20 项、Q-48–Q-50 结构校验） |
| `…/python manage.py test tests.test_ph026_dashboard_tabs tests.test_q33_dashboard tests.test_micro_scope -v 1` | 通过（阶段①② 回归无变化） |
| `node --check static/app.js` | exit 0 |
| `grep` 门禁：`static/app.css` v1 变量（`--green/--muted/--ink/--line/--bg/--surface/--orange/--radius/--shadow/--sidebar/--text`）、裸 hex 色值、`font-size` < 12px 字面量 | 0 命中 |
| `diff docs/engineering/ui-tokens.css static/ui/ui-tokens.css` | 输出为空（令牌同源） |
| `…/python manage.py test tests --verbosity 1`（全量，修复后 HEAD） | 1838 tests，2 failures + 76 skips；失败项与 main 既有环境基线一致（见 §5） |

## 2. 浏览器检查（独立于命令结果）

- 浏览器：Chromium（Playwright），视口 1440×900 / 390×844 / 320×700，`device_scale_factor=2`，`reduced_motion=reduce`
- 路由：`/projects/`（含筛选态与筛选空态）、`/projects/1/`（概览/关键任务/里程碑/项目财务/风险/团队 6 个 `?tab=` 深链）
- 结果（`evidence/verify_stage3.py`，82 项检查全部 PASS）：
  - 3 视口 × 10 页面：`body`/document 横向滚动 = 0、console error = 0、失败请求 = 0
  - 服务端渲染：每个 `?tab=` 直达时仅目标 `[role=tabpanel]` 可见，其余面板带 `hidden`；对应页签 `aria-selected="true"`、`tabindex="0"`，其余 `false`/`-1`
  - 非法 `?tab=bogus` 回退概览；无 JS 时页签为真实链接（`?tab=…`）、`?tab=milestones`/`?tab=finance` 服务端渲染正确面板
  - 键盘：←/→/Home/End 自动激活并同步 URL，焦点保留在页签；浏览器前进/后退恢复正确页签；鼠标点击切换面板不重载页面
  - 项目列表：关键词/阶段/负责人筛选控件、结果计数、筛选空态（保留条件 + 清除筛选）与初始空态（新建项目入口）；≤768 表格按 `data-label` 转行卡
- 成员（无财务权限）浏览器与测试断言：项目列表合同额/毛利/缺口显示“—”，页面不出现金额；项目详情无财务页签与财务面板，`?tab=finance` 回退概览。

## 3. 证据截图（`docs/screenshots/ph026-stage3-*`，同场景同视口同主题）

- Before 基线：`origin/main` = `448b7b3`（阶段②合并后、本切片前）+ seed_demo，owner 登录
  - `ph026-stage3-before-1440-projects.png` / `-1440-project-detail.png`；390/320 同名档
- After（本 PR HEAD）：
  - 项目列表：`ph026-stage3-after-1440-projects.png`、`-1440-projects-filtered.png`、`-1440-projects-empty.png`；390/320 各一张
  - 项目详情六页签：`ph026-stage3-after-1440-project-detail.png`（概览）与 `-tasks/-milestones/-finance/-risks/-team/-fallback.png`；390 概览 + 关键任务 + 项目财务；320 概览
  - 无 JavaScript：`ph026-stage3-after-1440-project-detail-nojs-finance.png`

## 4. 实现边界（本切片未改变）

- 业务数据、金额/权限/财务口径：项目列表与详情的合同额、毛利、缺口沿用 Q-33/Q-34 共享 1.0 口径（`core.project_finance` 只读聚合）；未新增写路径。
- 既有路由不变；写操作仅把重定向目标从老 hash 页签改为 `?tab=` 所属页签（任务→关键任务、里程碑→里程碑、风险→风险、合同额变更/单据/冲销→项目财务、阶段变更→概览；无财务权限成员提交单据回「我的费用」）。
- 旧 hash 深链收口（独立复核 B1）：`pmo_plugins/billing/templates/billing/billing.html` 的「返回项目收益与现金」改为 `?tab=finance`；`pmo_plugins/project_health/plugin.py`、`pmo_plugins/delivery_watch/plugin.py` 的提醒 URL 改为 `?tab=tasks|milestones|finance`；`core/plugin_runtime.py` 可信 URL 校验由仅允许 fragment 放宽为「已知 `?tab=` 值或 fragment」。
- `static/ui/ui-tokens.css` 未修改；非本切片的页面（工时/费用/现金流/设置/审计/图表）留给 Q-49/Q-50。

## 5. 全量测试与 CI

- 本机全量 `manage.py test tests`（修复后 HEAD）：**1838 tests，2 failures + 76 skips**。失败项为既有 macOS 本机环境基线：
  1. `tests.test_delivery_execution.ExecutorIntegrationTests.test_adapter_sandbox_callbacks_and_persisted_lifecycle`（macOS `/private/var` 与 `/var` 符号链接前缀差异，main 同样复现）
  2. `tests.test_delivery_execution_sandbox.SandboxConstructionTests.test_partial_home_components_and_intra_path_quantifiers_are_rejected`（macOS 沙箱 IsolationError 断言差异，main 同样复现）
- 过程记录：早前一次全量与一次 `tests.test_demo_script` 整类运行中，3 项演练脚本测试因本机子进程服务器 `/health/` 20s 等待超时失败；同样命令单品隔离重跑通过，`origin/main`（448b7b3）worktree 整类 15 项通过，最终全量运行未复现——判定为本机启动时序波动，与本次前端变更无共享代码路径。
- CI：9 项必需检查 + CodeQL 随 PR 运行，以 CI 双域结果为准；独立复核作用于最终 HEAD。

## 6. 独立复核与修复

- 首轮独立复核（reviewer-stage3，HEAD `153a796`，PR 评论）：request-changes。
  - B1（blocking）：迁移后仍有旧 hash 深链（billing 模板、project_health/delivery_watch 插件提醒 URL），点击落到概览；与“旧 #finance 链接已移除”的表述冲突。
  - N1（non-blocking）：本文件 §1 表格测试数字与实际不一致（52/1834/5 应为 54/1835/2/76）。
  - N2（non-blocking）：无财务权限成员提交单据后回退概览而非「我的费用」。
- 修复：billing 与两个插件的 URL 改为 `?tab=`，`core/plugin_runtime.py` 可信 URL 校验同步放宽（仅接受已知 tab 或 fragment）；`new_object`/`upload_evidence` 对无财务权限成员回到 `expenses` 页签；本文件数字更新；新增 3 项回归测试（member 单据回退、插件 URL 接受/拒绝）。
- 修复后 HEAD 需复核一次（复核作用于当前 HEAD）；复核结论见 PR #340 评论。

## 7. 未执行项与边界

- `not_run`：真实 PostgreSQL 并发场景（本切片无业务写路径变化）；200% 文字缩放与 reduced-motion 的逐页矩阵（阶段⑤统一走查，本切片仅 `reduced_motion=reduce` 浏览器截图）。
- 已废弃：项目详情旧 hash 页签 JS（`data-project-tabs`）在本切片移除；仓库内旧 `#finance/#tasks/#milestones` 深链（模板、视图重定向、插件提醒）已全部迁移为 `?tab=`。
