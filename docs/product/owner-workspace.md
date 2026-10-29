# 老板决策工作台：信息与交互契约

状态：PH-005 研究交付，静态原型与契约草案，不是产品实现。
研究输入基线：`6940892bfe7dd0c26c85a905ec0b0642817bcc08`；契约摘要：`c02b44cc8c26e605c7bd46fee3388a3137b147e4e665afe4fe68006c93e12fc1`。
本次整合基线：`1e06bc93d7568d086a4a99897d6552c9e5e63fce`；实现状态仍只取可信BASE tasks。
可浏览线框：[owner-workspace-wireframe.html](./owner-workspace-wireframe.html)。角色分工：[role-workspaces.md](./role-workspaces.md)。

## 1. 目的与非目标

老板首页只回答四件事：哪个项目经营变差、钱什么时候会不够、哪些事实缺失、下一步由谁办理。页面先给待拍板事项和异常，再给项目列表，最后给指标到来源的对照；不做装饰性数字墙。

非目标：

- 不在本任务实现页面、API、状态机、权限服务、读模型或数据库迁移。
- 不修改生产模板 CSS、`ui-tokens.css`、`ui-specimen.html`、任务清单、manifest、报告或财务口径。
- 不新增需求、第二数据源、第二套指标公式表或资源排程能力。
- 不把现金、预算余额、合同额、开票额或任务完成率当作利润或履约。
- 不把模型建议渲染成记账、审批或已执行动作。

## 2. 首页信息顺序

1. **待拍板事项**：需要老板选择范围、成本、资金或责任人的事项；每条有原因、来源、唯一办理人和截止时点。
2. **经营异常摘要**：项目预计盈亏、13 周资金占用、未实现收入缺口、数据缺口；每项标明事实/估计/缺项。
3. **项目列表**：按“经营差、钱不够、事实缺”排序，不按装饰性评分排序。
4. **指标—来源—动作对照**：每个指标的唯一 canonical 查询、schema 指针、确认时点、最小权限和下一动作。
5. **缺口清单**：缺什么、影响哪个判断、谁补、何时补；缺数据不是 0。

老板/PM/成员/财务的首页分开。老板看公司和授权项目汇总；PM 只看授权项目；成员只看本人记录；财务按财务权限办理。具体矩阵见 [role-workspaces.md](./role-workspaces.md)。

## 3. 指标到唯一来源的契约

查询请求与结果以 `docs/v1.0-development/contracts/queries.schema.json` 和 `query-results.schema.json` 为准。下表只陈述输入、语义和权限，不复制计算公式；JSON Pointer 中的 `/` 按 JSON Pointer 规则写作 `~1`。

| 业务问题 | 唯一 canonical 查询（真实 ID） | 结果 schema 指针 | 事实/估计/缺项 | 确认时点 | 最小权限 | 下一动作与唯一办理人 | 对应需求 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 这个项目做完预计剩多少？ | `insights.workspace.get/v1` 的 `forecast_result`；成本侧 `planning.eac.get/v1` | `query-results.schema.json#/$defs/insights.workspace.get~1v1`；`#/$defs/planning.eac.get~1v1` | 估计（`basis=forecast`）。输入为生效不含税价款、已确认成本、批准的剩余成本估计；缺 ETC 时是缺项，不是 0。 | ETC 批准快照日 + `as_of_date` + `watermark` | 老板/财务；PM 仅项目汇总，不见个人费率 | 若预计完工总成本超过生效价款：老板拍板范围/成本；否则 PM 更新 ETC。`#owner-decisions` / `#pm-todo` | `V1-PLN-06`、`V1-INS-01` |
| 未来 13 周项目资金占用如何？ | `cash.forecast.13week/v1` | `query-results.schema.json#/$defs/cash.forecast.13week~1v1` | 估计（`forecast`）。13 个 `weeks` 的流入、流出、累计净流；`overdue_source_refs` 与 `undated_source_refs` 单列，`coverage_complete` 标明范围。 | 每周截止日 + `as_of_date`；预测水位 | 老板/财务；PM 不见公司现金预测 | 逾期：财务跟进；日期未定：PM 补日期。`#finance-todo` / `#pm-todo` | `V1-CSH-10` |
| 银行里到底有多少可用钱？ | `cash.account_coverage.get/v1` | `query-results.schema.json#/$defs/cash.account_coverage.get~1v1` | 事实（`actual`）仅在覆盖完整时；否则只显示“已知范围”，`missing_coverage` 是缺项。 | 银行对账日/覆盖范围 + `as_of_date` | 老板/财务 | 财务补期初、非项目、未匹配交易；覆盖不全不得称公司银行余额。`#finance-todo` | `V1-CSH-11` |
| 交付影响链断在哪？ | `delivery.performance.available/v1` + `core.source.trace/v1` + `contracts.effective_line.get/v1` | `#/$defs/delivery.performance.available~1v1`；`#/$defs/core.source.trace~1v1`；`#/$defs/contracts.effective_line.get~1v1` | 事实（`actual`）以履约/验收确认为准；任务数量完成率不是履约。 | 验收/客户确认日期；来源版本 | 老板/PM（授权项目）；成员仅本人证据 | PM 补验收或证据；财务不得用开票/到账补收入。`#pm-todo` | `V1-PRO-04`、`V1-REV-05` |
| 未实现收入缺口多少？ | `management_revenue.contract_line.get/v1` | `#/$defs/management_revenue.contract_line.get~1v1` | 已确认部分是事实；未履约范围是缺口语义。当前读模型未提供逐明细缺口，显示“缺项/不可计算”。 | 履约或服务期间确认日 + `watermark` | 老板/财务；PM 项目汇总 | PM 补履约证据；财务只核对来源，不直接改收入。`#pm-todo` | `V1-REV-04`、`V1-CON-08` |
| 哪些事要老板拍板？ | `insights.workspace.get/v1` 的 `pending_refs` + `insights.data_gaps.get/v1` | `#/$defs/insights.workspace.get~1v1`；`#/$defs/insights.data_gaps.get~1v1` | 派生清单，不是存储事实；每条必须有原因、来源、责任人和下一步。 | 决策记录时间（待实现） | 老板；PM 只能提交请求 | 老板拍板；模型建议仅候选。`#owner-decisions` | `V1-INS-01` |
| 哪些事实缺失或滞后？ | `insights.data_gaps.get/v1`；实体 `DataGap` | `#/$defs/insights.data_gaps.get~1v1`；`entities.schema.json#/$defs/DataGap` | 缺项（`gap_type`：缺费率、缺证据、未匹配现金、未知税额、未确认政策、未知日期）；不是 0。 | `detected_on` + 处理状态 | 老板看全部；PM 看本项目；成员看本人；财务看财务类 | 唯一 owner 补事实；不新建第二数据源。`#owner-gaps` | `V1-INS-07` |
| 资源冲突在哪里？ | 无 canonical 查询 | 无 | 不可用：旧 scope 未实现，不新增资源排程。 | 不适用 | 不适用 | 只显示“尚无来源”；若业务需要，先做规划 PR，不在本任务实现。 | 不虚构需求 |
| 任务完成率能确认收入吗？ | 任务/工时列表仅作进度参考，不是收入或履约事实 | 不适用 | 不能；收入只认履约/验收来源。 | 不适用 | PM/成员 | 页面明确标注“进度参考，不是履约”；不改用完成率。 | `V1-REV-05` |

补充：`contracts.project_summary.get/v1` 提供 `effective_net_cents` 与 `pending_change_net_cents`，是合同价款侧的输入，不是利润；`settlement.aging.get/v1` 提供未清项和未分配收款；`time.approved.list/v1` 与 `costing.source.available/v1` 提供投入与成本来源。它们只在对应指标下钻时读取，不另建汇总口径。

## 4. 事实、估计、缺项与确认时点

- **核对事实（actual）**：没有已发生暂估成分的已确认成本、管理收入、现金与履约，均有来源、版本及确认日期。金额已入账不等于金额一定是actual。
- **已发生暂估（provisional）**：经济事项已经发生，但金额尚为暂估。聚合金额只要含暂估成分，就标provisional并保留差额/依据，不能一律改成actual。
- **未来预测（forecast）**：ETC、13周未来收付及预计结余，显示批准/生成时点。未来计划只进入forecast，不能计入已发生暂估或actual。
- **缺项**：没有来源、没有日期、没有覆盖或没有确认。页面显示“缺项/不可计算”，不显示 `0.00`；缺项进入缺口清单并指定唯一办理人。
- **确认时点**：每个指标同时显示 `as_of_date`、`watermark` 和业务确认日（ETC 批准日、验收日、银行对账日、缺口发现日）。没有确认日期的，标“待补”，不推断。

## 5. 三条不能混的口径

- **现金不是利润**：银行流水、净现金流、13 周预测回答“钱什么时候进出”；利润/预计结余回答“生效价款与预计完工总成本的关系”。两者不能互相替代。
- **13 周预测不是银行余额**：`cash.forecast.13week` 是未来流入流出；`cash.account_coverage` 才是账户覆盖范围。覆盖不完整时只能称“已知范围可用额”，不能称公司银行余额。
- **任务完成率不是履约**：任务数量、工时进度、日期到期都不能自动确认收入；只有三模板对应的批准履约/验收、客户确认计费工时或已确认服务期间才能进入管理收入。

本页不维护第二套公式；口径以 [policy-decisions.md](../v1.0-development/policy-decisions.md)、[contracts/README.md](../v1.0-development/contracts/README.md) 和查询结果 schema 为准。

## 6. 当前能力与目标能力（缺口单列）

| 能力 | 冻结契约/黄金夹具可演示来源 | 产品当前真实缺口 | 目标实施任务 |
| --- | --- | --- | --- |
| 项目预计盈亏 | 目标合同价款、成本及批准ETC结构；本例缺EAC不计算 | 现有产品只有工程验证版成本/预算等能力；正式合同及新ETC/EAC与目标经营读模型未实现，不可查询目标结果 | PH-177/178、PH-182/184 |
| 13周项目资金占用 | 目标逐周结构、逾期和未定日期字段；夹具无完整逐周序列 | 现有现金计划/收付不等于目标13周预测；不得画假曲线 | PH-179 |
| 未实现收入缺口 | 目标收入、结算及履约来源的黄金夹具 | 当前正式合同、管理收入、结算/应收及逐明细缺口服务未实现；仅可静态演练 | PH-166/167/168、PH-182 |
| 资源冲突 | 无可用canonical来源 | 未实现；不新增资源排程或假定已有数据 | 单独规划，不在1.0本片扩张 |
| 待拍板事项 | 原型清单与责任人结构 | 目标跨域决策读模型和通知未实现，当前已有审批不等于目标工作台 | PH-183/184 |
| 银行可用余额 | 目标账户覆盖查询结构 | 当前现金发生账不提供完整银行余额；新账户、期初与匹配未接入 | PH-169/179 |

本线框全部金额是目标能力的合成演练，不能视作当前产品结果。同一BASE-UAT-03的S03快照：管理收入50000分、未清应收53000分、银行现金变动及未抵扣预收各31800分；两互斥预测来源53000+53000分是剩余幅度，不是银行余额或利润，不混入S04抵扣后的快照。产品当前已实现能力以delivery-status为准。

## 7. 状态契约

线框至少覆盖五种状态，均可用键盘切换：

- **正常**：有已确认来源，指标显示事实与估计，缺口单列；不补零。
- **缺数据**：关键来源为空或覆盖不完整；所有数值改为“缺项/不可计算”，先给补事实动作，不给拍板结论。
- **逾期**：`overdue_source_refs` 非空；逾期单列、不自动顺延；到期日缺失时显示“缺项”，不编造日期。
- **无权限**：当前账号无项目财务权限；不显示金额和来源，说明联系路径；无权限与无数据文案不得混用。
- **手机**：390px 单列、触控目标至少 44px；320px 无 `body` 横向溢出；DOM 顺序与桌面一致。

静态线框的按钮、角色切换和状态切换只演示信息结构，不调用服务、不声称服务端权限。真实功能路径尚未定义，见 [role-workspaces.md](./role-workspaces.md) 的锚点表。

## 8. 交互与可访问性

- 角色/状态使用 `tablist` 语义；左右方向键、Home/End 切换，焦点进入新视图标题。
- 每页一个主操作；危险动作使用明确动词；链接用于导航，按钮用于操作。
- 正文至少 14px，辅助文字至少 12px；金额等宽数字并右对齐；状态同时有文字和符号，不只靠颜色。
- 所有交互控件有可见 `:focus-visible` 焦点环；跳过导航链接；表格有 `caption`。
- 窄屏表格转为带字段名的行卡片；仅矩阵允许表格容器局部横滚，页面不得横滚。
- 不使用远程字体、远程脚本、前端构建链或网络请求；线框自包含。

## 9. 需要业务判断的具体问题

1. 哪个项目预计结余为负或无法计算？谁在何时补 ETC，补完后是否改变拍板结论？
2. 未来 13 周哪一周累计净流出最低？银行覆盖是否完整到可以称“可用余额”？
3. 哪些已确认收入尚未结算、哪些已结算未履约？缺口由谁补证据，截止何时？
4. 哪个项目的验收→结算→收入链条断了？断点影响本月确认还是仅影响预测？
5. 哪些数据缺口会改变老板的范围/成本/资金决策？谁负责、何时补、补不上时如何决策？
6. 哪个项目需要老板在范围、成本、资金或责任人之间取舍？选项和后果各是什么？
7. 哪些逾期预测来源需要财务跟进？哪些日期未定需要 PM 补，不能自动顺延？
8. 资源冲突目前没有来源；是否需要单独立项补数据？若需要，先走规划 PR，不在本任务扩写。

## 10. 已决定，不再交回用户的技术选择

系统中文字体、正文 14px/辅助 12px、触控 44px、语义令牌、原生 HTML/CSS/JS、无远程依赖、五状态与键盘切换，均为本契约和前端规范的工程约束，不是业务选项。第9节用于封闭试点检验理解与办理效果，不形成额外开工审批；当前权限与业务口径继续按已冻结契约和企业开账配置执行，变更范围另走规划。

## 11. 验收与未验证

本任务只交付信息契约与静态线框。离线浏览器与键盘检查见下方实际记录；以下后台/客户事项均**未验证**：权限服务、查询读模型、13 周预测、EAC、未实现收入缺口、资源冲突来源、真实功能路径、客户访谈与付费意愿（不在本片范围）。不得把静态线框通过当作产品页面、业务或生产验证通过。

已实际阅读的依据：`project/specs/PH-005.md`、`docs/v1.0-delivery-plan.md`、`docs/v1.0-development/policy-decisions.md`、`docs/v1.0-development/requirement-trace.json`、`docs/v1.0-development/contracts/queries.schema.json`、`query-results.schema.json`、`entities.schema.json`、`docs/v1.0-development/fixtures/fixture.json`、`docs/engineering/frontend-standards.md`、`docs/engineering/visual-system.md`、`docs/engineering/ui-tokens.css`。

示例数值仅来自 `BASE-UAT-03` / `UAT-03-S01` 至 `S03` 的 `fixture.json` 字段与 `forecast_components`；其他数值一律缺项，不补零。

## 实际原型检查（2026-10-29）

主控在Chromium151实际打开离线线框，1440px及390px均完成四角色/五状态、Tab/左右/Home/End、跨角色动作焦点、无权限全部工作区隐藏；手机原生对话框Tab/Escape/关闭后焦点恢复。320/360/390/480/640/768/1024/1440八宽无整页横溢，无页面错误及外部网络请求，手机44px触控目标通过。视觉初检发现窄屏表caption竖排并修复，重新截图/检查通过。来源编号折叠，默认不展示无来源的资源排程条目。

[浏览器证据](owner-workspace-assets/browser-evidence.json)、[桌面截图](owner-workspace-assets/browser-1440.png)、[手机截图](owner-workspace-assets/browser-390.png)仅证明静态交互线框；后台查询/服务端权限/记账/真实客户没有因此验收。原生OpenCode超时，回收文档后由整合补齐HTML/键盘/令牌单源并审查，不将超时算作成功交付。
