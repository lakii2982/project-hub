# 批次 C 演练证据回填与发布资格清单契约（PH-366 冻结）

- 产出：PH-366（docs-only；**不执行**部署/恢复/并发/性能，不改 CI 门禁/产品代码/脚本）。
- 依赖（均 done）：PH-347 安装/升级演练、PH-348 备份/恢复演练、PH-349 PG 并发验收、PH-352 批次 C runbook。
- 承接：PH-357（演练证据回填与验收汇总）、PH-367（部署/恢复/并发汇总验收）；发布资格 gate 的唯一口径来源为本文 + PH-332 验收矩阵 + PH-345 脚本契约，不新增第二套验收规则。
- 原则：**没有可复现证据不得写 pass**；演练不得冒充生产/客户验收；未执行项如实 `not_run`/`blocked`；缺口/豁免必须显式登记与审批。

## 1. 证据来源（冻结）

| 来源 | 脚本/文件 | script 标识 | 覆盖 V1-OPS | 证据格式 |
| --- | --- | --- | --- | --- |
| PH-347 | `scripts/ops/install_drill.py` | `ops_preflight`, `ops_tls_probe` | 01/02 | append-only JSONL envelope |
| PH-347 | `scripts/ops/upgrade_drill.py` | `ops_upgrade` | 03 | append-only JSONL envelope |
| PH-348 | `scripts/ops/backup_drill.py` | `ops_backup_verify` | 04 | envelope + manifest/hash 清单 |
| PH-348 | `scripts/ops/restore_drill.py` | `ops_restore_rehearsal` | 04 | envelope + 恢复核对/RPO-RTO |
| PH-349 | `tests/test_v1_pg_concurrency.py` CLI | `ops_pg_concurrency` | 06 | envelope（`--out` JSON）+ PG 时序日志 |
| PH-350（pending） | `ops_health_capacity`（未实现） | `ops_health_capacity` | 05 | 同 envelope；当前 not implemented |
| PH-352 | `docs/v1.0-development/batch-c-runbook.md` | — | 全部执行方法 | 证据模板/回填格式（本文 §4） |
| PH-332 | `docs/v1.0-development/batch-c-acceptance.md` | — | M01–M15/五层证据 | 验收矩阵/通过判据 |
| PH-345 | `docs/engineering/ops-runbook.md` | — | 01–06 | envelope/退出码/角色/失败处置 |

- 证据落点约定：本地演练输出 JSONL/`--out` JSON 存放于 `runtime/evidence/<date>/<script>-<sha>.jsonl`（由 `.gitignore` 排除，不入发布包）；需要入库的**脱敏摘要**只追加到 `docs/verification.md` 并在 report 引用 `artifacts` 路径/哈希；真实客户/凭据/薪酬不得进入任何证据文件。
- 证据唯一性：同一 `script + mode + profile + started_at` 视为同一次运行；JSONL append-only，去重键重复时保留首次并标 `duplicate_ignored`，不得覆盖历史。

## 2. Envelope 必需字段（冻结，与 PH-345 §1.2 一致）

```json
{
  "script": "ops_upgrade",
  "contract_version": 1,
  "mode": "dry_run|execute",
  "profile": "rehearsal|staging|production",
  "app_sha": "<40-hex>",
  "started_at": "<ISO8601 UTC>",
  "finished_at": "<ISO8601 UTC>",
  "exit_code": 0,
  "status": "pass|not_run|fail|blocked",
  "checks": [{"id": "V1-OPS-03.1", "status": "pass|fail|not_run|blocked",
              "expected": "...", "actual": "...", "evidence_ref": "..."}],
  "artifacts": [{"kind": "log|sql|hash|screenshot|ci_run", "path_or_url": "...", "sha256": "..."}],
  "redactions": ["password", "token", "payroll", "customer", "secret", "key"],
  "notes": "失败/未执行原因与处置"
}
```

- 约束：`app_sha` 必须等于候选发布 SHA（40 hex）；`started_at/finished_at` 必须为 UTC ISO8601 且 `finished_at >= started_at`；`checks[].id` 采用 `V1-OPS-<两位>.<序号>`；任一 required check 非 `pass` → 该脚本证据不得按规定通过。
- 脱敏：`redactions` 至少含 password/token/payroll/customer；证据文件命中敏感原文视为无效证据（fail/blocked，按 §7 处理）。

## 3. exit_code → status 归一化（冻结）

| exit | status | 含义 | 发布资格处理 |
| --- | --- | --- | --- |
| 0 | pass | 全部断言通过 | 可作为对应层证据 |
| 2 | not_run | 前置缺失（无环境/无权限/无备份/未实现） | 缺口登记；不得 pass |
| 3 | fail | 断言失败/口径不一致 | 阻塞发布；修复后重跑 |
| 4 | fail（environment_error） | 环境/连接/权限异常 | 阻塞发布；重试一次后升级；不得盲重试破坏性步骤 |
| 5 | blocked | 安全检查拒绝（生产/未授权/敏感） | 阻塞发布；记录拒绝原因；不绕过 |

- 脚本自带的 status 文案（如 install_drill 的 pass/not_run/blocked）必须归一到上表；同一脚本不同 evidence 状态冲突时，取**最严**（fail > blocked > not_run > pass）并要求重跑，不允许选择性引用有利条目。
- `mode=dry_run` 证据只证明预检/只读检查，不证明 execute 能力；发布 gate 要求 execute 证据（或明确的、经审批的 dry_run 例外，见 §6）。

## 4. 回填到 `docs/verification.md` 的条目格式（冻结）

每次演练/验收追加一个条目（脱敏、可追溯、不可改写历史）：

```markdown
## <任务/演练>（YYYY-MM-DD，BASE <sha>）

- 候选 SHA：<40-hex>；环境/profile：rehearsal|staging|production；执行人角色：<role>；复核角色：<role>
- 脚本与版本：`ops_<name>` contract_version=1，mode=<dry_run|execute>
- 命令：`<实际命令>`（可复现）
- 结果：exit_code=<0|2|3|4|5>，status=<pass|not_run|fail|blocked>；checks pass/fail/not_run/blocked=<n>/<n>/<n>/<n>
- 证据 artifacts：<path_or_url>（sha256=<hash>）；env=<OS/PG/DB 版本>
- 证据层级：L1 <有/无>；L2 <有/无；命令与计数>；L3 <CI run URL/无>；L4 <截图/无>；L5 <生产/试点记录/无>
- 缺口/未执行：<not_run/blocked 项与原因、责任人、处置>
- 判定：<pass|fail|blocked>（与证据一致，不得仅凭脚本文本）
```

- `docs/verification.md` 只记录脱敏摘要与路径/哈希，不贴真实日志内容；完整 JSONL/JSON 留在 `runtime/evidence` 或 CI artifact。
- 条目必须由执行人+独立复核人双人确认（见 §6），并与 report 的 `tests`/`deviations`/`unverified` 交叉引用。

## 5. L1–L5 判定规则（冻结）

| 层 | 证据形态 | 可证明 | 不可证明/不得冒充 |
| --- | --- | --- | --- |
| L1 代码/脚本存在 | 文件路径 + 同 SHA 的 `git show`；脚本可导入/帮助 | 实现存在、可执行入口存在 | 运行正确、数据正确 |
| L2 自动化通过 | 本地测试/演练命令实际输出、计数、envelope（pass） | 该环境下的行为正确 | 其他环境/生产正确 |
| L3 CI（含 PG） | 同候选 SHA 的 CI run URL + job 名；PG 版本/连接数/时序日志 | 该 CI 环境（含 PG service）下的行为正确 | 生产/客户验收 |
| L4 浏览器 | 1440/390、键盘/焦点、空错载停用态截图/录屏 + 步骤 | UI 交互符合验收点 | 生产部署/真实客户 |
| L5 生产/试点 | 环境、候选 SHA、操作记录、RPO/RTO 实测、客户验收记录 | 真实发布/恢复/试点通过 | 不得用演练/文档冒充 |

- 演练（rehearsal/staging）证据属于 L2/L3，不自动升级为 L5；L5 必须单独执行与记录。
- “脚本存在/配置存在/文档已写”只算 L1，不构成 L2+。
- SQLite 结果不作为 PG 并发（L3）证据；dry_run 不作为 execute 证据。

## 6. 发布资格 gate（冻结清单）

发布候选（同一 40-hex SHA）必须逐项满足；任一项缺失/未执行/失败 → **release blocked**，除非按 §6.2 缺口登记与豁免获批。

### 6.1 必需证据集合

1. **代码/治理基线**：`projectctl check` ok；23 项治理测试 OK；`manage.py check` 0 error；`makemigrations --check` 无变更；全量测试失败集不高于基线（8 项既有 macOS 失败，无新增）；新库 `verify_integrity` ok；contract validate/check_fixture pass（如涉及契约）。
2. **V1-OPS-01/02/03（PH-347）**：`ops_preflight`、`ops_tls_probe`、`ops_upgrade` 在候选 SHA 的 execute 证据 `pass`；TLS 真实域名/证书续期；升级/回退演练成功、迁移只追加、失败可恢复、账本不倒放。
3. **V1-OPS-04（PH-348）**：`ops_backup_verify`、`ops_restore_rehearsal` `pass`；PG+附件一致快照、哈希/保留、隔离恢复后登录/报表/凭证/附件核对、RPO/RTO 实测达标；不得以“备份任务存在”通过。
4. **V1-OPS-06（PH-349）**：真实 PG 上 4 个探针/业务并发用例一胜一拒、无半笔、`verify_integrity` ok；PG 版本/连接数/时序日志 artifacts；SQLite 结果无效。
5. **V1-OPS-05（PH-350）**：`ops_health_capacity` 在候选 SHA 的 pass 证据；健康/积压/磁盘/备份失败可诊断；100 人规模关键路径基线（P50/P95/查询数）；日志无敏感原文。**当前 PH-350 未实现 → 该 gate 默认 blocked/not_run。**
6. **M01–M15 矩阵**（PH-332/PH-352）：按各 M 的 required 层证据；M15 需 V1-OPS-01–06 全部通过；未达标项必须在 §6.2 登记。
7. **浏览器 L4**：1440/390、键盘/焦点、空/错/载/停用态；无截图/步骤不得判 L4 pass。
8. **发布包/支持**：版本单一来源、同提交 CI、发布包无 runtime/.env/凭据/客户数据/虚拟环境/备份；发布说明/回滚点/支持与退出约定（V1-OPS-07/08）就绪。

### 6.2 缺口登记与豁免

- 每个 `not_run`/`blocked`/`fail` 必须登记：`item`、`evidence_expected`、`actual_status`、`reason`、`owner`、`due`、`risk`、`mitigation`。
- **默认不得豁免**：V1-OPS-03（升级/回退）、V1-OPS-04（备份/恢复）、V1-OPS-06（PG 并发）、M15 与 L5 核心项。仅非核心 UI/低风险项可申请书面豁免，需**产品负责人 + 发布负责人 + 独立验收复核人**三方批准并记录到期时间；豁免不得由执行人自批。
- 豁免 ≠ pass：发布记录必须写明“带缺口发布”的范围、风险与回滚方案；下一版本必须关闭。
- 证据过期/失效（见 §7）不得用旧豁免延续。

## 7. 双人复核、签名与时间戳

- **执行人与复核人不得同一人**：执行人（发布负责人/DBA/支持负责人）出证据；独立复核人（安全负责人/财务复核人/独立验收人）核对 evidence/artifacts/层级/缺口。
- 签名记录（发布证据文件，脱敏）：`role`、`member_ref`（内部 ID，不写真实姓名/邮箱）、`signed_at`（UTC ISO8601）、`scope`（哪个 gate 项）、`decision`（approve/reject）、`evidence_digest`（§8）。签名只证明复核该证据，不替代 L5 客户验收。
- 时间戳以 envelope 的 `started_at/finished_at` + 回填条目日期 + 签名时间三处一致；冲突时以 envelope 为准并要求重跑。

## 8. 证据失效与重跑规则

- **绑定候选 SHA**：任何证据必须匹配当前候选 `app_sha`；SHA 变化（即使仅文档/配置）→ 受影响证据必须重跑，不得复用旧 SHA 证据。
- **环境/契约变化**：profile/OS/DB/PG 版本、迁移、依赖、脚本 contract_version、验收口径变化 → 相关证据失效重跑；不再适用的证据标 `superseded`。
- **时效**：证据自 `finished_at` 起默认 **14 天**内有效（生产发布前复跑关键项）；超期需重跑或书面重新确认（记录理由与批准人）。
- **失败/阻塞后**：必须修复并重跑受影响脚本；同一 SHA 修复后产生新 `started_at` 条目，旧条目保留不可删。
- **禁止**：挑选有利旧证据、手改 JSONL、用 dry_run 代替 execute、用演练代替 L5、用 SQLite 代替 PG、用配置/脚本存在代替运行。

## 9. 与 PH-357 / PH-367 / PH-333/334/335 的边界

- **PH-357**：按本文 §4/§5 回填 PH-347/348/349（及 PH-350 完成后）证据，输出缺口清单与本契约 §6 的 gate 判定；不重复实现/执行演练。
- **PH-367**：部署/恢复/并发汇总验收前置契约，消费本文 gate 清单，按实际环境执行并回填；不改本文口径。
- **PH-333/334/335**：汇总验收片，按执行片证据（PH-347/348/349/350）签署；不得以本契约/文档代替执行证据。
- 本文不新增验收规则；与 PH-332 矩阵/PH-345 契约冲突时以二者为准并回写本文（独立规划）。

## 10. 当前状态（BASE fe9c257，2026-10-31）

- PH-347 done：L1+L2 本地演练证据（安装/升级/TLS dry_run、生产 execute 拒绝 exit5、隔离 SQLite 升级 migrate）；真实 staging/production、TLS 续期、升级/回退未执行 → `not_run`。
- PH-348 done：L1+L2 本地 SQLite 备份/恢复演练；PG 备份/恢复未执行（工具缺失/未配置）→ `not_run`；L3/L5 未执行。
- PH-349 done：L1+L2 契约测试 + 真实 PG 探针 **skipped（not_run）**；L3（CI PG）待执行。
- PH-350 ready、未实现：V1-OPS-05 `ops_health_capacity` 不存在 → **blocked/not_run**。
- PH-352 done：runbook/证据模板已落地（L1）。
- **发布资格结论：current candidate 不具备发布资格**（V1-OPS-05 缺失、L3/L4/L5 未执行、PG 恢复/并发真实证据缺失）；不得以本文件或演练报告声称发布就绪。

## 11. 反例（一律拒绝）

- 以脚本存在/配置存在/文档已写代替运行证据；无 artifacts/命令输出却回填 pass。
- 用 dry_run 代替 execute；用 rehearsal 代替 production/L5；用 SQLite 冒充 PG 并发。
- 证据 SHA 与候选不符、环境/profile 不符、过期未重跑、删除/覆盖旧 JSONL。
- 缺口未登记或执行人自批豁免；签名缺失/同一人执行+复核。
- 证据含密码/令牌/薪酬/客户原文，或发布包含 runtime/.env/备份/个人配置。

## 12. 变更控制与未验证

- 本文冻结后，证据字段/退出码/门禁项变更须独立规划并按 PH-332/PH-345 对齐；实现片不得静默削弱。
- 未验证：真实 staging/production 演练、PG 备份/恢复、真实 PG 并发、PH-350 健康/容量、L3 CI、L4 浏览器、L5 客户验收；本文为契约，无运行结果。
