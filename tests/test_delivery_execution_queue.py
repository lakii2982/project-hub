from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

from tooling.ai_delivery.execution.process import (
    ProcessUnknown,
    process_identity,
    stop_group,
)
from tooling.ai_delivery.execution.queue import (
    BusyError,
    ConflictError,
    LeaseError,
    RunStore,
)
from tooling.ai_delivery.execution.runner import Executor


def _claim_worker(root, request, output):
    try:
        claimed = RunStore(root).claim(request["run_id"])
        output.put(("ok", claimed["fencing_token"] if claimed else None))
    except Exception as exc:
        output.put((type(exc).__name__, str(exc)))


def _sleep_child():
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class QueueFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="delivery-queue-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        self.store = RunStore(self.root)
        self.worktree = Path(self.temp.name) / "worktree"
        self.worktree.mkdir()
        self.requests = []

    def request(self, **changes):
        run_id = changes.pop("run_id", str(uuid.uuid4()))
        request = {
            "run_id": run_id,
            "task_id": "PH-102",
            "base_sha": "a" * 40,
            "contract_digest": "b" * 64,
            "agent_id": "kimi_code",
            "repository": "example/project",
            "scope": [f"tooling/{run_id}.py"],
            "worktree": str(self.worktree),
            "deadline": int(time.time()) + 300,
            "attempt": 1,
            "mode": "implement",
            "prompt": "Implement the approved fixture task.",
            "expected_artifacts": ["project/reports/PH-102.json"],
        }
        request.update(changes)
        self.store.enqueue(request)
        self.requests.append(request)
        return request


class RunStoreTests(QueueFixture):
    def test_private_permissions_and_idempotent_enqueue(self):
        request = self.request()
        self.assertEqual(self.store.enqueue(request)["run_id"], request["run_id"])
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.root / "queue.db").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.root / "queue.lock").stat().st_mode), 0o600)
        stored = self.store.get(request["run_id"])
        self.assertEqual(stored["request"]["prompt"], request["prompt"])
        self.assertNotIn("env", stored["request"])

    def test_changed_run_id_request_conflicts_without_echoing_prompt(self):
        request = self.request()
        changed = dict(request, prompt="changed private fixture text")
        with self.assertRaises(ConflictError) as caught:
            self.store.enqueue(changed)
        self.assertNotIn("changed private fixture text", str(caught.exception))

    def test_validation_rejects_bad_identity_scope_and_worktree(self):
        cases = [
            {"run_id": "not-a-uuid"},
            {"base_sha": "A" * 40},
            {"contract_digest": "b" * 63},
            {"agent_id": "codex_controller"},
            {"mode": "continue"},
            {"attempt": 0},
            {"scope": ["../outside"]},
            {"worktree": "relative"},
            {"expected_artifacts": [1]},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.request(**changes)

    def test_multiprocess_claims_are_unique(self):
        request = self.request()
        # POSIX fork keeps the legacy unguarded manage.py entry from
        # recursively executing Django on spawn import; workers remain real processes.
        context = multiprocessing.get_context("fork")
        output = context.Queue()
        workers = [
            context.Process(target=_claim_worker, args=(str(self.root), request, output))
            for _ in range(2)
        ]
        for worker in workers:
            worker.start()
        results = [output.get(timeout=20) for _ in workers]
        for worker in workers:
            worker.join(20)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual([result[0] for result in results].count("ok"), 2)
        tokens = [result[1] for result in results if result[0] == "ok"]
        self.assertEqual(sum(token is not None for token in tokens), 1)

    def test_multiprocess_global_cap_is_three(self):
        requests = [self.request(scope=[f"parallel/{index}.py"]) for index in range(5)]
        # POSIX fork keeps the legacy unguarded manage.py entry from
        # recursively executing Django on spawn import; workers remain real processes.
        context = multiprocessing.get_context("fork")
        output = context.Queue()
        workers = [
            context.Process(target=_claim_worker, args=(str(self.root), request, output))
            for request in requests
        ]
        for worker in workers:
            worker.start()
        results = [output.get(timeout=20) for _ in workers]
        for worker in workers:
            worker.join(20)
            self.assertEqual(worker.exitcode, 0)
        self.assertEqual(sum(result[0] == "ok" and result[1] is not None for result in results), 3)
        self.assertEqual(sum(result[0] == "BusyError" for result in results), 2)
        self.assertEqual(len(self.store.active()), 3)

    def test_global_cap_and_conservative_scope_overlap(self):
        first = self.request(scope=["tooling/a.py", "tests/a.py"])
        self.request(scope=["tooling/b.py"])
        self.request(scope=["separate/file.py"])
        overlapping = self.request(scope=["tooling/*.py"])
        independent = self.request(scope=["other/file.py"])
        self.assertIsNotNone(self.store.claim(first["run_id"]))
        for request in self.requests[1:3]:
            self.assertIsNotNone(self.store.claim(request["run_id"]))
        with self.assertRaises(BusyError):
            self.store.claim(independent["run_id"])
        self.store.finish(
            first["run_id"], self.store.get(first["run_id"])["fencing_token"], "failed"
        )
        self.assertIsNone(self.store.claim(overlapping["run_id"]))
        self.assertIsNotNone(self.store.claim(independent["run_id"]))

    def test_stale_fencing_and_expired_lease_cannot_finish(self):
        request = self.request()
        claimed = self.store.claim(request["run_id"])
        self.store.renew(request["run_id"], claimed["fencing_token"], lease_seconds=90)
        with self.assertRaises(LeaseError):
            self.store.finish(request["run_id"], "stale", "completed")
        self.store.expire(request["run_id"], claimed["fencing_token"])
        with self.assertRaises(LeaseError):
            self.store.finish(request["run_id"], claimed["fencing_token"], "completed")

    def test_recover_dispatched_without_identity_quarantines(self):
        request = self.request()
        claimed = self.store.claim(request["run_id"])
        self.store.dispatch(request["run_id"], claimed["fencing_token"])
        self.store.expire(request["run_id"], claimed["fencing_token"])
        recovered = self.store.recover()
        self.assertEqual(recovered[0]["state"], "quarantined")
        self.assertEqual(self.store.get(request["run_id"])["state"], "quarantined")
        self.assertEqual(len(self.store.active()), 1)

    def test_unknown_live_identity_stays_quarantined_and_keeps_scope(self):
        request = self.request()
        claimed = self.store.claim(request["run_id"])
        self.store.dispatch(request["run_id"], claimed["fencing_token"])
        self.store.start(
            request["run_id"], claimed["fencing_token"], 12345, [12345, 12345, 999]
        )
        self.store.expire(request["run_id"], claimed["fencing_token"])

        def unknown(_pid):
            raise ProcessUnknown("identity unavailable")

        self.assertEqual(self.store.recover(identity=unknown)[0]["state"], "quarantined")
        blocked = self.request(scope=["tooling"])
        self.assertIsNone(self.store.claim(blocked["run_id"]))

    def test_events_are_append_only_and_bounded(self):
        request = self.request()
        with self.assertRaises(ValueError):
            self.store.event(request["run_id"], "oversized", {"text": "x" * 65537})
        self.store.event(request["run_id"], "fixture", {"ok": True})
        events = [event for event in self.store.events(request["run_id"]) if event["type"] == "fixture"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"], {"ok": True})


class ProcessTests(unittest.TestCase):
    def test_current_process_identity_is_exact_and_revalidated(self):
        identity = process_identity(os.getpid())
        self.assertEqual(identity, process_identity(os.getpid()))
        self.assertEqual(identity[0], os.getpid())
        self.assertTrue(all(isinstance(value, int) for value in identity))

    def test_mismatched_identity_never_signals_live_group(self):
        child = _sleep_child()
        self.addCleanup(self._kill_child, child)
        identity = process_identity(child.pid)
        with self.assertRaises(ProcessUnknown):
            stop_group([identity[0], identity[1], identity[2] + 1], timeout=0.1)
        self.assertIsNone(child.poll())

    @staticmethod
    def _kill_child(child):
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)


class ExecutorTests(QueueFixture):
    def setUp(self):
        super().setUp()
        self.launch_count = 0

    def executable(self, body):
        path = Path(self.temp.name) / f"agent-{len(self.requests)}-{self.launch_count}.py"
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o700)
        return path

    def executor(self, executable):
        def adapter(_config, _prompt, worktree, _runtime, review=False):
            return type("LaunchSpec", (), {
                "argv": [str(executable)],
                "env": {"PATH": os.environ.get("PATH", "")},
                "stdin": None,
                "cwd": Path(worktree),
                "read_roots": (),
                "write_roots": (Path(worktree),),
                "write_patterns": (),
            })()

        def sandbox(argv, **_kwargs):
            return argv

        self.launch_count += 1
        return Executor(self.store, test_adapter=adapter, test_sandbox=sandbox)

    def test_success_is_process_evidence_not_task_done(self):
        request = self.request()
        executable = self.executable("print('provider fixture evidence')\n")
        result = self.executor(executable).run_once(request["run_id"], object())
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["result"]["exit_code"], 0)
        self.assertNotIn("task_state", result["result"])
        self.assertNotIn("provider fixture evidence", json.dumps(result))

    def test_cancel_and_recover_do_not_double_launch(self):
        marker = self.worktree / "launch-count"
        request = self.request(deadline=int(time.time()) + 60)
        executable = self.executable(
            "import pathlib, time\n"
            f"p = pathlib.Path({str(marker)!r})\n"
            "p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')\n"
            "time.sleep(60)\n"
        )
        executor = self.executor(executable)
        thread = threading.Thread(target=executor.run_once, args=(request["run_id"], object()))
        thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.02)
        self.assertTrue(marker.exists())
        cancelled = executor.cancel(request["run_id"])
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertIn(cancelled["state"], {"cancelled", "quarantined"})
        executor.recover()
        self.assertEqual(marker.read_text(), "1")

    def test_large_bidirectional_io_does_not_deadlock(self):
        request = self.request()
        payload = b"x" * (1024 * 1024)
        executable = self.executable(
            "import sys\n"
            "data = sys.stdin.buffer.read()\n"
            "assert len(data) == 1048576\n"
            "sys.stdout.buffer.write(b'o' * 1048576)\n"
            "sys.stderr.buffer.write(b'e' * 1048576)\n"
        )
        executor = self.executor(executable)
        original_adapter = executor.test_adapter

        def with_stdin(config, prompt, worktree, runtime, review=False):
            spec = original_adapter(config, prompt, worktree, runtime, review=review)
            spec.stdin = payload
            return spec

        executor.test_adapter = with_stdin
        result = executor.run_once(request["run_id"], object())
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["result"]["stdout_bytes"], 1048576)
        self.assertEqual(result["result"]["stderr_bytes"], 1048576)

    def test_output_digest_matches_private_bounded_log(self):
        request = self.request()
        executable = self.executable("print('safe fixture output')\n")
        result = self.executor(executable).run_once(request["run_id"], object())
        log = self.root / "logs" / request["run_id"] / "stdout"
        self.assertEqual(
            result["result"]["stdout_sha256"], hashlib.sha256(log.read_bytes()).hexdigest()
        )
        self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
