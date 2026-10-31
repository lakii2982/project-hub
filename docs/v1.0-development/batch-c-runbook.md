# 批次 C 验收执行手册（M01–M15 / V1-OPS-01–06）

- 产出任务：PH-352（docs-only；不实现脚本、不部署、不执行真实客户数据）。
- 基线：见 PH-352 report 的 BASE/HEAD。**本文件只定义执行方法与证据模板，不代表任何项已通过。**
- 对齐：`docs/v1.0-development/batch-c-acceptance.md`（PH-332 验收矩阵）、`docs/engineering/ops-runbook.md`（PH-345 脚本契约：evidence envelope、退出码、角色与失败处置）。
- 状态约定：`not_run` = 有方法但本片未执行；`blocked` = 上游实现/环境尚未交付；`skipped` 须写明原因；**禁止把配置/文档/演练写成生产通过**。

## 1. 执行原则（与 PH-345 一致）

1. **五层证据**：L1 代码存在 / L2 自动化通过 / L3 CI（含 PG）/ L4 浏览器 / L5 生产或试点。低层不得冒充高层；截图、CI URL、PG 日志、命令实际输出才算证据。
2. **证据 envelope**：所有 V1-OPS 脚本输出按 PH-345 §1.2 JSON envelope 写入 `--out`；M 项命令输出按第 6 节模板追加到 `docs/verification.md` 并在 report 引用。
3. **退出码**：0 通过；2 前置缺失（not_run）；3 断言失败（fail，停止后续破坏性步骤）；4 环境/连接异常（fail，重试一次后升级支持）；5 安全检查拒绝（立即停止）。完整定义见 PH-345 §1.3。
4. **幂等与去重**：同一 profile+app_sha+script 的 evidence 可复用；SHA/环境变化必须重跑；dry_run/检查可重复，execute 必须幂等/可恢复。
5. **双人复核**：execute/destructive 由发布负责人执行、DBA/安全负责人复核；财务口径由财务复核人确认。
6. **脱敏**：证据不含密码/令牌/薪酬/真实客户原文；敏感信息以 `redactions` 字段声明；不把生产数据写入源码包。
7. **失败处置**：保留日志/快照/哈希，按 `upgrade-recovery.md` 回退/恢复；不自动“修数据”；不静默重试破坏性步骤。

## 2. 角色与职责（占位，发布前填具体人员）

| 角色 | 职责 |
| --- | --- |
| 发布负责人 | 执行安装/升级/发布；确认证据与回退点；对外发布说明 |
| DBA | 备份/恢复/迁移/PG 并发；RPO/RTO 实测；隔离恢复环境 |
| 财务复核人 | 金额/期间/税额/对账口径核对；财务类 M 项验收 |
| 支持负责人 | 健康/积压/容量/客户支持与退出 |
| 安全负责人 | TLS/密钥/权限/发布包与脱敏复核 |
| 验收复核人 | 独立复核证据与 not_run 声明，签发批次 C 退出结论 |

## 3. 执行协议（每项通用）

1. **准备**：确认 profile（staging/rehearsal/production）、app_sha、DB 版本/连接数、备份位置、数据量与脱敏声明；确认上游任务 done（见各项前置）。
2. **执行**：按各项命令/脚本运行；`dry_run` 先行；execute 前确认备份与双人复核。
3. **记录**：保存原始输出/日志/截图/CI URL；V1-OPS 写 envelope JSON；M 项按第 6 节追加 `docs/verification.md` 条目。
4. **判定**：按各项通过判据逐条判定；任一 required 检查 fail/not_run → 该项不得判通过；标 `fail`/`not_run`/`blocked` 并登记 blocker。
5. **回填**：由执行片（PH-347–350 等）在报告中引用证据；本手册不预填结果。
6. **退出标准**：M 项全部 required 层证据齐备；V1-OPS-01–06 全部真实执行且无 not_run/blocked（L5 由生产/试点单独验收）。

## 4. M01–M15 执行手册

命令均为**可执行命令**；本片未执行，状态按实际标 `not_run`/`blocked`。通用命令（每项均需）：

```sh
python scripts/projectctl.py check
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test tests --verbosity 1
python manage.py verify_integrity --json
```

### M01 企业、账号与权限（V1-ORG-01–08）
- 前置：PH-264/281/024 done；身份/权限模块已交付。
- 步骤：建企业/账号/角色/项目范围 → 停用/移交 → 敏感字段遮蔽检查 → PAT 撤销与审计；浏览器按 1440/390 与键盘/空错载停用态走查。
- 命令：通用命令 + `python manage.py test tests.test_v1_contracts tests.test_v1_identity_access_read tests.test_v1_identity_access_read_http --verbosity 1`（按实际存在）。
- 预期输出：测试 Ran/OK；停用后旧会话/令牌写请求拒绝；越 org/项目读/写/导出拒绝；审计含变更人/时间/来源。
- 证据模板：`M01-E1` 命令输出；`M01-E2` 越权反例输出；`M01-E3` 浏览器截图清单；`M01-E4` 审计记录；`M01-E5` CI URL。
- 通过判据：全部越权反例拒绝；停用即时生效；敏感字段不出现在页面/导出/context。
- 失败处置：权限漏洞立即停止发布，登记 blocker；不得以“按钮已隐藏”替代服务端拒绝。
- 角色：发布负责人 + 安全负责人；财务口径不涉及。
- 当前状态：`not_run`（方法就绪；本片未执行）。

### M02 统一基础资料（V1-MD-01–07）
- 前置：PH-024/212/214/228 等 done。
- 步骤：项目/员工/往来方/成本类别/资金账户唯一维护 → 合并/作废 → 引用一致性检查。
- 命令：通用命令 + `python manage.py test tests.test_masterdata --verbosity 1`（主数据回归）。
- 预期输出：同一 ID 被合同/收付/发票引用；旧 POST 不保留第二写规则；合并留审计。
- 证据模板：`M02-E1` 命令输出；`M02-E2` 引用一致性查询；`M02-E3` 合并审计记录。
- 通过判据：跨模块引用同一稳定 ID；历史引用不丢失；冲突人工确认。
- 失败处置：阻断合并路径；保留影响范围清单。
- 角色：发布负责人/业务负责人。
- 当前状态：`not_run`。

### M03 项目、交付与结项（V1-PRO-01–10）
- 前置：PH-219/222/265/275/312/322 done；迟到款/归档后处理按 PH-322 实现。
- 步骤：建项目/团队/里程碑/验收 → 暂停/取消/恢复 → 结项预检/归档 → 归档后迟到款受限流程。
- 命令：通用命令 + `python manage.py test tests.test_v1_close tests.test_v1_close_http tests.test_acceptance_revenue tests.test_acceptance_revenue_http --verbosity 1`。
- 预期输出：交付完成不自动产生收入；结项不强制清零；归档后迟到款经授权并留审计。
- 证据模板：`M03-E1` 命令输出；`M03-E2` 结项预检/blocker 清单；`M03-E3` 归档后处理审计。
- 通过判据：结项/归档规则与例外路径均按 spec；无强制清零/删除未清。
- 失败处置：停止结项；保留预检证据。
- 角色：发布负责人 + 财务复核人。
- 当前状态：`not_run`（PH-322 已合入；本片未执行）。

### M04 合同、收费条件与变化（V1-CON-01–08）
- 前置：PH-214/265/275/287 done。
- 步骤：三模板合同 → 批准版本/上限 → 变更/终止 → 履约/结算/票据/收款联查。
- 命令：通用命令 + `python manage.py test tests.test_v1_contracts tests.test_v1_contract_change tests.test_v1_contract_change_http tests.test_v1_contract_terms_snapshot tests.test_v1_contract_terms_snapshot_http --verbosity 1`。
- 预期输出：含税=不含税+税额；未批准增价不进有效价款；不以收款代应收。
- 证据模板：`M04-E1` 命令输出；`M04-E2` 条款守恒反例；`M04-E3` 版本/上限拒绝记录。
- 通过判据：门禁/守恒/版本规则全部满足。
- 失败处置：阻断批准；保留反例输出。
- 角色：财务复核人。
- 当前状态：`not_run`。

### M05 工时与投入（V1-TIM-01–08）
- 前置：PH-025/269/276 等 done。
- 步骤：本人/周视图/非项目/代录 → 提交/审核/退回/更正 → 缺费率/可计费/导出。
- 命令：通用命令 + `python manage.py test tests.test_v1_billable_confirmation tests.test_monthly_labor --verbosity 1`（工时/可计费/人工成本回归；其余 legacy tests 按实际补列）。
- 预期输出：缺费率不按 0 计成本；审批绑定版本；导出不含工资/费率。
- 证据模板：`M05-E1` 命令输出；`M05-E2` 缺费率/审批反例；`M05-E3` 导出字段清单。
- 通过判据：工时/成本/权限边界全部满足。
- 失败处置：阻断过账；登记缺费率风险。
- 角色：发布负责人 + 财务复核人。
- 当前状态：`not_run`。

### M06 人工、共享费用和项目成本（V1-CST-01–12）
- 前置：PH-217/244/250/305/313/**PH-330（已合入）** done；批次 C 演练在 PH-347–350。
- 步骤：工资池/政策/费率快照 → 成本计算一次 → 月度核对 → 共享费用分摊/预算 ETC → 工资支付/外包防重。
- 命令：通用命令 + `python manage.py test tests.test_v1_bank_charge tests.test_v1_bank_charge_approval tests.test_v1_bank_charge_cost tests.test_v1_bank_charge_pairing tests.test_v1_shared_cost_allocation tests.test_v1_shared_cost_http tests.test_v1_budget_etc --verbosity 1`。
- 预期输出：公司总成本一次；分摊净零重归属；历史不无痕重算；AC+ETC 命名分层。
- 证据模板：`M06-E1` 命令输出；`M06-E2` 净零/重复成本反例；`M06-E3` 分摊/预算核对输出。
- 通过判据：成本守恒/分摊净零/历史快照全部满足。
- 失败处置：停止结账/分摊；保留差异报告。
- 角色：财务复核人 + DBA（数据核对）。
- 当前状态：`not_run`（PH-330 已合入；执行片未跑）。

### M07 费用、采购和外包支出（V1-SPD-01–10）
- 前置：费用/采购/预付/税额实现切片（尚未全部交付）。
- 步骤：报销/费用 → 承诺/验收/审批 → 暂估/税额资格 → 借支/退货/共享/跨期预付。
- 命令：通用命令 + 对应 scoped tests（费用/采购实现交付后补列真实模块）；**未实现项标 blocked**。
- 预期输出：同来源只确认一次成本；税额资格未知不默认抵扣；退款不自动反成本。
- 证据模板：`M07-E1` 命令输出；`M07-E2` 重复成本/税额反例；`M07-E3` 未实现项 blocked 清单。
- 通过判据：成本一次/税额/退款边界满足。
- 失败处置：blocked 项不得判通过；登记实现片。
- 角色：财务复核人。
- 当前状态：`blocked`（费用/采购主体实现未交付）。

### M08 结算、应收应付与核销（V1-SET-01–10）
- 前置：PH-224/232/249/283 done；PG 并发由 V1-OPS-06 验证。
- 步骤：应收/应付来源 → 账龄 → 部分核销/预收抵扣 → 撤销/退回/折让 → 跨项目例外。
- 命令：通用命令 + `python manage.py test tests.test_v1_settlement tests.test_v1_settlement_http tests.test_v1_settlement_coverage_read tests.test_v1_settlement_coverage_read_http --verbosity 1` + 并发反例（V1-OPS-06）。
- 预期输出：期初+新增-核销守恒；不超额；撤销≠退款；跨企业拒绝。
- 证据模板：`M08-E1` 命令输出；`M08-E2` 超额/并发反例；`M08-E3` 账龄/核销核对。
- 通过判据：金额守恒/权限/并发边界满足。
- 失败处置：停止核销；保留时序日志。
- 角色：财务复核人 + DBA。
- 当前状态：`not_run`（并发证据 blocked 至 PH-349）。

### M09 实际现金、预收预付和预测（V1-CSH-01–12）
- 前置：PH-228/230/239/240/244/245/250/288 done。
- 步骤：银行事实/匹配/归属 → 预收预付/退款退票 → 公司收付/手续费净额/互转 → 计划/13 周预测/覆盖/更正。
- 命令：通用命令 + `python manage.py test tests.test_v1_cash tests.test_v1_cash_http tests.test_v1_cash_posting tests.test_v1_cash_refund tests.test_v1_cash_bridge_read tests.test_v1_cash_classification --verbosity 1`。
- 预期输出：资金事实不重复；净额+费用守恒；预测≠利润/余额；更正留真实日期。
- 证据模板：`M09-E1` 命令输出；`M09-E2` 净额/重复反例；`M09-E3` 预测与余额对照。
- 通过判据：现金守恒/来源/期间规则满足。
- 失败处置：停止记账；保留银行对账单。
- 角色：财务复核人。
- 当前状态：`not_run`。

### M10 发票与管理收入（V1-REV-01–10）
- 前置：PH-252/320/321/284/293 done；只读对账 PH-325 done。
- 步骤：发票登记/分配/红冲 → 收入方法/里程碑/工时/服务期 → 累计调整/配对/追溯/缺口。
- 命令：通用命令 + `python manage.py test tests.test_v1_invoice tests.test_v1_invoice_http tests.test_v1_invoice_tax tests.test_v1_invoice_tax_http tests.test_v1_revenue_pairing_read tests.test_v1_revenue_pairing_read_http --verbosity 1`。
- 预期输出：发票不自动产生收入/成本/现金；红冲关联原票；收入口径唯一；只读对账 issue 可见。
- 证据模板：`M10-E1` 命令输出；`M10-E2` 红冲/收入反例；`M10-E3` 对账 snapshot/issue 输出。
- 通过判据：发票/收入/对账规则满足。
- 失败处置：停止开票/红冲；保留原票与来源。
- 角色：财务复核人。
- 当前状态：`not_run`（PH-252/320/325 已合入；本片未执行）。

### M11 预算、剩余预计与报价复盘（V1-PLN-01–09）
- 前置：PH-305/306/313/**PH-330（已合入）**。
- 步骤：预算基线/变更 → 剩余投入 → 承诺/ETC/释放 → EAC/结余/情景/差异/复盘。
- 命令：通用命令 + `python manage.py test tests.test_v1_budget_etc tests.test_v1_shared_cost_allocation --verbosity 1`。
- 预期输出：AC+ETC 分层命名；同来源只覆盖一次；未批准变化不进基准。
- 证据模板：`M11-E1` 命令输出；`M11-E2` ETC/覆盖反例；`M11-E3` 差异复盘输出。
- 通过判据：预算/ETC 规则与来源唯一性满足。
- 失败处置：blocked 项登记；不做无来源估算。
- 角色：财务复核人 + PM。
- 当前状态：`not_run`（PH-330 已合入；本片未执行）。

### M12 月末核对、期间与纠错（V1-REC-01–09）
- 前置：PH-283/284/288/291/304/309/312/**PH-322 done**。
- 步骤：工资/费用/银行/合同收入核对 → 预检查（blocker/warning） → 关期 → 开放期冲销/补录 → 底稿导出/完整性。
- 命令：通用命令 + `python manage.py test tests.test_v1_close tests.test_v1_close_http tests.test_v1_ops_reconciliation_overview tests.test_v1_ops_workbench --verbosity 1` + `verify_integrity`。
- 预期输出：只前移锁定；开期冲销保原始；导出与页面同口径；硬错误阻断。
- 证据模板：`M12-E1` 命令输出；`M12-E2` 预检查/blocker 清单；`M12-E3` 冲销/导出一致性；`M12-E4` PG 锁竞争（V1-OPS-06）。
- 通过判据：期间/纠错/导出规则满足。
- 失败处置：停止锁账；保留预检与冲销证据。
- 角色：财务复核人 + DBA。
- 当前状态：`not_run`（PH-322 已合入；本片未执行）。

### M13 工作台、提醒与查询（V1-INS-01–07）
- 前置：PH-291/304/309 done；insights/通知实现片（PH-339/340 等）未交付。
- 步骤：四角色工作台 → 通知/到期扫描 → 汇总穿透 → 缺口/滞后。
- 命令：通用命令 + `python manage.py test tests.test_v1_ops_workbench tests.test_v1_ops_reconciliation_overview --verbosity 1`（既有只读来源回归）；insights/通知实现未交付，相关项 `blocked`。
- 预期输出：每条待办有原因/来源/责任人；空值不假零；页面/导出/API 一致。
- 证据模板：`M13-E1` 命令输出；`M13-E2` 权限/缺口输出；`M13-E3` blocked 清单。
- 通过判据：工作台/通知/缺口规则满足。
- 失败处置：blocked 项不得判通过。
- 角色：PM/发布负责人。
- 当前状态：`blocked`（insights/通知实现未交付）。

### M14 首次启用、期初与导入导出（V1-ONB-01–08）
- 前置：PH-274 done；PH-289 导入提交 proposed。
- 步骤：向导/从零或存量/期初 → 模板/映射/预览 → 原子提交/回退 → 开账核对/导出。
- 命令：通用命令 + `python manage.py test tests.test_v1_import_preview tests.test_v1_import_preview_http --verbosity 1`（PH-274 已交付；提交片 PH-289/PH-353 blocked）。
- 预期输出：预览不留业务痕迹；提交全有或全无；期初不重复成本/收入。
- 证据模板：`M14-E1` 命令输出；`M14-E2` 预览/提交幂等；`M14-E3` 期初核对。
- 通过判据：导入/期初规则满足。
- 失败处置：blocked 至 PH-289/PH-353；保留预览证据。
- 角色：财务复核人 + 支持负责人。
- 当前状态：`blocked`（导入提交未交付）。

### M15 安装、升级、备份和客户支持（V1-OPS-01–08）
- 前置：PH-333/334/335 与执行片 PH-347/348/349/350；脚本契约 PH-345。
- 步骤：按下节 V1-OPS-01–06 执行；支持/退出按 V1-OPS-07/08 文档与合同约定（本手册不伪造）。
- 命令：见第 5 节；未实现脚本标 `blocked`/`not_run`。
- 预期输出：各 V1-OPS envelope `status=pass` 且 artifacts 可复核；RPO/RTO 实测；发布包无敏感。
- 证据模板：V1-OPS envelope + 报告引用 + `docs/verification.md` 条目。
- 通过判据：V1-OPS-01–06 真实执行通过；L5 生产/试点单独验收。
- 失败处置：按 PH-345 退出码与失败处置；不自动回退数据。
- 角色：发布负责人/DBA/安全负责人/支持负责人。
- 当前状态：`blocked`/`not_run`（执行片未实现）。

## 5. V1-OPS-01–06 执行手册（对齐 PH-345 契约）

通用：脚本调用 `python scripts/ops/<script>.py --mode dry_run|execute --env <profile> --out <evidence.json>`；envelope/退出码/幂等/脱敏见 PH-345 §1–2；本片不创建 `scripts/ops/*`，未实现项按实际标 `not_run`/`blocked`。

### V1-OPS-01 安装与诊断（`ops_preflight`）
- 前置：空机或隔离环境、固定版本、独立密钥/DB/卷、应用/DB 不暴露公网。
- 步骤：OS/Compose 版本检查 → 端口/磁盘/内存 → 镜像/依赖 → 迁移列表 → 健康检查 → 诊断包（脱敏）。
- 预期输出：env/版本/端口/磁盘/迁移/健康结果；失败安全重试记录。
- 证据：envelope `checks` 对应 V1-OPS-01.x；artifacts 日志/诊断包（脱敏）。
- 通过判据：前置满足且健康检查通过；失败安全重试有记录。
- 失败处置：exit 2/3/4 对应 not_run/fail；不继续部署。
- 角色：发布负责人（execute）+ 安全负责人复核。
- 当前状态：`not_run`（PH-347 未实现）。

### V1-OPS-02 TLS/安全（`ops_tls_probe`）
- 前置：真实域名、证书链、代理边界、Cookie/HSTS 期望。
- 步骤：TLS 握手/证书到期/续期演练 → 安全头 → 错误配置拒绝。
- 预期输出：证书有效/可续期；HSTS/Cookie 边界正确；错误配置被拒。
- 证据：envelope V1-OPS-02.x + 抓包/头输出（脱敏）。
- 通过判据：真实 TLS 访问与错误配置拒绝均实证；不以配置存在代替。
- 失败处置：exit 3/4；不贴配置冒充通过。
- 角色：安全负责人 + 发布负责人。
- 当前状态：`not_run`（PH-347 未实现）。

### V1-OPS-03 升级与兼容回退（`ops_upgrade`）
- 前置：冻结基线 SHA、候选 SHA、备份完成且校验、迁移只追加、回退仅结构兼容。
- 步骤：备份 → 候选升级 → 迁移应用/校验 → 冒烟 → （必要时）回退演练。
- 预期输出：升级前后 SHA、迁移列表、冒烟结果、回退记录；账本不倒放。
- 证据：envelope V1-OPS-03.x + artifacts 日志/备份哈希/冒烟输出。
- 通过判据：迁移成功、冒烟通过、失败可恢复、回退不破坏结构兼容。
- 失败处置：停止升级；按 `upgrade-recovery.md` 回退；保留备份/日志。
- 角色：发布负责人 + DBA。
- 当前状态：`not_run`（PH-347 未实现）。

### V1-OPS-04 备份与恢复（`ops_backup_verify` / `ops_restore_rehearsal`）
- 前置：PG+附件一致快照、隔离恢复环境、最小权限、RPO/RTO 目标。
- 步骤：快照 → 清单/哈希/权限/保留 → 隔离恢复 → 登录/报表/凭证/附件核对 → RPO/RTO 实测。
- 预期输出：快照/哈希清单、恢复命令、核对结果、RPO/RTO 实测；禁止“备份任务存在”即通过。
- 证据：envelope V1-OPS-04.x + artifacts 快照清单/恢复日志/核对截图。
- 通过判据：隔离恢复成功且核对一致；RPO/RTO 达标。
- 失败处置：exit 3/4；保留现场；不在生产库演练恢复。
- 角色：DBA + 财务复核人。
- 当前状态：`not_run`（PH-348 未实现）。

### V1-OPS-05 健康/容量/积压（`ops_health_capacity`）
- 前置：健康端点、任务/队列、磁盘/备份失败、容量阈值、基线数据量。
- 步骤：健康检查 → 积压/磁盘/失败 → 容量阈值 → 告警可操作性；100 人规模关键路径 P50/P95/查询数。
- 预期输出：健康/积压/磁盘/失败/基线记录；日志脱敏。
- 证据：envelope V1-OPS-05.x + artifacts 健康输出/基线报告。
- 通过判据：健康项通过、积压/磁盘在阈值内、告警可诊断、日志无敏感原文。
- 失败处置：exit 3/4；不静默重试；登记容量 blocker。
- 角色：支持负责人 + DBA。
- 当前状态：`blocked`（PH-350 等 PH-347/348/349）。

### V1-OPS-06 PostgreSQL 多连接安全与并发（`ops_pg_concurrency`）
- 前置：真实 PG、脱敏测试数据、并发用例清单（双审批/超核销/退款/分摊/期初/锁账/发票红冲/汇率快照）。
- 步骤：每用例两连接时序 → 一胜一拒 → 无半笔 → `verify_integrity` ok → 时序日志。
- 预期输出：PG 版本/连接数、用例结果、时序日志；SQLite 结果不作为并发证据。
- 证据：envelope V1-OPS-06.x + artifacts 时序日志/verify 输出。
- 通过判据：每用例一胜一拒/幂等、无半笔；SQLite 不冒充 PG。
- 失败处置：exit 3/4；保留时序日志；不得串行 SQLite 冒充。
- 角色：DBA + 财务复核人。
- 当前状态：`blocked`（PH-349 未实现）。

## 6. 证据回填模板（M 项）

追加到 `docs/verification.md` 的条目模板（实际执行后填写，禁止预填）：

```markdown
## Mxx <名称> 批次 C 验收（YYYY-MM-DD）

- 环境/profile：staging|rehearsal|production；app_sha：<sha>；DB：<version>
- 执行角色：<role>；复核：<role>
- 命令与结果：
  - `python manage.py test ...` → Ran N tests, OK/FAILED（逐项）
  - `python manage.py verify_integrity --json` → {"ok": true, "errors": []}
- 证据：<artifacts 路径/CI URL/截图；V1-OPS envelope 路径>
- 判定：L2 pass / L3 pass / L4 pass / L5 not_run（逐层）
- 失败/未执行：<not_run/blocked 项与原因>
- 结论：pass|fail|blocked（不得在未执行时写 pass）
```

M 项与 V1-OPS 的 subject 对应：M15 包含 V1-OPS-01–08；其他 M 项涉及部署/恢复时引用对应 V1-OPS envelope，不复制结果。

## 7. 批次 C 退出标准

1. M01–M15 每项 required 层（L2/L3；涉及 UI 的 L4；M15 L5 由生产/试点）证据齐备，无 not_run/blocked 遗留；未达标项须有明确 blocker 与责任人，不得以“文档已写”判通过。
2. V1-OPS-01–06 全部真实执行且 envelope `status=pass`，artifacts 可复核；exit 非 0 不得判通过。
3. 批次 C 演练片（PH-347/348/349/350）报告引用证据；PH-333/334/335 汇总验收据此签署。
4. 本手册不改变 CI 门禁、验收命令或产品行为；执行前以可信 BASE 的 spec/契约复核。

## 8. 反例（一律拒绝）

- 无命令实际输出/CI URL/截图/PG 日志却写“通过”；用配置/文档/演练冒充生产。
- 用 SQLite 结果冒充 PG 并发；跳过备份直接升级；恢复演练在生产库进行。
- 证据含密码/令牌/薪酬/真实客户原文；把 not_run/skipped 计为通过。
- 以本手册存在代替 PH-347–350 执行证据。

## 9. 交叉引用与未验证

- 矩阵/五层证据：`docs/v1.0-development/batch-c-acceptance.md`（PH-332）。
- 脚本契约/envelope/退出码：`docs/engineering/ops-runbook.md`（PH-345）。
- 升级/恢复契约：`docs/engineering/upgrade-recovery.md`。
- 批次 C 路线：`ROADMAP.md`「批次 C 状态同步」；演练依赖：PH-330（done）/PH-345（done）→ PH-347/348/349 → PH-350。
- **未验证**：本片为 docs-only，未执行任何 M 项/V1-OPS 脚本；所有项状态为本章标注的 `not_run`/`blocked`，无通过结论。

## 10. PH-357 演练证据回填（2026-10-31，BASE ef13003）

> 本节只回填 PH-347/348/349/350/352 已实际执行并记录在报告中的本地证据；未重跑脚本、未执行真实环境。L3–L5 严格 not_run，发布资格 gate **release_ready=false**。

| V1-OPS | 执行片 | 实际本地证据（来源报告） | 层级 | 判定/缺口 |
| --- | --- | --- | --- | --- |
| 01 安装/诊断 | PH-347 | `install_drill.py --mode dry_run --env rehearsal`（报告 BASE cbb84c2）envelope pass；staging/production execute exit 5 拒绝 | L1+L2 | 本地预检 pass；L3 真实安装 not_run |
| 02 TLS/安全 | PH-347 | `install_drill.py --step tls`：无真实域名/证书 → not_run；错误配置拒绝有契约测试 | L1+L2（契约） | 真实 TLS L3 not_run；不得以配置冒充 |
| 03 升级/回退 | PH-347 | `upgrade_drill.py --mode dry_run --env rehearsal`：备份证据/迁移前缀/重复/破坏性/结构兼容回退检查 pass；生产 execute exit 5 | L1+L2 | 本地 dry_run pass；真实升级/回退 L3/L5 not_run |
| 04 备份/恢复 | PH-348 | `backup_drill.py` SQLite 快照/哈希/行数/integrity/manifest pass；`restore_drill.py` 隔离恢复/表/schema/附件/RPO-RTO pass；PG execute 诚实 not_run | L1+L2（SQLite） | 本地 SQLite pass；真实 PG 恢复 L3 not_run，不得冒充 |
| 05 健康/容量/积压 | PH-350 | `healthcheck.py --mode dry_run --env rehearsal --facts <complete-local>` envelope pass；缺 facts not_run；生产/staging execute exit 5；backlog 阈值 fail 探针 | L1+L2 | 本地阈值/脱敏 pass；真实容量/性能 L3/L5 not_run |
| 06 PG 并发 | PH-349 | 无 PG DSN → not_run/exit 2；SQLite DSN 拒绝；真实 PG 探针矩阵 L3 not_run | L1+L2（契约） | 真实 PG 一胜一拒 L3 not_run；SQLite 不冒充 |

### 10.1 发布资格 gate 判定

- **release_ready=false**：L1/L2 证据齐，但 L3（CI/PG/真实部署）、L4（浏览器 1440/390）、L5（生产/试点/客户验收）均未执行。
- **缺口清单**：L3 CI/PG 与真实 staging 安装/升级/TLS/恢复/并发；L4 浏览器；L5 生产/客户验收；PH-367（部署/恢复/并发汇总验收）与 PH-372（D 批验收 runbook）尚未完成；PH-333/334/335 汇总片不得以本回填替代执行证据。
- **双人复核/签名纪律**：回填条目须执行人+独立复核人确认；豁免/缺口需产品/发布/独立复核三方签批，执行人不得自批；证据绑定 SHA、14 天时效、变更重跑，不可挑选/覆盖（PH-366 §6）。
- 失败项保留原始错误摘要与处置；未执行项继续 not_run，不清零、不伪造 pass。
