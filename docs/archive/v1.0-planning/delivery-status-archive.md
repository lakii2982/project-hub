# 交付状态归档（历史条目，append-only）

2026-10-30 前的完整交付状态快照（含结构化现状分析与逐任务条目）与后续逐任务条目按时间顺序保留于此；当前能力概览见 [delivery-status.md](../delivery-status.md) 现状头。本文件条目只追加，不改写。

# 交付状态：0.1 工程验证版

汇总任务：PH-109（保留各历史任务的日期与证据）
更新日期：2026-10-27
口径：本文只陈述已核对的事实，依据是当前实代码、本机验证与对应提交的GitHub CI实际执行结果。`product-requirements.md` 与 `accounting-rules.md` 是目标设计，其中能力大多数尚未实现。插件化设计见 `plugin-architecture.md`。

## 1. 当前结论（2026-10-27）

当前发布版本仍为0.1.0工程验证版，代码主线继续v0.2基础产品化。项目工作流、成本/现金分账、审批审计、导入导出、个人API/MCP及三个叶插件已实现。最新主线还包括：

- PH-022：项目详情六页签、深链接/键盘导航、历史任务区和成本预算余额图。实际线只取项目成本账，包含冲销；计划线仅供参考，历史按当前批准预算重述。财务图遵循既有finance_read，个人费率与人工明细仍单独遮蔽。
- PH-023/024/025：统一视觉规范、Project/Employee/CostRate唯一维护服务、关键任务关联同项目里程碑或有效收付款计划、浏览器按项目登记工时。历史任务及可选task_id兼容保留，不重算历史成本。
- PH-100至107：前台macOS四CLI执行与GitHub交付闭环演练。PH-108只修取消测试同步，生产执行器的隔离规则未改变。

PH-026已ready但未实现：全站界面统一、独立基础资料模块、经营趋势仍待交付。正式合同、管理收入、应收应付、预收预付/退款、发票、多币种、共享费用分摊、月度人工池、期初数据与通知中心仍未实现；core业务未完全插件化。

代码与自动化：产品收口PR[#52](https://github.com/lakii2982/project-hub/pull/52)在最终head d1743ac的8项required CI通过后合入main b3d5f85；[业务/容器运行40486300463](https://github.com/lakii2982/project-hub/actions/runs/40486300463)及[CodeQL运行40486300490](https://github.com/lakii2982/project-hub/actions/runs/40486300490)均通过。该产品组合本机440项回归中436通过、4项环境限制跳过，22项详情专项通过。更早的95/128/417等数量是各日期历史快照，不代表当前数量。

浏览器：最终产品补丁实际检查1440px与390px、六页签、Home键、刷新、无页面横溢、窄屏图表局部滚动；统一令牌HTTP字节与仓库唯一源一致。关闭JavaScript的历史浏览器检查挂起，未计通过。本轮本机环境的真实登录与完整性结果见[验证记录](verification.md)和[测试环境说明](local-testing.md)。

生产边界：CI已执行SQLite/PG回归、迁移、append-only、MCP握手、容器健康/重启和数据库dump/隔离恢复；真实公网TLS、财务/锁账多连接并发、非空附件PG恢复后的凭证/报表检查、真实模型及产品个人Agent客户端联调仍未验证。CI通过不等于生产验收。

工程治理继续采用仓库任务契约、独立S级复核、PR与当次required CI。历史恢复PR#15及其128项测试记录保留在verification.md；本轮未修改保护、检查器或发布标签。

## 2. 已实现能力与验证证据

下表逐项证据沿用已存在的自动化用例，标注到方法名；「代码」指逻辑存在但无对应该行为的专门断言。

### 2.1 组织、权限与隐私

| 能力 | 实现 | 证据 |
| --- | --- | --- |
| 组织、成员、五角色；行级项目可见性 | core/models.py、core/access.py | 测试：test_domain.test_other_organization_pm_cannot_access_or_write_project、test_unassigned_pm_cannot_access_or_write_project；test_http.test_cross_company_project_and_evidence_are_not_accessible |
| 财务字段遮蔽（非财务角色金额置空） | core/views.py redact_card | 测试：test_http.test_project_views_hide_labor_entries_but_keep_total_cost |
| 个人费率/依据与工时成本明细仅 owner/finance；PM 与审阅不可见 | core/views.py（settings、time、export） | 测试：test_http.test_pm_and_auditor_time_views_hide_personal_rate_details、test_time_export_columns_depend_on_finance_role、test_ledger_export_is_limited_to_owner_and_finance |
| 提交人/业务本人不能审批本人记录 | core/services.py review | 测试：test_domain.test_employee_cannot_approve_own_submitted_time、test_employee_cannot_approve_time_submitted_by_an_agent |
| 登录锁定（django-axes）、生产强制独立密钥与 PG | pmo/settings.py | 代码；PG回归已通过，真实生产TLS未验证 |

### 2.2 项目生命周期与任务

| 能力 | 实现 | 证据 |
| --- | --- | --- |
| 三类模板生成真实里程碑及其关联关键任务；不生成日常执行任务 | core/blueprints.py、core/views.py create_from_form | 代码 |
| 项目 8 阶段状态机、乐观锁 version | core/services.py transition | 测试：test_domain.test_execution_transition_requires_approved_budget_baseline、..._requires_contract_reference、..._requires_a_task、..._requires_a_milestone（执行前置四项）；其余阶段门禁为代码 |
| 暂停阻止新增普通工时与任务；暂停/取消可登记实际收付款；归档项目补记收付被拒 | core/services.py check_open、submit_document | 测试：test_domain.test_create_task_rereads_project_and_rejects_stale_closed_project（closed 分支）；on_hold/cancelled 分支为代码 |
| 取消仅 owner；恢复已结束项目仅 owner | core/services.py transition | 代码 |
| 归档前置：ETC=0、无待确认现金计划、里程碑全验收、无未关闭高风险、无待审记录、已批计划全结清 | core/services.py transition | 代码 |
| 关键任务新建/编辑须关联同项目里程碑或有效收付款计划；乐观版本、循环与跨项目依赖检查 | core/services.py edit_task | 代码 |
| 任务依赖完成门禁、完成须附交付说明 | core/services.py update_task | 代码 |
| 里程碑验收证据、风险登记关闭 | core/services.py、core/views.py | 代码 |

### 2.3 工时、单据、账本与审计

| 能力 | 实现 | 证据 |
| --- | --- | --- |
| 浏览器按项目登记工时，无任务输入；提交/审批/退回/冲销及业务日费率快照 | core/services.py | 测试：test_domain.test_time_rate_is_selected_by_work_date、test_rate_change_after_approval_does_not_rewrite_history_cost |
| 同一员工跨项目当日总工时不超过 1440 分钟 | core/services.py submit_time | 测试：test_domain.test_cross_project_daily_total_over_1440_minutes_is_rejected |
| 提交幂等 token：同 token 同内容返回原记录，改内容拒绝 | core/services.py | 测试：test_domain.test_duplicate_submit_token_does_not_create_second_timesheet、test_same_time_token_with_changed_content_is_rejected、test_same_document_token_with_changed_amount_is_rejected |
| 六类单据审批后入账；费用/收付分账；可抵扣税影响成本 | core/services.py review | 测试：test_domain.test_expense_and_payment_post_only_to_their_respective_books、test_recoverable_tax_switch_changes_expense_cost、test_duplicate_approval_does_not_post_twice |
| 现金计划核销未结额检查、超结拒绝、锁内重读计划 | core/services.py | 测试：test_domain.test_partial_receipt_settles_plan_and_overallocation_is_rejected、test_submit_document_rereads_approved_plan_under_lock |
| 冲销只追加负值行、恢复计划、幂等 | core/services.py reverse | 测试：test_domain.test_reversal_adds_negative_record_restores_plan_and_is_idempotent |
| 账本与审计表 append-only 触发器 | core/migrations/0002_append_only.py | 测试（SQLite与PostgreSQL）：test_domain.test_append_only_audit_event_rejects_database_update、test_append_only_ledger_entry_rejects_database_delete；PG触发器已在回归中实跑 |
| 审计哈希链与整链校验 | core/services.py audit/verify_audit | 测试：test_domain.test_audit_chain_is_consistent_and_linked、test_audit_verification_detects_in_memory_payload_tampering |
| verify_integrity 只读完整性检查 | core/management/commands/verify_integrity.py | 代码；PG seed后verify_integrity执行通过。历史演练库47条账本记录仅作历史快照说明 |
| 锁账：截止日只前移、锁期拒绝补录、开放期冲销历史 | core/forms.py OrgForm、core/services.py | 测试：test_domain.test_locked_period_rejects_backdated_time、test_open_period_allows_reversal_of_locked_history |
| 金额解析：两位小数、拒绝分以下小数与非数值 | core/services.py cents | 测试：test_domain.test_cents_preserves_two_decimal_precision_for_ten_thousand_yuan、test_cents_rejects_fractional_cent、test_cents_rejects_nan_negative_and_non_numeric_values |

### 2.4 导入导出与 HTTP 安全

| 能力 | 实现 | 证据 |
| --- | --- | --- |
| CSV 导入三类：预览回滚、整批全或无、行级幂等冲突 | core/imports.py | 测试：test_imports_ai 的 9 个 CSV 用例（preview 回滚、commit 幂等、整批回滚、三类改内容冲突、BOM/中文、多列拒绝、重复来源拒绝） |
| CSV 导出按角色裁剪、公式注入转义 | core/views.py export_data | 测试：test_http.test_ledger_csv_neutralizes_formula_injection 及 2.1 隐私三条 |
| 13 个主页面真实模板渲染 | tests/test_http.py | 测试：test_thirteen_main_pages_render_with_real_templates |
| GET 不触发变更；无 CSRF 拒绝；匿名与越界访问拒绝 | core/views.py 等 | 测试：test_http.test_get_cannot_mutate_review_reverse_or_status_routes、test_post_without_csrf_token_is_rejected、test_anonymous_user_cannot_access_workspace_or_evidence、test_member_cannot_download_another_users_evidence |
| URL 主键长度有界（pid 转换器，最多 18 位数字） | pmo/urls.py SafeIdConverter | 代码 |

### 2.5 规则引擎、预测与系统侧 AI

| 能力 | 实现 | 证据 |
| --- | --- | --- |
| 规则告警（超预算、任务/里程碑逾期、未批基线、回款/付款逾期、ETC 缺失） | core/reporting.py alerts_for | 代码 |
| 13 周现金预测：只按未来已批准日期入桶，过期未执行项不挤入当前周 | core/reporting.py cash_forecast | 代码（口径已按 A20 修正） |
| 逾期未执行计划单列数量与待收/待付合计，页面标注「预计累计净流入」 | core/reporting.py overdue_plan_totals、模板 | 代码 |
| 模型草稿结构校验（schema、金额、证据回引、项目白名单、禁用时不发网络请求） | core/ai.py | 测试：test_imports_ai 的 7 个 validate_draft 用例与 test_disabled_external_model_does_not_call_network；真实模型调用未验证 |

### 2.6 个人 Agent 接入（api/v1 与 PAT）

实现：core/api.py（DRF 视图）、core/agent_auth.py（令牌签发/撤销）、core/migrations/0003（AgentToken、AgentOperation）、pmo/urls.py 的 /api/v1/ 七个端点、设置内「接入」页面（签发需 Django authenticate 密码确认，走 axes 限流）。

| 能力 | 证据 |
| --- | --- |
| 令牌只存 HMAC 哈希、不写审计明文；创建仅在页面展示一次 | 测试：test_agent_api.test_token_is_stored_hashed_and_not_recorded_in_audit |
| 过期、撤销、密码变更、账号停用立即失效 | 测试：test_revoke_expire_deactivate_password_change_are_effective |
| 项目范围与租户强制；不得为外部项目或特权动作签发 scope | 测试：test_project_scope_and_tenant_are_enforced、test_scope_cannot_be_issued_for_foreign_project_or_privileged_action |
| 只读令牌不能写；scope 在每次请求重查当前角色，降级即失效；限流生效 | 测试：test_read_only_token_cannot_submit、test_role_downgrade_removes_write_and_rate_limit_is_enforced |
| 写操作默认 dry-run 且整体回滚（业务、审计、outbox 均不留痕） | 测试：test_dry_run_is_default_and_rolls_back_business_and_outbox |
| 工时/费用仅本人、提交即待审批、幂等；同键改负载 409 | 测试：test_timesheet_submission_is_personal_pending_and_idempotent、test_same_idempotency_key_changed_payload_is_conflict、test_dry_run_after_submission_reports_existing_without_second_write |
| 严格 JSON：未知字段、伪造员工/审批字段、非严格类型一律拒绝 | 测试：test_spoofed_employee_approval_and_unknown_fields_are_rejected、test_strict_units_booleans_and_idempotency_header |
| 任务状态仅本人负责项、重试不推进 version | 测试：test_task_update_only_own_and_repeat_does_not_increment_version |
| 默认不暴露成本费率；财务汇总需显式 scope 且重查角色 | 测试：test_personal_and_read_scopes_do_not_disclose_finance |
| 费用接口不能创建付款或审批；无审批/付款/锁账路由 | 测试：test_expense_api_cannot_create_payment_or_approve_cost、test_agent_api_has_no_approval_payment_or_lock_route |
| 会话 Cookie 与查询串令牌不能调 API | 测试：test_session_alone_and_query_token_do_not_authenticate_api |

以上 17 项仅在 SQLite 测试环境验证；未接入真实 WorkBuddy/Codex 客户端。

### 2.7 MCP stdio 适配器

实现：integrations/mcp/server.py（官方 Python MCP SDK FastMCP，7 个工具）、README、workbuddy.example.json、codex.example.toml。行为：令牌只从进程环境读取；base URL 拒绝凭据/查询/非 HTTPS（本机 HTTP 需显式 PMO_ALLOW_LOCAL_HTTP=1）；写工具默认 dry_run；不跟随重定向；错误与成功载荷脱敏（去除费率字段、遮盖令牌）；严格整数在发网前拒绝。证据：test_server.py 8 项 unittest 通过；真实 stdio 握手列出 7 个工具通过。未接入真实 WorkBuddy/Codex 客户端联调。
端到端冒烟：scripts/smoke-personal-agent.py 真实跑通 MCP → HTTP → 服务层链路——4 个实时读工具与 3 个写工具 dry-run 全部通过，0 业务写入，临时演练 PAT 已撤销。仍未配置真实 WorkBuddy/Codex 客户端。

### 2.8 插件运行时与端到端流程

实现：core/plugin_runtime.py（pluggy 钩子、manifest 校验、kernel 版本与依赖图检查、每公司 PluginState 启停、DomainEvent/EventDelivery outbox、process_events 重试）、core/plugin_views.py（/plugins/ 页面）、core/migrations/0004；叶插件 pmo_plugins/project_health（规则洞察）、delivery_watch、agent_gateway（启停门控 api/v1）。边界：这是扩展骨架加 3 个叶插件，core 业务模块未拆分为插件，不能宣称全部业务已可拔插。

| 能力 | 证据 |
| --- | --- |
| 默认值与公司间隔离 | 测试：test_plugins.test_defaults_and_company_isolation |
| 仅 owner 可启停、依赖强制 | 测试：test_only_owner_can_toggle_and_dependencies_are_enforced |
| 版本不匹配停用至 owner 确认 | 测试：test_version_mismatch_disables_until_owner_accepts_installed_version |
| kernel 不兼容与依赖循环不拖垮无关插件 | 测试：test_invalid_kernel_and_dependency_cycle_do_not_break_unrelated_plugins |
| 洞察按授权快照裁剪、非财务快照不出财务洞察 | 测试：test_base_and_independent_extension_produce_scoped_insights、test_non_finance_snapshot_does_not_emit_financial_insights |
| 失败或试图改数据的插件被隔离；输入为副本 | 测试：test_failing_or_mutating_plugin_is_isolated_from_other_plugins |
| 不可信 URL 拒绝 | 测试：test_untrusted_insight_url_is_rejected |
| 业务回滚则 outbox 事件同事务消失 | 测试：test_business_rollback_also_removes_outbox_event |
| 投递幂等去重、停用保留队列、失败最多重试三次 | 测试：test_delivery_is_idempotent_and_disable_preserves_queued_event、test_failed_delivery_retries_at_most_three_times |
| 停用 agent 插件阻断 API 与新签发但允许撤销 | 测试：test_disabling_agent_plugin_blocks_api_and_new_grants_but_allows_revocation |
| 队列页包括未分发事件，不混入其他公司的事件或失败数 | 测试：test_plugin_page_shows_undispatched_backlog_without_other_company_counts |
| 真实表单从立项走到归档（多人分权） | 测试：test_workflow.test_project_from_creation_to_archive_uses_real_forms_and_separate_review |
| 逾期计划与未来预测、核销分列 | 测试：test_workflow.test_overdue_plans_are_separate_from_future_forecast_and_settlements |

### 2.9 演练与部署件

演练数据（4 项目）与正式公司初始化：core/management/commands/seed_demo.py、bootstrap.py；本地演练库已初始化。真实浏览器完成桌面与手机视图检查、插件启停及提醒变化检查，截图在 screenshots/。四个既有演练项目的开始日期已与演练任务对齐，并追加审计，未改账本值。

部署件：Dockerfile、compose.yaml（db、app、worker、proxy 四服务）、Caddyfile、scripts/entrypoint.sh、scripts/backup.sh（pg_dump、附件打包、SHA256）。容器冒烟实际构建db/app/worker，通过`check --deploy`、seed、HTTP健康、重启持久性；backup.sh完成pg_dump与SHA校验，并在隔离新PG库完成恢复与integrity。本地 `backup_local` 与 `scripts/verify-local-backup.py` 曾完成47条账目和1个演练附件的隔离SQLite恢复，属于历史快照证据；覆盖拒绝、权限 0700/0600、篡改检出、空库与空附件包曾通过。PostgreSQL备份链中的非空附件恢复与恢复后报表/凭证浏览未验证。没有覆盖活动数据库；备份与登录凭据不进入源码包。

生产备份脚本的4项mock测试覆盖：正常备份、已有目录/符号链接拒绝、pg_dump失败时恢复写服务、信号退出时只恢复原运行服务；生成相对路径校验清单，失败保留.incomplete。它们证明脚本控制流，不能替代生产级运行与PostgreSQL备份链中的非空附件恢复验证。

## 3. 现金计划的定位

CashPlan 是计划待收/待付，用于 13 周现金预测和实际收付款的未结额核对。它不是应收应付：没有结算事件产生债权债务，没有未清项实体，没有核销单；plan_remaining 只是「计划额 − 已关联实际收付」，不是 A13 的未清应收应付。没有管理收入确认（A12），项目毛利、毛利率当前算不出来，页面也不展示毛利；预计累计净流入不是利润也不是银行余额。逾期告警基于计划到期日，不是合同结算条件的账龄。

## 4. 双层 AI 现状

| 层 | 内容 | 状态 |
| --- | --- | --- |
| L1 规则与风险引擎 | reporting.alerts_for、阶段门禁、verify_integrity | 已实现，证据见 2.5 |
| L1 模型草稿提取 | core/ai.py finance-draft/v1 | 结构校验有测试；真实模型未验证，默认关闭 |
| L1 其余场景（摘要、匹配候选、异常解释） | 无 | 未实现 |
| L2 api/v1 与 PAT | core/api.py、core/agent_auth.py、迁移 0003 | 已实现，17 项测试（SQLite），证据见 2.6 |
| L2 MCP stdio 适配器 | integrations/mcp/ | 已实现，8 项测试（主代理跑通）；未做真实客户端联调 |

边界已按设计落地：个人 Agent 只能以本人身份提交本人工时/费用到待审批，无审批、付款、锁账、角色工具；服务器不持有个人模型凭据；无共享管理员密钥。

## 5. 与目标核算规则的口径差异

| 项 | 当前实现 | 目标设计 | 消除载体 |
| --- | --- | --- | --- |
| 金额上限 | 10^12 分，输入提示 100 亿元，已符合 A03 | A03.6 | 已一致 |
| 费率精度 | 小时费率为整数分（元两位小数），按生效日期取用 | A03.4/A05：8 位小数月度快照 | WO-05 |
| 工时成本取整 | 逐工时行 HALF_UP 到分 | A04：取整组守恒、最大余数法 | WO-05 |
| 通用分配 | 无分配算法 | A04 最大余数、含税分配守恒 | WO-04 |
| 币种 | 固定 CNY，无汇率 | A02/A03：原币整数、汇率 8 位小数 | 下一批工作单 |
| 时间口径 | 整数分钟、单日 1–1440 | D08：步长与日阈值可配置并留版本 | WO-06 |
| 锁账粒度 | 全公司单一截止日期 | A02/A23：期间对象、锁定快照、双视图 | 后续批次 |

## 6. 未验证事项

1. 生产TLS与Caddy：HSTS、Secure Cookie、SSL重定向与真实Caddy公网访问未在真实TLS环境验证。
2. 并发：PG回归通过不代表生产并发正确；多连接财务/锁账并发与锁账串行化行为仍未验证。
3. 备份恢复边界：数据库恢复与integrity已通过；PostgreSQL备份链中的非空附件恢复、恢复后报表与凭证浏览未验证。
4. 真实模型提取：服务器未配置模型凭据，AI_ENABLED 默认关；结构合规率与提示注入抵抗（P12）未经真实模型验证。
5. 真实 Agent 客户端：MCP → HTTP → 业务服务已实时冒烟通过；尚未配置真实 WorkBuddy/Codex 实例联调，示例配置不是客户端验证。
6. 项目规模下的吞吐、P95 与可访问性专项检查尚未执行；页面渲染用例及浏览器检查不构成负载测试。
7. 性能与真实业务规模下的专业PMS功能完整性仍待专项验证。

## 7. 尚未实现的目标设计能力

- 客户/供应商/机会对象；工作包。
- 正式合同、合同明细、计量方法、合同版本与变更单（WO-01）。
- 管理收入确认（A12）、结算项、应收应付未清项、核销、折让、坏账、账龄（WO-02）。
- 预收预付、抵扣、退款与银行退回（WO-03）；员工借支；预付服务分期。
- 共享费用池与分摊（WO-04）；月度人工成本池、有效工时、薪酬差额（WO-05）；加班标记。
- 发票登记、分配、红冲（WO-07）；税额待确定流转。
- 多币种与已实现汇兑损益（下一批）。
- 期初两条路径与启用向导（WO-08）。
- 通知中心、待办推送与定期业务扫描（WO-09）；已有 outbox 事件消费不能替代这些能力。
- 采购/外包验收与承诺管理；ETC 快照版本。
- 银行流水登记与待匹配区；手续费分解。
- 审批链配置、代理审批、审批绑定内容哈希。
- 「业务已结项、财务未结清」分离；归档后质保金收款通道。
- 双人锁账、期间快照报表。
- core 业务模块的插件化抽离（当前为扩展骨架加 3 个叶插件，目标模块及升级设计见 plugin-evolution.md，前置单 WO-14）。
- 系统侧 AI 其余场景；跨租户多组织成员切换。

## 8. 目标需求到工作单映射

| 目标能力 | 当前代码 | 证据 | 工作单 |
| --- | --- | --- | --- |
| 正式合同/变更（D06、7.4） | 项目合同额字段加预算基线/变更单据 | 测试（预算侧） | WO-01 待执行 |
| 应收应付未清项/核销（A13、P10） | CashPlan 计划口径 | 测试（计划口径） | WO-02 待执行 |
| 收入确认（A12） | 无 | 无 | WO-02 之后批次 |
| 预收预付/退款（A14） | 无 | 无 | WO-03 待执行 |
| 共享费用分摊（A10、T09/T28） | 无 | 无 | WO-04 待执行 |
| 费率池/组守恒（A04/A05） | 整数分费率逐行 HALF_UP | 测试（现行口径） | WO-05 待执行，依赖 WO-04 |
| 资源周工时（9.1、D08） | 逐行登记加日上限 | 测试（日上限） | WO-06 待执行 |
| 发票（A11） | 无 | 无 | WO-07 待执行，依赖 WO-02 |
| 期初与启用向导（A21、D07） | bootstrap CLI | 代码 | WO-08 待执行，依赖 WO-02/WO-03 |
| 通知/后台任务 | 页面告警列表 | 代码 | WO-09 待执行 |
| 生产 PG 并发/备份恢复（14、A22/A23） | append-only 迁移、公司级行锁、compose 与 backup.sh | SQLite与PG回归、容器冒烟与数据库备份/隔离恢复通过；生产级并发/性能未验证 | WO-10 待执行，依赖 WO-02/WO-03/WO-05 |
| 个人 Agent 接入（api/v1 加 PAT） | core/api.py、core/agent_auth.py | 17 项测试 | 已完成（原 WO-11） |
| MCP stdio 客户端 | integrations/mcp/ | 8 项测试 | 已完成（原 WO-12） |
| 插件 kernel、outbox 与叶插件 | core/plugin_runtime.py、pmo_plugins/ 三个叶插件 | 12 项测试 | WO-13 已完成（骨架；业务模块未全部插件化） |
| 多币种（A02/A03、T15/T16） | 无 | 无 | 下一批 |
| 毛利/经营视图（10.1、A18） | 成本/现金汇总，无收入侧 | 测试（成本侧） | 依赖 WO-02 后排单 |

## 9. 部署与运维件现状

Dockerfile（python:3.12-slim、非 root、collectstatic）、compose.yaml（postgres:17-alpine 的 db、app、worker、Caddy proxy 四服务，健康检查与命名卷）、Caddyfile、scripts/entrypoint.sh（migrate 加 gunicorn 2×2）、scripts/backup.sh（停应用、pg_dump、附件打包、SHA256 清单）已在仓库；源码与脚本内无凭据；compose 各敏感项为必填环境变量。生产配置缺 POSTGRES_HOST 或弱密钥时拒绝启动。db/app/worker容器构建、check --deploy、seed、HTTP健康、重启持久性与数据库备份/隔离恢复已冒烟通过；真实公网TLS与生产级并发/性能仍待验证。




## PH-090：项目详情改版规划（2026-10-23）

用户确认了项目详情横向页签与成本预算余额燃尽图的口径，具体契约见project/specs/PH-022.md。PH-022已登记为ready，尚未实现；计划使用独立工作区中的本机DSH执行，Codex负责共享接线、独立审阅、实际CI与合并。PH-091接续后续规划。代码、浏览器及本机DSH业务执行结果需由PH-022另行验证。

## PH-091：GPT主控与GitHub原生研发规划（2026-10-23）

用户要求只与GPT对话，由GitHub承载需求/缺陷/PR/交付，本地Kimi Code、Claude Code、OpenCode、ZCode与DeepSeek Harness作为执行器池。架构和精确边界见docs/engineering/ai-native-delivery.md；新增PH-100至PH-105六个薄片及PH-092后续规划槽，保留既有PH-022项目详情需求。

本轮交付为规划契约、GitHub父需求#19和六个子任务；没有实现常驻调度器、真实并行执行或生产发布。只读定位到Kimi Code 2.2.0、OpenCode v2.0.30、DSH 0.2.1-rc.2，当前PATH未找到Claude/ZCode入口；版本/帮助证据不等于真实任务通过。自动化与独立审阅记录见project/reports/PH-091.json，远端CI与合并状态查询实际PR。

## PH-100：六种身份协议与只读doctor（2026-10-24）

代码已实现封闭注册表、协议类型和五种worker只读doctor。配置仅接受已知ID的绝对executable路径；探针只运行固定`--version`，具有5秒超时、每流64KiB上限、私有临时环境、进程组清理与输出脱敏。没有任务执行、队列、隔离调度或模型调用。

本机自动化：PH-100/治理32项测试及全量141项测试通过，Django检查与迁移漂移检查通过。实际doctor中Codex 0.164.0、Kimi 2.3.1、Claude 2.1.309、OpenCode 2.0.30、DSH 0.2.1-rc.2为`probe_verified`，ZCode在PATH中为`missing`；版本不认证登录或执行，所有noninteractive/structured_output/resume/cancel能力仍为`unverified`。独立复核、精确证据及边界见project/reports/PH-100.json和project/handoffs/PH-100.md。未做浏览器、生产部署、非macOS或真实worker任务验证；远端CI以实际PR为准。

## PH-101：GitHub需求缺陷桥（2026-10-24）

代码已实现严格需求/缺陷payload、可信grant授权、私有SQLite与跨进程锁、固定gh客户端、Issue幂等创建/更新及unknown对账，另有两个白名单Issue模板。默认dry_run不联网；Issue不形成开工许可，没有worker调度、自动合并或发布。

自动化证据：46项目标/治理测试和168项全量测试通过，覆盖授权伪造、撤销/过期、多进程一次创建与revision顺序、崩溃和丢失响应对账；三轮独立复核通过。未做真实GitHub写操作、gh登录、远端断网或模板浏览器渲染，mock不证明GitHub exactly-once。精确证据见project/reports/PH-101.json和project/handoffs/PH-101.md，远端required checks以实际PR为准。


## PH-092 四CLI本地团队规划（2026-10-24）

已冻结GPT总控、GitHub任务、四CLI执行/复核/PR的最小实现契约，见local-team.md。PH-106/107记录真实审计修复，PH-102至105分阶段实现，PH-093保留下一规划入口。本条只陈述规划，未宣称调度、取消恢复或全流程已实现。PR #30经用户单次明确授权恢复planning入口，原8项required checks、各app_id与strict均已恢复；后续不复用豁免。实际测试/独立复核记录见PH-092报告。

## PH-106 GitHub桥审计修复（2026-10-24）

首次无效授权不占业务键；响应丢失只对完整远端结果恢复，更新绑定原Issue。原生Claude/GLM实施、DeepSeek Harness独立复核，52项专项与174项产品回归在无凭据隔离环境通过；没有生产或远端exactly-once声明。详见PH-106报告。

## PH-102 本地四CLI执行层（2026-10-25）

已有私有队列、三并发/scope互斥、进程身份、取消与保守恢复，以及macOS实机文件隔离和只读复核。Kimi/OpenCode/DSH参与实现，GLM/DeepSeek独立复核。最终79项专项中75项通过、4项嵌套沙箱跳过；240项应用回归有相同4项跳过，另有实机权限探针通过。原生客户端结果见PH-102报告；任务到PR的完整入口仍由PH-103/104交付。

四个原生客户端经本次Executor均完成精确文件写入；DSH与Claude成功区间实际重叠。Kimi最初因遗漏always_thinking能力声明被服务端400拒绝，保留所选模型公开元数据后单独重试通过，未改原配置。该私有fixture不冒充已治理的GitHub开发任务。

## PH-103 · GitHub 任务到本地团队入口（2026-10-25）

已实现统一 Python 命令与 API：任务计划、明确执行、状态、取消、恢复及互斥合件。GitHub Issue 是补充资料；权限取自允许仓库的 origin/main 冻结任务与 spec。各执行器独立目录、持久 run_id、契约摘要与完整差异，合件后补接线会改变最终摘要。72 项控制层测试通过，全量312项中308通过、4项因外层隔离不能嵌套后端而明确跳过；临时 SQLite 迁移与完整性通过。独立复核发现的 Issue、幂等记录、缓存类型、文件/目录替换问题均已修复并复查通过。此项交付的是控制入口，隔离验收、只读独立评审、PR 与合入门禁由 PH-104 继续。

## PH-093 · 交付集成范围补充（2026-10-25）

已证实的极短子进程身份采集竞态纳入 PH-104：仅增加 runner.py/既有执行测试白名单与对应验收，保持活组/未知组 quarantine 和全部 CI；此规划没有修复运行代码。PH-094 保留下一规划入口。

## PH-104 · 团队交付门禁（2026-10-25）

已实现固定验收、无凭据禁网副本、独立只读结构化复核、最终差异绑定、主控 PR 与显式合入门禁。36项门禁、27项执行层测试通过；全量353项中349通过、4项嵌套后端限制跳过，临时库完整性通过。真实 Issue24 的 Kimi/DeepSeek 开发成功且并行122秒，OpenCode 证据绑定模块完成；其较大草稿超时由总控接管，失败如实保留。极短子进程仅在自己退出且组不存在时完成，活组/未知组仍隔离。精确摘要/形式化回执与 CI 以 PR 和 PH-105 核对；不宣称无人值守部署或生产验证。

## PH-107 · 探针边界与四执行器（2026-10-25）

Claude Code/GLM与DSH/DeepSeek通过真实Issue32及新入口并行完成代码/测试，Kimi独立复核PASS。修复深层JSON脱敏、FIFO/非普通文件阻塞及相对PATH失效；当前注册表为总控+四CLI。37专项通过，完整358项354通过/4明确跳过；配置、迁移、临时库完整性通过。最终门禁和当次CI以PR/PH-105记录为准，不表示部署或生产验证。

## PH-105 · 四CLI真实GitHub闭环（2026-10-25）

PH-104需求PR38与PH-107缺陷PR39实际完成四CLI开发、独立复核、隔离验收和当前8项CI并正常合入。真实并行122/325秒；实际取消在新控制器进程中仍保持取消，Issue32重开后18项回归通过再关闭，done任务拒绝执行。默认客户端发现可用，无需新增配置。公开脱敏证据与4项离线核对见tests/delivery_e2e，简明使用见engineering/ai-delivery-pilot.md。完整362项358通过/4明确跳过，配置/迁移/临时库完整性通过。此为前台macOS演练，不是常驻部署或客户验收。

PH-105最后Claude全件复核一次超时，保留失败；补充差异优先的范围/预算提示，36项门禁回归通过，再按新摘要验收/复核。提示预算不是新增服务端权限控制。

## PH-094 · 成本与现金流工作区规划（2026-10-25）

用户六项新增需求已拆成PH-023视觉规范、PH-024基础资料、PH-025关键任务/项目工时、更新PH-022页签与成本图、PH-026全站界面和趋势。视觉由本机Kimi负责，其他执行器按互斥范围并行后端/测试；先规范后前端施工。此条仅为规划，不代表页面或业务已经实现。历史任务/工时关联和账目保留，兼容边界见ADR-0004及任务spec；PH-095保留后续规划入口。

## PH-095 · 视觉规范治理分级修正

可信门禁将AGENTS/任务状态列为critical paths，PH-023从A校正为S；不变更范围、CI或实施顺序。PH-096为后续规划槽。仅规划校正，产品前端未交付。

## PH-023 · Kimi视觉系统（2026-10-26）

视觉系统、前端规范、令牌和交互样张已完成；全部后续前端受AGENTS必读入口约束。2条隔离验收通过，DSH独立全件/GLM补丁复核PASS；主控浏览器320/390/768/1440、无JS降级、移动登录、图表局部滚动与aria展开通过。静态样张只作设计基线，产品界面/业务尚由后续任务交付；当次CI见PR，非生产验证。

## PH-024 · 2026-10-26

以既有Project/Employee/CostRate建立唯一基础资料服务、授权上下文及Kimi维护表单；旧创建/项目编辑委派同一服务，账户开通不再隐式新建员工。公开维护模块界面留PH026。 6项固定命令在无凭据禁网快照通过，最终独立只读复核PASS。后续PH026接入/master-data/唯一入口与Kimi页面；不要另建表。EmployeeEditForm(member,employee,initial)/ProjectMasterDataForm(member,project,initial)只清洗字段，写入由视图调用core.masterdata服务。费率追加、版本/原因/公司锁和审计保持，账户与员工拆开。 详细实际命令/哈希与偏差见project/reports/PH-024.json；当次PR8CI另核对。非生产验证。

## PH-025 · 2026-10-26

关键任务必须链接同项目里程碑或有效收付款计划；保留历史任务与task_id兼容，工时浏览器只按项目登记。追加0005迁移，蓝图仅生成3个真实里程碑及相应关键任务；Kimi最小表单与主控视图原子集成。 8项固定命令在无凭据禁网快照通过，最终独立只读复核PASS。PH022可开始项目页签和成本预算余额图，仍由Kimi负责前端、DSH/GLM负责只读汇总；PH026全站样式与唯一维护入口。Task字段milestone/cash_plan至少一项；estimate历史保留，新建0；TimeForm无task，API/MCP兼容原可选task_id不重算历史成本。 详细实际命令/哈希与偏差见project/reports/PH-025.json；当次PR8CI另核对。非生产验证。

## PH-022 · 2026-10-26

项目详情改为概览/关键任务/里程碑/有权财务/风险/团队页签，保留深链与原操作；概览增加按当前批准预算减业务日期累计确认成本的余额燃尽图和精确数表；历史任务独立保留，不驱动关键任务统计。 实际21专项与完整438项（434通过/4环境跳过）、固定验收通过；独立GLM/Kimi只读复核PASS；1440/390、六页签/键盘/hash/前进后退刷新/局部滚动通过。关闭脚本的浏览器检查挂起中断，未计PASS，静态结构已审；完整限制见report。全站UI和基础资料模块仍由PH026交付，非生产验证。

## PH-096 · 唯一视觉令牌源接线规划

PR52独立GitHub审查指出产品复制令牌会偏离规范；仅为PH022补入pmo/settings.py静态源接线白名单，允许app.css引用docs/engineering/ui-tokens.css唯一源，不改令牌/权限/验收/门禁。实现仍由独立PH022 PR完成，PH097保留规划入口。

## PH-096 · 2026-10-27收口规划补充

PR53连续两次SQLite取消回归失败，停止重试到绿。新增PH108限定恢复取消/回收竞态，PH109按最新代码同步文档及本机演练环境；均须以规划合入后BASE开工，专项恢复另需维护者明确授权。不修改运行代码或降低required checks。

## PH-108 · 2026-10-27取消测试同步

仅修测试同步：正常取消等待marker/running/持久身份；固定start前交错继续要求隔离、空组后恢复且不重启。28专项、418回归（4环境跳过）、配置/迁移/完整性通过，独立审阅通过。未改生产执行器；Popen生命周期ResourceWarning保留，详见PH108报告。远端CI按当前PR核对。

PH022最终补丁（2026-10-27）：canonical ui静态源+Kimi移除复制令牌，HTTP字节与唯一源一致；22专项、440全量（4环境跳过）与配置/迁移/完整性通过。新独立补丁复核通过，1440/390无页面横溢，窄屏图表局部滚动。auditor遵循既有finance_read项目汇总权，个人人工明细仍遮蔽。完整限制见最终报告；当次required CI另核对。


## 2026-10-27 · 蓝白设计规范规划

用户提供的蓝白极简设计包已登记为 PH-110 规范整合任务；本次仅规划，规范、令牌、样张和产品页面尚未切换。PH-026 在 PH-110 完成及后续契约调整前暂停。自动化、浏览器和生产验证均须按各自实际执行结果记录。


## PH-111 · 自主PR交付改造规划（2026-10-27）

从PR56最新main接续，冻结PH120至122的契约/可信仲裁、客观CI/发布分离、远端启用/演练三个薄片，保留PH123下一规划槽。仅规划，未实现或启用新门禁。独立规划审查PASS，实际19项治理测试通过；具体基线/审查边界见PH111报告，远端CI以本PR实际结果为准。原工作区仅本地规则及用户稿保留。

## PH-123 · 1.0开工准备规划（2026-10-27）

登记PH130至137八个准备薄片及PH138后续规划。产品保持119a049冻结，准备薄片中仅PH130先ready，PH138作为独立规划槽保持ready。契约、原型、夹具、环境/PG演练和正式PH晋级由后续任务实际交付；本规划没有实现业务功能。详见engineering/v1-startup-preparation.md及报告。

## 2026-10-28 PH-130准备设计入库

纳入1.0交付方案、简表和长程设计：137需求/62工作包/18UAT。实际只读结构检查、五种拒绝及19治理回归通过；独立复核修正临时加载缓存和政策状态后通过。设计覆盖不是产品实现或客户验收。PH131成为下一准备任务，当前PR的8项required CI仍须合入前核对；产品代码保持119a049，未发布/部署。

## 2026-10-28 PH-131当轮研发环境资格

Kimi/Claude/OpenCode完成真实受控文档写入及范围复检；DSH缺少所需既有认证，failclosed并排除。已实际取消running运行、核实原进程组消失、recover无重启、新工作目录恢复；OS合成隔离及离线Chromium环境探针通过。局部原生verify通过19测试，另一CLI只读review正确BLOCKING不完整矩阵；本任务完整候选另经独立复核PASS与19治理回归。证据见project/reviews/PH-131-environment.json。PG多连接留待PH135，产品、目标Linux/TLS、非空附件恢复和客户验收未验证；当前PR8required CI仍待合入前核对。

## 2026-10-28 PH132字段与政策准备

完成C01-C12目标schema、唯一owner/关系、严格客户端DTO/SourceRef、政策和旧新切写；9schema/2478本地引用、137需求、55命令/25查询/28事件，19正37拒绝实际校核通过，接口与财务独立复核通过。保留原book、金额和旧快照；新人工预算/暂估/实际分层与同源调差已写规格。仅文档/校核工具，不声称产品、迁移或PG已实现；PH133/134下一步准备。当前PR required CI仍须正常合入前核对。

## 2026-10-28 PH133业务原型准备

离线四职责/三模板低保真原型、页面字段权限与唯一来源映射已具备。实际Chromium1440/390验证键盘/焦点、日期错误与预览、财务拒绝和私有区块隐藏，320至1440九个宽度无页面横溢；主控读取全图与390原尺寸局部图并修正说明竖排。全部合成数据、无产品后端/真实权限或客户可用性研究。证据docs/v1.0-development/ux/；独立复核与当前PR/CI另核对。

## 2026-10-28 PH134黄金数据与旧库准备

两家合成企业/四职责/三模板，18UAT独立基线、分币预期/来源/拒绝与137逐条专项已建立；99 schema正例/8拒绝、92 typed来源及附件/CSV哈希算术校核实际通过。工资公司确认与项目分配分开，现金核销不再造现金，期初单列。两次从freeze119a049空库迁移/旧服务造账，各4原行成本2+2+2+52=58分、9审计/9事件，完整性通过、业务字节相同、已有输出拒绝且DB未变。仅冻结旧构建与规格样本；新迁移、18产品UAT、并发和目标环境未验证。详见fixtures/README及结果JSON，独立复核/当前PR/CI另核对。

## 2026-10-28 PH135准备验证候选

准备测试与独立workflow已形成：严格schema/样本及篡改拒绝、PG公司锁NOWAIT/释放/异公司行、跨连接回滚与现有复合unique、冻结旧库两次重建。原生候选运行已安全取消，主控接管修正，不计native完成。实际既有Verifier无认证禁网快照固定6项PASS；449项438通过/11环境跳过，含4PG和3schema未执行。完整性仅全新空开发库通过。源码独立审查通过；真实PG/schema/legacy专用CI与最终PR仍待验证，PH136尚proposed，不开始业务实现。

PH135资格补记：PR75提交8053697本轮全部原8required通过；准备CI40623378734 schema5/PG定向9均无skip，2schema+4PG标志实读确认，legacy2build58分/拒绝覆盖实际通过。第二最终本机Verifier ba07ceea...6项+migrate通过，449项97.640s11跳过不作PG证明。当前证据/状态收口提交还须重新全11checks及正常merge。135done/136ready仅在合入后可执行，不代表产品业务、目标环境或客户验收。

## 2026-10-28 PH136开发容量计划候选

静态计划覆盖62原包/137需求引用、55实施资格交付包与剩余PH137规划；按执行/验证/独立复核/整合分别估算，两执行者+复核+root+CI保守资源表实际校核不重叠/前序完成，合件后再验证/审阅。区间无真实产品速度样本，外部门/额度等待未知，不承诺日期；不是运行调度器或ready许可。原生Claude484秒多次计算命令被拒后安全取消无产物，root接管，待独立审查/固定检查与当前CI。业务未编码，137尚未放行。

## PH137 · 1.0正式开发契约（2026-10-29候选）

PH130–136准备输入承接为62包/73正式薄片、137需求唯一主责及支持证据关系；新增PH139–207，复用001/002/003/026。仅首个P0安全兼容PH139在本规划正常合入后ready；余实现proposed、026blocked。旧006–020、AI/资源/发行门与120–122契约保留。

字段设计已补齐现金草稿/匹配/更正、工资现金分类、手续费成本复用及净额非现金结清、发票同原始来源额度、手工现金计划、分阶段项目结项和核对刷新。schema结构检查不是服务实现。产品代码、账本、依赖和迁移未改；正式合同/收入/应收应付等1.0业务仍待实现。历史本文“PH026ready”属于当时快照，以当前tasks blocked为准。

实际本机设计/治理结果及独立复核见PH137报告。当前PR/全部11CI与合入证据须实时核对；本次无新增浏览器、目标Linux/TLS/非空附件恢复或客户签收证据，不能据此宣称1.0已交付。

## PH139 · 首个正式安全薄片（2026-10-29候选）
固定DRF3.18.2、新增13真实API安全兼容反例；API/账本/迁移口径不变。本机462回归455执行通过/7环境跳过(4PG/3schema)，独立schema环境5项全部执行。旧依赖独立两构建58分/原SHA，演练库1企业47账本完整性通过。真实PG/当前CI须核对本PR；1.0财务新模块尚待实现。正常合入后001从冻结规格开始SDK1，无生产发布或客户验收。

## PH001 · 最小SDK1（2026-10-29候选）
实现严格六字段domain_metadata校验与不可变领域注册快照、默认停用零模型示例，保留八字段manifest/旧Registry/叶插件/URL/账本。catalog与部署上下文缺失明确拒绝，受影响的坏依赖/冲突项不进入可用快照，无关项保留；不动态导入请求元数据。
固定工具环境36专项全执行；干净产品环境35执行1补充schema-parity跳过。最终干净全量485中477执行8环境跳过；前候选固定工具全量480执行4PG跳过。演练库47账本完整性通过。真实PG/当次CI待当前PR，不把SDK1当统一门控、迁移扫描或1.0财务功能已实现。
PH139正常合入PR79后实时查询5项原安全告警均已关闭；该事实与依赖升级前旧记录分别保留。下一片002须本PR正常合入后再执行。

PR81审阅补记：旧manifest允许的OrderedDict/list子类也已递归冻结；新增反例及独立复核通过，未放宽旧接纳规则。

## PH138 · 风险契约修正（2026-10-29候选）

PH005旧A风险与tasks.json关键路径要求S冲突，PR82被正确拦住未合入。本规划只升风险，不扩业务/文件/验收、不降门禁。208为后继纯规划槽，依赖138完成且本规划正常合入后生效。005原型已验证的静态范围与后台/客户未验证继续分开；002候选修复中，未合入，不声称1.0业务交付。

## PH002 · 统一入口候选（2026-10-29）

代码实现受信快照/静态命名空间入口、实时成员/显式项目及启用版本依赖门控、公司锁内重查与事务回滚；默认不挂新业务。HTTP写POST/CSRF，历史及具名撤销保留，旧hook有公司启用门控。access不替代原owner服务的业务授权；HTTP项目payload仍由owner解析。
固定工具52专项51执行/1PGskip，完整501中496执行/5PGskip；独立接线复核通过。现有演练库1企业47账本完整性通过。真实PG和当前全部CI/合入另核；不声称新财务模块、浏览器或客户交付。PH140仍proposed，需独立规划晋级。

PH005正式状态补记：PR82最终19a4996当前全部11检查成功并正常合入72ee245，四角色工作台信息契约与离线线框完成；实际浏览器两视口/八宽与SHA证据有效。它仍不是可用后台或客户验收。PH138 PR84全11成功合入6b4653b，风险修正已生效。

## PH208 · SDK3独立开工规划（2026-10-29候选）

PH002 PR85当前11检查通过并正常合入ff7aebd；统一受信入口完成的范围以002报告为准。新规划只补140实际迁移图/原子候选与只读旧库指纹规格，保留原白名单及旧账。独立静态可实施性复核与131check/19治理通过；140ready/209规划接续仅当前CI/正常合入后生效，尚未实现或执行SDK3/旧库升级，不能当客户交付。

## PH140 · 迁移验证与完整候选（2026-10-29候选）

真实AppConfig/有效迁移图只读扫描与checked工厂已实现，错误不发布半对象。没有新模型/DDL/默认业务入口。61专项和独立复核通过，全量510中505执行5PGskip；实际旧58分库当前migrate/扫描/重复migrate保持22表39PK与业务审计指纹，两次无待执行DDL，仅证明本片兼容重放。当前CI/PG及正常合入另核，非1.0财务/客户/生产验收。141因DoR缺口保持proposed，不自动晋级。

## PH209 · 核算契约前置规划（2026-10-29候选）

PH140最终3224dd2全部11CI成功、正常合入46f9cb0。141因独立DoR缺口保持proposed，增加210前置但保留140。此规划仅登记210最小单一冲销引用/原现金防重契约任务与211纯规划接续，不改canonical或业务；候选ready须本规划正常合入后生效。现金更正继续严格fullreverse+完整replacement、bank_effect永久防重，未改财务口径。

## 产品负责人业务优先调整（2026-10-29）

停止基础设施修整，PH210未合入代码保留本地5abecc4不继续。最小规划PH212直接做客户/供应商/联系人/项目合同草稿真实页面与存储，复用既有资产及146/150相同owner，不改原依赖、不旁路记账。候选ready须本规划正常合入后执行，尚未实现；此后优先业务而非基础设施。

## PH212 · 客户与合同草稿业务（2026-10-29候选）

真实客户/供应商单行双角色、联系人、现有项目合同草稿的浏览器登记/编辑/查询已实现；统一contracts owner、规范化去重、版本/请求防重、角色/公司/项目/模块门控、原审计Outbox同事务。新App0001仅加三表，原客户文本/历史账不动，不形成收入、成本或现金。

实际全量525中515执行通过/10PG跳过、15专项中10执行通过/5PG跳过、19治理；克隆旧58分库实际新迁移和重复迁移，22旧表39PK及业务审计指纹字节不变。真实Chromium1440/390与八宽、三对象创建编辑/同源改名/键盘/错误焦点/角色通过，独立S源码与专项复核通过。当前全CI和PG须正常合入前核对。7条既有SDK路由命名警告保留，不扩基础设施。仅草稿业务，不冒领完整合同、核算、1.0交付或生产客户验收。

## PH213 · 三模板合同首次批准接续规划

PH212 PR96最终9e3da67全部11当前检查及CodeQL汇总成功，0讨论、正常合入9dcfc825；实际PG17.11全525/517执行通过8非PG跳过60.073s，新5项真实公司锁/唯一性测试与新0001实际执行。

本规划只冻结214客户可完成的三模板配置、版本冻结、独立批准和首次生效；同contracts唯一事实/规则owner、真实内部审批证据与一次canonical事件，不进行收入/成本/现金确认。原150等完整依赖/proposed保留，旧项目金额明确立项预估与正式派生合计，不拷贝同步。214/215候选ready仅本规划正常合入后生效，尚未实现；不恢复210/基础设施修整，无发布/生产客户验收。

PH215范围补充候选：214合同业务代码已形成本地未合入WIP a9061e4，28专项20执行通过/8PG跳过。实际schema验收发现两份冻结摘要文件不在214白名单，未越界修改。本规划只补两文件四个既有哈希值同步权限和215前置，不改业务样例/财务预期/验证器/CI；214未done，继续直接合同业务验收，不展开基础设施。

## PH214 · 三模板合同首次内部批准（候选）

固定节点、确认工时、固定服务期三模板真实配置→提交冻结→另一负责人批准→首次生效已实现。价款内付款/质保、不可变版本及唯一Selection规则源、审计与一次canonicalOutbox；批准不形成收入/成本/应收/现金。项目页正式价税从同源明细派生并精确链接版本，历史立项预估不回填。

实际全量544/531执行通过13PG跳过、专项34/26通过8PG跳过，严格schema/既有19正37反/99正8反通过；独立S源码复核通过。真实三模板浏览器办理、五角色、1440/390与八宽及root读图通过；旧58分及已有212记录实际迁移/重复保持，详见report。当前CI/8项PG竞争/正常合入仍待最终head核对；非完整1.0或生产/客户验收。PH215仅必要四摘要范围已PR101合入0d35763，未扩大基础设施。


## PH216 月度人工业务规划（2026-10-29）

PH214合同首次批准与生效已由PR102合入5441aab。下一业务片PH217按ADR0006和具名spec冻结月度人工确认、公司成本一次、项目/非项目/未分配分配与追溯、撤分配及终态冲销；只在本规划独立S、当前CI、正常合入后获得可信BASE许可。本PR没有产品代码或运行验收；原141/142/159/160/161/210依赖及proposed保留，218为下一规划入口，不扩基础设施、不部署。


## PH217 月度人工业务候选（2026-10-30）

基线PH216 PR106已合入f1692a6。实际候选覆盖工资首次确认、同一工时、容量、守恒分配与未分配、追溯和原分撤销，当前in_review。旧账不重算，不将现金或预算余额称为利润；累计二次确认和暂估差额仍留原任务，不冒称完整1.0。独立S源码PASS及实际本机/浏览器/旧库证据见PH217报告；当前head CI和PG仍待核，不作为合入/生产或客户验收。


## PH217 已合入与PH218收入首片规划（2026-10-30）

PH217 PR107最终f21493a全12CI成功，正常合入eea63cc；最终PG17.11实际573项：564通过9非PG跳过，7项月池多连接实际执行，旧58及43表合同非空库原行值保持。本机/浏览器/升级与PG证据分别见报告和续接检查点，原候选段落保留其当时状态。1.0未全完成，无生产或客户验收。PH218仅规划219固定节点客户验收→管理收入→原分冲销与追溯，严格单一owner，收入不重复成本现金；原148/149/166等全部依赖/proposed不变。


## PH219 固定节点验收与管理收入候选（2026-10-30）

PH218 PR110已合入0b6b926后实现固定节点客户依据登记、独立验收、按批准合同原价累计取整、管理收入、原分冲销和明确替代链。复用真实节点和Contact，收入不记成本现金，账本与批准快照保持。独立S源码PASS，本机603项577执行通过26环境跳过、浏览器/旧库升级等实际分项见PH219报告；当前in_review，PG6项及精确head CI/正常合入仍待核。无生产/客户验收，不代表完整1.0，也不放行原proposed依赖。

## PH220 业务接入测试前置补充（2026-10-30）

PH219 PR111/e592275真实CI发现旧合同PG用例默认依赖前置不再成立（新验收/收入启用），以及业务环境可选jsonschema缺失。产品依赖拒绝是正确行为。本规划仅补两并发测试class setup范围和220依赖，不改产品门禁/SDK/CI/安装依赖/原断言；219仍未done，待新可信BASE实际测试/当前CI闭环。


PH219 当前源码1ab3ad2验收收口：PH220 PR113独立S/全12CI后正常合入f98755c，才在两个原合同PG class setup隔离新业务依赖；共同Fixture与原测试体保持。新默认依赖拒绝反例、无条件来源event验证保留，仅独立schema工具按实际依赖条件执行。本机605项101.443秒579通过26环境跳过；真实PG17.11当前CI605项95.546秒595通过10跳过，6新收入PG及原合同竞态实际执行，完整性ok=true/errors=[]。源码全部12CI成功/CLEAN/0讨论，独立窄修S35项21通过14跳过。最终元数据head仍须全部CI与正常合入；无生产/客户验收，完整1.0未完成。

## PH222 服务期客户确认与归属月份管理收入（2026-10-30）

PH221 PR116 合入 2e91362 后实现固定服务期首片：真实客户依据与独立确认 + 已批准 calendar_days 分月冻结 + 按服务月管理收入 + 原分反冲 + 反确认/替代链。收入仍唯一归 management_revenue，与固定节点 Acceptance 做 DB XOR，不生成成本/现金/应收/开票；no_partial 仅单月完整原段，跨月 no_partial 明确拒绝并留待正式合同变更能力。本机固定环境全量与专项通过，旧 0.0.58 与 PH219 非空库升级后完整性 ok，浏览器与 PG 证据分别记录；生产部署、客户签收与完整 1.0 仍未完成，PH-148/149/150/154/166/167 状态不放行。

## PH223 应收结算首片规划（2026-10-30）

PH222 PR117 合入 aec8f67 后完成应收规划：登记 PH-224「客户结算、到期/质保未清与账龄（应收首片）」与 PH-225 下一规划槽，冻结 ADR-0009「条款行组合结算」——一次结算覆盖 1..n 个完整且未消费的批准条款行，金额只由条款 gross 派生，net/tax 按整行累计比例 HALF_UP，可结算门槛为真实已确认履约且不超已确认减已结算；金样例 UAT-03 50%（50000/53000）与 UAT-10 90%+10%（90000/95400+质保 10000/10600）可成立。不做现金/核销、折让/坏账、应付、发票、争议延期、AgingCase 写入与生效结算反冲。本 PR 无产品代码/迁移/PG/浏览器，PH-155/156/157/158/170/171 状态与依赖不变，不代表完整 1.0。

## PH-226 应收结算业务接入测试前置补充（2026-10-30）

按PH-225规划登记并实施：将4处既有测试/并发类setUp改为“存在settlement时先停用settlement，再停用delivery/contracts”，以适配PH-224新增的插件依赖边；不改产品、manifest、门禁或断言，不跳过测试。本BASE（不含settlement）全量645项通过；含settlement的行为由PH-224 rebase后的全量与PostgreSQL CI验证。详细证据见`project/reports/PH-226.json`与`docs/verification.md`。


## PH-224 客户结算、到期/质保未清与账龄首片（2026-10-30）

新增settlement插件与“条款行组合结算 → 到期/质保未清 → 只读账龄”闭环：一次结算覆盖1..n个完整且未消费的批准条款行，金额只由条款gross派生并按整行累计比例HALF_UP守恒；批准按当前累计重算拒绝陈旧草稿；未清项分due/retention，质保未到release_on不计逾期；账龄纯读、五桶、CSV。不做现金/核销/折让/应付/发票/争议/反冲/期初；历史读取不依赖模块启用，HTTP路由在模块停用时拒绝。见`docs/verification.md`与`project/reports/PH-224.json`。


## PH-227 现金事实首片规划（2026-10-30）

按PH-224之后的应收连续性，登记PH-228现金事实首片（银行账户、银行流水稳定去重、往来方匹配、只读投影；无现金过账/分类/核销/退款/导入/期初），作为PH-170核销与PH-171退款的前置；PH-229保留下一规划槽。PH-169/PH-170/PH-171仍为proposed，本片不冒称完成。见`docs/verification.md`与`project/reports/PH-227.json`。


## PH-228 现金事实首片（2026-10-30）

新增cash插件与“银行账户→银行流水→往来方匹配”薄片：账户草稿/激活、流水草稿稳定去重登记、identify/reverse匹配、私有只读投影；明确不写账本/会计/收入/成本/应收/应付/未清，不做过账/分类/核销/退款/更正/转账/文件导入/期初。现金账桥接、核销、退款仍为后续片。见`docs/verification.md`与`project/reports/PH-228.json`。


## PH-229 现金过账与账户覆盖规划（2026-10-30）

按PH-228之后的现金连续性，登记PH-230“现金过账与账户覆盖”（草稿→posted、规范事件、只读覆盖、过账守卫；无账本/分类/核销/退款），为PH-170核销与PH-171退款提供不可变现金来源；PH-231保留下一规划槽并优先现金分类与账本桥接。PH-169/170/171仍为proposed，本片不冒称完成。见`docs/verification.md`与`project/reports/PH-229.json`。


## PH-230 现金过账与账户覆盖（2026-10-30）

在PH-228银行事实之上补齐“草稿→posted”过账：不可变已过账现金事实、恰一条规范登记事件、只读账户覆盖估算与过账守卫；posted后不可改/删，同流水并发过账仅一胜。明确不写账本/会计/收入/成本/应收/应付/未清，不做分类/分配/核销/退款/更正/转账/导入/期初；现金分类与账本桥接仍为后续片。见`docs/verification.md`与`project/reports/PH-230.json`。


## PH-231 核销首片规划（2026-10-30）

按PH-224未清项与PH-230已过账现金的闭环，登记PH-232“核销首片：已过账现金对未清项的部分核销与撤核”（部分核销、双方余额上限、撤核反向记录、规范事件、派生余额；无Offset/退款/手续费/转账/导入/期初/账本），并保留PH-233规划槽。PH-170/171仍为proposed，本片不冒称完成。见`docs/verification.md`与`project/reports/PH-231.json`。



## PH-233 交付效率规划与规划链续接（2026-10-30）

按用户2026-10-30授权的“token降40%、时间降50%”工程改造，登记PH-235（projectctl只读`orient`/`next --compact`/`draft-report`）与PH-234（AGENTS.md开场改orient/brief、delivery-status拆现状头+归档、ai-delivery-*四篇归档、work-packages.json裸读护栏），并保留PH-236规划槽。量化基线：新会话必读约125KB（冷启动约40K token）、全量套件94s/次、每任务本地全量2-3次。PH-233原业务候选顺延至PH-236，本片未冻结任何业务契约。见`docs/verification.md`与`project/reports/PH-233.json`。


## PH-235 projectctl会话效率命令（2026-10-30）

新增只读`orient`（单次输出manifest摘要+活动handoff正文+compact ready+AGENTS.md验收命令，缺失来源结构化报错）、`next --compact`（仅id/title/kind/risk/status/milestone/depends_on，默认输出与BASE逐字节一致）与`draft-report <id>`（报告骨架到stdout，result一律not_run、S级review pending，只读不写文件）。不改变既有命令语义、校验逻辑与CRITICAL_PATHS。见`docs/verification.md`与`project/reports/PH-235.json`。


## PH-234 会话上下文减负文档改造（2026-10-30）

delivery-status拆分落地区：现状头1996字节，原文件全文逐字入archive，新条目只追加归档。AGENTS.md开场改orient→brief/draft-report路径并禁止裸读tasks.json/delivery-status/work-packages.json全文；ai-delivery-controller/pilot/isolation/delivery.md四篇git mv入docs/engineering/archive/（内容零改动）；v1.0-development/README加work-packages.json与requirement-trace.json的按需查询护栏。遗留：ROADMAP.md与execution-protocol.md各一处旧路径链接留PH-236修复。见`docs/verification.md`与`project/reports/PH-234.json`。


## PH-237 文档收尾：失效链接修复与作用域测试指引（2026-10-30）

PH-234遗留的两处ai-delivery-*旧路径链接（ROADMAP、execution-protocol）改指archive；CONTRIBUTING加作用域测试指引（开发中模块级并行快速反馈、收尾全量一次、CI兜底），验收四命令未动。效率改造实测：新会话开场+任务上下文阅读量280,956→15,760 字节（-94.4%，1−15760/280956）。见`docs/verification.md`与`project/reports/PH-237.json`。


## PH-407 整理前现状页快照（2026-11-01）

保留原文，仅作历史追溯，不是当前任务状态；当前摘要回到 delivery-status.md。

# 交付状态：现状头

口径：本头只做能力概览；逐任务条目与完整历史快照在 [delivery-status-archive.md](delivery-status-archive.md)（append-only，不改写），新条目只进归档，本头仅在能力变化时重写。验证明细见 [verification.md](verification.md) 与 `project/reports/`；`product-requirements.md`、`accounting-rules.md` 是目标设计，多数未实现。任务状态唯一来源是 `project/tasks.json`（用 `projectctl brief <id>` 查询）。

## 当前能力（2026-10-30，依据最新main与当次CI）

- 版本：0.1.0 工程验证版已发布；main 在 v0.2 基础产品化与 1.0 现金/应收薄片迭代。
- 已实现：项目工作流（8阶段状态机、蓝图、关键任务）、成本/现金分账（工时与六类单据审批入账、费率快照、幂等token、负值冲销）、审批审计、导入导出、个人API/MCP、插件运行时与三叶插件；前端六页签与统一蓝白视觉（PH-022至025）；四CLI交付闭环演练（PH-100至108）。现金/应收薄片：现金事实、posted过账、客户结算与未清/账龄、服务期与管理收入已合入，核销首片（部分核销/撤核，PH-232）已实现；银行手续费草稿与净额/手续费对账首片（PH-244，仅未批准草稿，无成本/账本/未清写入）已实现；手续费提交/独立批准/驳回与批准时一次性公司成本过账（PH-250，成本只走 cost 账，无现金流水/未清核销写入）已实现。合同变更草稿/独立决策/生效新版本与终止（PH-265：终止保留历史并经既有只读条款接口阻断新结算；变更不写账本/现金/收入/未清）已实现。合同变更影响只读追溯（PH-275：变更前后批准版本/条款/行与已生效结算/未清/核销/管理收入只读聚合、终止后新结算/变更阻断深化；无迁移/无写入口/不重算历史）已实现。合同收费模板/生效条款只读快照与结算核对（PH-287：批准版本/模板/条款行/变更历史/终止标记只读快照 + 条款金额/结算分区/重复消费/跨项目/旧版本引用异常清单；无迁移/无写入口/不改写路径）已实现。客户可计费确认首片（PH-269：新建 time 插件，记录客户确认分钟并与已批准工时/合同条款对齐；只写确认事实与 canonical 事件，不写收入/账本/现金/未清）已实现。
- 跨域只读核对总览（PH-291：合同待审变更/终止条款、结算未清/超额核销/退款覆盖、管理收入确认与结算差额、time 可计费 pending/待审工时、导入预览缺失、现金过账净额/缺分类的统一只读看板；delivery 层只读聚合各 owner 既有事实，无迁移/无写入口/无审计、不建 insights 第二 owner；缺失上游以 null+missing 展示而非 0）已实现。
- 成员账号/项目范围与授权审计只读运营视图（PH-281：成员/角色/停用状态/项目范围/AgentToken 生命周期计数/身份审计摘要的统一只读聚合；canonical `identity-access` history_read 路由；无写/审计/迁移；pm 仅管理项目摘要、member 拒绝、脱敏不含令牌密文/密码/费率）已实现。
- 发票税额资格确认与跨 owner 编排（PH-320）：复用 PH-252 发票事实，新增 pending→confirmed 税额资格确认/取代（append-only 状态守护）与跨 owner 引用链登记（管理收入反冲/cash 退款/银行退回/结算调整，经 owner 事实存在性校验，不写其它 owner）；只读资格/编排引用链页面；无 canonical/新 owner 写/contract schema 变更。
- 多币种/汇率首片（PH-321，contracts-only）：`FxRateSnapshot` append-only 汇率事实（精确业务日、版本化、Decimal、来源、无最近汇率回退）+ `record_fx_rate`；合同首次批准/变更生效写入路径接受 `currency/original_*_cents/fx_rate_ref`，经冻结汇率一次性 ROUND_HALF_UP 换算并保存原币；无汇率拒绝非 CNY；合同行原币/记账币只读核对；无 core 迁移；跨 owner（结算/收入/现金/发票）写入与 verify_integrity FX 分支按 Lead 裁决不在本片。
- 管理收入确认与结算/合同条款只读配对（PH-284：RevenueRecognition 已确认/冲销、active Settlement/OpenItem/核销与批准条款快照只读聚合 + 确认超合同/终止后确认/结算无确认/金额与跨项目异常清单；无迁移/无写入口/零审计副作用）已实现。
- 未实现：全站界面统一与经营趋势（PH-026）、正式合同、应收应付全量、预收预付/退款、发票、多币种、共享分摊、月度人工池、期初、通知中心；core 未完全插件化。

## 验证边界

CI 已覆盖 SQLite/PG 回归、迁移、append-only、MCP、容器恢复、CodeQL、治理门禁；未验证真实TLS、锁账PG并发、非空附件恢复后检查、真实模型与个人客户端联调。CI通过不等于生产验收。

## 交付效率改造（2026-10-30 起）

会话开场 `projectctl orient`、任务上下文 `brief <id>`、报告起草 `draft-report <id>`；本文件、`project/tasks.json`、`docs/v1.0-development/work-packages.json` 禁止整文件读入。量化基线与复测口径见归档 PH-233 条目。

## PH-240 退款与银行退回（2026-10-30）

在PH-230已过账现金与PH-232核销覆盖之上实现“原现金覆盖内真实退款/银行退回”：可退额=posted gross−active核销−active退款−active退回（服务端派生、非负），退款生成反方向posted支出与Refund事实+`cash.refunded.v1`，银行退回按原方向取反生成新现金与BankReturn+`cash.returned.v1`，幂等收据与期间/锁期校验；新现金沿用PH-230过账事件/收据不变式。覆盖对称化：settlement新增只读clearance_coverage_for_cash，核销剩余与现金候选可用额扣除退款/退回，保证cleared+refunded+returned≤gross（Lead裁定A；PH-232 spec文字未同步，PH-247登记小修）。明确不写账本/收入/成本/应收应付/未清，不做Offset/手续费/转账/更正/导入/期初；PH-171不解除。见`docs/verification.md`与`project/reports/PH-240.json`。

## PH-239 现金分类与账本桥接首片（2026-10-30）

在PH-230已过账现金之上实现分类到现金账的闭环：posted现金分类为receipt/payment/advance/tax（payroll命令层拒绝），每流水至多一条有效分类、撤分类append-only且可重分类；每条有效分类经core具名桥接恰写一个`cash.classification` AccountingEvent与一条cash_in/cash_out、funding_account scope的LedgerPosting，撤分类追加镜像事件/负值posting（原记录不改不删），bank_effect claim每流水唯一永久、撤后重分类净额复用；canonical recorded/reversed事件posting_event_refs恰1个core/accounting_event（PH-243冻结）；core放宽`labor_posting_cost_scope`（core 0009）。cash迁移因PH-240先合入顺延为0005。verify_integrity七项现金账不变式（与PH-240退款provenance守卫合并保留）。独立S复核round 2 PASS（3阻断消除）。明确不做工资分类/项目分配/Offset/退款/手续费/互转/更正/导入/期初，不解除PH-169/170/171 proposed。见`docs/verification.md`与`project/reports/PH-239.json`。

## PH-241 预收/预付抵扣 Offset 首片（2026-10-31）

在PH-239 advance分类与PH-232未清核销之上实现预收抵扣闭环：owner/finance选择同企业/同往来方、已过账且有效advance分类的客户预收现金，部分抵扣active应收OpenItem；append-only Offset事实（settlement 0005），支持同一对多次部分抵扣，余额由服务端按advance可用额与OpenItem剩余额派生；无Offset撤销、无canonical事件、无账本/会计事件/收入/成本/应收应付写。按Lead裁决方案a修正PH-232服务行为：advance可用扣active cleared+offsets、核销候选/上限排除advance分类现金、同一现金不可同时clearance+offset；verify_integrity新增offset分支且不加全局禁令。未验证：PG并发（CI）、浏览器1440/390、独立复核、生产/UAT；PH-232 spec文字待PH-249同步。见`docs/verification.md`与`project/reports/PH-241.json`。

## PH-245 内部账户互转首片（2026-10-31）

在PH-239分类桥接之上实现内部账户互转：同企业、反方向、同额、同日的两条已过账现金成对关联为append-only InternalTransfer（cash 0006），经PH-239非路由内部服务为两腿各写一条transfer分类与cash_out/cash_in posting（funding_account scope），公司净现金恒为零；撤转append-only追加两条撤分类+冲销posting、InternalTransferReversal与canonical reversed事件；Unique(out/in_cash_transaction)无条件终身一次；两腿不同request_token；服务层先owner/finance fresh校验；canonical linked/reversed的posting_event_refs为0/0、changed_refs恰3、classification_reversal_refs=2；verify_integrity新增internal_transfer分支（批量、无全局禁令）。无新增canonical topic、无公开transfer通道；不做手续费/Offset/退款/更正/导入/期初，不写成本/收入/应收/应付/未清。未验证：PG并发（CI）、浏览器1440/390、独立复核、生产/UAT。见`docs/verification.md`与`project/reports/PH-245.json`。

## PH-264 成员账号生命周期与项目范围首片（2026-10-31）

实现成员账号最小生命周期闭环：owner 变更公司内成员角色（owner/pm/finance/member/auditor）、以 ProjectMember 差集维护可见项目范围、停用/恢复成员；停用要求先处理在职员工绑定与有效项目经理交接，同事务撤销全部未撤销 AgentToken、清空项目范围并写审计；最后 active owner 不可降级/停用、不可自停用、停用成员不可再改（除恢复）；公司级角色不接受项目范围；Membership.version 乐观版本（core 0010）+ generic audit identity.membership.changed（无新 canonical topic/无新 business 表）；同 token 重放、dry_run 零副作用；finance/auditor 只读全部成员，pm/member 仅读自己；settings 页面 owner 表单承载。未验证：浏览器1440/390、独立复核、生产/UAT。见`docs/verification.md`与`project/reports/PH-264.json`。

## PH-274 onboarding 导入预览实现前置首片（2026-10-31）

新建 pmo_plugins.onboarding 插件（ADR 0012，manifest requires={contracts,settlement,cash}）并落地只读导入预览：CSV 模板识别（time/expense/plan）、字段映射与未映射列、200 行内预览、逐行格式/主数据预检与内存态 ephemeral 冲突投影；零业务事实/账本/来源消费/规范事件（仅一条 generic audit），无表无迁移、无提交/期初/冲突持久化。权限 owner/finance/auditor 可读（auditor 经 history_read GET 只读预览，文件上传走 business_write），pm/member 拒绝、模块与依赖停用拒绝；7 个既有禁用 loop 测试按 PH-282 白名单先停用 onboarding。未验证：浏览器 1440/390、独立复核、PG、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-274.json`。
## PH-279 现金链路只读核对快照首片（2026-10-31）

新增现金只读核对：posted 现金 ↔ 有效分类 ↔ 账本事件/posting/bank_effect claim ↔ 退款/退回 ↔ 内部互转成对，输出逐流水快照、账户现金账净额（含冲销，非银行余额）与 issue_code 异常清单；offset_implemented=false 占位（PH-241 未合入）。owner/finance/auditor 只读，pm/member 拒绝；无迁移/无写入口/无审计写/不写账本事实。偏差：pm 因现金事实无项目归属而拒绝；两条路由 history_read，cash 停用后历史 GET 仍 200 可读；无审计写。未验证：浏览器 1440/390、独立复核、PG、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-279.json`。

## PH-288 银行账户/现金余额只读对账汇总首片（2026-10-31）

新增银行账户只读对账：按账户/期间派生 posted 现金 in/out 与净额、退款/退回、内部互转 in/out 与抵销、分类桥接现金账净额、期初/期末余额（closing=opening+posted_in-posted_out，不建余额表）与 issue_code 异常清单（无 posted 事实/未分类现金/账本净额不符/跨账户跨 org/同账户互转/负余额/关期后 future 事实）；offset_implemented=false 占位（PH-241 未合入）。owner/finance/auditor 只读，pm/member 拒绝（无项目归属）；两条 history_read 路由，cash 停用后历史 GET 仍 200；无迁移/写入口/审计写。偏差：pm 拒绝、Offset 未实现、余额含退款/互转实际现金腿、反例 append-only 兼容。未验证：浏览器 1440/390、独立复核、PG、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-288.json`。

## PH-304 运营核对底稿授权只读 CSV 导出首片（2026-10-31）

新增授权只读 CSV 导出：组合 PH-291 跨域总览、PH-283 结算覆盖、PH-279 现金桥接、PH-280 手续费配对四类只读数据集，输出 UTF-8+BOM CSV（表头/refs/as_of/生成时间/行数/#checksum sha256），dry_run 计划零文件、request_token 进程内幂等；canonical domain_metadata 声明 `ops-export/` history_read、urls 仅导入，无 POST 写入口。owner/finance/auditor 全量，pm 仅有项目归属的 3 类，member 拒绝；无迁移/写/审计/零事实变化。偏差：receipt 进程内非持久、pm cash_bridge 拒绝、单路由页面+下载、仅组合既有只读服务。未验证：浏览器 1440/390、独立复核、PG、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-304.json`。

## PH-309 运营核对工作台聚合首片（2026-10-31）

新增只读运营核对工作台：聚合 PH-291 跨域总览、PH-283 结算覆盖、PH-279 现金桥接、PH-280 手续费配对、PH-281 成员/授权五个分区的 issue 计数、关键合计、missing 与下钻链接；缺失 null+missing 不显示 0，不重算口径、不建第二 owner。canonical domain_metadata 声明 `ops-workbench/` history_read、urls 仅导入；owner/finance/auditor 全量、pm 管理项目摘要（cash_bridge 无项目归属不可用）、member 拒绝；无迁移/写/审计。偏差：五分区按 spec、合同/收入/银行余额经 PH-291 间接聚合、pm cash 分区不可用、missing 语义。未验证：浏览器 1440/390、独立复核、PG、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-309.json`。

## PH-316 运营核对工作台导出联动首片（2026-10-31）

工作台分区联动 PH-304 底稿导出：ops_overview/settlement_coverage/cash_bridge/fee_pairing 分区带 dataset/as_of/period 导出链接，复用既有 ops-export/ 服务与 history_read 路由；identity_access 无导出（PH-304 数据集不含身份敏感明细，export_dataset_not_implemented），pm cash_bridge 不可导出（无项目归属）。无新写通道/元数据副本/迁移/表/审计，零写、事实计数不变。未验证：浏览器 1440/390、独立复核、PG、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-316.json`。

## PH-252 发票实现首片（invoicing 插件）（2026-10-31）

新建 invoicing 插件：Invoice/InvoiceLine/InvoiceAllocation/InvoiceReversal/TaxQualification + 自有迁移 0001 + vendor-aware append-only 触发器；record/allocate/reverse 服务（公司锁、request_token 幂等、期间/锁期、整数分价税分离、来源额度只读派生、部分/全额红冲、append-only 负值冲销、红票≠退款），不写收入/现金/账本/未清；浏览器表单/列表/详情、verify_integrity 发票分支、generic audit + canonical events。插件默认停用（opt-in，避免反向依赖阻断模块停用）；分配首片仅 sales 合同明细/结算未清项/结算。未验证：浏览器 1440/390、独立复核、PG 触发器/并发、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-252.json`。

## PH-322 关期/结项实现首片（2026-10-31）

delivery append-only run：PeriodCloseRun/ProjectClose + 迁移 0005（vendor-aware append-only 守护）；close/reopen period/project 命令（公司锁、expected_version、DeliveryReceipt 幂等、dry_run 零写、closing+closed 成对、plugin. 审计，仅更新既有期间/项目当前行，不写业务事实）；只读预检复用既有 owner 接口（issue_code/reminder_ref/precheck_digest/missing）；close 页面/执行/重开表单；verify_integrity 五类 close 分支。偏差：PH-336 已补 models/migrations/verify_integrity/test 白名单；query-count 阈值按实测 84→88（4 条固定 bulk 查询）。未验证：reviewer-b 复审、PG trigger/并发、浏览器 1440/390、生产/UAT。见 `docs/verification.md` 与 `project/reports/PH-322.json`。
- 分摊/预算 ETC 实现首片（PH-330）：costing 0005/0006 落地共享费用分摊批次（净零重归属、末项吸收、append-only）与项目预算版本/四眼批准/budget_residual_v1 ETC 只读派生；只读核对页 + verifier 分支；无 canonical/schema/core migration/其它 owner 写。
- 批次 C 规划同步（PH-356）：PH-330 done 后晋级 PH-347/348/349 演练执行；PH-350 与 PH-333/334/335 保持 proposed 并刷新 blockers；登记 PH-357 证据回填、PH-358 发票批量导入、PH-359 insights、PH-360 通知后续与 PH-361 下一规划入口。
- 跨 owner 编排执行首片（PH-370）：复用 PH-320 引用链，按管理收入反冲/cash 退款/银行退回/settlement 核销调用具名 owner 服务；invoicing 只记录事实/引用，收据幂等、失败零链接，不声称单事务；只读执行页。
- 批次 C/D 规划同步（PH-376）：PH-357 done 后晋级 PH-367 汇总验收前置契约；PH-372 等 PH-367；登记 PH-377 发布资格后续、PH-378 发票批量导入、PH-379 insights、PH-380 通知收件箱、PH-381 下一规划入口（依赖 PH-376）。
- 批次 D 规划同步（PH-381）：登记 PH-382 D-01 编排验收执行（ready，deps PH-370 done）、PH-383 发票批量导入错误清单、PH-384 insights 性能/导出、PH-385 通知调度/已读后续（均 proposed）、PH-386 下一规划入口（等 PH-381 done）；PH-372/377 等 PH-367，其余批次 D 链保持精确 blockers。
- 任务板卫生（PH-386）：PH-329/336/341/367/371 已合并 → done；PH-318/326 多币种旧片与 PH-006/009/011/014 legacy 重复片 → cancelled（证据/范围转 PH-388 或通知链）；PH-372 晋级 ready；登记 PH-387 D-02 验收（ready）、PH-388 多币种反查、PH-389/390 D 批验收、PH-391 下一规划入口。
- PH-388 多币种汇率反查/对账：新增精确日期历史反查、fx ref 业务日校验、合同/结算/发票原币↔记账币对账（发票 CNY-only 显式 missing）；复用现有 FX 页面/路由，无 core 迁移；scoped 10 tests OK，L3 CI/PG、L4 浏览器、L5 生产 not_run。
## PH-396 v1.0 遗留对账（2026-10-31，BASE 7748ea1）
- 对账表/交付清单/发布阻塞/收尾动作见 `docs/v1.0-development/closure-reconciliation.md`；release_ready=false（L3/L4/L5、三方签批未完成）。

## PH-398 测试跨日恢复（2026-11-01）
- 仅将服务期并发测试完整月份夹具改为已结束自然月，保留业务未来日期拒绝和全部并发断言；scoped 34 tests OK，独立复核 PASS，最终 11 项业务/安全检查全绿并已合入 `032fdeb`。

## PH-120 可信 PR 交付（rolling，2026-10-31）
- BASE policy/离线元数据 gate 与真实合并入口已接线；S 授权绑定任务/spec 摘要，合并检查 mergeable/test-merge/远端保护，新增官方 Codex/Actions 仲裁读取路径。作用域 71 tests OK（含 workflow 反伪造）；全量仍仅既有 8 项 macOS 失败。`remote_checks_enforced=false`，远端强制与真实 Codex 对抗由 PH-122 验证，不能视为已启用。另登记 ready 的 PH-397 仅作后续 1.0 规划入口，不恢复 EXP-1、不发布 PH-110 设计参考。

## PH-397 v1.0 功能交付重启（2026-11-01）
- 收口 PH-241/120；晋级 PH-121 与 PH-354。PH-354 先提供唯一期间预检/digest，PH-353 待其后实现原子导入；PH-342/343/355 保持 proposed。登记 PH-399 为下一规划槽，另按真实 CI 证据登记 PH-400 修复仲裁 mergeable 兼容。仅规划，不宣称功能或发布完成。

## PH-400 仲裁 mergeable 兼容修复（2026-11-01）
- Delivery arbitration 改用当前 PR REST API 刷新可空 mergeable，并交叉核对 webhook BASE/HEAD；持续未知仍拒绝。登记 73 tests OK，独立复核 PASS，live PR #264 仲裁已通过（run 41269596652）。

## PH-399 规划修正（2026-11-01）
- PH-354 新增 11 个经全量回归发现的依赖停用测试白名单，仅允许最小顺序修正；PH-400 收口 done，登记 PH-401 下一规划槽。无产品实现或发布宣称。

## PH-121 覆盖率与发布边界（2026-11-01）
- CI 覆盖率门槛（实测 82.09%，底线 80%）、新增行 80%、未知 skip 阻断；发布改为人工 dispatch+受保护 environment，标签只校验不创建；新增 PG 公司锁/审计链并发反例。收口修复非 .py 变更误报，质量门 28 tests OK。真实 Linux CI/PG、浏览器与生产仍 not_run。

## PH-354 insights 期间预检只读 API 首片（done，2026-11-01）

新建 pmo_plugins.insights（manifest 8 字段/requires 四项/default_enabled=True，无模型/迁移/写通道），period_precheck_snapshot 按 period-precheck-contract 输出 envelope 与稳定 precheck_digest；复用 cash/settlement/management_revenue/costing 只读服务与 PH-291 总览，不复制金额/差额规则，零写；owner/finance/auditor 全量、pm 仅管理项目摘要、member/跨 org 拒绝、停用 history_read；project scope 按项目过滤或显式 missing，跨月 period 拒绝。新测试 25 OK，29 个白名单既有文件最小停用顺序通过，全量回到既有 macOS 基线；check/makemigrations/verify_integrity 通过。偏差：missing_code 扩展、future_as_of 口径待复核仲裁；未验证 PG/CI、浏览器 1440/390、生产/UAT、PH-353 联动；review PASS（两轮修复 project scope/跨月 period/offset 重复/角色 cash 隔离）。见 `docs/verification.md` 与 `project/reports/PH-354.json`。

## PH-401 规划推进（2026-11-01）
- PH-354 合入后晋级 PH-353（原子导入，复用真实 precheck/digest）与 PH-122（远端强制，补齐 8→9 checks 实现白名单）；登记 PH-403 下一规划槽。仅规划，无功能/发布宣称。

## PH-122 远端强制门禁启用（2026-11-01）
- required checks 8→9（Delivery arbitration/app 15368），策略/门禁校验 9/13；strict/admin/线性/讨论/PR/禁强推禁删除保持不变。负例 PR #275/#276 被拒绝；失败 CI/落后 BASE/伪造复核/未授权高风险均有阻断证据。单一维护者 approvals=0、release 环境未配置，均如实记录。

## PH-353 onboarding 导入提交原子首片（done，2026-11-01）

新增 onboarding 首个迁移与 ImportPreview/ImportConflict/ImportSubmissionReceipt；预览持久化规范化行摘要与冲突，提交在公司锁+单事务内复用 PH-354 期间预检重算 digest/as_of，校验权限/期间/未消解 blocker 冲突/request_token 幂等/稳定 import_key，dry_run 零写，非 dry_run 仅经既有 owner 服务 `core.imports.apply_rows` 写入且任一行失败整批回滚，成功后预览置 consumed。页面为 owner/finance 写、auditor 历史只读、pm/member 拒绝、停用 history_read。新测试 33 OK（26 服务 + 7 HTTP），受影响 40 OK；全量 1413 项仅既有 8 项 macOS 基线失败；check/makemigrations/verify_integrity 通过。独立 S 复核 PASS（修复跨期行未绑定 period）；PR #278 合入 5ca8d30；PG 并发、浏览器 1440/390、生产/UAT not_run。见 `docs/verification.md` 与 `project/reports/PH-353.json`。

## PH-403 规划推进（2026-11-01）
- PH-353 合入后晋级 PH-342（发票批量导入契约）与 PH-343（insights 契约）ready；登记 PH-404 proposed 作为下一规划槽。仅规划，无产品实现/发布宣称。

## PH-342 发票批量导入契约（done，2026-11-01）
- 新增 `docs/v1.0-development/invoice-import-contract.md`：冻结批量模板/字段映射、逐行校验与 issue_code、全批原子/幂等/公司锁/期间、权限与 PH-252/274/353/320 复用边界；docs-only，无产品代码/迁移，PH-338 仍 proposed。

## PH-343 insights 契约（done，2026-11-01）
- 新增 `docs/v1.0-development/insights-read-model-contract.md`：冻结只读投影、期间预检复用、差异单/项目工作台、as_of/period、missing/null、权限与内部读模型边界；docs-only，无产品代码/迁移，PH-339 仍 proposed。

## PH-404 规划推进（2026-11-01）
- PH-342/343 契约合入后晋级 PH-338 发票批量导入实现与 PH-339 insights 实现；PH-338 白名单按 PH-342 唯一路径扩展 core/imports.py 与 onboarding preview/commit；登记 PH-405 proposed。仅规划，无产品实现/发布宣称。

## PH-404 收口与 PH-339 迁移白名单（2026-11-01）
- PH-404 done；补 PH-339 `pmo_plugins/insights/migrations/__init__.py` 白名单（Django 5.2 迁移包必需），避免 check 因缺包失败。

## PH-338 销售发票批量导入首片（2026-11-01，BASE 7bb8351）

按 PH-342 冻结的唯一路径扩展导入编排：`core/imports.py` 新增 `HEADERS['invoicing']` 与 `apply_rows` 的 invoicing 分支（按 invoice_number+line_no 归组，冻结 uuid5 owner token，只经 `record_invoice`/`allocate_invoice_lines` 写发票事实，通用 `commit_rows` 拒绝绕过预览）；onboarding `preview_services` 增加发票模板精确识别与逐行/逐票预检（往来方 active/customer 唯一匹配、日期/金额/行票合计/重复票号/来源额度与往来方一致性），`import_commit_services` 增加 `invoice_date` 期间映射、kind=invoicing 行校验与 owner 异常到 issue_code 映射；invoicing 侧新增只读模板/预览/批次收据投影、history_read 页面与 ui-tokens 模板，提交仍只走 onboarding `commit_import`。未新增 canonical 实体/topic/迁移，不写收入/现金/账本/来源消费。L3/L4/L5（PG 并发、浏览器、staging/生产/UAT）not_run；独立 S 复核 pending（已完成）。

## PH-339 insights 读模型实现首片（in_review，2026-11-01，BASE 7bb8351）

- 新增 insights 自有模型/迁移、刷新/状态更新/项目工作台服务与差异单/工作台页面；期间预检直接复用 PH-354。差异单由预检 issue 派生，同 case_key 至多一条有效、证据变化旧单 superseded、刷新/更新 request_token 幂等；工作台经 contracts/management_revenue/costing/settlement 既有只读接口派生，缺失 null+missing、零业务写入；owner/finance/auditor 全量、pm 仅管理项目、member/跨 org 拒绝、停用 history_read、写入口仅 owner/finance。
- 交付工作树（PH-404 已补 `migrations/__init__.py` 白名单）新测试 68 OK（43 差异单/工作台/HTTP + 25 期间预检回归）；check/makemigrations/verify_integrity 通过。差异单 `difference_cents` 为非空 canonical 整数、`snapshot_watermark` 为 canonical SourceRef 数组、case/read-model append-only 触发器已落库；较早 as_of 不可覆盖较新快照。未验证 PG/CI、浏览器 1440/390、生产/UAT、独立 S review。

## PH-405 规划推进（2026-11-01）

- PH-338（PR #288，`088d5f0`）与 PH-339（PR #290，`3400774`）已合入并收口 done。
- PH-378/PH-379 cancelled：实现范围已分别被 PH-338/PH-339 覆盖；PH-383/PH-384 依赖改接 PH-338/PH-339 并保持 proposed，待下一规划槽按剩余缺口复核。
- PH-355 通知中心前置拆分晋级 ready（deps PH-364 done），spec 依赖描述与任务板对齐；PH-340/PH-380 保持 proposed 等 PH-355。
- 登记 PH-406 proposed（Issue #292）作为下一规划槽，PH-405 done 后置 ready。
- 真实环境 L3–L5、发布包与三方签批保持 not_run；EXP-1 暂停、PH-110 不入库。
