# WO-03 预收预付与退款

- 级别：S；状态：待执行；依赖：WO-02

## 目标

Prepayment（客户预收/供应商预付：余额、来源、币种预留）；抵扣单（预收抵应收、预付抵应付，双方余额等额减少，不产生现金与收入成本）；四类退款分别建模：退预收、退预付、已核销款银行退回、折让/退货退款。退款上限 = 实际可退余额 − 已退累计；部分退款可重复至余额为零；退款动作只按其类型影响对应口径，收到退款不自动反向收入或成本（A14、T12、T13）。

## 输入路径

docs/accounting-rules.md A14、T08、T12、T13；docs/product-requirements.md 10.2、P08；WO-02 产出。

## 不变约束

architecture.md 第 12 节全部；解除核销 ≠ 退款；退款必须登记独立真实现金动作；折让先更正应收/收入再退款，不与恢复应收共用同一无分类事件。

## 文件白名单

core/models.py（追加）、core/prepayments.py（新建）、core/migrations/（仅追加；集成时按当前叶节点生成下一号迁移）、tests/test_wo03_prepayments.py、templates/prepayments/（新建）。

## 禁止范围

pmo/urls.py、core/views.py 既有函数、既有测试与迁移、core/api.py；不引入新依赖；不改 WO-02 已交付语义（需要扩展接口时在 deviations 说明）。

## 失败例（先写失败测试）

1. 供应商退未使用预付 10000 分：若成本被减少即失败；结果只能是预付余额减少、现金流入（T12）。
2. 退款累计超过可退余额 → 拒绝。
3. 已核销收款被银行退回 3000 分：必须恢复应收 3000 并登记反向现金，收入不变；只删原收款即失败（T13）。
4. 用「解除核销」流程提交现金退款 → 拒绝（P08）。

## 验收命令示例

```
.venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test tests.test_wo03_prepayments
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python manage.py verify_integrity --json
```

## 退出条件

命令全过；全量测试无退化；交付 JSON 按 README schema。

