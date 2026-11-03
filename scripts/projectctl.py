#!/usr/bin/env python3
"""Read-only cross-agent project contract checks.

This CLI intentionally uses only the Python standard library. It validates the
project contract and evaluates committed reports; it does not execute task
acceptance commands, update task state, or modify Git state.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit


MANIFEST_PATH = "project/manifest.json"
TASKS_PATH = "project/tasks.json"
QUEUE_PATH = "project/QUEUE.md"
STATUS_PATH = "project/STATUS.md"
ACCEPTANCE_PATH = "project/ACCEPTANCE.md"
DECISIONS_PATH = "project/DECISIONS.md"
LIGHT_PROTOCOL_PATH = "docs/engineering/light-delivery.md"
TASK_ID_RE = re.compile(r"^PH-[0-9]{3,6}$")
QUEUE_STATUSES = ("待办", "进行中", "已交付")
QUEUE_WAVE_RE = re.compile(r"^W[1-9][0-9]?$")
REQUIREMENT_ID_RE = re.compile(r"^V1-[A-Z]{2,4}-[0-9]{2}$")
QUEUE_MIN_CHECKS = 3
QUEUE_MAX_CHECKS = 5
MANIFEST_FIELDS = (
    "schema_version",
    "name",
    "repository_url",
    "version_source",
    "current_iteration",
    "tasks_file",
    "active_handoff",
    "required_docs",
)
TASK_FIELDS = (
    "id",
    "title",
    "kind",
    "risk",
    "status",
    "priority",
    "milestone",
    "depends_on",
    "spec",
    "allowed_paths",
    "acceptance",
    "github_issue",
    "owner",
    "blockers",
    "handoff",
)
KINDS = {"feature", "fix", "infra", "docs", "research"}
RISKS = {"S", "A", "B"}
STATUSES = {
    "proposed",
    "ready",
    "in_progress",
    "in_review",
    "blocked",
    "done",
    "cancelled",
}
REPORT_TEST_RESULTS = {"pass", "fail", "not_run"}
REPORT_REVIEW_STATUSES = {"pass", "pending", "not_required"}
CRITICAL_PATHS = {
    "core/models.py",
    "core/services.py",
    "core/access.py",
    "core/api.py",
    "core/agent_auth.py",
    "core/plugin_runtime.py",
    "core/views.py",
    "core/forms.py",
    "core/imports.py",
    "core/ai.py",
    "pmo/settings.py",
    "pmo/urls.py",
    "AGENTS.md",
    "project/tasks.json",
    "project/manifest.json",
    "scripts/projectctl.py",
    "scripts/check_pr.py",
    "scripts/public_hygiene.py",
    "scripts/release_check.py",
    "scripts/backup.sh",
}

# Accept the normal PEP 440 release forms used by pyproject files without
# pulling in a non-standard packaging dependency.
PEP440_RE = re.compile(
    r"""
    ^v?
    (?:(?P<epoch>[0-9]+)!)?
    (?P<release>[0-9]+(?:\.[0-9]+)*)
    (?P<pre>[-_.]?(?:a|b|c|rc|alpha|beta|pre|preview)[-_.]?[0-9]*)?
    (?P<post>(?:-[0-9]+)|(?:[-_.]?(?:post|rev|r)[-_.]?[0-9]*))?
    (?P<dev>[-_.]?dev[-_.]?[0-9]*)?
    (?:\+(?P<local>[a-z0-9]+(?:[-_.][a-z0-9]+)*))?
    $
    """,
    re.IGNORECASE | re.VERBOSE,
)


class CliError(Exception):
    """A user-facing command error represented as machine-readable JSON."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str | None = None,
        errors: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.errors = errors

    def payload(self) -> list[dict[str, Any]]:
        if self.errors is not None:
            return self.errors
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.path is not None:
            error["path"] = self.path
        return [error]


class JsonArgumentParser(argparse.ArgumentParser):
    """Turn argparse failures into the same JSON error envelope as commands."""

    def error(self, message: str) -> None:
        raise CliError("usage_error", message)


def issue(code: str, message: str, path: str | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"code": code, "message": message}
    if path is not None:
        item["path"] = path
    return item


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _safe_relative_path(
    value: Any,
    path: str,
    issues: list[dict[str, Any]],
    *,
    markdown: bool,
) -> bool:
    if not _is_nonempty_string(value):
        issues.append(issue("invalid_path", "must be a non-empty relative path", path))
        return False
    if "\x00" in value or "\\" in value:
        issues.append(
            issue(
                "invalid_path",
                "must use forward slashes and must not contain NUL bytes",
                path,
            )
        )
        return False
    if re.match(r"^[A-Za-z]:", value):
        issues.append(issue("absolute_path", "absolute paths are not allowed", path))
        return False
    candidate = PurePosixPath(value)
    if candidate.is_absolute():
        issues.append(issue("absolute_path", "absolute paths are not allowed", path))
        return False
    if any(part == ".." for part in candidate.parts):
        issues.append(issue("parent_path", "'..' path segments are not allowed", path))
        return False
    if not candidate.parts or any(part in {"", "."} for part in candidate.parts):
        issues.append(issue("invalid_path", "path must identify a file", path))
        return False
    if markdown and candidate.suffix.lower() != ".md":
        issues.append(issue("invalid_path", "path must end in .md", path))
        return False
    return True


def _existing_file(root: Path, relative: str) -> bool:
    try:
        candidate = (root / relative).resolve()
        candidate.relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return candidate.is_file()


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise CliError("file_not_found", "file does not exist", path=str(path)) from exc
    except PermissionError as exc:
        raise CliError("permission_denied", "file cannot be read", path=str(path)) from exc
    except json.JSONDecodeError as exc:
        raise CliError(
            "invalid_json",
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
            path=str(path),
        ) from exc
    except OSError as exc:
        raise CliError("read_error", str(exc), path=str(path)) from exc


def _read_root_json(root: Path, relative: str) -> Any:
    return _read_json(root / relative)


def _validate_manifest(
    root: Path,
    *,
    check_files: bool = True,
    check_version: bool = True,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    try:
        data = _read_root_json(root, MANIFEST_PATH)
    except CliError as exc:
        return None, exc.payload()
    if not isinstance(data, dict):
        return None, [issue("invalid_type", "manifest root must be an object", MANIFEST_PATH)]

    for field in MANIFEST_FIELDS:
        if field not in data:
            issues.append(issue("required", "required field is missing", f"manifest.{field}"))

    if data.get("schema_version") != 1 or not _is_int(data.get("schema_version")):
        issues.append(
            issue(
                "schema_version",
                "schema_version must be the integer 1",
                "manifest.schema_version",
            )
        )
    for field in ("name", "repository_url", "current_iteration"):
        if not _is_nonempty_string(data.get(field)):
            issues.append(
                issue("invalid_value", "must be a non-empty string", f"manifest.{field}")
            )
    if data.get("version_source") != "pyproject.toml":
        issues.append(
            issue(
                "invalid_value",
                "version_source must be pyproject.toml",
                "manifest.version_source",
            )
        )
    if data.get("tasks_file") != TASKS_PATH:
        issues.append(
            issue(
                "invalid_value",
                f"tasks_file must be {TASKS_PATH}",
                "manifest.tasks_file",
            )
        )

    active_handoff = data.get("active_handoff")
    if active_handoff is not None:
        valid_handoff = _safe_relative_path(
            active_handoff,
            "manifest.active_handoff",
            issues,
            markdown=True,
        )
        if check_files and valid_handoff and not _existing_file(root, active_handoff):
            issues.append(issue("file_not_found", "active handoff does not exist", "manifest.active_handoff"))

    required_docs = data.get("required_docs")
    if not isinstance(required_docs, list):
        issues.append(
            issue("invalid_type", "required_docs must be an array", "manifest.required_docs")
        )
    else:
        for index, document in enumerate(required_docs):
            field = f"manifest.required_docs[{index}]"
            valid = _safe_relative_path(document, field, issues, markdown=True)
            if check_files and valid and not _existing_file(root, document):
                issues.append(
                    issue("file_not_found", "required document does not exist", field)
                )

    if check_version:
        issues.extend(_validate_pyproject_version(root))
    return data, issues


def _validate_pyproject_version(root: Path) -> list[dict[str, Any]]:
    path = root / "pyproject.toml"
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:
        return [issue("file_not_found", "pyproject.toml does not exist", "pyproject.toml")]
    except PermissionError:
        return [issue("permission_denied", "pyproject.toml cannot be read", "pyproject.toml")]
    except tomllib.TOMLDecodeError as exc:
        return [issue("invalid_toml", f"invalid TOML: {exc}", "pyproject.toml")]
    except OSError as exc:
        return [issue("read_error", str(exc), "pyproject.toml")]

    project = data.get("project")
    if not isinstance(project, dict):
        return [
            issue(
                "pyproject_version",
                "pyproject.toml must contain a [project] table",
                "pyproject.toml",
            )
        ]
    version = project.get("version")
    if not _is_nonempty_string(version):
        return [
            issue(
                "pyproject_version",
                "[project].version must be a non-empty string",
                "pyproject.toml",
            )
        ]
    if PEP440_RE.fullmatch(version) is None:
        return [
            issue(
                "pyproject_version",
                f"invalid PEP 440 version: {version!r}",
                "pyproject.toml",
            )
        ]
    return []


def _validate_task(
    task: Any,
    index: int,
    root: Path | None,
    issues: list[dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    field_root = f"tasks.tasks[{index}]"
    if not isinstance(task, dict):
        issues.append(issue("invalid_type", "task must be an object", field_root))
        return None, None

    for field in TASK_FIELDS:
        if field not in task:
            issues.append(issue("required", "required field is missing", f"{field_root}.{field}"))

    task_id = task.get("id")
    valid_id = isinstance(task_id, str) and TASK_ID_RE.fullmatch(task_id) is not None
    if not valid_id:
        issues.append(
            issue("task_id", "task id must match PH- followed by 3 to 6 digits", f"{field_root}.id")
        )

    if not _is_nonempty_string(task.get("title")):
        issues.append(issue("invalid_value", "must be a non-empty string", f"{field_root}.title"))
    if task.get("kind") not in KINDS:
        issues.append(
            issue(
                "enum",
                f"kind must be one of {sorted(KINDS)}",
                f"{field_root}.kind",
            )
        )
    if task.get("risk") not in RISKS:
        issues.append(
            issue(
                "enum",
                f"risk must be one of {sorted(RISKS)}",
                f"{field_root}.risk",
            )
        )
    if task.get("status") not in STATUSES:
        issues.append(
            issue(
                "enum",
                f"status must be one of {sorted(STATUSES)}",
                f"{field_root}.status",
            )
        )
    priority = task.get("priority")
    if not _is_int(priority) or priority not in {1, 2, 3}:
        issues.append(
            issue("invalid_value", "priority must be an integer from 1 to 3", f"{field_root}.priority")
        )
    if not _is_nonempty_string(task.get("milestone")):
        issues.append(
            issue("invalid_value", "must be a non-empty string", f"{field_root}.milestone")
        )

    dependencies = task.get("depends_on")
    if not isinstance(dependencies, list):
        issues.append(
            issue("invalid_type", "depends_on must be an array", f"{field_root}.depends_on")
        )
    else:
        for dependency_index, dependency in enumerate(dependencies):
            if not isinstance(dependency, str) or TASK_ID_RE.fullmatch(dependency) is None:
                issues.append(
                    issue(
                        "task_id",
                        "dependency id must match PH- followed by 3 to 6 digits",
                        f"{field_root}.depends_on[{dependency_index}]",
                    )
                )

    spec = task.get("spec")
    spec_valid = _safe_relative_path(spec, f"{field_root}.spec", issues, markdown=True)
    if root is not None and spec_valid and not _existing_file(root, spec):
        issues.append(issue("file_not_found", "spec file does not exist", f"{field_root}.spec"))

    allowed_paths = task.get("allowed_paths")
    if not isinstance(allowed_paths, list):
        issues.append(
            issue("invalid_type", "allowed_paths must be an array", f"{field_root}.allowed_paths")
        )
    else:
        for allowed_index, allowed_path in enumerate(allowed_paths):
            _safe_relative_path(
                allowed_path,
                f"{field_root}.allowed_paths[{allowed_index}]",
                issues,
                markdown=False,
            )

    acceptance = task.get("acceptance")
    if not _string_list(acceptance):
        issues.append(
            issue(
                "invalid_type",
                "acceptance must be an array of command strings",
                f"{field_root}.acceptance",
            )
        )

    github_issue = task.get("github_issue")
    if github_issue is not None:
        parsed_url = urlsplit(github_issue) if isinstance(github_issue, str) else None
        if (
            parsed_url is None
            or parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            issues.append(
                issue(
                    "invalid_url",
                    "github_issue must be null or an http(s) URL without credentials",
                    f"{field_root}.github_issue",
                )
            )

    owner = task.get("owner")
    if owner is not None and not _is_nonempty_string(owner):
        issues.append(
            issue("invalid_value", "owner must be null or a non-empty string", f"{field_root}.owner")
        )

    blockers = task.get("blockers")
    if not _string_list(blockers):
        issues.append(
            issue("invalid_type", "blockers must be an array of strings", f"{field_root}.blockers")
        )

    handoff = task.get("handoff")
    handoff_valid = True
    if handoff is not None:
        handoff_valid = _safe_relative_path(
            handoff,
            f"{field_root}.handoff",
            issues,
            markdown=True,
        )

    status = task.get("status")
    if status == "blocked" and (not isinstance(blockers, list) or not blockers):
        issues.append(
            issue(
                "blocked_without_blockers",
                "blocked tasks must list at least one blocker",
                f"{field_root}.blockers",
            )
        )
    if status in {"in_progress", "in_review"} and not _is_nonempty_string(owner):
        issues.append(
            issue(
                "owner_required",
                f"{status} tasks must have an owner",
                f"{field_root}.owner",
            )
        )
    if status in {"ready", "in_progress", "in_review", "done"}:
        if not allowed_paths:
            issues.append(issue("scope_required", "executable tasks require non-empty allowed_paths", f"{field_root}.allowed_paths"))
        if not acceptance or not all(_is_nonempty_string(command) for command in acceptance):
            issues.append(issue("acceptance_required", "executable tasks require acceptance commands", f"{field_root}.acceptance"))
    if status in {"in_review", "done"} and handoff is None:
        issues.append(
            issue(
                "handoff_required",
                f"{status} tasks must have a handoff",
                f"{field_root}.handoff",
            )
        )
    if (
        root is not None
        and status in {"in_review", "done"}
        and handoff is not None
        and handoff_valid
        and not _existing_file(root, handoff)
    ):
        issues.append(issue("file_not_found", "handoff file does not exist", f"{field_root}.handoff"))

    return task_id if valid_id else None, task


def _validate_dependencies(
    tasks: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> None:
    by_id: dict[str, dict[str, Any]] = {}
    duplicates: set[str] = set()
    for task in tasks:
        task_id = task.get("id")
        if isinstance(task_id, str) and TASK_ID_RE.fullmatch(task_id):
            if task_id in by_id:
                duplicates.add(task_id)
            else:
                by_id[task_id] = task
    for task_id in sorted(duplicates):
        issues.append(issue("duplicate_task_id", f"duplicate task id: {task_id}", "tasks.tasks"))

    graph: dict[str, list[str]] = {}
    for task_id, task in by_id.items():
        dependencies = task.get("depends_on")
        valid_dependencies: list[str] = []
        if isinstance(dependencies, list):
            for dependency in dependencies:
                if not isinstance(dependency, str):
                    continue
                if dependency not in by_id:
                    issues.append(
                        issue(
                            "dependency_not_found",
                            f"{task_id} depends on missing task {dependency}",
                            f"task.{task_id}.depends_on",
                        )
                    )
                else:
                    valid_dependencies.append(dependency)
                    dependency_status = by_id[dependency].get("status")
                    if (
                        task.get("status") in {"ready", "in_progress", "in_review", "done"}
                        and dependency_status != "done"
                    ):
                        issues.append(
                            issue(
                                "dependency_not_done",
                                f"{task_id} requires {dependency} to be done",
                                f"task.{task_id}.depends_on",
                            )
                        )
        graph[task_id] = valid_dependencies

    states: dict[str, int] = {}
    stack: list[str] = []
    reported_cycles: set[frozenset[str]] = set()

    def visit(task_id: str) -> None:
        states[task_id] = 1
        stack.append(task_id)
        for dependency in graph.get(task_id, []):
            if states.get(dependency, 0) == 0:
                visit(dependency)
            elif states.get(dependency) == 1:
                start = stack.index(dependency)
                cycle = stack[start:] + [dependency]
                signature = frozenset(cycle)
                if signature not in reported_cycles:
                    reported_cycles.add(signature)
                    issues.append(
                        issue(
                            "dependency_cycle",
                            "dependency cycle: " + " -> ".join(cycle),
                            "tasks.tasks",
                        )
                    )
        stack.pop()
        states[task_id] = 2

    for task_id in sorted(graph):
        if states.get(task_id, 0) == 0:
            visit(task_id)


def _validate_tasks_data(
    data: Any,
    root: Path | None,
    *,
    check_files: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    issues: list[dict[str, Any]] = []
    if not isinstance(data, dict):
        return [], [issue("invalid_type", "tasks root must be an object", "tasks.json")]
    if data.get("schema_version") != 1 or not _is_int(data.get("schema_version")):
        issues.append(
            issue(
                "schema_version",
                "schema_version must be the integer 1",
                "tasks.schema_version",
            )
        )
    raw_tasks = data.get("tasks")
    if not isinstance(raw_tasks, list):
        issues.append(issue("invalid_type", "tasks must be an array", "tasks.tasks"))
        return [], issues

    tasks: list[dict[str, Any]] = []
    for index, task in enumerate(raw_tasks):
        _, validated = _validate_task(task, index, root if check_files else None, issues)
        if validated is not None:
            tasks.append(validated)
    _validate_dependencies(tasks, issues)
    return tasks, issues


def _load_and_validate(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not root.is_dir():
        raise CliError("root_not_found", "root is not a directory", path=str(root))
    manifest, manifest_issues = _validate_manifest(root)
    tasks_data = _read_root_json(root, TASKS_PATH)
    tasks, task_issues = _validate_tasks_data(tasks_data, root, check_files=True)
    errors = manifest_issues + task_issues
    for task in tasks:
        if task.get("status") == "done":
            try:
                report = _read_root_json(root, f"project/reports/{task['id']}.json")
                errors.extend(_validate_report(report, task['id'], task.get('risk')))
            except CliError as exc:
                errors.extend(exc.payload())
    if errors:
        raise CliError(
            "validation_failed",
            f"project contract has {len(errors)} validation error(s)",
            errors=errors,
        )
    assert manifest is not None
    return manifest, tasks


def _queue_item_blocks(text: str) -> list[tuple[str, list[str]]]:
    """Split QUEUE.md into (item id, block lines) pairs keyed by '### Q-nn' headings."""
    blocks: list[tuple[str, list[str]]] = []
    current_id: str | None = None
    current: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^###\s+(Q-[0-9]{2,3})\b", line)
        if heading:
            if current_id is not None:
                blocks.append((current_id, current))
            current_id = heading.group(1)
            current = []
            continue
        if line.startswith("## ") and current_id is not None:
            blocks.append((current_id, current))
            current_id, current = None, []
            continue
        if current_id is not None:
            current.append(line)
    if current_id is not None:
        blocks.append((current_id, current))
    return blocks


def _queue_block_values(lines: list[str]) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    checks: list[str] = []
    key: str | None = None
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            continue
        bullet = re.match(r"^[-*]\s*([^：:]+)[：:]\s*(.*)$", line.strip())
        if bullet:
            key = bullet.group(1).strip()
            values[key] = bullet.group(2).strip()
            continue
        numbered = re.match(r"^\s*(?:[0-9]+[.)]|[-*])\s+(.*\S)\s*$", line)
        if numbered and key == "验收检查":
            checks.append(numbered.group(1).strip())
    return values, checks


def _split_ids(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"[,，、\s]+", value or "") if item.strip()]


def parse_queue(root: Path, tasks: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    path = root / QUEUE_PATH
    if not path.is_file():
        return {"path": QUEUE_PATH, "available": False, "items": [], "issues": [],
                "item_count": 0, "waves": {}}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {"path": QUEUE_PATH, "available": False, "items": [], "issues": [
            issue("read_error", "queue document cannot be read", QUEUE_PATH)],
            "item_count": 0, "waves": {}}
    task_ids = {task.get("id") for task in tasks or [] if isinstance(task, dict)}
    requirement_ids: set[str] | None = None
    trace = root / "docs/archive/v1.0-planning/requirement-trace.json"
    if trace.is_file():
        try:
            requirement_ids = {
                row["requirement_id"] for row in json.loads(trace.read_text(encoding="utf-8")).get("requirements", [])
                if isinstance(row, dict) and isinstance(row.get("requirement_id"), str)
            }
        except (OSError, ValueError, KeyError):
            requirement_ids = None
    items: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()
    last_wave = 0
    for item_id, block in _queue_item_blocks(text):
        field_root = f"{QUEUE_PATH}#{item_id}"
        values, checks = _queue_block_values(block)
        wave = values.get("波次", "")
        status = values.get("状态", "待办")
        outcome = values.get("用户可见成果", "")
        demo = values.get("演示方式", "")
        legacy = _split_ids(values.get("遗留任务", ""))
        requirements = _split_ids(values.get("需求", ""))
        if item_id in seen:
            issues.append(issue("queue_duplicate", "queue item id appears more than once", item_id))
        seen.add(item_id)
        if not QUEUE_WAVE_RE.fullmatch(wave):
            issues.append(issue("queue_wave", "wave must look like W1", field_root))
        else:
            wave_number = int(wave[1:])
            if wave_number < last_wave:
                issues.append(issue("queue_order", "queue items must be ordered by wave", field_root))
            last_wave = max(last_wave, wave_number)
        if status not in QUEUE_STATUSES:
            issues.append(issue("queue_status", "status must be one of " + "/".join(QUEUE_STATUSES), field_root))
        if len(outcome) < 8:
            issues.append(issue("queue_outcome", "user-visible outcome must be a concrete sentence", field_root))
        if not QUEUE_MIN_CHECKS <= len(checks) <= QUEUE_MAX_CHECKS:
            issues.append(issue("queue_checks", f"acceptance checks must number {QUEUE_MIN_CHECKS}-{QUEUE_MAX_CHECKS}", field_root))
        if not demo:
            issues.append(issue("queue_demo", "demo method is required", field_root))
        if not legacy:
            issues.append(issue("queue_legacy", "mapped legacy PH ids are required", field_root))
        for task_id in legacy:
            if TASK_ID_RE.fullmatch(task_id) is None:
                issues.append(issue("queue_legacy", f"invalid legacy task id: {task_id}", field_root))
            elif task_ids and task_id not in task_ids:
                issues.append(issue("queue_legacy", f"legacy task {task_id} is not in {TASKS_PATH}", field_root))
        if not requirements:
            issues.append(issue("queue_requirement", "mapped V1 requirement ids are required", field_root))
        for requirement_id in requirements:
            if REQUIREMENT_ID_RE.fullmatch(requirement_id) is None:
                issues.append(issue("queue_requirement", f"invalid requirement id: {requirement_id}", field_root))
            elif requirement_ids is not None and requirement_id not in requirement_ids:
                issues.append(issue("queue_requirement", f"requirement {requirement_id} is not in the archived trace", field_root))
        items.append({"id": item_id, "wave": wave, "status": status, "outcome": outcome,
                      "checks": checks, "demo": demo, "legacy": legacy,
                      "requirements": requirements})
    if not items:
        issues.append(issue("queue_empty", "queue document defines no items", QUEUE_PATH))
    waves: dict[str, int] = {}
    for item in items:
        waves[item["wave"]] = waves.get(item["wave"], 0) + 1
    return {"path": QUEUE_PATH, "available": True, "items": items, "issues": issues,
            "item_count": len(items), "waves": waves}


def _queue_summary(parsed: dict[str, Any], limit: int = 3) -> dict[str, Any]:
    pending = [item for item in parsed["items"] if item["status"] != "已交付"][:limit]
    return {
        "path": parsed["path"],
        "available": parsed["available"],
        "item_count": parsed["item_count"],
        "waves": parsed["waves"],
        "delivered": sum(1 for item in parsed["items"] if item["status"] == "已交付"),
        "next": [{"id": item["id"], "wave": item["wave"], "status": item["status"],
                  "outcome": item["outcome"]} for item in pending],
        "issues": parsed["issues"],
    }


def command_queue(root: Path, compact: bool = False) -> dict[str, Any]:
    _, tasks = _load_and_validate(root)
    parsed = parse_queue(root, tasks)
    if parsed["issues"]:
        raise CliError(
            "queue_validation_failed",
            f"queue document has {len(parsed['issues'])} validation error(s)",
            errors=parsed["issues"],
        )
    if compact:
        return _queue_summary(parsed, limit=parsed["item_count"])
    payload = _queue_summary(parsed, limit=len(parsed["items"]))
    payload["items"] = [
        {key: item[key] for key in ("id", "wave", "status", "outcome", "demo")}
        for item in parsed["items"]
    ]
    return payload


def command_check(root: Path) -> dict[str, Any]:
    _, tasks = _load_and_validate(root)
    return {"task_count": len(tasks)}


ORIENT_COMPACT_FIELDS = ("id", "title", "kind", "risk", "status", "milestone", "depends_on")


def _ready_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = [
        task
        for task in tasks
        if task.get("status") == "ready"
        and all(
            any(
                candidate.get("id") == dependency and candidate.get("status") == "done"
                for candidate in tasks
            )
            for dependency in task.get("depends_on", [])
        )
    ]
    eligible.sort(key=lambda task: (task["priority"], task["id"]))
    return eligible


def _compact_task(task: dict[str, Any]) -> dict[str, Any]:
    return {field: task.get(field) for field in ORIENT_COMPACT_FIELDS}


def command_next(root: Path, compact: bool = False) -> dict[str, Any]:
    _, tasks = _load_and_validate(root)
    eligible = _ready_tasks(tasks)
    if compact:
        return {"tasks": [_compact_task(task) for task in eligible]}
    return {"tasks": eligible}


def _acceptance_commands(root: Path, agents_relative: str = "AGENTS.md") -> list[str]:
    path = root / agents_relative
    if not path.is_file():
        raise CliError("file_not_found", f"{agents_relative} does not exist", path=agents_relative)
    text = path.read_text(encoding="utf-8")
    section_index = text.find("## 验收与交付")
    if section_index < 0:
        raise CliError(
            "section_not_found",
            "no 验收与交付 section in AGENTS.md",
            path=agents_relative,
        )
    match = re.search(r"```sh\n(.*?)```", text[section_index:], re.DOTALL)
    if match is None:
        raise CliError(
            "acceptance_block_not_found",
            "no sh code block after the 验收与交付 section",
            path=agents_relative,
        )
    commands = [line.strip() for line in match.group(1).splitlines() if line.strip()]
    if not commands:
        raise CliError(
            "acceptance_block_empty",
            "the acceptance block contains no commands",
            path=agents_relative,
        )
    return commands


def _delivery_flow(root: Path, tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """Bounded, offline hints; path changes never prove usable product delivery."""
    active = [task["id"] for task in tasks
              if task.get("kind") == "feature"
              and task.get("status") in {"in_progress", "in_review"}]
    result = {
        "rules": LIGHT_PROTOCOL_PATH,
        "protocol": LIGHT_PROTOCOL_PATH,
        "queue": QUEUE_PATH,
        "status": STATUS_PATH,
        "acceptance": ACCEPTANCE_PATH,
        "decisions": DECISIONS_PATH,
        "focus": STATUS_PATH,
        "metric_boundary": "路径与任务类型仅为代理指标；不证明业务可用、浏览器通过或发布就绪。",
        "active_product_task_count": len(active),
        "active_product_tasks": active[:10],
        "history_status": "unavailable",
        "history_reason": "main_ref_unavailable",
        "history_ref": None,
        "sample_size": None,
        "product_code_prs": None,
        "trailing_support_prs": None,
        "warnings": ["product_wip_over_two"] if len(active) > 2 else [],
        "action": "从队列取下一条待办成果，交付一个可评审 PR；告警时先解除具体阻塞，不新增规划/契约/收口 PR。",
    }
    history_ref = None
    for candidate in ("refs/remotes/origin/main", "refs/heads/main"):
        try:
            _run_git(root, ["rev-parse", "--verify", candidate])
            history_ref = candidate
            break
        except CliError:
            continue
    if history_ref is None:
        return result
    result["history_ref"] = history_ref
    kinds = {task["id"]: task["kind"] for task in tasks}
    try:
        lines = _run_git(root, ["log", history_ref, "--first-parent", "--max-count=60",
                                "--format=%H%x09%s"]).decode("utf-8").splitlines()
        sample = [line.split("\t", 1) for line in lines
                  if "\t" in line and re.search(r"\(#\d+\)$", line)][:20]
        if not sample:
            result["history_reason"] = "no_merged_pr_history"
            return result
        products = []
        for sha, title in sample:
            paths = _run_git(root, ["diff-tree", "--root", "--no-commit-id", "--name-only",
                                    "-r", "--first-parent", "-m", sha]).decode("utf-8").splitlines()
            task_match = re.search(r"\bPH-[0-9]{3,6}\b", title)
            kind = kinds.get(task_match.group()) if task_match else None
            product = kind != "docs" and any(
                path.startswith(("core/", "pmo_plugins/", "pmo/", "templates/", "static/", "integrations/mcp/"))
                and PurePosixPath(path).suffix.lower() in {".py", ".html", ".css", ".js", ".svg", ".png", ".jpg", ".webp"}
                and "tests" not in PurePosixPath(path).parts
                and PurePosixPath(path).name != "tests.py"
                and not PurePosixPath(path).name.startswith("test_")
                for path in paths)
            products.append(product)
    except (CliError, UnicodeError):
        result["history_reason"] = "git_history_unavailable"
        return result
    streak = next((index for index, product in enumerate(products) if product), len(products))
    result.update(history_status="available", history_reason=None,
                  sample_size=len(sample), product_code_prs=sum(products), trailing_support_prs=streak)
    if streak >= 3:
        result["warnings"].append("support_pr_streak")
    return result


def command_orient(root: Path) -> dict[str, Any]:
    manifest, tasks = _load_and_validate(root)
    context = {
        field: manifest.get(field)
        for field in (
            "schema_version",
            "name",
            "repository_url",
            "version_source",
            "current_iteration",
            "tasks_file",
            "active_handoff",
        )
    }
    context["repository_url"] = _sanitized_repository_url(context["repository_url"])

    handoff_relative = manifest.get("active_handoff")
    if not isinstance(handoff_relative, str) or not handoff_relative:
        raise CliError(
            "active_handoff_missing",
            "manifest.active_handoff must point to a handoff document",
            path=MANIFEST_PATH,
        )
    handoff_path = root / handoff_relative
    if not handoff_path.is_file():
        raise CliError(
            "file_not_found",
            "active handoff does not exist",
            path=handoff_relative,
        )
    return {
        "manifest": context,
        "active_handoff": {
            "path": handoff_relative,
            "content": handoff_path.read_text(encoding="utf-8"),
        },
        "ready": [_compact_task(task) for task in _ready_tasks(tasks)],
        "acceptance_commands": _acceptance_commands(root),
        "delivery_flow": _delivery_flow(root, tasks),
        "queue": _queue_summary(parse_queue(root, tasks)),
    }


def command_draft_report(root: Path, task_id: str) -> dict[str, Any]:
    if not root.is_dir():
        raise CliError("root_not_found", "root is not a directory", path=str(root))
    if TASK_ID_RE.fullmatch(task_id) is None:
        raise CliError("task_id", "task id must match PH- followed by 3 to 6 digits")
    _, tasks = _load_and_validate(root)
    matches = [task for task in tasks if task.get("id") == task_id]
    if not matches:
        raise CliError("task_not_found", f"task {task_id} does not exist", path=TASKS_PATH)
    task = matches[0]
    if task.get("status") not in {"ready", "in_progress", "in_review"}:
        raise CliError(
            "task_not_executable",
            "draft-report requires a ready, in_progress or in_review task",
            path=TASKS_PATH,
        )
    risk = task.get("risk")
    review_required = risk == "S"
    skeleton = {
        "task_id": task_id,
        "summary": "",
        "tests": [
            {"command": command, "result": "not_run", "exit_code": None, "evidence": ""}
            for command in task.get("acceptance", [])
        ],
        "review": {
            "required": review_required,
            "reviewer": None,
            "status": "pending" if review_required else "not_required",
            "findings": [],
        },
        "unverified": [],
        "deviations": [],
    }
    return {
        "task": {"id": task_id, "risk": risk, "acceptance": task.get("acceptance", [])},
        "report_path": f"project/reports/{task_id}.json",
        "report": skeleton,
    }


def _sanitized_repository_url(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    parsed = urlsplit(value)
    if parsed.scheme and parsed.netloc and (parsed.username is not None):
        hostname = parsed.hostname or ""
        if parsed.port is not None:
            hostname = f"{hostname}:{parsed.port}"
        return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))
    if parsed.scheme and parsed.netloc and (parsed.query or parsed.fragment):
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    return value


def command_brief(root: Path, task_id: str) -> dict[str, Any]:
    if not root.is_dir():
        raise CliError("root_not_found", "root is not a directory", path=str(root))
    if TASK_ID_RE.fullmatch(task_id) is None:
        raise CliError("task_id", "task id must match PH- followed by 3 to 6 digits")

    manifest = _read_root_json(root, MANIFEST_PATH)
    tasks_document = _read_root_json(root, TASKS_PATH)
    if not isinstance(manifest, dict):
        raise CliError("invalid_type", "manifest root must be an object", path=MANIFEST_PATH)
    if not isinstance(tasks_document, dict) or not isinstance(tasks_document.get("tasks"), list):
        raise CliError("invalid_type", "tasks document must contain a tasks array", path=TASKS_PATH)

    matches = [
        task
        for task in tasks_document["tasks"]
        if isinstance(task, dict) and task.get("id") == task_id
    ]
    if not matches:
        raise CliError("task_not_found", f"task {task_id} does not exist", path=TASKS_PATH)
    if len(matches) > 1:
        raise CliError("duplicate_task_id", f"task {task_id} appears more than once", path=TASKS_PATH)

    context = {
        field: manifest.get(field)
        for field in (
            "schema_version",
            "name",
            "repository_url",
            "version_source",
            "current_iteration",
            "tasks_file",
            "active_handoff",
        )
    }
    context["repository_url"] = _sanitized_repository_url(context["repository_url"])
    task = {field: matches[0].get(field) for field in TASK_FIELDS}
    return {"manifest": context, "task": task}


def _run_git(root: Path, arguments: Sequence[str]) -> bytes:
    environment = os.environ.copy()
    for variable in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        environment.pop(variable, None)
    environment.update({"GIT_OPTIONAL_LOCKS": "0", "GIT_NO_REPLACE_OBJECTS": "1", "LC_ALL": "C"})
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            shell=False,
            check=False,
            capture_output=True,
            timeout=30,
            env=environment,
        )
    except FileNotFoundError as exc:
        raise CliError("git_not_found", "git executable was not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise CliError("git_timeout", "git command timed out") from exc
    except OSError as exc:
        raise CliError("git_error", str(exc)) from exc
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CliError(
            "git_error",
            stderr or f"git exited with status {completed.returncode}",
        )
    return completed.stdout


def _resolve_commit(root: Path, reference: str, label: str) -> str:
    if not _is_nonempty_string(reference):
        raise CliError("invalid_ref", f"{label} must be a non-empty Git reference")
    try:
        output = _run_git(
            root,
            ["rev-parse", "--verify", "--end-of-options", f"{reference}^{{commit}}"],
        )
    except CliError as exc:
        raise CliError(
            "invalid_ref",
            f"cannot resolve {label} {reference!r}: {exc.message}",
        ) from exc
    return output.decode("ascii").strip()


def _git_json(root: Path, revision: str, path: str) -> Any:
    try:
        raw = _run_git(root, ["show", f"{revision}:{path}"])
    except CliError as exc:
        raise CliError(
            "file_not_in_revision",
            f"{path} does not exist in revision {revision}",
            path=path,
        ) from exc
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise CliError("invalid_encoding", f"{path} is not UTF-8", path=path) from exc
    except json.JSONDecodeError as exc:
        raise CliError(
            "invalid_json",
            f"{path} contains invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}",
            path=path,
        ) from exc


def _parse_name_status_z(raw: bytes) -> list[dict[str, str | None]]:
    fields = raw.split(b"\x00")
    if fields and fields[-1] == b"":
        fields.pop()
    changes: list[dict[str, str | None]] = []
    index = 0
    while index < len(fields):
        try:
            status = fields[index].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CliError("invalid_git_output", "git returned a non-UTF-8 status") from exc
        index += 1
        if not status:
            raise CliError("invalid_git_output", "git returned an empty name-status record")
        kind = status[0]
        if kind in {"R", "C"}:
            if index + 1 >= len(fields):
                raise CliError("invalid_git_output", "truncated rename/copy record")
            try:
                old_path = fields[index].decode("utf-8")
                path = fields[index + 1].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CliError("invalid_git_output", "git returned a non-UTF-8 path") from exc
            index += 2
            changes.append(
                {"status": status, "kind": kind, "old_path": old_path, "path": path}
            )
        else:
            if index >= len(fields):
                raise CliError("invalid_git_output", "truncated name-status record")
            try:
                path = fields[index].decode("utf-8")
            except UnicodeDecodeError as exc:
                raise CliError("invalid_git_output", "git returned a non-UTF-8 path") from exc
            index += 1
            changes.append(
                {"status": status, "kind": kind, "old_path": None, "path": path}
            )
    return changes


def _is_migration(path: str) -> bool:
    return fnmatch.fnmatchcase(path, "*/migrations/[0-9]*.py")


def _is_critical(path: str) -> bool:
    return path in CRITICAL_PATHS or _is_migration(path) or path.startswith((".github/", ".cursor/"))


def _validate_report(
    report: Any,
    task_id: str,
    risk: str,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    report_path = f"project/reports/{task_id}.json"
    if not isinstance(report, dict):
        return [issue("invalid_type", "report root must be an object", report_path)]

    required = (
        "task_id",
        "summary",
        "tests",
        "review",
        "unverified",
        "deviations",
    )
    for field in required:
        if field not in report:
            issues.append(issue("required", "required report field is missing", f"report.{field}"))
    if report.get("task_id") != task_id:
        issues.append(
            issue("task_id_mismatch", f"report task_id must be {task_id}", "report.task_id")
        )
    if not _is_nonempty_string(report.get("summary")):
        issues.append(issue("invalid_value", "summary must be a non-empty string", "report.summary"))

    tests = report.get("tests")
    if not isinstance(tests, list):
        issues.append(issue("invalid_type", "tests must be an array", "report.tests"))
        tests = []
    pass_count = 0
    fail_count = 0
    for index, test in enumerate(tests):
        field_root = f"report.tests[{index}]"
        if not isinstance(test, dict):
            issues.append(issue("invalid_type", "test entry must be an object", field_root))
            continue
        for field in ("command", "result", "evidence"):
            if field not in test:
                issues.append(
                    issue("required", "required test field is missing", f"{field_root}.{field}")
                )
        if not _is_nonempty_string(test.get("command")):
            issues.append(
                issue("invalid_value", "command must be a non-empty string", f"{field_root}.command")
            )
        result = test.get("result")
        if result not in REPORT_TEST_RESULTS:
            issues.append(
                issue(
                    "enum",
                    f"result must be one of {sorted(REPORT_TEST_RESULTS)}",
                    f"{field_root}.result",
                )
            )
        elif result == "pass":
            pass_count += 1
        elif result == "fail":
            fail_count += 1
        # Old reports remain readable; explicit modern claims must be consistent.
        if "exit_code" in test:
            code = test["exit_code"]
            valid_code = ((result == "not_run" and code is None)
                          or (type(code) is int and
                              ((result == "pass" and code == 0)
                               or (result == "fail" and code != 0))))
            if not valid_code:
                issues.append(issue("test_exit_code_mismatch",
                                    "pass requires integer 0; fail nonzero integer; not_run null",
                                    f"{field_root}.exit_code"))
        if "evidence" in test and not isinstance(test.get("evidence"), str):
            issues.append(
                issue("invalid_type", "evidence must be a string", f"{field_root}.evidence")
            )
    if not tests:
        issues.append(issue("tests_required", "at least one test declaration is required", "report.tests"))
    if pass_count == 0:
        issues.append(
            issue("tests_no_pass", "at least one test must have result pass", "report.tests")
        )
    if fail_count:
        issues.append(issue("tests_failed", "report contains a failed test", "report.tests"))

    review = report.get("review")
    if not isinstance(review, dict):
        issues.append(issue("invalid_type", "review must be an object", "report.review"))
        review = {}
    for field in ("required", "reviewer", "status", "findings"):
        if field not in review:
            issues.append(
                issue("required", "required review field is missing", f"report.review.{field}")
            )
    if not isinstance(review.get("required"), bool):
        issues.append(
            issue("invalid_type", "required must be a boolean", "report.review.required")
        )
    reviewer = review.get("reviewer")
    if reviewer is not None and not isinstance(reviewer, str):
        issues.append(
            issue("invalid_type", "reviewer must be null or a string", "report.review.reviewer")
        )
    if review.get("status") not in REPORT_REVIEW_STATUSES:
        issues.append(
            issue(
                "enum",
                f"status must be one of {sorted(REPORT_REVIEW_STATUSES)}",
                "report.review.status",
            )
        )
    if not isinstance(review.get("findings"), list):
        issues.append(
            issue("invalid_type", "findings must be an array", "report.review.findings")
        )

    for field in ("unverified", "deviations"):
        if not isinstance(report.get(field), list):
            issues.append(issue("invalid_type", f"{field} must be an array", f"report.{field}"))

    if risk == "S" and (
        review.get("required") is not True
        or not _is_nonempty_string(reviewer)
        or review.get("status") != "pass"
    ):
        issues.append(
            issue(
                "high_risk_review_required",
                "risk S reports require a named reviewer with status pass",
                "report.review",
            )
        )
    return issues


def _verify_registration(root: Path, base: str, head: str, task_id: str,
                         baseline: dict[str, Any]) -> dict[str, Any]:
    """A BASE-owned registration path; never authorizes implementation in this PR."""
    def reject(message: str) -> None:
        raise CliError("registration_rejected", message)

    def canonical(value: Any) -> str:
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

    candidate = _git_json(root, head, TASKS_PATH)
    tasks, errors = _validate_tasks_data(candidate, None, check_files=False)
    if errors:
        reject("new task contract is invalid")
    old_tasks = baseline["tasks"]
    if len(tasks) != len(old_tasks) + 1 or canonical(tasks[:-1]) != canonical(old_tasks):
        reject("registration may only append one task; existing tasks must remain identical")
    remaining = dict(candidate)
    remaining["tasks"] = old_tasks
    if canonical(remaining) != canonical(baseline):
        reject("task document metadata must remain identical")
    task = tasks[-1]
    if task["id"] != task_id or task["status"] != "ready" or task["owner"] is not None:
        reject("new primary task must be ready and unclaimed; dependencies must be done")
    spec_path = f"project/specs/{task_id}.md"
    report_path = f"project/reports/{task_id}.json"
    handoff_path = f"project/handoffs/{task_id}.md"
    if task["spec"] != spec_path or task["handoff"] != handoff_path:
        reject("registration requires new task-specific spec and handoff paths")
    manifest = _git_json(root, base, MANIFEST_PATH)
    repository = manifest.get("repository_url", "").removesuffix(".git").rstrip("/")
    if (not repository.startswith("https://github.com/")
            or not re.fullmatch(re.escape(repository) + r"/issues/[1-9][0-9]*",
                                task.get("github_issue") or "")):
        reject("new task requires a real issue URL in the BASE repository")
    required = {TASKS_PATH, spec_path, report_path, handoff_path}
    policy_path = "project/delivery-policy.json"
    if task["risk"] == "S":
        required.add(policy_path)
    changes = _parse_name_status_z(_run_git(root, ["diff", "--no-ext-diff", "--no-textconv",
                  "--name-status", "-z", "--find-renames=50%", f"{base}...{head}", "--"]))
    if {change["path"] for change in changes} != required:
        reject("registration may only change task metadata and its new spec/report/handoff/grant")
    for change in changes:
        path = str(change["path"])
        expected_kind = "M" if path in {TASKS_PATH, policy_path} else "A"
        if change["kind"] != expected_kind or change["old_path"] is not None:
            reject("registration forbids overwrites of existing specs, renames and deletions")
        tree = _run_git(root, ["ls-tree", head, "--", path]).decode("utf-8")
        if not tree.startswith("100644 blob "):
            reject("registration files must be regular non-executable files")
    spec = _run_git(root, ["show", f"{head}:{spec_path}"]).decode("utf-8")
    if not spec.strip():
        reject("new spec cannot be empty")
    report = _git_json(root, head, report_path)
    report_errors = _validate_report(report, task_id, "S")
    if report_errors:
        reject("registration requires passing common checks and independent S review")
    if report.get("registration_only") is not True:
        reject("report must explicitly identify registration_only, not implementation")
    source = report.get("authorization_source")
    if not isinstance(source, str) or not re.fullmatch(r"user-[A-Za-z0-9._:/-]{1,195}", source):
        reject("report must identify the explicit user authorization source")
    common = {"python scripts/projectctl.py check",
              "python -m unittest tests.test_projectctl tests.test_governance -v"}
    passed = {entry["command"] for entry in report["tests"] if entry["result"] == "pass"}
    if not common.issubset(passed):
        reject("registration common governance acceptance has not passed")
    if any(entry.get("result") == "pass" and (type(entry.get("exit_code")) is not int or entry["exit_code"] != 0)
           for entry in report["tests"]):
        reject("new registration passing evidence requires explicit integer exit_code 0")
    if task["risk"] == "S":
        old_policy = _git_json(root, base, policy_path)
        new_policy = _git_json(root, head, policy_path)
        old_grants = old_policy.get("s_authorization", {}).get("tasks", [])
        new_grants = new_policy.get("s_authorization", {}).get("tasks", [])
        fields = ("id", "title", "kind", "risk", "depends_on", "spec",
                  "allowed_paths", "acceptance", "github_issue")
        digest = hashlib.sha256(canonical({"task": {key: task[key] for key in fields},
                                           "spec": spec}).encode("utf-8")).hexdigest()
        grant = {"task_id": task_id, "contract_digest": digest, "source": source}
        if canonical(new_grants) != canonical(old_grants + [grant]):
            reject("only the new task's exact S authorization grant may be appended")
        # Compare every existing policy value, including JSON types, unchanged.
        expected = json.loads(canonical(old_policy))
        expected["s_authorization"]["tasks"].append(grant)
        if canonical(new_policy) != canonical(expected):
            reject("registration cannot change policy requirements or existing authorizations")
    return {"base": base, "head": head, "registration_only": True,
            "implementation_allowed": False, "task": {"id": task_id, "risk": "S"},
            "report_path": report_path, "changes": changes,
            "next_step": "Merge registration with required CI, then implement against the new ready BASE contract.",
            "authorization_boundary": "Source and review declarations do not prove user intent or reviewer identity; retain actual independent review and required CI."}


def command_verify_change(root: Path, base: str, task_id: str) -> dict[str, Any]:
    if not root.is_dir():
        raise CliError("root_not_found", "root is not a directory", path=str(root))
    if TASK_ID_RE.fullmatch(task_id) is None:
        raise CliError("task_id", "task id must match PH- followed by 3 to 6 digits")

    base_commit = _resolve_commit(root, base, "base")
    head_commit = _resolve_commit(root, "HEAD", "HEAD")

    baseline_tasks_document = _git_json(root, base_commit, TASKS_PATH)
    baseline_tasks, baseline_issues = _validate_tasks_data(
        baseline_tasks_document,
        None,
        check_files=False,
    )
    if baseline_issues:
        raise CliError(
            "baseline_validation_failed",
            f"baseline task contract has {len(baseline_issues)} validation error(s)",
            errors=baseline_issues,
        )
    baseline_matches = [task for task in baseline_tasks if task.get("id") == task_id]
    if not baseline_matches:
        return _verify_registration(root, base_commit, head_commit, task_id,
                                    baseline_tasks_document)
    baseline_task = baseline_matches[0]
    if baseline_task.get("status") not in {"ready", "in_progress", "in_review"}:
        raise CliError("task_not_executable", "BASE task must be ready, in_progress or in_review", path=TASKS_PATH)
    allowed_patterns = baseline_task.get("allowed_paths")
    risk = baseline_task.get("risk")
    if not isinstance(allowed_patterns, list) or risk not in RISKS:
        raise CliError(
            "baseline_task_invalid",
            f"baseline task {task_id} has an invalid allowed_paths or risk field",
            path=TASKS_PATH,
        )

    raw_diff = _run_git(
        root,
        [
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--name-status",
            "-z",
            "--find-renames=50%",
            f"{base_commit}...{head_commit}",
            "--",
        ],
    )
    changes = _parse_name_status_z(raw_diff)

    issues: list[dict[str, Any]] = []
    for change in changes:
        status = str(change["status"])
        kind = str(change["kind"])
        path = str(change["path"])
        old_path = change["old_path"]
        candidates = [path]
        if isinstance(old_path, str):
            candidates.insert(0, old_path)

        for candidate in dict.fromkeys(candidates):
            if not any(fnmatch.fnmatchcase(candidate, pattern) for pattern in allowed_patterns):
                issues.append(
                    issue(
                        "path_out_of_scope",
                        f"{status} path is outside the BASE task allowed_paths: {candidate}",
                        candidate,
                    )
                )

        if _is_migration(path) and kind != "A":
            issues.append(
                issue(
                    "migration_change",
                    "existing migrations may only be modified or deleted by a new migration",
                    path,
                )
            )
        if isinstance(old_path, str) and _is_migration(old_path) and kind in {"D", "R"}:
            issues.append(
                issue(
                    "existing_migration_change",
                    "existing migrations may not be deleted or renamed",
                    old_path,
                )
            )

        changed_critical_path = any(_is_critical(candidate) for candidate in candidates)
        if changed_critical_path and risk != "S":
            issues.append(
                issue(
                    "critical_path_requires_high_risk",
                    "financial, permission, agent, plugin, or migration changes require BASE risk S",
                    path,
                )
            )

    report_path = f"project/reports/{task_id}.json"
    report = _git_json(root, head_commit, report_path)
    issues.extend(_validate_report(report, task_id, risk))
    if isinstance(report, dict) and report.get("registration_only") is True:
        issues.append(issue("registration_report_not_implementation", "registration evidence cannot satisfy implementation acceptance", report_path))
    if isinstance(report, dict):
        passed_commands = {entry.get("command") for entry in report.get("tests", []) if isinstance(entry, dict) and entry.get("result") == "pass" and isinstance(entry.get("command"), str)}
        for command in baseline_task.get("acceptance", []):
            if command not in passed_commands:
                issues.append(issue("acceptance_not_passed", "required BASE acceptance command has no passing declaration: " + command, report_path))

    if issues:
        raise CliError(
            "verification_failed",
            f"change verification failed with {len(issues)} error(s)",
            errors=issues,
        )

    return {
        "base": base_commit,
        "head": head_commit,
        "task": {
            "id": task_id,
            "risk": risk,
            "allowed_paths": allowed_patterns,
        },
        "changes": changes,
        "report_path": report_path,
        "assertion_scope": "declaration_only",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(
        prog="projectctl.py",
        description="Read-only project contract checks for cross-agent work.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_parser = subparsers.add_parser("check", help="validate manifest and tasks")
    check_parser.add_argument("--root", default=".")

    queue_parser = subparsers.add_parser(
        "queue",
        help="validate and show the ordered outcome queue (project/QUEUE.md)",
    )
    queue_parser.add_argument("--compact", action="store_true",
                              help="omit per-item detail and print only the summary")
    queue_parser.add_argument("--root", default=".")

    next_parser = subparsers.add_parser("next", help="list dependency-ready tasks")
    next_parser.add_argument("--compact", action="store_true", help="emit only id/title/kind/risk/status/milestone/depends_on")
    next_parser.add_argument("--root", default=".")

    brief_parser = subparsers.add_parser("brief", help="show minimal task context")
    brief_parser.add_argument("task_id")
    brief_parser.add_argument("--root", default=".")

    orient_parser = subparsers.add_parser(
        "orient",
        help="emit one-shot session context: manifest, active handoff, ready tasks, acceptance commands",
    )
    orient_parser.add_argument("--root", default=".")

    draft_parser = subparsers.add_parser(
        "draft-report",
        help="print a report skeleton (result not_run) for a task; never writes files",
    )
    draft_parser.add_argument("task_id")
    draft_parser.add_argument("--root", default=".")

    verify_parser = subparsers.add_parser(
        "verify-change",
        help="verify changed paths and the committed report against BASE",
    )
    verify_parser.add_argument("--base", required=True)
    verify_parser.add_argument("--task", required=True)
    verify_parser.add_argument("--root", default=".")
    return parser


def _emit(payload: dict[str, Any]) -> None:
    json.dump(payload, sys.stdout, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    sys.stdout.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    command_name: str | None = None
    try:
        parser = build_parser()
        arguments = parser.parse_args(argv)
        command_name = arguments.command
        root = Path(arguments.root).resolve()
        if command_name == "check":
            result = command_check(root)
        elif command_name == "queue":
            result = command_queue(root, compact=arguments.compact)
        elif command_name == "next":
            result = command_next(root, compact=arguments.compact)
        elif command_name == "brief":
            result = command_brief(root, arguments.task_id)
        elif command_name == "orient":
            result = command_orient(root)
        elif command_name == "draft-report":
            result = command_draft_report(root, arguments.task_id)
        elif command_name == "verify-change":
            result = command_verify_change(root, arguments.base, arguments.task)
        else:
            raise CliError("usage_error", f"unknown command: {command_name}")
        _emit({"ok": True, "command": command_name, **result})
        return 0
    except CliError as exc:
        _emit({"ok": False, "command": command_name, "errors": exc.payload()})
        return 1
    except Exception as exc:  # Keep failures machine-readable and never report a false pass.
        _emit(
            {
                "ok": False,
                "command": command_name,
                "errors": [
                    issue(
                        "internal_error",
                        f"{type(exc).__name__}: {exc}",
                    )
                ],
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
