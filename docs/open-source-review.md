# 开源选型与复用边界

日期：2026-10-22。当前选择为 Django 上的项目管理账应用；这是工程选择，未来可依据试点结果调整。成熟框架已经承担认证、ORM、表单、迁移、API、登录限流、静态资源、插件钩子与 MCP 协议，业务核算规则仍由本项目负责。

## 1. 产品参考

| 项目 | 官方来源 | 已核对的价值 | 当前采用方式 |
| --- | --- | --- | --- |
| ERPNext | [项目文档](https://docs.frappe.io/erpnext/user/manual/en/projects) · [源码](https://github.com/frappe/erpnext) | 项目、工时、费用、成本归集与结算领域模型 | 需求与行为对照；未复制业务源码 |
| Frappe Framework | [部署文档](https://docs.frappe.io/framework/user/en/production-setup) | DocType、模块化与自托管运维 | 选型对照；未采用运行时 |
| OpenProject | [成本管理](https://www.openproject.org/docs/user-guide/time-and-costs/) · [源码](https://github.com/opf/openproject) | 工作包、工时、预算与成本视图 | 交付过程与视图对照；未复制源码 |
| Odoo | [18.0 文档](https://www.odoo.com/documentation/18.0/) · [许可说明](https://www.odoo.com/documentation/18.0/legal/licenses.html) | 业务应用分层和模块依赖 | 扩展机制对照；未采用模块 |
| Plane | [源码](https://github.com/makeplane/plane) | 轻量项目交互与任务管理 | 产品体验参考；未复制源码 |
| DeepSeek Harness（用户所指 dsh） | [官方仓库](https://github.com/deepseek-ai/deepseek-harness) | 稳定执行框架、明确工具边界与可扩展能力的思路 | 设计参考；没有执行或嵌入其代码 |
| Jira Plugin Framework | [官方框架说明](https://developer.atlassian.com/server/framework/atlassian-sdk/plugin-framework/) | 插件描述、依赖、生命周期及扩展点 | 设计参考；未采用 Jira 运行时 |

参考关系不能证明本系统已具备对方产品的能力；实际功能与测试逐项见 delivery-status.md。

## 2. 许可与商业交付

GPL、AGPL 都允许商业使用和自托管，并不与本产品定位天然冲突。分发受 GPL 覆盖的作品，需要遵守对应源码等要求；AGPL 对修改后通过网络交互使用的版本另有对应源码要求。独立组件、组合方式与衍生作品的边界需要按具体集成方式判断，不能把同仓库的全部代码一概视为同一许可。

ERPNext 与 OpenProject 公开仓库采用 GPL-3.0；Plane 的公开核心采用 AGPL-3.0，具体版本和 edition 需单独核对。Odoo 社区框架为 LGPL-3.0，企业模块和部分应用有各自许可；Frappe Framework 为 MIT。用户尚未指定本产品要闭源，因此不以排斥 GPL/AGPL 作为选择自研的理由。正式分发前应冻结产品许可、组件版本、NOTICE 与来源清单。

本项目未复制以上产品的业务源码。参考文档与通过 API 独立集成，不等同于复制业务代码。

## 3. 已实际复用的组件

| 组件 | 承担的工作 | 许可概况 |
| --- | --- | --- |
| Django | 账户、ORM、迁移、表单、模板、CSRF | BSD |
| Django REST Framework | API 认证和结构化输入输出 | BSD |
| django-axes | 登录失败限制 | MIT |
| WhiteNoise、Gunicorn | 静态资源与生产 WSGI 服务 | MIT |
| psycopg | PostgreSQL 驱动 | LGPL；按驱动自身要求使用 |
| pluggy | 插件钩子注册和调用 | MIT |
| 官方 Python MCP SDK | stdio 握手、工具 schema 与协议 | MIT |
| httpx、pydantic | MCP 适配器 HTTP 与类型校验 | BSD / MIT |

主应用固定依赖见 requirements.txt；个人 MCP 环境见 integrations/mcp/requirements.txt。上表不是完整的传递依赖许可证清单；商业发布门槛还包括对锁文件生成依赖清单、核对许可证文本和安全公告。

## 4. 当前选择与代价

ERPNext 可以从零承接没有财务软件的小公司；OpenProject 和 Odoo 也能为小团队提供成熟能力。不能假定采用它们就必须已有 ERP/OA，或认为它们无法适配百人以下企业。

选择当前实现的主要理由是：把项目管理账、双层 AI 的有限权限接口和插件启停契约放在同一可控代码范围内；浏览器流程和 Agent 调同一服务，输入负担按软件开发、实施、技术服务这三类场景设计。部署目标为 PostgreSQL、应用、事件 worker、Caddy 四个 Compose 服务，不需要额外消息中间件。

代价同样明确：合同、收入、应收应付、发票和分摊的成熟业务规则仍需实现并验证。工程验证版尚未达到完整产品范围；不能因为代码规模小就宣称维护成本更低或回归风险更小。下一阶段应以真实试点的录入负担、财务反例和维护工时检验此选择。

## 5. 改选条件

1. 若试点的主要需求是标准 ERP 结算和发票，优先做 ERPNext/Frappe 或 Odoo 社区模块的实际原型比较，再决定继续自研还是作为项目交互层集成。
2. 若核心需求变成复杂进度与资源排程，优先核对 OpenProject 的现有能力与扩展代价。
3. 若没有能力持续维护财务规则、并发和迁移，缩小自研范围，复用成熟业务内核；不能用增加提示词来代替这部分工程责任。
4. 无论选择哪条路线，都保留项目标识、来源键、API 契约与可导出数据，避免把客户数据锁在当前实现中。
