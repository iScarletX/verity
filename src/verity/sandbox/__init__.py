"""V2 Skill-execution configuration and data contracts.

The product adapter is OFF by default and, when explicitly requested, fails
closed with ``sandbox_isolation_hardening_required``. The historical
``sandbox-exec`` prototype is retained in private implementation modules for
controlled research/unit tests only: it allows host-wide reads and lacks the
process, output, disk, and cleanup guarantees required for untrusted execution.
It is not exported from this package and is not reachable from ``run_review``,
the CLI, the Web app, or ``tools/run_sandbox.py``.

A future V2 release requires a separately reviewed outer container or microVM
boundary and a new controlled detector-hit report schema. The current data
classes do not make that prototype safe to invoke.
"""

from .config import SANDBOX_DEFAULT, SandboxConfig
from .models import SandboxObservation, SandboxRunRequest

__all__ = [
    "SandboxObservation",
    "SandboxRunRequest",
    "SandboxConfig",
    "SANDBOX_DEFAULT",
]
