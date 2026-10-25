"""Local references only: no token values or arbitrary command configuration."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat

from ..execution import AgentConfig
from ..execution.adapters import KNOWN_AGENT_IDS

DEFAULT_REPOSITORIES = ("lakii2982/project-hub",)
LIMIT = 64 * 1024
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\Z")
FIELDS = {"executable", "model", "auth_paths", "provider", "base_url", "runtime_paths"}


def load_config(path=None):
    """Return trusted repository choices and same-client credential references."""
    if path is None:
        try:
            import pwd
        except ImportError:
            raise ValueError("unsupported_platform") from None
        candidate = Path(pwd.getpwuid(os.getuid()).pw_dir) / ".project-hub-agentd/config.json"
        if not candidate.exists() and not candidate.is_symlink():
            return DEFAULT_REPOSITORIES, None
        path = candidate
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > LIMIT:
            raise ValueError("invalid_local_configuration")
        raw = handle.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ValueError("invalid_local_configuration")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ValueError("invalid_local_configuration") from None
    if not isinstance(data, dict) or set(data) - {"repositories", "agents"}:
        raise ValueError("invalid_local_configuration")
    repositories = data.get("repositories", list(DEFAULT_REPOSITORIES))
    if (not isinstance(repositories, list) or not 1 <= len(repositories) <= 32
            or any(not isinstance(item, str) or not SLUG.fullmatch(item) for item in repositories)):
        raise ValueError("invalid_local_configuration")
    values = data.get("agents", {})
    if not isinstance(values, dict) or set(values) - KNOWN_AGENT_IDS:
        raise ValueError("invalid_local_configuration")
    configs = {}
    for agent, value in values.items():
        if not isinstance(value, dict) or set(value) - FIELDS or "executable" not in value:
            raise ValueError("invalid_local_configuration")
        options = dict(value)
        for field in ("auth_paths", "runtime_paths"):
            entries = options.get(field, [])
            if not isinstance(entries, list) or any(not isinstance(entry, str) for entry in entries):
                raise ValueError("invalid_local_configuration")
            options[field] = tuple(Path(entry) for entry in entries)
        configs[agent] = AgentConfig(agent_id=agent, **options)
    return tuple(repositories), configs or None
