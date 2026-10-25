from __future__ import annotations

from contextlib import chdir, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tooling.ai_delivery.agents.registry import AGENTS, ConfigurationError, validate_config
from tooling.ai_delivery.doctor import STREAM_LIMIT, doctor, main, probe


REPOSITORY = Path(__file__).resolve().parents[1]


class DeliveryAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="delivery-doctor-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def executable(self, body, name="fake-agent"):
        path = self.root / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(0o700)
        return str(path)

    def test_missing_command_and_exact_schema(self):
        with patch("tooling.ai_delivery.doctor.shutil.which", return_value=None):
            result = doctor()
        self.assertEqual(set(result), {"schema_version", "controller", "agents"})
        self.assertEqual(result["controller"]["id"], "codex_controller")
        self.assertEqual(set(AGENTS), {"codex_controller", "kimi_code", "opencode", "dsh", "claude_code"})
        self.assertEqual(len(result["agents"]), 4)
        self.assertEqual({entry["id"] for entry in result["agents"]},
                         {"kimi_code", "opencode", "dsh", "claude_code"})
        for entry in [result["controller"], *result["agents"]]:
            self.assertEqual(set(entry), {"id", "available", "executable", "version", "status", "capabilities", "diagnostics"})
            self.assertEqual(entry["status"], "missing")
            self.assertFalse(entry["available"])
            self.assertEqual(set(entry["capabilities"].values()), {"unverified"})

    def test_config_rejects_unknown_ids_fields_and_non_paths(self):
        for config in [{"arbitrary": "/bin/true"}, {"zcode": "/bin/true"}, {"dsh": {"executable": "/bin/true"}}, {"dsh": "relative"}, {"dsh": "/bin/true --version"}, [], {"dsh": 1}, {"dsh": "/bin/true\x00"}]:
            with self.subTest(config=config), self.assertRaises(ConfigurationError):
                validate_config(config)

    def test_explicit_bad_config_does_not_fall_back_to_path(self):
        nonexec = self.root / "plain"
        nonexec.write_text("plain")
        for value in [str(nonexec), str(self.root), str(self.root / "missing"), "/bin/true -x"]:
            with patch("tooling.ai_delivery.doctor.shutil.which") as lookup:
                result = probe(AGENTS["dsh"], {"dsh": value})
            lookup.assert_not_called()
            self.assertEqual(result["status"], "config_required")

    def test_only_fixed_version_argv_and_no_shell_expansion(self):
        marker = self.root / "command-injection"
        exe = self.executable("import sys\nassert sys.argv[1:] == ['--version']\nprint('dsh 0.2.1-rc.2')\n", "agent; touch command-injection")
        result = probe(AGENTS["dsh"], {"dsh": exe})
        self.assertEqual(result["status"], "probe_verified")
        self.assertEqual(result["version"], "0.2.1-rc.2")
        self.assertFalse(marker.exists())
        self.assertEqual(set(result["capabilities"].values()), {"unverified"})

    def test_probe_environment_is_private_and_does_not_inherit_credentials(self):
        exe = self.executable("import os\nfrom pathlib import Path\nassert 'GH_TOKEN' not in os.environ\nassert 'ANTHROPIC_API_KEY' not in os.environ\nassert Path.cwd().resolve() == Path(os.environ['HOME']).resolve()\nPath('temporary-cache').write_text('safe')\nprint('1.2.3')\n")
        with patch.dict(os.environ, {"GH_TOKEN": "sensitive-fixture", "ANTHROPIC_API_KEY": "sensitive-fixture"}):
            result = probe(AGENTS["dsh"], {"dsh": exe})
        self.assertEqual(result["status"], "probe_verified")
        self.assertFalse((self.root / "temporary-cache").exists())

    def test_output_and_exception_text_are_not_disclosed(self):
        exe = self.executable("import sys\nprint('dsh 1.2.3')\nprint('/Users/private-person/.auth token=secret-fixture', file=sys.stderr)\n")
        result = probe(AGENTS["dsh"], {"dsh": exe})
        serialized = json.dumps(result)
        self.assertNotIn("private-person", serialized)
        self.assertNotIn("secret-fixture", serialized)
        self.assertNotIn(str(self.root), serialized)
        self.assertEqual(result["executable"], "fake-agent")

    def test_nonzero_exit_does_not_verify_version(self):
        exe = self.executable("print('1.2.3')\nraise SystemExit(1)\n")
        result = probe(AGENTS["dsh"], {"dsh": exe})
        self.assertEqual(result["status"], "probe_failed")
        self.assertIsNone(result["version"])

    def test_no_version_output_is_not_verified(self):
        exe = self.executable("print('native binary not installed')\n")
        result = probe(AGENTS["claude_code"], {"claude_code": exe})
        self.assertEqual(result["status"], "config_required")

    def test_timeout_is_bounded(self):
        exe = self.executable("import time\ntime.sleep(20)\n")
        started = time.monotonic()
        with patch("tooling.ai_delivery.doctor.PROBE_TIMEOUT", 0.15):
            result = probe(AGENTS["dsh"], {"dsh": exe})
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(result["diagnostics"], ["probe_timeout"])

    def test_each_stream_is_bounded_and_probe_terminates(self):
        for stream in [1, 2]:
            exe = self.executable(f"import os\nwhile True: os.write({stream}, b'x' * 8192)\n")
            started = time.monotonic()
            result = probe(AGENTS["dsh"], {"dsh": exe})
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(result["diagnostics"], ["probe_output_limit"])

    def test_cli_reads_valid_config(self):
        exe = self.executable("print('dsh 1.2.3')\n")
        config = self.root / "agents.json"
        config.write_text(json.dumps({"dsh": exe}))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("tooling.ai_delivery.doctor.shutil.which", return_value=None), redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(["--config", str(config)])
        self.assertEqual(status, 0)
        self.assertEqual(stderr.getvalue(), "")
        result = json.loads(stdout.getvalue())
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(next(agent for agent in result["agents"] if agent["id"] == "dsh")["status"], "probe_verified")

    def test_cli_rejects_invalid_json_without_echoing_content(self):
        config = self.root / "agents.json"
        config.write_text('{"dsh": "secret-fixture"')
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(["--config", str(config)])
        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue()), {"error": "invalid_agent_configuration"})
        self.assertNotIn("secret-fixture", stderr.getvalue())

    def test_cli_rejects_oversized_config(self):
        config = self.root / "agents.json"
        config.write_bytes(b" " * (STREAM_LIMIT + 1))
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(["--config", str(config)])
        self.assertEqual(status, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue()), {"error": "invalid_agent_configuration"})

    def cli(self, config, timeout=10):
        return subprocess.run(
            [sys.executable, "-m", "tooling.ai_delivery.doctor", "--config", str(config)],
            cwd=REPOSITORY, capture_output=True, text=True, errors="replace", timeout=timeout,
        )

    def assert_rejected(self, completed):
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(completed.stdout, "")
        self.assertEqual(completed.stderr.strip(), '{"error":"invalid_agent_configuration"}')
        self.assertNotIn("Traceback", completed.stderr)

    def test_cli_rejects_deeply_nested_json_without_echoing_content(self):
        for shape, content in {"arrays": "[" * 2000 + '"secret-fixture"' + "]" * 2000,
                               "objects": '{"a":' * 2000 + "1" + "}" * 2000}.items():
            with self.subTest(shape=shape):
                self.assertLess(len(content.encode()), STREAM_LIMIT)
                config = self.root / f"{shape}.json"
                config.write_text(content)
                completed = self.cli(config)
                self.assert_rejected(completed)
                self.assertNotIn("secret-fixture", completed.stderr)
                self.assertNotIn(str(self.root), completed.stderr)

    def test_cli_rejects_fifo_config_without_blocking(self):
        fifo = self.root / "agents.fifo"
        try:
            os.mkfifo(fifo)
        except (AttributeError, NotImplementedError, OSError):
            self.skipTest("FIFO not supported")
        started = time.monotonic()
        try:
            completed = self.cli(fifo, timeout=5)
        except subprocess.TimeoutExpired:
            self.fail("configuration read blocked on a FIFO")
        self.assertLess(time.monotonic() - started, 5)
        self.assert_rejected(completed)
        self.assertNotIn(str(self.root), completed.stderr)

    def test_cli_rejects_symlink_config(self):
        target = self.root / "agents.json"
        target.write_text(json.dumps({"dsh": "/bin/true"}))
        link = self.root / "agents-link.json"
        try:
            os.symlink(target, link)
        except (AttributeError, NotImplementedError, OSError):
            self.skipTest("symlinks unavailable")
        completed = self.cli(link)
        self.assert_rejected(completed)
        self.assertNotIn(str(target), completed.stderr)
        self.assertNotIn("/bin/true", completed.stderr)

    def test_cli_rejects_directory_config(self):
        self.assert_rejected(self.cli(self.root))

    def test_relative_path_is_resolved_before_probe_changes_cwd(self):
        bindir = self.root / "bin"
        bindir.mkdir()
        exe = bindir / "dsh"
        exe.write_text(f"#!{sys.executable}\nprint('dsh 4.5.6')\n")
        exe.chmod(0o700)
        with chdir(self.root), patch.dict(os.environ, {"PATH": "bin"}):
            result = doctor()
        entry = next(agent for agent in result["agents"] if agent["id"] == "dsh")
        self.assertEqual(entry["status"], "probe_verified")
        self.assertEqual(entry["version"], "4.5.6")


if __name__ == "__main__":
    unittest.main()
