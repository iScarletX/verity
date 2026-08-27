"""Round 99: close VR-SKILL-010's L1_semantic=none gap with a new Finding
Type, semantic.skill.template_injection_input_trust_gap.
"""
from verity.intake import intake_directory
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import CATALOG, extract_template_injection_input_trust_gap
from verity.semantic_benchmark import BUTLER_REFERENCE_SKILLS
from verity.standards import (load_detector_mappings, load_risks,
                              validate_runtime_detector_coverage)

FINDING_TYPE = "semantic.skill.template_injection_input_trust_gap"
RISK_ID = "VR-SKILL-010"


def test_catalog_has_new_finding_type_with_expected_shape():
    definition, extractor = CATALOG[FINDING_TYPE]
    assert definition.engine == "skill"
    assert definition.defaultSeverity == "high"
    assert definition.subjectKeyFields == ["templateTrustGapKind"]
    assert extractor is extract_template_injection_input_trust_gap


def test_detector_mapping_targets_vr_skill_010_as_signal():
    mappings = load_detector_mappings()
    entry = mappings[("semantic_finding_type", FINDING_TYPE)]
    assert entry["riskIds"] == [RISK_ID]
    assert entry["contribution"] == "signal"


def test_detector_mapping_count_grew_by_exactly_one_new_row():
    mappings = load_detector_mappings()
    assert len(mappings) == 156  # Four agent-runtime signals were added


def test_vr_skill_010_l1_semantic_coverage_is_now_signal():
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


def test_extractor_seeds_with_candidate_hint_on_user_controlled_input_framing(tmp_path):
    root = tmp_path / "template-injection-user-input"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: template-injection-user-input\n"
        "description: Renders a welcome template built from the "
        "user-supplied display name submitted at signup.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "from jinja2 import Template\n"
        "display_name = 'Alice'\n"
        "template = Template(f'Welcome, {display_name}!')\n"
        "output = template.render()\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_template_injection_input_trust_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, evidence = seeds[0]
    assert source["templateRenderFactCount"] >= 1
    assert source["candidateHints"]
    assert source["candidateHints"][0]["subject"]["templateTrustGapKind"] == (
        "user_controlled_template_source")
    paths = {loc["artifactPath"] for ev in evidence for loc in ev["locations"]}
    assert paths == {"SKILL.md", "run.py"}


def test_extractor_skips_candidate_hint_on_safe_construction_framing(tmp_path):
    root = tmp_path / "template-injection-fixed"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: template-injection-fixed\n"
        "description: Renders a welcome template built from a sanitized, "
        "hardcoded display name for local testing.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "from jinja2 import Template\n"
        "display_name = 'Alice'\n"
        "template = Template(f'Welcome, {display_name}!')\n"
        "output = template.render()\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_template_injection_input_trust_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, _evidence = seeds[0]
    assert "candidateHints" not in source
    assert source["modelCandidatePolicy"] == "skip_without_catalog_hint"


def test_extractor_does_not_seed_without_a_template_render_fact(tmp_path):
    root = tmp_path / "no-template-render"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: no-template-render\ndescription: Prints a greeting.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text("print('hello')\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_template_injection_input_trust_gap(review_to_dict(review), data)
    assert seeds == []


def test_extractor_does_not_seed_a_static_template_with_no_dynamic_construction(tmp_path):
    root = tmp_path / "static-template"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: static-template\n"
        "description: Renders a fixed status report template.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "from jinja2 import Template\n"
        "template = Template('Status: all systems operational.')\n"
        "output = template.render()\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_template_injection_input_trust_gap(review_to_dict(review), data)
    assert seeds == []


def test_extractor_detects_dynamic_from_string_construction(tmp_path):
    root = tmp_path / "template-injection-from-string"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: template-injection-from-string\n"
        "description: Builds a report template from an external template "
        "string received from the remote caller and renders it.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "from jinja2 import Environment\n"
        "prefix = 'Report: '\n"
        "env = Environment()\n"
        "template = env.from_string(prefix + '{{ total }}')\n"
        "output = template.render(total=42)\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_template_injection_input_trust_gap(review_to_dict(review), data)
    assert seeds
    source, _evidence_ids, _evidence = seeds[0]
    assert source["candidateHints"]


def test_extractor_is_skill_engine_only():
    assert extract_template_injection_input_trust_gap(
        {"engine": "prompt", "snapshot": {"files": []}}, {}) == []


def test_extractor_exposes_only_bounded_metadata_and_normal_evidence(tmp_path):
    root = tmp_path / "bounded-template-injection"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\nname: bounded-template-injection\n"
        "description: Renders a welcome template built from the "
        "user-supplied display name submitted at signup.\n"
        "allowed-tools: Read\n---\n")
    (root / "run.py").write_text(
        "from jinja2 import Template\n"
        "display_name = 'Alice'\n"
        "template = Template(f'Welcome, {display_name}!')\n"
        "output = template.render()\n")
    snap, data = intake_directory(root)
    review = run_review(ReviewInputs("skill", snap, data, profile="minimal"))
    seeds = extract_template_injection_input_trust_gap(review_to_dict(review), data)
    assert seeds
    serialized = str(seeds)
    assert str(tmp_path) not in serialized
    _source, _evidence_ids, evidence = seeds[0]
    assert all(ev["sensitivity"] == "normal" for ev in evidence)
