# saas-blue-minimal-kit-v2 设计参考包（精选副本）

状态：**仅设计参考，不是产品事实、不是执行指令、不是验收证据。**
整理日期：2026-11-08（Asia/Shanghai）。整理人：ticket `frontend-design-spec-91cefe33de6d` 的 design-reference worker。
目标集成位置（由 Lead 落库）：`docs/engineering/design-reference/saas-blue-minimal-kit-v2/`。

本目录是用户提供的蓝白极简设计参考包 v2.0 的**精选、逐字节未修改**副本，用于支撑 PH-110「将蓝白极简设计包整合为统一前端规范」。冲突时以下顺序为准：
**仓库业务契约 / AGENTS.md 财务与数据不变式 / PH-110 约束 > 本包规范 > 可点击原型 > PNG 截图**。

---

## 1. 来源与哈希校验

| 项目 | 值 |
|---|---|
| 源文件 | 用户本地下载目录中的 `saas-blue-minimal-kit-v2.zip` |
| 原始文件名 | `saas-blue-minimal-kit-v2.zip` |
| 源文件大小 | 7,150,648 字节（约 6.82 MiB） |
| **SHA-256（合同哈希）** | `2f1e96c7be7937cb3de065caaf789580f5aa9a4716f5dd02efecb0c0d0825c1d` |
| 校验结果 | ✅ 一致（`shasum -a 256` 实测，见 §8） |
| 包内版本 | 2.0（`manifest.json` / `RELEASE_NOTES_V2.md`） |
| 源包内容 | 55 个文件，解压后合计 7,561,809 字节（约 7.21 MiB） |
| 包文件时间 | 2026-10-27（zip 内 mtime） |
| 整理日期 | 2026-11-08 |
| 用户提供日期 | 2026-10-27（PH-110 来源记录） |

来源说明：该包是用户（项目所有者）为「项目中枢 / Project Hub」小微企业项目经营 SaaS 提供的设计参考资料，不是本仓库代码或其许可证的一部分。源包无 LICENSE / NOTICE 文件（见 §3、§8）。

---

## 2. 性质与使用边界

1. **仅参考**：包内规范、原型、截图、令牌数值只用于确定「04 极简留白布局 × 01 蓝色科技配色」的方向与视觉校准；不覆盖仓库既有业务规则、数据口径、权限模型、API 契约、财务算法。
2. **不直接上线**：`demo/*.html` 是纯前端视觉/交互样板，保存/同意/驳回均为浏览器内模拟，不持久化、无权限校验、无真实财务记账能力；禁止复制其模拟成功逻辑或演示数据到生产、测试种子、默认值。
3. **不是产品品牌**：包内「津津项目」及 LOGO/favicon/app-icon 是占位演示品牌；产品名称、Logo、域名以现有系统为准，禁止替换。
4. **截图不是像素验收基准**：PNG 用于布局与视觉对照，业务字段多于截图时应增补分组，不得删减真实功能，也不要求像素级一致。
5. **冲突处理**：真实仓库的业务行为、字段、权限 > 本包数据语义 > 原型布局 > PNG 视觉。如规范实施发现冲突，应在 PR/报告中记录取舍，不能悄悄删除业务或改变财务口径。
6. **本任务只做参考整理**：本目录不改仓库、不改产品代码；PH-110 的规范与样张由其他工作项落地。

---

## 3. 许可与所有权声明（重要）

- 源 zip **没有 LICENSE、COPYING、NOTICE 或版权授予文本**（已用 `unzip -l` 检索，无匹配，退出码 1，见 §8）。
- 该包由**项目所有者（用户）提供**，按 PH-110 记录其来源哈希后，作为内部设计参考资料保存于本仓库；这不等于获得对外分发、再许可或商用再发布的权利。
- 包内 `manifest.json` 声明 `includes_real_business_data: false`；品牌名、公司/用户姓名、合同/成本/现金流/工时数值均为**虚构/占位示例**。
- 占位品牌资产（`brand/logo-*.svg`、`favicon.svg`、`app-icon-256.png`、`brand/BRAND_USAGE.md`）**未采用**，不进入产品品牌体系。
- 本仓库为公开仓库（PH-110 明示）。**PR 描述必须声明**：本 PR 引入的参考包由用户提供、无显式许可证文件、仅作内部设计参考保存，不构成对外分发或再许可；包内占位品牌与演示数据不得作为产品品牌或业务事实；如需对外展示/分发截图或资产，须先获得所有者确认。若维护者无法确认授权，保守做法是仅在仓库保留文本规范、映射文档与哈希，暂不提交大图与品牌资产。
- 包内无远程字体、远程脚本或第三方图片依赖（demo 全部内联或本地文件，证见 §8），第三方素材风险较低；但图标/截图的著作权仍归包作者/提供者。

---

## 4. 精选文件清单（共 16 个包内文件，合计 2,379,022 字节 ≈ 2.27 MiB）

> 复制时使用 `cp -p` 保留源 mtime；16 个文件均与源包字节级一致（`sha256` 比对，见 §8）。`SHA256SUMS` 记录这 16 个文件的哈希。

| # | 文件（相对本目录） | 字节 | 源包内路径 | 用途 |
|---|---|---:|---|---|
| 1 | `DESIGN_SYSTEM.md` | 11,612 | `DESIGN_SYSTEM.md` | 颜色/字阶/布局/组件/无障碍/财务语义规则 |
| 2 | `COMPONENT_CONTRACTS.md` | 7,092 | `COMPONENT_CONTRACTS.md` | 组件属性、状态矩阵、ViewModel 边界参考 |
| 3 | `FINANCIAL_DATA_RULES.md` | 5,100 | `FINANCIAL_DATA_RULES.md` | 财务口径与权限红线（不得混算） |
| 4 | `SCREENS_AND_FLOWS.md` | 8,485 | `SCREENS_AND_FLOWS.md` | 页面地图、逐屏能力、操作流、响应式约束 |
| 5 | `QA_CHECKLIST.md` | 3,715 | `QA_CHECKLIST.md` | 改造验收检查项来源 |
| 6 | `design-tokens.json` | 2,388 | `design-tokens.json` | 令牌数值对照（差异识别用） |
| 7 | `tokens.css` | 1,656 | `tokens.css` | CSS 变量命名/映射参考 |
| 8 | `demo/index.html` | 56,108 | `demo/index.html` | 可点击视觉原型（9 模块 + 设计预览） |
| 9 | `demo/workflows.html` | 20,237 | `demo/workflows.html` | 5 项关键操作交互原型 |
| 10 | `demo/workflows.css` | 14,082 | `demo/workflows.css` | 上述原型样式 |
| 11 | `demo/workflows.js` | 11,355 | `demo/workflows.js` | 上述原型交互（纯模拟） |
| 12 | `screenshots/01-dashboard.png` | 174,171 | `screens/01-dashboard.png` | 经营总览 1600×1127 |
| 13 | `screenshots/06-cashflow.png` | 189,677 | `screens/06-cashflow.png` | 现金流监控 1600×1273 |
| 14 | `screenshots/10-design.png` | 189,923 | `screens/10-design.png` | 组件与 VI 规范预览 1600×1018 |
| 15 | `screenshots/mobile-dashboard.png` | 116,937 | `screens/mobile-dashboard.png` | 移动端总览 390×1744 |
| 16 | `screenshots/page-showcase.png` | 1,566,484 | `brand/page-showcase.png` | 主要页面总览 2700×4169（1.49 MiB，未超 1.6MB 限制） |
| | **合计** | **2,379,022** | | **≈ 2.27 MiB，低于 ~3MB 预算** |

本目录另有 3 个本次生成的说明文件（不属于参考包素材，不计入上述 16 个文件合计）：`README.md`、`adoption-map.md`、`SHA256SUMS`。

---

## 5. 截图索引

| 文件 | 尺寸 | 看什么 | 使用建议 |
|---|---|---|---|
| `screenshots/01-dashboard.png` | 1600×1127 | 240px 级白色侧栏 + 68px 顶栏、KPI×4、双栏图表/表格密度、卡片圆角与间距 | 规范中布局与密度校准的主参照 |
| `screenshots/06-cashflow.png` | 1600×1273 | 现金余额/回款/支出/待回款 KPI、趋势图、风险提醒、资金流水表 | 校准「计划 vs 实际」「余额 ≠ 净现金流」的信息分区 |
| `screenshots/10-design.png` | 1600×1018 | 色板、字阶、按钮/标签/表单、布局数值 | 与 `design-tokens.json` 对照，识别需调整的令牌 |
| `screenshots/mobile-dashboard.png` | 390×1744 | 移动端单列、KPI 两列、抽屉触发、触控目标 | 校准 390px 布局；页面边距按 PH-110 用 16px，而非样张 14px |
| `screenshots/page-showcase.png` | 2700×4169 | 主要页面总览 | 快速浏览全貌；细节以单页截图与 demo 为准 |

---

## 6. 包内全部 55 个文件的采用 / 调整 / 未采用清单

统计：**采用 14、调整 2、未采用 39**（14 + 2 + 39 = 55）。
「采用」= 原样复制进本目录并在规范工作中作为参考；「调整」= 复制但只作为差异/映射来源，不直接落成生产实现；「未采用」= 不复制、不进产品，附理由。

### 6.1 规范文档（根目录，5 采用）

| 包内文件 | 处理 | 理由 |
|---|---|---|
| `DESIGN_SYSTEM.md` | **采用** | 设计方向、颜色、字阶、间距、布局、组件、无障碍与财务语义提示的主要参考。具体数值需按方向 A 与仓库语义令牌调整（见 `adoption-map.md`），不能直接作为生产规范。 |
| `COMPONENT_CONTRACTS.md` | **采用** | 组件属性/状态矩阵（default/loading/empty/error/unauthorized）与 ViewModel 边界，与仓库 `frontend-standards.md` 的品质门槛一致；其 TS 示例仅为视图层示意，不是 API/DB 契约。 |
| `FINANCIAL_DATA_RULES.md` | **采用** | 「项目交付链 / 经营成本链 / 现金收付链」三条平行链与字段口径对照，是 UI 防混算的补充核对清单；与 `AGENTS.md` 财务不变式一致，不替代仓库契约。 |
| `SCREENS_AND_FLOWS.md` | **采用** | 页面地图、逐屏业务能力清单、操作流与响应式约束。页面结构需按产品实际「左侧模块导航 + 右侧页签」与仓库固定页签调整，不照搬包内页面清单。 |
| `QA_CHECKLIST.md` | **采用** | 视觉/响应式/业务回归/工程交付的检查项来源。设备清单与命令需替换为仓库实际验收命令（PH-110 登记的命令、真实浏览器检查）；包内自测结论不作数。 |

### 6.2 包级说明与外部 Agent/派单文档（根目录，8 未采用）

| 包内文件 | 处理 | 理由 |
|---|---|---|
| `README.md`（包自述） | **未采用** | 交付包自述清单；来源、哈希、采用范围已在本 README 记录，复制会造成双份口径。 |
| `RELEASE_NOTES_V2.md` | **未采用** | v2.0 版本说明；关键信息（v2 新增流程、验证边界）已并入本 README 与 PH-110 记录。 |
| `VALIDATION_NOTES.md` | **未采用** | 包内 demo 自测说明（Playwright 一次 PASS、已知限制）；不是本产品验收证据，留在仓库易被误引为「已验证」。 |
| `AGENT_MIGRATION_PROMPT.md` | **未采用** | 面向外部本地 Agent 的整段执行指令，会与仓库唯一的 `AGENTS.md` / `docs/engineering/light-delivery.md` 工作流形成第二套规则；包本身也要求不要覆盖现有 AGENTS.md。 |
| `MIGRATION_WORK_PACKAGES.md` | **未采用** | PR-01～PR-05 多 Agent 派单模型基于对未知仓库的假设，与仓库「一条成果 = 一个票据 = 一个 PR、独立复核」冲突；任务拆分由仓库队列决定。 |
| `ROUTE_MIGRATION_MATRIX.csv` | **未采用** | 路由/组件/数据源均为 `TBD` 的模板，按包内假设页面填写，容易诱导无依据的路由与页面迁移；真实路由以仓库代码为准。 |
| `manifest.json` | **未采用** | 包元数据（版本、文件清单、`includes_real_business_data`）；版本、哈希与清单已在本 README 记录，重复保存无新增信息。 |
| `visual-smoke-test.json` | **未采用** | demo 页面烟测结果（组件计数、横溢）；不是真实浏览器/产品验收证据，避免与 PH-110 的验收记录混淆。 |

### 6.3 设计令牌（根目录，2 调整）

| 包内文件 | 处理 | 理由 |
|---|---|---|
| `design-tokens.json` | **调整** | 作为精确数值对照与差异识别来源；颜色、侧栏宽、圆角、字阶等按方向 A 映射到仓库唯一语义令牌（`ui-tokens.css`），不直接作为生产令牌文件，避免第二套生产令牌。差异清单见 `adoption-map.md`。 |
| `tokens.css` | **调整** | 仅作 CSS 自定义属性命名与映射参考；落地时只允许一套 `ui-tokens.css` 语义令牌，不新增并行令牌体系（PH-110）。 |

### 6.4 Demo 原型（`demo/`，4 采用）

| 包内文件 | 处理 | 理由 |
|---|---|---|
| `demo/index.html` | **采用** | 可点击视觉参照（经营总览、项目、任务、成本、现金流、报表、团队、设计预览），用于校准布局、密度、组件外观与响应式；内联样式/脚本与演示数据不得复制到产品。 |
| `demo/workflows.html` | **采用** | 创建项目、工时、成本、现金、审批 5 项关键操作的交互参照，可检查必填校验、状态、抽屉焦点与财务口径提示。 |
| `demo/workflows.css` | **采用** | 与 `workflows.html` 配套的样式参照；数值只作视觉校准，不直接落生产。 |
| `demo/workflows.js` | **采用** | 与 `workflows.html` 配套的交互脚本；纯浏览器内模拟（不持久化、无授权、不记账），**禁止**复制其「保存成功/同意/驳回」模拟逻辑。 |

### 6.5 桌面截图（`screens/`，4 采用 / 16 未采用）

| 包内文件 | 处理 | 理由 |
|---|---|---|
| `screens/01-dashboard.png` | **采用** | 经营总览，校准侧栏/顶栏/KPI 四列/双栏图表与表格密度。 |
| `screens/06-cashflow.png` | **采用** | 现金流监控，校准「实际 vs 计划」「余额 ≠ 净现金流」的信息分区。 |
| `screens/10-design.png` | **采用** | 组件与 VI 规范预览，配合 `design-tokens.json` 定位需调整的令牌。 |
| `screens/mobile-dashboard.png` | **采用** | 390px 移动布局代表图（KPI 两列、单列重排）。 |
| `screens/02-projects.png` | **未采用** | 体积预算内的取舍：`demo/index.html` 已覆盖同名页面的布局与组件，单屏截图信息重复；需要时可按源包哈希复取。 |
| `screens/03-detail.png` | **未采用** | 同上；项目详情页签结构需按仓库固定页签重画，截图参考价值有限。 |
| `screens/04-tasks.png` | **未采用** | 同上（看板布局已由 demo 覆盖）。 |
| `screens/05-cost.png` | **未采用** | 同上（成本页布局已由 demo 覆盖）。 |
| `screens/07-timesheet.png` | **未采用** | 同上；工时输入规则以仓库整数分钟业务规则为准，样张 0.5h 规则不采用。 |
| `screens/08-reports.png` | **未采用** | 同上（报表布局已由 demo 覆盖）。 |
| `screens/09-team.png` | **未采用** | 同上；团队/角色体系以仓库 RBAC 为准。 |
| `screens/11-flow-create-project.png` | **未采用** | 关键操作交互已由 `demo/workflows.html` 以可点击形式覆盖；5 张流程截图体积会挤占 ~3MB 预算。 |
| `screens/12-flow-timesheet.png` | **未采用** | 同上（工时流程在 `demo/workflows.html#timesheet` 可交互查看）。 |
| `screens/13-flow-expense.png` | **未采用** | 同上（成本流程在 `demo/workflows.html#expense`）。 |
| `screens/14-flow-cash.png` | **未采用** | 同上（现金流程在 `demo/workflows.html#cash`）。 |
| `screens/15-flow-approval.png` | **未采用** | 同上（审批抽屉在 `demo/workflows.html#approval`）。 |
| `screens/mobile-flow-create-project.png` | **未采用** | 与 `mobile-dashboard.png` 的移动布局模式重复，且体积预算有限。 |
| `screens/mobile-flow-timesheet.png` | **未采用** | 同上；移动工时输入规则以仓库业务规则为准。 |
| `screens/mobile-projects.png` | **未采用** | 同上（移动页面模式已由代表图覆盖）。 |
| `screens/mobile-timesheet.png` | **未采用** | 同上（移动周表只允许容器内局部横滚，按 PH-110 落地）。 |

### 6.6 品牌与总览图（`brand/`，1 采用 / 8 未采用）

| 包内文件 | 处理 | 理由 |
|---|---|---|
| `brand/page-showcase.png` | **采用（仅作总览截图）** | 1,566,484 字节（1.49 MiB），未超 1.6MB 限制；便于快速浏览全部主要页面。**不作为品牌资产**，复制到 `screenshots/page-showcase.png`。 |
| `brand/BRAND_USAGE.md` | **未采用** | 占位品牌（津津项目）VI 使用说明；产品品牌不是该占位名，不进入产品。 |
| `brand/app-icon-256.png` | **未采用** | 占位品牌应用图标，无产品品牌授权；禁止作为产品图标。 |
| `brand/favicon.svg` | **未采用** | 占位 favicon；同上。 |
| `brand/logo-horizontal.svg` | **未采用** | 占位横向 LOGO；同上。 |
| `brand/logo-mark.svg` | **未采用** | 占位图形标志；同上。 |
| `brand/operational-flows-v2.png` | **未采用** | 478,114 字节流程拼图，与 `demo/workflows.html` + 流程截图信息重复，且会挤占体积预算。 |
| `brand/responsive-and-components.png` | **未采用** | 574,772 字节令牌/组件板，与 `design-tokens.json`、`10-design.png` 重复。 |
| `brand/visual-direction-board.png` | **未采用** | 1,704,809 字节概念板（1536×1024），非实际页面；计入会明显超出 ~3MB 预算且无新增规范信息。 |

### 6.7 框架主题映射（`tokens/`，4 未采用）

| 包内文件 | 处理 | 理由 |
|---|---|---|
| `tokens/README.md` | **未采用** | 说明 Tailwind/Ant Design/Element Plus 映射的适用范围；本仓库没有这些框架接入需求。 |
| `tokens/antd-theme.reference.ts` | **未采用** | Ant Design 5 ConfigProvider 主题映射；仓库为 Django 模板 + 原生 CSS/JS/SVG，PH-110 禁止引入组件库/前端构建链。 |
| `tokens/element-plus.reference.scss` | **未采用** | Element Plus 主题映射；同上，且会诱导升级/替换 UI 库。 |
| `tokens/tailwind.config.reference.cjs` | **未采用** | Tailwind 3 映射；本仓库无 Tailwind，禁止为视觉改造引入构建链。 |

### 6.8 工具脚本（`tools/`，3 未采用）

| 包内文件 | 处理 | 理由 |
|---|---|---|
| `tools/preflight_repo.py` | **未采用** | 只读仓库勘查脚本；会扫描真实仓库文件结构，非规范文档所需；仓库已有 `projectctl.py orient` 与事实文档，避免外来源码在仓库内被误运行。 |
| `tools/build_flow_board.py` | **未采用** | 生成流程总览拼图的图片加工脚本，仅服务包内 PNG 制作，无规范价值。 |
| `tools/test_workflow_demo.py` | **未采用** | Playwright/Chromium 烟测脚本，依赖额外运行时与浏览器；不是产品测试，不能替代仓库 CI 与真实浏览器验收。 |

---

## 7. 为什么这样取舍（体积与职责）

- 预算：16 个包内文件合计 **2,379,022 字节 ≈ 2.27 MiB**，加说明文档后仍远低于 ~3MB；保留最关键的规范、令牌、可点击原型与 5 张代表图。
- 去重：截图与 `brand/` 拼图大量重复同一布局；`demo/*.html` 能覆盖 02–09 与 11–15 的页面/流程信息，且可交互，优先保留 demo。
- 去工具化：`tools/` 与三套框架映射只服务于包内演示环境，不属于设计规范；留在仓库会诱导外部 Agent 运行脚本或更换组件库。
- 去指令化：`AGENT_MIGRATION_PROMPT.md` / `MIGRATION_WORK_PACKAGES.md` / `ROUTE_MIGRATION_MATRIX.csv` 是执行指令与派单模板，且基于对未知仓库的假设；仓库流程唯一入口是 `AGENTS.md` 与 `docs/engineering/light-delivery.md`。
- 去品牌化：占位品牌资产无授权、非产品品牌，不进入仓库与产品；总览图只作截图使用。

需要包内其他文件时，可按 §1 的 SHA-256 从源 zip 重新解压获取，**不要手改或从别处补文件**。

---

## 8. 校验记录（命令、退出码、结果）

执行环境：macOS，`/bin/bash`，2026-11-08。工作目录为 ticket staging 目录。`shasum -a 256` 与 Linux `sha256sum -a 256` 等价。

| # | 命令（摘要） | 退出码 | 结果 |
|---|---|---:|---|
| 1 | `shasum -a 256 saas-blue-minimal-kit-v2.zip`（用户本地源文件） | 0 | 输出 `2f1e96c7be7937cb3de065caaf789580f5aa9a4716f5dd02efecb0c0d0825c1d`，与合同哈希一致 |
| 2 | `unzip -l <zip> \| grep -iE "license\|notice\|\.env\|\.pem\|credential\|secret"` | 1（无匹配） | 源包无 LICENSE/NOTICE/凭据文件 |
| 3 | 重新解压源 zip 到临时目录并与检查副本 `diff -rq` | 0 | 重新解压内容与既有检查副本完全一致（55 个文件） |
| 4 | `cp -p` 复制 16 个文件到本目录 | 0 | 复制成功（保留 mtime） |
| 5 | `find . -type f ! -name SHA256SUMS -print0 \| sort -z \| xargs -0 shasum -a 256 > SHA256SUMS` | 0 | 生成 16 行哈希清单 |
| 6 | `python3` 脚本：逐文件 `sha256` 与源包比对、统计字节数 | 0 | **16/16 IDENTICAL**；`TOTAL_BYTES 2379022`；`TOTAL_MiB 2.269`；`FILE_COUNT 16` |
| 7 | `grep -rInE "BEGIN (RSA\|OPENSSH\|EC\|PRIVATE) KEY\|AKIA[0-9A-Z]{16}\|password...\|secret...\|api[_-]?key...\|Authorization: Bearer\|eyJ..."`（仅文本文件，`--exclude=README.md --exclude=adoption-map.md` 避免说明文档自引用） | 1（无匹配） | 未发现凭据/令牌/私钥模式 |
| 8 | `find . \( -name ".env*" -o -name "*.pem" -o -name "*.key" -o -name "id_rsa*" -o -name "*credential*" -o -name "*secret*" \) -print` | 0（无输出） | 无敏感文件 |
| 9 | `grep -rInE "<email 正则>\|<大陆手机号正则>"`（仅文本文件） | 0 | 仅有 `demo/index.html` 中 `zhang@example.com` 等 **RFC 2606 保留域名示例邮箱**（虚构角色），无真实联系人信息 |
| 10 | 检查排除项是否存在于本目录：`tokens/`、`tools/`、`AGENT_MIGRATION_PROMPT.md`、`MIGRATION_WORK_PACKAGES.md`、`ROUTE_MIGRATION_MATRIX.csv` | 0 | 全部 **ABSENT**（10/10 不存在） |
| 11 | `grep -rInE "https?://"`（复制后的文本文件，排除说明文档自身与 `$schema` 元数据 URL） | 1（无匹配） | 复制内容无远程字体/脚本/图片引用；`design-tokens.json` 仅含 `$schema` 元数据地址 |
| 12 | `find . -maxdepth 2 -iname "*license*" -o -iname "*notice*"` | 0（无输出） | 本目录无 LICENSE/NOTICE 文件（与源包一致） |
| 13 | `file screenshots/*.png` / `brand/*.png`（源包） | 0 | 截图尺寸与索引一致；`page-showcase.png` 2700×4169、1,566,484 字节 |
| 14 | 全目录体积复核（含说明文档） | 0 | 见下方「最终体积」 |
| 15 | `shasum -a 256 -c SHA256SUMS` | 0 | 16/16 文件 `OK`，与源包字节级一致 |

**最终体积**：本目录合计 **19 个文件、约 2.43MB（≈2.32 MiB）**（`du -sh .` 约 2.4M，含文件系统块开销）；其中 16 个包内文件精确合计 2,379,022 字节，另含本 README、`adoption-map.md`、`SHA256SUMS` 三个说明/校验文档。

**残留风险（如实记录）**：
- 「无真实客户/工资/凭据数据」的确认基于文件来源声明、包内 `manifest.json`（`includes_real_business_data: false`）与上述模式扫描/人工抽查；无法穷举证明不存在任何现实世界数据。抽查所见姓名、公司、金额均为虚构示例（如「星河科技」「张经理」），生产接入前不得复用。
- 包作者未提供许可文本，授权范围依赖项目所有者的提供行为；若仓库对外公开后涉及截图/资产分发，需按 §3 取得确认或移除大图。
- 包内 demo 的 0.5h 工时、14px 移动边距、11px 辅助字、低对比度状态色等**均不采用**，以 PH-110 与仓库既有约束为准（见 `adoption-map.md`）。

---

## 9. 本地查看方式

```sh
# 在含 demo/ 与 tokens.css 的目录（即本目录）启动本地静态服务
cd docs/engineering/design-reference/saas-blue-minimal-kit-v2   # 落库后路径
python3 -m http.server 8765
# 浏览器打开：
#   http://127.0.0.1:8765/demo/index.html
#   http://127.0.0.1:8765/demo/workflows.html
```

也可直接 `file://` 打开 `demo/index.html`；`demo/workflows.html` 通过相对路径 `../tokens.css` 引用令牌，保持目录结构即可。原型无远程依赖，可离线查看。

---

## 10. 维护规则

1. 本目录中的 16 个包内文件**只读**：与源 zip 保持一致，不得手改；需要变更时从源包重新解压并更新哈希清单。
2. 采用/调整/未采用结论变化时，先改 `adoption-map.md` 与 PH-110 任务记录，再改本清单，避免双份口径。
3. 任何数值进入 `ui-tokens.css` 前必须经过 `adoption-map.md` 的映射与对比度/浏览器检查；不得直接引用包内数值作为生产令牌。
4. 本目录不构成产品验收证据；产品验收以仓库命令、真实浏览器检查与业务回归为准。

---

## 11. 相关文件

- `adoption-map.md`：方向 A 令牌映射、前端规范必须解决的差异、业务不可覆盖规则。
- `SHA256SUMS`：16 个包内文件的 SHA-256。
- 仓库上下文：`docs/engineering/frontend-standards.md`、`docs/engineering/visual-system.md`、`docs/engineering/ui-tokens.css`、`docs/engineering/ui-specimen.html`、`project/specs/PH-110.md`、`AGENTS.md`、`docs/development-contract.md`、`docs/delivery-status.md`。
