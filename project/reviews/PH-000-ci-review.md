# PH-000-CI-REVIEW

## 范围

本次仅审阅：

- `.github/workflows/test.yml`
- `.github/workflows/release.yml`
- `.github/workflows/codeql.yml`
- `scripts/backup.sh`
- `scripts/publish-release.py`
- `scripts/check_pr.py`

未读取完整仓库或业务代码，未创建子代理，未发网，未调用 GitHub API，也未使用本地 `gh` 凭据。报告由当前代理执行，无法据此证明评审者是独立人员。

## 阻断项

### 1. PR 代码先于 BASE 策略执行，可篡改“可信 BASE”的 Git 解析结果

**位置**

- `.github/workflows/test.yml:25-35`
- `scripts/check_pr.py:24-29`

**问题**

`test.yml:25-26` 先执行 PR HEAD 中的 `scripts/projectctl.py check` 和 `scripts/release_check.py`，之后 `test.yml:33-34` 才从 BASE 提取并执行策略脚本。同一个 job、同一个 `.git` 目录内的任意 PR Python 代码都可以修改 Git 元数据。

具体反例：PR 的 `scripts/projectctl.py` 在 `check` 路径创建一个指向攻击者提交的 replacement object，再执行：

```sh
git replace "$BASE_SHA" "$ATTACKER_COMMIT"
```

随后 `.github/workflows/test.yml:33` 的 `git show "$BASE_SHA:scripts/check_pr.py"` 默认会解析 replacement ref，输出的就不是 BASE 中的原脚本；攻击者脚本可直接 `exit 0`。`scripts/check_pr.py:25` 同样用 `git show "$base:scripts/projectctl.py"` 读取策略，因此也会继承替换对象的影响。内层 `git` 子进程默认继承 replacement-object 行为。

这不是普通“PR 可以修改自己的检查脚本”问题：工作流声称从 BASE 载入策略，却在一个已被不可信 PR 代码修改过的仓库状态中完成载入。若该治理 job 被分支保护设为合并门槛，PR 可以伪造通过结果。

**最小修法**

在同一个 governance job 中，至少把 `test.yml:27-35` 的 BASE 策略步骤移到 checkout 之后、任何 PR 自带 Python 入口之前，并给该步骤设置：

```yaml
env:
  GIT_NO_REPLACE_OBJECTS: '1'
```

更稳妥的边界是把 BASE 策略校验放到独立 job 的干净 checkout 中，再由其他作业 `needs` 它；该 job 不执行 HEAD 脚本，并同时禁用 replacement objects。

## 已核对且未发现新增阻断项的接线

- `release.yml:26-36` 的 `--tag`、`--sha40hex`、`--assets-dir`、`--notes` 与 `publish-release.py:334-340` 的参数定义一致。
- `release.yml:16-18` 只把 `contents: write` 授予发布 job；`GH_TOKEN` 使用 `github.token`，checkout 设置了 `persist-credentials: false`。
- `publish-release.py:270-307` 只有 draft 或首次创建路径会上传，已公开 release 只下载并逐字节/哈希校验两个资产，没有公开资产覆写路径。
- draft 路径使用 `--clobber`，便于在部分上传后重试；公开 release 路径不会调用 `release upload --clobber`。
- `test.yml:164-170` 的备份、SHA256 校验、新库创建、`pg_restore --exit-on-error` 与恢复库完整性校验在命令接线层面闭合。
- `backup.sh:7-25` 拒绝已有输出目录并以 700 权限新建；`backup.sh:59-78` 停止并恢复原先运行的应用服务，成功后才移除 `.incomplete` 标记。

## 本地只读验证

- `sh -n scripts/backup.sh`：通过。
- Ruby `YAML.parse_file` 解析三个 workflow：通过。
- 对 `scripts/publish-release.py` 和 `scripts/check_pr.py` 做内存 AST 编译：通过。
- `./.venv/bin/python scripts/publish-release.py --help`：成功显示预期的四个参数。

以上仅是静态格式和 CLI 检查，不是 GitHub 运行证明。

```json
{
  "task_id": "PH-000-CI-REVIEW",
  "verdict": "fail",
  "findings": [
    {
      "severity": "blocker",
      "path": ".github/workflows/test.yml",
      "lines": "25-35",
      "summary": "PR HEAD scripts run before the trusted BASE policy is loaded in the same git repository; the PR can install a refs/replace mapping so git show resolves the BASE path to attacker-controlled content.",
      "counterexample": "The PR version of scripts/projectctl.py creates a replacement object for github.event.pull_request.base.sha; test.yml:33 then materializes attacker content as check_pr.py and exits successfully.",
      "minimal_fix": "Run the BASE-policy step immediately after checkout, before any HEAD Python script, with GIT_NO_REPLACE_OBJECTS=1 and git --no-replace-objects; prefer an isolated clean-checkout job gated with needs."
    }
  ],
  "unverified": [
    "No real GitHub Actions run was executed, so workflow expression expansion, runner behavior, branch-protection requirements, and fork-token behavior are unverified.",
    "No GitHub release was created, downloaded, repaired, or published; GH_TOKEN write authority and the prescribed draft-retry path remain runtime-unverified.",
    "Compose configuration, Django host/static settings, container health behavior, and the complete environment mapping were not read or run; the localhost health request and restore database override remain integration-unverified.",
    "scripts/release_check.py, scripts/package.py, scripts/verify-source-package.py, and their generated asset names/content contracts are outside the read whitelist and were not validated.",
    "CodeQL analysis and SARIF upload behavior against the actual repository and repository visibility were not run.",
    "The local gh credential issue was not tested or used; this review cannot establish whether the GHA built-in token can publish under the repository's current organization and environment policies."
  ]
}
```

## 复验记录

复验只覆盖原阻断项及其最小修复，不再扩展审查范围。

- `.github/workflows/test.yml:25-36`：`trusted BASE` 步骤现位于 `python scripts/projectctl.py check` 和 `python scripts/release_check.py` 之前。外层提取使用 `git --no-replace-objects show`，步骤环境设置 `GIT_NO_REPLACE_OBJECTS=1`。
- `scripts/check_pr.py:25-29`：内层 BASE `projectctl.py` 同样通过 `git --no-replace-objects show` 读取；执行 BASE 策略时显式传入 `GIT_NO_REPLACE_OBJECTS=1`。
- `scripts/projectctl.py:746-760`：`_run_git` 在清理 `GIT_DIR`、`GIT_WORK_TREE`、`GIT_INDEX_FILE` 后强制设置 `GIT_NO_REPLACE_OBJECTS=1`，实际 Git 子进程继承该设置。
- `tests/test_projectctl.py:326-345`：测试创建真实临时 Git replacement object，将 BASE 任务契约替换为更宽的 `allowed_paths=["*"]`，随后加入范围外文件并确认 `verify-change` 返回 `path_out_of_scope`。

实际执行的定向测试：

```sh
.venv/bin/python manage.py test tests.test_projectctl.ProjectCtlTests.test_git_replace_cannot_substitute_a_wider_base_contract --verbosity 2
```

结果：`Ran 1 test`，`OK`，系统检查无问题。原阻断项已消除，本轮未发现剩余阻断项。真实 GitHub Actions 仍未运行。

```json
{
  "task_id": "PH-000-CI-REVIEW",
  "verdict": "pass",
  "remaining_findings": [],
  "unverified": [
    "真实 GitHub Actions 未运行；本次结论来自修复代码静态核对和指定的真实临时 Git replacement 定向单测，不代表 GitHub runner 上的工作流已通过。"
  ]
}
```
