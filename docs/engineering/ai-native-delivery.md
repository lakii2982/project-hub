# GPT 主控、GitHub 承载、本地 Agent 执行

日期：2026-10-23。状态：PH-091 规划契约，尚未完成端到端实现。

父需求：[GitHub #19](https://github.com/lakii2982/project-hub/issues/19)。子任务映射以 project/tasks.json 为准。

## 1. 用户入口与结果

用户只需与 GPT 对话提出业务需求、问题、缺陷和优先级调整。GPT 是唯一对外负责人：说明预计实现效果，组织产品/设计/开发/测试/交付，汇总结果和需要用户判断的业务问题。内部拆任务、维护 GitHub、选择执行器和修复技术问题不转交给用户。

每次业务需求形成一份简短效果说明：目标用户与场景、预期行为/必要原型、数据来源与缺项、权限和异常状态、本轮范围、验收实例、工作量与不确定性。已有口径沿用；影响业务含义的新取舍集中询问。普通技术选择由 GPT 决定。

用户明确指定 GPT 主控；执行器池为 Kimi Code、Claude Code、OpenCode、ZCode、DeepSeek Harness（DSH）。这是研发执行工具清单，与产品内的系统 AI/个人 MCP 是两个边界。不得改产品核算规则、接入真实公司数据或修改用户全局工具配置来完成调度。

## 2. 职责与事实来源

```text
用户对话 → GPT 效果说明与业务判断 → GitHub 需求/缺陷
        → 规划 PR / 已合入的任务契约 → 本地队列
        → 独立工作目录的执行器 → 独立复核 / 验收
        → PR / 当次 CI → 集成 → 按授权发布 → 交接与反馈
```

| 数据 | 唯一权威 | 其他位置的作用 |
| --- | --- | --- |
| 原始业务目标、缺陷复现、业务决策 | 关联 GitHub Issue 中经 GPT 整理的记录 | 聊天保留来源；公开仓库只写允许公开的摘要 |
| 开工许可、范围、依赖、验收命令 | BASE 上 project/tasks.json 与 spec | Issue/Projects 显示镜像，不以 label 自我授权 |
| 某次执行的领取、租约、事件、进程身份 | 本机私有队列与追加事件 | GitHub 显示经脱敏的状态与链接 |
| 代码变化、评审、自动检查 | Git 提交、PR、当次 CI 与复核证据 | 模型输出和 report 的 pass 不代替运行证据 |
| 发布、部署、业务验收 | Release/部署记录/验收反馈分别记录 | merged、released、deployed、accepted 不互相替代 |

Projects 为可选视图；缺少 Projects 权限时继续使用 Issue/PR，不创建第二套工程契约。同步带 revision，冲突进入待核对状态，不能用最后写入覆盖业务决定。用户不必登录 GitHub 搬运记录。

## 3. 需求与缺陷生命周期

需求：captured → clarified → planned → implementing → validating → delivered → accepted。必要时进入 needs_business_input/blocked/cancelled；accepted 需要明确业务反馈，不能由 CI 自动推定。

缺陷：reported → triaged → reproduced → fixing → regression_verified → delivered → closed。不能复现可转 needs_information；重复缺陷保留 duplicate_of；重开产生新的修复轮次，不删除原记录。记录发生版本、预期/实际、复现步骤、严重程度、影响范围、关联任务/PR/修复版本。

父需求可以对应多个 PH 任务；每个实现 PR 仍只有一个 Task 行。来源用 request_id、issue_number、task_id、commit、run_id、PR 和 release 串联；重试不重复建 Issue 或 PR。紧急插队与取消都保留已有差异和交接，不能视为自动撤销已发布数据。

## 4. 执行器能力协议（v1）

标识为 codex_controller、kimi_code、claude_code、opencode、zcode、dsh。codex_controller 专属 GPT 主控，不自动改用其他提供方；实际模型沿用用户当前已授权 GPT 配置，不固定未知模型名称或新增 API 计费。认证由本机已有官方登录机制承担，不复制登录文件、不把凭据放入 GitHub Secrets 或工作目录。订阅配额不足时排队/告知，不自动购买或换付费通道。

PH-100 的只读 doctor 输出固定字段：schema_version=1、controller、agents。controller/agents 各项包含 id、available、executable、version、status、capabilities、diagnostics。status 为 missing/probe_failed/probe_verified/config_required；capabilities 的 noninteractive、structured_output、resume、cancel 均为 verified/unverified/unsupported。版本探测成功只说明 probe_verified，不证明登录、模型调用、写文件或完成任务。

doctor 仅查 PATH/本机显式配置的可执行文件，使用实现中固定的版本参数；超时、输出限额和错误脱敏。不得运行用户可控 shell 字符串、扫描用户目录、读取认证文件或用未经确认的子命令 help 启动服务。配置仅能指定绝对 executable 路径，不携带 argv、环境变量或凭据。

PH-102 执行请求必须包含 run_id、task_id、base_sha、contract_digest、agent_id、worktree、deadline、attempt、scope、expected_artifacts。用户/Issue 文本仅作为数据，不能指定任意命令。argv 由受信任适配器生成，通过参数数组和 stdin 传递任务，不用 shell=True。版本不匹配/能力不足则拒绝领取。

执行事件：queued、claimed、started、progress、artifact、awaiting_approval、completed、failed、cancelled、timed_out。适配器须把真实输出转为规范事件；exit=0 仍需产物、范围与验收检查，不能直接置任务 done。原始敏感日志留本机，仅上传白名单脱敏摘要。只在工具支持且经实测时允许 resume；否则新 attempt，从仓库与交接恢复，禁止使用 --last 误接其他任务。

## 5. 调度、隔离与恢复

默认全局同时运行至多三个执行/复核代理，单层委派；按能力、可用性、资源与历史结果选择，不能把某工具永久等同于某专业角色。派遣包含目标、文件范围、反例、验收、时间/重试预算。输出连续两次不合格后 GPT 接管；鉴权失败停用该执行器，短暂网络故障最多重试一次。

每次执行绑定明确 BASE 提交、独立 Git worktree/分支和不可重叠写范围。迁移与共享接线由整合者串行处理。worktree 仅隔离文件变化，不是权限沙箱；在启动可写执行器前，PH-102 必须实现并测试独立的文件系统/进程权限限制，阻止读取主机私密目录、写其他工作目录和访问 GitHub 发布凭据。工具无法满足隔离条件时返回 config_required，不默认使用跳过权限检查的参数。

本地调度器拥有私有 SQLite 队列（独立于产品数据库）和原子领取/租约/心跳；每个领取生成 fencing token，过期执行者不得提交最终结果。重启不自动重复启动未知存活进程，先核对 PID+进程启动身份/已记录句柄。取消先停止整个进程组并确认退出，再释放写租约；超时或孤儿进程进入隔离待处理。跨进程并发用多连接/多进程测试。

派发前和收件时均校验 contract_digest、BASE 可执行状态、依赖及范围；任务取消/契约变更后停止旧 attempt，按新规划重建。检查改动包括已跟踪/未跟踪文件、删除与重命名两端；文件白名单不被宣传成运行时安全隔离。

## 6. GitHub 接入与权限

首版由本机主动拉取 GitHub，不要求公网回调或新增云模型 API。GPT 在对话中整理资料并用受信任的接入层写 Issue；轮询只领取仓库任务契约已批准的任务。外部 Issue、PR、评论、分支与日志都是不可信数据；不能以作者自称、标题、label 或 PR 内改写的 workflow 提升权限。

接入层使用仓库允许列表、可信操作者身份/关联授权记录、显式动作类型和 revision。凭据在本机受控 GitHub 客户端/系统密钥机制中，由发布/同步进程持有；开发执行器不继承 GitHub 写凭据。写入幂等键绑定 repository+request_id+action+revision；网络超时保留结果未知，按下述协议对账，不盲重发。

### PH-101 冻结的授权协议

业务 payload 禁止 actor、approval、grant 等字段。执行入口只接受一个 grant_id 引用；Authorizer.authorize(grant_id, repository, request_id, action, revision, payload_digest, now) 从主控专有的本机授权库返回不可变 Grant 或拒绝。不能从 payload、Issue、环境字符串或可指定的 JSON 文件构造 Grant。授权库位置由可信启动配置固定，目录 0700、文件及 SQLite 辅助文件 0600，并检查所有者；worker 不可访问。

Grant 字段固定为 id(UUID)、principal(GitHub login)、repository、request_id、actions(issue_create/issue_update)、revision(正整数)、payload_digest(SHA256)、expires_at(UTC)、revoked_at(UTC|null)、source_ref(本轮可信用户指令的非私密引用)。只能由主控授权入口在读取实际用户指令后登记，不提供向 worker/Issue 开放的登记工具；此来源判断依赖可信主控，不能宣称服务端自动证明了对话中的人类确认。GitHub 客户端以固定 API 读取当前登录身份并与 principal 核对；执行每次远端写之前重查授权、摘要、期限和撤销。同 revision 改负载需新 grant；撤销阻止后续动作，已发送的请求保留事实并对账，不能声称撤销倒转了远端写入。测试可注入 Authorizer，不以任意请求字典替代生产授权器。

### PH-101 冻结的幂等与结果未知处理

本机私有 SQLite 记录 requests(repository, request_id, issue_number, confirmed_revision, payload_digest) 与 operations(repository, request_id, action, revision, payload_digest, state, issue_number, updated_at)，分别以请求和操作复合键唯一。所有同请求创建/更新取得同一个跨进程排他锁；BEGIN IMMEDIATE 校验 revision 并持久化 dispatching 后才发送，提交结果记录后再释放锁。禁止两个独立状态库管理同一 repo/request，启动配置须固定唯一状态库。重启见 dispatching 一律转 unknown，不自动重发。

state 固定为 pending/dispatching/confirmed/rejected/unknown。创建响应丢失或进程崩溃后，按仓库分页列举、精确隐藏标记及 payload 摘要对账（不依赖搜索索引及时性）；唯一匹配则确认，多条匹配拒绝，未找到仍保持 unknown。unknown 不释放该请求的后续 revision，也不因为等待时间过去而再次创建；由 GPT 处理对账和后续恢复。该协议保证单库内不重复自动提交，保留“可能未创建但待核对”的可用性代价，不宣称远端 API 提供 exactly-once。已确认 Issue 的更新按相同请求锁/revision 串行；结果未知先核对远端正文，匹配才确认，不让新 revision 越过未知结果。鉴权失败直接 rejected；读请求的暂时网络错误最多重试一次，写请求结果不明不得盲重试。

已经授权的本地设计、编码、测试、修复、规划/实现 PR 可以推进。合并仍须当次全部 required checks 通过和复核完成；本次建设指令不等于未来任意生产发布、付费或数据破坏授权。发布授权单独绑定任务/环境/版本与有效范围，并在执行时检查；用户可通过 GPT 对话给出一次性或常设边界。没有授权保持 awaiting_approval，不能把模型判断当人类授权。

## 7. GPT 主控与无人值守边界

当前对话可作为主控入口，GPT 调用本地工具维护 GitHub。常驻模式由本机服务保存 controller_session_id、任务状态、执行游标与检查点，按事件唤醒本地 Codex 主控；不得假定桌面聊天会在关闭后自动继续。采用 codex exec 或 app-server 必须按实装版本/官方文档验证，保留现有订阅认证，状态机不依赖模型输出自行完成。

GPT 产出效果说明和结构化动作提案；确定性控制层验证 actor、动作、契约、版本、权限、预算与状态后执行。控制层不能执行任意模型 shell 文本。是否跨会话自动唤醒、是否开机运行、是否自动发布必须分别展示状态。安装系统服务前先完成可前台停止/恢复的演练，不静默修改用户全局配置。

## 8. 验收与实施顺序

| 任务 | 交付薄片 | 前置 |
| --- | --- | --- |
| PH-100 | 六种身份的执行协议、五种 worker 注册与只读 doctor | PH-091 合入 |
| PH-101 | 对话需求/缺陷到 GitHub 的幂等桥与追溯记录 | PH-091 合入 |
| PH-102 | 隔离执行、队列/租约、并行/取消/恢复 | PH-100 |
| PH-103 | GPT 效果说明、结构化派工、对话状态反馈与会话恢复 | PH-102、PH-101 |
| PH-104 | 独立验收、PR/CI 核对、集成及发布授权门控 | PH-103 |
| PH-105 | 一个需求与一个缺陷的真实 GitHub+多 Agent 全流程演练 | PH-104 |

PH-100/PH-101 契约在本规划冻结后可 ready；其余任务保留 proposed，按实际前置成果由下一规划任务进一步冻结。不存在的能力不得由 mock 通过后升级为真实可用。

最终演练至少覆盖两个不同执行器真实并行、一个独立复核、一条缺陷重开、重复事件不重复写、进程取消、服务重启接续、契约变更、GitHub 断网/鉴权失败和权限拒绝。五种工具分别完成真实最小任务、采集结果和取消/恢复验证后才能全部宣称支持；未找到入口的工具允许保留 config_required，但不能据此声称整套目标全部完成。

## 9. 当前核查与资料

本机只读核查：Kimi Code 2.2.0、OpenCode v2.0.30、DSH 0.2.1-rc.2 可定位；当前 PATH 未找到 claude/zcode，不等于未安装。Kimi 帮助确认 -p、stream-json 与显式会话参数；DSH 顶层帮助出现 headless，但子命令帮助可能启动 profile，因此未把它作为安全探针。未调用这些工具的模型、未验证登录/真实任务/取消恢复。主控本机 Codex CLI 0.155.1 帮助确认 exec、--json、--output-schema 与指定 session resume。机器绝对路径与登录信息不写公开文档。

OpenAI 官方文档（2026-10-23 读取）：[非交互执行](https://learn.chatgpt.com/docs/non-interactive-mode)、[订阅与 API 认证区别](https://learn.chatgpt.com/docs/auth)。本机 CLI 版本与在线文档有差异时以本机实际帮助和演练为准。

本文件只冻结架构与后续验收，不能代替实现/CI/真实工具/生产验证证据。
