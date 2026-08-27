"""Round 97: close VR-SKILL-015's L1_semantic=none gap with a new Finding
Type, semantic.skill.sql_injection_input_trust_gap.
"""
from verity.intake import intake_directory
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import CATALOG, extract_sql_injection_input_trust_gap
from verity.semantic_benchmark import BUTLER_REFERENCE_SKILLS
from verity.standards import (load_detector_mappings, load_risks,
                              validate_runtime_detector_coverage)

FINDING_TYPE = "semantic.skill.sql_injection_input_trust_gap"
RISK_ID = "VR-SKILL-015"


def test_catalog_has_new_finding_type_with_expected_shape():
    definition, extractor = CATALOG[FINDING_TYPE]
    assert definition.engine == "skill"
    assert definition.defaultSeverity == "high"
    assert definition.subjectKeyFields == ["injectionTrustGapKind"]
    assert extractor is extract_sql_injection_input_trust_gap


def test_detector_mapping_targets_vr_skill_015_as_signal():
    mappings = load_detector_mappings()
    entry = mappings[("semantic_finding_type", FINDING_TYPE)]
    assert entry["riskIds"] == [RISK_ID]
    assert entry["contribution"] == "signal"


def test_detector_mapping_count_grew_by_exactly_one_new_row():
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added


def test_vr_skill_015_l1_semantic_coverage_is_now_signal():
    risks = load_risks()
    coverage = risks[RISK_ID]["currentCoverage"]
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"
    # Round 130 later flipped V2_sandbox from "none" to "signal" (new
    # sandbox_sql_injected_query row, built on sqlite3.connect instrumentation)
    # -- was "none" at this round.
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


def test_extractor_seeds_with_candidate_hint_on_user_controlled_input_framing(tmp_path):
    root = tmp_path / "sql-injection-user-input"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: sql-injection-user-input\n"
        "description: Looks up a user record using the user-provided "
        "username submitted through the login form.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "username = 'alice'\n"
        "cursor.execute(f\"SELECT * FROM users WHERE name = '{username}'\")\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_sql_injection_input_trust_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, evidence = seeds[0]
    assert source["sqlQueryFactCount"] >= 1
    assert source["candidateHints"]
    assert source["candidateHints"][0]["subject"]["injectionTrustGapKind"] == (
        "user_controlled_query_input")
    paths = {loc["artifactPath"] for ev in evidence for loc in ev["locations"]}
    assert paths == {"SKILL.md", "run.py"}


def test_extractor_skips_candidate_hint_on_safe_query_construction_framing(tmp_path):
    root = tmp_path / "sql-injection-parameterized"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: sql-injection-parameterized\n"
        "description: Looks up an account balance using a parameterized "
        "query built only from sanitized, internal data.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "account_id = 'internal-account-1'\n"
        "cursor.execute(f\"SELECT balance FROM accounts WHERE id = "
        "'{account_id}'\")\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_sql_injection_input_trust_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, _evidence = seeds[0]
    assert "candidateHints" not in source
    assert source["modelCandidatePolicy"] == "skip_without_catalog_hint"


def test_extractor_does_not_seed_without_a_sql_query_fact(tmp_path):
    root = tmp_path / "no-sql-query"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: no-sql-query\ndescription: Reads a plain text file.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "with open('notes.txt') as f:\n    text = f.read()\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_sql_injection_input_trust_gap(review_to_dict(review), data)
    assert seeds == []


def test_extractor_does_not_seed_a_static_query_with_no_dynamic_construction(tmp_path):
    root = tmp_path / "static-sql-query"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: static-sql-query\n"
        "description: Counts rows in a fixed reporting table.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "cursor.execute('SELECT COUNT(*) FROM report_table')\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_sql_injection_input_trust_gap(review_to_dict(review), data)
    assert seeds == []


def test_extractor_is_skill_engine_only():
    assert extract_sql_injection_input_trust_gap(
        {"engine": "prompt", "snapshot": {"files": []}}, {}) == []


def test_extractor_exposes_only_bounded_metadata_and_normal_evidence(tmp_path):
    root = tmp_path / "bounded-sql-injection"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: bounded-sql-injection\n"
        "description: Searches order records using an external request "
        "parameter received from the API caller.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "order_id = '1001'\n"
        "cursor.execute(\"SELECT * FROM orders WHERE id = '\" + order_id + "
        "\"'\")\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_sql_injection_input_trust_gap(review_to_dict(review), data)
    assert seeds
    serialized = str(seeds)
    assert str(tmp_path) not in serialized
    _source, _evidence_ids, evidence = seeds[0]
    assert all(ev["sensitivity"] == "normal" for ev in evidence)
