from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


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


if __name__ == "__main__":
    unittest.main()
