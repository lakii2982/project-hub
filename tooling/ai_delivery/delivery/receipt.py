"""Proof-to-final-source binding checks for delivery receipts.

Pure standard library. No IO, no network.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping

BINDING_FIELDS = (
    "run_id",
    "task_id",
    "repository",
    "base_sha",
    "contract_digest",
    "diff_digest",
)

_TASK_ID_RE = re.compile(r"PH-[0-9]+")
_REPOSITORY_RE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9._-]{1,100}"
)
_SHA40_RE = re.compile(r"[0-9a-f]{40}")
_DIGEST64_RE = re.compile(r"[0-9a-f]{64}")


def _valid_run_id(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def _valid_repository(value: str) -> bool:
    if not _REPOSITORY_RE.fullmatch(value):
        return False
    owner, name = value.split("/", 1)
    if name in (".", "..") or name.endswith(".git"):
        return False
    return not any(ch in value for ch in "\\\x00")


def _valid_task_id(value: str) -> bool:
    return _TASK_ID_RE.fullmatch(value) is not None


_VALIDATORS = {
    "run_id": _valid_run_id,
    "task_id": _valid_task_id,
    "repository": _valid_repository,
    "base_sha": lambda v: _SHA40_RE.fullmatch(v) is not None,
    "contract_digest": lambda v: _DIGEST64_RE.fullmatch(v) is not None,
    "diff_digest": lambda v: _DIGEST64_RE.fullmatch(v) is not None,
}


def _normalized(value: object) -> dict[str, str] | None:
    if not isinstance(value, Mapping):
        return None
    out: dict[str, str] = {}
    for field in BINDING_FIELDS:
        item = value.get(field)
        if not isinstance(item, str) or not item:
            return None
        if not _VALIDATORS[field](item):
            return None
        out[field] = item
    return out


def same_binding(left: object, right: object) -> bool:
    """True only when both bindings are valid and identical on every field."""
    first = _normalized(left)
    if first is None:
        return False
    second = _normalized(right)
    if second is None:
        return False
    return all(first[field] == second[field] for field in BINDING_FIELDS)
