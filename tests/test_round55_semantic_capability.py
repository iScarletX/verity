"""Round 55 semantic evidence, policy, and Butler-floor regressions."""
from pathlib import Path

import pytest

from verity.intake import intake_directory, intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import CATALOG, _make_evidence_records
from verity.semantic.config import SemanticBudget
from verity.semantic.egress import build_generator_request


ROOT = Path(__file__).resolve().parents[1]


def _policy_dict(finding_type):
    policy = CATALOG[finding_type][0].judgmentPolicy
    return {
        "appliesWhen": policy.appliesWhen,
        "confirmWhen": policy.confirmWhen,
        "rejectWhen": policy.rejectWhen,
        "insufficientWhen": policy.insufficientWhen,
    }


def _prompt_request(text, finding_type, *, prompt_kind="system_prompt"):
    snapshot, file_bytes = intake_text(text, prompt_kind=prompt_kind)
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    seeds = CATALOG[finding_type][1](review_to_dict(review), file_bytes)
    assert seeds
    evidence = {}
    for _hint, _ids, records in seeds:
        for item in records:
            evidence.setdefault(item["evidenceId"], item)
    request = build_generator_request(
        review_id="round55", engine="prompt", finding_type=finding_type,
        evidences=list(evidence.values()), file_bytes=file_bytes,
        egress_policy="redacted_evidence", subject_taxonomy={},
        max_evidence=8, prompt_kind=prompt_kind,
        judgment_policy=_policy_dict(finding_type))
    return request


def _skill_request(relative_path, finding_type):
    path = ROOT / relative_path
    snapshot, file_bytes = intake_directory(path)
    review = run_review(ReviewInputs(
        "skill", snapshot, file_bytes, profile="minimal"))
    seeds = CATALOG[finding_type][1](review_to_dict(review), file_bytes)
    assert seeds
    evidence = {}
    for _hint, _ids, records in seeds:
        for item in records:
            evidence.setdefault(item["evidenceId"], item)
    return build_generator_request(
        review_id="round55", engine="skill", finding_type=finding_type,
        evidences=list(evidence.values()), file_bytes=file_bytes,
        egress_policy="redacted_evidence", subject_taxonomy={},
        max_evidence=8, judgment_policy=_policy_dict(finding_type))


def test_every_semantic_type_has_a_falsifiable_judgment_policy():
    assert len(CATALOG) == 28
    for finding_type, (definition, _extractor) in CATALOG.items():
        policy = definition.judgmentPolicy
        assert policy.appliesWhen, finding_type
        assert policy.confirmWhen, finding_type
        assert policy.rejectWhen, finding_type
        assert policy.insufficientWhen, finding_type
        assert all(len(item) <= 500 for group in (
            policy.appliesWhen, policy.confirmWhen, policy.rejectWhen,
            policy.insufficientWhen) for item in group)


def test_default_budget_can_attempt_every_applicable_semantic_type():
    budget = SemanticBudget()
    prompt_types = sum(
        definition.engine == "prompt"
        for definition, _extractor in CATALOG.values())
    skill_types = sum(
        definition.engine == "skill"
        for definition, _extractor in CATALOG.values())
    assert budget.max_candidate_generation_calls >= max(
        prompt_types, skill_types)


def test_same_prompt_span_keeps_extractor_specific_evidence_identity():
    text = (
        "Fetch every API record and provide a detailed explanation for each. "
        "Keep the complete answer concise and under 100 words.")
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    projection = review_to_dict(review)
    budget_seeds = CATALOG[
        "semantic.prompt.output_budget_pressure"][1](projection, file_bytes)
    failure_seeds = CATALOG[
        "semantic.prompt.failure_strategy_gap"][1](projection, file_bytes)
    assert budget_seeds and failure_seeds
    budget_evidence = budget_seeds[0][2][0]
    failure_evidence = failure_seeds[0][2][0]
    assert budget_evidence["locations"] == failure_evidence["locations"]
    assert budget_evidence["evidenceId"] != failure_evidence["evidenceId"]
    assert budget_evidence["metadata"]["signalFamilies"] == [
        "output_volume", "output_limit"]
    assert failure_evidence["metadata"]["signalFamilies"] == [
        "failure_prone_operation"]


def test_same_producer_and_span_keeps_distinct_fact_identity():
    location = {
        "fileId": "file-1",
        "artifactPath": "run.py",
        "fileDigest": "a" * 64,
        "sourceByteRange": {"start": 0, "end": 12},
        "locationSchemaVersion": "1",
    }
    records = _make_evidence_records(
        [location, location], snapshot_id="snapshot-1",
        producer_id="extractor.skill.permission_capability",
        metadata_by_index=[
            {
                "evidenceRole": "capability_fact",
                "capabilityFamily": "process_execution",
                "capabilityTarget": "cat",
            },
            {
                "evidenceRole": "capability_fact",
                "capabilityFamily": "process_execution",
                "capabilityTarget": "echo",
            },
        ])
    assert records[0]["locations"] == records[1]["locations"]
    assert records[0]["evidenceId"] != records[1]["evidenceId"]


def test_conflict_evidence_distinguishes_output_stages():
    request = _prompt_request(
        "Start with a summary of fewer than ten words.\n"
        "Then provide a detailed explanation of at least two hundred words.\n",
        "semantic.prompt.instruction_conflict",
        prompt_kind="user_prompt")
    metadata = [item["metadata"] for item in request["evidence"]]
    assert metadata[0]["outputStages"] == ["opening_segment"]
    assert metadata[0]["contentTargets"] == ["summary"]
    assert metadata[1]["outputStages"] == ["later_segment"]
    assert metadata[1]["contentTargets"] == ["explanation"]
    assert any("opening segment" in item.lower()
               for item in request["judgmentPolicy"]["rejectWhen"])


@pytest.mark.parametrize(
    "text, expected_fields",
    [
        ("Return the deployment plan as YAML.", 0),
        ("Return YAML with fields: service (string), replicas (integer), "
         "and regions (list of strings).", 1),
    ],
)
def test_output_contract_evidence_distinguishes_container_from_schema(
        text, expected_fields):
    request = _prompt_request(
        text, "semantic.prompt.missing_output_contract",
        prompt_kind="user_prompt")
    metadata = request["evidence"][0]["metadata"]
    assert metadata["requestedFormats"] == ["yaml"]
    if expected_fields:
        assert metadata["namedFieldSignalCount"] >= expected_fields
        assert metadata["typeMarkerCount"] >= 3
    else:
        assert metadata["namedFieldSignalCount"] == 0
        assert metadata["typeMarkerCount"] == 0


def test_declared_network_behavior_matches_observed_network_facts():
    request = _skill_request(
        "evals/corpus/v1/semantic-quality/selection/behavior-safe",
        "semantic.skill.declared_behavior_mismatch")
    facts = [
        item for item in request["evidence"]
        if item.get("metadata", {}).get("evidenceRole") == "capability_fact"
    ]
    assert facts
    assert all(item["metadata"]["capabilityFamily"] == "network_access"
               for item in facts)
    assert all(item["metadata"]["declaredBehaviorMatch"] is True
               for item in facts)
    assert any("urlopen" in item.get("textSnippet", "") for item in facts)


def test_denied_network_behavior_does_not_match_observed_network_facts():
    request = _skill_request(
        "evals/corpus/v1/semantic-quality/selection/behavior-positive",
        "semantic.skill.declared_behavior_mismatch")
    manifest = request["evidence"][0]["metadata"]
    facts = [
        item for item in request["evidence"]
        if item.get("metadata", {}).get("evidenceRole") == "capability_fact"
    ]
    assert "network_access" in manifest["deniedCapabilityFamilies"]
    assert facts
    assert all(item["metadata"]["declaredBehaviorMatch"] is False
               for item in facts)


def test_narrow_bash_permission_matches_fixed_command_target():
    request = _skill_request(
        "evals/corpus/v1/semantic-quality/selection/permission-safe",
        "semantic.skill.permission_capability_mismatch")
    manifest = request["evidence"][0]["metadata"]
    process = next(
        item for item in request["evidence"]
        if item.get("metadata", {}).get("capabilityFamily")
        == "process_execution")
    assert manifest["declaredProcessTargets"] == ["echo"]
    assert process["metadata"]["capabilityTarget"] == "echo"
    assert process["metadata"]["declaredPermissionMatch"] is True
    assert "subprocess.run" in process["textSnippet"]


def test_later_bash_target_can_match_after_an_earlier_nonmatch(tmp_path):
    root = tmp_path / "multi-command"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\n"
        "name: multi-command\n"
        "description: Runs one fixed local echo command.\n"
        "allowed-tools: Bash(cat:*) Bash(echo:*)\n"
        "---\n",
        encoding="utf-8")
    (root / "run.py").write_text(
        "import subprocess\n"
        "subprocess.run(['/bin/echo', 'ok'], check=True)\n",
        encoding="utf-8")
    snapshot, file_bytes = intake_directory(root)
    review = run_review(ReviewInputs(
        "skill", snapshot, file_bytes, profile="minimal"))
    seeds = CATALOG[
        "semantic.skill.permission_capability_mismatch"][1](
            review_to_dict(review), file_bytes)
    process = next(
        item for item in seeds[0][2]
        if item["metadata"].get("capabilityFamily") == "process_execution")
    assert process["metadata"]["capabilityTarget"] == "echo"
    assert process["metadata"]["declaredPermissionMatch"] is True


def test_read_permission_does_not_match_fixed_process_target():
    request = _skill_request(
        "evals/corpus/v1/semantic-quality/selection/permission-positive",
        "semantic.skill.permission_capability_mismatch")
    process = next(
        item for item in request["evidence"]
        if item.get("metadata", {}).get("capabilityFamily")
        == "process_execution")
    assert process["metadata"]["capabilityTarget"] == "echo"
    assert process["metadata"]["declaredPermissionMatch"] is False


@pytest.mark.parametrize(
    "finding_type, positive_path, safe_path",
    [
        ("semantic.prompt.output_budget_pressure",
         "output-budget-pressure-positive", "output-budget-pressure-safe"),
        ("semantic.prompt.authority_boundary_ambiguity",
         "authority-boundary-positive", "authority-boundary-safe"),
        ("semantic.prompt.failure_strategy_gap",
         "failure-strategy-gap-positive", "failure-strategy-gap-safe"),
        ("semantic.prompt.ambiguous_operational_criteria",
         "ambiguous-criteria-positive", "ambiguous-criteria-safe"),
        ("semantic.prompt.grounding_requirement_gap",
         "grounding-gap-positive", "grounding-gap-safe"),
        ("semantic.prompt.sensitive_reasoning_exposure",
         "reasoning-exposure-positive", "reasoning-exposure-safe"),
        ("semantic.prompt.verification_step_gap",
         "verification-step-gap-positive", "verification-step-gap-safe"),
        ("semantic.prompt.input_and_default_contract_gap",
         "input-contract-positive", "input-contract-safe"),
        ("semantic.prompt.example_contract_mismatch",
         "example-contract-positive", "example-contract-safe"),
        ("semantic.prompt.tool_call_contract_gap",
         "tool-call-contract-positive", "tool-call-contract-safe"),
        ("semantic.prompt.capability_dependency_gap",
         "capability-dependency-positive", "capability-dependency-safe"),
        ("semantic.prompt.sensitive_data_handling_gap",
         "sensitive-data-positive", "sensitive-data-safe"),
        ("semantic.prompt.role_scope_contract_gap",
         "role-scope-positive", "role-scope-safe"),
        ("semantic.prompt.workflow_dependency_gap",
         "workflow-dependency-positive", "workflow-dependency-safe"),
        ("semantic.prompt.field_constraint_gap",
         "field-constraint-positive", "field-constraint-safe"),
        ("semantic.prompt.error_response_contract_gap",
         "error-response-positive", "error-response-safe"),
        ("semantic.prompt.attention_dilution",
         "attention-dilution-positive", "attention-dilution-safe"),
        ("semantic.prompt.streaming_recovery_gap",
         "streaming-recovery-positive", "streaming-recovery-safe"),
        ("semantic.prompt.multi_turn_state_gap",
         "multi-turn-state-positive", "multi-turn-state-safe"),
        ("semantic.prompt.safety_policy_gap",
         "safety-policy-positive", "safety-policy-safe"),
        ("semantic.prompt.source_use_policy_gap",
         "source-use-positive", "source-use-safe"),
    ],
)
def test_new_semantic_types_route_positive_and_safe_counterexamples(
        finding_type, positive_path, safe_path):
    base = ROOT / "evals/corpus/v1/semantic-cases"
    for name in (positive_path, safe_path):
        text = (base / name / "prompt.txt").read_text("utf-8")
        request = _prompt_request(text, finding_type)
        assert request["judgmentPolicy"]["rejectWhen"]
        assert request["evidence"][0]["metadata"]["evidenceRole"] in {
            "prompt_analysis", "output_contract", "prompt_constraint"}


@pytest.mark.parametrize(
    "finding_type, safe_path, required_control_signals",
    [
        ("semantic.prompt.role_scope_contract_gap", "role-scope-safe",
         ("audienceSignalCount", "dutySignalCount", "exclusionSignalCount")),
        ("semantic.prompt.workflow_dependency_gap",
         "workflow-dependency-safe",
         ("dependencySignalCount", "intermediateResultSignalCount",
          "workflowBranchSignalCount")),
        ("semantic.prompt.field_constraint_gap", "field-constraint-safe",
         ("fieldTypeSignalCount", "unitPrecisionSignalCount",
          "rangeSignalCount", "boundaryValueSignalCount")),
        ("semantic.prompt.error_response_contract_gap",
         "error-response-safe",
         ("errorSchemaSignalCount", "recoverySignalCount",
          "errorFormatSignalCount")),
        ("semantic.prompt.attention_dilution", "attention-dilution-safe",
         ("hierarchySignalCount",)),
        ("semantic.prompt.streaming_recovery_gap",
         "streaming-recovery-safe",
         ("framingSignalCount", "completionSignalCount",
          "resumeSignalCount", "partialStreamSignalCount")),
        ("semantic.prompt.multi_turn_state_gap", "multi-turn-state-safe",
         ("stateInheritanceSignalCount", "stateUpdateSignalCount",
          "stateResetSignalCount", "stateInvariantSignalCount")),
        ("semantic.prompt.safety_policy_gap", "safety-policy-safe",
         ("refusalSignalCount", "safeAlternativeSignalCount",
          "escalationSignalCount")),
        ("semantic.prompt.source_use_policy_gap", "source-use-safe",
         ("attributionSignalCount", "transformationSignalCount",
          "sourceLimitSignalCount")),
    ],
)
def test_new_safe_counterexamples_preserve_falsifying_control_signals(
        finding_type, safe_path, required_control_signals):
    path = (
        ROOT / "evals/corpus/v1/semantic-cases"
        / safe_path / "prompt.txt")
    request = _prompt_request(path.read_text("utf-8"), finding_type)
    metadata = request["evidence"][0]["metadata"]
    assert all(metadata[key] >= 1 for key in required_control_signals)


def test_example_quality_and_user_prompt_privacy_produce_bounded_evidence():
    example_request = _prompt_request(
        "These few-shot examples are representative of the real input "
        "distribution, but contain only outdated English consumer records.",
        "semantic.prompt.example_contract_mismatch")
    assert (
        example_request["evidence"][0]["metadata"]
        ["exampleQualitySignalCount"] >= 3)

    privacy_request = _prompt_request(
        "Collect personal data and display each email address in the answer.",
        "semantic.prompt.sensitive_data_handling_gap",
        prompt_kind="user_prompt")
    assert privacy_request["promptKind"] == "user_prompt"
    assert (
        privacy_request["evidence"][0]["metadata"]
        ["sensitiveDataSignalCount"] >= 2)
