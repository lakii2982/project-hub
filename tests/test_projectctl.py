from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts import projectctl


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "projectctl.py"


def make_task(task_id: str, **overrides: Any) -> dict[str, Any]:
    task = {
        "id": task_id,
        "title": f"Task {task_id}",
        "kind": "feature",
        "risk": "A",
        "status": "ready",
        "priority": 2,
        "milestone": "M1",
        "depends_on": [],
        "spec": f"project/specs/{task_id}.md",
        "allowed_paths": ["safe.txt", f"project/reports/{task_id}.json"],
        "acceptance": ["python -m unittest tests.test_projectctl -v"],
        "github_issue": None,
        "owner": None,
        "blockers": [],
        "handoff": None,
    }
    task.update(overrides)
    return task


def make_manifest(*, required_docs: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "name": "project-hub",
        "repository_url": "https://example.invalid/project-hub.git",
        "version_source": "pyproject.toml",
        "current_iteration": "M1",
        "tasks_file": "project/tasks.json",
        "active_handoff": None,
        "required_docs": required_docs or ["docs/required.md"],
    }


def make_report(task_id: str, **overrides: Any) -> dict[str, Any]:
    report = {
        "task_id": task_id,
        "summary": "Implemented the assigned thin slice.",
        "tests": [
            {
                "command": "python -m unittest tests.test_projectctl -v",
                "result": "pass",
                "evidence": "The declared test command passed.",
            }
        ],
        "review": {
            "required": False,
            "reviewer": None,
            "status": "not_required",
            "findings": [],
        },
        "unverified": [],
        "deviations": [],
    }
    report.update(overrides)
    return report


class ProjectCtlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(prefix="projectctl-test-")
        self.addCleanup(self.temp_directory.cleanup)
        self.root = Path(self.temp_directory.name)

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def write_json(self, relative: str, data: Any) -> Path:
        return self.write(relative, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def write_fixture(
        self,
        tasks: list[dict[str, Any]],
        *,
        manifest: dict[str, Any] | None = None,
        skip_files: set[str] | None = None,
    ) -> None:
        skip_files = skip_files or set()
        manifest = manifest or make_manifest()
        self.write_json("project/manifest.json", manifest)
        self.write_json("project/tasks.json", {"schema_version": 1, "tasks": tasks})
        self.write(
            "pyproject.toml",
            '[project]\nname = "project-hub"\nversion = "0.1.0"\nrequires-python = ">=3.12"\n',
        )
        for document in manifest["required_docs"]:
            if document not in skip_files:
                self.write(document, "# Required\n")
        for task in tasks:
            spec = task.get("spec")
            if isinstance(spec, str) and spec not in skip_files:
                self.write(spec, f"# {task.get('id')}\n")
            handoff = task.get("handoff")
            if isinstance(handoff, str) and handoff not in skip_files:
                self.write(handoff, f"# Handoff {task.get('id')}\n")
            if task.get('status') == 'done':
                self.write_json(f"project/reports/{task['id']}.json", make_report(task['id']))

    def run_cli(self, *arguments: str) -> tuple[int, dict[str, Any], str]:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            self.fail(
                f"CLI did not emit JSON: stdout={completed.stdout!r}, "
                f"stderr={completed.stderr!r}, error={exc}"
            )
        return completed.returncode, payload, completed.stderr

    def git(self, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=self.root,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            self.fail(f"git {' '.join(arguments)} failed: {completed.stderr}")
        return completed.stdout.strip()

    def init_repository(self) -> str:
        self.git("init", "-q")
        self.git("config", "user.name", "ProjectCtl Test")
        self.git("config", "user.email", "projectctl@example.invalid")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        return self.git("rev-parse", "HEAD")

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def error_codes(self, payload: dict[str, Any]) -> set[str]:
        return {error["code"] for error in payload.get("errors", [])}

    def test_long_lived_project_accepts_four_digit_task_id(self) -> None:
        self.write_fixture([make_task("PH-1000")])
        code, payload, _ = self.run_cli("check", "--root", str(self.root))
        self.assertEqual(code, 0, payload)
        code, payload, _ = self.run_cli("brief", "PH-1000", "--root", str(self.root))
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["task"]["id"], "PH-1000")

    def test_check_reports_schema_errors(self) -> None:
        invalid = make_task("PH-01", status="unknown")
        self.write_fixture([invalid])

        code, payload, _ = self.run_cli("check", "--root", str(self.root))

        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("task_id", self.error_codes(payload))
        self.assertIn("enum", self.error_codes(payload))

    def test_check_rejects_dependency_cycle(self) -> None:
        tasks = [
            make_task("PH-001", depends_on=["PH-002"]),
            make_task("PH-002", depends_on=["PH-001"]),
        ]
        self.write_fixture(tasks)

        code, payload, _ = self.run_cli("check", "--root", str(self.root))

        self.assertEqual(code, 1)
        self.assertIn("dependency_cycle", self.error_codes(payload))

    def test_next_orders_by_priority_then_id(self) -> None:
        handoff = "project/handoffs/PH-001.md"
        tasks = [
            make_task(
                "PH-001",
                status="done",
                owner="agent-a",
                handoff=handoff,
            ),
            make_task("PH-002", priority=2),
            make_task("PH-003", priority=1, depends_on=["PH-001"]),
            make_task("PH-004", priority=1),
        ]
        self.write_fixture(tasks)

        code, payload, _ = self.run_cli("next", "--root", str(self.root))

        self.assertEqual(code, 0, payload)
        self.assertEqual(
            [task["id"] for task in payload["tasks"]],
            ["PH-003", "PH-004", "PH-002"],
        )

    def test_check_rejects_missing_handoff_file(self) -> None:
        handoff = "project/handoffs/PH-001.md"
        task = make_task(
            "PH-001",
            status="in_review",
            owner="agent-a",
            handoff=handoff,
        )
        self.write_fixture([task], skip_files={handoff})

        code, payload, _ = self.run_cli("check", "--root", str(self.root))

        self.assertEqual(code, 1)
        self.assertIn("file_not_found", self.error_codes(payload))

    def test_brief_outputs_minimal_context_without_url_credentials(self) -> None:
        task = make_task("PH-001", internal_debug_secret="do-not-output")
        manifest = make_manifest()
        manifest["repository_url"] = (
            "https://user:token@example.invalid/project.git?access_token=secret#private"
        )
        self.write_fixture([task], manifest=manifest)

        code, payload, _ = self.run_cli(
            "brief",
            "PH-001",
            "--root",
            str(self.root),
        )

        self.assertEqual(code, 0, payload)
        self.assertEqual(
            payload["manifest"]["repository_url"],
            "https://example.invalid/project.git",
        )
        self.assertNotIn("internal_debug_secret", payload["task"])
        serialized = json.dumps(payload)
        self.assertNotIn("token", serialized)
        self.assertNotIn("do-not-output", serialized)

    def test_verify_change_accepts_scoped_change_and_report(self) -> None:
        task_id = "PH-001"
        task = make_task(
            task_id,
            allowed_paths=[
                "safe.txt",
                f"project/reports/{task_id}.json",
            ],
        )
        self.write_fixture([task])
        self.write("safe.txt", "base\n")
        base = self.init_repository()

        self.write("safe.txt", "head\n")
        self.write_json(f"project/reports/{task_id}.json", make_report(task_id))
        self.commit("head")

        code, payload, _ = self.run_cli(
            "verify-change",
            "--root",
            str(self.root),
            "--base",
            base,
            "--task",
            task_id,
        )

        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["assertion_scope"], "declaration_only")

    def test_base_scope_cannot_be_expanded_by_head(self) -> None:
        task_id = "PH-001"
        task = make_task(
            task_id,
            allowed_paths=["safe.txt"],
        )
        self.write_fixture([task])
        self.write("safe.txt", "base\n")
        base = self.init_repository()

        expanded = make_task(
            task_id,
            allowed_paths=[
                "safe.txt",
                "outside.txt",
                f"project/reports/{task_id}.json",
            ],
        )
        self.write_json("project/tasks.json", {"schema_version": 1, "tasks": [expanded]})
        self.write("outside.txt", "cannot self-authorize\n")
        self.write_json(f"project/reports/{task_id}.json", make_report(task_id))
        self.commit("expanded scope")

        code, payload, _ = self.run_cli(
            "verify-change",
            "--root",
            str(self.root),
            "--base",
            base,
            "--task",
            task_id,
        )

        self.assertEqual(code, 1)
        self.assertIn("path_out_of_scope", self.error_codes(payload))
        paths = {error.get("path") for error in payload["errors"]}
        self.assertIn("outside.txt", paths)

    def test_git_replace_cannot_substitute_a_wider_base_contract(self) -> None:
        task_id = "PH-001"
        task = make_task(task_id, allowed_paths=["safe.txt", f"project/reports/{task_id}.json"])
        self.write_fixture([task])
        self.write("safe.txt", "base\n")
        base = self.init_repository()
        forged = dict(task, risk="S", allowed_paths=["*"])
        self.write_json("project/tasks.json", {"schema_version": 1, "tasks": [forged]})
        self.git("add", "-A")
        tree = self.git("write-tree")
        replacement = self.git("commit-tree", tree, "-m", "forged base")
        self.write_json("project/tasks.json", {"schema_version": 1, "tasks": [task]})
        self.write("outside.txt", "not authorized\n")
        review = {"required": True, "reviewer": "independent-reviewer", "status": "pass", "findings": []}
        self.write_json(f"project/reports/{task_id}.json", make_report(task_id, review=review))
        self.commit("out of scope")
        self.git("replace", base, replacement)
        code, payload, _ = self.run_cli("verify-change", "--root", str(self.root), "--base", base, "--task", task_id)
        self.assertEqual(code, 1, payload)
        self.assertIn("path_out_of_scope", self.error_codes(payload))

    def test_verify_change_rejects_out_of_scope_file(self) -> None:
        task_id = "PH-001"
        task = make_task(
            task_id,
            allowed_paths=["safe.txt", f"project/reports/{task_id}.json"],
        )
        self.write_fixture([task])
        self.write("safe.txt", "base\n")
        base = self.init_repository()

        self.write("outside.txt", "not authorized\n")
        self.write_json(f"project/reports/{task_id}.json", make_report(task_id))
        self.commit("out of scope")

        code, payload, _ = self.run_cli(
            "verify-change",
            "--root",
            str(self.root),
            "--base",
            base,
            "--task",
            task_id,
        )

        self.assertEqual(code, 1)
        self.assertIn("path_out_of_scope", self.error_codes(payload))

    def test_verify_change_rejects_old_migration_modification(self) -> None:
        task_id = "PH-001"
        migration = "core/migrations/0001_initial.py"
        review = {
            "required": True,
            "reviewer": "reviewer-a",
            "status": "pass",
            "findings": [],
        }
        task = make_task(
            task_id,
            risk="S",
            allowed_paths=[migration, f"project/reports/{task_id}.json"],
        )
        self.write_fixture([task])
        self.write(migration, "VALUE = 1\n")
        base = self.init_repository()

        self.write(migration, "VALUE = 2\n")
        report = make_report(task_id, review=review)
        self.write_json(f"project/reports/{task_id}.json", report)
        self.commit("modify old migration")

        code, payload, _ = self.run_cli(
            "verify-change",
            "--root",
            str(self.root),
            "--base",
            base,
            "--task",
            task_id,
        )

        self.assertEqual(code, 1)
        self.assertIn("migration_change", self.error_codes(payload))

    def test_verify_change_requires_risk_s_for_new_migration(self) -> None:
        task_id = "PH-001"
        migration = "core/migrations/0002_new.py"
        task = make_task(
            task_id,
            risk="A",
            allowed_paths=["core/migrations/*.py", f"project/reports/{task_id}.json"],
        )
        self.write_fixture([task])
        base = self.init_repository()

        self.write(migration, "VALUE = 1\n")
        self.write_json(f"project/reports/{task_id}.json", make_report(task_id))
        self.commit("new migration without S")

        code, payload, _ = self.run_cli(
            "verify-change",
            "--root",
            str(self.root),
            "--base",
            base,
            "--task",
            task_id,
        )

        self.assertEqual(code, 1)
        self.assertIn("critical_path_requires_high_risk", self.error_codes(payload))

    def test_verify_change_checks_both_rename_paths(self) -> None:
        task_id = "PH-001"
        task = make_task(
            task_id,
            allowed_paths=["old.txt", f"project/reports/{task_id}.json"],
        )
        self.write_fixture([task])
        self.write("old.txt", "rename me\n")
        base = self.init_repository()

        self.git("mv", "old.txt", "new.txt")
        self.write_json(f"project/reports/{task_id}.json", make_report(task_id))
        self.commit("rename")

        code, payload, _ = self.run_cli(
            "verify-change",
            "--root",
            str(self.root),
            "--base",
            base,
            "--task",
            task_id,
        )

        self.assertEqual(code, 1)
        self.assertIn("path_out_of_scope", self.error_codes(payload))
        paths = {error.get("path") for error in payload["errors"]}
        self.assertIn("new.txt", paths)

    def test_verify_change_rejects_risk_s_without_passing_review(self) -> None:
        task_id = "PH-001"
        task = make_task(
            task_id,
            risk="S",
            allowed_paths=["safe.txt", f"project/reports/{task_id}.json"],
        )
        self.write_fixture([task])
        self.write("safe.txt", "base\n")
        base = self.init_repository()

        self.write("safe.txt", "head\n")
        self.write_json(f"project/reports/{task_id}.json", make_report(task_id))
        self.commit("missing high-risk review")

        code, payload, _ = self.run_cli(
            "verify-change",
            "--root",
            str(self.root),
            "--base",
            base,
            "--task",
            task_id,
        )

        self.assertEqual(code, 1)
        self.assertIn("high_risk_review_required", self.error_codes(payload))

    def test_verify_change_rejects_report_with_failed_test(self) -> None:
        task_id = "PH-001"
        task = make_task(
            task_id,
            allowed_paths=["safe.txt", f"project/reports/{task_id}.json"],
        )
        self.write_fixture([task])
        self.write("safe.txt", "base\n")
        base = self.init_repository()

        self.write("safe.txt", "head\n")
        report = make_report(task_id)
        report["tests"][0]["result"] = "fail"
        self.write_json(f"project/reports/{task_id}.json", report)
        self.commit("failed report")

        code, payload, _ = self.run_cli(
            "verify-change",
            "--root",
            str(self.root),
            "--base",
            base,
            "--task",
            task_id,
        )

        self.assertEqual(code, 1)
        self.assertIn("tests_failed", self.error_codes(payload))

    def test_next_compact_limits_fields_and_keeps_default_shape(self) -> None:
        ready = make_task("PH-002", priority=1, allowed_paths=["safe.txt", "other.txt"])
        self.write_fixture([ready, make_task("PH-001", status="proposed")])

        code, payload, _ = self.run_cli("next", "--root", str(self.root))
        self.assertEqual(code, 0, payload)
        self.assertIn("allowed_paths", payload["tasks"][0])

        code, payload, _ = self.run_cli("next", "--compact", "--root", str(self.root))
        self.assertEqual(code, 0, payload)
        self.assertEqual([task["id"] for task in payload["tasks"]], ["PH-002"])
        self.assertEqual(
            sorted(payload["tasks"][0]),
            sorted(["id", "title", "kind", "risk", "status", "milestone", "depends_on"]),
        )

    def test_orient_emits_one_shot_session_facts(self) -> None:
        manifest = make_manifest()
        manifest["active_handoff"] = "project/handoffs/PH-001.md"
        self.write_fixture([make_task("PH-001")], manifest=manifest)
        self.write("project/handoffs/PH-001.md", "# Handoff PH-001\n\n继续薄片实现。\n")
        self.write(
            "AGENTS.md",
            "# 约束\n\n## 验收与交付\n\n"
            "```sh\n.venv/bin/python manage.py check\n"
            ".venv/bin/python manage.py test tests --verbosity 1\n```\n",
        )

        code, payload, _ = self.run_cli("orient", "--root", str(self.root))

        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["manifest"]["active_handoff"], "project/handoffs/PH-001.md")
        self.assertIn("继续薄片实现", payload["active_handoff"]["content"])
        self.assertEqual([task["id"] for task in payload["ready"]], ["PH-001"])
        self.assertNotIn("allowed_paths", payload["ready"][0])
        self.assertEqual(
            payload["acceptance_commands"],
            [
                ".venv/bin/python manage.py check",
                ".venv/bin/python manage.py test tests --verbosity 1",
            ],
        )

    def test_orient_rejects_missing_sources_instead_of_guessing(self) -> None:
        self.write_fixture([make_task("PH-001")])

        code, payload, _ = self.run_cli("orient", "--root", str(self.root))
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("active_handoff_missing", self.error_codes(payload))

        manifest = make_manifest()
        manifest["active_handoff"] = "project/handoffs/PH-001.md"
        self.write_json("project/manifest.json", manifest)
        self.write("project/handoffs/PH-001.md", "# Handoff\n")

        code, payload, _ = self.run_cli("orient", "--root", str(self.root))
        self.assertEqual(code, 1)
        self.assertFalse(payload["ok"])
        self.assertIn("file_not_found", self.error_codes(payload))

    def test_draft_report_skeleton_never_predeclares_pass(self) -> None:
        acceptance = [
            "python scripts/projectctl.py check",
            "python manage.py test tests --verbosity 1",
        ]
        self.write_fixture(
            [
                make_task("PH-002", risk="S", acceptance=list(acceptance)),
                make_task("PH-003", risk="A", acceptance=list(acceptance), status="proposed"),
            ]
        )

        code, payload, _ = self.run_cli("draft-report", "PH-002", "--root", str(self.root))
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["report_path"], "project/reports/PH-002.json")
        report = payload["report"]
        self.assertEqual(report["task_id"], "PH-002")
        self.assertEqual(report["summary"], "")
        self.assertTrue(report["review"]["required"])
        self.assertEqual(report["review"]["status"], "pending")
        self.assertEqual([entry["result"] for entry in report["tests"]], ["not_run"] * 2)
        self.assertEqual([entry["command"] for entry in report["tests"]], acceptance)
        self.assertFalse(any(entry["result"] == "pass" for entry in report["tests"]))
        self.assertEqual([entry["exit_code"] for entry in report["tests"]], [None, None])

        ready_a = make_task("PH-003", risk="A", acceptance=list(acceptance))
        self.write_json(
            "project/tasks.json",
            {"schema_version": 1, "tasks": [ready_a]},
        )
        code, payload, _ = self.run_cli("draft-report", "PH-003", "--root", str(self.root))
        self.assertEqual(code, 0, payload)
        self.assertFalse(payload["report"]["review"]["required"])
        self.assertEqual(payload["report"]["review"]["status"], "not_required")

        code, payload, _ = self.run_cli("draft-report", "PH-999", "--root", str(self.root))
        self.assertEqual(code, 1)
        self.assertIn("task_not_found", self.error_codes(payload))


    def flow_fixture(self, tasks=None):
        manifest = make_manifest()
        manifest["active_handoff"] = "project/handoffs/PH-001.md"
        self.write_fixture(tasks or [make_task("PH-001")], manifest=manifest)
        self.write("project/handoffs/PH-001.md", "# Handoff\n")
        self.write("AGENTS.md", "## 验收与交付\n```sh\npython check\n```\n")

    def flow(self):
        code, payload, _ = self.run_cli("orient", "--root", str(self.root))
        self.assertEqual(code, 0, payload)
        return payload["delivery_flow"]

    def init_flow_history(self):
        self.init_repository()
        self.git("branch", "-M", "main")

    def test_flow_without_git_or_main_is_unavailable(self):
        self.flow_fixture()
        flow = self.flow()
        self.assertEqual(flow["history_status"], "unavailable")
        self.assertIsNone(flow["trailing_support_prs"])
        self.init_flow_history()
        self.assertEqual(self.flow()["history_reason"], "no_merged_pr_history")
        self.git("branch", "-m", "topic-only")
        self.assertEqual(self.flow()["history_reason"], "main_ref_unavailable")

    def test_flow_warns_on_three_support_prs_despite_feat_titles(self):
        self.flow_fixture()
        self.init_flow_history()
        for i in range(3):
            self.write("docs/note.md", str(i))
            self.commit(f"feat(PH-001): looks like a feature (#{i + 1})")
        flow = self.flow()
        self.assertEqual(flow["trailing_support_prs"], 3)
        self.assertEqual(flow["product_code_prs"], 0)
        self.assertIn("support_pr_streak", flow["warnings"])
        self.assertEqual(flow["history_ref"], "refs/heads/main")
        before = self.git("status", "--porcelain")
        self.flow()
        self.assertEqual(self.git("status", "--porcelain"), before)

    def test_flow_product_code_resets_streak_but_docs_and_tests_do_not(self):
        self.flow_fixture()
        self.init_flow_history()
        for i, path in enumerate(["core/README.md", "core/tests/test_example.py", "core/test_example.py", "core/tests.py"]):
            self.write(path, "# support\n")
            self.commit(f"feat(PH-001): support (#{i + 1})")
        self.assertEqual(self.flow()["trailing_support_prs"], 4)
        self.write("core/example.py", "value = 1\n")
        self.commit("docs(PH-001): title is not evidence (#4)")
        flow = self.flow()
        self.assertEqual(flow["trailing_support_prs"], 0)
        self.assertEqual(flow["product_code_prs"], 1)
        self.assertNotIn("support_pr_streak", flow["warnings"])

    def test_flow_docs_task_code_does_not_claim_a_product_delivery(self):
        self.flow_fixture([make_task("PH-001", kind="docs")])
        self.init_flow_history()
        self.write("core/example.py", "value = 1\n")
        self.commit("feat(PH-001): documentation task (#1)")
        self.assertEqual(self.flow()["product_code_prs"], 0)

    def test_flow_uses_main_first_parent_and_excludes_topic_work(self):
        self.flow_fixture()
        self.init_flow_history()
        self.write("docs/note.md", "main")
        self.commit("docs(PH-001): main (#1)")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("checkout", "-b", "topic")
        self.write("core/example.py", "value = 1\n")
        self.commit("feat(PH-001): unmerged (#2)")
        flow = self.flow()
        self.assertEqual(flow["history_ref"], "refs/remotes/origin/main")
        self.assertEqual(flow["sample_size"], 1)
        self.assertEqual(flow["trailing_support_prs"], 1)
        self.git("checkout", "main")
        self.git("merge", "--no-ff", "topic", "-m", "feat(PH-001): merged (#3)")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        flow = self.flow()
        self.assertEqual(flow["sample_size"], 2)
        self.assertEqual(flow["product_code_prs"], 1)

    def test_flow_bounds_pr_history_and_warns_on_product_wip(self):
        tasks = [make_task(f"PH-{i:03}", status="in_progress", owner="worker") for i in range(1, 4)]
        tasks.append(make_task("PH-004", kind="docs", status="in_progress", owner="planner"))
        self.flow_fixture(tasks)
        self.assertEqual(self.flow()["active_product_task_count"], 3)
        self.assertIn("product_wip_over_two", self.flow()["warnings"])
        self.init_flow_history()
        for i in range(22):
            self.write("docs/note.md", str(i))
            self.commit(f"docs(PH-004): support (#{i + 1})")
        flow = self.flow()
        self.assertEqual(flow["sample_size"], 20)
        self.assertEqual(flow["trailing_support_prs"], 20)

    def test_report_exit_code_rejects_contradictions_and_boolean_values(self):
        for result, exit_code in [("pass", 1), ("pass", None), ("pass", False),
                                  ("pass", 0.0), ("pass", "0"), ("fail", 0),
                                  ("fail", None), ("fail", True), ("not_run", 0)]:
            with self.subTest(result=result, exit_code=exit_code):
                report = make_report("PH-001")
                report["tests"][0].update(result=result, exit_code=exit_code)
                errors = projectctl._validate_report(report, "PH-001", "A")
                self.assertIn("test_exit_code_mismatch", {error["code"] for error in errors})

    def test_report_exit_code_accepts_valid_values_and_legacy_missing_field(self):
        report = make_report("PH-001")
        self.assertEqual(projectctl._validate_report(report, "PH-001", "A"), [])
        report["tests"][0]["exit_code"] = 0
        self.assertEqual(projectctl._validate_report(report, "PH-001", "A"), [])
        report["tests"].append(dict(command="optional", result="not_run", exit_code=None, evidence="not run"))
        self.assertEqual(projectctl._validate_report(report, "PH-001", "A"), [])
        report["tests"][1].update(result="fail", exit_code=2)
        errors = {error["code"] for error in projectctl._validate_report(report, "PH-001", "A")}
        self.assertIn("tests_failed", errors)
        self.assertNotIn("test_exit_code_mismatch", errors)

    def registration_fixture(self, risk="S"):
        manifest = make_manifest()
        manifest["repository_url"] = "https://github.com/example/project-hub"
        self.write_fixture([make_task("PH-001")], manifest=manifest)
        self.write_json("project/delivery-policy.json", {"checks": ["required"], "s_authorization": {"tasks": []}})
        base = self.init_repository()
        task = make_task("PH-002", risk=risk, github_issue="https://github.com/example/project-hub/issues/2",
                         handoff="project/handoffs/PH-002.md")
        spec = "# New task\nOne bounded user outcome.\n"
        self.write(task["spec"], spec)
        self.write(task["handoff"], "# Registration only; no implementation.\n")
        data = json.loads((self.root / "project/tasks.json").read_text())
        data["tasks"].append(task)
        self.write_json("project/tasks.json", data)
        report = make_report("PH-002", registration_only=True, authorization_source="user-test")
        report["review"] = dict(required=True, reviewer="independent", status="pass", findings=[])
        report["tests"].extend(dict(command=command, result="pass", evidence="fixture") for command in
                               ["python scripts/projectctl.py check", "python -m unittest tests.test_projectctl tests.test_governance -v"])
        for entry in report["tests"]:
            entry["exit_code"] = 0
        self.write_json("project/reports/PH-002.json", report)
        if risk == "S":
            fields = ("id", "title", "kind", "risk", "depends_on", "spec", "allowed_paths", "acceptance", "github_issue")
            digest = hashlib.sha256(json.dumps({"task": {key: task[key] for key in fields}, "spec": spec},
                                              sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
            self.write_json("project/delivery-policy.json", {"checks": ["required"], "s_authorization": {"tasks": [
                dict(task_id="PH-002", contract_digest=digest, source="user-test")]}})
        return base

    def test_registration_has_a_bounded_path_then_normal_implementation(self):
        base = self.registration_fixture()
        registered = self.commit("register new task")
        code, payload, _ = self.run_cli("verify-change", "--base", base, "--task", "PH-002")
        self.assertEqual(code, 0, payload)
        self.assertTrue(payload["registration_only"])
        self.assertFalse(payload["implementation_allowed"])
        self.write("safe.txt", "implementation\n")
        self.commit("try using registration report as implementation")
        code, payload, _ = self.run_cli("verify-change", "--base", registered, "--task", "PH-002")
        self.assertEqual(code, 1, payload)
        self.assertIn("registration_report_not_implementation", self.error_codes(payload))
        report = json.loads((self.root / "project/reports/PH-002.json").read_text())
        report.pop("registration_only")
        self.write_json("project/reports/PH-002.json", report)
        self.commit("actual implementation acceptance")
        code, payload, _ = self.run_cli("verify-change", "--base", registered, "--task", "PH-002")
        self.assertEqual(code, 0, payload)
        self.assertNotIn("registration_only", payload)

    def test_registration_lower_risk_still_requires_independent_s_review(self):
        base = self.registration_fixture(risk="A")
        self.commit("register A task with S review")
        code, payload, _ = self.run_cli("verify-change", "--base", base, "--task", "PH-002")
        self.assertEqual(code, 0, payload)
        report = json.loads((self.root / "project/reports/PH-002.json").read_text())
        report["review"] = dict(required=False, reviewer=None, status="not_required", findings=[])
        self.write_json("project/reports/PH-002.json", report)
        self.commit("remove independent review")
        self.assertEqual(self.run_cli("verify-change", "--base", base, "--task", "PH-002")[0], 1)

    def test_registration_rejects_existing_contract_changes_and_implementation(self):
        for mutation in ["old_task", "product", "checker", "workflow", "manifest", "second_task", "metadata", "not_ready", "issue", "unmet_dep"]:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                previous = self.root
                self.root = Path(directory)
                try:
                    base = self.registration_fixture()
                    data = json.loads((self.root / "project/tasks.json").read_text())
                    if mutation == "old_task": data["tasks"][0]["title"] = "changed"
                    elif mutation == "second_task": data["tasks"].append(make_task("PH-003"))
                    elif mutation == "metadata": data["untrusted"] = True
                    elif mutation == "not_ready": data["tasks"][-1]["status"] = "proposed"
                    elif mutation == "issue": data["tasks"][-1]["github_issue"] = None
                    elif mutation == "unmet_dep": data["tasks"][-1]["depends_on"] = ["PH-001"]
                    elif mutation == "product": self.write("core/example.py", "value = 1\n")
                    elif mutation == "checker": self.write("scripts/projectctl.py", "# bypass\n")
                    elif mutation == "workflow": self.write(".github/workflows/test.yml", "# bypass\n")
                    elif mutation == "manifest": self.write_json("project/manifest.json", make_manifest())
                    self.write_json("project/tasks.json", data)
                    self.commit("malicious registration")
                    self.assertEqual(self.run_cli("verify-change", "--base", base, "--task", "PH-002")[0], 1)
                finally:
                    self.root = previous

    def test_registration_rejects_policy_mutations_wrong_digest_and_binary_files(self):
        for mutation in ["policy", "digest", "source", "spec_binary", "symlink", "executable", "missing_checks", "missing_exit"]:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                previous = self.root
                self.root = Path(directory)
                try:
                    base = self.registration_fixture()
                    policy_path = self.root / "project/delivery-policy.json"
                    policy = json.loads(policy_path.read_text())
                    if mutation == "policy": policy["checks"] = []
                    elif mutation == "digest": policy["s_authorization"]["tasks"][-1]["contract_digest"] = "0" * 64
                    elif mutation == "source": policy["s_authorization"]["tasks"][-1]["source"] = "different"
                    elif mutation == "spec_binary": (self.root / "project/specs/PH-002.md").write_bytes(b"\xff\x00")
                    elif mutation == "symlink":
                        path = self.root / "project/specs/PH-002.md"
                        path.unlink()
                        path.symlink_to("PH-001.md")
                    elif mutation == "executable": os.chmod(self.root / "project/specs/PH-002.md", 0o755)
                    elif mutation == "missing_exit":
                        report = json.loads((self.root / "project/reports/PH-002.json").read_text())
                        report["tests"][0].pop("exit_code")
                        self.write_json("project/reports/PH-002.json", report)
                    elif mutation == "missing_checks":
                        report = json.loads((self.root / "project/reports/PH-002.json").read_text())
                        report["tests"] = report["tests"][:1]
                        self.write_json("project/reports/PH-002.json", report)
                    self.write_json("project/delivery-policy.json", policy)
                    self.commit("invalid registration evidence")
                    self.assertEqual(self.run_cli("verify-change", "--base", base, "--task", "PH-002")[0], 1)
                finally:
                    self.root = previous

if __name__ == "__main__":
    unittest.main()


class QueueDocumentTests(unittest.TestCase):
    """The ordered outcome queue is the single delivery entry (light delivery)."""

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(prefix="projectctl-queue-")
        self.addCleanup(self.temp_directory.cleanup)
        self.root = Path(self.temp_directory.name)

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def item(self, item_id: str, *, wave: str = "W1", status: str = "待办",
             checks: int = 3, outcome: str = "用户完成一个可评审的经营动作并看到结果。",
             demo: str = "`python scripts/demo.py`", legacy: str = "PH-001",
             requirements: str = "V1-OPS-08") -> str:
        lines = [
            f"### {item_id} · 成果 {item_id}",
            "",
            f"- 波次：{wave}",
            f"- 状态：{status}",
            f"- 用户可见成果：{outcome}",
            "- 验收检查：",
        ]
        lines.extend(f"  {index}. 检查 {index}" for index in range(1, checks + 1))
        lines.extend([
            f"- 演示方式：{demo}",
            f"- 遗留任务：{legacy}",
            f"- 需求：{requirements}",
            "",
        ])
        return "\n".join(lines)

    def document(self, *items: str) -> str:
        return "# 队列\n\n" + "\n".join(items)

    def tasks(self, *task_ids: str) -> list[dict[str, Any]]:
        return [make_task(task_id) for task_id in task_ids]

    def codes(self, parsed: dict[str, Any]) -> set[str]:
        return {error["code"] for error in parsed["issues"]}

    def test_missing_queue_is_reported_without_failing_offline_fixtures(self) -> None:
        parsed = projectctl.parse_queue(self.root)
        self.assertFalse(parsed["available"])
        self.assertEqual(parsed["items"], [])
        self.assertEqual(parsed["issues"], [])

    def test_valid_queue_parses_items_in_wave_order(self) -> None:
        self.write("project/QUEUE.md", self.document(
            self.item("Q-01"), self.item("Q-02", wave="W2", status="已交付")))
        parsed = projectctl.parse_queue(self.root, self.tasks("PH-001"))
        self.assertEqual(parsed["issues"], [])
        self.assertEqual([item["id"] for item in parsed["items"]], ["Q-01", "Q-02"])
        self.assertEqual(parsed["items"][1]["status"], "已交付")
        self.assertEqual(parsed["items"][0]["checks"], ["检查 1", "检查 2", "检查 3"])
        self.assertEqual(parsed["waves"], {"W1": 1, "W2": 1})
        summary = projectctl._queue_summary(parsed)
        self.assertEqual([item["id"] for item in summary["next"]], ["Q-01"])
        self.assertEqual(summary["delivered"], 1)

    def test_requirement_ids_are_checked_against_the_archived_trace(self) -> None:
        self.write("project/QUEUE.md", self.document(self.item("Q-01")))
        trace = {"requirements": [{"requirement_id": "V1-OPS-08"}]}
        self.write("docs/archive/v1.0-planning/requirement-trace.json",
                   json.dumps(trace))
        parsed = projectctl.parse_queue(self.root, self.tasks("PH-001"))
        self.assertEqual(parsed["issues"], [])
        self.write("project/QUEUE.md", self.document(
            self.item("Q-01", requirements="V1-NOPE-99")))
        self.assertIn("queue_requirement", self.codes(
            projectctl.parse_queue(self.root, self.tasks("PH-001"))))

    def test_invalid_blocks_report_each_contract_violation(self) -> None:
        cases = {
            "queue_wave": self.item("Q-01", wave="wave-one"),
            "queue_order": self.item("Q-01", wave="W2") + self.item("Q-02", wave="W1"),
            "queue_status": self.item("Q-01", status="done"),
            "queue_outcome": self.item("Q-01", outcome="短"),
            "queue_checks": self.item("Q-01", checks=2),
            "queue_demo": self.item("Q-01", demo=""),
            "queue_legacy": self.item("Q-01", legacy=""),
            "queue_requirement": self.item("Q-01", requirements=""),
        }
        for code, body in cases.items():
            with self.subTest(code=code):
                self.write("project/QUEUE.md", self.document(body))
                parsed = projectctl.parse_queue(self.root, self.tasks("PH-001"))
                self.assertIn(code, self.codes(parsed))

    def test_unknown_legacy_task_and_duplicate_item_are_rejected(self) -> None:
        self.write("project/QUEUE.md", self.document(
            self.item("Q-01", legacy="PH-999"), self.item("Q-01")))
        parsed = projectctl.parse_queue(self.root, self.tasks("PH-001"))
        self.assertIn("queue_duplicate", self.codes(parsed))
        self.assertIn("queue_legacy", self.codes(parsed))
        self.write("project/QUEUE.md", self.document(self.item("Q-01", legacy="not-a-task")))
        self.assertIn("queue_legacy", self.codes(
            projectctl.parse_queue(self.root, self.tasks("PH-001"))))
        self.write("project/QUEUE.md", self.document(self.item("Q-01", requirements="nope")))
        self.assertIn("queue_requirement", self.codes(
            projectctl.parse_queue(self.root, self.tasks("PH-001"))))

    def test_empty_queue_document_is_rejected(self) -> None:
        self.write("project/QUEUE.md", "# 队列\n\n没有条目。\n")
        parsed = projectctl.parse_queue(self.root, [])
        self.assertIn("queue_empty", self.codes(parsed))
        self.assertTrue(projectctl.parse_queue(self.root)["available"])


class QueueCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(prefix="projectctl-queue-cli-")
        self.addCleanup(self.temp_directory.cleanup)
        self.root = Path(self.temp_directory.name)

    write = ProjectCtlTests.write
    write_json = ProjectCtlTests.write_json
    write_fixture = ProjectCtlTests.write_fixture
    run_cli = ProjectCtlTests.run_cli
    error_codes = ProjectCtlTests.error_codes

    def test_queue_command_emits_summary_and_item_detail(self) -> None:
        self.write_fixture([make_task("PH-001")])
        self.write("project/QUEUE.md", "# 队列\n")
        self.write("project/QUEUE.md", "\n".join([
            "# 队列",
            "### Q-01 · 成果一",
            "- 波次：W1",
            "- 用户可见成果：用户完成一个可评审的经营动作并看到结果。",
            "- 验收检查：",
            "  1. 检查一",
            "  2. 检查二",
            "  3. 检查三",
            "- 演示方式：`python scripts/demo.py`",
            "- 遗留任务：PH-001",
            "- 需求：V1-OPS-08",
        ]))
        code, payload, _ = self.run_cli("queue", "--root", str(self.root))
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["item_count"], 1)
        self.assertEqual([item["id"] for item in payload["items"]], ["Q-01"])
        code, payload, _ = self.run_cli("queue", "--compact", "--root", str(self.root))
        self.assertEqual(code, 0, payload)
        self.assertNotIn("items", payload)
        self.assertEqual(payload["next"][0]["id"], "Q-01")

        self.write("project/QUEUE.md", "# 队列\n")
        code, payload, _ = self.run_cli("queue", "--root", str(self.root))
        self.assertEqual(code, 1)
        self.assertIn("queue_empty", self.error_codes(payload))

    def test_orient_surfaces_queue_summary(self) -> None:
        manifest = make_manifest()
        manifest["active_handoff"] = "project/handoffs/PH-001.md"
        self.write_fixture([make_task("PH-001")], manifest=manifest)
        self.write("project/handoffs/PH-001.md", "# Handoff\n")
        self.write("AGENTS.md", "## 验收与交付\n```sh\npython check\n```\n")
        code, payload, _ = self.run_cli("orient", "--root", str(self.root))
        self.assertEqual(code, 0, payload)
        self.assertFalse(payload["queue"]["available"])
        self.assertEqual(payload["delivery_flow"]["queue"], "project/QUEUE.md")
        self.assertEqual(payload["delivery_flow"]["rules"],
                         "docs/engineering/light-delivery.md")
        self.assertEqual(payload["delivery_flow"]["focus"], "project/STATUS.md")
