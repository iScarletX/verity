"""V2 Skill sandbox tests.

Following the bandit_runner / gitleaks_runner test discipline:
- Injectable ``spawn``/``ps_probe`` stub tests run on every platform and
  in CI (no real subprocess, no real sandbox-exec).
- A small number of real integration tests exercise the actual macOS
  ``sandbox-exec`` boundary and are skipped everywhere else.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from verity.intake import intake_directory
from verity.sandbox.models import SandboxObservation, SandboxRunRequest
from verity.sandbox.profile import build_sandbox_profile
from verity.sandbox.runner import (
    SandboxRunner,
    _SpawnHandle,
    _remove_tmpdir_with_retry,
    sandbox_exec_available,
)


REPO = Path(__file__).parent.parent


def _skill(tmp_path: Path, entry_relpath: str = "scripts/main.py",
           body: str = "print('hi')\n"):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: t\ndescription: t\nversion: 1.0.0\n---\n")
    entry = tmp_path / entry_relpath
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(body)
    return intake_directory(str(tmp_path))


# --------------------------------------------------------------------- #
# Fake subprocess.Popen-like object for the injectable spawn stub       #
# --------------------------------------------------------------------- #

class _FakePopen:
    """Enough of the ``subprocess.Popen`` surface for ``SandboxRunner`` to
    drive: ``pid``, ``communicate(timeout=)``, ``returncode``, ``poll()``.
    """

    def __init__(self, *, pid: int = 4242, returncode: int = 0,
                 stdout: bytes = b"", stderr: bytes = b"",
                 hang_seconds: float = 0.0,
                 observation_writer=None):
        self.pid = pid
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._hang_seconds = hang_seconds
        self._observation_writer = observation_writer
        self._killed = False

    def communicate(self, timeout=None):
        if self._hang_seconds:
            if timeout is not None and self._hang_seconds > timeout:
                # Simulate the real subprocess module's behaviour: a
                # communicate() call that would block past `timeout`
                # raises, and the caller is expected to kill + retry.
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
            time.sleep(self._hang_seconds)
        if self._observation_writer:
            self._observation_writer()
        return self._stdout, self._stderr

    def poll(self):
        return self.returncode


def _make_spawn_stub(popen: _FakePopen, *, pgid: Optional[int] = 4242):
    """Return a ``spawn`` callable compatible with SandboxRunner's
    ``inject_spawn`` hook, plus a ``calls`` list capturing every call."""
    calls: List[Dict] = []

    def _spawn(args, *, cwd, env, cpu_seconds):
        calls.append({"args": args, "cwd": cwd, "env": env, "cpu_seconds": cpu_seconds})
        return _SpawnHandle(popen=popen)

    return _spawn, calls


def _stub_ps_probe_constant(rss_bytes: Optional[int]):
    def _probe(pgid):
        return rss_bytes
    return _probe


def _write_observation(tmpdir: str, payload: dict) -> None:
    (Path(tmpdir) / "_verity_observation.json").write_text(json.dumps(payload))


# --------------------------------------------------------------------- #
# A. Detection / availability                                           #
# --------------------------------------------------------------------- #

class TestAvailability:
    def test_sandbox_exec_available_false_when_path_missing(self, tmp_path):
        assert sandbox_exec_available(path=str(tmp_path / "no-such-binary")) is False

    def test_not_available_short_circuits_run(self, tmp_path, monkeypatch):
        snap, b = _skill(tmp_path)
        runner = SandboxRunner(sandbox_exec_path=str(tmp_path / "missing"))
        assert runner.is_available() is False
        req = SandboxRunRequest(entry_point="scripts/main.py")
        obs = runner.run(req, snapshot=snap, file_bytes=b)
        assert obs.status == "not_available"
        assert obs.reasonCode == "sandbox_exec_not_found"
        assert obs.isolationMechanism == "none"

    def test_not_available_never_calls_spawn(self, tmp_path):
        # Regression guard: when unavailable, the runner must not attempt
        # to spawn anything at all (fail closed, not fall back unconfined).
        snap, b = _skill(tmp_path)
        calls = []

        def _spawn(*a, **k):
            calls.append((a, k))
            raise AssertionError("spawn must not be called when unavailable")

        runner = SandboxRunner(sandbox_exec_path=str(tmp_path / "missing"),
                                inject_spawn=_spawn)
        req = SandboxRunRequest(entry_point="scripts/main.py")
        obs = runner.run(req, snapshot=snap, file_bytes=b)
        assert obs.status == "not_available"
        assert calls == []


# --------------------------------------------------------------------- #
# B. Entry-point validation                                             #
# --------------------------------------------------------------------- #

class TestEntryPointValidation:
    def _available_runner(self, *, inject_spawn=None) -> SandboxRunner:
        runner = SandboxRunner(inject_spawn=inject_spawn)
        runner._sandbox_available = True  # force past the availability gate
        return runner

    def test_missing_entry_point_reports_no_entry_point(self, tmp_path):
        snap, b = _skill(tmp_path)
        runner = self._available_runner()
        req = SandboxRunRequest(entry_point="scripts/does_not_exist.py")
        obs = runner.run(req, snapshot=snap, file_bytes=b)
        assert obs.status == "no_entry_point"
        assert obs.reasonCode == "entry_point_not_found_in_snapshot"

    def test_path_escape_entry_point_rejected(self, tmp_path):
        snap, b = _skill(tmp_path)
        runner = self._available_runner()
        req = SandboxRunRequest(entry_point="../../../etc/passwd")
        obs = runner.run(req, snapshot=snap, file_bytes=b)
        assert obs.status == "no_entry_point"

    def test_valid_entry_point_reaches_spawn(self, tmp_path):
        snap, b = _skill(tmp_path)
        popen = _FakePopen(returncode=0,
                            observation_writer=lambda: None)
        spawn, calls = _make_spawn_stub(popen)

        # The stub spawn never actually writes the observation file, so
        # the runner must degrade to failed/observation_file_missing --
        # this also proves spawn was reached with the resolved entry path.
        runner = self._available_runner(inject_spawn=spawn)
        req = SandboxRunRequest(entry_point="scripts/main.py")
        obs = runner.run(req, snapshot=snap, file_bytes=b)
        assert len(calls) == 1
        call_args = calls[0]["args"]
        assert call_args[0] == runner.sandbox_exec_path
        assert "scripts/main.py" not in call_args  # entry is staged to an absolute tmp path
        assert any(str(a).endswith("scripts/main.py") for a in call_args)
        assert obs.status == "failed"
        assert obs.reasonCode == "observation_file_missing"


# --------------------------------------------------------------------- #
# C. Status mapping via injected spawn (timeout / memory / cpu / ok)    #
# --------------------------------------------------------------------- #

class TestStatusMapping:
    def _runner(self, *, inject_spawn, inject_ps_probe=None) -> SandboxRunner:
        runner = SandboxRunner(inject_spawn=inject_spawn,
                                inject_ps_probe=inject_ps_probe)
        runner._sandbox_available = True
        return runner

    def test_completed_with_clean_observation(self, tmp_path):
        snap, b = _skill(tmp_path)

        def writer_factory(tmpdir_holder):
            def _writer():
                _write_observation(tmpdir_holder["dir"], {
                    "raisedException": None,
                    "fileEvents": [{"op": "read", "path": "x", "insideSandbox": True}],
                    "networkAttempts": [],
                    "subprocessAttempts": [],
                    "truncated": {"fileEvents": False, "networkAttempts": False,
                                  "subprocessAttempts": False},
                    "driverExitCode": 0,
                })
            return _writer

        tmpdir_holder = {"dir": None}

        def _spawn(args, *, cwd, env, cpu_seconds):
            tmpdir_holder["dir"] = cwd
            popen = _FakePopen(returncode=0, observation_writer=writer_factory(tmpdir_holder))
            return _SpawnHandle(popen=popen)

        runner = self._runner(inject_spawn=_spawn,
                               inject_ps_probe=_stub_ps_probe_constant(10 * 1024 * 1024))
        req = SandboxRunRequest(entry_point="scripts/main.py")
        obs = runner.run(req, snapshot=snap, file_bytes=b)
        assert obs.status == "completed"
        assert obs.exitCode == 0
        assert obs.fileEvents == [{"op": "read", "path": "x", "insideSandbox": True}]
        assert obs.isolationMechanism == "sandbox-exec"

    def test_timeout_maps_to_timeout_status(self, tmp_path):
        snap, b = _skill(tmp_path)

        def _spawn(args, *, cwd, env, cpu_seconds):
            popen = _FakePopen(returncode=-signal.SIGKILL, hang_seconds=999)
            return _SpawnHandle(popen=popen)

        runner = self._runner(inject_spawn=_spawn,
                               inject_ps_probe=_stub_ps_probe_constant(None))
        # killpg on a fake pid will raise ProcessLookupError, which the
        # runner already swallows via os.killpg's except clause.
        req = SandboxRunRequest(entry_point="scripts/main.py", wall_seconds=0)
        obs = runner.run(req, snapshot=snap, file_bytes=b)
        assert obs.status == "timeout"
        assert obs.reasonCode == "wall_clock_budget_exceeded"
        assert obs.terminatedBySignal == "SIGKILL"

    def test_memory_breach_maps_to_killed_memory(self, tmp_path, monkeypatch):
        # The fake Popen below uses a placeholder pid that is not a real
        # OS process, so os.getpgid()/os.killpg() must be stubbed at the
        # os-module level (never let a fake pid reach a *real* killpg
        # call, which could otherwise hit an unrelated live process
        # group). This still exercises the runner's real pgid-resolution
        # and kill-request code paths, just against a safe double.
        import verity.sandbox.runner as sbr
        monkeypatch.setattr(sbr.os, "getpgid", lambda pid: 999999)
        killpg_calls = []
        monkeypatch.setattr(sbr.os, "killpg",
                            lambda pgid, sig: killpg_calls.append((pgid, sig)))

        snap, b = _skill(tmp_path)

        def _spawn(args, *, cwd, env, cpu_seconds):
            # Long enough hang that the watchdog's kill fires first, but
            # short enough to keep the test fast.
            popen = _FakePopen(returncode=-signal.SIGKILL, hang_seconds=0.3)
            return _SpawnHandle(popen=popen)

        # RSS probe always reports way over any reasonable budget.
        runner = self._runner(inject_spawn=_spawn,
                               inject_ps_probe=_stub_ps_probe_constant(10 * 1024 * 1024 * 1024))
        req = SandboxRunRequest(entry_point="scripts/main.py",
                                 memory_mb=1, wall_seconds=30,
                                 poll_interval_seconds=0.01)
        obs = runner.run(req, snapshot=snap, file_bytes=b)
        assert obs.status == "killed_memory"
        assert obs.reasonCode == "rss_budget_exceeded"
        assert obs.peakMemoryMb is not None and obs.peakMemoryMb > 1
        assert killpg_calls  # the watchdog's breach handler did request a kill

    def test_sigxcpu_maps_to_killed_cpu(self, tmp_path):
        snap, b = _skill(tmp_path)

        def _spawn(args, *, cwd, env, cpu_seconds):
            popen = _FakePopen(returncode=-signal.SIGXCPU, hang_seconds=0)
            return _SpawnHandle(popen=popen)

        runner = self._runner(inject_spawn=_spawn,
                               inject_ps_probe=_stub_ps_probe_constant(None))
        req = SandboxRunRequest(entry_point="scripts/main.py")
        obs = runner.run(req, snapshot=snap, file_bytes=b)
        assert obs.status == "killed_cpu"
        assert obs.reasonCode == "cpu_budget_exceeded"
        assert obs.terminatedBySignal == "SIGXCPU"

    def test_spawn_file_not_found_becomes_failed(self, tmp_path):
        snap, b = _skill(tmp_path)

        def _spawn(args, *, cwd, env, cpu_seconds):
            raise FileNotFoundError("no such binary")

        runner = self._runner(inject_spawn=_spawn)
        req = SandboxRunRequest(entry_point="scripts/main.py")
        obs = runner.run(req, snapshot=snap, file_bytes=b)
        assert obs.status == "failed"
        assert "spawn_failed" in (obs.reasonCode or "")


# --------------------------------------------------------------------- #
# D. Observation-file parsing / degradation                             #
# --------------------------------------------------------------------- #

class TestObservationParsing:
    def _runner_with_writer(self, write_fn) -> SandboxRunner:
        def _spawn(args, *, cwd, env, cpu_seconds):
            popen = _FakePopen(returncode=0, observation_writer=lambda: write_fn(cwd))
            return _SpawnHandle(popen=popen)
        runner = SandboxRunner(inject_spawn=_spawn,
                                inject_ps_probe=_stub_ps_probe_constant(None))
        runner._sandbox_available = True
        return runner

    def test_missing_observation_file_becomes_failed(self, tmp_path):
        snap, b = _skill(tmp_path)
        runner = self._runner_with_writer(lambda cwd: None)
        obs = runner.run(SandboxRunRequest(entry_point="scripts/main.py"),
                         snapshot=snap, file_bytes=b)
        assert obs.status == "failed"
        assert obs.reasonCode == "observation_file_missing"

    def test_malformed_json_becomes_failed(self, tmp_path):
        snap, b = _skill(tmp_path)

        def _write(cwd):
            (Path(cwd) / "_verity_observation.json").write_text("{not json")

        runner = self._runner_with_writer(_write)
        obs = runner.run(SandboxRunRequest(entry_point="scripts/main.py"),
                         snapshot=snap, file_bytes=b)
        assert obs.status == "failed"
        assert obs.reasonCode == "observation_malformed_json"

    def test_oversize_observation_becomes_failed(self, tmp_path):
        snap, b = _skill(tmp_path)

        def _write(cwd):
            # Valid JSON but comfortably bigger than max_observation_bytes.
            (Path(cwd) / "_verity_observation.json").write_text(
                json.dumps({"pad": "x" * 1000}))

        def _spawn(args, *, cwd, env, cpu_seconds):
            popen = _FakePopen(returncode=0, observation_writer=lambda: _write(cwd))
            return _SpawnHandle(popen=popen)

        runner = SandboxRunner(inject_spawn=_spawn,
                                inject_ps_probe=_stub_ps_probe_constant(None),
                                max_observation_bytes=100)
        runner._sandbox_available = True
        obs = runner.run(SandboxRunRequest(entry_point="scripts/main.py"),
                         snapshot=snap, file_bytes=b)
        assert obs.status == "failed"
        assert obs.reasonCode == "observation_file_over_budget"

    def test_non_dict_json_becomes_failed(self, tmp_path):
        snap, b = _skill(tmp_path)

        def _write(cwd):
            (Path(cwd) / "_verity_observation.json").write_text(json.dumps([1, 2, 3]))

        runner = self._runner_with_writer(_write)
        obs = runner.run(SandboxRunRequest(entry_point="scripts/main.py"),
                         snapshot=snap, file_bytes=b)
        assert obs.status == "failed"
        assert obs.reasonCode == "observation_unexpected_shape"

    def test_malformed_list_entries_are_dropped_not_crashed(self, tmp_path):
        snap, b = _skill(tmp_path)

        def _write(cwd):
            (Path(cwd) / "_verity_observation.json").write_text(json.dumps({
                "raisedException": None,
                "fileEvents": [
                    {"op": "read", "path": "ok", "insideSandbox": True},
                    {"op": "read"},  # missing keys -> dropped
                    "not-a-dict",     # wrong type -> dropped
                ],
                "networkAttempts": "not-a-list",
                "subprocessAttempts": None,
                "truncated": {"fileEvents": True, "networkAttempts": False,
                              "subprocessAttempts": False},
                "driverExitCode": 0,
            }))

        runner = self._runner_with_writer(_write)
        obs = runner.run(SandboxRunRequest(entry_point="scripts/main.py"),
                         snapshot=snap, file_bytes=b)
        assert obs.status == "completed"
        assert obs.fileEvents == [{"op": "read", "path": "ok", "insideSandbox": True}]
        assert obs.networkAttempts == []
        assert obs.subprocessAttempts == []
        assert obs.truncated["fileEvents"] is True

    def test_raised_exception_is_carried_through(self, tmp_path):
        snap, b = _skill(tmp_path)

        def _write(cwd):
            (Path(cwd) / "_verity_observation.json").write_text(json.dumps({
                "raisedException": {"type": "ValueError", "message": "boom"},
                "fileEvents": [], "networkAttempts": [], "subprocessAttempts": [],
                "truncated": {"fileEvents": False, "networkAttempts": False,
                              "subprocessAttempts": False},
                "driverExitCode": 1,
            }))

        def _spawn(args, *, cwd, env, cpu_seconds):
            popen = _FakePopen(returncode=1, observation_writer=lambda: _write(cwd))
            return _SpawnHandle(popen=popen)

        runner = SandboxRunner(inject_spawn=_spawn,
                                inject_ps_probe=_stub_ps_probe_constant(None))
        runner._sandbox_available = True
        obs = runner.run(SandboxRunRequest(entry_point="scripts/main.py"),
                         snapshot=snap, file_bytes=b)
        assert obs.status == "completed"
        assert obs.exitCode == 1
        assert obs.raisedException == {"type": "ValueError", "message": "boom"}


# --------------------------------------------------------------------- #
# E. Controlled env / cleanup                                           #
# --------------------------------------------------------------------- #

class TestControlledEnvAndCleanup:
    def test_controlled_env_never_leaks_arbitrary_caller_vars(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VERITY_TEST_SECRET_TOKEN", "should-not-appear")
        snap, b = _skill(tmp_path)
        captured = {}

        def _spawn(args, *, cwd, env, cpu_seconds):
            captured["env"] = env
            popen = _FakePopen(returncode=0)
            return _SpawnHandle(popen=popen)

        runner = SandboxRunner(inject_spawn=_spawn,
                                inject_ps_probe=_stub_ps_probe_constant(None))
        runner._sandbox_available = True
        runner.run(SandboxRunRequest(entry_point="scripts/main.py"),
                  snapshot=snap, file_bytes=b)
        assert "VERITY_TEST_SECRET_TOKEN" not in captured["env"]
        assert captured["env"].get("VERITY_SANDBOX") == "1"

    def test_tmpdir_removed_after_run(self, tmp_path):
        snap, b = _skill(tmp_path)
        seen_dir = {}

        def _spawn(args, *, cwd, env, cpu_seconds):
            seen_dir["dir"] = cwd
            popen = _FakePopen(returncode=0)
            return _SpawnHandle(popen=popen)

        runner = SandboxRunner(inject_spawn=_spawn,
                                inject_ps_probe=_stub_ps_probe_constant(None))
        runner._sandbox_available = True
        runner.run(SandboxRunRequest(entry_point="scripts/main.py"),
                  snapshot=snap, file_bytes=b)
        assert seen_dir["dir"]
        assert not Path(seen_dir["dir"]).exists()

    def test_remove_tmpdir_retry_succeeds_after_transient_error(self, tmp_path, monkeypatch):
        import verity.sandbox.runner as sbr
        d = tmp_path / "verity-sandbox-xyz"
        d.mkdir()
        real_rmtree = sbr.shutil.rmtree
        calls = {"n": 0}

        def flaky_rmtree(path, ignore_errors=False):
            calls["n"] += 1
            if calls["n"] == 1 and not ignore_errors:
                raise OSError("transient")
            return real_rmtree(path, ignore_errors=ignore_errors)

        monkeypatch.setattr(sbr.shutil, "rmtree", flaky_rmtree)
        monkeypatch.setattr(sbr.time, "sleep", lambda _s: None)
        _remove_tmpdir_with_retry(str(d))
        assert not d.exists()
        assert calls["n"] >= 2

    def test_remove_tmpdir_missing_dir_is_noop(self, tmp_path):
        _remove_tmpdir_with_retry(str(tmp_path / "does-not-exist"))


# --------------------------------------------------------------------- #
# F. Profile generation                                                 #
# --------------------------------------------------------------------- #

class TestProfileGeneration:
    def test_profile_denies_default_and_scopes_write_to_tmpdir(self, tmp_path):
        text = build_sandbox_profile(str(tmp_path))
        assert "(deny default)" in text
        assert f'(allow file-write* (subpath "{tmp_path}"))' in text
        assert "network" not in text  # no network-allow clause anywhere

    def test_profile_escapes_quotes_in_path(self):
        text = build_sandbox_profile('/tmp/has"quote')
        assert '\\"quote' in text

    def test_profile_has_no_network_allow_clause(self, tmp_path):
        text = build_sandbox_profile(str(tmp_path))
        for line in text.splitlines():
            assert not line.strip().startswith("(allow network")


# --------------------------------------------------------------------- #
# G. Models sanity                                                      #
# --------------------------------------------------------------------- #

class TestModels:
    def test_default_request_budgets(self):
        req = SandboxRunRequest(entry_point="scripts/main.py")
        assert req.cpu_seconds == 10
        assert req.memory_mb == 256
        assert req.wall_seconds == 20

    def test_observation_default_truncated_shape(self):
        obs = SandboxObservation(status="completed")
        assert set(obs.truncated) == {"fileEvents", "networkAttempts", "subprocessAttempts"}
        assert obs.argv == []
        assert obs.fileEvents == []


# --------------------------------------------------------------------- #
# H. Real macOS sandbox-exec integration                                #
# --------------------------------------------------------------------- #

_HAS_REAL_SANDBOX = sys.platform == "darwin" and Path("/usr/bin/sandbox-exec").exists()


@pytest.mark.skipif(not _HAS_REAL_SANDBOX, reason="requires macOS sandbox-exec")
class TestRealSandboxExec:
    def test_network_attempt_is_blocked_and_observed(self, tmp_path):
        snap, b = _skill(tmp_path, body=(
            "import urllib.request\n"
            "try:\n"
            "    urllib.request.urlopen('https://1.1.1.1', timeout=2)\n"
            "except Exception as e:\n"
            "    print('caught:', e)\n"
            "    raise\n"
        ))
        runner = SandboxRunner()
        assert runner.is_available() is True
        req = SandboxRunRequest(entry_point="scripts/main.py",
                                 cpu_seconds=5, memory_mb=128, wall_seconds=10)
        obs = runner.run(req, snapshot=snap, file_bytes=b)
        assert obs.status == "completed"
        assert obs.exitCode == 1
        assert obs.isolationMechanism == "sandbox-exec"
        assert len(obs.networkAttempts) >= 1
        assert obs.networkAttempts[0]["host"] == "1.1.1.1"
        assert obs.networkAttempts[0]["allowed"] is False
        assert obs.raisedException is not None
        assert obs.raisedException["type"] in ("URLError", "OSError", "socket.error")

    def test_file_write_inside_sandbox_is_observed(self, tmp_path):
        snap, b = _skill(tmp_path, body=(
            "with open('out.txt', 'w') as f:\n"
            "    f.write('hello from sandboxed skill')\n"
            "print('wrote file')\n"
        ))
        runner = SandboxRunner()
        assert runner.is_available() is True
        req = SandboxRunRequest(entry_point="scripts/main.py",
                                 cpu_seconds=5, memory_mb=128, wall_seconds=10)
        obs = runner.run(req, snapshot=snap, file_bytes=b)
        assert obs.status == "completed"
        assert obs.exitCode == 0
        assert obs.raisedException is None
        writes = [e for e in obs.fileEvents if e["op"] == "write"]
        assert writes, obs.fileEvents
        assert writes[0]["insideSandbox"] is True
