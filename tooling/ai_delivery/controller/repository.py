from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import time
from urllib.parse import urlsplit
import uuid


_GIT_TIMEOUT = 30
_GIT_OUTPUT_LIMIT = 16 * 1024 * 1024
_GIT_PATH = "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_SLUG = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9_.-]{1,100}\Z")
_TASK_ID = re.compile(r"PH-[0-9]+\Z")
_MODES = frozenset({"100644", "100755", "120000"})
_EXECUTABLE_STATES = frozenset({"ready", "in_progress", "in_review"})
_DIGEST_TASK_FIELDS = (
    "id", "title", "kind", "risk", "depends_on", "spec",
    "allowed_paths", "acceptance", "github_issue",
)


class ControllerError(RuntimeError):
    pass


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _directory(value, name):
    if not isinstance(value, (str, os.PathLike)) or isinstance(value, bytes):
        raise ControllerError(f"invalid_{name}")
    try:
        path = Path(value).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ControllerError(f"invalid_{name}") from None
    if not path.is_dir():
        raise ControllerError(f"invalid_{name}")
    return path


def _relative_path(value, *, pattern=False):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ControllerError("invalid_relative_path")
    if any(ord(char) < 32 or char == "\\" for char in value):
        raise ControllerError("invalid_relative_path")
    path = Path(value)
    parts = value.split("/")
    if path.is_absolute() or value.startswith("/") or any(part in {"", ".", ".."} for part in parts):
        raise ControllerError("invalid_relative_path")
    if not pattern and any(char in value for char in "*?["):
        raise ControllerError("scope_must_be_literal")
    return value


def _path_matches(path, pattern):
    # Use the same allowed-path semantics as trusted projectctl governance.
    return fnmatch.fnmatchcase(path, pattern)


def _in_scope(path, scope):
    return any(_path_matches(path, pattern) for pattern in scope)


def _excluded(path, *, mode=None):
    parts = path.split("/")
    if ".git" in parts:
        return True
    return (mode in {"100644", "100755"} and len(parts) >= 2
            and parts[-2] == "__pycache__" and parts[-1].endswith(".pyc"))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _kill(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            process.kill()
        except OSError:
            pass


def _run_git(root, arguments, *, accepted=(0,), operation="git"):
    argv = [
        "git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.ext.allow=never",
        "-c", "diff.external=", *arguments,
    ]
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update({
        "PATH": _GIT_PATH,
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    })
    try:
        process = subprocess.Popen(
            argv, cwd=root, env=env, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
    except OSError:
        raise ControllerError(f"{operation}_unavailable") from None
    streams = {}
    selector = selectors.DefaultSelector()
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
        streams[name] = bytearray()
    deadline = time.monotonic() + _GIT_TIMEOUT
    failed = None
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failed = "timeout"
                break
            for key, _events in selector.select(min(remaining, 0.2)):
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(stream)
                    continue
                output = streams[key.data]
                if len(output) + len(chunk) > _GIT_OUTPUT_LIMIT:
                    failed = "output_limit"
                    break
                output.extend(chunk)
            if failed:
                break
            if process.poll() is not None and not selector.get_map():
                break
        if failed:
            _kill(process)
        try:
            returncode = process.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            _kill(process)
            process.wait()
            failed = "timeout"
    finally:
        for stream in (process.stdout, process.stderr):
            stream.close()
        selector.close()
    if failed == "timeout":
        raise ControllerError(f"{operation}_timeout")
    if failed == "output_limit":
        raise ControllerError(f"{operation}_output_too_large")
    if returncode not in accepted:
        raise ControllerError(f"{operation}_failed")
    return bytes(streams["stdout"])


def _origin_slug(url):
    if not isinstance(url, str) or not url or any(ord(char) < 32 for char in url):
        raise ControllerError("invalid_origin")
    scp = re.fullmatch(r"git@github\.com:([^\s/?#]+/[^\s/?#]+)", url)
    if scp:
        path = scp.group(1)
    else:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise ControllerError("invalid_origin") from None
        if parsed.scheme not in {"https", "ssh"} or parsed.hostname != "github.com":
            raise ControllerError("invalid_origin")
        if parsed.query or parsed.fragment or parsed.password is not None:
            raise ControllerError("invalid_origin")
        if parsed.scheme == "https":
            if parsed.username is not None or port is not None:
                raise ControllerError("invalid_origin")
        elif parsed.username != "git" or port not in {None, 22}:
            raise ControllerError("invalid_origin")
        path = parsed.path
        if not path.startswith("/") or path.endswith("/"):
            raise ControllerError("invalid_origin")
        path = path[1:]
    if path.endswith(".git"):
        path = path[:-4]
    if not _SLUG.fullmatch(path) or path.split("/", 1)[1] in {".", ".."}:
        raise ControllerError("invalid_origin")
    return path


def _base_sha(value):
    if not isinstance(value, str) or not _HEX40.fullmatch(value):
        raise ControllerError("invalid_base_sha")
    return value


def _entry_descriptor(mode, data):
    return {"mode": mode, "type": "blob", "sha256": _sha(data)}


def _validate_descriptor(value):
    if not isinstance(value, dict) or set(value) != {"mode", "type", "sha256"}:
        raise ControllerError("invalid_manifest")
    if value["mode"] not in _MODES or value["type"] != "blob":
        raise ControllerError("invalid_manifest")
    if not isinstance(value["sha256"], str) or not _HEX64.fullmatch(value["sha256"]):
        raise ControllerError("invalid_manifest")
    return dict(value)


def _safe_entry(root, relative, *, allow_missing_parent=False):
    path = root.joinpath(*relative.split("/"))
    parent = path.parent
    try:
        resolved = parent.resolve(strict=True)
    except FileNotFoundError:
        if allow_missing_parent:
            return path
        raise ControllerError("filesystem_conflict") from None
    except (OSError, RuntimeError):
        raise ControllerError("filesystem_conflict") from None
    if resolved != parent or not resolved.is_relative_to(root):
        raise ControllerError("filesystem_escape")
    return path


def _read_entry(root, relative, *, required=True):
    path = _safe_entry(root, relative, allow_missing_parent=not required)
    try:
        info = path.lstat()
    except FileNotFoundError:
        if required:
            raise ControllerError("filesystem_conflict") from None
        return None
    except OSError:
        raise ControllerError("filesystem_conflict") from None
    if stat.S_ISLNK(info.st_mode):
        try:
            text = os.readlink(path)
            data = text.encode("utf-8")
        except (OSError, UnicodeError):
            raise ControllerError("invalid_symlink") from None
        _check_symlink(root, path, text)
        return _entry_descriptor("120000", data)
    if not stat.S_ISREG(info.st_mode):
        raise ControllerError("special_file_rejected")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise ControllerError("filesystem_conflict")
            digest = hashlib.sha256()
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
    except ControllerError:
        raise
    except OSError:
        raise ControllerError("filesystem_conflict") from None
    mode = "100755" if opened.st_mode & 0o111 else "100644"
    return {"mode": mode, "type": "blob", "sha256": digest.hexdigest()}


def _check_symlink(root, path, text):
    if not text or "\x00" in text:
        raise ControllerError("invalid_symlink")
    try:
        target = Path(text) if Path(text).is_absolute() else path.parent / text
        resolved = target.resolve(strict=False)
    except (OSError, RuntimeError):
        raise ControllerError("invalid_symlink") from None
    if not resolved.is_relative_to(root):
        raise ControllerError("symlink_escape")


def _create_parents(root, relative):
    current = root
    for part in relative.split("/")[:-1]:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            try:
                current.mkdir()
            except OSError:
                raise ControllerError("filesystem_conflict") from None
            continue
        except OSError:
            raise ControllerError("filesystem_conflict") from None
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ControllerError("filesystem_conflict")


class GitRepo:
    def __init__(self, root, allowed_repositories):
        self.root = _directory(root, "repository_root")
        if isinstance(allowed_repositories, (str, bytes)):
            raise ControllerError("invalid_allowed_repositories")
        try:
            allowed = frozenset(allowed_repositories)
        except TypeError:
            raise ControllerError("invalid_allowed_repositories") from None
        if not allowed or any(not isinstance(slug, str) or not _SLUG.fullmatch(slug) for slug in allowed):
            raise ControllerError("invalid_allowed_repositories")
        top = _run_git(self.root, ["rev-parse", "--show-toplevel"], operation="repository_check")
        try:
            top_path = Path(os.fsdecode(top).strip()).resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            raise ControllerError("invalid_repository") from None
        if top_path != self.root:
            raise ControllerError("repository_root_mismatch")
        url = os.fsdecode(_run_git(
            self.root, ["remote", "get-url", "origin"], operation="origin_check"
        )).strip()
        self.slug = _origin_slug(url)
        if self.slug not in allowed:
            raise ControllerError("repository_not_allowed")

    def base(self):
        value = _run_git(
            self.root, ["rev-parse", "--verify", "refs/remotes/origin/main^{commit}"],
            operation="base_check",
        )
        try:
            return _base_sha(value.decode("ascii").strip())
        except UnicodeError:
            raise ControllerError("invalid_base") from None

    def refresh(self):
        _run_git(self.root, ["fetch", "origin", "main"], operation="fetch")

    def tasks(self):
        return self._tasks_at(self.base())

    def _tasks_at(self, base):
        raw = _run_git(self.root, ["cat-file", "blob", f"{base}:project/tasks.json"], operation="tasks_read")
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise ControllerError("invalid_tasks") from None
        if not isinstance(document, dict) or not isinstance(document.get("tasks"), list):
            raise ControllerError("invalid_tasks")
        if any(not isinstance(task, dict) for task in document["tasks"]):
            raise ControllerError("invalid_tasks")
        return document["tasks"]

    def _is_ancestor(self, base_sha, current):
        try:
            _run_git(
                self.root, ["merge-base", "--is-ancestor", base_sha, current],
                operation="base_ancestry",
            )
        except ControllerError as exc:
            if str(exc) == "base_ancestry_failed":
                raise ControllerError("base_not_ancestor") from None
            raise

    def contract(self, task_id, base_sha=None):
        current = self.base()
        recorded = current if base_sha is None else _base_sha(base_sha)
        if recorded != current:
            self._is_ancestor(recorded, current)
        if not isinstance(task_id, str) or not task_id:
            raise ControllerError("invalid_task_id")
        if recorded != current:
            latest = self._tasks_at(current)
            current_task = next((task for task in latest if task.get("id") == task_id), None)
            latest_by_id = {task.get("id"): task for task in latest}
            if (current_task is None or current_task.get("status") not in _EXECUTABLE_STATES
                    or any(latest_by_id.get(dep, {}).get("status") != "done"
                           for dep in current_task.get("depends_on", []))):
                raise ControllerError("task_not_executable")
        tasks = self._tasks_at(recorded)
        matches = [task for task in tasks if task.get("id") == task_id]
        if len(matches) != 1:
            raise ControllerError("task_not_found")
        task = matches[0]
        if task.get("status") not in _EXECUTABLE_STATES:
            raise ControllerError("task_not_executable")
        by_id = {}
        for candidate in tasks:
            identifier = candidate.get("id")
            if isinstance(identifier, str):
                if identifier in by_id:
                    raise ControllerError("duplicate_task_id")
                by_id[identifier] = candidate
        dependencies = task.get("depends_on")
        if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
            raise ControllerError("invalid_dependencies")
        if any(by_id.get(item, {}).get("status") != "done" for item in dependencies):
            raise ControllerError("task_dependencies_not_done")
        for field in ("title", "kind", "risk"):
            if not isinstance(task.get(field), str) or not task[field]:
                raise ControllerError("invalid_task_contract")
        spec_path = _relative_path(task.get("spec"))
        allowed = task.get("allowed_paths")
        acceptance = task.get("acceptance")
        if (not isinstance(allowed, list) or not allowed
                or any(not isinstance(item, str) for item in allowed)):
            raise ControllerError("invalid_allowed_paths")
        for pattern in allowed:
            _relative_path(pattern, pattern=True)
        if (not isinstance(acceptance, list) or not acceptance
                or any(not isinstance(item, str) or not item.strip() for item in acceptance)):
            raise ControllerError("invalid_acceptance")
        issue = task.get("github_issue")
        issue_pattern = rf"https://github\.com/{re.escape(self.slug)}/issues/([0-9]+)"
        if not isinstance(issue, str) or not re.fullmatch(issue_pattern, issue) or int(issue.rsplit("/", 1)[1]) < 1:
            raise ControllerError("invalid_github_issue")
        spec_bytes = _run_git(self.root, ["cat-file", "blob", f"{recorded}:{spec_path}"], operation="spec_read")
        try:
            spec = spec_bytes.decode("utf-8")
        except UnicodeError:
            raise ControllerError("invalid_spec_utf8") from None
        frozen = {field: task[field] for field in _DIGEST_TASK_FIELDS}
        digest = _sha(_canonical_json({"task": frozen, "spec": spec}).encode("utf-8"))
        return {
            "repository": self.slug,
            "base_sha": recorded,
            "task": task,
            "spec": spec,
            "contract_digest": digest,
        }

    def validate_scope(self, contract, scope=None):
        if not isinstance(contract, dict) or not isinstance(contract.get("task"), dict):
            raise ControllerError("invalid_contract")
        allowed = contract["task"].get("allowed_paths")
        if not isinstance(allowed, list) or not allowed:
            raise ControllerError("invalid_allowed_paths")
        for pattern in allowed:
            if not isinstance(pattern, str):
                raise ControllerError("invalid_allowed_paths")
            _relative_path(pattern, pattern=True)
        if scope is None:
            return list(allowed)
        if not isinstance(scope, (list, tuple)) or not scope:
            raise ControllerError("invalid_scope")
        result = []
        for value in scope:
            if value in allowed:
                result.append(_relative_path(value, pattern=True))
                continue
            path = _relative_path(value)
            if not any(_path_matches(path, pattern) for pattern in allowed):
                raise ControllerError("scope_not_allowed")
            result.append(path)
        return sorted(set(result))

    def create_worktree(self, task_id, run_id, base_sha, target):
        base = _base_sha(base_sha)
        if base != self.base():
            raise ControllerError("new_worktree_requires_current_base")
        self.contract(task_id, base)
        if not _TASK_ID.fullmatch(task_id):
            raise ControllerError("invalid_task_id")
        try:
            parsed_run = str(uuid.UUID(run_id))
        except (ValueError, TypeError, AttributeError):
            raise ControllerError("invalid_run_id") from None
        if run_id != parsed_run:
            raise ControllerError("invalid_run_id")
        if not isinstance(target, (str, os.PathLike)) or isinstance(target, bytes):
            raise ControllerError("invalid_worktree_target")
        requested = Path(target)
        if not requested.is_absolute() or requested.name in {"", ".", ".."}:
            raise ControllerError("invalid_worktree_target")
        if requested.exists() or requested.is_symlink():
            raise ControllerError("worktree_target_exists")
        try:
            parent = requested.parent.resolve(strict=True)
        except (OSError, RuntimeError):
            raise ControllerError("invalid_worktree_target") from None
        destination = parent / requested.name
        branch = f"feat/{task_id}-agent-{parsed_run}"
        _run_git(
            self.root, ["worktree", "add", "-b", branch, str(destination), base],
            operation="worktree_add",
        )
        return _directory(destination, "worktree_target")

    def _baseline(self, base_sha):
        base = _base_sha(base_sha)
        current = self.base()
        if base != current:
            self._is_ancestor(base, current)
        output = _run_git(
            self.root, ["ls-tree", "-r", "-z", "--full-tree", base], operation="baseline_read"
        )
        result = {}
        for record in output.split(b"\x00"):
            if not record:
                continue
            try:
                metadata, raw_path = record.split(b"\t", 1)
                mode, kind, object_sha = metadata.decode("ascii").split(" ")
                path = raw_path.decode("utf-8")
            except (UnicodeError, ValueError):
                raise ControllerError("invalid_baseline") from None
            _relative_path(path, pattern=True)
            if kind != "blob" or mode not in _MODES or not _HEX40.fullmatch(object_sha):
                raise ControllerError("unsupported_baseline_entry")
            if _excluded(path, mode=mode):
                continue
            data = _run_git(self.root, ["cat-file", "blob", object_sha], operation="baseline_blob_read")
            result[path] = _entry_descriptor(mode, data)
        return result

    def _candidate(self, worktree):
        def scan_failed(_error):
            raise ControllerError("filesystem_scan_failed")

        result = {}
        for parent, directories, files in os.walk(
                worktree, topdown=True, onerror=scan_failed, followlinks=False):
            parent_path = Path(parent)
            kept = []
            for name in sorted(directories):
                path = parent_path / name
                relative = _relative_path(path.relative_to(worktree).as_posix(), pattern=True)
                if _excluded(relative):
                    continue
                if path.is_symlink():
                    result[relative] = _read_entry(worktree, relative)
                else:
                    kept.append(name)
            directories[:] = kept
            for name in sorted(files):
                path = parent_path / name
                relative = _relative_path(path.relative_to(worktree).as_posix(), pattern=True)
                entry = _read_entry(worktree, relative)
                if not _excluded(relative, mode=entry["mode"]):
                    result[relative] = entry
        return result

    def manifest(self, worktree, base_sha, scope):
        root = _directory(worktree, "worktree")
        if not isinstance(scope, (list, tuple)) or not scope:
            raise ControllerError("invalid_scope")
        checked_scope = [_relative_path(item, pattern=True) for item in scope]
        old = self._baseline(base_sha)
        new = self._candidate(root)
        result = []
        for path in sorted(set(old) | set(new)):
            before = old.get(path)
            after = new.get(path)
            if before == after:
                continue
            if not _in_scope(path, checked_scope):
                raise ControllerError("changed_path_outside_scope")
            result.append({"path": path, "old": before, "new": after})
        return result

    def diff_digest(self, manifest):
        return _sha(_canonical_json(manifest).encode("utf-8"))

    def apply_manifest(self, source, target, manifest):
        source_root = _directory(source, "source")
        target_root = _directory(target, "target")
        if source_root == target_root:
            raise ControllerError("source_target_must_differ")
        if not isinstance(manifest, list):
            raise ControllerError("invalid_manifest")
        entries = []
        seen = set()
        for item in manifest:
            if not isinstance(item, dict) or set(item) != {"path", "old", "new"}:
                raise ControllerError("invalid_manifest")
            path = _relative_path(item["path"], pattern=True)
            if path in seen or _excluded(path):
                raise ControllerError("invalid_manifest")
            seen.add(path)
            before = None if item["old"] is None else _validate_descriptor(item["old"])
            after = None if item["new"] is None else _validate_descriptor(item["new"])
            if before is None and after is None:
                raise ControllerError("invalid_manifest")
            entries.append({"path": path, "old": before, "new": after})
        entries.sort(key=lambda item: item["path"])
        # Compare file descriptors as a set, so a deleted file can become a
        # directory and a deleted directory's contents can become one file.
        target_entries = self._candidate(target_root)
        deletions = {item["path"] for item in entries if item["new"] is None}
        remove_directories = set()
        for item in entries:
            if item["new"] is not None:
                actual = _read_entry(source_root, item["path"])
                if actual != item["new"]:
                    raise ControllerError("source_manifest_mismatch")
            if target_entries.get(item["path"]) != item["old"]:
                raise ControllerError("target_manifest_conflict")
            destination = target_root / item["path"]
            if item["new"] is not None and destination.is_dir() and not destination.is_symlink():
                # Every existing leaf must be an explicit deletion. Include
                # excluded caches here: an unlisted file must never be removed.
                for parent, dirs, files in os.walk(destination, followlinks=False):
                    parent = Path(parent)
                    remove_directories.add(parent)
                    for name in dirs + files:
                        child = parent / name
                        if child.is_symlink() or not child.is_dir():
                            if child.relative_to(target_root).as_posix() not in deletions:
                                raise ControllerError("target_manifest_conflict")
        for item in sorted(entries, key=lambda row: (row["path"].count("/"), row["path"]), reverse=True):
            if item["new"] is None:
                if _read_entry(target_root, item["path"]) != item["old"]:
                    raise ControllerError("target_manifest_conflict")
                try:
                    _safe_entry(target_root, item["path"]).unlink()
                except OSError:
                    raise ControllerError("filesystem_conflict") from None
        for directory in sorted(remove_directories, key=lambda path: len(path.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                raise ControllerError("target_manifest_conflict") from None
        for item in entries:
            after = item["new"]
            if after is None:
                continue
            path = item["path"]
            if _read_entry(source_root, path) != after:
                raise ControllerError("source_manifest_mismatch")
            _create_parents(target_root, path)
            if _read_entry(target_root, path, required=False) != item["old"]:
                raise ControllerError("target_manifest_conflict")
            destination = _safe_entry(target_root, path)
            source_path = _safe_entry(source_root, path)
            if after["mode"] == "120000":
                try:
                    text = os.readlink(source_path)
                except OSError:
                    raise ControllerError("source_manifest_mismatch") from None
                _check_symlink(target_root, destination, text)
                if item["old"] is not None:
                    try:
                        destination.unlink()
                    except OSError:
                        raise ControllerError("filesystem_conflict") from None
                try:
                    os.symlink(text, destination)
                except OSError:
                    raise ControllerError("filesystem_conflict") from None
                continue
            if item["old"] is not None and item["old"]["mode"] == "120000":
                try:
                    destination.unlink()
                except OSError:
                    raise ControllerError("filesystem_conflict") from None
            flags = os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            if item["old"] is None:
                flags |= os.O_EXCL
            else:
                flags |= os.O_TRUNC
            try:
                source_descriptor = os.open(source_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(source_descriptor, "rb") as source_file:
                    if not stat.S_ISREG(os.fstat(source_file.fileno()).st_mode):
                        raise ControllerError("source_manifest_mismatch")
                    descriptor = os.open(destination, flags, int(after["mode"], 8))
                    with os.fdopen(descriptor, "wb") as output:
                        while True:
                            chunk = source_file.read(1024 * 1024)
                            if not chunk:
                                break
                            output.write(chunk)
                os.chmod(destination, int(after["mode"], 8), follow_symlinks=False)
            except OSError:
                raise ControllerError("filesystem_conflict") from None
            if _read_entry(target_root, path) != after:
                raise ControllerError("applied_manifest_mismatch")


__all__ = ["ControllerError", "GitRepo"]
