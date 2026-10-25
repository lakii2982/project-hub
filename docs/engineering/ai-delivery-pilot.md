# 四 CLI 真实 GitHub 闭环试点

## 直接使用

GPT 在当前对话中做总控，调用本机原生 Kimi Code、OpenCode、DeepSeek Harness（DSH）和 Claude Code。最短路固定为：

**GitHub ready 任务 → 分范围运行 → assemble 合件 → verify 实测 → 另一 CLI review → pr → 当前 8 项 required CI 全绿 → 总控在现有授权内明确调用 merge。**

不需要新组件、常驻进程、网页、消息中间件或额外模型 API；不购买额度，不改全局认证，不发布或部署。关闭对话后 GPT 不会继续工作。当前真实文件沙箱只在 macOS 验证，其他操作系统后端明确不支持。

完整安全约束见 `local-team.md`。Issue 文本和标签只是资料，开工权来自固定 BASE 中的 ready 契约；模型输出不是审批。worker 不持 GitHub/SSH 凭据，提交、PR 和合入只由主控执行。

## 主控最小命令

本机已实测默认发现四个客户端及各自现有认证引用，无需额外配置。下面由 GPT 总控执行，`SOURCE_ID` 使用 `assemble` 返回的 ID。只有要覆盖路径或已有模型选择时才使用可选的 `--config LOCAL`，文件只放本机引用，不放密钥、不提交仓库；无需手工构造请求或许可。

以下以本轮固定任务和 run 为例；已完成任务不要重复执行。GPT 对新任务仍使用同一命令形状，并先确认其为 ready。

```sh
python -m tooling.ai_delivery doctor
python -m tooling.ai_delivery tasks

python -m tooling.ai_delivery run PH-104 --agent kimi_code \
  --scope tooling/ai_delivery/delivery/verification.py --execute
python -m tooling.ai_delivery run PH-104 --agent dsh \
  --scope docs/engineering/ai-delivery-delivery.md --execute
python -m tooling.ai_delivery run PH-104 --agent opencode \
  --scope tooling/ai_delivery/delivery/receipt.py --execute

python -m tooling.ai_delivery assemble \
  5645cce6-75e5-42e2-a945-5e63d8b17030 \
  376cb7bd-0221-4e85-8be4-04bec341dc01 \
  152b0b10-b943-467d-8560-5c8a3b901ac5
python -m tooling.ai_delivery verify SOURCE_ID
python -m tooling.ai_delivery review SOURCE_ID --agent claude_code
python -m tooling.ai_delivery pr SOURCE_ID
# 当前8项检查通过后，总控在本轮用户授权内明确调用合入。
python -m tooling.ai_delivery merge SOURCE_ID
```

`run` 不加 `--execute` 只看计划，不调用模型或远端写。可重复 `--scope` 指定多个互斥文件范围；并行只用于不重叠的开发/测试范围，全局最多三个运行。`assemble` 冲突即停止，不能覆盖差异。任何最终差异变化都会使旧验收和复核失效。

`verify` 在无凭据、禁网的干净副本中运行契约内实际验收，不能替换成版本号、帮助、mock 或 worker 自报。`review` 必须选另一 CLI，返回结构化 PASS 或具体阻断；普通退出 0 不算通过。`pr` 默认停在等待 CI，`merge` 会重新核对最终 head、最新 BASE、当前 8 项 checks 和讨论，不沿用旧结论。

## 状态、取消与恢复

```sh
python -m tooling.ai_delivery status
python -m tooling.ai_delivery status 5645cce6-75e5-42e2-a945-5e63d8b17030
python -m tooling.ai_delivery cancel RUN_ID
python -m tooling.ai_delivery recover
```

`RUN_ID` 使用待取消运行实际返回的完整 ID。`queued/claimed/running` 表示排队、领取、运行；`completed` 只表示结果可收集，不等于验收通过。`failed/timed_out/cancelled/interrupted` 分别是失败、超时、已取消、已中断；`config_required` 表示配置或隔离不足。

`quarantined` 表示进程身份或存活状态不确定，继续占槽并锁定范围，不能强行解锁。取消会核对进程身份并终止整个进程组；恢复不会擅自重跑，仍存活或身份不明就保持隔离，确认退出后才释放。需要重试时由主控建立新 attempt，不任意续接旧会话。单次调用上限 15 分钟，每环节最多两次；短暂网络失败可合理重试一次，鉴权失败立即停用该 CLI。

## 本轮真实发现

- **需求 PH-104**：GitHub Issue #24、PR #38，已合入提交 `307f69d4be5c8fdbccd15f9758e0327e490ff358`。Kimi 编写验证模块，DSH/DeepSeek 编写文档，两者进程实际重叠 122 秒；OpenCode 另完成小范围 receipt helper。Claude Code 使用既有 GLM 配置做独立复核；CLI 名称不冒充实际模型名称。
- **缺陷 PH-107**：Issue #32，在 PH-104 之后由新链路修复已知审计问题。Claude Code/GLM 编写代码，DSH/DeepSeek 编写测试，实际并行 325 秒；Kimi 做代码复核。[PR #39](https://github.com/lakii2982/project-hub/pull/39)已通过当前8项检查并正常合入`dcbaed8e5e5f08e32d1c1df03b347ec340e32542`；最终隔离验收和Kimi完整独立复核均PASS，精确摘要与链接见证据文件。
- **失败保留**：OpenCode `069d786a` 大草稿超时不是通过；总控修正后只采纳可用部分，小范围 receipt helper 的成功是另一条独立证据。DSH 长复核超时同样不是通过，不能用其他结果替代。
- **取消演练**：OpenCode 临时 run `811ac6d4` 越出分配范围后被取消，独立 `recover` 调用确认保持 `cancelled`；该演练不破坏真实分支，也不计作成功开发。
- Issue #32实际重开事件34182647370后，18项探针回归全部通过；已done任务仍拒绝执行，再以事件34182697950关闭，没有制造代码回归。真实运行与公开摘要由整合者汇总到 `tests/delivery_e2e/pilot-evidence.json`；自动测试不触发模型或远端写，原始日志和本机私有配置不公开。

这些结果只证明上述公开演练动作，不代表客户接受、生产部署、无人值守运行或所有环境均受支持。

最终全件复核曾因Claude读取未改业务与历史而达到900秒上限，不计通过。现有入口增加差异优先、具体问题才查依赖、追加文档只看变化段及40次工具调用建议预算；进程deadline仍由执行器强制。提示调整后须重验新摘要，再由DSH独立复核；最终证明以PR回执为准。
