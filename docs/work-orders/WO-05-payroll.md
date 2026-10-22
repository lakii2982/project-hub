# WO-05 月度人工成本池、8 位费率快照与组守恒

- 级别：S；状态：待执行；依赖：WO-04（复用 core/allocation.py）

## 目标

PayrollPool（员工 × 年月 × 版本：成本池组成、有效分钟、来源说明、批准人）；RateSnapshot（元/小时费率 Decimal 8 位小数、不可变、含计算规则版本）；工时成本按取整组（员工 × 月 × 快照 × 成本类别）：先加总未取整分值、组总额 HALF_UP 到分、再最大余数法分到工时行（A04）；锁期后补报建新调整组，不重分配锁定组；费率变更只生新版本与差额，不改历史；闲置与差额留在非项目池并披露（A05、A06、T03–T05）。既有 Timesheet.cost_cents 为旧口径历史数据，按切换日期起用新口径，不重算历史（A23）。

## 输入路径

docs/accounting-rules.md A03、A04、A05、A06、A08、T03–T05、T21；core/allocation.py（WO-04）；core/models.py、core/services.py（现行工时成本路径）。

## 不变约束

architecture.md 第 12 节全部；分母为零或未批准拒绝发布费率；个人费率仍仅 owner/finance 可见；工资付款不产生第二笔人工成本。

## 文件白名单

core/payroll.py（新建）、core/models.py（追加）、core/migrations/（仅追加；集成时按当前叶节点生成下一号迁移）、core/reporting.py（追加差额披露只读）、tests/test_wo05_payroll.py、templates/payroll/（新建）。

## 禁止范围

pmo/urls.py、core/views.py 既有函数、既有测试与迁移、core/api.py、core/allocation.py（缺陷修复除外，须在 deviations 说明并补测试）；不改既有 Timesheet 历史行。

## 失败例（先写失败测试）

1. 费率 100.00000000 元/小时、三条 1 分钟工时：组总额 500 分，行必须 167/167/166；逐行 167 合计 501 即失败（T04）。
2. 有效分钟为 0 时发布费率 → 拒绝，不得按 0 元过账（T05）。
3. 下月改薪后重查上月已批工时成本：必须保持原值（T03）。
4. 成本池 2400000 分、项目 100 小时、内部 20 小时、闲置 40 小时：项目 1500000、内部 300000、未分配 600000；全摊进项目即失败（T05）。

## 验收命令示例

```
.venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test tests.test_wo05_payroll
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python manage.py verify_integrity --json
```

## 退出条件

命令全过；全量测试无退化；交付 JSON 按 README schema。

