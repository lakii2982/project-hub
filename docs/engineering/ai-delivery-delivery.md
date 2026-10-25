# AI 团队交付：验收、独立复核与 PR/合入门控

任务 PH-104；实现 `docs/engineering/local-team.md` 第 5 节。本文只定义主控前台的交付动作和证据边界，不新增常驻服务、开机启动、发布/部署入口、审批 UI 或任意命令入口。worker 不持有 GitHub 凭据，也没有 push、创建 PR 或 merge 的工具；GitHub 写操作只由主控凭据执行。

## 命令与来源

```sh
python -m tooling.ai_delivery verify SOURCE_ID
python -m tooling.ai_delivery review SOURCE_ID --agent kimi_code|opencode|dsh|claude_code
python -m tooling.ai_delivery pr SOURCE_ID
python -m tooling.ai_delivery merge SOURCE_ID
```

`SOURCE_ID` 是本机已存在 run 的 UUID（可以是 `assemble` 后的集成来源）。命令只按该 ID 读取已持久化的 BASE、契约和完整差异，不接受仓库路径、分支、任意 argv 或 shell 片段；ID 不存在、状态不符、来源有歧义时拒绝。`review --agent` 只能是四个已知执行器，且必须不同于来源 run 的 `agent_id`。

首次交付的默认终点是“PR 已创建或更新，等待当前 CI”。`pr` 不隐含 merge；`merge` 是主控在同一用户授权范围内的单独显式动作。没有发布或部署动作。

## 1. verify：隔离的真实验收

`verify` 绑定并重新核对以下事实：

- `source_run_id`、`base_sha`、`contract_digest`、最终 `diff_digest`；
- 任务仍为可交付状态、依赖已完成、范围未扩大；
- 契约仍来自可信 `origin/main` 的固定对象，BASE 与契约未在运行中变化。

验收从 BASE 加已核对差异重建干净副本，不信任 worker 的 HEAD、工作区或自报配置。命令只来自可信 BASE 中冻结的验收白名单（Python unittest/Django/projectctl/`git diff --check` 等固定 argv）。解析时拒绝 shell 元字符、管道、重定向、命令替换、内联代码和未知可执行程序；不使用 shell。

候选源码和测试同样不可信。验收进程必须：

- 不继承任何模型提供方、GitHub、SSH 或产品数据库凭据；
- 禁用网络；
- 拒绝读取主控、其他工作目录和凭据，仅必要运行时可读；只写该副本及其缓存；
- 有 deadline 和日志上限，原始日志只留在本机私有目录。

运行前后都计算最终改动摘要；检查结果必须关联同一个最终 `diff_digest`。摘要变化、超时、非零退出、日志超限或无法确认隔离时，一律不生成完成证明，保留失败/未验证事实。不能用“命令跑过”代替实际成功。

## 2. review：另一 run、另一 agent、只读

`review` 必须创建另一个 run，由另一 agent 在 review 模式执行，评审目录只读，仍允许其自己的私有 home/cache/tmp 写入。复核者拿到契约、最终差异和 verify 证据；除自身提供方的最小运行认证外，不拿 GitHub/SSH 或额外权限，也不能修改被审阅 worktree。

只接受结构化的明确 `PASS` 或具体阻断项；普通自然语言加退出 0、空结果、无法解析、超时或与证据矛盾，都视为未通过。证据至少包含：

```text
run_id, base_sha, contract_digest, diff_digest,
checks, reviewer_run_id, reviewer_agent, result, limitations
```

复核结果必须绑定与 verify 相同的最终 `diff_digest`。任何差异改变都会同时使此前的验收和复核失效；必须重新执行受影响的两个环节，不能沿用旧 PASS。复核者与实现者相同、或复核 run 无法证明独立时，拒绝。

## 3. pr：主控凭据、固定 argv、幂等

创建 PR 前，主控须确认：范围与冻结白名单一致；`verify` 为实际通过；独立 `review` 为 `PASS`；report/handoff 已写入；任务状态已更新；PR 将含唯一 Task 行。

- 只由主控使用固定的 git/gh argv 执行 commit、push 与 PR 创建/更新，不拼接用户输入，不调用 shell，不执行任意命令。
- 分支使用任务的短分支；同一来源和同一分支重复调用时更新现有 PR，不重复创建。已存在 PR 的 head 变化必须重新验收和复核，不能沿用旧证明。
- PR 正文必须包含且只包含一行任务标识，例如 `Task: PH-104`；不得出现第二行 Task。正文只放任务、commit、PR、测试摘要等公开信息，不上传原始私有日志、提示词、凭据或主控目录内容。
- 创建/更新后默认停止，等待当前 CI。记录 PR 编号、URL、head SHA、分支和 CI 状态；CI 缺失或失败不得声称可合入。

report/handoff 也属于最终交付内容；写入后若最终摘要变化，应按同一规则重新绑定验收与复核，不能把旧结果复制成新证明。

## 4. merge：远端实时门禁

`merge` 仅在主控明确调用且处于当前用户授权范围内执行；默认不 merge。调用时立即重新查询远端，不缓存旧结论，不按本地记录猜测：

1. PR head 与验收、复核、PR 证据绑定的最终 head 完全相同；出现任何新 commit 即阻止。
2. BASE 是严格最新的 `origin/main` 对象，不是过期或本地推断的基线。
3. 远端分支保护返回的 8 项 required checks 逐项为实际 `success`；缺失、排队中、neutral、skipped、cancelled、失败或状态未知均阻止。远端不是恰好 8 项 required checks 时同样阻止并报告；不能按本地列表、历史 run 或“应该会过”补足。
4. 没有未解决的 review 讨论/thread，review 结论与最终 head 一致。
5. 远端 mergeable 状态可读且允许合并。

任何远端状态无法读取时拒绝并保留检查结果，不做乐观合并。合并使用固定 `gh` argv；合并后记录 merge commit 与当前状态。合并不等于发布、部署或生产验收，这些入口不提供。

## 5. 失败、变更与公开边界

- verify 或 review 失败：保留实际命令、退出码/摘要和限制，不生成完成证明，不用跳过、放宽断言或 mock 掩盖。
- 任一边界变化（BASE、契约、最终 diff、head、任务状态）：旧验收和旧复核同时失效，必须重跑。
- 公开内容只限任务、commit、PR、测试摘要；原始日志、提示词、本机路径清单和任何凭据不得上传。
- 命令状态只写本机私有队列/证据目录，不写产品业务数据，不改用户全局设置。

## 6. 实际运行证据（由整合者填写）

以下不是自报结论；PH-104 交付时必须用真实演练结果替换空白。未填写的项目一律视为未验证。

| 项目 | 实际结果 |
| --- | --- |
| verify：source UUID / base / contract / diff / 结果 | 待填 |
| review：reviewer run / agent / 结果 / 限制 | 待填 |
| PR：编号 / URL / head / CI run | 待填 |
| merge：远端最新 BASE / 8 项 checks / 讨论 / 最终 head | 待填（未 merge 则写“未执行”） |
| report / handoff / 任务行 | 待填 |
| 未验证项 | 真实 verify、独立 review、真实 PR/CI、远端 8 项检查与授权 merge |

自动化测试和 CI 通过只证明对应检查实际通过；不代替上述真实交付动作，也不能由本文档自行宣称。

## 7. 验收命令

由主控在无凭据、禁网络的独立验收环境执行：

```sh
python scripts/projectctl.py check
python -m unittest tests.test_delivery_gate -v
python -m unittest tests.test_delivery_execution -v
```

失败时按失败记录，不生成完成证明。执行层 short-lived child 衔接修复及其确定性反例由主控在 `execution/runner.py` 与既有测试中维护，本文档不扩大该范围。
