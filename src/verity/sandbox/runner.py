"""V2 Skill sandbox — controlled execution adapter.

This module runs a reviewed Skill's entry point inside a one-shot,
isolated environment so Verity can *observe* filesystem/network/
subprocess behaviour, following the same discipline as
``bandit_runner.py`` / ``gitleaks_runner.py``:

- injectable spawn (``inject_spawn``) so tests never touch a real
  process;
- controlled env (only a small allowlist, never the reviewed
  environment's secrets);
- fixed budgets (cpu / memory / wall clock), enforced from multiple
  independent angles so no single control is a single point of
  failure:
    * ``resource.setrlimit(RLIMIT_CPU, ...)`` via ``preexec_fn`` — the
      kernel delivers ``SIGXCPU`` when the *reviewed script's own*
      accumulated CPU time (not the sandbox-exec wrapper's) exceeds
      the budget.
    * an RSS-polling watchdog thread — ``sandbox-exec``/the OS gives us
      no memory rlimit primitive that reliably applies to the whole
      process tree on macOS, so Verity polls ``ps -o rss=`` and kills
      the process group if the budget is exceeded.
    * a hard wall-clock timeout on the ``Popen.wait()`` call itself.
- ``start_new_session=True`` + ``os.killpg`` cleanup in a ``finally``
  block, so a runaway reviewed script (or anything it spawned) cannot
  outlive the run;
- ``sandbox-exec`` (macOS Seatbelt) is the actual isolation boundary —
  the RLIMIT_CPU/RSS-watchdog/timeout stack is defense in depth around
  it, not a replacement for it. When ``sandbox-exec`` is unavailable
  (non-macOS, or the binary is missing), the runner refuses to execute
  anything and reports ``status="not_available"`` rather than silently
  running the reviewed script unconfined.

This module is NEVER imported by the default review pipeline
(``review.py``, ``engine.py``, ``cli.py``). It is reached only through
the explicit opt-in CLI, ``tools/run_sandbox.py`` (see AGENTS.md §4,
"V2 Skill sandbox ... NOT yet implemented" on the default path — this
module exists so that gate can eventually be lifted, under its own
review, but this round does not flip it).
"""

from __future__ import annotations

import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from .models import SandboxObservation, SandboxRunRequest
from .profile import build_sandbox_profile

SANDBOX_EXEC_PATH = "/usr/bin/sandbox-exec"

_DRIVER_SOURCE_FILE = Path(__file__).parent / "_driver_source.py"
_DRIVER_STAGED_NAME = "_sandboxdriver.py"
_OBSERVATION_FILE_NAME = "_verity_observation.json"
_PROFILE_FILE_NAME = "_verity_profile.sb"

# Same reliable-cleanup discipline as bandit_runner._remove_tmpdir_with_retry.
_TMPDIR_REMOVE_ATTEMPTS = 5
_TMPDIR_REMOVE_BACKOFF_SECONDS = 0.05

# Observation-file / output budgets — small multiples of the driver's own
# caps (500 file events / 50 network / 50 subprocess) so a malformed or
# hostile observation file cannot make the parent allocate unbounded memory.
MAX_OBSERVATION_BYTES = 4 * 1024 * 1024
MAX_STDOUT_STDERR_BYTES = 4 * 1024 * 1024

DEFAULT_CPU_SECONDS = 10
DEFAULT_MEMORY_MB = 256
DEFAULT_WALL_SECONDS = 20
DEFAULT_POLL_INTERVAL_SECONDS = 0.2

# Small grace window after SIGKILL before we give up waiting on the process
# group during forced cleanup (should be near-instant; bounds worst case).
_KILL_WAIT_SECONDS = 2.0


def sandbox_exec_available(*, path: str = SANDBOX_EXEC_PATH) -> bool:
    """Detect ``sandbox-exec`` on this host. Cheap enough to call directly;
    ``SandboxRunner`` still caches the result at construction time so a
    single run only probes the filesystem once.
    """
    return sys.platform == "darwin" and os.path.isfile(path) and os.access(path, os.X_OK)


def _remove_tmpdir_with_retry(tmpdir: str) -> None:
    """Identical discipline to ``bandit_runner._remove_tmpdir_with_retry``:
    retries transient ``rmtree`` failures and never raises, so it is safe
    inside a ``finally`` block."""
    for attempt in range(_TMPDIR_REMOVE_ATTEMPTS):
        try:
            shutil.rmtree(tmpdir)
            return
        except FileNotFoundError:
            return
        except OSError:
            if attempt == _TMPDIR_REMOVE_ATTEMPTS - 1:
                shutil.rmtree(tmpdir, ignore_errors=True)
                return
            time.sleep(_TMPDIR_REMOVE_BACKOFF_SECONDS)


@dataclass
class _SpawnHandle:
    """What ``spawn`` returns: enough for the runner to watch, bound, and
    reap the process without depending on a specific ``Popen`` shape (so
    tests can inject something simpler)."""

    popen: object   # subprocess.Popen-like: has .pid, .wait(timeout=), .poll()


class _RssWatchdog:
    """Polls RSS for a process group and requests a kill when the budget
    is exceeded. Runs in its own thread so it never blocks the main
    ``wait()`` call. Cooperates with the runner via a shared stop Event
    and records the observed peak (best-effort; RSS sampling is inherently
    approximate for a short-lived process)."""

    def __init__(self, *, pgid: int, memory_mb: int, poll_interval: float,
                 ps_probe, on_breach) -> None:
        self._pgid = pgid
        self._budget_bytes = memory_mb * 1024 * 1024
        self._poll_interval = poll_interval
        self._ps_probe = ps_probe
        self._on_breach = on_breach
        self._stop = threading.Event()
        self._peak_bytes = 0
        self._breached = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    @property
    def peak_mb(self) -> Optional[float]:
        if self._peak_bytes <= 0:
            return None
        return self._peak_bytes / (1024 * 1024)

    @property
    def breached(self) -> bool:
        return self._breached

    def _run(self) -> None:
        while not self._stop.is_set():
            rss_bytes = self._ps_probe(self._pgid)
            if rss_bytes is not None and rss_bytes > self._peak_bytes:
                self._peak_bytes = rss_bytes
            if rss_bytes is not None and rss_bytes > self._budget_bytes:
                self._breached = True
                self._on_breach()
                return
            self._stop.wait(self._poll_interval)


def _default_ps_probe(pgid: int) -> Optional[int]:
    """Sum RSS (KiB, per ``ps`` on macOS/BSD) across every process whose
    process GROUP id is ``pgid``, return bytes. Returns None if the group
    has no live members (already exited) or ``ps`` itself failed."""
    try:
        proc = subprocess.run(
            ["ps", "-o", "rss=", "-g", str(pgid)],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    total_kib = 0
    found = False
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            total_kib += int(line)
            found = True
        except ValueError:
            continue
    if not found:
        return None
    return total_kib * 1024


_SIGNAL_NAMES = {
    getattr(signal, name): name
    for name in dir(signal)
    if name.startswith("SIG") and not name.startswith("SIG_")
}


def _signal_name(signum: int) -> str:
    try:
        return _SIGNAL_NAMES.get(signal.Signals(signum), f"SIG{signum}")
    except ValueError:
        return f"SIG{signum}"


class SandboxRunner:
    """Injectable runner: swap ``spawn``/``ps_probe`` in tests to simulate
    timeout / memory-kill / malformed observation without touching a real
    subprocess or a real macOS sandbox.
    """

    def __init__(self, *, python_executable: str = sys.executable,
                 sandbox_exec_path: str = SANDBOX_EXEC_PATH,
                 max_observation_bytes: int = MAX_OBSERVATION_BYTES,
                 max_output_bytes: int = MAX_STDOUT_STDERR_BYTES,
                 inject_spawn=None,
                 inject_ps_probe=None) -> None:
        self.python_executable = python_executable
        self.sandbox_exec_path = sandbox_exec_path
        self.max_observation_bytes = max_observation_bytes
        self.max_output_bytes = max_output_bytes
        # Cached at construction time, mirroring bandit/gitleaks' "detect
        # once, reuse" pattern; tests override via inject_spawn instead of
        # needing a real binary on disk.
        self._sandbox_available = sandbox_exec_available(path=sandbox_exec_path)
        self._spawn = inject_spawn or self._real_spawn
        self._ps_probe = inject_ps_probe or _default_ps_probe

    # ------------------------------------------------------------------
    # Injection points

    def _real_spawn(self, args: List[str], *, cwd: str, env: Dict[str, str],
                     cpu_seconds: int) -> _SpawnHandle:
        def _preexec():
            # ``start_new_session=True`` below already calls ``setsid()``
            # before this runs, giving the child its own pgid so the whole
            # tree can be killed with one killpg. CPU rlimit is enforced
            # by the kernel independent of anything the reviewed script
            # does.
            try:
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
            except (ValueError, OSError):
                pass

        popen = subprocess.Popen(
            args, cwd=cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, preexec_fn=_preexec,
            shell=False,
        )
        return _SpawnHandle(popen=popen)

    def is_available(self) -> bool:
        return self._sandbox_available

    def _controlled_env(self, tmpdir: str) -> Dict[str, str]:
        # Deliberately narrow allowlist — never the caller's full
        # environment (which may hold live provider keys, tokens, etc.
        # per AGENTS.md §5/§8). TMPDIR is pinned to the staged sandbox
        # directory so any stdlib code that consults it for scratch space
        # stays inside the isolation boundary.
        keep = ("PATH", "LC_ALL", "LANG", "SYSTEMROOT")
        env = {k: os.environ[k] for k in keep if k in os.environ}
        env["HOME"] = tmpdir
        env["TMPDIR"] = tmpdir
        env["VERITY_SANDBOX"] = "1"
        return env

    # ------------------------------------------------------------------
    # Staging (same path-escape discipline as bandit_runner.run_on_snapshot)

    def _stage_snapshot(self, tmpdir: str, snapshot, file_bytes: Dict[str, bytes]) -> Dict[str, str]:
        path_map: Dict[str, str] = {}
        root = Path(tmpdir).resolve()
        for f in snapshot.files:
            if f.status != "included":
                continue
            if f.entryType != "file":
                continue
            data = file_bytes.get(f.fileId, b"")
            dst = Path(tmpdir) / f.normalizedPath
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                dst.resolve().relative_to(root)
            except ValueError:
                # Should never happen (intake already normalized/rejected
                # escaping paths) but never trust a single layer.
                continue
            dst.write_bytes(data)
            path_map[f.normalizedPath] = f.fileId
        return path_map

    # ------------------------------------------------------------------
    # Public API

    def run(self, request: SandboxRunRequest, *, snapshot, file_bytes: Dict[str, bytes]
            ) -> SandboxObservation:
        if not self._sandbox_available:
            return SandboxObservation(
                status="not_available",
                reasonCode="sandbox_exec_not_found",
                isolationMechanism="none",
                entryPoint=request.entry_point,
                argv=list(request.argv),
            )

        tmpdir = tempfile.mkdtemp(prefix="verity-sandbox-")
        # Resolve once: macOS's default TMPDIR sits under a symlink
        # (/tmp -> /private/tmp), and the Seatbelt profile's ``subpath``
        # clause must reference the real path or the write-allow rule
        # silently fails to match.
        real_tmpdir = os.path.realpath(tmpdir)
        try:
            path_map = self._stage_snapshot(tmpdir, snapshot, file_bytes)
            entry_abs = (Path(tmpdir) / request.entry_point)
            try:
                entry_abs.resolve().relative_to(Path(tmpdir).resolve())
            except ValueError:
                return SandboxObservation(
                    status="no_entry_point",
                    reasonCode="entry_point_escapes_sandbox",
                    isolationMechanism="sandbox-exec",
                    entryPoint=request.entry_point,
                    argv=list(request.argv),
                )
            if request.entry_point not in path_map or not entry_abs.is_file():
                return SandboxObservation(
                    status="no_entry_point",
                    reasonCode="entry_point_not_found_in_snapshot",
                    isolationMechanism="sandbox-exec",
                    entryPoint=request.entry_point,
                    argv=list(request.argv),
                )

            driver_dst = Path(tmpdir) / _DRIVER_STAGED_NAME
            driver_dst.write_bytes(_DRIVER_SOURCE_FILE.read_bytes())

            profile_text = build_sandbox_profile(real_tmpdir)
            profile_path = Path(tmpdir) / _PROFILE_FILE_NAME
            profile_path.write_text(profile_text)

            args = [
                self.sandbox_exec_path, "-f", str(profile_path),
                self.python_executable, str(driver_dst),
                str(entry_abs), *request.argv,
            ]

            return self._run_and_observe(
                args=args, tmpdir=tmpdir, request=request,
            )
        finally:
            _remove_tmpdir_with_retry(tmpdir)

    # ------------------------------------------------------------------

    def _run_and_observe(self, *, args: List[str], tmpdir: str,
                          request: SandboxRunRequest) -> SandboxObservation:
        env = self._controlled_env(tmpdir)
        t0 = time.monotonic()
        try:
            handle = self._spawn(args, cwd=tmpdir, env=env,
                                  cpu_seconds=request.cpu_seconds)
        except FileNotFoundError as exc:
            return SandboxObservation(
                status="failed", reasonCode=f"spawn_failed:{exc}",
                isolationMechanism="sandbox-exec",
                entryPoint=request.entry_point, argv=list(request.argv),
            )
        popen = handle.popen

        watchdog: Optional[_RssWatchdog] = None
        killed_for = {"memory": False}

        def _on_memory_breach():
            killed_for["memory"] = True
            self._killpg(popen)

        try:
            pgid = os.getpgid(popen.pid)
        except (OSError, AttributeError):
            pgid = None

        if pgid is not None:
            watchdog = _RssWatchdog(
                pgid=pgid, memory_mb=request.memory_mb,
                poll_interval=request.poll_interval_seconds,
                ps_probe=self._ps_probe, on_breach=_on_memory_breach,
            )
            watchdog.start()

        timed_out = False
        stdout_bytes = b""
        stderr_bytes = b""
        try:
            try:
                stdout_bytes, stderr_bytes = popen.communicate(timeout=request.wall_seconds)
            except subprocess.TimeoutExpired:
                timed_out = True
                self._killpg(popen)
                try:
                    stdout_bytes, stderr_bytes = popen.communicate(timeout=_KILL_WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    stdout_bytes, stderr_bytes = b"", b""
        finally:
            if watchdog is not None:
                watchdog.stop()
            # Always attempt a final killpg — belt and suspenders so no
            # grandchild of the reviewed script can outlive this call even
            # if communicate() returned because the direct child exited
            # while children of ITS OWN remained.
            self._killpg(popen, ignore_missing=True)

        duration = time.monotonic() - t0
        returncode = popen.returncode

        stdout_bytes = stdout_bytes or b""
        stderr_bytes = stderr_bytes or b""
        stdout_len = len(stdout_bytes)
        stderr_len = len(stderr_bytes)

        terminated_by_signal: Optional[str] = None
        if returncode is not None and returncode < 0:
            terminated_by_signal = _signal_name(-returncode)

        status: str
        reason_code: Optional[str] = None
        if killed_for["memory"]:
            status = "killed_memory"
            reason_code = "rss_budget_exceeded"
        elif timed_out:
            status = "timeout"
            reason_code = "wall_clock_budget_exceeded"
        elif terminated_by_signal == "SIGXCPU":
            status = "killed_cpu"
            reason_code = "cpu_budget_exceeded"
        else:
            status = "completed"

        observation = SandboxObservation(
            status=status,
            reasonCode=reason_code,
            isolationMechanism="sandbox-exec",
            entryPoint=request.entry_point,
            argv=list(request.argv),
            durationSeconds=duration,
            exitCode=returncode,
            terminatedBySignal=terminated_by_signal,
            peakMemoryMb=watchdog.peak_mb if watchdog is not None else None,
            stdoutBytes=stdout_len,
            stderrBytes=stderr_len,
        )

        if status == "completed":
            self._merge_driver_observation(observation, tmpdir)

        return observation

    def _killpg(self, popen, *, ignore_missing: bool = False) -> None:
        try:
            pgid = os.getpgid(popen.pid)
        except (OSError, AttributeError):
            if ignore_missing:
                return
            pgid = None
        if pgid is None:
            return
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def _merge_driver_observation(self, observation: SandboxObservation, tmpdir: str) -> None:
        """Read ``_verity_observation.json`` written by the staged driver
        and fold its fields into ``observation``. Any parse/shape problem
        degrades the sandbox-level ``status`` to ``failed`` rather than
        silently reporting an empty (falsely reassuring) observation —
        the driver's own bounded caps (§ ``_driver_source.py``) mean the
        file is always small when it IS well-formed, so oversize/garbled
        content is itself suspicious.
        """
        obs_path = Path(tmpdir) / _OBSERVATION_FILE_NAME
        try:
            raw = obs_path.read_bytes()
        except OSError:
            observation.status = "failed"
            observation.reasonCode = "observation_file_missing"
            return
        if len(raw) > self.max_observation_bytes:
            observation.status = "failed"
            observation.reasonCode = "observation_file_over_budget"
            return
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            observation.status = "failed"
            observation.reasonCode = "observation_malformed_json"
            return
        if not isinstance(parsed, dict):
            observation.status = "failed"
            observation.reasonCode = "observation_unexpected_shape"
            return

        raised = parsed.get("raisedException")
        if isinstance(raised, dict) and "type" in raised and "message" in raised:
            observation.raisedException = {
                "type": str(raised.get("type"))[:200],
                "message": str(raised.get("message"))[:2000],
            }

        observation.fileEvents = _validated_list(parsed.get("fileEvents"),
                                                   {"op", "path", "insideSandbox"})
        observation.networkAttempts = _validated_list(parsed.get("networkAttempts"),
                                                        {"host", "port", "allowed"})
        observation.subprocessAttempts = _validated_list(parsed.get("subprocessAttempts"),
                                                           {"argv0", "argvPreview"})
        truncated = parsed.get("truncated")
        if isinstance(truncated, dict):
            observation.truncated = {
                "fileEvents": bool(truncated.get("fileEvents", False)),
                "networkAttempts": bool(truncated.get("networkAttempts", False)),
                "subprocessAttempts": bool(truncated.get("subprocessAttempts", False)),
            }


def _validated_list(value, required_keys: set) -> List[Dict]:
    if not isinstance(value, list):
        return []
    out: List[Dict] = []
    for item in value:
        if isinstance(item, dict) and required_keys.issubset(item.keys()):
            out.append(item)
    return out
