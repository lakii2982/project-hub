# 批次 D：剩余 1.0 缺口验收矩阵

- 产出任务：PH-375（docs-only；本文件只定义验收方法与证据格式，**不代表任何能力已通过**）。
- 基线：见 PH-375 report 的 BASE/HEAD。
- 对齐（同一口径，不新增第二套规则）：
  - 五层证据：`docs/v1.0-development/batch-c-acceptance.md`（PH-332）§1；
  - 执行模板/命令可执行性：`docs/v1.0-development/batch-c-runbook.md`（PH-352）；
  - 演练证据/发布 gate：`docs/v1.0-development/batch-c-release-evidence-contract.md`（PH-366）；
  - 脚本契约/envelope/退出码：`docs/engineering/ops-runbook.md`（PH-345）；
  - 关期/结项专项：`docs/engineering/close-runbook.md`（PH-327）。
- 状态约定：`not_run`（有方法未执行）；`blocked`（上游未交付/依赖未 done）；`pass` 仅在有真实执行证据并回填 `docs/verification.md` 后填写。**未执行项禁止写通过。**

## 1. 批次 D 范围

批次 D 覆盖 1.0 剩余缺口与收尾：跨 owner 编排/税额成本（PH-370/371）、发票批量导入（PH-338/353）、insights 读模型（PH-339/354）、通知中心（PH-340/355）、导入提交（PH-289/362）、批次 C 收尾 gate（PH-335/357/367/372）与多币种后续重定（PH-318/326）。

| 编号 | 切片/任务 | 归属需求（M） | 当前状态 |
| --- | --- | --- | --- |
| D-01 | PH-370 跨 owner 编排执行（已合入） | M08/M09/M10/M12 | 实现 done；**L2 部分执行（2026-10-31）：cash-refund 真实 e2e pass；revenue/settlement 真实 e2e、L3/L4/L5 not_run → D-01 未通过** |
| D-02 | PH-371 税额→costing 开放期成本更正 | M06/M10/M12 | L2 pass；L3/L4/L5 not_run → **不通过（false）** |
| D-03 | PH-338 发票批量/导入后续 | M10/M14 | proposed（等 PH-289；PH-362 契约已 done）→ **blocked** |
| D-04 | PH-353 导入提交拆分 | M03/M14 | proposed（等 PH-181/185/289；PH-362 契约已 done）→ **blocked** |
| D-05 | PH-339 insights 读模型实现 | M12/M13 | proposed（等 PH-272）→ **blocked** |
| D-06 | PH-354 insights 期间预检只读 | M12/M13 | proposed（等 PH-272）→ **blocked** |
| D-07 | PH-340 通知中心实现 | M13 | proposed（等 PH-301/314）→ **blocked** |
| D-08 | PH-355 reminder refs/扫描骨架 | M13 | proposed（等 PH-179/183/151）→ **blocked** |
| D-09 | PH-372 批次 C 收尾证据/gate 判定 | M15/V1-OPS | PH-372 done：**blocked**；PH-377 后续复核 proposed |
| D-10 | 批次 C 汇总/发布资格（PH-335/357/367） | M15/V1-OPS-01–06 | PH-333/334/335/357/372 done（L1+L2）；PH-394 终审 proposed → **blocked** |
| D-11 | 多币种后续（PH-318/326 重定/关闭） | M04/M08/M10 | proposed 重定 → **blocked** |

## 2. 必需证据层（按行适用）

| 编号 | 必需层（最低） | 说明 |
| --- | --- | --- |
| D-01 | L2 + L3 + 财务复核 | 编排执行/引用链；真实 PG 由 PH-349/PH-334 覆盖；L4 页面走查；L5 生产单独 |
| D-02 | L2 + L3 | 税额→成本更正；期间/锁期反例；PG 并发由 PH-349 |
| D-03/D-04 | L2 + L3 + L4 | 批量导入/提交原子性；浏览器上传/错误行/大文件 |
| D-05/D-06 | L2 + L3 + L4 | insights 只读；差异单/工作台；权限/缺口 |
| D-07/D-08 | L2 + L3 + L4 | 通知/扫描去重；无外部推送 |
| D-09/D-10 | L2 + L3 + L5 | 演练/gate；L5 由生产/试点单独验收（不得以演练冒充） |
| D-11 | L1 + L2（先决） | 重定/关闭决策；未重定前不实现 |

- L4 浏览器：1440/390、键盘/焦点、空/错/载/停用态，截图/录屏+步骤+角色；无证据不得判 L4。
- L3 CI（含 PG）：同候选 SHA 的 CI run URL/job；SQLite 结果不作为 PG 并发证据。
- L5 生产/试点：环境/候选 SHA/操作记录/RPO-RTO/客户验收；演练与文档不得冒充。

## 3. D 批验收矩阵

通用命令（实际执行后回填真实输出；未执行标 not_run/blocked）：
```sh
python scripts/projectctl.py check
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test tests --verbosity 1
python manage.py verify_integrity --json
```
| 编号 | 验收点 | 命令/证据（可执行或 blocked） | 通过判据 | 责任角色 | 未验证/现状 |
| --- | --- | --- | --- | --- | --- |
| D-01 | 编排按 PH-320 引用链调用收入反冲/cash 退款/settlement 调整；失败项留引用+核对；不直接写其它 owner | `python manage.py test tests.test_v1_invoice_orchestration tests.test_v1_invoice_orchestration_http --verbosity 1` + 通用命令 + 财务复核输出 | 一胜一拒/无半笔；引用链完整；发票事实不被静默回滚；跨 owner 无直接写 | 财务复核人 + 发布负责人 | **L2 部分 pass（cash-refund 真实；9 tests OK；全量 1310/5F+3E 无新增）；revenue/settlement 真实 e2e/L3/L4/L5 not_run → D-01 未通过** |
| D-02 | 仅 confirmed/not_recoverable 税额可生成开放期成本调整；引用完整、幂等、期间/锁期拒绝 | `python manage.py test tests.test_v1_tax_cost_adjustment tests.test_v1_tax_cost_adjustment_http --verbosity 1`（实际执行：14 tests OK）+ `verify_integrity` fresh DB ok | pending/recoverable 拒绝；跨期/锁期拒绝；重复更正拒绝；金额/来源一致 | 财务复核人 | L2 pass；L3 CI/PG、L4 浏览器、L5 生产 not_run；**D-02 整体不通过（false）** |
| D-03 | 发票批量导入模板/预检/批量登记/分配；重排幂等、全有或全无 | `tests.test_v1_invoice_import`/`_http`（实现后）+ 通用命令 + L4 上传 | 错误行拒绝零半笔；重试幂等；金额守恒 | 财务复核人 + 支持负责人 | **blocked**（PH-289 未 done；PH-362 契约已 done） |
| D-04 | 导入提交持久化/原子提交/冲突回滚；预览不留业务痕迹 | `tests.test_v1_import_submission`/`_http`（实现后）+ 通用命令 | 提交全有或全无；重复 token 幂等；冲突零业务事实 | 财务复核人 | **blocked**（PH-181/185/289；PH-362 已 done） |
| D-05 | insights 差异单/工作台只读；同 case_key 至多一条有效；missing/缺口正确 | `tests.test_v1_insights`/`_http`（实现后）+ 只读零写计数 | 页面/导出/API 一致；空值不假零；不复制表 | 财务复核人 + PM | **blocked**（PH-272 未 done） |
| D-06 | 期间预检只读 API：blockers/warnings/issue_code/refs/digest；零写 | `tests.test_v1_insights_precheck`/`_http`（实现后）+ PH-363 契约核对 | 预检与来源一致；member/跨 org 拒绝；零写 | 财务复核人 | **blocked**（PH-272） |
| D-07 | 通知收件箱：按来源去重、权限过滤、已读/停用；无外部推送 | `tests.test_v1_notifications`/`_http`（实现后）+ L4 | 同事实只一条；越权/跨 org 拒绝；停用历史只读 | 支持负责人 | **blocked**（PH-301/314） |
| D-08 | reminder refs/扫描骨架：键去重、幂等扫描、无外部副作用 | `tests.test_v1_reminders`/`_http`（实现后，PH-355） | 重复扫描不重复；不发邮件/短信/Webhook；权限/停用正确 | 支持负责人 | **blocked**（PH-179/183/151） |
| D-09 | 批次 C 证据回填与 release gate 判定：SHA/层级/缺口/豁免/签名 | `docs/verification.md` 回填 + PH-366 gate 清单 + `projectctl check` | 每证据有 SHA/artifacts/层级；gate 结论与缺口一致；无证据不写 pass | 独立验收复核人 | **blocked**（PH-357/367 未 done） |
| D-10 | V1-OPS-01–06 汇总：安装/升级/恢复/并发/容量证据；发布资格清单 | PH-347/348/349/350 envelope + CI PG + PH-366 gate | 各脚本/探针 pass 且 artifacts 可复核；无 not_run 遗留（除批准豁免） | 发布负责人 + DBA + 安全负责人 | PH-335/357 ready、PH-367 proposed → **blocked** |
| D-11 | 多币种后续范围重定/关闭决策（PH-318/326）与已交付 PH-321/324 的边界 | 规划评审输出（决策记录/任务状态） | 决策有证据、无重复实现、后续片 DoR 完整 | 架构/整合者 | **blocked**（待重定） |

## 4. 退出标准

1. D-01–D-10 每项 required 层证据齐备并回填 `docs/verification.md`；D-11 有明确重定/关闭决策。
2. 无 `not_run`/`blocked` 遗留（除 PH-366 允许且三方签批的非核心豁免；核心项不得豁免）。
3. 发布资格按 PH-366 §6 清单判定；演练证据不得冒充 L5；SQLite 不冒充 PG；dry_run 不冒充 execute。
4. 本矩阵不改变 CI 门禁/验收命令/产品行为；执行前以可信 BASE 的 spec/契约复核。

## 5. 反例（拒绝）

- 无命令实际输出/CI URL/截图/时序日志却写 pass；配置/文档/演练冒充运行/生产。
- 用 SQLite 冒充 PG 并发；dry_run 冒充 execute；演练冒充 L5。
- 证据 SHA 与候选不符、环境/profile 不符、过期未重跑；缺口未登记/豁免自批。
- 证据含密码/令牌/薪酬/客户原文；以本矩阵存在代替实现/执行证据。

## 6. 未验证

- D-01 已执行本地 L2 部分验收（PH-382：cash-refund 真实 e2e pass；revenue/settlement 真实 e2e、L3/L4/L5 not_run；整体未通过）；D-02–D-11 均未实现/未执行（blocked/not_run）。
- 无浏览器 L4、CI L3、生产 L5 证据；批次 D 发布资格仍 blocked。
