"""Round 116: new V2 sandbox signals -- sandbox_undeclared_network_attempt
and sandbox_undeclared_subprocess_attempt (standing initiative #2).

Closes part of VR-SKILL-004's ("Overbroad declared permissions or
capabilities") and VR-SKILL-012's ("Declared behavior differs from
implementation") V2_sandbox=none gap. Both risks' layerBoundaries.V2_sandbox
text already promised this ("May observe attempted capabilities and policy
denials" / "May compare actual attempted behavior with declarations"), but
no sandbox signal previously read the manifest at all.

Unlike every prior sandbox signal (Rounds 89/102/111/114), which observe the
SandboxObservation alone, this is the first signal that cross-references two
independent sources: the Skill's *declared* permission families (from its
manifest, via scoring.py::_declared_capability_families, which reuses
semantic/catalog.py::_permission_descriptor's own family-prefix rules so this
runtime comparison can never silently drift from the existing static
semantic.skill.permission_capability_mismatch comparison) against the
sandbox's *observed* runtime networkAttempts/subprocessAttempts. A bare
attempt is already caught by the pre-existing unconditional
sandbox_network_attempt/sandbox_subprocess_attempt signals -- these two new
signals fire only on the *undeclared* subset, which is qualitatively
stronger evidence of an overbroad/mismatched permission actually being
exploited at runtime, not just imported or call-sited.

Deliberately mirrors _permission_descriptor's existing precedent of NOT
treating a bare "*" wildcard permission as declaring every family: the
static comparison already treats "*" as unmatched (VR-SKILL-004 is
specifically about overbroad permissions, so a Skill hiding behind "*"
should not silently suppress this signal), and test_wildcard_permission_
does_not_suppress_the_signal below locks that in for the runtime side too.

No live sandbox execution anywhere in this file -- follows Round 89/102/
111/114's convention of testing signal-hit behaviour directly against
synthetic SandboxObservation-shaped dicts and a synthetic manifest dict.
"""
from __future__ import annotations

import verity.scoring as scoring
from verity.scoring import (_declared_capability_families,
                            _sandbox_signal_hit, compute_score)
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


def sandbox_view(file_events=None, network_attempts=None, subprocess_attempts=None):
    return {"status": "completed", "fileEvents": file_events or [],
            "networkAttempts": network_attempts or [],
            "subprocessAttempts": subprocess_attempts or []}


class TestDeclaredCapabilityFamilies:
    def test_no_manifest_yields_empty_set(self):
        assert _declared_capability_families({}) == set()

    def test_missing_permissions_key_yields_empty_set(self):
        assert _declared_capability_families({"description": "no perms field"}) == set()

    def test_webfetch_declares_network(self):
        fam = _declared_capability_families({"permissions": ["WebFetch"]})
        assert fam == {"network_access"}

    def test_bash_declares_process_execution(self):
        fam = _declared_capability_families({"permissions": ["Bash(git:*)"]})
        assert fam == {"process_execution"}

    def test_both_declared_together(self):
        fam = _declared_capability_families(
            {"permissions": ["WebFetch", "Bash(curl:*)"]})
        assert fam == {"network_access", "process_execution"}

    def test_read_only_permission_declares_neither(self):
        fam = _declared_capability_families({"permissions": ["Read"]})
        assert fam == set()

    def test_wildcard_permission_declares_neither(self):
        # Deliberate: mirrors _permission_descriptor's own precedent of not
        # resolving "*" to any specific family.
        fam = _declared_capability_families({"permissions": ["*"]})
        assert fam == set()

    def test_non_string_permission_entries_are_ignored_not_crashed_on(self):
        fam = _declared_capability_families({"permissions": [None, 42, "WebFetch"]})
        assert fam == {"network_access"}


class TestSignalHit:
    def test_network_attempt_with_no_declared_permissions_is_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "evil.example", "port": 443, "allowed": False}])
        assert _sandbox_signal_hit(NETWORK_DETECTOR, sandbox,
                                   declared_families=set()) is True

    def test_network_attempt_with_network_declared_is_not_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "evil.example", "port": 443, "allowed": False}])
        assert _sandbox_signal_hit(NETWORK_DETECTOR, sandbox,
                                   declared_families={"network_access"}) is False

    def test_no_network_attempt_is_not_a_hit_regardless_of_declaration(self):
        sandbox = sandbox_view()
        assert _sandbox_signal_hit(NETWORK_DETECTOR, sandbox,
                                   declared_families=set()) is False

    def test_subprocess_attempt_with_no_declared_permissions_is_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "/bin/sh", "argvPreview": ["sh", "-c", "id"]}])
        assert _sandbox_signal_hit(SUBPROCESS_DETECTOR, sandbox,
                                   declared_families=set()) is True

    def test_subprocess_attempt_with_process_declared_is_not_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "/bin/sh", "argvPreview": ["sh", "-c", "id"]}])
        assert _sandbox_signal_hit(SUBPROCESS_DETECTOR, sandbox,
                                   declared_families={"process_execution"}) is False

    def test_no_subprocess_attempt_is_not_a_hit_regardless_of_declaration(self):
        sandbox = sandbox_view()
        assert _sandbox_signal_hit(SUBPROCESS_DETECTOR, sandbox,
                                   declared_families=set()) is False

    def test_declared_families_defaults_to_empty_when_omitted(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "evil.example", "port": 443, "allowed": False}])
        assert _sandbox_signal_hit(NETWORK_DETECTOR, sandbox) is True


def test_signals_are_registered_in_the_fixed_vocabulary():
    for detector_id in (NETWORK_DETECTOR, SUBPROCESS_DETECTOR):
        assert detector_id in SANDBOX_SIGNAL_DETECTORS
        assert SANDBOX_SIGNAL_DETECTORS.count(detector_id) == 1


def test_detector_mappings_registered_for_both_risks():
    mappings = load_detector_mappings()
    # Round 129 later added a third riskId, VR-PROMPT-007, to both of these
    # rows -- see test_round129_excessive_authorization_triple_mapping.py.
    # This round's own dual-mapping invariant (VR-SKILL-004 + VR-SKILL-012)
    # still holds; the rows just grew a third entry rather than being
    # replaced.
    for detector_id in (NETWORK_DETECTOR, SUBPROCESS_DETECTOR):
        key = ("sandbox_signal", detector_id)
        assert key in mappings
        assert mappings[key]["riskIds"] == [
            "VR-SKILL-004", "VR-SKILL-012", "VR-PROMPT-007"]
        assert mappings[key]["contribution"] == "signal"


def test_risk_coverage_flipped_to_signal_for_both_risks():
    risks = load_risks()
    for risk_id in ("VR-SKILL-004", "VR-SKILL-012"):
        coverage = risks[risk_id]["currentCoverage"]
        assert coverage["V2_sandbox"] == "signal"
        # Unaffected layers stay exactly as they were before this round.
        assert coverage["L0_static"] == "signal"
        assert coverage["L1_semantic"] == "signal"
        assert coverage["V1_5_blackbox"] == "none"


def test_known_gaps_disclose_round_116_honestly():
    risks = load_risks()
    for risk_id in ("VR-SKILL-004", "VR-SKILL-012"):
        gaps = risks[risk_id]["knownGaps"]
        assert any("Round 116" in g for g in gaps)
        assert any("wildcard" in g for g in gaps)


def test_validate_runtime_detector_coverage_has_no_drift():
    validate_runtime_detector_coverage()


def test_undeclared_network_attempt_deducts_against_both_risks_via_scoring():
    """End-to-end: an undeclared network attempt trips BOTH the new signal
    (VR-SKILL-004/012) and the pre-existing bare sandbox_network_attempt
    signal (VR-SKILL-009) -- independent detectors evaluated independently,
    same as Round 114's propagation-signal precedent."""
    report = projection(manifest={"permissions": []})
    report["skillSandbox"] = sandbox_view(network_attempts=[
        {"host": "evil.example", "port": 443, "allowed": False}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {NETWORK_DETECTOR, "sandbox_network_attempt"}
    deduction = by_detector[NETWORK_DETECTOR]
    # Round 129 later added a third riskId, VR-PROMPT-007, to this row --
    # "VR-PROMPT-007" sorts before both VR-SKILL entries, so it is now the
    # primaryRiskId instead of VR-SKILL-004.
    assert deduction["riskIds"] == ["VR-PROMPT-007", "VR-SKILL-004", "VR-SKILL-012"]
    assert deduction["sourceLayer"] == "V2_sandbox"
    assert deduction["severity"] == "high"


def test_declared_network_attempt_only_trips_the_base_signal():
    """A Skill that DID declare WebFetch and then uses it should not also
    be flagged as having an undeclared capability -- only the pre-existing
    unconditional sandbox_network_attempt signal fires."""
    report = projection(manifest={"permissions": ["WebFetch"]})
    report["skillSandbox"] = sandbox_view(network_attempts=[
        {"host": "api.example.com", "port": 443, "allowed": False}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {"sandbox_network_attempt"}


def test_undeclared_subprocess_attempt_deducts_against_both_risks_via_scoring():
    report = projection(manifest={"permissions": []})
    report["skillSandbox"] = sandbox_view(subprocess_attempts=[
        {"argv0": "/bin/sh", "argvPreview": ["sh", "-c", "id"]}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {SUBPROCESS_DETECTOR, "sandbox_subprocess_attempt"}
    deduction = by_detector[SUBPROCESS_DETECTOR]
    # Round 129 later added a third riskId, VR-PROMPT-007, to this row --
    # "VR-PROMPT-007" sorts before both VR-SKILL entries, so it is now the
    # primaryRiskId instead of VR-SKILL-004.
    assert deduction["riskIds"] == ["VR-PROMPT-007", "VR-SKILL-004", "VR-SKILL-012"]
    assert deduction["sourceLayer"] == "V2_sandbox"
    assert deduction["severity"] == "high"


def test_declared_subprocess_attempt_only_trips_the_base_signal():
    report = projection(manifest={"permissions": ["Bash(git:*)"]})
    report["skillSandbox"] = sandbox_view(subprocess_attempts=[
        {"argv0": "/usr/bin/git", "argvPreview": ["git", "status"]}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {"sandbox_subprocess_attempt"}


def test_wildcard_permission_does_not_suppress_the_signal():
    """Deliberate design choice: "*" is not treated as declaring every
    family, matching the existing static comparison's precedent."""
    report = projection(manifest={"permissions": ["*"]})
    report["skillSandbox"] = sandbox_view(network_attempts=[
        {"host": "evil.example", "port": 443, "allowed": False}])
    score = compute_score(report)
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert NETWORK_DETECTOR in by_detector


def test_no_attempts_at_all_produces_no_new_deductions():
    report = projection(manifest={"permissions": []})
    report["skillSandbox"] = sandbox_view()
    score = compute_score(report)
    assert score["status"] == "available"
    assert score["deductions"] == []


def test_missing_artifact_model_does_not_crash_scoring():
    report = projection()
    del report["artifactModel"]
    report["skillSandbox"] = sandbox_view(network_attempts=[
        {"host": "evil.example", "port": 443, "allowed": False}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert NETWORK_DETECTOR in by_detector
