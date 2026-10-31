# PH-396 · v1.0 遗留任务对账与关闭（docs-only）

- 里程碑：v1.0.0；风险：S；状态：ready；依赖：无（对账基于可信 BASE 事实）。
- 承接：PH-386/PH-391 规划入口、PH-392 批次 D 执行顺序；本片**只对账/关闭/登记**，不实现、不执行真实环境、不改产品代码/迁移/门禁。

## 目标与范围

对 v1.0.0 剩余 open 任务（当前约 115 项，含 PH-141–207 legacy「唯一主交付」链）逐项对账：映射到已合并实现/契约（列 PR/任务证据），给出结论与精确 gap；输出最终 v1.0 交付清单与阻塞清单（L3/L4/L5、人工签批）。**不做**实现/演练/真实环境执行；不得批量无据关闭。

## 行为（冻结 DoR）

### 1. 对账范围与来源

- 范围：可信 BASE `project/tasks.json` 中 status ∈ {proposed, ready, in_review, blocked} 的任务；重点 PH-141–PH-207 legacy 链、PH-301/314/179/183/151 通知链、PH-272/339/354 insights 链、PH-181/185/289/338/353/362 导入链、PH-318/326 多币种遗留、PH-333/334/335/357/367/372 批次 C 汇总。
- 证据来源（只读）：已合入报告 `project/reports/*.json`、handoff、PR/merge commit、spec 契约、`docs/verification.md`、`docs/delivery-status.md`、`docs/v1.0-development/*`、当前任务依赖图。
- 不得以“文档存在/配置存在/演练 L1-L2”当实现覆盖；覆盖必须有代码/契约/测试/验收证据。

### 2. 三类结论与规则

| 结论 | 条件 | 动作 |
| --- | --- | --- |
| **已覆盖 → 关闭** | 有已合并实现/契约/验收证据直接满足原 DoR；引用具体任务/PR/commit/verification 条目 | 任务置 done 或 cancelled（注明关闭理由/证据/复核人）；不删除历史 |
| **部分覆盖 → 转新片** | 核心能力已交付，剩余范围明确且可独立规划 | 新建薄片（先核号）并写清继承范围/上游证据/白名单；原任务关闭或 superseded 并链接新片 |
| **未开工 → 保留** | 无实现证据 | 保留原状态，写精确 gap（缺什么/依赖谁/DoR 缺项）；不得虚假晋级/关闭 |

关闭规则（硬约束）：
1. 每项关闭必须有**至少一条证据**（合并 commit/PR/报告/verification/契约），并由独立复核人确认；执行人不得自批。
2. 禁止批量无据关闭、禁止“规划槽存在即覆盖”、禁止把 proposed/占位/文档视为已实现。
3. 依赖未 done 的不得晋级；仅依赖全 done 且 DoR 完整才可 ready。
4. 旧任务与新片不得同范围重复；关闭时写明 superseded-by/覆盖证据。
5. legacy 编号（PH-141–207 等）以实际交付为准，不因编号旧自动关闭。

### 3. 输出

1. **对账表**：任务 ID/标题/原 DoR/证据（PR/commit/report/verification）/结论/关闭理由或新片 ID/复核人。
2. **最终 v1.0 交付清单**：已覆盖能力 + 证据索引。
3. **阻塞清单**：L3 CI/PG、L4 浏览器、L5 生产/客户、人工签批/豁免（PH-366/PH-367/PH-372 口径）；每条写 owner/due/mitigation。
4. **任务板状态变更**：done/cancelled/新片/保留；总 count 与编号连续说明。
5. 报告/交接：实际命令输出、独立 S 复核、偏差、未验证项。

## 模型与字段 / 迁移

- 无模型/迁移；仅任务元数据与文档。

## 精确白名单

执行者可写：`docs/v1.0-development/closure-reconciliation.md`、`docs/v1.0-development/*`、`docs/verification.md`、`ROADMAP.md`、`project/tasks.json`、`project/manifest.json`、`docs/delivery-status.md`、`project/reports/PH-396.json`、`project/handoffs/PH-396.md`。禁止改产品代码/脚本/迁移/契约 schema/CI 门禁。

## 验收与反例

```sh
python scripts/projectctl.py check
python -m unittest tests.test_projectctl tests.test_governance -v
```

- 金样例：每项关闭有证据/复核；三类结论齐全；阻塞清单完整；编号/依赖一致；无批量无据关闭。
- 反例：无证据关闭、把文档/占位当实现、关闭后仍依赖该任务、重复新片、执行人自批、改门禁/白名单。
