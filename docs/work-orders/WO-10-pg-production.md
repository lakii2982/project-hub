# WO-10 生产 PG 并发、锁账与备份恢复验证

- 级别：S；状态：待执行；依赖：WO-02、WO-03、WO-05（验证其并发与守恒语义）；需要 docker 环境，本机无 docker 时由具备环境者执行并回传证据

## 目标

在 compose.yaml 起真实 PostgreSQL 栈：迁移含 append-only 触发器实测 UPDATE/DELETE 拒绝；并发用例实跑（P10/T22：两笔 8000 分并发核销 10000 分应收最多一笔成功；并发审批同一单据；并发锁账与补录）；锁账在 PG 下的期间拒绝与开放期冲销；备份脚本真实执行并在全新环境恢复——恢复后能登录、打开原凭证、verify_integrity 通过、项目报表数字一致；升级与回滚演练记录（应用回滚、数据库不回滚的约束验证）。`check --deploy` 在真实 TLS 与生产参数下重跑。

## 输入路径

compose.yaml、Dockerfile、scripts/backup.sh、scripts/entrypoint.sh、core/migrations/0002_append_only.py、docs/accounting-rules.md A22、A23、T22；docs/architecture.md 第 6、9 节。

## 不变约束

SQLite 通过不作为 PG 证据；并发用例必须用真实多连接（线程或多进程），禁止串行模拟；恢复演练必须使用全新空环境；演练数据与真实参数分离，.env 不入库不入备份包。

## 文件白名单

tests/pg/（新建，可用 TransactionTestCase 或脚本）、scripts/verify_restore.sh（新建）、docs/ops-runbook.md（新建）；compose.yaml 与 scripts/ 仅允许缺陷修复（在 deviations 说明）。

## 禁止范围

core/ 业务逻辑（发现缺陷只记录并开新单，不在验证单里顺手改）；既有测试；不引入新依赖。

## 失败例

1. PG 下并发两笔 8000 分核销 10000 分应收若都成功 → 验证失败（A22/T22）。
2. 直接 SQL UPDATE core_ledgerentry 未被触发器拒绝 → 失败。
3. 恢复后 verify_integrity 报错或审计链校验不通过 → 失败。
4. 备份包内发现 .env 或任何凭据 → 失败。

## 验收命令示例

```
docker compose up -d --build
docker compose exec app python manage.py check --deploy
docker compose exec app python manage.py test tests.pg
scripts/backup.sh backups/pre-upgrade
scripts/verify_restore.sh backups/pre-upgrade
```

## 退出条件

全部步骤的真实输出归档（日志或录屏索引）；并发与恢复用例全过；ops-runbook.md 记录升级/回滚步骤与结果；交付 JSON 按 README schema。本机无 docker 时状态为 blocked，blocker 写明「需要 docker 环境」。

