"""Round 55 answer-hidden Verity/Butler comparison protocol."""
from copy import deepcopy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tools import semantic_head_to_head
from verity.corpus import CorpusError
from verity.semantic.catalog import CATALOG
from verity.semantic_benchmark import (
    BUTLER_REFERENCE_SKILLS,
    BUTLER_REFERENCE_SKILL_MAP_VERSION,
    COMPARISON_PROTOCOL_ID,
    COMPARISON_PROTOCOL_VERSION,
    COMPARISON_THRESHOLDS,
    DEFAULT_COMPARISON_MAX_TOTAL_CALLS,
    _packet_item_digest,
    butler_breadth_summary,
    build_independent_label_attestation,
    build_semantic_comparison_packet,
    compare_semantic_systems,
    evaluate_independent_label_reviewer_observations,
    evaluate_verity_comparison_observations,
    load_butler_crosswalk,
    load_semantic_comparison_manifest,
    validate_observations,
    validate_semantic_comparison_seed_coverage,
)
from verity.semantic.config import ProviderConfig, ProviderCredentials
from verity.semantic.provider import ProviderResponse

ROOT = Path(__file__).resolve().parents[1]

SUBJECTS = {
    "semantic.prompt.instruction_conflict": {
        "conflictKind": "contradictory_directive"},
    "semantic.prompt.missing_output_contract": {"expectedFormat": "json"},
    "semantic.skill.declared_behavior_mismatch": {
        "mismatchKind": "capability_undeclared"},
    "semantic.prompt.trust_boundary_ambiguity": {
        "boundaryKind": "retrieved_content"},
    "semantic.prompt.excessive_tool_scope": {
        "scopeKind": "unnecessary_tool"},
    "semantic.skill.permission_capability_mismatch": {
        "mismatchKind": "undeclared_capability"},
    "semantic.skill.external_instruction_trust_gap": {
        "trustGapKind": "unverified_source"},
    "semantic.prompt.output_budget_pressure": {
        "pressureKind": "implicit_lower_bound"},
    "semantic.prompt.authority_boundary_ambiguity": {
        "authorityKind": "external_side_effect"},
    "semantic.prompt.failure_strategy_gap": {"gapKind": "fallback"},
    "semantic.prompt.ambiguous_operational_criteria": {
        "criterionKind": "undefined_boundary"},
    "semantic.prompt.grounding_requirement_gap": {
        "groundingKind": "verification_required"},
    "semantic.prompt.sensitive_reasoning_exposure": {
        "exposureKind": "chain_of_thought"},
    "semantic.prompt.verification_step_gap": {
        "verificationKind": "downstream_validity"},
    "semantic.prompt.input_and_default_contract_gap": {
        "gapKind": "missing_input"},
    "semantic.prompt.example_contract_mismatch": {
        "exampleGapKind": "rule_mismatch"},
    "semantic.prompt.tool_call_contract_gap": {
        "contractGapKind": "parameter_provenance"},
    "semantic.prompt.capability_dependency_gap": {
        "dependencyKind": "web_access"},
    "semantic.prompt.sensitive_data_handling_gap": {
        "dataPolicyKind": "redaction"},
    "semantic.prompt.role_scope_contract_gap": {
        "roleGapKind": "exclusions"},
    "semantic.prompt.workflow_dependency_gap": {
        "dependencyGapKind": "reversed_order"},
    "semantic.prompt.field_constraint_gap": {
        "fieldGapKind": "type_or_unit"},
    "semantic.prompt.error_response_contract_gap": {
        "errorGapKind": "schema"},
    "semantic.prompt.attention_dilution": {
        "dilutionKind": "buried_critical_rule"},
    "semantic.prompt.streaming_recovery_gap": {
        "streamingGapKind": "framing"},
    "semantic.prompt.multi_turn_state_gap": {
        "stateGapKind": "reset"},
    "semantic.prompt.safety_policy_gap": {
        "safetyGapKind": "refusal_boundary"},
    "semantic.prompt.source_use_policy_gap": {
        "sourceGapKind": "reproduction_limit"},
}


def _observations(packet, runs_by_item, repetitions=2):
    return {
        "schemaVersion": 1,
        "protocolId": COMPARISON_PROTOCOL_ID,
        "protocolVersion": COMPARISON_PROTOCOL_VERSION,
        "systemId": packet["systemId"],
        "configurationFingerprint": "a" * 64,
        "corpusFingerprint": packet["corpusFingerprint"],
        "repetitions": repetitions,
        "observations": [
            {"itemId": item["itemId"],
             "runs": list(runs_by_item[item["itemId"]])}
            for item in packet["items"]
        ],
    }


def _complete_butler_crosswalk():
    """Synthetic all-covered inventory used only to isolate metric tests."""
    crosswalk = deepcopy(load_butler_crosswalk())
    for entry in crosswalk["entries"]:
        entry["status"] = "covered"
        entry["verityFindingTypes"] = [
            "semantic.prompt.instruction_conflict"]
        entry["plannedFindingType"] = None
    return crosswalk


def test_v3_development_manifest_is_fresh_paired_and_seeded():
    manifest = load_semantic_comparison_manifest()
    assert manifest["protocolVersion"] == "3.0.0"
    assert manifest["status"] == "development_calibration"
    assert manifest["labelStatus"] == "provisional_single_review"
    assert len(manifest["cases"]) == 112
    by_type = {}
    for case in manifest["cases"]:
        by_type.setdefault(case["findingType"], []).append(
            case["authorAssessment"])
        assert "semantic-quality/" not in case["path"]
        assert "semantic-cases/" not in case["path"]
    assert set(by_type) == set(CATALOG)
    assert all(
        sorted(states) == ["absent", "absent", "present", "present"]
        for states in by_type.values())
    assert len({case["riskId"] for case in manifest["cases"]}) == 27
    assert COMPARISON_THRESHOLDS["minimumRiskCount"] == 27
    assert COMPARISON_THRESHOLDS["minimumFindingTypeCount"] == 28
    assert validate_semantic_comparison_seed_coverage() == 112
    assert DEFAULT_COMPARISON_MAX_TOTAL_CALLS >= 112 * 2 * 2


def test_butler_reference_skill_map_covers_every_semantic_type():
    assert BUTLER_REFERENCE_SKILL_MAP_VERSION == "3.0.0"
    assert set(BUTLER_REFERENCE_SKILLS) == set(CATALOG)
    assert all(
        1 <= len(skill_ids) <= 2 and len(set(skill_ids)) == len(skill_ids)
        for skill_ids in BUTLER_REFERENCE_SKILLS.values())
    assert all(
        skill_id.count("_") >= 2
        for skill_ids in BUTLER_REFERENCE_SKILLS.values()
        for skill_id in skill_ids)


def test_butler_crosswalk_accounts_for_all_45_checks_without_open_gaps():
    crosswalk = load_butler_crosswalk()
    summary = butler_breadth_summary(crosswalk)
    inventory = {
        entry["butlerSkillId"] for entry in crosswalk["entries"]}
    routed = {
        skill_id
        for skill_ids in BUTLER_REFERENCE_SKILLS.values()
        for skill_id in skill_ids
    }
    assert len(inventory) == 45
    assert routed < inventory
    assert summary == {
        "referenceCommit": "20979b573107a29232bf95dcf56801e06504d801",
        "referenceSourceFingerprint": (
            "de147af7ce65e417c692115600076044"
            "b0e4dc23b55c70b7930940138cf27737"),
        "inventoryCount": 45,
        "coveredCount": 45,
        "notAdoptedCount": 0,
        "openGapCount": 0,
        "claimReady": True,
    }


def test_butler_source_fingerprint_binds_commit_inventory_and_bytes(
        tmp_path, monkeypatch):
    root = tmp_path / "butler"
    registry_path = root / "src/core/skillLoader/loadBuiltinSkills.ts"
    registry_path.parent.mkdir(parents=True)
    crosswalk = deepcopy(load_butler_crosswalk())
    registry_text = "\n".join(
        f"{{ id: '{entry['butlerSkillId']}' }},"
        for entry in crosswalk["entries"])
    (root / "package.json").write_text(
        '{"name":"butler"}', encoding="utf-8")
    (root / "package-lock.json").write_text("{}", encoding="utf-8")
    registry_path.write_text(registry_text, encoding="utf-8")

    files = [root / "package.json", root / "package-lock.json"]
    files.extend(
        path for path in sorted((root / "src").rglob("*"))
        if path.is_file() and path.suffix.lower() in {
            ".ts", ".tsx", ".json", ".md"})
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(root).as_posix().encode()
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    crosswalk["referenceSourceFingerprint"] = digest.hexdigest()

    monkeypatch.setattr(
        semantic_head_to_head, "load_butler_crosswalk",
        lambda: crosswalk)
    monkeypatch.setattr(
        semantic_head_to_head.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, stdout=crosswalk["referenceCommit"] + "\n"))
    assert semantic_head_to_head._butler_source_fingerprint(
        root) == crosswalk["referenceSourceFingerprint"]

    registry_path.write_text(
        registry_text + "\n// local mutation\n", encoding="utf-8")
    with pytest.raises(CorpusError, match="source fingerprint differs"):
        semantic_head_to_head._butler_source_fingerprint(root)


def test_answer_free_packets_hide_labels_and_randomize_aliases():
    first, first_map = build_semantic_comparison_packet(
        system_id="verity", seed="round55-verity-seed")
    second, _second_map = build_semantic_comparison_packet(
        system_id="butler", seed="round55-butler-seed")
    assert first["itemCount"] == 112
    assert first["corpusFingerprint"] == second["corpusFingerprint"]
    assert [row["itemId"] for row in first["items"]] != [
        row["itemId"] for row in second["items"]]
    packet_text = json.dumps(first, sort_keys=True)
    for forbidden in (
            "authorAssessment", "labelStatus", "findingType", "riskId",
            "semantic-comparison-v3-cal-", "case-005"):
        assert forbidden not in packet_text
    assert len(first_map["aliases"]) == 112
    assert all("authorAssessment" in value
               for value in first_map["aliases"].values())
    assert all("packetItemDigest" in value
               for value in first_map["aliases"].values())


def test_observations_require_complete_repeated_scrubbed_rows():
    packet, _mapping = build_semantic_comparison_packet(
        system_id="verity", seed="round55-observation-seed")
    runs = {item["itemId"]: ["absent", "absent"]
            for item in packet["items"]}
    value = _observations(packet, runs)
    assert validate_observations(value, packet) is value
    value["observations"][0]["runs"] = ["absent"]
    with pytest.raises(CorpusError, match="runs invalid"):
        validate_observations(value, packet)


def test_provisional_or_missing_labels_can_never_claim_superiority():
    verity_packet, verity_map = build_semantic_comparison_packet(
        system_id="verity", seed="round55-verity-blocked")
    butler_packet, butler_map = build_semantic_comparison_packet(
        system_id="butler", seed="round55-butler-blocked")
    verity_obs = _observations(
        verity_packet,
        {item["itemId"]: ["absent", "absent"]
         for item in verity_packet["items"]})
    butler_obs = _observations(
        butler_packet,
        {item["itemId"]: ["absent", "absent"]
         for item in butler_packet["items"]})
    report = compare_semantic_systems(
        verity_packet=verity_packet, verity_mapping=verity_map,
        verity_observations=verity_obs,
        butler_packet=butler_packet, butler_mapping=butler_map,
        butler_observations=butler_obs, label_attestation=None)
    assert report["status"] == "not_eligible"
    assert report["reasonCodes"] == ["labels_missing"]
    assert report["claim"] is None
    assert report["butlerBreadth"]["openGapCount"] == 0


def _synthetic_pair(case_count=112):
    fingerprint = "b" * 64
    def packet_item(item_id):
        return {
            "itemId": item_id,
            "objectType": "prompt",
            "language": "en",
            "promptKind": "user_prompt",
            "targetRisk": {
                "title": "Synthetic target",
                "definition": "Synthetic comparison target.",
                "reviewBoundary": "Judge only the synthetic target.",
                "falsificationQuestion": "Is the synthetic target present?",
            },
            "artifact": {
                "displayRootName": None,
                "files": [{"path": "prompt.txt", "content": "Synthetic."}],
            },
        }
    verity_items = [
        packet_item(f"SC-{index + 1:03d}-{index:06x}")
        for index in range(case_count)]
    butler_items = [
        packet_item(f"SC-{index + 1:03d}-{index + case_count:06x}")
        for index in range(case_count)]
    instructions = {
        "question": "Judge the target.",
        "independence": "Do not seek labels.",
        "repetitions": "Run each item twice.",
    }
    verity_packet = {
        "schemaVersion": 1,
        "protocolId": COMPARISON_PROTOCOL_ID,
        "protocolVersion": COMPARISON_PROTOCOL_VERSION,
        "systemId": "verity", "corpusFingerprint": fingerprint,
        "itemCount": case_count,
        "instructions": instructions,
        "items": verity_items,
    }
    butler_packet = {
        "schemaVersion": 1,
        "protocolId": COMPARISON_PROTOCOL_ID,
        "protocolVersion": COMPARISON_PROTOCOL_VERSION,
        "systemId": "butler", "corpusFingerprint": fingerprint,
        "itemCount": case_count,
        "instructions": instructions,
        "items": butler_items,
    }
    verity_aliases = {}
    butler_aliases = {}
    labels = []
    verity_runs = {}
    butler_runs = {}
    for index in range(case_count):
        case_id = f"case-{index:03d}"
        risk_id = f"risk-{index % 27:02d}"
        assessment = "present" if index % 2 == 0 else "absent"
        va = verity_items[index]["itemId"]
        ba = butler_items[index]["itemId"]
        meta = {
            "caseId": case_id, "findingType": f"type-{index % 28:02d}",
            "riskId": risk_id, "authorAssessment": assessment,
            "payloadDigest": f"{index:064x}",
        }
        verity_aliases[va] = {
            **meta, "packetItemDigest": _packet_item_digest(
                verity_items[index])}
        butler_aliases[ba] = {
            **meta, "packetItemDigest": _packet_item_digest(
                butler_items[index])}
        labels.append({"caseId": case_id, "assessment": assessment})
        verity_runs[va] = [assessment, assessment]
        # Reference system has two stable false positives but no recall loss.
        observed = (
            "present" if assessment == "absent" and index in {1, 3}
            else assessment)
        butler_runs[ba] = [observed, observed]
    verity_map = {
        "schemaVersion": 1,
        "protocolId": COMPARISON_PROTOCOL_ID,
        "protocolVersion": COMPARISON_PROTOCOL_VERSION,
        "systemId": "verity",
        "corpusFingerprint": fingerprint,
        "aliases": verity_aliases,
    }
    butler_map = {
        "schemaVersion": 1,
        "protocolId": COMPARISON_PROTOCOL_ID,
        "protocolVersion": COMPARISON_PROTOCOL_VERSION,
        "systemId": "butler",
        "corpusFingerprint": fingerprint,
        "aliases": butler_aliases,
    }
    attestation = {
        "schemaVersion": 1,
        "protocolId": COMPARISON_PROTOCOL_ID,
        "protocolVersion": COMPARISON_PROTOCOL_VERSION,
        "corpusFingerprint": fingerprint,
        "labelStatus": "independent_ai_review",
        "reviewers": [
            {
                "reviewerId": "reviewer-a",
                "systemId": "label-reviewer-a",
                "configurationFingerprint": "c" * 64,
                "reviewArtifactDigest": "d" * 64,
            },
            {
                "reviewerId": "reviewer-b",
                "systemId": "label-reviewer-b",
                "configurationFingerprint": "e" * 64,
                "reviewArtifactDigest": "f" * 64,
            },
        ],
        "labels": [
            {
                "caseId": row["caseId"],
                "payloadDigest": f"{index:064x}",
                "assessment": row["assessment"],
            }
            for index, row in enumerate(labels)
        ],
    }
    return (
        verity_packet, verity_map,
        _observations(verity_packet, verity_runs),
        butler_packet, butler_map,
        _observations(butler_packet, butler_runs),
        attestation,
    )


def test_superiority_claim_requires_absolute_and_relative_gate():
    (vp, vm, vo, bp, bm, bo, labels) = _synthetic_pair()
    report = compare_semantic_systems(
        verity_packet=vp, verity_mapping=vm, verity_observations=vo,
        butler_packet=bp, butler_mapping=bm, butler_observations=bo,
        label_attestation=labels,
        butler_crosswalk=_complete_butler_crosswalk())
    assert report["status"] == "passed"
    assert report["claim"] == (
        "verity_exceeds_butler_on_this_independently_labelled_benchmark")
    assert report["verity"]["recall"] == 1.0
    assert report["verity"]["safeFalsePositiveRate"] == 0.0
    assert report["butler"]["recall"] == 1.0
    assert report["butler"]["safeFalsePositiveRate"] > 0
    assert report["butlerBreadth"]["claimReady"] is True
    assert all(report["absoluteChecks"].values())
    assert all(report["relativeChecks"].values())


def test_real_crosswalk_allows_claim_when_metrics_and_labels_pass():
    vp, vm, vo, bp, bm, bo, labels = _synthetic_pair()
    report = compare_semantic_systems(
        verity_packet=vp, verity_mapping=vm, verity_observations=vo,
        butler_packet=bp, butler_mapping=bm, butler_observations=bo,
        label_attestation=labels)
    assert report["status"] == "passed"
    assert report["reasonCodes"] == []
    assert report["claim"] == (
        "verity_exceeds_butler_on_this_independently_labelled_benchmark")
    assert report["butlerBreadth"]["openGapCount"] == 0


def test_label_attestation_is_derived_from_two_distinct_consensus_reviews():
    packet_a, map_a = build_semantic_comparison_packet(
        system_id="label-reviewer-a", seed="round55-label-reviewer-a")
    packet_b, map_b = build_semantic_comparison_packet(
        system_id="label-reviewer-b", seed="round55-label-reviewer-b")
    observations_a = _observations(
        packet_a, {
            alias: [metadata["authorAssessment"]] * 3
            for alias, metadata in map_a["aliases"].items()
        }, repetitions=3)
    observations_b = _observations(
        packet_b, {
            alias: [metadata["authorAssessment"]] * 3
            for alias, metadata in map_b["aliases"].items()
        }, repetitions=3)
    observations_a["configurationFingerprint"] = "1" * 64
    observations_b["configurationFingerprint"] = "2" * 64
    attestation = build_independent_label_attestation(
        reviewer_a_packet=packet_a, reviewer_a_mapping=map_a,
        reviewer_a_observations=observations_a,
        reviewer_b_packet=packet_b, reviewer_b_mapping=map_b,
        reviewer_b_observations=observations_b)
    assert len(attestation["reviewers"]) == 2
    assert len(attestation["labels"]) == 112
    assert all(set(row) == {"caseId", "payloadDigest", "assessment"}
               for row in attestation["labels"])
    assert "authorAssessment" not in json.dumps(attestation)


def test_label_attestation_accepts_error_free_two_thirds_consensus():
    packet_a, map_a = build_semantic_comparison_packet(
        system_id="label-reviewer-a", seed="round59-label-majority-a")
    packet_b, map_b = build_semantic_comparison_packet(
        system_id="label-reviewer-b", seed="round59-label-majority-b")
    observations_a = _observations(
        packet_a, {
            alias: [metadata["authorAssessment"]] * 3
            for alias, metadata in map_a["aliases"].items()
        }, repetitions=3)
    observations_b = _observations(
        packet_b, {
            alias: [metadata["authorAssessment"]] * 3
            for alias, metadata in map_b["aliases"].items()
        }, repetitions=3)
    observations_a["configurationFingerprint"] = "1" * 64
    observations_b["configurationFingerprint"] = "2" * 64
    first_a = observations_a["observations"][0]["runs"]
    first_b = observations_b["observations"][0]["runs"]
    first_a[0] = "absent" if first_a[0] == "present" else "present"
    first_b[1] = "absent" if first_b[1] == "present" else "present"

    attestation = build_independent_label_attestation(
        reviewer_a_packet=packet_a, reviewer_a_mapping=map_a,
        reviewer_a_observations=observations_a,
        reviewer_b_packet=packet_b, reviewer_b_mapping=map_b,
        reviewer_b_observations=observations_b)

    assert len(attestation["labels"]) == 112


def test_label_attestation_accepts_three_reviewer_majority():
    packet_a, map_a = build_semantic_comparison_packet(
        system_id="label-reviewer-a", seed="round59-label-three-a")
    packet_b, map_b = build_semantic_comparison_packet(
        system_id="label-reviewer-b", seed="round59-label-three-b")
    packet_c, map_c = build_semantic_comparison_packet(
        system_id="label-reviewer-c", seed="round59-label-three-c")

    def reviewed(packet, mapping, fingerprint):
        observations = _observations(
            packet, {
                alias: [metadata["authorAssessment"]] * 3
                for alias, metadata in mapping["aliases"].items()
            }, repetitions=3)
        observations["configurationFingerprint"] = fingerprint
        return observations

    observations_a = reviewed(packet_a, map_a, "1" * 64)
    observations_b = reviewed(packet_b, map_b, "2" * 64)
    observations_c = reviewed(packet_c, map_c, "3" * 64)
    first_b = observations_b["observations"][0]["runs"]
    first_b[:] = [
        "absent" if first_b[0] == "present" else "present"
    ] * 3

    attestation = build_independent_label_attestation(
        reviewer_a_packet=packet_a, reviewer_a_mapping=map_a,
        reviewer_a_observations=observations_a,
        reviewer_b_packet=packet_b, reviewer_b_mapping=map_b,
        reviewer_b_observations=observations_b,
        reviewer_c_packet=packet_c, reviewer_c_mapping=map_c,
        reviewer_c_observations=observations_c)

    assert len(attestation["reviewers"]) == 3
    assert len(attestation["labels"]) == 112


def test_label_attestation_allows_one_nonvoting_error_with_two_thirds_consensus():
    packet_a, map_a = build_semantic_comparison_packet(
        system_id="label-reviewer-a", seed="round59-label-error-a")
    packet_b, map_b = build_semantic_comparison_packet(
        system_id="label-reviewer-b", seed="round59-label-error-b")
    observations_a = _observations(
        packet_a, {
            alias: [metadata["authorAssessment"]] * 3
            for alias, metadata in map_a["aliases"].items()
        }, repetitions=3)
    observations_b = _observations(
        packet_b, {
            alias: [metadata["authorAssessment"]] * 3
            for alias, metadata in map_b["aliases"].items()
        }, repetitions=3)
    observations_a["configurationFingerprint"] = "1" * 64
    observations_b["configurationFingerprint"] = "2" * 64
    observations_a["observations"][0]["runs"][0] = "error"

    attestation = build_independent_label_attestation(
        reviewer_a_packet=packet_a, reviewer_a_mapping=map_a,
        reviewer_a_observations=observations_a,
        reviewer_b_packet=packet_b, reviewer_b_mapping=map_b,
        reviewer_b_observations=observations_b)
    assert len(attestation["labels"]) == 112


def test_label_attestation_refuses_split_decisions_with_provider_error():
    packet_a, map_a = build_semantic_comparison_packet(
        system_id="label-reviewer-a", seed="round59-label-split-error-a")
    packet_b, map_b = build_semantic_comparison_packet(
        system_id="label-reviewer-b", seed="round59-label-split-error-b")
    observations_a = _observations(
        packet_a, {
            alias: [metadata["authorAssessment"]] * 3
            for alias, metadata in map_a["aliases"].items()
        }, repetitions=3)
    observations_b = _observations(
        packet_b, {
            alias: [metadata["authorAssessment"]] * 3
            for alias, metadata in map_b["aliases"].items()
        }, repetitions=3)
    observations_a["configurationFingerprint"] = "1" * 64
    observations_b["configurationFingerprint"] = "2" * 64
    first = observations_a["observations"][0]["runs"][0]
    observations_a["observations"][0]["runs"] = [
        first,
        "absent" if first == "present" else "present",
        "error",
    ]

    with pytest.raises(CorpusError, match="two-thirds decisive consensus"):
        build_independent_label_attestation(
            reviewer_a_packet=packet_a, reviewer_a_mapping=map_a,
            reviewer_a_observations=observations_a,
            reviewer_b_packet=packet_b, reviewer_b_mapping=map_b,
            reviewer_b_observations=observations_b)


def test_label_attestation_refuses_reviewer_disagreement():
    packet_a, map_a = build_semantic_comparison_packet(
        system_id="label-reviewer-a", seed="round55-label-disagree-a")
    packet_b, map_b = build_semantic_comparison_packet(
        system_id="label-reviewer-b", seed="round55-label-disagree-b")
    observations_a = _observations(
        packet_a, {
            alias: [metadata["authorAssessment"]] * 3
            for alias, metadata in map_a["aliases"].items()
        }, repetitions=3)
    observations_b = _observations(
        packet_b, {
            alias: [metadata["authorAssessment"]] * 3
            for alias, metadata in map_b["aliases"].items()
        }, repetitions=3)
    observations_a["configurationFingerprint"] = "3" * 64
    observations_b["configurationFingerprint"] = "4" * 64
    observations_b["observations"][0]["runs"] = [
        "absent" if observations_b["observations"][0]["runs"][0] == "present"
        else "present"
    ] * 3
    with pytest.raises(CorpusError, match="reviewers disagree"):
        build_independent_label_attestation(
            reviewer_a_packet=packet_a, reviewer_a_mapping=map_a,
            reviewer_a_observations=observations_a,
            reviewer_b_packet=packet_b, reviewer_b_mapping=map_b,
            reviewer_b_observations=observations_b)


def test_small_external_set_cannot_pass_even_with_independent_labels():
    vp, vm, vo, bp, bm, bo, labels = _synthetic_pair(case_count=28)
    report = compare_semantic_systems(
        verity_packet=vp, verity_mapping=vm, verity_observations=vo,
        butler_packet=bp, butler_mapping=bm, butler_observations=bo,
        label_attestation=labels,
        butler_crosswalk=_complete_butler_crosswalk())
    assert report["status"] == "failed"
    assert report["claim"] is None
    assert "minimumCaseCount" in report["reasonCodes"]


def test_comparison_rejects_alias_map_swapped_between_packet_items():
    vp, vm, vo, bp, bm, bo, labels = _synthetic_pair()
    aliases = list(bm["aliases"])
    first, second = aliases[:2]
    bm["aliases"][first], bm["aliases"][second] = (
        bm["aliases"][second], bm["aliases"][first])
    with pytest.raises(CorpusError, match="alias metadata mismatch"):
        compare_semantic_systems(
            verity_packet=vp, verity_mapping=vm, verity_observations=vo,
            butler_packet=bp, butler_mapping=bm, butler_observations=bo,
            label_attestation=labels)


def test_comparison_revalidates_packet_for_answer_leaks():
    vp, vm, vo, bp, bm, bo, labels = _synthetic_pair()
    leaked_alias = vp["items"][0]["itemId"]
    vp["items"][0]["authorAssessment"] = "present"
    vm["aliases"][leaked_alias]["packetItemDigest"] = _packet_item_digest(
        vp["items"][0])
    with pytest.raises(CorpusError, match="leaks answer metadata"):
        compare_semantic_systems(
            verity_packet=vp, verity_mapping=vm, verity_observations=vo,
            butler_packet=bp, butler_mapping=bm, butler_observations=bo,
            label_attestation=labels)


def test_attestation_refuses_more_than_three_independent_reviewers():
    vp, vm, vo, bp, bm, bo, labels = _synthetic_pair()
    labels["reviewers"].append({
        "reviewerId": "reviewer-c",
        "systemId": "label-reviewer-c",
        "configurationFingerprint": "1" * 64,
        "reviewArtifactDigest": "2" * 64,
    })
    labels["reviewers"].append({
        "reviewerId": "reviewer-d",
        "systemId": "label-reviewer-d",
        "configurationFingerprint": "3" * 64,
        "reviewArtifactDigest": "4" * 64,
    })
    report = compare_semantic_systems(
        verity_packet=vp, verity_mapping=vm, verity_observations=vo,
        butler_packet=bp, butler_mapping=bm, butler_observations=bo,
        label_attestation=labels,
        butler_crosswalk=_complete_butler_crosswalk())
    assert report["status"] == "not_eligible"
    assert report["reasonCodes"] == ["labels_not_independently_reviewed"]


def test_three_reviewer_attestation_requires_all_configurations_distinct():
    vp, vm, vo, bp, bm, bo, labels = _synthetic_pair()
    labels["reviewers"].append({
        "reviewerId": "reviewer-c",
        "systemId": "label-reviewer-c",
        "configurationFingerprint": (
            labels["reviewers"][0]["configurationFingerprint"]),
        "reviewArtifactDigest": "1" * 64,
    })
    report = compare_semantic_systems(
        verity_packet=vp, verity_mapping=vm, verity_observations=vo,
        butler_packet=bp, butler_mapping=bm, butler_observations=bo,
        label_attestation=labels,
        butler_crosswalk=_complete_butler_crosswalk())
    assert report["status"] == "not_eligible"
    assert report["reasonCodes"] == ["labels_not_independently_reviewed"]


def test_packet_cli_uses_private_seed_env_and_writes_local_map(tmp_path):
    output = tmp_path / "verity"
    env = dict(os.environ)
    seed_value = "round55-private-cli-seed"
    env["VERITY_TEST_COMPARISON_SEED"] = seed_value
    proc = subprocess.run(
        [
            sys.executable, "tools/semantic_head_to_head.py", "packet",
            "--system-id", "verity",
            "--seed-env", "VERITY_TEST_COMPARISON_SEED",
            "--output-dir", str(output),
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    packet_text = (output / "packet.json").read_text("utf-8")
    map_text = (output / "alias-map.json").read_text("utf-8")
    assert "authorAssessment" not in packet_text
    assert "authorAssessment" in map_text
    assert seed_value not in packet_text + map_text + proc.stdout + proc.stderr

    env.pop("VERITY_TEST_COMPARISON_SEED")
    refused = subprocess.run(
        [
            sys.executable, "tools/semantic_head_to_head.py", "packet",
            "--system-id", "verity",
            "--seed-env", "VERITY_TEST_COMPARISON_SEED",
            "--output-dir", str(tmp_path / "missing"),
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30)
    assert refused.returncode == 2
    assert "missing or empty" in refused.stderr


class _CandidateProvider:
    def generate_candidates(self, *, call, request):
        return ProviderResponse(ok=True, payload={"candidates": [{
            "proposedCandidateId": "synthetic",
            "findingType": request["findingType"],
            "subject": SUBJECTS[request["findingType"]],
            "claim": "Bounded test claim.",
            "evidenceIds": [
                item["evidenceId"] for item in request["evidence"][:8]],
        }]})


class _RejectingValidator:
    def validate_candidate(self, *, call, request):
        return ProviderResponse(ok=True, payload={
            "candidateId": request["candidate"]["candidateId"],
            "decision": "rejected",
            "reasonCodes": ["evidence_contradicts_claim"],
        })


class _LabelReviewer:
    def __init__(self, assessment="absent"):
        self.assessment = assessment
        self.calls = []

    def review_label(self, *, call, request):
        self.calls.append((call, request))
        return ProviderResponse(
            ok=True, payload={"assessment": self.assessment})


class _TransientLabelReviewer(_LabelReviewer):
    def review_label(self, *, call, request):
        self.calls.append((call, request))
        if len(self.calls) == 1:
            return ProviderResponse(ok=False, reason_code="provider_timeout")
        return ProviderResponse(
            ok=True, payload={"assessment": self.assessment})


def test_verity_observation_runner_is_label_free_and_complete(monkeypatch):
    packet, mapping = build_semantic_comparison_packet(
        system_id="verity", seed="round55-runner-seed")
    monkeypatch.setenv("VERITY_TEST_HEAD_TO_HEAD_KEY", "local-test-value")
    credentials = ProviderCredentials("VERITY_TEST_HEAD_TO_HEAD_KEY")
    common = {
        "provider_id": "test", "model_id": "fixed",
        "base_url": "https://example.invalid/v1",
        "credentials": credentials,
    }
    generator_config = ProviderConfig(
        role="candidate_generator", **common)
    validator_config = ProviderConfig(role="validator", **common)
    observations = evaluate_verity_comparison_observations(
        packet=packet, mapping=mapping, repetitions=2,
        generator=_CandidateProvider(), validator=_RejectingValidator(),
        generator_config=generator_config,
        validator_config=validator_config,
        role_prompt_version="3.0.0")
    assert len(observations["observations"]) == 112
    assert all(row["runs"] == ["absent", "absent"]
               for row in observations["observations"])
    serialized = json.dumps(observations)
    assert "authorAssessment" not in serialized
    assert "Bounded test claim" not in serialized
    assert "local-test-value" not in serialized


def test_verity_runner_cannot_masquerade_as_an_independent_label_reviewer(
        monkeypatch):
    packet, mapping = build_semantic_comparison_packet(
        system_id="label-reviewer-a", seed="round55-runner-role-boundary")
    monkeypatch.setenv("VERITY_TEST_HEAD_TO_HEAD_KEY", "local-test-value")
    credentials = ProviderCredentials("VERITY_TEST_HEAD_TO_HEAD_KEY")
    common = {
        "provider_id": "test", "model_id": "fixed",
        "base_url": "https://example.invalid/v1",
        "credentials": credentials,
    }
    with pytest.raises(CorpusError, match="system-id=verity"):
        evaluate_verity_comparison_observations(
            packet=packet, mapping=mapping, repetitions=2,
            generator=_CandidateProvider(), validator=_RejectingValidator(),
            generator_config=ProviderConfig(
                role="candidate_generator", **common),
            validator_config=ProviderConfig(role="validator", **common),
            role_prompt_version="3.0.0")


def test_independent_label_runner_is_answer_hidden_and_complete(monkeypatch):
    packet, mapping = build_semantic_comparison_packet(
        system_id="label-reviewer-a", seed="round57-label-runner-seed")
    monkeypatch.setenv("VERITY_TEST_HEAD_TO_HEAD_KEY", "local-test-value")
    reviewer = _LabelReviewer()
    observations = evaluate_independent_label_reviewer_observations(
        packet=packet, mapping=mapping, repetitions=3, reviewer=reviewer,
        reviewer_config=ProviderConfig(
            role="label_reviewer", provider_id="independent-test",
            model_id="fixed-reviewer", base_url="https://example.invalid/v1",
            credentials=ProviderCredentials("VERITY_TEST_HEAD_TO_HEAD_KEY")),
        role_prompt_version="1.0.0")
    assert len(observations["observations"]) == 112
    assert all(row["runs"] == ["absent", "absent", "absent"]
               for row in observations["observations"])
    assert len(reviewer.calls) == 336
    assert all(call.call_role == "label_reviewer"
               and call.egress_policy == "answer_hidden_label_review"
               for call, _request in reviewer.calls)
    serialised_requests = json.dumps(
        [request for _call, request in reviewer.calls])
    assert "authorAssessment" not in serialised_requests
    assert "findingType" not in serialised_requests
    assert "payloadDigest" not in serialised_requests
    assert "local-test-value" not in serialised_requests


def test_independent_label_runner_retries_transient_failure(monkeypatch):
    packet, mapping = build_semantic_comparison_packet(
        system_id="label-reviewer-a", seed="round59-label-retry")
    monkeypatch.setenv("VERITY_TEST_HEAD_TO_HEAD_KEY", "local-test-value")
    reviewer = _TransientLabelReviewer()
    observations = evaluate_independent_label_reviewer_observations(
        packet=packet, mapping=mapping, repetitions=3, reviewer=reviewer,
        reviewer_config=ProviderConfig(
            role="label_reviewer", provider_id="independent-test",
            model_id="fixed-reviewer", base_url="https://example.invalid/v1",
            credentials=ProviderCredentials("VERITY_TEST_HEAD_TO_HEAD_KEY")),
        max_total_calls=672, max_attempts_per_repetition=2,
        role_prompt_version="1.0.0")

    assert all(row["runs"] == ["absent", "absent", "absent"]
               for row in observations["observations"])
    assert len(reviewer.calls) == 337
    assert reviewer.calls[0][0].call_id.endswith("-attempt-1")
    assert reviewer.calls[1][0].call_id.endswith("-attempt-2")


def test_independent_label_runner_refuses_evaluated_system_id(monkeypatch):
    packet, mapping = build_semantic_comparison_packet(
        system_id="verity", seed="round57-label-runner-boundary")
    monkeypatch.setenv("VERITY_TEST_HEAD_TO_HEAD_KEY", "local-test-value")
    with pytest.raises(CorpusError, match="non-evaluated system id"):
        evaluate_independent_label_reviewer_observations(
            packet=packet, mapping=mapping, repetitions=2,
            reviewer=_LabelReviewer(),
            reviewer_config=ProviderConfig(
                role="label_reviewer", provider_id="independent-test",
                model_id="fixed-reviewer", base_url="https://example.invalid/v1",
                credentials=ProviderCredentials("VERITY_TEST_HEAD_TO_HEAD_KEY")),
            role_prompt_version="1.0.0")


def test_independent_label_runner_requires_odd_repetition_count(monkeypatch):
    packet, mapping = build_semantic_comparison_packet(
        system_id="label-reviewer-a", seed="round59-label-repetitions")
    monkeypatch.setenv("VERITY_TEST_HEAD_TO_HEAD_KEY", "local-test-value")
    with pytest.raises(CorpusError, match="must be odd and 3..9"):
        evaluate_independent_label_reviewer_observations(
            packet=packet, mapping=mapping, repetitions=2,
            reviewer=_LabelReviewer(),
            reviewer_config=ProviderConfig(
                role="label_reviewer", provider_id="independent-test",
                model_id="fixed-reviewer",
                base_url="https://example.invalid/v1",
                credentials=ProviderCredentials(
                    "VERITY_TEST_HEAD_TO_HEAD_KEY")),
            role_prompt_version="1.0.0")


def test_comparator_refuses_reviewer_configuration_used_by_system():
    vp, vm, vo, bp, bm, bo, labels = _synthetic_pair()
    labels["reviewers"][0]["configurationFingerprint"] = (
        vo["configurationFingerprint"])
    report = compare_semantic_systems(
        verity_packet=vp, verity_mapping=vm, verity_observations=vo,
        butler_packet=bp, butler_mapping=bm, butler_observations=bo,
        label_attestation=labels, butler_crosswalk=_complete_butler_crosswalk())
    assert report["status"] == "not_eligible"
    assert report["reasonCodes"] == [
        "label_reviewer_configuration_not_independent"]


def test_butler_reference_response_body_has_a_streaming_byte_cap():
    source = (
        ROOT / "tools/butler_reference_entry.ts"
    ).read_text("utf-8")
    assert "async function readBoundedResponse(" in source
    assert "totalBytes > maxBytes" in source
    assert "response.arrayBuffer()" not in source
