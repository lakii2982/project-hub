# Project Hub 开发约束

## 交付流程（唯一入口）

按 [轻交付协议](docs/engineering/light-delivery.md) 工作：**一条需求 → 有序成果队列 → 一条成果 = 一个票据 = 一个 PR**。
只读这六个入口，不要再造规划/契约/收口 PR 或 per-task 报告：

- [docs/product-scope.md](docs/product-scope.md)：1.0 产品范围的唯一定义（小微企业简化版：项目收益 + 现金流；保留/简化/移出清单与发布标准）。
- [project/QUEUE.md](project/QUEUE.md)：1.0 剩余范围的唯一有序清单（成果、验收检查、演示方式、历史 PH/V1 映射）。
- [project/STATUS.md](project/STATUS.md)：现在能做什么、还缺什么（一页）。
- [project/ACCEPTANCE.md](project/ACCEPTANCE.md)：每两周候选的验收清单与模板（一页）。
- [project/DECISIONS.md](project/DECISIONS.md)：已定决定与待用户判断事项。
- [docs/engineering/light-delivery.md](docs/engineering/light-delivery.md)：票据模板、PR 规则、复核规则、合并条件、失败与停止规则、不可让步底线。

硬性规则：

- 一个 PR ≤1 天工作量、生产代码 ≤约 300 行（测试与迁移随 PR）；跨模块契约写成 ≤1 页设计说明放在同一个 PR。
- 每个 PR 自带证据：代码 + 测试 + 必需 CI + 一条真实演示（UI 必须是浏览器演示，命令类要有命令与退出码）。
- 每个 PR 恰好一次独立复核（正确性、金额/权限不变式、测试有效性），复核作用于当前 HEAD，作者不得自审。
- **合并没有人工步骤**：9 项必需检查与独立复核通过后，由维护代理开启
  `gh pr merge <n> --auto --squash --match-head-commit <sha>`；`--match-head-commit` 保证被复核的提交就是被合并的提交。执行代理不合并、不开启 auto-merge、不改分支保护。
- 每两周一个候选：一次真实环境走查 + ACCEPTANCE 清单；未执行项写 `not_run`/`blocked`，缺环境写清前提。
- 不新增 `project/tasks.json` 任务（331 条旧任务是历史台账，编号不复用）；`Task: PH-nnn` 只在过渡期兼容保留。
- 连续 3 个 PR 没有带来可演示能力或真实验收证据时停止扩展，先解除一个具体阻塞。

## 开始与接续

本仓库是长期项目，任何 coding agent 都从仓库事实恢复，不依赖之前的聊天。开场先运行一次
`python scripts/projectctl.py orient`（manifest 摘要、当前 STATUS、ready 任务、验收命令、队列摘要），
再看 `git status` 与最近提交，保留用户的脏修改。`python scripts/projectctl.py queue` 查看队列校验与下一项。

队列顺序即开工顺序；同一波次最多 2 条在制。分支用 `feat/Q-nn-short` / `fix/Q-nn-short`；
除首次建库，全部经 PR 合入 main；禁止强推 main、删除用户修改或重写已发布标签。技术决策由实现者在 PR 内做；
产品口径、范围、费用或对外承诺有冲突才交给用户判断。历史规划/验收材料在 `docs/archive/v1.0-planning/`
只读保留，不是开工依据；与当前规范、Git 事实或 CI 冲突时不得沿用旧流程（规划槽、固定检查数量、旧状态）。

模型与工具不设提供方硬依赖；按可验证任务使用现有低成本渠道。不得自动购买额度、转用未知计费 API、
向外部模型提供凭据或真实客户/工资。鉴权失败不盲重试；短暂超时合理重试一次，输出两次未通过则接管。

## 实现边界

- Python 3.12+、Django、DRF、PostgreSQL生产/SQLite开发；使用已固定依赖。不为新表单引入前端构建链或消息中间件。
- 项目是全部业务的共同中心。未来业务插件遵循 docs/plugin-evolution.md；新增合同/结算等对象走独立插件 App，避免继续扩张 core。
- 不原地修改已应用迁移。并行执行者不预占迁移序号、不同时写共享接线文件；写范围互斥。
- 一条队列成果交付一个薄片；失败不得靠扩大权限、删测试或虚构输出解决。

## 财务与数据不变式

- 金额持久化整数分，工时整数分钟，计算用 Decimal，舍入用 ROUND_HALF_UP；禁止 float。当前单笔上限 10^12 分（100亿元）。
- 费率按业务日期快照；当前逐行取整与未来月度取整组是不同口径，不能无迁移地重算历史。
- 费用确认成本，付款确认现金；同一费用付款不能再记一次成本。现金计划不等同于应收应付；净现金流不等同于利润或银行余额。
- 服务层负责租户/项目/角色/期间/状态/来源检查、幂等、事务和审计。浏览器、导入与个人 API 共用业务服务。
- 账本与审计只追加；纠错追加负值冲销，保留原始来源。不得关闭触发器、直接编辑余额或通过插件改历史账。
- PostgreSQL 公司锁用于串行化业务写。SQLite 不支持 select_for_update，不能用 SQLite 测试宣称生产并发正确。
- 过期未执行计划单列跟进，不自动移动到预测当周。锁账、未来实际日期、自审与超额核销的拒绝路径不可省略。

## 双层 AI 与插件

- 模型只提取候选、解释和建议，确定性规则负责核算。任何模型文本都不是审批或记账授权。
- 个人令牌绑定本人、scope、项目和期限；默认字段最少。不得新增共享管理员密钥、审批/支付/锁账工具或暴露个人费率。
- 写工具默认 dry_run。工具说明要求先确认业务事实，但不能把客户端对话约定说成服务端已强制的人类确认。
- 当前 manifest 恰好8字段，见 docs/plugin-architecture.md；同进程插件是可信代码，不是沙箱。
- 停用保留历史与队列，依赖必须检查；服务/API/导入/后台统一门控。新业务走插件接口，不能只隐藏按钮。

## 验收与交付

产品变更按队列条目的 3–5 条验收检查执行；纯文档/工具变更按实际作用域验证，必需 CI 不变。
开发中跑作用域检查，收尾完整验收一次；仅新修改、失败或未解决风险才重跑相关检查。

```sh
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test tests --verbosity 1
.venv/bin/python manage.py verify_integrity --json
```

另外常跑：`python scripts/projectctl.py check`、`python scripts/projectctl.py queue`、
`python -m unittest tests.test_projectctl tests.test_governance tests.test_delivery_policy -v`。
修改 MCP 时另跑 integrations/mcp 的独立测试与 stdio 握手。安全或金额变更需权限/幂等/锁期反例与独立复核；
真实并发用 PostgreSQL 多连接。

使用演练数据，不能把真实客户、工资、凭据放入外部代理上下文或源码包。runtime、.env、虚拟环境、
附件与备份不进入发布包；不得在 PR/代码/日志中出现真实客户数据或密钥。

交付只说明新增能做什么、入口与操作、实际证据、剩余阻塞；分别说明代码存在、自动化通过、浏览器检查、
真实环境验证。不用 PR/任务数当产品完成率；检查范围之外的事项明确保留，禁止以配置存在代替运行证据。

## 前端必读入口（规范 v2.0，Q-46）

所有产品前端修改前，必须先读 `docs/engineering/frontend-standards.md` 与 `docs/engineering/visual-system.md`（A 方向「蓝白极简 SaaS」+ 左侧模块/右侧页签结构），使用 `docs/engineering/ui-tokens.css` 的语义令牌（产品页面经 `static/app.css` → `static/ui/ui-tokens.css` 加载，两份令牌必须同源同步）并复用 `docs/engineering/ui-specimen.html` 的共享模式；新增模式先更新规范与样张。用户提供的蓝白极简设计包仅作风格参考，见 `docs/engineering/design-reference/saas-blue-minimal-kit-v2/README.md`（占位品牌与示例数据不是产品事实）。审查必须覆盖 1440px、390px、320px、键盘与焦点、溢出及空态/错误/加载/不可用状态，不接受只换色而不统一布局。规范 v2.0 是改造基线，不表示产品页面已完成迁移；全站布局重构由 PH-026 在规范评审通过后单独重排。
