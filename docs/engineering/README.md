# 工程规范导航

日期：2026-10-22。本目录是长期、跨 coding-agent 的工程执行规范，面向后续所有参与本仓库的人与代理。规范不重复业务与契约内容：财务与数据不变式、工作单机制、验收命令以 [../development-contract.md](../development-contract.md) 为准，当前实现与验证事实以 [../delivery-status.md](../delivery-status.md) 为准。发生冲突时，不变式与事实文档优先，本目录只规定协作与流程。

## 阅读顺序

1. 新参与者先读仓库 README、delivery-status.md、本页，再按任务类型读对应文件。
2. 接任务前读 [lifecycle.md](lifecycle.md)：任务从需求到交接的完整生命周期、Definition of Ready/Done、S/A/B 风险分级、何时必须找用户判断。
3. 涉及版本、分支、标签、hotfix、回滚读 [releases.md](releases.md)。
4. 使用或调度 coding-agent、处理并行任务与机器事实读 [agent-protocol.md](agent-protocol.md)。
5. 任何执行中异常（脏目录、断会话、限流、迁移分叉、秘密泄漏等）按 [recovery.md](recovery.md) 的矩阵处置。

## 各文件职责

| 文件 | 回答的问题 |
| --- | --- |
| lifecycle.md | 一件事从提出到交付走完哪些环节，每个环节的进出标准是什么 |
| releases.md | 代码怎么分支、怎么编号、怎么发布、出问题怎么回退 |
| agent-protocol.md | 多代理并行时事实以什么为准、写范围怎么互斥、交接证据放哪里 |
| recovery.md | 执行失败了按什么路径恢复，谁接管，什么情况必须停下来找用户 |
| ../development-contract.md | 实现必须遵守的冻结契约、不变式与机器验收（不在这里复制） |
| ../delivery-status.md | 当前到底实现了什么、验证过什么、还缺什么（不在这里复制） |

## 规范的修订

AI 原生研发新增目标见 [ai-native-delivery.md](ai-native-delivery.md) 与 [ADR-0004](../adr/0004-ai-native-delivery.md)：用户与 GPT 对话，GitHub 承载需求/缺陷，五种本地执行器按能力派工。PH-090 规划不表示执行平台已经实现。

- 本目录文件的修改本身按 lifecycle.md 的普通任务执行；修改 S 级相关条款（风险分级、独立复核要求、财务不变式引用）按 S 级处理。
- 规范只写可执行的规则和真实状态。禁止把计划中的工具、未通过的 CI、未发生的发布写成既成事实；机器事实的权威来源见 agent-protocol.md 第 2 节。
- 规范之间不互相复制条款，用链接引用。发现两份文件矛盾时以较新的条款为准，并在同一任务内消除矛盾，不留双份口径。
