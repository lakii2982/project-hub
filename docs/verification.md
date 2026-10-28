# 验证记录（当前结果与历史记录分开）


## 当前产品组合：2026-10-27

代码依据为PH-022合入main b3d5f85，发布版本仍0.1.0。产品最终本机完整440项回归在94.033秒完成，436通过、4环境跳过；详情专项22项1.173秒通过，配置/迁移漂移/隔离演练库完整性通过，collectstatic182与唯一令牌HTTP字节校验通过。

PR[#52](https://github.com/lakii2982/project-hub/pull/52)最终head d1743ac的8项required CI均通过：[业务/容器运行40486300463](https://github.com/lakii2982/project-hub/actions/runs/40486300463)、[CodeQL运行40486300490](https://github.com/lakii2982/project-hub/actions/runs/40486300490)。容器job122128811257的配置、seed、完整性、健康/重启、真实dump与隔离PG恢复均实际通过。生产TLS、财务/锁账多连接并发、非空附件PG恢复、真实模型和产品个人Agent客户端仍未验证。

最终产品补丁1440/390真实浏览器已检查：六页签、Home键、刷新、页面无横溢、390下图表局部滚动（316/760px）。原GLM/Kimi组件复核与新独立补丁复核通过；关闭脚本浏览器曾挂起，不计通过。旧数字和数据量属于以下历史日期，不作为当前环境断言。

本轮环境：现有演练库先备份再应用0005迁移，完整性ok=true/errors=[]，1组织/48账目；未重置账号或业务数据，真实owner登录到经营总览及工时入口通过。最终交付复验与限制见PH-109报告。

macOS自动化使用规范化TMPDIR=/private/tmp。未规范化的首次417回归有两项因/var与/private/var别名失败；规范化后417通过，再经过PH108修复/PH022集成达到上述440。异常启动回收用例存在Popen生命周期ResourceWarning，实际空进程组断言通过，未证明活进程泄漏。

## 历史基线：2026-10-22

日期：2026-10-22。环境：macOS、Python 3.12.14、Django 5.2.18；业务测试与演练使用SQLite。数据为本项目生成的演练数据，未录入真实客户或工资。

## 自动化与实际执行

| 检查 | 命令或路径 | 实际结果 |
| --- | --- | --- |
| 应用检查 | `.venv/bin/python manage.py check` | 无问题 |
| 迁移漂移 | `manage.py makemigrations --check --dry-run` | No changes detected |
| 主应用回归 | `manage.py test tests --verbosity 1` | 95项通过，4.687秒；包含4项mock Docker脚本测试 |
| MCP单测 | `.agent-venv/bin/python -m unittest discover -s integrations/mcp -p 'test*.py'` | 8项通过，0.057秒 |
| MCP协议握手 | `.agent-venv/bin/python integrations/mcp/smoke_stdio.py` | initialize和list_tools通过，列出全部7个工具 |
| MCP实时接口 | `.agent-venv/bin/python scripts/smoke-personal-agent.py` | 4个读取、3个dry-run通过；0业务写入；临时PAT最终撤销 |
| 演练账校验 | `manage.py verify_integrity --json` | ok=true，1个组织、47条账本记录、errors=[] |
| 事件消费 | `manage.py process_events --once --limit 500` | expanded=20，processed=20，retried/failed/blocked均为0 |
| 静态资源 | `manage.py collectstatic --noinput` | 167个资源就绪 |
| 本地备份 | `manage.py backup_local --output runtime/backup-acceptance-20261022` | 新目录备份成功；未覆盖活动库 |
| 隔离恢复 | `scripts/verify-local-backup.py runtime/backup-acceptance-20261022` | ok=true，47条账目、1个附件 |
| 备份反例 | 在临时目录中复制、篡改并恢复；空库另做迁移 | 覆盖拒绝、0700/0600权限、篡改检出、0账目0附件空包均通过 |
| 生产配置静态检查 | `manage.py check --deploy`，使用专门的测试环境参数 | 无警告；没有连接PG或访问真实TLS |

MCP实时脚本另以冲突的POSTGRES_HOST、PMO_ENV和PMO_DB测试环境运行，通过本地库强制选择；没有尝试连接该测试PG地址。终端沙箱默认不能连接回环端口，该实时检查通过获准的本机连接执行。没有模型调用或付费API调用。

## 流程与界面

- tests/test_workflow.py 走真实表单：立项→预算基线→任务→执行→工时和单据→独立审批→验收→维保→归档；检查逾期计划与未来预测分列。
- 本地实际10路页面GET均200：总览、项目、工时、审批、财务、助手、组织设置、审计、接入、插件。私有演练附件下载200且内容匹配；审计链通过。
- 浏览器实际启用交付巡检插件：增加3条临近里程碑提醒；停用后这些扩展提醒消失，原始项目与账目保留。最终恢复原默认启停配置。
- 插件页增加“待分发事件”和“待处理投递”两个计数。反例用未运行消费者的事件及另一公司失败投递，确认积压可见且数据隔离。
- 桌面1280×720和手机350×767已检查；没有把截图检查当作完整可访问性或负载测试。

界面文件：

- [经营总览](screenshots/desktop-overview.png)
- [插件管理](screenshots/desktop-plugins.png)
- [个人接入](screenshots/desktop-agent-connections.png)
- [手机总览](screenshots/mobile-overview.png)

## 源码交付包

`scripts/package.py`产生版本化ZIP、内部逐文件SHA256清单及外部包SHA256。`scripts/verify-source-package.py`验证路径、逐文件摘要、包摘要与运行数据排除策略。

已实际检查：相同输入连续构建摘要一致；包内没有runtime、数据库、真实附件、演练登录文件、.env或虚拟环境。将源码包解压到全新临时目录，使用已安装的同版依赖执行check、migrate、seed_demo、verify_integrity全部通过，新演练库为47条账目。该检查证明打包内容完整，不等同于在另一操作系统上安装依赖或构建Docker镜像。

复验：

```sh
.venv/bin/python scripts/package.py
.venv/bin/python scripts/verify-source-package.py releases/project-hub-0.1.0-source.zip
```

## 2026-10-22当时尚未验证或交付（后续CI结果见下文）

Docker构建和四服务启动、真实PostgreSQL迁移/触发器/多连接并发、生产TLS、PG备份恢复、真实模型提取质量、真实WorkBuddy/Codex客户端配置、目标规模负载与完整可访问性检查仍待执行。本机没有Docker；项目没有使用真实模型凭据。

业务能力缺口不是“待验证”的同义词：正式合同、管理收入、应收应付、退款/预收预付、发票、共享分摊、月度人工池和完整业务插件抽离尚未实现。完整差距见 [交付状态](delivery-status.md)，后续依赖见 [工作单](work-orders/README.md)。

## 公开工程基线补充（PH-000）

本地新增治理后主测试124项通过（6.841秒）；机器契约22项任务通过；actionlint v1.7.12与Gitleaks v8.30.1的官方发行SHA256已核对，工作流静态检查与公开源文件秘密扫描通过。独立评审及修复见project/reviews/。真实远端CI与发布结果见project/repository-status.json；本节不以文件存在声称远端已通过。此前47条账目/1附件是当时演练快照，维护者后续在本机继续演练，当前数据量应现场核对。
# PH-021 PostgreSQL兼容修复

首次公开CI：https://github.com/lakii2982/project-hub/actions/runs/39784003292 。SQLite/MCP/治理/公开内容通过；PG任务更新两项和容器seed因可空外连接FOR UPDATE失败。补丁只限定Task锁为self，保留公司锁、事务和业务校验。SQLite128项测试7.149秒通过，无迁移漂移；独立审阅见project/reviews/PH-021-review.md。PG与完整容器链路以随后真实CI记录为准。


## 公开远端收口（PH-000 / PH-021）

恢复PR #15头e4c99bf的6项核心检查全部通过：https://github.com/lakii2982/project-hub/actions/runs/39789487592 ；两项CodeQL通过：https://github.com/lakii2982/project-hub/actions/runs/39789487620 。SQLite/PG各128项测试、PG迁移/触发器、MCP8项/7工具stdio、容器构建/seed/健康/重启持久性、真实dump/SHA/隔离新PG库restore/integrity已实际运行。main规则85205254已保存，8required checks/strict/PR/linear/conversation/admins生效，审批数0，force/delete禁止。#15在全绿后合入a112b789。

常规收口PR #16移除临时初始化入口并更新接续状态；该PR与最终发行的结果分别以对应提交的Actions/GitHub Release核验。上述CI不替代真实生产TLS、PG多连接财务并发、非空附件恢复/凭证与报表检查、真实模型和真实客户端验收。


## PH-090 项目详情改版规划

本次仅冻结范围，不包含产品实现。projectctl与治理单元测试及独立规划审阅的实际记录见project/reports/PH-090.json；本PR实际CI以对应GitHub提交为准。DSH通信探针曾真实返回DSH_PIPELINE_OK，运行记录确认opencode-go/deepseek-v4.1-flash；该探针不代表业务实现完成。
# PH-091规划核查（2026-10-23）

本轮为纯规划，无业务实现与迁移：在最新远端BASE保留PH-022项目详情契约，新增PH-100至PH-105和PH-092。projectctl check通过32任务，治理测试实际执行；精确结果和独立审阅见project/reports/PH-091.json。没有运行真实worker模型、浏览器新功能、并行开发或生产部署。本机CLI仅版本/帮助探针，不构成登录、执行、取消或恢复验收。远端CI结果单独核对。

## PH-100只读doctor验证（2026-10-24）

接管Codex未提交实现后，独立上下文复核为PASS、无阻断项；补入CLI合法配置、坏JSON、超大配置及参数串反例。`.venv/bin/python -m unittest tests.test_delivery_agents tests.test_projectctl tests.test_governance -v`实际32项通过，`.venv/bin/python manage.py test tests --verbosity 1`实际141项通过；Django系统检查无问题，迁移漂移为No changes detected，projectctl校验32任务通过。

实际运行`.venv/bin/python -m tooling.ai_delivery.doctor`仅做固定版本探测：Codex 0.164.0、Kimi 2.3.1、Claude 2.1.309、OpenCode 2.0.30、DSH 0.2.1-rc.2成功，ZCode在PATH中缺失。未验证认证、推理、非交互任务、结构化输出、取消或恢复；未读取现有runtime，未做浏览器、生产或非macOS检查。详细证据见project/reports/PH-100.json，远端required checks以实际PR为准。

## PH-101 GitHub桥验证（2026-10-24）

接管既有未提交实现并补齐Bridge后，独立复核三轮通过：首轮修复confirmed重放授权、pending恢复和授权反例隔离；第二轮将三个真实进程测试改为POSIX fork，消除unguarded manage.py在Django runner下的spawn递归引导；最终轮修复唯一状态库、unknown principal、恢复事务、确定拒绝、marker有界解析和revision并发证据。

最终代码实际运行：目标/治理46项测试通过，全量Django 168项测试通过；系统检查无问题、迁移无漂移、projectctl校验32任务、公开内容检查无错误。未读取现有runtime，未执行真实GitHub写入、gh认证、远端断网或模板浏览器检查；FakeClient和subprocess mock不证明远端exactly-once。详细命令与结果见project/reports/PH-101.json，远端CI以实际PR为准。


## PH-092 四CLI本地团队规划（2026-10-24）

已冻结GPT总控、GitHub任务、四CLI执行/复核/PR的最小实现契约，见local-team.md。PH-106/107记录真实审计修复，PH-102至105分阶段实现，PH-093保留下一规划入口。本条只陈述规划，未宣称调度、取消恢复或全流程已实现。PR #30经用户单次明确授权恢复planning入口，原8项required checks、各app_id与strict均已恢复；后续不复用豁免。实际测试/独立复核记录见PH-092报告。

## PH-106 桥恢复反例（2026-10-24）

固定Python3.12，在无模型/GitHub/SSH凭据、禁网络的macOS文件沙箱执行：桥/治理52项通过、Django174项通过；check和迁移漂移检查通过，临时空库迁移后integrity ok=true、errors=[]。独立复核发现并纠正换人更新/原创建者删除后的恢复问题。原始私有日志不上传，测试摘要见report。

## PH-102 执行与隔离（2026-10-25）

固定Python3.12，无凭据、禁网络沙箱内79项专项（75通过、4嵌套后端跳过），240项应用回归（236通过、4跳过）。额外固定探针真实应用最终权限规则：自身文件/缓存和自身认证刷新允许，其他目录/认证及原始混合配置拒绝，review源码不可写，网络EPERM。私有队列真实多进程领取、取消/启动交错、未知身份不误杀、DSH零退出但未完成均有反例。独立GLM/DeepSeek的最终纠正与证据哈希见报告。

四个原生客户端经本次Executor均完成精确文件写入；DSH与Claude成功区间实际重叠。Kimi最初因遗漏always_thinking能力声明被服务端400拒绝，保留所选模型公开元数据后单独重试通过，未改原配置。该私有fixture不冒充已治理的GitHub开发任务。

## PH-103 · 主控入口验证（2026-10-25）

- `python -m unittest tests.test_delivery_controller -v`：实际72项，30.300秒，全部通过；真实临时 Git 对象和持久队列，模型/GitHub边界使用替身，无远端写。
- `python manage.py test tests --verbosity 1`：实际312项，48.027秒，308通过、4项嵌套 macOS 后端限制跳过。验收进程无提供方/GitHub/SSH凭据，文件系统隔离、禁网、临时SQLite。
- Django check、makemigrations --check --dry-run、临时库 migrate、verify_integrity：通过；完整性 ok=true/errors=[]。
- Kimi 独立服务/CLI复核，以及 OpenCode/DeepSeek 仓库复核发现的4项边界问题有新增反例；最终 Kimi 两次限定范围复查均 PASS。一次复核连接中断保留，不计成功。
- 真实 GitHub 数据入口的 tasks 与默认 run 计划已在无凭据禁网沙箱、基于批准的真实 Git 对象实测；明确执行的客户端记录另列，计划结果不冒充开发成功。

真实入口运行：OpenCode/free DeepSeek 通过 Issue #23 和真实冻结 BASE 入队。run73a3e1e2 因单行输出上限失败；单次重试811ac6d4 尝试修改文档范围外源码，总控取消，独立恢复调用与状态确认为 cancelled。两次均不计成功开发。补充 run 返回前和幂等重新返回前的完整差异/契约检查，新增2项反例；文档由总控接管。后续真实成功开发证据仍由 PH-104/107 产生。

## PH-093 · 范围补充依据（2026-10-25）

PR #36 首次 SQLite 作业40174894779/job121113355733中两个既有执行测试出现 process_identity_unavailable；同提交 PostgreSQL通过，有限重跑SQLite job121114047405通过。原失败保留，以此冻结PH-104的局部启动/完成衔接修复；不把重试通过描述为根因已修复。

## PH-104 · 交付与执行衔接验证（2026-10-25）

- 门禁36项28.964秒、执行层27项9.004秒均通过；全量353项78.667秒，349通过、4项嵌套 macOS 后端跳过。全部候选验收在无凭据、禁网沙箱和临时SQLite运行。
- Django check、迁移漂移检查、临时库迁移及完整性通过，ok=true/errors=[]/organizations0/entries0。
- GitHub 提交重建使用真实临时Git、固定网络替身，覆盖 worker .git 不可信、重命名两端、来源app/HEAD/最新重跑、严格8项、分页讨论、最后时刻BASE/HEAD变化；真实远端写单独由总控显式执行。
- 真实 Issue24 开发run：Kimi5645cce6、DSH376cb7bd、OpenCode152b0b10均完成并通过完整范围检查，Kimi与DSH进程重叠122秒。OpenCode069d786a超时、DSH长复核超时均保留，未冒充通过；独立Kimi/Claude最终组件复核PASS。
- 报告冻结后，再由新入口对最终合件生成精确差异的隔离验收、另一执行器复核和PR回执。最终摘要/CI不嵌入自身报告形成自引用；后续PH-105核对真实记录。

## PH-107（2026-10-25）

- 无凭据、禁网文件沙箱：37项专项5.231秒全部通过，358项完整回归99.776秒中354通过、4项嵌套macOS后端跳过。
- 真实子进程测试验证深层JSON、FIFO、symlink、目录拒绝及相对PATH；配置/迁移漂移/临时库integrity实际通过。
- 原生Claude Code/GLM代码与DSH/DeepSeek测试各有真实run/base/contract/diff/日志摘要，进程重叠325秒；Kimi代码复核PASS。报告冻结后的最终回执及当次PR/CI在PH-105审计，不把自报pass当远端证明。

## PH-105（2026-10-25）

- 实际4项离线证据核对0.001秒通过；无凭据禁网沙箱完整362项89.832秒中358通过、4项嵌套macOS后端跳过，静态目录warning保留。配置、迁移漂移、临时SQLite完整性实际通过。
- PH-104/107五条成功开发记录覆盖四CLI、真实重叠122/325秒；同源摘要的隔离验收、独立复核、PR38/39当前8CI、合入提交均可在公开证据逐项核对。
- 真实Issue32于12:59:01Z重开，18项回归2.855秒通过，done任务仍拒绝执行，13:00:18Z关闭；事件34182647370/34182697950。取消任务811ac6d4在两个新进程recover/status仍为cancelled。
- 当前默认版本探针已通过，能力仍标unverified；能力证明来自单独真实开发记录。本任务OpenCode/Kimi默认配置实际写入，Claude独立只读审核PASS。报告冻结后再生成最终精确差异门禁/PR/CI，不能以本报告自报代替远端事实。

- 最终Claude全件复核900秒超时/171次工具读取，不计PASS。基于实测发散补充门禁复核范围提示，36项门禁回归39.814秒通过。40次调用是提示建议而非硬沙箱；固定进程deadline仍强制。旧精确摘要证据失效，新的验收/DSH复核必须绑定新摘要。

## PH-094 · 2026-10-25 规划验证

projectctl check通过（41任务），projectctl/governance共19项通过（3.075s）。原生Claude Code既有GLM在只读快照返回PASS/0阻断，完整范围与限制见PH-094报告；工具代理路由失败未计成功。仅规划，无产品页面、生产或PG并发验证。

PH-095：projectctl check（42任务）与19项规划/治理测试（2.152s）通过；原生Claude/GLM只读风险校正复核PASS。最终PH-023仍须新S契约下复核/CI。

## PH-023 · Kimi视觉系统（2026-10-26）

视觉系统、前端规范、令牌和交互样张已完成；全部后续前端受AGENTS必读入口约束。2条隔离验收通过，DSH独立全件/GLM补丁复核PASS；主控浏览器320/390/768/1440、无JS降级、移动登录、图表局部滚动与aria展开通过。静态样张只作设计基线，产品界面/业务尚由后续任务交付；当次CI见PR，非生产验证。

PR49容量输入P2：Kimi改为整数分钟，独立新上下文确认输入有效并要求纠正旧报告；已纠正，最终新上下文复核71db08dd已PASS。浏览器默认480分钟validity=true/stepMismatch=false，保存演练反馈正常。无JS按钮禁用、JS按钮启用后阻止发送，纠正旧GLM静态推断。

## PH-024 · 2026-10-26

以既有Project/Employee/CostRate建立唯一基础资料服务、授权上下文及Kimi维护表单；旧创建/项目编辑委派同一服务，账户开通不再隐式新建员工。公开维护模块界面留PH026。 6项固定命令在无凭据禁网快照通过，最终独立只读复核PASS。后续PH026接入/master-data/唯一入口与Kimi页面；不要另建表。EmployeeEditForm(member,employee,initial)/ProjectMasterDataForm(member,project,initial)只清洗字段，写入由视图调用core.masterdata服务。费率追加、版本/原因/公司锁和审计保持，账户与员工拆开。 详细实际命令/哈希与偏差见project/reports/PH-024.json；当次PR8CI另核对。非生产验证。

## PH-025 · 2026-10-26

关键任务必须链接同项目里程碑或有效收付款计划；保留历史任务与task_id兼容，工时浏览器只按项目登记。追加0005迁移，蓝图仅生成3个真实里程碑及相应关键任务；Kimi最小表单与主控视图原子集成。 8项固定命令在无凭据禁网快照通过，最终独立只读复核PASS。PH022可开始项目页签和成本预算余额图，仍由Kimi负责前端、DSH/GLM负责只读汇总；PH026全站样式与唯一维护入口。Task字段milestone/cash_plan至少一项；estimate历史保留，新建0；TimeForm无task，API/MCP兼容原可选task_id不重算历史成本。 详细实际命令/哈希与偏差见project/reports/PH-025.json；当次PR8CI另核对。非生产验证。

PH025最终组合：已接最新批准BASEef27efb，417项全量回归（413通过/4环境跳过）与固定验收通过；节点选择框显示名称/方向/日期，Kimi最小修正，独立ec2a6e24复核PASS。真实演练owner登录已检查，不把旧页面当全站改造完成。

## PH-022 · 2026-10-26

项目详情改为概览/关键任务/里程碑/有权财务/风险/团队页签，保留深链与原操作；概览增加按当前批准预算减业务日期累计确认成本的余额燃尽图和精确数表；历史任务独立保留，不驱动关键任务统计。 实际21专项与完整438项（434通过/4环境跳过）、固定验收通过；独立GLM/Kimi只读复核PASS；1440/390、六页签/键盘/hash/前进后退刷新/局部滚动通过。关闭脚本的浏览器检查挂起中断，未计PASS，静态结构已审；完整限制见report。全站UI和基础资料模块仍由PH026交付，非生产验证。

## PH-108 · 2026-10-27取消测试同步

仅修测试同步：正常取消等待marker/running/持久身份；固定start前交错继续要求隔离、空组后恢复且不重启。28专项、418回归（4环境跳过）、配置/迁移/完整性通过，独立审阅通过。未改生产执行器；Popen生命周期ResourceWarning保留，详见PH108报告。远端CI按当前PR核对。

PH022最终补丁（2026-10-27）：canonical ui静态源+Kimi移除复制令牌，HTTP字节与唯一源一致；22专项、440全量（4环境跳过）与配置/迁移/完整性通过。新独立补丁复核通过，1440/390无页面横溢，窄屏图表局部滚动。auditor遵循既有finance_read项目汇总权，个人人工明细仍遮蔽。完整限制见最终报告；当次required CI另核对。

## PH-109 · 2026-10-27文档与本机交付

本轮五项验收均实际通过：check45任务、Django无问题、迁移无漂移、440回归91.420秒（436通过/4环境跳过）、现有演练库integrity1组织48账目/errors=[]。真实8765登录owner进入经营总览，再打开项目六页签与成本图。文档差异独立复核PASS；当次PR CI及合入SHA查询GitHub，文档PR合入后再从main启动并保存HEAD/HTTP回执到本机忽略目录，该最后复验当前待完成，凭据不入库。


## PH-111 · 自主PR交付改造规划（2026-10-27）

从PR56最新main接续，冻结PH120至122的契约/可信仲裁、客观CI/发布分离、远端启用/演练三个薄片，保留PH123下一规划槽。仅规划，未实现或启用新门禁。独立规划审查PASS，实际19项治理测试通过；具体基线/审查边界见PH111报告，远端CI以本PR实际结果为准。原工作区仅本地规则及用户稿保留。

## PH-123 1.0准备规划

本次仅任务/spec登记，验收与独立审阅结果见project/reports/PH-123.json；CI以本PR实际运行核对。未进行产品实现或生产验证。

## 2026-10-28 PH-130准备设计入库

纳入1.0交付方案、简表和长程设计：137需求/62工作包/18UAT。实际只读结构检查、五种拒绝及19治理回归通过；独立复核修正临时加载缓存和政策状态后通过。设计覆盖不是产品实现或客户验收。PH131成为下一准备任务，当前PR的8项required CI仍须合入前核对；产品代码保持119a049，未发布/部署。

## 2026-10-28 PH-131当轮研发环境资格

Kimi/Claude/OpenCode完成真实受控文档写入及范围复检；DSH缺少所需既有认证，failclosed并排除。已实际取消running运行、核实原进程组消失、recover无重启、新工作目录恢复；OS合成隔离及离线Chromium环境探针通过。局部原生verify通过19测试，另一CLI只读review正确BLOCKING不完整矩阵；本任务完整候选另经独立复核PASS与19治理回归。证据见project/reviews/PH-131-environment.json。PG多连接留待PH135，产品、目标Linux/TLS、非空附件恢复和客户验收未验证；当前PR8required CI仍待合入前核对。

## 2026-10-28 PH132字段与政策准备

完成C01-C12目标schema、唯一owner/关系、严格客户端DTO/SourceRef、政策和旧新切写；9schema/2478本地引用、137需求、55命令/25查询/28事件，19正37拒绝实际校核通过，接口与财务独立复核通过。保留原book、金额和旧快照；新人工预算/暂估/实际分层与同源调差已写规格。仅文档/校核工具，不声称产品、迁移或PG已实现；PH133/134下一步准备。当前PR required CI仍须正常合入前核对。

## 2026-10-28 PH133离线业务原型

实际bundled Node/Playwright执行docs/v1.0-development/ux/check-browser.cjs，Chromium151.0.7922.34，1440/390四职责三模板路径、缺日期聚焦/预览、页签Home/End/左右、差额展开、手机模态导航约束/回焦通过；无JS异常/外部请求，320..1440九宽无横溢。主控实际视读全图及390结算局部图，caption竖排修复后再跑/再看通过。原始失败：草案按钮ID不匹配、手机焦点边界/close回调竞争；均已修复，失败未计通过。仅原型合成证据，客户、生产及后端权限未验证。

## 2026-10-28 PH134样本及冻结旧库

主控实际运行.agent-venv Python -B fixtures/check_fixture.py：99正例、8拒绝、92来源、18独立UAT、137专项、输入/附件/CSV哈希和声明算术PASS，不导入产品、不读DB、不执行产品命令。执行者实际运行verify_legacy.py两次旧构建；主控复核输入SHA绑定及两run结果：各旧账58分、4工时/4账本、9审计/9事件、verify_integrity ok，business.json同SHA fb6ede3c5ac1c05d63515caebca82aa28dd873f0026fc853d8877503dd537ab0；已有输出拒绝、DB哈希不变。旧库和原始日志保留外部临时目录，未入源码。独立审阅/固定治理与当前CI另记任务报告；产品UAT、新升级及PG并发尚未执行。

## 2026-10-28 PH135隔离本机准备回归

可信BASE e5ce5c3，原生Kimi run62f50fc1-32a9-4544-9572-4685797eabf0生成3文件后主控检查发现锁验证假通过窗口，安全取消(562秒)并scope核对后接管；不是成功运行。既有Verifier本机无认证禁网快照回执bd1ef002-f08e-4ad0-a316-df6245b1099b固定6项及前置migrate均PASS；60任务、19治理2.092s、449测试93.505s(438通过/11跳过：旧4+PG4+schema3)、系统/无迁移漂移、空开发库完整性ok。未执行PG/schema领域不计通过。新定向测试、真实PG不同PID锁/rollback/unique和legacyCI仍待当前PR，不修改原8required或生产依赖。

PH135资格补记：PR75提交8053697本轮全部原8required通过；准备CI40623378734 schema5/PG定向9均无skip，2schema+4PG标志实读确认，legacy2build58分/拒绝覆盖实际通过。第二最终本机Verifier ba07ceea...6项+migrate通过，449项97.640s11跳过不作PG证明。当前证据/状态收口提交还须重新全11checks及正常merge。135done/136ready仅在合入后可执行，不代表产品业务、目标环境或客户验收。

## 2026-10-28 PH136静态排期算术

主控实际运行/private/tmp/ph136_plan.py，从冻结work-packages.json取62节点和137唯一需求引用；低/高情景分别核对角色合计20460/57870分钟、关键路径8375/23690、容量表13595/37930、缓冲16994/56895；每资源无重叠、前序CI完成才能后序。生成结果bind源SHA，六已交付/本次PRE不重算，外部等待null而非0。仅估算/静态图，不是产品运行或排期承诺。固定治理/独立审查/当前CI由任务报告收口。

## PH137 · 开发契约晋级（2026-10-29）

Python3.12下19治理测试2.093秒、固定schema环境5准备测试4.521秒实际通过。canonical9schemas、2959本地refs、71commands/25queries/47events；旧19正37拒绝和99fixture正8拒绝保留；新增银行5正及审批收据、invoice2正4拒绝、补齐16正22拒绝。这些是设计输入/结构验证，未执行产品业务。

正式规格73份、137需求唯一主责和辅助证据，62包原依赖保持；独立最终复核和当次CI记录以PH137报告/PR为准。未验证产品新核算、浏览器、生产并发/部署/客户。原旧库58分与业务SHA证据是PH134/135历史，未因schema哈希更新重跑或宣称新升级通过。

## PH139 · 实际依赖安全验证（2026-10-29）
新隔离环境Python3.12.14/Django5.2.18/DRF3.18.2：13专项0.360秒、17旧API0.710秒通过；canonicalTMPDIR全量462项83.203秒，455执行/7环境跳过。首跑两项temp别名失败在旧版复现，未改相关测试/隔离。固定schema环境5项4.739秒全执行。演练库verify_integrity为1企业47entries无错误。
旧冻结独立3.16.1依赖两构建58分/同SHA、覆盖拒绝和hash不变通过。老安全反例真实红、新13绿；非文本codec未进reader、大请求未进真实业务验证，Accept长度/数量/深参数有界。独立安全审查PASS，真实PG/当前全部CI/合入另取当次PR证据，无生产客户证据。

## PH001 · SDK1实际验证（2026-10-29）
固定MCP工具环境35专项全执行0.278秒；仅requirements的全新产品环境34执行1补充schema-parity明示跳过0.267秒。产品不新增jsonschema依赖；结构拒绝/上下文/版本图/冲突/不变性/真实AppConfig/旧启用升级规则等测试仍执行。独立复核在两个环境重复确认。
全量canonicalTMPDIR：干净产品484项88.942秒，476执行通过8skip(4PG/3准备schema/1SDKparity)；固定工具484项94.088秒，480执行通过4PGskip，准备schema双marker实际执行。check/migrationdrift/pipcheck/seeded47entry完整性通过。原生Claude超时非成功；root回收并修复后才有这些证据。无产品浏览器/客户/生产声明，当前PG和CI以PR为准。
