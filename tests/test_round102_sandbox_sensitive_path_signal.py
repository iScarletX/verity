"""Round 102: new V2 sandbox signal -- sandbox_sensitive_path_read (standing
initiative #2).

Closes VR-SKILL-014's V2_sandbox=none gap by reusing engine.py's
_SENSITIVE_PATH_PATTERNS (the same vocabulary behind the L0 static rule
skill_sensitive_path_access) against real fileEvents reads observed by the
V2 sandbox. Mirrors Round 89's mechanical pattern for the first three
sandbox signals: a new SANDBOX_SIGNAL_DETECTORS entry, a matching
detector_mappings.json row, and a currentCoverage flip. No live sandbox
execution -- signal-hit behavior is tested directly against synthetic
SandboxObservation-shaped dicts, matching test_round89_sandbox_scoring.py's
existing convention.
"""
from __future__ import annotations

import verity.scoring as scoring
from verity.sandbox.models import SANDBOX_SIGNAL_DETECTORS
from verity.scoring import compute_score
from verity.standards import (load_detector_mappings, load_risks,
                               validate_runtime_detector_coverage)


DETECTOR_ID = "sandbox_sensitive_path_read"


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


def test_signal_is_registered_in_the_fixed_vocabulary():
    assert DETECTOR_ID in SANDBOX_SIGNAL_DETECTORS
    assert SANDBOX_SIGNAL_DETECTORS.count(DETECTOR_ID) == 1


class TestSignalHit:
    def test_read_of_ssh_key_is_a_hit(self):
        sandbox = sandbox_view(file_events=[
            {"op": "read", "path": "/Users/x/.ssh/id_rsa", "insideSandbox": False}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_read_of_aws_credentials_is_a_hit(self):
        sandbox = sandbox_view(file_events=[
            {"op": "read", "path": "/Users/x/.aws/credentials", "insideSandbox": False}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_write_of_sensitive_path_is_not_a_hit_for_this_signal(self):
        # A write outside the tmpdir is already covered unambiguously by
        # sandbox_write_outside_tmpdir regardless of path; this signal is
        # deliberately read-only so it does not double-count with that
        # existing signal (see test_write_outside_tmpdir_is_deducted_with_
        # correct_risk_and_layer in test_round89_sandbox_scoring.py, which
        # uses /etc/passwd as its write-outside-tmpdir example).
        sandbox = sandbox_view(file_events=[
            {"op": "write", "path": "/etc/passwd", "insideSandbox": False}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_read_of_ordinary_stdlib_path_is_not_a_hit(self):
        sandbox = sandbox_view(file_events=[
            {"op": "read", "path": "/usr/lib/python3.11/os.py", "insideSandbox": False}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_no_file_events_is_not_a_hit(self):
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox_view()) is False


def test_detector_mapping_registered():
    mappings = load_detector_mappings()
    key = ("sandbox_signal", DETECTOR_ID)
    assert key in mappings
    assert mappings[key]["riskIds"] == ["VR-SKILL-014"]
    assert mappings[key]["contribution"] == "signal"


def test_risk_coverage_flipped_to_signal():
    risks = load_risks()
    coverage = risks["VR-SKILL-014"]["currentCoverage"]
    assert coverage["V2_sandbox"] == "signal"
    # Unaffected layers stay exactly as they were before this round.
    assert coverage["L0_static"] == "signal"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"


def test_validate_runtime_detector_coverage_has_no_drift():
    validate_runtime_detector_coverage()


def test_sensitive_path_read_deducts_against_correct_risk_via_scoring():
    """End-to-end check that the new mapping row is actually wired, not just
    present -- exercises the real scoring path Round 89 built, rather than
    re-testing scoring logic already covered by test_round89_sandbox_scoring.py.
    """
    report = projection()
    report["skillSandbox"] = sandbox_view(file_events=[
        {"op": "read", "path": "/Users/x/.ssh/id_rsa", "insideSandbox": False}])
    score = compute_score(report)
    assert score["status"] == "available"
    assert len(score["deductions"]) == 1
    deduction = score["deductions"][0]
    assert deduction["riskIds"] == ["VR-SKILL-014"]
    assert deduction["sourceLayer"] == "V2_sandbox"
    assert deduction["detectorIds"] == [DETECTOR_ID]
    assert deduction["severity"] == "high"
    assert score["value"] <= 59  # high severity cap


def test_unrelated_write_outside_tmpdir_case_from_round89_is_unaffected():
    """Guards against the new signal double-counting Round 89's existing
    /etc/passwd write-outside-tmpdir fixture (see TestSignalHit above)."""
    report = projection()
    report["skillSandbox"] = sandbox_view(file_events=[
        {"op": "write", "path": "/etc/passwd", "insideSandbox": False}])
    score = compute_score(report)
    assert score["status"] == "available"
    assert len(score["deductions"]) == 1
    assert score["deductions"][0]["detectorIds"] == ["sandbox_write_outside_tmpdir"]
