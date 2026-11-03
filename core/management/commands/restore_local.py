"""Q-43: 把 backup_local 产出的本地备份恢复到全新隔离目录（只读源备份）。

校验 manifest、数据库/附件 SHA256 与 SQLite 完整性后，复制数据库并安全解包附件；
目标目录必须尚不存在，绝不覆盖活动库/生产库。恢复后打印只读核对命令。
"""
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
        raise CommandError("备份中的附件路径无效")
    relative = PurePosixPath(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise CommandError(f"备份中的附件路径越界：{value}")
    return relative


def _valid_digest(value: object) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value.lower())):
        raise CommandError("备份 SHA256 格式无效")
    return value.lower()


def _regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _load_manifest(backup: Path) -> dict:
    path = backup / MANIFEST_NAME
    if not _regular_file(path):
        raise CommandError("备份缺少 manifest.json")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise CommandError("备份 manifest.json 无法读取") from error
    if not isinstance(manifest, dict) or manifest.get("format_version") != 1:
        raise CommandError("不支持的备份格式版本")
    for key, filename in (("database", DATABASE_NAME), ("uploads", UPLOADS_NAME)):
        artifact = manifest.get(key)
        if not isinstance(artifact, dict) or artifact.get("filename") != filename:
            raise CommandError(f"备份 manifest 的 {key} 记录无效")
        _valid_digest(artifact.get("sha256"))
    if not isinstance(manifest.get("evidence"), list):
        raise CommandError("备份 manifest 的 evidence 记录无效")
    return manifest


def _manifest_evidence(manifest: dict) -> dict[str, str]:
    evidence: dict[str, str] = {}
    for item in manifest["evidence"]:
        if not isinstance(item, dict):
            raise CommandError("备份 manifest 的 evidence 条目无效")
        path = _safe_relative(item.get("path")).as_posix()
        digest = _valid_digest(item.get("sha256"))
        if path in evidence:
            raise CommandError("备份 manifest 存在重复附件路径")
        evidence[path] = digest
    return evidence


def _database_evidence(database: Path) -> dict[str, str]:
    try:
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as source:
            rows = source.execute("SELECT file, sha256 FROM core_evidence ORDER BY file").fetchall()
    except sqlite3.Error as error:
        raise CommandError("无法读取备份数据库中的 core_evidence") from error
    evidence: dict[str, str] = {}
    for file_name, digest in rows:
        path = _safe_relative(file_name).as_posix()
        evidence[path] = _valid_digest(digest)
    return evidence


def _extract_uploads(archive_path: Path, destination: Path) -> set[str]:
    names: set[str] = set()
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive.getmembers():
                relative = _safe_relative(member.name).as_posix()
                if not member.isfile() or relative in names:
                    raise CommandError("备份附件包含不安全或重复的归档成员")
                names.add(relative)
            archive.extractall(destination, filter="data")
    except (OSError, tarfile.TarError) as error:
        raise CommandError("备份附件归档无法解包") from error
    return names


class Command(BaseCommand):
    help = "把 backup_local 的数据库与附件备份恢复到全新隔离目录（不覆盖任何已有目录）"

    def add_arguments(self, parser):
        parser.add_argument("--backup", required=True, help="backup_local 生成的备份目录")
        parser.add_argument("--target", required=True, help="尚不存在的隔离恢复目录")
        parser.add_argument("--json", action="store_true", help="输出单行 JSON 结果")

    def handle(self, *args, **options):
        if getattr(settings, "PRODUCTION", False):
            raise CommandError("本地恢复命令不允许在 production 环境运行")
        backup = Path(options["backup"]).expanduser().absolute()
        target = Path(options["target"]).expanduser().absolute()
        if not backup.is_dir():
            raise CommandError("备份目录不存在")
        if os.path.lexists(target):
            raise CommandError("恢复目标目录已存在，拒绝覆盖")
        if not target.parent.is_dir():
            raise CommandError("恢复目标目录的父目录不存在")
        manifest = _load_manifest(backup)
        database_source = backup / DATABASE_NAME
        uploads_source = backup / UPLOADS_NAME
        if not _regular_file(database_source) or not _regular_file(uploads_source):
            raise CommandError("备份缺少 database.sqlite3 或 uploads.tar.gz")
        if _sha256(database_source) != _valid_digest(manifest["database"]["sha256"]):
            raise CommandError("备份数据库 SHA256 不匹配，拒绝恢复")
        if _sha256(uploads_source) != _valid_digest(manifest["uploads"]["sha256"]):
            raise CommandError("备份附件 SHA256 不匹配，拒绝恢复")
        expected = _manifest_evidence(manifest)
        created = False
        try:
            target.mkdir(mode=0o700)
            created = True
            os.chmod(target, 0o700)
            database = target / DATABASE_NAME
            shutil.copyfile(database_source, database)
            os.chmod(database, 0o600)
            with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as source:
                if source.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise CommandError("恢复后的数据库完整性检查失败")
            if _database_evidence(database) != expected:
                raise CommandError("备份 manifest 与数据库内附件清单不一致")
            uploads = target / UPLOADS_NAME.replace(".tar.gz", "")
            uploads.mkdir(mode=0o700)
            names = _extract_uploads(uploads_source, uploads)
            if names != set(expected):
                raise CommandError("备份附件归档与 manifest 不一致")
            for relative, digest in expected.items():
                path = uploads.joinpath(*PurePosixPath(relative).parts)
                if not _regular_file(path) or _sha256(path) != digest:
                    raise CommandError(f"备份附件校验失败：{relative}")
        except Exception as error:
            if created:
                shutil.rmtree(target, ignore_errors=True)
            if isinstance(error, CommandError):
                raise
            raise CommandError(f"本地恢复失败：{error}") from error

        payload = {"ok": True, "target": str(target), "database": str(target / DATABASE_NAME),
                   "uploads": str(uploads), "evidence": len(expected)}
        if options["json"]:
            self.stdout.write(json.dumps(payload, ensure_ascii=False))
            return
        self.stdout.write(self.style.SUCCESS("本地备份已恢复到隔离目录（源备份未修改，目标目录不覆盖）"))
        self.stdout.write(f"数据库：{payload['database']}\n附件：{payload['uploads']}（{payload['evidence']} 个）")
        self.stdout.write("校验：manifest/数据库/附件 SHA256 与 SQLite 完整性通过")
        self.stdout.write("只读核对（不会写生产库）：")
        self.stdout.write(f"  PMO_DB={payload['database']} PMO_MEDIA_ROOT={payload['uploads']} "
                          ".venv/bin/python manage.py verify_integrity --json")
        self.stdout.write(f"  PMO_DB={payload['database']} PMO_MEDIA_ROOT={payload['uploads']} "
                          "PMO_ENV=development .venv/bin/python manage.py runserver 127.0.0.1:8770 --noreload")
