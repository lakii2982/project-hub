# PH-021 最小独立审阅

- 审阅日期：2026-10-22
- 审阅范围：`project/specs/PH-021.md`、`core/services.py` 的 `update_task`/`lock_company`、`tests/test_task_locks.py`
- 本地环境：SQLite（Django 5.2.18）

## Verdict

`PASS_WITH_UNVERIFIED_PG`

生产补丁本身没有发现阻断。`update_task` 仍由 `@transaction.atomic` 包裹并先执行公司级 `lock_company`；任务查询继续使用行锁，只通过 `of=("self",)` 避免 PostgreSQL 锁定可空外连接侧，未删除锁或吞掉数据库异常。

依赖、版本和审计语义保持：未完成前置拒绝开始、已完成前置允许开始、前置开始后禁止恢复、旧版本拒绝更新，以及成功更新写入审计、拒绝路径不留半成品状态，均由定向测试覆盖。

## Findings

1. 生产 diff 仅对 `update_task` 的 `select_for_update` 增加 `of=("self",)` 和说明注释，未改动模型、迁移、公司锁、事务边界、校验顺序、版本自增或审计调用。
2. `tests/test_task_locks.py` 覆盖无前置、未完成前置、已完成前置和旧版本四条路径；定向 SQLite 测试实际结果为 4/4 通过。
3. 当前工作树中 `tests/test_task_locks.py` 仍是未跟踪文件，提交前必须纳入 PR，否则契约测试不会随修复交付。

## Unverified

- 未执行 PostgreSQL，不能据此确认真实 `FOR UPDATE ... OF self` SQL、行锁和 CI 两个 PG 测试通过。
- 未执行容器 seed/重启/备份/恢复链路，相关验收仍需真实运行证据。
