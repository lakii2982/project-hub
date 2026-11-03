"""Q-39: one-command demo environment regression tests.

The tests execute ``scripts/demo.py`` as a real subprocess against isolated
SQLite files. They cover the printed contract (URL/accounts/5 steps), empty
environment bootstrap, idempotent repeat runs, no-overwrite behavior, refusal
to seed a real-company database, real server start/reuse/stop and the
README/STATUS command consistency.
"""
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

from django.test import TestCase

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_SCRIPT = PROJECT_ROOT / "scripts" / "demo.py"
EXPECTED_COMMAND = "python scripts/demo.py --slice micro"


def _base_env():
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("POSTGRES_")}
    env.update({"PMO_ENV": "development", "PMO_SCOPE": "micro", "DJANGO_DEBUG": "1"})
    return env


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _health_ok(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health/", timeout=1) as resp:
            return resp.status == 200
    except OSError:
        return False


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _query(path, sql):
    with sqlite3.connect(path) as connection:
        return connection.execute(sql).fetchone()[0]


class DemoScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Migrate once; each test copies this empty-but-migrated database so the
        # subprocess tests exercise demo.py's own idempotent migrate call cheaply.
        cls.template_dir = tempfile.TemporaryDirectory(prefix="demo-template-")
        cls.empty_db = Path(cls.template_dir.name) / "empty.sqlite3"
        env = _base_env()
        env["PMO_DB"] = str(cls.empty_db)
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "manage.py"), "migrate", "--noinput"],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"template migrate failed: {result.stderr[-800:]}")

    @classmethod
    def tearDownClass(cls):
        cls.template_dir.cleanup()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="demo-script-")
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db = self.root / "runtime" / "demo.sqlite3"
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.runtime = self.root / "runtime"

    def prepare_migrated_db(self):
        shutil.copy2(self.empty_db, self.db)
        return self.db

    def run_demo(self, *extra, slice_name="micro", port=None, db=None, timeout=180,
                 env_overrides=None, runtime=None):
        args = [sys.executable, str(DEMO_SCRIPT), "--slice", slice_name,
                "--db", str(db or self.db), "--runtime-dir", str(runtime or self.runtime)]
        if port is not None:
            args += ["--port", str(port)]
        args += list(extra)
        env = _base_env()
        env.update(env_overrides or {})
        return subprocess.run(args, cwd=PROJECT_ROOT, env=env,
                              capture_output=True, text=True, timeout=timeout)

    def assert_contract_printed(self, result, url="http://127.0.0.1:8765/"):
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn(url, output)
        self.assertIn("演练账号", output)
        self.assertIn("owner", output)
        self.assertIn("演练密码：", output)
        steps = re.findall(r"^\s{2}[1-5]\. ", output, flags=re.MULTILINE)
        self.assertEqual(len(steps), 5, output)
        self.assertIn("停止服务器：python scripts/demo.py --stop", output)

    def assert_demo_data_chain(self, db_path):
        self.assertTrue(Path(db_path).is_file())
        with sqlite3.connect(db_path) as connection:
            def count(sql):
                return connection.execute(sql).fetchone()[0]
            self.assertEqual(count("SELECT COUNT(*) FROM core_organization WHERE is_demo=1"), 1)
            self.assertEqual(count("SELECT COUNT(*) FROM core_organization"), 1)
            self.assertGreater(count("SELECT COUNT(*) FROM core_project"), 0)
            self.assertGreater(count("SELECT COUNT(*) FROM core_timesheet"), 0)
            self.assertGreater(count("SELECT COUNT(*) FROM core_document WHERE kind='expense'"), 0)
            self.assertGreater(count("SELECT COUNT(*) FROM billing_invoice"), 0)
            self.assertGreater(count("SELECT COUNT(*) FROM billing_receipt"), 0)
            self.assertGreater(count("SELECT COUNT(*) FROM expenses_expensepayment"), 0)
            usernames = {row[0] for row in connection.execute("SELECT username FROM auth_user")}
            self.assertTrue({"owner", "finance", "pm", "member", "auditor", "newbie"} <= usernames)

    def counts(self, db_path):
        with sqlite3.connect(db_path) as connection:
            def count(table):
                return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            return {table: count(table) for table in (
                "core_organization", "core_project", "core_timesheet", "core_document",
                "billing_invoice", "billing_receipt", "expenses_expensepayment")}

    def test_empty_environment_prints_url_accounts_and_five_steps(self):
        result = self.run_demo("--no-serve")
        self.assert_contract_printed(result)
        self.assertIn("演练数据：已建立", result.stdout)
        self.assert_demo_data_chain(self.db)
        credentials = self.runtime / "demo-credentials.txt"
        self.assertTrue(credentials.is_file())
        self.assertEqual(stat.S_IMODE(credentials.stat().st_mode), 0o600)
        password = re.search(r"演练密码：(\S+)",
                             credentials.read_text(encoding="utf-8")).group(1)
        self.assertNotIn(password, result.stdout + result.stderr,
                         "demo password must not be logged in clear text")
        self.assertIsNone(re.search(r"\b(138\d{8}|1[3-9]\d{9})\b", result.stdout))

    def test_repeat_run_is_idempotent_and_never_overwrites_database(self):
        first = self.run_demo("--no-serve")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        digest = _sha256(self.db)
        counts = self.counts(self.db)
        credentials = (self.runtime / "demo-credentials.txt").read_text(encoding="utf-8")
        second = self.run_demo("--no-serve")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("已存在，复用（未重复造数、未迁移）", second.stdout)
        self.assertIn("已有库不迁移、不覆盖", second.stdout)
        self.assertEqual(_sha256(self.db), digest, "repeat run must not rewrite the existing database")
        self.assertEqual(self.counts(self.db), counts)
        self.assertEqual((self.runtime / "demo-credentials.txt").read_text(encoding="utf-8"),
                         credentials)
        self.assert_contract_printed(second)

    def test_unknown_slice_is_rejected_without_creating_database(self):
        result = self.run_demo("--no-serve", slice_name="full")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.db.exists())

    def test_real_company_database_is_rejected_not_overwritten(self):
        self.prepare_migrated_db()
        env = _base_env()
        env["PMO_DB"] = str(self.db)
        created = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "manage.py"), "shell", "-c",
             "from core.models import Organization; Organization.objects.create(name='真实公司库')"],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(created.returncode, 0, created.stderr[-800:])
        digest = _sha256(self.db)
        result = self.run_demo("--no-serve")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, output)
        self.assertIn("真实公司", output)
        self.assertEqual(_sha256(self.db), digest, "refused run must not modify the real database")
        self.assertEqual(_query(self.db, "SELECT COUNT(*) FROM core_organization WHERE is_demo=1"), 0)

    def test_non_sqlite_file_is_refused_without_touching_it(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.db.write_bytes(b"this is not a sqlite database\n")
        digest = _sha256(self.db)
        result = self.run_demo("--no-serve")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, output)
        self.assertIn("SQLite", output)
        self.assertEqual(_sha256(self.db), digest)

    def test_stop_without_a_running_server_is_a_clean_noop(self):
        result = self.run_demo("--stop")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("没有由 demo.py 启动", result.stdout)
        self.assertFalse((self.runtime / "demo-server.pid").exists())

    def test_server_starts_is_reused_by_repeat_run_and_stops(self):
        port = _free_port()
        first = self.run_demo(port=port)
        self.addCleanup(lambda: self.run_demo("--stop", port=port))
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assert_contract_printed(first, url=f"http://127.0.0.1:{port}/")
        self.assertIn(f"http://127.0.0.1:{port}/", first.stdout)
        self.assertIn("已在后台启动", first.stdout)
        state_file = self.runtime / "demo-server.json"
        self.assertTrue(state_file.is_file())
        state = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["port"], port)
        self.assertEqual(state["db"], str(self.db))
        self.assertEqual(state["repo"], str(PROJECT_ROOT))
        deadline = time.time() + 10
        while time.time() < deadline and not _health_ok(port):
            time.sleep(0.2)
        self.assertTrue(_health_ok(port))
        digest = _sha256(self.db)
        second = self.run_demo(port=port)
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("复用已在运行的后台服务器", second.stdout)
        self.assertIn("演练密码：", second.stdout)
        self.assertEqual(_sha256(self.db), digest)
        stopped = self.run_demo("--stop", port=port)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assertIn("已停止演练服务器", stopped.stdout)
        self.assertFalse(state_file.exists())
        deadline = time.time() + 10
        while time.time() < deadline and _health_ok(port):
            time.sleep(0.2)
        self.assertFalse(_health_ok(port))

    def test_readme_and_status_use_the_same_demo_command(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        status = (PROJECT_ROOT / "project" / "STATUS.md").read_text(encoding="utf-8")
        self.assertIn(EXPECTED_COMMAND, readme)
        self.assertIn("scripts/demo.py", status)
        help_result = subprocess.run(
            [sys.executable, str(DEMO_SCRIPT), "--help"], cwd=PROJECT_ROOT,
            env=_base_env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--slice", help_result.stdout)
        self.assertIn("--stop", help_result.stdout)


class DatabaseRefusalTests(unittest.TestCase):
    """Existing files that are not a recognized demo DB must be refused untouched."""

    @classmethod
    def setUpClass(cls):
        cls.template_dir = tempfile.TemporaryDirectory(prefix="demo-refusal-template-")
        cls.empty_db = Path(cls.template_dir.name) / "empty.sqlite3"
        env = _base_env()
        env["PMO_DB"] = str(cls.empty_db)
        result = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "manage.py"), "migrate", "--noinput"],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(f"template migrate failed: {result.stderr[-800:]}")

    @classmethod
    def tearDownClass(cls):
        cls.template_dir.cleanup()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="demo-refusal-")
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db = self.root / "runtime" / "demo.sqlite3"
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.runtime = self.root / "runtime"

    def run_demo(self, *extra, port=None, timeout=180, env_overrides=None,
                 runtime=None, db=None):
        args = [sys.executable, str(DEMO_SCRIPT), "--slice", "micro",
                "--db", str(db or self.db),
                "--runtime-dir", str(runtime or self.runtime)]
        if port is not None:
            args += ["--port", str(port)]
        args += list(extra)
        env = _base_env()
        env.update(env_overrides or {})
        return subprocess.run(args, cwd=PROJECT_ROOT, env=env,
                              capture_output=True, text=True, timeout=timeout)

    def test_zero_byte_database_is_refused_without_touching_it(self):
        self.db.write_bytes(b"")
        digest = _sha256(self.db)
        result = self.run_demo("--no-serve")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, output)
        self.assertIn("0 字节", output)
        self.assertEqual(_sha256(self.db), digest)
        self.assertEqual(self.db.stat().st_size, 0)

    def test_empty_or_non_project_sqlite_is_refused_without_touching_it(self):
        with sqlite3.connect(self.db) as connection:
            connection.execute("CREATE TABLE unrelated (id INTEGER)")
            connection.commit()
        digest = _sha256(self.db)
        result = self.run_demo("--no-serve")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, output)
        self.assertIn("不是项目中枢演练库", output)
        self.assertEqual(_sha256(self.db), digest)

    def test_old_schema_demo_database_is_refused_not_migrated(self):
        first = self.run_demo("--no-serve")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        with sqlite3.connect(self.db) as connection:
            deleted = connection.execute(
                "DELETE FROM django_migrations WHERE app='billing' AND name='0001_initial'"
            ).rowcount
            connection.commit()
        self.assertEqual(deleted, 1)
        digest = _sha256(self.db)
        result = self.run_demo("--no-serve")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, output)
        self.assertIn("旧结构", output)
        self.assertEqual(_sha256(self.db), digest,
                         "old-schema demo database must not be migrated in place")

    def test_production_refusal_happens_before_creating_runtime_directory(self):
        runtime = self.root / "never-created-runtime"
        db = self.root / "never-created-db" / "demo.sqlite3"
        result = self.run_demo("--no-serve", env_overrides={
            "PMO_ENV": "production", "PMO_DB": str(db)}, runtime=runtime, db=db)
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertIn("production", result.stderr)
        self.assertFalse(runtime.exists())
        self.assertFalse(db.exists())

    def test_stop_refuses_state_mismatch_and_keeps_server_running(self):
        port = _free_port()
        first = self.run_demo(port=port)
        self.addCleanup(lambda: self.run_demo("--stop", port=port))
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        state_file = self.runtime / "demo-server.json"
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["db"] = str(self.root / "other-demo.sqlite3")
        state_file.write_text(json.dumps(state), encoding="utf-8")
        refused = self.run_demo("--stop", port=port)
        output = refused.stdout + refused.stderr
        self.assertEqual(refused.returncode, 3, output)
        self.assertIn("不一致", output)
        self.assertTrue(_health_ok(port), "mismatched --stop must not kill the server")
        state = json.loads(state_file.read_text(encoding="utf-8"))
        state["db"] = str(self.db)
        state_file.write_text(json.dumps(state), encoding="utf-8")
        stopped = self.run_demo("--stop", port=port)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)

    def test_foreign_health_server_is_not_reused(self):
        port = _free_port()
        script = (
            "import http.server, sys\n"
            "class Handler(http.server.BaseHTTPRequestHandler):\n"
            "    def do_GET(self):\n"
            "        self.send_response(200); self.end_headers(); self.wfile.write(b'{}')\n"
            "    def log_message(self, *args): pass\n"
            "http.server.HTTPServer(('127.0.0.1', int(sys.argv[1])), Handler).serve_forever()\n")
        foreign = subprocess.Popen([sys.executable, "-c", script, str(port)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(foreign.kill)
        deadline = time.time() + 10
        while time.time() < deadline and not _health_ok(port):
            time.sleep(0.2)
        self.assertTrue(_health_ok(port))
        result = self.run_demo(port=port)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 5, output)
        self.assertIn("不是由本脚本启动", output)
        self.assertTrue(_health_ok(port), "foreign server must not be stopped by demo.py")


if __name__ == "__main__":
    unittest.main()


class SeedDemoRuntimeTests(TestCase):
    """In-process coverage for the Q-39 seed_demo runtime-dir override."""

    def test_seed_demo_writes_credentials_to_runtime_dir_and_is_idempotent(self):
        import os
        import stat
        import tempfile
        from io import StringIO
        from pathlib import Path
        from unittest import mock

        from django.core.management import call_command
        from core.models import Document, Organization

        with tempfile.TemporaryDirectory(prefix="seed-demo-runtime-") as runtime:
            with mock.patch.dict(os.environ, {"PMO_DEMO_RUNTIME_DIR": runtime}):
                first = StringIO()
                call_command("seed_demo", stdout=first)
                output = first.getvalue()
                self.assertIn("演练数据已建立", output)
                credentials = Path(runtime) / "demo-credentials.txt"
                self.assertIn(str(credentials), output)
                self.assertTrue(credentials.is_file())
                self.assertEqual(stat.S_IMODE(credentials.stat().st_mode), 0o600)
                text = credentials.read_text(encoding="utf-8")
                for username in ("owner", "finance", "pm", "member", "auditor", "newbie"):
                    self.assertIn(username, text)
                organization_count = Organization.objects.count()
                document_count = Document.objects.count()
                self.assertEqual(organization_count, 1)
                self.assertGreater(document_count, 0)
                second = StringIO()
                call_command("seed_demo", stdout=second)
                self.assertIn("演练数据已存在，未重置", second.getvalue())
                self.assertEqual(Organization.objects.count(), organization_count)
                self.assertEqual(Document.objects.count(), document_count)
