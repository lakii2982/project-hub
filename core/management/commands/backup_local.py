from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
from pathlib import Path, PurePosixPath

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

DATABASE_NAME = "database.sqlite3"
UPLOADS_NAME = "uploads.tar.gz"
MANIFEST_NAME = "manifest.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise CommandError("Evidence 文件路径无效")
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise CommandError(f"Evidence 文件路径越界：{value}")
    return relative


def _snapshot_evidence(database: Path) -> dict[str, str]:
    try:
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as source:
            rows = source.execute(
                "SELECT file, sha256 FROM core_evidence ORDER BY file"
            ).fetchall()
    except sqlite3.Error as error:
        raise CommandError("无法读取数据库快照中的 core_evidence") from error

    evidence: dict[str, str] = {}
    for file_name, digest in rows:
        relative = _safe_relative(file_name).as_posix()
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest.lower())
        ):
            raise CommandError(f"Evidence SHA256 无效：{relative}")
        digest = digest.lower()
        if relative in evidence and evidence[relative] != digest:
            raise CommandError(f"同一 Evidence 路径存在不同 SHA256：{relative}")
        evidence[relative] = digest
    return evidence


def _resolve_media_file(media_root: Path, relative: str) -> Path:
    resolved = (media_root / Path(*PurePosixPath(relative).parts)).resolve(strict=True)
    if not resolved.is_relative_to(media_root) or not resolved.is_file():
        raise CommandError(f"Evidence 文件越界或不是普通文件：{relative}")
    return resolved


class _HashingReader:
    def __init__(self, source):
        self.source = source
        self.digest = hashlib.sha256()

    def read(self, size: int = -1) -> bytes:
        chunk = self.source.read(size)
        self.digest.update(chunk)
        return chunk


class Command(BaseCommand):
    help = "创建本地 SQLite 数据库、Evidence 附件及 SHA256 清单的一致性备份"

    def add_arguments(self, parser):
        parser.add_argument("--output", required=True, help="尚不存在的新备份目录")

    def handle(self, *args, **options):
        output = Path(options["output"]).expanduser().absolute()
        if os.path.lexists(output):
            raise CommandError("备份输出目录已存在，拒绝覆盖")
        if getattr(settings, "PRODUCTION", False):
            raise CommandError("本地备份命令不允许在 production 环境运行")

        database_config = settings.DATABASES["default"]
        engine = database_config.get("ENGINE", "")
        if not engine.endswith("sqlite3"):
            raise CommandError("本地备份命令仅支持 SQLite")
        if not output.parent.is_dir():
            raise CommandError("备份输出目录的父目录不存在")

        created = False
        try:
            output.mkdir(mode=0o700)
            created = True
            os.chmod(output, 0o700)

            database_path = output / DATABASE_NAME
            connection.ensure_connection()
            sqlite_source = connection.connection
            if not isinstance(sqlite_source, sqlite3.Connection):
                raise CommandError("当前数据库连接不是 SQLite")
            with sqlite3.connect(database_path) as destination:
                sqlite_source.backup(destination)
            os.chmod(database_path, 0o600)

            with sqlite3.connect(
                f"{database_path.as_uri()}?mode=ro", uri=True
            ) as source:
                if source.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise CommandError("SQLite 快照完整性检查失败")

            evidence = _snapshot_evidence(database_path)
            media_root = Path(settings.MEDIA_ROOT).resolve()
            uploads_path = output / UPLOADS_NAME
            with tarfile.open(uploads_path, "w:gz") as archive:
                for relative, expected_digest in evidence.items():
                    source_path = _resolve_media_file(media_root, relative)
                    with source_path.open("rb") as source_file:
                        stat = os.fstat(source_file.fileno())
                        info = tarfile.TarInfo(relative)
                        info.size = stat.st_size
                        info.mode = 0o600
                        info.mtime = int(stat.st_mtime)
                        reader = _HashingReader(source_file)
                        archive.addfile(info, reader)
                    if reader.digest.hexdigest() != expected_digest:
                        raise CommandError(f"Evidence SHA256 不匹配：{relative}")
            os.chmod(uploads_path, 0o600)

            manifest = {
                "format_version": 1,
                "database": {
                    "filename": DATABASE_NAME,
                    "sha256": _sha256(database_path),
                },
                "uploads": {
                    "filename": UPLOADS_NAME,
                    "sha256": _sha256(uploads_path),
                },
                "evidence": [
                    {"path": path, "sha256": digest}
                    for path, digest in sorted(evidence.items())
                ],
            }
            manifest_path = output / MANIFEST_NAME
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.chmod(manifest_path, 0o600)
        except Exception as error:
            if created:
                shutil.rmtree(output, ignore_errors=True)
            if isinstance(error, CommandError):
                raise
            raise CommandError(f"本地备份失败：{error}") from error

        self.stdout.write(self.style.SUCCESS("本地一致性备份完成"))
