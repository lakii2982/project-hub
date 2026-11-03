# 接续开发

1. 读 AGENTS.md，运行 `.venv/bin/python scripts/projectctl.py orient` 与 `python scripts/projectctl.py queue`，
   核对 Git 状态与最近提交，保留其他人的修改。
2. 只看四个活文档：[project/QUEUE.md](project/QUEUE.md)（下一条成果与验收检查）、
   [project/STATUS.md](project/STATUS.md)（现在能做什么/还缺什么）、
   [project/ACCEPTANCE.md](project/ACCEPTANCE.md)（候选验收）、[project/DECISIONS.md](project/DECISIONS.md)（已定决定）。
   流程见 [docs/engineering/light-delivery.md](docs/engineering/light-delivery.md)。
3. 从队列取一条待办成果，一个 PR 交付：代码 + 测试 + 必需 CI + 一条真实演示（UI 用浏览器）。
   不建规划/契约/收口 PR，不新增 `project/tasks.json` 任务（旧台账只读）。
4. 必需检查与独立复核通过后由维护代理自动合并；执行代理不合并、不改保护。每两周一次候选走查并更新 ACCEPTANCE。
   历史规划/验收矩阵在 [docs/archive/v1.0-planning/](docs/archive/v1.0-planning/README.md)，只读历史。

完整规范见 docs/engineering/README.md；交付事实以 main 代码、必需 CI 与真实演示为准。
