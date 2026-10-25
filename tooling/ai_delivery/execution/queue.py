from __future__ import annotations

from contextlib import contextmanager
import fnmatch
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
import uuid


AGENT_IDS = frozenset({"kimi_code", "opencode", "dsh", "claude_code"})
ACTIVE_STATES = ("claimed", "dispatching", "running", "cancelling", "quarantined")
FINAL_STATES = frozenset({"completed", "failed", "cancelled", "timed_out", "config_required", "interrupted"})
REQUEST_FIELDS = frozenset({
    "run_id", "task_id", "base_sha", "contract_digest", "agent_id", "repository",
    "scope", "worktree", "deadline", "attempt", "mode", "prompt", "expected_artifacts",
})
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class QueueError(RuntimeError):
    pass


class ConflictError(QueueError):
    pass


class BusyError(QueueError):
    pass


class LeaseError(QueueError):
    pass


class StateError(QueueError):
    pass


def _now():
    return int(time.time())


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _path(value, name):
    if not isinstance(value, str) or not value or "\x00" in value:
        raise ValueError(f"invalid_{name}")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"invalid_{name}")
    return str(path.resolve(strict=True))


def _relative_paths(values, name):
    if not isinstance(values, (tuple, list)) or not values:
        raise ValueError(f"invalid_{name}")
    result = []
    for value in values:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise ValueError(f"invalid_{name}")
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or "." in path.parts:
            raise ValueError(f"invalid_{name}")
        normalized = path.as_posix()
        if normalized != value or normalized.startswith("~"):
            raise ValueError(f"invalid_{name}")
        result.append(normalized)
    return sorted(set(result))


def normalize_request(request):
    if not isinstance(request, dict) or set(request) - REQUEST_FIELDS:
        raise ValueError("invalid_request_fields")
    required = REQUEST_FIELDS
    if not required <= set(request):
        raise ValueError("invalid_request_fields")
    try:
        run_id = str(uuid.UUID(request["run_id"]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("invalid_run_id") from exc
    if request["run_id"] != run_id:
        raise ValueError("invalid_run_id")
    base_sha = request["base_sha"]
    contract_digest = request["contract_digest"]
    if not isinstance(base_sha, str) or not HEX40.fullmatch(base_sha):
        raise ValueError("invalid_base_sha")
    if not isinstance(contract_digest, str) or not HEX64.fullmatch(contract_digest):
        raise ValueError("invalid_contract_digest")
    if request["agent_id"] not in AGENT_IDS:
        raise ValueError("invalid_agent_id")
    if request["mode"] not in {"implement", "review"}:
        raise ValueError("invalid_mode")
    if type(request["deadline"]) is not int or type(request["attempt"]) is not int:
        raise ValueError("invalid_integer")
    if request["deadline"] <= 0 or request["attempt"] <= 0:
        raise ValueError("invalid_integer")
    task_id = request["task_id"]
    prompt = request["prompt"]
    if (not isinstance(task_id, str) or not task_id.strip() or len(task_id) > 256
            or any(ord(char) < 32 for char in task_id)):
        raise ValueError("invalid_task_id")
    if not isinstance(prompt, str) or not prompt or len(prompt.encode("utf-8")) > 8 * 1024 * 1024:
        raise ValueError("invalid_prompt")
    worktree = _path(request["worktree"], "worktree")
    if not Path(worktree).is_dir():
        raise ValueError("invalid_worktree")
    repository = request["repository"]
    if (not isinstance(repository, str) or not repository.strip() or len(repository) > 1024
            or any(ord(char) < 32 for char in repository)):
        raise ValueError("invalid_repository")
    return {
        "run_id": run_id,
        "task_id": task_id,
        "base_sha": base_sha,
        "contract_digest": contract_digest,
        "agent_id": request["agent_id"],
        "repository": repository,
        "scope": _relative_paths(request["scope"], "scope"),
        "worktree": worktree,
        "deadline": request["deadline"],
        "attempt": request["attempt"],
        "mode": request["mode"],
        "prompt": prompt,
        "expected_artifacts": _relative_paths(request["expected_artifacts"], "expected_artifacts"),
    }


def _fixed_prefix(pattern):
    magic = [index for index in (pattern.find("*"), pattern.find("?"), pattern.find("[")) if index >= 0]
    return pattern[:min(magic)] if magic else pattern


def scopes_overlap(left, right):
    for first in left:
        for second in right:
            if first == second or fnmatch.fnmatchcase(first, second) or fnmatch.fnmatchcase(second, first):
                return True
            first_prefix = _fixed_prefix(first)
            second_prefix = _fixed_prefix(second)
            if first_prefix.startswith(second_prefix) or second_prefix.startswith(first_prefix):
                return True
            first_parts = first.split("/")
            second_parts = second.split("/")
            shorter = min(len(first_parts), len(second_parts))
            if not _fixed_prefix(first) and not _fixed_prefix(second) and first_parts[:shorter] == second_parts[:shorter]:
                return True
    return False


class RunStore:
    def __init__(self, root=None):
        self.root = Path.home().joinpath(".project-hub-agentd") if root is None else Path(root)
        if not self.root.is_absolute():
            raise ValueError("queue_root_must_be_absolute")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._check_root()
        os.chmod(self.root, 0o700)
        for name in ("queue.db", "queue.lock"):
            descriptor = os.open(self.root / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            os.close(descriptor)
        self._check_files()
        with self._locked():
            with self._connect() as connection:
                connection.executescript("""
                    CREATE TABLE IF NOT EXISTS runs (
                        run_id TEXT PRIMARY KEY,
                        request_json TEXT NOT NULL,
                        request_digest TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        base_sha TEXT NOT NULL,
                        contract_digest TEXT NOT NULL,
                        agent_id TEXT NOT NULL,
                        repository TEXT NOT NULL,
                        scope_json TEXT NOT NULL,
                        worktree TEXT NOT NULL,
                        deadline INTEGER NOT NULL,
                        attempt INTEGER NOT NULL,
                        mode TEXT NOT NULL,
                        state TEXT NOT NULL,
                        fencing_token TEXT,
                        lease_until INTEGER NOT NULL DEFAULT 0,
                        pid INTEGER,
                        spawn_pgid INTEGER,
                        process_identity TEXT,
                        result TEXT,
                        error TEXT,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS runs_state ON runs(state);
                    CREATE TABLE IF NOT EXISTS events (
                        seq INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        at INTEGER NOT NULL,
                        payload TEXT NOT NULL
                    );
                    CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
                    BEGIN SELECT RAISE(ABORT, 'events_are_append_only'); END;
                    CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                    BEGIN SELECT RAISE(ABORT, 'events_are_append_only'); END;
                """)
                columns = {
                    row["name"] for row in connection.execute("PRAGMA table_info(runs)")
                }
                if "spawn_pgid" not in columns:
                    connection.execute("ALTER TABLE runs ADD COLUMN spawn_pgid INTEGER")

    @classmethod
    def production(cls):
        return cls()

    def _check_root(self):
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise QueueError("unsafe_queue_root")
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise QueueError("unsafe_queue_owner")
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise QueueError("unsafe_queue_permissions")

    def _check_files(self):
        self._check_root()
        for name in ("queue.db", "queue.lock"):
            info = (self.root / name).lstat()
            if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise QueueError("unsafe_queue_file")
            if hasattr(os, "getuid") and info.st_uid != os.getuid():
                raise QueueError("unsafe_queue_owner")
            if stat.S_IMODE(info.st_mode) != 0o600:
                raise QueueError("unsafe_queue_permissions")

    def _connect(self):
        self._check_files()
        connection = sqlite3.connect(self.root / "queue.db", timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _locked(self):
        self._check_files()
        descriptor = os.open(self.root / "queue.lock", os.O_RDWR | os.O_NOFOLLOW)
        try:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    @contextmanager
    def _transaction(self):
        with self._locked():
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.execute("COMMIT")
            except BaseException:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    @staticmethod
    def _row(row):
        if row is None:
            return None
        result = dict(row)
        result["request"] = json.loads(result.pop("request_json"))
        result["scope"] = json.loads(result.pop("scope_json"))
        result["process_identity"] = json.loads(result["process_identity"]) if result["process_identity"] else None
        result["result"] = json.loads(result["result"]) if result["result"] else None
        result.pop("request_digest", None)
        return result

    def enqueue(self, request):
        normalized = normalize_request(request)
        encoded = _json(normalized)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = _now()
        with self._transaction() as connection:
            existing = connection.execute("SELECT request_digest FROM runs WHERE run_id=?", (normalized["run_id"],)).fetchone()
            if existing:
                if not hmac.compare_digest(existing["request_digest"], digest):
                    raise ConflictError("run_id_request_conflict")
            else:
                connection.execute("""
                    INSERT INTO runs (
                        run_id, request_json, request_digest, task_id, base_sha, contract_digest,
                        agent_id, repository, scope_json, worktree, deadline, attempt, mode,
                        state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """, (
                    normalized["run_id"], encoded, digest, normalized["task_id"], normalized["base_sha"],
                    normalized["contract_digest"], normalized["agent_id"], normalized["repository"],
                    _json(normalized["scope"]), normalized["worktree"], normalized["deadline"],
                    normalized["attempt"], normalized["mode"], now, now,
                ))
                self._event(connection, normalized["run_id"], "queued", {"attempt": normalized["attempt"]})
        return self.get(normalized["run_id"])

    def get(self, run_id):
        with self._locked(), self._connect() as connection:
            return self._row(connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())

    def list(self):
        with self._locked(), self._connect() as connection:
            rows = connection.execute("SELECT * FROM runs ORDER BY created_at, run_id").fetchall()
            return [self._row(row) for row in rows]

    def active(self):
        with self._locked(), self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM runs WHERE state IN ({','.join('?' for _ in ACTIVE_STATES)}) ORDER BY created_at, run_id",
                ACTIVE_STATES,
            ).fetchall()
            return [self._row(row) for row in rows]

    def claim(self, run_id, lease_seconds=90):
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid_lease")
        token = secrets.token_urlsafe(32)
        now = _now()
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None or row["state"] != "queued":
                return None
            active = connection.execute(
                f"SELECT repository, scope_json FROM runs WHERE state IN ({','.join('?' for _ in ACTIVE_STATES)})",
                ACTIVE_STATES,
            ).fetchall()
            if len(active) >= 3:
                raise BusyError("execution_capacity_full")
            scope = json.loads(row["scope_json"])
            for other in active:
                if other["repository"] == row["repository"] and scopes_overlap(scope, json.loads(other["scope_json"])):
                    return None
            cursor = connection.execute("""
                UPDATE runs SET state='claimed', fencing_token=?, lease_until=?, updated_at=?
                WHERE run_id=? AND state='queued'
            """, (token, now + lease_seconds, now, run_id))
            if cursor.rowcount != 1:
                return None
            self._event(connection, run_id, "claimed", {"lease_until": now + lease_seconds})
        return self.get(run_id)

    def _fenced(self, connection, run_id, token, states):
        row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None or row["state"] not in states or not row["fencing_token"] or not token:
            raise LeaseError("invalid_fencing_state")
        if not hmac.compare_digest(row["fencing_token"], token) or row["lease_until"] <= _now():
            raise LeaseError("invalid_or_expired_lease")
        return row

    def dispatch(self, run_id, token):
        with self._transaction() as connection:
            self._fenced(connection, run_id, token, ("claimed",))
            connection.execute("UPDATE runs SET state='dispatching', updated_at=? WHERE run_id=?", (_now(), run_id))
            self._event(connection, run_id, "dispatched", {})

        return self.get(run_id)

    def spawned(self, run_id, token, pid, pgid):
        """Persist a just-spawned PID and its new-session PGID before probing identity.

        The caller starts the child in a new session, so it is the group leader
        and ``pgid`` must equal ``pid``.  This is intentionally not a full
        process identity: it is only enough for conservative recovery after an
        identity capture failure, never for signaling.
        """

        if type(pid) is not int or type(pgid) is not int or pid <= 0 or pgid != pid:
            raise ValueError("invalid_spawn_identity")
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            # A cancellation can land after Popen and before this durable fact.
            # Preserve the fact under the same token even when quarantined or
            # expired. This does not renew authority or publish a result.
            if (row is None or row["state"] not in {"dispatching", "cancelling", "quarantined"}
                    or not token or not hmac.compare_digest(row["fencing_token"] or "", token)
                    or row["pid"] not in (None, pid)):
                raise LeaseError("spawn_record_fencing_changed")
            connection.execute(
                "UPDATE runs SET pid=?, spawn_pgid=?, updated_at=? WHERE run_id=?",
                (pid, pgid, _now(), run_id),
            )
            self._event(connection, run_id, "spawned", {"pid": pid, "pgid": pgid})
        return self.get(run_id)

    def renew(self, run_id, token, lease_seconds=90):
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid_lease")
        with self._transaction() as connection:
            self._fenced(connection, run_id, token, ("claimed", "dispatching", "running"))
            until = _now() + lease_seconds
            connection.execute(
                "UPDATE runs SET lease_until=?, updated_at=? WHERE run_id=?", (until, _now(), run_id)
            )
        return self.get(run_id)

    def start(self, run_id, token, pid, identity):
        if type(pid) is not int or pid <= 0 or not isinstance(identity, (tuple, list)) or len(identity) != 3:
            raise ValueError("invalid_process_identity")
        if any(type(value) is not int or value <= 0 for value in identity) or identity[0] != pid:
            raise ValueError("invalid_process_identity")
        with self._transaction() as connection:
            self._fenced(connection, run_id, token, ("dispatching",))
            connection.execute("""
                UPDATE runs SET state='running', pid=?, spawn_pgid=?, process_identity=?, updated_at=? WHERE run_id=?
            """, (pid, identity[1], _json(tuple(identity)), _now(), run_id))
            self._event(connection, run_id, "started", {"pid": pid})
        return self.get(run_id)

    def finish(self, run_id, token, state, result=None, error=None):
        if state not in FINAL_STATES:
            raise ValueError("invalid_final_state")
        if result is not None:
            encoded = _json(result)
            if len(encoded.encode("utf-8")) > 65536:
                raise ValueError("result_too_large")
        if error is not None and (not isinstance(error, str) or len(error) > 4096):
            raise ValueError("invalid_error")
        with self._transaction() as connection:
            self._fenced(connection, run_id, token, ("claimed", "dispatching", "running"))
            connection.execute("""
                UPDATE runs SET state=?, result=?, error=?, lease_until=0,
                    fencing_token=NULL, pid=NULL, spawn_pgid=NULL, process_identity=NULL, updated_at=?
                WHERE run_id=?
            """, (state, _json(result) if result is not None else None, error, _now(), run_id))
            self._event(connection, run_id, state, {"error": error} if error else {})
        return self.get(run_id)

    def expire(self, run_id, token):
        with self._transaction() as connection:
            row = connection.execute("SELECT fencing_token FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None or not row["fencing_token"] or not hmac.compare_digest(row["fencing_token"], token):
                raise LeaseError("invalid_fencing_token")
            connection.execute("UPDATE runs SET lease_until=0, updated_at=? WHERE run_id=?", (_now(), run_id))
        return self.get(run_id)

    def cancel(self, run_id, timeout=5):
        from .process import ProcessGone, ProcessUnknown, group_empty, stop_group

        terminal = None
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise StateError("unknown_run")
            if row["state"] == "queued":
                connection.execute("UPDATE runs SET state='cancelled', updated_at=? WHERE run_id=?", (_now(), run_id))
                self._event(connection, run_id, "cancelled", {})
                terminal = True
            elif row["state"] not in ACTIVE_STATES or row["state"] == "quarantined":
                terminal = True
            else:
                token = row["fencing_token"]
                connection.execute("UPDATE runs SET state='cancelling', updated_at=? WHERE run_id=?", (_now(), run_id))
                self._event(connection, run_id, "cancellation_requested", {})
                identity = json.loads(row["process_identity"]) if row["process_identity"] else None
        if terminal:
            return self.get(run_id)
        if identity is None:
            return self._quarantine(run_id, token, "process_identity_unavailable")
        try:
            stop_group(identity, timeout=timeout)
        except (ProcessUnknown, ProcessGone):
            # A signaled group can briefly look unknown (for example while the
            # child is a zombie).  Confirm emptiness within the same bounded
            # cancellation window before releasing anything.
            deadline = time.monotonic() + max(0, timeout)
            while True:
                try:
                    empty = group_empty(identity[1])
                except ProcessUnknown:
                    empty = None
                if empty:
                    break
                if time.monotonic() >= deadline:
                    reason = "process_group_not_empty" if empty is False else "process_group_unknown"
                    return self._quarantine(run_id, token, reason)
                time.sleep(0.05)
        with self._transaction() as connection:
            current = connection.execute("SELECT state, fencing_token FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if current["state"] != "cancelling" or not hmac.compare_digest(current["fencing_token"] or "", token or ""):
                raise LeaseError("cancellation_fencing_changed")
            connection.execute("""
                UPDATE runs SET state='cancelled', lease_until=0, fencing_token=NULL,
                    pid=NULL, spawn_pgid=NULL, process_identity=NULL, updated_at=? WHERE run_id=?
            """, (_now(), run_id))
            self._event(connection, run_id, "cancelled", {})
        return self.get(run_id)

    def _quarantine(self, run_id, token, reason):
        with self._transaction() as connection:
            row = connection.execute("SELECT state, fencing_token FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None or (token and not hmac.compare_digest(row["fencing_token"] or "", token)):
                raise LeaseError("quarantine_fencing_changed")
            connection.execute("UPDATE runs SET state='quarantined', error=?, updated_at=? WHERE run_id=?", (reason, _now(), run_id))
            self._event(connection, run_id, "quarantined", {"reason": reason})
        return self.get(run_id)

    def recover(self, identity=None):
        from .process import ProcessGone, ProcessUnknown, group_empty, process_identity

        probe = process_identity if identity is None else identity
        recovered = []

        def group_is_empty(pgid):
            # A group can look unknown for a moment after the leader is reaped.
            # Give the kernel a short, bounded grace; never release on unknown.
            deadline = time.monotonic() + 0.5
            while True:
                try:
                    return group_empty(pgid)
                except ProcessUnknown:
                    if time.monotonic() >= deadline:
                        return None
                    time.sleep(0.05)

        for run in self.active():
            state = run["state"]
            if state not in {"quarantined", "cancelling"} and run["lease_until"] > _now():
                continue
            token = run["fencing_token"]
            recorded = run["process_identity"]
            spawn_pid = run["pid"]
            spawn_pgid = run["spawn_pgid"]
            if state == "claimed" and recorded is None and spawn_pid is None:
                with self._transaction() as connection:
                    current = connection.execute("SELECT state, fencing_token FROM runs WHERE run_id=?", (run["run_id"],)).fetchone()
                    if current["state"] == "claimed" and current["fencing_token"] == token:
                        connection.execute("UPDATE runs SET state='queued', lease_until=0, fencing_token=NULL, updated_at=? WHERE run_id=?", (_now(), run["run_id"]))
                        self._event(connection, run["run_id"], "requeued", {})
                recovered.append(self.get(run["run_id"]))
                continue
            if recorded is None:
                # A spawn record is only a conservative absence check.  It is
                # never enough to signal: a live or unverifiable PID stays
                # quarantined with its capacity and scope reserved.
                if spawn_pid is None or spawn_pgid is None:
                    recovered.append(self._quarantine(run["run_id"], token, "process_identity_unavailable"))
                    continue
                try:
                    probe(spawn_pid)
                except ProcessGone:
                    empty = group_is_empty(spawn_pgid)
                    if empty is None:
                        recovered.append(self._quarantine(run["run_id"], token, "process_group_unknown"))
                        continue
                    if not empty:
                        recovered.append(self._quarantine(run["run_id"], token, "process_group_not_empty"))
                        continue
                except (ProcessUnknown, OSError):
                    recovered.append(self._quarantine(run["run_id"], token, "process_identity_unknown"))
                    continue
                else:
                    recovered.append(self._quarantine(run["run_id"], token, "spawn_process_still_live"))
                    continue
            else:
                try:
                    current = probe(recorded[0])
                    if tuple(current) == tuple(recorded):
                        raise ProcessUnknown("recorded process is still live")
                    empty = group_is_empty(recorded[1])
                    if empty is None:
                        recovered.append(self._quarantine(run["run_id"], token, "process_group_unknown"))
                        continue
                    if not empty:
                        recovered.append(self._quarantine(run["run_id"], token, "process_group_not_empty"))
                        continue
                except ProcessGone:
                    empty = group_is_empty(recorded[1])
                    if empty is None:
                        recovered.append(self._quarantine(run["run_id"], token, "process_group_unknown"))
                        continue
                    if not empty:
                        recovered.append(self._quarantine(run["run_id"], token, "process_group_not_empty"))
                        continue
                except (ProcessUnknown, OSError):
                    recovered.append(self._quarantine(run["run_id"], token, "process_identity_unknown"))
                    continue
            cancelled = state == "cancelling" or any(
                event["type"] == "cancellation_requested"
                for event in self.events(run["run_id"])
            )
            final_state = "cancelled" if cancelled else "interrupted"
            with self._transaction() as connection:
                current_row = connection.execute("SELECT state, fencing_token FROM runs WHERE run_id=?", (run["run_id"],)).fetchone()
                if current_row["state"] != state or current_row["fencing_token"] != token:
                    continue
                connection.execute("""
                    UPDATE runs SET state=?, lease_until=0, fencing_token=NULL,
                        pid=NULL, spawn_pgid=NULL, process_identity=NULL, updated_at=? WHERE run_id=?
                """, (final_state, _now(), run["run_id"]))
                self._event(connection, run["run_id"], final_state, {"recovered": True})
            recovered.append(self.get(run["run_id"]))
        return recovered

    @staticmethod
    def _event(connection, run_id, event_type, payload):
        if not isinstance(event_type, str) or not event_type or len(event_type) > 128:
            raise ValueError("invalid_event_type")
        encoded = _json({} if payload is None else payload)
        if len(encoded.encode("utf-8")) > 65536:
            raise ValueError("event_too_large")
        connection.execute(
            "INSERT INTO events(run_id, type, at, payload) VALUES (?, ?, ?, ?)",
            (run_id, event_type, _now(), encoded),
        )

    def event(self, run_id, event_type, payload=None, token=None):
        with self._transaction() as connection:
            if token is not None:
                self._fenced(connection, run_id, token, ("claimed", "dispatching", "running"))
            elif connection.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone() is None:
                raise StateError("unknown_run")
            self._event(connection, run_id, event_type, payload)
        return self.events(run_id)[-1]

    def events(self, run_id=None):
        query = "SELECT * FROM events" + (" WHERE run_id=?" if run_id is not None else "") + " ORDER BY seq"
        args = (run_id,) if run_id is not None else ()
        with self._locked(), self._connect() as connection:
            rows = connection.execute(query, args).fetchall()
            return [{"seq": row["seq"], "run_id": row["run_id"], "type": row["type"], "at": row["at"], "payload": json.loads(row["payload"])} for row in rows]
