"""Fail-closed macOS ``sandbox-exec`` command construction.

There is exactly one real backend in this task: Apple's ``sandbox-exec`` on
Darwin.  Linux/Windows and an unusable ``sandbox-exec`` return
``IsolationError("config_required")``; this module never degrades to an
unsandboxed command and never claims cross-platform isolation.

The generated profile is intentionally shaped as:

* ``(allow default)`` so system libraries and binaries keep working;
* deny ``file-read-data`` for the user/temp/controller roots, with only the
  run's worktree/runtime, explicit auth references and native runtime roots as
  exceptions (a global read deny cannot be repaired by later allows);
* deny all ``file-write*`` except the run's writable roots, fixed auth patterns
  and a small set of ``/dev`` devices;
* deny ``network*`` for acceptance-style runs.

All paths are canonicalized, symlink roots are rejected and broad HOME/root/
temp exceptions are refused.
"""

from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import sys
import sysconfig


CONFIG_REQUIRED = "config_required"
_PROTECTED_ROOTS = (
    Path("/Users"),
    Path("/private/tmp"),
    Path("/private/var/folders"),
    Path("/private/var/tmp"),
    Path("/Volumes"),
)
_DEVICE_WRITE_ROOTS = (
    Path("/dev/fd"),
)
_DEVICE_WRITE_LITERALS = (
    Path("/dev/null"),
    Path("/dev/zero"),
    Path("/dev/random"),
    Path("/dev/urandom"),
    Path("/dev/tty"),
    Path("/dev/stdout"),
    Path("/dev/stderr"),
)


class IsolationError(RuntimeError):
    """No proven isolation backend, or an unsafe sandbox request."""


def _os_home() -> Path:
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=True)
    except (KeyError, OSError):
        raise IsolationError(CONFIG_REQUIRED) from None


def _sandbox_executable() -> str:
    for candidate in ("/usr/bin/sandbox-exec", shutil.which("sandbox-exec")):
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise IsolationError(CONFIG_REQUIRED)


@lru_cache(maxsize=1)
def _probe_backend() -> tuple[bool, str]:
    if sys.platform != "darwin":
        return False, "unsupported_platform"
    try:
        executable = _sandbox_executable()
    except IsolationError:
        return False, "sandbox_exec_missing"
    try:
        result = subprocess.run(
            [executable, "-p", "(version 1)(allow default)", "/usr/bin/true"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin"},
            cwd="/",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "sandbox_exec_probe_failed"
    if result.returncode != 0:
        return False, "sandbox_exec_unavailable"
    return True, ""


def backend_status() -> tuple[bool, str]:
    """Return ``(usable, reason)``; reasons contain no user data."""

    if sys.platform != "darwin":
        return False, "unsupported_platform"
    return _probe_backend()


backend_status.cache_clear = _probe_backend.cache_clear  # type: ignore[attr-defined]


@lru_cache(maxsize=64)
def _profile_is_accepted(profile: str) -> bool:
    """Prove that this exact profile parses and applies before a child runs."""

    try:
        executable = _sandbox_executable()
        result = subprocess.run(
            [executable, "-p", profile, "/usr/bin/true"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={"PATH": "/usr/bin:/bin"},
            cwd="/",
            timeout=5,
            check=False,
        )
    except (IsolationError, OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _reject_broad(path: Path) -> None:
    home = _os_home()
    forbidden = {
        Path("/"), Path("/Users"), Path("/private"), Path("/private/tmp"),
        Path("/private/var"), Path("/private/var/tmp"), Path("/private/var/folders"),
        Path("/tmp"), Path("/var"), Path("/var/tmp"), Path("/var/folders"), home,
    }
    if path in forbidden:
        raise IsolationError("invalid_sandbox_roots")
    for candidate in forbidden:
        if candidate != path and candidate.is_relative_to(path):
            raise IsolationError("invalid_sandbox_roots")


def _canonical(raw: object, *, kind: str, name: str, allow_symlink: bool = False) -> Path:
    if not isinstance(raw, (str, Path)) or isinstance(raw, bytes):
        raise IsolationError("invalid_sandbox_roots")
    text = str(raw)
    if not text or "\x00" in text or "\n" in text or "\r" in text:
        raise IsolationError("invalid_sandbox_roots")
    path = Path(text)
    if not path.is_absolute():
        raise IsolationError("invalid_sandbox_roots")
    if path.is_symlink() and not allow_symlink:
        raise IsolationError("invalid_sandbox_roots")
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise IsolationError("invalid_sandbox_roots") from None
    if kind == "dir" and not resolved.is_dir():
        raise IsolationError("invalid_sandbox_roots")
    if kind == "any" and not (resolved.is_dir() or resolved.is_file()):
        raise IsolationError("invalid_sandbox_roots")
    _reject_broad(resolved)
    return resolved


def _state_root(runtime: Path) -> Path | None:
    for candidate in (runtime, *runtime.parents):
        if candidate.name in {".project-hub-agentd", ".project-hub"}:
            return candidate
    parent = runtime.parent
    if parent == Path("/") or parent in _PROTECTED_ROOTS or parent == _os_home():
        return None
    # Also protect sibling runs when the caller places runtime directly under
    # a private per-controller directory.
    if len(parent.parts) >= 2:
        return parent
    return None


def _protected_roots(runtime: Path) -> tuple[Path, ...]:
    roots = list(_PROTECTED_ROOTS)
    state = _state_root(runtime)
    if state is not None:
        roots.append(state)
    result: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root not in seen:
            seen.add(root)
            result.append(root)
    return tuple(result)


def _sbpl_string(value: str) -> str:
    if any(char in value for char in ('"', "\x00", "\n", "\r")):
        raise IsolationError("invalid_sandbox_roots")
    return value.replace("\\", "\\\\")


def _sbpl_path(path: Path, operation: str = "subpath") -> str:
    return f'({operation} "{_sbpl_string(str(path))}")'


def _normalise_regex(pattern: str) -> str:
    # Python's re.escape emits backslash escapes for literal punctuation.
    # Apple's regex literal is happiest with character classes instead.
    classes = {
        ".": "[.]", "+": "[+]", "(": "[(]", ")": "[)]", "[": "[[]", "]": "[]]",
        "{": "[{]", "}": "[}]", "^": "[\\^]", "$": "[$]", "|": "[|]",
        "*": "[*]", "?": "[?]",
    }
    result: list[str] = []
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern) and pattern[index + 1] in classes:
            result.append(classes[pattern[index + 1]])
            index += 2
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _sbpl_regex(pattern: str) -> str:
    if any(char in pattern for char in ('"', "\x00", "\n", "\r")):
        raise IsolationError("invalid_sandbox_roots")
    return f'(regex #"{_normalise_regex(pattern)}")'


def _fixed_prefix(pattern: str) -> str:
    """Only fixed literal paths, with optional '$' or trailing '.*$', are supported."""
    if not pattern.startswith("^"):
        raise IsolationError("invalid_sandbox_roots")
    body = pattern[1:]
    if body.endswith(".*$"):
        body = body[:-3]
    elif body.endswith("$"):
        body = body[:-1]
    result = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\":
            if index + 1 >= len(body) or body[index + 1].isalnum():
                raise IsolationError("invalid_sandbox_roots")
            result.append(body[index + 1])
            index += 2
        elif body.startswith("[[]", index):
            result.append("[")
            index += 3
        elif body.startswith("[]]", index):
            result.append("]")
            index += 3
        elif char == "[" and index + 2 < len(body) and body[index + 2] == "]" and body[index + 1] not in "^\\-":
            result.append(body[index + 1])
            index += 3
        elif char in ".+*?[](){}|^$":
            raise IsolationError("invalid_sandbox_roots")
        else:
            result.append(char)
            index += 1
    return "".join(result)


def _pattern_root(pattern: str) -> Path:
    prefix = _fixed_prefix(pattern)
    # An unanchored filename prefix may match longer names. Its containing
    # directory is the provable boundary, never the partial component itself.
    return Path(prefix) if prefix.endswith("/") else Path(prefix).parent


def _validate_pattern(pattern: object) -> str:
    if not isinstance(pattern, str) or not pattern or len(pattern) > 512:
        raise IsolationError("invalid_sandbox_roots")
    # Alternation can escape a prefix or anchor; it is unsupported here.
    if "|" in pattern:
        raise IsolationError("invalid_sandbox_roots")
    if not pattern.startswith("^"):
        raise IsolationError("invalid_sandbox_roots")
    if pattern.startswith("^.*") or pattern.startswith("^/.*"):
        raise IsolationError("invalid_sandbox_roots")
    try:
        re.compile(pattern)
    except re.error:
        raise IsolationError("invalid_sandbox_roots") from None
    prefix = _fixed_prefix(pattern)
    if not prefix.startswith("/"):
        raise IsolationError("invalid_sandbox_roots")
    try:
        _reject_broad(_pattern_root(pattern))
    except IsolationError:
        raise
    return pattern


def _dedupe(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def _optional_native_roots(argv0: Path) -> list[Path]:
    roots: list[Path] = []
    for candidate in (argv0, argv0.parent):
        try:
            if candidate.exists():
                resolved = candidate.resolve(strict=True)
                _reject_broad(resolved)
                roots.append(resolved)
        except (OSError, IsolationError):
            continue
    candidates: list[object] = [sys.executable, sys.base_prefix, sys.prefix]
    try:
        paths = sysconfig.get_paths()
        candidates.extend((paths.get("purelib"), paths.get("platlib")))
    except (KeyError, OSError):
        pass
    for candidate in candidates:
        if not isinstance(candidate, (str, Path)) or not str(candidate):
            continue
        try:
            path = Path(candidate)
            if path.exists():
                resolved = path.resolve(strict=True)
                _reject_broad(resolved)
                roots.append(resolved)
        except (OSError, IsolationError):
            continue
    for entry in os.environ.get("PATH", os.defpath).split(os.pathsep):
        if not entry:
            continue
        try:
            path = Path(entry)
            if path.is_absolute() and path.is_dir():
                resolved = path.resolve(strict=True)
                _reject_broad(resolved)
                roots.append(resolved)
        except (OSError, IsolationError):
            continue
    return _dedupe(roots)


def _read_filters(argv0: Path, worktree: Path, runtime: Path, read_roots: list[Path]) -> list[str]:
    optional: list[Path] = []
    for path in _optional_native_roots(argv0):
        if any(target != path and target.is_relative_to(path) for target in (worktree, runtime)):
            continue
        optional.append(path)
    paths = _dedupe([worktree, runtime, *read_roots, *optional])
    filters: list[str] = []
    for path in paths:
        if path.is_dir():
            filters.append(_sbpl_path(path, "subpath"))
        elif path.is_file():
            filters.append(_sbpl_path(path, "literal"))
    return filters


def _write_filters(worktree: Path, runtime: Path, write_roots: list[Path],
                   patterns: list[str], readonly: bool) -> list[str]:
    directories: list[Path] = [runtime]
    if not readonly:
        directories.append(worktree)
    for root in write_roots:
        if readonly:
            if root == worktree:
                continue
            if root.is_relative_to(worktree):
                # A private cache inside the reviewed tree must not become a
                # writable escape hatch.
                continue
            if worktree.is_relative_to(root):
                raise IsolationError("invalid_sandbox_roots")
        directories.append(root)
    filters = [_sbpl_path(path, "subpath") for path in _dedupe(directories)]
    filters.extend(_sbpl_path(path, "subpath") for path in _DEVICE_WRITE_ROOTS)
    filters.extend(_sbpl_path(path, "literal") for path in _DEVICE_WRITE_LITERALS)
    for pattern in patterns:
        checked = _validate_pattern(pattern)
        prefix = _pattern_root(checked)
        for target in (worktree, runtime):
            if prefix != target and target.is_relative_to(prefix):
                raise IsolationError("invalid_sandbox_roots")
        if readonly:
            if prefix == worktree or prefix.is_relative_to(worktree):
                raise IsolationError("invalid_sandbox_roots")
        filters.append(_sbpl_regex(checked))
    return filters


def _any_filter(filters: list[str]) -> str:
    return "(require-any " + " ".join(filters) + ")"


def _build_profile(*, argv0: Path, worktree: Path, runtime: Path, read_roots: list[Path],
                   write_roots: list[Path], patterns: list[str], readonly: bool,
                   network: bool) -> str:
    protected = _protected_roots(runtime)
    protected_filters = [_sbpl_path(path, "subpath") for path in protected]
    read = _read_filters(argv0, worktree, runtime, read_roots)
    read.extend(_sbpl_regex(_validate_pattern(pattern)) for pattern in patterns)
    lines = ["(version 1)", "(allow default)"]
    if read:
        lines.append(
            "(deny file-read-data (require-all "
            + _any_filter(protected_filters)
            + " (require-not " + _any_filter(read) + ")))"
        )
    else:
        lines.append("(deny file-read-data " + _any_filter(protected_filters) + ")")

    write = _write_filters(worktree, runtime, write_roots, patterns, readonly)
    if write:
        lines.append("(deny file-write* (require-not " + _any_filter(write) + "))")
    else:
        lines.append("(deny file-write*)")
    if not network:
        lines.append("(deny network*)")
    return "\n".join(lines) + "\n"


def sandbox_argv(argv, *, worktree, runtime, read_roots=(), write_roots=(),
                 write_patterns=(), readonly=False, network=True) -> list[str]:
    """Return ``sandbox-exec -p PROFILE <argv>`` or fail with config_required."""

    if sys.platform != "darwin":
        raise IsolationError(CONFIG_REQUIRED)
    usable, _reason = backend_status()
    if not usable:
        raise IsolationError(CONFIG_REQUIRED)

    if not isinstance(argv, (list, tuple)) or not argv:
        raise IsolationError("invalid_argv")
    checked_argv: list[str] = []
    for item in argv:
        if not isinstance(item, str) or not item or "\x00" in item:
            raise IsolationError("invalid_argv")
        checked_argv.append(item)
    if not Path(checked_argv[0]).is_absolute():
        raise IsolationError("invalid_argv")

    worktree_path = _canonical(worktree, kind="dir", name="worktree")
    runtime_path = _canonical(runtime, kind="dir", name="runtime")
    if (worktree_path == runtime_path
            or worktree_path.is_relative_to(runtime_path)
            or runtime_path.is_relative_to(worktree_path)):
        raise IsolationError("invalid_sandbox_roots")

    reads = [_canonical(item, kind="any", name="read_roots") for item in _as_sequence(read_roots)]
    writes = [_canonical(item, kind="dir", name="write_roots") for item in _as_sequence(write_roots)]
    patterns = list(_as_sequence(write_patterns))
    if not isinstance(readonly, bool) or not isinstance(network, bool):
        raise IsolationError("invalid_sandbox_roots")
    if readonly and worktree_path in writes:
        writes = [path for path in writes if path != worktree_path]

    # An allow rule for a parent would re-expose the host and sibling runs.
    for root in reads:
        for target in (worktree_path, runtime_path):
            if root != target and target.is_relative_to(root):
                raise IsolationError("invalid_sandbox_roots")
    for root in writes:
        for target in (worktree_path, runtime_path):
            if root != target and target.is_relative_to(root):
                raise IsolationError("invalid_sandbox_roots")

    argv0 = _canonical(checked_argv[0], kind="any", name="argv", allow_symlink=True)
    profile = _build_profile(
        argv0=argv0,
        worktree=worktree_path,
        runtime=runtime_path,
        read_roots=reads,
        write_roots=writes,
        patterns=patterns,
        readonly=readonly,
        network=network,
    )
    if not _profile_is_accepted(profile):
        raise IsolationError(CONFIG_REQUIRED)
    return [_sandbox_executable(), "-p", profile, *checked_argv]


def _as_sequence(value: object) -> tuple:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, Path)) or not hasattr(value, "__iter__"):
        raise IsolationError("invalid_sandbox_roots")
    return tuple(value)  # type: ignore[arg-type]


__all__ = [
    "CONFIG_REQUIRED",
    "IsolationError",
    "backend_status",
    "sandbox_argv",
]
