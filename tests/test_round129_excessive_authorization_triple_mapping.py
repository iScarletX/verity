"""Round 129: close VR-PROMPT-007's V2_sandbox=none gap by reusing the
existing sandbox_undeclared_network_attempt / sandbox_undeclared_
subprocess_attempt signal pair a third time each, rather than adding a
near-duplicate detector pair (standing initiative #2).

VR-SKILL-004 ("Overbroad declared permissions or capabilities") and
VR-PROMPT-007 ("Excessive tool authorization") describe the same
underlying concern from two scopes: 004 is Skill-manifest-specific
(declared file/process/network/credential/tool capabilities exceed least
privilege), 007 is the broader prompt/agent-config-level authorization
being wider than the task requires or using open-ended wildcard grants.
Their own layerBoundaries.V2_sandbox text is nearly verbatim the same
mechanism: VR-SKILL-004 says "May observe attempted capabilities and
policy denials"; VR-PROMPT-007 says "May observe actual attempted
capability use under policy". Round 116's undeclared-network/subprocess-
attempt signal pair already implements exactly this: cross-referencing a
Skill's declared manifest permission families against its observed
runtime network/subprocess attempts. No new CATALOG entry, decoy, or
scoring.py branch is required -- only the standards-layer riskIds list for
both existing rows needed a third entry each, the exact same shape Round
92 established for a semantic_finding_type row and Rounds 120/127/128
extended for the sandbox_injected_content_propagation row.

The two detectors are kept in lockstep (both rows get the same third
riskId), mirroring Round 116's own original design choice to always treat
the network/subprocess pair as a matched set for VR-SKILL-004/012.

Screened and declined alongside this candidate: VR-SKILL-015 (still needs
new database-driver instrumentation, per Round 128's own screening note --
unaffected by this round). No other VR-PROMPT-* risk's V2_sandbox text
matches an existing signal's mechanism this closely without needing new
plumbing.

No live sandbox execution anywhere in this file -- follows the Round 89/
102/111/114/116/120/124/127/128 convention of testing signal-hit behaviour
directly against synthetic SandboxObservation-shaped dicts and a synthetic
manifest dict (identical to Round 116's own fixtures, since these are the
same two detectors).
"""
from __future__ import annotations

from verity.scoring import compute_score
from verity.sandbox.models import SANDBOX_SIGNAL_DETECTORS
from verity.standards import (load_detector_mappings, load_risks,
                               validate_runtime_detector_coverage)

NETWORK_DETECTOR = "sandbox_undeclared_network_attempt"
SUBPROCESS_DETECTOR = "sandbox_undeclared_subprocess_attempt"


def projection(manifest=None):
    return {
        "engine": "skill", "coverage": {"status": "sufficient", "reasonCodes": []},
        "findings": [], "ruleMatches": [], "evidences": [],
        "artifactModel": {"manifest": manifest or {}},
        "capabilities": {
            "static": {"status": "completed"},
            "semantic": {"status": "not_enabled"},
            "promptBlackbox": {"status": "not_enabled"},
            "skillSandbox": {"status": "completed"},
        },
    }


def sandbox_view(network_attempts=None, subprocess_attempts=None):
    return {"status": "completed", "fileEvents": [],
            "networkAttempts": network_attempts or [],
            "subprocessAttempts": subprocess_attempts or []}


def test_signals_are_still_exactly_two_registered_detectors():
    # Adding a third riskId onto each existing row must not duplicate or
    # rename either detector.
    for detector_id in (NETWORK_DETECTOR, SUBPROCESS_DETECTOR):
        assert detector_id in SANDBOX_SIGNAL_DETECTORS
        assert SANDBOX_SIGNAL_DETECTORS.count(detector_id) == 1


def test_both_detectors_now_map_to_all_three_risks():
    mappings = load_detector_mappings()
    for detector_id in (NETWORK_DETECTOR, SUBPROCESS_DETECTOR):
        key = ("sandbox_signal", detector_id)
        entry = mappings[key]
        assert entry["riskIds"] == ["VR-SKILL-004", "VR-SKILL-012", "VR-PROMPT-007"]
        assert entry["contribution"] == "signal"


def test_vr_prompt_007_v2_sandbox_coverage_is_now_signal():
    risks = load_risks()
    coverage = risks["VR-PROMPT-007"]["currentCoverage"]
    assert coverage["V2_sandbox"] == "signal"
    # Unaffected layers stay exactly as they were before this round.
    assert coverage["L0_static"] == "partial"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "signal"


def test_vr_skill_004_and_vr_skill_012_coverage_are_unaffected_by_the_triple_mapping():
    risks = load_risks()
    for risk_id in ("VR-SKILL-004", "VR-SKILL-012"):
        coverage = risks[risk_id]["currentCoverage"]
        assert coverage["V2_sandbox"] == "signal"
        assert coverage["L0_static"] == "signal"
        assert coverage["L1_semantic"] == "signal"
        assert coverage["V1_5_blackbox"] == "none"


def test_known_gaps_disclose_round_129_honestly():
    risks = load_risks()
    gaps = risks["VR-PROMPT-007"]["knownGaps"]
    assert any("Round 129" in g for g in gaps)
    assert any("third, equally-valid risk mapping" in g for g in gaps)
    # The pre-existing gap bullets are untouched.
    assert "Only strict wildcard forms" in gaps
    assert "No task-to-capability necessity model" in gaps
    assert "No MCP scope analysis" in gaps


def test_detector_mapping_total_row_count_is_unchanged():
    # Reusing both existing rows for a third riskId each must not create a
    # new row -- same invariant as Round 92/120/127/128's precedent.
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added


def test_runtime_detector_coverage_has_no_drift_after_triple_mapping():
    validate_runtime_detector_coverage()


def test_undeclared_network_attempt_deducts_against_all_three_risks_via_scoring():
    """riskIds is sorted lexicographically -- "VR-PROMPT-007" sorts before
    both VR-SKILL entries -- so VR-PROMPT-007 is now the arithmetic root
    (primaryRiskId) for this detector's deductions, not VR-SKILL-004."""
    report = projection(manifest={"permissions": []})
    report["skillSandbox"] = sandbox_view(network_attempts=[
        {"host": "evil.example", "port": 443, "allowed": False}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {NETWORK_DETECTOR, "sandbox_network_attempt"}
    deduction = by_detector[NETWORK_DETECTOR]
    assert deduction["riskIds"] == ["VR-PROMPT-007", "VR-SKILL-004", "VR-SKILL-012"]
    assert deduction["primaryRiskId"] == "VR-PROMPT-007"
    assert deduction["sourceLayer"] == "V2_sandbox"
    assert deduction["severity"] == "high"


def test_undeclared_subprocess_attempt_deducts_against_all_three_risks_via_scoring():
    report = projection(manifest={"permissions": []})
    report["skillSandbox"] = sandbox_view(subprocess_attempts=[
        {"argv0": "/bin/sh", "argvPreview": ["sh", "-c", "id"]}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {SUBPROCESS_DETECTOR, "sandbox_subprocess_attempt"}
    deduction = by_detector[SUBPROCESS_DETECTOR]
    assert deduction["riskIds"] == ["VR-PROMPT-007", "VR-SKILL-004", "VR-SKILL-012"]
    assert deduction["primaryRiskId"] == "VR-PROMPT-007"
    assert deduction["sourceLayer"] == "V2_sandbox"
    assert deduction["severity"] == "high"


def test_declared_attempts_do_not_trip_either_new_mapping():
    report = projection(manifest={"permissions": ["WebFetch", "Bash(git:*)"]})
    report["skillSandbox"] = sandbox_view(
        network_attempts=[{"host": "api.example.com", "port": 443, "allowed": False}],
        subprocess_attempts=[{"argv0": "/usr/bin/git", "argvPreview": ["git", "status"]}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert by_detector.keys() == {"sandbox_network_attempt", "sandbox_subprocess_attempt"}


def test_no_attempts_produces_no_deduction_for_any_risk():
    report = projection(manifest={"permissions": []})
    report["skillSandbox"] = sandbox_view()
    score = compute_score(report)
    assert score["status"] == "available"
    assert score["deductions"] == []
