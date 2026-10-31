# PostgreSQL 多连接并发验收（PH-349 / V1-OPS-06）

- 产出：PH-349（实现探针与契约测试；真实 PG 执行交 CI/部署环境）。
- 对齐：PH-334（并发验收片）、PH-345 `docs/engineering/ops-runbook.md`（evidence envelope、退出码、幂等、双人复核、脱敏、失败处置）、PH-352 `docs/v1.0-development/batch-c-runbook.md`（M12/M15 执行手册）。
- 原则：**SQLite 结果不能作为并发证据**；真实 PG 多连接；不触碰生产数据；隔离 schema/事务；未执行标 `not_run`。

## 1. 运行方式

```sh
# 本地/CI，真实 PG（示例：CI service 或隔离演练库）
DJANGO_SETTINGS_MODULE=pmo.settings PMO_PG_DSN='postgresql://user:pass@host:5432/db?connect_timeout=5' \
  python -m tests.test_v1_pg_concurrency --profile ci --out /tmp/ph349-evidence.json

# scoped 契约/探针测试（有 PG DSN 时跑真实探针，否则 PG 用例 skip 并注明 not_run）
PMO_PG_DSN='postgresql://...' python manage.py test tests.test_v1_pg_concurrency --verbosity 1
```

- DSN 来源：`--dsn` 优先，其次 `PMO_PG_DSN`，再次 `DATABASE_URL`。
- 缺 DSN / SQLite / 非 PostgreSQL → **exit 2（not_run）**，不生成通过结论。
- 连接失败/环境异常 → **exit 4（fail）**，重试一次后升级支持。
- 断言失败（一胜一拒不成立、半笔、死锁无法重试等）→ **exit 3（fail）**。
- 安全检查拒绝 → **exit 5**。

## 2. 探针矩阵（`V1-OPS-06.1`–`.4`）

| id | 断言 | 期望 |
| --- | --- | --- |
| V1-OPS-06.1 | 公司锁串行化 | 两连接竞争同一 advisory lock，第二连接等待第一释放后取得；顺序确定 |
| V1-OPS-06.2 | 幂等收据唯一 | 同一 `org + request_token` 并发插入仅一次成功，另一连接 `UniqueViolation` 回滚 |
| V1-OPS-06.3 | append-only 拒绝 | 守卫表 UPDATE/DELETE 均被数据库 trigger 拒绝（2/2） |
| V1-OPS-06.4 | 死锁检测+重试 | 相反加锁顺序触发 `DeadlockDetected`（SQLSTATE 40P01）；回滚后重试成功，无静默数据丢失 |

- 隔离：每次运行创建随机 `ph349_<uuid>` schema，结束 `DROP ... CASCADE`；所有事务提交/回滚受控；不写业务表、不改已应用迁移。
- 时序日志/失败保留为 artifacts（`kind=log`）；证据不含密码/令牌/薪酬/客户原文（`redactions`）。
- 业务级并发（双审批、超核销、退款、分摊、期初、锁账、发票红冲、汇率快照）沿用既有 owner 测试（`tests/test_v1_settlement_concurrency.py`、`test_v1_cash_concurrency.py`、`test_acceptance_revenue_concurrency.py`、`test_service_period_revenue_concurrency.py` 等）在 PG 上执行；本探针聚焦跨域基础设施语义（锁/幂等/append-only/死锁），不替代业务用例。

## 3. Evidence envelope（PH-345 §1.2）

CLI 输出并写入 `--out`：

```json
{
  "script": "ops_pg_concurrency",
  "contract_version": 1,
  "mode": "execute",
  "profile": "ci|staging|rehearsal",
  "app_sha": "<git sha>",
  "started_at": "<ISO8601>",
  "finished_at": "<ISO8601>",
  "exit_code": 0,
  "status": "pass|fail|not_run|blocked",
  "checks": [{"id": "V1-OPS-06.1", "status": "pass", "expected": "...", "actual": "...", "evidence_ref": "stderr"}],
  "artifacts": [{"kind": "log", "path_or_url": "pg_version", "sha256": ""}],
  "redactions": ["password", "token", "payroll", "customer"],
  "notes": "..."
}
```

- 只有真实 PG 执行才可写 `status=pass`；本地产物无 PG 时为 `not_run`。
- 证据回填：`docs/verification.md` 条目 + report 引用 artifacts；CI PG job URL 作为 L3 证据。

## 4. 当前状态与未验证

- **本地执行（2026-10-31，BASE a5ba2fd）**：无 PG DSN → PG 探针用例 **skipped**（`not_run`）；仅契约测试（envelope/退出码/拒绝 SQLite/不可达 PG exit 4）通过。**不得据此宣称并发正确**。
- 真实 PG 探针由 CI/部署环境执行（CI 若有 PG job 覆盖）；未执行前 `V1-OPS-06` 保持 not_run。
- 未验证：业务级并发在 PG 的逐用例一胜一拒、RPO/RTO（PH-348）、容量基线（PH-350）、生产 L5。
- 安全：不写生产业务事实、不改账本/审计、不删备份；执行需发布负责人/DBA 双人复核。
