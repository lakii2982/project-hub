# 可信 PR 交付契约（PH-120）

## 版本化策略与授权

`project/delivery-policy.json`（`project-hub.delivery-policy/v1`）是交付契约的机器可读来源：

- PR 正文恰好一行 `Task: PH-###`；任务、白名单和验收仍以 BASE 的 `project/tasks.json` 与 spec 为准。
- `base_policy_source: git-show-base`：policy、离线检查脚本和任务契约从 BASE 读取，候选 HEAD 不能修改策略自授权。
- **12 项门禁** = 既有 8 项 required status checks（Project governance、SQLite domain、PostgreSQL domain、MCP protocol、Public hygiene、Container smoke、CodeQL python/javascript-typescript）+ 4 项仲裁证据：`independent_review_app_identity`、`arbitration_workflow_and_event`、`event_base_and_head_sha`、`test_merge_sha`。
- `s_authorization`：S 级任务同时绑定用户授权来源和 BASE 任务/spec 的 `contract_digest`。当前仅登记 PH-120/121/122；新范围必须由独立规划 PR 增加授权，Issue、label、候选报告不能提供授权。非 S 任务沿用既有冻结任务契约。
- `independent_review`：本地复核必须独立于实现 actor；远端仲裁另核对官方 Codex Connector App `1144995`、bot user `199175422`、当前 HEAD 的最新完整结果和未解决 findings。
- `merge`：strict、管理员保护、PR/讨论/线性历史保护、mergeable、最新 BASE/HEAD 和 test-merge SHA 均在合并前重新核对；拒绝自报 pass、伪造来源、skipped/neutral/未知或过期结果。

## 实现入口

| 入口 | 作用 |
| --- | --- |
| `scripts/check_pr.py` | 从 BASE 读取 `projectctl.py` 执行 `verify-change`；唯一 Task 行。 |
| `scripts/delivery_gate.py` | 离线读取 BASE policy，校验策略、S 授权摘要与 PR 元数据；不做网络、不执行候选代码。 |
| `.github/workflows/delivery.yml` | 产生 `Delivery arbitration` 检查；在 PR 元数据上运行 BASE 脚本，只读、无写令牌。引入该脚本前的首个 PR 仅作非强制 bootstrap。 |
| `tooling/ai_delivery/delivery/service.py` | 已有 DeliveryGate：真实验收→独立 review→PR→显式 merge，复核证据绑定 BASE/contract/diff。 |
| `tooling/ai_delivery/delivery/github.py` | 合并前读取 BASE policy，检查远端保护、8 项 checks、mergeable、test-merge SHA、讨论和（启用后）Codex/Actions 仲裁。 |

## 本地验收

```sh
python scripts/projectctl.py check
python -m unittest tests.test_projectctl tests.test_governance tests.test_delivery_policy tests.test_delivery_gate -v
python scripts/delivery_gate.py --base <sha> --event <pull_request.json> --root .
```

## bootstrap 与未验证边界

- 当前 `bootstrap.remote_checks_enforced=false`：本片实现并测试远端仲裁读取路径，但不宣称新检查已成为分支保护，也不宣称已有真实 Codex PASS。PH-122 必须在真实 PR 上核对 App、workflow/event、BASE/HEAD/test-merge SHA 后才能启用。
- 官方 App/bot 数字身份来自 GitHub 公共 API 实查；评论正文不是授权来源，只有符合身份、HEAD 和完整状态要求的复核结果才可作为仲裁证据。
- 启用远端仲裁时，HEAD 与 BASE 的 `delivery.yml` 必须逐字节一致；同名但由候选修改的 workflow 不能伪造可信检查。
- 本片不伪造人类 GitHub 批准，不关闭既有保护，不创建标签或发布生产。旧历史报告不批量重写；真实部署、生产验收另走对应任务。
