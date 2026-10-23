"""Read-only version probes: python -m tooling.ai_delivery.doctor [--config FILE].

Configuration is a JSON mapping of known IDs to absolute executable paths.
Only fixed --version argv is run. No task, help, headless or login command is
available. A temporary HOME/cwd avoids normal user profile initialization;
this is hygiene, not an operating-system security sandbox. Raw tool output is
discarded, including on error. No authentication or inference is attempted.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from .agents.protocol import AgentSpec, DoctorResult, ProbeResult, unverified_capabilities
from .agents.registry import AGENTS, ConfigurationError, validate_config


PROBE_TIMEOUT = 5.0
STREAM_LIMIT = 64 * 1024
VERSION = re.compile(r"(?<![\w.])v?(\d{1,4}\.\d{1,4}\.\d{1,4}(?:-(?:rc|alpha|beta|pre|dev)\.?\d{1,4})?)(?![\w.])")


def _basename(executable: str) -> str:
    name = Path(executable).name
    if (not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", name)
            or re.search(r"(?i)(token|secret|password|ghp_|github_pat_|sk-)", name)):
        return "redacted-executable"
    return name


def _kill_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _version_probe(spec: AgentSpec, executable: str) -> tuple[str | None, str | None]:
    # Only whitelist harmless process environment keys; never inherit API/GH
    # tokens or the user's HOME. PATH is needed by installed node shebangs.
    with tempfile.TemporaryDirectory(prefix="ai-delivery-probe-") as private_home:
        env = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8",
               "HOME": private_home, "XDG_CONFIG_HOME": private_home,
               "XDG_CACHE_HOME": private_home, "XDG_DATA_HOME": private_home,
               "XDG_STATE_HOME": private_home, "TMPDIR": private_home,
               "NO_COLOR": "1", "TERM": "dumb"}
        try:
            process = subprocess.Popen(
                [executable, *spec.version_args], stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
                cwd=private_home, shell=False, start_new_session=True,
            )
        except OSError:
            return None, "probe_launch_failed"
        buffers = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + PROBE_TIMEOUT
        error = None
        try:
            with selectors.DefaultSelector() as selector:
                for label, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, label)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        error = "probe_timeout"
                        break
                    for key, _ in selector.select(min(remaining, 0.05)):
                        chunk = os.read(key.fileobj.fileno(), 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        elif len(buffers[key.data]) + len(chunk) > STREAM_LIMIT:
                            error = "probe_output_limit"
                            break
                        else:
                            buffers[key.data].extend(chunk)
                    if error:
                        break
                if not error:
                    try:
                        process.wait(timeout=max(0.001, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        error = "probe_timeout"
        finally:
            # Also kill descendants retaining the process group after the
            # parent exits; a probe must not leave an implicit service running.
            _kill_group(process)
            process.stdout.close()
            process.stderr.close()
        if error:
            return None, error
        if process.returncode != 0:
            return None, "probe_nonzero_exit"
        match = VERSION.search(buffers["stdout"].decode("utf-8", errors="replace"))
        if not match:
            return None, "version_unrecognized"
        return match.group(1), None


def probe(spec: AgentSpec, config: object = None) -> ProbeResult:
    paths = validate_config({} if config is None else config)
    explicit = spec.id in paths
    executable = paths[spec.id] if explicit else shutil.which(spec.command)
    result: ProbeResult = {
        "id": spec.id, "available": False,
        "executable": _basename(executable) if executable else None,
        "version": None, "status": "missing",
        "capabilities": unverified_capabilities(), "diagnostics": [],
    }
    if not executable:
        result["diagnostics"] = ["command_missing"]
        return result
    try:
        usable = Path(executable).is_file() and os.access(executable, os.X_OK)
    except OSError:
        usable = False
    if not usable:
        result["status"] = "config_required" if explicit else "missing"
        result["diagnostics"] = ["executable_unavailable"]
        return result
    result["available"] = True
    version, error = _version_probe(spec, executable)
    if error:
        result["status"] = "config_required" if error == "version_unrecognized" else "probe_failed"
        result["diagnostics"] = [error]
    else:
        result["status"] = "probe_verified"
        result["version"] = version
        result["diagnostics"] = ["version_only_execution_unverified"]
    return result


def doctor(config: object = None) -> DoctorResult:
    paths = validate_config({} if config is None else config)
    return {"schema_version": 1, "controller": probe(AGENTS["codex_controller"], paths),
            "agents": [probe(spec, paths) for key, spec in AGENTS.items() if key != "codex_controller"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="Known agent IDs mapped to absolute executable paths")
    args = parser.parse_args(argv)
    try:
        if args.config:
            # Bound config reads too; invalid content is never echoed.
            with args.config.open("rb") as handle:
                content = handle.read(STREAM_LIMIT + 1)
            if len(content) > STREAM_LIMIT:
                raise ConfigurationError("configuration_too_large")
            config = json.loads(content)
        else:
            config = {}
        result = doctor(config)
    except (ConfigurationError, OSError, ValueError, UnicodeError):
        print('{"error":"invalid_agent_configuration"}', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
