# GitHub 上领任务，本机四个 CLI 干活

2026-10-24 用户要求的实现契约。本文取代 PH-091 中尚未实现的 ZCode、另建常驻 GPT 主控等后续设计；历史交付记录保留。GPT 是当前对话中的总控，执行器仅 Kimi Code、OpenCode、DeepSeek Harness、Claude Code。Claude Code 当前使用既有 GLM 配置，不把工具名称当作实际模型名称。

## 1. 直接使用的流程

GPT 整理需求/缺陷并关联 GitHub Issue → 已合入的任务契约 ready → 一个本地命令领任务 → 独立目录的执行器 → 另一执行器复核 → 冻结验收实际通过 → PR/当前 CI → GPT 在已授权范围内合入。

不增加网页、消息中间件或模型 API，不装开机服务。前台进程可停止，状态保存到本机；关闭对话不等于 GPT 仍在工作。GitHub 承载需求、代码与评审，仓库 BASE 契约批准开工，私有队列只记录运行事实。Issue 文本和 labels 都不产生开工权。发布、部署、付费不在本轮授权中。

## 2. PH-102：执行层接口

目录 tooling/ai_delivery/execution；公开接口可在独立模块实现，但以下语义固定：

- `AgentConfig(agent_id, executable, model=None, auth_paths=())`：仅四个已知 ID、绝对可执行文件；model 是主控本机已配置选择，禁止自动未知计费回退。auth_paths 是可信主控指定的本执行器配置/认证路径，不接受 Issue 输入，不把配置/令牌值写日志。可增加可选的运行库路径与本机设置字段，但不得接受任意 argv 或 shell。
- `RunStore(root)`：私有0700目录、0600文件、owner/type/symlink检查。生产固定到 OS 账号 HOME 下 `.project-hub-agentd`，测试可注入临时路径；worker 不可访问队列。SQLite runs 至少保存 run_id/task_id/base_sha/contract_digest/agent_id/scope/worktree/deadline/attempt/state/fencing_token/lease_until/pid/process_identity/result/error；events 是只追加 seq/run_id/type/at/payload。每请求持久化后才启动。
- `enqueue(request)`：run_id 为 UUID、base_sha40位、contract_digest SHA256、scope是task白名单子集、deadline UTC epoch、attempt正整数、worktree绝对目录、mode implement/review、prompt为主控生成的公开任务说明，expected_artifacts字符串列表。完全相同run_id幂等，改内容冲突。只接受已验证的主控请求，不向worker暴露入队入口。
- `claim(run_id)`：BEGIN IMMEDIATE，原子领取、随机fencing_token、全局最多3个未释放运行；scope可能重叠时保守串行，同repo的不同明确文件子集可以并行。不靠线程内锁替代跨进程事务。
- `Executor(store).run_once(run_id, config)`：固定适配器 argv，stdin/参数传任务，shell=False；保存真实进程组身份，30秒内续租、默认90秒租约，检查deadline。stdout/stderr各最多8MiB、单条事件最多64KiB，原始输出只留私有本机。返回持久运行记录，不直接把PH任务置done。
- `cancel(run_id)` 与 `recover()`：取消先核对PID启动身份并终止整个进程组，确认退出再释放范围；不确认则quarantined。恢复对租约过期的运行核对真实进程；活进程或身份不明保持quarantined，绝不重复启动。已确认不存在的进程置interrupted/failed，显式新attempt从已有差异恢复，不使用任意工具的last/continue。fencing token过期不得提交结果。

状态至少 queued/claimed/running/completed/failed/cancelled/timed_out/quarantined/interrupted/config_required；completed只说明进程结果可收集，controller仍要验收。明确支持的状态可额外细分，未知值拒绝。quarantined继续保留并发槽和scope；status明确显示原因，显式recover仅在再次证明进程死亡后释放，不能force解锁。三槽占满时返回忙和待处理run，不重复启动。进程启动身份macOS使用内核提供的微秒启动信息或同等强度证明，Linux使用/proc starttime；无法证明则拒绝跨进程终止，不能凭PID猜测。启动和取消的并发交错不能漏杀后释放租约。

## 3. 四个适配器和隔离

- Kimi：`kimi -p PROMPT --output-format stream-json`；按明确配置可加 `--model`。仅在已验证的版本使用受控自动编辑模式；保留风险操作拒绝，不使用 --auto 绕过全部询问。
- OpenCode：`opencode run --standalone --format json PROMPT`，可加明确 `--model`；不用共享背景server。
- DSH：`dsh --profile headless --json`，完整任务stdin。需要现有GUI登录时只给本次调用生成的 `--patch` 启用 credentials/authorization/deepseek-account；不改全局profile，不自动切换API计费。输出final和退出码共同判定完成。
- Claude：`claude --print --output-format stream-json --verbose --permission-mode acceptEdits --safe-mode --strict-mcp-config`，显式空MCP与最少工具/权限。不得使用危险跳过权限参数，真实有效参数按本机帮助验证。

执行器只收到其独立工作目录、该任务所需资料和自身官方认证所需的最小路径。通过私有HOME/XDG/应用数据目录隔离会话与缓存；凭据以现有客户端读取或受控路径引用复用，不复制密钥/登录文件。官方客户端正常续期需要刷新锁和原位更新时，仅允许该提供方指定OAuth/credentials路径的必要写入，不开放其他认证、全局设置或历史会话目录。仅允许该提供方所需的环境变量；移除GH/GITHUB/SSH及其他提供方密钥。GitHub写客户端与授权库由主控持有。

macOS 使用 sandbox-exec 的真实文件规则：工作目录、私有缓存、指定工具运行库和已明确配置的本执行器auth_paths可读；用户其他私密目录、其他工作目录与临时目录不可读；每run有独立worktree与独立home/cache/tmp；只允许这些本运行目录及必要系统设备写入，队列和主控日志目录不暴露。review模式单独拒绝写被审阅worktree，仍允许其自己的home/cache/tmp写入。认证续期写例外只限其自身指定路径。共享Git目录不可写，worker不执行git add/commit/push；设置GIT_OPTIONAL_LOCKS=0，提交由主控在验收后执行。路径全部canonical且拒绝越界符号链接，文件范围不是运行时沙箱。系统运行库的必要读路径可允许，但不能将整个用户HOME或所有临时目录作为例外。若无法建立/验证隔离，则config_required，禁止退化为无隔离。测试包含实际禁止宿主读取和外部写入的独立进程。其他OS没有有效后端时明确不支持；不能用macOS通过宣称跨平台。

本地执行器需要出网使用其既有提供方；禁止提供GitHub/SSH/产品数据库凭据。只用公开源码和演练数据。网络失败最多合理重试一次，鉴权失败立即停用该通道；每环节两次不合格由总控接管。

## 4. PH-103：主控命令

`python -m tooling.ai_delivery doctor|tasks|run|status|cancel|recover`。可选 `--json`；task/agent/run_id显式指定，不要求用户手填复杂请求JSON。实现层可提供Python API供当前GPT直接调用。主控执行时的本机config只配置仓库允许列表、四CLI路径/既有model/自身认证引用，不携带任意命令。

任务权威是固定的 `origin/main` Git对象；远端必须是已允许的GitHub owner/repo。run只允许BASE中ready/in_progress/in_review且依赖done、具备Issue/spec/白名单/验收的任务。GitHub只读取该task关联Issue，将其作为不可信补充资料。默认dry_run不联网、不入队；执行需主控显式execute。本轮用户的研发授权是当前主控调用的来源，不由payload自称approval替代。

同任务的互斥子run由主控assemble(run_ids)收集：必须同repo/base/contract且scope互斥，逐个核对完整差异，再在新集成worktree按固定git apply或逐文件内容合入；冲突停止，不丢弃/覆盖现有差异。集成产物记录所有源run和最终摘要，随后验证与复核只绑定这个集成结果。主控创建独立 Git worktree/短分支，记录base_sha和冻结task/spec摘要。入队的scope可以是任务白名单的明确子集，供开发/测试并行；含共享文件的完整任务不并行。最终diff校验不信worker自行改过的HEAD/config，使用已记录BASE、禁止replace objects与外部diff；检查已跟踪/未跟踪/删除/重命名两端及symlink路径。检查前后契约改变、任务取消或BASE不适用则拒绝交付。错误可读、不输出秘密；恢复输出run_id和残留目录。

contract_digest固定为UTF-8规范JSON的SHA256：task取id/title/kind/risk/depends_on/spec/allowed_paths/acceptance/github_issue，另含BASE的spec原始UTF-8文本；sort_keys=true、ensure_ascii=false、separators=(逗号,冒号)。状态/owner/handoff不入摘要，单独重查可执行状态。diff_digest对完整变化manifest规范JSON做同样SHA256：按POSIX相对path排序，每项记录path、old/new的Git模式、类型和原始字节SHA256；删除/新增表示重命名两端；symlink记录链接文本且检查实际目标不越worktree。包含未跟踪/忽略文件，除可信实现固定的数据/编译缓存排除项；排除项不能承载提交源码。增加固定向量与内容/模式/重命名/未跟踪变化反例。

## 5. PH-104：验证、复核和PR

增加 `verify|review|pr|merge` 主控动作；命令参数只能选择已存在run，不接受任意shell。冻结验收只支持可信主控实现的argv解析和白名单（Python unittest/Django/projectctl/git diff --check），拒绝shell元字符、管道、重定向、命令替换和未知可执行程序；在由BASE加已核对差异重建的干净副本中使用演练数据库；候选源码与测试同样不可信，验收进程必须使用无任何提供方/GitHub/SSH凭据的隔离环境，拒绝读取主控/其他工作目录，禁用网络，只允许写该临时副本与缓存。不能用主控宿主权限运行候选manage.py/tests。验收有deadline和日志限额。运行前后对最终改动计算摘要。

独立review必须另一run、另一agent，拿契约/最终差异/测试证据；评审目录只读。只接受明确PASS/具体阻断的结构化结果；没有可验证结果视为未通过，不能以普通自然语言exit0代替。证据至少 run_id/base_sha/contract_digest/diff_digest/checks/reviewer_run_id/reviewer_agent/result/limitations。任何diff改变使此前验收和review失效。

PR由主控凭据创建，worker无push/merge工具。提交对应report/handoff，PR正文一个Task行。先检查范围、实际验收、独立review，再提交/push/创建或更新同分支PR；重复调用不重复建PR。PR默认停止在等待CI。merge仅主控明确调用且在当前用户授权内，核对同一head、最新BASE、远端保护8个required checks实际success、无未解决review讨论。发布/部署不提供入口。无法读取远端状态不得猜测通过。

## 6. 顺序、状态与真实演练

PH-106桥修复和PH-102执行层可按互斥代码/测试子集并行；PH-107依赖PH-104，必须在新链路可用后执行，作为真实缺陷任务。PH-103依赖执行层，PH-104依赖主控和桥修复，PH-105依赖交付和探针修复。本轮以PH-104真实功能开发与随后PH-107已知缺陷修复构成演练；PH-105核对这一轮真实产生的证据，不要求PH-105开工时缺陷仍开放。Issue重开复验不制造代码回归。不得虚构产品功能或缺陷。

每个任务交付时，只能在本规划已冻结的字段、范围和依赖内，把前置全done的下一任务置ready；不得扩白名单。PH-092完成时创建PH-093 ready规划槽，防止再次无规划入口；规划槽后续完成仍须留继任者。

真实证据须含四CLI分别最小开发结果、至少两种实际并行、独立复核、一次取消/恢复、GitHub Issue/PR/当前CI和缺陷重开记录。自动化测试不触发付费或远端写；真实演练另由主控显式执行并记录。失败/缺额度/不支持必须保留，不能用mock、另一模型的结果或帮助输出冒充该CLI成功。前台原型通过不等于无人值守部署或用户业务验收。


## 本机参数证据与交付证据要求

本机帮助及真实调用已确认四CLI的以上版本参数；Claude Code 2.1.309支持--safe-mode，OpenCode 2.0.30支持run --standalone --format json，DSH 0.2.1-rc.2支持--patch。Kimi 2.3.1原OAuth需自身oauth/credentials目录的正常刷新锁；Claude需CLAUDE_CODE_TMPDIR指向私有目录；DSH headless需明确复用原桌面配置的opencode-go适配器和现有认证，不能默认回落到缺失密钥的deepseek-official。OpenCode可按本机清单明确选择opencode/deepseek-v4.1-flash-free；默认端点曾503，不能冒称默认通道稳定。这里只证明真实回显与研究/复核，开发能力仍需各任务实测。

PH-105证据清单必须包含真实run_id、CLI和实际provider/model、开始/结束时间、改动摘要、独立review、验收结果、Issue/PR/commit/当次CI链接、取消/恢复结果及其脱敏日志摘要/哈希。回显和mock不得填作真实开发记录；无这些证据不能宣称演练done。仓库所有者仍可伪造声明，该证据不是对管理员的防篡改系统。active_handoff保留最近已完成交接用于恢复，后续ready任务通过projectctl next/brief定位，不能把活动指针描述成自动开工许可。
