from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

from ..controller.repository import ControllerError, _run_git
from ..execution.sandbox import IsolationError, sandbox_argv


_TIMEOUT = 180
_STREAM_LIMIT = 8 * 1024 * 1024
_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_TASK_ID = re.compile(r"PH-[0-9]+\Z")
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\Z")
_FORBIDDEN = frozenset("\r\n;&|<>`$(){}\\")


class VerificationError(RuntimeError):
    pass


def parse_acceptance(command: str) -> list[str]:
    if not isinstance(command, str) or not command or any(char in _FORBIDDEN for char in command):
        raise VerificationError("invalid_acceptance_command")
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        raise VerificationError("invalid_acceptance_command") from None
    if len(words) < 2 or words[0] not in {"python", "git"}:
        raise VerificationError("acceptance_not_allowed")
    if words[0] == "git":
        if words[1:] != ["diff", "--check"]:
            raise VerificationError("acceptance_not_allowed")
        return ["/usr/bin/git", *words[1:]]
    if words[1:] == ["scripts/projectctl.py", "check"]:
        return [sys.executable, *words[1:]]
    if words[1] == "-m" and len(words) >= 3 and words[2] == "unittest":
        rest = words[3:]
        if rest and rest[0] == "discover":
            if (len(rest) == 4 and rest[1] == "-s" and rest[3] == "-v"
                    and (rest[2] == "tests" or rest[2].startswith("tests/"))
                    and all(part not in {"", ".", ".."} for part in rest[2].split("/"))
                    and "\\" not in rest[2]):
                return [sys.executable, *words[1:]]
        else:
            verbose = rest[-1:] == ["-v"]
            modules = rest[:-1] if verbose else rest
            if modules and all(_MODULE.fullmatch(module) for module in modules):
                return [sys.executable, *words[1:]]
        raise VerificationError("acceptance_not_allowed")
    if words[1] == "manage.py" and words[2:] in (
        ["check"],
        ["makemigrations", "--check", "--dry-run"],
        ["test", "tests", "--verbosity", "1"],
        ["verify_integrity", "--json"],
    ):
        return [sys.executable, *words[1:]]
    raise VerificationError("acceptance_not_allowed")


def _private_directory(value, name):
    try:
        path = Path(value).resolve(strict=True)
        mode = path.stat()
    except (TypeError, ValueError, OSError, RuntimeError):
        raise VerificationError(f"invalid_{name}") from None
    if not path.is_dir() or stat.S_IMODE(mode.st_mode) != 0o700 or mode.st_uid != os.getuid():
        raise VerificationError(f"invalid_{name}")
    return path


def _safe_tar_members(archive):
    members = archive.getmembers()
    for member in members:
        name = member.name
        parts = name.split("/")
        if (not name or name.startswith("/") or "\\" in name or "\x00" in name
                or any(part in {"", ".", ".."} for part in parts) or ".git" in parts):
            raise VerificationError("unsafe_base_archive")
        if not (member.isdir() or member.isreg() or member.issym()):
            raise VerificationError("unsafe_base_archive")
        if member.issym():
            target = member.linkname
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
            if not target or target.startswith("/") or resolved == ".." or resolved.startswith("../"):
                raise VerificationError("unsafe_base_archive")
    return members


def _extract_base(data, snapshot):
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            archive.extractall(snapshot, members=_safe_tar_members(archive), filter="data")
    except VerificationError:
        raise
    except (OSError, tarfile.TarError):
        raise VerificationError("invalid_base_archive") from None


def _kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass


def _execute(argv, *, cwd, env, timeout, stdout_path, stderr_path):
    limitations = []
    process = subprocess.Popen(
        argv, cwd=cwd, env=env, shell=False, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    streams = {}
    outputs = {}
    for name, stream, path in (
        ("stdout", process.stdout, stdout_path),
        ("stderr", process.stderr, stderr_path),
    ):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
        streams[name] = stream
        outputs[name] = path.open("wb")
    deadline = time.monotonic() + timeout
    failed = None
    sizes = {"stdout": 0, "stderr": 0}
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failed = "timeout"
                break
            for key, _events in selector.select(min(remaining, 0.2)):
                name = key.data
                try:
                    chunk = os.read(streams[name].fileno(), 65536)
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(streams[name])
                    continue
                if sizes[name] + len(chunk) > _STREAM_LIMIT:
                    failed = "output_limit"
                    break
                outputs[name].write(chunk)
                sizes[name] += len(chunk)
            if failed:
                break
        if failed:
            _kill_group(process)
            limitations.append(failed)
        returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        _kill_group(process)
        returncode = process.wait()
        if "timeout" not in limitations:
            limitations.append("timeout")
    finally:
        selector.close()
        for stream in streams.values():
            stream.close()
        for output in outputs.values():
            output.close()
    return {"exit_code": returncode, "limitations": limitations}


class Verifier:
    def __init__(self, repo, root, python=None, runner=None):
        self.repo = repo
        self.root = _private_directory(root, "root")
        # Preserve the venv executable path: resolving its symlink would
        # silently select the base interpreter without the pinned dependencies.
        self.python = Path(sys.executable).absolute()
        if python is not None:
            try:
                requested = Path(python).absolute()
            except (TypeError, ValueError, OSError):
                raise VerificationError("invalid_python") from None
            if requested != self.python:
                raise VerificationError("python_runtime_not_supported")
        if not self.python.is_file():
            raise VerificationError("invalid_python")
        if runner is not None and not callable(runner):
            raise VerificationError("invalid_runner")
        self.runner = runner or _execute

    def _assert_snapshot(self, snapshot, view, task):
        actual = self.repo.manifest(snapshot, view["base_sha"], task["allowed_paths"])
        if actual != view["manifest"] or self.repo.diff_digest(actual) != view["diff_digest"]:
            raise VerificationError("snapshot_manifest_mismatch")

    @staticmethod
    def _environment(runtime):
        home = runtime / "home"
        temporary = runtime / "tmp"
        xdg = runtime / "xdg"
        for path in (home, temporary, xdg):
            path.mkdir(mode=0o700)
        return {
            "PATH": "/usr/bin:/bin",
            "HOME": str(home),
            "TMPDIR": str(temporary),
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "XDG_CACHE_HOME": str(xdg),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PMO_ENV": "development",
            "PMO_DB": str(runtime / "acceptance.sqlite3"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }

    @staticmethod
    def _sha(path):
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def verify(self, view: dict, task: dict) -> dict:
        if not isinstance(view, dict) or not isinstance(task, dict):
            raise VerificationError("invalid_verification_input")
        required = {"run_id", "task_id", "base_sha", "contract_digest", "diff_digest",
                    "worktree", "manifest", "repository"}
        if not required.issubset(view):
            raise VerificationError("invalid_verification_view")
        identity_fields = ("task_id", "base_sha", "contract_digest", "diff_digest")
        if (not isinstance(view["run_id"], str) or not view["run_id"]
                or not all(isinstance(view[field], str) for field in identity_fields)
                or not _TASK_ID.fullmatch(view["task_id"])
                or task.get("id") != view["task_id"]
                or not _HEX40.fullmatch(view["base_sha"])
                or not _HEX64.fullmatch(view["contract_digest"])
                or not _HEX64.fullmatch(view["diff_digest"])):
            raise VerificationError("invalid_verification_identity")
        acceptance = task.get("acceptance")
        allowed_paths = task.get("allowed_paths")
        if (not isinstance(acceptance, list) or not acceptance
                or not all(isinstance(item, str) for item in acceptance)
                or not isinstance(allowed_paths, list) or not allowed_paths):
            raise VerificationError("invalid_frozen_task")
        commands = [parse_acceptance(command) for command in acceptance]
        for command in commands:
            if command[0] == sys.executable:
                command[0] = str(self.python)
        if not isinstance(view["manifest"], list) or self.repo.diff_digest(view["manifest"]) != view["diff_digest"]:
            raise VerificationError("invalid_diff_digest")

        job = Path(tempfile.mkdtemp(prefix="acceptance-", dir=self.root))
        snapshot = job / "snapshot"
        runtime = job / "runtime"
        logs = job / "logs"
        for path in (snapshot, runtime, logs):
            path.mkdir(mode=0o700)
        env = self._environment(runtime)
        evidence = {
            "run_id": view["run_id"],
            "base_sha": view["base_sha"],
            "contract_digest": view["contract_digest"],
            "diff_digest": view["diff_digest"],
            "checks": [],
            "result": "BLOCKING",
            "snapshot": str(snapshot),
            "limitations": [],
        }
        try:
            archive = _run_git(self.repo.root, ["archive", "--format=tar", view["base_sha"]])
            _extract_base(archive, snapshot)
            if any(command[0] == "/usr/bin/git" for command in commands):
                _run_git(snapshot, ["init", "--quiet"])
                _run_git(snapshot, ["add", "--all", "--"])
            self.repo.apply_manifest(view["worktree"], snapshot, view["manifest"])
            self._assert_snapshot(snapshot, view, task)
        except (ControllerError, OSError):
            raise VerificationError("snapshot_rebuild_failed") from None

        jobs = []
        for original, argv in zip(acceptance, commands):
            if argv[1:] == ["manage.py", "verify_integrity", "--json"]:
                jobs.append(("python manage.py migrate --noinput",
                             [str(self.python), "manage.py", "migrate", "--noinput"], False))
            jobs.append((original, argv, argv[1:] == ["manage.py", "verify_integrity", "--json"]))
        migration_failed = False
        for index, (original, argv, needs_migration) in enumerate(jobs):
            stdout_path = logs / f"{index:03d}.stdout"
            stderr_path = logs / f"{index:03d}.stderr"
            stdout_path.touch(mode=0o600)
            stderr_path.touch(mode=0o600)
            check = {
                "command": original,
                "result": "BLOCKING",
                "exit_code": None,
                "stdout_sha256": _EMPTY_SHA256,
                "stderr_sha256": _EMPTY_SHA256,
                "limitations": [],
            }
            evidence["checks"].append(check)
            if needs_migration and migration_failed:
                check["limitations"].append("migration_failed")
                evidence["limitations"].append("migration_failed")
                continue
            try:
                self._assert_snapshot(snapshot, view, task)
                isolated = sandbox_argv(
                    argv, worktree=snapshot, runtime=runtime,
                    read_roots=[Path(sys.prefix), Path(sys.base_prefix)], network=False,
                )
                outcome = self.runner(
                    isolated, cwd=snapshot, env=env, timeout=_TIMEOUT,
                    stdout_path=stdout_path, stderr_path=stderr_path,
                )
                if not isinstance(outcome, dict):
                    raise TypeError("invalid_runner_result")
                exit_code = outcome.get("exit_code")
                if isinstance(exit_code, bool) or not isinstance(exit_code, int):
                    raise TypeError("invalid_runner_result")
                raw_limitations = outcome.get("limitations", [])
                if not isinstance(raw_limitations, list) or not all(
                        item in {"timeout", "output_limit"} for item in raw_limitations):
                    raise TypeError("invalid_runner_result")
                check["exit_code"] = exit_code
                check["limitations"].extend(raw_limitations)
                if exit_code != 0:
                    check["limitations"].append("nonzero_exit")
                check["result"] = "PASS" if exit_code == 0 and not raw_limitations else "BLOCKING"
                self._assert_snapshot(snapshot, view, task)
            except IsolationError:
                check["limitations"].append("config_required")
            except (VerificationError, ControllerError):
                check["limitations"].append("snapshot_manifest_mismatch")
            except (OSError, subprocess.SubprocessError, TypeError, ValueError):
                check["limitations"].append("execution_failed")
            check["stdout_sha256"] = self._sha(stdout_path)
            check["stderr_sha256"] = self._sha(stderr_path)
            for limitation in check["limitations"]:
                if limitation not in evidence["limitations"]:
                    evidence["limitations"].append(limitation)
            if original == "python manage.py migrate --noinput" and check["result"] != "PASS":
                migration_failed = True
        if all(check["result"] == "PASS" for check in evidence["checks"]):
            evidence["result"] = "PASS"
        return evidence


__all__ = ["VerificationError", "Verifier", "parse_acceptance"]
