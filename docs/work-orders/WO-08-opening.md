# WO-08 期初数据与启用向导

- 级别：S；状态：待执行；依赖：WO-02、WO-03

## 目标

首次启用向导：主体、人员角色、月历、费率口径、审批人、期初截止日，逐项展示 D01–D08 默认假设并保存确认版本；无期初数据时须显式确认「从零开始」（D07）。期初两条路径互斥：明细重建（历史逐笔恢复）或余额承接（累计收入/成本、未清应收应付、预收预付、资金范围形成专用期初事件）；同项目同期间同类别只能选一种，保存覆盖清单；期初未清项不再确认收入或成本；缺失历史现金时显示「从启用日起现金净额」（A21、T23、P09）。

## 输入路径

docs/accounting-rules.md A21、T23；docs/product-requirements.md 2（D01–D08）、5.1、12.2、P04、P09；core/management/commands/bootstrap.py；WO-02、WO-03 产出。

## 不变约束

architecture.md 第 12 节全部；期初导入沿用行级幂等键；覆盖范围冲突为强阻断；历史明细备查不再生成同范围发生额。

## 文件白名单

core/onboarding.py（新建）、core/models.py（追加）、core/migrations/（仅追加；集成时按当前叶节点生成下一号迁移）、core/forms.py（追加）、templates/onboarding/（新建）、tests/test_wo08_opening.py；core/imports.py 追加期初导入类型（仅追加，不改既有三类语义）。

## 禁止范围

pmo/urls.py、core/views.py 既有函数、既有测试与迁移、core/api.py；不引入新依赖。

## 失败例（先写失败测试）

1. 某项目已余额承接历史人工成本 200000 分，再导入同覆盖期间工时拟入成本 → 阻断；得到 400000 即失败（P09）。
2. 期初应收 50000 分收款：只计现金并结清应收，收入不变；再确认一次收入即失败（T23）。
3. 向导未完成 D01–D08 确认即进入正式录入 → 阻断（可用演练模式）。
4. 期初事件在锁账截止日后被修改 → 拒绝（期初事件进入不可变约束）。

## 验收命令示例

```
.venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test tests.test_wo08_opening
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test
```

## 退出条件

命令全过；全量测试无退化；交付 JSON 按 README schema。

