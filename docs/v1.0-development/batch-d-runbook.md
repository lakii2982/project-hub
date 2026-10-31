# 批次 D 验收执行 runbook

- 产出任务：PH-375（docs-only；不实现/不执行；本文件为执行模板，不含通过结论）。
- 对齐：`batch-d-acceptance.md`（矩阵）、`batch-c-runbook.md`（执行协议）、`batch-c-release-evidence-contract.md`（envelope/证据/发布 gate）、`docs/engineering/ops-runbook.md`（脚本契约）。
- 当前状态：D-01 已执行本地 L2 部分验收（PH-382：cash-refund 真实 e2e pass；revenue/settlement 真实 e2e、L3/L4/L5 not_run；整体未通过）；D-02–D-11 `blocked`；发布资格仍按 PH-366 **blocked**。

## 1. 通用执行协议

1. **准备**：候选 SHA、环境/profile（ci/staging/rehearsal/production）、数据脱敏声明、执行人+复核人、备份/回退点；确认上游依赖 done（见矩阵）。
2. **执行**：按各项命令（scoped pytest/Django tests、ops 脚本、CI PG）；先 dry_run/只读，后 execute；不写生产业务事实。
3. **记录**：保存原始输出/CI URL/截图/时序日志；V1-OPS envelope 按 PH-345；M 项按 `docs/verification.md` 模板。
4. **判定**：按矩阵 required 层逐项 pass/fail/not_run/blocked；任一 required 非 pass → 该项不得判通过。
5. **回填**：`docs/verification.md` 条目 + report 引用 artifacts；缺口登记（item/reason/owner/due/risk/mitigation）。
6. **退出**：全部 required 齐备且无 not_run/blocked（除三方签批豁免）；发布 gate 按 PH-366 §6。

## 2. 证据回填模板（对齐 PH-366）

```markdown
## D-xx <切片> 批次 D 验收（YYYY-MM-DD，BASE <sha>）

- 候选 SHA：<40-hex>；环境/profile：ci|staging|rehearsal|production；执行角色：<role>；复核角色：<role>
- 命令：`<实际命令>`（可复现）
- 结果：Ran <n> tests, OK/FAILED（逐项）；或 ops envelope exit_code/status/checks 计数
- 证据 artifacts：<路径/CI URL/截图/时序日志>（sha256=<hash>）
- 证据层级：L1 <有/无>；L2 <有/无>；L3 <CI run URL/无>；L4 <截图/无>；L5 <生产/试点/无>
- 缺口/未执行：<not_run/blocked 项、原因、责任人、处置>
- 判定：<pass|fail|blocked>；四眼签名：<role + signed_at + evidence_digest>
```

- 演练（rehearsal/staging）= L2/L3，不冒充 L5；SQLite 不作 PG 证据；dry_run 不作 execute 证据；无证据不得写 pass。
- 证据绑定候选 SHA；环境/契约变化、超 14 天 → 重跑或书面重新确认（PH-366 §7/§8）。

## 3. 切片执行卡

### D-01 PH-370 跨 owner 编排执行（实现已合入）
- 前置：PH-320/321/322/325/330 done；测试 org/合同/发票/红冲/退款/结算调整场景；owner/finance 执行、auditor 只读、pm 项目摘要、member 拒绝。
- 步骤：建发票→红冲→按 PH-320 引用链执行收入反冲/cash 退款/settlement 调整→核对引用链/未完成项→`verify_integrity`。
- 命令：通用命令 + `python manage.py test tests.test_v1_invoice_orchestration tests.test_v1_invoice_orchestration_http --verbosity 1`；真实 PG 交 CI/PH-349；L4 走查页面。
- 预期：一胜一拒/无半笔；引用链完整；不直接写其它 owner；失败项可追。
- 证据：命令输出、编排引用链/核对输出、L4 截图、CI URL；模板见 §2。
- 判据：矩阵 D-01 全部满足；财务复核人签字。
- 失败处置：停止执行；保留引用链/审计；不得手工补写其它 owner；按 owner 既有更正流程。
- 状态：**部分执行（2026-10-31，PH-382）**：L2 cash-refund 真实 e2e pass、9 scoped tests OK；revenue/settlement 真实 e2e、L3/L4/L5 **not_run**；D-01 整体未通过（evidence: docs/verification.md PH-382 条）。

### D-02 PH-371 税额→costing 成本更正
- 前置：PH-371 实现已合入（done）。
- 步骤：确认 not_recoverable 税额→开放期成本调整→期间/锁期/重复/权限反例→verify_integrity。
- 命令：实现后 `python manage.py test tests.test_v1_tax_cost_adjustment tests.test_v1_tax_cost_adjustment_http --verbosity 1`。
- 判据/失败处置：仅 confirmed/not_recoverable；跨期/锁期/重复拒绝；失败保留原资格/期间证据。
- 状态：**L2 已执行 pass（14 tests OK）；L3/L4/L5 not_run；D-02 整体不通过（false）**。真实 CI/PG、浏览器、生产需另行回填。

### D-03 PH-338 发票批量/导入
- 前置：PH-289 done（当前未 done；PH-362 契约已 done）→ blocked。
- 步骤：模板→预检→批量登记→行/来源分配→冲突/错误行→重排重试幂等；L4 上传/错误页。
- 命令：实现后 `python manage.py test tests.test_v1_invoice_import tests.test_v1_invoice_import_http --verbosity 1`。
- 判据：全有或全无、零半笔、幂等；失败处置：拒绝整批并可下载错误清单，不部分提交。
- 状态：**blocked**。

### D-04 PH-353 导入提交
- 前置：PH-181/185/289 done（当前未 done；PH-362 契约已 done）→ blocked。
- 步骤：预览→提交（原子/幂等）→冲突回滚→期初核对；`verify_integrity`。
- 命令：实现后 `tests.test_v1_import_submission`/`_http`；判据：无半笔/零业务事实于失败、重复 token 幂等。
- 状态：**blocked**。

### D-05/D-06 PH-339/PH-354 insights
- 前置：PH-272 done（当前未 done → blocked）。
- 步骤：期间预检/差异单/工作台只读；权限/缺口/缺失；L4 页面。
- 命令：实现后 `tests.test_v1_insights*` / `pass tests.test_v1_insights_precheck*`；判据：只读零写、页面/导出/API 一致、missing 不假零。
- 状态：**blocked**。

### D-07/D-08 PH-340/PH-355 通知
- 前置：PH-301/314 或 PH-179/183/151 done（当前未 done → blocked）。
- 步骤：扫描/收件箱/去重/权限/停用；无外部推送。
- 命令：实现后 `tests.test_v1_notifications*` / `tests.test_v1_reminders*`；判据：同事实一条、重复扫描不重复、停用 history_read。
- 状态：**blocked**。

### D-09/D-10 批次 C 收尾与发布资格
- 前置：PH-335/357/367/372 done（当前 PH-335/357 ready、PH-367/372 proposed → blocked）。
- 步骤：按 PH-366 §6 收集 V1-OPS-01–06 证据→回填 `docs/verification.md`→输出 gate/缺口/豁免→三方签批。
- 命令：`python scripts/projectctl.py check` + `python -m unittest tests.test_projectctl tests.test_governance -v` + 各 ops 脚本/CI PG URL；判据：每项 pass/无 not_run 遗留；失败处置：blocked 不发布。
- 状态：**blocked/not_run**（PH-357 证据回填未执行；发布资格 blocked）。

### D-11 多币种后续重定
- 前置：PH-318/326 范围重定决策（当前 blocked）。
- 步骤：对照 PH-321/324 已交付能力，决定关闭或重定后续薄片；记录决策与 DoR。
- 判据：不重复已交付、后续片依赖/白名单完整；状态：**blocked**。

## 4. 未验证与发布资格

- 本片 docs-only；D-01 已有 PH-382 本地 L2 部分证据（cash-refund 真实；revenue/settlement 真实 e2e 未执行）；D-02–D-11 全部 blocked。
- 无 L3 CI PG、L4 浏览器、L5 生产/试点证据；批次 D 发布资格仍 **blocked**（依 PH-366，不得以本 runbook 宣称就绪）。

## 5. 反例

- 无证据写 pass、演练冒充 L5、SQLite 冒充 PG、dry_run 冒充 execute、跳过预检/四眼、删除/覆盖证据、含客户/薪资/凭据。
