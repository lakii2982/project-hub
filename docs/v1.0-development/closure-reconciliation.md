# PH-396 · v1.0 遗留任务对账与关闭（docs-only）

- 里程碑：v1.0.0；风险：S；状态：in_review；BASE：7748ea1；对账范围：BASE 中 v1.0.0 未完成（proposed/ready/in_review/blocked）任务 113 项 + 非 v1.0 遗留 17 项（附录）。
- 规则：每项关闭必须有已合并实现/契约/验收证据并逐项列出；不得批量无据关闭；执行人不得自批，独立 S 复核见 §6；旧任务与新片不重复，历史保留。

## 1. 结论汇总

| 结论 | 数量 | 动作 |
| --- | --- | --- |
| 已覆盖→关闭 | 6 | 置 cancelled（保留历史），逐项覆盖证据见 §2.1 |
| 未开工/部分覆盖→保留 | 106（含本片 PH-396） | 保留原状态，写精确 blocker/gap；不虚假晋级 |
| 验收中→保留 | 1（PH-241） | 保持 in_review，待合并/验收证据 |
| 合计 | 113 | 任务板状态与证据见 §2 |

- 关键事实：PH-371/372/387/388/391/392 已 done；PH-241 报告/复核 pass 但尚未合入；PH-388 ready；PH-389/390/393/394/395 仍 blocked；v1.0 发布资格 **release_ready=false**。
- 关闭原则：legacy PH-141–207 以**实际已合并交付**（PH-210–370 链）为准，不因编号旧自动关闭；仅在有实现/契约/验收证据时关闭；保留项逐项写缺口。

## 2. 逐项对账表（v1.0.0）

| 任务 | 标题 | BASE 状态 | 结论 | 证据 / 剩余 gap |
| --- | --- | --- | --- | --- |
| PH-020 | 1.0上线就绪评审与发布候选 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-141 | 来源、期间与过账统一基础（2片） | proposed | 部分覆盖→保留/转新片 | 依赖 PH-140/PH-210 已 done，但 DoR 未闭合：缺受信来源 resolver/批准与权限/book/scope/稳定消费映射，旧 locked_through 与 posting_period/开放期调整权威适配，新 AccountingEvent/Posting/SourceConsumption/幂等持久约束与唯一 audit/Outbox owner； |
| PH-142 | 来源、期间与过账统一基础（2片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-143 | 账号生命周期、授权与敏感字段（2片） | proposed | 未开工→保留 | 成员生命周期子集已登记 PH-264（ready）；剩余账号邀请/敏感字段细分待后续规划槽复核；依赖 PH-142 仍 proposed。 |
| PH-144 | 账号生命周期、授权与敏感字段（2片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-145 | 员工、账号绑定与共同字典（1片） | proposed | 未开工→保留 | 员工档案/账号绑定已由 PH-024 交付；共同字典/交接规则待后续槽复核；依赖 PH-144 仍 proposed。 |
| PH-146 | 统一往来方、联系人和去重（1片） | proposed | 未开工→保留 | 客户/供应商与联系人主数据已由 PH-212 交付；去重/合并策略待后续槽复核；依赖 PH-143 仍 proposed。 |
| PH-147 | 项目主档、成员与基础状态（1片） | proposed | 未开工→保留 | 项目主档/成员基础已由既有 core 交付（PH-021/PH-024/PH-025）；成员项目范围变更由 PH-264（ready）覆盖；依赖 PH-145 仍 proposed。 |
| PH-148 | 交付节点、履约证据与风险（2片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-149 | 交付节点、履约证据与风险（2片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-150 | 合同、收费明细与生效方法（1片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-151 | 合同变化、终止和关联视图（1片） | proposed | 未开工→保留 | 依赖 PH-150/PH-149 仍 proposed、PH-110 ready 未 done；PH-265 合同变更首片已另立，PH-151 关联视图需等 PH-265 实现事实后重定，保持 proposed。 |
| PH-152 | 项目/非项目投入与周填报（1片） | proposed | 未开工→保留 | 工时填报/审批能力已由 PH-025/PH-217 在 core 交付；本片仍缺依赖 PH-145/PH-147/PH-149/PH-110 done（均 proposed）。可计费确认归属见 PH-266 ADR 0011；本片不改变状态。 |
| PH-153 | 投入审批与缺项处理（1片） | proposed | 未开工→保留 | 工时独立审批能力已由 PH-025/PH-217 在 core 交付；本片仍缺 PH-152（proposed）与 PH-110 done；可计费确认归属见 PH-266 ADR 0011，本片不改变状态。 |
| PH-154 | 客户可计费投入确认（1片） | proposed | 未开工→保留 | 剩余客户可计费确认依赖 PH-266 ADR 0011（time 插件归属，已决策：新建 pmo_plugins.time）；仍缺 PH-153/PH-150 done；实现片候选 DoR 见 project/specs/PH-266.md，待下一规划槽冻结为 PH-269+ 再晋级 ready。本片保持 proposed。 |
| PH-155 | 客户结算、未清、账龄与质保（2片） | proposed | 未开工→保留 | 结算/未清/账龄首片已由 PH-224 交付且 PH-232 扩展核销；质保与后续片待后续槽复核。 |
| PH-156 | 客户结算、未清、账龄与质保（2片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-157 | 供应商及员工费用义务接口（1片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-158 | 折让、减免、坏账与信用更正（1片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-159 | 工资池、有效产能与费率版本（1片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-160 | 人工分配、差额和开放期调整（2片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-161 | 人工分配、差额和开放期调整（2片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-162 | 报销费用、发生确认与待定税额（1片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-163 | 外包采购承诺与验收成本（1片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-164 | 暂估转正式、退货折让和支出更正（1片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-165 | 共享费用分配与重分配（1片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-166 | 固定价履约收入和累计调整（1片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-167 | 工时及固定服务期收入（1片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-168 | 收入和结算配对及结果查询（1片） | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-169 | 唯一主交付：账户、银行事实、归属与待匹配 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-170 | 唯一主交付：核销、预收预付抵扣与撤核 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-171 | 唯一主交付：退款、银行退回与恢复 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-172 | 唯一主交付：工资社保税费现金 | proposed | 未开工→保留 | PH-169 仍 proposed（银行事实/归属未关闭）；工资社保税费现金命令层未实现，保留 proposed。 |
| PH-173 | 唯一主交付：手续费与内部互转 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-174 | 唯一主交付：员工借支与报销抵扣 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-175 | 唯一主交付：跨期预付服务耗用 | proposed | 未开工→保留 | PH-170 仍 proposed（核销/预收预付抵扣未关闭）；跨期预付服务耗用未实现，保留 proposed。 |
| PH-176 | 唯一主交付：发票、分配与红冲 | proposed | 已覆盖→关闭（cancelled） | 覆盖：PH-252/PH-320/PH-325（发票登记/分配、签发/红冲、税额资格与只读对账，merged） 覆盖；证据见 §2.1 |
| PH-177 | 唯一主交付：预算版本与批准 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-178 | 唯一主交付：剩余预计与实际释放 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-179 | 唯一主交付：来源升级与13周预测 | proposed | 未开工→保留 | 依赖 PH-170/PH-171/PH-172/PH-173 仍 proposed、PH-110 ready 未 done；13 周预测能力未交付，保持 proposed。 |
| PH-180 | 唯一主交付：期间预检、只前移锁定与调整 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-181 | 唯一主交付：四类核对与差额追踪 | proposed | 未开工→保留 | 依赖 PH-180/PH-179/PH-174/PH-175 仍 proposed、PH-110 ready 未 done；四类核对/差额追踪未交付，保持 proposed。 |
| PH-182 | 唯一主交付：经营指标、缺口与复盘草稿 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-183 | 唯一主交付：到期扫描、幂等待办与停用保留 | proposed | 未开工→保留 | PH-364 仅契约冻结；到期扫描/待办/通知实现未开工（PH-380 链），保留 proposed。 |
| PH-184 | 唯一主交付：四职责工作台 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-185 | 唯一主交付：项目聚合、结项与归档后迟到款 | proposed | 未开工→保留 | 依赖 PH-184/PH-180/PH-170/PH-178 仍 proposed、PH-110 ready 未 done；项目聚合/结项/归档后迟到款未交付，保持 proposed。 |
| PH-186 | 唯一主交付：授权导出与核对底稿 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-187 | CSV 上传 → 模板识别 → 字段/单位/主数据映射 → 逐行冲突预览；预览零账本、零来源消费  ／  只读预览；`ImportPreview.preview | proposed | 已覆盖→关闭（cancelled） | 覆盖：PH-274（CSV 模板识别/映射/逐行冲突只读预览，merged） 覆盖；证据见 §2.1 |
| PH-188 | 批准预览 → 单事务按域调用既有命令 → 任一行失败整批回滚；重排/重试幂等；已过账走更正  ／  不新增 `import.*` 命令；提交只编排既有具名命令 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-189 | 选择从零/存量与截止日 → 类别/项目/期间覆盖清单 → 明细重建或余额承接互相排斥的草稿与试算  ／  只草稿/试算；零账本/现金/收入/义务副作用 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-190 | 正式提交 → 按固定域顺序写各域期初事件与桥 → 覆盖冲突拒绝、金额只记一次  ／  不另建核算规则；期初未清不再造收入/成本 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-191 | 开账核对报告与责任人确认；UAT-12 全链：期初、收款、后续只核销  ／  报告只呈现服务结果；缺项不得显示完整经营结果 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-192 | 首公司/起账日/币种/时区/核对负责人与角色 → 缺项清单 → 会话可续接；正式启用后不可重复接管  ／  企业唯一租户；正式账后不直接改主体/本位币 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-193 | 首项目/首合同/收费模板（三模板分别配置）→ 一合同一项目；缺价格/分配/确认依据只草稿  ／  不在向导内自建收入/成本规则 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-194 | 自助说明、样例与数据导出入口；角色说明与续接说明可读  ／  示例/演练数据不得进入正式账 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-195 | 健康、worker、备份状态与失败可见；重启后持久化；部署证据写入 manifest  ／  健康页只读脱敏；配置存在≠运行通过 | proposed | 未开工→保留 | 真实健康/worker/备份页与运行证据未执行；PH-347/350 仅本地 drill，保留 proposed。 |
| PH-196 | 真实域名 HTTPS 登录/附件；HTTP→HTTPS；Secure/HttpOnly/SameSite；代理头不可伪造；证书续期；错误配置拒绝并留证据  ／  | proposed | 未开工→保留 | 真实域名/TLS/证书与 L3/L4 证据未执行；需 PH-347 execute 与浏览器走查，保留 proposed，不得以 dry_run 冒充。 |
| PH-197 | 冻结基线 `119a049…` 合成旧库升级到候选；旧 58 分/PK/审计/来源不变；重复升级与中断重试安全；仅结构兼容才回退  ／  迁移只追加；不伪造旧  | proposed | 未开工→保留 | PH-347 upgrade execute not_run（无旧库 58 分/PK/审计升级证据）；保留 proposed。 |
| PH-198 | PG + 非空附件备份与隔离恢复；hash/计数/权限/报表核对；实测 RPO/RTO  ／  不以 SQLite 文件拷贝或 `check --deploy` | proposed | 未开工→保留 | PH-348 PG execute 为 not_run/exit 2（无 pg_restore/隔离恢复/RPO-RTO）；真实 PG 备份恢复未执行，保留 proposed。 |
| PH-199 | UAT-14 两连接：核销/退款/锁期竞争；确定性屏障；只一笔有效消费；证据 `backend=postgresql`、`all_skipped=false`  | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-200 | 审批、分摊、期初竞争与“事实后、Outbox 前”故障注入回滚；跨域原子性  ／  测试不改产品规则；不得绕过权限/锁期 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-201 | 在具名起测环境按夹具基准测列表/详情/核心写 P95 与 10 并发；记录环境、数据规模、错误率、连接数  ／  基准是待测起点，不是产品上限；超基准不拒绝业务 | proposed | 未开工→保留 | 真实 P95/10 并发与具名环境未执行；PH-350 仅本地基线，保留 proposed。 |
| PH-202 | 首用路径（登录→向导→首项目→首合同→首条投入）与长任务进度/取消/失败可见；1440/390、键盘/焦点  ／  不改业务规则；不隐藏慢/失败 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-203 | 客户授权人申请导出 → manifest/附件清单/hash 可核对；临时/个人令牌撤销立即生效；数据交还引用批准记录  ／  只列授权范围；不显示个人费率/工 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-204 | 支持期/响应/备份责任/临时访问期限/退出 runbook 文档化并演练  ／  **阻塞**：`docs/operations/support-*.md` 不 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-205 | 候选源码/镜像/配置/许可证/摘要打包；敏感内容排除；同提交 CI；9 项旧门只读核对；`ReleaseManifest` 记录  ／  只产出候选资产/清单； | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-206 | 三家客户分批按 owner/finance/PM/member 真实办理约定周期；三模板分别验收（固定价验收/确认工时/确认期间）；完整月度核对、决策留痕、客户 | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-207 | 只读汇总 137 Must、18UAT、9 门与 `ReleaseManifest`；逐项引用唯一证据；生成收口报告  ／  只核对，不改门、不打标签、不发布； | proposed | 未开工→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-241 | 预收/预付抵扣 Offset 首片：客户预收对未清项的部分抵扣 | in_review | 验收中→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-251 | 手续费净额结清、既有成本复用与承担方后续片 | proposed | 未开工→保留 | PH-250/280 仅为批准与只读配对，未实现 ReceiptFeeSettlement/existing_cost_ref/承担方；保留 proposed。 |
| PH-255 | 共享费用分摊首片：单笔已确认共享费用按显式份额跨项目分摊（无重分摊/无驱动因子） | proposed | 未开工→保留 | 缺 PH-162（报销费用/发生确认）done；PH-153 的工时审批能力已由 PH-025/PH-217 交付但 PH-153 任务仍 proposed。依赖完成后由后续规划槽复核 DoR 再晋级。 |
| PH-256 | 导入预览与冲突清单首片：CSV 模板识别、字段/主数据映射与逐行冲突（预览零账本、无提交） | proposed | 已覆盖→关闭（cancelled） | 覆盖：PH-274（导入预览/冲突清单只读首片，merged） 覆盖；证据见 §2.1 |
| PH-257 | 期间预检与对账差异清单只读首片：ReconciliationCase 生成、责任人分派与差额追踪 | proposed | 未开工→保留 | PH-277/363 仅契约；ReconciliationCase/期间预检/差异单读模型未实现，保留 proposed。 |
| PH-260 | 管理收入确认与结算配对首片：单一收入方法确认 + 与结算/成本的只读配对结果 | proposed | 未开工→保留 | 缺 PH-162（报销费用/发生确认）与 PH-161（人工分配/差额）done；PH-167/PH-156 legacy 任务仍 proposed，但固定价/服务期管理收入与客户结算能力已由 PH-219/PH-222/PH-224 交付。依赖完成后由后续规划槽复核 DoR 再晋级。 |
| PH-261 | 预算版本与 ETC 首片：预算版本/行草稿与批准、剩余预计（ETC）发布（不做预测覆盖） | proposed | 已覆盖→关闭（cancelled） | 覆盖：PH-306/PH-330（预算版本/ETC 契约冻结与实现首片，merged） 覆盖；证据见 §2.1 |
| PH-262 | 经营指标与项目工作台读模型首片：只读指标快照 + 项目汇总视图（无写入口） | proposed | 未开工→保留 | PH-291/PH-309/PH-316 仅为只读总览/工作台聚合/导出联动；仓库无 insights 插件、无 MetricSnapshot 模型与 project+period 指标刷新（active/superseded、request_token、workspace 视图）实现，原 DoR 未覆盖；保留 proposed，需新片实现。 |
| PH-271 | 导入预览实现首片：CSV 模板识别、映射与逐行冲突（预览零账本） | proposed | 已覆盖→关闭（cancelled） | 覆盖：PH-274（导入预览实现，merged） 覆盖；证据见 §2.1 |
| PH-272 | 期间核对与工作台读模型实现首片：只读预检、差异单与项目汇总 | proposed | 未开工→保留 | PH-277/363 仅契约；期间预检/差异单读模型未实现，保留 proposed。 |
| PH-285 | 核对底稿只读导出首片：结算/收入/现金核对清单的授权 CSV 导出（无写入口） | proposed | 未开工→保留 | PH-304 仅导出 ops_overview/settlement_coverage/cash_bridge/fee_pairing；PH-316 仅给这四类加描述符；**无 revenue_pairing 导出**、PH-325 未接通；原 DoR 的导出数据集 {settlement_coverage, revenue_pairing, cash_bridge} 未覆盖， |
| PH-289 | onboarding 导入提交与冲突持久化前置：ImportPreview/ImportConflict 落库与逐域提交编排（无业务写） | proposed | 未开工→保留 | PH-362 仅导入提交契约；ImportPreview/ImportConflict 持久化与提交未实现（PH-353 仍 proposed），保留 proposed。 |
| PH-292 | onboarding 导入提交首片：预览持久化后逐域提交编排实现（先前置后写） | proposed | 未开工→保留 | PH-353 仍 proposed 且无报告/实现；导入提交首片未开工，保留 proposed。 |
| PH-295 | 工资现金命令层首片：工资批次发放/代扣/社保与个税的现金命令边界（先前置后写） | proposed | 未开工→保留 | PH-171 仍 proposed（退款/银行退回未关闭）；工资现金命令层未实现，保留 proposed。 |
| PH-296 | 费用报销/支出（WP-EXPENSE/SPEND）首片：费用申请-审批-现金/成本链路前置冻结 | proposed | 未开工→保留 | PH-150/PH-155 仍 proposed；费用报销/支出链路未实现，保留 proposed。 |
| PH-297 | 服务期预收/递延（WP-PREPAID-SERVICE）首片：预收批次与月度递延确认前置冻结 | proposed | 未开工→保留 | PH-163/PH-170 仍 proposed；服务期预收/递延未实现，保留 proposed。 |
| PH-301 | 到期扫描与通知中心（WP-NOTIFY）首片：待办/到期提醒只读收件箱与去重 | proposed | 未开工→保留 | PH-364 到期扫描/通知契约已 done；实现由 PH-380 承接，PH-340/344/355 链未 done，保留 proposed。 |
| PH-310 | 运营核对导出后续首片：CSV 归档/格式版本与导出审计（只读底稿后续） | proposed | 未开工→保留 | PH-285 重开 proposed（缺 revenue_pairing 导出）；PH-304 已 done；真实 gap：CSV 归档/格式版本/导出审计未实现，保留 proposed。 |
| PH-314 | 通知中心/导出归档实现前置：提醒派生与导出归档落地边界 | proposed | 未开工→保留 | PH-364 通知契约已 done；导出归档/通知实现由 PH-340/380 承接，保留 proposed。 |
| PH-338 | 发票批量/导入后续：发票导入模板、批量登记与来源分配（按 ADR 0015/PH-252） | proposed | 未开工→保留 | PH-181/PH-185/PH-289 仍 proposed；发票批量/导入实现未开工，保留 proposed。 |
| PH-339 | insights 读模型实现首片：期间预检/核对差异单/工作台只读投影（按 ADR 0014） | proposed | 未开工→保留 | PH-272 仍 proposed（期间预检/差异单读模型未实现）；insights 验收未开工，保留 proposed。 |
| PH-340 | 通知中心实现首片：待办/到期提醒收件箱、去重与权限过滤（WP-NOTIFY） | proposed | 未开工→保留 | PH-301/PH-314 未 done；通知收件箱实现需先完成前置契约，保留 proposed。 |
| PH-342 | 发票批量/导入实现前置契约冻结（docs-only） | proposed | 未开工→保留 | PH-289 仍 proposed；发票批量/导入契约冻结未开工，保留 proposed。 |
| PH-343 | insights 读模型实现前置契约冻结（docs-only） | proposed | 未开工→保留 | PH-272 仍 proposed；insights 契约冻结需与 PH-363 去重，保留 proposed。 |
| PH-344 | 通知中心契约前置（docs-only） | proposed | 未开工→保留 | PH-301/PH-314 未 done；通知契约前置保留 proposed。 |
| PH-353 | 导入提交拆分：ImportPreview/ImportConflict 持久化与原子提交首片（PH-289 拆分） | proposed | 未开工→保留 | PH-289 仍 proposed；导入提交拆分未开工，保留 proposed。 |
| PH-354 | insights 读模型前置拆分：期间预检只读 API 首片（PH-272 拆分） | proposed | 未开工→保留 | PH-272 仍 proposed；期间预检只读 API 未实现，保留 proposed。 |
| PH-355 | 通知中心前置拆分：reminder refs 契约与到期扫描骨架（PH-301 拆分） | proposed | 未开工→保留 | PH-364 通知契约已 done；reminder refs/扫描骨架需与 PH-380 链对齐，保留 proposed。 |
| PH-358 | 发票批量导入实现首片（PH-338 后续） | proposed | 未开工→保留 | PH-289 仍 proposed；发票批量导入实现首片未开工，保留 proposed。 |
| PH-359 | insights 差异单/工作台只读实现首片（PH-339 后续） | proposed | 未开工→保留 | PH-272 仍 proposed；insights 差异单/工作台实现未开工，保留 proposed。 |
| PH-360 | 通知中心收件箱实现首片（PH-340 后续） | proposed | 未开工→保留 | 缺 PH-340（通知中心实现首片）与 PH-344（通知中心契约前置）done；二者 proposed，等 PH-301/PH-314 等依赖完成后复核 DoR 再晋级。 |
| PH-377 | 批次 C 发布资格 gate 判定后续（PH-372 后续，docs-only） | proposed | 已覆盖→关闭（cancelled） | 覆盖：PH-372（批次 C 发布资格 gate 判定执行，merged） 覆盖；证据见 §2.1 |
| PH-378 | 发票批量导入实现首片（PH-338/353 后续） | proposed | 未开工→保留 | PH-289 仍 proposed；发票批量导入实现首片未开工，保留 proposed。 |
| PH-379 | insights 差异单/工作台实现首片（PH-339/354 后续） | proposed | 未开工→保留 | PH-272 仍 proposed；insights 实现首片未开工，保留 proposed。 |
| PH-380 | 通知中心收件箱实现首片（PH-340/355 后续） | proposed | 未开工→保留 | 缺 PH-340（通知中心实现首片）与 PH-355（reminder refs 契约/扫描骨架）done；二者 proposed，依赖 PH-301/314/179/183/151 完成后复核 DoR 再晋级。 |
| PH-383 | 发票批量导入错误清单/来源覆盖后续（PH-378 后续） | proposed | 未开工→保留 | 缺 PH-378（发票批量导入实现首片，proposed）done；PH-378 完成后复核 DoR 再晋级。 |
| PH-384 | insights 工作台/差异单性能与导出后续（PH-379 后续） | proposed | 未开工→保留 | 缺 PH-379（insights 差异单/工作台实现首片，proposed）done；PH-379 完成后复核 DoR 再晋级。 |
| PH-385 | 通知中心扫描调度/已读后续（PH-380 后续） | proposed | 未开工→保留 | 缺 PH-380（通知中心收件箱实现首片，proposed）done；PH-380 完成后复核 DoR 再晋级。 |
| PH-388 | 多币种汇率反查/对账后续（接收 PH-318/326 剩余范围） | ready | 已就绪待实现→保留 | 无实现证据；保留原状态，需 DoR/依赖复核 |
| PH-389 | 批次 D-03/04 发票/导入验收执行（PH-378/383 后续） | proposed | 未开工→保留 | 缺 PH-378（发票批量导入实现，proposed）与 PH-383（错误清单/来源覆盖，proposed）done；PH-362/PH-353 等上游未齐；依赖完成后由下一规划槽复核 DoR 再晋级。 |
| PH-390 | 批次 D-05/06 insights 验收执行（PH-379/384 后续） | proposed | 未开工→保留 | 缺 PH-379（insights 实现，proposed）与 PH-384（性能/导出后续，proposed）done；依赖完成后由下一规划槽复核 DoR 再晋级。 |
| PH-393 | D-01 跨 owner 编排 L3/L4 验收补证（PG/浏览器） | proposed | 部分覆盖→保留/转新片 | 真实 PostgreSQL/浏览器 L3/L4 环境未就绪；PH-382/PH-392 已 done，PH-334/PH-349 仅有 L1/L2 证据；保留 proposed，L3/L4 不得用本地 SQLite 冒充。 |
| PH-394 | 批次 D 发布资格 gate 终审（PH-366 契约） | proposed | 未开工→保留 | 缺 PH-372/PH-387/PH-388/PH-389/PH-390/PH-392 done；L3 CI/PG、L4 浏览器、L5 生产/客户验收未执行；不得自批豁免。 |
| PH-395 | v1.0 发布资格阻塞清单与三方签批清单（docs-only） | proposed | 未开工→保留 | 缺 PH-372/PH-387/PH-388/PH-394 done；L3/L4/L5 与三方签批未完成；只登记缺口，不得宣称发布就绪。 |
| PH-396 | v1.0 遗留任务对账与关闭（docs：~115 open 任务映射/关闭规则/最终交付与阻塞清单） | ready | 本片 in_review→保留 | 本片即对账任务，不参与关闭计数；随复审合入 |

### 2.1 关闭项逐项证据表（6 项，cancelled 保留历史）

| 任务 | 关闭原因（DoR 已被合并实现/验收覆盖） | 证据（merged PR / report） |
| --- | --- | --- |
| PH-176 唯一主交付：发票、分配与红冲 | 发票登记/分配/红冲/税额资格已由 PH-252 首片 + PH-320 税额确认/跨 owner 编排 + PH-325 只读对账完整覆盖 | #205（PH-252）、#214（PH-320）、#215（PH-325）及对应 `project/reports/PH-252|320|325.json` |
| PH-187 CSV 上传→模板识别→映射→冲突预览 | onboarding 导入预览（ImportPreview/ImportConflict、零账本、逐行冲突）已由 PH-274 交付 | PH-274 merged 实现与 `project/reports/PH-274.json` |
| PH-256 导入预览与冲突清单首片 | 同上：PH-274 已交付模板识别/字段主数据映射/逐行冲突、预览零账本 | PH-274 |
| PH-271 导入预览实现首片 | 同上：PH-274 即该能力的实现首片 | PH-274 |
| PH-261 预算版本与 ETC 首片 | 预算版本/行/批准与 ETC 只读派生已由 PH-306（ETC 契约/实现前置）+ PH-330（costing 0006 预算版本/批准/ETC）覆盖 | #206（PH-306）、#218（PH-330） |
| PH-377 批次 C 发布资格 gate 判定后续 | 该后续判定已由 PH-372 执行（blocked、release_ready=false、G1–G8、digest 可复算）覆盖 | #250（PH-372）及 `project/reports/PH-372.json` |

### 2.2 重开清单（60 项）

原 66 项关闭中，除 §2.1 的 6 项外，其余 **60 项**因下列原因重开为 BASE 状态（proposed/blocked，逐项 gap 见 §2 表）：
- 被引用的实现/验收报告明确声明相关任务仍 proposed（如 PH-169/170/171、PH-183/355、PH-289/353/338）；
- 仅有契约/本地 rehearsal（如 PH-195/197/198/201 的 L3/PG/生产未执行）；
- 只读总览/聚合/导出描述符不满足 DoR 的模型与刷新要求（如 PH-262 MetricSnapshot、PH-285 revenue_pairing 导出）；
- 其余未开工/部分覆盖任务按 §2 表保留并刷新精确 blocker。

重开后已同步：tasks 状态/`depends_on`/blockers（无 cancelled 悬空引用）、ROADMAP、`docs/delivery-status.md`、`docs/verification.md`。

## 3. 最终 v1.0 交付清单（已覆盖能力 + 证据索引）

| 能力域 | 已合并交付（证据索引） |
| --- | --- |
| 身份/成员/项目/往来方 | PH-212/PH-264/PH-281（reports、merged） |
| 合同/变更/收费模板 | PH-212/PH-214/PH-265/PH-275/PH-287 |
| 时间/可计费确认 | PH-269/PH-276 |
| 结算/未清/核销/退款 | PH-224/PH-232/PH-240/PH-283/PH-249 |
| 现金/银行/手续费/互转 | PH-228/PH-230/PH-239/PH-244/PH-245/PH-279/PH-288 |
| 成本/共享分摊/预算 ETC | PH-217/PH-305/PH-306/PH-313/PH-330 |
| 发票/税额/红冲/成本更正 | PH-252/PH-293/PH-317/PH-320/PH-325/PH-371 |
| 管理收入/服务期/编排 | PH-219/PH-222/PH-284/PH-370 |
| 关期/结项/预测/核对 | PH-299/PH-322/PH-363/PH-291/PH-309/PH-316 |
| 导入/onboarding 契约 | PH-274/PH-362（实现链 PH-353/PH-378 保留） |
| 批次 C 演练/验收 | PH-333/PH-334/PH-335/PH-347/PH-348/PH-349/PH-350/PH-357/PH-366/PH-367/PH-372 |
| 批次 D 验收/规划 | PH-375/PH-382/PH-387/PH-388/PH-392（其余保留） |

> 证据以各任务 `project/reports/<ID>.json`、merged commit/PR 与 `docs/verification.md` 为准；本清单不替代发布资格 gate。

## 4. 发布阻塞清单（v1.0）

| 阻塞 | 内容 | 责任/处置 |
| --- | --- | --- |
| L3 CI/PG | 真实 PostgreSQL 并发/恢复/容量与 CI run URL 未执行；SQLite/本地不冒充 | CI/部署 + PH-334/349/393；补 CI artifact |
| L4 浏览器 | 1440/390、键盘/焦点、空态/错误/加载未走查 | 前端/验收 + PH-393；补截图/记录 |
| L5 生产/客户 | 生产/试点/客户验收、RPO/RTO 实测未执行 | 产品/发布 + PH-206/PH-394；三方签批 |
| 人工签批/豁免 | PH-366/367/372 三方签批（产品/发布/独立复核）未完成；执行人不得自批 | 发布负责人；PH-394/395 |
| 批次 D 依赖 | PH-389/390 依赖 PH-378/383/379/384 未 done；PH-393/394/395 未执行 | 规划/执行；按 PH-392 顺序 |
| 发布包/支持/UAT | PH-204/205/206/207 未实现；发布包/支持/UAT 证据缺失 | 发布负责人；PH-394/395 |

## 5. 建议 1.0 收尾动作

1. 先合入/验收 PH-241（report/reviewer pass 已有），再按 PH-392 顺序执行 D-02（PH-387）→ D-09（PH-372）→ D-03/04（PH-389）→ D-05/06（PH-390）。
2. 完成 PH-393（L3/L4 补证）、PH-394（批次 D gate 终审）、PH-395（v1.0 阻塞/三方签批清单）；PH-020 上线就绪评审在 gate 通过后执行。
3. 关闭 v1.0-preparation 规划链（PH-391 已收尾）；不再登记规划槽，执行/验收由现有片承接。
4. 任何 L3–L5 未执行项保持 not_run/blocked，不以文档/配置/本地 L2 冒充发布资格。

## 6. 独立复核

- 复核人：independent PH-396 closure reviewer（fresh context，read-only，pending）。
- 复核点：逐项证据是否可追溯、关闭是否有证据、保留 gap 是否精确、依赖/编号是否一致、是否批量无据关闭、是否把文档当实现、是否自批豁免。
- 执行人不得自批；reviewer PASS 后由 reviewer-b 写入 pass 块，最终 evidence commit 再更新 report/verification。

## 7. 任务板与编号

- v1.0.0 对账后：**6 项关闭**（覆盖 cancelled）、**106 项保留（未开工/部分覆盖，含本片 PH-396）**、**1 项验收中（PH-241 in_review）** = **113**；cancelled 共 12 = 原 6（PH-318/326 + 非 v1.0 4）+ 新 6；非 v1.0 附录 17 项保留。
- 编号：PH-392 已合入；PH-393–PH-396 连续；本片未新增编号，关闭项保留历史 ID，不删除、不复用。
- 依赖：关闭项置 cancelled 后不再阻塞保留项；保留项 blocker 已按最新 BASE 刷新；无“关闭后仍被依赖”的悬空引用。

## 附录 A：非 v1.0.0 遗留（保留，不属本片关闭范围）

| 任务 | 里程碑 | 状态 | 标题 | gap |
| --- | --- | --- | --- | --- |
| PH-003 | v0.2.0 | proposed | 建立可复验的Linux部署与容器集成验收 |  |
| PH-004 | v0.2.0 | proposed | 实现首次启用向导与重复安装保护 |  |
| PH-007 | v0.3.0 | proposed | 结算插件：应收应付与并发核销 |  |
| PH-008 | v0.3.0 | proposed | 现金插件：预收预付、退款与未分配收付 |  |
| PH-010 | v0.3.0 | proposed | 人工成本池与组守恒费率快照 |  |
| PH-012 | v0.3.0 | proposed | 发票登记、分配与红冲 |  |
| PH-013 | v0.3.0 | proposed | 存量期初迁移与启用核对 |  |
| PH-015 | v0.3.0 | proposed | 管理收入确认：方法互斥、来源与调整 |  |
| PH-016 | v0.4.0 | proposed | 经营读模型与老板决策工作台实现 |  |
| PH-017 | v0.4.0 | proposed | 系统AI：结构化提取与可追溯经营简报 |  |
| PH-018 | v0.4.0 | proposed | 个人Agent：真实客户端接入与对话填报验收 |  |
| PH-019 | v0.4.0 | proposed | PostgreSQL并发、恢复、升级与规模验收 |  |
| PH-026 | v0.2.0 | blocked | Kimi全站界面与经营趋势可视化 | 须依赖完成后，经下一独立审阅PR检查DoR并晋级ready；本规划不派遣实施。 |
| PH-110 | v0.2.0 | ready | 将蓝白极简设计包整合为统一前端规范 |  |
| PH-120 | rolling | ready | 可信PR交付契约、独立审查与自动仲裁 |  |
| PH-121 | rolling | proposed | 客观覆盖率、财务反例和发布边界 |  |
| PH-122 | rolling | proposed | 远端强制门禁启用、对抗演练与新会话交接 |  |
