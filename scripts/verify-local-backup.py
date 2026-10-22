from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

DATABASE_NAME = "database.sqlite3"
UPLOADS_NAME = "uploads.tar.gz"
MANIFEST_NAME = "manifest.json"


class VerificationError(Exception):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise VerificationError("relative path is invalid")
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise VerificationError("relative path escapes its root")
    return relative


def _valid_digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value.lower())
    ):
        raise VerificationError("invalid SHA256")
    return value.lower()


def _regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _load_manifest(backup: Path) -> dict:
    manifest_path = backup / MANIFEST_NAME
    if not _regular_file(manifest_path):
        raise VerificationError("manifest is missing")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError("manifest is unreadable") from error
    if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
        raise VerificationError("unsupported manifest version")
    for key, filename in (
        ("database", DATABASE_NAME),
        ("uploads", UPLOADS_NAME),
    ):
        artifact = manifest.get(key)
        if not isinstance(artifact, dict) or artifact.get("filename") != filename:
            raise VerificationError(f"invalid {key} manifest")
        _valid_digest(artifact.get("sha256"))
    if not isinstance(manifest.get("evidence"), list):
        raise VerificationError("invalid evidence manifest")
    return manifest


def _manifest_evidence(manifest: dict) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for item in manifest["evidence"]:
        if not isinstance(item, dict):
            raise VerificationError("invalid evidence entry")
        path = _safe_relative(item.get("path")).as_posix()
        digest = _valid_digest(item.get("sha256"))
        if path in evidence:
            raise VerificationError("duplicate evidence path")
        evidence[path] = digest
    return evidence


def _snapshot_evidence(database: Path) -> dict[str, str]:
    try:
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as source:
            rows = source.execute(
                "SELECT file, sha256 FROM core_evidence ORDER BY file"
            ).fetchall()
    except sqlite3.Error as error:
        raise VerificationError("snapshot evidence query failed") from error
    evidence: dict[str, str] = {}
    for file_name, digest in rows:
        path = _safe_relative(file_name).as_posix()
        digest = _valid_digest(digest)
        if path in evidence and evidence[path] != digest:
            raise VerificationError("conflicting snapshot evidence")
        evidence[path] = digest
    return evidence


def _safe_extract(archive_path: Path, destination: Path) -> set[str]:
    names: set[str] = set()
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                relative = _safe_relative(member.name).as_posix()
                if not member.isfile() or relative in names:
                    raise VerificationError("archive contains unsafe member")
                names.add(relative)
            archive.extractall(destination, filter="data")
    except (OSError, tarfile.TarError) as error:
        raise VerificationError("archive is unreadable") from error
    return names


def _verify_uploads(
    tar_path: Path, destination: Path, expected: dict[str, str]
) -> None:
    destination.mkdir(mode=0o700)
    names = _safe_extract(tar_path, destination)
    if names != set(expected):
        raise VerificationError("archive manifest mismatch")
    for relative, digest in expected.items():
        path = destination.joinpath(*PurePosixPath(relative).parts)
        if not _regular_file(path) or _sha256(path) != digest:
            raise VerificationError(f"attachment mismatch: {relative}")


def _run_integrity(database_copy: Path) -> tuple[bool, int | None]:
    project = Path(__file__).resolve().parent.parent
    python = project / ".venv" / "bin" / "python"
    command = [str(python), str(project / "manage.py"), "verify_integrity", "--json"]
    environment = os.environ.copy()
    for key in (
        "POSTGRES_HOST",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_DB",
        "POSTGRES_PORT",
    ):
        environment.pop(key, None)
    environment["PMO_DB"] = str(database_copy)
    environment["PMO_ENV"] = "development"
    if not python.is_file():
        return False, None
    try:
        result = subprocess.run(
            command,
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False, None
    payload = None
    for line in reversed(result.stdout.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            payload = candidate
            break
    if payload is None:
        return False, None
    entries = payload.get("entries")
    if not isinstance(entries, int) or isinstance(entries, bool):
        entries = None
    ok = result.returncode == 0 and payload.get("ok") is True
    return ok, entries


def main(argv: list[str]) -> int:
    entries: int | None = None
    attachments = 0
    ok = False
    try:
        if len(argv) != 2:
            raise VerificationError("usage")
        backup = Path(argv[1]).expanduser().resolve(strict=True)
        if not backup.is_dir():
            raise VerificationError("backup directory is invalid")
        manifest = _load_manifest(backup)
        manifest_hash = _manifest_evidence(manifest)
        database_path = backup / DATABASE_NAME
        uploads_path = backup / UPLOADS_NAME
        if not _regular_file(database_path) or not _regular_file(uploads_path):
            raise VerificationError("artifact is missing")
        if _sha256(database_path) != _valid_digest(
            manifest["database"]["sha256"]
        ):
            raise VerificationError("database SHA256 mismatch")
        if _sha256(uploads_path) != _valid_digest(manifest["uploads"]["sha256"]):
            raise VerificationError("uploads SHA256 mismatch")

        with tempfile.TemporaryDirectory(prefix="pmo-local-backup-") as temporary:
            temporary_root = Path(temporary)
            database_copy = temporary_root / DATABASE_NAME
            shutil.copyfile(database_path, database_copy)
            os.chmod(database_copy, 0o600)
            snapshot_evidence = _snapshot_evidence(database_copy)
            if manifest_hash != snapshot_evidence:
                raise VerificationError("manifest snapshot mismatch")
            attachments = len(snapshot_evidence)
            _verify_uploads(
                uploads_path, temporary_root / "uploads", snapshot_evidence
            )
            ok, entries = _run_integrity(database_copy)
    except Exception:
        ok = False

    print(
        json.dumps(
            {"ok": ok, "entries": entries, "attachments": attachments},
            ensure_ascii=False,
        )
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
