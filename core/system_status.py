"""Q-43 只读系统状态/备份可见性聚合：不写业务事实，不读取或展示密钥。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.db import connection
from django.utils import timezone as django_timezone

MAX_BACKUPS = 50


def _display_time(timestamp: float) -> str:
    value = datetime.fromtimestamp(timestamp, tz=dt_timezone.utc)
    return django_timezone.localtime(value).strftime("%Y.%m.%d %H:%M")


def _valid_digest(value: object) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value.lower()))


def database_status() -> dict:
    engine = str(settings.DATABASES.get("default", {}).get("ENGINE", "")).rsplit(".", 1)[-1]
    reachable = False
    pending = None
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        reachable = True
        from django.db.migrations.loader import MigrationLoader
        loader = MigrationLoader(connection)
        pending = len(set(loader.disk_migrations) - set(loader.applied_migrations))
    except Exception:
        reachable = False
    return {"engine": engine or "unknown", "reachable": reachable,
            "pending_migrations": pending}


def _latest_backup(root: Path) -> Path | None:
    candidates = []
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.stat().st_mtime,
                         reverse=True)[:MAX_BACKUPS]
    except OSError:
        return None
    for entry in entries:
        if entry.is_dir() and not entry.is_symlink() and (entry / "manifest.json").is_file():
            candidates.append(entry)
    return candidates[0] if candidates else None


def _backup_summary(root: Path, directory) -> dict:
    try:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"found": True, "valid": False, "reason": "manifest",
                "when": _display_time(directory.stat().st_mtime)}
    artifacts = {}
    valid = isinstance(manifest, dict) and manifest.get("format_version") == 1
    if valid:
        for key, filename in (("database", "database.sqlite3"), ("uploads", "uploads.tar.gz")):
            artifact = manifest.get(key)
            if (not isinstance(artifact, dict) or artifact.get("filename") != filename
                    or not _valid_digest(artifact.get("sha256"))):
                valid = False
                break
            path = directory / filename
            if not path.is_file() or path.is_symlink():
                valid = False
                break
            artifacts[key] = path.stat().st_size
    if valid:
        evidence = manifest.get("evidence")
        valid = isinstance(evidence, list) and all(
            isinstance(item, dict) and isinstance(item.get("path"), str)
            and _valid_digest(item.get("sha256")) for item in evidence)
    return {"found": True, "valid": valid,
            "when": _display_time(directory.stat().st_mtime),
            "database_bytes": artifacts.get("database"),
            "uploads_bytes": artifacts.get("uploads"),
            "evidence": len(manifest.get("evidence") or []) if isinstance(manifest, dict) else None}


def backup_status() -> dict:
    root = Path(str(getattr(settings, "PMO_BACKUP_DIR",
                            Path(settings.BASE_DIR) / "runtime" / "backups"))).expanduser()
    summary = {"found": False, "valid": False, "directory_configured": bool(os.getenv("PMO_BACKUP_DIR")),
               "env_var": "PMO_BACKUP_DIR"}
    if not root.is_dir():
        summary["reason"] = "directory_missing"
        return summary
    latest = _latest_backup(root)
    if latest is None:
        summary["reason"] = "no_backup"
        return summary
    return _backup_summary(root, latest)


def evidence_summary(member) -> dict:
    from core.models import Evidence
    queryset = Evidence.objects.filter(document__project__organization=member.organization)
    total = queryset.count()
    missing = 0
    for item in queryset.only("file"):
        try:
            if not item.file.storage.exists(item.file.name):
                missing += 1
        except Exception:
            missing += 1
    return {"count": total, "missing": missing}
