# WO-07 发票登记、分配与红冲

- 级别：B（税额分配守恒按 S 级复核）；状态：待执行；依赖：WO-02

## 目标

Invoice（开具/收到：号码、代码或全电票标识、开票方与购方、日期、币种预留、含税/税额、状态、附件）；InvoiceAllocation：一票关联多单、多票关联一单，含税与税额分配均不超原票未分配余额；红字票关联原票与对应业务更正，红票本身不代表已退款；发票登记不自动产生收入、成本、应收或应付（A11）；税额待确定状态可流转为已确认可抵扣/不可抵扣并在开放期做差额更正（T08）。

## 输入路径

docs/accounting-rules.md A11、T07、T08、T28；docs/product-requirements.md 12.1；WO-02 产出（OpenItem）；core/allocation.py（WO-04，税额分配）。

## 不变约束

architecture.md 第 12 节全部；发票号只用于查重不作全局唯一 ID；未接税务查验服务不得展示「已查验」。

## 文件白名单

core/invoicing.py（新建）、core/models.py（追加）、core/migrations/（仅追加；集成时按当前叶节点生成下一号迁移）、tests/test_wo07_invoices.py、templates/invoicing/（新建）。

## 禁止范围

pmo/urls.py、core/views.py 既有函数、既有测试与迁移、core/api.py；不引入新依赖；不实现 OCR。

## 失败例（先写失败测试）

1. 对同一发票分配含税总额超过票面未分配余额 → 拒绝。
2. 登记发票后检查应收与成本：均不得新增（A11 发票不复制原结算）。
3. 红字票登记后检查现金账本：不得新增任何现金行。
4. 税额待确定费用先按含税入成本，确认可抵扣 6000 分后：开放期出现成本 −6000 与税额 +6000 的差额更正，最终成本正确且不重复（T08）。

## 验收命令示例

```
.venv/bin/python manage.py makemigrations --check --dry-run
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test tests.test_wo07_invoices
DJANGO_SETTINGS_MODULE=pmo.settings .venv/bin/python -m django test
```

## 退出条件

命令全过；全量测试无退化；交付 JSON 按 README schema。

