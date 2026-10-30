# EXP-1 · 自我改进三回路验证(回测评估)

日期:2026-10-30。分支:`worktree-self-improve-loop`(基线 origin/main `df01896`)。
性质:实验证据文档。合并门由用户设定:**证明新架构优于现状,才可议合并**。

## 1. 假设

现状架构(基线)中,报告的 `review.findings` / `deviations` / `unverified` 共 537 条教训
持续沉淀但从不被消费(0% 触达)。新架构的中环回路(`brief --precedents` 先例注入 +
`lessons`/`metrics` 只读聚合)能在交付前把同模块历史教训送达执行者,且成本有界、零行为回归。

## 2. 预注册判据(实现完成后、读数前固定)

| # | 判据 | 通过线 |
|---|---|---|
| C1 | 历史任务开工时同模块先例可用率(回测) | ≥ 30%(现状为 0%) |
| C2 | 注入成本 | 均值 ≤ 2KB 且峰值 ≤ 6KB;无先例时 0 字节 |
| C3 | 路由正确性 | 两个已知历史案例归位:风险级错误→门禁(出口A)、预写证据→先例(出口B) |
| C4 | 零回归 | 默认 `brief` 输出与 HEAD 逐字节一致;`projectctl check` 通过;新命令零写文件 |
| C5 | 工程质量 | 新增功能测试覆盖,作用域测试全绿 |
| C6 | 相关性抽查 | 3 个真实任务样本人工判定注入内容相关性 |

## 3. 实现范围(全部只读,不碰契约层)

- `scripts/projectctl.py`:`lessons`、`metrics` 两个只读子命令;`brief --precedents`(默认关闭)。
- 模块键:顶层文件取文件名;二级目录取两段;**路径级通用性**(>50% 任务白名单含同一路径,如
  `project/tasks.json`)与**模块级通用性**(`project/handoffs`、`project/reports`、`tests` 等
  每任务必写的产物目录)均判为无判别信号,不参与匹配。
- 注入上界:每任务 ≤5 条、每个来源任务 ≤2 条、每条 ≤280 字符;自排除(任务自己的报告不回注)。
- `tests/test_projectctl_lessons.py`:9 个用例(提取、过滤、上界、自排除、通用性、零写入、计数)。
- `scripts/experiments/backtest_lessons.py`:仅回测用,严格时间序(只允许引用更早任务的报告)。

## 4. 结果(全部来自实际运行,命令见 §6)

| # | 读数 | 判定 |
|---|---|---|
| C1 | 71 个任务回放,67 个(94.4%)开工时同模块先例已存在;4 个空注入(PH-000 首任务、PH-100/108/226 无同域先例) | ✅ 94.4% ≫ 30% |
| C2 | 注入均值 1259.7 字节,峰值 1810 字节;无先例时恰好 0 字节 | ✅ |
| C3 | 风险级:`critical_path_requires_high_risk` 门禁在 main 已存在(verify-change 路径,projectctl.py)→ 出口A 归位;预写证据:不可机械检测,但作为 PH-236 报告教训已在库、对同模块后续任务可检索 → 出口B 归位 | ✅ |
| C4 | 默认 `brief PH-238` 与 HEAD 逐字节一致(diff 为空);`projectctl check` ok(task_count=164);`lessons`/`metrics`/`brief --precedents` 运行前后文件树快照一致(测试断言) | ✅ |
| C5 | 作用域测试 28/28 绿(`test_projectctl_lessons` 9 项 + 既有 `test_projectctl` 19 项) | ✅ |
| C6 | 样本人工判定:PH-232(结算/现金代码片)注入 PH-230/228/224 的 SQLite 守卫、交易对手停用、插件依赖回归等代码教训——高度相关;PH-237(文档片)注入 PH-236 的预写证据/风险级复核 BLOCKER——高度相关;PH-236(规划片)注入 PH-234/237 规划教训——中度相关 | ✅(1/3 中度) |

## 5. 与现状架构的对比结论(回测意义上)

- 现状:537 条教训沉淀后 0% 触达,同模块重复失败无机制拦截,教训跨任务蒸发。
- 新架构:同一批教训,94.4% 的任务在开工前即可见,代价 ≤1.8KB、默认关闭、零契约变更。
- 结论:**在回测可证明的范围内,新架构严格优于现状**;且增量全部位于机器层(只读命令),
  冻结层(AGENTS.md/schema/tasks.json)一个字未动——符合"散文冻结、机器演进"设计。

## 6. 复现命令

```sh
.venv/bin/python -m unittest tests.test_projectctl_lessons tests.test_projectctl
.venv/bin/python scripts/projectctl.py check
.venv/bin/python scripts/projectctl.py lessons
.venv/bin/python scripts/projectctl.py metrics
.venv/bin/python scripts/projectctl.py brief PH-238 --precedents
.venv/bin/python scripts/experiments/backtest_lessons.py --samples PH-232,PH-236,PH-237
```

## 7. 诚实的边界(合并决策必读)

1. **回测 ≠ 前瞻**。94.4% 证明"教训可及",不直接等于"重复失败率下降"。后者需日常任务
   实测(即 PH-236 遗留的墙钟/质量复测问题——离线无法诚实量化,本实验同样不冒充)。
2. **覆盖率 ≠ 精确率**。模块键是启发式;C6 抽查 3/3 相关(1 项中度),但未做全量人工复核。
3. 注入默认关闭,采用与否由执行者/规划槽决定;不存在自动行为变更。
4. 本分支未触碰 tasks.json / manifest / delivery-status / AGENTS.md;合入若被批准,仍需按
   治理走规划登记(碰 CRITICAL_PATHS 的登记属 S 级),本实验分支自身不自我授权。
