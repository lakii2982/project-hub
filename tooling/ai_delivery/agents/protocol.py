"""Protocol declarations only; PH-100 does not implement task execution."""

from dataclasses import dataclass
from typing import Literal, TypedDict


AgentId = Literal["codex_controller", "kimi_code", "claude_code", "opencode", "zcode", "dsh"]
Capability = Literal["verified", "unverified", "unsupported"]
ProbeStatus = Literal["missing", "probe_failed", "probe_verified", "config_required"]


class Capabilities(TypedDict):
    noninteractive: Capability
    structured_output: Capability
    resume: Capability
    cancel: Capability


class ProbeResult(TypedDict):
    id: AgentId
    available: bool
    executable: str | None
    version: str | None
    status: ProbeStatus
    capabilities: Capabilities
    diagnostics: list[str]


class DoctorResult(TypedDict):
    schema_version: Literal[1]
    controller: ProbeResult
    agents: list[ProbeResult]


@dataclass(frozen=True)
class AgentSpec:
    id: AgentId
    command: str
    version_args: tuple[str, ...] = ("--version",)


def unverified_capabilities() -> Capabilities:
    # A version string proves none of the execution capabilities.
    return {"noninteractive": "unverified", "structured_output": "unverified",
            "resume": "unverified", "cancel": "unverified"}
