# 运维 runbook 与批次 C 可执行验收脚本契约（PH-345 冻结）

- 产出：PH-345（docs-only；本文件只冻结脚本契约，不实现脚本、不执行部署/恢复/并发，不记录任何“通过”）。
- 对齐：`docs/v1.0-development/batch-c-acceptance.md`（PH-332）五层证据、M15/V1-OPS-01–06；实现片 PH-333/334/335。
- 原则：低层证据不得冒充高层；`not_run`/`blocked`/`skipped` 如实标注；不写生产数据、不包含真实客户/凭据；脚本默认 dry_run/只读，执行态需显式参数与角色确认。

## 1. 通用脚本契约（所有 V1-OPS 脚本）

### 1.1 调用与角色

| 项 | 冻结 |
| --- | --- |
| 调用方式 | `python scripts/ops/<script>.py --mode dry_run|execute --env <profile> --out <evidence.json>`；具体脚本由 PH-333/334/335 实现，本文件不创建 `scripts/ops/*` |
| 默认模式 | `dry_run`；只读检查，不写业务/生产数据 |
| 执行角色 | 发布负责人（execute）、DBA（DB/恢复/并发）、财务复核人（财务口径核对）、支持负责人（健康/积压）、安全负责人（TLS/密钥）；双人复核 destructive/execute |
| 环境前置 | 真实目标环境 profile 必须声明：OS/容器版本、应用 SHA、DB 版本/连接数、域名/TLS、备份位置、数据量与脱敏声明 |
| 禁止 | 不写生产业务事实、不改账本/审计、不删除备份、不把真实客户/工资/凭据写入证据；不得以 SQLite 冒充 PG 并发 |

### 1.2 证据 envelope（JSON，写入 `--out` 并由报告引用）

```json
{
  "script": "ops_upgrade",
  "contract_version": 1,
  "mode": "dry_run",
  "profile": "staging|rehearsal|production",
  "app_sha": "<git sha>",
  "started_at": "<ISO8601>",
  "finished_at": "<ISO8601>",
  "exit_code": 0,
  "status": "pass|fail|not_run|blocked",
  "checks": [{"id": "V1-OPS-03.1", "status": "pass|fail|not_run|blocked", "expected": "...", "actual": "...", "evidence_ref": "..."}],
  "artifacts": [{"kind": "log|sql|hash|screenshot|ci_run", "path_or_url": "...", "sha256": "..."}],
  "redactions": ["password", "token", "payroll", "customer"],
  "notes": "..."
}
```

- 证据落点：`docs/verification.md` 追加条目 + 报告引用 `artifacts`；不得只写“脚本存在”。
- 金额整数分、日期/版本/来源可追溯；日志脱敏后保留。

### 1.3 退出码（冻结）

| exit | 含义 | 处置 |
| --- | --- | --- |
| 0 | 全部断言通过（L2/L3 或对应层） | 记录证据；高层仍须单独证据 |
| 2 | 前置缺失（无环境/无权限/无备份/未实现） | `status=not_run`，不得写 pass；登记 blocker |
| 3 | 断言失败/口径不一致 | `status=fail`；停止后续 destructive 步骤；保留现场 |
| 4 | 环境/连接/权限异常 | `status=fail`；重试一次后升级支持；不得盲目重试破坏性步骤 |
| 5 | 安全检查拒绝（生产/敏感/未授权 execute） | 立即停止；记录拒绝原因；不绕过 |

### 1.4 幂等与失败处置

- dry_run/检查类可重复执行，输出追加而非覆盖；evidence 以 `started_at+script+profile` 去重。
- execute 类必须幂等/可恢复：升级先备份、恢复只在隔离库、并发脚本只读；失败不得倒放账本或删除备份。
- 失败时保留日志/快照/哈希，按升级回退与恢复契约处置；不自动“修数据”。
- 同一 profile 同一 SHA 的 evidence 可复用；SHA/环境变化必须重跑，不得用旧证据冒充新版本。

## 2. V1-OPS-01–06 脚本契约

### V1-OPS-01 安装与诊断（PH-333）

| 项 | 冻结 |
| --- | --- |
| 脚本 | `ops_preflight`（安装前诊断，只读） |
| 输入 | profile、OS/Compose 版本、目标 SHA、端口/磁盘/内存、env/密钥引用（不读明文值） |
| 前置 | 空机或隔离环境；应用/DB 不暴露公网；版本固定 |
| 输出/证据 | 环境检查、版本、端口/磁盘、迁移列表、健康检查；`status` 按结果 |
| 通过判据 | 全部前置满足且健康检查 200；失败安全重试记录 |
| 失败处置 | exit 2/3/4；不继续部署；记录诊断包（脱敏） |
| 当前状态 | 未实现（PH-333）；`not_run` |

### V1-OPS-02 TLS/安全（PH-333）

| 项 | 冻结 |
| --- | --- |
| 脚本 | `ops_tls_probe`（只读） |
| 输入 | 域名、证书链、代理边界、Cookie/HSTS 期望 |
| 输出/证据 | TLS 握手/证书到期/续期演练、安全头、错误配置拒绝结果 |
| 通过判据 | 证书有效/可续期、HSTS/Cookie 边界正确、错误配置拒绝 |
| 失败处置 | exit 3/4；不贴配置冒充通过 |
| 当前状态 | 未实现（PH-333）；`not_run` |

### V1-OPS-03 升级与兼容回退（PH-333）

| 项 | 冻结 |
| --- | --- |
| 脚本 | `ops_upgrade`（`--mode dry_run|execute`） |
| 输入 | 冻结基线 SHA、候选 SHA、迁移列表、备份证据、冒烟命令 |
| 前置 | 备份完成且校验；迁移只追加；回退仅结构兼容 |
| 输出/证据 | 升级前后 SHA、迁移应用列表、冒烟结果、回退演练记录 |
| 通过判据 | 迁移成功、冒烟通过、失败可恢复、不倒放账本 |
| 失败处置 | 停止升级；按 `upgrade-recovery.md` 回退；保留备份/日志 |
| 当前状态 | 未实现（PH-333）；`not_run` |

### V1-OPS-04 备份与恢复（PH-333）

| 项 | 冻结 |
| --- | --- |
| 脚本 | `ops_backup_verify`、`ops_restore_rehearsal` |
| 输入 | PG 连接（只读/隔离恢复库）、附件/对象存储位置、保留策略、RPO/RTO 目标 |
| 前置 | 快照一致性（DB+附件同点）、隔离恢复环境、权限最小化 |
| 输出/证据 | 快照/哈希清单、恢复命令、登录/报表/凭证/附件核对、RPO/RTO 实测 |
| 通过判据 | 隔离恢复成功且核对一致；RPO/RTO 达标；禁止只凭“备份任务存在” |
| 失败处置 | exit 3/4；保留现场；不得在生产库演练恢复 |
| 当前状态 | 未实现（PH-333）；`not_run` |

### V1-OPS-05 健康/容量/积压（PH-335）

| 项 | 冻结 |
| --- | --- |
| 脚本 | `ops_health_capacity`（只读） |
| 输入 | profile、健康端点、任务/队列、磁盘/备份失败、容量阈值、基线数据量 |
| 输出/证据 | 健康输出、积压/磁盘/失败记录、基线环境与数据量、告警可操作性 |
| 通过判据 | 健康项通过、积压/磁盘在阈值内、告警可诊断；日志不含密码/令牌/薪酬 |
| 失败处置 | exit 3/4；不静默重试；登记容量/积压 blocker |
| 当前状态 | 未实现（PH-335，等 PH-333/334）；`blocked` |

### V1-OPS-06 PostgreSQL 多连接安全与并发（PH-334）

| 项 | 冻结 |
| --- | --- |
| 脚本 | `ops_pg_concurrency`（只读/隔离，真实 PG） |
| 输入 | PG 版本/连接数、测试 org/数据（脱敏）、并发用例清单 |
| 用例 | 双审批、超核销、退款、分摊、期初、锁账、发票红冲、汇率快照竞争各两连接时序 |
| 输出/证据 | PG 版本、连接数、一胜一拒、无半笔、`verify_integrity` ok、时序日志 |
| 通过判据 | 每用例一胜一拒/幂等，无半笔；SQLite 结果不作为并发证据 |
| 失败处置 | exit 3/4；保留时序日志；不得用串行 SQLite 冒充 PG |
| 当前状态 | 未实现（PH-334，等 PH-330）；`blocked` |

## 3. 与批次 C 验收矩阵映射

| 矩阵项 | 脚本契约 | 实现片 | 当前状态 |
| --- | --- | --- | --- |
| V1-OPS-01 安装/诊断 | `ops_preflight` | PH-333 | not_run |
| V1-OPS-02 TLS/安全 | `ops_tls_probe` | PH-333 | not_run |
| V1-OPS-03 升级/回退 | `ops_upgrade` | PH-333 | not_run |
| V1-OPS-04 备份/恢复 | `ops_backup_verify` / `ops_restore_rehearsal` | PH-333 | not_run |
| V1-OPS-05 健康/容量/积压 | `ops_health_capacity` | PH-335 | blocked |
| V1-OPS-06 PG 并发 | `ops_pg_concurrency` | PH-334 | blocked |

- 本文件冻结的是**契约**，不是运行结果；PH-333/334/335 实现后按本契约产出 evidence，且只有真实执行才能写 L2/L3/L5。
- 生产/客户验收（L5）不在本片范围；不得以演练/文档冒充生产。

## 4. 责任与变更控制

- 脚本实现/参数/证据字段变更需实现片按实际 BASE 复核；本契约不得被实现片静默削弱。
- 安全/凭据/客户数据按最小权限；证据脱敏；发布前由发布负责人/安全负责人复核。
- 本文件不改变 CI 门禁、验收命令或产品行为。
