from __future__ import annotations

from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from tooling.ai_delivery.execution import adapters, sandbox
from tooling.ai_delivery.execution.adapters import (
    AdapterError,
    AgentConfig,
    ConfigurationRequired,
    build_launch,
)
from tooling.ai_delivery.execution.sandbox import IsolationError


SENSITIVE_ENV = {
    "GH_TOKEN": "sensitive-gh",
    "GITHUB_TOKEN": "sensitive-github",
    "GITHUB_PAT": "sensitive-pat",
    "SSH_AUTH_SOCK": "/private/tmp/sensitive-agent.sock",
    "SSH_AGENT_PID": "999999",
    "OPENAI_API_KEY": "sensitive-openai",
    "AWS_SECRET_ACCESS_KEY": "sensitive-aws",
    "KIMI_API_KEY": "sensitive-kimi",
    "DATABASE_URL": "postgres://sensitive",
    "DJANGO_SECRET_KEY": "sensitive-django",
}


class _DeliveryTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="delivery-exec-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        self.home_patch = patch.dict(os.environ, {"HOME": str(self.home)}, clear=False)
        self.home_patch.start()
        self.addCleanup(self.home_patch.stop)

    def executable(self, name: str = "agent") -> Path:
        path = self.root / name
        path.write_text(f"#!{sys.executable}\nprint('fake-agent')\n", encoding="utf-8")
        path.chmod(0o700)
        return path

    def kimi_auth(self, *, provider: str = "managed:kimi-code", include_token: bool = True) -> Path:
        base = self.home / ".kimi-code"
        (base / "oauth").mkdir(parents=True)
        (base / "oauth" / "kimi-code").write_text(
            '{"access_token":"kimi-oauth-fixture","refresh_token":"refresh-fixture"}',
            encoding="utf-8",
        )
        (base / "credentials").mkdir()
        (base / "credentials" / "kimi-code.json").write_text(
            '{"access_token":"kimi-credential-fixture"}', encoding="utf-8"
        )
        lines = [
            'default_model = "kimi-code/k3.1-pro"',
            "",
            f'[providers."{provider}"]',
            'type = "kimi"',
            'base_url = "https://kimi.example.invalid/v1"',
        ]
        if include_token:
            lines.append('api_key = "kimi-provider-secret-fixture"')
        lines.extend([
            "",
            f'[providers."{provider}".oauth]',
            'storage = "file"',
            'key = "oauth/kimi-code"',
            "",
            '[providers."Other Provider"]',
            'type = "openai"',
            'base_url = "https://other.example.invalid/v1"',
            'api_key = "other-provider-secret-fixture"',
            "",
            '[models."kimi-code/k3.1-pro"]',
            f'provider = "{provider}"',
            'model = "k3.1-pro"',
            'max_context_size = 1048576',
            'capabilities = ["thinking", "always_thinking", "tool_use"]',
            "",
            '[models."unrelated/model"]',
            'provider = "Other Provider"',
            'model = "other-model"',
            'max_context_size = 8192',
        ])
        (base / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return base

    def claude_settings(self) -> Path:
        directory = self.home / ".claude"
        directory.mkdir()
        settings = directory / "settings.json"
        settings.write_text(json.dumps({
            "model": "glm-5.3-flash[1M]",
            "env": {
                "ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/paas/v4",
                "ANTHROPIC_AUTH_TOKEN": "claude-provider-secret-fixture",
                "ANTHROPIC_API_KEY": "claude-api-secret-fixture",
                "ANTHROPIC_MODEL": "glm-5.3-flash[1M]",
                "GH_TOKEN": "claude-gh-secret-fixture",
            },
        }), encoding="utf-8")
        return settings

    def dsh_config(self, *, include_token: bool = True) -> Path:
        directory = self.home / ".kimi-code"
        directory.mkdir(exist_ok=True)
        path = directory / "config.toml"
        lines = [
            '[providers."OpenCode Go"]',
            'type = "opencode-go"',
        ]
        if include_token:
            lines.append('api_key = "dsh-opencode-go-secret-fixture"')
        lines.extend([
            "",
            '[providers."Unrelated Provider"]',
            'api_key = "dsh-unrelated-secret-fixture"',
        ])
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path


class AgentConfigAndLaunchTests(_DeliveryTestCase):
    def test_unknown_ids_relative_executables_and_model_injection_are_rejected(self):
        exe = self.executable()
        invalid = [
            lambda: AgentConfig("unknown", str(exe)),
            lambda: AgentConfig("codex_controller", str(exe)),
            lambda: AgentConfig("opencode", "opencode"),
            lambda: AgentConfig("opencode", str(self.root)),
            lambda: AgentConfig("opencode", str(self.root / "missing")),
            lambda: AgentConfig("opencode", str(exe), model="--dangerously-skip-permissions"),
            lambda: AgentConfig("opencode", str(exe), model="-p"),
            lambda: AgentConfig("opencode", str(exe), model="x; touch /tmp/pwned"),
            lambda: AgentConfig("opencode", str(exe), model="model with space"),
            lambda: AgentConfig("opencode", str(exe), model=123),
            lambda: AgentConfig("opencode", str(exe), auth_paths=("relative",)),
            lambda: AgentConfig("opencode", str(exe), auth_paths=(self.root / "fake-auth",)),
            lambda: AgentConfig("opencode", str(exe), base_url="http://example.invalid/v1"),
            lambda: AgentConfig("opencode", str(exe), base_url="https://user:pass@example.invalid/v1"),
        ]
        for factory in invalid:
            with self.subTest(factory=factory), self.assertRaises((AdapterError, ConfigurationRequired)):
                factory()

    def test_discover_uses_installed_executable_and_os_uid_home(self):
        exe = self.executable("kimi")
        uid_home = patch.object(
            adapters.pwd, "getpwuid", return_value=SimpleNamespace(pw_dir=str(self.home)),
        )
        with uid_home, patch.object(adapters.shutil, "which", return_value=str(exe)):
            discovered = AgentConfig.discover("kimi_code")
        self.assertEqual(discovered.agent_id, "kimi_code")
        self.assertEqual(discovered.executable, str(exe.resolve()))
        self.assertEqual(discovered.auth_paths, ())

        base = self.kimi_auth()
        spoofed_home = self.root / "spoofed-home"
        spoofed_home.mkdir()
        with uid_home, patch.dict(os.environ, {"HOME": str(spoofed_home)}), \
                patch.object(adapters.shutil, "which", return_value=str(exe)):
            discovered = AgentConfig.discover("kimi_code")
        self.assertEqual(discovered.auth_paths, (base.resolve(),))
        with patch.object(adapters.shutil, "which", return_value=None):
            with self.assertRaises(ConfigurationRequired):
                AgentConfig.discover("opencode")
        with self.assertRaises(AdapterError):
            AgentConfig.discover("unknown")

    def test_opencode_fixed_argv_and_private_environment(self):
        exe = self.executable()
        prompt = "Implement the task; $(touch /tmp/pwned) `whoami` | echo nope"
        config = AgentConfig("opencode", str(exe))
        with patch.dict(os.environ, SENSITIVE_ENV, clear=False):
            spec = build_launch(config, prompt, self.worktree, self.runtime)

        self.assertEqual(spec.argv, [
            str(exe.resolve()), "run", "--standalone", "--format", "json",
            "--model", "opencode/deepseek-v4.1-flash-free", "--", prompt,
        ])
        self.assertIsNone(spec.stdin)
        self.assertEqual(spec.cwd, self.worktree.resolve())
        self.assertNotIn("--dangerously-skip-permissions", spec.argv)
        for key in SENSITIVE_ENV:
            self.assertNotIn(key, spec.env)
        for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
                    "XDG_STATE_HOME", "TMPDIR"):
            self.assertTrue(Path(spec.env[key]).is_relative_to(self.runtime.resolve()))
        self.assertEqual(spec.env["OPENCODE_DISABLE_AUTOUPDATE"], "1")
        self.assertEqual(spec.env["OPENCODE_DISABLE_PROJECT_CONFIG"], "1")
        self.assertEqual(spec.env["OPENCODE_DISABLE_CLAUDE_CODE"], "1")
        self.assertTrue(spec.env["PATH"])

    def test_prompt_is_one_argv_item_and_no_shell_text(self):
        exe = self.executable()
        prompt = '"; touch /tmp/pwned; echo "'
        config = AgentConfig("opencode", str(exe))
        spec = build_launch(config, prompt, self.worktree, self.runtime)
        self.assertEqual(spec.argv.count(prompt), 1)
        self.assertEqual(spec.argv[-1], prompt)

    def test_kimi_fixed_argv_minimal_config_and_refresh_patterns(self):
        base = self.kimi_auth()
        exe = self.executable()
        config = AgentConfig("kimi_code", str(exe), auth_paths=(base,))
        with patch.dict(os.environ, SENSITIVE_ENV, clear=False):
            spec = build_launch(config, "do the work", self.worktree, self.runtime)

        self.assertEqual(spec.argv[:4], [str(exe.resolve()), "-p", "do the work", "--output-format"])
        self.assertIn("stream-json", spec.argv)
        self.assertIn("kimi-code/k3.1-pro", spec.argv)
        self.assertNotIn("-y", spec.argv)
        self.assertNotIn("--auto", spec.argv)
        self.assertNotIn("--dangerously-skip-permissions", spec.argv)

        kimi_home = Path(spec.env["KIMI_CODE_HOME"])
        self.assertTrue(kimi_home.is_relative_to(self.runtime.resolve()))
        generated = (kimi_home / "config.toml").read_text(encoding="utf-8")
        parsed = tomllib.loads(generated)
        self.assertEqual(parsed["default_model"], "kimi-code/k3.1-pro")
        self.assertEqual(set(parsed["providers"]), {"managed:kimi-code"})
        self.assertEqual(parsed["providers"]["managed:kimi-code"]["type"], "kimi")
        self.assertEqual(parsed["providers"]["managed:kimi-code"]["base_url"],
                         "https://kimi.example.invalid/v1")
        self.assertEqual(parsed["providers"]["managed:kimi-code"]["oauth"], {
            "storage": "file", "key": "oauth/kimi-code",
        })
        self.assertEqual(parsed["models"], {
            "kimi-code/k3.1-pro": {
                "provider": "managed:kimi-code",
                "model": "k3.1-pro",
                "max_context_size": 1048576,
                "capabilities": ["thinking", "always_thinking", "tool_use"],
            },
        })
        self.assertIn('default_model = "kimi-code/k3.1-pro"', generated)
        self.assertIn('type = "kimi"', generated)
        self.assertIn("https://kimi.example.invalid/v1", generated)
        self.assertNotIn("api_key", generated)
        self.assertNotIn("unrelated/model", generated)
        self.assertNotIn("other-provider-secret-fixture", generated)
        self.assertNotIn("kimi-provider-secret-fixture", generated)

        oauth = (base / "oauth" / "kimi-code").resolve()
        credential = (base / "credentials" / "kimi-code.json").resolve()
        self.assertIn(oauth, spec.read_roots)
        self.assertIn(credential, spec.read_roots)
        self.assertNotIn(oauth, spec.write_roots)
        self.assertNotIn(oauth.parent, spec.read_roots)
        self.assertNotIn((base / "config.toml").resolve(), spec.read_roots)
        self.assertNotIn(credential.parent, spec.write_roots)
        self.assertTrue((kimi_home / "oauth").is_symlink())
        self.assertTrue((kimi_home / "credentials").is_symlink())
        self.assertEqual((kimi_home / "oauth").resolve(), oauth.parent)
        self.assertEqual((kimi_home / "credentials").resolve(), credential.parent)

        for path in (oauth, credential):
            for suffix in ("", ".lock", ".tmp-123"):
                self.assertTrue(any(re.match(pattern, str(path) + suffix)
                                    for pattern in spec.write_patterns))
            sibling = path.with_name("other-account")
            self.assertFalse(any(re.match(pattern, str(sibling))
                                 for pattern in spec.write_patterns))
        for key in SENSITIVE_ENV:
            self.assertNotIn(key, spec.env)
        self.assertNotIn("kimi-provider-secret-fixture", repr(spec))
        self.assertNotIn("kimi-credential-fixture", repr(spec))

    def test_kimi_other_provider_and_missing_auth_require_configuration(self):
        base = self.kimi_auth()
        exe = self.executable()
        with self.assertRaises(ConfigurationRequired):
            build_launch(
                AgentConfig("kimi_code", str(exe), provider="openai", auth_paths=(base,)),
                "work", self.worktree, self.runtime,
            )
        missing = self.root / "missing-fake-auth" / ".kimi-code"
        missing.mkdir(parents=True)
        with self.assertRaises(ConfigurationRequired):
            build_launch(
                AgentConfig("kimi_code", str(exe), auth_paths=(missing,)),
                "work", self.worktree, self.runtime,
            )

    def test_kimi_minimal_metadata_preserves_required_thinking_capabilities(self):
        base = self.kimi_auth()
        exe = self.executable()
        spec = build_launch(AgentConfig("kimi_code", str(exe), auth_paths=(base,)),
                            "public fixture", self.worktree, self.runtime)
        data = tomllib.loads((self.runtime / "kimi-code/config.toml").read_text())
        selected = data["models"][data["default_model"]]
        self.assertIn("always_thinking", selected["capabilities"])
        self.assertEqual(set(data["providers"]), {"managed:kimi-code"})
        self.assertNotIn("Other Provider", str(data))

    def test_claude_uses_only_selected_env_and_review_tools(self):
        settings = self.claude_settings()
        exe = self.executable()
        config = AgentConfig("claude_code", str(exe), auth_paths=(settings,))
        with patch.dict(os.environ, SENSITIVE_ENV, clear=False):
            spec = build_launch(config, "implement", self.worktree, self.runtime)
            review = build_launch(config, "review", self.worktree, self.runtime, review=True)

        self.assertIn("--print", spec.argv)
        self.assertIn("--output-format", spec.argv)
        self.assertIn("--verbose", spec.argv)
        self.assertIn("--safe-mode", spec.argv)
        self.assertIn("--strict-mcp-config", spec.argv)
        self.assertIn('{"mcpServers":{}}', spec.argv)
        self.assertIn("--permission-mode", spec.argv)
        self.assertIn("acceptEdits", spec.argv)
        self.assertNotIn("--dangerously-skip-permissions", spec.argv)
        self.assertLess(spec.argv.index("implement"), spec.argv.index("--tools"))
        self.assertEqual(spec.argv[-7:], [
            "--tools", "Read", "Write", "Edit", "Glob", "Grep", "Bash",
        ])
        self.assertEqual(review.argv[-4:], ["--tools", "Read", "Glob", "Grep"])

        self.assertEqual(spec.env["ANTHROPIC_AUTH_TOKEN"], "claude-provider-secret-fixture")
        self.assertNotIn("ANTHROPIC_API_KEY", spec.env)
        self.assertEqual(spec.env["ANTHROPIC_BASE_URL"], "https://open.bigmodel.cn/api/paas/v4")
        self.assertEqual(spec.env["ANTHROPIC_MODEL"], "glm-5.3-flash[1M]")
        self.assertNotIn("GH_TOKEN", spec.env)
        self.assertTrue(Path(spec.env["CLAUDE_CODE_TMPDIR"]).is_relative_to(self.runtime.resolve()))
        self.assertTrue(Path(spec.env["CLAUDE_CONFIG_DIR"]).is_relative_to(self.runtime.resolve()))
        self.assertNotIn(settings.resolve(), spec.read_roots)
        self.assertNotIn("claude-provider-secret-fixture", repr(spec))
        self.assertNotIn("claude-api-secret-fixture", repr(spec))

    def test_dsh_private_patch_and_existing_opencode_go_token(self):
        config_path = self.dsh_config()
        exe = self.executable()
        config = AgentConfig("dsh", str(exe), auth_paths=(config_path,))
        with patch.dict(os.environ, SENSITIVE_ENV, clear=False):
            spec = build_launch(config, "headless task", self.worktree, self.runtime)

        self.assertEqual(spec.argv[:4], [
            str(exe.resolve()), "--profile", "headless", "--patch",
        ])
        self.assertEqual(spec.argv[-1], "--json")
        patch_path = Path(spec.argv[4])
        self.assertTrue(patch_path.is_relative_to(self.runtime.resolve()))
        self.assertEqual(spec.stdin, b"headless task")
        self.assertEqual(spec.env["OPENCODE_API_KEY"], "dsh-opencode-go-secret-fixture")
        self.assertTrue(Path(spec.env["DSH_HOME"]).is_relative_to(self.runtime.resolve()))
        for key in SENSITIVE_ENV:
            self.assertNotIn(key, spec.env)
        self.assertNotIn("dsh-opencode-go-secret-fixture", repr(spec))
        self.assertNotIn("dsh-unrelated-secret-fixture", repr(spec))
        self.assertNotIn(config_path.resolve(), spec.read_roots)

        patch_data = json.loads(patch_path.read_text(encoding="utf-8"))
        self.assertEqual(len(patch_data), 5)
        self.assertEqual(patch_data[0]["id"], "agent-default-model")
        self.assertEqual(patch_data[0]["config"]["provider"], "opencode-go")
        self.assertEqual(patch_data[0]["config"]["model"], "deepseek-v4.1-flash")
        self.assertEqual(patch_data[0]["config"]["reasoningEffort"], "high")
        self.assertEqual(patch_data[1]["id"], "llm-pi-ai")
        self.assertEqual(patch_data[1]["name"], "@deepseek-ai/dsh-llm-pi-ai")
        model = patch_data[1]["config"]["providers"]["opencode-go"]["models"][0]
        self.assertEqual(model["id"], "deepseek-v4.1-flash")
        self.assertEqual(model["contextWindow"], 1_000_000)
        self.assertEqual(model["input"], ["text", "image"])
        self.assertNotIn("dsh-opencode-go-secret-fixture", patch_path.read_text(encoding="utf-8"))

    def test_dsh_headless_patch_enables_only_native_auth_plugins(self):
        patch = adapters._dsh_patch("deepseek-v4.1-flash")
        flags = {row["id"]: row.get("disabled") for row in patch}
        for name in ("credentials", "authorization", "deepseek-account"):
            self.assertIs(flags[name], False)

    def test_dsh_missing_or_unsupported_route_is_configuration_required(self):
        exe = self.executable()
        with self.assertRaises(ConfigurationRequired):
            build_launch(
                AgentConfig("dsh", str(exe)),
                "work", self.worktree, self.runtime,
            )
        config_path = self.dsh_config(include_token=False)
        with self.assertRaises(ConfigurationRequired):
            build_launch(
                AgentConfig("dsh", str(exe), auth_paths=(config_path,)),
                "work", self.worktree, self.runtime,
            )
        config_path = self.dsh_config()
        with self.assertRaises(ConfigurationRequired):
            build_launch(
                AgentConfig("dsh", str(exe), model="gpt-5", auth_paths=(config_path,)),
                "work", self.worktree, self.runtime,
            )

    def test_agent_config_repr_has_no_secret_fields(self):
        exe = self.executable()
        config = AgentConfig("claude_code", str(exe), model="glm-5.3-flash[1M]")
        self.assertNotIn("token", repr(config).lower())
        self.assertNotIn("secret", repr(config).lower())


class SandboxConstructionTests(_DeliveryTestCase):
    @contextmanager
    def fake_macos_backend(self):
        with patch.object(sandbox.sys, "platform", "darwin"), \
                patch.object(sandbox, "backend_status", return_value=(True, "")), \
                patch.object(sandbox, "_sandbox_executable", return_value="/usr/bin/sandbox-exec"), \
                patch.object(sandbox, "_profile_is_accepted", return_value=True):
            yield

    def test_linux_and_windows_return_config_required_without_claiming_isolation(self):
        for platform in ("linux", "win32"):
            with self.subTest(platform=platform), patch.object(sandbox.sys, "platform", platform):
                with self.assertRaises(IsolationError) as raised:
                    sandbox.sandbox_argv(
                        ["/bin/true"], worktree=self.worktree, runtime=self.runtime,
                    )
                self.assertEqual(str(raised.exception), "config_required")

    def test_unusable_macos_backend_returns_config_required(self):
        with patch.object(sandbox.sys, "platform", "darwin"), \
                patch.object(sandbox, "backend_status", return_value=(False, "sandbox_exec_unavailable")):
            with self.assertRaises(IsolationError) as raised:
                sandbox.sandbox_argv(
                    ["/bin/true"], worktree=self.worktree, runtime=self.runtime,
                )
            self.assertEqual(str(raised.exception), "config_required")

    def test_profile_has_real_read_write_and_network_rules(self):
        auth = self.root / "auth" / "settings.json"
        auth.parent.mkdir()
        auth.write_text("fixture", encoding="utf-8")
        writable = self.root / "writable"
        writable.mkdir()
        pattern = "^" + re.escape(str(writable / "cache.bin")) + ".*$"
        with self.fake_macos_backend():
            argv = sandbox.sandbox_argv(
                [sys.executable, "-c", "pass"],
                worktree=self.worktree,
                runtime=self.runtime,
                read_roots=(auth,),
                write_roots=(writable,),
                write_patterns=(pattern,),
                network=False,
            )
        self.assertTrue(argv[0].endswith("sandbox-exec"))
        self.assertEqual(argv[1], "-p")
        self.assertEqual(argv[3:], [sys.executable, "-c", "pass"])
        profile = argv[2]
        self.assertIn("(version 1)", profile)
        self.assertIn("(allow default)", profile)
        self.assertIn("(deny file-read-data", profile)
        self.assertIn("(deny file-write*", profile)
        self.assertIn("(require-not", profile)
        self.assertIn(f'(subpath "{self.worktree.resolve()}")', profile)
        self.assertIn(f'(subpath "{self.runtime.resolve()}")', profile)
        self.assertIn(f'(literal "{auth.resolve()}")', profile)
        self.assertIn(f'(subpath "{writable.resolve()}")', profile)
        self.assertIn("(regex #", profile)
        self.assertIn("(deny network*)", profile)
        self.assertIn("/Users", profile)
        self.assertIn("/private/tmp", profile)
        self.assertIn("/private/var/folders", profile)
        self.assertIn("/dev/null", profile)

    def test_literal_regex_prefixes_preserve_dot_directories_and_reject_broad_home(self):
        home = sandbox._os_home()
        own = home / ".kimi-code/oauth/kimi-code"
        pattern = "^" + adapters._regex_literal(str(own)) + "[.]"
        self.assertEqual(sandbox._fixed_prefix(pattern), str(own) + ".")
        self.assertEqual(sandbox._validate_pattern(pattern), pattern)
        escaped = "^" + re.escape(str(own)) + "$"
        self.assertEqual(sandbox._fixed_prefix(escaped), str(own))
        with self.assertRaises(IsolationError):
            sandbox._validate_pattern("^" + re.escape(str(home)) + "/.*")

    def test_quantified_or_alternate_prefix_cannot_grant_home_or_review_writes(self):
        home = re.escape(str(sandbox._os_home()))
        for suffix in ("/x?.*", "/[x]*.*", "/[x]+.*", "/x{0,1}.*", "[.]?/.*", "/safe|/.*"):
            with self.subTest(suffix=suffix), self.assertRaises(IsolationError):
                sandbox._validate_pattern("^" + home + suffix)
        pattern = "^" + re.escape(str(self.worktree)) + "[.]?/foo"
        with self.fake_macos_backend(), self.assertRaises(IsolationError):
            sandbox.sandbox_argv([sys.executable, "-c", "pass"], worktree=self.worktree,
                                 runtime=self.runtime, write_patterns=(pattern,), readonly=True)

    def test_partial_home_components_and_intra_path_quantifiers_are_rejected(self):
        home = str(sandbox._os_home())
        for raw in ("^" + re.escape(home[:-1]) + "?", "^" + re.escape(home[:-1]),
                    "^" + re.escape(home[:-1]) + "[x]?/file"):
            with self.subTest(pattern=raw), self.assertRaises(IsolationError):
                sandbox._validate_pattern(raw)
        with self.fake_macos_backend(), self.assertRaises(IsolationError):
            sandbox.sandbox_argv([sys.executable, "-c", "pass"], worktree=self.worktree,
                                 runtime=self.runtime,
                                 write_patterns=("^" + re.escape(str(self.worktree)[:-1]),),
                                 readonly=True)

    def test_unicode_paths_stay_utf8_and_auth_prefixes_are_simple(self):
        auth = self.root / "认证-😀" / "kimi-code.json"
        auth.parent.mkdir()
        auth.write_text("fixture", encoding="utf-8")
        literal = str(auth).replace(".", "[.]")
        exact = "^" + literal + "$"
        prefix = "^" + literal + "[.]"
        with self.fake_macos_backend():
            argv = sandbox.sandbox_argv(
                [sys.executable, "-c", "pass"],
                worktree=self.worktree,
                runtime=self.runtime,
                read_roots=(auth,),
                write_patterns=(exact, prefix),
            )
        profile = argv[2]
        self.assertIn(str(auth), profile.replace("[.]", "."))
        self.assertNotIn("\\u8ba4", profile)
        self.assertNotIn("\\U0001f600", profile)
        self.assertIn(f'(regex #"{prefix}")', profile)

    def test_network_allowed_omits_network_deny(self):
        with self.fake_macos_backend():
            argv = sandbox.sandbox_argv(
                [sys.executable, "-c", "pass"],
                worktree=self.worktree, runtime=self.runtime,
            )
        self.assertNotIn("(deny network*)", argv[2])

    def test_review_profile_refuses_worktree_writes_but_keeps_private_cache(self):
        with self.fake_macos_backend():
            argv = sandbox.sandbox_argv(
                [sys.executable, "-c", "pass"],
                worktree=self.worktree, runtime=self.runtime,
                write_roots=(self.worktree, self.runtime),
                readonly=True,
            )
        profile = argv[2]
        write_section = profile.split("(deny file-write*", 1)[1]
        self.assertNotIn(str(self.worktree.resolve()), write_section)
        self.assertIn(str(self.runtime.resolve()), write_section)
        self.assertIn(f'(subpath "{self.worktree.resolve()}")', profile)  # still readable

    def test_broad_roots_symlink_escape_and_nested_runtime_are_rejected(self):
        link = self.root / "link"
        link.symlink_to(self.worktree, target_is_directory=True)
        broad = [Path("/"), Path("/Users"), Path("/private/tmp"), sandbox._os_home()]
        with self.fake_macos_backend():
            for root in broad:
                with self.subTest(root=root), self.assertRaises(IsolationError):
                    sandbox.sandbox_argv(
                        [sys.executable, "-c", "pass"],
                        worktree=self.worktree, runtime=self.runtime,
                        read_roots=(root,),
                    )
            with self.assertRaises(IsolationError):
                sandbox.sandbox_argv(
                    [sys.executable, "-c", "pass"],
                    worktree=self.worktree, runtime=self.runtime,
                    read_roots=(link,),
                )
            nested = self.worktree / "private-runtime"
            with self.assertRaises(IsolationError):
                sandbox.sandbox_argv(
                    [sys.executable, "-c", "pass"],
                    worktree=self.worktree, runtime=nested,
                )
            with self.assertRaises(IsolationError):
                sandbox.sandbox_argv(
                    [sys.executable, "-c", "pass"],
                    worktree=self.worktree, runtime=self.runtime,
                    write_patterns=("^/Users/.*$",),
                )

    def test_relative_and_malformed_argv_are_rejected(self):
        with self.fake_macos_backend():
            for argv in (["true"], [], ["/bin/true\x00x"]):
                with self.subTest(argv=argv), self.assertRaises(IsolationError):
                    sandbox.sandbox_argv(argv, worktree=self.worktree, runtime=self.runtime)


@unittest.skipUnless(sys.platform == "darwin", "real macOS sandbox-exec canaries require macOS")
class RealMacSandboxCanaryTests(_DeliveryTestCase):
    def setUp(self):
        super().setUp()
        usable, reason = sandbox.backend_status()
        if not usable:
            self.skipTest(f"macOS sandbox-exec backend unavailable: {reason}")
        (self.runtime / "home").mkdir(parents=True, exist_ok=True)
        (self.runtime / "tmp").mkdir(parents=True, exist_ok=True)

    def run_sandboxed(self, code: str, *args: object, readonly: bool = False,
                      network: bool = True, read_roots: tuple[Path, ...] = (),
                      write_roots: tuple[Path, ...] = (),
                      write_patterns: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
        argv = [sys.executable, "-c", code, *(str(arg) for arg in args)]
        sandboxed = sandbox.sandbox_argv(
            argv,
            worktree=self.worktree,
            runtime=self.runtime,
            read_roots=(self.worktree, self.runtime, *read_roots),
            write_roots=(self.worktree, self.runtime, *write_roots),
            write_patterns=write_patterns,
            readonly=readonly,
            network=network,
        )
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(self.runtime / "home"),
            "TMPDIR": str(self.runtime / "tmp"),
            "LANG": "C.UTF-8",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        return subprocess.run(
            sandboxed,
            cwd=str(self.worktree),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_real_read_write_canaries_deny_host_and_other_temp(self):
        own = self.worktree / "owned.txt"
        own.write_text("own", encoding="utf-8")
        host = self.root / "host-public" / "README.md"
        host.parent.mkdir()
        host.write_text("host-public", encoding="utf-8")
        other_temp = tempfile.TemporaryDirectory(prefix="delivery-other-")
        self.addCleanup(other_temp.cleanup)
        other = Path(other_temp.name) / "other.txt"
        other.write_text("other", encoding="utf-8")

        code = """
import json, pathlib, sys
worktree, runtime, host, other = (pathlib.Path(item) for item in sys.argv[1:])
result = {}
(worktree / 'written.txt').write_text('ok')
(runtime / 'cache.txt').write_text('ok')
result['own'] = (worktree / 'owned.txt').read_text()
for label, path in (('host', host), ('other', other)):
    try:
        path.read_text()
        result[label + '_read'] = 'allowed'
    except OSError:
        result[label + '_read'] = 'denied'
    try:
        (path.parent / (label + '-write.txt')).write_text('x')
        result[label + '_write'] = 'allowed'
    except OSError:
        result[label + '_write'] = 'denied'
print(json.dumps(result))
"""
        completed = self.run_sandboxed(code, self.worktree, self.runtime, host, other)
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        result = json.loads(completed.stdout)
        self.assertEqual(result["own"], "own")
        self.assertTrue((self.worktree / "written.txt").is_file())
        self.assertTrue((self.runtime / "cache.txt").is_file())
        self.assertEqual(result["host_read"], "denied")
        self.assertEqual(result["host_write"], "denied")
        self.assertEqual(result["other_read"], "denied")
        self.assertEqual(result["other_write"], "denied")

    def test_real_kimi_auth_canary_allows_only_own_files_and_refresh_siblings(self):
        base = self.kimi_auth()
        exe = self.executable()
        spec = build_launch(
            AgentConfig("kimi_code", str(exe), auth_paths=(base,)),
            "work", self.worktree, self.runtime,
        )
        oauth = base / "oauth" / "kimi-code"
        credential = base / "credentials" / "kimi-code.json"
        original_config = base / "config.toml"
        sibling = base / "oauth" / "other-account"
        sibling.write_text("other-fixture", encoding="utf-8")
        code = """
import json, pathlib, sys
oauth, credential, original_config, sibling = (pathlib.Path(item) for item in sys.argv[1:])
result = {}
for label, path in (('oauth', oauth), ('credential', credential)):
    try:
        path.read_bytes()
        result[label + '_read'] = 'allowed'
    except OSError:
        result[label + '_read'] = 'denied'
for label, path in (('config', original_config), ('sibling', sibling)):
    try:
        path.read_bytes()
        result[label + '_read'] = 'allowed'
    except OSError:
        result[label + '_read'] = 'denied'
    try:
        path.write_text('must-not-change')
        result[label + '_write'] = 'allowed'
    except OSError:
        result[label + '_write'] = 'denied'
for label, path in (
    ('oauth', oauth), ('oauth_lock', pathlib.Path(str(oauth) + '.lock')),
    ('credential_temp', pathlib.Path(str(credential) + '.tmp')),
):
    try:
        path.write_text('refreshed-fixture')
        result[label + '_write'] = 'allowed'
    except OSError:
        result[label + '_write'] = 'denied'
print(json.dumps(result))
"""
        completed = self.run_sandboxed(
            code, oauth, credential, original_config, sibling,
            read_roots=spec.read_roots,
            write_roots=spec.write_roots,
            write_patterns=spec.write_patterns,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        result = json.loads(completed.stdout)
        self.assertEqual(result, {
            "oauth_read": "allowed",
            "credential_read": "allowed",
            "config_read": "denied",
            "config_write": "denied",
            "sibling_read": "denied",
            "sibling_write": "denied",
            "oauth_write": "allowed",
            "oauth_lock_write": "allowed",
            "credential_temp_write": "allowed",
        })
        self.assertEqual(sibling.read_text(encoding="utf-8"), "other-fixture")

    def test_real_review_canary_denies_worktree_writes_but_allows_private_cache(self):
        own = self.worktree / "reviewed.txt"
        own.write_text("reviewed", encoding="utf-8")
        code = """
import pathlib, sys
worktree, runtime = (pathlib.Path(item) for item in sys.argv[1:])
result = {'own_read': (worktree / 'reviewed.txt').read_text()}
try:
    (worktree / 'review-write.txt').write_text('x')
    result['worktree_write'] = 'allowed'
except OSError:
    result['worktree_write'] = 'denied'
try:
    (runtime / 'review-cache.txt').write_text('x')
    result['cache_write'] = 'allowed'
except OSError:
    result['cache_write'] = 'denied'
print(result)
"""
        completed = self.run_sandboxed(
            code, self.worktree, self.runtime, readonly=True,
            write_roots=(self.worktree, self.runtime),
        )
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        self.assertEqual(completed.stdout.strip(), (
            "{'own_read': 'reviewed', 'worktree_write': 'denied', 'cache_write': 'allowed'}"
        ))
        self.assertFalse((self.worktree / "review-write.txt").exists())
        self.assertTrue((self.runtime / "review-cache.txt").is_file())

    def test_real_network_false_blocks_local_connection(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(5)
        port = server.getsockname()[1]
        accepted = []

        def accept() -> None:
            try:
                connection, _ = server.accept()
                accepted.append(True)
                connection.close()
            except OSError:
                pass

        thread = threading.Thread(target=accept, daemon=True)
        thread.start()
        code = """
import socket, sys
client = socket.socket()
client.settimeout(2)
try:
    client.connect(('127.0.0.1', int(sys.argv[1])))
    print('connected')
except OSError:
    print('blocked')
finally:
    client.close()
"""
        completed = self.run_sandboxed(code, port, network=False)
        server.close()
        thread.join(timeout=2)
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        self.assertEqual(completed.stdout.strip(), "blocked")
        self.assertEqual(accepted, [])


if __name__ == "__main__":
    unittest.main()
