# GitHub 驱动本地团队：主控入口

当前 GPT 对话负责拆分、分派、整合和验收。GitHub 承载需求、任务契约、PR 与 CI；本机的 Kimi Code、OpenCode、DeepSeek Harness、Claude Code 执行范围明确的工作。此阶段交付前台命令，不会安装常驻服务或开机启动项。

在仓库目录使用：

```sh
python -m tooling.ai_delivery doctor
python -m tooling.ai_delivery tasks
python -m tooling.ai_delivery run PH-104 --agent kimi_code
python -m tooling.ai_delivery run PH-104 --agent kimi_code --scope tooling/ai_delivery/delivery/verification.py --execute
python -m tooling.ai_delivery status RUN_ID
python -m tooling.ai_delivery cancel RUN_ID
python -m tooling.ai_delivery recover
python -m tooling.ai_delivery assemble RUN_A RUN_B
```

`run` 默认只输出计划，不联网、不入队、不调用模型。主控明确使用 `--execute` 才执行；一个前台调用等待该客户端结束。并行时主控分别调用互斥 scope，持久队列全局最多占用三个槽位。执行后重新检查契约与完整范围；模型进程完成不代表任务通过验收。

`--repository` 指定仓库，`--json` 输出机器可读结果，`--config` 指定本机配置；可放在子命令前后。默认允许本仓库并按实际安装路径发现客户端。配置可放在当前系统用户主目录的 `.project-hub-agentd/config.json`，只含 `repositories` 和 `agents`；客户端配置为 executable/model/provider/base_url/runtime_paths/auth_paths 等本机引用，不接受任意命令或令牌值。不要把本机配置提交到 Git。原有客户端配置不会被覆盖，模型仅获得自身所需认证。

任务、依赖、文件白名单、spec 与验收命令来自已允许仓库的固定 `origin/main` 对象；只有 ready/in_progress/in_review 且前置完成、有关联 Issue 的任务可执行。Issue 文字、标签与模型输出均不能扩大权限。契约摘要不含 owner/status/handoff，但执行前后会另外核对任务状态。客户端只读相关来源，分段读取和编辑，避免单次输出超过固定上限。

每次明确执行都有 UUID、冻结 BASE/契约、独立 Git worktree 与持久事件。相同 UUID 与相同参数不会重复调用模型；改变参数或缺少完整契约记录会拒绝，已完成记录再次返回前也会核对当前差异。`status` 列出本机目录和状态，不公开原始日志或提示词。取消通过已证明的进程身份终止；身份不确定时保留 quarantine、槽位和文件范围，恢复不能猜测进程已死。

`assemble` 只接纳同仓库、BASE、契约且 scope 互斥的已完成 run，冲突时停止。它创建新的集成目录并记录各来源；不会冒充一个额外模型运行。主控可以在冻结的完整任务范围内补接线、报告和交接，任何改动都会改变最终摘要。

完整差异按原始字节、Git 模式和链接文本核对，包含未跟踪、忽略、删除及重命名两端；文件与目录相互替换也有反例。只有固定普通编译缓存文件被排除，同名目录或链接仍检查，越界链接拒绝。后续验收从 BASE 加已核对差异重建干净副本，不带客户端缓存。

PH-104 继续增加隔离验收、另一客户端的只读独立复核、PR 和合入门禁。当前不能用 `run` 的进程状态宣称这些门禁已经通过。本轮真实文档试跑一次因输出过大失败，一次因 worker 越过分配范围取消；失败如实保留，不计作成功开发。自动化测试与真实演练结果见 `project/reports/PH-103.json`。
