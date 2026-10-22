# WO-06 资源周工时

- 级别：B；状态：待执行；依赖：无

## 目标

周视图批量填报：一人一周多项目行一次提交；可复制上周的项目与任务结构（不复制实际工时）；行级校验沿用 submit_time 同一服务层；租户可配置最小录入步长（默认 15 分钟）与日异常阈值（默认 1440 分钟），配置留版本；周视图显示容量对比（已填/标准产能），超阈值进入异常提示而非静默通过。不改变 api/v1 既有契约。

## 输入路径

docs/product-requirements.md 5.1、5.2、9.1，D08；core/services.py submit_time；core/forms.py TimeForm；templates/time.html。

## 不变约束

architecture.md 第 12 节全部；批量提交逐行来源键幂等，整批可重放不双写；成员仍只能填本人；暂停/取消项目行拒绝。

## 文件白名单

core/weekly.py（新建）、core/forms.py（追加）、templates/weekly.html（新建）、tests/test_wo06_weekly.py；如加租户配置模型则 core/models.py 追加与迁移 0009_*。

## 禁止范围

pmo/urls.py、core/views.py 既有函数、既有测试与迁移、core/api.py、core/payroll*；不引入新依赖；不改 submit_time 语义。

## 失败例（先写失败测试）

1. 同一周同一批行重复提交：不得产生第二组工时行（逐行来源键幂等）。
2. 某行超过当日阈值：整批不入库、行级错误指到具体行，其余行不偷偷落库（与 CSV 全或无同语义）。
3. 复制上周结构后检查 minutes 字段：必须为空白，不得带出上周实际工时。
4. 步长配置 15 分钟时提交 7 分钟 → 拒绝。

## 验收命令示例

```
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test tests.test_wo06_weekly
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test
```

## 退出条件

命令全过；全量测试无退化；交付 JSON 按 README schema。

