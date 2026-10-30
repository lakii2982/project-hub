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


def make_task(task_id: str, allowed_paths: list[str], status: str = "ready") -> dict[str, Any]:
    return {
        "id": task_id,
        "title": f"Task {task_id}",
        "kind": "feature",
        "risk": "A",
        "status": status,
        "priority": 2,
        "milestone": "M1",
        "depends_on": [],
        "spec": f"project/specs/{task_id}.md",
        "allowed_paths": allowed_paths,
        "acceptance": ["true"],
        "github_issue": None,
        "owner": None,
        "blockers": [],
        "handoff": None,
    }


def make_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "name": "project-hub",
        "repository_url": "https://example.invalid/project-hub.git",
        "version_source": "pyproject.toml",
        "current_iteration": "M1",
        "tasks_file": "project/tasks.json",
        "active_handoff": None,
        "required_docs": [],
    }


class LessonsCliTests(unittest.TestCase):
    """Tests for the read-only lessons/metrics commands and brief --precedents."""

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory(prefix="projectctl-lessons-")
        self.addCleanup(self.temp_directory.cleanup)
        self.root = Path(self.temp_directory.name)

    def write_json(self, relative: str, data: Any) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write_raw(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_standard_fixtures(self) -> None:
        """PH-001 (docs, done, lessons), PH-002 (docs, ready, no report),
        PH-003 (app, done, blocker finding), PH-004 (unparsable report)."""
        self.write_json("project/manifest.json", make_manifest())
        self.write_json(
            "project/tasks.json",
            {
                "schema_version": 1,
                "tasks": [
                    make_task("PH-001", ["docs/standards.md"], status="done"),
                    make_task("PH-002", ["docs/brief-target.md"]),
                    make_task("PH-003", ["app/models.py"], status="done"),
                    make_task("PH-004", ["app/views.py"], status="done"),
                ],
            },
        )
        self.write_json(
            "project/reports/PH-001.json",
            {
                "task_id": "PH-001",
                "summary": "Docs slice.",
                "tests": [],
                "review": {
                    "required": True,
                    "reviewer": "reviewer",
                    "status": "pass",
                    "findings": [
                        "BLOCKER 1 - CLOSED: pre-declared evidence was rejected.",
                        {"finding": "stale link target", "resolution": "repointed to archive"},
                    ],
                },
                "deviations": ["Risk raised from A to S."],
                "unverified": ["wall-clock improvement not measured offline"],
            },
        )
        self.write_json(
            "project/reports/PH-003.json",
            {
                "task_id": "PH-003",
                "summary": "Slice.",
                "tests": [],
                "review": {"required": False, "reviewer": None, "status": "not_required", "findings": []},
                "deviations": [],
                "unverified": [],
            },
        )
        self.write_raw("project/reports/PH-004.json", "{not json")

    def run_cli(self, *arguments: str) -> tuple[int, dict[str, Any]]:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            payload = json.loads(completed.stdout)
        except ValueError:
            self.fail(f"non-JSON output: {completed.stdout!r} stderr={completed.stderr!r}")
        return completed.returncode, payload

    def test_lessons_extracts_all_sources_and_counts_skipped(self) -> None:
        self.write_standard_fixtures()
        code, payload = self.run_cli("lessons")
        self.assertEqual(0, code)
        self.assertTrue(payload["ok"])
        self.assertEqual(2, payload["reports_scanned"])
        self.assertEqual(1, payload["reports_skipped"])
        text_all = " ".join(
            entry["text"] for entry in payload["lessons"] if entry["task_id"] == "PH-001"
        )
        self.assertIn("BLOCKER 1 - CLOSED", text_all)
        self.assertIn("stale link target → repointed to archive", text_all)
        self.assertIn("Risk raised from A to S.", text_all)
        self.assertIn("wall-clock improvement", text_all)

    def test_lessons_path_filter_restricts_to_matching_tasks(self) -> None:
        self.write_standard_fixtures()
        code, payload = self.run_cli("lessons", "--path", "docs/")
        self.assertEqual(0, code)
        self.assertEqual({"PH-001"}, {entry["task_id"] for entry in payload["lessons"]})
        self.assertEqual("docs/", payload["path_filter"])

    def test_brief_precedents_injects_same_module_and_excludes_self(self) -> None:
        self.write_standard_fixtures()
        code, payload = self.run_cli("brief", "PH-002", "--precedents")
        self.assertEqual(0, code)
        precedents = payload["precedents"]
        self.assertEqual(["docs"], precedents["modules"])
        self.assertGreater(precedents["bytes"], 0)
        self.assertEqual({"PH-001"}, {entry["task_id"] for entry in precedents["entries"]})
        # PH-001 must not see its own lessons; PH-003 lives in another module.
        code, payload = self.run_cli("brief", "PH-001", "--precedents")
        self.assertEqual(0, code)
        self.assertEqual([], payload["precedents"]["entries"])
        self.assertEqual(0, payload["precedents"]["bytes"])

    def test_brief_without_flag_keeps_output_shape(self) -> None:
        self.write_standard_fixtures()
        code, payload = self.run_cli("brief", "PH-002")
        self.assertEqual(0, code)
        self.assertNotIn("precedents", payload)

    def test_brief_precedents_respects_entry_and_snippet_bounds(self) -> None:
        self.write_standard_fixtures()
        long_text = "x" * 900
        self.write_json(
            "project/reports/PH-000.json",
            {
                "task_id": "PH-000",
                "summary": "Slice.",
                "tests": [],
                "review": {"required": False, "reviewer": None, "status": "pass", "findings": [long_text]},
                "deviations": [f"deviation-{index}" for index in range(6)],
                "unverified": [],
            },
        )
        self.write_json(
            "project/tasks.json",
            {
                "schema_version": 1,
                "tasks": [
                    make_task("PH-000", ["docs/old.md"], status="done"),
                    make_task("PH-001", ["docs/standards.md"], status="done"),
                    make_task("PH-002", ["docs/brief-target.md"]),
                    make_task("PH-003", ["app/models.py"], status="done"),
                    make_task("PH-004", ["app/views.py"], status="done"),
                ],
            },
        )
        code, payload = self.run_cli("brief", "PH-002", "--precedents")
        self.assertEqual(0, code)
        entries = payload["precedents"]["entries"]
        self.assertLessEqual(len(entries), 5)
        for entry in entries:
            self.assertLessEqual(len(entry["text"]), 280)
            self.assertLessEqual(
                sum(1 for other in entries if other["task_id"] == entry["task_id"]),
                2,
            )

    def test_metrics_counts_findings_blockers_and_deviations(self) -> None:
        self.write_standard_fixtures()
        code, payload = self.run_cli("metrics")
        self.assertEqual(0, code)
        summary = payload["summary"]
        self.assertEqual(2, summary["reports_scanned"])
        self.assertEqual(1, summary["reports_skipped"])
        self.assertEqual(2, summary["findings"])
        self.assertEqual(1, summary["blockers"])
        self.assertEqual(1, summary["deviations"])
        self.assertEqual(1, summary["unverified"])
        per_task = {entry["task_id"]: entry for entry in payload["metrics"]}
        self.assertEqual(1, per_task["PH-001"]["blockers"])
        self.assertEqual("pass", per_task["PH-001"]["review_status"])

    def test_universal_paths_carry_no_module_signal(self) -> None:
        """Governance paths present in most whitelists must not glue modules together."""
        self.write_json("project/manifest.json", make_manifest())
        self.write_json(
            "project/tasks.json",
            {
                "schema_version": 1,
                "tasks": [
                    make_task(
                        "PH-101",
                        ["project/tasks.json", "docs/engineering/standards.md"],
                        status="done",
                    ),
                    make_task("PH-102", ["project/tasks.json", "core/services/cash.py"]),
                    make_task(
                        "PH-103",
                        ["project/tasks.json", "core/services/refund.py"],
                        status="done",
                    ),
                ],
            },
        )
        self.write_json(
            "project/reports/PH-101.json",
            {
                "task_id": "PH-101",
                "summary": "Docs slice.",
                "tests": [],
                "review": {
                    "required": True,
                    "reviewer": "reviewer",
                    "status": "pass",
                    "findings": ["Docs lesson that must not leak into core tasks."],
                },
                "deviations": [],
                "unverified": [],
            },
        )
        self.write_json(
            "project/reports/PH-103.json",
            {
                "task_id": "PH-103",
                "summary": "Slice.",
                "tests": [],
                "review": {"required": False, "reviewer": None, "status": "not_required", "findings": []},
                "deviations": [],
                "unverified": [],
            },
        )
        code, payload = self.run_cli("brief", "PH-102", "--precedents")
        self.assertEqual(0, code)
        # Without universality filtering, PH-101's docs lesson would leak in via "project".
        self.assertEqual([], payload["precedents"]["entries"])
        self.assertEqual(0, payload["precedents"]["bytes"])

    def test_universal_module_keys_carry_no_signal_either(self) -> None:
        """Per-task artifact dirs (tests/, project/reports) glue everything if unfiltered."""
        self.write_json("project/manifest.json", make_manifest())
        self.write_json(
            "project/tasks.json",
            {
                "schema_version": 1,
                "tasks": [
                    make_task(
                        "PH-201",
                        ["project/reports/PH-201.json", "tests/test_a.py", "pmo_plugins/settlement/models.py"],
                        status="done",
                    ),
                    make_task(
                        "PH-202",
                        ["project/reports/PH-202.json", "tests/test_b.py", "core/services/cash.py"],
                    ),
                    make_task(
                        "PH-203",
                        ["project/reports/PH-203.json", "tests/test_c.py", "pmo_plugins/cash/views.py"],
                        status="done",
                    ),
                ],
            },
        )
        self.write_json(
            "project/reports/PH-201.json",
            {
                "task_id": "PH-201",
                "summary": "Slice.",
                "tests": [],
                "review": {
                    "required": True,
                    "reviewer": "reviewer",
                    "status": "pass",
                    "findings": ["Settlement lesson."],
                },
                "deviations": [],
                "unverified": [],
            },
        )
        self.write_json(
            "project/reports/PH-203.json",
            {
                "task_id": "PH-203",
                "summary": "Slice.",
                "tests": [],
                "review": {"required": False, "reviewer": None, "status": "not_required", "findings": []},
                "deviations": [],
                "unverified": [],
            },
        )
        code, payload = self.run_cli("brief", "PH-202", "--precedents")
        self.assertEqual(0, code)
        # "tests" (3/3 tasks) and "project/reports" (3/3) are universal module keys;
        # the only remaining modules (settlement vs cash) do not overlap.
        self.assertEqual([], payload["precedents"]["entries"])
        self.assertEqual(0, payload["precedents"]["bytes"])

    def test_commands_never_write_files(self) -> None:
        self.write_standard_fixtures()
        snapshot = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        for arguments in (["lessons"], ["metrics"], ["brief", "PH-002", "--precedents"]):
            code, _payload = self.run_cli(*arguments)
            self.assertEqual(0, code)
        after = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*"))
        self.assertEqual(snapshot, after)


if __name__ == "__main__":
    unittest.main()
