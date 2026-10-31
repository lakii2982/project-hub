"""Unit boundaries use fake model/GitHub calls; no live model or remote writes."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import sys
import unittest
from unittest import mock
import uuid

from tests.test_delivery_controller import ControllerFixture, TEST_SCOPE, write_file
from tooling.ai_delivery.delivery import DeliveryGate, GateError
from tooling.ai_delivery.delivery.receipt import same_binding
from tooling.ai_delivery.delivery.service import _read_final
from tooling.ai_delivery.delivery.verification import Verifier, VerificationError, parse_acceptance


class StubVerifier:
    def __init__(self):
        self.result = "PASS"
        self.mutate = None

    def verify(self, view, task):
        outcome = {key: view[key] for key in ("run_id", "base_sha", "contract_digest", "diff_digest")}
        outcome.update(result=self.result, limitations=[], checks=[
            {"command": c, "result": self.result, "exit_code": 0 if self.result == "PASS" else 1,
             "stdout_sha256": "0" * 64, "stderr_sha256": "0" * 64}
            for c in task["acceptance"]])
        if self.mutate:
            self.mutate(outcome)
        return outcome


class StubReviewer:
    def __init__(self, store):
        self.store = store
        self.calls = []
        self.answer = '{"verdict":"PASS","findings":[]}'
        self.change_context = False
        self.tool_echo_only = False
        self.missing_stdout = False
        self.delete_context = False

    def _runtime(self, identifier):
        runtime = self.store.root / "runtime" / identifier
        runtime.mkdir(parents=True, mode=0o700)
        return runtime

    def run_once(self, identifier, config):
        row = self.store.claim(identifier)
        self.calls.append(row["request"])
        log = self.store.root / "logs" / identifier
        log.mkdir(parents=True)
        event = ({"role": "tool", "content": self.answer} if self.tool_echo_only else
                 {"type": "result", "result": self.answer, "is_error": False})
        if not self.missing_stdout:
            (log / "stdout").write_text(json.dumps(event) + "\n")
        if self.delete_context:
            (self.store.root / "runtime" / identifier / "review-context.json").unlink()
        if self.change_context:
            (self.store.root / "runtime" / identifier / "review-context.json").write_text("{}")
        return self.store.finish(identifier, row["fencing_token"], "completed",
                                 result={"exit_code": 0})


class StubGitHub:
    def __init__(self):
        self.published = []
        self.merged = []

    def publish(self, view, title, body):
        self.published.append((view, title, body))
        return {"head": "a" * 40, "pr": 1, "base": view["base_sha"],
                "url": "https://github.com/example/project/pull/1", "status": "created"}

    def merge(self, pr, base, head):
        self.merged.append((pr, base, head))
        return {"status": "merged"}


class BaseGateFixture(ControllerFixture):
    def setUp(self):
        super().setUp()
        row = self.execute()
        self.source_id = row["run_id"]
        self.source = self.run_worktree(self.source_id)
        write_file(self.source, TEST_SCOPE, "public fixture\n")
        write_file(self.source, "project/reports/PH-103.json", "{}\n")
        write_file(self.source, "project/handoffs/PH-103.md", "fixture handoff\n")
        self.verifier = StubVerifier()
        self.reviewer = StubReviewer(self.store)
        self.github = StubGitHub()
        self.gate = DeliveryGate(self.controller, verifier=self.verifier,
                                 executor=self.reviewer, github=self.github)

    def prepared(self):
        check = self.gate.verify(self.source_id)
        review = self.gate.review(self.source_id, "claude_code")
        return check, review

class GateTests(BaseGateFixture):
    def test_actual_source_bound_receipts_and_explicit_merge(self):
        check, review = self.prepared()
        self.assertEqual(check["result"], "PASS")
        self.assertNotEqual(review["reviewer_run_id"], self.source_id)
        self.assertEqual(review["reviewer_agent"], "claude_code")
        self.assertEqual(self.reviewer.calls[0]["mode"], "review")
        self.assertNotEqual(Path(self.reviewer.calls[0]["worktree"]), self.source)
        receipt = self.gate.pr(self.source_id)
        self.assertEqual(receipt["result"], "PR_CREATED")
        self.assertEqual(self.github.merged, [])
        body = self.github.published[0][2]
        self.assertEqual([line for line in body.splitlines() if line.startswith("Task:")], ["Task: PH-103"])
        self.assertIn("Closes https://github.com/", body)
        self.assertEqual(self.gate.merge(self.source_id)["status"], "merged")
        self.assertEqual(len(self.github.merged), 1)

    def test_source_edit_invalidates_all_previous_evidence(self):
        self.prepared()
        write_file(self.source, TEST_SCOPE, "edited after acceptance\n")
        with self.assertRaises(GateError):
            self.gate.pr(self.source_id)
        self.assertEqual(self.github.published, [])

    def test_no_review_no_pr_even_if_report_claims_pass(self):
        self.gate.verify(self.source_id)
        with self.assertRaises(GateError):
            self.gate.pr(self.source_id)

    def test_source_actor_cannot_review_own_assembly_or_run(self):
        self.gate.verify(self.source_id)
        with self.assertRaisesRegex(GateError, "source_actor"):
            self.gate.review(self.source_id, "kimi_code")
        self.assertEqual(self.reviewer.calls, [])

    def test_failed_checks_cannot_start_review(self):
        self.verifier.result = "BLOCKING"
        self.assertEqual(self.gate.verify(self.source_id)["result"], "BLOCKING")
        with self.assertRaisesRegex(GateError, "verification_not_passed"):
            self.gate.review(self.source_id, "claude_code")

    def test_missing_frozen_command_refuses_receipt(self):
        self.verifier.mutate = lambda result: result["checks"].pop()
        with self.assertRaisesRegex(GateError, "checks_missing"):
            self.gate.verify(self.source_id)

    def test_mismatched_check_diff_refuses_receipt(self):
        self.verifier.mutate = lambda result: result.update(diff_digest="f" * 64)
        with self.assertRaisesRegex(GateError, "binding_mismatch"):
            self.gate.verify(self.source_id)

    def test_review_exit_zero_without_json_is_not_a_pass(self):
        self.gate.verify(self.source_id)
        self.reviewer.answer = "Looks good. PASS"
        with self.assertRaisesRegex(GateError, "not_json"):
            self.gate.review(self.source_id, "claude_code")

    def test_tool_echo_is_not_an_assistant_review(self):
        self.gate.verify(self.source_id)
        self.reviewer.tool_echo_only = True
        with self.assertRaisesRegex(GateError, "final_missing"):
            self.gate.review(self.source_id, "claude_code")

    def test_review_context_edit_refuses_receipt(self):
        self.gate.verify(self.source_id)
        self.reviewer.change_context = True
        with self.assertRaisesRegex(GateError, "context_changed"):
            self.gate.review(self.source_id, "claude_code")

    def test_missing_review_stdout_is_a_controlled_refusal(self):
        self.gate.verify(self.source_id)
        self.reviewer.missing_stdout = True
        with self.assertRaisesRegex(GateError, "review_output_missing"):
            self.gate.review(self.source_id, "claude_code")

    def test_deleted_review_context_is_a_controlled_refusal(self):
        self.gate.verify(self.source_id)
        self.reviewer.delete_context = True
        with self.assertRaisesRegex(GateError, "review_context_missing"):
            self.gate.review(self.source_id, "claude_code")

    def test_assembly_cannot_guess_missing_source_actor_list(self):
        view = self.controller.validate(self.source_id)
        identifier = str(uuid.uuid4())
        view.update(run_id=identifier, kind="assembly")
        with mock.patch.object(self.controller, "validate", return_value=view):
            with self.assertRaisesRegex(GateError, "source_actors_unknown"):
                self.gate._view(identifier)

    def test_pass_with_findings_is_refused(self):
        self.gate.verify(self.source_id)
        self.reviewer.answer = '{"verdict":"PASS","findings":[{"reason":"unresolved"}]}'
        with self.assertRaisesRegex(GateError, "has_findings"):
            self.gate.review(self.source_id, "claude_code")

    def test_missing_handoff_cannot_publish(self):
        (self.source / "project/handoffs/PH-103.md").unlink()
        self.prepared()
        with self.assertRaisesRegex(GateError, "document_missing"):
            self.gate.pr(self.source_id)

    def test_new_verification_requires_review_of_new_evidence(self):
        self.prepared()
        self.gate.verify(self.source_id)
        with self.assertRaisesRegex(GateError, "review_missing_or_stale"):
            self.gate.pr(self.source_id)


class VerificationSnapshotTests(BaseGateFixture):
    # GateTests exercises coordinator boundary; these execute no candidate code.
    def actual_verifier(self, mutate=None):
        root = self.tmp / ("verification-" + str(uuid.uuid4()))
        root.mkdir(mode=0o700)
        self.invocations = []

        def runner(argv, **kwargs):
            self.invocations.append((argv, kwargs))
            self.assertNotIn("GH_TOKEN", kwargs["env"])
            self.assertNotIn("SSH_AUTH_SOCK", kwargs["env"])
            self.assertNotIn("ANTHROPIC_API_KEY", kwargs["env"])
            self.assertFalse((Path(kwargs["cwd"]) / ".git").exists())
            if mutate:
                mutate(Path(kwargs["cwd"]))
            return {"exit_code": 0, "limitations": []}
        return Verifier(self.controller._repository(), root, runner=runner)

    def test_rebuilds_base_plus_manifest_with_clean_environment(self):
        view, contract = self.gate._view(self.source_id)
        verifier = self.actual_verifier()
        with mock.patch("tooling.ai_delivery.delivery.verification.sandbox_argv",
                        side_effect=lambda argv, **kw: list(argv)) as sandbox, \
             mock.patch.dict(os.environ, {"GH_TOKEN": "fixture-not-a-token"}):
            result = verifier.verify(view, contract["task"])
        self.assertEqual(result["result"], "PASS")
        self.assertEqual(len(self.invocations), len(contract["task"]["acceptance"]))
        self.assertTrue(all(call.kwargs["network"] is False for call in sandbox.call_args_list))
        self.assertNotEqual(Path(result["snapshot"]), self.source)

    def test_snapshot_mutation_during_check_blocks_proof(self):
        view, contract = self.gate._view(self.source_id)
        verifier = self.actual_verifier(lambda root: write_file(root, TEST_SCOPE, "tampered\n"))
        with mock.patch("tooling.ai_delivery.delivery.verification.sandbox_argv",
                        side_effect=lambda argv, **kw: list(argv)):
            result = verifier.verify(view, contract["task"])
        self.assertEqual(result["result"], "BLOCKING")
        self.assertIn("snapshot_manifest_mismatch", result["limitations"])

    def test_pinned_venv_path_is_preserved_and_other_interpreters_refused(self):
        verifier = self.actual_verifier()
        self.assertEqual(verifier.python, Path(sys.executable).absolute())
        with self.assertRaisesRegex(VerificationError, "python_runtime_not_supported"):
            Verifier(self.controller._repository(), verifier.root,
                     python=str(Path(sys.executable).absolute()) + "-different")

    def test_unsupported_backend_never_uses_unsandboxed_runner(self):
        from tooling.ai_delivery.execution.sandbox import IsolationError
        view, contract = self.gate._view(self.source_id)
        verifier = self.actual_verifier()
        with mock.patch("tooling.ai_delivery.delivery.verification.sandbox_argv",
                        side_effect=IsolationError("config_required")):
            result = verifier.verify(view, contract["task"])
        self.assertEqual(result["result"], "BLOCKING")
        self.assertEqual(self.invocations, [])


class AcceptanceParserTests(unittest.TestCase):
    def test_accepts_only_known_shapes(self):
        for command in [
            "python scripts/projectctl.py check",
            "python -m unittest tests.test_delivery_gate -v",
            "python -m unittest tests.test_delivery_agents tests.test_governance -v",
            "python -m unittest discover -s tests/delivery_e2e -v",
            "python manage.py check",
            "python manage.py test tests --verbosity 1",
            "python manage.py makemigrations --check --dry-run",
            "python manage.py verify_integrity --json",
            "git diff --check",
        ]:
            with self.subTest(command=command):
                self.assertTrue(Path(parse_acceptance(command)[0]).is_absolute())

    def test_rejects_shell_custom_scripts_inline_code_and_extra_flags(self):
        for command in ["python -c print(1)", "python arbitrary.py", "echo hello",
                        "python manage.py shell", "python -m unittest tests.x; echo bad",
                        "python -m unittest $(echo bad)", "python -m unittest tests.x > out",
                        "python -m unittest discover -s tests/../outside -v",
                        "python -m unittest tests.x --unknown"]:
            with self.subTest(command=command):
                with self.assertRaises(VerificationError):
                    parse_acceptance(command)

    def test_invalid_bindings_do_not_match(self):
        self.assertFalse(same_binding({}, {}))
        self.assertFalse(same_binding(None, None))
        value = {"run_id": str(uuid.uuid4()), "task_id": "PH-104", "repository": "owner/repo",
                 "base_sha": "a" * 40, "contract_digest": "b" * 64, "diff_digest": "c" * 64}
        self.assertTrue(same_binding(value, copy.deepcopy(value)))
        changed = dict(value, diff_digest="d" * 64)
        self.assertFalse(same_binding(value, changed))
        self.assertFalse(same_binding(dict(value, run_id="../outside"), dict(value, run_id="../outside")))


class GitHubGuardTests(ControllerFixture):
    def setUp(self):
        super().setUp()
        from tooling.ai_delivery.delivery.github import GitHubDelivery, _REQUIRED_CHECKS
        self.delivery = GitHubDelivery(self.controller._repository())
        self.head = "a" * 40
        self.protection = {name: 15368 for name in _REQUIRED_CHECKS}
        self.rows = [
            {"id": index + 1, "name": name, "app": {"id": app}, "head_sha": self.head,
             "status": "completed", "conclusion": "success"}
            for index, (name, app) in enumerate(sorted(self.protection.items()))]

    def check_rows(self, rows):
        with mock.patch.object(self.delivery, "_gh_json",
                               return_value={"total_count": len(rows), "check_runs": rows}):
            return self.delivery._checks(self.head, self.protection)

    def test_all_eight_current_head_and_correct_app_required(self):
        self.assertEqual(len(self.check_rows(self.rows)), 8)

    def test_wrong_head_app_or_missing_check_cannot_pass(self):
        from tooling.ai_delivery.delivery.github import DeliveryError
        variants = [
            self.rows[:-1],
            [dict(row, head_sha="f" * 40) for row in self.rows],
            [dict(row, app={"id": 999}) for row in self.rows],
        ]
        for rows in variants:
            with self.subTest(rows=len(rows)):
                with self.assertRaises(DeliveryError):
                    self.check_rows(rows)

    def test_newer_failed_retry_overrides_older_success(self):
        from tooling.ai_delivery.delivery.github import DeliveryError
        latest = dict(self.rows[0], id=999, conclusion="failure")
        with self.assertRaisesRegex(DeliveryError, "not_successful"):
            self.check_rows(self.rows + [latest])

    def test_another_apps_same_name_cannot_replace_protected_app(self):
        other = dict(self.rows[0], id=999, app={"id": 777}, conclusion="failure")
        self.assertEqual(len(self.check_rows(self.rows + [other])), 8)

    def test_pending_cancelled_and_neutral_are_not_success(self):
        from tooling.ai_delivery.delivery.github import DeliveryError
        for status, conclusion in [("in_progress", None), ("completed", "neutral"), ("completed", "cancelled")]:
            with self.subTest(conclusion=conclusion):
                with self.assertRaises(DeliveryError):
                    self.check_rows([dict(self.rows[0], status=status, conclusion=conclusion), *self.rows[1:]])

    def test_incomplete_api_page_cannot_be_guessed_green(self):
        from tooling.ai_delivery.delivery.github import DeliveryError
        with mock.patch.object(self.delivery, "_gh_json",
                               return_value={"total_count": 9, "check_runs": self.rows}):
            with self.assertRaisesRegex(DeliveryError, "incomplete"):
                self.delivery._checks(self.head, self.protection)

    def test_protection_must_remain_strict_and_exactly_eight(self):
        from tooling.ai_delivery.delivery.github import DeliveryError
        required = {"strict": True, "checks": [{"context": n, "app_id": app} for n, app in self.protection.items()]}
        payload = {"required_status_checks": required, "enforce_admins": {"enabled": True},
                   "required_linear_history": {"enabled": True},
                   "required_conversation_resolution": {"enabled": True},
                   "required_pull_request_reviews": {"required_approving_review_count": 0}}
        with mock.patch.object(self.delivery, "_gh_json", return_value=payload):
            self.assertEqual(self.delivery._protection(), self.protection)
        for broken in [dict(required, strict=False), dict(required, checks=required["checks"][:-1])]:
            with mock.patch.object(self.delivery, "_gh_json",
                                   return_value=dict(payload, required_status_checks=broken)):
                with self.assertRaises(DeliveryError):
                    self.delivery._protection()

    @staticmethod
    def thread_page(resolved, more=False, cursor=None):
        return {"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"isResolved": resolved}],
            "pageInfo": {"hasNextPage": more, "endCursor": cursor},
        }}}}}

    def test_unresolved_discussion_on_later_page_still_blocks(self):
        from tooling.ai_delivery.delivery.github import DeliveryError
        with mock.patch.object(self.delivery, "_gh_json", side_effect=[
                self.thread_page(True, True, "cursor"), self.thread_page(False)]):
            with self.assertRaisesRegex(DeliveryError, "unresolved"):
                self.delivery._threads(1)

    def test_unknown_review_state_refuses_merge(self):
        from tooling.ai_delivery.delivery.github import DeliveryError
        with mock.patch.object(self.delivery, "_gh_json", return_value={"errors": [{"message": "fixture"}]}):
            with self.assertRaises(DeliveryError):
                self.delivery._threads(1)

    def test_base_advance_after_checks_prevents_actual_merge(self):
        from tooling.ai_delivery.delivery.github import DeliveryError
        pr = {"pr": 1, "head": self.head, "base": self.BASE, "branch": "feat/PH-103-delivery-fixture"}
        state = {"state": "OPEN", "headRefOid": self.head, "headRefName": pr["branch"],
                 "baseRefName": "main", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}
        with mock.patch.object(self.delivery.repo, "refresh"), \
             mock.patch.object(self.delivery.repo, "base", side_effect=[self.BASE, "f" * 40]), \
             mock.patch.object(self.delivery, "_pr", return_value=state), \
             mock.patch.object(self.delivery, "_protection", return_value=self.protection), \
             mock.patch.object(self.delivery, "_checks", return_value=self.rows), \
             mock.patch.object(self.delivery, "_test_merge_sha", return_value="b" * 40), \
             mock.patch.object(self.delivery, "_threads"), \
             mock.patch.object(self.delivery, "_gh") as write:
            with self.assertRaisesRegex(DeliveryError, "base_changed"):
                self.delivery.merge(pr, self.BASE, self.head)
            write.assert_not_called()

    def test_head_change_after_checks_prevents_actual_merge(self):
        from tooling.ai_delivery.delivery.github import DeliveryError
        pr = {"pr": 1, "head": self.head, "base": self.BASE, "branch": "feat/PH-103-delivery-fixture"}
        state = {"state": "OPEN", "headRefOid": self.head, "headRefName": pr["branch"],
                 "baseRefName": "main", "mergeable": "MERGEABLE", "mergeStateStatus": "CLEAN"}
        with mock.patch.object(self.delivery.repo, "refresh"), \
             mock.patch.object(self.delivery.repo, "base", return_value=self.BASE), \
             mock.patch.object(self.delivery, "_pr", side_effect=[state, dict(state, headRefOid="f" * 40)]), \
             mock.patch.object(self.delivery, "_protection", return_value=self.protection), \
             mock.patch.object(self.delivery, "_checks", return_value=self.rows), \
             mock.patch.object(self.delivery, "_test_merge_sha", return_value="b" * 40), \
             mock.patch.object(self.delivery, "_threads"), \
             mock.patch.object(self.delivery, "_gh") as write:
            with self.assertRaisesRegex(DeliveryError, "head_changed"):
                self.delivery.merge(pr, self.BASE, self.head)
            write.assert_not_called()

    def test_unknown_or_conflicting_merge_state_blocks_before_merge(self):
        from tooling.ai_delivery.delivery.github import DeliveryError
        for mergeable, state in [("CONFLICTING", "DIRTY"), ("MERGEABLE", "UNKNOWN"),
                                 (None, "CLEAN")]:
            with self.subTest(mergeable=mergeable, state=state):
                data = {"state": "OPEN", "headRefOid": self.head,
                        "headRefName": "feat/PH-103-delivery-fixture", "baseRefName": "main",
                        "mergeable": mergeable, "mergeStateStatus": state}
                with self.assertRaisesRegex(DeliveryError, "not_mergeable"):
                    self.delivery._open(data, None, self.head)

    def test_remote_arbitration_requires_official_current_head_and_actions_event(self):
        policy = json.loads((Path(__file__).resolve().parent.parent /
                             "project/delivery-policy.json").read_text())
        summary = {"code_review": {"commit_sha": self.head, "status": "success",
                                    "has_any_findings": False, "has_unresolved_findings": False}}
        comment = {"id": 7, "user": {"id": 199175422,
                                      "login": "chatgpt-codex-connector[bot]", "type": "Bot"},
                   "body": f"<!-- codex-review-state:v1 {json.dumps(summary)} -->"}
        responses = [
            {"id": 1144995}, [comment],
            {"total_count": 1, "check_runs": [{"id": 9, "name": "Delivery arbitration", "app": {"id": 15368},
                              "head_sha": self.head, "status": "completed", "conclusion": "success",
                              "html_url": "https://github.com/example/project/actions/runs/42"}]},
            {"id": 42, "event": "pull_request", "head_sha": self.head,
             "path": ".github/workflows/delivery.yml", "status": "completed", "conclusion": "success"},
        ]
        with mock.patch.object(self.delivery, "_gh_json", side_effect=responses):
            result = self.delivery._arbitration(1, self.head, policy)
        self.assertEqual(result["actions_run_id"], 42)
        stale = dict(summary, code_review=dict(summary["code_review"], commit_sha="f" * 40))
        comment["body"] = f"<!-- codex-review-state:v1 {json.dumps(stale)} -->"
        from tooling.ai_delivery.delivery.github import DeliveryError
        with mock.patch.object(self.delivery, "_gh_json", side_effect=responses[:2]), \
             self.assertRaisesRegex(DeliveryError, "not_passed"):
            self.delivery._arbitration(1, self.head, policy)

    def test_publication_rebuilds_from_manifest_without_worker_git_authority(self):
        row = self.execute()
        source = self.run_worktree(row["run_id"])
        write_file(source, TEST_SCOPE, "candidate content\n")
        write_file(source, "project/reports/PH-103.json", "{}\n")
        write_file(source, "project/handoffs/PH-103.md", "handoff\n")
        (source / ".git").write_text("untrusted worker Git metadata\n")
        view = self.controller.validate(row["run_id"])
        real_git = self.delivery._git
        pushes = []

        def local_git(root, argv, operation):
            if argv[0] == "push":
                pushes.append((root, argv))
                return b""
            return real_git(root, argv, operation)
        with mock.patch.object(self.delivery, "_git", side_effect=local_git), \
             mock.patch.object(self.delivery, "_remote", return_value=None), \
             mock.patch.object(self.delivery, "_upsert_pr", return_value={"status": "created"}):
            result = self.delivery.publish(view, "PH-103 fixture", "Task: PH-103\n\nFixture delivery.\n")
        self.assertEqual(result["status"], "created")
        self.assertEqual(len(pushes), 1)
        self.assertNotEqual(Path(pushes[0][0]), source)
        self.assertEqual((source / TEST_SCOPE).read_text(), "candidate content\n")
        self.assertNotIn("--force", pushes[0][1])

    def test_publication_keeps_both_ends_of_a_rename(self):
        tasks = copy.deepcopy(self.tasks)
        next(task for task in tasks if task["id"] == "PH-103")["allowed_paths"].extend(
            ["tests/keep.txt", "tests/renamed.txt"])
        self.BASE = self.advance_origin(tasks=tasks, message="freeze rename fixture")
        row = self.execute()
        source = self.run_worktree(row["run_id"])
        (source / "tests/keep.txt").rename(source / "tests/renamed.txt")
        write_file(source, "project/reports/PH-103.json", "{}\n")
        write_file(source, "project/handoffs/PH-103.md", "handoff\n")
        view = self.controller.validate(row["run_id"])
        real_git = self.delivery._git
        def local_git(root, argv, operation):
            return b"" if argv[0] == "push" else real_git(root, argv, operation)
        with mock.patch.object(self.delivery, "_git", side_effect=local_git), \
             mock.patch.object(self.delivery, "_remote", return_value=None), \
             mock.patch.object(self.delivery, "_upsert_pr", return_value={"status": "created"}):
            self.assertEqual(self.delivery.publish(
                view, "PH-103 rename", "Task: PH-103\n\nRename fixture.\n")["status"], "created")
