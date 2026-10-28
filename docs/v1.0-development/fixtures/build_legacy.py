#!/usr/bin/env python3
"""Preparation fixture: generate synthetic SQLite from frozen source, never a live DB.

Run with an existing Python environment containing the frozen dependencies.
No network, dependency install, current-source import or seed_demo is used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import uuid

ARCHIVE_SHA256 = "4ae5b5648c7716006830f6a21b73af3ac12eef4a0bd592c0d0b2dee1f30e9ef5"
BASELINE_SHA = "119a04916e3e96a5e4d86516a5f13e841faf2f67"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--output-dir", type=Path,
                        default=Path(tempfile.gettempdir()) / ("ph134-legacy-" + uuid.uuid4().hex),
                        help="A NEW directory inside your chosen runtime/temp location")
    args = parser.parse_args()
    repo = args.repo.resolve(strict=True)
    output = args.output_dir.resolve()
    if output == repo or repo in output.parents:
        raise SystemExit("Refusing output inside source repository; choose external temp directory")
    actual = subprocess.check_output(
        ["git", "--no-replace-objects", "-c", "tar.umask=0002", "-C", str(repo), "rev-parse", BASELINE_SHA + "^{commit}"], text=True).strip()
    if actual != BASELINE_SHA:
        raise SystemExit("Frozen commit identity mismatch")
    # Exclusive mkdir prevents replacing an existing fixture or default runtime DB.
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    database = output / "legacy.sqlite3"
    log = []
    # Do not inherit database selectors, credentials, Django settings or Python paths.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR") if key in os.environ}
    env.update(PMO_DB=str(database), PMO_ENV="development", PMO_AI_ENABLED="0",
               DJANGO_SETTINGS_MODULE="pmo.settings", PYTHONDONTWRITEBYTECODE="1")
    with tempfile.TemporaryDirectory(prefix="ph-frozen-source-") as folder:
        source = Path(folder)
        archive = source / "baseline.tar"
        with archive.open("wb") as stream:
            subprocess.run(["git", "--no-replace-objects", "-c", "tar.umask=0002", "-C", str(repo), "archive", "--format=tar", BASELINE_SHA],
                           stdout=stream, check=True)
        archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        if archive_hash != ARCHIVE_SHA256:
            raise SystemExit("Frozen archive hash mismatch; do not seed unknown source")
        with tarfile.open(archive) as stream:
            stream.extractall(source, filter="data")
        archive.unlink()

        def run(command):
            result = subprocess.run(command, cwd=source, env=env, text=True, capture_output=True)
            log.append({"command": command, "cwd": str(source), "PMO_DB": str(database),
                        "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
            (output / "commands.json").write_text(json.dumps(log, indent=2, ensure_ascii=False) + "\n")
            if result.returncode:
                raise SystemExit("Command failed; inspect " + str(output / "commands.json"))
            return result.stdout

        run([sys.executable, "manage.py", "migrate", "--noinput"])
        # The management shell owns sys.path; imports therefore come only from archive.
        seed = Path(__file__).with_name("seed_legacy.py").read_text()
        script = source / "_synthetic_seed.py"
        script.write_text(seed)
        run([sys.executable, "manage.py", "shell", "-c",
             "exec(open('_synthetic_seed.py').read())"])
        integrity = json.loads(run([sys.executable, "manage.py", "verify_integrity", "--json"]))
        business = json.loads((output / "business.json").read_text())
        summary = {"schema_version": 1, "classification": "preparation_fixture_synthetic_only",
                   "baseline_sha": BASELINE_SHA, "archive_sha256": archive_hash,
                   "python": sys.version, "database": str(database),
                   "database_sha256": hashlib.sha256(database.read_bytes()).hexdigest(),
                   "business_sha256": hashlib.sha256((output / "business.json").read_bytes()).hexdigest(),
                   "counts": business["counts"], "total_cost_cents": business["total_cost_cents"],
                   "integrity": integrity,
                   "scope": "Frozen per-row costing only; no new monthly rule or upgrade tested",
                   "reproducibility": "business.json and audit hashes are deterministic; SQLite file hash is run-specific (migration timestamps)."}
        (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    database.chmod(0o600)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
