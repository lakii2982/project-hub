# ADR 0012：导入/期初域归属——新建 pmo_plugins.onboarding 插件（推荐方案 A）

状态：PH-267 决策产出（推荐方案 A）；导入预览/期初实现由后续规划槽按本 ADR 冻结，本文不授权开工、不创建代码。日期：2026-10-31。

## 背景与已冻结契约（按实代码/契约核对）

- canonical 实体已冻结且 owner 明确：`ImportPreview`、`ImportConflict`、`OpeningBatch`、`OpeningCoverage`、`OpeningEvent` 的 `x-owner=onboarding`（ImportPreview/ImportConflict 为 ephemeral_preview；OpeningBatch/Coverage/Event 为 owned_fact，status draft/active/posted/reversed/superseded）。
- canonical 命令：`onboarding.opening_batch.draft.save/v1`、`onboarding.opening.commit/v1`；事件/桥按 contracts 目录（本文不改 schema，不新增 topic）。**没有** `import.*` 命令：导入不得新增命令通道，提交只能逐域调用既有 owner 命令。
- 当前没有 onboarding/importing 插件 App；`pmo_plugins` 现有 contracts/costing/delivery/management_revenue/settlement/cash 等 app。迁移当前叶：core 0010、contracts 0003、cash 0006、settlement 0005（新 app 迁移不得抢号）。
- 插件运行时（`core/plugin_runtime.py`/`docs/plugin-architecture.md`）：manifest 恰好 8 字段、插件固定列在 `settings.PMO_PLUGIN_MODULES`、无 URL/页面注册点（页面由 `pmo_plugins/delivery/urls.py` 绑定）、无插件独立迁移链；PH-001/PH-002/PH-140 的 SDK 注册/入口门控已交付。
- 相关 owner：`Organization.opening_cash_cents`（既有期初现金字段）、PH-230 posted 现金事实、PH-239 现金分类/账本桥接、PH-224 结算未清、PH-189/190/191 legacy 期初覆盖/提交/核对、WP-IMPORT 的导入预览/原子提交边界。

## 选项

- **方案 A（推荐）：新建 `pmo_plugins.onboarding` 插件 app**，作为导入预览/冲突清单与期初批次/覆盖/事件事实的写 owner（与 canonical x-owner=onboarding 一致）。
- **方案 B：复用 `delivery` 插件**承载导入/期初。
- **方案 C：放进 core**（kernel 内新增 onboarding 业务表/命令）。
- **方案 D：复用 `contracts`/其它业务 owner**。
- **方案 E：暂缓导入/期初**。

## 判据与逐项比较

| 判据 | A 新建 onboarding | B delivery | C core | D contracts 等 | E 暂缓 |
| --- | --- | --- | --- | --- | --- |
| canonical owner 一致性 | 与 x-owner=onboarding 一致 | owner 语义不符（delivery 是履约/项目交付） | core 是 kernel，非业务 owner | contracts 是合同/收费，非导入期初 | — |
| 迁移 isolation/抢号 | onboarding 自有 0001，不抢 core/contracts/cash/settlement | 与 delivery 迁移耦合 | 改 core 链，风险高 | 与 contracts 0003 抢号 | — |
| manifest/requires | 8 字段：requires={contracts,settlement,cash}（编排各域提交）；capabilities=[] | 可表达但语义错位 | 不适用 | 不适用 | — |
| 入口门控/路由 | delivery/urls root 单写 + domain_metadata access；`onboarding.*` 具名服务/浏览器 | 同模式但入口归属错位 | kernel 入口膨胀 | 同左 | — |
| 导入/期初边界 | 预览零账本/零来源消费；提交逐域调既有命令、整批回滚 | 同左 | 同左 | 同左 | 阻塞 WP-IMPORT/OPENING |
| 与现金/未清/账本关系 | 只编排既有 owner 命令，不另造余额/收入 | 同左 | 同左 | 同左 | — |
| 测试/部署/回滚 | 新 app 一次 bootstrap；停用保留历史 | 与交付插件发布耦合 | kernel 风险 | 与合同发布耦合 | — |

## 决定

采用**方案 A：新建 `pmo_plugins.onboarding` 插件 app**，作为导入/期初域写 owner；首片范围冻结为「导入预览与冲突清单」+「期初批次/覆盖/事件编排」，不改 contract schema、不实现业务代码。

实现约束（由后续规划槽冻结的实现片落地，本 ADR 不建 scaffold）：

1. **app 形态**：`pmo_plugins/onboarding/{__init__.py,apps.py,plugin.py,domain_metadata.py,models.py,preview_services.py,services.py,views.py,forms.py,templates/onboarding/*,migrations/0001_initial.py}`；`plugin.py` manifest 恰好 8 字段：`id='onboarding'`、`name='导入与期初'`、`version='0.1.0'`、`kernel_api='>=1.0,<2'`、`requires={'contracts': '>=0.1.0', 'settlement': '>=0.1.0', 'cash': '>=0.1.0'}`、`default_enabled=True`、`capabilities=[]`、`description` 简述导入预览/期初编排。
2. **注册**：`pmo/settings.py`（INSTALLED_APPS + PMO_PLUGIN_MODULES）+ `pmo_plugins/delivery/urls.py` root 单写；domain_metadata 声明 `plugin_id`/access 元数据（owner/finance 写、auditor 读、pm/member 拒绝；停用写拒绝）。
3. **迁移 owner**：onboarding 自有迁移链首号 `0001_initial`；**不改** core 0010/contracts 0003/cash 0006/settlement 0005 等已应用迁移；不抢号；停用保留历史。
4. **入口/命令暴露**：`onboarding.opening_batch.draft.save/v1`、`onboarding.opening.commit/v1` 首片仅具名服务/浏览器工作流；**不新增 `import.*` 命令**、不接 PAT/MCP/个人 AI 审批/支付/锁账；导入预览为 ephemeral_preview，`preview_only=True`。
5. **导入预览边界（PH-256）**：只做模板识别/字段与主数据映射/逐行冲突清单；`normalized_commands` 是“待执行命令清单”，预览阶段**零业务事实、零账本、零来源消费、零规范事件**（仅 generic audit）；冲突以 ImportConflict 落 ephemeral_preview，可重放预览但不产生提交。
6. **期初边界（legacy PH-187–191）**：OpeningBatch/OpeningCoverage/OpeningEvent 为 append-only owned_fact，记录覆盖类别/期间与每域来源金额；正式 `onboarding.opening.commit` 只能**逐域调用既有 owner 命令**（现金走 PH-230 posted 事实路径，未清走 PH-224/PH-232，成本/收入走各自 owner），单事务执行、任一行失败整批回滚、重试幂等；已过账走更正，不直接改账；金额只记一次，不另建可编辑余额、不造收入/成本。
7. **与既有事实关系**：`Organization.opening_cash_cents`、PH-230 posted 现金、PH-239 分类桥接、PH-224 未清之间不新增余额层；期初事件只记录来源与金额供各 owner 消费，现金/未清的正式事实仍由对应 owner 命令写入；本期初不重算历史费率/不生成收入。
8. **insights 归属（PH-257）**：期间预检/核对清单**不并入本插件**；它是只读读模型域，建议单独决策片（下一规划槽按 PH-257 spec 与 ADR 0012 登记独立归属决策），本 ADR 不把 insights 塞进 onboarding。
9. **依赖顺序**：PH-256 实现须待 PH-181/PH-185 done 且本 ADR 合入；PH-257 须待 PH-180/PH-181 done 且其独立归属决策完成；两者保持 proposed。

## 影响面

- PH-256：decision point 改为引用 ADR 0012（新建 pmo_plugins.onboarding，非 pmo_plugins.importing）；精确白名单与 tasks allowed_paths 同步为 onboarding bootstrap/文件（含 __init__.py/apps.py/plugin.py 与 pmo/settings.py、pmo/urls.py），保持精确逐文件；仍 proposed（缺 PH-181/PH-185）。
- PH-257：decision point/blockers 注明 insights 归属单列、不并入 onboarding；仍 proposed（缺 PH-180/PH-181）。
- 现有 owner：contracts/settlement/cash/core 契约与代码不变；只读接口由实现片按白名单新增/消费。
- 风险：新 app 一次 bootstrap 注册成本；若未来把导入/期初并入其它 app 需另立规划，不在实现 PR 自我授权。

## 放弃的备选与理由

- **B delivery**：delivery 是项目履约/交付 owner，导入/期初是跨域数据承接，混入会让交付插件扩张为数据导入 owner，边界失真。
- **C core**：kernel 已承担项目/工时/单据/账本基础，继续塞入导入/期初业务会扩大 kernel 且迫使改 core 迁移链（与 core 0010 抢号）。
- **D contracts 等**：合同/结算/现金 owner 各自已有明确职责，导入/期初是跨域编排，不是任一 owner 的子域；塞入会造成双 owner 与抢号。
- **E 暂缓**：阻塞 WP-IMPORT/WP-OPENING 与 UAT-12 全链（期初、收款、后续只核销），且 PH-002/PH-024 已 done，不存在需要等待的新前置。

## 后续

- 下一规划槽按本 ADR 冻结 PH-256 实现片（导入预览）与 legacy PH-187–191 的期初实现片（或合并为薄片，按依赖事实），并复核 PH-181/PH-185 是否 done；PH-257 另行登记独立归属决策片（insights）。
- 实现片开工前复核 contracts 的 ImportPreview/Opening* 字段与 `onboarding.*` 命令仍为当前契约；契约漂移走独立规划 PR。
