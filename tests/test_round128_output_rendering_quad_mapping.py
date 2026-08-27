"""Round 128: close VR-SKILL-010's V2_sandbox=none gap by reusing the
existing sandbox_injected_content_propagation signal a fourth time, rather
than adding a near-duplicate detector (standing initiative #2).

VR-SKILL-010 ("Unsafe output rendering or downstream handling") is defined
as: "Generated, retrieved, or user-controlled output reaches templates,
commands, browsers, or tools without context-appropriate validation or
escaping." Two of its four named sinks -- "commands" and "tools" -- are
exactly what sandbox_injected_content_propagation (Round 114) already
observes: a fixed synthetic decoy representing retrieved content, checked
for whether its canary marker propagates into a subprocessAttempts argv
(a command) or a networkAttempts host (a tool/service call). VR-SKILL-010's
own layerBoundaries.V2_sandbox text ("May observe actual rendering/tool
effects under isolation") does not contradict this -- "tool effects" is
precisely a subprocess/network sink. This is the same "N risks, one
detector" shape Round 92 established for a semantic_finding_type row and
Rounds 120/127 already extended twice for this exact sandbox_signal row
(VR-PROMPT-008, then VR-SKILL-013). This round adds a fourth: VR-SKILL-010.
Precedent for a detector reused this many times already exists --
capability_extractor ("skill.capability_facts.v1") maps to six riskIds.

Screened and declined alongside this candidate: VR-SKILL-015 ("SQL
injection via string-built queries") -- its V2_sandbox text ("May observe
an actual injected query reaching a database driver under isolation")
would need new instrumentation that intercepts database driver calls
(sqlite3/psycopg2/etc.), which the sandbox does not have today (only
fileEvents/networkAttempts/subprocessAttempts are observed) -- a genuinely
new capability, not a reuse, so out of scope for this round.

No live sandbox execution anywhere in this file -- follows the Round 89/
102/111/114/116/117/119/120/124/127 convention of testing signal-hit
behaviour directly against synthetic SandboxObservation-shaped dicts
(identical to Round 114/120/127's own fixtures, since this is the same
detector).
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
    # Quad-mapping a fourth riskId onto an existing row must not duplicate
    # or rename the detector itself.
    assert DETECTOR_ID in SANDBOX_SIGNAL_DETECTORS
    assert SANDBOX_SIGNAL_DETECTORS.count(DETECTOR_ID) == 1


def test_detector_now_maps_to_all_four_risks():
    mappings = load_detector_mappings()
    key = ("sandbox_signal", DETECTOR_ID)
    entry = mappings[key]
    assert entry["riskIds"] == [
        "VR-SKILL-005", "VR-PROMPT-008", "VR-SKILL-013", "VR-SKILL-010"]
    assert entry["contribution"] == "signal"


def test_vr_skill_010_v2_sandbox_coverage_is_now_signal():
    risks = load_risks()
    coverage = risks["VR-SKILL-010"]["currentCoverage"]
    assert coverage["V2_sandbox"] == "signal"
    # Unaffected layers stay exactly as they were before this round.
    assert coverage["L0_static"] == "signal"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"


def test_other_three_mapped_risks_are_unaffected_by_the_quad_mapping():
    risks = load_risks()
    skill_005 = risks["VR-SKILL-005"]["currentCoverage"]
    assert skill_005["V2_sandbox"] == "signal"
    assert skill_005["L0_static"] == "signal"
    assert skill_005["L1_semantic"] == "signal"
    assert skill_005["V1_5_blackbox"] == "none"
    prompt_008 = risks["VR-PROMPT-008"]["currentCoverage"]
    assert prompt_008["V2_sandbox"] == "signal"
    assert prompt_008["V1_5_blackbox"] == "signal"
    skill_013 = risks["VR-SKILL-013"]["currentCoverage"]
    assert skill_013["V2_sandbox"] == "signal"
    assert skill_013["L0_static"] == "none"
    assert skill_013["L1_semantic"] == "none"


def test_known_gaps_disclose_round_128_honestly():
    risks = load_risks()
    gaps = risks["VR-SKILL-010"]["knownGaps"]
    assert any("Round 128" in g for g in gaps)
    assert any("fourth, equally-valid risk mapping" in g for g in gaps)
    # The pre-existing gap bullets are untouched -- this reuse does not
    # provide a general source/sink graph, template escaping check, or
    # browser evaluation.
    assert "Only selected Jinja autoescape check" in gaps
    assert "No general source/sink graph" in gaps
    assert "No browser or tool-output evaluation" in gaps


def test_detector_mapping_total_row_count_is_unchanged():
    # Reusing an existing row for a fourth riskId must not create a new
    # row -- same invariant as Round 92/120/127's precedent.
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added


def test_runtime_detector_coverage_has_no_drift_after_quad_mapping():
    validate_runtime_detector_coverage()


def test_propagation_deducts_against_all_four_risks_via_scoring():
    """End-to-end check that the quad-mapped row is actually wired.

    riskIds is sorted lexicographically -- "VR-PROMPT-008" still sorts
    first, so it remains the arithmetic root (primaryRiskId), unaffected by
    adding the fourth riskId. Declares a Bash permission so Round 116's
    sandbox_undeclared_subprocess_attempt signal does not also co-fire.
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
