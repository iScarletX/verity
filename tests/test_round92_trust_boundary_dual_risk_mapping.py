"""Round 92: close VR-PROMPT-001's L1_semantic=none gap by reusing the
existing semantic.prompt.trust_boundary_ambiguity Finding Type, rather than
adding a near-duplicate detector.

VR-PROMPT-001 ("Instruction injection and priority override") and
VR-PROMPT-008 ("Untrusted content boundary is undefined") describe the same
underlying judgment from two risk angles: 008 is the missing separation
between untrusted content and trusted instructions; 001 is the resulting
override this separation gap enables. trust_boundary_ambiguity's own
confirmWhen policy -- "content can be interpreted as instructions and no
data-only boundary is declared" -- already states both halves of VR-PROMPT-
001's definition verbatim, so no new CATALOG entry, corpus fixture, or
threshold change is required: only the standards-layer risk-to-detector
mapping was missing.
"""
from verity.semantic.catalog import CATALOG
from verity.standards import (load_detector_mappings, load_risks,
                              validate_runtime_detector_coverage)

FINDING_TYPE = "semantic.prompt.trust_boundary_ambiguity"


def test_trust_boundary_ambiguity_now_maps_to_both_risks():
    mappings = load_detector_mappings()
    entry = mappings[("semantic_finding_type", FINDING_TYPE)]
    assert entry["riskIds"] == ["VR-PROMPT-008", "VR-PROMPT-001"]
    assert entry["contribution"] == "signal"


def test_vr_prompt_001_l1_semantic_coverage_is_now_signal():
    risks = load_risks()
    assert risks["VR-PROMPT-001"]["currentCoverage"]["L1_semantic"] == "signal"
    # L0_static and V1_5_blackbox coverage are untouched by this change.
    assert risks["VR-PROMPT-001"]["currentCoverage"]["L0_static"] == "signal"
    assert risks["VR-PROMPT-001"]["currentCoverage"]["V1_5_blackbox"] == "signal"


def test_finding_type_engine_is_within_both_risks_declared_scopes():
    definition, _extractor = CATALOG[FINDING_TYPE]
    risks = load_risks()
    for risk_id in ("VR-PROMPT-008", "VR-PROMPT-001"):
        assert definition.engine in risks[risk_id]["scopes"] or (
            definition.engine == "prompt"
            and "system_prompt" in risks[risk_id]["scopes"])


def test_runtime_detector_coverage_has_no_drift_after_dual_mapping():
    validate_runtime_detector_coverage()


def test_detector_mapping_count_is_unchanged_by_reusing_an_existing_detector():
    # Adding a second riskId to an existing row must not create a new row:
    # this closes a mapping gap, it does not add a new detector. (Rounds 93,
    # 94, and 95 each added an unrelated new detector row for a different
    # risk, so this count reflects those later additions too -- see
    # test_round93_*.py / test_round94_*.py / test_round95_*.py for the
    # assertions that those rounds' own rows are genuine net-new mappings.)
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added
