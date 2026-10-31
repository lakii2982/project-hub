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
最终固定MCP工具环境36专项全执行0.296秒；仅requirements的全新产品环境35执行1补充schema-parity明示跳过0.261秒。产品不新增jsonschema依赖；结构拒绝/上下文/版本图/冲突/不变性/真实AppConfig/旧启用升级规则等测试仍执行。独立复核在两个环境重复确认。
全量canonicalTMPDIR：最终干净产品485项89.313秒，477执行通过8skip(4PG/3准备schema/1SDKparity)；前候选固定工具484项94.088秒，480执行通过4PGskip，准备schema双marker实际执行。check/migrationdrift/pipcheck/seeded47entry完整性通过。原生Claude超时非成功；root回收并修复后才有这些证据。无产品浏览器/客户/生产声明，当前PG和CI以PR为准。

PR81审阅补记：旧manifest允许的OrderedDict/list子类也已递归冻结；新增反例及独立复核通过，未放宽旧接纳规则。

## PH138 · 纯规划检查（2026-10-29）

Python3.12实际130任务check、19治理测试通过，独立静态S复核通过；208补齐业务schema不适用理由和范围拒绝/通过预期。没有执行这些未来反例作为产品结果，也没有修改治理检查器。最终候选/当前CI/正常合入另核；PR82风险与摘要误报、002版本门控问题保持未验收状态。

## PH002 · 入口接线实际验证（2026-10-29）

Python3.12.14固定MCP/schema环境：52专项0.434秒、51执行1PGskip；独立复跑相同。root补实际pmo.urls挂载/默认无路由/停用拒绝，真实CSRF客户端反例。canonicalTMPDIR全量501项93.788秒496执行5PGskip，严格schema/99正8负夹具marker实读。check/migrationdrift无问题；既有隔离演练库47entries完整性ok。
PG新测试用线程独立连接/backend PID/pg_blocking_pids验证禁用等待公司锁，不能用本机SQLite跳过当通过；当前PG CI须另核。原生初稿失败与root修复分开记录，无客户/生产/浏览器验收。

## PH208 · 规划验证（2026-10-29）

131task check、19治理2.042秒通过，独立131check/19tests通过；边归属与规范哈希两具体设计缺口已补，再审PASS。没有140产品运行、旧库升级、PG或浏览器证据。前序002实际PG成功仅记录前序事实，不迁移为新薄片验收。

## PH140 · 实际验证（2026-10-29）

固定工具环境61组合60执行1PGskip；独立61同0.505s，SDK48/47执行1skip0.249s。全量510项93.920s、505执行5PGskip，strictschema与99正8负marker实读；check/drift通过。
冻结旧依赖重建后，当前代码7步骤全部0：migrate两次均无待执行DDL，实际MigrationLoader(connection)33已应用图与checkedfactory成功，旧58分/22表39PK/三componenthash与snapshot前后重复字节一致，完整性1企业4entryok。无新schema迁移/真实PG/部署/客户证明，当前CI另核。原生取消无产物与接管实现证据分开。

## PH209 · 独立规划检查（2026-10-29）

133task check实际通过，独立133check/diffcheck通过；现金更正草案的差额/释放歧义已按既有bank plan收紧，再审限域PASS。规划没有执行210新schema或141服务/PG/客户验收，当前全部CI及正常合入另核。

## PH211 · 业务优先最小授权

135task check实际通过；只冻结客户/供应商与合同草稿业务片，不执行基础设施修整。212的业务/浏览器/PG/生产未验证，当前CI及正常合入另核。

## PH212 · 实际业务验证（2026-10-29）

固定Python3.12工具环境，TMPDIR=/private/tmp；全量525/515执行通过/10PG跳过，专项15/10通过/5PG跳过，治理19通过；check0错误7已有SDK W003，迁移漂移无变化。PG新增两连接同名/同编号与停用先持锁、撤权先持锁、业务先提交的实际阻塞断言已编码，SQLite不作PG证明。当前PR实际CI另核。

非空旧库备份副本实际应用contracts0001，再次migrate无待执行；前后和重复快照22旧表39PK、4旧成本2+2+2+52=58分、审计来源字节相同，SHA04dc594d2a607a322c626efdd883332ebfe7b7a9c42a63a38c663d77d6d9ef82。原库未写入。独立合成浏览器DB，Chromium151真实登录、双角色party/联系人/合同draft创建编辑、客户改名由FK反映、错误保留与焦点、PM/auditor/member、1440/390与320至1440八宽无页面横溢；root读图。运行日志与截图留原项目目录的私有忽略runtime/ph212-browser（不在隔离工作区），未装入产品/发布包。没有生产/TLS/客户签收或新财务过账验收。

PH212远端补证：PR96最终9e3da6741dbe523a9a420a3f43d0ec810f1fa935，当前原8required+3准备检查及CodeQL汇总均SUCCESS，0未解决讨论，正常squash9dcfc825af85d3876baf0fb7c17636549cb053ca。PG17.11真实作业123297062026（Actions40828522014）525测试/517执行通过8非PG环境跳过60.073s，5新增PG竞争未跳过，当前新contracts0001迁移实际应用；与本机515执行/10PG跳过分别记录。PH213仅规划，不新增任何产品/PG/浏览器通过声明。

PH214本轮真实结果（尚未交付）：原生Kimi f3902885仅两schema completed且Controller/BASE/SHA验证；业务专项28中20通过8PG跳过。check_contract_cases19正37拒绝通过；validate_contracts因extension-freeze两schema摘要、check_fixture因fixture两schema摘要失败，不记通过。PH215独立规划只补必要四摘要值同步白名单，保持全部核算样例、冻结输入和检查不变；后续214实际修正并重跑，旧值Git/report追溯。

PH214实际验证：固定工具全544/531执行通过13PG跳过97.213s；专项34/26执行通过8PG跳过0.958s，包含独立发现的保存回放实时覆盖冲突RED→GREEN。严格schema新状态/版本与条款归属/唯一规则/实际事件反例通过，9schema及19正37拒绝、99正8拒绝全部通过。schema摘要失败→PH215独立范围PR101合入→仅四摘要更新，全财务oracle/输入及验证器/CI未动。

Chromium151真实三模板各配置/提交/finance独立批准并active，元2dp输入整数分、价款内质保；五角色、八宽无body横溢、键盘、3个准确版本链接；项目正式价税300000+18000=318000与旧预估9999分区分。root读1440及390。私有日志/图在原项目runtime/ph214-browser与ph214-validation。已有212数据备份副本0002/重复/完整性0：37旧表236行（3party/3contact/2draft）保留，新增5表及仅新增metadata权限类型；旧58分副本新0001/0002/重复保留22旧表39PK/审计来源指纹04dc...。当前PR实PG/CI仍待核，不把本机13skip称生产并发正确。


## PH216 月度人工业务规划（2026-10-29）

PH214合同首次批准与生效已由PR102合入5441aab。下一业务片PH217按ADR0006和具名spec冻结月度人工确认、公司成本一次、项目/非项目/未分配分配与追溯、撤分配及终态冲销；只在本规划独立S、当前CI、正常合入后获得可信BASE许可。本PR没有产品代码或运行验收；原141/142/159/160/161/210依赖及proposed保留，218为下一规划入口，不扩基础设施、不部署。


## PH217 月度人工业务候选（2026-10-30）

基线PH216 PR106已合入f1692a6。实际候选覆盖工资首次确认、同一工时、容量、守恒分配与未分配、追溯和原分撤销，当前in_review。旧账不重算，不将现金或预算余额称为利润；累计二次确认和暂估差额仍留原任务，不冒称完整1.0。独立S源码PASS及实际本机/浏览器/旧库证据见PH217报告；当前head CI和PG仍待核，不作为合入/生产或客户验收。


## PH217 已合入与PH218收入首片规划（2026-10-30）

PH217 PR107最终f21493a全12CI成功，正常合入eea63cc；最终PG17.11实际573项：564通过9非PG跳过，7项月池多连接实际执行，旧58及43表合同非空库原行值保持。本机/浏览器/升级与PG证据分别见报告和续接检查点，原候选段落保留其当时状态。1.0未全完成，无生产或客户验收。PH218仅规划219固定节点客户验收→管理收入→原分冲销与追溯，严格单一owner，收入不重复成本现金；原148/149/166等全部依赖/proposed不变。


## PH219 固定节点验收与管理收入候选（2026-10-30）

PH218 PR110已合入0b6b926后实现固定节点客户依据登记、独立验收、按批准合同原价累计取整、管理收入、原分冲销和明确替代链。复用真实节点和Contact，收入不记成本现金，账本与批准快照保持。独立S源码PASS，本机603项577执行通过26环境跳过、浏览器/旧库升级等实际分项见PH219报告；当前in_review，PG6项及精确head CI/正常合入仍待核。无生产/客户验收，不代表完整1.0，也不放行原proposed依赖。

## PH220 业务接入测试前置补充（2026-10-30）

PH219 PR111/e592275真实CI发现旧合同PG用例默认依赖前置不再成立（新验收/收入启用），以及业务环境可选jsonschema缺失。产品依赖拒绝是正确行为。本规划仅补两并发测试class setup范围和220依赖，不改产品门禁/SDK/CI/安装依赖/原断言；219仍未done，待新可信BASE实际测试/当前CI闭环。


PH219 当前源码1ab3ad2验收收口：PH220 PR113独立S/全12CI后正常合入f98755c，才在两个原合同PG class setup隔离新业务依赖；共同Fixture与原测试体保持。新默认依赖拒绝反例、无条件来源event验证保留，仅独立schema工具按实际依赖条件执行。本机605项101.443秒579通过26环境跳过；真实PG17.11当前CI605项95.546秒595通过10跳过，6新收入PG及原合同竞态实际执行，完整性ok=true/errors=[]。源码全部12CI成功/CLEAN/0讨论，独立窄修S35项21通过14跳过。最终元数据head仍须全部CI与正常合入；无生产/客户验收，完整1.0未完成。

## PH222 服务期客户确认与归属月份管理收入（2026-10-30）

固定 Python3.12 + DRF3.18.2 + TMPDIR=/private/tmp 实际执行：全量 645 项 OK（34 环境/PG skip）、专项 40 项 OK（8 skip，其中 25 服务层含 canonical 事件/审计与草稿锁期用例 + 7 HTTP 执行）、check 与 makemigrations 无问题、verify_integrity ok（47 entries）。迁移：全新库 migrate/seed 通过；旧 0.0.58 备份（36 表/47 LedgerEntry）与 PH219 非空业务库（2 验收/2 收入/合同行）副本实际升级，重复 migrate 无待执行，旧业务列逐表比对 0 变化（仅权限/内容类型/迁移记录/sqlite_sequence 预期增长）。契约 9 schema/2977 refs/137 requirements/71 commands/47 events、19 正 37 拒、fixture errors=[]。浏览器真实登录 1440/390/768/1024/1440、客户确认日期与服务月分列、逐月办理/反冲/重复400/锁月400/CSV/五角色/键盘/停用态证据见 runtime/ph222-browser/evidence.json（私有忽略，未装产品；修复前 403 与 768 溢出见 phases 留证，由 304d2b1/64a0704 修复）。草稿保存阶段已按 spec 增加覆盖月锁期/关期检查（2 条新用例）；月行 INSERT 触发器另校验 service_month 与覆盖月份一致；月行合计=父净额由服务层派生、读侧冻结校验与集成测试断言，未加 DB 聚合触发器。PG 多连接竞态由 CI PostgreSQL 作业执行，本机无 PG，不以 SQLite 冒充。首轮 PG CI 暴露三处真实缺陷（并发夹具未来日期、同一项目已批准服务段不可重叠导致第二合同构造不可能、触发器 RAISE 未映射 IntegrityError），已修为「上一完整月内两个不相交确认子区间」与「同一批准行两草稿并发审批」，PG 触发器 RAISE 使用 ERRCODE 23514；验证库已按更新后的迁移从 BASE 重建并重录升级/完整性证据，待本轮 PG CI 复跑确认。独立 S 复核与当次 CI 另核；无生产或客户验收。

## PH223 应收结算首片规划（2026-10-30）

146 task check、19 项治理测试、verify-change 实际通过；契约 validate_contracts 9 schema/2977 refs/71 commands/47 events、check_contract_cases 19 正 37 拒、check_fixture errors=[]（无 schema/fixture/hash 改动）。独立 S 复核先 PASS-WITH-BLOCKERS，四个阻塞（陈旧草稿需批准重算、due_date 必填与“可为空”冲突、全部条款含已消费口径、报告 review pending）已定点修正后复核落 pass。无 PH-224 产品代码、迁移、PG、浏览器、旧库升级或客户验收；本规划不构成产品通过。

## PH-226 应收结算业务接入测试前置补充（2026-10-30）

基线19b6781（PH-225规划合入）。本任务按冻结白名单只改4处测试前置：`tests/test_acceptance_revenue.py`与`tests/test_service_period_revenue_http.py`在停用delivery/contracts前，若插件目录存在settlement则先停用（`plugin_catalog()`判断）；`tests/test_v1_contracts.py`与`tests/test_contract_approval.py`并发类setUp的停用顺序改为`('management_revenue','settlement','delivery')`，沿用既有`get_registry().manifests`守卫。产品/manifest/门禁/断言均未改。

实际执行：`manage.py test tests.test_acceptance_revenue tests.test_service_period_revenue_http`=28项通过；`manage.py test tests --verbosity 1`=645项通过、34项环境跳过（本BASE不含settlement，适配分支为no-op）；`manage.py check`/`makemigrations --check`/`verify_integrity --json` 通过；独立复核落 pass 后实际重跑 `projectctl check` 与 `verify-change`（ok=true）。含settlement的真实行为与PG并发执行由PH-224在其rebase后的BASE验证。未验证：PH-226改动在含settlement BASE上的行为（待PH-224）。


## PH-224 客户结算、到期/质保未清与账龄首片（2026-10-30）

基线83a2577（PH-226测试前置合入）。新增settlement插件（结算/未清项/收据、SQLite+PG守卫迁移0001/0002）、条款行组合结算服务、只读账龄、6条路由与4个模板、两个跨owner只读接口、verify_integrity分支。金额按整行累计比例HALF_UP且累计差守恒（53000/50000/3000、42400/40000/2400、10600/10000/600 → 106000/100000/6000）；批准公司锁内按当前累计重算并拒绝陈旧草稿；条款一次性消费；不做现金/核销/会计事件/账本写入。
实际执行：全量680项通过、40项环境跳过（含6项PG-only并发）；settlement+http+concurrency目标35项通过（本机6项PG skip）；check/makemigrations/verify_integrity通过；真实结算浏览器库与新建种子库完整性ok；旧库与非空库升级后完整性ok且既有列无变化；浏览器1440/390五角色/CSV/停用态/键盘/无横溢/0个500；独立S复核两轮。PostgreSQL并发由PR的PG CI实跑。未验证：生产/UAT、核销/折让/应付/发票/反冲/期初。


## PH-227 现金事实首片规划（2026-10-30）

基线f484a2e（PH-224应收首片合入）。登记PH-228“现金事实首片：银行账户、银行流水与往来方匹配（无过账）”（ready，依赖PH-214/PH-219/PH-222/PH-224）与PH-229规划槽；PH-227完成。本片只规划PH-169第1片：FundingAccount/CashTransaction/CashPartyMatch的草稿登记、external_source_key稳定去重、往来方identify/reverse、只读投影与角色边界；明确不写AccountingEvent/LedgerPosting/LedgerEntry/收入/成本/应收/应付/未清，不做过账/分类/核销/退款/更正/转账/文件导入/期初。实际执行check与19项治理测试；verify-change在review落pass后重跑。未验证：无产品代码/迁移/测试/浏览器/PG/CI；PH-169未完成。


## PH-228 现金事实首片（2026-10-30）

基线200a507（PH-227规划合入）。新增cash插件：FundingAccount（草稿/激活，active不可变）、CashTransaction（org+账户+external_source_key唯一、payee三类、evidence≥1、未来日/锁账/关期拒绝、仅draft可改）、CashPartyMatch（identify/reverse、reverse需match_ref+reason、部分唯一防重复反向）、CashReceipt（幂等）；9条路由/5模板/导航与财务入口；迁移0001/0002双实现守卫；verify_integrity分支。明确不写会计事件/账本/收入/成本/应收/应付/未清/消费，不做现金过账/分类/核销/退款/更正/转账/导入/期初。
实际执行：cash 23项通过（18+5），PG并发3项本机skip由CI实跑；全量706项通过、43项环境跳过；check/makemigrations/verify_integrity通过；旧库与非空库升级后完整性ok；浏览器证据见runtime/ph228-browser。未验证：PG并发、生产/UAT、过账/分类/核销/退款/导入/期初；PH-169未完成。


## PH-229 现金过账与账户覆盖规划（2026-10-30）

基线35e0a25（PH-228现金事实首片合入）。登记PH-230“现金过账与账户覆盖”（ready，依赖PH-214/219/222/224/228）与PH-231规划槽；PH-229完成。本片只做draft→posted、恰一条cash.transaction_recorded.v1规范事件、只读账户覆盖（available=posted net、cleared=0）与过账守卫；明确不写AccountingEvent/LedgerPosting/LedgerEntry，不做分类/分配/核销/退款/更正/转账/导入/期初，不解除PH-169/170/171 proposed。实际执行check与19项治理测试；verify-change在review落pass后重跑。未验证：无产品代码/迁移/测试/浏览器/PG/CI；PH-169未完成。


## PH-230 现金过账与账户覆盖（2026-10-30）

基线bac9d33（PH-229规划合入）。实现草稿→posted私有过账服务（条件更新一胜、receipt 重放、occurred_on/posting_period 一致性、未来日/锁账/关期拒绝）、恰一条cash.transaction_recorded.v1规范事件（posting_event_refs=[]）、posted不可变（模型守卫+0003 SQLite/PG触发器）、只读账户覆盖估算（posted_in/out/net_estimate/available_estimate/cleared=0，UI标注非现金余额）、流水status_display/can_post/posted_at与详情/列表过账UI、verify_integrity过账/事件/覆盖分支。明确不写AccountingEvent/LedgerPosting/LedgerEntry，不做分类/分配/核销/退款/更正/转账/导入/期初。
实际执行：全量722项通过、45项环境跳过（含5项cash PG-only并发）；cash目标37项通过（22+7+8）；check/makemigrations/verify_integrity通过；旧库/非空库升级完整性ok；浏览器证据见runtime/ph230-browser。未验证：PG并发、生产/UAT、分类/账本/核销/退款/导入/期初；PH-169未完成。


## PH-231 核销首片规划（2026-10-30）

基线a8edb17（PH-230现金过账合入）。登记PH-232“核销首片：已过账现金对未清项的部分核销与撤核”（ready，依赖PH-214/219/222/224/228/230）与PH-233规划槽；PH-231完成。本片实现canonical settlement.clearance.create/reverse的私有服务语义：部分核销、上限=双方剩余、撤核=反向记录不改写原记录、恰一条cleared/clearance_reversed规范事件、余额由Clearance聚合派生；不做Offset/退款/手续费/转账/批量/导入/期初/账本/会计/收入/成本，不解除PH-170/171 proposed。实际执行check与19项治理测试；verify-change在review落pass后重跑。未验证：无产品代码/迁移/测试/浏览器/PG/CI；PH-170未完成。



## PH-233 交付效率规划（2026-10-30）

基线c4348ac（PH-231规划合入）。纯规划片：登记PH-234/PH-235实现契约（ready，依赖PH-233）与PH-236规划槽，PH-233置done。无产品代码、无迁移、无测试改动。实际执行check与治理测试；verify-change在独立S复核落pass后重跑。量化基线与优化目标记录见delivery-status同名条目；PH-235/234完成后按同口径复测。未验证：无产品/浏览器/PG/CI证据；优化收益本身待PH-235/234交付后实测，不预先宣称达成。


## PH-235 projectctl会话效率命令（2026-10-30）

基线7ca6004（PH-233规划合入前头）。scripts/projectctl.py新增command_orient/_acceptance_commands/_ready_tasks/_compact_task/command_draft_report与next --compact参数；command_next判定逻辑原样提取为_ready_tasks。tests/test_projectctl.py末尾追加4个测试（compact字段集、orient事实与缺源报错、draft骨架禁pass），既有16个测试未动。实际执行：23项治理测试通过（19项projectctl含4个新增+4项governance）；BASE版vs HEAD版next/brief输出diff逐字节一致；真实repo上orient/draft-report冒烟通过。未验证：全量产品测试套件（本片不触产品代码，CI全量覆盖）；实际token/时间收益待PH-234合入后按PH-233基线复测。


## PH-234 会话上下文减负文档改造（2026-10-30）

基线7605fa9（PH-235命令合入前头）。纯文档片：delivery-status新现状头1996字节（≤2KB，wc -c实测），原60KB全文逐字进delivery-status-archive.md（diff核对无改写）；AGENTS.md仅改开场两段，四条验收命令与财务/插件/双层AI条款原样；四篇ai-delivery-*经git mv入archive/（内容零改动，git diff确认rename 100%）；engineering/README与v1.0-development/README各加归档标注/查询护栏。实际执行check与23项治理测试。未验证：全量产品套件（CI覆盖）；实际收益复测按PH-233基线待做；ROADMAP/execution-protocol旧链接修复不在本片白名单，留PH-236。


## PH-210 核算契约缺口修正（2026-10-30）

基线7a2cc17。设计契约薄片：AccountingEvent新增单一server-only只读reverses_event_ref（非空仅core/accounting_event）、SourceConsumption新增cash内部用途并永久保留原bank_effect、event-reversal补只读方向投影、客户端DTO不放开该字段；未改产品代码/迁移/财务口径，不声明服务已强制。固定jsonschema4.26环境prep7测试全执行0skip，新增边界6正10拒绝，原19正37拒绝与99正8负保留；validate_contracts/check_contract_cases/check_fixture、check与23项治理实际通过。摘要仅刷新5个canonical；旧freeze证据保留。未验证：来源resolver/批准/期间/持久模型/服务拒绝/PG/浏览器/客户/生产，141与后续实现仍待。

## PH-236 交付效率收尾规划（2026-10-30）

基线7a2cc17（PH-234合入）。纯规划片：登记PH-237（ready，S——由A校正，因其白名单含project/tasks.json属CRITICAL_PATHS）与PH-238规划槽，PH-236置done，active_handoff指向PH-236。无产品代码。实际执行：23项治理测试通过；check与verify-change在独立S复核落pass后按真实输出记录（独立复核首轮打回，指出首版报告的pass声明不可复现且A/S矛盾；已更正证据顺序与风险级后二次复核）。未验证：PH-237未实现；墙钟时间复测需日常使用统计，不预设结论。


## PH-232 核销首片（2026-10-30）

基线b931be0（PH-237合入后）。在PH-230已过账现金与PH-224未清项之上实现“核销→部分结清→撤核”闭环：部分核销上限=双方剩余、撤核为原记录单字段active→reversed（余额=Σactive）、规范cleared/clearance_reversed事件、账龄/详情/CSV显示cleared/remaining；verify_integrity补引用同org、双侧余额非负、无账本/会计事件、每clearance恰一条cleared与reversed事件envelope；PM对自己管理项目只读、cash候选接口owner/finance/auditor专属。明确不写账本/会计/收入/成本，不做Offset/退款/手续费/转账/批量/导入/期初，PH-170仍proposed。
独立S复核（fresh context）PASS，0阻断，实际重跑命令与差异逐条核对。实际执行（Lead rebase后同一HEAD）：全量753项中已知8项macOS环境限制失败（与main同基线一致、无新增）；clearance scoped 30项27过3项PG-only skip；check/makemigrations/verify_integrity通过；verify-change ok。未验证：PG并发3项由PR CI实跑；浏览器视觉复检、生产/UAT未验证；Offset/退款/账本/分类/导入/期初未实现。

## PH-237 文档收尾（2026-10-30）

基线5ee8af1（PH-236规划#130合入）。三处文档改动：ROADMAP.md与execution-protocol.md的ai-delivery-*旧链接改指archive（grep确认各自唯一改动点、目标文件存在）；CONTRIBUTING.md「环境与验收」段插入作用域测试指引一段（开发中模块级`--parallel`快速反馈、收尾全量一次作报告证据、CI按PR全量兜底；四条验收命令与其余部分diff为空）。独立复核（fresh context，与PH-236同轮双片审）内容PASS。实际执行：check、治理测试23项、`manage.py test tests.test_projectctl --parallel`作用域自证、verify-change均于最终HEAD实跑通过。未验证：全量产品套件本地未跑（CI覆盖）；墙钟时间复测待日常统计。 量化口径校准（PH-246，固定 SHA 复现）：基线 7605fa9 = manifest 663 + tasks.json 217565 + PH-233 handoff 1510 + delivery-status 61218 = 280956 字节；最终路径 b931be0 = orient 3423 + brief PH-232 1893 + delivery-status 1996 + PH-232 spec 8448 = 15760 字节；降幅 1−15760/280956 = 94.39%≈94.4%。复现命令与逐分量输出见 project/reports/PH-246.json。


## PH-238 后续交付独立规划入口（2026-10-30）

基线3c21a29（PH-237 PR#131、PH-210 PR#132、PH-232 PR#133已合入，本分支只含PH-238）。纯规划片：登记PH-239（现金分类与账本桥接首片，ready——依赖PH-210/PH-230均done）、PH-240（退款与银行退回，proposed——依赖PH-230/PH-232均已done，按Lead分工晋级动作留给PH-242复核）、PH-241（预收/预付抵扣Offset首片，proposed——缺PH-239 done），各冻结字段/状态机/权限/幂等/金额/迁移/白名单/反例/验收命令/risk S；登记PH-242下一规划槽（ready，依赖PH-238 done）。PH-157/PH-158按spec判断不重复登记（PH-110 ready未done、其余上游proposed）。实际执行（最终HEAD）：check ok=true/task_count=164；治理测试23项OK；verify-change --base 3c21a29 --task PH-238 ok=true、9个变更文件全在白名单；作用域测试19项OK。独立S复核首轮打回2条（PH-241迁移号、PH-157/158依赖事实），08671ab二次复核PASS；随后rebase到3c21a29仅带入main的PH-232合入并同步PH-240/241晋级理由文字。未验证：PH-239/240/241均未实现，无产品代码/迁移/浏览器/PG/CI证据；PH-240/PH-241晋级由PH-242复核。


## PH-242 后续交付独立规划入口（2026-10-30）

基线df01896（PH-228/230/232/237/238已合入）。纯规划片：登记PH-243（PH-239契约修正与白名单扩权，ready）、PH-244（手续费与净额到账草稿事实/规范事件/对账，ready；批准与成本过账延后）、PH-245（内部账户互转，proposed）、PH-246（PH-237量化证据统一94.4%，ready）、PH-247（下一规划槽，ready）；PH-240按已done依赖（PH-230/PH-232）晋级ready；PH-239因硬前置PH-243未done保持proposed并写blocker；PH-241保持proposed。各spec冻结字段/状态机/权限/幂等/金额/迁移/白名单/反例/验收命令/risk S。实际执行（证据 HEAD 0c2103d）：check ok=true/task_count=169；治理测试23项OK；verify-change --base df01896 --task PH-242 ok=true、13个变更文件（含本文件）全在白名单。abfc9d9 为证据 commit 前的计数（12 文件、当时本条目尚未加入）；其后的 provenance 更正 commit 不改变已验证的规划内容（任务状态/白名单/spec 均未变），仅修正本报告/本条目的证据表述。独立S复核共三轮：首轮3阻断+4NOTE、二轮1阻断（冻结句与重分类冲突），修复后三轮PASS（HEAD c342659）。未验证：PH-243/244/245/246均未实现，无产品代码/迁移/运行时/浏览器/PG/CI证据；PH-246三处数字一致性grep待其执行时实跑。


## PH-244 银行手续费草稿与净额对账首片（2026-10-30）

基线e91346a（PH-242合入后）。costing owner新增BankCharge草稿事实（每原始posted收款流水至多一条，gross≤现金gross、公司承担、项目范围、existing_cost_ref固定null、税额可空、期间/锁期/未来日校验）与BankChargeReceipt幂等收据；每次保存/改稿写generic audit、恰一条canonical costing.bank_charge_saved.v1（source=bank_charge ref、payload posting_event_refs=[]）及新版本；提交/批准/驳回、core.document.decide handler、成本过账、ReceiptFeeSettlement、existing_cost_ref、counterparty-borne明确延后PH-247。只读列表/详情/净额对账：approved_fee恒0、implied_gross=posted_net、draft_fee仅展示、reconciled恒false；停用模块写403、历史读仍允许（服务层）。root_only三文件（cash/transaction_services.py新增posted_cash_for_fee只读、verify_integrity手续费分支、delivery/urls绑定）由单worker承担并记report.deviations；无core模型/迁移变更，costing迁移0003双实现触发器（禁删、状态/版本/draft改稿保护）。

实际执行（最终HEAD）：projectctl check ok=true/task_count=175；manage.py check exit0；makemigrations --check --dry-run无变更；全量772 tests 111.552s，5 failures/3 errors均为基线已知macOS环境限制（6 DRF Accept/codec/large-JSON、2 delivery symlink），55环境skips，无新增失败；verify_integrity --json在全新迁移SQLite上ok=true；scoped 13 tests OK。13新增测试含服务层（事件envelope/幂等/dry-run/改稿版本/唯一性/期间/权限/停用/history read/DB触发器/verify反例）与HTTP（角色/CSRF/非法字段/批准越权/草稿流水/改稿）。未验证：无PG并发（本片无并发锁外场景，CI覆盖）、无浏览器1440/390实测（模板按平台规范但未跑截图/键盘）、无真实附件/客户/生产；canonical command通道未接通（私有服务/浏览器适配，报告披露）。

独立S复核（fresh context, HEAD efdfaa7）实际重跑6条验收命令、与当前main基线（ceded2e）逐项核对失败名称一致，PASS 0阻断；非阻断残余为verify_integrity仅验证latest saved事件envelope、未断言每次保存恰一条事件计数，留PH-247/后续加固。


口径校准（PH-254）：全量口径以 PH-244 合入后的 main 为准——PH-244 基于 merge base ceded2e、合入 main 为 37b5fdf，合入后 main 实测为 773 tests、56 environment skips，失败集仍为同一 8 项 macOS 环境限制（6 DRF Accept/codec/large-JSON、2 delivery symlink）；上段 772/55 是 rebase 前 base e91346a 的测量，仅作历史对比。PH-254 在 BASE 26d0408（main 已含 PH-239/PH-243/PH-248/PH-250/PH-253 等切片）按同一命令实际重跑为 849 tests/118.423s、5 failures/3 errors、62 environment skips，失败集名称与上述 8 项完全一致、无新增；该计数高于 PH-244 合入时是因为 BASE 测试集已扩展，PH-244 全量证据行同步更新，详见 PH-254 报告。
## PH-243 分类桥接 canonical 契约修正（2026-10-30）

基线e91346a（PH-242合入）。纯契约/测试/规划片：events.schema.json 两个classification事件posting_event_refs改1/1且items绑定core/accounting_event（reversed namespace cash→core），5个其它0/0 cash事件未动；entities SourceConsumption invariants[5]逐字替换冻结句；catalog-completeness仅补注解；extension-freeze仅更新3个受影响canonical摘要+显式supersede cash-extension.plan.json#/service_semantics/classification_reverse/4（保base_sha/201/input_sha256/历史）；fixture仅3个contract_artifact_sha256；tests/test_v1_preparation.py新增严格正反例（reserved原19/37、99/8计数）。实际执行：check在独立S复核落pass后按最终HEAD记录；治理测试23项OK；validate_contracts/check_contract_cases/check_fixture严格.agent-venv全pass；PH135_REQUIRE_SCHEMAS=1 prep测试8项OK 0 skip（新增positive=3/rejected=9）。PH-239 allowed_paths扩core/models.py+core/migrations/*.py+docs/v1.0-development/verification.md并晋级ready。未实现PH-239业务；core 0009迁移、verify_integrity改造、PG/浏览器/生产均未验证。

补充（最终证据 HEAD）：独立 S 复核 PASS，0 阻断；check ok=true/task_count=169；verify-change --base e91346a --task PH-243 ok=true、13 文件全在白名单；严格 .agent-venv + PH135_REQUIRE_SCHEMAS=1 的 prep 测试 8 项 OK 0 skip（PH243 positive=3/rejected=9；PH210 6/10；fixture 99/8 计）。residual（非阻断）：PH-243 spec 正文“PH-247 晋级 PH-239”与实际（本次已置 PH-239 ready）不一致，由 PH-247 规划槽消解，handoff 已记。


## PH-246 PH-237 量化证据修正（2026-10-30）

基线ceded2e（PH-243合入）。纯证据修正片，仅三处：project/reports/PH-237.json 的量化测量条目、docs/delivery-status-archive.md 的 PH-237 条目数字/公式、docs/verification.md 的 PH-237 校准句；三处统一为固定 SHA 复现口径：基线 7605fa9 = manifest 663 + tasks.json 217565 + PH-233 handoff 1510 + delivery-status 61218 = 280956 字节；最终路径 b931be0 = orient 3423 + brief PH-232 1893 + delivery-status 1996 + PH-232 spec 8448 = 15760 字节；降幅 1−15760/280956 = 94.39%≈94.4%。实际复算：上述四分量与四分量逐条 wc -c 实跑一致（280956/15760），`round((1-15760/280956)*100,2)` = 94.39；grep 确认三文件无 94.5%/94.6%/281349/15360 旧值且均含 94.4%。未改 PH-237 三处正文（ROADMAP/execution-protocol/CONTRIBUTING）、未改写 archive 其它历史条目、未改 PH-237 review 块与历史 pass 语义。未验证：全量产品套件本地未跑（本片 docs-only，CI 覆盖）。独立 S 复核 PASS，0 阻断；NOTE（grep 证据中 verification.md 的 94.4% 出现次数应为 3 次而非 2 次）已在最终证据 commit 修正。

## PH-240 退款与银行退回（2026-10-30）

基线e91346a（PH-242规划合入、含PH-232核销）。新增cash Refund/BankReturn append-only事实与0004_refund_guards迁移（同org插入校验、UPDATE/DELETE拒绝；产生的posted现金沿用PH-230不可变守卫）；refund_services实现原现金覆盖内真实退款（direction=in原现金→新posted out现金，服务器生成来源编号、证据引用原现金、Refund事实、generic audit、恰一条cash.refunded.v1、action=refund收据）与银行退回（方向取反、BankReturn、cash.returned.v1、action=bank_return收据）；可退额=posted gross−active核销−active退款−active退回，服务端派生、不为负。为使退款×核销联合覆盖对称（Lead裁定A），settlement新增只读clearance_coverage_for_cash，create_clearance.remaining_cash与cash_candidates_for_open_item.available_cents改为扣除退款/退回（核销自身语义/事件/金额口径不变；PH-232 spec文字未同步，由PH-247登记口径同步小修）。只读cash_refund_coverage/list/snapshot/provenance；UI退款/退回列表、表单、现金详情区；verify_integrity新增退款/退回引用同org、方向/日期/金额、两类canonical事件计数与envelope、覆盖非负、无重复external_source_key、无cash会计事件/账本。明确不写账本/收入/成本/应收应付/未清/核销（只读），不做Offset/手续费/转账/更正/导入/期初/预测。
实际执行（最终 rebase 到 0721ad4 后）：scoped退款三模块20项（17通过+3项PG并发本机skip）；全量793项（726通过、59环境skip、8项本机环境限制失败=6项DRF+2项macOS symlink，与基线一致、CI Linux为权威）；manage.py check exit0（53条既有W003警告、0错误）、makemigrations无变更、verify_integrity在全新迁移SQLite上ok=true；projectctl check ok=true/task_count=175；verify-change --base 0721ad4 ok=true、22个变更文件全在白名单。独立S复核第1轮打回1阻断（verify_integrity新增的cash-namespace全局账本禁令会误杀PH-243/PH-239合法现金分类桥接），修复为仅退款/退回provenance守卫（source kind∈{refund,bank_return}或source直接指向退款/退回产生现金），显式允许source kind=cash_classification的桥接，并加正向边界回归（含“对退款产生现金再分类桥接”场景）与负向退款账本反例；第2轮PASS 0阻断。未验证：PG并发、浏览器1440/390、生产/UAT、旧库升级；新现金账本桥接属PH-239。

## PH-247 后续交付独立规划入口（接续 PH-242）（2026-10-30）

基线0721ad4（PH-244/PH-246合入后）。纯规划片：消解PH-243 spec中「PH-247晋级PH-239」措辞（PH-239已在PH-243交付时ready）；登记PH-249（PH-232覆盖公式口径同步，proposed——缺PH-240 done）、PH-250（手续费提交/批准与成本过账首片，ready——依赖PH-210/PH-244 done）、PH-251（手续费净额结清/既有成本/承担方后续片，proposed）、PH-252（发票登记与逐行分配首片，proposed——缺PH-150/PH-155 done）、PH-253（PH-244 verify_integrity saved-event计数加固，ready——依赖PH-244 done）；登记PH-248下一规划槽（ready，依赖PH-247 done）。PH-241/PH-245仍proposed（依赖PH-239 ready未done）。各spec冻结字段/状态机/权限/幂等/金额/迁移/白名单/反例/验收命令/risk S。实际执行：check、23项治理测试；verify-change在独立S复核落pass后按最终HEAD重跑记录。未验证：PH-249/250/251/252/253均未实现，无产品代码/迁移/浏览器/PG/CI证据；review由独立S复核。

补充（最终证据 HEAD 8346d55）：独立 S 复核两轮——首轮 2 阻断（PH-251 spec/tasks 白名单不一致+残留文本；PH-253 与 PH-250 approved/cost 事实冲突）+4 NOTE，修复后二轮 PASS；最终 check ok=true/task_count=175，verify-change --base 0721ad4 --task PH-247 ok=true、13 个变更文件全在白名单。**PH-249 依赖现状**：PH-240 已在本任务 BASE 之后以 a760cf7（PR #139）合入 main，故“缺 PH-240 done”在最新 main 已解除；但本片按冻结 BASE 0721ad4 保持 PH-249 proposed，晋级 ready 属 PH-248 规划槽动作，本片不改状态、不自我授权 rebase。

## PH-253 verify_integrity saved-event 计数加固（2026-10-30）

基线1f02520（PH-244/PH-247合入后）。纯加固片：`core/management/commands/verify_integrity.py` 手续费分支从“仅验证 latest saved 事件 envelope”升级为——每 BankCharge 的 `costing.bank_charge_saved.v1` 事件数必须恰等于其 version，逐事件校验 source=`costing/bank_charge` 稳定 ref、source.version 对应版本、topic/schema_version/changed_refs=[该版本 ref]/posting_event_refs=[]，缺失/多出/旧版本损坏/错版本覆盖均报 corruption；status 接受 canonical 五枚举（draft/pending/rejected/superseded 不得有成本事实；approved 无成本事实为 PH-250 未落地过渡态，存在成本事实时要求恰一个 claim（consumer=cost/coverage_key=bank_charge:<id>/金额=gross）+ 恰一条 cost AccountingEvent（event_type=costing.bank_charge.cost、effect=gross、发生日/期间=冻结值）+ 恰一条 cost LedgerPosting（book=cost、origin=current、scope=project、金额=gross），重复/缺失/金额不符/claim 不符/孤儿成本事件与孤立 claim 均报错；labor 事件循环放行 PH-250 的 costing/bank_charge 成本事件（仅由本手续费分支校验）。`tests/test_v1_bank_charge.py` 追加 11 个损坏探测/生命周期用例（空库与正常草稿/改稿、中间版本事件删除、同版本重复事件、篡改 source.version/changed_refs、approved 过渡态、approved 合法成本事实、重复成本事件、缺失 posting、金额不符、unapproved 携带成本事实、orphan 成本事件/claim）。

实际执行（最终HEAD）：projectctl check ok=true/task_count=175；manage.py check exit0（仅既有 urls.W003）；makemigrations --check --dry-run无变更；`verify_integrity --json` 全新迁移空库 ok=true/errors=[]；临时SQLite探针库：正常草稿/改稿（version=2）ok=true，删除中间版本 saved 事件后 ok=false，报 bank_charge:<id>:saved_event_count 与 saved_event_version_coverage（exit 1）；scoped 19 tests OK；治理23项OK；全量804 tests/114.456s，5F+3E 与基线已知 macOS 环境限制名称完全一致、59环境skips、无新增失败（本分支相对基线新增11测试）。未验证：PH-250 真实审批/成本过账服务未落地，approved 合法成本事实仅以合成 core 事实验证；本机无PG（触发器/事务路径由CI）；无浏览器变更。
独立S复核（fresh context, HEAD 70e898e）实跑6条验收命令并与基线核对：PASS 0阻断；4条NOTE已记report.unverified（approved过渡态须由PH-250产事实且不改version、逐charge查询、直接DB跨org伪造事件/claim低风险、draft/superseded成本事实与cost event source.version未单测），PH-250实现遵循version不随submit/decide变化。

## PH-239 现金分类与账本桥接首片（2026-10-30）

基线1f02520（main，含PH-240/244/246/247；PH-243契约修正已合入；PH-247为docs-only，迁移号不变）。新增cash CashClassification/CashClassificationReversal与`0005_cash_classification_guards`迁移（PH-240先合入占0004，故本片顺延0005，依赖0004_refund_guards；同org插入、仅active→reversed单字段、禁删、reversal不可变；每流水至多一条有效分类）；classification_services实现分类/撤分类（posted/同org/方向/advance有效往来方/期间/锁期/未来/证据/幂等/dry-run）与只读snapshot/list、cash_book_coverage。按PH-243冻结契约落账本桥接：core/cash_accounting.py具名接口为每条有效分类恰写一个AccountingEvent（event_type=cash.classification、source=分类ref、occurred_on=流水日期、算法常量）与恰一条LedgerPosting（book=cash_in/out、amount=+gross、scope_kind=funding_account、scope_id=账户、origin=current）；撤分类追加镜像事件（reverses_event_ref=原事件、claim_effect=-gross）与负值posting（reverses_posting_id=原posting），原事件/原posting不改不删；bank_effect SourceConsumption每流水唯一永久、撤后重分类复用且要求现存事件净额0；canonical recorded/reversed事件posting_event_refs恰1个core/accounting_event。core models保留约束名labor_posting_cost_scope并放宽为cost↔project/nonproject或cash_in/out↔funding_account/legacy_project（core 0009，origin/current与金额边界保留）。verify_integrity与PH-240合并后保留退款/退回provenance守卫、删除旧两处“cash不得有会计事件/claim”全局禁令、generic loop放行cash，并新增七项分类桥接不变式（引用同org、单有效分类、事件恰一条与envelope含恰1 posting ref、event/posting正负互反配对、per-分类与per-流水净额、claim唯一与净额一致、无cost/revenue/未清/核销行；批量聚合）。明确不做payroll/CashAllocation/Offset/退款/手续费/互转/更正/导入/期初，不解除PH-169/170/171 proposed。
复核round 1三阻断修复：①classification_list按真实月份区间筛选（原DateField startswith只匹配每月1日）；②按冻结spec恢复transfer内部枚举（model/DB含transfer，客户端命令仍拒绝，新增非路由内部服务供PH-245，本片不暴露transfer入口）；③verify_integrity现金块预取reversal/classification映射消除N+1并加CaptureQueriesContext常量查询回归，另补“伪造错误金额桥接”“同一分类第二组桥接”两个反例。实际执行（最终HEAD）：scoped分类三模块29项（26通过+3项PG并发本机skip）；全量822项（752通过、62环境skip、8项本机环境限制失败=6项DRF+2项macOS symlink，与基线一致、CI Linux为权威）；manage.py check exit0（58条既有W003警告、0错误）、makemigrations无变更、verify_integrity在全新迁移SQLite上ok=true；projectctl check ok=true/task_count=175。独立S复核round 2 PASS（3阻断全部消除；round-1 findings历史保留）；reviewer NOTE（CLIENT_CLASSIFICATION_KINDS未使用）已用客户端分类校验消解。未验证：PG并发（CI）、浏览器1440/390、生产/UAT、旧库升级。

## PH-250 手续费提交/独立批准与一次性成本过账（2026-10-30）

基线c1cd18d（PH-239/PH-253与PH-244/PH-247合入后；本片 rebase 合并 PH-253 逐版本 saved 检查与 PH-250 approved 成本事实校验）。costing BankCharge 增加审批链字段（submitted_by/at、approved_by/at、reject_reason）与 costing 迁移0004：状态机触发器替代PH-244 draft-only 守卫——draft改稿仍 version+1；submit（draft→pending）不改 version、冻结草稿事实并记录提交人；decide（pending→approved/rejected）不改 version、记录独立审批人与驳回原因；自审/非法跳转/改事实被DB拒绝；禁删。服务 `submit_bank_charge`/`decide_bank_charge`（公司锁、期间/锁期/未来校验、幂等 BankChargeReceipt action=bank_charge.submit/decide、同token重放/改输入冲突、创建人或提交人不得审批、驳回必填原因、并发仅一胜）；批准时同事务写一次性公司成本：SourceConsumption(consumer=cost, coverage_key=bank_charge:<id>, amount=gross, source=bank_charge ref) + core AccountingEvent(event_type=costing.bank_charge.cost, occurred_on/posting_period=冻结值, effect=gross) + 恰一条 LedgerPosting(book=cost, origin=current, scope=project, amount=gross)，通用审计 costing.bank_charge.submitted/approved/rejected/cost_posted，无独立 canonical topic（按spec不新增/不伪造），saved 事件仍只由保存路径产生；`core.services.decide_document` 提供 core.document.decide 的 bank_charge handler（私有服务/浏览器承载，命令通道未公开）。视图/表单增加提交、独立批准、驳回动作与审批链/成本引用展示。verify_integrity 手续费分支改为五状态兼容：pending/rejected/superseded 禁成本事实；approved 校验审批人存在且≠创建/提交人、驳回原因口径、成本事实恰一 claim/event/posting（金额=gross、scope=project、cost账）；PH-253 兼容口径下 approved 无事实视为过渡态不报错（PH-250 服务保证新批准必产事实），孤儿成本事件/杂散 fee claim 检测保留，labor 事件循环放行 fee 成本事件。

实际执行（最终HEAD）：projectctl check ok=true/task_count=175；manage.py check exit0（仅既有54项 urls.W003警告）；makemigrations --check --dry-run无变更；migrate+verify_integrity全新SQLite ok=true/errors=[]；临时SQLite探针：draft→submit→独立批准后 approved/version=1/claim=1/cost event=1，verify_integrity ok=true；复制一条cost事件后 verify exit1 报 bank_charge:<id>:cost_event_count；scoped PH-244+PH-250 24 tests OK；全量809 tests/142.014s，5F+3E为基线已知8项macOS环境失败（名称一致）、59 skips、无新增（基线793 + 本片16测试）。未验证：ReceiptFeeSettlement/existing_cost_ref/counterparty/税额深化（PH-251）、发票（PH-252）；无本机PG并发（CI双连接覆盖并发decide）；无浏览器1440/390实测；真实客户/生产。

独立S复核（fresh context, HEAD ed3e742）实跑6条验收命令并与基线1f02520核对：PASS 0阻断；5条NOTE记report.unverified（与PH-253的verify_integrity合并顺序/UI can_decide UUID-字符串比较仅显示层/冻结字段防御性复述/approved过渡态/浏览器与PG并发未本地验证），未在复核后改码。

### PH-250 最终集成 rebase（c1cd18d）

rebase 到 c1cd18d（PH-239/PH-253 合入后）并合并 verify_integrity 共享手续费分支：保留 PH-253 逐版本 saved_event_count/version_coverage、PH-239 现金账桥接块、PH-240 退款守卫，加入 PH-250 审批链与 approved 成本事实校验（链强制以链状态/append-only submit/decide 收据为条件，兼容 PH-253 合成过渡态）；SQLite 触发器名沿用 `costing_bankcharge_update_guard` 以兼容 PH-253 force_status helper。NOTE② 修复 can_decide 字符串比较 + HTTP 回归。post-rebase 实跑：check ok=true/175；makemigrations 无变更；migrate+verify_integrity 空库 ok=true/errors=[]；批准探针 ok=true、复制成本事件后 exit1 cost_event_count；scoped 35 tests OK；全量 849 tests/116.240s，5F+3E 为基线已知 8 项（名称一致）、62 skips、无新增（基线 833 + 16）。

## PH-248 后续交付独立规划入口（接续 PH-247）（2026-10-30）

基线c1cd18d（PH-239/PH-253合入后）。纯规划片：晋级 PH-249（PH-232 覆盖公式同步；PH-240 done a760cf7，公式经实现复核 gross−cleared−refunded−returned，`refund_coverage_for_cash.total_cents` 聚合退款+退回）、PH-241（Offset；依赖 PH-210/224/230/232/239 全 done，advance 分类/桥接复用 PH-239 已实现事实）、PH-245（内部互转；依赖 PH-239/PH-244 done；spec 写明复用 PH-239 非路由内部服务 `record_internal_classification(member, *, cash_transaction_id, evidence, occurred_on, posting_period, request_token, dry_run)`，transfer 仅内部、客户端仍四值，与 PH-244 手续费无耦合；cash 下一迁移号 0006）。登记 PH-254（PH-244 全量证据 772/55→773/56 口径修正，ready，deps PH-244 done）、PH-255（共享费用分摊首片，proposed，缺 PH-162/PH-153）、PH-256（导入预览与冲突清单首片，proposed，缺 PH-181/PH-185 且 importing app 归属待前置决策）、PH-257（期间预检与对账差异清单只读首片，proposed，缺 PH-180/PH-181 且 insights app 归属待决策）、PH-258 下一规划槽（ready，deps PH-248 done）。count 180。实际执行：check、23项治理测试；verify-change在独立S复核落pass后按最终HEAD重跑记录。独立 S 复核两轮：首轮 PASS（2eb00dc 后被终审打回 1 阻断+2 NOTE：PH-241 spec 白名单漏 3 路径、PH-245 角色校验措辞、PH-249 返回字段），修复 HEAD 1a684d3 后二轮 PASS 0 阻断。最终证据 HEAD 59bf686：projectctl check ok=true/task_count=180；verify-change --base c1cd18d --task PH-248 ok=true、13 文件全在白名单。reviewer 非阻断 NOTE 交后续：PH-249 编辑 PH-232 时按实现对齐 clearance_coverage_for_cash 返回字段；PH-254 的“ceded2e 基底”措辞可在执行时收紧。未验证：PH-254/255/256/257/258均未实现；无产品代码/迁移/浏览器/PG/CI证据；PH-244 全量证据重跑待 PH-254 执行。合入前规划缺口补丁（Lead 要求）：PH-241 白名单补 clearance/aging 服务并冻结方案 a（advance 可用=gross−Σcleared−Σoffsets，PH-232 核销排除 advance 现金，clearance/offset 互斥；PH-232 spec 文字未同步交 PH-249/独立小修）；PH-245 补 canonical InternalTransferReversal 模型、无条件 Unique 与实现注意。


## PH-249 PH-232 覆盖公式口径同步（2026-10-31）

基线26d0408（PH-248/PH-250合入后）。纯文档口径同步：project/specs/PH-232.md 余额口径改为「现金未核销余额/可用 = posted gross_cents − Σactive cleared − Σactive 退款 − Σactive 银行退回；Offset 扩展位 0」，并引用真实接口（refund_coverage_for_cash 返回 refunded/returned/total 且 offset_implemented=false；clearance_coverage_for_cash 返回 posted_gross/cleared/offset=0/total=cleared/offset_implemented=false，无 refunded 字段；create_clearance 上限按 gross−_cash_cleared−refund total 计算）；金样例/反例/verify_integrity 补退款退回扣减、不得再核销已退金额与 advance 排除待同步项（PH-241 契约 ready 未实现，不宣称已实现）。未改 PH-232 已交付实现描述/历史 pass/代码/测试/迁移，未宣称 Offset 已实现。实际执行与最终证据（HEAD 1b8d97c）：独立 S 复核 PASS，0 阻断（逐字段核对合并代码、公式/金样例/反例/边界均一致）；projectctl check ok=true/task_count=180；23 项治理测试 OK；verify-change --base 26d0408 --task PH-249 ok=true、6 文件全白名单。未验证：PH-241 的 advance 排除与 clearance/offset 互斥仅有契约/待同步项记录，运行时未实现；无产品代码/迁移/测试改动。


## PH-258 后续交付独立规划入口（接续 PH-248）+ PH-254 验收契约缺陷修复（2026-10-31）

基线79ee448（PH-249合入后）。纯规划/契约文字片，两部分：
1. **PH-254 验收契约缺陷修复**：tasks.json 中 PH-254 acceptance 旧 4 条含字面占位符 `python scripts/projectctl.py verify-change --base <PH-254 BASE> --task PH-254`（projectctl 按字面比对 BASE acceptance 与 report pass 声明，占位符永不满足，PR #148 governance 已失败）→ 新 3 条（check、23项治理、`manage.py test tests --verbosity 1`）；PH-254 spec 验收段注明「verify-change 由报告 tests 记录实际 BASE/输出，不进 acceptance」；未改 PH-254 report/review 块/代码/已交付实现，PH-254 将 rebase 重跑 CI。
2. **常规规划**：核号依据——BASE 最大 PH-258；检查 open PR #143/#144/#148 与远端分支的 tasks.json，均未占用 ≥259（最大 254/258），故从 PH-259 起连续登记：PH-259 发票插件归属与契约决策前置（ready，deps PH-224/PH-232 done；产出 ADR 并同步 PH-252 decision point/白名单）、PH-260 管理收入确认与结算配对首片（proposed，缺 PH-167/156/162/161）、PH-261 预算版本与 ETC 首片（proposed，缺 PH-177/164/161）、PH-262 经营指标与项目工作台读模型首片（proposed，缺 PH-182/183；insights app 归属待决策）、PH-263 下一规划槽（ready，deps PH-258 done）。count 180→185，编号 259-263 连续唯一；PH-251/252/255/256/257 保持 proposed（PH-252 前置改由 PH-259 决策）。独立 S 复核两轮：首轮 1 阻断（PH-259 新登记又带 `<PH-259 BASE>` 字面占位符，与 PH-254 同病）→ 修复（PH-259 acceptance/spec 删占位符、仅留 check+治理；全局 grep tasks.json+specs 无 `<PH-`）→ 二轮 PASS；合入前追加 PH-241 白名单修正（classification_services.py，解决其 path_out_of_scope），reviewer 快速终审 PASS（reviewed HEAD 92d59ed）。最终证据 HEAD 64ca16e：projectctl check ok=true/task_count=185；23 项治理 OK；verify-change --base 79ee448 --task PH-258 ok=true、12 文件全白名单。未验证：PH-254 修复后需其分支 rebase/CI 复跑；PH-259/260/261/262 未实现，无产品代码/迁移/浏览器/PG/CI证据。

## PH-241 预收/预付抵扣 Offset 首片（2026-10-31）

基线26d0408（PH-248规划合入后的main；PH-250已在main）。settlement新增append-only Offset事实与0005_offset_guards迁移（SQLite/PG双触发器：插入要求OpenItem同org/project、UPDATE/DELETE拒绝；每流水可多次部分抵扣、无Offset撤销）。offset_services实现create_offset（公司锁、owner/finance、cash+settlement模块启用、同org/同往来方、OpenItem active应收、advance posted+有效advance分类（PH-239事实）、金额/双方余额、日期=现金日期、未来/锁期/关期、SettlementReceipt幂等、dry_run零副作用、锁内复核非负、generic audit settlement.offset.created）与只读offset_snapshot/list、open_item_offset_snapshot、advance_offset_snapshot。按Lead裁决方案a：advance可用=gross−Σactive cleared−Σactive offsets、OpenItem剩余=gross−Σactive cleared−Σactive offsets；PH-232核销候选与上限排除advance分类现金（经PH-239分类只读判定，属对PH-232服务行为的修正，PH-232 spec文字待PH-249/独立小修同步）；同一现金不可同时有active clearance与active offset。cash侧新增root具名只读posted_advance_for_offset/posted_advance_candidates（posted/in/同org/有效往来方/有效advance分类）。verify_integrity新增offset分支（引用同org/project、advance现金posted/in且advance分类有效、advance_ref一致、每offset恰一条generic audit、OpenItem剩余与advance可用非负、clearance/offset互斥、advance现金不得被核销、无settlement会计事件、无offset canonical topic；批量查询，不加全局禁令）。UI：offset列表/登记、账龄与未清项/核销页显示cleared/offset/remaining；offset_list为history_read（模块停用历史可读）、offset_save为business_write。无canonical offset事件（契约仅internal_cash_orchestration_only）、无Offset撤销。
实际执行（最终HEAD）：scoped三模块32项（29通过+3项PG并发本机skip）；全量881项（813通过、65环境skip、8项本机环境限制失败=6项DRF+2项macOS symlink，与基线一致、CI Linux为权威）；manage.py check exit0（61条既有W003警告、0错误）、makemigrations无变更（settlement 0005）、verify_integrity在全新迁移SQLite上ok=true；projectctl check ok=true/task_count=180。复核round 1三money级阻断已修：①advance现金已有active clearance时offset与advance分类双向拒绝（含Lead授权跨owner小改classification_services）；②advance可用再扣refund/return覆盖且verify断言cleared+offset+refunded+returned≤gross；③verify批量补advance往来方一致、advance_ref.version、OpenItem active检查。未验证：PG并发（CI）、浏览器1440/390、独立S复核round2 pending、生产/UAT、旧库升级。


## PH-259 发票插件归属与契约决策（ADR 0010）（2026-10-31）

基线e8183ff（PH-241 合入后）。纯决策/ADR 片：新增 `docs/adr/0010-invoicing-plugin-ownership.md`，给出候选方案 A（新建 pmo_plugins.invoicing 插件）/B1（并入 management_revenue）/B2（并入 settlement）/B3（并入 contracts/delivery）/C（暂缓）及判据（域所有权/迁移隔离/manifest-requires/路由与命令暴露/SDK 演进/测试部署回滚成本/core 膨胀），推荐并决定**方案 A**：新建 pmo_plugins.invoicing 作为发票域写 owner，契约 schema 不变，contracts/settlement 仅具名只读；实现约束（manifest 8 字段 requires contracts/settlement、自有迁移链 0001、delivery/urls root 单写、canonical invoicing.* 首片仅私有服务/浏览器、PH-252 仍 proposed 且待 PH-150/PH-155 done）。同步 PHP-252 spec decision point 指向该 ADR；未改 PH-252 状态/白名单/契约 schema，未建 scaffold/写业务代码。白名单说明：BASE 的 PH-259 allowed_paths 未含 docs/v1.0-development/*，故未改 decisions-and-readiness.md（记录在 ADR+report/handoff，如需正式录入请另行授权）。独立 S 复核两轮：首轮阻断（PH-252 whitelist 缺 bootstrap 文件 __init__.py/apps.py/plugin.py 且仍留“不得自建 scaffold”旧句——ADR 决策不可执行）+ 措辞（WO-14 SDK 表述不准）→ 修复（PH-252 tasks/spec 补 3 bootstrap 文件、替换旧句、更新 blocker；ADR 背景改为 SDK-1/2/PH-140 已交付）→ 复查又指出文档准确性（summary/deviations/handoff 关于 PH-252 白名单的表述）→ 修正 → 二轮 PASS。最终证据 HEAD 90e8913：projectctl check ok=true/task_count=185；23 项治理 OK；verify-change --base e8183ff --task PH-259 ok=true、7 文件全白名单。未验证：PH-252 未实现（无 app scaffold/迁移/业务代码）。

## PH-263 后续交付独立规划入口（接续 PH-258）（2026-10-31）

基线 e8183ff（PH-241 合入后；PH-239/PH-240/PH-243/PH-244/PH-249/PH-250/PH-253/PH-254/PH-258 done）。纯规划片：新增 5 个任务（编号 264–268 连续，核号：BASE 最大 263、远端分支/PR 无 ≥264 占用）并冻结各自 spec——PH-264 成员账号生命周期与项目范围首片（ready；`identity.membership.change/v1` 具名服务/浏览器、角色/项目范围/停用撤销 AgentToken、core 迁移 0010）；PH-265 合同变更与终止首片（ready；canonical `contracts.contract_change.draft.save/v1`、`contracts.change.decide/v1`、`contracts.change_effective.v1`、`contracts.version_approved.v1`、contracts 迁移 0003、终止守卫经既有 `approved_terms_snapshot` 阻断新结算）；PH-266 time 插件归属与可计费确认契约决策前置（ready，docs-only，ADR 0011）；PH-267 导入/期初插件归属与契约决策前置（ready，docs-only，ADR 0012）；PH-268 下一规划槽（依赖 PH-263，完成后置 ready）。复核既有 proposed 并写精确 blockers：PH-251/252/255/256/257/260/261/262 与 PH-143/145/146/147/151/155；PH-259 保持 ready。评估结论：主数据/账号由 PH-264 消费 PH-143/144 子集；工时/人工能力已由 PH-025/PH-217 交付、剩余可计费确认需 PH-266 归属决策；合同/结算能力已由 PH-212/214/224/232 交付、变更/终止由 PH-265；发票前置 PH-259 ready、PH-252 待其完成；管理收入/预算/ETC/读模型/期初/期间核对维持 proposed 并写精确 blockers，无虚假晋级。实际执行：check ok=true/task_count=190；治理 23 项 OK；新 spec/tasks 的验收命令无任务编号占位符，规格与 tasks 的占位符检索无命中。未验证：PH-264+ 均未实现（无业务代码/迁移/浏览器/PG 证据）；独立 S 复核 pending，pass 后置 PH-263 done、PH-268 ready 并重跑 check+verify-change。

补充（最终证据 HEAD）：独立 S 复核 round 3 PASS 0 阻断（首轮 1 阻断为 ADR 0010 与 PH-259 invoicing 决策编号冲突，已改 PH-266→ADR 0011、PH-267→ADR 0012；tasks.json 已恢复 BASE 顺序）；PH-263 置 done、PH-268 置 ready 并清空 blocker；最终 check/治理/verify-change 在收尾 HEAD 实际重跑并记录于 report/handoff。

## PH-245 内部账户互转首片（2026-10-31）

基线e8183ff（PH-241 Offset合入后的main；PH-250/248已在main）。cash新增append-only InternalTransfer（out/in CashTransaction FK、out/in_cash_ref、transfer_date、gross_cents、out/in_transaction_date、status canonical枚举保留、version）与0006_internal_transfer_guards迁移（SQLite/PG双触发器：插入校验两侧现金同org且方向out/in；UPDATE仅允许active→reversed且其余字段不变；DELETE拒绝；无条件Unique(out/in_cash_transaction)终身一次）。新增append-only InternalTransferReversal（internal_transfer FK unique、internal_transfer_ref、out/in_classification_reversal_ref、occurred_on、posting_period、status=posted、version；reversed事件source指向它）与InternalTransferReceipt（org+token唯一、action link/reverse）。internal_transfer_services实现link_internal_transfer（公司锁、owner/finance、两条posted/同org/方向out-in/不同账户/同额/同日、期间/锁期/未来、未参与互转、整数分、幂等、dry_run零副作用；经PH-239非路由record_internal_classification两腿各写transfer分类+分类事件+posting（cash_out/cash_in、funding_account scope），再写InternalTransfer、generic audit、canonical cash.internal_transfer_linked.v1（posting_event_refs=[]、changed_refs 3）与receipt）与reverse_internal_transfer（仅active、两条reverse_classification（不同token）写撤分类+冲销posting、状态active→reversed条件更新、InternalTransferReversal、audit、canonical cash.internal_transfer_reversed.v1（source=reversal ref、changed_refs 3、original ref、classification_reversal_refs=2、posting_event_refs=[]）与receipt）；只读internal_transfer_snapshot/list、internal_transfer_integrity、internal_transfer_for_cash、internal_transfer_candidates。UI：内部互转列表/登记/撤转、现金流水详情互转区；read三路由、write两路由。verify_integrity新增internal_transfer分支（两侧现金同org/posted/方向/金额/日期、transfer_date一致、每active互转两侧各恰一条active transfer分类、linked事件恰一条且envelope正确、每腿恰一条classification会计事件与一条cash_in/out posting（funding_account scope、金额=gross）、公司净额0、reversed时恰一条reversal行/无active分类/每原分类恰一条撤分类/reversed事件envelope正确、状态仅active/reversed；批量、无全局禁令，与PH-239/240/241/250/253分支共存）。无新增canonical topic、无公开canonical通道、transfer仅内部服务写入（客户端分类仍四值）；不写成本/收入/应收/应付/未清。
实际执行（最终HEAD）：scoped三模块24项（21通过+3项PG并发本机skip）；全量905项（832通过、68环境skip、8项本机环境限制失败=6项DRF+2项macOS symlink，与基线一致、CI Linux为权威）；manage.py check exit0（66条既有W003警告、0错误）、makemigrations无变更（cash 0006）、verify_integrity在全新迁移SQLite上ok=true；projectctl check ok=true/task_count=185。独立S复核PASS（HEAD dc0dc95，BASE e8183ff，19文件全在白名单；实际重跑全量905/5F+3E/68skip与scoped 24=21+3skip）。未验证：PG并发（CI）、浏览器1440/390、生产/UAT、旧库升级。


## PH-266 time 插件归属与可计费确认契约决策（ADR 0011）（2026-10-31）

基线1e4c480（PH-245/PH-263 合入后）。纯决策/ADR 片：新增 `docs/adr/0011-time-plugin-ownership.md`——按 canonical `BillableConfirmation`（x-owner=time、owned_fact、draft/confirmed/reversed）与 `time.billable.confirm/v1`/`time.billable_confirmed.v1`、core.Timesheet/PH-025/PH-217、contracts confirmed_time 模板与 settlement/management_revenue 边界，比较方案 A（新建 pmo_plugins.time）/B core/C contracts/D 暂缓，判据含 owner 一致性、kernel 膨胀、迁移 owner/抢号、manifest/requires、路由与命令暴露、labor/costing 边界、测试部署回滚、未来工时域抽离；**决定方案 A**：新建 pmo_plugins.time 作为 time 域写 owner，首片只承载 BillableConfirmation（manifest 8 字段 requires contracts、自有迁移 0001、delivery/urls root 单写、`time.billable.confirm/v1` 仅具名服务/浏览器、core.Timesheet 只读不复制、不写收入/账本/现金/未清）。PH-266 spec 增加“可计费确认实现片候选 DoR 骨架”（字段/状态机/权限/幂等/迁移/精确白名单/反例/验收，供 PH-269+，不登记 ready）；PH-154 spec 增加归属/边界注记；tasks 更新 PH-152/153/154 blockers 结论，PH-266 自身 done+handoff。未建 scaffold/产品代码/迁移，未改 contract schema，未改 PH-152/153/154 状态。独立 S 复核 PASS，0 阻断（核对 ADR 方案/owner、manifest 8 字段/requires、迁移 0001 无抢号、命令暴露、core.Timesheet 只读边界、下游不写、PH-152/153/154 blockers 与 PH-266 DoR 骨架一致）。最终证据 HEAD 6e1b130：projectctl check ok=true/task_count=190；23 项治理 OK；`grep -q "time.billable.confirm/v1" docs/adr/0011-time-plugin-ownership.md` EXIT 0；verify-change --base 1e4c480 --task PH-266 ok=true、8 文件全白名单。未验证：可计费确认未实现（无 time app/迁移/业务代码）。

## PH-264 成员账号生命周期与项目范围首片（2026-10-31）

基线1e4c480（PH-245合入后的main）。core.Membership新增canonical乐观版本version（PositiveIntegerField default=1）与0010_membership_version迁移（仅AddField回填1，SQLite/PostgreSQL双实现，无触发器）；复用ProjectMember项目范围、Employee绑定校验、AgentToken.revoked_at撤销。core/masterdata.py新增具名服务change_membership（公司锁；fresh重读owner/目标成员/员工/项目/令牌；expected_version校验；角色owner/pm/finance/member/auditor；project_scope以ProjectMember差集维护、同公司、去重、公司级角色必须空数组；最后active owner不可降级/停用、不可自停用；停用前须无在职员工绑定、无有效项目manager（stage非closed/cancelled）；停用同事务撤销全部未撤销AgentToken并清空ProjectMember；恢复不自动恢复范围/令牌；Membership.version+1；generic audit identity.membership.changed含changed_fields/role/active/project_scope_ids/request_token/input_digest/result不含敏感字段；同token重放原结果、改输入冲突；dry_run零副作用）与只读membership_snapshot（owner/finance/auditor全部、pm/member仅自己）。浏览器：settings成员表新增项目范围/版本列与owner可见成员生命周期表单（core/forms.py MembershipChangeForm、core/views.py settings_page membership POST分支、templates/settings.html），不新增路由。不做邀请/注册/邮件、密码/MFA、跨公司迁移、删除账号、角色权限矩阵扩展、canonical事件topic、账本/金额/期间写、AgentToken能力扩展。幂等复用append-only AuditEvent payload而非新收据表；generic audit的常规非canonical outbox行已披露；无新canonical topic。
实际执行（最终HEAD）：scoped两模块17项全通过；全量922项（849通过、68环境skip、8项本机环境限制失败=6项DRF+2项macOS symlink，与基线一致、CI Linux为权威）；manage.py check exit0（66条既有W003警告、0错误）、makemigrations无变更（core 0010）、verify_integrity在全新迁移SQLite上ok=true；projectctl check ok=true/task_count=190。独立S复核PASS（HEAD 04f5336，BASE 1e4c480，13文件全在白名单；实际重跑全量922/5F+3E/68skip与scoped 17全通过）。未验证：浏览器1440/390、生产/UAT、旧库升级。

## PH-265 合同变更与终止首片（2026-10-31）

基线1e4c480（PH-241/PH-245/PH-263合入后）。contracts 新增 append-only `ContractChange`（canonical 实体字段：change_type∈add_scope/reduce_scope/price_change/extension/termination、五状态、有符号 deltas、evidence、draft_content、决策人/时间/说明）与 `ContractChangeReceipt` 幂等收据；`Contract` 新增 `terminated_on`；contracts 迁移0003双实现触发器（变更禁删、draft 改稿 version+1、决策仅一次且创建人≠决策人由服务层+触发器双保护、终止后合同不可再改）。服务 `save_contract_change_draft`（公司锁、生效合同唯一批准版本、草稿 schema 复用 PH-214 校验器、deltas 服务端派生并校验客户端传值、终止不得携带新版本内容、同合同至多一个 draft、同 token 重放/改输入冲突、版本+1）与 `decide_contract_change`（owner/finance 独立决策 approved/rejected/withdrawn；批准时非终止生成新 `ContractVersion` approved 并把旧批准版本 superseded、恰一条 `contracts.version_approved.v1`（source=新版本、changed_refs=[新版本, 变更]）与恰一条 `contracts.change_effective.v1`（source=变更、changed_refs=[变更, 新版本]）；终止不生成新版本、`Contract.status=terminated`+`terminated_on=requested_on`、`changed_refs=[变更, 合同]`；posting_event_refs=[]；无 canonical topic/command 新增，使用已冻结契约）。拒绝路径：自审、跨org、非生效合同、终止后再变更/再结算、deltas 不符、终止带线路、非法模板/金额、决策后重决、token 改输入；拒绝零半笔。`approved_line_snapshot` 允许终止前业务日期读取，`approved_terms_snapshot` 在 as_of>=terminated_on 拒绝新结算；既有已过账结算/OpenItem 不改。视图/表单/模板新增变更列表、草稿表单、独立决策与合同详情变更区（1440/390/keyboard 结构复用既有面板）。`verify_integrity` 新增合同变更分支：状态/类型/组织、approved 变更恰一条 change_effective envelope 与终止/新版本对应、draft/未批准不得有生效事件、每合同至多一个 draft 变更、active 合同恰一批准版本、terminated 有日期且保留一批准版本、批准版本审批链与恰一条 version_approved envelope。
实际执行（最终HEAD）：projectctl check ok=true/task_count=190；manage.py check exit0（71项既有 urls.W003）；makemigrations --check无变更；migrate+verify_integrity全新SQLite ok=true/errors=[]；临时SQLite探针：批准 price_change 后 contract version=3、verify ok=true，删除 change_effective 事件后 exit1 报 contract_change:<id>:effective_event_count；scoped 3模块13 tests OK；全量918 tests/123.894s，5F+3E为基线已知8项macOS环境失败（名称一致）、68 skips、无新增（基线905 + 本片13）。未验证：PG并发（CI双连接覆盖同合同并发 draft/重复批准）、浏览器1440/390与键盘焦点实测、真实客户/生产；PH-150/151/155 legacy 任务未解除。

复核修复（PH-265 round 2）：独立 S 复核首轮 3 阻断——①创建人独立性仅服务层：已在 SQLite/PostgreSQL ContractChange update 触发器与 verify_integrity 增加 `creator≠decider` 断言并补直改 DB 自批反例；②草稿编辑被触发器封死：改为允许 draft_content/evidence/deltas 在 draft 阶段更新（身份/创建字段仍不可变、决策时冻结）并补编辑回归；③终止后新变更未拦截/事件校验缺口：INSERT 触发器要求合同 active，verify 增加 terminated 合同新变更/新版本与 version_approved `changed_refs=[version, change]` 断言及 forged 反例。修复后 scoped 17 tests OK、全量 922（基线 905 + 17，8 项 macOS 基线失败不变）、verify_integrity 探针 ok/删事件可报、fresh 库 ok。

补充（PH-265 最终证据 HEAD）：独立 S 复核 round 2 PASS 0 阻断（三项 round-1 阻断已在双 vendor 触发器 + verify_integrity + 回归测试层修复：creator≠decider、draft 可编辑且决策冻结、终止后禁新变更与 version_approved changed_refs 校验）；PH-265 置 done，report.tests 使用最终 HEAD 实跑证据；最终 check/verify-change 在收尾 HEAD 重跑记录。


## PH-267 导入/期初插件归属与契约决策（ADR 0012）（2026-10-31）

基线69c0bb4（PH-264/265/266 合入后）。纯决策/ADR 片：新增 `docs/adr/0012-importing-plugin-ownership.md`——按 canonical ImportPreview/ImportConflict/OpeningBatch/OpeningCoverage/OpeningEvent 的 `x-owner=onboarding`、`onboarding.opening_batch.draft.save/v1`/`onboarding.opening.commit/v1`（无 `import.*` 命令）、当前迁移叶（core 0010/contracts 0003/cash 0006/settlement 0005）与 PH-230/PH-239/PH-224/Organization.opening_cash_cents 边界，比较方案 A（新建 pmo_plugins.onboarding）/B delivery/C core/D contracts 等/E 暂缓，**决定方案 A**：新建 pmo_plugins.onboarding 插件承载导入预览/冲突清单与期初批次/覆盖/事件编排；约束：manifest 8 字段 requires={contracts,settlement,cash}、自有迁移 0001（不抢号）、settings+PMO_PLUGIN_MODULES+delivery/urls root 单写、`onboarding.*` 仅具名服务/浏览器、预览零账本/零来源消费、正式提交逐域调用既有命令且整批回滚、已过账走更正、不另造余额/收入；insights（PH-257）归属单列、不并入 onboarding。同步 PH-256 spec/tasks 白名单为 onboarding bootstrap/文件并指向 ADR 0012；PH-257 spec/tasks 注明 insights 独立决策片；PH-256/257 仍 proposed。未建 scaffold/产品代码/迁移，未改 contract schema。独立 S 复核 PASS，0 阻断（核对 onboarding owner、manifest/requires、迁移 0001 无抢号、命令暴露/无 import.*、预览零账本、期初逐域提交与回滚、PH-256 白名单同步、insights 单列）。最终证据 HEAD 703e4c1：projectctl check ok=true/task_count=190；23 项治理 OK；`grep -q "importing" docs/adr/0012-importing-plugin-ownership.md` EXIT 0；verify-change --base 69c0bb4 --task PH-267 ok=true、8 文件全白名单。未验证：导入/期初/insights 均未实现（无 app/迁移/业务代码）。

## PH-268 后续交付独立规划入口（接续 PH-263）（2026-10-31）

基线 69c0bb4（PH-264/PH-265 合入后；PH-259/PH-266 done、PH-267 ready）。纯规划片：新增 5 个任务（编号 269–273 连续，核号：BASE 最大 268、远端/本机分支与 specs 无 ≥269 占用）并冻结各自 spec——PH-269 time 可计费确认实现首片（ready；deps PH-025/214/217/224/266 done；ADR 0011 方案 A 新建 pmo_plugins.time、迁移 0001、time.billable.confirm/v1、canonical time.billable_confirmed.v1、只读 core.Timesheet 与 contracts 条款、不写收入/账本/现金/未清）；PH-270 insights 插件归属与期间核对/读模型契约决策前置（ready，docs-only，ADR 0013）；PH-271 导入预览实现首片（proposed；blockers PH-267 done + PH-181/PH-185 done；预览零账本/冲突清单/幂等已冻结）；PH-272 期间核对与工作台读模型实现首片（proposed；blockers PH-270 done + PH-180/181/182/183 done；只读预检/差异单/读模型已冻结）；PH-273 下一规划槽（依赖 PH-268，完成后置 ready）。PH-252 晋级复核：PH-259 ADR 0010 已定新建 invoicing 插件与 bootstrap 白名单，但 PH-252 冻结 DoR 仍要求 PH-150/PH-155 done（二者 proposed）；能力虽由 PH-212/214/224/232 交付，按 DoR 不虚假晋级，保持 proposed 并写精确 blocker。同步更新 PH-256/257/260/262 blockers。未建 scaffold/未改 contract schema/未自扩白名单；新 spec 无占位符、验收命令全部可执行。实际执行：check ok=true/task_count=195；治理 23 项 OK。未验证：PH-269+ 均未实现（无业务代码/迁移/浏览器/PG 证据）；独立 S 复核 pending，pass 后置 PH-268 done、PH-273 ready 并重跑 check+verify-change。

复核修复（PH-268 round 2）：独立 S 复核首轮 1 阻断——PH-271 使用错误 importing owner/通配白名单，与 canonical ImportPreview/ImportConflict 的 x-owner=onboarding 冲突；已改为 `pmo_plugins.onboarding` 精确 24 路径（bootstrap/settings/urls/delivery/verify_integrity/tests/治理）、generic audit `onboarding.import_preview.created`，blockers 更正为 PH-267（导入/期初决策）与 PH-181/PH-185；PH-256 spec/tasks 同步 onboarding owner，PH-272 白名单改为精确 insights 文件，tasks.json 恢复 BASE 顺序。修复后 check ok=195、治理 23 OK。

补充（PH-268 最终证据 HEAD）：独立 S 复核 round 2 PASS 0 阻断（PH-271/PH-256 已对齐 canonical pmo_plugins.onboarding 精确 24 路径与 onboarding.import_preview.created，PH-272 精确 insights 白名单，PH-235 重排移除；PH-252 保持不晋级、PH-269/270 ready、PH-271/272 proposed 均被确认）；PH-268 置 done、PH-273 置 ready 并清 blocker；report.tests 使用最终 HEAD 实跑证据，最终 check/verify-change 在收尾 HEAD 重跑记录。


## PH-270 insights 插件归属与期间核对/读模型契约决策（ADR 0013）（2026-10-31）

基线61950e1（PH-268 合入后）。纯决策/ADR 片：新增 `docs/adr/0013-insights-plugin-ownership.md`——按 canonical ReconciliationCase（x-owner=insights owned_fact）与 MetricDefinition（x-owner=insights read_projection）、`insights.reconciliation.refresh/v1`/`insights.reconciliation.case.update/v1`/`insights.export.create/v1` 与事件、迁移叶（core 0010/contracts 0003/cash 0006/settlement 0005/costing 0004）、只读来源（cash/settlement/costing/contracts/management_revenue、AccountingPeriod/locked_through）比较方案 A（新建 pmo_plugins.insights）/B 复用 delivery/management_revenue/C core/D 暂缓，**决定方案 A**：新建 pmo_plugins.insights 作为 insights 域写 owner；约束：manifest 8 字段 requires={cash,settlement,costing,contracts}、自有迁移 0001（不抢号）、settings+PMO_PLUGIN_MODULES+delivery/urls root 单写、refresh/case.update 首片仅具名服务/浏览器（export 延后，不接 PAT/MCP/支付/关期）、读模型/差异单只追加不写业务事实、不建可编辑余额/第二套账、期间预检只读 AccountingPeriod/locked_through、与既有 owner 只读消费。同步 PH-257/PH-262 decision point/白名单（补 bootstrap __init__.py/apps.py/plugin.py + settings/urls）与 tasks allowed_paths/blockers；PH-272 owner 注记改 ADR 0013；三者仍 proposed。未建 scaffold/产品代码/迁移，未改 contract schema。独立 S 复核两轮：首轮阻断（ReconciliationCase canonical 字段/状态不一致：实际为 status draft/pending/approved/rejected/superseded/closed、required source_refs/difference_cents/owner_member_id/resolution_note、只读 case_key/rule_code/coverage_key/snapshot_watermark，而 ADR/PH-257/PH-272 写成 open/assigned/resolved/dismissed 及 period/scope/category/assignee/due_date/note）→ 修复（按 canonical 对齐并给出业务映射）→ 二轮 PASS。最终证据 HEAD 02f7790：projectctl check ok=true/task_count=195；23 项治理 OK；`grep -q "insights" docs/adr/0013-insights-plugin-ownership.md` EXIT 0；verify-change --base 61950e1 --task PH-270 ok=true、9 文件全白名单。未验证：insights 未实现（无 app/迁移/业务代码）。

## PH-273 后续交付独立规划入口（2026-10-31）

基线61950e1（PH-268规划合入后的main）。本片为规划槽+授权白名单修正：①PH-269 allowed_paths由25扩为29，新增tests/test_v1_contract_change.py、tests/test_v1_contract_change_http.py、tests/test_acceptance_revenue.py、tests/test_v1_cash_book_bridge.py，并在PH-269 spec精确白名单加入同四文件及「白名单扩容说明」——time插件manifest requires={contracts}使contracts模块停用被「工时与可计费」依赖正确阻塞，故3个既有禁用/顺序loop测试需同实现一并停用/恢复time；verify_integrity新增time分支使cash bridge query-count上限80→84（保留「随分类历史常量」第二段断言、仅上调上限不改断言语义）。②按BASE事实核号（最大PH-273，无EXP id/远端分支占用）登记PH-274 onboarding导入预览前置（ready，deps PH-267/002/024 done）、PH-275合同变更影响只读追溯与终止阻断深化（ready，deps PH-265/224/232/219/222 done）、PH-276 time confirmed撤销与收入/结算只读配对（proposed，缺PH-269 done）、PH-277 insights读模型契约冻结（proposed，缺PH-270 done）、PH-278下一规划槽（proposed，缺PH-273 done）；刷新PH-251/252/256/257/262/271/272 blockers到当前事实。只规划/登记，不实现业务、不建scaffold、不改contract schema，除授权项外未自扩白名单；新任务acceptance全可执行、无占位符。实际执行（最终HEAD）：projectctl check ok=true/task_count=200；tests.test_projectctl+tests.test_governance共23项OK；占位符grep无输出；verify-change --base 61950e1 --task PH-273在review pending时仅high_risk_review_required（预期）。复核round 1阻断已修：PH-276撤销DoR与PH-269 receipt/触发器约束矛盾——PH-276现冻结自有time 0002_time_billable_reversal_guards（扩receipt action允许reverse、双vendor触发器允许confirmed→reversed，不改已应用0001、不新增表），白名单加time migrations；另按非阻断意见把PH-269「可修改/删除草稿」改为「创建/修改、草稿禁删」。未验证：PH-274/275未实现、PH-276/277/278仍proposed；本片docs-only无全量测试/浏览器/PG；复核round 2仅剩PH-276 tasks/spec精确白名单不一致（tasks缺time迁移路径），已补`pmo_plugins/time/migrations/*.py`并逐项对齐（spec 17=tasks 17），PH-276仍proposed；独立S复核终审pending。


## PH-278 后续交付独立规划入口（接续 PH-273）（2026-10-31）

基线7d8877f（PH-273 合入后，count 200）。纯规划片：
1. **晋级**：PH-277（insights 读模型实现前置/契约冻结，deps PH-270 done + PH-002/024/235 done）→ ready；PH-274/275 已 ready 保持不变。
2. **核号**：BASE 最大 PH-278；检查远端分支 tasks（含 EXP PR #143/#144 分支，最高 PH-254）与 open PR，均未占用 ≥279；故 PH-279 起连续登记。
3. **新登记（ready，依赖全 done）**：PH-279 现金分类/账本桥接与退款/互转只读核对快照（deps PH-239/240/244/245/230/228/224 done；无迁移/无写入口）；PH-280 手续费批准/成本与净额只读配对（deps PH-244/250/224/232/230 done；无迁移）；PH-281 成员账号/项目范围与授权审计只读运营视图（deps PH-264/024/110 done）；PH-282 下一规划槽（deps PH-278 done）。count 200→204，编号 279-282 连续。
4. **blockers 刷新**：PH-276 更新为“缺 PH-269 done（ready 未 done）”；其余 proposed（PH-251/252/255/256/257/260/261/262/271/272）依赖未 done，保持 proposed 并未虚假晋级。
5. 占位符检查：改动 specs/tasks 无 `<PH-`/`{tid}`；acceptance 全可执行命令。独立 S 复核：首轮阻断（规划槽状态惯例——候选提前置 PH-278 done/PH-282 ready）→ 修复（PH-278 in_review+owner、PH-282 proposed+blocker）→ 二轮 PASS；最终证据 HEAD d7a5cdb 置 PH-278 done/PH-282 ready、blocker 清空。最终门禁：projectctl check ok=true/task_count=204；23 项治理 OK；verify-change --base 7d8877f --task PH-278 ok=true、10 文件全白名单。未验证：PH-279/280/281 未实现（无代码/迁移/运行时证据）；PH-282 为下一规划槽。


## PH-282 后续交付独立规划入口（接续 PH-278）+ PH-269/PH-274 白名单修正（2026-10-31）

基线8748c1a（PH-278 合入后，count 204）。纯规划/契约文字片：
1. **PH-269 allowed_paths/spec 白名单加 `tests/test_contract_approval.py`**：time `requires={contracts}` 使该 PG 并发审批测试在 contracts 停用 loop 中需先停用/恢复 time；spec 写明用途与“仅最小必要修改、不改业务断言语义”。
2. **PH-274 allowed_paths/spec 白名单加 7 个测试文件**（test_acceptance_revenue、test_v1_cash_http、test_v1_clearance_http、test_v1_settlement_http、test_v1_contract_change、test_v1_contract_change_http、test_service_period_revenue_http）：onboarding `requires={contracts,settlement,cash}` 使既有禁用/顺序 loop 需先停用/恢复 onboarding；spec 写明最小必要修改、不改业务断言语义。
3. **核号**：BASE 最大 PH-282；远端分支/PR（含 EXP #143/#144 与 PH-269 PR #161）无 ≥283 占用 → PH-283 起连续登记。
4. **新登记**：PH-283 结算未清/核销/退款覆盖只读核对（ready，deps PH-224/232/240/230 done）、PH-284 管理收入确认与结算/合同条款只读配对（ready，deps PH-219/222/224/232/265 done）、PH-285 核对底稿只读导出（proposed，缺 PH-181/182 与 PH-272/277）、PH-286 下一规划槽（proposed，待 PH-282 done 后置 ready）。count 204→208，编号 283-286 连续。
5. blockers 刷新：PH-276 等 PH-269 done；其余 proposed 未虚假晋级。无占位符；acceptance 全可执行。独立 S 复核 PASS（0 阻断，核对白名单修正用途/编号/晋级事实）；最终证据 HEAD bc47cf2 按槽惯例置 PH-282 done / PH-286 ready 并清 blocker。最终门禁：projectctl check ok=true/task_count=208；23 项治理 OK；verify-change --base 8748c1a --task PH-282 ok=true、11 文件全白名单。未验证：PH-283/284/285 未实现；PH-269/PH-274 自身的测试修改由其实现 PR/CI 验证。


## PH-286 后续交付独立规划入口（接续 PH-282）+ PH-269/PH-274 补白名单（2026-10-31）

基线e38b9a6（PH-282 合入后，count 208）。纯规划/契约文字片：
1. **PH-269** allowed_paths/spec 精确白名单加 `tests/test_v1_contracts.py`（PG 并发测试 `set_enabled(...,'contracts',False)`；time requires={contracts} → 先停用/恢复 time；最小必要、不改业务断言）。
2. **PH-274** allowed_paths/spec 精确白名单加 `tests/test_v1_contracts.py` 与 `tests/test_contract_approval.py`（二者均禁用 contracts；onboarding requires={contracts} → 先停用/恢复 onboarding；最小必要、不改业务断言）。
3. **程序化比对**：PH-269/PH-274 的 spec 精确白名单与 tasks allowed_paths 逐项一致（spec-only=[]、tasks-only=[]）。
4. **核号**：BASE 最大 PH-286；远端分支/PR（含 EXP #143/#144、PH-269 PR #161）无 ≥287 占用 → PH-287 起连续。
5. **新登记**：PH-287 合同收费模板/生效条款只读快照与结算核对（ready，deps PH-214/265/224/232/219/222 done）、PH-288 银行账户/现金余额只读对账汇总（ready，deps PH-228/230/239/240/245 done）、PH-289 onboarding 导入提交/冲突持久化前置（proposed，缺 PH-274/PH-181/PH-185）、PH-290 下一规划槽（proposed，待 PH-286 done 后置 ready）。count 208→212，编号 287-290 连续。
6. blockers 刷新：PH-276 等 PH-269 done；其余 proposed 未虚假晋级；无占位符；acceptance 全可执行。独立 S 复核 PASS（0 阻断，核对补漏用途/程序化一致性/编号/晋级事实）；最终证据 HEAD ce3dc3f 按槽惯例置 PH-286 done / PH-290 ready 并清 blocker。最终门禁：projectctl check ok=true/task_count=212；23 项治理 OK；verify-change --base e38b9a6 --task PH-286 ok=true、11 文件全白名单。未验证：PH-287/288/289 未实现；PH-269/PH-274 自身测试修改由其实现 PR/CI 验证。
## PH-269 time 可计费确认实现首片（2026-10-31）

基线61950e1（PH-268 规划合入后；PH-259/PH-266/PH-267 done）。新建 `pmo_plugins.time` 插件（manifest 8 字段 requires={contracts}、capabilities=[]；自有迁移 `time/0001_initial`；settings INSTALLED_APPS/PMO_PLUGIN_MODULES 与 delivery urls 绑定 root 单写），首片只承载 `BillableConfirmation`（canonical x-owner=time owned_fact）：org、status draft/confirmed（reversed 枚举保留不产生）、timesheet_ref（namespace=time/kind=timesheet/version）、contract_line_ref（contracts）、confirmed_minutes 1..1440、confirmed_on、evidence≥1、version、created_by/confirmed_by/at、create_token/digest；`BillableConfirmationReceipt` action=draft.save/confirm 幂等收据。迁移双实现触发器：仅 draft 可插入；draft 可改（version+1）；draft→confirmed 单次且 confirmed_by/at 非空、creator≠confirmer；confirmed 后全字段不可改、禁删；收据禁改删。服务 `save_billable_confirmation_draft`/`confirm_billable`/list/get：公司锁、fresh 角色/模块（停用写拒绝、历史读保留）、`core.services.approved_timesheet_for_billable` 只读校验已批准同 org 工时且分钟不超、contracts `approved_line_snapshot` 只读校验唯一批准版本/confirmed_time 模板/项目一致/确认日≥生效日、期间/锁期/未来校验、同 timesheet+条款唯一 confirmed、token 重放/改输入冲突；确认写恰一条 `time.billable_confirmed.v1`（source=confirmation ref、changed_refs=[confirmation, timesheet, contract_line] 3 项、posting_event_refs=[]）与 suppressed `plugin.time.billable.confirmed` audit，不写收入/账本/现金/未清；create≠confirmer 由服务+触发器双保护。视图/表单/模板（列表/草稿表单/独立确认）与 domain metadata 路由（business_read/write）；`verify_integrity` 新增 time 分支（状态/分钟/工时与条款引用/envelope/每 timesheet+条款至多一 confirmed/自审/无财务事件与 SourceConsumption）。
实际执行（最终 HEAD，BASE ddc86d6）：projectctl check ok=true/task_count=212；manage.py check exit0（75 项既有 urls.W003）；makemigrations --check 无变更（time 0001）；migrate+verify_integrity 全新 SQLite ok=true/errors=[]；临时 SQLite 探针：draft→独立确认后 confirmed/360 分钟、verify ok=true，删除 time.billable_confirmed 事件后 exit1 报 billable_confirmation:<id>:confirmed_event_count；scoped 3 模块 12 tests OK（1 项 PG-only skip）；PH-273/PH-282/PH-286 白名单修正后最小同步 6 个跨片测试文件（4 个依赖/顺序/query 上限 + tests/test_contract_approval.py 与 tests/test_v1_contracts.py 的 PG 并发 fixture 先停 time），全量 951 tests/128.912s 回到基线 5F+3E（同一 8 项 macOS）、69 skips。PG 域修复：probe 触发器 drop vendor-aware（PG 合并触发器 ON TABLE；SQLite 三分支）、timesheet_id UUID5 与 core 整数 pk 统一 _timesheet_key(str(pk))、confirm_billable PG select_for_update(of=('self',))、contract_approval PG fixture 先停 time；PG 运行证据以 CI 为准。偏差披露：spec 行为段“可删除草稿”与模型/触发器“禁改删”冲突时以模型/触发器为准（草稿可改、不可删）；确认未另写同名 generic audit `time.billable.confirmed`（core.audit 会为同名非 plugin 动作生成同 topic 非 canonical outbox 事件，与 canonical topic 冲突，改用 suppressed plugin audit 作 canonical source）。未验证：PG 双连接并发（CI）、浏览器 1440/390/键盘/空错停用态、真实客户/生产；PH-152/153/154/150 legacy 未解除。

复核修复（PH-269）：草稿编辑此前被触发器误冻结；已改为 draft 阶段允许修改业务字段（timesheet/contract_line 引用、minutes、date、evidence）并 version+1，身份/创建字段冻结，draft→confirmed 决策时冻结最终业务事实、confirmed 后全字段不可改/禁删、creator≠confirmer 保留；新增“草稿编辑→确认采用编辑值（120 分钟/证据，version 1→2→3）”回归，scoped 12 tests OK（1 PG skip）。
## PH-274 onboarding 导入预览实现前置首片（2026-10-31）

基线814bbb6（PH-269 合入后的 main）。按ADR 0012新建pmo_plugins.onboarding插件（manifest 8字段、requires={contracts,settlement,cash}、INSTALLED_APPS/PMO_PLUGIN_MODULES注册、delivery root单写路由），实现只读CSV模板识别/字段映射/逐行格式与主数据预检（time/expense/plan），返回200行内预览与内存态ephemeral冲突投影；不写ImportPreview/ImportConflict表、不做冲突持久化/normalized_commands/提交/期初，零业务事实/账本/来源消费/规范事件（仅一条generic audit onboarding.import_previewed）；owner/finance/auditor可读（auditor经history_read GET只读预览，文件上传走business_write路由）、pm/member拒绝、模块/依赖停用拒绝；预览无收据、重复确定；不新增迁移/业务表（按main spec，无0001）。按PH-282/PH-286白名单修正恢复ADR requires/migration_dependencies，并在9个既有禁用loop测试（7个+test_v1_contracts/test_contract_approval；与PH-269 time 同loop时先onboarding再time）中先停用onboarding（最小必要、不改业务断言语义），服务层依赖门保留为防御纵深。偏差披露：无0001迁移（按spec）、冲突清单内存态、预览表头宽松映射不改core严格parse_csv、auditor路由拆分、generic audit的常规非canonical outbox行。实际执行（最终HEAD，BASE 814bbb6）：projectctl check ok=true/task_count=212（PH-269 done、PH-274 done）；manage.py check exit0（77条既有W003、0错误）；makemigrations无变更；全量966项=889 pass+69 skip+8项既有macOS失败（无新增）；verify_integrity全新迁移库ok=true；scoped onboarding 15项全通过；verify-change --base 814bbb6 --task PH-274 ok=true（28文件全白名单）。未验证：浏览器1440/390、PG/并发（本片无表/触发器/迁移，CI含PG域）、生产/UAT、旧库升级。


## PH-277 insights 读模型实现前置：契约与只读口径冻结（ADR 0014）（2026-10-31）

基线71d5665（PH-274合入后）。纯契约冻结片（docs-only）：新增 `docs/adr/0014-insights-read-model-contract.md`（编号核验 0014 空闲，未占 0010-0013），冻结 insights 首片三类只读接口（期间预检/核对差异单/项目工作台汇总）的字段、来源 owner 只读接口、canonical ReconciliationCase 字段与状态机、MetricDefinition 引用、角色矩阵（owner/finance/auditor 全量、pm 仅管理项目、member 拒绝）、分页/幂等/并发、无写业务事实边界、实现片精确白名单与反例。同步 PH-257/PH-262 spec decision point 引用 ADR 0014；tasks 更新 PH-277 done、PH-257/262 blockers、PH-272 deps 增加 PH-277 与 blocker；未改 contract schema、未建 scaffold、未写业务代码/迁移/测试逻辑，PH-257/262/272 仍 proposed。偏差：PH-272 spec 不在本任务 BASE allowed_paths，故仅通过 tasks.json 同步其依赖/blocker（spec 文字由 PH-272 自己的实现/规划 PR 同步）。独立 S 复核 PASS（0 阻断：ADR 0014 逐项对照 canonical entities.schema.json——ReconciliationCase required/status/readOnly、MetricDefinition required/status/scope 一致，MetricSnapshot 明确非 canonical；同步与白名单合规）。最终证据 HEAD 71f767f：projectctl check ok=true/task_count=212；23 项治理 OK；verify-change --base 71d5665 --task PH-277 ok=true、9 文件全白名单。未验证：无实现/运行时/浏览器/PG证据（docs-only）。

## PH-275 合同变更影响只读追溯与终止阻断深化（2026-10-31）

基线71d5665（PH-274 onboarding 合入后；PH-269 done）。只读跨域追溯片，无迁移/无新 canonical schema/无写入口：`contracts.approval_services` 新增 `contract_change_impact(member, change_id)` 与 `contract_change_impact_list(member, *, contract_id=None)`——从既有 Contract/ContractVersion/ContractChange/Settlement/OpenItem/Clearance/RevenueRecognition 聚合，返回 change ref/类型/状态/申请日/版本、合同 ref/状态/终止标记、变更前批准版本（price-change 取新版本 version_no-1、终止取仍批准版本）与生效后新批准版本（终止无新版本）、受影响行 refs（前后行合并）、已生效 Settlement 计数/含税合计、active OpenItem 计数/含税合计、active Clearance 计数/已核合计、posted RevenueRecognition 计数/净额合计、差额；只读、无 audit/receipt/event 副作用。终止阻断深化：变更草稿仍拒绝 terminated（PH-265 既有）；结算创建经 `approved_terms_snapshot` 在 as_of>=terminated_on 拒绝（PH-265 既有守卫，本片补反例测试与零半笔断言，不改 settlement 写语义）；`approved_line_snapshot`/`approved_terms_snapshot` 结果新增 `terminated`/`terminated_on`/`contract_status` 只读标记，避免下游静默读取。权限/门控：owner/finance/auditor 读、pm 仅其管理项目且 `summary_only=true`、member 拒绝、跨公司/未知变更拒绝；新增 history_read 路由 `contract-change-impact`，contracts 模块停用后历史影响页仍可读（service 不走模块写门控），无写路由。浏览器：合同详情新增「变更影响（只读）」聚合区；新增只读 `change_detail.html` 影响页（前后版本/引用事实计数金额/终止标记），无编辑/提交入口。verify_integrity 不新增业务不变式（沿用既有契约/审计检查）。实际执行（候选 HEAD）：check ok=true/task_count=212；manage.py check exit0；makemigrations 无变更；migrate+verify_integrity 全新 SQLite ok=true/errors=[]；scoped 10 tests OK；全量 976 tests/116.491s，5F+3E 恰为已知 8 项 macOS 基线、69 skips、无新增；verify-change --base 71d5665 --task PH-275 待 review pass 后记录；review round-1 阻断（effective_on 复制 requested_on）已修：effective_on 改为从已记录事实派生（termination=terminated_on；approved non-termination=新版本 effective_on；draft/rejected=None），补 requested_on≠effective_on 与 termination effective_on==terminated_on 回归，scoped 10 tests OK、全量 976 tests/116.491s 基线 8 项无新增。偏差：②未在 settlement 服务重复加显式 terminated 检查（文件不在白名单，PH-265 approved_terms_snapshot 已覆盖；按 spec“若已覆盖则只补测试与错误文案”）；④以 snapshot 结果新增 terminated 标记实现，不改任何写语义。未验证：PG 并发/CI、浏览器 1440/390/键盘/空错停用态实机检查、真实客户/生产；PH-150/151/155 legacy 未解除。


## PH-290 后续交付独立规划入口（接续 PH-286）（2026-10-31）

基线6ddb951（PH-277/PH-275 合入后，count 216→? 登记前 212）。纯规划片：
1. **晋级**：PH-276（time confirmed 撤销/配对；deps PH-269 done + PH-266/PH-219/PH-222 done）→ ready；PH-280/281/283/284/287/288 已 ready 保持不变。
2. **核号**：BASE 最大 PH-290；远端分支/PR（EXP #143/#144 等）无 ≥291 占用 → PH-291 起连续。
3. **新登记**：PH-291 跨域只读核对总览（ready，deps PH-219/222/224/232/265/269/274 done；delivery 层只读聚合、无迁移/无写入口、不建 insights 第二 owner）、PH-292 onboarding 导入提交首片（proposed，缺 PH-289/PH-181/PH-185）、PH-293 发票/价税分离/红冲契约冻结 ADR 0015（proposed，缺 PH-150/155/252）、PH-294 下一规划槽（proposed，待 PH-290 done 后置 ready）。count 212→216，编号 291-294 连续。
4. **blockers 刷新**：PH-276 清空（晋级）；PH-251/252/255/256/257/260/261/262/271/272/285/289/292/293 保持 proposed，无虚假晋级。
5. 无占位符；acceptance 全可执行。独立 S 复核 PASS（0 阻断：PH-276 晋级/编号/新切片依赖事实核对通过）；最终证据 HEAD 83635ee 按槽惯例置 PH-290 done / PH-294 ready 并清 blocker。最终门禁：projectctl check ok=true/task_count=224；23 项治理 OK；verify-change --base 6ddb951 --task PH-290 ok=true、10 文件全白名单。未验证：PH-291/292/293 未实现；PH-276 由其实现 PR/CI 验证。

## PH-287 合同收费模板/生效条款只读快照与结算核对（2026-10-31）

基线6ddb951（PH-275 合入后；PH-269/274 已合入）。只读核对片，无迁移/无写入口/无 canonical schema/不改 contracts 写路径：新增 `pmo_plugins/contracts/terms_snapshot_services.py`。`approved_terms_snapshot(member, *, contract_id, as_of=None)` 返回合同 ref/状态/终止标记、按 as_of 选出的批准或历史版本 ref/生效日、收费模板集合、条款行（每行经既有 `approval_services.approved_terms_snapshot` 读取；终止/历史版本/金额不一致时回退只读投影并标记 readable=false+原因）、行上限 200、变更历史摘要（变更 ref/类型/状态/申请日/生效日/前后版本）、`summary_only` 与 settlement_guard。`terms_settlement_issues(member, *, as_of=None)` 只读对账异常，issue_code∈{terms_amount_mismatch（条款合计≠行含税）、settlement_partition_mismatch（已生效结算含税≠未清分区合计）、settlement_terms_mismatch（结算冻结条款明细合计≠结算金额）、terms_unapproved_referenced、stale_version_referenced（结算引用 superseded 版本）、line_repeated_consumption（同一条款行多个 active 结算）、cross_project_reference、cross_org_reference、terminated_still_settled（终止日及之后仍有 active 结算）、old_terms_still_active（历史版本条款仍 active）}，每条含 issue_code/contract_ref/refs/expected/actual。权限：owner/finance/auditor 全量；pm 仅管理项目且 summary_only=true；member 拒绝；跨租户/未知拒绝；contracts 模块停用后 history_read 页面仍可读（service 不走模块写门控）。浏览器：新只读 `terms_snapshot.html` + history_read 路由 `contract-terms-snapshot`（合同/日期只读筛选；未知参数 400；无 POST/写入口）。金额整数分；模板代码以 PH-214 实际交付为准（fixed_milestone/confirmed_time/fixed_service_period，非 spec 文字中的旧名）。未写 generic audit `contracts.terms_snapshot.viewed`（保持零副作用；可选，report 披露）。实际执行（候选 HEAD）：check ok=true/task_count=212；manage.py check exit0（80 项既有 urls.W003）；makemigrations 无变更；migrate+verify_integrity 全新 SQLite ok=true/errors=[]；scoped 10 tests OK；全量 986 tests/119.110s，5F+3E 恰为已知 8 项 macOS 基线、69 skips、无新增；verify-change --base 6ddb951 --task PH-287 待 review 后记录（预期仅 review 门禁）。未验证：PG 并发/CI、浏览器 1440/390/键盘/空错停用态实机、真实客户/生产。

## PH-279 现金分类/账本桥接与退款/互转只读核对快照首片（2026-10-31）

基线6ddb951（PH-275合入后的main）。新增pmo_plugins/cash/reconciliation_services.py只读聚合：posted现金→有效CashClassification→AccountingEvent/LedgerPosting/bank_effect claim→Refund/BankReturn→InternalTransfer成对；cash_bridge_snapshot（逐流水投影+账户现金账净额复用classification_services.cash_book_coverage+汇总）与cash_bridge_issues（issue_code/refs/期望/实际，覆盖分类缺失/重复、事件缺失/重复、posting数量/金额/账户范围、claim缺失/净额不符、退款+退回超gross、互转跨org/缺腿/金额日期不一致）；退款/退回反向现金derived_cash覆盖不误报未分类；offset_implemented=false占位（PH-241未合入不伪造）。权限owner/finance/auditor只读、pm/member拒绝；浏览器只读快照/异常清单页+as_of/账户筛选；无迁移/无表/无写入口/无审计写，不写账本/事实。偏差披露：pm因现金事实无项目归属而拒绝（无法安全派生项目摘要）；两条路由以history_read注册，cash停用后GET历史快照/异常清单仍200可读（无写入口、POST 405），服务层历史读保持；offset占位；无审计写；未改verify_integrity断言。实际执行（最终HEAD）：projectctl check ok=true/task_count=212；manage.py check exit0（80条既有W003、0错误）；makemigrations无变更；全量989项=912 pass+69 skip+8项既有macOS失败（BASE 976/5F+3E/69+13新测试，无新增）；verify_integrity全新迁移库ok=true；scoped 13项全通过；verify-change --base 6ddb951 --task PH-279在review pending时仅high_risk_review_required（预期）。未验证：浏览器1440/390、独立S复核、PG/并发、生产/UAT、旧库升级；Offset/自动更正/导出/持久化快照/pm项目归属摘要未实现。


## PH-294 后续交付独立规划入口（接续 PH-290）+ PH-276 白名单修正（2026-10-31）

基线 cfa185f（PH-279 合入后，count 216）。纯规划/契约文字片：
1. **PH-276 allowed_paths + spec 精确白名单加 `core/management/commands/verify_integrity.py`**（用途：扩展 PH-269 既有 time 分支校验 confirmed→reversed 的 status/envelope/audit/receipt、禁止 canonical reversed topic；不新增业务不变式、不加全局禁令）；程序化比对 spec/tasks 去重后逐项一致（18 路径，spec-only=[]、tasks-only=[]）。
2. **核号**：BASE 最大 PH-294；远端分支/PR（EXP #143/#144 等）无 ≥295 占用 → PH-295 起连续。
3. **新登记**：PH-295 工资现金命令层首片（proposed，缺 PH-171/172）、PH-296 费用报销/支出首片（proposed，缺 PH-150/155）、PH-297 服务期预收/递延首片（proposed，缺 PH-163/170）、PH-298 下一规划槽（proposed，待 PH-294 done 后置 ready）。count 216→220，编号 295-298 连续。
4. **晋级**：无新增（PH-280/281/283/284/288/291 等已 ready 保持；依赖未 done 的 proposed 均未虚假晋级）。
5. blockers 刷新：PH-292/293/295/296/297 保持 proposed 并写清缺口；无占位符；acceptance 全可执行。独立 S 复核两轮 PASS（首轮阻断：PH-291 domain_metadata 白名单与 PH-110 ready-未-done 依赖事实，已修；次轮 0 阻断）；最终证据 HEAD 873a530 置 PH-294 done / PH-298 ready 并清 blocker。最终门禁：projectctl check ok=true/task_count=224；23 项治理 OK；verify-change --base cfa185f --task PH-294 ok=true、11 文件全白名单、无 errors。未验证：PH-295/296/297 未实现；PH-276 白名单由其实现 PR/CI 验证。
## PH-276 time 可计费确认撤销与收入/结算只读配对（2026-10-31）

基线fa1ca8d（PH-287 合入后）。time 自有迁移 **0002_time_billable_reversal_guards**（不改 0001）：扩 `BillableConfirmationReceipt.action` CheckConstraint 允许 `draft.save/confirm/reverse`；双 vendor 守卫改为——PG 单函数分支、SQLite 单 `_update_guard`（保留旧触发器名以兼容既有 corruption 探针）允许且仅允许 confirmed→reversed 单次状态迁移（version+1、confirmer/at 与业务字段不变），保留 draft 编辑/draft→confirmed、identity 冻结、receipt 不可改删与禁删。服务 `reverse_billable_confirmation(member, confirmation_id, *, reason, request_token, expected_version, dry_run=True)`：公司锁、`select_for_update(of=('self',))`（nullable FK 教训）、仅 confirmed、创建人不能撤销自己（由另一位 owner/finance 撤销）、reason 1..500、expected_version、同 token 幂等/改输入冲突、dry_run 零副作用；同事务 status→reversed+version+1、generic audit `time.billable_confirmation.reversed`（reason/refs，无 canonical 事件 topic，spec 契约无 reversed 事件）、复用 receipt action=reverse；不写收入/账本/现金/未清。只读配对 `billable_confirmation_pairing(member, confirmation_id)`/`billable_confirmation_pairing_list(member, *, project_id=None)`：返回 confirmation ref/status/minutes、timesheet/contract_line refs、按批准单价估算确认价值、posted RevenueRecognition 计数/净额、active Settlement 计数/含税、差额与 paired/unpaired/partial；只读、无 audit/receipt/事实写。权限：写 owner/finance；读 owner/finance/auditor；pm 仅管理项目；member 拒绝；time 模块停用写拒绝、list/detail 改 history_read 保留历史读。UI：列表详情入口/撤销状态、新增 `billable_confirmation_detail.html`（事实/只读配对/撤销表单）、`billable_confirmation_reverse` 写路由。verify_integrity time 分支扩展：reversed 行要求批准时 canonical confirmed 事件 source 版本=当前-1（历史事件不被撤销改写）、恰一条 reversal generic audit（payload refs/reason）与一条 reverse receipt；confirmed 行不得有 reversal audit；draft 不得有 reversal audit/事件；全库禁止 `time.billable_reversed.v1` canonical topic；receipt action 限于 draft.save/confirm/reverse 且 reverse receipt 必须对应 reversed 行；无全局禁令。实际执行（候选 HEAD）：check ok=true/task_count=216；manage.py check exit0（82 项既有 urls.W003）；makemigrations 无变更（time 0002 为叶）；全新 SQLite 迁移+verify_integrity ok=true/errors=[]；探针：确认后由另一位 finance 撤销→reversed/version3、verify ok，另造“直接 UPDATE reversed 无 audit/receipt”探针后 exit1 报 reversal_audit_count/reversal_receipt_count；scoped 11 tests OK；全量 997 tests/127.008s，5F+3E 恰为已知 8 项 macOS 基线、69 skips、无新增。偏差：spec 冻结“不新增 canonical event topic”（Lead 口述 `time.billable_reversed.v1` 与 spec 冲突，按 spec 不新增；verifier 显式禁止该 topic）；generic audit 由 kernel 自动生成非 canonical DomainEvent `time.billable_confirmation.reversed.v1`，非 canonical topic，报告披露；配对收入优先走既有 owner export，post 数据缺 accounting_event 时回退只读行级投影（不写）；SQLite 守卫保留单 `_update_guard` 名称以兼容 PH-269 既有 corruption 探针。白名单授权披露：PH-276 冻结 allowed_paths 未含 `core/management/commands/verify_integrity.py`，Lead 授权先最小修改（同 PH-241/PH-210 先例），并安排 PH-294 规划槽加入 allowed_paths（18 路径）；扩展仅识别 reversed/校验 reversal audit+receipt+envelope/禁止 canonical reversed topic，不新增业务不变式、不加全局禁令；PR 合并前须先有白名单修正 BASE。未验证：PG 并发/触发器 CI、浏览器 1440/390/键盘/空错停用态实机、真实客户/生产。

## PH-283 结算未清/核销/退款覆盖只读核对首片（2026-10-31）

基线a4584d1（PH-294合入后的main）。新增pmo_plugins/settlement/coverage_read_services.py只读聚合：OpenItem gross→Σactive Clearance→Refund/BankReturn覆盖→posted现金gross；open_item_coverage_snapshot（逐未清项gross/cleared/offset(0)/refunded/returned/remaining服务端派生+clearances refs，上限200项+汇总）与open_item_coverage_issues（cleared_over_gross/remaining_negative/remaining_mismatch/clearance_duplicate_active/clearance_cross_org/clearance_cash_missing/cross_org/not_posted/direction/ref_mismatch/cash_coverage_over_gross）；remaining=gross-active cleared-offset(0)-refunded/returned（summary同口径），offset_cents=0、offset_implemented=false（PH-241未合入，不读Offset表不伪造）；duplicate按canonical (open_item,cash_transaction)判重。权限owner/finance/auditor全量、pm仅visible_projects中manager=member管理项目、member拒绝；settlement停用后服务层历史可读，两条history_read浏览器路由GET仍200；无audit/写/迁移。浏览器只读未清覆盖/异常清单页+as_of/项目筛选。偏差披露：Offset未实现；现金事实无项目归属故跨项目仅能核对跨org/缺失/非posted/方向/过期ref；退款/退回覆盖直接读Refund/BankReturn模型保持历史可读；反例全部append-only兼容（INSERT额外active Clearance/重复行，绝不UPDATE/DELETE）；remaining_mismatch用调整后模型剩余对比新口径；无审计写、未改verify_integrity断言。实际执行（最终HEAD，BASE a4584d1）：projectctl check ok=true/task_count=224；manage.py check exit0（83条既有W003、0错误）；makemigrations无变更；全量1013项=936 pass+69 skip+8项既有macOS失败（无新增）；verify_integrity全新迁移库ok=true；scoped 14项全通过；verify-change --base a4584d1 --task PH-283 ok=true。未验证：浏览器1440/390、独立S复核终审、PG/并发、生产/UAT、旧库升级；Offset/自动更正/导出/持久化快照/跨项目现金归属未实现。

## PH-291 跨域只读核对总览首片（2026-10-31）

基线 a4584d1（PH-294 合入后）。交付 `pmo_plugins/delivery/operations_read_services.py`（新，`ops_reconciliation_overview(member, *, as_of=None, period=None)`）+ `delivery/domain_metadata.py`、`views.py`、`forms.py`、`urls.py`、模板 `delivery/ops_reconciliation_overview.html` 与 17 个测试；路由 `modules/delivery/ops-reconciliation-overview/` 在 canonical `domain_metadata.py` 声明为 `history_read`（urls.py 仅导入使用，无元数据副本），模块停用后历史只读、业务写不可用。

范围与口径：合同（待审变更/已终止合同/终止后仍生效条款）、结算与未清（活动未清项/超额核销/退款/退回/退款未覆盖；pm 按管理项目 OpenItem→active Clearance 可见现金严格过滤退款/退回，不泄漏他人项目金额）、管理收入（已过账/已冲销/终止后确认/确认-结算差额）、工时与可计费（待确认/已确认分钟/待审工时；pm 按可见 Timesheet 的 canonical UUID5 `uuid5(NAMESPACE_URL,'pmo:timesheet:<pk>')` 派生范围）、导入预览（PH-274 无持久化 → null+`import_preview_persistence_not_implemented`）、现金（已过账/缺分类/流入流出净额；pm 为 null+`cash_not_scoped_to_project`）；金额整数分、期间 YYYY-MM 与 as_of 只读过滤；缺失 null+missing 不显示 0。纯度：无写、无审计、无收据、无迁移、无 session；测试断言零 INSERT/UPDATE/DELETE 查询与零事实/审计/事件计数变化。反例覆盖 member 拒绝、跨 org/项目隔离、pm 退款/工时范围、非法 as_of/period/未知参数 400、POST 405、模块停用历史可读。

独立 S 复核两轮 PASS（首轮 3 阻断：canonical domain_metadata 路由、pm 结算退款泄漏、pm time UUID5，已修；次轮 0 阻断）。最终证据 HEAD fc244d7：projectctl check ok=true/task_count=224；manage.py check 83 条既有 warning/0 error；makemigrations --check 无变更；全量 1016 tests=5F+3E+69 skips（既有 macOS/DRF 环境失败，与基线一致、无新增）；verify_integrity 新库 {"ok": true, "errors": []}；scoped 17 tests OK（含 pm 退款/pm UUID5 回归）；verify-change --base a4584d1 --task PH-291 ok=true、14 文件全白名单、无 errors。未验证：PG 并发/浏览器 1440-390 交 CI/后续；生产/UAT 未验证。


## PH-298 后续交付独立规划入口（接续 PH-294）+ PH-281 canonical 路由白名单（2026-10-31）

基线 e43c2c2（PH-291 合入后，count 220）。纯规划/契约文字片：
1. **PH-281 allowed_paths + spec 精确白名单加 `pmo_plugins/delivery/domain_metadata.py`**（用途：identity-access 运营视图路由 canonical 声明，path identity-access/，access=history_read；delivery/urls.py 仅导入使用，不复制元数据）；程序化比对去重后逐项一致（14 路径，spec-only=[]、tasks-only=[]）。
2. **核号**：BASE 最大 PH-298；远端分支/PR（EXP #143/#144 等）无 ≥299 占用 → PH-299 起连续。
3. **新登记**：PH-299 期间关期/预测覆盖契约冻结（proposed，缺 PH-180/181/182）、PH-300 项目关账/结项前置冻结（proposed，缺 PH-180/181/185）、PH-301 到期扫描/通知中心首片（proposed，缺 PH-179/183/151）、PH-302 下一规划槽（proposed，待 PH-298 done 后置 ready）。count 220→224，编号 299-302 连续。
4. **晋级**：无新增（PH-280/281/284/288 等 ready 保持；依赖未 done 的 proposed 均未虚假晋级）。
5. blockers 刷新：PH-292/293/295/296/297/299/300/301 保持 proposed 并写清缺口；无占位符；acceptance 全可执行。实际执行：projectctl check、23项治理；verify-change在独立S复核落pass后按最终HEAD重跑记录。未验证：PH-299/300/301 未实现；review由独立S复核。

## PH-280 手续费批准/成本与净额只读配对（2026-10-31）

基线e43c2c2（PH-291/PH-283/PH-276 等合入后）。只读核对片，无迁移/无写入口/无 canonical schema/不改 costing 写路径：新增 `pmo_plugins/costing/bank_charge_pairing_services.py`。`bank_charge_pairing(member, *, status=None, cash_transaction_id=None, as_of=None)` 返回可见 BankCharge 的只读配对列表：charge ref/状态/版本、原 posted 净额现金 ref/gross/status/direction、费用 gross/tax（tax 拆分标记未实现）、审批链 submitted/approved 人与时间、一次性成本 claim coverage_key/claim refs/amount、AccountingEvent refs/claim_effect、LedgerPosting refs/amount/book、净额+费用派生 implied gross、退款/退回覆盖合计与 offset_implemented=false、PH-251 占位（ReceiptFeeSettlement/existing_cost_ref 复用/counterparty-borne/税额拆分均 not_implemented）、issues 与 pairing_status（draft/approved_unposted/posted/partial/paired/异常；异常优先）。`bank_charge_pairing_issues(member, *, as_of=None)` 展开 issue 列表：approved_without_cost、cost_event_duplicate、cost_posting_duplicate、cost_posting_amount_mismatch、cost_claim_amount_mismatch、cost_claim_duplicate、fee_exceeds_cash_gross、cash_not_posted_in、cross_org_reference、cross_project_reference、unapproved_cost_posted、coverage_unavailable；每条含 issue_code/charge_ref/refs/expected/actual。权限：owner/finance/auditor 全量、pm 仅管理项目（scope project）、member 拒绝；costing 模块停用后 history_read 页面仍可读；只读零副作用（无 audit/receipt/事件/事实写）。浏览器：新只读 `bank_charge_pairing.html` + history_read 路由 `bank-charge-pairing`（状态/基准日/现金流水筛选；未知参数 400；无 POST 写入口）。实际执行（候选 HEAD）：check ok=true/task_count=224；manage.py check exit0（88 项既有 urls.W003）；makemigrations 无变更；全新 SQLite 迁移+verify_integrity ok=true/errors=[]；scoped 8 tests OK；全量 1049 tests/129.292s，5F+3E 恰为已知 8 项 macOS 基线、69 skips、无新增。金样例：净额现金 9800 + 费用 200 草稿→提交→另一位 finance 批准→成本 claim/event/posting 200 → 配对 posted、implied gross 10000、refund 0、issues=[]；异常反例逐条命中 approved_without_cost/cost_event_duplicate/cost_posting_amount_mismatch/fee_exceeds_cash_gross/cash_not_posted_in/unapproved_cost_posted/cross_project_reference/cross_org_reference。偏差：配对接口按筛选返回列表（现金唯一时 0/1 条）；成本 rows 由既有 `bank_charge_cost_snapshot` 之外直接只读查询 claim/event/posting 以发现重复（owner 只读接口不含重复清单），无写；cross-org 读仅在同 org 快照不可用时用只读 CashTransaction 组织字段判定；PH-251 字段一律 not_implemented 占位不伪造；可选 generic audit 未写以保持零副作用。反例测试兼容 append-only：仅用合法服务迁移 + 对无法由服务产生的 approved-无成本/重复事件/错额/未批准成本等半笔用 vendor-aware 临时 drop 触发器后 **INSERT** 构造（不 UPDATE/DELETE 历史事实），TestCase 回滚恢复；PG 同一路径。未验证：PG CI、浏览器 1440/390/键盘/空错停用态实机、真实客户/生产。
## PH-288 银行账户/现金余额只读对账汇总首片（2026-10-31）

基线24efcf7（PH-280合入后的main）。新增pmo_plugins/cash/bank_balance_services.py只读派生：按银行账户/期间聚合posted现金in/out与净额、退款/退回合计、内部互转in/out与抵销净额、分类桥接现金账in/out净额、期初/期末余额（closing=opening+posted_in-posted_out，以posted现金事实派生、不建余额表），上限200账户+汇总；bank_account_issues输出account_no_posted_facts/unclassified_posted_cash/ledger_net_mismatch/refund/return/transfer跨账户或跨org/transfer_same_account/amount_mismatch/negative_balance/future_fact_after_closed_period；ledger cash_in/out与posted同窗口[period start,as_of]计算，refund/return按refund_date/return_date、互转按transfer_date窗口过滤（summary distinct pair数）；offset_implemented=false、offset_cents=0（PH-241未合入，不读Offset表不伪造）。权限owner/finance/auditor只读、pm/member拒绝（银行账户与现金事实无项目归属，pm项目摘要不可安全派生——已披露）；cash停用后服务层历史可读，两条history_read浏览器路由GET仍200；无audit/写/迁移。浏览器只读余额汇总/异常清单页+as_of/期间/账户筛选。偏差披露：pm因无项目归属而拒绝；Offset未实现；余额含退款/退回/互转实际现金腿，ledger_net与非派生bridge posted同窗口比较；future_fact_after_closed_period用AccountingPeriod.status='posted'+closed_through；反例append-only兼容（额外LedgerPosting/未来互转用INSERT，绝不UPDATE/DELETE）；无审计写、未改verify_integrity断言。实际执行（最终HEAD，BASE 24efcf7）：projectctl check ok=true/task_count=224；manage.py check exit0（88条既有W003、0错误）；makemigrations无变更；全量1056项=979 pass+69 skip+8项既有macOS失败（无新增）；verify_integrity全新迁移库ok=true；scoped 15项全通过；verify-change --base 24efcf7 --task PH-288 ok=true。未验证：浏览器1440/390、独立S复核终审、PG/并发、生产/UAT、旧库升级；Offset/手续费/银行导入/自动更正/导出/持久化余额表/pm项目归属摘要未实现。


## PH-281 成员账号/项目范围与授权审计只读运营视图首片（2026-10-31）

基线 328e119（PH-288/PH-280 合入后）。交付 `core/identity_read_services.py`（新，`member_access_snapshot` / `member_access_audit` / `member_access_issues`）、`core/identity_views.py`、`core/identity_forms.py`、`core/templates/core/identity_access.html`、canonical `pmo_plugins/delivery/domain_metadata.py` 路由 `identity-access/`（access=history_read）+ `delivery/urls.py` 仅导入绑定，15 个测试。

范围与口径：成员/角色/停用状态/项目范围 refs/AgentToken 计数（有效/撤销/过期，as_of 派生）/最近身份审计；审计摘要只返回安全字段（changed_fields/role/active/project_scope_ids/name），不含 token_hash/auth_snapshot/密码/费率；pm 审计始终限制到管理项目可见成员（含 AgentToken entity 映射），无 member_id 也不泄漏全公司；issues 覆盖停用成员持有效令牌、过期未撤销、公司角色带项目范围、跨公司范围、审计缺口、同名重复有效令牌；owner/finance/auditor 全量、pm 仅管理项目摘要（令牌明细/最近变更隐藏）、member 拒绝；分页≤200；as_of 不得未来；无写/审计/收据/session/迁移；测试断言零 INSERT/UPDATE/DELETE 与事实计数不变。

实跑（HEAD 328e119）：projectctl check ok=true/task_count=224；manage.py check 91 条既有 warning/0 error；makemigrations --check 无变更；全量 1079 tests=5F+3E+69 skips（既有 macOS/DRF 环境失败，无新增）；verify_integrity 新库 {"ok": true, "errors": []}；scoped 15 tests OK（含 pm 审计范围回归）；verify-change --base 328e119 --task PH-281 ok=true、14 文件全白名单、无 errors。未验证：PG/浏览器 1440-390/生产/UAT。


## PH-302 后续交付独立规划入口（接续 PH-298）+ flaky 测试修复登记（2026-10-31）

基线 c640a1c（PH-281 合入后，count 224）。纯规划/契约文字片：
1. **flaky 测试修复任务 PH-303（ready，deps PH-291 done）**：已合并 PH-291 测试 `test_pm_page_scope_and_cash_missing` 用 `assertNotContains(response,'C8')` 断言合同编码不可见，随机命中 CSRF token（PH-281 PR 首次 CI 失败、二次通过）；PH-303 仅改该测试文件（唯一长编码/上下文断言，保留业务断言，连续 3 次稳定），白名单仅测试文件+治理/report/handoff。
2. **核号**：BASE 最大 PH-302；远端分支/PR（EXP #143/#144 等）无 ≥303 占用 → PH-303 起连续。
3. **新登记**：PH-303 flaky 修复（ready）、PH-304 运营核对底稿授权只读导出（ready，deps PH-291/283/288/280/281 done）、PH-305 共享费用分摊契约冻结（proposed，缺 PH-162/255）、PH-306 预算/ETC 契约冻结（proposed，缺 PH-177/261）、PH-307 下一规划槽（proposed，待 PH-302 done 后置 ready）。count 224→229，编号 303-307 连续。
4. **晋级**：无新增（PH-284 等 ready 保持；PH-292/293/295/296/297/299/300/301/305/306 保持 proposed，无虚假晋级）。
5. 无占位符；acceptance 全可执行。独立 S 复核 PASS（0 阻断：flaky 修复白名单/编号/晋级事实核对通过）；最终证据 HEAD b83b02e 按槽惯例置 PH-302 done / PH-307 ready 并清 blocker。最终门禁：projectctl check ok=true/task_count=229；23 项治理 OK；verify-change --base c640a1c --task PH-302 ok=true、10 文件全白名单、无 errors。未验证：PH-303/304/305/306 未实现；PH-303 由其实现 PR/CI 验证（连续 3 次稳定）。
## PH-284 管理收入确认与结算/合同条款只读配对核对（2026-10-31）

基线c640a1c（PH-281/PH-288/PH-283/PH-276 等合入后）。只读核对片，无迁移/无写入口/无 canonical schema/不改 management_revenue/settlement/contracts 写路径：新增 `pmo_plugins/management_revenue/pairing_read_services.py`。`revenue_pairing_snapshot(member, *, project_id=None, period=None, as_of=None)`：按项目/期间返回 contract_value（approved 合同条款净额，经 contracts approved_contract_totals）、recognized（posted RevenueRecognition 净额 − posted 调整，经 management_revenue revenue_export_rows；owner export 依赖 accounting_event 时回退同权限只读行级投影）、settled（active Settlement gross，经 settlement_snapshot）、unsettled（active OpenItem gross − active Clearance，只读）、cash_coverage（active Clearance 合计）、difference（recognized−settled）、contract_variance（contract_value−recognized）、terminated/终止合同 refs、recognition/settlement refs（≤200），project_id=None 时返回项目列表与汇总。`revenue_pairing_issues(member, *, as_of=None)` 返回 issue_code/refs/expected/actual：recognized_over_contract_line、recognition_source_invalid、recognized_after_termination、settlement_source_invalid、settlement_after_termination、settlement_without_recognition、settled_exceeds_recognized、terms_without_recognition、cross_project_reference、cross_org_reference、duplicate_recognition。权限：owner/finance/auditor 全量、pm 仅管理项目（project manager 过滤）、member 拒绝；management_revenue 模块停用后 history_read 页面仍可读；只读零副作用（无 audit/receipt/事件/事实写）。浏览器：新只读 `revenue_pairing_snapshot.html` + history_read 路由 `revenue-pairing`（项目/期间/基准日筛选；未知参数 400；无 POST 写入口）。实际执行（候选 HEAD）：check ok=true/task_count=224；manage.py check exit0（92 项既有 urls.W003）；makemigrations 无变更；全新 SQLite 迁移+verify_integrity ok=true/errors=[]；scoped 9 tests OK；全量 1088 tests/129.468s，5F+3E 恰为已知 8 项 macOS 基线、69 skips、无新增。金样例：合同净额 100000、确认 60000、结算 40000、未结清 10000、现金覆盖 30000 → snapshot 各派生值一致、issues=[]；异常反例命中 recognized_over_contract_line/settlement_without_recognition/recognized_after_termination/settlement_after_termination/cross_project_reference。偏差：contract_value 用净额、settled 用 settlement gross（与 recognized 净额口径差异仅展示不调整）；unsettled 为 OpenItem gross−active clearance、cash_coverage 为 active clearance；revenue owner export 若缺 accounting_event 回退只读行级投影；settlement/OpenItem/Clearance 聚合为只读模型查询（owner 无按项目清单接口）；pm 的 settlement_snapshot 若被角色拒绝则显示 0/缺失而非伪造；duplicate_recognition 受 DB OneToOne 约束通常不可达，代码仍检测；不写可选 viewed audit。反例测试 append-only 兼容：所有异常事实均 INSERT 构造或合法服务迁移，不 UPDATE/DELETE 历史。复核 round-1 阻断修复：被拒的 settlement_snapshot 不再被静默跳过当 0；settled/unsettled/cash/difference 变为 null + settlement_missing/missing 标记 + settlement_unreadable issue，页面显示“缺失”，并新增 service/HTTP 回归（mock PermissionDenied）。最终 BASE a352b4a：check ok/229、scoped 11 OK、全量 1090 tests/135.179s（5F+3E/69=基线 8 项）、verify_integrity 新库 ok。未验证：PG CI、浏览器 1440/390/键盘/空错停用态实机、真实客户/生产。

## PH-307 后续交付独立规划入口（接续 PH-302）+ PH-303 risk A→S 修正（2026-10-31）

基线 a352b4a（PH-302 合入后，count 229）。纯规划/契约文字片：
1. **PH-303 risk A→S**（old→new：A→S）：PH-303 白名单含 `project/tasks.json`、`project/manifest.json` 等治理 critical-path 文件，verify-change 规则要求 BASE risk=S；PH-303 spec 风险段已同步说明（行为/白名单不变，test-only），PH-307 report 记录 old→new→影响。此修正是 PH-303 解阻塞关键路径。
2. **核号**：BASE 最大 PH-307；远端分支/PR（EXP #143/#144 等）无 ≥308 占用 → PH-308 起连续。
3. **新登记**：PH-308 多币种/汇率契约冻结（proposed，缺 PH-150/155/252）、PH-309 运营核对工作台聚合（ready，deps PH-291/280/281/283/288 done）、PH-310 导出归档/格式/审计后续（proposed，缺 PH-304/285/181/182）、PH-311 下一规划槽（proposed，待 PH-307 done 后置 ready）。count 229→233，编号 308-311 连续。
4. **晋级**：无新增（PH-303/304/284 等 ready 保持；PH-292/293/295/296/297/299/300/301/305/306/308/310 保持 proposed，无虚假晋级）。
5. 无占位符；acceptance 全可执行。独立 S 复核 PASS（0 阻断：PH-303 risk A→S 修正、编号/晋级事实核对通过）；最终证据 HEAD 4c7fac3 按槽惯例置 PH-307 done / PH-311 ready 并清 blocker。最终门禁：projectctl check ok=true/task_count=233；23 项治理 OK；verify-change --base a352b4a --task PH-307 ok=true、10 文件全白名单、无 errors。未验证：PH-308/309/310 未实现；PH-303 由其后 rebase 的实现 PR/CI 验证。
## PH-304 运营核对底稿授权只读 CSV 导出首片（2026-10-31）

基线5361a09（PH-307合入后的main）。新增pmo_plugins/delivery/operations_export_services.py只读导出：按授权组合PH-291跨域总览、PH-283结算未清/核销/退款覆盖、PH-279现金分类/桥接/退款/互转、PH-280手续费批准/成本/净额配对四类数据集，生成UTF-8+BOM CSV（表头、refs/金额/状态、as_of/期间、生成时间、行数与#checksum sha256行）；dry_run返回计划不生成文件；request_token进程内幂等（plan/result分开缓存，按dry_run选择；同token同输入同content_id、改输入冲突；非持久已披露）。权限owner/finance/auditor全量；pm仅ops_overview/settlement_coverage/fee_pairing（有项目归属），cash_bridge拒绝；member拒绝；delivery停用后history_read历史可读。canonical pmo_plugins/delivery/domain_metadata.py声明ops-export/ history_read路由，delivery/urls.py仅导入；无POST写入口；无写/无迁移/无审计/零事实计数变化。浏览器ops_export页面+download=1 CSV响应，plan token带入下载URL保证同输入幂等。偏差披露：receipt进程内非持久（不建表）；pm cash_bridge拒绝（无项目归属）；单路由承载页面/下载；CSV布局与content_id=sha256；仅组合已交付只读服务不重算；无审计写、未改verify_integrity；反例/测试不UPDATE/DELETE历史行。实际执行（最终HEAD，BASE 5361a09）：projectctl check ok=true/task_count=233；manage.py check exit0（既有W003、0错误）；makemigrations无变更；全量1106项=1029 pass+69 skip+8项既有macOS失败（无新增）；verify_integrity全新迁移库ok=true；scoped 16项全通过；verify-change --base 5361a09 --task PH-304 ok=true。未验证：浏览器1440/390、独立S复核终审、PG/并发、生产/UAT、旧库升级；ZIP/外发/定时邮件/持久化导出收据/身份敏感明细未实现。

## PH-303 修复 PH-291 运营核对总览 HTTP 测试随机 CSRF 误匹配（test-only）（2026-10-31）

基线 5361a09（PH-307 合入后，PH-303 risk=S）。test-only 修复：`tests/test_v1_ops_reconciliation_overview_http.py::test_pm_page_scope_and_cash_missing` 原用 `assertNotContains(response, 'C8')` 断言 finance 项目合同编码不可见，但 `'C8'` 会随机命中 CSRF/十六进制文本（PH-281 PR 首次 CI 失败、二次通过）。修复：合同编码改为唯一长编码 `CT-HIDDEN-C8-9f3a`，并改为断言派生 payload（pm 计数=1、pm refs 含可见变更且不含隐藏变更、响应不含完整长编码；owner 计数=2 且含隐藏变更 ref）；保留 `cash_not_scoped_to_project` 与 pm 范围业务断言。未改产品代码/迁移/其它测试/门禁。risk=S 由 PH-307 规划槽修正（白名单含 tasks/manifest 治理关键路径）。

实跑（候选 HEAD，BASE 5361a09）：同一 HTTP 测试连续运行 3 次均 OK；scoped 服务测试 10 tests OK；全量 1090 tests=5F+3E+69 skips（既有 macOS/DRF 环境失败，无新增）；projectctl check ok=true/task_count=233；23 项治理 OK；独立 S 复核 PASS；verify-change --base 5361a09 --task PH-303 ok=true、6 文件全白名单、无 errors。未验证：PG/浏览器（test-only 无要求）；生产/UAT 不适用。


## PH-311 后续交付独立规划入口（接续 PH-307）（2026-10-31）

基线 fd0136a（PH-303/304/307/284 合入后，count 233）。纯规划/契约文字片：
1. **核号**：BASE 最大 PH-311；远端分支/PR（EXP #143/#144 等）无 ≥312 占用 → PH-312 起连续。
2. **新登记**：PH-312 期间关期/项目结项实现前置（proposed，缺 PH-299/300/180/181/182/185）、PH-313 共享费用分摊/预算 ETC 实现前置（proposed，缺 PH-305/306/162/255/177/261）、PH-314 通知中心/导出归档实现前置（proposed，缺 PH-301/310/285/179/183）、PH-315 下一规划槽（proposed，待 PH-311 done 后置 ready）。count 233→237，编号 312-315 连续。
3. **晋级**：无新增（PH-309/304/284 等 ready 保持；PH-292/293/295/296/297/299/300/301/305/306/308/310/312/313/314 保持 proposed，无虚假晋级）。
4. blockers 刷新：PH-310 更新为“PH-304 done 已满足，仍缺 PH-285/181/182”；其余 proposed 按事实保留。
5. 无占位符；acceptance 全可执行。独立 S 复核 PASS（0 阻断：编号/无虚假晋级/blockers 刷新核对通过）；最终证据 HEAD 78cc148 按槽惯例置 PH-311 done / PH-315 ready 并清 blocker。最终门禁：projectctl check ok=true/task_count=237；23 项治理 OK；verify-change --base fd0136a --task PH-311 ok=true、9 文件全白名单、无 errors。未验证：PH-312/313/314 未实现；PH-309/304 由各自实现 PR/CI 验证。
## PH-309 运营核对工作台聚合首片（2026-10-31）

基线81ec533（PH-311合入后的main）。新增pmo_plugins/delivery/ops_workbench_services.py只读聚合：ops_overview（PH-291，含合同/结算/收入/时间/导入/现金）、settlement_coverage（PH-283）、cash_bridge（PH-279）、fee_pairing（PH-280）、identity_access（PH-281）五分区，每分区返回issue计数、关键合计、missing、available与下钻URL；缺失null+missing不显示0；不重算口径、不建第二owner、无写/无迁移/无审计/无收据。权限owner/finance/auditor全量；pm仅管理项目摘要（cash_bridge无项目归属→available=false+missing=permission）；member拒绝；delivery停用后history_read历史可读。canonical domain_metadata声明ops-workbench/ history_read路由，delivery/urls.py仅导入；浏览器只读工作台页面+as_of/期间筛选，无POST。偏差披露：五分区按spec、合同/收入/银行余额经PH-291间接聚合；pm cash_bridge不可用（无项目归属）；missing/null语义；无审计写、未改verify_integrity；反例/测试不UPDATE/DELETE历史行。实际执行（最终HEAD，BASE 81ec533）：projectctl check ok=true/task_count=237；manage.py check exit0（既有W003、0错误）；makemigrations无变更；全量1115项=1038 pass+69 skip+8项既有macOS失败（无新增）；verify_integrity全新迁移库ok=true；scoped 9项全通过；verify-change --base 81ec533 --task PH-309 ok=true。未验证：浏览器1440/390、独立S复核终审、PG/并发、生产/UAT、旧库升级；持久化工作台快照/导出/告警/客户门户/新核对规则未实现。

## PH-315 后续交付独立规划入口（接续 PH-311）（2026-10-31）

基线 81ec533（PH-311 合入后，count 237）。纯规划/契约文字片：
1. **核号**：BASE 最大 PH-315；远端分支/PR（EXP #143/#144 等）无 ≥316 占用 → PH-316 起连续。
2. **新登记**：PH-316 工作台导出联动（proposed，缺 PH-309）、PH-317 发票红冲/税额抵扣实现前置（proposed，缺 PH-293/252/150/155）、PH-318 多币种实现前置（proposed，缺 PH-308/150/155/252）、PH-319 下一规划槽（proposed，待 PH-315 done 后置 ready）。count 237→241，编号 316-319 连续。
3. **晋级**：无新增（PH-309 仍 ready 待合入；PH-292/293/295/296/297/299/300/301/305/306/308/310/312/313/314/316/317/318 保持 proposed，无虚假晋级）。
4. blockers 按事实保留；无占位符；acceptance 全可执行。独立 S 复核 PASS（0 阻断：编号/无虚假晋级/依赖事实核对通过）；最终证据 HEAD 9c0db33 按槽惯例置 PH-315 done / PH-319 ready 并清 blocker。最终门禁：projectctl check ok=true/task_count=241；23 项治理 OK；verify-change --base 81ec533 --task PH-315 ok=true、9 文件全白名单、无 errors。未验证：PH-316/317/318 未实现；PH-309 由实现 PR/CI 验证。


## PH-319 下一规划槽：依赖基线审计 + 剩余 1.0 路线（2026-10-31）

基线 2ec2232（PH-309/PH-315 合入后；241 tasks，120 done，99 个 v1.0.0 open）。纯规划/契约文字片：
1. **依赖基线审计**：统计 99 个 open 任务的 depends_on/blockers，高频 stale 依赖 PH-110(49)/PH-181(19)/PH-150(15)/PH-142(11)/PH-180(9)/PH-185(9)/PH-155(8)/PH-162(8)/PH-182(8) 等；逐项对照既有 done 能力与 canonical 契约。
2. **晋级（7 项，均有 done 证据）**：PH-293 发票 ADR（deps→PH-214/224/232/265/284/287）、PH-308 多币种 ADR（→PH-214/224/232/287）、PH-299 关期 ADR（→PH-219/224/232/265/269/274/277/283/288/291）、PH-300 结项 ADR（→PH-275/283/284/287/288/291）、PH-305 分摊契约（→PH-217/244/250）、PH-306 预算契约（→PH-214/217/224/232/244/250/287）、PH-316 工作台导出（→PH-309/PH-304 均 done）。spec 依赖/风险段同步 old→new 理由，blockers 清空，status ready。
3. **不晋级**：PH-317/318（等 PH-293/308 done）、PH-312/313/314（等 ADR/实现前置）、PH-310（等 PH-285/181/182）、PH-292（等 PH-289/181/185）等保持 proposed + 精确 blocker，无虚假晋级。
4. **实现路线**：新增 `ROADMAP.md（剩余 1.0 实现路线节）`（批次 A ADR 冻结 → 批次 B 写侧首片 → 批次 C 验收/运维与剩余缺口）。
5. **登记**：PH-320 发票实现（proposed，deps PH-293/252/214/224/232）、PH-321 多币种实现（proposed，deps PH-308/150/155）、PH-322 关期/结项实现（proposed，deps PH-299/300/312/180-185）、PH-323 下一规划槽（proposed，待 PH-319 done 后置 ready）。count 241→245，编号 320-323 连续。
6. 独立 S 复核首轮 1 阻断（PH-305/306 写侧误指 PH-320/PH-323）已修为 PH-313，二轮 PASS；最终证据 HEAD 9fc38a8 按槽惯例置 PH-319 done / PH-323 ready 并清 blocker。最终门禁：projectctl check ok=true/task_count=245；23 项治理 OK；verify-change --base 2ec2232 --task PH-319 ok=true、17 文件全白名单、无 errors。未验证：PH-293/299/300/305/306/308/316 的 ADR/契约文档本身与 PH-320/321/322 实现尚未交付；review 二轮 PASS。


## PH-293 发票/价税分离与红冲 ADR 0015 契约冻结（docs-only）（2026-10-31）

基线 15cf20c（PH-319 合入后）。docs-only 契约冻结：新增 `docs/adr/0015-invoice-tax-contract.md`（按 BASE 白名单字面文件名，不占 0010–0014）。内容：引用 canonical `Invoice`/`InvoiceLine`/`InvoiceAllocation`/`InvoiceReversal`/`TaxQualification` 字段与 `invoicing.invoice.record|reverse/v1`、`invoicing.lines.allocate/v1` 命令/事件；冻结状态语义（私有 draft 不输出 canonical，登记成功即 `active`，`posted` 预留后续入账，`reversed` 仅全额红冲状态位、`superseded` 需后续规划）；价税分离（整数分、gross=net+tax、行和守恒、ROUND_HALF_UP 末项吸收、税率快照不推断）；税额资格（pending/unknown 默认含税暂计成本，recoverable/not_recoverable 在开放期由 costing owner 更正）；红冲 append-only（负值、≤未冲、原票/行/分配永不编辑，红冲≠退款，收入/现金走各自 owner）；命令幂等/公司锁/查重；角色矩阵与模块停用 history_read；与 PH-252 关系（唯一写侧实现仍 proposed，ADR 不授权实现）及只读核对接入点。同步 PH-252 spec/bloker 指向 ADR。未改已合并 ADR 0010–0014、未改 contract schema、无代码/迁移/运行时验证（docs-only）。独立 S 复核首轮 1 阻断（部分红冲原票误留 posted）已修为保持 active 并补状态转移表，二轮 PASS。最终门禁：projectctl check ok=true/task_count=245；23 项治理 OK；verify-change --base 15cf20c --task PH-293 ok=true、7 文件全白名单、无 errors。未验证：PH-252/PH-320 实现未做；无运行时/浏览器/PG 验证（docs-only）。

## PH-299 期间关期与预测覆盖契约冻结 ADR（2026-10-31）

基线15cf20c。docs-only：新增docs/adr/0016-forecast-close-contract.md，冻结关期状态机（open→closing→closed→reopened，append-only结论、重复关期拒绝、公司锁/request_token/并发一胜）、锁账边界（AccountingPeriod.closed_through为唯一服务端边界；关期不代写业务事实、关期后owner写拒绝、更正只走既有负值冲销/撤核/撤转或明确重新开期）、预测覆盖（forecast batch period/version/scope/status/coverage/covered/gap/source_refs/as_of；覆盖服务端派生、缺失null+missing、客户端不得传覆盖/金额；版本append-only superseded）、跨owner边界（delivery只读组合PH-291/283/288/280/281/279/277/274，不建第二owner、不重算口径）、只读核对与权限矩阵（owner/finance写、auditor只读、pm管理项目摘要、member拒绝、模块停用历史只读）。同步更新tasks.json的PH-322 blocker指向ADR 0016与PH-300；不写代码/迁移/schema/canonical topic/command。编号核验：ADR叶0014、0015已被发票ADR任务占用、0016按spec冻结。实际执行（最终HEAD）：projectctl check ok=true/task_count=245；23项治理OK；verify-change --base 15cf20c --task PH-299在review pending时仅high_risk_review_required（预期）。未验证：docs-only无运行时/浏览器/PG/迁移；独立S复核pending；关期/预测实现由PH-322承接。

## PH-300 项目关账/结项前置契约冻结 ADR（2026-10-31）

基线15cf20c。docs-only：新增docs/adr/0017-project-close-contract.md，冻结项目结项状态机（active→closing→closed→reopened，append-only结论、重复结项拒绝、公司锁/request_token/并发一胜）、结项前置核对清单（合同/变更终止、结算/未清与覆盖、收入配对、成本/手续费、现金对账、时间/导入、期间关期；引用PH-275/283/284/280/288/269/274与ADR 0016）、未清项处理（必须经既有核销/退款/退回/关闭命令清零，不直接改OpenItem；结项不代写）、结项后写拒绝与更正（owner服务按项目状态拒绝；只走负值冲销/撤核/撤转/撤分类或明确重新开项）、只读结项核对投影（缺失null+missing、不建第二owner、history_read）、权限/幂等/审计与并发。同步更新tasks.json的PH-322 blocker指向ADR 0016/0017；不写代码/迁移/schema/canonical topic/command。编号核验：ADR叶0014、0015由PH-293发票ADR占用、0016/0017为PH-299/PH-300冻结编号。实际执行（最终HEAD）：projectctl check ok=true/task_count=245；23项治理OK；verify-change --base 15cf20c --task PH-300在review pending时仅high_risk_review_required（预期）。未验证：docs-only无运行时/浏览器/PG/迁移；独立S复核pending；结项实现由PH-322承接。

## PH-308 多币种/汇率（WP-FX）契约冻结首片（2026-10-31）

BASE 15cf20c，docs-only：沿用 Lead 裁决 C，将契约冻结正文写入 `project/specs/PH-308.md`（BASE 白名单不含 docs/adr；实际 ADR 叶 0014，若新增应为 0015）。冻结内容：org 级记账本位币 CNY 与 ISO 4217 功能币种；汇率快照只追加/版本化/业务生效日/组织+币种对+日期+版本唯一/Decimal 精度/source/不可改历史/无最近汇率回退/不自动求倒数/缺汇率显示 missing；换算边界显式一次并记录 fx_rate_ref+原币种/原币金额，ROUND_HALF_UP 到整数分一次，聚合已舍入分，禁止先汇总后换算与客户端权威换算值；跨 owner 边界 contracts/settlement/management_revenue/cash/costing-time 各 owner 口径；只读展示原币+记账币+汇率 ref+missing/issue，缺失不显示 0/不猜汇率；权限 owner/finance 维护、auditor 只读、pm 项目摘要、member 拒绝、停用 history_read；本片无 canonical/model/migration/route/代码；历史重算必须显式迁移回填。反例 12 条与未实现清单已列。无代码/迁移/UI 变更，不适用 PG/浏览器/生产验证。实际执行：`projectctl check` ok=true/task_count=245；`python -m unittest tests.test_projectctl tests.test_governance -v` 23 tests OK；verify-change 预期仅 review pending。

### PH-308 复核 round-1 阻断修复（canonical 币种扩展）

复核阻断：契约要求功能币种+原币金额，但 canonical 实体仅 CNY-only `*CnyCents`，本片又不改 schema/无扩展任务。修复 option A：`project/specs/PH-308.md` 新增 §9A「所需 canonical 扩展」，登记 proposed **PH-324**（contracts/settlement/management_revenue/cash/invoicing 金额事实增加 currency+原币整数分或 Money 类型；加法迁移+CNY 回填+双读兼容+非 CNY 写入拒绝+显式迁移重算+PG 覆盖；发布顺序 PH-324→PH-321）；§1/§2/§5 明确 PH-324 完成前仅 CNY、非 CNY 写入不得开始；PH-321 depends_on 增加 PH-324。重跑 check ok=true/task_count=246、23 治理、verify-change 仅 review pending。PH-324 spec 暂复用 PH-308 §9A（白名单不含新 spec 文件，后续规划槽拆独立 spec）。

### PH-308 复核 round-2 阻断修复

阻断1：PH-321 blockers 文本未含新增 PH-324（与 depends_on 不一致）→ PH-321 blockers 补 PH-324。阻断2：PH-324 allowed_paths 缺 canonical 契约 schema → 增加 `docs/v1.0-development/contracts/*` 与 `docs/verification.md`，并在 spec §9A.3 明确需更新 entities.schema.json/relationships.json/source-registry.json/contract-bundle.schema.json/commands.schema.json/validate_contracts.py。重跑 check ok=true/task_count=246、23 治理 OK。

## PH-306 预算/ETC（WP-BUDGET/ETC）契约冻结首片（2026-10-31）

BASE 15cf20c，docs-only：契约冻结正文写入 `project/specs/PH-306.md`（白名单不含 docs/adr；Lead 裁决同 PH-308）。冻结内容：预算基线项目/期间/版本、draft→approved→superseded、批准后不可改、唯一有效版本、科目 labor/expense/procurement/other、整数分、四眼审批、request_token+expected_version+公司锁、as_of 缺失 null；ETC 默认 `budget_residual_v1`（ETC=max(0,预算−已发生成本)、EAC=已发生+ETC、variance/over_budget/gap/remaining），预算或成本缺失/不可读时 null+missing 不伪造 0；已发生成本仅 costing 已过账 book=cost/origin=current（含负值冲销），排除现金付款/结算收入/未批准手续费/预算/未过账；缺口/未分配/超支语义、科目不可归属显示 missing 不摊派；跨 owner 边界（costing/time/contracts/settlement/revenue/cash/budget）；只读展示权限 owner/finance/auditor/pm/member/停用 history_read；本片无 canonical/model/migration/route/代码；历史重算必须显式迁移回填；12 条反例与未实现清单。无代码/迁移/UI 变更，不适用 PG/浏览器/生产验证。实际执行：`projectctl check` ok=true/task_count=245；`python -m unittest tests.test_projectctl tests.test_governance -v` 23 tests OK；verify-change 预期仅 review pending。

### PH-306 复核 round-1 阻断修复

复核阻断：预算版本状态机未把 `rejected` 纳入枚举/合法迁移，却定义了 rejected 终止草稿。修复：冻结枚举 `draft/approved/superseded/rejected`；合法迁移 `draft→approved`、`draft→rejected`（终态、必填 reason、保留审计、不生效不参与 as_of）、`approved→superseded`（仅新版本批准触发）；`superseded/rejected` 终态不可回到 `draft/approved`。重跑 check ok=true/task_count=245、23 治理 OK。

## PH-305 共享费用分摊契约冻结（docs-only）（2026-10-31）

基线 847f66b（PH-299 合入后）。按 Lead 更正口径：白名单不含 `docs/adr/`，契约冻结正文写入 `project/specs/PH-305.md`（不新增 ADR 文件）。冻结内容：共享 pool 仅 `scope.kind∈{nonproject,unallocated}` 的已过账成本（当前=PH-217 非项目/未分配人工）；**明确排除 PH-250 一次性手续费**（post 为 scope=project 的直接项目成本，入池重复计成本违反 T09）；pool_key=sha256(org|period|pool_kind|有序 source_ref digest)、source 互斥、source 变化只能冲销+开放期新批次；append-only 分摊批次 kind∈{allocation,reversal}，正向/冲销 draft→posted，冲销使原批次 posted→reversed（仅状态位），条件唯一=同 org+period+pool_key 至多一条有效正向（kind=allocation AND status=posted AND reversed_by_ref IS NULL），冲销批次终态；driver 枚举；整数 basis points 合计 10000，正向批次 ROUND_HALF_UP+末项吸收（各项≥0、sum==amount），冲销批次精确负镜像原明细（各项≤0、sum==−amount、不重算）；不重复计公司总成本（costing/账本 owner 追加冲减+重归属，T09）；只读摊回核对与 issue 清单（含 source_in_multiple_pools/invalid_reversal_mirror）；幂等/公司锁/期间锁期/dry_run；不可无迁移重算历史（冲销+新批次，algorithm_version）；权限矩阵与 history_read；owner 边界；与 PH-313 写侧关系（不授权实现、不新增 canonical topic/command）。未改 contract schema、无代码/迁移/运行时验证（docs-only）。独立 S 复核首轮 4 阻断（PH-250 入池重复计成本/负值冲销与唯一有效批次冲突/负批次非负规则/pool_key 歧义）已全部修复，二轮 PASS。最终门禁：projectctl check ok=true/task_count=245；23 项治理 OK；verify-change --base 847f66b --task PH-305 ok=true、6 文件全白名单、无 errors。未验证：PH-313 写侧实现未做；无运行时/浏览器/PG 验证（docs-only）。

## PH-316 运营核对工作台导出联动首片（2026-10-31）

基线15cf20c。扩展pmo_plugins/delivery/ops_workbench_services.py：每个工作台分区增加导出描述符（dataset/as_of/period/export_missing）；ops_overview/settlement_coverage/cash_bridge/fee_pairing链接到PH-304既有ops-export/具名导出服务与路由，使用与工作台相同as_of/期间，行数/合计与分区摘要一致；identity_access因PH-304导出数据集不含身份敏感明细而export=null+export_dataset_not_implemented；pm cash_bridge export=null+permission。模板为可导出分区渲染「导出底稿」链接，导出页回链工作台；复用既有canonical ops-workbench/与ops-export/ history_read路由，不新增元数据副本/写通道/迁移/表/审计；工作台与导出同一as_of/期间口径；零写、事实计数不变；反例append-only兼容。实际执行（最终HEAD）：projectctl check ok=true/task_count=245；manage.py check exit0（93条既有W003、0错误）；makemigrations无变更；全量1125项=1048 pass+69 skip+8项既有macOS失败（BASE 15cf20c 1115+10新测试，无新增）；verify_integrity全新迁移库ok=true；scoped 10项全通过；verify-change --base 15cf20c --task PH-316在review pending时仅high_risk_review_required（预期）。未验证：浏览器1440/390、独立S复核、PG/并发、生产/UAT、旧库升级；新导出格式（PH-310）/身份敏感导出/ZIP外发/定时邮件/持久化收据未实现。


## PH-323 批次 B 写侧首片晋级：依赖再基线（2026-10-31）

基线 1ce48fc（批次 A 全部合入；注册后 count 250）。纯规划/契约文字片：
1. **依赖再基线**：对 PH-312/313/314/320/321/322/324 逐条对照 done 证据；晋级 4 项：PH-324（deps PH-308 done）、PH-252（→PH-214/224/232/287/293）、PH-312（→PH-299/300+PH-291/283/288/280/281/284/287/309/304）、PH-313（→PH-305/306+PH-217/244/250+PH-214/287）。
2. **保持 proposed**：PH-321（等 PH-324）、PH-320（等 PH-252）、PH-322（等 PH-312）、PH-314（等 PH-301/310/285/179/183）；无虚假晋级，blockers 精确。
3. **顺序**：PH-324 → PH-321 → PH-252 → PH-320 → PH-312 → PH-322 → PH-313 → PH-314（ROADMAP 已记录）。
4. **登记**：PH-325 发票-结算/收入对账+税额资格（proposed）、PH-326 汇率历史（proposed）、PH-327 关期/结项验收 runbook（proposed）、PH-328 下一规划槽（proposed，待 PH-323 done 后置 ready）。count 246→250，编号 325-328 连续。
5. 无占位符；acceptance 全可执行。独立 S 复核 PASS（0 阻断：依赖重映射/晋级证据/blockers/编号事实核对通过）；最终证据 HEAD 7de3412 按槽惯例置 PH-323 done / PH-328 ready 并清 blocker。最终门禁：projectctl check ok=true/task_count=250；23 项治理 OK；verify-change --base 1ce48fc --task PH-323 ok=true、18 文件全白名单、无 errors。未验证：PH-324/252/312/313 未实现；PH-325/326/327 未实现。
