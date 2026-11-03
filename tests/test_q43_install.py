"""Q-43 clean-machine install script tests (fake docker, no real Docker required)."""
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "scripts" / "install.sh"

FAKE_DOCKER = """#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$1 $2" in
  "compose version") [ "${FAKE_COMPOSE:-ok}" = "missing" ] && exit 1; exit 0 ;;
  "compose config") exit 0 ;;
  "compose up")
    if [ "${FAKE_UP:-ok}" = "fail" ]; then
      printf '%s\\n' "Error response from daemon: fake build failure" >&2
      exit 1
    fi
    exit 0 ;;
  "compose exec")
    if [ "${FAKE_HEALTH:-ok}" = "fail" ]; then exit 1; fi
    exit 0 ;;
  "compose ps") printf '%s\\n' "NAME STATUS app exited(1)"; exit 0 ;;
  "compose logs") printf '%s\\n' "fake app log line"; exit 0 ;;
esac
exit 1
"""


class InstallScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="q43-install-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        (self.root / "scripts" / "install.sh").write_bytes(INSTALL.read_bytes())
        os.chmod(self.root / "scripts" / "install.sh", 0o755)
        (self.root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        docker = self.bin / "docker"
        docker.write_text(FAKE_DOCKER, encoding="utf-8")
        os.chmod(docker, 0o755)
        self.log = self.root / "docker.log"
        self.addCleanup(lambda: None)
        self.env = {key: value for key, value in os.environ.items()}
        self.env.update({"PATH": f"{self.bin}:{self.env.get('PATH', '')}",
                         "FAKE_DOCKER_LOG": str(self.log),
                         "PMO_INSTALL_HEALTH_TRIES": "2",
                         "PMO_INSTALL_HEALTH_INTERVAL": "0"})
        self.env.pop("PMO_DOMAIN", None)

    def run_install(self, *args, extra=None):
        env = dict(self.env)
        env.update(extra or {})
        return subprocess.run(["sh", str(self.root / "scripts" / "install.sh"), *args],
                              cwd=self.root, env=env, capture_output=True, text=True,
                              timeout=120)

    def env_file(self):
        return self.root / ".env"

    def env_digest(self):
        return hashlib.sha256(self.env_file().read_bytes()).hexdigest()

    def docker_calls(self):
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def test_first_install_generates_secrets_and_is_retry_safe(self):
        result = self.run_install("--domain", "pmo.example.test")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("安装完成：https://pmo.example.test/", result.stdout)
        env_file = self.env_file()
        self.assertTrue(env_file.is_file())
        self.assertEqual(stat.S_IMODE(env_file.stat().st_mode), 0o600)
        text = env_file.read_text(encoding="utf-8")
        self.assertIn("PMO_DOMAIN=pmo.example.test", text)
        self.assertIn("PMO_SCOPE=micro", text)
        self.assertNotIn("replace-with", text)
        secret = [line.split("=", 1)[1] for line in text.splitlines()
                  if line.startswith("DJANGO_SECRET_KEY=")][0]
        password = [line.split("=", 1)[1] for line in text.splitlines()
                    if line.startswith("POSTGRES_PASSWORD=")][0]
        self.assertGreaterEqual(len(secret), 40)
        self.assertGreaterEqual(len(password), 32)
        self.assertNotEqual(secret, password)
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn(password, result.stdout + result.stderr)
        calls = self.docker_calls()
        self.assertIn("compose up -d --build", calls)
        self.assertIn("compose exec", calls)
        self.assertNotIn("down -v", calls)
        digest = self.env_digest()
        second = self.run_install("--domain", "ignored.example.test")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("复用且不修改（重试安全）", second.stdout)
        self.assertEqual(self.env_digest(), digest, ".env must never be overwritten")

    def test_missing_domain_creates_nothing(self):
        result = self.run_install()
        self.assertEqual(result.returncode, 2)
        self.assertIn("首次安装必须提供 --domain", result.stderr)
        self.assertFalse(self.env_file().exists())

    def test_placeholder_env_is_reported_and_not_modified(self):
        self.env_file().write_text("PMO_DOMAIN=pmo.example.test\n"
                                   "DJANGO_SECRET_KEY=replace-with-a-secret\n"
                                   "POSTGRES_PASSWORD=replace-with-a-password\n",
                                   encoding="utf-8")
        digest = self.env_digest()
        result = self.run_install()
        self.assertEqual(result.returncode, 2)
        self.assertIn("占位值", result.stderr)
        self.assertEqual(self.env_digest(), digest)
        self.assertNotIn("compose up", self.docker_calls())

    def test_failed_install_keeps_env_and_reports_diagnostics(self):
        result = self.run_install("--domain", "pmo.example.test", extra={"FAKE_UP": "fail"})
        self.assertEqual(result.returncode, 3)
        self.assertIn("安装失败", result.stderr)
        self.assertIn("已保留 .env 与数据卷", result.stderr)
        self.assertIn("docker compose logs", result.stderr)
        self.assertIn("compose up", self.docker_calls())
        self.assertTrue(self.env_file().is_file())
        self.assertNotIn("down -v", self.docker_calls())

    def test_retry_after_failure_reuses_env(self):
        first = self.run_install("--domain", "pmo.example.test", extra={"FAKE_UP": "fail"})
        self.assertEqual(first.returncode, 3)
        digest = self.env_digest()
        retry = self.run_install()
        self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
        self.assertEqual(self.env_digest(), digest)
        self.assertEqual(self.docker_calls().count("compose up -d --build"), 2)

    def test_health_timeout_is_diagnosable_and_keeps_state(self):
        result = self.run_install("--domain", "pmo.example.test", extra={"FAKE_HEALTH": "fail"})
        self.assertEqual(result.returncode, 3)
        self.assertIn("未在 180 秒内通过 /health/", result.stderr)
        self.assertTrue(self.env_file().is_file())
        self.assertNotIn("down -v", self.docker_calls())

    def test_missing_docker_is_a_precondition_error(self):
        # GitHub runners ship /usr/bin/docker, so use an isolated bin containing only
        # the shell helper the script needs before it checks for docker.
        empty_bin = self.root / "empty-bin"
        empty_bin.mkdir()
        dirname = shutil.which("dirname")
        self.assertIsNotNone(dirname)
        os.symlink(dirname, empty_bin / "dirname")
        env = {key: value for key, value in self.env.items() if key != "PATH"}
        env["PATH"] = str(empty_bin)
        result = subprocess.run(["/bin/sh", str(self.root / "scripts" / "install.sh"),
                                 "--domain", "pmo.example.test"], cwd=self.root, env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn("未找到 docker", result.stderr)
        self.assertFalse(self.env_file().exists())

    def test_docker_present_but_compose_unusable_is_an_install_failure(self):
        result = self.run_install("--domain", "pmo.example.test",
                                  extra={"FAKE_COMPOSE": "missing"})
        self.assertEqual(result.returncode, 3)
        self.assertIn("docker compose v2 不可用", result.stderr)
        self.assertFalse(self.env_file().exists())


if __name__ == "__main__":
    unittest.main()
