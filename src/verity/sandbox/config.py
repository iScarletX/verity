"""Trusted request shape for the unavailable V2 Skill sandbox stage.

The object is supplied only by the caller and defaults to ``enabled=False``.
A disabled config remains ``not_enabled``. An enabled config does **not**
execute anything in this release: every supported product path returns
``failed`` / ``unavailable`` with
``sandbox_isolation_hardening_required`` before validating an entry point or
constructing the internal research prototype.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple


@dataclass(frozen=True)
class SandboxConfig:
    """Compatibility configuration retained for a future hardened V2.

    Values are validated to keep the request shape bounded, but no field can
    enable Skill execution in the current product release.
    """

    enabled: bool = False
    entry_point: str = ""
    argv: Tuple[str, ...] = ()
    cpu_seconds: int = 10
    memory_mb: int = 256
    wall_seconds: int = 20
    environment_policy: str = "artifact_aware"

    def __post_init__(self) -> None:
        if self.environment_policy not in {"artifact_aware", "legacy_all"}:
            raise ValueError(
                "environment_policy must be artifact_aware or legacy_all")
        if not (0 < self.cpu_seconds <= 300):
            raise ValueError("cpu_seconds must be in (0, 300]")
        if not (0 < self.memory_mb <= 4096):
            raise ValueError("memory_mb must be in (0, 4096]")
        if not (0 < self.wall_seconds <= 600):
            raise ValueError("wall_seconds must be in (0, 600]")
        if len(self.argv) > 64:
            raise ValueError("argv must have at most 64 entries")
        if any(len(a) > 4096 for a in self.argv):
            raise ValueError("each argv entry must be at most 4096 characters")


SANDBOX_DEFAULT = SandboxConfig()
