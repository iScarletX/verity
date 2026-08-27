"""Round 114: new V2 sandbox signal -- sandbox_injected_content_propagation
(standing initiative #2).

Closes part of VR-SKILL-005's V2_sandbox=none gap, whose knownGaps literally
named the missing measurement ("No indirect-injection sandbox"). Unlike
Round 111's sandbox_fake_credential_read (a bare "did the Skill read this
planted file" check), this signal tests *propagation*: SandboxRunner now
also plants a second fixed, synthetic decoy file
("external_tool_cache.json", representing cached external/tool content) at
the tmpdir root before every run (see
SandboxRunner._stage_injected_content_decoy). That decoy embeds one fixed
canary marker string that reads like a bare hostname. If a reviewed Skill
parses the decoy's "content" field and blindly acts on the embedded
"instruction" -- shelling out to it or connecting to it -- the exact marker
shows up verbatim in a subprocessAttempts argvPreview item or a
networkAttempts host, which is what scoring.py's
_sandbox_signal_hit("sandbox_injected_content_propagation", ...) checks for.
A bare read of the decoy with no propagation does not trip this signal --
that is deliberate (see the risk's updated knownGaps entry and
test_bare_read_of_decoy_with_no_propagation_is_not_a_hit below).

TestDecoyStaging exercises the real SandboxRunner (same injectable-spawn
discipline as test_round111's TestDecoyStaging) to prove the decoy is
actually planted -- and skipped on collision -- before the reviewed script
would run. TestSignalHit follows Round 89/102/111's convention of testing
signal-hit behaviour directly against synthetic SandboxObservation-shaped
dicts. No live sandbox execution, and the canary marker is a synthetic
``.invalid``-TLD string that can never resolve to a real host.
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


DETECTOR_ID = "sandbox_injected_content_propagation"
CANARY = "verity-injected-content-canary-a91f7d3c.invalid"


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


def sandbox_view(file_events=None, network_attempts=None, subprocess_attempts=None):
    return {"status": "completed", "fileEvents": file_events or [],
            "networkAttempts": network_attempts or [],
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
            decoy = Path(cwd) / "external_tool_cache.json"
            seen["exists"] = decoy.is_file()
            seen["content"] = decoy.read_text() if decoy.is_file() else None
            return _SpawnHandle(popen=_FakePopen())

        runner = self._available_runner(inject_spawn=_spawn)
        req = SandboxRunRequest(entry_point="scripts/main.py")
        runner.run(req, snapshot=snap, file_bytes=b)
        assert seen["exists"] is True
        assert CANARY in seen["content"]
        assert "never a real fetch result" in seen["content"]

    def test_decoy_is_skipped_when_artifact_already_ships_that_filename(self, tmp_path):
        real_content = '{"real": "this file belongs to the reviewed skill"}'
        snap, b = _skill(tmp_path, extra_files={"external_tool_cache.json": real_content})
        seen = {}

        def _spawn(args, *, cwd, env, cpu_seconds):
            seen["content"] = (Path(cwd) / "external_tool_cache.json").read_text()
            return _SpawnHandle(popen=_FakePopen())

        runner = self._available_runner(inject_spawn=_spawn)
        req = SandboxRunRequest(entry_point="scripts/main.py")
        runner.run(req, snapshot=snap, file_bytes=b)
        assert seen["content"] == real_content


class TestSignalHit:
    def test_marker_verbatim_in_network_host_is_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": CANARY, "port": 80, "allowed": False}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_marker_embedded_inside_a_larger_host_string_is_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": f"status.{CANARY}", "port": 443, "allowed": False}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_marker_case_insensitive_in_network_host_is_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": CANARY.upper(), "port": 80, "allowed": False}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_marker_verbatim_in_subprocess_argv_preview_is_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "/usr/bin/curl", "argvPreview": ["curl", CANARY]}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_marker_embedded_inside_a_larger_argv_item_is_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "/bin/sh", "argvPreview": ["-c", f"curl https://{CANARY}/x"]}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_unrelated_network_attempt_is_not_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "example.com", "port": 443, "allowed": False}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_unrelated_subprocess_attempt_is_not_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "/bin/ls", "argvPreview": ["ls", "-la"]}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_bare_read_of_decoy_with_no_propagation_is_not_a_hit(self):
        # Reading the decoy alone proves nothing -- only propagation into a
        # subprocess/network sink is the signal (see the module docstring).
        sandbox = sandbox_view(file_events=[
            {"op": "read", "path": "external_tool_cache.json", "insideSandbox": True}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_no_attempts_at_all_is_not_a_hit(self):
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox_view()) is False


def test_signal_is_registered_in_the_fixed_vocabulary():
    assert DETECTOR_ID in SANDBOX_SIGNAL_DETECTORS
    assert SANDBOX_SIGNAL_DETECTORS.count(DETECTOR_ID) == 1


def test_detector_mapping_registered():
    mappings = load_detector_mappings()
    key = ("sandbox_signal", DETECTOR_ID)
    assert key in mappings
    # Round 120 later mapped this same row to a second riskId, VR-PROMPT-008
    # -- see test_round120_untrusted_content_boundary_dual_sandbox_mapping.py.
    # Round 127 later mapped it to a third, VR-SKILL-013 -- see
    # test_round127_cross_language_dataflow_triple_mapping.py. Round 128
    # later mapped it to a fourth, VR-SKILL-010 -- see
    # test_round128_output_rendering_quad_mapping.py.
    assert mappings[key]["riskIds"] == [
        "VR-SKILL-005", "VR-PROMPT-008", "VR-SKILL-013", "VR-SKILL-010"]
    assert mappings[key]["contribution"] == "signal"


def test_risk_coverage_flipped_to_signal():
    risks = load_risks()
    coverage = risks["VR-SKILL-005"]["currentCoverage"]
    assert coverage["V2_sandbox"] == "signal"
    # Unaffected layers stay exactly as they were before this round.
    assert coverage["L0_static"] == "signal"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"


def test_stale_known_gap_was_removed():
    risks = load_risks()
    gaps = risks["VR-SKILL-005"]["knownGaps"]
    assert "No indirect-injection sandbox" not in gaps
    assert any("canary marker" in g for g in gaps)


def test_validate_runtime_detector_coverage_has_no_drift():
    validate_runtime_detector_coverage()


def test_injected_content_propagation_deducts_against_correct_risk_via_scoring():
    """End-to-end check that the new mapping row is actually wired, not
    just present -- exercises the real scoring path Round 89 built.

    A subprocessAttempts entry carrying the canary marker necessarily also
    trips the pre-existing bare sandbox_subprocess_attempt signal (any
    attempt at all is already a hit for that one) -- SANDBOX_SIGNAL_
    DETECTORS are evaluated independently, so this legitimately produces
    two deductions against two different risks, not one. That overlap is
    inherent to layering a propagation check on top of a presence check,
    not a bug; see the module docstring.

    Declares a Bash permission so Round 116's
    sandbox_undeclared_subprocess_attempt signal does not also co-fire and
    add a third, out-of-scope deduction to this test.
    """
    report = projection()
    report["artifactModel"] = {"manifest": {"permissions": ["Bash(curl:*)"]}}
    report["skillSandbox"] = sandbox_view(subprocess_attempts=[
        {"argv0": "/usr/bin/curl", "argvPreview": ["curl", CANARY]}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {DETECTOR_ID, "sandbox_subprocess_attempt"}
    deduction = by_detector[DETECTOR_ID]
    # riskIds is sorted, and Round 120 later added VR-PROMPT-008 to this
    # same row -- "VR-PROMPT-008" < "VR-SKILL-005" lexicographically, so it
    # is now also the arithmetic root (primaryRiskId), not VR-SKILL-005.
    # Round 127 later added a third riskId, VR-SKILL-013, and Round 128 a
    # fourth, VR-SKILL-010, which sorts between VR-SKILL-005 and
    # VR-SKILL-013.
    assert deduction["riskIds"] == [
        "VR-PROMPT-008", "VR-SKILL-005", "VR-SKILL-010", "VR-SKILL-013"]
    assert deduction["primaryRiskId"] == "VR-PROMPT-008"
    assert deduction["sourceLayer"] == "V2_sandbox"
    assert deduction["severity"] == "high"
    assert by_detector["sandbox_subprocess_attempt"]["riskIds"] == ["VR-SKILL-006"]
    assert score["value"] <= 59  # high severity cap


def test_unrelated_subprocess_attempt_case_from_round89_is_unaffected():
    """Guards against the new signal double-counting the pre-existing
    bare sandbox_subprocess_attempt signal.

    Declares a Bash permission so Round 116's
    sandbox_undeclared_subprocess_attempt signal does not also co-fire --
    this test is scoped to the propagation signal's non-interference alone.
    """
    report = projection()
    report["artifactModel"] = {"manifest": {"permissions": ["Bash(ls:*)"]}}
    report["skillSandbox"] = sandbox_view(subprocess_attempts=[
        {"argv0": "/bin/ls", "argvPreview": ["ls", "-la"]}])
    score = compute_score(report)
    assert score["status"] == "available"
    assert len(score["deductions"]) == 1
    assert score["deductions"][0]["detectorIds"] == ["sandbox_subprocess_attempt"]
