# 升级回退与备份恢复演练契约（PH-345 冻结，配合 PH-333）

- 产出：PH-345（docs-only；冻结契约，不执行真实升级/恢复，不记录通过）。
- 对齐：`docs/engineering/ops-runbook.md`（脚本契约/证据 envelope/退出码）、`docs/v1.0-development/batch-c-acceptance.md`（M15/V1-OPS-03/04 五层证据）。
- 原则：先备份后升级；迁移只追加；回退仅结构兼容；恢复只在隔离环境；失败保留现场，不自动修数据/倒放账本。

## 1. 升级前置检查（execute 前必须全部满足）

| 检查 | 判据 | 证据 |
| --- | --- | --- |
| 冻结基线 | 当前生产 SHA/迁移列表已记录 | `git rev-parse HEAD`、`showmigrations` |
| 备份 | DB+附件同点快照、哈希/保留策略校验 | `ops_backup_verify` evidence |
| 迁移兼容 | 只追加、无破坏性 DDL、旧代码可读新结构（回退前提） | 迁移 diff/评审记录 |
| 冒烟清单 | 登录/工作台/关期/发票/现金/结算只读冒烟 | 冒烟命令与结果 |
| 回退决策人 | 发布负责人+安全/财务复核人 | 发布记录 |

## 2. 升级执行时序（`ops_upgrade --mode execute`）

1. 冻结写入/进入维护窗口（按真实 runbook）；记录升级前 SHA。
2. 再次校验备份证据（失败 exit 3，停止）。
3. 应用候选版本镜像/代码；执行迁移；记录迁移列表与耗时。
4. 冒烟：健康检查、登录、关键只读页面、`verify_integrity --json`。
5. 失败：停止对外服务，保留日志/DB 快照，按 §3 回退；不得继续迁移或“修数据”。
6. 成功：记录升级后 SHA、迁移列表、冒烟结果、观察窗口；恢复写入。

## 3. 回退契约（仅结构兼容）

- 触发：迁移/冒烟失败且回退安全（旧代码可读新 schema；无不可逆数据变换）。
- 步骤：停止应用→回退镜像/代码到冻结基线→按需回退迁移（仅允许已评审的向后兼容迁移）→恢复备份（如需要，只在隔离/受控流程）→健康/冒烟→记录。
- 禁止：倒放账本/审计、删除备份、无评审的破坏性降级、用回退掩盖数据错误。
- 证据：升级前后 SHA、迁移列表、失败日志摘要、回退步骤与结果、恢复点；L5 仅在真实生产演练后记录。

## 4. 备份/恢复演练契约（`ops_backup_verify`/`ops_restore_rehearsal`）

| 项 | 冻结 |
| --- | --- |
| 快照 | PG 逻辑/物理备份 + 附件/对象存储同点；记录大小/哈希/权限/保留 |
| 隔离恢复 | 独立 PG 实例/库/卷，不与生产同实例；最小权限账号 |
| 核对 | 登录、关键报表、凭证/附件可读、`verify_integrity --json`、行数/金额抽样 |
| RPO/RTO | 记录目标与实测（备份时点→恢复完成），不达标标 fail/blocked |
| 失败 | 保留恢复日志/快照哈希；不覆盖生产；按 runbook 升级支持 |
| 禁止 | 只证明“备份任务存在”；在生产库上演练恢复；把演练当生产验收 |

## 5. 证据与 not_run 纪律

- 每次演练产出一份 `ops_runbook` evidence JSON（见 ops-runbook.md §1.2），写入 `docs/verification.md` 条目并在报告引用。
- 未执行/环境缺失必须写 `not_run`/`blocked` 与原因；不得写“通过”。
- PH-347 已实现 `scripts/ops/upgrade_drill.py` 的本地/隔离 rehearsal 契约：dry_run 检查备份/迁移只追加/回滚兼容；execute 仅隔离 SQLite（真迁移 + smoke 未执行时 not_run）；production execute 永远 exit 5 拒绝；真实 staging/production 未执行。
- 本文件与脚本契约由 PH-333/PH-347 实现时按实际 BASE 复核；PH-334/335 的健康/并发证据另行产出，不互相冒充。
- 本文件与脚本契约由 PH-333 实现时按实际 BASE 复核；PH-334/335 的健康/并发证据另行产出，不互相冒充。

## 6. 实现状态（PH-348，2026-10-31，V1-OPS-04 首片）

- 已实现可执行脚本：
  - `scripts/ops/backup_drill.py`（对应契约 `ops_backup_verify`）：SQLite 只读快照（`sqlite3.backup`，源库 `mode=ro`）、PG `pg_dump`（客户端存在时；否则 `not_run`）、可选附件树复制/逐文件哈希、`PRAGMA integrity_check`/表行数/schema 哈希/页数、manifest（快照 SHA256/大小/保留/RPO-RTO 目标）、PH-345 evidence envelope/退出码；生产 profile 的 execute 直接拒绝（exit 5），dry_run 零快照。
  - `scripts/ops/restore_drill.py`（对应契约 `ops_restore_rehearsal`）：只从 manifest 读取，校验快照 SHA256；恢复到**隔离空目标目录**（非空拒绝、生产 execute 拒绝），恢复后 `integrity_check`/表行数/schema/附件哈希与 manifest 对比，实测 RPO（manifest.created_at→完成）与 RTO（恢复耗时）并对照目标；备份与源库只读/不变。
- 证据：脚本 envelope 含 script/contract_version/mode/profile/app_sha/时间/exit_code/status/checks/artifacts/redactions；退出码 0 pass、2 not_run、3 fail、4 环境、5 安全拒绝；测试 `tests/test_v1_ops_backup_restore.py`（11 项）覆盖 dry_run 零写、execute manifest/hash/行数、附件、隔离恢复/RPO-RTO/源不变、缺库 not_run、生产拒绝、快照篡改 fail、缺 manifest not_run、幂等重跑、PG 工具缺失 not_run 且不泄露密码。
- **层级别**：本实现只产生 L1（代码存在）+ L2（本地自动化）证据；L3（CI/PG 真实环境）、L4（浏览器）、L5（生产/客户验收）均未执行，必须另行演练并记录，不得用本片本地测试冒充。
- 未实现/边界：真实 PG 备份/恢复（仅当 CI/环境提供 pg_dump/pg_restore 时由 PH-333/CI 执行）、对象存储/异地副本、附件大文件校验、生产恢复（禁止）、安装/升级/并发/容量（PH-347/349/335）。
