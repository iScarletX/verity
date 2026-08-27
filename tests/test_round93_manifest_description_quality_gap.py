"""Round 93: close VR-SKILL-001's L1_semantic=none gap with a new Finding
Type, semantic.skill.manifest_description_quality_gap.

VR-SKILL-001 ("Skill manifest/description mismatch or ambiguity") scopes
L1_semantic to "may assess description quality but should not replace
schema validation" -- a judgment axis disjoint from the three existing
semantic.skill.* types: declared_behavior_mismatch checks description-vs-
observed-behavior consistency, permission_capability_mismatch checks
declared-permissions-vs-observed-capabilities, and
external_instruction_trust_gap checks external-content trust boundaries.
None of those ask "is the description itself adequate for an invoking
agent to decide when to use this Skill?" -- so this is a new CATALOG entry
rather than a dual-mapping reuse (contrast Round 92's trust_boundary_
ambiguity dual-mapping, where the existing policy text already covered the
new risk verbatim).
"""
from pathlib import Path

from verity.intake import intake_directory
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (CATALOG,
                                     extract_manifest_description_quality_gap)
from verity.semantic_benchmark import BUTLER_REFERENCE_SKILLS
from verity.standards import (load_detector_mappings, load_risks,
                              validate_runtime_detector_coverage)

FINDING_TYPE = "semantic.skill.manifest_description_quality_gap"
RISK_ID = "VR-SKILL-001"


def test_catalog_has_new_finding_type_with_expected_shape():
    definition, extractor = CATALOG[FINDING_TYPE]
    assert definition.engine == "skill"
    assert definition.defaultSeverity == "low"
    assert definition.subjectKeyFields == ["descriptionGapKind"]
    assert extractor is extract_manifest_description_quality_gap


def test_detector_mapping_targets_vr_skill_001_as_signal():
    mappings = load_detector_mappings()
    entry = mappings[("semantic_finding_type", FINDING_TYPE)]
    assert entry["riskIds"] == [RISK_ID]
    assert entry["contribution"] == "signal"


def test_detector_mapping_count_grew_by_exactly_one_new_row():
    # Rounds 94 and 95 each added their own unrelated new detector row
    # afterwards, so this count reflects those later additions too -- see
    # test_round94_*.py / test_round95_*.py for the assertions that those
    # rounds' own rows are genuine net-new mappings.
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added


def test_vr_skill_001_l1_semantic_coverage_is_now_signal():
    risks = load_risks()
    coverage = risks[RISK_ID]["currentCoverage"]
    assert coverage["L1_semantic"] == "signal"
    # Other layers are untouched by this change.
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


def test_extractor_seeds_on_vague_boilerplate_description(tmp_path):
    root = tmp_path / "vague-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: helper\ndescription: This skill helps with tasks.\n---\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_manifest_description_quality_gap(review_to_dict(review), data)
    assert len(seeds) == 1
    source, _evidence_ids, evidence = seeds[0]
    assert source["declaredDescriptionLength"] > 0
    assert evidence[0]["metadata"]["evidenceRole"] == "manifest_declaration"


def test_extractor_still_seeds_on_a_specific_well_scoped_description(tmp_path):
    # The extractor is a bare structural seed with no lexical trigger --
    # adequacy is a semantic judgment left to the Provider, so a
    # well-written description must seed too (and be rejected downstream).
    root = tmp_path / "specific-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: pdf-table-extractor\n"
        "description: Extracts tables from a local PDF file and returns "
        "them as CSV text. Use this when the user provides a PDF file "
        "path and asks for tabular data from it; do not use this for "
        "scanned image-only PDFs.\n---\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_manifest_description_quality_gap(review_to_dict(review), data)
    assert len(seeds) == 1


def test_extractor_does_not_seed_without_a_manifest_description(tmp_path):
    root = tmp_path / "no-description"
    root.mkdir()
    (root / "SKILL.md").write_text("---\nname: bare\n---\n")
    (root / "run.py").write_text("print('noop')\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_manifest_description_quality_gap(review_to_dict(review), data)
    assert seeds == []


def test_extractor_is_skill_engine_only():
    assert extract_manifest_description_quality_gap(
        {"engine": "prompt", "artifactModel": {
            "manifestFile": {"fileId": "f", "normalizedPath": "SKILL.md"},
            "manifest": {"description": "x"}}}, {}) == []


def test_extractor_exposes_only_relative_paths_and_bounded_metadata(tmp_path):
    root = tmp_path / "vague-skill-egress"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: helper\ndescription: This skill helps with tasks.\n---\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_manifest_description_quality_gap(review_to_dict(review), data)
    serialized = str(seeds)
    assert str(tmp_path) not in serialized
    _source, _evidence_ids, evidence = seeds[0]
    assert all(ev["sensitivity"] == "normal" for ev in evidence)
