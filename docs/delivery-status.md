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
