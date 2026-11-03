# 批次 C 发布资格 gate 判定记录（PH-372 执行，PH-366/PH-367 契约）

- 执行任务：PH-372（docs-only；本片**不执行**部署/恢复/并发/性能，只合并既有证据并判定）。
- 执行 BASE / 候选 SHA：**5278c96**（main，PH-386/382 已合入）；候选发布 SHA 以此记录绑定；产品代码基线为 **PH-371（#244, 36cdf00）**，其后无产品代码提交（PH-382 docs-only / PH-386 docs）。
- 执行时间：2026-10-31（UTC+8）；执行环境：macOS 本地 SQLite（仅治理基线），无 staging/production。
- 契约：`batch-c-release-evidence-contract.md`（PH-366）、`batch-c-aggregate-acceptance-contract.md`（PH-367）、`ops-runbook.md`（PH-345）、`batch-c-runbook.md §10`（PH-357）。
- 结论先行：**gate = blocked；release_ready=false；无有效豁免；未发布**。详见 §6/§7。

## 1. 方法与来源

1. 收集 PH-333/334/335/357 汇总产物与 PH-347/348/349/350 已记录证据（只读，不重跑演练）。
2. 按 PH-366 §3/§7 归一 exit→status；按 PH-367 §3 **多来源取最严**（fail > blocked > not_run > pass），L2–L5 分层保留；冲突记录（本次无冲突）。
3. 按 PH-366 §6.1 必需证据集合逐项判定；缺口登记；核心项不豁免。
4. **产品代码基线**：BASE `5278c96` 中最新产品代码提交为 **PH-371（#244, 36cdf00）**；其后 PH-382（#247，docs-only）与 PH-386（#246，docs/规划）未改产品代码。本记录绑定候选 SHA 5278c96，不声称“PH-370 后产品代码未变”。
5. **合并记录与 digest（可复现）**：完整 canonical 记录如下（下文 JSON 块）。digest 算法：对**解析后的该 JSON 对象**执行 `json.dumps(record, ensure_ascii=False, sort_keys=True)`（UTF-8、默认分隔符），取 SHA-256。任何人可从下方 JSON 重算并验证。

```json
{
  "aggregate_id": "batch-c-release-gate-2026-10-31",
  "candidate_sha": "5278c96",
  "checks": [
    {
      "evidence": "projectctl check ok/312; 23 governance OK; manage.py check 0 errors; makemigrations clean; verify_integrity ok",
      "id": "governance.baseline",
      "layers": [
        "L2"
      ],
      "status": "pass"
    },
    {
      "evidence": "local dry_run pass; real install L3 not_run",
      "id": "V1-OPS-01",
      "layers": [
        "L1",
        "L2"
      ],
      "status": "not_run"
    },
    {
      "evidence": "real TLS/domain not_run",
      "id": "V1-OPS-02",
      "layers": [
        "L1",
        "L2"
      ],
      "status": "not_run"
    },
    {
      "evidence": "local dry_run pass; real upgrade/rollback L3 not_run",
      "id": "V1-OPS-03",
      "layers": [
        "L1",
        "L2"
      ],
      "status": "not_run"
    },
    {
      "evidence": "SQLite backup/restore local; real PG L3 not_run",
      "id": "V1-OPS-04",
      "layers": [
        "L1",
        "L2"
      ],
      "status": "not_run"
    },
    {
      "evidence": "rehearsal facts pass; real capacity L3 not_run",
      "id": "V1-OPS-05",
      "layers": [
        "L1",
        "L2"
      ],
      "status": "not_run"
    },
    {
      "evidence": "contract tests pass; real PG concurrency L3 not_run",
      "id": "V1-OPS-06",
      "layers": [
        "L1",
        "L2"
      ],
      "status": "not_run"
    },
    {
      "evidence": "no browser walkthrough",
      "id": "L4_browser",
      "layers": [
        "L4"
      ],
      "status": "not_run"
    },
    {
      "evidence": "no production/customer acceptance",
      "id": "L5_production",
      "layers": [
        "L5"
      ],
      "status": "not_run"
    },
    {
      "evidence": "only L1/L2 summary",
      "id": "M15_rollup",
      "layers": [
        "L3",
        "L4",
        "L5"
      ],
      "status": "not_run"
    }
  ],
  "conflicts": [],
  "gaps": [
    {
      "item": "V1-OPS-01/03/04 real staging/production deploys",
      "reason": "not_run"
    },
    {
      "item": "V1-OPS-02 TLS",
      "reason": "not_run"
    },
    {
      "item": "V1-OPS-04 real PG backup/restore + RPO/RTO",
      "reason": "not_run"
    },
    {
      "item": "V1-OPS-05 CI/PG capacity/backlog/health",
      "reason": "not_run"
    },
    {
      "item": "V1-OPS-06 real PG concurrency",
      "reason": "not_run"
    },
    {
      "item": "L4 browser 1440/390",
      "reason": "not_run"
    },
    {
      "item": "L5 production/customer",
      "reason": "not_run"
    },
    {
      "item": "independent reviewer signature",
      "reason": "pending; executor cannot self-sign"
    }
  ],
  "merged_at": "2026-10-31T13:30:00+00:00",
  "merged_by_role": "worker-docs (execution)",
  "overall_status": "blocked",
  "product_code_baseline": "PH-371 (#244, 36cdf00) is the latest product-code commit in this BASE; product code is unchanged after it (PH-382 #247 docs-only, PH-386 #246 docs/planning).",
  "profile": "local/rehearsal (no staging/production)",
  "release_ready": false,
  "sources": [
    {
      "layers": [
        "L1",
        "L2"
      ],
      "note": "local/rehearsal; V1-OPS-02 TLS not_run; release_ready=false",
      "script": "ops_rollup_acceptance",
      "status": "pass",
      "task": "PH-333"
    },
    {
      "layers": [
        "L1",
        "L2"
      ],
      "note": "contract only; real PG not_run",
      "script": "ops_pg_concurrency",
      "status": "pass",
      "task": "PH-334"
    },
    {
      "layers": [
        "L1",
        "L2"
      ],
      "note": "rehearsal facts; L3-L5 not_run; release_ready=false",
      "script": "ops_health_capacity",
      "status": "pass",
      "task": "PH-335"
    },
    {
      "layers": [
        "L1",
        "L2"
      ],
      "note": "backfill local evidence; gaps L3-L5",
      "script": "docs",
      "status": "pass",
      "task": "PH-357"
    },
    {
      "layers": [
        "contract"
      ],
      "note": "execution prerequisite contract",
      "script": "docs",
      "status": "pass",
      "task": "PH-367"
    },
    {
      "layers": [
        "contract"
      ],
      "note": "release evidence/gate contract",
      "script": "docs",
      "status": "pass",
      "task": "PH-366"
    }
  ]
}
```

- **evidence_digest（canonical JSON SHA-256）**：`3ee83650a3a5fb7df1ec0efe589ee176a6e9df9af11e36adcdc0fbfb8d86b17b`（canonical 字节数 3159；`merged_at=2026-10-31T13:30:00+00:00`）。

### 1.1 来源登记

| 来源 | 产物 | 声明状态 | 层级 | 说明 |
| --- | --- | --- | --- | --- |
| PH-333 | `scripts/ops/rollup_acceptance.py`（`ops_rollup_acceptance`） | pass | L1+L2 | 本地/隔离；V1-OPS-02 TLS not_run；release_ready=false |
| PH-334 | `tests.test_v1_pg_concurrency` 汇总契约 | pass | L1+L2 | 无真实 PG 时 not_run；SQLite 被拒 |
| PH-335 | `healthcheck.rollup_evidence` | pass | L1+L2 | rehearsal facts；L3–L5 not_run；release_ready=false |
| PH-357 | `batch-c-runbook.md §10` / `batch-c-acceptance.md §7` | pass | L1+L2 | 回填本地证据；缺口 L3–L5 |
| PH-367 | `batch-c-aggregate-acceptance-contract.md` | contract | — | 执行前置/合并/gate 流程 |
| PH-366 | `batch-c-release-evidence-contract.md` | contract | — | 必需集/豁免/签名 |

## 2. 治理基线（本次实跑）

| 命令 | 实际结果 |
| --- | --- |
| `python scripts/projectctl.py check` | ok=true/task_count=312，exit 0 |
| `python -m unittest tests.test_projectctl tests.test_governance -v` | Ran 23 tests, OK |
| `python manage.py check` | 119 个既有警告、0 error |
| `python manage.py makemigrations --check --dry-run` | No changes detected |
| `python manage.py verify_integrity --json`（新库 migrate 后） | {"ok":true,"errors":[],"organizations":0,"entries":0} |
| `python manage.py test tests --verbosity 1` | Ran 1310 tests in 141.269s，FAILED (failures=5, errors=3, skipped=70)；8 个失败名=既有 macOS 基线（drf_security 4F+3E、delivery_execution 1F、delivery_execution_sandbox 1F），无新增 |

- 代码存在/自动化基线：可支持“本地 L1+L2”；**不构成 L3/L4/L5 证据**。

## 3. 合并矩阵（多来源取最严）

| gate 项 | 本地 L1 | L2 | L3 CI/PG | L4 浏览器 | L5 生产 | 合并状态 |
| --- | --- | --- | --- | --- | --- | --- |
| V1-OPS-01 安装/诊断 | 有 | local pass | **not_run** | not_run | not_run | **not_run** |
| V1-OPS-02 TLS/安全 | 有 | 契约 pass；真实 TLS not_run | **not_run** | not_run | not_run | **not_run** |
| V1-OPS-03 升级/回退 | 有 | local dry_run pass | **not_run** | not_run | not_run | **not_run** |
| V1-OPS-04 备份/恢复 | 有 | SQLite pass；PG execute not_run | **not_run** | not_run | not_run | **not_run** |
| V1-OPS-05 健康/容量/积压 | 有 | rehearsal pass | **not_run** | not_run | not_run | **not_run** |
| V1-OPS-06 PG 并发 | 有 | 契约 pass；真实 PG not_run | **not_run** | not_run | not_run | **not_run** |
| M15 汇总 | 有 | L1+L2 汇总 | **not_run** | **not_run** | **not_run** | **not_run** |
| 浏览器 1440/390 | — | — | — | **not_run** | — | **not_run** |
| 发布包/运行时排除 | 有 | 治理基线 | — | — | — | pass（仅本地） |

- 合并结果：**overall_status=blocked**（任一 required 非 pass 且无有效豁免）；无冲突来源。
- 规则：演练/本地属于 L2；SQLite 不作 PG 证据；dry_run 不作 execute；rehearsal 不冒充 L5。

## 4. 必需证据集合核对（PH-366 §6.1）

| 项 | 要求 | 现状 | 判定 |
| --- | --- | --- | --- |
| 治理基线 | check/23/check/makemigrations/verify_integrity/全量无新增 | 满足（本地） | pass（L2） |
| V1-OPS-01/02/03/04 | execute 证据 pass（真实环境） | 仅本地 L2；真实环境 not_run | **not_run** |
| V1-OPS-05 | `ops_health_capacity` pass | rehearsal only；真实 L3 not_run | **not_run** |
| V1-OPS-06 | 真实 PG 一胜一拒 + artifacts | 契约 pass；真实 PG not_run | **not_run** |
| M01–M15 required 层 | 按矩阵 | M15 L3–L5 not_run | **not_run** |
| L4 浏览器 | 截图/步骤/角色 | 未执行 | **not_run** |
| L5 生产/试点 | 生产记录/RPO-RTO/客户验收 | 未执行 | **not_run** |
| 发布包 | 无 runtime/.env/凭据/客户数据 | 仅本地基线检查 | pass（本地） |

## 5. 缺口登记（PH-366 §6.2；未登记豁免）

| # | item | expected | actual | reason | owner | due | risk | mitigation |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| G1 | V1-OPS-01/03/04 真实 staging/production | execute envelope（pass）+ artifacts | not_run | 无 staging/production 环境 | 发布负责人 + DBA | 下一变更窗口 | 高 | 按 PH-367 顺序执行并回填 |
| G2 | V1-OPS-02 TLS | 真实域名/证书/续期 evidence | not_run | 无真实域名/证书 | 安全负责人 | 下一窗口 | 高 | 真实 TLS 探针；不得自签冒充 |
| G3 | V1-OPS-04 PG 备份/恢复 + RPO/RTO | PG 快照/恢复/RPO-RTO 实测 | not_run | 本地仅 SQLite；工具/DSN 缺失 | DBA | 下一窗口 | 高 | 隔离 PG 恢复演练 |
| G4 | V1-OPS-05 真实容量/积压 | CI/部署 facts/env | not_run | 无 CI/部署采集 | DBA + 支持 | 下一窗口 | 中 | 部署 profile facts + PH-368 SLO |
| G5 | V1-OPS-06 真实 PG 并发 | 两连接一胜一拒/时序日志 | not_run | 无 PG DSN | DBA | 下一窗口 | 高 | PH-349 CLI/PH-334 |
| G6 | L4 浏览器 | 1440/390/键盘/空错载停用截图 | not_run | 未走查 | 支持负责人 | 下一窗口 | 中 | 按 PH-352/375 清单 |
| G7 | L5 生产/客户验收 | 生产记录/客户验收 | not_run | 未发布 | 发布负责人 | 发布前 | 高 | 试点+客户验收，不得演练冒充 |
| G8 | 独立复核签名 | 执行人+独立复核人签名/digest | pending | 本片 review 中；执行人不得自批 | 独立验收复核人 | 评审通过时 | 中 | 复核后用同一 digest 签批 |

- **豁免登记：空**。核心项（03/04/06/M15/L5）按 PH-366 §6.2 不可豁免；本片无三方签批，故无 `released_with_gaps`。
- 缺口不是 pass：G1–G7 未关闭前不得对外称“发布就绪”。

## 6. Gate 判定

- **判定：`blocked`**（PH-366 §6.4：required 证据 not_run 且无有效豁免）。
- `release_ready=false`；未发布、未进入试点；不允许 `released`/`released_with_gaps`。
- 允许表述：批次 C 本地 L1+L2 基线完成；**发布资格未通过**。
- 禁止表述：演练通过=发布通过；SQLite=PG；dry_run=execute；配置存在=运行证据；文档=验收。

## 7. 签名与时间戳（不伪造）

| 角色 | 成员引用 | 范围 | 时间 | decision | evidence_digest |
| --- | --- | --- | --- | --- | --- |
| 执行（documentation/evidence merge） | worker-docs | gate 记录/合并 | 2026-10-31 (UTC+8) | submitted-for-review | `3ee83650a3a5fb7df1ec0efe589ee176a6e9df9af11e36adcdc0fbfb8d86b17b` |
| 独立复核人 | 待独立 S 复核 | gate 判定/缺口/签名 | 评审时 | pending | 同上 |
| 产品/发布/独立复核（豁免，如需） | 无 | — | — | 未申请 | — |

- 本记录**不含任何自批 pass**；复核 PASS 后由复核人以其名义签署该 digest；若证据/缺口变化，digest 重算并重签。
- 时效：证据执行日 2026-10-31，PH-366 §8 的 14 天有效；SHA/环境/契约变化即失效重跑。

## 8. 失效/重跑与变更控制

- 候选 SHA 变化、staging/production 环境变化、PH-366/PH-367 契约变化、缺口关闭后重跑 → 重新合并并生成新 digest；旧记录保留。
- 本判定不改变 CI 门禁/验收命令/产品行为；后续执行片按 PH-367 顺序执行并把真实 artifacts 回填后重新判定。
- 若未来申请豁免：仅非核心项，产品+发布+独立复核三方签批并写入本文件“豁免登记”，豁免≠pass。

## 9. 反例（本片未采用）

- 用本地/SQLite/rehearsal 冒充 L3/L5；dry_run 冒充 execute；无证据写 pass；挑有利来源；跨 SHA 合并；执行人自批；缺口不登记。

## 10. 未验证

- 真实 staging/production 部署/升级/恢复/并发/TLS、L3 CI/PG、L4 浏览器、L5 生产/客户验收全部未执行。
- 无真实 RPO/RTO、无生产容量/积压、无发布包生产验证；本记录仅为 gate 判定，不是发布证明。
