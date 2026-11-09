"""Q-52: 演练库 → 正式库一键切换（``scripts/go-live.py``）回归测试。

测试把 ``scripts/go-live.py`` 当作真实子进程，在隔离的临时目录/临时 SQLite 文件上
执行，覆盖：演练服务运行中拒绝且两库哈希不变、非空（真实公司）库拒绝、演练库作为
目标拒绝（含同一路径不建文件）、空库成功迁移 + 无演练组织 + 打印 /setup/、重复执行
幂等、``--check`` 只读与稳定退出码、非项目结构拒绝、``--stop-demo`` 停止真实演练服务，
以及 README/STATUS/--help 的「演练→正式」说明。所有演练数据均为 ``seed_demo`` 虚构数据。
"""
import hashlib
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GO_LIVE_SCRIPT = PROJECT_ROOT / "scripts" / "go-live.py"
DEMO_SCRIPT = PROJECT_ROOT / "scripts" / "demo.py"
SETUP_URL = "http://127.0.0.1:8000/setup/"


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


class GoLiveScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 模板只建一次：空的已迁移正式库、真实公司库、seed_demo 演练库（含凭据）。
        cls.template_dir = tempfile.TemporaryDirectory(prefix="golive-template-")
        root = Path(cls.template_dir.name)
        cls.empty_db = root / "empty.sqlite3"
        env = _base_env()
        env["PMO_DB"] = str(cls.empty_db)
        migrated = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "manage.py"), "migrate", "--noinput"],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=600)
        if migrated.returncode != 0:
            raise RuntimeError(f"template migrate failed: {migrated.stderr[-800:]}")

        cls.real_db = root / "real.sqlite3"
        shutil.copy2(cls.empty_db, cls.real_db)
        env["PMO_DB"] = str(cls.real_db)
        created = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "manage.py"), "shell", "-c",
             "from core.models import Organization; Organization.objects.create(name='真实公司库')"],
            cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, timeout=300)
        if created.returncode != 0:
            raise RuntimeError(f"real-company template failed: {created.stderr[-800:]}")

        cls.seeded_db = root / "demo.sqlite3"
        cls.seeded_runtime = root / "demo-runtime"
        seeded = subprocess.run(
            [sys.executable, str(DEMO_SCRIPT), "--slice", "micro",
             "--db", str(cls.seeded_db), "--runtime-dir", str(cls.seeded_runtime),
             "--no-serve"],
            cwd=PROJECT_ROOT, env=_base_env(), capture_output=True, text=True, timeout=300)
        if seeded.returncode != 0:
            raise RuntimeError(f"seed_demo template failed: {seeded.stderr[-800:]}")

    @classmethod
    def tearDownClass(cls):
        cls.template_dir.cleanup()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="golive-script-")
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.target = self.root / "formal.sqlite3"
        self.demo_db = self.root / "demo.sqlite3"
        self.runtime = self.root / "runtime"

    def run_go_live(self, *extra, db=None, demo_db=None, runtime=None, timeout=600,
                    env_overrides=None):
        args = [sys.executable, str(GO_LIVE_SCRIPT),
                "--db", str(db or self.target),
                "--demo-db", str(demo_db or self.demo_db),
                "--runtime-dir", str(runtime or self.runtime)]
        args += list(extra)
        env = _base_env()
        env.update(env_overrides or {})
        return subprocess.run(args, cwd=PROJECT_ROOT, env=env,
                              capture_output=True, text=True, timeout=timeout)

    def copy_into_target(self, template):
        shutil.copy2(template, self.target)
        return self.target

    def copy_seeded_demo(self, destination=None):
        destination = destination or self.demo_db
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.seeded_db, destination)
        self.runtime.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.seeded_runtime / "demo-credentials.txt",
                     self.runtime / "demo-credentials.txt")
        return destination

    def start_demo_server(self):
        """Start a real demo.py-managed server on a free port and stop it afterwards."""
        self.copy_seeded_demo()
        port = _free_port()
        result = subprocess.run(
            [sys.executable, str(DEMO_SCRIPT), "--slice", "micro",
             "--db", str(self.demo_db), "--runtime-dir", str(self.runtime),
             "--port", str(port)],
            cwd=PROJECT_ROOT, env=_base_env(), capture_output=True, text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.addCleanup(subprocess.run,
                        [sys.executable, str(DEMO_SCRIPT), "--slice", "micro",
                         "--db", str(self.demo_db), "--runtime-dir", str(self.runtime),
                         "--port", str(port), "--stop"],
                        cwd=PROJECT_ROOT, env=_base_env(), capture_output=True, text=True,
                        timeout=120)
        return port

    def seeded_password(self):
        text = (self.seeded_runtime / "demo-credentials.txt").read_text(encoding="utf-8")
        return re.search(r"演练密码：(\S+)", text).group(1)

    def insert_business_row_without_org(self, table):
        """Insert one minimally-valid row into a migrated DB without any organization.

        The test crafts a structurally migrated but inconsistent database (no company,
        but a leftover business row) to prove classification scans every user table.
        """
        with sqlite3.connect(self.target) as connection:
            columns = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            names, values = [], []
            present = {row[1] for row in columns}
            for _, name, kind, notnull, default, primary in columns:
                if primary or not notnull or default is not None:
                    continue
                names.append('"' + name.replace('"', '""') + '"')
                upper = (kind or "").upper()
                if name == "payload":
                    values.append("{}")  # append-only audit rows require valid JSON
                elif "INT" in upper:
                    values.append(1)
                elif "DATE" in upper or "TIME" in upper:
                    values.append("2026-11-09 00:00:00")
                elif "BLOB" in upper:
                    values.append(b"x")
                else:
                    values.append("x")
            # core_ledgerentry's one_ledger_source check requires exactly one source FK.
            if table == "core_ledgerentry" and "document_id" in present:
                names.append('"document_id"')
                values.append(1)
            placeholders = ",".join("?" for _ in values)
            connection.execute(
                f'INSERT INTO "{table}" ({",".join(names)}) VALUES ({placeholders})', values)
            connection.commit()

    # ---------------------------------------------------------------- 拒绝路径

    def test_refuses_while_demo_server_running_and_keeps_both_databases_unchanged(self):
        port = self.start_demo_server()
        self.copy_into_target(self.empty_db)
        target_digest = _sha256(self.target)
        demo_digest = _sha256(self.demo_db)

        result = self.run_go_live()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("python scripts/demo.py --stop", output)
        self.assertIn(f"127.0.0.1:{port}", output)
        self.assertEqual(_sha256(self.target), target_digest,
                         "refusal must not write the target database")
        self.assertEqual(_sha256(self.demo_db), demo_digest,
                         "refusal must not write the demo database")
        self.assertEqual(_query(self.target, "SELECT COUNT(*) FROM core_organization"), 0)

        check = self.run_go_live("--check")
        self.assertEqual(check.returncode, 2, check.stdout + check.stderr)
        self.assertIn("运行中", check.stdout)
        self.assertIn("python scripts/demo.py --stop", check.stdout)

    def test_refuses_real_company_database_without_writing(self):
        self.copy_into_target(self.real_db)
        digest = _sha256(self.target)
        result = self.run_go_live()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, output)
        self.assertIn("已有公司", output)
        self.assertIn("真实", output)
        self.assertEqual(_sha256(self.target), digest,
                         "refused run must not modify the real-company database")
        self.assertEqual(_query(self.target, "SELECT COUNT(*) FROM core_organization"), 1)
        self.assertEqual(_query(self.target,
                                "SELECT COUNT(*) FROM core_organization WHERE is_demo=0"), 1)

    def test_refuses_migrated_db_with_business_rows_but_no_org(self):
        # Regression: a migrated DB with core_organization=0 but leftovers in any user
        # table must be refused, not treated as an empty formal database.
        for table in ("core_employee", "core_ledgerentry", "core_auditevent"):
            with self.subTest(table=table):
                target = self.root / f"{table}.sqlite3"
                shutil.copy2(self.empty_db, target)
                with sqlite3.connect(target) as connection:
                    # Keep the helper pointed at this copy for the generic inserter.
                    pass
                self.target = target
                self.insert_business_row_without_org(table)
                digest = _sha256(target)
                result = self.run_go_live(db=target)
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 3, output)
                self.assertIn("业务数据", output)
                self.assertIn("没有公司记录", output)
                self.assertEqual(_sha256(target), digest,
                                 f"refusal must not write the target database ({table})")
                self.assertEqual(_query(target, "SELECT COUNT(*) FROM core_organization"), 0)
                check = self.run_go_live("--check", db=target)
                self.assertEqual(check.returncode, 3, check.stdout + check.stderr)
                self.assertIn("业务数据", check.stdout)
                self.assertEqual(_sha256(target), digest,
                                 f"--check must not write the target database ({table})")

    def test_refuses_demo_database_as_target_without_writing(self):
        self.copy_into_target(self.seeded_db)
        digest = _sha256(self.target)
        result = self.run_go_live()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, output)
        self.assertIn("演练库", output)
        self.assertEqual(_sha256(self.target), digest,
                         "refused run must not modify the demo database")
        self.assertEqual(_query(self.target,
                                "SELECT COUNT(*) FROM core_organization WHERE is_demo=1"), 1)

    def test_refuses_demo_database_path_even_when_file_missing(self):
        result = self.run_go_live(demo_db=self.target)
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, output)
        self.assertIn("演练库", output)
        self.assertFalse(self.target.exists(),
                         "refusal must not create the target database")

    def test_refuses_non_project_sqlite_without_touching_it(self):
        with sqlite3.connect(self.target) as connection:
            connection.execute("CREATE TABLE unrelated (id INTEGER)")
            connection.commit()
        digest = _sha256(self.target)
        result = self.run_go_live()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 3, output)
        self.assertIn("不是本工具", output)
        self.assertEqual(_sha256(self.target), digest)

    def test_refuses_production_environment_without_creating_target(self):
        result = self.run_go_live(env_overrides={"PMO_ENV": "production"})
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("scripts/install.sh", output)
        self.assertFalse(self.target.exists())

    # ---------------------------------------------------------------- 成功与幂等

    def test_empty_target_switches_successfully_and_prints_setup_entry(self):
        result = self.run_go_live()
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn(SETUP_URL, output)
        self.assertIn("/setup/", output)
        self.assertIn("scripts/install.sh", output)
        self.assertIn("已应用迁移", output)
        self.assertTrue(self.target.is_file())
        self.assertEqual(_query(self.target, "SELECT COUNT(*) FROM core_organization"), 0)
        self.assertEqual(_query(
            self.target, "SELECT COUNT(*) FROM core_organization WHERE is_demo=1"), 0)
        self.assertGreater(_query(self.target, "SELECT COUNT(*) FROM django_migrations"), 0)
        self.assertNotIn(self.seeded_password(), output,
                         "command output must not contain the demo password")

    def test_repeat_run_is_idempotent(self):
        first = self.run_go_live()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        digest = _sha256(self.target)
        counts = {
            "orgs": _query(self.target, "SELECT COUNT(*) FROM core_organization"),
            "migrations": _query(self.target, "SELECT COUNT(*) FROM django_migrations"),
        }
        second = self.run_go_live()
        output = second.stdout + second.stderr
        self.assertEqual(second.returncode, 0, output)
        self.assertIn("已是最新结构", output)
        self.assertIn("重复执行未重复迁移", output)
        self.assertIn(SETUP_URL, output)
        self.assertEqual(_sha256(self.target), digest,
                         "repeat run must not rewrite the migrated empty database")
        self.assertEqual(_query(self.target, "SELECT COUNT(*) FROM core_organization"),
                         counts["orgs"])
        self.assertEqual(_query(self.target, "SELECT COUNT(*) FROM django_migrations"),
                         counts["migrations"])

    # ---------------------------------------------------------------- --check

    def test_check_is_read_only_with_stable_exit_codes(self):
        missing = self.run_go_live("--check")
        self.assertEqual(missing.returncode, 0, missing.stdout + missing.stderr)
        self.assertIn("可以安全切换", missing.stdout)
        self.assertIn(str(self.target), missing.stdout)
        self.assertFalse(self.target.exists(),
                         "--check must not create the target database")

        self.copy_into_target(self.real_db)
        digest = _sha256(self.target)
        real = self.run_go_live("--check")
        self.assertEqual(real.returncode, 3, real.stdout + real.stderr)
        self.assertIn("拒绝切换", real.stdout)
        self.assertEqual(_sha256(self.target), digest,
                         "--check must not modify an existing database")

        self.copy_into_target(self.seeded_db)
        demo_digest = _sha256(self.target)
        demo = self.run_go_live("--check")
        self.assertEqual(demo.returncode, 3, demo.stdout + demo.stderr)
        self.assertEqual(_sha256(self.target), demo_digest)

    # ---------------------------------------------------------------- --stop-demo

    def test_stop_demo_stops_the_running_server_then_switch_succeeds(self):
        port = self.start_demo_server()
        self.assertTrue(_health_ok(port))
        stopped = self.run_go_live("--stop-demo")
        output = stopped.stdout + stopped.stderr
        self.assertEqual(stopped.returncode, 0, output)
        self.assertIn("已停止演练服务器", output)
        self.assertFalse((self.runtime / "demo-server.json").exists())
        deadline = time.time() + 10
        while time.time() < deadline and _health_ok(port):
            time.sleep(0.2)
        self.assertFalse(_health_ok(port), "stopped demo server must not keep listening")

        switched = self.run_go_live()
        self.assertEqual(switched.returncode, 0, switched.stdout + switched.stderr)
        self.assertIn(SETUP_URL, switched.stdout)
        self.assertEqual(_query(self.target, "SELECT COUNT(*) FROM core_organization"), 0)

    # ---------------------------------------------------------------- 文档一致性

    def test_readme_status_and_help_document_the_switch(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        status = (PROJECT_ROOT / "project" / "STATUS.md").read_text(encoding="utf-8")
        as_delivered = (PROJECT_ROOT / "project" / "QUEUE.md").read_text(encoding="utf-8")
        self.assertIn("scripts/go-live.py", readme)
        self.assertIn("演练", readme)
        self.assertIn("正式库", readme)
        self.assertIn("scripts/go-live.py", status)
        self.assertIn("演练", status)
        self.assertIn("### Q-52", as_delivered)
        self.assertIn("演练库到正式库的一键切换", as_delivered)

        help_result = subprocess.run(
            [sys.executable, str(GO_LIVE_SCRIPT), "--help"], cwd=PROJECT_ROOT,
            env=_base_env(), capture_output=True, text=True, timeout=60)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for flag in ("--check", "--db", "--stop-demo"):
            self.assertIn(flag, help_result.stdout)


if __name__ == "__main__":
    unittest.main()
