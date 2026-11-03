# v1.0 规划与验收材料（只读归档）

归档：2026-11-03。**这里的内容是历史，不是开工依据，也不是验收依据。**
现行流程见 [docs/engineering/light-delivery.md](../../engineering/light-delivery.md)、
[project/QUEUE.md](../../../project/QUEUE.md)、[project/STATUS.md](../../../project/STATUS.md)、
[project/ACCEPTANCE.md](../../../project/ACCEPTANCE.md)、[project/DECISIONS.md](../../../project/DECISIONS.md)。

## 为什么归档

旧流程每个切片要 3–4 个 PR（规划入口、契约冻结、实现、收口）、12+ 份重叠的状态/验收/发布文档、
331 条任务和每切片五层验收矩阵，几天没有可用输出。2026-11-03 起改为
"一条需求 → 有序成果队列 → 一条成果 = 一个票据 = 一个 PR"，并把重型规划材料原样移到这里保留历史。
内容零改动（`git mv`），也不再有维护责任；如与当前规范、Git 事实或 CI 冲突，以现行文档为准。

## 归档清单

| 文件 | 原位置 | 内容 |
| --- | --- | --- |
| [batch-c-acceptance.md](batch-c-acceptance.md) | docs/v1.0-development/ | 批次 C 五层证据验收矩阵（PH-332） |
| [batch-d-acceptance.md](batch-d-acceptance.md) | docs/v1.0-development/ | 批次 D D-01–D-11 验收矩阵（PH-375） |
| [batch-d-execution-orchestration.md](batch-d-execution-orchestration.md) | docs/v1.0-development/ | 批次 D 执行编排 |
| [closure-reconciliation.md](closure-reconciliation.md) | docs/v1.0-development/ | v1.0 遗留任务对账与关闭（PH-396） |
| [release-evidence-backfill.md](release-evidence-backfill.md) | docs/v1.0-development/ | 发布资格证据回填与 gate 判定（PH-372） |
| [requirement-trace.json](requirement-trace.json) | docs/v1.0-development/ | 137 条 V1-* 需求到工作包/PH 的追踪 |
| [implementation-plan.json](implementation-plan.json) | docs/v1.0-development/ | 72 片实现计划 |
| [schedule.json](schedule.json) | docs/v1.0-development/ | 依赖拓扑与排期估算 |
| [work-packages.json](work-packages.json) | docs/v1.0-development/ | 62 个工作包结构化目录 |
| [work-package-index.md](work-package-index.md) | docs/v1.0-development/ | 工作包索引 |
| [delivery-status-archive.md](delivery-status-archive.md) | docs/ | 旧交付现状全文（逐项历史） |
| [queue-professional-2026-11-03.md](queue-professional-2026-11-03.md) | project/QUEUE.md | 专业版 1.0 的 30 条有序队列（Q-01–Q-30），2026-11-03 范围重定义后归档只读 |

归档后为保持可导航，仅修正了 `queue-professional-2026-11-03.md` 开头 5 条相对链接的路径，正文内容未改。

`requirement-trace.json` 仍被 `docs/v1.0-development/contracts/validate_contracts.py` 读取用于契约一致性校验，
因此归档路径是该脚本的唯一数据源；契约本身没有归档。

## 复跑旧设计校核（可选）

`docs/v1.0-development/check-design.py` 与 `check-design-cases.py` 是这套数据的只读校验脚本，
数据移走后需要把它们的 `DESIGN` 指向本目录才能复跑。它们不在 CI 中，也不构成授权。

## 不该从这里做的事

- 不要把这里的矩阵、层级、清单当成当前验收要求：当前验收是 [project/ACCEPTANCE.md](../../../project/ACCEPTANCE.md) 的一页清单。
- 不要据此新增规划任务、规划槽或契约冻结 PR。
- 不要用归档里的 "done/pass" 结论证明现在可用；以 `main` 代码、必需 CI 与真实演示为准。
