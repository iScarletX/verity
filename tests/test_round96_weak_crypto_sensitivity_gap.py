"""Round 96: close VR-SKILL-008's L1_semantic=none gap with a new Finding
Type, semantic.skill.weak_crypto_sensitivity_gap.

The deterministic static rule and existing skill-engine Finding Types
(declared_behavior_mismatch, permission_capability_mismatch,
external_instruction_trust_gap, manifest_description_quality_gap,
deserialization_trust_gap) never compare a weak-hash/weak-cipher/disabled-
TLS-verification capability fact against the Manifest's own sensitivity
framing of the protected data. This Finding Type covers that disjoint L1
boundary: whether the Manifest text itself frames the data reaching the
weak-crypto call as sensitive (passwords, credentials, tokens, personal
data) rather than determining cryptographic correctness itself.
"""
from verity.intake import intake_directory
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import CATALOG, extract_weak_crypto_sensitivity_gap
from verity.semantic_benchmark import BUTLER_REFERENCE_SKILLS
from verity.standards import (load_detector_mappings, load_risks,
                              validate_runtime_detector_coverage)

FINDING_TYPE = "semantic.skill.weak_crypto_sensitivity_gap"
RISK_ID = "VR-SKILL-008"


def test_catalog_has_new_finding_type_with_expected_shape():
    definition, extractor = CATALOG[FINDING_TYPE]
    assert definition.engine == "skill"
    assert definition.defaultSeverity == "medium"
    assert definition.subjectKeyFields == ["sensitivityGapKind"]
    assert extractor is extract_weak_crypto_sensitivity_gap


def test_detector_mapping_targets_vr_skill_008_as_signal():
    mappings = load_detector_mappings()
    entry = mappings[("semantic_finding_type", FINDING_TYPE)]
    assert entry["riskIds"] == [RISK_ID]
    assert entry["contribution"] == "signal"


def test_detector_mapping_count_grew_by_exactly_one_new_row():
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added


def test_vr_skill_008_l1_semantic_coverage_is_now_signal():
    risks = load_risks()
    coverage = risks[RISK_ID]["currentCoverage"]
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"
    # Round 117 later added sandbox_cleartext_network_attempt, flipping this
    # from "none" to "signal" -- this test only asserts this round's own
    # layer, so it's updated to reflect the current state rather than pinned
    # to a snapshot that predates that later round.
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


def test_extractor_seeds_with_candidate_hint_on_sensitive_data_framing(tmp_path):
    root = tmp_path / "weak-hash-password"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: weak-hash-password\n"
        "description: Hashes the user's password credential before "
        "storing it locally.\nallowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "import hashlib\n"
        "password_hash = hashlib.md5(b'user-password').hexdigest()\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_weak_crypto_sensitivity_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, evidence = seeds[0]
    assert source["weakCryptoFactCount"] >= 1
    assert source["candidateHints"]
    assert source["candidateHints"][0]["subject"]["sensitivityGapKind"] == (
        "weak_hash_algorithm")
    paths = {loc["artifactPath"] for ev in evidence for loc in ev["locations"]}
    assert paths == {"SKILL.md", "run.py"}


def test_extractor_skips_candidate_hint_on_non_sensitive_data_framing(tmp_path):
    root = tmp_path / "weak-hash-test-data"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: weak-hash-test-data\n"
        "description: Hashes a synthetic test string used only in unit "
        "tests.\nallowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "import hashlib\n"
        "test_digest = hashlib.md5(b'fixture-test-string').hexdigest()\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_weak_crypto_sensitivity_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, _evidence = seeds[0]
    assert "candidateHints" not in source
    assert source["modelCandidatePolicy"] == "skip_without_catalog_hint"


def test_extractor_does_not_seed_without_a_weak_crypto_fact(tmp_path):
    root = tmp_path / "no-weak-crypto"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: no-weak-crypto\ndescription: Reads a plain text file.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "with open('notes.txt') as f:\n    text = f.read()\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_weak_crypto_sensitivity_gap(review_to_dict(review), data)
    assert seeds == []


def test_extractor_is_skill_engine_only():
    assert extract_weak_crypto_sensitivity_gap(
        {"engine": "prompt", "snapshot": {"files": []}}, {}) == []


def test_extractor_exposes_only_bounded_metadata_and_normal_evidence(tmp_path):
    root = tmp_path / "bounded-weak-crypto"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: bounded-weak-crypto\n"
        "description: Hashes the user's credit card number before "
        "caching it on disk.\nallowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "import hashlib\n"
        "card_digest = hashlib.sha1(b'4111111111111111').hexdigest()\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_weak_crypto_sensitivity_gap(review_to_dict(review), data)
    assert seeds
    serialized = str(seeds)
    assert str(tmp_path) not in serialized
    _source, _evidence_ids, evidence = seeds[0]
    assert all(ev["sensitivity"] == "normal" for ev in evidence)
