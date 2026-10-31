#!/usr/bin/env python3
"""PH-350 V1-OPS-05 health/capacity/backlog baseline drill.

Read-only local/rehearsal baseline collector. The PH-345 contract freezes the
JSONL evidence envelope and exit codes 0/2/3/4/5; production/staging execute is
refused and real CI/deployment capacity remains not_run. Facts are supplied as
redacted JSON so this drill never reads production credentials or customer data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
import sys

CONTRACT_VERSION = 1
PROFILES = {"rehearsal", "staging", "production"}
EXIT = {"pass": 0, "not_run": 2, "fail": 3, "environment_error": 4, "refused": 5}
SENSITIVE = ("password", "token", "secret", "credential", "payroll", "customer",
             "private_key", "api_key", "authorization")
REDACTIONS = ["password", "token", "payroll", "customer", "secret", "credential"]
THRESHOLDS = {
    "queue_warning": 100, "queue_blocker": 1000,
    "reminder_warning": 200, "reminder_blocker": 1000,
    "todo_warning": 200, "todo_blocker": 1000,
    "unsettled_warning": 200, "unsettled_blocker": 1000,
    "disk_warning_pct": 10, "disk_blocker_pct": 5,
    "backup_warning_hours": 24, "backup_blocker_hours": 72,
    "connection_warning_pct": 80, "connection_blocker_pct": 95,
    "slow_query_warning": 10, "slow_query_blocker": 50,
    "table_warning_bytes": 10 * 1024 ** 3, "table_blocker_bytes": 50 * 1024 ** 3,
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _check(check_id, status, expected, actual, ref=""):
    return {"id": check_id, "status": status, "expected": expected,
            "actual": actual, "evidence_ref": ref}


def _envelope(script, mode, profile, checks, exit_code, *, status=None, artifacts=None,
              notes=""):
    if status is None:
        status = "pass" if exit_code == 0 else (
            "not_run" if exit_code == 2 else "blocked" if exit_code == 5 else "fail")
    return {"script": script, "contract_version": CONTRACT_VERSION, "mode": mode,
            "profile": profile, "app_sha": _git_sha(), "started_at": _now(),
            "finished_at": _now(), "exit_code": exit_code, "status": status,
            "checks": checks, "artifacts": artifacts or [], "redactions": REDACTIONS,
            "notes": notes}


def _git_sha():
    import subprocess
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                       stderr=subprocess.DEVNULL, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def append_evidence(path, envelope):
    """Append-only JSONL; dedupe by script+profile+started_at."""
    if not path:
        return None
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
    existing = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    for raw in existing:
        try:
            old = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if (old.get("script") == envelope.get("script")
                and old.get("profile") == envelope.get("profile")
                and old.get("started_at") == envelope.get("started_at")):
            return str(target)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    return str(target)


def _redact(value):
    """Recursively remove sensitive keys from fact snapshots."""
    if isinstance(value, dict):
        clean = {}
        for key, item in value.items():
            if any(word in str(key).lower() for word in SENSITIVE):
                clean[key] = "[redacted]"
            else:
                clean[key] = _redact(item)
        return clean
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _fact_checks(facts):
    checks = []
    db = facts.get("db") or {}
    if not db:
        checks.append(_check("health.db", "not_run", "DB connectivity facts", "missing"))
    else:
        checks.append(_check("health.db", "pass" if db.get("reachable", True) else "fail",
                             "database reachable", "reachable" if db.get("reachable", True) else "unreachable"))
        pending = db.get("migration_pending")
        if pending is None:
            checks.append(_check("health.migrations", "not_run",
                                 "migration pending count", "missing"))
        else:
            checks.append(_check("health.migrations", "fail" if pending else "pass",
                                 "0 pending migrations", pending))
    cache = facts.get("cache") or {}
    if not cache:
        checks.append(_check("health.cache", "not_run", "cache reachability facts", "missing"))
    else:
        checks.append(_check("health.cache", "pass" if cache.get("reachable") else "fail",
                             "cache reachable", cache.get("backend", "missing")))
    queue = facts.get("queue") or {}
    if not queue:
        checks.append(_check("health.queue", "not_run", "worker/queue facts", "missing"))
    else:
        checks.append(_check("health.queue", "pass" if queue.get("worker_alive") else "fail",
                             "worker alive", "alive" if queue.get("worker_alive") else "dead"))
    disk = facts.get("disk") or {}
    free_pct = disk.get("free_percent")
    if free_pct is None:
        checks.append(_check("health.disk", "not_run", "disk free percent", "missing"))
    else:
        status = "fail" if free_pct < THRESHOLDS["disk_blocker_pct"] or free_pct < THRESHOLDS["disk_warning_pct"] else "pass"
        checks.append(_check("health.disk", status,
                             f">= {THRESHOLDS['disk_warning_pct']}% free", free_pct))
    backup = facts.get("backup") or {}
    age = backup.get("age_hours")
    if age is None:
        checks.append(_check("health.backup", "not_run", "last successful backup age", "missing"))
    else:
        status = "fail" if age >= THRESHOLDS["backup_blocker_hours"] or age >= THRESHOLDS["backup_warning_hours"] else "pass"
        checks.append(_check("health.backup", status,
                             f"< {THRESHOLDS['backup_warning_hours']}h", age))
    return checks


def _capacity_checks(facts):
    checks = []
    capacity = facts.get("capacity") or {}
    db = facts.get("db") or {}
    current = capacity.get("connections", db.get("connections"))
    maximum = capacity.get("max_connections", db.get("max_connections"))
    if current is None or maximum is None:
        checks.append(_check("capacity.connections", "not_run",
                             "connection facts", "missing"))
    else:
        pct = (current / maximum * 100) if maximum else 100
        status = "fail" if pct >= THRESHOLDS["connection_warning_pct"] else "pass"
        checks.append(_check("capacity.connections", status,
                             f"< {THRESHOLDS['connection_warning_pct']}% used", f"{pct:.1f}%"))
    largest = capacity.get("largest_table_bytes")
    if largest is None:
        checks.append(_check("capacity.tables", "not_run", "largest table bytes", "missing"))
    else:
        status = "fail" if largest >= THRESHOLDS["table_warning_bytes"] else "pass"
        checks.append(_check("capacity.tables", status,
                             f"< {THRESHOLDS['table_warning_bytes']} bytes", largest))
    slow = capacity.get("slow_query_count")
    if slow is None:
        checks.append(_check("capacity.slow_queries", "not_run", "slow query count", "missing"))
    else:
        status = "fail" if slow >= THRESHOLDS["slow_query_warning"] else "pass"
        checks.append(_check("capacity.slow_queries", status,
                             f"< {THRESHOLDS['slow_query_warning']}", slow))
    attachment = capacity.get("attachment_bytes")
    if attachment is None:
        checks.append(_check("capacity.attachments", "not_run", "attachment bytes", "missing"))
    else:
        checks.append(_check("capacity.attachments", "pass", "recorded attachment bytes", attachment))
    return checks


def _backlog_checks(facts):
    checks = []
    backlog = facts.get("backlog") or {}
    for key, warning, blocker in (("queue_depth", "queue_warning", "queue_blocker"),
                                  ("reminders", "reminder_warning", "reminder_blocker"),
                                  ("todos", "todo_warning", "todo_blocker"),
                                  ("unsettled", "unsettled_warning", "unsettled_blocker")):
        value = backlog.get(key)
        if value is None:
            checks.append(_check(f"backlog.{key}", "not_run", f"{key} count", "missing"))
        else:
            status = "fail" if value >= THRESHOLDS[blocker] else (
                "pass" if value < THRESHOLDS[warning] else "fail")
            checks.append(_check(f"backlog.{key}", status,
                                 f"< {THRESHOLDS[warning]}", value))
    return checks


def _perf_checks(facts):
    perf = facts.get("perf") or {}
    samples = perf.get("samples_ms") or []
    if not samples or any(type(value) not in (int, float) or value < 0 for value in samples):
        return [_check("perf.samples", "not_run", "non-negative duration samples", "missing")], None
    ordered = sorted(float(value) for value in samples)

    def nearest(percent):
        index = max(0, math.ceil(percent / 100 * len(ordered)) - 1)
        return ordered[min(index, len(ordered) - 1)]

    p50, p95 = nearest(50), nearest(95)
    return [
        _check("perf.samples", "pass", "collected samples", len(ordered)),
        _check("perf.p50", "pass", "record local P50", p50),
        _check("perf.p95", "pass", "record local P95", p95),
    ], {"count": len(ordered), "p50_ms": p50, "p95_ms": p95,
        "unit": "ms", "environment": perf.get("environment", "local")}


def run_healthcheck(profile="rehearsal", mode="dry_run", *, facts=None,
                    confirm_production=False):
    """Collect local/read-only health, capacity, backlog and perf facts."""
    facts = _redact(dict(facts or {}))
    checks = []
    if profile not in PROFILES:
        return EXIT["fail"], _envelope("ops_health_capacity", mode, profile, [
            _check("profile.known", "fail", "rehearsal|staging|production", profile)
        ], EXIT["fail"])
    if mode not in {"dry_run", "execute"}:
        return EXIT["fail"], _envelope("ops_health_capacity", mode, profile, [
            _check("mode.valid", "fail", "dry_run|execute", mode)
        ], EXIT["fail"])
    if mode == "execute" and profile != "rehearsal":
        return EXIT["refused"], _envelope("ops_health_capacity", mode, profile, [
            _check("safety.execute_profile", "fail",
                   "execute only in isolated rehearsal", profile)
        ], EXIT["refused"], status="blocked",
            notes="Execute is restricted to the rehearsal profile; real CI/deployment capacity remains not_run.")
    checks.append(_check("profile.known", "pass", "rehearsal|staging|production", profile))
    if not facts:
        checks.append(_check("environment.supplied", "not_run",
                             "redacted local/test facts JSON", "missing"))
        return EXIT["not_run"], _envelope("ops_health_capacity", mode, profile, checks,
                                          EXIT["not_run"], status="not_run",
                                          notes="No environment facts supplied; no production collection attempted.")
    checks.append(_check("environment.supplied", "pass", "redacted local/test facts", "provided"))
    checks.extend(_fact_checks(facts))
    checks.extend(_capacity_checks(facts))
    checks.extend(_backlog_checks(facts))
    perf_checks, perf_summary = _perf_checks(facts)
    checks.extend(perf_checks)
    failed = [c for c in checks if c["status"] == "fail"]
    not_run = [c for c in checks if c["status"] == "not_run"]
    code = EXIT["fail"] if failed else EXIT["not_run"] if not_run else EXIT["pass"]
    status = "fail" if failed else "not_run" if not_run else "pass"
    artifacts = []
    if perf_summary is not None:
        artifacts.append({"kind": "log", "path_or_url": "local-perf-summary.json",
                          "sha256": hashlib.sha256(
                              json.dumps(perf_summary, sort_keys=True).encode()).hexdigest()})
    return code, _envelope("ops_health_capacity", mode, profile, checks, code,
                           status=status, artifacts=artifacts,
                           notes="Local/rehearsal facts only; real CI/deployment capacity is not measured and must not be replaced by local numbers.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="PH-350 health/capacity/backlog baseline")
    parser.add_argument("--mode", choices=["dry_run", "execute"], default="dry_run")
    parser.add_argument("--env", dest="profile", choices=sorted(PROFILES), default="rehearsal")
    parser.add_argument("--out", default="")
    parser.add_argument("--facts", default="")
    args = parser.parse_args(argv)
    try:
        facts = json.loads(args.facts) if args.facts else {}
    except json.JSONDecodeError:
        print(json.dumps({"error": "facts must be valid JSON"}), file=sys.stderr)
        return EXIT["fail"]
    code, evidence = run_healthcheck(args.profile, args.mode, facts=facts)
    append_evidence(args.out, evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
