"""Closed registry; configuration supplies executable paths, never commands."""

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from .protocol import AgentSpec


AGENTS = MappingProxyType({spec.id: spec for spec in (
    AgentSpec("codex_controller", "codex"),
    AgentSpec("kimi_code", "kimi"),
    AgentSpec("claude_code", "claude"),
    AgentSpec("opencode", "opencode"),
    AgentSpec("zcode", "zcode"),
    AgentSpec("dsh", "dsh"),
)})


class ConfigurationError(ValueError):
    """Intentionally contains no user-supplied values or filesystem paths."""


def validate_config(config: object) -> dict[str, str]:
    if not isinstance(config, Mapping) or any(key not in AGENTS for key in config):
        raise ConfigurationError("invalid_agent_configuration")
    result = {}
    for key, value in config.items():
        if (not isinstance(value, str) or not value or "\x00" in value
                or "\n" in value or "\r" in value or not Path(value).is_absolute()
                or " --" in value):
            raise ConfigurationError("invalid_executable_path")
        result[key] = value
    return result
