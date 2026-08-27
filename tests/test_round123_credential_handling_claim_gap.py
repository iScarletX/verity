"""Round 123: close VR-SKILL-011's L1_semantic=none gap with a new Finding
Type, semantic.skill.credential_handling_claim_gap.

Round 118 screened VR-SKILL-011 and declined it as "already generically
covered by declared_behavior_mismatch / permission_capability_mismatch, so
a dedicated extractor would duplicate rather than close a gap." Re-checking
that claim this round: detector_mappings.json maps declared_behavior_
mismatch to VR-SKILL-012 and permission_capability_mismatch to VR-SKILL-004
-- neither is mapped to VR-SKILL-011. Their shared implementation helper
(_skill_manifest_and_capability_seed) is a generic "capability family lacks
a declared permission" completeness check across every category; it never
parses the Manifest's natural-language claims about credentials. So this
Finding Type is a materially different, narrower signal -- a claim-vs-fact
contradiction, not a permission-completeness gap -- and closes a real hole
rather than duplicating one.

The existing `credential`/`environment_access` capability fact
(capabilities.py, fires on os.getenv/os.environ.get) already records that
a Skill reads an environment variable at runtime -- zero new fact-
extraction plumbing. This Finding Type pairs that fact with the Manifest's
own text: does the Manifest claim no credentials/API key/authentication
are required while the artifact snapshot contains an environment-variable-
access capability fact? Mirrors the dependency_provenance_claim_gap (Round
118) / isolation_claim_trust_gap (Round 100) "trust gap" shape exactly.
Deliberately narrow, per VR-SKILL-011's own L1_semantic boundary ("Must not
receive known secrets and is not the primary credential detector") -- this
never receives or judges any actual secret value, only the claim text and
the presence/absence of an os.getenv/os.environ.get capability fact.
"""
from verity.intake import intake_directory
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (CATALOG,
                                     extract_credential_handling_claim_gap)
from verity.semantic_benchmark import BUTLER_REFERENCE_SKILLS
from verity.standards import (load_detector_mappings, load_risks,
                              validate_runtime_detector_coverage)

FINDING_TYPE = "semantic.skill.credential_handling_claim_gap"
RISK_ID = "VR-SKILL-011"


def test_catalog_has_new_finding_type_with_expected_shape():
    definition, extractor = CATALOG[FINDING_TYPE]
    assert definition.engine == "skill"
    assert definition.defaultSeverity == "medium"
    assert definition.subjectKeyFields == ["credentialClaimGapKind"]
    assert extractor is extract_credential_handling_claim_gap


def test_detector_mapping_targets_vr_skill_011_as_signal():
    mappings = load_detector_mappings()
    entry = mappings[("semantic_finding_type", FINDING_TYPE)]
    assert entry["riskIds"] == [RISK_ID]
    assert entry["contribution"] == "signal"


def test_detector_mapping_count_grew_by_exactly_one_new_row():
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added
    # + Round 124's sandbox_signal row + Round 125's blackbox_scenario row
    # + Round 126's blackbox_scenario row


def test_vr_skill_011_l1_semantic_coverage_is_now_signal():
    risks = load_risks()
    coverage = risks[RISK_ID]["currentCoverage"]
    assert coverage["L1_semantic"] == "signal"
    # Unaffected layers stay exactly as they were before this round.
    assert coverage["L0_static"] == "partial"
    assert coverage["V1_5_blackbox"] == "none"
    assert coverage["V2_sandbox"] == "signal"


def test_known_gap_documents_the_narrow_claim_vs_fact_scope():
    risks = load_risks()
    gaps = risks[RISK_ID]["knownGaps"]
    assert any(
        "narrow claim-vs-fact contradiction" in g
        and "never receives or judges any actual secret value" in g
        for g in gaps)


def test_finding_type_engine_is_within_the_risks_declared_scope():
    definition, _extractor = CATALOG[FINDING_TYPE]
    risks = load_risks()
    assert definition.engine in risks[RISK_ID]["scopes"]


def test_runtime_detector_coverage_has_no_drift_after_new_mapping():
    validate_runtime_detector_coverage()


def test_butler_reference_skills_covers_the_new_finding_type():
    assert FINDING_TYPE in BUTLER_REFERENCE_SKILLS
    assert BUTLER_REFERENCE_SKILLS[FINDING_TYPE]


def test_extractor_seeds_with_candidate_hint_on_no_credential_claim(tmp_path):
    root = tmp_path / "no-key-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: no-key-skill\n"
        "description: A lookup helper that works without any API key -- "
        "no credentials required to use it.\nallowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "import os\n\ntoken = os.getenv('LOOKUP_SERVICE_TOKEN')\n"
        "print(token)\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_credential_handling_claim_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, evidence = seeds[0]
    assert source["credentialAccessFactCount"] >= 1
    assert source["candidateHints"]
    assert source["candidateHints"][0]["subject"]["credentialClaimGapKind"] == (
        "undisclosed_credential_access")
    paths = {loc["artifactPath"] for ev in evidence for loc in ev["locations"]}
    assert paths == {"SKILL.md", "run.py"}


def test_extractor_skips_candidate_hint_on_disclosed_credential_requirement(
        tmp_path):
    root = tmp_path / "disclosed-key-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: disclosed-key-skill\n"
        "description: Requires an API token set via the LOOKUP_SERVICE_"
        "TOKEN environment variable.\nallowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "import os\n\ntoken = os.getenv('LOOKUP_SERVICE_TOKEN')\n"
        "print(token)\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_credential_handling_claim_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, _evidence = seeds[0]
    assert "candidateHints" not in source
    assert source["modelCandidatePolicy"] == "skip_without_catalog_hint"


def test_extractor_does_not_seed_without_a_credential_access_fact(tmp_path):
    root = tmp_path / "no-env-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: no-env-skill\ndescription: Works without any API key "
        "-- no credentials required.\nallowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "with open('notes.txt') as f:\n    text = f.read()\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_credential_handling_claim_gap(review_to_dict(review), data)
    assert seeds == []


def test_extractor_is_skill_engine_only():
    assert extract_credential_handling_claim_gap(
        {"engine": "prompt", "snapshot": {"files": []}}, {}) == []


def test_extractor_exposes_only_bounded_metadata_and_normal_evidence(tmp_path):
    root = tmp_path / "bounded-credential-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: bounded-credential-skill\n"
        "description: Formats currency values -- no authentication "
        "required to run this skill.\nallowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "import os\n\napi_key = os.environ.get('CURRENCY_API_KEY')\n"
        "print(api_key)\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_credential_handling_claim_gap(review_to_dict(review), data)
    assert seeds
    serialized = str(seeds)
    assert str(tmp_path) not in serialized
    _source, _evidence_ids, evidence = seeds[0]
    assert all(ev["sensitivity"] == "normal" for ev in evidence)


def test_extractor_never_receives_or_inspects_a_secret_value(tmp_path):
    """VR-SKILL-011's L1_semantic boundary text: "Must not receive known
    secrets and is not the primary credential detector." The extractor's
    only inputs are the Manifest claim text and the capability fact's
    category/operation/location -- confirm the actual runtime environment
    variable value never appears anywhere in its output, even when one is
    set in the process environment while intake/review run.
    """
    import os as _os
    root = tmp_path / "live-env-skill"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: live-env-skill\n"
        "description: A lookup helper that works without any API key -- "
        "no credentials required to use it.\nallowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "import os\n\ntoken = os.getenv('LIVE_ENV_SKILL_SECRET')\n")
    _os.environ["LIVE_ENV_SKILL_SECRET"] = "sk-not-a-real-secret-value-12345"
    try:
        snap, data = intake_directory(root)
        review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
        seeds = extract_credential_handling_claim_gap(
            review_to_dict(review), data)
    finally:
        del _os.environ["LIVE_ENV_SKILL_SECRET"]
    assert seeds
    assert "sk-not-a-real-secret-value-12345" not in str(seeds)
