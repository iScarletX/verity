"""Round 111: new V2 sandbox signal -- sandbox_fake_credential_read (standing
initiative #2).

Closes VR-SKILL-011's V2_sandbox=none gap, whose knownGaps literally named
the missing measurement ("No runtime fake-credential observation"). Unlike
Round 102's sandbox_sensitive_path_read (a pure scoring.py addition reusing
an existing host-path vocabulary already collected by the driver), this
signal needed one small runner.py change: SandboxRunner now plants a fixed,
synthetic decoy credential file ("credentials.json", an obviously-fake
value) at the tmpdir root before every sandboxed run (see
SandboxRunner._stage_fake_credential_decoy), so a Skill that
opportunistically reads credential-shaped files during its own execution
has something real to find. The decoy is never staged when the reviewed
artifact already ships a same-named file, so Verity never overwrites the
Skill's own content.

TestDecoyStaging exercises the real SandboxRunner (same injectable-spawn
discipline as test_sandbox.py) to prove the decoy is actually planted --
and skipped on collision -- before the reviewed script would run. The rest
follows Round 89/102's convention of testing signal-hit behaviour directly
against synthetic SandboxObservation-shaped dicts. No live sandbox
execution, and no real credential value anywhere in this file.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

import verity.scoring as scoring
from verity.intake import intake_directory
from verity.sandbox.models import SANDBOX_SIGNAL_DETECTORS, SandboxRunRequest
from verity.sandbox.runner import SandboxRunner, _SpawnHandle
from verity.scoring import compute_score
from verity.standards import (load_detector_mappings, load_risks,
                               validate_runtime_detector_coverage)


DETECTOR_ID = "sandbox_fake_credential_read"


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


def sandbox_view(file_events=None):
    return {"status": "completed", "fileEvents": file_events or [],
            "networkAttempts": [], "subprocessAttempts": []}


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
            decoy = Path(cwd) / "credentials.json"
            seen["exists"] = decoy.is_file()
            seen["content"] = decoy.read_text() if decoy.is_file() else None
            return _SpawnHandle(popen=_FakePopen())

        runner = self._available_runner(inject_spawn=_spawn)
        req = SandboxRunRequest(entry_point="scripts/main.py")
        runner.run(req, snapshot=snap, file_bytes=b)
        assert seen["exists"] is True
        assert "FAKE-EXAMPLE-DO-NOT-USE" in seen["content"]
        assert "never a real secret" in seen["content"]

    def test_decoy_is_skipped_when_artifact_already_ships_that_filename(self, tmp_path):
        real_content = '{"real": "this file belongs to the reviewed skill"}'
        snap, b = _skill(tmp_path, extra_files={"credentials.json": real_content})
        seen = {}

        def _spawn(args, *, cwd, env, cpu_seconds):
            seen["content"] = (Path(cwd) / "credentials.json").read_text()
            return _SpawnHandle(popen=_FakePopen())

        runner = self._available_runner(inject_spawn=_spawn)
        req = SandboxRunRequest(entry_point="scripts/main.py")
        runner.run(req, snapshot=snap, file_bytes=b)
        assert seen["content"] == real_content


class TestSignalHit:
    def test_read_of_decoy_inside_sandbox_is_a_hit(self):
        sandbox = sandbox_view(file_events=[
            {"op": "read", "path": "credentials.json", "insideSandbox": True}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_read_of_decoy_via_absolute_staged_path_is_a_hit(self):
        sandbox = sandbox_view(file_events=[
            {"op": "read", "path": "/tmp/verity-sandbox-abc123/credentials.json",
             "insideSandbox": True}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_write_of_decoy_is_not_a_hit_for_this_signal(self):
        # Deliberately read-only -- a write escaping the tmpdir is already
        # covered unambiguously by sandbox_write_outside_tmpdir.
        sandbox = sandbox_view(file_events=[
            {"op": "write", "path": "credentials.json", "insideSandbox": True}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_read_outside_sandbox_of_same_basename_is_not_a_hit(self):
        # The decoy is only ever planted inside the tmpdir; an unrelated
        # host-side credentials.json (insideSandbox=False) is not the
        # planted decoy and must not be conflated with it.
        sandbox = sandbox_view(file_events=[
            {"op": "read", "path": "/Users/x/project/credentials.json",
             "insideSandbox": False}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_read_of_unrelated_filename_is_not_a_hit(self):
        sandbox = sandbox_view(file_events=[
            {"op": "read", "path": "notes.txt", "insideSandbox": True}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_no_file_events_is_not_a_hit(self):
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox_view()) is False


def test_signal_is_registered_in_the_fixed_vocabulary():
    assert DETECTOR_ID in SANDBOX_SIGNAL_DETECTORS
    assert SANDBOX_SIGNAL_DETECTORS.count(DETECTOR_ID) == 1


def test_detector_mapping_registered():
    mappings = load_detector_mappings()
    key = ("sandbox_signal", DETECTOR_ID)
    assert key in mappings
    assert mappings[key]["riskIds"] == ["VR-SKILL-011"]
    assert mappings[key]["contribution"] == "signal"


def test_risk_coverage_flipped_to_signal():
    risks = load_risks()
    coverage = risks["VR-SKILL-011"]["currentCoverage"]
    assert coverage["V2_sandbox"] == "signal"
    # Unaffected layers stay exactly as they were before this round.
    assert coverage["L0_static"] == "partial"
    # Round 123 later flipped L1_semantic from "none" to "signal"
    # (semantic.skill.credential_handling_claim_gap).
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"


def test_stale_known_gap_was_removed():
    risks = load_risks()
    gaps = risks["VR-SKILL-011"]["knownGaps"]
    assert "No runtime fake-credential observation" not in gaps
    assert any("credentials.json" in g for g in gaps)


def test_validate_runtime_detector_coverage_has_no_drift():
    validate_runtime_detector_coverage()


def test_fake_credential_read_deducts_against_correct_risk_via_scoring():
    """End-to-end check that the new mapping row is actually wired, not
    just present -- exercises the real scoring path Round 89 built."""
    report = projection()
    report["skillSandbox"] = sandbox_view(file_events=[
        {"op": "read", "path": "credentials.json", "insideSandbox": True}])
    score = compute_score(report)
    assert score["status"] == "available"
    assert len(score["deductions"]) == 1
    deduction = score["deductions"][0]
    assert deduction["riskIds"] == ["VR-SKILL-011"]
    assert deduction["sourceLayer"] == "V2_sandbox"
    assert deduction["detectorIds"] == [DETECTOR_ID]
    assert deduction["severity"] == "high"
    assert score["value"] <= 59  # high severity cap


def test_unrelated_sensitive_path_read_case_from_round102_is_unaffected():
    """Guards against the new signal double-counting Round 102's existing
    ~/.ssh/id_rsa sensitive-path-read fixture."""
    report = projection()
    report["skillSandbox"] = sandbox_view(file_events=[
        {"op": "read", "path": "/Users/x/.ssh/id_rsa", "insideSandbox": False}])
    score = compute_score(report)
    assert score["status"] == "available"
    assert len(score["deductions"]) == 1
    assert score["deductions"][0]["detectorIds"] == ["sandbox_sensitive_path_read"]
