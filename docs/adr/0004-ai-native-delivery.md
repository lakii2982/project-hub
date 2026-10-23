# ADR-0004：GPT 单一主控与 GitHub 驱动的本地多执行器

日期：2026-10-23。状态：规划决定，具体实现按 PH-022 至 PH-027。

## 决定

用户只向 GPT 提业务需求和问题。GPT 负责预计效果、任务拆分、调度、整合与验收；GitHub 承载业务记录、PR、CI 和版本证据；本地受控调度器管理执行器、隔离、队列与恢复。Kimi Code、Claude Code、OpenCode、ZCode、DeepSeek Harness 均为目标 worker 适配器，不替代 GPT 主控。

工程契约继续以可信 BASE 上 tasks/spec 为权威；Projects/Issue 不获得自我授权能力。研发调度代码位于 tooling/ai_delivery，独立于产品业务 core、个人 MCP 与插件 SDK；不增加 Django 业务表或消息中间件。GPT 保留现有本地订阅登录方式，不新增 GPT API 费用，不改用户全局工具配置。

## 备选与取舍

- 单靠 GitHub Actions：适合验证，不能自动提供本机 Agent 会话、权限隔离和进程恢复；不把公开 PR 直接接入持有本机凭据的 runner。
- 每种 CLI 自建流程：会分散事实和验收；采用能力适配协议和统一状态机，承担不同输出/取消语义的适配成本。
- 把 GitHub 当唯一任务数据库：会破坏已有 BASE 契约保护；保留业务记录与工程许可各自唯一权威，以 revision 和关联 ID 同步。
- 一次实现整个平台：契约与真实工具能力尚有缺口；先只读探针和 GitHub 桥并行，再冻结执行、主控与集成薄片。

## 后果与范围

需要维护本地服务与 CLI 版本兼容；五种工具的真实可用性逐一演练。worktree 不替代沙箱，模型完成文本不替代测试，人类业务授权不由模型生成。需求/缺陷到发布的关联可被追溯，跨会话以仓库、队列和 GitHub 恢复。

完整协议、状态、信任边界和验收见 [AI 原生交付契约](../engineering/ai-native-delivery.md)。
