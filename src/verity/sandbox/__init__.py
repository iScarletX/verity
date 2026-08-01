"""V2 Skill sandbox — explicit opt-in execution-observation layer.

Mirrors the ``verity.blackbox`` package's role for the *execution* track
of the mission (see AGENTS.md §0/§4): observing a reviewed Skill's
runtime file/network/subprocess behaviour inside a one-shot, isolated
sandbox, never as part of the default deterministic/semantic review
pipeline.

Design principles
------------------
- **Explicit opt-in only.** Nothing in this package runs from
  ``review.py``, ``engine.py``, or ``cli.py``. The only caller is
  ``tools/run_sandbox.py`` (a deliberate research/audit command, not a
  product path) or a future explicitly-gated adapter.
- **macOS ``sandbox-exec`` isolation, not a Python-level sandbox.** The
  actual security boundary is the OS Seatbelt profile built by
  ``profile.py`` (deny-by-default, no network-allow clause, writes
  confined to the run's own tmpdir). The staged driver's
  ``sys.addaudithook`` only *observes* — it is instrumentation, not the
  enforcement mechanism.
- **One-shot and disposable.** Every run gets a fresh tmpdir, a fresh
  ``python3`` subprocess in a new process group, and reliable cleanup
  (bounded-retry ``rmtree`` + ``killpg`` in a ``finally`` block) so
  nothing from a reviewed Skill's execution survives past the call.
- **Fails closed.** When ``sandbox-exec`` is not present (any non-macOS
  host, or a macOS host without the binary), ``SandboxRunner`` reports
  ``status="not_available"`` and never falls back to running the
  reviewed script unconfined.
- **Honest reporting.** ``SandboxObservation`` distinguishes the
  sandbox's own outcome (completed/failed/timeout/killed_memory/
  killed_cpu/not_available/no_entry_point) from whatever the reviewed
  script itself did (``exitCode``, ``raisedException``). A caught
  exception inside the reviewed script is a normal ``completed`` sandbox
  run, not a sandbox failure.
"""

from .models import SandboxObservation, SandboxRunRequest
from .runner import SandboxRunner, sandbox_exec_available

__all__ = [
    "SandboxObservation",
    "SandboxRunRequest",
    "SandboxRunner",
    "sandbox_exec_available",
]
