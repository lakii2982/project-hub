# WO-01 正式合同、合同明细与变更单

- 级别：S；状态：待执行；依赖：无

## 目标

建立 Contract、ContractLine、ChangeOrder：一个项目可有多合同；合同明细含业务类型（software/implementation/service）、计量方法（fixed_milestone/time_material/fixed_period/quantity）、币种（首版仅 CNY，字段预留）、含税/不含税/税额、金额上限、履约期间、关联项目与有效版本；变更单记录旧值、新值、生效日、成本/收入/现金影响说明、审批人；批准后形成新合同版本，旧版本只读不可覆盖；未批准变更不计入已批准合同额。项目上的 contract_cents 改为派生展示（当前批准合同明细合计），不双写。

## 输入路径

docs/product-requirements.md 第 6、7.4、8 节；docs/accounting-rules.md A11、A12；core/models.py；core/services.py（金额、审计、幂等模式）。

## 不变约束

architecture.md 第 12 节全部；含税 = 不含税 + 税额（行与单据两级）；计量方法枚举只增不改；所有写经服务层事务并记审计；提交幂等 token 同语义。

## 文件白名单

core/models.py（追加新模型）、core/contracts.py（新建服务层）、core/migrations/（仅追加；集成时按当前叶节点生成下一号迁移）、core/forms.py（追加表单类）、templates/contracts/（新建目录）、tests/test_wo01_contracts.py（新建）。

## 禁止范围

pmo/urls.py、core/views.py 既有函数、既有模板、既有测试、既有迁移、core/api.py、core/ai.py；不得引入新依赖。

## 失败例（先写失败测试）

1. 变更批准后旧合同版本字段被原地修改 → 必须失败（旧版本只读）。
2. 合同明细 含税10600 ≠ 不含税10000 + 税额500 仍保存 → 必须校验拒绝。
3. 未批准变更的金额计入项目已批准合同额 → 汇总必须只含已批准版本。
4. 同一明细同时选 fixed_period 与 time_material 两种计量 → 拒绝（互斥，D06）。

## 验收命令示例

```
.venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test tests.test_wo01_contracts
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test
```

## 退出条件

上述命令全过；全量测试无退化；交付 JSON 按 README schema。阻塞时写明具体缺口（如契约需变更），不得绕道实现。

## 交付 JSON schema

见 docs/work-orders/README.md「交付 JSON schema」。

