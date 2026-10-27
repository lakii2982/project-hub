from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from tooling.ai_delivery.execution.process import (
    ProcessGone,
    ProcessUnknown,
    group_empty,
    process_identity,
    stop_group,
)
from tooling.ai_delivery.execution.queue import RunStore
from tooling.ai_delivery.execution.runner import Executor


class DeliveryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="delivery-execution-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "private"
        self.store = RunStore(self.root)
        self.worktree = Path(self.temp.name) / "worktree"
        self.worktree.mkdir()
        self.launch_calls = []
        self.sandbox_calls = []
        self.executable_count = 0

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
        return request

    def enqueue(self, **changes):
        request = self.request(**changes)
        self.store.enqueue(request)
        return request

    def executable(self, body):
        self.executable_count += 1
        path = Path(self.temp.name) / f"fixture-agent-{self.executable_count}.py"
        path.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
        path.chmod(0o700)
        return path

    def adapter(self, executable):
        def build(config, prompt, worktree, runtime, review=False):
            self.launch_calls.append({
                "config": config,
                "prompt": prompt,
                "worktree": Path(worktree),
                "runtime": Path(runtime),
                "review": review,
            })
            return type("LaunchSpec", (), {
                "argv": [str(executable)],
                "env": {"PATH": os.environ.get("PATH", "")},
                "stdin": None,
                "cwd": Path(worktree),
                "read_roots": (),
                "write_roots": (Path(worktree),),
                "write_patterns": (),
            })()

        return build

    def sandbox(self, argv, **kwargs):
        self.sandbox_calls.append((tuple(argv), kwargs))
        return list(argv)

    def executor(self, executable, **kwargs):
        return Executor(
            self.store,
            test_adapter=self.adapter(executable),
            test_sandbox=self.sandbox,
            **kwargs,
        )

    def run_once(self, request, executable, **kwargs):
        return self.executor(executable, **kwargs).run_once(request["run_id"], object())

    @staticmethod
    def kill_recorded(pid):
        try:
            identity = process_identity(pid)
        except (ProcessGone, ProcessUnknown, OSError):
            return
        deadline = time.monotonic() + 10
        while True:
            try:
                stop_group(identity, timeout=0.5)
                return
            except ProcessGone:
                return
            except ProcessUnknown:
                if time.monotonic() >= deadline:
                    return
                time.sleep(0.05)
            except OSError:
                return


class OutputLimitIntegrationTests(DeliveryFixture):
    def test_fast_exit_per_stream_overflow_fails_in_final_drain(self):
        request = self.enqueue()
        executable = self.executable(
            "import sys\n"
            "sys.stdout.buffer.write(b'x' * 4096)\n"
            "sys.stderr.buffer.write(b'y' * 4096)\n"
        )
        result = self.run_once(
            request, executable, output_limit=512, line_limit=4096
        )

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"], "output_limit_exceeded")
        self.assertTrue(result["result"]["output_limit_exceeded"])
        self.assertFalse(result["result"]["stdout_line_limit_exceeded"])
        self.assertEqual(result["result"]["stdout_bytes"], 4096)
        self.assertEqual(result["result"]["stderr_bytes"], 4096)
        self.assertEqual(result["result"]["stdout_captured_bytes"], 512)
        self.assertEqual(result["result"]["stderr_captured_bytes"], 512)
        self.assertEqual(
            (self.root / "logs" / request["run_id"] / "stdout").stat().st_size, 512
        )

    def test_fast_exit_oversized_stdout_event_line_fails(self):
        request = self.enqueue()
        line = b'{"event":"' + (b"x" * 256) + b'"}\n'
        executable = self.executable(
            "import sys\n"
            f"sys.stdout.buffer.write({line!r})\n"
        )
        result = self.run_once(
            request, executable, output_limit=65536, line_limit=64
        )

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"], "stdout_line_limit_exceeded")
        self.assertTrue(result["result"]["stdout_line_limit_exceeded"])
        self.assertFalse(result["result"]["output_limit_exceeded"])
        self.assertEqual(result["result"]["stdout_bytes"], len(line))

    def test_event_lines_at_the_cap_are_accepted(self):
        request = self.enqueue()
        line = b'{"event":"' + (b"x" * 51) + b'"}\n'
        self.assertEqual(len(line), 64)
        executable = self.executable(
            "import sys\n"
            f"sys.stdout.buffer.write({line!r})\n"
        )
        result = self.run_once(
            request, executable, output_limit=65536, line_limit=64
        )

        self.assertEqual(result["state"], "completed", result["error"])
        self.assertEqual(result["result"]["stdout_bytes"], len(line))
        self.assertFalse(result["result"]["stdout_line_limit_exceeded"])

    def test_non_json_stream_output_is_not_misread_as_an_event_line(self):
        request = self.enqueue()
        executable = self.executable(
            "import sys\n"
            "sys.stdout.buffer.write(b'x' * 256 + b'\\n')\n"
        )
        result = self.run_once(
            request, executable, output_limit=65536, line_limit=64
        )

        self.assertEqual(result["state"], "completed", result["error"])
        self.assertEqual(result["result"]["stdout_bytes"], 257)
        self.assertFalse(result["result"]["stdout_line_limit_exceeded"])


class ExecutorIntegrationTests(DeliveryFixture):
    def test_adapter_sandbox_callbacks_and_persisted_lifecycle(self):
        marker = str(uuid.uuid4())
        request = self.enqueue(mode="review", prompt=f"PUBLIC-{marker}")
        release = self.worktree / "lifecycle-started"
        executable = self.executable(
            "import pathlib,time\n"
            f"p=pathlib.Path({str(release)!r})\n"
            "until=time.monotonic()+5\n"
            "while not p.exists():\n"
            "    if time.monotonic()>=until: raise SystemExit(90)\n"
            "    time.sleep(0.01)\n"
            "print('{\"event\":\"done\"}')\n"
        )
        real_start = self.store.start
        def start_then_release(*args, **kwargs):
            record = real_start(*args, **kwargs)
            release.write_text("started")
            return record
        with patch.object(self.store, "start", side_effect=start_then_release):
            result = self.run_once(request, executable)

        self.assertEqual(result["state"], "completed")
        self.assertEqual(len(self.launch_calls), 1)
        call = self.launch_calls[0]
        self.assertEqual(call["prompt"], f"PUBLIC-{marker}")
        self.assertEqual(call["worktree"], Path(request["worktree"]))
        self.assertTrue(call["review"])
        self.assertEqual(len(self.sandbox_calls), 1)
        argv, sandbox_kwargs = self.sandbox_calls[0]
        self.assertEqual(argv, (str(executable),))
        self.assertTrue(sandbox_kwargs["readonly"])
        self.assertEqual(sandbox_kwargs["worktree"], Path(request["worktree"]))

        event_types = [event["type"] for event in self.store.events(request["run_id"])]
        self.assertEqual(
            event_types,
            ["queued", "claimed", "dispatched", "spawned", "started", "completed"],
        )
        spawned = self.store.events(request["run_id"])[3]["payload"]
        self.assertEqual(spawned["pid"], spawned["pgid"])
        self.assertGreater(spawned["pid"], 0)
        self.assertNotIn(marker, json.dumps(result["result"]))
        self.assertNotIn(
            marker,
            (self.root / "logs" / request["run_id"] / "stdout").read_text(encoding="utf-8"),
        )

    def test_nonzero_exit_is_recorded_as_failure(self):
        request = self.enqueue()
        executable = self.executable(
            "import sys\n"
            "sys.stderr.write('fixture failure\\n')\n"
            "raise SystemExit(7)\n"
        )
        result = self.run_once(request, executable)

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["error"], "nonzero_exit")
        self.assertEqual(result["result"]["exit_code"], 7)
        self.assertGreater(result["result"]["stderr_bytes"], 0)
        self.assertIsNone(result["pid"])
        self.assertIsNone(result["process_identity"])

    def test_frequent_polling_enforces_deadline(self):
        request = self.enqueue(deadline=int(time.time()) + 1)
        executable = self.executable("import time\ntime.sleep(30)\n")
        started = time.monotonic()
        result = self.run_once(
            request, executable, heartbeat=0.1, lease_seconds=5
        )
        elapsed = time.monotonic() - started

        self.assertEqual(result["state"], "timed_out")
        self.assertEqual(result["error"], "deadline_exceeded")
        self.assertLess(elapsed, 5)
        self.assertIsNone(result["pid"])
        self.assertIsNone(result["process_identity"])

    def test_cancel_stops_the_group_and_recovery_does_not_relaunch(self):
        marker = self.worktree / "launch-count"
        request = self.enqueue(deadline=int(time.time()) + 60)
        executable = self.executable(
            "import pathlib, time\n"
            f"p = pathlib.Path({str(marker)!r})\n"
            "p.write_text(str(int(p.read_text()) + 1) if p.exists() else '1')\n"
            "time.sleep(60)\n"
        )
        executor = self.executor(executable, heartbeat=0.1, lease_seconds=5)
        outcome = {}
        thread = threading.Thread(
            target=lambda: outcome.setdefault(
                "result", executor.run_once(request["run_id"], object())
            )
        )
        thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            running = self.store.get(request["run_id"])
            if marker.exists() and running["state"] == "running" and running["process_identity"]:
                break
            time.sleep(0.02)
        self.assertTrue(marker.exists())
        self.assertEqual(running["state"], "running")
        self.assertIsNotNone(running["process_identity"])

        cancelled = executor.cancel(request["run_id"])
        thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(cancelled["state"], "cancelled")
        self.assertIn(outcome["result"]["state"], {"cancelling", "cancelled"})
        executor.recover()
        self.assertEqual(marker.read_text(), "1")
        self.assertEqual(self.store.get(request["run_id"])["state"], "cancelled")

    def test_child_marker_before_persisted_identity_keeps_early_cancel_quarantined(self):
        marker = self.worktree / "early-launch-count"
        request = self.enqueue()
        executable = self.executable(
            "import pathlib, time\n"
            f"pathlib.Path({str(marker)!r}).write_text('1')\n"
            "time.sleep(60)\n"
        )
        original_start = self.store.start
        cancellations = []

        def cancel_before_start(*args, **kwargs):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not marker.exists():
                time.sleep(0.02)
            self.assertTrue(marker.exists())
            pending = self.store.get(request["run_id"])
            self.assertEqual(pending["state"], "dispatching")
            self.assertIsNone(pending["process_identity"])
            cancellations.append(self.store.cancel(request["run_id"]))
            return original_start(*args, **kwargs)

        with patch.object(self.store, "start", side_effect=cancel_before_start):
            result = self.run_once(request, executable)

        self.assertEqual(cancellations[0]["state"], "quarantined")
        self.assertEqual(cancellations[0]["error"], "process_identity_unavailable")
        self.assertEqual(result["state"], "quarantined")
        self.assertTrue(group_empty(result["spawn_pgid"]))
        self.assertEqual(self.store.recover()[0]["state"], "cancelled")
        self.assertEqual(marker.read_text(), "1")
        self.assertFalse(self.store.active())

    def test_renew_uses_heartbeat_not_the_poll_interval(self):
        request = self.enqueue()
        executable = self.executable("import time\ntime.sleep(0.75)\n")
        executor = self.executor(executable, heartbeat=0.5, lease_seconds=5)
        original = self.store.renew
        renewals = []

        def counting_renew(run_id, token, lease_seconds=90):
            renewals.append(time.monotonic())
            return original(run_id, token, lease_seconds=lease_seconds)

        self.store.renew = counting_renew
        result = executor.run_once(request["run_id"], object())

        self.assertEqual(result["state"], "completed", result["error"])
        self.assertLessEqual(len(renewals), 2)
        if len(renewals) == 2:
            self.assertGreaterEqual(renewals[1] - renewals[0], 0.4)


class SpawnRecoveryIntegrationTests(DeliveryFixture):
    def test_cancel_during_adapter_preparation_never_spawns_or_holds_capacity(self):
        request = self.enqueue()
        executable = self.executable("raise AssertionError('must never execute')\n")
        executor = self.executor(executable)
        original = executor.test_adapter
        def cancelled_config(*args, **kwargs):
            self.store.cancel(request["run_id"])
            return original(*args, **kwargs)
        executor.test_adapter = cancelled_config
        with patch("tooling.ai_delivery.execution.runner.subprocess.Popen") as popen:
            result = executor.run_once(request["run_id"], object())
        popen.assert_not_called()
        self.assertEqual(result["state"], "cancelled")
        self.assertFalse(self.store.active())
        self.assertEqual(executor.recover(), [])

    def test_cancel_between_spawn_and_start_stops_proven_group_then_recovers_cancelled(self):
        request = self.enqueue()
        executable = self.executable("import time\ntime.sleep(60)\n")
        from tooling.ai_delivery.execution.process import process_identity as real_probe
        captured = []
        def cancel_before_probe(pid):
            captured.append(pid)
            self.store.cancel(request["run_id"])
            return real_probe(pid)
        with patch("tooling.ai_delivery.execution.runner.process_identity", side_effect=cancel_before_probe):
            result = self.run_once(request, executable)
        self.assertEqual(result["state"], "quarantined")
        self.assertTrue(group_empty(captured[0]))
        recovered = self.store.recover()
        self.assertEqual(recovered[0]["state"], "cancelled")
        self.assertFalse(self.store.active())

    def test_cancel_before_spawn_fact_does_not_lose_child_or_cancellation(self):
        request = self.enqueue()
        executable = self.executable("import time\ntime.sleep(60)\n")
        real_popen = subprocess.Popen
        children = []
        def child(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            children.append(process)
            self.store.cancel(request["run_id"])
            return process
        with patch("tooling.ai_delivery.execution.runner.subprocess.Popen", side_effect=child):
            result = self.run_once(request, executable)
        self.assertEqual(result["state"], "quarantined")
        self.assertEqual(result["pid"], children[0].pid)
        self.assertTrue(group_empty(children[0].pid))
        self.assertEqual(self.store.recover()[0]["state"], "cancelled")

    def test_exited_child_probe_is_never_persisted_or_signalled(self):
        request = self.enqueue()
        executable = self.executable("import time\ntime.sleep(60)\n")
        real_popen = subprocess.Popen
        spawned = []
        def child(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            spawned.append(process)
            process.poll = unittest.mock.Mock(side_effect=[None, 0])
            return process
        with patch("tooling.ai_delivery.execution.runner.subprocess.Popen", side_effect=child), patch("tooling.ai_delivery.execution.runner.stop_group") as stop:
            result = self.run_once(request, executable)
        spawned[0].poll = lambda: subprocess.Popen.poll(spawned[0])
        self.addCleanup(self.kill_recorded, spawned[0].pid)
        self.assertEqual(result["state"], "quarantined")
        self.assertIsNone(result["process_identity"])
        stop.assert_not_called()


    def test_identity_failure_persists_spawn_pid_and_quarantines_until_proven_dead(self):
        request = self.enqueue()
        marker = self.worktree / "spawn-marker"
        executable = self.executable(
            "import pathlib, time\n"
            f"pathlib.Path({str(marker)!r}).write_text('started')\n"
            "time.sleep(60)\n"
        )
        executor = self.executor(executable, heartbeat=0.1, lease_seconds=5)
        with patch(
            "tooling.ai_delivery.execution.runner.process_identity",
            side_effect=ProcessUnknown("identity fixture unavailable"),
        ):
            result = executor.run_once(request["run_id"], object())

        self.assertEqual(result["state"], "quarantined")
        self.assertEqual(result["error"], "process_identity_unavailable")
        self.assertIsNone(result["process_identity"])
        self.assertGreater(result["pid"], 0)
        self.assertEqual(result["spawn_pgid"], result["pid"])
        pid = result["pid"]
        self.addCleanup(self.kill_recorded, pid)
        spawned_event = [
            event for event in self.store.events(request["run_id"])
            if event["type"] == "spawned"
        ]
        self.assertEqual(spawned_event[0]["payload"], {"pid": pid, "pgid": pid})

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.02)
        self.assertTrue(marker.exists())

        def unknown(_pid):
            raise ProcessUnknown("identity fixture unknown")

        self.assertEqual(self.store.recover(identity=unknown)[0]["state"], "quarantined")
        self.assertEqual(
            self.store.get(request["run_id"])["error"], "process_identity_unknown"
        )
        live = executor.recover()
        self.assertEqual(live[0]["state"], "quarantined")
        self.assertEqual(live[0]["error"], "spawn_process_still_live")
        self.assertEqual(live[0]["pid"], pid)
        self.assertEqual(live[0]["spawn_pgid"], pid)

        blocked = self.enqueue(scope=["tooling"])
        self.assertIsNone(self.store.claim(blocked["run_id"]))

        identity = process_identity(pid)
        self.assertEqual(identity[0], pid)
        self.kill_recorded(pid)
        released = executor.recover()
        self.assertEqual(released[0]["state"], "interrupted", released[0]["error"])
        self.assertIsNone(released[0]["pid"])
        self.assertIsNone(released[0]["spawn_pgid"])
        self.assertIsNone(released[0]["process_identity"])
        self.assertIsNotNone(self.store.claim(blocked["run_id"]))

    def test_spawned_live_and_unknown_recovery_retain_capacity_and_scope(self):
        request = self.enqueue()
        claimed = self.store.claim(request["run_id"])
        token = claimed["fencing_token"]
        self.store.dispatch(request["run_id"], token)
        self.store.spawned(request["run_id"], token, 424242, 424242)
        self.store.expire(request["run_id"], token)

        def unknown(_pid):
            raise ProcessUnknown("identity fixture unknown")

        recovered = self.store.recover(identity=unknown)
        self.assertEqual(recovered[0]["state"], "quarantined")
        self.assertEqual(recovered[0]["error"], "process_identity_unknown")
        self.assertEqual(recovered[0]["pid"], 424242)
        self.assertEqual(recovered[0]["spawn_pgid"], 424242)

        recovered = self.store.recover(identity=lambda pid: (pid, pid, 999))
        self.assertEqual(recovered[0]["state"], "quarantined")
        self.assertEqual(recovered[0]["error"], "spawn_process_still_live")
        self.assertEqual(len(self.store.active()), 1)

        blocked = self.enqueue(scope=["tooling"])
        self.assertIsNone(self.store.claim(blocked["run_id"]))

    def test_spawned_recovery_releases_only_when_pid_absent_and_group_empty(self):
        request = self.enqueue()
        claimed = self.store.claim(request["run_id"])
        token = claimed["fencing_token"]
        self.store.dispatch(request["run_id"], token)
        self.store.spawned(request["run_id"], token, 424242, 424242)
        self.store.expire(request["run_id"], token)

        def gone(_pid):
            raise ProcessGone("identity fixture absent")

        with patch(
            "tooling.ai_delivery.execution.process.group_empty", return_value=False
        ):
            recovered = self.store.recover(identity=gone)
        self.assertEqual(recovered[0]["state"], "quarantined")
        self.assertEqual(recovered[0]["error"], "process_group_not_empty")
        self.assertEqual(recovered[0]["pid"], 424242)

        with patch(
            "tooling.ai_delivery.execution.process.group_empty",
            side_effect=ProcessUnknown("group fixture unknown"),
        ):
            recovered = self.store.recover(identity=gone)
        self.assertEqual(recovered[0]["state"], "quarantined")
        self.assertEqual(recovered[0]["error"], "process_group_unknown")
        self.assertEqual(recovered[0]["pid"], 424242)

        with patch(
            "tooling.ai_delivery.execution.process.group_empty", return_value=True
        ):
            recovered = self.store.recover(identity=gone)
        self.assertEqual(recovered[0]["state"], "interrupted")
        self.assertIsNone(recovered[0]["pid"])
        self.assertIsNone(recovered[0]["spawn_pgid"])
        self.assertIsNone(recovered[0]["process_identity"])
        self.assertEqual(self.store.active(), [])

    def test_cancel_without_identity_never_signals_the_raw_spawn_pid(self):
        request = self.enqueue()
        claimed = self.store.claim(request["run_id"])
        token = claimed["fencing_token"]
        self.store.dispatch(request["run_id"], token)
        self.store.spawned(request["run_id"], token, 424242, 424242)

        with patch(
            "tooling.ai_delivery.execution.process.os.killpg",
            side_effect=AssertionError("must not signal a raw PID"),
        ):
            cancelled = self.store.cancel(request["run_id"])

        self.assertEqual(cancelled["state"], "quarantined")
        self.assertEqual(cancelled["error"], "process_identity_unavailable")
        self.assertEqual(cancelled["pid"], 424242)
        self.assertEqual(cancelled["spawn_pgid"], 424242)


class RequestBoundaryIntegrationTests(DeliveryFixture):
    def test_missing_repository_is_rejected_before_queue_write(self):
        request = dict(self.enqueue())
        request["run_id"] = str(uuid.uuid4())
        del request["repository"]
        before = len(self.store.list())
        with self.assertRaisesRegex(ValueError, "invalid_request_fields"):
            self.store.enqueue(request)
        self.assertEqual(len(self.store.list()), before)


    def test_unknown_or_secret_bearing_request_fields_are_rejected(self):
        secret = "ghp_fixture_secret_must_not_echo"
        base = self.request()
        for changes in (
            {"env": {"GH_TOKEN": secret}},
            {"token": secret},
            {"secrets": [secret]},
            {"authorization": secret},
            {"approval": True},
        ):
            with self.subTest(changes=tuple(changes)), self.assertRaises(ValueError) as caught:
                self.store.enqueue(dict(base, **changes))
            self.assertNotIn(secret, str(caught.exception))
        self.assertIsNone(self.store.get(base["run_id"]))

    def test_executor_rejects_an_agent_configuration_mismatch_before_spawn(self):
        request = self.enqueue()
        executable = self.executable("raise SystemExit(0)\n")

        class WrongConfig:
            agent_id = "opencode"

        result = self.executor(executable).run_once(request["run_id"], WrongConfig())

        self.assertEqual(result["state"], "config_required")
        self.assertEqual(result["error"], "configuration_or_isolation_failed")
        self.assertEqual(self.launch_calls, [])
        self.assertEqual(self.sandbox_calls, [])


if __name__ == "__main__":
    unittest.main()


class NativeFinalProtocolTests(unittest.TestCase):
    def test_dsh_requires_completed_turn_and_nonempty_final(self):
        from tooling.ai_delivery.execution.runner import _dsh_completed
        complete = b'{"type":"status","phase":"turn_end","reason":{"kind":"completed"}}\n'
        final = b'{"type":"final","text":"Implemented and checked."}\n'
        self.assertTrue(_dsh_completed(complete + final))
        self.assertFalse(_dsh_completed(final))
        self.assertFalse(_dsh_completed(complete))
        self.assertFalse(_dsh_completed(complete + b'{"type":"final","text":""}'))
        self.assertFalse(_dsh_completed(complete + final + b'{"type":"error"}'))
        self.assertFalse(_dsh_completed(complete.replace(b'completed', b'cancelled') + final))



class EarlyExitIntegrationTests(DeliveryFixture):
    def reaped_run(self, body, **kwargs):
        request = self.enqueue(agent_id=kwargs.pop("agent_id", "kimi_code"))
        executable = self.executable(body)
        real_popen = subprocess.Popen
        def reaped(*args, **options):
            process = real_popen(*args, **options)
            process.wait(timeout=5)
            return process
        with patch("tooling.ai_delivery.execution.runner.subprocess.Popen", side_effect=reaped):
            result = self.run_once(request, executable, **kwargs)
        return request, result

    def test_already_reaped_child_keeps_actual_exit_and_output(self):
        for code in (0, 7):
            with self.subTest(code=code):
                with patch("tooling.ai_delivery.execution.runner.stop_group") as stop:
                    request, row = self.reaped_run("import sys\nsys.stdout.write('actual output')\nraise SystemExit(" + str(code) + ")\n")
                self.assertEqual(row["state"], "completed" if code == 0 else "failed", row["error"])
                self.assertEqual(row["result"]["exit_code"], code)
                self.assertTrue(row["result"]["exited_before_identity"])
                self.assertGreater(row["result"]["stdout_bytes"], 0)
                self.assertIsNone(row["process_identity"])
                self.assertIsNone(row["pid"])
                self.assertNotIn("started", {event["type"] for event in self.store.events(request["run_id"])})
                stop.assert_not_called()

    def test_reaped_child_unknown_or_live_group_retains_quarantine(self):
        for probe in (False, ProcessUnknown("group unavailable")):
            with self.subTest(probe=type(probe).__name__):
                options = {"side_effect": probe} if isinstance(probe, Exception) else {"return_value": probe}
                with patch("tooling.ai_delivery.execution.runner.group_empty", **options), patch("tooling.ai_delivery.execution.runner.stop_group") as stop:
                    request, row = self.reaped_run("print('exited')\n")
                self.assertEqual(row["state"], "quarantined")
                self.assertIsNotNone(row["pid"])
                self.assertIsNone(row["process_identity"])
                stop.assert_not_called()
                self.assertIn(request["run_id"], {active["run_id"] for active in self.store.active()})

    def test_reaped_child_output_limits_are_still_enforced(self):
        _request, row = self.reaped_run("print('{'+ 'x'*100 +'}')\n", line_limit=64)
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["error"], "stdout_line_limit_exceeded")
        self.assertTrue(row["result"]["stdout_line_limit_exceeded"])

    def test_reaped_dsh_still_requires_a_completed_final(self):
        _request, row = self.reaped_run("print('{\"type\":\"final\",\"text\":\"not enough\"}')\n", agent_id="dsh")
        self.assertEqual(row["state"], "failed")
        self.assertEqual(row["error"], "dsh_final_not_completed")

    def test_reaped_leader_with_actual_live_descendant_is_not_released(self):
        body = ("import subprocess,sys\n"
                "subprocess.Popen([sys.executable,'-c','import time;time.sleep(0.4)'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n")
        with patch("tooling.ai_delivery.execution.runner.stop_group") as stop:
            _request, row = self.reaped_run(body)
        self.assertEqual(row["state"], "quarantined")
        self.assertIsNone(row["process_identity"])
        stop.assert_not_called()
        # The fixture descendant exits itself promptly; no raw PID cleanup.
        time.sleep(0.5)


class DshCompletionIntegrationTests(DeliveryFixture):
    def test_zero_exit_alone_does_not_complete_a_dsh_run(self):
        completed = '{"type":"status","phase":"turn_end","reason":{"kind":"completed"}}\n'
        final = '{"type":"final","text":"Work completed."}\n'
        for output, expected in [(completed+final,"completed"),(final,"failed"),(completed+final+'{"type":"error"}\n',"failed")]:
            request = self.enqueue(agent_id="dsh")
            executable = self.executable("import sys\nsys.stdout.write(" + repr(output) + ")\n")
            result = self.run_once(request, executable)
            self.assertEqual(result["state"], expected, result.get("error"))
            self.assertEqual(result["result"]["exit_code"], 0)
