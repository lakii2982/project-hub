# 健康/容量/积压可观测性基线契约（PH-350 实现，配合 PH-345 V1-OPS-05）

- 实现：`scripts/ops/healthcheck.py`；契约：PH-345 `ops-runbook.md` 通用 envelope/退出码（本片白名单不含 ops-runbook，故 V1-OPS-05 状态更新只记在本文件，未修改 ops-runbook）；本文件记录 PH-350 的输入事实、检查项、阈值、脱敏与证据边界。
- 原则：只读；不触碰生产；真实容量/积压以 CI/部署环境为准，本地/隔离数字不冒充生产容量；缺失 not_run，不猜阈值。

## 1. 调用

```sh
python scripts/ops/healthcheck.py --mode dry_run --env rehearsal \
  --facts /tmp/health-facts.json --out /tmp/health-evidence.jsonl
```

- `--facts`：脱敏后的本地/测试环境事实 JSON（结构见 §2）；缺省或空 → `exit 2/not_run`，不自动探测生产。
- `--mode`：默认 `dry_run`；只有 `--env rehearsal` 可以产生 pass；staging/production dry_run → **not_run/exit 2**（本地 facts 不能代表真实容量），execute → **exit 5 blocked**。
- `--out`：append-only JSONL evidence envelope（PH-345 字段：script/contract_version/mode/profile/app_sha/started_at/finished_at/exit_code/status/checks/artifacts/redactions/notes）；按 script+profile+started_at 去重。
- 退出码：0 全部检查通过；2 前置/事实缺失 not_run；3 检查失败；4 环境异常；5 安全拒绝。证据只记录 status，不把配置存在当运行证据。

## 2. facts 结构（本地/测试，脱敏）

```json
{
  "db": {"reachable": true, "migration_pending": 0, "connections": 5, "max_connections": 100},
  "cache": {"reachable": true, "backend": "redis"},
  "queue": {"worker_alive": true, "depth": 0},
  "disk": {"free_percent": 50},
  "backup": {"age_hours": 2},
  "capacity": {"largest_table_bytes": 1073741824, "slow_query_count": 0, "attachment_bytes": 1000},
  "backlog": {"queue_depth": 0, "reminders": 0, "todos": 0, "unsettled": 0},
  "perf": {"samples_ms": [10, 20, 30], "environment": "local-sqlite"}
}
```

- 事实由采集者/部署环境提供；脚本不读取生产凭据、不连生产库、不爬客户数据。`db.reachable` 必须显式提供：true=pass、false=fail、缺失=not_run，不得默认 true。
- 敏感键（password/token/secret/credential/payroll/customer/private_key/api_key/authorization）递归替换为 `[redacted]`，且不会写入 checks/artifacts。
- 缺失字段 = `not_run`；0 只在真实计数为 0 时使用，不用 0 冒充缺失。

## 3. 检查项与阈值（冻结默认值）

| 检查 id | 判据 | 失败阈值 |
| --- | --- | --- |
| health.db | DB reachable | unreachable |
| health.migrations | 无未应用迁移 | migration_pending > 0 |
| health.cache | cache reachable | unreachable |
| health.queue | worker alive | dead |
| health.disk | 磁盘剩余百分比 | < 10% 即 fail |
| health.backup | 最近成功备份小时数 | >= 24h 即 fail |
| capacity.connections | 连接使用率 | >= 80% 即 fail |
| capacity.tables | 最大表字节 | >= 10GiB 即 fail |
| capacity.slow_queries | 慢查询计数 | >= 10 即 fail |
| capacity.attachments | 附件字节记录 | 缺失 not_run |
| backlog.queue_depth | 队列积压 | >= 100 即 fail |
| backlog.reminders | reminder 积压 | >= 200 即 fail |
| backlog.todos | 待办积压 | >= 200 即 fail |
| backlog.unsettled | 未清积压 | >= 200 即 fail |
| perf.samples/p50/p95 | 本地样本数/P50/P95（nearest-rank） | 无样本 not_run |

- 阈值是**本地演练默认值**，不是生产 SLO；生产阈值由部署/CI 按实际环境另行配置，不能用本地数字宣称生产通过。
- 积压是只读计数；本片不实现 reminder 派生（PH-364/355）或队列 worker，只度量传入事实。
- 性能 P50/P95 仅记录样本环境/数量/值；不猜阈值、不把本地 SQLite 当生产 PG。

## 4. 证据与未执行纪律

- 本地证据落 `/tmp/ph350-evidence/*.jsonl`，报告引用；不提交 runtime/客户数据/凭据。
- 真实 CI/部署环境的数据库连接数、表大小、慢查询、附件、备份、队列积压与页面 P50/P95 未执行，统一标 not_run；由部署/CI 证据另行回填。
- 告警推送/外部平台/自动扩缩容非本片目标；本脚本不发送外部消息。
- 反例：用本地数字冒充生产容量、配置存在当通过、缺失显示 0、敏感值进入证据、staging/production execute 未拒绝。

## 5. 验收

```sh
python scripts/projectctl.py check
python manage.py check
python -m unittest tests.test_projectctl tests.test_governance -v
python manage.py test tests.test_v1_ops_observability --verbosity 1
```

- 契约测试覆盖：envelope、退出码 0/2/3/4/5、缺环境 not_run、积压/磁盘/备份/连接/迁移阈值、脱敏、P50/P95、JSONL 幂等。
- 真实容量/PG/部署环境由 CI/部署另行执行；本片不声明。


## 6. PH-350 状态

- V1-OPS-05 新状态：本地/隔离已实现（`healthcheck.py`）；只有 rehearsal 可 pass；staging/production dry_run=not_run、execute=exit5，真实 CI/部署容量统一 `not_run`。
- 本片只改 `docs/engineering/observability.md`（白名单），未改 `ops-runbook.md`；ops-runbook 的 V1-OPS-05 状态由后续获授权规划/实现片按实际 BASE 同步。

## 7. PH-335 汇总验收（L1+L2 only，release_ready=false）

- 执行承担：PH-350 `healthcheck.py`；本片只做汇总，不重实现健康/容量/积压/性能采集。
- 汇总函数：`healthcheck.rollup_evidence(envelope)` 将本地 PH-350 envelope 聚合为 L1–L5 检查：
  - **L1 pass**：脚本存在并产出 envelope；
  - **L2 pass/fail/not_run**：本地 rehearsal 检查（缺 facts 时 not_run）；
  - **L3 not_run**：CI/PostgreSQL 真实容量、连接、积压、慢查询证据；
  - **L4 not_run**：浏览器 1440/390/键盘全流程证据；
  - **L5 not_run**：生产/客户验收证据。
- `release_ready=false` 恒为 false；本地数字/阈值不得写成生产容量或发布资格；`missing=["L3_ci_pg","L4_browser","L5_production"]`。
- 验收命令：
  ```sh
  python scripts/projectctl.py check
  python manage.py check
  python -m unittest tests.test_projectctl tests.test_governance -v
  python manage.py test tests.test_v1_ops_observability --verbosity 1
  ```
- 证据：scoped 11 项覆盖本地 L1+L2、缺环境 not_run、L3–L5 not_run、release_ready=false；真实 L3/L4/L5 未执行，不在本片声明通过。
- 反例：release_ready=true、L2 not_run 却写 pass、本地 facts 冒充 CI/PG/生产、隐藏 L3–L5 missing。
