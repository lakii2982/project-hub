# 批次 C 部署/恢复/并发汇总验收执行前置契约（PH-367 冻结）

- 产出：PH-367（docs-only；**不执行**任何演练/部署/恢复/并发，不改产品/脚本/迁移/门禁）。
- 依赖（均 done）：PH-333 部署/升级/回滚汇总（`scripts/ops/rollup_acceptance.py`）、PH-334 PG 并发汇总（`tests/test_v1_pg_concurrency.py` + `docs/engineering/pg-concurrency.md §5`）、PH-335 健康/容量/积压汇总（`healthcheck.rollup_evidence`）、PH-357 证据回填（`batch-c-runbook.md §10`/`batch-c-acceptance.md §7`）、PH-366 证据/发布资格契约、PH-345 脚本契约、PH-352 runbook、PH-332 五层证据。
- 目的：冻结「汇总验收执行」的**前置条件、证据合并规则（多来源取最严）、PH-366 gate 判定流程、缺口/豁免三方签批、执行顺序与回滚**，供后续真实环境执行片按同一口径执行；本文件自身不含任何通过结论。

## 1. 汇总来源与产物（冻结）

| 来源 | 产物/命令 | 覆盖 | 当前层级/状态 |
| --- | --- | --- | --- |
| PH-333 | `python scripts/ops/rollup_acceptance.py`（`ops_rollup_acceptance` envelope，L1–L5 检查） | V1-OPS-01/02/03/04、M15 | L1 代码 + L2 本地/隔离；`release_ready=false`；L3/L4/L5 not_run |
| PH-334 | `tests.test_v1_pg_concurrency`（无 DSN → not_run/exit2；SQLite 被拒） | V1-OPS-06 | L1+L2 契约；真实 PG/CI not_run |
| PH-335 | `healthcheck.rollup_evidence`（L1–L5） | V1-OPS-05 | L1+L2 本地；L3/L4/L5 not_run；`release_ready=false` |
| PH-357 | `batch-c-runbook.md §10`、`batch-c-acceptance.md §7`、`docs/verification.md` 回填 | 全 V1-OPS/M | 本地 L1+L2 证据回填；缺口清单 L3/L4/L5 |
| PH-366 | gate 清单/证据规范/豁免/签名 | 发布资格 | 契约 |

- 汇总**只消费**上述产物与 PH-347/348/349/350 已实现脚本输出，不新造口径/不重写脚本/不改 threshold。
- 产物落点：envelope/汇总 JSON 存 `runtime/evidence/<date>/`（不入发布包）；脱敏摘要入 `docs/verification.md`；CI artifact 以 URL+job 记录。

## 2. 执行前置条件（冻结，任一不满足 → blocked）

### 2.1 通用

1. **候选 SHA**：40-hex，等于当前待发布/演练候选；BASE 与 spec/契约一致；工作区干净（执行片不夹带无关改动）。
2. **环境/profile**：`rehearsal|staging|production` 明确；证据必须绑定该 profile；不能用 rehearsal 证据顶替 staging/production。
3. **数据/权限**：仅演练数据；无真实客户/薪资/凭据；执行人（owner/finance/DBA/支持）与独立复核人明确；四眼/双人控制；生产需变更窗口/审批。
4. **备份/回退点**：升级/恢复前有可验证备份与回退方案；PG 有快照/连接预算；失败可停止且不自动修数据。
5. **证据采集**：命令可复现、输出/artifacts 可保存、时间戳/环境/SHA 齐全；脱敏规则（PH-345/PH-366）已就位。
6. **边界**：演练不触碰真实客户/生产凭据；staging/production execute 需显式审批；不自动扩缩容/不自动修复。

### 2.2 分项前置

| 项 | 前置 |
| --- | --- |
| V1-OPS-01 安装预检 | 目标机/依赖/端口/磁盘/迁移版本就绪；dry_run 通过；回退包可用 |
| V1-OPS-02 TLS 探针 | 真实域名/证书/续期路径；DNS/端口可达；非本地自签冒充 |
| V1-OPS-03 升级/回退 | 候选 SHA 升级包、只追加迁移、回退包；升级前备份；窗口/审批 |
| V1-OPS-04 备份/恢复 | PG+附件一致快照、哈希/保留策略、隔离恢复环境；RPO/RTO 目标；不覆盖源 |
| V1-OPS-05 健康/容量 | 环境 facts 或 SLO（PH-368）；rehearsal 默认阈值不得作为生产 SLO；备份年龄/磁盘/连接/积压事实 |
| V1-OPS-06 PG 并发 | 真实 PG DSN（非 SQLite）、版本/连接上限、两连接/业务并发脚本、无半笔断言、verify_integrity |
| M15 汇总 | V1-OPS-01–06 证据齐备；缺口/豁免已登记；三方签批 |

- 前置不满足时**不执行**并在汇总中记 `blocked`，不得以 dry_run 或配置存在代替。

## 3. 证据合并规则（冻结：多来源取最严）

### 3.1 归一化

- 每个来源 envelope 按 PH-345/PH-366 归一：`exit 0→pass`、`2→not_run`、`3→fail`、`4→fail(environment_error)`、`5→blocked`；同一来源 `status` 与 exit 冲突时取最严并标 `conflict`。
- 证据以 `(candidate_sha, profile, script/check_id)` 定位；重复来源同 key → 保留全部并取最严，不覆盖历史。

### 3.2 合并算法

1. 按 gate 项（V1-OPS-01–06、M15）收集所有来源 check。
2. 对同一 `check_id` 多来源状态取**最严**：`fail > blocked > not_run > pass`（与 PH-366 §3 一致）。
3. 同一来源不同环境（rehearsal/staging/production）**分层保留**，不得用低层覆盖高层；L3/L4/L5 缺失 → 对应层 `not_run`。
4. 合并输出：

```json
{
  "aggregate_id": "batch-c-...", "candidate_sha": "<40hex>", "profile": "rehearsal|staging|production",
  "sources": [{"script": "ops_rollup_acceptance", "status": "pass", "artifacts": []}],
  "checks": [{"id": "V1-OPS-03.1", "status": "not_run", "evidence_refs": [], "layers": ["L2"]}],
  "overall_status": "pass|not_run|blocked|fail",
  "release_ready": false,
  "gaps": [{"item": "V1-OPS-06", "reason": "no real PG", "owner": "DBA", "due": "..."}],
  "conflicts": [], "merged_at": "<ISO8601 UTC>", "merged_by_role": "..."
}
```

5. `release_ready=true` 只能由 PH-366 gate 在**全部必需证据 pass 且无 not_run/blocked（除签批豁免）**后置；合并脚本/文档不得自发置 true。
6. **禁止**：挑选有利来源、删除/覆盖旧证据、跨 SHA 合并、把 dry_run 合并成 execute、把 rehearsal 合并成 L5。

## 4. PH-366 gate 判定流程（冻结）

1. **校验**：candidate SHA/scope/profile/白名单；证据时效（14 天）/环境/契约版本。
2. **前置**：§2 全部满足；缺失 → `blocked`，停止。
3. **收集**：§1 来源 + §3 归一化/合并；记录 conflicts。
4. **必需集**：按 PH-366 §6.1 逐项（治理基线、V1-OPS-01–06、M01–M15 required 层、L4、发布包）判定 pass/fail/not_run/blocked。
5. **缺口/豁免**：每个非 pass 登记（item/expected/actual/reason/owner/due/risk/mitigation）；核心项（03/04/06/M15/L5）不可豁免；非核心豁免需产品+发布+独立复核三方签批+到期，豁免≠pass。
6. **签名**：执行人/复核人角色、signed_at、scope、decision、evidence_digest（PH-366 §7）；执行人不得自批。
7. **结论**：
   - 全部 required pass 且无缺口 → `released`（发布资格通过）；
   - 任一 required fail → `blocked`（修复后重跑）；
   - 任一 required not_run/blocked 且无有效豁免 → `blocked`；
   - 带签批豁免 → `released_with_gaps`（记录范围/风险/回滚，下一版必须关闭）。
8. 判定写入 `docs/verification.md` + report；不得只写“已通过”而无证据/签名。

## 5. 缺口/豁免三方签批（冻结）

- 记录：`item, evidence_expected, actual_status, reason, owner, due, risk, mitigation, waiver_scope, expires_at, approvers[product, release, independent_reviewer], signed_at, evidence_digest`。
- 三方缺一 → 不生效；执行人/单一角色自批无效；核心项不可豁免；过期失效需重跑/重新签批。
- 豁免只允许“带缺口发布”，不得把缺口项写成 pass；发布说明必须披露范围/风险/回滚；下一版必须关闭。

## 6. 执行顺序与回滚（冻结）

1. **顺序**：前置/治理基线 → V1-OPS-05 健康/容量（只读）→ V1-OPS-01 安装预检 → V1-OPS-02 TLS → V1-OPS-03 升级/回退 → V1-OPS-04 备份/恢复 → V1-OPS-06 PG 并发 → 合并/PH-366 gate 判定。
2. **每步 checkpoint**：保存 envelope/输出/artifacts；失败即停止，不继续后续步骤；破坏性步骤失败不盲目重试（最多一次按 runbook，需复核）。
3. **回滚**：按 `docs/engineering/upgrade-recovery.md`/备份恢复；回滚也需证据与复核；不自动修数据；恢复后 `verify_integrity`。
4. **重跑**：SHA/环境/契约/依赖变化或证据过期 → 受影响项重跑；旧证据保留标 `superseded`，不得覆盖；同一失败连续两次升级支持/工程。
5. **失败处置**：保留现场/日志/收据/审计；记录 PH-345 exit 语义；对外话术不承诺修复时间、不泄露客户/薪资。

## 7. 证据映射（PH-366 V1-OPS 表）

| gate 项 | 汇总来源 | L2 本地（现状） | L3 CI/PG | L4 浏览器 | L5 生产 |
| --- | --- | --- | --- | --- | --- |
| V1-OPS-01 安装 | PH-333 rollup | pass（本地） | not_run | not_run | not_run |
| V1-OPS-02 TLS | PH-333 rollup | not_run（无真实域名） | not_run | not_run | not_run |
| V1-OPS-03 升级/回退 | PH-333 rollup | pass（dry_run/隔离） | not_run | not_run | not_run |
| V1-OPS-04 备份/恢复 | PH-333 rollup | pass（SQLite 隔离）；PG not_run | not_run | not_run | not_run |
| V1-OPS-05 健康/容量 | PH-335 rollup | pass（rehearsal） | not_run | not_run | not_run |
| V1-OPS-06 PG 并发 | PH-334 契约 | 契约 pass；真实 PG not_run | not_run | not_run | not_run |
| M15 | PH-333/335/334 | L1+L2 | not_run | not_run | not_run |

- 现状：`release_ready=false`；真实 L3/L4/L5 全部 not_run；缺口清单（L3 CI/PG/真实 staging 部署、L4 浏览器、L5 生产/客户验收）以 PH-357 回填为准。
- 本映射不新增层级/口径；只引用 PH-332 五层与 PH-366 必需集。

## 8. 当前状态（BASE 3c77653，2026-10-31）

- PH-333/334/335/357/366 均 done：汇总脚本/汇总证据/回填/契约存在（L1），本地/隔离 L2 通过；`release_ready=false`。
- 真实 staging/production 部署、真实 PG 并发、真实 TLS、L4 浏览器、L5 生产/客户验收：**not_run**；汇总验收 gate **blocked**（不得宣称发布就绪）。
- 本契约不执行任何演练；后续执行片按本契约与 PH-366 执行并回填。

## 9. 反例（拒绝）

- 用本地/SQLite/rehearsal 证据冒充 PG/staging/production；dry_run 冒充 execute；配置存在冒充运行。
- 多来源冲突时挑有利项；跨 SHA 合并；删除/覆盖旧证据；未登记缺口/自批豁免。
- 未满足前置仍执行；失败后自动修数据/继续后续破坏性步骤。
- 无命令输出/artifacts/签名却写 pass；证据含客户/薪资/凭据。

## 10. 变更控制与未验证

- 本文冻结后，前置/合并/gate/豁免顺序变更须独立规划并保持与 PH-366/PH-352/PH-345 一致；执行片不得静默削弱 not_run/redaction/gate 规则。
- 未验证：真实 staging/production 部署/升级/恢复/并发、真实 PG、TLS、L3/L4/L5；本片 docs-only，无运行结果。
