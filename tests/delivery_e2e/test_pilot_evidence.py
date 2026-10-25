"""Offline audit of the PH-105 four-CLI pilot evidence (public record, no network/models/shell)."""

import json
import unittest
from pathlib import Path

FIXTURE = Path(__file__).with_name("pilot-evidence.json")
REPOSITORY = "lakii2982/project-hub"
AGENT_IDS = ("kimi_code", "opencode", "dsh", "claude_code")
TASK_IDS = ("PH-104", "PH-107")
BINDING_FIELDS = ("run_id", "task_id", "repository", "base_sha", "contract_digest", "diff_digest")
REQUIRED_CHECKS = (
    "CodeQL (javascript-typescript)", "CodeQL (python)", "Container smoke",
    "MCP protocol", "PostgreSQL domain", "Project governance",
    "Public hygiene", "SQLite domain",
)
PR_PREFIX = "https://github.com/lakii2982/project-hub/pull/"


def _load():
    with FIXTURE.open(encoding="utf-8") as handle:
        return json.load(handle)


def _is_hex(value, length):
    return (isinstance(value, str) and len(value) == length
            and all(c in "0123456789abcdef" for c in value))

def _sha256(test, value, label):
    test.assertTrue(_is_hex(value, 64), "expected SHA256 for %s" % label)


def _interval(row):
    return row["process_started_at"], row["process_finished_at"]


def _completed_by_agent(evidence):
    groups = {}
    for row in evidence["development"]:
        if row["state"] == "completed":
            groups.setdefault(row["agent_id"], []).append(row)
    return groups


class PilotEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.evidence = _load()

    def test_all_four_native_clis_completed_real_writes(self):
        self.assertEqual(self.evidence["schema_version"], 1)
        self.assertEqual(self.evidence["repository"], REPOSITORY)
        groups = _completed_by_agent(self.evidence)
        for agent_id in AGENT_IDS:
            self.assertIn(agent_id, groups, agent_id)
            for row in groups[agent_id]:
                start, finish = _interval(row)
                self.assertIsInstance(start, int)
                self.assertIsInstance(finish, int)
                self.assertGreater(finish, start, agent_id)
                self.assertTrue(row["run_id"], agent_id)
                self.assertTrue(_is_hex(row["base_sha"], 40), agent_id)
                for field in ("contract_digest", "diff_digest", "stdout_sha256"):
                    _sha256(self, row[field], field)
                self.assertTrue(row["manifest"], agent_id)
                for entry in row["manifest"]:
                    path = entry["path"]
                    self.assertTrue(path and not Path(path).is_absolute(), path)
                    self.assertNotIn("..", Path(path).parts, path)
                    _sha256(self, entry["new"]["sha256"], path)

    def test_two_different_clis_overlap(self):
        groups = _completed_by_agent(self.evidence)
        pairs = set()
        for left in AGENT_IDS:
            for right in AGENT_IDS:
                if left >= right:
                    continue
                for a in groups.get(left, []):
                    for b in groups.get(right, []):
                        a_start, a_end = _interval(a)
                        b_start, b_end = _interval(b)
                        if max(a_start, b_start) < min(a_end, b_end):
                            pairs.add((left, right))
        self.assertTrue(pairs, "no overlap between different CLIs")

    def _assert_delivery(self, task_id, delivery):
        source = delivery["source"]
        self.assertEqual(source["task_id"], task_id)
        self.assertEqual(source["repository"], REPOSITORY)
        self.assertTrue(source["run_id"])
        self.assertTrue(_is_hex(source["base_sha"], 40))
        for field in ("contract_digest", "diff_digest"):
            _sha256(self, source[field], field)
        verification = delivery["verification"]
        self.assertEqual(verification["result"], "PASS")
        self.assertTrue(verification["checks"])
        self.assertEqual({check["command"] for check in verification["checks"]},
                         set(delivery["acceptance"]))
        for field in BINDING_FIELDS:
            self.assertEqual(verification[field], source[field], field)
        for check in verification["checks"]:
            self.assertEqual(check["result"], "PASS")
            self.assertEqual(check["exit_code"], 0)
            self.assertTrue(check["command"])
        review = delivery["review"]
        self.assertEqual(review["result"], "PASS")
        self.assertEqual(review["findings"], [])
        for field in BINDING_FIELDS:
            self.assertEqual(review[field], source[field], field)
        self.assertTrue(review["reviewer_run_id"])
        self.assertNotEqual(review["reviewer_run_id"], source["run_id"])
        self.assertTrue(delivery["source_actors"])
        self.assertIn(review["reviewer_agent"], AGENT_IDS)
        self.assertNotIn(review["reviewer_agent"], delivery["source_actors"])
        pull = delivery["pull_request"]
        head = pull["head"]
        self.assertTrue(_is_hex(head, 40))
        self.assertTrue(_is_hex(pull["merge_commit"], 40))
        self.assertTrue(pull["url"].startswith(PR_PREFIX))
        self.assertTrue(pull["url"][len(PR_PREFIX):].isdigit())
        self.assertEqual(len(pull["checks"]), 8)
        names = {check["name"] for check in pull["checks"]}
        self.assertEqual(names, set(REQUIRED_CHECKS))
        for check in pull["checks"]:
            self.assertEqual(check["app_id"], 15368)
            self.assertEqual(check["status"], "completed")
            self.assertEqual(check["conclusion"], "success")
        self.assertTrue(set(REQUIRED_CHECKS) <= {ci["name"] for ci in pull["ci"]})
        for ci in pull["ci"]:
            self.assertEqual(ci["app_id"], 15368)
            self.assertEqual(ci["head_sha"], head)
            self.assertEqual(ci["conclusion"], "success")
            self.assertTrue(ci["url"].startswith("https://github.com/lakii2982/project-hub/"))
            self.assertNotIn(" ", ci["url"])

    def test_deliveries_bind_independent_verification_and_ci(self):
        deliveries = {item["task_id"]: item for item in self.evidence["deliveries"]}
        for task_id in TASK_IDS:
            self.assertIn(task_id, deliveries)
            self._assert_delivery(task_id, deliveries[task_id])

    def test_cancel_recovery_and_issue_revalidation(self):
        cancel = self.evidence["cancel_recovery"]
        self.assertTrue(cancel["run_id"])
        self.assertEqual(cancel["cancel_state"], "cancelled")
        self.assertEqual(cancel["new_process_state"], "cancelled")
        self.assertEqual(cancel["recover_exit_code"], 0)
        _sha256(self, cancel["recover_stdout_sha256"], "recover stdout")
        events = cancel["event_types"]
        self.assertIn("cancellation_requested", events)
        self.assertIn("cancelled", events)
        self.assertLess(events.index("cancellation_requested"), events.index("cancelled"))
        issue = self.evidence["issue_revalidation"]
        self.assertEqual(issue["number"], 32)
        self.assertEqual(issue["final_state"], "closed")
        self.assertEqual(issue["test_exit_code"], 0)
        _sha256(self, issue["test_stdout_sha256"], "issue stdout")
        _sha256(self, issue["test_stderr_sha256"], "issue stderr")
        self.assertIs(issue["done_task_rejected"], True)
        recorded = {}
        for event in issue["events"]:
            recorded.setdefault(event["event"], []).append(event["id"])
        self.assertIn("reopened", recorded)
        self.assertIn("closed", recorded)
        self.assertLess(min(recorded["reopened"]), max(recorded["closed"]))
