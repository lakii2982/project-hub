# WO-02 结算项、应收应付未清项与核销

- 级别：S；状态：待执行；依赖：WO-01

## 目标

Settlement（由合同明细与已批准验收生成草稿，人工确认生效，来源键幂等，重复生成命中同键）；OpenItem（应收/应付：总额、币种预留字段、税额、来源、到期日、质保与争议属性）；Allocation（核销：同租户同交易对手，金额不超收付款可用额与未清额，并发只允许合法余额内一笔生效）；解除核销只恢复双方余额，不产生现金事件。未清额按 A13 公式派生，禁止直接改余额。账龄只对剩余未清额计算。

## 输入路径

docs/accounting-rules.md A12、A13、A22、A25、T10、T22；docs/product-requirements.md 10.2、P10；core/services.py（锁、幂等、审计模式）；WO-01 产出。

## 不变约束

architecture.md 第 12 节全部；同一结算项不得因开票/收入确认重复建应收；核销不创造第二条现金流水；超收进预收/未分配，不做负应收。

## 文件白名单

core/models.py（追加）、core/settlement.py（新建）、core/migrations/（仅追加；集成时按当前叶节点生成下一号迁移）、core/reporting.py（追加只读汇总）、tests/test_wo02_settlement.py、templates/settlement/（新建）。

## 禁止范围

pmo/urls.py、core/views.py 既有函数、既有测试与迁移、core/api.py、core/plugin_runtime.py；不引入新依赖；不实现收入确认本体（A12 事件模型只留接口与注释）。

## 失败例（先写失败测试）

1. 两笔并发各 8000 分核销同一 10000 分应收：若都成功即失败；最终未清必须为 2000 分（T22/P10）。
2. 核销额超过收付款可用额或未清额 → 拒绝且无孤立核销行。
3. 解除 1000 分核销后检查现金账本：不得新增任何现金行（T10）。
4. 同一 Settlement 来源键重复生成 → 返回已有记录，不新增 OpenItem。

## 验收命令示例

```
.venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test tests.test_wo02_settlement
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python manage.py verify_integrity --json
```

## 退出条件

命令全过；全量测试无退化；verify_integrity 扩展覆盖新对象且通过；交付 JSON 按 README schema。

