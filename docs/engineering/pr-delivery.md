# 可信 PR 交付契约（PH-120）

## 版本化策略与授权

`project/delivery-policy.json`（`project-hub.delivery-policy/v1`）是交付契约的机器可读来源：

- PR 正文恰好一行 `Task: PH-###`；任务、白名单和验收仍以 BASE 的 `project/tasks.json` 与 spec 为准。
- `base_policy_source: git-show-base`：policy、离线检查脚本和任务契约从 BASE 读取，候选 HEAD 不能修改策略自授权。
- **13 项门禁** = 既有 8 项 required status checks（Project governance、SQLite domain、PostgreSQL domain、MCP protocol、Public hygiene、Container smoke、CodeQL python/javascript-typescript）加启用后的 Delivery arbitration（共 9 项）+ 4 项仲裁证据：`independent_review_app_identity`、`arbitration_workflow_and_event`、`event_base_and_head_sha`、`test_merge_sha`。
- `s_authorization`：S 级任务同时绑定用户授权来源和 BASE 任务/spec 的 `contract_digest`。当前登记 PH-120/121/122（原工程授权）及 PH-397（用户 2026-10-31 的 1.0 完整交付规划授权）；新范围必须由独立规划 PR 增加授权，Issue、label、候选报告不能提供授权。非 S 任务沿用既有冻结任务契约。
- `independent_review`：本地复核必须独立于实现 actor；远端仲裁另核对官方 Codex Connector App `1144995`、bot user `199175422`、当前 HEAD 的最新完整结果和未解决 findings。
- `merge`：strict、管理员保护、PR/讨论/线性历史保护、mergeable、最新 BASE/HEAD 和 test-merge SHA 均在合并前重新核对；拒绝自报 pass、伪造来源、skipped/neutral/未知或过期结果。
- `quality`：覆盖率总量底线、新增/修改行门槛、最低测试数与未知 skip 白名单同样从 BASE policy 读取；覆盖率缺失/损坏、测试失败、未知 skip 或低于门槛一律阻断。覆盖率不替代财务反例。
- **PR 合并证据**：合并前重新核对当次 BASE/HEAD、9 项 checks（含 Delivery arbitration 与 CodeQL 最新重跑结果，后完成的失败重跑覆盖旧成功）、test-merge SHA、讨论与保护状态；合并后记录 merge commit。just-merge 不启用未经支持的 merge queue。

## 实现入口

| 入口 | 作用 |
| --- | --- |
| `scripts/check_pr.py` | 从 BASE 读取 `projectctl.py` 执行 `verify-change`；唯一 Task 行。 |
| `scripts/delivery_gate.py` | 离线读取 BASE policy，校验策略、S 授权摘要与 PR 元数据；不做网络、不执行候选代码。 |
| `scripts/quality_gate.py` | 读取 BASE `quality` 策略，校验 coverage JSON 与 unittest 日志的总量、新增行、测试数与 skip 白名单；缺失/损坏/未知即拒绝。 |
| `.github/workflows/delivery.yml` | 产生 `Delivery arbitration` 检查；在 PR 元数据上运行 BASE 脚本，只读、无写令牌。通过当前 PR REST API 刷新可空的 `mergeable` 并交叉核对 webhook BASE/HEAD；有限重试后仍未知即拒绝，不执行候选代码。 |
| `tooling/ai_delivery/delivery/service.py` | 已有 DeliveryGate：真实验收→独立 review→PR→显式 merge，复核证据绑定 BASE/contract/diff。 |
| `tooling/ai_delivery/delivery/github.py` | 合并前读取 BASE policy，检查远端保护、9 项 checks、mergeable、test-merge SHA、讨论和（启用后）Codex/Actions 仲裁。 |

## 本地验收

```sh
python scripts/projectctl.py check
python -m unittest tests.test_projectctl tests.test_governance tests.test_delivery_policy tests.test_delivery_gate tests.test_quality_gate -v
python scripts/delivery_gate.py --base <sha> --event <pull_request.json> --root .
python scripts/quality_gate.py --base <sha> --coverage coverage.json --test-log test-output.txt
```

## bootstrap 与未验证边界

- 当前 `bootstrap.remote_checks_enforced=false`：本片实现并测试远端仲裁读取路径，但不宣称新检查已成为分支保护，也不宣称已有真实 Codex PASS。PH-122 已把 Delivery arbitration 设为 required 并通过真实保护合入；但真实 Codex Connector PASS 因用量限制未取得，`remote_checks_enforced` 仍为 false，4 个仲裁来源尚未在真实合并路径执行，已登记为阻断性未验证项。
- 官方 App/bot 数字身份来自 GitHub 公共 API 实查；评论正文不是授权来源，只有符合身份、HEAD 和完整状态要求的复核结果才可作为仲裁证据。
- 启用远端仲裁时，HEAD 与 BASE 的 `delivery.yml` 必须逐字节一致；同名但由候选修改的 workflow 不能伪造可信检查。
- 本片不伪造人类 GitHub 批准，不关闭既有保护，不创建标签或发布生产。旧历史报告不批量重写；真实部署、生产验收另走对应任务。
- 覆盖率门槛由 SQLite/PostgreSQL 实测后设定；SQLite 结果不作为 PostgreSQL 并发证据，PG 专属并发测试在 SQLite 明确 skip 并由 PostgreSQL job 实跑。
