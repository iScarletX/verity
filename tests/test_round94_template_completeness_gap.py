"""Round 94: close VR-PROMPT-002's L1_semantic=none gap with a new Finding
Type, semantic.prompt.template_completeness_gap.

The deterministic prompt.unfilled_placeholder rule already proves the
mustache/dollar-brace/angle-bracket/square-bracket wrapped placeholder
syntax (e.g. "{{ name }}", "[INSERT NAME]"). This Finding Type covers the
disjoint L1 boundary: free-form prose placeholder/unfinished-template
language that never uses that deterministic syntax at all, e.g. "lorem
ipsum", unwrapped "insert your own ... here", or "still under
construction".
"""
from pathlib import Path

from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import CATALOG, extract_template_completeness_gap
from verity.semantic_benchmark import BUTLER_REFERENCE_SKILLS
from verity.standards import (load_detector_mappings, load_risks,
                              validate_runtime_detector_coverage)

FINDING_TYPE = "semantic.prompt.template_completeness_gap"
RISK_ID = "VR-PROMPT-002"


def test_catalog_has_new_finding_type_with_expected_shape():
    definition, extractor = CATALOG[FINDING_TYPE]
    assert definition.engine == "prompt"
    assert definition.defaultSeverity == "medium"
    assert definition.subjectKeyFields == ["templateGapKind"]
    assert extractor is extract_template_completeness_gap


def test_detector_mapping_targets_vr_prompt_002_as_signal():
    mappings = load_detector_mappings()
    entry = mappings[("semantic_finding_type", FINDING_TYPE)]
    assert entry["riskIds"] == [RISK_ID]
    assert entry["contribution"] == "signal"


def test_detector_mapping_count_grew_by_exactly_one_new_row():
    # Round 95 added its own unrelated new detector row afterwards, so this
    # count reflects that later addition too -- see test_round95_*.py for
    # the assertion that Round 95's own row is a genuine net-new mapping.
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added


def test_vr_prompt_002_l1_semantic_coverage_is_now_signal():
    risks = load_risks()
    coverage = risks[RISK_ID]["currentCoverage"]
    assert coverage["L1_semantic"] == "signal"
    assert coverage["L0_static"] == "partial"
    assert coverage["V1_5_blackbox"] == "none"
    assert coverage["V2_sandbox"] == "none"


def test_finding_type_engine_is_within_the_risks_declared_scope():
    definition, _extractor = CATALOG[FINDING_TYPE]
    risks = load_risks()
    assert definition.engine in risks[RISK_ID]["scopes"]


def test_runtime_detector_coverage_has_no_drift_after_new_mapping():
    validate_runtime_detector_coverage()


def test_butler_reference_skills_covers_the_new_finding_type():
    assert FINDING_TYPE in BUTLER_REFERENCE_SKILLS
    assert BUTLER_REFERENCE_SKILLS[FINDING_TYPE]


def test_extractor_seeds_on_prose_placeholder_language():
    text = (
        "You are a billing support assistant for Acme Corp.\n\n"
        "Escalation contact: to be filled in.\n\n"
        "When a customer requests a refund above $500, escalate to the "
        "contact listed above before approving payment.\n"
    )
    snap, data = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snap, data))
    seeds = extract_template_completeness_gap(review_to_dict(review), data)
    assert len(seeds) == 1
    source, _evidence_ids, evidence = seeds[0]
    assert source["triggerCount"] >= 1
    assert evidence[0]["metadata"]["evidenceRole"] == "prompt_analysis"


def test_extractor_still_seeds_on_a_disjoint_prose_phrase_lorem_ipsum():
    text = "Sample response format: lorem ipsum dolor sit amet.\n"
    snap, data = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snap, data))
    seeds = extract_template_completeness_gap(review_to_dict(review), data)
    assert len(seeds) == 1


def test_extractor_does_not_seed_without_prose_placeholder_language():
    text = "Answer the user's billing question directly and cite the invoice number.\n"
    snap, data = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snap, data))
    seeds = extract_template_completeness_gap(review_to_dict(review), data)
    assert seeds == []


def test_extractor_is_prompt_engine_only():
    assert extract_template_completeness_gap(
        {"engine": "skill", "snapshot": {"files": []}}, {}) == []


def test_extractor_does_not_fire_on_deterministic_bracket_syntax_alone():
    """Disjointness guard: the deterministic mustache/bracket syntax already
    covered by prompt.unfilled_placeholder must not, by itself, also seed
    this free-form-prose Finding Type."""
    text = "Send the report to {{ recipient_email }} every Friday.\n"
    snap, data = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snap, data))
    seeds = extract_template_completeness_gap(review_to_dict(review), data)
    assert seeds == []


def test_extractor_exposes_only_bounded_metadata(tmp_path):
    text = "Escalation contact: still under construction.\n"
    snap, data = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snap, data))
    seeds = extract_template_completeness_gap(review_to_dict(review), data)
    serialized = str(seeds)
    assert str(tmp_path) not in serialized
    _source, _evidence_ids, evidence = seeds[0]
    assert all(ev["sensitivity"] == "normal" for ev in evidence)
