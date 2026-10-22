# WO-04 共享费用池与 A04 最大余数分摊

- 级别：S；状态：待执行；依赖：无（建议最先，WO-05 依赖本单工具）

## 目标

core/allocation.py：最大余数法分配（绝对值理论分值向下取整、余数降序、同余稳定行 ID 升序、恢复符号、Σ分配额 = 总额）；含税额分配守恒（先分含税再按同权重分税额、税额不超行含税、分差给有容量行）。SharedCostPool 与 AllocationRun：共享费用先入池，分摊单指定来源、可分配余额、期间、范围与权重（批准工时/人数天/收入/手工）；项目视图只计分配后金额，公司总额只计源一次；重分摊先反向原分配再重建。

## 输入路径

docs/accounting-rules.md A04、A10、T02、T09、T28；core/services.py（事务与审计模式）。

## 不变约束

architecture.md 第 12 节全部；零权重不分配、权重和为零拒绝；严格冲销逐分反向复制，不重算；源池与项目分配不得重复汇总。

## 文件白名单

core/allocation.py（新建）、core/sharing.py（新建）、core/models.py（追加）、core/migrations/（仅追加；集成时按当前叶节点生成下一号迁移）、tests/test_wo04_allocation.py、templates/sharing/（新建）。

## 禁止范围

pmo/urls.py、core/views.py 既有函数、既有测试与迁移、core/api.py、core/payroll*（WO-05 范围）；不引入新依赖。

## 失败例（先写失败测试）

1. 100 分三等权分 A/B/C：必须 34/33/33；任何逐行向上取整得 34/33/34 或 100 分不满即失败（T02）。
2. −100 分同权：必须 −34/−33/−33。
3. 含税 101 分、税额 1 分全可抵扣、A/B 等权：必须含税 51/50、税额 1/0、成本 50/50（T28）。
4. 共享云费 10001 分 A/B 等权：项目成本合计必须 10001，公司总额仍 10001；出现 20002 即失败（T09）。

## 验收命令示例

```
.venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test tests.test_wo04_allocation
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test
```

## 退出条件

命令全过；全量测试无退化；交付 JSON 按 README schema。

