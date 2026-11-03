"""Q-43 local backup restore command tests (isolated target, no overwrite, safe extraction)."""
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import stat
import tarfile
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def sha256_bytes(value):
    return hashlib.sha256(value).hexdigest()


def make_backup(root, files, *, tamper_database=False, tamper_uploads=False,
                unsafe_member=False):
    backup = Path(root) / "backup"
    backup.mkdir(parents=True)
    database = backup / "database.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE core_evidence (file TEXT PRIMARY KEY, sha256 TEXT)")
        connection.executemany("INSERT INTO core_evidence VALUES (?, ?)",
                               [(path, sha256_bytes(content)) for path, content in files.items()])
        connection.commit()
    uploads = backup / "uploads.tar.gz"
    with tarfile.open(uploads, "w:gz") as archive:
        for path, content in files.items():
            info = tarfile.TarInfo(path)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        if unsafe_member:
            info = tarfile.TarInfo("../evil.txt")
            info.size = 3
            archive.addfile(info, io.BytesIO(b"bad"))
    manifest = {
        "format_version": 1,
        "database": {"filename": "database.sqlite3", "sha256": sha256_bytes(database.read_bytes())},
        "uploads": {"filename": "uploads.tar.gz", "sha256": sha256_bytes(uploads.read_bytes())},
        "evidence": [{"path": path, "sha256": sha256_bytes(content)}
                     for path, content in sorted(files.items())],
    }
    (backup / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if tamper_database:
        database.write_bytes(database.read_bytes() + b"tampered")
    if tamper_uploads:
        uploads.write_bytes(uploads.read_bytes() + b"tampered")
    return backup


class RestoreLocalCommandTests(SimpleTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="q43-restore-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {"q43/凭证.txt": "凭证正文".encode("utf-8"),
                      "q43/nested/other.txt": b"other"}
        self.backup = make_backup(self.root, self.files)

    def restore(self, target, **options):
        stdout = io.StringIO()
        call_command("restore_local", "--backup", str(self.backup), "--target", str(target),
                      stdout=stdout, **options)
        return stdout.getvalue()

    def test_restores_database_and_attachments_without_touching_source(self):
        backup_digests = {path.name: sha256_bytes(path.read_bytes())
                          for path in self.backup.iterdir()}
        target = self.root / "restored"
        output = self.restore(target, json=True)
        payload = json.loads(output.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["evidence"], 2)
        self.assertEqual(payload["database"], str(target / "database.sqlite3"))
        self.assertEqual((target / "database.sqlite3").stat().st_mode & 0o777, 0o600)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
        for path, content in self.files.items():
            restored = target / "uploads" / path
            self.assertEqual(restored.read_bytes(), content)
        self.assertEqual({path.name: sha256_bytes(path.read_bytes())
                          for path in self.backup.iterdir()}, backup_digests)
        with sqlite3.connect(target / "database.sqlite3") as connection:
            self.assertEqual(connection.execute("PRAGMA quick_check").fetchone()[0], "ok")

    def test_existing_target_is_refused(self):
        target = self.root / "existing"
        target.mkdir()
        marker = target / "keep.txt"
        marker.write_text("keep", encoding="utf-8")
        with self.assertRaises(CommandError):
            self.restore(target)
        self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_tampered_database_is_refused_and_no_partial_target(self):
        backup = make_backup(self.root / "tampered-db", self.files, tamper_database=True)
        self.backup = backup
        target = self.root / "not-created"
        with self.assertRaises(CommandError):
            self.restore(target)
        self.assertFalse(target.exists())

    def test_tampered_uploads_are_refused(self):
        self.backup = make_backup(self.root / "tampered-uploads", self.files, tamper_uploads=True)
        target = self.root / "not-created"
        with self.assertRaises(CommandError):
            self.restore(target)
        self.assertFalse(target.exists())

    def test_unsafe_tar_member_is_refused(self):
        self.backup = make_backup(self.root / "unsafe", self.files, unsafe_member=True)
        target = self.root / "not-created"
        with self.assertRaises(CommandError):
            self.restore(target)
        self.assertFalse(target.exists())
        self.assertFalse((self.root / "evil.txt").exists())

    def test_verification_python_falls_back_to_caller_interpreter(self):
        spec = importlib.util.spec_from_file_location(
            "q43_verify_backup", PROJECT_ROOT / "scripts" / "verify-local-backup.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix="q43-no-venv-") as directory:
            with unittest.mock.patch.dict(os.environ, {"PMO_PYTHON": sys.executable}):
                self.assertEqual(module._verification_python(Path(directory)),
                                 Path(sys.executable))
            with unittest.mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("PMO_PYTHON", None)
                self.assertEqual(module._verification_python(Path(directory)),
                                 Path(sys.executable))

    def test_production_is_refused(self):
        with override_settings(PRODUCTION=True):
            with self.assertRaises(CommandError):
                self.restore(self.root / "not-created")
        self.assertFalse((self.root / "not-created").exists())

    def test_manifest_evidence_must_match_database_and_archive(self):
        manifest_path = self.backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["evidence"].append({"path": "q43/missing.txt", "sha256": "0" * 64})
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(CommandError):
            self.restore(self.root / "not-created")
        self.assertFalse((self.root / "not-created").exists())


class RestoreLocalIntegrationTests(SimpleTestCase):
    """backup_local (real demo DB + attachment) -> restore_local -> integrity ok."""

    def test_real_backup_restores_identical_amounts_and_attachment(self):
        import subprocess
        import sys

        project = Path(__file__).resolve().parents[1]
        temp = tempfile.TemporaryDirectory(prefix="q43-e2e-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        db = root / "source" / "demo.sqlite3"
        media = root / "source" / "uploads"
        db.parent.mkdir()
        media.mkdir()
        env = {key: value for key, value in os.environ.items()
               if not key.startswith("POSTGRES_")}
        env.update({"PMO_DB": str(db), "PMO_MEDIA_ROOT": str(media), "PMO_ENV": "development",
                    "PMO_SCOPE": "micro", "DJANGO_DEBUG": "1",
                    "PMO_DEMO_RUNTIME_DIR": str(root / "runtime")})

        def run(*args, **kwargs):
            return subprocess.run([sys.executable, str(project / "manage.py"), *args],
                                  cwd=project, env=env, capture_output=True, text=True,
                                  timeout=300, **kwargs)

        migrated = run("migrate", "--noinput")
        self.assertEqual(migrated.returncode, 0, migrated.stderr[-800:])
        seeded = run("seed_demo")
        self.assertEqual(seeded.returncode, 0, seeded.stderr[-800:])
        attachment = "q43/attachment.txt"
        (media / "q43").mkdir()
        (media / attachment).write_bytes(b"Q-43 attachment")
        shell = (
            "from core.models import Document, Evidence\n"
            "import hashlib, os\n"
            "doc = Document.objects.filter(kind='expense').first()\n"
            f"content = open(os.path.join(os.environ['PMO_MEDIA_ROOT'], {attachment!r}), 'rb').read()\n"
            "Evidence.objects.create(document=doc, file=%r, original_name='q43.txt', "
            "sha256=hashlib.sha256(content).hexdigest(), uploaded_by=doc.submitted_by)\n"
            % attachment)
        created = run("shell", "-c", shell)
        self.assertEqual(created.returncode, 0, created.stderr[-800:])
        backup = root / "backup"
        backed = run("backup_local", "--output", str(backup))
        self.assertEqual(backed.returncode, 0, backed.stderr[-800:])
        target = root / "restored"
        restored = run("restore_local", "--backup", str(backup), "--target", str(target),
                       "--json")
        self.assertEqual(restored.returncode, 0, restored.stderr[-800:])
        payload = json.loads(restored.stdout.strip().splitlines()[-1])
        self.assertTrue(payload["ok"])
        self.assertEqual((target / "uploads" / attachment).read_bytes(), b"Q-43 attachment")
        restored_env = dict(env)
        restored_env.update({"PMO_DB": str(target / "database.sqlite3"),
                             "PMO_MEDIA_ROOT": str(target / "uploads")})
        verified = subprocess.run(
            [sys.executable, str(project / "manage.py"), "verify_integrity", "--json"],
            cwd=project, env=restored_env, capture_output=True, text=True, timeout=300)
        self.assertEqual(verified.returncode, 0, verified.stdout[-800:] + verified.stderr[-800:])
        self.assertTrue(json.loads(verified.stdout.strip().splitlines()[-1])["ok"])
        # verify-local-backup.py must not hard-depend on .venv; force the fallback path.
        verify_env = dict(env)
        verify_env["PMO_PYTHON"] = sys.executable
        verify_env.pop("PMO_DB", None)
        checked = subprocess.run(
            [sys.executable, str(project / "scripts" / "verify-local-backup.py"), str(backup)],
            cwd=project, env=verify_env, capture_output=True, text=True, timeout=300)
        self.assertEqual(checked.returncode, 0, checked.stdout[-800:] + checked.stderr[-800:])
        self.assertTrue(json.loads(checked.stdout.strip().splitlines()[-1])["ok"])
        with sqlite3.connect(db) as source, sqlite3.connect(target / "database.sqlite3") as copy:
            for sql in ("SELECT COUNT(*) FROM core_project",
                        "SELECT COUNT(*) FROM core_ledgerentry",
                        "SELECT COUNT(*) FROM billing_invoice",
                        "SELECT COALESCE(SUM(amount_cents), 0) FROM billing_receipt"):
                self.assertEqual(source.execute(sql).fetchone(), copy.execute(sql).fetchone(), sql)
            self.assertEqual(source.execute("SELECT COUNT(*) FROM core_evidence").fetchone(),
                             copy.execute("SELECT COUNT(*) FROM core_evidence").fetchone())


if __name__ == "__main__":
    unittest.main()
