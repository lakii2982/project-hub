# 参与开发

本文是入口；可执行的详细规范在 [docs/engineering/](docs/engineering/README.md)。开始任何任务前，先读 README.md、docs/delivery-status.md 和任务对应的契约文档。

## 基本规则

- 当前为 0.1 工程验证版。需求文档描述目标，不能把目标写成已实现；当前真实状态以 docs/delivery-status.md 为准。
- 分支模型：`main` 主干加短分支 `feat/PH-001-x` / `fix/` / `hotfix/`，一切经 PR 合入，合并前当次 CI 必须实际通过。版本与发布见 [releases](docs/engineering/releases.md)。
- 任务生命周期（ready 标准、完成标准、S/A/B 风险分级、何时找用户判断）见 [lifecycle](docs/engineering/lifecycle.md)。
- 多代理并行、任务契约位置（`project/tasks.json` 等）与交接证据要求见 [agent-protocol](docs/engineering/agent-protocol.md)；执行失败按 [recovery](docs/engineering/recovery.md) 矩阵处置。
- 财务与数据不变式、工作单机制、机器验收以 [docs/development-contract.md](docs/development-contract.md) 为准：金额整数分、工时整数分钟、ROUND_HALF_UP、禁 float、账本审计只追加、既有测试只许加强。

## 环境与验收

Python 3.12+，依赖固定在 requirements.txt。交付前实际运行并通过：

```sh
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test tests --verbosity 1
.venv/bin/python manage.py verify_integrity --json
```

涉及 MCP 适配器时另跑 integrations/mcp 的独立测试与 stdio 握手。SQLite 通过不代表生产并发正确；并发与生产口径需要 PostgreSQL 验证。

## 提交与 PR

- 一个 PR 对应一个任务，不夹带白名单外修改；PR 描述写清问题、行为变化、风险级别、验证证据与未验证事项。
- 交付时同步更新 docs/delivery-status.md 与验证记录：分别说明代码存在、自动化通过、浏览器检查、生产验证；检查范围之外的事项明确保留。
- 不把凭据、真实客户数据、工资信息放进代码、日志、截图或外部代理上下文；演练一律用 seed_demo 数据。

## 许可

本仓库采用 [Apache-2.0](LICENSE)。贡献代码按相同许可提供，作者保留相应版权；贡献者须有权提交，不引入雇主、客户或其他项目的专有代码。组件边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
