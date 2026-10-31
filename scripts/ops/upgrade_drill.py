#!/usr/bin/env python3
"""PH-347 local upgrade/rollback drill (V1-OPS-03).

Contract: docs/engineering/ops-runbook.md + upgrade-recovery.md. Never upgrades
production; execute is limited to an isolated rehearsal. Exit codes 0/2/3/4/5
as frozen by PH-345. Evidence is append-only JSONL envelopes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

CONTRACT_VERSION = 1
PROFILES = {"rehearsal", "staging", "production"}
REDACTIONS = ["password", "token", "payroll", "customer", "secret", "key"]
EXIT = {"pass": 0, "not_run": 2, "fail": 3, "environment_error": 4, "refused": 5}
DESTRUCTIVE = ("drop_", "delete_", "remove_", "truncate_")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _git_sha():
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                       stderr=subprocess.DEVNULL, timeout=5).strip()
    except (OSError, subprocess.SubprocessError):
        return None


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


def append_evidence(path, envelope):
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


def _migration_checks(before, after):
    checks = []
    before = list(before or [])
    after = list(after or [])
    if not before:
        checks.append(_check("migration.baseline", "not_run", "baseline migrations recorded", "missing"))
    if not after:
        checks.append(_check("migration.candidate", "not_run", "candidate migrations recorded", "missing"))
    if before and after:
        if not after[:len(before)] == before:
            checks.append(_check("migration.append_only", "fail",
                                 "candidate starts with baseline migrations", "history rewritten"))
        else:
            checks.append(_check("migration.append_only", "pass",
                                 "candidate starts with baseline migrations",
                                 f"+{len(after) - len(before)} append"))
        destructive = [name for name in after[len(before):]
                       if any(word in str(name).lower() for word in DESTRUCTIVE)]
        checks.append(_check("migration.destructive", "fail" if destructive else "pass",
                             "no destructive DDL", ",".join(destructive) or "none"))
        duplicates = len(after) != len(set(after))
        checks.append(_check("migration.duplicates", "fail" if duplicates else "pass",
                             "candidate migration list has no duplicates",
                             "duplicate entries" if duplicates else "none"))
    return checks


def _backup_checks(backup):
    if not backup:
        return [_check("backup.evidence", "not_run", "verified DB+attachment backup",
                       "missing")]
    path = Path(str(backup))
    if not path.exists() or path.stat().st_size == 0:
        return [_check("backup.evidence", "fail", "non-empty backup artifact",
                       "missing/empty")]
    return [_check("backup.evidence", "pass", "non-empty backup artifact",
                   hashlib.sha256(path.read_bytes()).hexdigest())]


def upgrade_plan(profile, mode="dry_run", *, baseline=None, candidate=None,
                 migrations_before=None, migrations_after=None, backup=None,
                 rollback_compatible=True, confirm_production=False, db=None,
                 run_migrate=False):
    """V1-OPS-03: plan or isolated-rehearsal upgrade; production is refused."""
    profile = profile if profile in PROFILES else "rehearsal"
    checks = []
    if mode not in {"dry_run", "execute"}:
        return EXIT["fail"], _envelope("ops_upgrade", mode, profile, [
            _check("mode.valid", "fail", "dry_run|execute", mode)], EXIT["fail"])
    if mode == "execute" and profile == "production":
        return EXIT["refused"], _envelope("ops_upgrade", mode, profile, [
            _check("safety.production_execute", "fail", "production execute refused", "refused")
        ], EXIT["refused"], status="blocked",
            notes="Production upgrade is never performed by this drill.")
    checks.append(_check("profile.known", "pass", "rehearsal|staging|production", profile))
    for label, value in (("baseline", baseline), ("candidate", candidate)):
        ok = isinstance(value, str) and value.strip() != ""
        checks.append(_check(f"release.{label}", "pass" if ok else "not_run",
                             f"{label} SHA recorded", value or "missing"))
    checks.extend(_backup_checks(backup))
    checks.extend(_migration_checks(migrations_before, migrations_after))
    checks.append(_check("rollback.contract", "pass" if rollback_compatible else "fail",
                         "structural-compatible rollback only",
                         "compatible" if rollback_compatible else "incompatible"))
    if mode == "execute" and not backup:
        return EXIT["not_run"], _envelope("ops_upgrade", mode, profile, checks,
                                          EXIT["not_run"], status="not_run",
                                          notes="Execute requires verified backup evidence; no upgrade performed.")
    if mode == "execute":
        with tempfile.TemporaryDirectory(prefix="ph347-upgrade-") as directory:
            artifact = Path(directory) / "upgrade-plan.json"
            artifact.write_text(json.dumps({"baseline": baseline, "candidate": candidate,
                                            "migrations_before": migrations_before,
                                            "migrations_after": migrations_after,
                                            "rollback_compatible": rollback_compatible}),
                                encoding="utf-8")
            artifacts = [{"kind": "log", "path_or_url": "upgrade-plan.json",
                          "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}]
        checks.append(_check("upgrade.rehearsal", "pass", "isolated upgrade plan artifact",
                             "created and discarded"))
        if db and run_migrate:
            try:
                code = _run_migrate(db)
            except (OSError, subprocess.SubprocessError) as exc:
                checks.append(_check("upgrade.migrate", "fail", "migrate exit 0", str(exc)[:200]))
                return EXIT["fail"], _envelope("ops_upgrade", mode, profile, checks,
                                               EXIT["fail"], artifacts=artifacts,
                                               notes="Rehearsal migration failed; rollback only if structure-compatible.")
            checks.append(_check("upgrade.migrate", "pass" if code == 0 else "fail",
                                 "migrate exit 0", f"exit={code}"))
        else:
            checks.append(_check("upgrade.migrate", "not_run",
                                 "isolated migration explicitly requested", "skipped"))
        checks.append(_check("upgrade.smoke", "not_run",
                             "health/login/verify_integrity smoke", "not executed in drill"))
    else:
        artifacts = []
    failed = [c for c in checks if c["status"] == "fail"]
    not_run = [c for c in checks if c["status"] == "not_run"]
    code = EXIT["fail"] if failed else EXIT["not_run"] if not_run else EXIT["pass"]
    status = "fail" if failed else "not_run" if not_run else "pass"
    return code, _envelope("ops_upgrade", mode, profile, checks, code, status=status,
                           artifacts=artifacts,
                           notes="Local/rehearsal evidence only; production not executed.")


def _run_migrate(db):
    db = Path(db)
    with tempfile.TemporaryDirectory(prefix="ph347-migrate-") as directory:
        copy = Path(directory) / "rehearsal.sqlite3"
        shutil.copy2(db, copy)
        env = dict(__import__("os").environ)
        env["PMO_DB"] = str(copy)
        return subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"],
                              cwd=Path(__file__).resolve().parents[2],
                              env=env, capture_output=True, text=True, timeout=180).returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description="PH-347 upgrade/rollback drill")
    parser.add_argument("--mode", choices=["dry_run", "execute"], default="dry_run")
    parser.add_argument("--env", dest="profile", choices=sorted(PROFILES), default="rehearsal")
    parser.add_argument("--out", default="")
    parser.add_argument("--baseline", default="")
    parser.add_argument("--candidate", default="")
    parser.add_argument("--backup", default="")
    parser.add_argument("--db", default="")
    parser.add_argument("--run-migrate", action="store_true")
    parser.add_argument("--migrations-before", default="")
    parser.add_argument("--migrations-after", default="")
    parser.add_argument("--rollback-compatible", dest="rollback_compatible",
                        action="store_true", default=True)
    parser.add_argument("--no-rollback-compatible", dest="rollback_compatible",
                        action="store_false")
    parser.add_argument("--confirm-production", action="store_true")
    args = parser.parse_args(argv)
    parse = lambda raw: json.loads(raw) if raw else []
    code, evidence = upgrade_plan(
        args.profile, args.mode, baseline=args.baseline or None,
        candidate=args.candidate or None,
        migrations_before=parse(args.migrations_before),
        migrations_after=parse(args.migrations_after),
        backup=args.backup or None, rollback_compatible=args.rollback_compatible,
        confirm_production=args.confirm_production, db=args.db or None,
        run_migrate=args.run_migrate)
    append_evidence(args.out, evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
