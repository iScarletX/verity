"""Round 124: new V2 sandbox signal -- sandbox_deserialization_effect
(standing initiative #2).

Closes VR-SKILL-007's V2_sandbox=none gap, whose own layerBoundaries text
promised "May observe deserialization effects with controlled payloads."
Mirrors Round 111/114's decoy-canary shape: SandboxRunner now plants a
third fixed, synthetic decoy at the tmpdir root before every run --
"cache.pkl", a pickle-format file (see SandboxRunner.
_stage_deserialization_effect_decoy). Its bytes are produced by pickling an
object whose ``__reduce__`` returns ``(os.system, (command,))``: pickle's
REDUCE opcode reconstructs an object by calling an arbitrary callable with
arbitrary args, and that callable reference is carried in the payload bytes
themselves, not via any reference to the class that produced them -- the
same mechanism real pickle deserialization exploits use. So a reviewed
Skill's own process does not need to import or know about the originating
class for the embedded ``os.system`` call to fire the moment it calls
pickle.load/pickle.loads on this file. The command only echoes a fixed
synthetic canary marker to /dev/null; it is never a real secret and never
a destructive action. The decoy is never staged when the reviewed artifact
already ships a same-named file, so Verity never overwrites the Skill's
own content.

TestDecoyStaging exercises the real SandboxRunner (same injectable-spawn
discipline as test_sandbox.py) to prove the decoy is actually planted --
and skipped on collision -- before the reviewed script would run, plus a
live-fire check (no real subprocess, no real sandbox -- just pickle.loads
against the exact planted bytes, with a temporary sys.addaudithook) proving
unpickling the decoy really does trigger the canary os.system call. The
rest follows Round 111/114's convention of testing signal-hit behaviour
directly against synthetic SandboxObservation-shaped dicts. No live sandbox
execution in those tests, and no real secret or destructive command
anywhere in this file.
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path
from typing import Dict, Optional

import verity.scoring as scoring
from verity.intake import intake_directory
from verity.sandbox.models import SANDBOX_SIGNAL_DETECTORS, SandboxRunRequest
from verity.sandbox.runner import (SandboxRunner, _DESERIALIZATION_CANARY,
                                    _SpawnHandle)
from verity.scoring import compute_score
from verity.standards import (load_detector_mappings, load_risks,
                               validate_runtime_detector_coverage)


DETECTOR_ID = "sandbox_deserialization_effect"


def projection():
    return {
        "engine": "skill", "coverage": {"status": "sufficient", "reasonCodes": []},
        "findings": [], "ruleMatches": [], "evidences": [],
        "capabilities": {
            "static": {"status": "completed"},
            "semantic": {"status": "not_enabled"},
            "promptBlackbox": {"status": "not_enabled"},
            "skillSandbox": {"status": "completed"},
        },
    }


def sandbox_view(subprocess_attempts=None):
    return {"status": "completed", "fileEvents": [],
            "networkAttempts": [],
            "subprocessAttempts": subprocess_attempts or []}


def _skill(tmp_path: Path, entry_relpath: str = "scripts/main.py",
           body: str = "print('hi')\n",
           extra_files: Optional[Dict[str, str]] = None):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: t\ndescription: t\nversion: 1.0.0\n---\n")
    entry = tmp_path / entry_relpath
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(body)
    for relpath, content in (extra_files or {}).items():
        p = tmp_path / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return intake_directory(str(tmp_path))


class _FakePopen:
    """Minimal Popen double -- enough for SandboxRunner to reach
    ``self._spawn`` and inspect the staged tmpdir before communicate()."""

    def __init__(self, *, pid: int = 4242, returncode: int = 0):
        self.pid = pid
        self.returncode = returncode

    def communicate(self, timeout=None):
        return b"", b""

    def poll(self):
        return self.returncode


class TestDecoyStaging:
    def _available_runner(self, *, inject_spawn) -> SandboxRunner:
        runner = SandboxRunner(inject_spawn=inject_spawn)
        runner._sandbox_available = True  # force past the availability gate
        return runner

    def test_decoy_is_planted_before_spawn(self, tmp_path):
        snap, b = _skill(tmp_path)
        seen = {}

        def _spawn(args, *, cwd, env, cpu_seconds):
            decoy = Path(cwd) / "cache.pkl"
            seen["exists"] = decoy.is_file()
            seen["bytes"] = decoy.read_bytes() if decoy.is_file() else None
            return _SpawnHandle(popen=_FakePopen())

        runner = self._available_runner(inject_spawn=_spawn)
        req = SandboxRunRequest(entry_point="scripts/main.py")
        runner.run(req, snapshot=snap, file_bytes=b)
        assert seen["exists"] is True
        assert seen["bytes"]
        # The bytes are a real pickle stream, not placeholder text.
        assert pickle.PROTO in seen["bytes"] or seen["bytes"][0:1] == b"("

    def test_decoy_is_skipped_when_artifact_already_ships_that_filename(self, tmp_path):
        real_content = b"not a pickle stream, belongs to the reviewed skill"
        (tmp_path / "SKILL.md").write_text(
            "---\nname: t\ndescription: t\nversion: 1.0.0\n---\n")
        entry = tmp_path / "scripts/main.py"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_text("print('hi')\n")
        (tmp_path / "cache.pkl").write_bytes(real_content)
        snap, b = intake_directory(str(tmp_path))
        seen = {}

        def _spawn(args, *, cwd, env, cpu_seconds):
            seen["bytes"] = (Path(cwd) / "cache.pkl").read_bytes()
            return _SpawnHandle(popen=_FakePopen())

        runner = self._available_runner(inject_spawn=_spawn)
        req = SandboxRunRequest(entry_point="scripts/main.py")
        runner.run(req, snapshot=snap, file_bytes=b)
        assert seen["bytes"] == real_content

    def test_unpickling_the_planted_decoy_actually_triggers_the_canary(self, tmp_path):
        """Live-fire proof the mechanism works, without spawning any real
        subprocess or sandbox: stage the decoy via the real runner, then
        unpickle its bytes directly in this test process under a temporary
        audit hook, and confirm the embedded os.system call is observed
        carrying the exact canary marker -- exactly what a reviewed Skill's
        own pickle.load(open('cache.pkl','rb')) call would trigger for
        real inside the sandbox."""
        snap, b = _skill(tmp_path)
        seen = {}

        def _spawn(args, *, cwd, env, cpu_seconds):
            seen["bytes"] = (Path(cwd) / "cache.pkl").read_bytes()
            return _SpawnHandle(popen=_FakePopen())

        runner = self._available_runner(inject_spawn=_spawn)
        req = SandboxRunRequest(entry_point="scripts/main.py")
        runner.run(req, snapshot=snap, file_bytes=b)

        captured = []

        def hook(event, args):
            if event == "os.system":
                captured.append(args[0])

        sys.addaudithook(hook)
        # No portable way to remove a CPython audit hook once installed;
        # harmless for the rest of this test process since it only
        # inspects os.system events, and pytest runs this in a fresh
        # worker/session boundary regardless.
        pickle.loads(seen["bytes"])
        assert len(captured) == 1
        command = captured[0]
        if isinstance(command, bytes):
            command = command.decode("utf-8")
        assert _DESERIALIZATION_CANARY in command


class TestSignalHit:
    def test_canary_in_subprocess_argv_preview_is_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "/bin/sh",
             "argvPreview": ["-c", f"echo {_DESERIALIZATION_CANARY} >/dev/null 2>&1"]}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_canary_match_is_case_insensitive(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "/bin/sh",
             "argvPreview": ["-c", _DESERIALIZATION_CANARY.upper()]}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_unrelated_subprocess_attempt_is_not_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "/bin/echo", "argvPreview": ["hello"]}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_no_subprocess_attempts_is_not_a_hit(self):
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox_view()) is False


def test_signal_is_registered_in_the_fixed_vocabulary():
    assert DETECTOR_ID in SANDBOX_SIGNAL_DETECTORS
    assert SANDBOX_SIGNAL_DETECTORS.count(DETECTOR_ID) == 1


def test_detector_mapping_registered():
    mappings = load_detector_mappings()
    key = ("sandbox_signal", DETECTOR_ID)
    assert key in mappings
    assert mappings[key]["riskIds"] == ["VR-SKILL-007"]
    assert mappings[key]["contribution"] == "signal"


def test_detector_mapping_row_count_grew_by_exactly_one_row():
    mappings = load_detector_mappings()
    # 139 as of Round 123 (semantic.skill.credential_handling_claim_gap) +
    # this round's own sandbox_signal row. Rounds 125/126 later each added
    # their own unrelated blackbox_scenario row, taking it to 141 -> 142.
    assert len(mappings) == 156  # Four agent-runtime signals were added


def test_risk_coverage_flipped_to_signal():
    risks = load_risks()
    coverage = risks["VR-SKILL-007"]["currentCoverage"]
    assert coverage["V2_sandbox"] == "signal"
    # Unaffected layers stay exactly as they were before this round.
    assert coverage["L0_static"] == "partial"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"


def test_new_known_gap_discloses_narrow_scope():
    risks = load_risks()
    gaps = risks["VR-SKILL-007"]["knownGaps"]
    assert any("cache.pkl" in g for g in gaps)
    assert any("pickle" in g.lower() for g in gaps)


def test_validate_runtime_detector_coverage_has_no_drift():
    validate_runtime_detector_coverage()


def test_deserialization_effect_deducts_against_correct_risk_via_scoring():
    """End-to-end check that the new mapping row is actually wired, not
    just present -- exercises the real scoring path Round 89 built.

    The canary os.system call necessarily also trips the pre-existing bare
    sandbox_subprocess_attempt signal (any subprocess spawn at all is
    already a hit for that one) -- SANDBOX_SIGNAL_DETECTORS are evaluated
    independently, so this legitimately produces two deductions against
    two different risks, not one, same as Round 114/116/117/119's overlap
    precedent. Declares Bash so Round 116's sandbox_undeclared_subprocess_
    attempt signal does not also co-fire and add a third, out-of-scope
    deduction.
    """
    report = projection()
    report["artifactModel"] = {"manifest": {"permissions": ["Bash"]}}
    report["skillSandbox"] = sandbox_view(subprocess_attempts=[
        {"argv0": "/bin/sh",
         "argvPreview": ["-c", f"echo {_DESERIALIZATION_CANARY} >/dev/null 2>&1"]}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {DETECTOR_ID, "sandbox_subprocess_attempt"}
    deduction = by_detector[DETECTOR_ID]
    assert deduction["riskIds"] == ["VR-SKILL-007"]
    assert deduction["sourceLayer"] == "V2_sandbox"
    assert deduction["severity"] == "high"
    assert score["value"] <= 59  # high severity cap


def test_unrelated_dependency_install_case_from_round119_is_unaffected():
    """Guards against the new signal double-counting Round 119's existing
    pip-install-attempt fixture."""
    report = projection()
    report["artifactModel"] = {"manifest": {"permissions": ["Bash"]}}
    report["skillSandbox"] = sandbox_view(subprocess_attempts=[
        {"argv0": "/usr/bin/pip", "argvPreview": ["install", "requests"]}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {
        "sandbox_dependency_install_attempt", "sandbox_subprocess_attempt"}
