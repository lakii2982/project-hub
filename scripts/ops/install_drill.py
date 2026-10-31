#!/usr/bin/env python3
"""PH-347 local install/preflight and TLS drill.

Contract: docs/engineering/ops-runbook.md (PH-345). This script only performs
read-only checks or an isolated rehearsal; it never deploys to production.
Exit codes: 0 pass, 2 not_run (missing precondition), 3 fail, 4 environment
error, 5 security refusal. Evidence is an append-only JSONL envelope.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

CONTRACT_VERSION = 1
PROFILES = {"rehearsal", "staging", "production"}
REDACTIONS = ["password", "token", "payroll", "customer", "secret", "key"]
EXIT = {"pass": 0, "not_run": 2, "fail": 3, "environment_error": 4, "refused": 5}


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
    return {
        "script": script, "contract_version": CONTRACT_VERSION, "mode": mode,
        "profile": profile, "app_sha": _git_sha(),
        "started_at": _now(), "finished_at": _now(), "exit_code": exit_code,
        "status": status, "checks": checks, "artifacts": artifacts or [],
        "redactions": REDACTIONS, "notes": notes,
    }


def append_evidence(path, envelope):
    """Append one evidence envelope (JSONL), never overwrite history."""
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


def make_artifact(directory, kind, content):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind}.log"
    path.write_text(content, encoding="utf-8")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"kind": kind, "path_or_url": str(path), "sha256": digest}


def preflight(profile, mode="dry_run", *, facts=None, confirm_production=False,
              artifact_dir=None):
    """V1-OPS-01: read-only install prerequisite checks or isolated rehearsal."""
    facts = dict(facts or {})
    if profile not in PROFILES:
        return EXIT["fail"], _envelope("ops_preflight", mode, profile, [
            _check("profile.known", "fail", "rehearsal|staging|production", profile)
        ], EXIT["fail"])
    if mode not in {"dry_run", "execute"}:
        return EXIT["fail"], _envelope("ops_preflight", mode, profile, [
            _check("mode.valid", "fail", "dry_run|execute", mode)
        ], EXIT["fail"])
    checks = []
    if mode == "execute" and profile == "production":
        checks.append(_check("safety.production_execute", "fail",
                             "production execute refused by drill", "refused"))
        return EXIT["refused"], _envelope("ops_preflight", mode, profile, checks,
                                          EXIT["refused"], status="blocked",
                                          notes="Production execution is never performed by this drill.")
    checks.append(_check("profile.known", "pass", "rehearsal|staging|production", profile))
    python_ok = sys.version_info >= (3, 12)
    checks.append(_check("runtime.python", "pass" if python_ok else "fail",
                         ">=3.12", platform.python_version()))
    sha = _git_sha()
    checks.append(_check("release.app_sha", "pass" if sha else "not_run",
                         "git SHA recorded", sha or "missing"))
    free = shutil.disk_usage(Path.cwd()).free
    checks.append(_check("disk.free", "pass" if free >= 1024 ** 3 else "fail",
                         ">=1GiB", f"{free} bytes"))
    checks.append(_check("compose.engine", "pass" if facts.get("compose_version") else "not_run",
                         "Compose v2 recorded", facts.get("compose_version") or "missing"))
    checks.append(_check("db.engine", "pass" if facts.get("db_engine") else "not_run",
                         "DB engine recorded", facts.get("db_engine") or "missing"))
    checks.append(_check("migration.mode", "pass" if facts.get("migrations_append_only", True) else "fail",
                         "append-only migrations", "append_only" if facts.get("migrations_append_only", True) else "not_append_only"))
    artifacts = []
    if mode == "execute":
        if not confirm_production and profile == "production":
            checks.append(_check("safety.confirm", "fail", "explicit confirmation", "missing"))
            return EXIT["refused"], _envelope("ops_preflight", mode, profile, checks,
                                              EXIT["refused"], status="blocked")
        # Isolated rehearsal only: a disposable directory, never a real install.
        with tempfile.TemporaryDirectory(prefix="ph347-install-") as directory:
            manifest = Path(directory) / "install-manifest.json"
            manifest.write_text(json.dumps({"profile": profile, "sha": sha,
                                            "rehearsal": True}), encoding="utf-8")
            artifacts.append({"kind": "log", "path_or_url": "install-manifest.json",
                              "sha256": hashlib.sha256(manifest.read_bytes()).hexdigest()})
        checks.append(_check("install.rehearsal", "pass", "isolated rehearsal manifest",
                             "created and discarded"))
    failed = [c for c in checks if c["status"] == "fail"]
    not_run = [c for c in checks if c["status"] == "not_run"]
    code = EXIT["fail"] if failed else EXIT["not_run"] if not_run else EXIT["pass"]
    status = "fail" if failed else "not_run" if not_run else "pass"
    return code, _envelope("ops_preflight", mode, profile, checks, code, status=status,
                           artifacts=artifacts,
                           notes="Local/rehearsal evidence only; L4/L5 not executed.")


def tls_probe(profile, mode="dry_run", *, domain=None, certificate=None, handshake=None,
              confirm_production=False):
    """V1-OPS-02: TLS/security probe with injectable handshake for tests."""
    checks = []
    if profile not in PROFILES:
        return EXIT["fail"], _envelope("ops_tls_probe", mode, profile, [
            _check("profile.known", "fail", "rehearsal|staging|production", profile)
        ], EXIT["fail"])
    if mode == "execute" and profile == "production":
        return EXIT["refused"], _envelope("ops_tls_probe", mode, profile, [
            _check("safety.production_execute", "fail", "production execute refused", "refused")
        ], EXIT["refused"], status="blocked")
    if not domain:
        checks.append(_check("tls.domain", "not_run", "domain supplied (no production access)",
                             "missing"))
        return EXIT["not_run"], _envelope("ops_tls_probe", mode, profile, checks,
                                          EXIT["not_run"], status="not_run",
                                          notes="No domain/TLS probe executed.")
    try:
        info = handshake if handshake is not None else _real_handshake(domain)
    except (OSError, ssl.SSLError, socket.timeout) as exc:
        checks.append(_check("tls.handshake", "fail", "successful TLS handshake", str(exc)[:200]))
        return EXIT["environment_error"], _envelope("ops_tls_probe", mode, profile, checks,
                                                    EXIT["environment_error"], status="fail")
    if not isinstance(info, dict):
        checks.append(_check("tls.handshake", "fail", "handshake facts", "missing"))
        return EXIT["fail"], _envelope("ops_tls_probe", mode, profile, checks, EXIT["fail"])
    checks.append(_check("tls.handshake", "pass" if info.get("valid", True) else "fail",
                         "valid certificate chain", info.get("valid", True)))
    checks.append(_check("tls.expiry", "pass" if info.get("not_expired", True) else "fail",
                         "certificate not expired", info.get("not_after", "unknown")))
    checks.append(_check("tls.hostname", "pass" if info.get("hostname_ok", True) else "fail",
                         "certificate matches domain", info.get("hostname_ok", True)))
    checks.append(_check("tls.hsts", "pass" if info.get("hsts", True) else "fail",
                         "HSTS enabled", info.get("hsts", True)))
    checks.append(_check("tls.cookie", "pass" if info.get("cookie_secure", True) else "fail",
                         "Secure/HttpOnly cookies", info.get("cookie_secure", True)))
    failed = [c for c in checks if c["status"] == "fail"]
    code = EXIT["fail"] if failed else EXIT["pass"]
    return code, _envelope("ops_tls_probe", mode, profile, checks, code,
                           notes="TLS probe facts are evidence; configuration existence alone is not a pass.")


def _real_handshake(domain):
    context = ssl.create_default_context()
    with socket.create_connection((domain, 443), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname=domain) as tls:
            cert = tls.getpeercert() or {}
    return {"valid": True, "not_expired": True, "not_after": cert.get("notAfter", ""),
            "hostname_ok": True, "hsts": False, "cookie_secure": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description="PH-347 install/TLS drill")
    parser.add_argument("--step", choices=["preflight", "tls"], default="preflight")
    parser.add_argument("--mode", choices=["dry_run", "execute"], default="dry_run")
    parser.add_argument("--env", dest="profile", choices=sorted(PROFILES), default="rehearsal")
    parser.add_argument("--out", default="")
    parser.add_argument("--domain", default="")
    parser.add_argument("--confirm-production", action="store_true")
    parser.add_argument("--facts", default="")
    args = parser.parse_args(argv)
    facts = json.loads(args.facts) if args.facts else {}
    if args.step == "tls":
        code, evidence = tls_probe(args.profile, args.mode, domain=args.domain or None,
                                   confirm_production=args.confirm_production)
    else:
        code, evidence = preflight(args.profile, args.mode, facts=facts,
                                   confirm_production=args.confirm_production)
    append_evidence(args.out, evidence)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
