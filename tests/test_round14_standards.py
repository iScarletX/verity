"""Round 14: authoritative source, taxonomy, and detector-map gates."""
import copy
import json
from pathlib import Path

import pytest

from verity.standards import (
    COVERAGE_LEVELS,
    DETECTION_LAYERS,
    DETECTOR_TYPES,
    StandardsError,
    load_detector_candidates,
    load_detector_mappings,
    load_risks,
    load_sources,
    summarize_coverage,
    validate_runtime_detector_coverage,
)


def test_primary_source_registry_is_strict_and_public_https():
    sources = load_sources()
    assert len(sources) >= 15
    assert {
        "OWASP-LLM-2025", "OWASP-AGENTIC-2025",
        "OWASP-AGENTIC-TOP10-2026", "NIST-AI-RMF-1.0",
        "NIST-AI-600-1", "MITRE-ATLAS", "CWE-4.20", "CAPEC-3.9",
        "SLSA-1.2", "OPENSSF-SCORECARD", "AGENT-SKILLS-SPEC",
        "MCP-SECURITY-2025-11-25",
    } <= set(sources)
    assert all(s["url"].startswith("https://") for s in sources.values())


def test_every_risk_has_traceability_boundaries_and_visible_gaps():
    risks = load_risks()
    assert len(risks) >= 20
    assert any("prompt" in r["scopes"] for r in risks.values())
    assert any("skill" in r["scopes"] for r in risks.values())
    assert any("mcp" in r["scopes"] for r in risks.values())
    for risk in risks.values():
        assert risk["sourceRefs"] or risk.get("verityOriginalRationale")
        assert risk["knownGaps"]
        assert set(risk["layerBoundaries"]) == {
            "L0_static", "L1_semantic", "V1_5_blackbox", "V2_sandbox",
            "V2_agent_runtime",
        }


def test_no_pre_corpus_claim_exceeds_partial():
    risks = load_risks()
    ceiling = COVERAGE_LEVELS.index("partial")
    assert all(
        COVERAGE_LEVELS.index(level) <= ceiling
        for risk in risks.values()
        for level in risk["currentCoverage"].values()
    )


def test_detector_candidate_decisions_are_traceable_and_controlled():
    candidates = load_detector_candidates()
    assert set(candidates) == {"osv-scanner", "shellcheck", "semgrep-oss",
                               "gitleaks"}
    assert candidates["osv-scanner"]["decision"] == "adopt_next"
    assert candidates["shellcheck"]["decision"] == "defer_license_review"
    assert any("metrics off" in control
               for control in candidates["semgrep-oss"]["requiredControls"])
    assert candidates["gitleaks"]["maintenance"] == (
        "feature-complete_security-fixes")


def test_every_runtime_detector_is_mapped_exactly_once():
    validate_runtime_detector_coverage()
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added


def test_agent_runtime_is_a_first_class_detection_layer_and_detector_type():
    assert DETECTION_LAYERS == (
        "L0_static",
        "L1_semantic",
        "V1_5_blackbox",
        "V2_sandbox",
        "V2_agent_runtime",
    )
    assert "agent_runtime_signal" in DETECTOR_TYPES


def test_agent_runtime_mapping_drift_is_rejected(monkeypatch):
    from verity.agent_runtime import models as runtime_models

    monkeypatch.setattr(
        runtime_models,
        "AGENT_RUNTIME_SIGNAL_DETECTORS",
        (*runtime_models.AGENT_RUNTIME_SIGNAL_DETECTORS, "unmapped_test_signal"),
    )

    with pytest.raises(StandardsError, match="agent runtime signal mapping drift"):
        validate_runtime_detector_coverage()


def test_taxonomy_exposes_known_high_value_gaps():
    risks = load_risks()
    assert risks["VR-SKILL-013"]["currentCoverage"]["L0_static"] == "none"
    assert risks["VR-MCP-001"]["currentCoverage"]["L0_static"] == "none"
    # Round 127 later flipped V2_sandbox from "none" to "signal" (triple-
    # mapped the existing sandbox_injected_content_propagation signal) --
    # was "none" at this round.
    assert risks["VR-SKILL-013"]["currentCoverage"]["V2_sandbox"] == "signal"
    assert risks["VR-SKILL-013"]["currentCoverage"]["V1_5_blackbox"] == "none"


def test_execution_status_and_capability_breadth_are_separate():
    summary = summarize_coverage()
    assert summary["riskCount"] >= 20
    # Runtime reports use completed/failed/not_enabled; taxonomy breadth
    # deliberately uses a different vocabulary.
    breadth_words = set(COVERAGE_LEVELS)
    runtime_words = {"completed", "failed", "not_enabled"}
    assert breadth_words.isdisjoint(runtime_words)
    assert sum(summary["byLayer"]["L0_static"].values()) == summary["riskCount"]
    assert summary["byLayer"]["V2_agent_runtime"] == {
        "none": 42,
        "signal": 4,
        "partial": 0,
        "substantial": 0,
        "evaluated": 0,
    }


def test_loader_rejects_unknown_source_control(monkeypatch):
    from verity import standards
    original = standards._load

    def corrupted(name):
        value = copy.deepcopy(original(name))
        if name == "risks.json":
            value["risks"][0]["sourceRefs"][0]["controlIds"] = ["INVENTED"]
        return value

    monkeypatch.setattr(standards, "_load", corrupted)
    with pytest.raises(StandardsError, match="unknown source control"):
        standards.load_risks()


def test_loader_rejects_stronger_claim_without_evaluation(monkeypatch):
    from verity import standards
    original = standards._load

    def inflated(name):
        value = copy.deepcopy(original(name))
        if name == "risks.json":
            value["risks"][0]["currentCoverage"]["L0_static"] = "evaluated"
        return value

    monkeypatch.setattr(standards, "_load", inflated)
    with pytest.raises(StandardsError, match="corpus evidence"):
        standards.load_risks()


def test_loader_rejects_unmapped_new_rule(monkeypatch):
    from verity import standards
    from verity.registry import RuleDefinition
    from verity import builtins

    original = standards.build_prompt_rule_registry if hasattr(
        standards, "build_prompt_rule_registry") else None
    # The production validator imports inside the function. Mutate the
    # builtins factory return in a controlled wrapper to simulate rule drift.
    real_factory = builtins.build_prompt_rule_registry

    def with_unmapped(ft):
        registry = real_factory(ft)
        registry.register(RuleDefinition(
            ruleId="prompt.unmapped_test_rule", ruleVersion="1.0.0",
            supersedes=[], engine="prompt", title="controlled test",
            findingType="prompt.instruction_override_marker",
            implementationId="test", applicableKinds=["prompt"],
            requiredEvidenceKinds=["source_span"], defaultSeverity="low",
        ))
        return registry

    monkeypatch.setattr(builtins, "build_prompt_rule_registry", with_unmapped)
    with pytest.raises(StandardsError, match="mapping drift"):
        validate_runtime_detector_coverage()
