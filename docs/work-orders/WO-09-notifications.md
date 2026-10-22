# WO-09 通知与后台任务

- 级别：A；状态：待执行；依赖：无（WO-02 落地后扩展应收付类提醒）

## 目标

Notification（类型、严重度、接收人、来源对象、已读状态、创建时间）：审批待办、逾期提醒、里程碑到期、锁账检查清单；页面通知中心与未读计数。后台任务框架：Django management command（如 run_tasks）由系统定时器触发，逾期扫描与月度检查为幂等扫描——同一事项不重复产生未读通知；任务失败不影响在线请求；通知只是提醒，不产生任何业务动作。优先复用 outbox/DomainEvent 通道（core/plugin_runtime.py），不引入新的消息中间件。

## 输入路径

docs/product-requirements.md 11、12.3；core/reporting.py alerts_for；core/plugin_runtime.py（事件通道）；core/management/commands/verify_integrity.py（命令模式参考）。

## 不变约束

architecture.md 第 12 节全部；通知按租户与接收人隔离；扫描幂等（来源事项加类型去重）；后台任务只读业务表加写通知，不直接改账。

## 文件白名单

core/notifications.py（新建）、core/models.py（追加）、core/migrations/（仅追加；集成时按当前叶节点生成下一号迁移）、core/management/commands/run_tasks.py（新建）、templates/notifications/（新建）、tests/test_wo09_notifications.py。

## 禁止范围

pmo/urls.py、core/views.py 既有函数、既有测试与迁移、core/api.py；不引入 celery/Redis 等新基础设施；不改 alerts_for 语义（可调用，不复制）。

## 失败例（先写失败测试）

1. 同一逾期计划连续扫描两次：未读通知必须只有一条（幂等去重）。
2. 通知标注已读后重扫：不得复活为新未读。
3. A 公司扫描产生的通知不得出现在 B 公司成员的通知中心。
4. 后台任务中对业务表写操作（绕过服务层）→ 静态检查或运行断言拒绝。

## 验收命令示例

```
.venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test tests.test_wo09_notifications
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test
.venv/bin/python manage.py run_tasks
```

## 退出条件

命令全过（run_tasks 在演练库输出处理摘要且无错误）；全量测试无退化；交付 JSON 按 README schema。

