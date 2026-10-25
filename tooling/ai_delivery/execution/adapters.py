"""Fixed adapters for the four local delivery CLIs.

The public surface is deliberately small: :class:`AgentConfig` validates a
trusted controller's local choices, and :func:`build_launch` turns one of them
into a :class:`LaunchSpec`.  Provider commands are fixed here; a task prompt or
an Issue can never contribute argv, shell text or an endpoint.

Secrets are read only from an explicitly trusted settings/config file, only in
this trusted process, and are placed only into the child environment.  They are
never copied into the worktree, the generated provider config, an argv value or
a dataclass ``repr``.  The OS sandbox itself lives in
``tooling.ai_delivery.execution.sandbox``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import sys
import sysconfig
import tomllib
from urllib.parse import urlsplit


KNOWN_AGENT_IDS = frozenset({"kimi_code", "opencode", "dsh", "claude_code"})
AGENT_IDS = KNOWN_AGENT_IDS

_COMMANDS = {
    "kimi_code": "kimi",
    "opencode": "opencode",
    "dsh": "dsh",
    "claude_code": "claude",
}
_DEFAULT_MODELS = {
    "kimi_code": "k3.1-pro",
    "opencode": "opencode/deepseek-v4.1-flash-free",
    "dsh": "deepseek-v4.1-flash",
    "claude_code": None,
}
DEFAULT_MODELS = dict(_DEFAULT_MODELS)

_MAX_PROMPT = 1_000_000
_MAX_SETTINGS = 1_048_576
_MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-\[\]]{0,159}\Z")
_PROVIDER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@+/\-]{0,159}\Z")
_ENV_KEY_RE = re.compile(r"[A-Z_][A-Z0-9_]{0,127}\Z")
_TOML_KEY_RE = re.compile(r"[A-Za-z0-9_\-]+\Z")


class AdapterError(ValueError):
    """Invalid trusted configuration; messages never contain secret values."""


class ConfigurationRequired(RuntimeError):
    """The selected CLI/model cannot be launched safely with local auth."""


ConfigRequired = ConfigurationRequired


def _os_home() -> Path:
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=True)
    except (KeyError, OSError):
        raise AdapterError("local_home_unavailable") from None


@dataclass(frozen=True)
class AgentConfig:
    """Trusted, local-only selection of one known CLI.

    ``auth_paths`` are settings/credential references for the *same* provider
    only.  They are read by this trusted process, never handed to the worker as
    a directory to browse.  ``provider``/``base_url``/``runtime_paths`` are the
    optional trusted startup settings.  No token or arbitrary argv field exists.
    """

    agent_id: str
    executable: str
    model: str | None = None
    auth_paths: tuple[Path, ...] = ()
    provider: str | None = None
    base_url: str | None = None
    runtime_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.agent_id, str) or self.agent_id not in KNOWN_AGENT_IDS:
            raise AdapterError("unknown_agent_id")
        object.__setattr__(self, "executable", _validated_executable(self.executable))
        object.__setattr__(self, "model", _validated_model(self.model))
        object.__setattr__(self, "auth_paths", _validated_auth_paths(self.agent_id, self.auth_paths))
        object.__setattr__(self, "provider", _validated_provider(self.provider))
        object.__setattr__(self, "base_url", _validated_base_url(self.base_url))
        object.__setattr__(self, "runtime_paths", _validated_runtime_paths(self.runtime_paths))

    @classmethod
    def discover(cls, agent_id: str, *, model: str | None = None) -> "AgentConfig":
        """Resolve the installed CLI and current UID's default auth references.

        Only references are discovered.  The native clients keep using their
        existing account/service; no token is copied and no new account is made.
        """

        if not isinstance(agent_id, str) or agent_id not in KNOWN_AGENT_IDS:
            raise AdapterError("unknown_agent_id")
        executable = shutil.which(_COMMANDS[agent_id])
        if not executable:
            raise ConfigurationRequired("agent_not_installed")
        return cls(
            agent_id=agent_id,
            executable=executable,
            model=model,
            auth_paths=_default_auth_paths(agent_id),
        )

    @classmethod
    def for_local_user(cls, agent_id: str, *, model: str | None = None) -> "AgentConfig":
        return cls.discover(agent_id, model=model)

    @classmethod
    def local(cls, agent_id: str, *, model: str | None = None) -> "AgentConfig":
        return cls.discover(agent_id, model=model)


@dataclass(frozen=True)
class LaunchSpec:
    """Fully constructed, still unsandboxed launch for one fixed adapter.

    The runner applies :func:`tooling.ai_delivery.execution.sandbox.sandbox_argv`
    with ``read_roots``/``write_roots``/``write_patterns`` and ``review`` before
    handing ``argv``/``env``/``stdin`` to the bounded process runner.
    """

    argv: list[str]
    env: dict[str, str] = field(repr=False, compare=False)
    stdin: bytes | None
    cwd: Path
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]
    write_patterns: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Validation helpers


def _validated_executable(value: object) -> str:
    if not isinstance(value, str) or not value or "\x00" in value or "\n" in value or "\r" in value:
        raise AdapterError("invalid_executable")
    raw = Path(value)
    if not raw.is_absolute():
        raise AdapterError("invalid_executable")
    try:
        resolved = raw.resolve(strict=True)
    except OSError:
        raise AdapterError("invalid_executable") from None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise AdapterError("invalid_executable")
    return str(resolved)


def _validated_model(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _MODEL_RE.fullmatch(value):
        raise AdapterError("invalid_model")
    return value


def _validated_provider(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _PROVIDER_RE.fullmatch(value):
        raise AdapterError("invalid_provider")
    return value


def _validated_base_url(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 2048:
        raise AdapterError("invalid_base_url")
    if any(char in value for char in "\x00\r\n"):
        raise AdapterError("invalid_base_url")
    try:
        parts = urlsplit(value)
    except ValueError:
        raise AdapterError("invalid_base_url") from None
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment):
        raise AdapterError("invalid_base_url")
    return value


def _coerce_path_sequence(value: object, name: str) -> tuple[Path, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes, Path)) or not hasattr(value, "__iter__"):
        raise AdapterError(f"invalid_{name}")
    result: list[Path] = []
    for item in value:  # type: ignore[union-attr]
        if not isinstance(item, (str, Path)) or isinstance(item, bytes):
            raise AdapterError(f"invalid_{name}")
        text = str(item)
        if not text or "\x00" in text or "\n" in text or "\r" in text:
            raise AdapterError(f"invalid_{name}")
        path = Path(text)
        if not path.is_absolute():
            raise AdapterError(f"invalid_{name}")
        if path.is_symlink():
            raise AdapterError(f"invalid_{name}")
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            raise AdapterError(f"invalid_{name}") from None
        result.append(resolved)
    return tuple(result)


def _validated_auth_paths(agent_id: str, value: object) -> tuple[Path, ...]:
    paths = _coerce_path_sequence(value, "auth_paths")
    if not paths:
        return ()
    if agent_id == "opencode":
        raise AdapterError("auth_paths_not_supported")
    for path in paths:
        _reject_broad_path(path)
        if agent_id == "kimi_code":
            valid = (
                path.name in {"config.toml", ".kimi-code"}
                or (path.name == "kimi-code" and path.parent.name == "oauth")
                or (path.name == "kimi-code.json" and path.parent.name == "credentials")
            )
        elif agent_id == "claude_code":
            valid = path.name in {"settings.json", ".claude"}
        else:
            valid = path.name in {"config.toml", ".kimi-code"}
        if not valid:
            raise AdapterError("unexpected_auth_path")
    return paths


def _validated_runtime_paths(value: object) -> tuple[Path, ...]:
    paths = _coerce_path_sequence(value, "runtime_paths")
    for path in paths:
        _reject_broad_path(path)
    return paths


def _reject_broad_path(path: Path) -> None:
    home = _os_home()
    forbidden = {
        Path("/"), Path("/Users"), Path("/private"), Path("/private/tmp"),
        Path("/private/var"), Path("/private/var/tmp"), Path("/private/var/folders"),
        Path("/tmp"), Path("/var"), Path("/var/tmp"), Path("/var/folders"), home,
    }
    if path in forbidden:
        raise AdapterError("overly_broad_path")
    for candidate in forbidden:
        if candidate != path and candidate.is_relative_to(path):
            raise AdapterError("overly_broad_path")


def _default_auth_paths(agent_id: str) -> tuple[Path, ...]:
    home = _os_home()
    candidates: tuple[Path, ...]
    if agent_id == "kimi_code":
        base = home / ".kimi-code"
        candidates = (base,) if base.is_dir() else ()
    elif agent_id == "claude_code":
        candidate = home / ".claude" / "settings.json"
        candidates = (candidate,) if candidate.is_file() else ()
    elif agent_id == "dsh":
        candidate = home / ".kimi-code" / "config.toml"
        candidates = (candidate,) if candidate.is_file() else ()
    else:
        candidates = ()
    return candidates


# ---------------------------------------------------------------------------
# Runtime / environment setup


def _lexical_absolute(value: object, name: str) -> Path:
    if not isinstance(value, (str, Path)) or isinstance(value, bytes):
        raise AdapterError(f"invalid_{name}")
    text = str(value)
    if not text or "\x00" in text or "\n" in text or "\r" in text:
        raise AdapterError(f"invalid_{name}")
    path = Path(text)
    if not path.is_absolute() or path.is_symlink():
        raise AdapterError(f"invalid_{name}")
    return path


def _ensure_private_dir(path: Path) -> Path:
    if path.is_symlink():
        raise AdapterError("symlink_runtime_path")
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
        resolved = path.resolve(strict=True)
    except OSError:
        raise AdapterError("runtime_unavailable") from None
    if not resolved.is_dir():
        raise AdapterError("runtime_unavailable")
    return resolved


def _prepare_runtime(worktree: object, runtime: object) -> tuple[Path, Path]:
    worktree_path = _lexical_absolute(worktree, "worktree")
    runtime_path = _lexical_absolute(runtime, "runtime")
    try:
        worktree_resolved = worktree_path.resolve(strict=True)
    except OSError:
        raise AdapterError("worktree_unavailable") from None
    if not worktree_resolved.is_dir():
        raise AdapterError("worktree_unavailable")
    runtime_resolved = _ensure_private_dir(runtime_path)
    _reject_broad_path(worktree_resolved)
    _reject_broad_path(runtime_resolved)
    if (worktree_resolved == runtime_resolved
            or worktree_resolved.is_relative_to(runtime_resolved)
            or runtime_resolved.is_relative_to(worktree_resolved)):
        raise AdapterError("worktree_runtime_overlap")

    for child in ("home", "tmp", "kimi-code", "dsh-home", "claude-config", "claude-tmp",
                  "xdg", "xdg/config", "xdg/cache", "xdg/data", "xdg/state", "xdg/runtime"):
        _ensure_private_dir(runtime_resolved / child)
    return worktree_resolved, runtime_resolved


def _safe_path_env() -> str:
    value = os.environ.get("PATH", os.defpath)
    if not isinstance(value, str) or "\x00" in value or "\n" in value or not value:
        return os.defpath
    return value


def _base_env(runtime: Path) -> dict[str, str]:
    env = {
        "PATH": _safe_path_env(),
        "HOME": str(runtime / "home"),
        "XDG_CONFIG_HOME": str(runtime / "xdg" / "config"),
        "XDG_CACHE_HOME": str(runtime / "xdg" / "cache"),
        "XDG_DATA_HOME": str(runtime / "xdg" / "data"),
        "XDG_STATE_HOME": str(runtime / "xdg" / "state"),
        "XDG_RUNTIME_DIR": str(runtime / "xdg" / "runtime"),
        "XDG_RUNTIME_HOME": str(runtime / "xdg" / "runtime"),
        "TMPDIR": str(runtime / "tmp"),
        "TMP": str(runtime / "tmp"),
        "TEMP": str(runtime / "tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_CONFIG_NOSYSTEM": "1",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return env


def _set_env(env: dict[str, str], key: str, value: object) -> None:
    if not isinstance(key, str) or not _ENV_KEY_RE.fullmatch(key):
        raise AdapterError("invalid_environment")
    if not isinstance(value, str) or not value or len(value) > 16384:
        raise AdapterError("invalid_environment")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise AdapterError("invalid_environment")
    env[key] = value


def _write_private(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        path.chmod(0o600)
    except OSError:
        raise AdapterError("private_write_failed") from None


def _read_limited(path: Path) -> bytes:
    try:
        if path.stat().st_size > _MAX_SETTINGS:
            raise ConfigurationRequired("settings_too_large")
        with path.open("rb") as handle:
            data = handle.read(_MAX_SETTINGS + 1)
    except ConfigurationRequired:
        raise
    except OSError:
        raise ConfigurationRequired("settings_unavailable") from None
    if len(data) > _MAX_SETTINGS:
        raise ConfigurationRequired("settings_too_large")
    return data


def _load_toml(path: Path) -> dict:
    try:
        data = tomllib.loads(_read_limited(path).decode("utf-8"))
    except ConfigurationRequired:
        raise
    except (UnicodeError, tomllib.TOMLDecodeError):
        raise ConfigurationRequired("settings_invalid") from None
    if not isinstance(data, dict):
        raise ConfigurationRequired("settings_invalid")
    return data


def _load_json(path: Path) -> dict:
    try:
        data = json.loads(_read_limited(path).decode("utf-8"))
    except ConfigurationRequired:
        raise
    except (UnicodeError, ValueError):
        raise ConfigurationRequired("settings_invalid") from None
    if not isinstance(data, dict):
        raise ConfigurationRequired("settings_invalid")
    return data


def _safe_value(value: object, *, maximum: int = 8192) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ConfigurationRequired("settings_invalid")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ConfigurationRequired("settings_invalid")
    return value


def _validated_base_url_or_required(value: object) -> str:
    try:
        checked = _validated_base_url(value)
    except AdapterError:
        raise ConfigurationRequired("base_url_invalid") from None
    if checked is None:
        raise ConfigurationRequired("base_url_required")
    return checked


def _validated_model_or_required(value: object) -> str:
    try:
        checked = _validated_model(value)
    except AdapterError:
        raise ConfigurationRequired("model_invalid") from None
    if checked is None:
        raise ConfigurationRequired("model_required")
    return checked


def _python_read_roots() -> list[Path]:
    candidates: list[object] = [
        sys.executable,
        sys.base_prefix,
        sys.prefix,
    ]
    try:
        paths = sysconfig.get_paths()
        candidates.extend((paths.get("purelib"), paths.get("platlib")))
    except (KeyError, OSError):
        pass
    roots: list[Path] = []
    for candidate in candidates:
        if not isinstance(candidate, (str, Path)) or not str(candidate):
            continue
        try:
            path = Path(candidate)
            if path.exists():
                roots.append(path.resolve(strict=True))
        except OSError:
            continue
    return roots


def _path_read_roots() -> list[Path]:
    roots: list[Path] = []
    home = _os_home()
    for entry in _safe_path_env().split(os.pathsep):
        if not entry:
            continue
        try:
            path = Path(entry)
            if not path.is_absolute() or not path.exists():
                continue
            resolved = path.resolve(strict=True)
            if resolved == home or not resolved.is_dir():
                continue
            _reject_broad_path(resolved)
            roots.append(resolved)
        except (OSError, AdapterError):
            continue
    return roots


def _unique_paths(values: object) -> tuple[Path, ...]:
    result: list[Path] = []
    seen: set[Path] = set()
    for value in values:  # type: ignore[union-attr]
        try:
            path = Path(value).resolve(strict=True)
        except OSError:
            continue
        if path not in seen:
            seen.add(path)
            result.append(path)
    return tuple(result)


# ---------------------------------------------------------------------------
# Adapter-specific launch construction


def _kimi_auth_layout(config: AgentConfig) -> tuple[Path, Path, Path]:
    base = _os_home() / ".kimi-code"
    oauth = base / "oauth" / "kimi-code"
    credential = base / "credentials" / "kimi-code.json"
    source = base / "config.toml"
    for path in config.auth_paths:
        if path.name == "config.toml":
            source = path
        elif path.name == "kimi-code.json":
            credential = path
        elif path.name == "kimi-code" and path.parent.name == "oauth":
            oauth = path
        elif path.name == ".kimi-code" and path.is_dir():
            base = path
            oauth = base / "oauth" / "kimi-code"
            credential = base / "credentials" / "kimi-code.json"
            source = base / "config.toml"
    if (oauth.is_symlink() or credential.is_symlink() or source.is_symlink()
            or not oauth.is_file() or not credential.is_file()):
        raise ConfigurationRequired("kimi_auth_required")
    if not source.is_file():
        raise ConfigurationRequired("kimi_settings_required")
    try:
        return oauth.resolve(strict=True), credential.resolve(strict=True), source.resolve(strict=True)
    except OSError:
        raise ConfigurationRequired("kimi_auth_required") from None


def _kimi_metadata(config: AgentConfig, source: Path) -> tuple[str, str, str, dict]:
    provider_name = config.provider or "managed:kimi-code"
    if provider_name != "managed:kimi-code":
        raise ConfigurationRequired("kimi_provider_unsupported")
    data = _load_toml(source)
    model = config.model
    if model is None:
        try:
            model = _validated_model_or_required(data.get("default_model"))
        except ConfigurationRequired:
            raise ConfigurationRequired("kimi_model_invalid") from None
    providers = data.get("providers")
    provider = providers.get(provider_name) if isinstance(providers, dict) else None
    if not isinstance(provider, dict) or provider.get("type") != "kimi":
        raise ConfigurationRequired("kimi_provider_unsupported")
    oauth = provider.get("oauth")
    if (not isinstance(oauth, dict) or oauth.get("storage") != "file"
            or oauth.get("key") != "oauth/kimi-code"):
        raise ConfigurationRequired("kimi_oauth_unsupported")
    models = data.get("models")
    selected = models.get(model) if isinstance(models, dict) else None
    if not isinstance(selected, dict) or selected.get("provider") != provider_name:
        raise ConfigurationRequired("kimi_model_invalid")
    try:
        model_id = _validated_model_or_required(selected.get("model"))
    except ConfigurationRequired:
        raise ConfigurationRequired("kimi_model_invalid") from None
    context_size = selected.get("max_context_size")
    if type(context_size) is not int or context_size <= 0:
        raise ConfigurationRequired("kimi_model_invalid")
    base_url = config.base_url
    if base_url is None:
        base_url = _validated_base_url_or_required(provider.get("base_url"))
    settings = {"max_context_size": context_size}
    for key in ("capabilities", "support_efforts"):
        if key in selected:
            values = selected[key]
            if (not isinstance(values, list) or len(values) > 32
                    or any(not isinstance(value, str)
                           or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value) for value in values)):
                raise ConfigurationRequired("kimi_model_invalid")
            settings[key] = list(values)
    if "default_effort" in selected:
        value = selected["default_effort"]
        if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value):
            raise ConfigurationRequired("kimi_model_invalid")
        settings["default_effort"] = value
    return model, base_url, model_id, settings


def _toml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_key(value: str) -> str:
    if _TOML_KEY_RE.fullmatch(value):
        return value
    return _toml_quote(value)


def _kimi_config_text(model: str, base_url: str, model_id: str, settings: dict) -> str:
    provider = _toml_key("managed:kimi-code")
    lines = [
        f"default_model = {_toml_quote(model)}",
        "",
        f"[providers.{provider}]",
        'type = "kimi"',
        f"base_url = {_toml_quote(base_url)}",
        "",
        f"[providers.{provider}.oauth]",
        'storage = "file"',
        'key = "oauth/kimi-code"',
        "",
        f"[models.{_toml_key(model)}]",
        'provider = "managed:kimi-code"',
        f"model = {_toml_quote(model_id)}",
        f"max_context_size = {settings['max_context_size']}",
    ]
    for key in ("capabilities", "support_efforts"):
        if key in settings:
            lines.append(key + " = [" + ", ".join(_toml_quote(value) for value in settings[key]) + "]")
    if "default_effort" in settings:
        lines.append("default_effort = " + _toml_quote(settings["default_effort"]))
    return "\n".join(lines) + "\n"


def _link_reference(link: Path, target: Path) -> None:
    if link.is_symlink():
        try:
            if link.resolve(strict=True) == target:
                return
        except OSError:
            pass
        link.unlink()
    elif link.exists():
        raise AdapterError("private_auth_link_conflict")
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        raise AdapterError("private_auth_link_failed") from None


def _link_auth_parent(link: Path, target: Path) -> None:
    if link.is_dir() and not link.is_symlink():
        try:
            link.rmdir()
        except OSError:
            raise AdapterError("private_auth_link_conflict") from None
    _link_reference(link, target)


def _regex_literal(value: str) -> str:
    result: list[str] = []
    for char in value:
        if char in ".+()[]{}^$|*?\\":
            if char == "]":
                result.append("[]]")
            elif char == "[":
                result.append("[[]")
            elif char == "\\":
                result.append("[\\\\]")
            else:
                result.append("[" + char + "]")
        else:
            result.append(char)
    return "".join(result)


def _launch_kimi(config: AgentConfig, prompt: str, worktree: Path, runtime: Path,
                 review: bool) -> tuple[list[str], dict[str, str], bytes | None, list[Path], list[Path], list[str]]:
    oauth, credential, source = _kimi_auth_layout(config)
    model, base_url, model_id, settings = _kimi_metadata(config, source)

    kimi_home = _ensure_private_dir(runtime / "kimi-code")
    _ensure_private_dir(kimi_home / "oauth")
    _ensure_private_dir(kimi_home / "credentials")
    _write_private(
        kimi_home / "config.toml",
        _kimi_config_text(model, base_url, model_id, settings).encode("utf-8"),
    )
    _link_auth_parent(kimi_home / "oauth", oauth.parent)
    _link_auth_parent(kimi_home / "credentials", credential.parent)

    argv = [config.executable, "-p", prompt, "--output-format", "stream-json", "--model", model]
    env: dict[str, str] = {"KIMI_CODE_HOME": str(kimi_home)}

    patterns: list[str] = []
    for path in (oauth, credential):
        literal = _regex_literal(str(path))
        patterns.extend(("^" + literal + "$", "^" + literal + "[.]"))
    return argv, env, None, [oauth, credential], [], patterns


def _claude_settings(config: AgentConfig) -> Path:
    home = _os_home()
    settings = home / ".claude" / "settings.json"
    for path in config.auth_paths:
        if path.name == "settings.json":
            settings = path
        elif path.name == ".claude" and path.is_dir():
            settings = path / "settings.json"
    if not settings.is_file():
        raise ConfigurationRequired("claude_settings_required")
    return settings


def _launch_claude(config: AgentConfig, prompt: str, worktree: Path, runtime: Path,
                   review: bool) -> tuple[list[str], dict[str, str], bytes | None, list[Path], list[Path], list[str]]:
    settings = _claude_settings(config)
    data = _load_json(settings)
    values = data.get("env")
    if not isinstance(values, dict):
        raise ConfigurationRequired("claude_env_required")

    token_key = None
    token = None
    for key in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"):
        candidate = values.get(key)
        if candidate is not None:
            token_key = key
            token = _safe_value(candidate)
            break
    if token_key is None or token is None:
        raise ConfigurationRequired("claude_auth_required")

    base_url = config.base_url
    if base_url is None:
        base_url = _validated_base_url_or_required(values.get("ANTHROPIC_BASE_URL"))
    model = config.model
    if model is None:
        selected = values.get("ANTHROPIC_MODEL")
        if selected is None:
            selected = data.get("model")
        if selected is not None:
            model = _validated_model_or_required(selected)

    claude_config = _ensure_private_dir(runtime / "claude-config")
    claude_tmp = _ensure_private_dir(runtime / "claude-tmp")
    env: dict[str, str] = {
        "ANTHROPIC_BASE_URL": base_url,
        token_key: token,
        "CLAUDE_CONFIG_DIR": str(claude_config),
        "CLAUDE_CODE_TMPDIR": str(claude_tmp),
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    if model is not None:
        env["ANTHROPIC_MODEL"] = model

    tools = (["Read", "Glob", "Grep"] if review
             else ["Read", "Write", "Edit", "Glob", "Grep", "Bash"])
    argv = [
        config.executable,
        "--print",
        prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--safe-mode",
        "--strict-mcp-config",
        "--mcp-config", '{"mcpServers":{}}',
        "--permission-mode", "acceptEdits",
    ]
    if config.model is not None:
        argv.extend(("--model", config.model))
    # The prompt must precede this variadic option; each tool is its own value.
    argv.append("--tools")
    argv.extend(tools)
    return argv, env, None, [], [], []


def _opencode_model(config: AgentConfig) -> str:
    return config.model or _DEFAULT_MODELS["opencode"]


def _launch_opencode(config: AgentConfig, prompt: str, worktree: Path, runtime: Path,
                     review: bool) -> tuple[list[str], dict[str, str], bytes | None, list[Path], list[Path], list[str]]:
    if config.auth_paths:
        raise AdapterError("opencode_auth_paths_not_supported")
    model = _opencode_model(config)
    argv = [
        config.executable, "run", "--standalone", "--format", "json",
        "--model", model, "--", prompt,
    ]
    env = {
        # The verified free model needs a clean, private OpenCode state.
        "OPENCODE_DISABLE_AUTOUPDATE": "1",
        "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "1",
        "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1",
    }
    return argv, env, None, [], [], []


def _dsh_source(config: AgentConfig) -> Path:
    home = _os_home()
    source = home / ".kimi-code" / "config.toml"
    for path in config.auth_paths:
        if path.name == "config.toml":
            source = path
        elif path.name == ".kimi-code" and path.is_dir():
            source = path / "config.toml"
    if not source.is_file():
        raise ConfigurationRequired("dsh_opencode_go_auth_required")
    return source


def _dsh_token(source: Path) -> str:
    data = _load_toml(source)
    providers = data.get("providers")
    if not isinstance(providers, dict):
        raise ConfigurationRequired("dsh_opencode_go_auth_required")
    provider = providers.get("OpenCode Go")
    if not isinstance(provider, dict):
        raise ConfigurationRequired("dsh_opencode_go_auth_required")
    token = provider.get("api_key")
    return _safe_value(token)


def _dsh_patch(model: str) -> list[dict]:
    return [
        {
            "id": "agent-default-model",
            "config": {
                "provider": "opencode-go",
                "model": model,
                "reasoningEffort": "high",
            },
        },
        {
            "id": "llm-pi-ai",
            "name": "@deepseek-ai/dsh-llm-pi-ai",
            "config": {
                "providers": {
                    "opencode-go": {
                        "models": [
                            {
                                "id": model,
                                "name": "DeepSeek V4.1 Flash",
                                "contextWindow": 1_000_000,
                                "input": ["text", "image"],
                            }
                        ]
                    }
                }
            },
        },
        {"id": "credentials", "disabled": False},
        {"id": "authorization", "disabled": False},
        {"id": "deepseek-account", "disabled": False},
    ]


def _launch_dsh(config: AgentConfig, prompt: str, worktree: Path, runtime: Path,
                review: bool) -> tuple[list[str], dict[str, str], bytes | None, list[Path], list[Path], list[str]]:
    source = _dsh_source(config)
    token = _dsh_token(source)
    model = config.model or _DEFAULT_MODELS["dsh"]
    if model != _DEFAULT_MODELS["dsh"]:
        # Only the verified opencode-go route is used; no unknown paid fallback.
        raise ConfigurationRequired("dsh_model_unsupported")

    dsh_home = _ensure_private_dir(runtime / "dsh-home")
    patch_path = runtime / "dsh-patch.json"
    _write_private(
        patch_path,
        json.dumps(_dsh_patch(model), ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
    )
    argv = [config.executable, "--profile", "headless", "--patch", str(patch_path), "--json"]
    env = {
        "DSH_HOME": str(dsh_home),
        "OPENCODE_API_KEY": token,
        "DSH_TELEMETRY_DISABLED": "1",
    }
    return argv, env, prompt.encode("utf-8"), [], [], []


# ---------------------------------------------------------------------------
# Public launch builder


def build_launch(config: AgentConfig, prompt: str, worktree: object, runtime: object,
                 review: bool = False) -> LaunchSpec:
    """Build a fixed adapter launch.  This does not execute or sandbox it."""

    if not isinstance(config, AgentConfig):
        raise AdapterError("invalid_agent_config")
    if not isinstance(prompt, str) or not prompt or "\x00" in prompt or len(prompt) > _MAX_PROMPT:
        raise AdapterError("invalid_prompt")
    if not isinstance(review, bool):
        raise AdapterError("invalid_review_flag")

    worktree_path, runtime_path = _prepare_runtime(worktree, runtime)
    if config.agent_id == "kimi_code":
        adapter = _launch_kimi
    elif config.agent_id == "claude_code":
        adapter = _launch_claude
    elif config.agent_id == "opencode":
        adapter = _launch_opencode
    else:
        adapter = _launch_dsh

    argv, extra_env, stdin, extra_read, extra_write, patterns = adapter(
        config, prompt, worktree_path, runtime_path, review
    )
    env = _base_env(runtime_path)
    for key, value in extra_env.items():
        _set_env(env, key, value)

    common_read = [worktree_path, runtime_path, Path(config.executable)]
    common_read.extend(extra_read)
    common_read.extend(_python_read_roots())
    common_read.extend(_path_read_roots())
    common_read.extend(config.runtime_paths)

    common_write = [runtime_path]
    if not review:
        common_write.append(worktree_path)
    common_write.extend(extra_write)

    return LaunchSpec(
        argv=list(argv),
        env=env,
        stdin=stdin,
        cwd=worktree_path,
        read_roots=_unique_paths(common_read),
        write_roots=_unique_paths(common_write),
        write_patterns=tuple(patterns),
    )


__all__ = [
    "AGENT_IDS",
    "AdapterError",
    "AgentConfig",
    "ConfigRequired",
    "ConfigurationRequired",
    "DEFAULT_MODELS",
    "KNOWN_AGENT_IDS",
    "LaunchSpec",
    "build_launch",
]
