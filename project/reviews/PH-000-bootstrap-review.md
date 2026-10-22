# PH-000 初始化恢复门禁独立审阅

日期：2026-10-22。审阅范围为初始提交、冻结 A/B 提交、B 上的 C workflow 工作区差异，以及 `projectctl.py verify-change` 与 PH-000/PH-021 契约。未读取 runtime 或凭据，未创建代理，未操作 PR。

## 结论

**PASS_WITH_ACCEPTANCE_CONDITIONS**：固定 `initial → A → B → C` 的一次初始化恢复方案没有剩余仓库内可实证阻断。父链和固定 SHA 能防止 PR 自选中间契约；每个阶段用前一段提交中的 policy 做范围校验，且全程禁用 replacement refs。六个常规 CI job 仍照常执行，未增加跳过、放宽或 `needs` 依赖。

已核对：

- A 唯一父为 `0c7a95785e96c98176a76c3ffb531bc51a920b78`；B 唯一父为 A；B 是当前 HEAD 祖先。
- `verify-change --base initial --task PH-000` 在 A 通过；`verify-change --base A --task PH-021` 在 B 通过。B 的 8 个变更路径全部在 A 的 PH-021 白名单内。
- C 的差异限定在 workflow、PH-000 报告与任务状态：PR base 等于固定初始 SHA 时进入一次恢复分支，并按 `initial/PH-000 → A/PH-021 → B/PH-000` 校验；其他 PR base 继续走原 BASE `check_pr.py`。
- 治理校验先执行，policy 从固定前序提交读取；阶段 A/B 使用临时 detached worktree，最终阶段在干净 PR checkout 上校验。当前 `python -c` 单行的 PR 任务断言无 heredoc 缩进问题。后续 SQLite、PostgreSQL、MCP、hygiene、Container 六个 job 的步骤和触发保持执行。
- `seed_demo.py` 新增的 `runtime` 目录创建只发生在全新演练公司已生成、写演练凭据之前；重复初始化路径在函数开头返回。静态审阅未见业务对象、核算逻辑或既有运行数据被改变。新目录为 0700、凭据随后 chmod 0600，符合全新 CI checkout 的预期。

## 剩余风险

1. workflow 与固定 SHA 本身来自待合入 C，仍是 S 级信任边界。该机制不能防御有权限修改 workflow 的维护者或管理员；只能依赖本独立审阅、逐行 diff 和后续常规 BASE 门禁。
2. 最终阶段采用 B 的 PH-000 契约。该契约允许的治理路径较宽，且 workflow 只要求 B 是 HEAD 祖先，不证明 C 之后没有其他 PH-000 范围内提交。机器校验证明范围合法，不证明“仅治理收口”这一语义。
3. `verify-change` 仍是 declaration-only，不执行测试；SQLite 本地结果和既有远端记录不能替代最终 C 提交的 PostgreSQL 与容器运行。
4. 分支保护是外部状态。CI 全绿本身不能阻止 direct push、stale base 合入或未把全部检查设为 required。
5. `mkdir(..., exist_ok=True)` 只解决缺失目录，不收紧已存在 `runtime` 目录的权限或拒绝符号链接；这是既有本地部署边界，不是新增业务影响。CI 全新目录路径已按 0700/0600 要求实测。

## 接受条件

1. 将当前 workflow 与本审阅文件纳入未公开的同一个 C 提交；人工复核 `B..C` 仅包含治理收口、PH-000 证据/审阅及契约允许的收口文件，不得夹带业务实现。
2. PR body 唯一任务行为 `Task: PH-000`，base 仍是固定初始 SHA；远端运行中 governance、SQLite、PostgreSQL、MCP、Public hygiene、Container 六项全部通过，CodeQL 等实际触发的其他 workflow 也全部通过，不使用跳过或红灯合并。
3. 分支保护要求全部必需检查并通过；若 base 在等待或合并窗口前进，放弃该恢复 PR，在新 main base 上按常规 BASE 门禁重建收口。
4. 合并后的第一个常规 PH-000 收口 PR 删除临时初始化分支并更新交付/发布证据，最终发行包不得携带该一次性入口。
