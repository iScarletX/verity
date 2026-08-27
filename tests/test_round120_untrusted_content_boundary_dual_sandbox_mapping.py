"""Round 120: close VR-PROMPT-008's V2_sandbox=none gap by reusing the
existing sandbox_injected_content_propagation signal, rather than adding a
near-duplicate detector (standing initiative #2).

VR-SKILL-005 ("Untrusted external instructions or tool content") and
VR-PROMPT-008 ("Untrusted content boundary is undefined") describe the same
underlying runtime behavior from two risk angles: 005 is Skill-specific
(fetching external content and treating it as trusted instructions without
provenance/validation/isolation), 008 is the broader missing separation
between untrusted content and trusted instructions across prompt and skill
scopes alike. VR-PROMPT-008's own layerBoundaries.V2_sandbox text ("May
observe how a Skill propagates retrieved content into tools and prompts")
already describes, almost verbatim, the exact mechanism
sandbox_injected_content_propagation (Round 114) implements: a fixed
synthetic decoy representing retrieved/tool-produced content, and a check
for whether its canary marker propagates into a subprocess or network
sink. No new CATALOG entry, runner.py decoy, or scoring.py branch is
required -- only the standards-layer risk-to-detector mapping was missing,
the exact same shape Round 92 established for a semantic_finding_type row
(test_round92_trust_boundary_dual_risk_mapping.py).

Screened and declined alongside this candidate: VR-SKILL-001 (its
V2_sandbox boundary text, "may observe compatibility failures", would need
a raisedException/crash-based signal -- sandbox/models.py's own docstring
already deliberately excludes crash/exception terminal states as "equally
consistent with an inefficient/buggy Skill as with a hostile one", so this
stays out of scope). VR-PROMPT-005 (encoding) was also considered -- its
V2_sandbox text ("may observe decoded runtime artifacts for Skills") would
need to correlate a specific L0/L1-identified encoded byte span with a
runtime-decoded value, new cross-layer plumbing this round deliberately
avoids, same shape as VR-SKILL-013 being previously declined for needing
taint/dataflow analysis. VR-MCP-001 requires an MCP intake pipeline that
does not exist at all yet ("No MCP intake" in its own knownGaps) -- out of
scope for an incremental detector addition.

No live sandbox execution anywhere in this file -- follows the Round 89/
102/111/114/116/117/119 convention of testing signal-hit behaviour
directly against synthetic SandboxObservation-shaped dicts (identical to
Round 114's own fixtures, since this is the same detector).
"""
from __future__ import annotations

from verity.scoring import compute_score
from verity.sandbox.models import SANDBOX_SIGNAL_DETECTORS
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


def sandbox_view(subprocess_attempts=None, network_attempts=None):
    return {"status": "completed", "fileEvents": [],
            "networkAttempts": network_attempts or [],
            "subprocessAttempts": subprocess_attempts or []}


def test_signal_is_still_a_single_registered_detector():
    # Dual-mapping a second riskId onto an existing row must not duplicate
    # or rename the detector itself.
    assert DETECTOR_ID in SANDBOX_SIGNAL_DETECTORS
    assert SANDBOX_SIGNAL_DETECTORS.count(DETECTOR_ID) == 1


def test_detector_now_maps_to_both_risks():
    mappings = load_detector_mappings()
    key = ("sandbox_signal", DETECTOR_ID)
    entry = mappings[key]
    # Round 127 later added a third riskId, VR-SKILL-013, to this same row
    # -- see test_round127_cross_language_dataflow_triple_mapping.py. Round
    # 128 later added a fourth, VR-SKILL-010 -- see
    # test_round128_output_rendering_quad_mapping.py. This round's own
    # dual-mapping invariant (VR-SKILL-005 + VR-PROMPT-008) still holds; the
    # row just grew two more entries rather than being replaced.
    assert entry["riskIds"] == [
        "VR-SKILL-005", "VR-PROMPT-008", "VR-SKILL-013", "VR-SKILL-010"]
    assert entry["contribution"] == "signal"


def test_vr_prompt_008_v2_sandbox_coverage_is_now_signal():
    risks = load_risks()
    coverage = risks["VR-PROMPT-008"]["currentCoverage"]
    assert coverage["V2_sandbox"] == "signal"
    # Unaffected layers stay exactly as they were before this round.
    assert coverage["L0_static"] == "signal"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "signal"


def test_vr_skill_005_coverage_is_unaffected_by_the_dual_mapping():
    risks = load_risks()
    coverage = risks["VR-SKILL-005"]["currentCoverage"]
    assert coverage["V2_sandbox"] == "signal"
    assert coverage["L0_static"] == "signal"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"


def test_known_gaps_disclose_round_120_honestly():
    risks = load_risks()
    gaps = risks["VR-PROMPT-008"]["knownGaps"]
    assert any("Round 120" in g for g in gaps)
    assert any("prompts' half of this risk's own V2_sandbox boundary text "
               "remains unobserved" in g for g in gaps)


def test_detector_mapping_total_row_count_is_unchanged():
    # Reusing an existing row for a second riskId must not create a new
    # row -- same invariant as Round 92's dual-mapping precedent.
    # (Rounds 124/125/126 later each added their own new row, bumping this
    # fixed snapshot from 139 -> 140 -> 141 -> 142; that growth is unrelated
    # to this round's dual-mapping invariant, which this test still guards.)
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added


def test_runtime_detector_coverage_has_no_drift_after_dual_mapping():
    validate_runtime_detector_coverage()


def test_propagation_deducts_against_both_risks_via_scoring():
    """End-to-end check that the dual-mapped row is actually wired.

    riskIds is sorted lexicographically -- "VR-PROMPT-008" sorts before
    "VR-SKILL-005" -- so VR-PROMPT-008 is now the arithmetic root
    (primaryRiskId) for this deduction, while VR-SKILL-005 remains visible
    in the explanation. Declares a Bash permission so Round 116's
    sandbox_undeclared_subprocess_attempt signal does not also co-fire.

    Round 127 later added a third riskId, VR-SKILL-013, and Round 128 a
    fourth, VR-SKILL-010, to this same row; neither changes the
    primaryRiskId.
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
    assert deduction["riskIds"] == [
        "VR-PROMPT-008", "VR-SKILL-005", "VR-SKILL-010", "VR-SKILL-013"]
    assert deduction["primaryRiskId"] == "VR-PROMPT-008"
    assert deduction["sourceLayer"] == "V2_sandbox"
    assert deduction["severity"] == "high"


def test_no_propagation_produces_no_deduction_for_either_risk():
    report = projection()
    report["skillSandbox"] = sandbox_view()
    score = compute_score(report)
    assert score["status"] == "available"
    assert score["deductions"] == []
