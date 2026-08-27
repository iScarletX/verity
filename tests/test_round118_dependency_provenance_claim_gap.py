"""Round 118: close VR-SKILL-003's L1_semantic=none gap with a new Finding
Type, semantic.skill.dependency_provenance_claim_gap.

Screened alongside VR-PROMPT-005 (encoding -- deferred, its L0 detector
never surfaces findings into review_dict for a semantic extractor to
read), VR-SKILL-011 (credentials -- already generically covered by
declared_behavior_mismatch / permission_capability_mismatch, so a
dedicated extractor would duplicate rather than close a gap), and
VR-SKILL-013 (cross-file dataflow -- no capability fact exists for
sibling/local imports at all, needs new AST plumbing this round
deliberately avoids). VR-SKILL-003 was the only remaining L1_semantic=none
candidate buildable purely from an existing capability fact.

The existing `installation`/`dependency_manifest` capability fact
(capabilities.py) already records that a dependency-manifest file
(requirements.txt, pyproject.toml, package.json, or similar lockfile) is
present in the artifact snapshot -- zero new fact-extraction plumbing.
This Finding Type pairs that fact with the Manifest's own text: does the
Manifest claim to be self-contained or dependency-free while the snapshot
itself contains a dependency-manifest file? Mirrors the isolation_claim_
trust_gap (Round 100) / weak_crypto_sensitivity_gap (Round 96) "trust gap"
shape exactly. Deliberately narrow, per VR-SKILL-003's own L1_semantic
boundary ("should not invent dependency vulnerability facts") -- this
never asserts a CVE, a version claim, or that the dependency itself is
actually vulnerable, only a disclosure-framing gap between the Manifest's
own text and the presence of a dependency-manifest file.
"""
from verity.intake import intake_directory
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (CATALOG,
                                     extract_dependency_provenance_claim_gap)
from verity.semantic_benchmark import BUTLER_REFERENCE_SKILLS
from verity.standards import (load_detector_mappings, load_risks,
                              validate_runtime_detector_coverage)

FINDING_TYPE = "semantic.skill.dependency_provenance_claim_gap"
RISK_ID = "VR-SKILL-003"


def test_catalog_has_new_finding_type_with_expected_shape():
    definition, extractor = CATALOG[FINDING_TYPE]
    assert definition.engine == "skill"
    assert definition.defaultSeverity == "medium"
    assert definition.subjectKeyFields == ["provenanceClaimGapKind"]
    assert extractor is extract_dependency_provenance_claim_gap


def test_detector_mapping_targets_vr_skill_003_as_signal():
    mappings = load_detector_mappings()
    entry = mappings[("semantic_finding_type", FINDING_TYPE)]
    assert entry["riskIds"] == [RISK_ID]
    assert entry["contribution"] == "signal"


def test_detector_mapping_count_grew_by_exactly_one_new_row():
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added
    # signal + Round 121's semantic row + Round 122's blackbox_scenario row
    # + Round 123's semantic_finding_type row + Round 124's sandbox_signal row
    # + Round 125's blackbox_scenario row + Round 126's blackbox_scenario row


def test_vr_skill_003_l1_semantic_coverage_is_now_signal():
    risks = load_risks()
    coverage = risks[RISK_ID]["currentCoverage"]
    assert coverage["L1_semantic"] == "signal"
    # Unaffected layers stay exactly as they were before this round.
    assert coverage["L0_static"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"
    # Round 119 later flipped V2_sandbox from "none" to "signal"
    # (sandbox_dependency_install_attempt) -- was "none" at this round.
    assert coverage["V2_sandbox"] == "signal"


def test_finding_type_engine_is_within_the_risks_declared_scope():
    definition, _extractor = CATALOG[FINDING_TYPE]
    risks = load_risks()
    assert definition.engine in risks[RISK_ID]["scopes"]


def test_runtime_detector_coverage_has_no_drift_after_new_mapping():
    validate_runtime_detector_coverage()


def test_butler_reference_skills_covers_the_new_finding_type():
    assert FINDING_TYPE in BUTLER_REFERENCE_SKILLS
    assert BUTLER_REFERENCE_SKILLS[FINDING_TYPE]


def test_extractor_seeds_with_candidate_hint_on_self_contained_claim(tmp_path):
    root = tmp_path / "self-contained-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: self-contained-skill\n"
        "description: A fully self-contained skill with no external "
        "dependencies.\nallowed-tools: Read\n---\n")
    (root / "requirements.txt").write_text("requests==2.31.0\n")
    (root / "run.py").write_text("import requests\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_dependency_provenance_claim_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, evidence = seeds[0]
    assert source["dependencyManifestFactCount"] >= 1
    assert source["candidateHints"]
    assert source["candidateHints"][0]["subject"]["provenanceClaimGapKind"] == (
        "undisclosed_external_dependency")
    paths = {loc["artifactPath"] for ev in evidence for loc in ev["locations"]}
    assert paths == {"SKILL.md", "requirements.txt"}


def test_extractor_skips_candidate_hint_on_disclosed_dependency_framing(tmp_path):
    root = tmp_path / "disclosed-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: disclosed-skill\n"
        "description: Requires the following dependencies to run properly.\n"
        "allowed-tools: Read\n---\n")
    (root / "requirements.txt").write_text("requests==2.31.0\n")
    (root / "run.py").write_text("import requests\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_dependency_provenance_claim_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, _evidence = seeds[0]
    assert "candidateHints" not in source
    assert source["modelCandidatePolicy"] == "skip_without_catalog_hint"


def test_extractor_does_not_seed_without_a_dependency_manifest_fact(tmp_path):
    root = tmp_path / "no-deps-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: no-deps-skill\ndescription: Reads a plain text file.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "with open('notes.txt') as f:\n    text = f.read()\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_dependency_provenance_claim_gap(review_to_dict(review), data)
    assert seeds == []


def test_extractor_is_skill_engine_only():
    assert extract_dependency_provenance_claim_gap(
        {"engine": "prompt", "snapshot": {"files": []}}, {}) == []


def test_extractor_exposes_only_bounded_metadata_and_normal_evidence(tmp_path):
    root = tmp_path / "bounded-pyproject"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: bounded-pyproject\n"
        "description: A zero dependencies, dependency-free helper skill.\n"
        "allowed-tools: Read\n---\n")
    (root / "pyproject.toml").write_text("[project]\nname = \"bounded\"\n")
    (root / "run.py").write_text("print('hello')\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_dependency_provenance_claim_gap(review_to_dict(review), data)
    assert seeds
    serialized = str(seeds)
    assert str(tmp_path) not in serialized
    _source, _evidence_ids, evidence = seeds[0]
    assert all(ev["sensitivity"] == "normal" for ev in evidence)
