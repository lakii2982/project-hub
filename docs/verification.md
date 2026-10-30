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

实际执行（最终HEAD）：projectctl check ok=true/task_count=169；manage.py check exit0；makemigrations --check --dry-run无变更；全量772 tests 111.552s，5 failures/3 errors均为基线已知macOS环境限制（6 DRF Accept/codec/large-JSON、2 delivery symlink），55环境skips，无新增失败；verify_integrity --json在全新迁移SQLite上ok=true；scoped 13 tests OK。13新增测试含服务层（事件envelope/幂等/dry-run/改稿版本/唯一性/期间/权限/停用/history read/DB触发器/verify反例）与HTTP（角色/CSRF/非法字段/批准越权/草稿流水/改稿）。未验证：无PG并发（本片无并发锁外场景，CI覆盖）、无浏览器1440/390实测（模板按平台规范但未跑截图/键盘）、无真实附件/客户/生产；canonical command通道未接通（私有服务/浏览器适配，报告披露）。

独立S复核（fresh context, HEAD efdfaa7）实际重跑6条验收命令、与当前main基线（ceded2e）逐项核对失败名称一致，PASS 0阻断；非阻断残余为verify_integrity仅验证latest saved事件envelope、未断言每次保存恰一条事件计数，留PH-247/后续加固。

## PH-243 分类桥接 canonical 契约修正（2026-10-30）

基线e91346a（PH-242合入）。纯契约/测试/规划片：events.schema.json 两个classification事件posting_event_refs改1/1且items绑定core/accounting_event（reversed namespace cash→core），5个其它0/0 cash事件未动；entities SourceConsumption invariants[5]逐字替换冻结句；catalog-completeness仅补注解；extension-freeze仅更新3个受影响canonical摘要+显式supersede cash-extension.plan.json#/service_semantics/classification_reverse/4（保base_sha/201/input_sha256/历史）；fixture仅3个contract_artifact_sha256；tests/test_v1_preparation.py新增严格正反例（reserved原19/37、99/8计数）。实际执行：check在独立S复核落pass后按最终HEAD记录；治理测试23项OK；validate_contracts/check_contract_cases/check_fixture严格.agent-venv全pass；PH135_REQUIRE_SCHEMAS=1 prep测试8项OK 0 skip（新增positive=3/rejected=9）。PH-239 allowed_paths扩core/models.py+core/migrations/*.py+docs/v1.0-development/verification.md并晋级ready。未实现PH-239业务；core 0009迁移、verify_integrity改造、PG/浏览器/生产均未验证。

补充（最终证据 HEAD）：独立 S 复核 PASS，0 阻断；check ok=true/task_count=169；verify-change --base e91346a --task PH-243 ok=true、13 文件全在白名单；严格 .agent-venv + PH135_REQUIRE_SCHEMAS=1 的 prep 测试 8 项 OK 0 skip（PH243 positive=3/rejected=9；PH210 6/10；fixture 99/8 计）。residual（非阻断）：PH-243 spec 正文“PH-247 晋级 PH-239”与实际（本次已置 PH-239 ready）不一致，由 PH-247 规划槽消解，handoff 已记。


## PH-246 PH-237 量化证据修正（2026-10-30）

基线ceded2e（PH-243合入）。纯证据修正片，仅三处：project/reports/PH-237.json 的量化测量条目、docs/delivery-status-archive.md 的 PH-237 条目数字/公式、docs/verification.md 的 PH-237 校准句；三处统一为固定 SHA 复现口径：基线 7605fa9 = manifest 663 + tasks.json 217565 + PH-233 handoff 1510 + delivery-status 61218 = 280956 字节；最终路径 b931be0 = orient 3423 + brief PH-232 1893 + delivery-status 1996 + PH-232 spec 8448 = 15760 字节；降幅 1−15760/280956 = 94.39%≈94.4%。实际复算：上述四分量与四分量逐条 wc -c 实跑一致（280956/15760），`round((1-15760/280956)*100,2)` = 94.39；grep 确认三文件无 94.5%/94.6%/281349/15360 旧值且均含 94.4%。未改 PH-237 三处正文（ROADMAP/execution-protocol/CONTRIBUTING）、未改写 archive 其它历史条目、未改 PH-237 review 块与历史 pass 语义。未验证：全量产品套件本地未跑（本片 docs-only，CI 覆盖）。独立 S 复核 PASS，0 阻断；NOTE（grep 证据中 verification.md 的 94.4% 出现次数应为 3 次而非 2 次）已在最终证据 commit 修正。

## PH-240 退款与银行退回（2026-10-30）

基线e91346a（PH-242规划合入、含PH-232核销）。新增cash Refund/BankReturn append-only事实与0004_refund_guards迁移（同org插入校验、UPDATE/DELETE拒绝；产生的posted现金沿用PH-230不可变守卫）；refund_services实现原现金覆盖内真实退款（direction=in原现金→新posted out现金，服务器生成来源编号、证据引用原现金、Refund事实、generic audit、恰一条cash.refunded.v1、action=refund收据）与银行退回（方向取反、BankReturn、cash.returned.v1、action=bank_return收据）；可退额=posted gross−active核销−active退款−active退回，服务端派生、不为负。为使退款×核销联合覆盖对称（Lead裁定A），settlement新增只读clearance_coverage_for_cash，create_clearance.remaining_cash与cash_candidates_for_open_item.available_cents改为扣除退款/退回（核销自身语义/事件/金额口径不变；PH-232 spec文字未同步，由PH-247登记口径同步小修）。只读cash_refund_coverage/list/snapshot/provenance；UI退款/退回列表、表单、现金详情区；verify_integrity新增退款/退回引用同org、方向/日期/金额、两类canonical事件计数与envelope、覆盖非负、无重复external_source_key、无cash会计事件/账本。明确不写账本/收入/成本/应收应付/未清/核销（只读），不做Offset/手续费/转账/更正/导入/期初/预测。
实际执行（最终 rebase 到 0721ad4 后）：scoped退款三模块20项（17通过+3项PG并发本机skip）；全量793项（726通过、59环境skip、8项本机环境限制失败=6项DRF+2项macOS symlink，与基线一致、CI Linux为权威）；manage.py check exit0（53条既有W003警告、0错误）、makemigrations无变更、verify_integrity在全新迁移SQLite上ok=true；projectctl check ok=true/task_count=169；verify-change --base 0721ad4 ok=true、22个变更文件全在白名单。独立S复核第1轮打回1阻断（verify_integrity新增的cash-namespace全局账本禁令会误杀PH-243/PH-239合法现金分类桥接），修复为仅退款/退回provenance守卫（source kind∈{refund,bank_return}或source直接指向退款/退回产生现金），显式允许source kind=cash_classification的桥接，并加正向边界回归（含“对退款产生现金再分类桥接”场景）与负向退款账本反例；第2轮PASS 0阻断。未验证：PG并发、浏览器1440/390、生产/UAT、旧库升级；新现金账本桥接属PH-239。

## PH-247 后续交付独立规划入口（接续 PH-242）（2026-10-30）

基线0721ad4（PH-244/PH-246合入后）。纯规划片：消解PH-243 spec中「PH-247晋级PH-239」措辞（PH-239已在PH-243交付时ready）；登记PH-249（PH-232覆盖公式口径同步，proposed——缺PH-240 done）、PH-250（手续费提交/批准与成本过账首片，ready——依赖PH-210/PH-244 done）、PH-251（手续费净额结清/既有成本/承担方后续片，proposed）、PH-252（发票登记与逐行分配首片，proposed——缺PH-150/PH-155 done）、PH-253（PH-244 verify_integrity saved-event计数加固，ready——依赖PH-244 done）；登记PH-248下一规划槽（ready，依赖PH-247 done）。PH-241/PH-245仍proposed（依赖PH-239 ready未done）。各spec冻结字段/状态机/权限/幂等/金额/迁移/白名单/反例/验收命令/risk S。实际执行：check、23项治理测试；verify-change在独立S复核落pass后按最终HEAD重跑记录。未验证：PH-249/250/251/252/253均未实现，无产品代码/迁移/浏览器/PG/CI证据；review由独立S复核。

补充（最终证据 HEAD 8346d55）：独立 S 复核两轮——首轮 2 阻断（PH-251 spec/tasks 白名单不一致+残留文本；PH-253 与 PH-250 approved/cost 事实冲突）+4 NOTE，修复后二轮 PASS；最终 check ok=true/task_count=175，verify-change --base 0721ad4 --task PH-247 ok=true、13 个变更文件全在白名单。**PH-249 依赖现状**：PH-240 已在本任务 BASE 之后以 a760cf7（PR #139）合入 main，故“缺 PH-240 done”在最新 main 已解除；但本片按冻结 BASE 0721ad4 保持 PH-249 proposed，晋级 ready 属 PH-248 规划槽动作，本片不改状态、不自我授权 rebase。

## PH-253 verify_integrity saved-event 计数加固（2026-10-30）

基线1f02520（PH-244/PH-247合入后）。纯加固片：`core/management/commands/verify_integrity.py` 手续费分支从“仅验证 latest saved 事件 envelope”升级为——每 BankCharge 的 `costing.bank_charge_saved.v1` 事件数必须恰等于其 version，逐事件校验 source=`costing/bank_charge` 稳定 ref、source.version 对应版本、topic/schema_version/changed_refs=[该版本 ref]/posting_event_refs=[]，缺失/多出/旧版本损坏/错版本覆盖均报 corruption；status 接受 canonical 五枚举（draft/pending/rejected/superseded 不得有成本事实；approved 无成本事实为 PH-250 未落地过渡态，存在成本事实时要求恰一个 claim（consumer=cost/coverage_key=bank_charge:<id>/金额=gross）+ 恰一条 cost AccountingEvent（event_type=costing.bank_charge.cost、effect=gross、发生日/期间=冻结值）+ 恰一条 cost LedgerPosting（book=cost、origin=current、scope=project、金额=gross），重复/缺失/金额不符/claim 不符/孤儿成本事件与孤立 claim 均报错；labor 事件循环放行 PH-250 的 costing/bank_charge 成本事件（仅由本手续费分支校验）。`tests/test_v1_bank_charge.py` 追加 11 个损坏探测/生命周期用例（空库与正常草稿/改稿、中间版本事件删除、同版本重复事件、篡改 source.version/changed_refs、approved 过渡态、approved 合法成本事实、重复成本事件、缺失 posting、金额不符、unapproved 携带成本事实、orphan 成本事件/claim）。

实际执行（最终HEAD）：projectctl check ok=true/task_count=175；manage.py check exit0（仅既有 urls.W003）；makemigrations --check --dry-run无变更；`verify_integrity --json` 全新迁移空库 ok=true/errors=[]；临时SQLite探针库：正常草稿/改稿（version=2）ok=true，删除中间版本 saved 事件后 ok=false，报 bank_charge:<id>:saved_event_count 与 saved_event_version_coverage（exit 1）；scoped 19 tests OK；治理23项OK；全量804 tests/114.456s，5F+3E 与基线已知 macOS 环境限制名称完全一致、59环境skips、无新增失败（本分支相对基线新增11测试）。未验证：PH-250 真实审批/成本过账服务未落地，approved 合法成本事实仅以合成 core 事实验证；本机无PG（触发器/事务路径由CI）；无浏览器变更。
独立S复核（fresh context, HEAD 70e898e）实跑6条验收命令并与基线核对：PASS 0阻断；4条NOTE已记report.unverified（approved过渡态须由PH-250产事实且不改version、逐charge查询、直接DB跨org伪造事件/claim低风险、draft/superseded成本事实与cost event source.version未单测），PH-250实现遵循version不随submit/decide变化。

## PH-239 现金分类与账本桥接首片（2026-10-30）

基线a760cf7（main，含PH-240/244/246；PH-243契约修正已合入）。新增cash CashClassification/CashClassificationReversal与`0005_cash_classification_guards`迁移（PH-240先合入占0004，故本片顺延0005，依赖0004_refund_guards；同org插入、仅active→reversed单字段、禁删、reversal不可变；每流水至多一条有效分类）；classification_services实现分类/撤分类（posted/同org/方向/advance有效往来方/期间/锁期/未来/证据/幂等/dry-run）与只读snapshot/list、cash_book_coverage。按PH-243冻结契约落账本桥接：core/cash_accounting.py具名接口为每条有效分类恰写一个AccountingEvent（event_type=cash.classification、source=分类ref、occurred_on=流水日期、算法常量）与恰一条LedgerPosting（book=cash_in/out、amount=+gross、scope_kind=funding_account、scope_id=账户、origin=current）；撤分类追加镜像事件（reverses_event_ref=原事件、claim_effect=-gross）与负值posting（reverses_posting_id=原posting），原事件/原posting不改不删；bank_effect SourceConsumption每流水唯一永久、撤后重分类复用且要求现存事件净额0；canonical recorded/reversed事件posting_event_refs恰1个core/accounting_event。core models保留约束名labor_posting_cost_scope并放宽为cost↔project/nonproject或cash_in/out↔funding_account/legacy_project（core 0009，origin/current与金额边界保留）。verify_integrity与PH-240合并后保留退款/退回provenance守卫、删除旧两处“cash不得有会计事件/claim”全局禁令、generic loop放行cash，并新增七项分类桥接不变式（引用同org、单有效分类、事件恰一条与envelope含恰1 posting ref、event/posting正负互反配对、per-分类与per-流水净额、claim唯一与净额一致、无cost/revenue/未清/核销行；批量聚合）。明确不做payroll/CashAllocation/Offset/退款/手续费/互转/更正/导入/期初，不解除PH-169/170/171 proposed。
复核round 1三阻断修复：①classification_list按真实月份区间筛选（原DateField startswith只匹配每月1日）；②按冻结spec恢复transfer内部枚举（model/DB含transfer，客户端命令仍拒绝，新增非路由内部服务供PH-245，本片不暴露transfer入口）；③verify_integrity现金块预取reversal/classification映射消除N+1并加CaptureQueriesContext常量查询回归，另补“伪造错误金额桥接”“同一分类第二组桥接”两个反例。实际执行（最终HEAD）：scoped分类三模块29项（26通过+3项PG并发本机skip）；全量822项（752通过、62环境skip、8项本机环境限制失败=6项DRF+2项macOS symlink，与基线一致、CI Linux为权威）；manage.py check exit0（58条既有W003警告、0错误）、makemigrations无变更、verify_integrity在全新迁移SQLite上ok=true；projectctl check ok=true/task_count=169。未验证：PG并发、浏览器1440/390、独立S复核round2 pending、生产/UAT、旧库升级。
