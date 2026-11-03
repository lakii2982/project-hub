# 批次 D 验收执行顺序与证据回填编排（PH-392 冻结）

- 产出：PH-392（docs-only；**不执行**真实 PG/浏览器/生产，不重实现，不改产品代码/契约/门禁）。
- 依据：`batch-d-acceptance.md`（PH-375 矩阵）、`batch-d-runbook.md`（PH-375 执行卡）、`batch-c-release-evidence-contract.md`（PH-366 gate/证据/签名）、`release-evidence-backfill.md`（PH-372 当前 gate 判定）、PH-382/PH-387 已执行证据。
- 目的：冻结 D-01–D-11 的**执行顺序、依赖/可并行性、证据回填顺序、gate 重算时机、真实环境(L3–L5)门槛**；不新增第二套验收口径。
- 当前结论：批次 D **无任何项整体通过**；发布资格 gate 仍 **blocked / release_ready=false**（PH-372）。

## 1. 当前状态基线（BASE c931137）

| 项 | 当前证据 | 判定 | 说明 |
| --- | --- | --- | --- |
| D-01 跨 owner 编排 | PH-382：L2 cash-refund 真实 e2e、9 tests OK；revenue/settlement 仅 patch；L3/L4/L5 not_run | **未通过** | 本地 L2 部分 |
| D-02 税额→costing | PH-387：L2 14 tests OK；L3/L4/L5 not_run | **不通过（false）** | 缺 L3 |
| D-03/04 发票/导入 | PH-338/353 等 upstream proposed；实现片 PH-378 未开工；验收 PH-389 未开工 | **blocked** | 等实现/验收 |
| D-05/06 insights | PH-339/354 等 PH-272；实现片 PH-379 未开工；验收 PH-390 未开工 | **blocked** | 等实现/验收 |
| D-07/08 通知 | PH-340/355 等 PH-301/314/179/183/151；实现片 PH-380/后续 PH-385 | **blocked** | 等实现 |
| D-09 批次 C gate | PH-372 done：**blocked**；PH-377 后续复核 proposed | **blocked** | 见 PH-372 记录 |
| D-10 批次 C 汇总/发布资格 | PH-333/334/335/357 done（L1+L2）；PH-394 终审 proposed | **blocked** | 缺 L3–L5 |
| D-11 多币种后续 | PH-388 ready（汇率反查/对账，本地只读）；PH-318/326 重定/关闭待处理 | **blocked** | 本地可先做 PH-388 |

- 依赖落点：PH-378/379/380 分别等 PH-338/353、PH-339/354、PH-340/355；PH-389/390 为 D-03/04、D-05/06 验收入口；PH-393 为 D-01 L3/L4 补证；PH-394 为批次 D gate 终审；PH-395 为 v1.0 阻塞清单/三方签批。

## 2. 执行波次（DAG，冻结）

### W0：已完成基座（不再重跑）

- D-01 本地 L2（PH-382）、D-02 本地 L2（PH-387）、D-09 初次 gate 判定（PH-372，blocked）。
- 产出：`docs/verification.md` PH-382/PH-387/PH-372 条 + batch-d 行/卡 + `release-evidence-backfill.md`（digest）。

### W1：纯本地可先做（无真实环境，可并行）

| 顺序 | 项 | 命令/证据 | 通过条件 | 备注 |
| --- | --- | --- | --- | --- |
| 1 | PH-388 多币种汇率反查/对账（ready） | scoped tests + 通用命令 + L2 证据 | L2 pass；不冒充 PG/生产 | 与 D-11 关联，不能替代 L3/L5 |
| 2 | PH-377 批次 C gate 后续复核（proposed，deps PH-372 done） | 只读合并 PH-372 记录 + 新证据 | 有真实 L3+ 证据才可改变 blocked | 文档复核不能升级 gate |
| 3 | D-03/04、D-05/06、D-07/08 的**实现片**（PH-378/379/380 + 上游） | 各自 scoped tests + 本地 L2 | 实现 done 且 L2 pass | 上游 PH-338/353/339/354/340/355 未 done 前 blocked |

- 本波可并行；但共享写范围（各 owner）互斥，按任务白名单执行；不写“验收通过”。

### W2：必须真实环境（L3/L4；可分组并行、资源串行）

| 分组 | 项 | 环境 | 可并行的条件 |
| --- | --- | --- | --- |
| A. D-01/D-02 L3 补证 | PH-393（D-01 L3/L4）、D-02 真实 CI/PG | CI + 真实 PG；浏览器 1440/390 | 共用 PG/CI job 时串行；浏览器走查可独立 |
| B. D-03/04 验收 | PH-389（发票/导入 L3/L4） | staging + PG + 浏览器 | 与 A 可并行，若 PG 实例独立 |
| C. D-05/06 验收 | PH-390（insights L3/L4） | staging/CI + 浏览器 | 与 B 可并行 |
| D. D-07/08 验收 | PH-380/385 后续 | staging + 浏览器 | 取决于实现完成 |
| E. D-11 多币种 | PH-388 + L3/L5（如需） | CI/生产 | 本地部分不阻塞 |

- 串行点：**DBA/PG 实例**、**浏览器验收人**、**发布窗口**、**gate digest 合并**（同一时刻只允许一个合并记录）。
- 任一 L3/L4 失败 → 该 D 项不通过；保留 artifacts，不自动修数据。

### W3：gate 与发布资格（严格串行）

1. **PH-394 批次 D gate 终审**（proposed；deps PH-372/387/388/389/390/392）：合并 D 批真实证据，按 PH-366 §6.1 必需集判定；核心项不可豁免。
2. **PH-395 v1.0 阻塞清单/三方签批**（proposed；deps PH-372/387/388/394）：仅当 gate 接近可发布时；产品+发布+独立复核签批，执行人不得自批。
3. D-10/D-11 最后完成；发布资格 gate 仍以 PH-366/PH-372 记录为准。

## 3. 证据回填顺序（每项固定 5 步）

1. **执行**：候选 SHA/环境/profile/角色/备份回退点确认；保存命令输出/CI URL/截图/时序日志。
2. **先回填 `docs/verification.md`**：SHA、环境、命令、exit/status、checks 计数、artifacts、L1–L5、缺口、四眼签名（PH-366 §4）。
3. **再更新 `batch-d-acceptance.md` 行 + `batch-d-runbook.md` 执行卡状态**：只写真实结果；未执行保持 not_run/blocked。
4. **更新 `release-evidence-backfill.md` 与合并记录**：按 PH-372 方法多来源取最严；重算 canonical digest；旧 digest/记录保留并标 superseded。
5. **最后通知/更新 ROADMAP/任务状态**：仅在 review PASS 后置 done；不提前置 pass。

- 反例：先写结论后补证据、只更新矩阵不写 verification、跳过 digest 重算、删除旧记录。

## 4. Gate 重算时机（冻结）

必须重算 gate 合并记录/digest 的事件：

1. 任一 required 证据（V1-OPS-01–06、M15、L4、L5）新增/状态变化。
2. 缺口关闭或豁免获批（三方签批）。
3. 候选 SHA、staging/production 环境、PH-366/367 契约或验收命令变化。
4. 证据超过 14 天时效或依赖实现片合入改变适用性。
5. PH-393/PH-389/PH-390 等执行片完成并回填后。

不得触发重算的情况：仅文档措辞更新、仅配置存在、仅演练 L1/L2、无 artifacts 的口头结论。重算后若 required 仍非全 pass → gate 仍 blocked。

## 5. 真实环境门槛（L3–L5）

| 门槛 | 必须真实环境 | 本地/文档不能替代 |
| --- | --- | --- |
| V1-OPS-01/02/03/04 | staging/production 安装/TLS/升级回退/恢复 | 本地 dry_run/SQLite 备份 |
| V1-OPS-05 | CI/部署 health/capacity/backlog facts | rehearsal facts/文档 |
| V1-OPS-06 | 真实 PG 多连接（非 SQLite） | 契约测试/SQLite |
| D-01 L3/L4 | CI PG + 浏览器 | 本地 L2 |
| D-02 L3 | CI PG/真实成本事实 | 本地 14 tests |
| D-03/04/05/06/07/08 | staging + PG + 浏览器 L4 | 本地实现片 |
| D-09/D-10 | 汇总后 gate | 文档/演练 |
| D-11 | 多币种 L3/L5（如适用） | 本地只读 |
| L5 | 生产/试点客户验收 | 任何演练/L2–L4 |

- 本地可完成：L1 代码、L2 scoped tests、治理基线、证据合并/回填、SLO/文档准备；**不能**完成 L3–L5。
- 未执行项写 `not_run`；依赖未 done 写 `blocked`；两者都不得写 pass。

## 6. blocked / not_run / fail 语义

| 状态 | 含义 | 是否阻塞 gate |
| --- | --- | --- |
| not_run | 方法有、环境/证据缺失 | 是（必需项） |
| blocked | 上游实现/契约未 done，无法开始 | 是 |
| fail | 已执行但断言失败 | 是；修复后重跑 |
| pass | 有真实命令/artifacts/层级/复核 | 否（必需项全 pass 才可 gate） |

- 缺口登记：item/expected/actual/reason/owner/due/risk/mitigation；core 项（V1-OPS-03/04/06/M15/L5）不可豁免。
- 重试：失败保留现场；同一失败最多按 runbook 重试一次，仍失败升级；不自动修数据/不跳过后续破坏性步骤。

## 7. 角色与签名

- 执行人：owner/finance/DBA/支持/发布；复核人：独立验收/安全/财务复核（不得同一人）。
- 每项：执行人提交 → 独立复核签名 → digest 绑定；gate 重算后重新签 digest；14 天时效内有效。
- 执行人不得自批豁免/发布；三方签批（产品+发布+独立复核）仅用于非核心缺口，豁免≠pass。

## 8. 当前结论与未验证

- **批次 D 整体不通过**：D-01/D-02 仅 L2；D-03–D-08 blocked；D-09/D-10 blocked；D-11 blocked。发布资格 gate **blocked / release_ready=false**（PH-372）。
- PH-393/PH-394/PH-395 尚未执行；真实 CI/PG、浏览器 L4、生产/客户 L5 全部 not_run。
- 本编排不改变任何实现/契约/门禁；执行顺序/证据回填/gate 重算以本文为冻结口径，后续执行片按实际 BASE 复核。

## 9. 反例（拒绝）

- 先写 pass 再找证据；以配置/文档/演练冒充 L3–L5；SQLite 冒充 PG；本地 facts 冒充生产容量。
- 跳过 verification 直接改矩阵；未重算 digest；跨 SHA 合并；执行人自批；豁免未签批。
- 不按波次/串行点执行（并发写同一合并记录、同时占用 PG/发布窗口）；失败后自动修数据。
