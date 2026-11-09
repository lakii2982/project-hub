# Project Hub 0.2.0-rc.1 · 小微企业简化版预发布候选

状态：**候选（candidate）**，未打标签、未创建 GitHub Release、未部署。  
版本来源：`pyproject.toml` = `0.2.0`；候选标识：`0.2.0-rc.1`（正式发布时应标为 GitHub pre-release）。  
日期：2026-11-04。代码基线：`main` = `e642ec2`（Q-43 合并后）；Q-44 仅新增发布元数据/版本号与验收记录。

## 1. 候选包含什么

面向 5–50 人软件/技术服务公司的小微企业简化版核心链路：
客户 → 项目 → 合同额/收款计划 → 工时与费用 → 开票 → 收款 → 项目收益 + 现金流。

| 队列 | 能力 | 证据入口 |
| --- | --- | --- |
| Q-31 | 1.0 范围重定义、队列重排、轻交付协议 | `docs/product-scope.md`、`project/QUEUE.md`、`docs/engineering/light-delivery.md` |
| Q-32 | `PMO_SCOPE=micro` 入口收口/停用页/历史只读 | `tests.test_micro_scope`（19 OK）、`docs/screenshots/q32-*` |
| Q-33 | 老板工作台（开票/收款/支出/账户余额/未收/毛利排行） | `tests.test_q33_dashboard`、`docs/screenshots/q33-*` |
| Q-34 | 项目页合同额→开票→收款→成本→毛利→现金 | `tests.test_q34_project_finance`、`docs/screenshots/q34-*` |
| Q-35 | 周工时、费率快照、更正/幂等、缺费率缺口 | `tests.test_weekly_time`、`docs/screenshots/q35-*` |
| Q-36 | 费用/外包确认计成本、分次付款只计现金、未付计划 | `tests.test_q36_expenses`、`docs/screenshots/q36-*` |
| Q-37 | 极简开票/收款/资金账户基础 | `tests.test_q37_billing`（27 OK） |
| Q-38 | 账户派生余额、流水、只读 8 周计划与缺口 | `tests.test_q38_cash`、`docs/screenshots/q38-*` |
| Q-39 | 一键演练环境 `scripts/demo.py --slice micro` | `tests.test_demo_script`（15 OK） |
| Q-40 | 首次启用向导（空库到首个项目/工时） | `tests.test_q40_onboarding`（15 OK）、`docs/screenshots/q40-*` |
| Q-41 | 四类 CSV 导出（授权范围/同口径/审计）与站内到期提醒 | `tests.test_q41_exports_reminders`、`docs/screenshots/q41-*` |
| Q-42 | L4 浏览器端到端验收（1440/390/键盘/四态/越权） | `docs/q42-browser-acceptance.md`、`docs/screenshots/q42-*`、`tests.test_q42_e2e` |
| Q-43 | Compose 安装脚本、只读 `/status/`、SQLite 备份/隔离恢复实测 | `tests.test_q43_*`、`docs/screenshots/q43-*` |

逐项结果与证据见 `project/ACCEPTANCE.md`「当前候选」；**`not_run`/`blocked` 项没有写成 pass**。
本候选不包含专业财务（合同版本/审批、应收结算核销、管理收入、预算 ETC、工资税、多币种等），
它们默认停用、历史只读保留（见 `docs/product-scope.md` §6）。

## 2. 源码包（reproducible）

```sh
python scripts/package.py
# 输出 releases/project-hub-0.2.0-source.zip 与 .sha256
python scripts/verify-source-package.py releases/project-hub-0.2.0-source.zip
# {"ok": true, "files": 1875, "sha256": "e8607daf527c8624e4c9888ef20553b22dbcf3d8f1be97b257033bf1b9253dee", "runtime_excluded": true}
```

- 归档 SHA-256：`e8607daf527c8624e4c9888ef20553b22dbcf3d8f1be97b257033bf1b9253dee`（`source.zip.sha256` 同步保存）。
- 文件数：`1875`；逐文件摘要：包内 `SHA256MANIFEST.json`（不提交到仓库；其 SHA-256 记录在 `candidate.json`）。
- 确定性：ZIP 条目固定时间戳/权限，同一源码树重复构建得到相同字节与摘要；发布元数据
  `docs/candidates/` 不进入包内，避免“描述包的文件改变包摘要”的循环。
- 包内容：`core/`、`pmo/`、`pmo_plugins/`、`templates/`、`static/`、`tests/`、`scripts/`、
  `docs/`（不含 `docs/candidates/`）、`integrations/`、`.github/`、`project/`、部署文件
  （`compose.yaml`、`Dockerfile`、`Caddyfile`、`.env.example` 等）与 `manage.py`/依赖/文档。
- 排除：`runtime/`、`.env`（仅 `.env.example` 允许）、`.venv/`、`__pycache__/`、`node_modules/`、
  `staticfiles/`、`backups/`、`releases/`、数据库/附件/备份、`.pem/.key/.p12`、`.sqlite3/.pyc`。
- 许可证：`LICENSE`（Apache-2.0）、`NOTICE`、`THIRD_PARTY_NOTICES.md`；第三方组件及许可概况随包保留，
  完整传递依赖锁定在 `requirements.txt` / `integrations/mcp/requirements.txt`。本候选**不是**
  完整二进制 SBOM；正式镜像发布前仍需核对发行物许可清单与依赖安全结果。

## 3. 验证状态（同一 HEAD）

- 9 项必需 CI 与独立复核必须作用于 Q-44 PR 的同一 HEAD；PR 检查结果与复核评论是该 HEAD 的事实来源。
- 自动化：`manage.py test tests` 全量通过（本地 SQLite；PG 由 CI PostgreSQL domain 覆盖）；
  `projectctl check/queue`、`manage.py check`、`makemigrations --check`、`verify_integrity` 通过。
- L4（本机 Chromium + SQLite）：Q-42 `docs/q42-browser-acceptance.md` 的 1440/390/键盘/四态/越权证据；
  Q-43 SQLite 备份/隔离恢复后页面与金额一致（`docs/verification.md` 2026-11-04 段）。
- L3：CI SQLite/PostgreSQL/容器/协议/治理检查（见 PR checks）。
- L5：**not_run / blocked**，见下。

## 4. 未验证 / 已知风险（不得写成 pass）

| 项 | 状态 | 说明 |
| --- | --- | --- |
| 真实 Docker/Compose 安装 | not_run | 本机无 Docker；`scripts/install.sh` 只有 fake-docker 回归（Q-43 明示） |
| 生产 PostgreSQL 备份恢复 | not_run | 无 pg_dump/pg_restore/真实 PG；SQLite 恢复不能冒充 PG 恢复；PG 恢复 execute 诚实 `not_run` |
| TLS / 真实域名 / 反向代理 | not_run | 未部署 staging；Caddy/TLS 只存在于配置与文档 |
| L5 真实环境/客户走查 | blocked | 真实域名/PG 环境未就绪（P-02）；客户 UAT/三方签批**不是**本次预发布前置（DECISIONS D-12） |
| 独立复核 | pending | 需一名非作者复核者在 Q-44 HEAD 上给出结论 |
| 1.0 发布门槛 | not met | 本版本是 0.x 预发布候选，不表示生产可用；1.0 门槛见 `docs/engineering/releases.md` §2 |

## 5. 发布动作边界

- 本次**不**创建/推送标签，**不**创建 GitHub Release，**不**部署，**不**上传镜像。
- 若后续决定发布：维护者按 `docs/engineering/releases.md` 在确认提交上打 `v0.2.0`，GitHub 标为
  pre-release，运行 release workflow（同一提交重跑检查、上传 ZIP+SHA256、下载核对）；不得移动标签或
  覆盖公开资产。
- 发现缺陷走 PATCH/hotfix；已发布内容不撤回、不改写。
