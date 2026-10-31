# 容量/健康/积压后续契约（PH-368 冻结）

- 产出：PH-368（docs-only；**不实现**、不新增脚本/迁移/告警平台/自动扩缩容）。
- 上游（done）：PH-350 `scripts/ops/healthcheck.py` + `docs/engineering/observability.md`；PH-335 汇总验收（L1+L2，`release_ready=false`）；PH-345 envelope/退出码；PH-366 证据/发布资格 gate；PH-364 到期扫描/待办只读契约。
- 目的：在已实现的本地/隔离可观测性基线之上，冻结**后续** SLO/阈值治理、告警/积压策略、历史趋势/容量规划与 PH-366 证据映射；本文件不替代 PH-350 实现、不重定义已有 checks/阈值。

## 1. 现状基线（不重实现）

- `healthcheck.py`：`--facts` 脱敏事实 JSON → checks（health.*、capacity.*、backlog.*、perf.*）；PH-345 envelope/退出码 0/2/3/4/5；dry_run 默认；**仅 rehearsal 可 pass**；staging/production dry_run=not_run、execute=exit5；敏感键递归 redact。
- `rollup_evidence(envelope)`：L1 脚本存在、L2 本地 rehearsal、L3 CI/PG、L4 浏览器、L5 生产；`release_ready=false` 恒 false，`missing=[L3_ci_pg, L4_browser, L5_production]`。
- 本后续契约只新增**治理/策略/趋势/证据映射**，不改变既有 checks id/退出码/阈值默认值；如需改默认阈值必须走实现片按实际 BASE 复核并回填证据。

## 2. SLO 与阈值治理（冻结）

### 2.1 定义

```json
{
  "slo_id": "v1-ops05-health-availability",
  "scope": "company|project|environment",
  "metric": "health.db|health.migrations|health.cache|health.queue|health.disk|health.backup|capacity.connections|capacity.tables|capacity.slow_queries|backlog.queue_depth|backlog.reminders|backlog.todos|backlog.unsettled|perf.p50_ms|perf.p95_ms",
  "environment": "rehearsal|staging|production",
  "target": {"operator": "<|<=|>=|>", "value": 0, "unit": "count|percent|bytes|hours|ms"},
  "window": "instant|1h|24h|7d",
  "severity": "warning|blocker",
  "owner_role": "DBA|support|security|release",
  "approved_by": ["release", "support"],
  "evidence_ref": "<path_or_url>",
  "status": "active|superseded"
}
```

### 2.2 规则

1. **rehearsal 默认阈值不是生产 SLO**：`docs/engineering/observability.md §3` 的默认值只适用于本地/隔离演练；staging/production 必须由部署 profile 提供 SLO 记录并审批，不得把 rehearsal 数字写成生产 SLO。
2. **SLO 记录版本化**：`slo_id + environment + window` 唯一；变更追加新版本并 `superseded` 旧版；历史证据按当时版本解释，不重算。
3. **审批**：生产 SLO 由发布负责人 + 支持负责人（容量/延迟/积压）或安全负责人（磁盘/备份）共同批准；执行人不得自批。
4. **缺失 SLO**：环境无 SLO 记录 → `not_run`/`blocked`，不得用默认值冒充；0/阈值只在真实配置时使用。
5. **单位/精度**：金额不用；百分比 0–100；字节整数；ms 整数；hours 整数；日期 ISO8601。
6. **阈值来源**：优先部署 profile/CI 配置；无配置时 rehearsal 默认值仅限 rehearsal，且 evidence 必须标 `profile=rehearsal`。

### 2.3 建议的 SLO 类别（供部署配置，不在本片实现）

| 类别 | 指标 | 典型判据（由部署审批，非本片数字） |
| --- | --- | --- |
| 可用性 | health.db/cache/queue | 持续可达；不可达=blocker |
| 迁移健康 | health.migrations | pending>0=blocker（发布前） |
| 备份新鲜度 | health.backup | 超过批准备份窗口=blocker |
| 磁盘 | health.disk | 低于批准余量=blocker/warning |
| 连接容量 | capacity.connections | 超过批准使用率=warning |
| 大表/慢查询 | capacity.tables/slow_queries | 超过批准阈值=warning |
| 积压 | backlog.* | 超过批准积压=warning；持续不降=blocker |
| 延迟 | perf.p50/p95 | 超过批准 SLO=warning |

## 3. 告警/积压策略（冻结）

1. **告警记录**（只读派生，不依赖外部平台）：

```json
{"alert_key": "env|metric|scope|window", "slo_id": "...", "severity": "warning|blocker",
 "first_seen_at": "<ISO8601>", "last_seen_at": "<ISO8601>", "count": 1,
 "state": "open|acknowledged|resolved", "owner_role": "...", "refs": [], "redacted": true}
```

2. **去重**：`alert_key` 相同视为同一告警；按 window 聚合 count，不按消息文本；重试/扫描不新增重复告警。
3. **升级**：blocker → 发布/支持负责人 + DBA；warning → 支持负责人；无 acknowledgement 不自动关闭；恢复需证据（指标回到 SLO 内并记录）。
4. **不做自动动作**：不自动扩缩容、不自动重启、不自动改配置、不发送外部消息（邮件/短信/Webhook 属后续实现片）；本契约只冻结记录/去重/升级/关闭规则。
5. **积压策略**：按队列/reminders/todos/unsettled 分别度量；阈值由环境 SLO 配置；持续 N 个窗口不降 → 升级 blocker；不自动清理、不自动补数据。
6. **与 PH-364 边界**：提醒/待办只读 refs 与去重键沿用 `docs/v1.0-development/` 通知契约（PH-364）；本契约不重复实现 reminder 派生，只消费其只读输出；通知发送/收件箱属 PH-340/PH-355 后续。

## 4. 历史趋势与容量规划（冻结）

### 4.1 时间序列记录

```json
{"series_id": "env|metric|scope", "metric": "capacity.tables|backlog.queue_depth|perf.p95_ms|health.backup",
 "scope": "company|project", "environment": "staging|production", "as_of": "<ISO8601>",
 "value": 0, "unit": "bytes|count|ms|hours", "app_sha": "<40-hex>",
 "source": "healthcheck|ci|deploy", "evidence_ref": "<path_or_url>", "retention": "30d|90d|1y"}
```

- append-only；同 `series_id + as_of` 唯一；缺失样本 → 该点 null + `missing_sample`，不得插值/补 0。
- 保留期由部署配置；本地/演练数据不并入生产趋势；环境/口径变化需新 series 或标注断点。
- 趋势计算需要**同一 environment/series 的 ≥N 个有效样本**（默认 N=7，部署可批准调整）；不足 → `insufficient_samples`，不预测。
- 聚合：日/周均值/最大值；P50/P95 用 nearest-rank（与 healthcheck 一致）；不得用本地 SQLite 结果冒充 PG/生产趋势。

### 4.2 容量规划记录

```json
{"plan_id": "cap-...", "scope": "...", "metric": "...", "environment": "production",
 "current_value": 0, "observed_growth_per_day": 0, "days_to_warning": null,
 "days_to_blocker": null, "assumptions": ["..."], "confidence": "low|medium|high",
 "owner_role": "DBA", "reviewer_roles": ["release"], "reviewed_at": "<ISO8601>",
 "action": "monitor|expand|optimize", "evidence_refs": []}
```

- 预测只能基于已审批 SLO 阈值 + 真实历史样本；样本不足 → `days_to_* = null` + `insufficient_samples`，不猜。
- `confidence=low` 时必须复评；规划动作不自动执行（扩容/清理/调参均为人工/后续实现）。
- 容量规划输出进入发布评审（PH-366 gate）作为 V1-OPS-05 证据的补充，不替代真实执行证据。

## 5. 与 PH-366 发布 gate 的证据映射（冻结）

| PH-366 gate 项 | 来源证据 | 层级 | 当前状态 |
| --- | --- | --- | --- |
| V1-OPS-05 健康/容量/积压 | `healthcheck.py` envelope（checks/summary/artifacts）+ rehearsal facts | L2 | 本地/隔离已实现；staging/production not_run |
| V1-OPS-05 CI/PG 真实容量 | CI/部署 healthcheck + PG 统计（连接/表/慢查询） | L3 | **not_run** |
| V1-OPS-05 浏览器可见性 | 1440/390 健康/告警页面/空错载停用态 | L4 | **not_run** |
| V1-OPS-05 生产/试点 | 生产 healthcheck/趋势/告警记录/RPO-RTO | L5 | **not_run** |
| M12/M15 汇总 | PH-335 `rollup_evidence` + PH-357 回填 | L2/L3 | `release_ready=false` |

- 映射规则：envelope `status=pass` 且 `profile=rehearsal` 只可作 L2；staging/production 证据必须来自对应 profile 且含环境信息；L3 需 CI run URL + PG 版本/连接数；L4 需截图/步骤；L5 需生产记录。
- `not_run`/`blocked`/缺失 → PH-366 gate 对应项不得 pass；豁免按 PH-366 §6.2（核心容量/恢复项不可豁免）。
- 证据绑定候选 SHA/环境/时效（PH-366 §7/§8）；rehearsal 证据不得复用为生产证据。

## 6. 未实现/not_run 纪律（冻结）

1. 本契约**不新增**脚本/表/迁移/告警平台/趋势存储；实现由后续片按实际 BASE 申请。
2. 当前未实现：SLO 配置存储、告警记录/去重实现、趋势存储/聚合、容量预测、外部告警发送、自动扩缩容。
3. 当前证据状态：PH-350 本地 rehearsal L1+L2；PH-335 `release_ready=false`；L3/L4/L5 全部 not_run。
4. 任何“健康/容量已通过发布验收”的表述必须先有 L3/L4/L5 证据并按 PH-366 gate 签批；**不得**以本契约/本地数字/配置存在代替。

## 7. 角色与数据安全

- 角色：DBA（容量/备份/连接）、支持负责人（积压/告警）、安全负责人（磁盘/TLS/凭据）、发布负责人（gate/审批）、独立验收复核人。
- 只读/最小权限；不读生产凭据、不爬客户数据；facts/趋势/告警证据递归 redact（password/token/secret/credential/payroll/customer/private_key/api_key/authorization）。
- 不发送外部消息；不上传客户数据到第三方；证据存放 `runtime/observability`（不入发布包），仅脱敏摘要入 `docs/verification.md`。

## 8. 后续实现片验收（按实际 BASE 复核）

```sh
python scripts/projectctl.py check
python manage.py check
python -m unittest tests.test_projectctl tests.test_governance -v
python manage.py test tests.test_v1_ops_observability --verbosity 1
# 后续实现片示例（未创建，勿在本片执行）：
# python manage.py test tests.test_v1_observability_followup --verbosity 1
```

- 金样例：SLO 记录版本化/审批/缺失 not_run；告警去重/升级/关闭；趋势 ≥N 样本才预测；容量规划 null 不猜；PH-366 mapping 每项有层级/证据。
- 反例：本地数字冒充生产、rehearsal 阈值当生产 SLO、缺失当 0、无样本预测、自动扩缩容/自动关闭、证据含敏感原文。

## 9. 变更控制与未验证

- 本文冻结后，阈值/field/SLO 结构变更须独立规划并按 PH-350/PH-345/PH-366 对齐；实现片不得静默削弱 not_run/redaction/gate 规则。
- 未验证：SLO/告警/趋势/容量规划均未实现；L3/L4/L5 未执行；本片 docs-only，无运行结果。
