"""Round 127: close VR-SKILL-013's V2_sandbox=none gap by reusing the
existing sandbox_injected_content_propagation signal a third time, rather
than adding a near-duplicate detector (standing initiative #2).

VR-SKILL-013 ("Cross-file or cross-language unsafe data flow") is defined
almost verbatim as the mechanism sandbox_injected_content_propagation
(Round 114) already implements: "Untrusted data crosses files, modules,
processes, or language boundaries and reaches a privileged sink without
adequate validation." The fixed synthetic decoy this signal already plants
(a file at the sandboxed tmpdir root carrying an embedded canary marker)
IS untrusted data crossing a file boundary; a subprocessAttempts argv or
networkAttempts host containing that canary IS that data reaching a
privileged sink. VR-SKILL-013's own layerBoundaries.V2_sandbox text ("May
observe flows/effects that static analysis cannot resolve") does not
contradict this -- there genuinely is no call graph or taint engine
(L0_static/L1_semantic correctly stay "none"; L1_semantic's own boundary
text explicitly says it "cannot substitute for data-flow facts", and no
such fact exists here, unlike VR-SKILL-003's dependency_manifest capability
fact that Round 118 built on), and V1_5_blackbox stays "none" because its
own boundary text says "Not applicable" -- black-box probing cannot
observe cross-file/cross-process dataflow at all. Only V2_sandbox is
viably closable, and only by reusing the existing generic signal, exactly
the same shape Round 92 established for a semantic_finding_type row and
Round 120 established for this exact sandbox_signal row's second riskId
(VR-PROMPT-008). This round adds a third: VR-SKILL-013.

No live sandbox execution anywhere in this file -- follows the Round 89/
102/111/114/116/117/119/120/124 convention of testing signal-hit behaviour
directly against synthetic SandboxObservation-shaped dicts (identical to
Round 114/120's own fixtures, since this is the same detector).
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
    # Triple-mapping a third riskId onto an existing row must not duplicate
    # or rename the detector itself.
    assert DETECTOR_ID in SANDBOX_SIGNAL_DETECTORS
    assert SANDBOX_SIGNAL_DETECTORS.count(DETECTOR_ID) == 1


def test_detector_now_maps_to_all_three_risks():
    mappings = load_detector_mappings()
    key = ("sandbox_signal", DETECTOR_ID)
    entry = mappings[key]
    # Round 128 later added a fourth riskId, VR-SKILL-010, to this same row
    # -- see test_round128_output_rendering_quad_mapping.py. This round's
    # own triple-mapping invariant still holds; the row just grew a fourth
    # entry rather than being replaced.
    assert entry["riskIds"] == [
        "VR-SKILL-005", "VR-PROMPT-008", "VR-SKILL-013", "VR-SKILL-010"]
    assert entry["contribution"] == "signal"


def test_vr_skill_013_v2_sandbox_coverage_is_now_signal():
    risks = load_risks()
    coverage = risks["VR-SKILL-013"]["currentCoverage"]
    assert coverage["V2_sandbox"] == "signal"
    # Unaffected layers stay exactly as they were before this round -- none
    # of L0_static/L1_semantic/V1_5_blackbox are reachable by this reuse.
    assert coverage["L0_static"] == "none"
    assert coverage["L1_semantic"] == "none"
    assert coverage["V1_5_blackbox"] == "none"


def test_vr_skill_005_and_vr_prompt_008_coverage_are_unaffected_by_the_triple_mapping():
    risks = load_risks()
    skill_005 = risks["VR-SKILL-005"]["currentCoverage"]
    assert skill_005["V2_sandbox"] == "signal"
    assert skill_005["L0_static"] == "signal"
    assert skill_005["L1_semantic"] == "signal"
    assert skill_005["V1_5_blackbox"] == "none"
    prompt_008 = risks["VR-PROMPT-008"]["currentCoverage"]
    assert prompt_008["V2_sandbox"] == "signal"
    assert prompt_008["L0_static"] == "signal"
    assert prompt_008["L1_semantic"] == "signal"
    assert prompt_008["V1_5_blackbox"] == "signal"


def test_known_gaps_disclose_round_127_honestly():
    risks = load_risks()
    gaps = risks["VR-SKILL-013"]["knownGaps"]
    assert any("Round 127" in g for g in gaps)
    assert any("third, equally-valid risk mapping" in g for g in gaps)
    # The pre-existing gap bullets are untouched -- this reuse does not
    # provide a call graph, taint engine, or cross-process model.
    assert "No call graph" in gaps
    assert "No taint engine" in gaps
    assert "No cross-process model" in gaps


def test_detector_mapping_total_row_count_is_unchanged():
    # Reusing an existing row for a third riskId must not create a new
    # row -- same invariant as Round 92/120's dual-mapping precedent.
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added


def test_runtime_detector_coverage_has_no_drift_after_triple_mapping():
    validate_runtime_detector_coverage()


def test_propagation_deducts_against_all_three_risks_via_scoring():
    """End-to-end check that the triple-mapped row is actually wired.

    riskIds is sorted lexicographically -- "VR-PROMPT-008" sorts before
    both VR-SKILL entries -- so VR-PROMPT-008 remains the arithmetic root
    (primaryRiskId), unaffected by adding the third riskId. Declares a Bash
    permission so Round 116's sandbox_undeclared_subprocess_attempt signal
    does not also co-fire.

    Round 128 later added a fourth riskId, VR-SKILL-010, to this same row,
    which sorts between VR-SKILL-005 and VR-SKILL-013 and does not change
    the primaryRiskId.
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


def test_no_propagation_produces_no_deduction_for_any_risk():
    report = projection()
    report["skillSandbox"] = sandbox_view()
    score = compute_score(report)
    assert score["status"] == "available"
    assert score["deductions"] == []
