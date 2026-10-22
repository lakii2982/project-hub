import hashlib
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import textwrap
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKUP_SCRIPT = PROJECT_ROOT / "scripts" / "backup.sh"


MOCK_DOCKER = """\
#!/bin/sh
set -eu

printf '%s\\n' "$*" >> "$MOCK_DOCKER_LOG"

case "$*" in
    "compose ps --status running --services")
        printf '%s\\n' "$MOCK_RUNNING_SERVICES"
        ;;
    "compose stop "*|"compose start "*)
        ;;
    "compose exec -T db sh -c "*)
        if [ "${MOCK_PG_DUMP_FAIL:-0}" = "1" ]; then
            printf 'pg_dump failed\\n' >&2
            exit 19
        fi
        if [ "${MOCK_SIGNAL_DURING_DUMP:-0}" = "1" ]; then
            kill -TERM "$PPID"
            exit 0
        fi
        printf 'fake database dump'
        ;;
    "compose run --rm --no-deps --entrypoint python app -c "*)
        printf 'fake evidence archive'
        ;;
    "compose exec -T db pg_restore --list")
        printf 'fake restore listing\\n'
        ;;
    *)
        printf 'unexpected docker command: %s\\n' "$*" >&2
        exit 97
        ;;
esac
"""


class BackupScriptTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.bin_dir = self.root / "bin"
        self.bin_dir.mkdir()
        self.docker_log = self.root / "docker.log"

        docker = self.bin_dir / "docker"
        docker.write_text(textwrap.dedent(MOCK_DOCKER), encoding="utf-8")
        docker.chmod(0o755)

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_backup(self, backup_dir, running_services="app\nworker", **extra_env):
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.bin_dir}:{env.get('PATH', '')}",
                "MOCK_DOCKER_LOG": str(self.docker_log),
                "MOCK_RUNNING_SERVICES": running_services,
            }
        )
        env.update(extra_env)
        return subprocess.run(
            ["/bin/sh", str(BACKUP_SCRIPT), str(backup_dir)],
            cwd=self.root,
            env=env,
            text=True,
            errors="replace",
            capture_output=True,
            timeout=10,
            check=False,
        )

    def docker_commands(self):
        if not self.docker_log.exists():
            return []
        return self.docker_log.read_text(encoding="utf-8").splitlines()

    def assert_private_mode(self, path, expected):
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected, path)

    def test_successful_backup_uses_private_files_and_relative_manifest(self):
        backup_dir = self.root / "backup"

        result = self.run_backup(backup_dir)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("备份已保存到", result.stdout)
        self.assertNotIn("pg_dump failed", result.stderr)
        self.assertFalse((backup_dir / ".incomplete").exists())
        self.assert_private_mode(backup_dir, 0o700)
        for name in (
            "database.dump",
            "evidence.tar.gz",
            "database.contents.txt",
            "SHA256SUMS",
        ):
            self.assert_private_mode(backup_dir / name, 0o600)

        manifest_lines = (backup_dir / "SHA256SUMS").read_text(
            encoding="utf-8"
        ).splitlines()
        manifest_names = [line.split(maxsplit=1)[1] for line in manifest_lines]
        self.assertEqual(
            manifest_names,
            ["database.dump", "evidence.tar.gz", "database.contents.txt"],
        )

        for line, name in zip(manifest_lines, manifest_names):
            digest = line.split(maxsplit=1)[0]
            self.assertEqual(
                digest,
                hashlib.sha256((backup_dir / name).read_bytes()).hexdigest(),
            )

        commands = self.docker_commands()
        self.assertEqual(commands[0], "compose ps --status running --services")
        self.assertIn("compose stop app worker", commands)
        self.assertEqual(commands[-1], "compose start app worker")

    def test_existing_directory_and_symlink_are_refused(self):
        existing_dir = self.root / "existing"
        existing_dir.mkdir()
        symlink_target = self.root / "target"
        symlink_target.mkdir()
        existing_symlink = self.root / "existing-link"
        existing_symlink.symlink_to(symlink_target, target_is_directory=True)

        for backup_dir in (existing_dir, existing_symlink):
            with self.subTest(backup_dir=backup_dir):
                result = self.run_backup(backup_dir)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("备份已保存到", result.stdout)
                self.assertIn("拒绝覆盖", result.stderr)

        self.assertEqual(self.docker_commands(), [])

    def test_pg_dump_failure_restores_services_and_keeps_marker(self):
        backup_dir = self.root / "failed-backup"

        result = self.run_backup(backup_dir, MOCK_PG_DUMP_FAIL="1")

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("备份已保存到", result.stdout)
        self.assertTrue((backup_dir / ".incomplete").exists())
        self.assert_private_mode(backup_dir / ".incomplete", 0o600)
        self.assert_private_mode(backup_dir / "database.dump", 0o600)
        commands = self.docker_commands()
        self.assertIn("compose stop app worker", commands)
        self.assertEqual(commands[-1], "compose start app worker")
        self.assertFalse(
            any("pg_restore --list" in command for command in commands)
        )

    def test_signal_exits_nonzero_and_restores_only_originally_running_service(self):
        backup_dir = self.root / "signaled-backup"

        result = self.run_backup(
            backup_dir,
            running_services="worker",
            MOCK_SIGNAL_DURING_DUMP="1",
        )

        self.assertEqual(result.returncode, 143, result.stderr)
        self.assertNotIn("备份已保存到", result.stdout)
        self.assertTrue((backup_dir / ".incomplete").exists())
        commands = self.docker_commands()
        self.assertNotIn("compose stop app worker", commands)
        self.assertNotIn("compose stop app", commands)
        self.assertIn("compose stop worker", commands)
        self.assertEqual(commands[-1], "compose start worker")


if __name__ == "__main__":
    unittest.main()
