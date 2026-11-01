# 接续开发

1. 读 AGENTS.md，运行 `.venv/bin/python scripts/projectctl.py orient`，核对 Git 状态与最近提交，保留其他人的修改。新会话须确认当前分支与远端主线的差异；原本地交接仅作历史线索。
2. 只读 docs/delivery-status.md 的现状头和本轮焦点；先确定谁从哪个入口完成什么操作、怎样验收、何时结束，再复用 ready 任务的 brief/spec。
3. 按 [唯一流程成本规则](docs/engineering/agent-protocol.md#9-按可用成果推进2026-11-01-起生效) 控制最多两条主线；连续支持性 PR 告警时先解除具体阻塞，不再自动开下一规划槽。真实缺口无任务承载时用第9节受限登记入口，先合入登记再实现；不预建规划槽，不绕过 BASE。
4. 实现、实际验证、必要独立复核、报告/交接/状态随同一 PR 交付；保留原有 required CI，合入后不专开收口 PR。检查代码、自动化、浏览器、部署/客户验收各自证据，不以任务数报完成率。

完整规范见 docs/engineering/README.md；任务唯一权威是仓库 tasks 契约（用 brief），Git/CI 决定实现与验证事实。
