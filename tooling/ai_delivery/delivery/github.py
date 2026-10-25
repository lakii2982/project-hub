"""Trusted publication and merge gate for verified deliveries.

Only the controller calls this module, after it has completed verification,
independent review and delivery-document checks.  Everything here is rebuilt
from the current BASE plus the exact verified manifest; the worker's HEAD,
repository configuration and ``.git`` state are never trusted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import uuid

from ..controller import repository


class DeliveryError(RuntimeError):
    """Stable, sanitized publication/merge failure."""


_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_TASK = re.compile(r"PH-[0-9]+\Z")
_SLUG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}\Z")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,150}\Z")
_MODES = frozenset({"100644", "100755", "120000"})
_MAIN = "main"
_GH = ("/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/usr/bin/gh")
_TIMEOUT = 30
_OUTPUT_LIMIT = 2 * 1024 * 1024
_ADD_CHUNK = 200
_REQUIRED_CHECKS = frozenset({
    "CodeQL (javascript-typescript)",
    "CodeQL (python)",
    "Container smoke",
    "MCP protocol",
    "SQLite domain",
    "PostgreSQL domain",
    "Public hygiene",
    "Project governance",
})
_REVIEW_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!,$cursor:String){"
    "repository(owner:$owner,name:$name){pullRequest(number:$number){"
    "reviewThreads(first:100,after:$cursor){pageInfo{hasNextPage endCursor}"
    "nodes{isResolved}}}}}"
)


def _fail(code):
    raise DeliveryError(code)


def _gh_executable():
    candidates = [*_GH, str(Path.home() / ".local/bin/gh")]
    resolved = shutil.which("gh")
    if resolved:
        candidates.append(resolved)
    for candidate in candidates:
        try:
            path = Path(candidate)
            if path.is_absolute() and path.is_file() and os.access(path, os.X_OK):
                return str(path)
        except OSError:
            continue
    _fail("github_unavailable")


def _run(argv, *, cwd, stdin_text=None, operation):
    options = {
        "cwd": str(cwd), "text": True, "shell": False,
        "stdout": subprocess.PIPE, "stderr": subprocess.PIPE,
        "timeout": _TIMEOUT, "check": False,
    }
    if stdin_text is None:
        options["stdin"] = subprocess.DEVNULL
    else:
        options["input"] = stdin_text
    try:
        completed = subprocess.run(argv, **options)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        _fail(f"{operation}_unavailable")
    if completed.stdout is None or completed.stderr is None:
        _fail(f"{operation}_unavailable")
    if len(completed.stdout) > _OUTPUT_LIMIT or len(completed.stderr) > _OUTPUT_LIMIT:
        _fail(f"{operation}_output_too_large")
    return completed


def _json(raw, operation):
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        _fail(f"{operation}_invalid")


def _sha(value, length, code):
    if not isinstance(value, str):
        _fail(code)
    pattern = _HEX40 if length == 40 else _HEX64
    if not pattern.fullmatch(value):
        _fail(code)
    return value


def _relative(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        _fail("invalid_manifest")
    if "\x00" in value or "\\" in value:
        _fail("invalid_manifest")
    if any(ord(char) < 32 for char in value):
        _fail("invalid_manifest")
    path = Path(value)
    parts = value.split("/")
    if (path.is_absolute() or value.startswith("/")
            or any(part in {"", ".", ".."} for part in parts)):
        _fail("invalid_manifest")
    return value


def _descriptor(value):
    if not isinstance(value, dict) or set(value) != {"mode", "type", "sha256"}:
        _fail("invalid_manifest")
    if value.get("mode") not in _MODES or value.get("type") != "blob":
        _fail("invalid_manifest")
    _sha(value.get("sha256"), 64, "invalid_manifest")
    return {"mode": value["mode"], "type": "blob", "sha256": value["sha256"]}


def _manifest(value):
    if not isinstance(value, (list, tuple)) or not value:
        _fail("empty_manifest")
    result = []
    seen = set()
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "old", "new"}:
            _fail("invalid_manifest")
        path = _relative(item["path"])
        if path in seen:
            _fail("invalid_manifest")
        seen.add(path)
        old = None if item["old"] is None else _descriptor(item["old"])
        new = None if item["new"] is None else _descriptor(item["new"])
        if old is None and new is None:
            _fail("invalid_manifest")
        result.append({"path": path, "old": old, "new": new})
    result.sort(key=lambda row: row["path"])
    return result


def _title(value):
    if not isinstance(value, str):
        _fail("invalid_title")
    text = value.strip()
    if not text or len(text) > 200 or "\n" in value or "\r" in value:
        _fail("invalid_title")
    return text


def _body(value, task_id):
    if not isinstance(value, str) or len(value) > 65536 or "\x00" in value:
        _fail("invalid_body")
    lines = [line for line in value.splitlines() if "Task:" in line]
    if len(lines) != 1 or lines[0].strip() != f"Task: {task_id}":
        _fail("invalid_task_line")
    return value


def _branch(task_id, run_id, kind=None):
    prefix = "fix" if kind == "fix" else "feat"
    return f"{prefix}/{task_id}-delivery-{run_id}"


def _validated_view(repo, view):
    if not isinstance(view, dict):
        _fail("invalid_view")
    run_id = view.get("run_id")
    try:
        if not isinstance(run_id, str) or str(uuid.UUID(run_id)) != run_id:
            _fail("invalid_view")
    except (ValueError, AttributeError, TypeError):
        _fail("invalid_view")
    task_id = view.get("task_id")
    if not isinstance(task_id, str) or not _TASK.fullmatch(task_id):
        _fail("invalid_view")
    base_sha = _sha(view.get("base_sha"), 40, "invalid_view")
    _sha(view.get("contract_digest"), 64, "invalid_view")
    diff_digest = _sha(view.get("diff_digest"), 64, "invalid_view")
    if view.get("repository") != repo.slug:
        _fail("repository_mismatch")
    worktree = view.get("worktree")
    if not isinstance(worktree, str) or not worktree:
        _fail("invalid_view")
    manifest = _manifest(view.get("manifest"))
    if repo.diff_digest(manifest) != diff_digest:
        _fail("view_mismatch")
    return run_id, task_id, base_sha, diff_digest, worktree, manifest


class GitHubDelivery:
    """Publish a verified manifest and explicitly merge the resulting PR."""

    def __init__(self, repo):
        if not isinstance(repo, repository.GitRepo):
            _fail("invalid_repository")
        if not isinstance(repo.slug, str) or not _SLUG.fullmatch(repo.slug):
            _fail("invalid_repository")
        self.repo = repo

    # -- fixed, bounded GitHub calls -------------------------------------
    def _gh(self, arguments, *, stdin_text=None, operation):
        executable = _gh_executable()
        completed = _run(
            [executable, *arguments], cwd=self.repo.root,
            stdin_text=stdin_text, operation=operation,
        )
        if completed.returncode != 0:
            _fail(f"{operation}_failed")
        return completed.stdout

    def _gh_json(self, arguments, operation):
        return _json(self._gh(arguments, operation=operation), operation)

    def _git(self, root, arguments, operation):
        try:
            return repository._run_git(root, arguments, operation=operation)
        except repository.ControllerError:
            _fail(f"{operation}_failed")

    def _decode(self, value, operation):
        try:
            return value.decode("utf-8")
        except (AttributeError, UnicodeError):
            _fail(f"{operation}_invalid")

    # -- publication ------------------------------------------------------
    def publish(self, view, title, body):
        """Rebuild the verified diff on a clean worktree and open/update its PR."""
        try:
            run_id, task_id, base_sha, diff_digest, source, manifest = _validated_view(
                self.repo, view
            )
            title = _title(title)
            body = _body(body, task_id)
            if self.repo.base() != base_sha:
                _fail("base_changed")
            branch = _branch(task_id, run_id, self.repo.contract(task_id, base_sha=base_sha)["task"]["kind"])
            parent = Path(tempfile.mkdtemp(prefix="ph-delivery-"))
            try:
                os.chmod(parent, 0o700)
            except OSError:
                shutil.rmtree(parent, ignore_errors=True)
                _fail("publication_worktree_unavailable")
            worktree = None
            try:
                # A fresh scratch UUID avoids colliding with the run's own
                # worktree branch; the publication branch stays deterministic.
                worktree = self.repo.create_worktree(
                    task_id, str(uuid.uuid4()), base_sha, str(parent / "publication")
                )
                if (not isinstance(worktree, (str, os.PathLike))
                        or isinstance(worktree, bytes)):
                    _fail("publication_worktree_unavailable")
                worktree = Path(worktree)
                self.repo.apply_manifest(source, str(worktree), manifest)
                rebuilt = self.repo.manifest(
                    str(worktree), base_sha, [row["path"] for row in manifest]
                )
                if rebuilt != manifest or self.repo.diff_digest(rebuilt) != diff_digest:
                    _fail("rebuilt_manifest_mismatch")
                self._require_artifacts(worktree, task_id)
                head_sha = self._stage_and_publish(
                    worktree, branch, run_id, task_id, base_sha, diff_digest, manifest
                )
                return self._upsert_pr(
                    task_id, branch, base_sha, head_sha, title, body
                )
            finally:
                if worktree is not None:
                    self._cleanup(worktree, parent)
                else:
                    shutil.rmtree(parent, ignore_errors=True)
        except DeliveryError:
            raise
        except repository.ControllerError:
            _fail("publication_failed")
        except OSError:
            _fail("publication_failed")

    def _require_artifacts(self, worktree, task_id):
        for relative in (
            f"project/reports/{task_id}.json",
            f"project/handoffs/{task_id}.md",
        ):
            try:
                metadata = os.lstat(worktree / relative)
            except OSError:
                _fail("delivery_artifacts_missing")
            if not stat.S_ISREG(metadata.st_mode):
                _fail("delivery_artifacts_missing")

    def _cleanup(self, worktree, parent):
        try:
            repository._run_git(
                self.repo.root, ["worktree", "remove", "--force", str(worktree)],
                operation="worktree_cleanup",
            )
        except repository.ControllerError:
            pass
        shutil.rmtree(parent, ignore_errors=True)

    def _stage_and_publish(self, worktree, branch, run_id, task_id, base_sha,
                           diff_digest, manifest):
        paths = [row["path"] for row in manifest]
        chunk = []
        size = 0
        for path in paths:
            if chunk and (len(chunk) >= _ADD_CHUNK or size + len(path) > 65536):
                self._git(worktree, ["add", "-A", "--", *chunk], "add")
                chunk = []
                size = 0
            chunk.append(path)
            size += len(path)
        if chunk:
            self._git(worktree, ["add", "-A", "--", *chunk], "add")
        staged = self._decode(
            self._git(worktree, ["diff", "--cached", "--no-renames", "--name-only", "-z"], "staged"),
            "staged",
        ).split("\x00")
        if set(staged) - {""} != set(paths) or len(staged) - 1 != len(paths):
            _fail("staged_manifest_mismatch")
        tree = self._decode(
            self._git(worktree, ["write-tree"], "tree_write"), "tree_write"
        ).strip()
        _sha(tree, 40, "tree_write_failed")
        remote = self._remote(branch, base_sha)
        if remote is not None:
            if remote["tree"] != tree:
                _fail("publication_conflict")
            return remote["sha"]
        message = (
            f"{task_id}: publish verified delivery\n\n"
            f"run: {run_id}\nbase: {base_sha}\ndiff: {diff_digest}"
        )
        self._git(
            worktree,
            ["-c", "user.name=Project Hub Delivery",
             "-c", "user.email=local-team@users.noreply.github.com",
             "-c", "commit.gpgSign=false",
             "commit", "--no-verify", "--no-gpg-sign", "-m", message],
            "commit",
        )
        committed = self._decode(
            self._git(worktree, ["rev-parse", "HEAD^{tree}"], "commit_read"),
            "commit_read",
        ).strip()
        if committed != tree:
            _fail("commit_mismatch")
        head_sha = self._decode(
            self._git(worktree, ["rev-parse", "HEAD"], "commit_read"), "commit_read"
        ).strip()
        _sha(head_sha, 40, "commit_read_failed")
        self._git(
            worktree, ["push", "--porcelain", "origin", f"HEAD:refs/heads/{branch}"],
            "push",
        )
        return head_sha

    def _remote(self, branch, base_sha):
        ref = f"refs/heads/{branch}"
        output = self._decode(
            self._git(
                self.repo.root, ["ls-remote", "--heads", "origin", ref], "remote_branch"
            ),
            "remote_branch",
        )
        lines = [line for line in output.splitlines() if line.strip()]
        if not lines:
            return None
        if len(lines) != 1:
            _fail("publication_conflict")
        parts = lines[0].split("\t")
        if len(parts) != 2 or parts[1] != ref:
            _fail("publication_conflict")
        sha = parts[0]
        _sha(sha, 40, "publication_conflict")
        temporary = f"refs/ph-delivery/{uuid.uuid4().hex}"
        try:
            self._git(
                self.repo.root,
                ["fetch", "--no-tags", "origin", f"{ref}:{temporary}"],
                "remote_fetch",
            )
            tree = self._decode(
                self._git(
                    self.repo.root, ["rev-parse", f"{temporary}^{{tree}}"], "remote_read"
                ),
                "remote_read",
            ).strip()
            _sha(tree, 40, "publication_conflict")
            try:
                self._git(
                    self.repo.root,
                    ["merge-base", "--is-ancestor", base_sha, sha], "remote_base"
                )
            except DeliveryError:
                _fail("publication_conflict")
            return {"sha": sha, "tree": tree}
        finally:
            try:
                repository._run_git(
                    self.repo.root, ["update-ref", "-d", temporary],
                    operation="remote_cleanup",
                )
            except repository.ControllerError:
                pass

    # -- pull requests ----------------------------------------------------
    def _pr_list(self, branch):
        data = self._gh_json(
            ["pr", "list", "--repo", self.repo.slug, "--head", branch,
             "--state", "all", "--limit", "20", "--json",
             "number,url,state,headRefName,headRefOid,baseRefName,isDraft"],
            "pr_list",
        )
        if not isinstance(data, list) or len(data) > 20:
            _fail("pr_list_invalid")
        for row in data:
            if not isinstance(row, dict):
                _fail("pr_list_invalid")
        return data

    def _pr(self, number, operation="pr_view"):
        data = self._gh_json(
            ["pr", "view", str(number), "--repo", self.repo.slug, "--json",
             "number,url,state,headRefName,headRefOid,baseRefName,baseRefOid,"
             "isDraft,mergeCommit"],
            operation,
        )
        if not isinstance(data, dict) or data.get("number") != number:
            _fail(f"{operation}_invalid")
        url = data.get("url")
        if not isinstance(url, str) or not url.startswith(
                f"https://github.com/{self.repo.slug}/pull/{number}"):
            _fail(f"{operation}_invalid")
        return data

    def _result(self, number, url, branch, base_sha, head_sha, status, **extra):
        result = {
            "pr": number,
            "number": number,
            "url": url,
            "head": head_sha,
            "base": base_sha,
            "base_ref": _MAIN,
            "branch": branch,
            "repository": self.repo.slug,
            "status": status,
        }
        result.update(extra)
        return result

    def _row(self, row, branch, head_sha):
        if (row.get("headRefName") != branch or row.get("headRefOid") != head_sha
                or row.get("baseRefName") != _MAIN):
            _fail("pr_head_changed")
        return row

    def _upsert_pr(self, task_id, branch, base_sha, head_sha, title, body):
        rows = self._pr_list(branch)
        opened = [row for row in rows if row.get("state") == "OPEN"]
        if len(opened) > 1:
            _fail("pr_ambiguous")
        if opened:
            row = self._row(opened[0], branch, head_sha)
            number = row.get("number")
            if type(number) is not int or number < 1:
                _fail("pr_list_invalid")
            self._gh(
                ["pr", "edit", str(number), "--repo", self.repo.slug,
                 "--title", title, "--body-file", "-"],
                stdin_text=body, operation="pr_edit",
            )
            return self._result(
                number, row.get("url"), branch, base_sha, head_sha, "updated",
                task_id=task_id,
            )
        merged = [row for row in rows if row.get("state") == "MERGED"]
        if merged:
            if len(merged) != 1:
                _fail("pr_ambiguous")
            row = self._row(merged[0], branch, head_sha)
            number = row.get("number")
            if type(number) is not int or number < 1:
                _fail("pr_list_invalid")
            return self._result(
                number, row.get("url"), branch, base_sha, head_sha, "merged",
                task_id=task_id,
            )
        if any(row.get("state") == "CLOSED" for row in rows):
            _fail("pr_closed")
        output = self._gh(
            ["pr", "create", "--repo", self.repo.slug, "--base", _MAIN,
             "--head", branch, "--title", title, "--body-file", "-"],
            stdin_text=body, operation="pr_create",
        )
        match = re.search(
            rf"https://github\.com/{re.escape(self.repo.slug)}/pull/([0-9]+)", output
        )
        if match is None:
            _fail("pr_create_invalid")
        number = int(match.group(1))
        if number < 1:
            _fail("pr_create_invalid")
        url = match.group(0)
        return self._result(
            number, url, branch, base_sha, head_sha, "created", task_id=task_id
        )

    # -- explicit merge gate ---------------------------------------------
    def merge(self, pr, base_sha, head_sha):
        """Merge an existing open PR only after rechecking every remote gate."""
        try:
            if not isinstance(pr, dict):
                _fail("invalid_pr")
            base_sha = _sha(base_sha, 40, "invalid_base")
            head_sha = _sha(head_sha, 40, "invalid_head")
            number = pr.get("pr", pr.get("number"))
            if type(number) is not int or number < 1:
                _fail("invalid_pr")
            if pr.get("head") is not None and pr.get("head") != head_sha:
                _fail("pr_head_mismatch")
            if pr.get("base") is not None and pr.get("base") != base_sha:
                _fail("pr_base_mismatch")
            branch = pr.get("branch")
            if branch is not None and (
                    not isinstance(branch, str) or not _BRANCH.fullmatch(branch)
                    or ".." in branch or "//" in branch or branch.endswith("/")):
                _fail("invalid_pr")
            self.repo.refresh()
            if self.repo.base() != base_sha:
                _fail("base_changed")
            current = self._pr(number)
            branch = self._open(current, branch, head_sha)
            protection = self._protection()
            checks = self._checks(head_sha, protection)
            self._threads(number)
            # Final immediate recheck: BASE, PR identity and head must still
            # be the exact state that just passed verification.
            self.repo.refresh()
            if self.repo.base() != base_sha:
                _fail("base_changed")
            self._open(self._pr(number), branch, head_sha)
            self._gh(
                ["pr", "merge", str(number), "--repo", self.repo.slug,
                 "--squash", "--match-head-commit", head_sha],
                operation="pr_merge",
            )
            merged = self._pr(number, operation="merge_verify")
            if merged.get("state") != "MERGED" or merged.get("headRefOid") != head_sha:
                _fail("merge_unconfirmed")
            commit = merged.get("mergeCommit")
            commit_sha = commit.get("oid") if isinstance(commit, dict) else None
            return self._result(
                number, merged.get("url"), branch, base_sha, head_sha, "merged",
                checks=checks, merge_commit=commit_sha,
            )
        except DeliveryError:
            raise
        except repository.ControllerError:
            _fail("merge_failed")
        except OSError:
            _fail("merge_failed")

    def _open(self, data, expected_branch, head_sha):
        if data.get("state") != "OPEN":
            _fail("pr_not_open")
        if data.get("headRefOid") != head_sha:
            _fail("pr_head_changed")
        if data.get("baseRefName") != _MAIN:
            _fail("pr_base_changed")
        if data.get("isDraft") is True:
            _fail("pr_draft")
        name = data.get("headRefName")
        if not isinstance(name, str) or not _BRANCH.fullmatch(name):
            _fail("pr_head_changed")
        if expected_branch is not None and name != expected_branch:
            _fail("pr_head_changed")
        return name

    def _protection(self):
        data = self._gh_json(
            ["api", "--hostname", "github.com",
             f"repos/{self.repo.slug}/branches/{_MAIN}/protection"],
            "protection",
        )
        if not isinstance(data, dict):
            _fail("protection_unavailable")
        required = data.get("required_status_checks")
        if not isinstance(required, dict) or required.get("strict") is not True:
            _fail("protection_strict_missing")
        rows = required.get("checks")
        if not isinstance(rows, list) or len(rows) != len(_REQUIRED_CHECKS):
            _fail("protection_checks_mismatch")
        mapping = {}
        for row in rows:
            if not isinstance(row, dict):
                _fail("protection_checks_mismatch")
            name = row.get("context")
            app_id = row.get("app_id")
            if not isinstance(name, str) or type(app_id) is not int or name in mapping:
                _fail("protection_checks_mismatch")
            mapping[name] = app_id
        if set(mapping) != _REQUIRED_CHECKS:
            _fail("protection_checks_mismatch")
        return mapping

    def _checks(self, head_sha, protection):
        rows = []
        total = None
        page = 1
        while True:
            if page > 50:
                _fail("checks_incomplete")
            data = self._gh_json(
                ["api", "--hostname", "github.com",
                 f"repos/{self.repo.slug}/commits/{head_sha}/check-runs"
                 f"?per_page=100&page={page}"],
                "checks",
            )
            if not isinstance(data, dict):
                _fail("checks_unavailable")
            count = data.get("total_count")
            page_rows = data.get("check_runs")
            if type(count) is not int or count < 0 or not isinstance(page_rows, list):
                _fail("checks_unavailable")
            if total is None:
                total = count
            elif total != count:
                _fail("checks_incomplete")
            rows.extend(page_rows)
            if len(rows) >= total:
                break
            if len(page_rows) < 100:
                _fail("checks_incomplete")
            page += 1
        if len(rows) != total:
            _fail("checks_incomplete")
        latest = {}
        for row in rows:
            if not isinstance(row, dict):
                _fail("checks_unavailable")
            if row.get("head_sha") != head_sha:
                continue
            name = row.get("name")
            app = row.get("app")
            app_id = app.get("id") if isinstance(app, dict) else None
            if not isinstance(name, str) or type(app_id) is not int:
                _fail("checks_unavailable")
            key = (name, app_id)
            if name not in protection or protection[name] != app_id:
                continue
            identity = row.get("id")
            if type(identity) is not int:
                _fail("checks_unavailable")
            if key not in latest or identity > latest[key]["id"]:
                latest[key] = {
                    "id": identity,
                    "name": name,
                    "app_id": app_id,
                    "status": row.get("status"),
                    "conclusion": row.get("conclusion"),
                }
        summary = []
        for name in sorted(_REQUIRED_CHECKS):
            key = (name, protection[name])
            row = latest.get(key)
            if row is None:
                _fail("required_check_missing")
            if row["status"] != "completed" or row["conclusion"] != "success":
                _fail("required_check_not_successful")
            summary.append({
                "name": name,
                "app_id": row["app_id"],
                "status": row["status"],
                "conclusion": row["conclusion"],
            })
        return summary

    def _threads(self, number):
        owner, name = self.repo.slug.split("/", 1)
        cursor = None
        for _page in range(100):
            arguments = [
                "api", "graphql", "-f", f"query={_REVIEW_QUERY}",
                "-F", f"owner={owner}", "-F", f"name={name}",
                "-F", f"number={number}",
            ]
            if cursor is not None:
                arguments += ["-F", f"cursor={cursor}"]
            data = self._gh_json(arguments, "review_threads")
            if not isinstance(data, dict) or data.get("errors"):
                _fail("review_threads_unavailable")
            payload = data.get("data")
            repository_data = payload.get("repository") if isinstance(payload, dict) else None
            pull = repository_data.get("pullRequest") if isinstance(repository_data, dict) else None
            threads = pull.get("reviewThreads") if isinstance(pull, dict) else None
            if not isinstance(threads, dict):
                _fail("review_threads_unavailable")
            nodes = threads.get("nodes")
            info = threads.get("pageInfo")
            if not isinstance(nodes, list) or not isinstance(info, dict):
                _fail("review_threads_unavailable")
            for node in nodes:
                if not isinstance(node, dict) or not isinstance(node.get("isResolved"), bool):
                    _fail("review_threads_unavailable")
                if node["isResolved"] is False:
                    _fail("unresolved_review_thread")
            next_page = info.get("hasNextPage")
            if not isinstance(next_page, bool):
                _fail("review_threads_unavailable")
            if not next_page:
                return
            cursor = info.get("endCursor")
            if not isinstance(cursor, str) or not cursor:
                _fail("review_threads_unavailable")
        _fail("review_threads_incomplete")
