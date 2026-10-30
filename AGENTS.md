# Project Hub 开发约束

## 开始、续接与完成任务

本仓库是长期项目，任何coding agent都从仓库事实恢复，不依赖之前的聊天。开场先运行 `python scripts/projectctl.py orient` 一次获取manifest摘要、活动handoff正文、ready任务与验收命令，再执行git status与最近提交检查，保留用户的脏修改。

用 `python scripts/projectctl.py brief PH-001` 获取目标任务的最小上下文；`draft-report PH-001` 起草交付报告骨架。只执行ready任务；proposed/blocked不是开工许可。禁止把project/tasks.json、docs/delivery-status.md（只读现状头）或docs/v1.0-development/work-packages.json整文件读入上下文：任务契约用brief，工作包用work-package-index.md或jq按包ID查询。用户提出新问题时先定位，再补入规划任务；普通技术决策由整合者处理，不让用户反复确认。业务口径冲突才交给用户判断。

分支用 `feat/PH-001-short-name` / `fix/PH-001-short-name`。除首次建库，全部经PR合入main；禁止强推main、删除用户修改或重写已发布标签。任务范围取BASE中的tasks契约；需要扩大范围先做规划PR，再实现，不能在同一PR自我授权。

提交 `project/reports/PH-001.json` 与 `project/handoffs/PH-001.md`，同步任务状态；报告包含实际命令与结果、独立审阅、偏差和未验证项。CI从可信BASE读取治理门禁。报告里的pass不是机器证明，实际CI和证据仍要核对。详情见CONTRIBUTING.md和docs/engineering/。

模型与工具不设提供方硬依赖；按可验证任务使用现有低成本渠道。不得自动购买额度、转用未知计费API、向外部模型提供凭据或真实客户/工资。鉴权失败不盲重试；短暂超时合理重试一次，输出两次未通过则接管。派遣边界、取消、交接和恢复规则见docs/engineering/agent-protocol.md与recovery.md。


当前为0.1工程验证版。先读 README.md、docs/delivery-status.md 和任务对应契约；需求文档描述目标，不能把目标直接写成已实现。技术决策由当前任务的主代理负责，业务口径不明确时给出具体待判断项。

## 实现边界

- Python 3.12+、Django、DRF、PostgreSQL生产/SQLite开发；使用已固定依赖。不为新表单引入前端构建链或消息中间件。
- 项目是全部业务的共同中心。未来业务插件遵循 docs/plugin-evolution.md；先完成 WO-14 的 SDK，新增合同/结算等对象再进入独立插件 App，避免继续扩张 core。
- 不原地修改已应用迁移。由集成者按当前迁移叶节点生成下一号；并行执行者不预占序号、不同时写共享接线文件。
- 一张任务交付一个薄片，明确文件白名单、schema、状态机、反例、验收命令与未验证事项。写范围互斥；失败不得靠扩大权限、删测试或虚构输出解决。

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

```sh
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py test tests --verbosity 1
.venv/bin/python manage.py verify_integrity --json
```

修改 MCP 时另跑 integrations/mcp 的独立测试与 stdio 握手。安全或金额变更需权限/幂等/锁期反例与独立复核；真实并发用 PostgreSQL 多连接。

使用演练数据，不能把真实客户、工资、凭据放入外部代理上下文或源码包。runtime、.env、虚拟环境、附件与备份不进入发布包。用户的 Codex/WorkBuddy 设置不属于产品初始化范围。

交付更新 docs/delivery-status.md 与验证记录；分别说明代码存在、自动化通过、浏览器检查、生产验证。检查范围之外的事项明确保留，禁止以配置存在代替运行证据。

## 前端必读入口（PH-023）

所有产品前端修改前，必须先读 `docs/engineering/frontend-standards.md` 与 `docs/engineering/visual-system.md`，使用 `docs/engineering/ui-tokens.css` 的语义令牌并复用 `docs/engineering/ui-specimen.html` 的共享模式；新增模式先更新规范与样张。审查必须覆盖 1440px、390px、键盘与焦点、溢出及空态/错误/加载/不可用状态，不接受只换色而不统一布局。
