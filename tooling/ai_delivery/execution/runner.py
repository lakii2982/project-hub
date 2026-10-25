from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import threading
import time

from .process import ProcessGone, ProcessUnknown, group_empty, process_identity, stop_group
from .queue import LeaseError


OUTPUT_LIMIT = 8 * 1024 * 1024
STDOUT_LINE_LIMIT = 64 * 1024
STDIN_LIMIT = 8 * 1024 * 1024
POLL_INTERVAL = 0.05


class Executor:
    def __init__(self, store, *, test_adapter=None, test_sandbox=None, heartbeat=15, lease_seconds=90,
                 output_limit=OUTPUT_LIMIT, line_limit=STDOUT_LINE_LIMIT):
        self.store = store
        self.test_adapter = test_adapter
        self.test_sandbox = test_sandbox
        if not isinstance(heartbeat, (int, float)) or not 0 < heartbeat <= 30:
            raise ValueError("heartbeat must not exceed 30 seconds")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid lease")
        if type(output_limit) is not int or output_limit <= 0:
            raise ValueError("invalid output limit")
        if type(line_limit) is not int or line_limit <= 0:
            raise ValueError("invalid line limit")
        self.heartbeat = heartbeat
        self.lease_seconds = lease_seconds
        self.output_limit = output_limit
        self.line_limit = line_limit

    def _adapter(self):
        if self.test_adapter is not None:
            return self.test_adapter
        from .adapters import build_launch
        return build_launch

    def _sandbox(self):
        if self.test_sandbox is not None:
            return self.test_sandbox
        from .sandbox import sandbox_argv
        return sandbox_argv

    def _runtime(self, run_id):
        runtime = self.store.root / "runtime" / run_id
        for path in (self.store.root / "runtime", runtime, runtime / "home", runtime / "cache", runtime / "tmp"):
            path.mkdir(mode=0o700, exist_ok=True)
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
                raise OSError("unsafe runtime directory")
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise OSError("unsafe runtime owner")
        return runtime

    def _log(self, run_id, name):
        directory = self.store.root / "logs" / run_id
        for path in (self.store.root / "logs", directory):
            path.mkdir(mode=0o700, exist_ok=True)
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700:
                raise OSError("unsafe log directory")
        descriptor = os.open(
            directory / name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        return os.fdopen(descriptor, "wb", buffering=0)

    def run_once(self, run_id, config):
        claimed = self.store.claim(run_id, lease_seconds=self.lease_seconds)
        if claimed is None:
            return self.store.get(run_id)
        token = claimed["fencing_token"]
        request = claimed["request"]
        process = None
        identity = None
        try:
            if getattr(config, "agent_id", request["agent_id"]) != request["agent_id"]:
                raise ValueError("agent configuration does not match request")
            runtime = self._runtime(run_id)
            spec = self._adapter()(
                config, request["prompt"], Path(request["worktree"]), runtime,
                review=request["mode"] == "review",
            )
            stdin = spec.stdin
            if stdin is not None and (not isinstance(stdin, bytes) or len(stdin) > STDIN_LIMIT):
                raise ValueError("invalid or oversized process stdin")
            argv = self._sandbox()(
                spec.argv,
                worktree=Path(request["worktree"]),
                runtime=runtime,
                read_roots=spec.read_roots,
                write_roots=spec.write_roots,
                write_patterns=spec.write_patterns,
                readonly=request["mode"] == "review",
            )
            if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv):
                raise ValueError("invalid sandbox argv")
            self.store.dispatch(run_id, token)
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=spec.cwd,
                env=spec.env,
                shell=False,
                start_new_session=True,
            )
            # The child is a new session leader, so its PGID is its PID.  Save
            # that spawn fact before probing the stronger identity: if probing
            # fails, recovery still has enough evidence to distinguish an
            # absent process from a live or unknown one without signaling.
            self.store.spawned(run_id, token, process.pid, process.pid)
            if process.poll() is not None:
                raise ProcessGone("spawned child already exited")
            # One bounded retry of a transient kernel identity lookup. Never
            # use a raw PID or Popen.terminate as a substitute for group proof.
            try:
                identity = process_identity(process.pid)
            except ProcessUnknown:
                identity = process_identity(process.pid)
            if process.poll() is not None:
                identity = None
                raise ProcessGone("spawned child exited during identity capture")
            if identity[0] != identity[1]:
                raise ProcessUnknown("child is not a process group leader")
            self.store.start(run_id, token, process.pid, identity)
        except Exception as exc:
            if process is not None:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
            if process is not None and identity is not None:
                try:
                    stop_group(identity)
                except (ProcessUnknown, ProcessGone):
                    current = self.store.get(run_id)
                    if current["fencing_token"] == token:
                        return self.store._quarantine(run_id, token, "incomplete_launch_cleanup")
            current = self.store.get(run_id)
            if current["state"] in {"cancelling", "quarantined"}:
                return current
            if current["fencing_token"] != token:
                return current
            if process is not None and identity is None:
                return self.store._quarantine(run_id, token, "process_identity_unavailable")
            code = "launch_failed" if process is not None else "configuration_or_isolation_failed"
            return self.store.finish(run_id, token, "config_required" if process is None else "failed", error=code)

        output_limited = threading.Event()
        line_limited = threading.Event()
        streams = {}
        drain_errors = []
        threads = []

        def drain(name, source):
            captured = bytearray()
            total = 0
            line_size = 0
            line_structured = False
            line_prefix_checked = False

            def feed_line(segment):
                nonlocal line_size, line_structured, line_prefix_checked
                position = 0
                if not line_prefix_checked:
                    while position < len(segment) and segment[position] in b" \t\r":
                        position += 1
                    if position < len(segment):
                        line_structured = segment[position:position + 1] in (b"{", b"[")
                        line_prefix_checked = True
                line_size += len(segment)
                if line_structured and line_size > self.line_limit:
                    line_limited.set()

            try:
                with self._log(run_id, name) as log:
                    while True:
                        chunk = source.read(65536)
                        if not chunk:
                            break
                        total += len(chunk)
                        remaining = self.output_limit - len(captured)
                        if remaining > 0:
                            saved = chunk[:remaining]
                            captured.extend(saved)
                            log.write(saved)
                        if len(captured) >= self.output_limit and total > self.output_limit:
                            output_limited.set()
                        if name == "stdout":
                            parts = chunk.split(b"\n")
                            for part in parts[:-1]:
                                feed_line(part)
                                line_size = 0
                                line_structured = False
                                line_prefix_checked = False
                            feed_line(parts[-1])
            except Exception:
                drain_errors.append(name)
            finally:
                source.close()
                streams[name] = (total, bytes(captured))

        for name, source in (("stdout", process.stdout), ("stderr", process.stderr)):
            thread = threading.Thread(target=drain, args=(name, source), daemon=True)
            thread.start()
            threads.append(thread)

        def write_stdin():
            try:
                process.stdin.write(stdin)
                process.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        if stdin is not None:
            writer = threading.Thread(target=write_stdin, daemon=True)
            writer.start()
        else:
            writer = None

        final_state = None
        error = None
        next_renew = time.monotonic() + self.heartbeat
        try:
            while process.poll() is None:
                if output_limited.is_set() or line_limited.is_set():
                    final_state = "failed"
                    error = ("stdout_line_limit_exceeded" if line_limited.is_set()
                             else "output_limit_exceeded")
                    break
                if int(time.time()) >= request["deadline"]:
                    final_state, error = "timed_out", "deadline_exceeded"
                    break
                now = time.monotonic()
                if now >= next_renew:
                    self.store.renew(run_id, token, lease_seconds=self.lease_seconds)
                    next_renew = now + self.heartbeat
                time.sleep(min(POLL_INTERVAL, max(0.0, next_renew - now)))
            if final_state is not None:
                try:
                    stop_group(identity)
                    process.wait(timeout=5)
                except (ProcessUnknown, ProcessGone):
                    # The signal may already have worked while the group briefly
                    # looks unknown (for example a zombie).  Only release after
                    # reaping our child and proving the group is empty.
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired as exc:
                        raise ProcessUnknown("process did not exit after cleanup") from exc
                    if not group_empty(identity[1]):
                        raise ProcessUnknown("process group is still present")
        except LeaseError:
            try:
                stop_group(identity)
            except (ProcessUnknown, ProcessGone):
                pass
            return self.store.get(run_id)
        except (ProcessUnknown, ProcessGone):
            return self.store._quarantine(run_id, token, "process_cleanup_unknown")
        finally:
            for thread in threads:
                thread.join(timeout=5)
            if writer is not None:
                writer.join(timeout=5)

        if any(thread.is_alive() for thread in threads):
            return self.store._quarantine(run_id, token, "output_drain_incomplete")
        if drain_errors:
            return self.store._quarantine(run_id, token, "output_capture_failed")
        # A fast-exiting child can cross a limit while the final drain runs,
        # after the poll loop has already stopped observing the flags.
        if (output_limited.is_set() or line_limited.is_set()) and final_state in {None, "completed"}:
            final_state = "failed"
            error = ("stdout_line_limit_exceeded" if line_limited.is_set()
                     else "output_limit_exceeded")
        try:
            if not group_empty(identity[1]):
                return self.store._quarantine(run_id, token, "process_group_not_empty")
        except ProcessUnknown:
            return self.store._quarantine(run_id, token, "process_group_unknown")
        current = self.store.get(run_id)
        if current["state"] != "running" or current["fencing_token"] != token:
            return current
        if final_state is None:
            final_state = "completed" if process.returncode == 0 else "failed"
            if process.returncode != 0:
                error = "nonzero_exit"
        if final_state == "completed" and request["agent_id"] == "dsh":
            if not _dsh_completed(streams.get("stdout", (0, b""))[1]):
                final_state, error = "failed", "dsh_final_not_completed"
        result = {
            "exit_code": process.returncode,
            "output_limit_exceeded": output_limited.is_set(),
            "stdout_line_limit_exceeded": line_limited.is_set(),
        }
        for name in ("stdout", "stderr"):
            total, captured = streams.get(name, (0, b""))
            result[f"{name}_bytes"] = total
            result[f"{name}_captured_bytes"] = len(captured)
            result[f"{name}_sha256"] = hashlib.sha256(captured).hexdigest()
        return self.store.finish(run_id, token, final_state, result=result, error=error)

    def cancel(self, run_id):
        return self.store.cancel(run_id)

    def recover(self):
        return self.store.recover()


def _dsh_completed(output):
    """A zero exit is insufficient: require an explicit completed turn/final."""
    final = None
    completed = False
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except (ValueError, UnicodeError):
            return False
        if not isinstance(record, dict) or record.get("type") == "error":
            return False
        if record.get("type") == "status" and record.get("phase") == "turn_end":
            reason = record.get("reason")
            completed = isinstance(reason, dict) and reason.get("kind") == "completed"
        if record.get("type") == "final":
            final = record.get("text")
    return completed and isinstance(final, str) and bool(final.strip())
