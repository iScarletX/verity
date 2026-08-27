"""Round 100: close VR-SKILL-014's L1_semantic=none gap with a new Finding
Type, semantic.skill.isolation_claim_trust_gap.
"""
from verity.intake import intake_directory
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import CATALOG, extract_isolation_claim_trust_gap
from verity.semantic_benchmark import BUTLER_REFERENCE_SKILLS
from verity.standards import (load_detector_mappings, load_risks,
                              validate_runtime_detector_coverage)

FINDING_TYPE = "semantic.skill.isolation_claim_trust_gap"
RISK_ID = "VR-SKILL-014"


def test_catalog_has_new_finding_type_with_expected_shape():
    definition, extractor = CATALOG[FINDING_TYPE]
    assert definition.engine == "skill"
    assert definition.defaultSeverity == "high"
    assert definition.subjectKeyFields == ["isolationTrustGapKind"]
    assert extractor is extract_isolation_claim_trust_gap


def test_detector_mapping_targets_vr_skill_014_as_signal():
    mappings = load_detector_mappings()
    entry = mappings[("semantic_finding_type", FINDING_TYPE)]
    assert entry["riskIds"] == [RISK_ID]
    assert entry["contribution"] == "signal"


def test_detector_mapping_count_grew_by_exactly_one_new_row():
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added


def test_vr_skill_014_l1_semantic_coverage_is_now_signal():
    risks = load_risks()
    coverage = risks[RISK_ID]["currentCoverage"]
    assert coverage["L1_semantic"] == "signal"


def test_finding_type_engine_is_within_the_risks_declared_scope():
    definition, _extractor = CATALOG[FINDING_TYPE]
    risks = load_risks()
    assert definition.engine in risks[RISK_ID]["scopes"]


def test_runtime_detector_coverage_has_no_drift_after_new_mapping():
    validate_runtime_detector_coverage()


def test_butler_reference_skills_covers_the_new_finding_type():
    assert FINDING_TYPE in BUTLER_REFERENCE_SKILLS
    assert BUTLER_REFERENCE_SKILLS[FINDING_TYPE]


def test_extractor_seeds_with_candidate_hint_on_isolation_claim_framing(tmp_path):
    root = tmp_path / "isolation-claim-subprocess"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: isolation-claim-subprocess\n"
        "description: Runs entirely locally in an isolated sandbox with no "
        "subprocess and no external commands.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "import subprocess\n"
        "subprocess.run(['ls', '-la'])\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_isolation_claim_trust_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, evidence = seeds[0]
    assert source["hostFacingFactCount"] >= 1
    assert source["candidateHints"]
    assert source["candidateHints"][0]["subject"]["isolationTrustGapKind"] == (
        "contradicted_isolation_claim")
    paths = {loc["artifactPath"] for ev in evidence for loc in ev["locations"]}
    assert paths == {"SKILL.md", "run.py"}


def test_extractor_skips_candidate_hint_on_disclosed_host_access_framing(tmp_path):
    root = tmp_path / "isolation-claim-disclosed"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: isolation-claim-disclosed\n"
        "description: Spawns a subprocess to run the bundled ls command as "
        "part of its documented function.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "import subprocess\n"
        "subprocess.run(['ls', '-la'])\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_isolation_claim_trust_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, _evidence = seeds[0]
    assert "candidateHints" not in source
    assert source["modelCandidatePolicy"] == "skip_without_catalog_hint"


def test_extractor_does_not_seed_without_a_host_facing_fact(tmp_path):
    root = tmp_path / "no-host-fact"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: no-host-fact\ndescription: Runs entirely locally in an "
        "isolated sandbox with no network access.\nallowed-tools: Read\n---\n")
    (root / "run.py").write_text("x = 1 + 1\nprint(x)\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_isolation_claim_trust_gap(review_to_dict(review), data)
    assert seeds == []


def test_extractor_skips_candidate_hint_when_manifest_makes_no_isolation_claim(
        tmp_path):
    root = tmp_path / "isolation-claim-silent"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: isolation-claim-silent\n"
        "description: Lists files in the current directory for the user.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "import subprocess\n"
        "subprocess.run(['ls', '-la'])\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_isolation_claim_trust_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, _evidence = seeds[0]
    assert "candidateHints" not in source
    assert source["modelCandidatePolicy"] == "skip_without_catalog_hint"


def test_extractor_detects_network_based_host_access_construction(tmp_path):
    root = tmp_path / "isolation-claim-network"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: isolation-claim-network\n"
        "description: Does not access the network; runs fully offline "
        "using only bundled local data.\nallowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "import requests\n"
        "response = requests.get('https://api.example.com/status')\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_isolation_claim_trust_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, _evidence = seeds[0]
    assert source["candidateHints"]


def test_extractor_is_skill_engine_only():
    assert extract_isolation_claim_trust_gap(
        {"engine": "prompt", "snapshot": {"files": []}}, {}) == []


def test_extractor_exposes_only_bounded_metadata_and_normal_evidence(tmp_path):
    root = tmp_path / "bounded-isolation-claim"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: bounded-isolation-claim\n"
        "description: Runs entirely locally in an isolated sandbox with no "
        "subprocess and no external commands.\nallowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "import subprocess\n"
        "subprocess.run(['ls', '-la'])\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_isolation_claim_trust_gap(review_to_dict(review), data)
    assert seeds
    serialized = str(seeds)
    assert str(tmp_path) not in serialized
    _source, _evidence_ids, evidence = seeds[0]
    assert all(ev["sensitivity"] == "normal" for ev in evidence)
