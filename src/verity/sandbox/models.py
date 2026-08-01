"""Request/observation dataclasses for the V2 Skill sandbox.

These mirror the shape of ``BanditRunResult`` / ``GitleaksRunResult``:
a plain dataclass returned by the runner, with a controlled set of
``status`` values and a ``reasonCode`` for the failure path. Nothing
here executes anything; this module only defines the data shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ``status`` controlled values:
#   completed        — the driver ran to completion (the reviewed script
#                       may itself have raised; that is still "completed"
#                       from the sandbox's point of view, see
#                       ``raisedException``).
#   failed            — the sandbox harness itself could not run (e.g.
#                        malformed observation JSON, staging error).
#   timeout           — wall-clock budget exceeded; process killed.
#   killed_memory     — RSS watchdog breached the memory budget; killed.
#   killed_cpu        — CPU-time rlimit breached; process received SIGXCPU
#                        (surfaced here rather than as a bare "killed").
#   not_available     — sandbox-exec is not present on this host/platform.
#   no_entry_point    — the requested entry point does not exist in the
#                        staged snapshot.
SANDBOX_STATUSES = (
    "completed",
    "failed",
    "timeout",
    "killed_memory",
    "killed_cpu",
    "not_available",
    "no_entry_point",
)


@dataclass
class SandboxRunRequest:
    """What the caller wants executed, and under what budget.

    ``entry_point`` is a snapshot-relative, forward-slash path (same
    normalization as ``ArtifactFile.normalizedPath``); it is validated
    against the staged tmpdir before use, never trusted as an absolute
    host path.
    """

    entry_point: str
    argv: List[str] = field(default_factory=list)
    cpu_seconds: int = 10
    memory_mb: int = 256
    wall_seconds: int = 20
    poll_interval_seconds: float = 0.2


@dataclass
class SandboxObservation:
    status: str                                   # see SANDBOX_STATUSES
    reasonCode: Optional[str] = None
    isolationMechanism: str = "none"              # sandbox-exec|none
    entryPoint: Optional[str] = None
    argv: List[str] = field(default_factory=list)
    durationSeconds: Optional[float] = None
    exitCode: Optional[int] = None
    terminatedBySignal: Optional[str] = None
    peakMemoryMb: Optional[float] = None
    raisedException: Optional[Dict] = None         # {type, message}
    fileEvents: List[Dict] = field(default_factory=list)       # {op, path, insideSandbox}
    networkAttempts: List[Dict] = field(default_factory=list)  # {host, port, allowed}
    subprocessAttempts: List[Dict] = field(default_factory=list)  # {argv0, argvPreview}
    stdoutBytes: int = 0
    stderrBytes: int = 0
    truncated: Dict = field(default_factory=lambda: {
        "fileEvents": False,
        "networkAttempts": False,
        "subprocessAttempts": False,
    })
