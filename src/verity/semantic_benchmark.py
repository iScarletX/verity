"""Blind, same-case semantic comparison for Verity and a reference system.

This module never calls a model. It builds answer-free packets, validates
scrubbed repeated observations, and computes an absolute plus relative gate.
Provisional author labels can exercise the plumbing but can never produce a
superiority claim.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .blind_review import _anonymous_identity, _safe_content_files
from .corpus import (CASE_ID_RE, CORPUS_DIR, CorpusError,
                     _case_payload_digest, _safe_case_path)
from .intake import IntakeBudget, intake_directory, intake_text
from .report import review_to_dict
from .review import ReviewInputs, run_review
from .semantic.catalog import CATALOG
from .semantic.config import ProviderConfig
from .standards import load_detector_mappings, load_risks


COMPARISON_MANIFEST_PATH = CORPUS_DIR / "semantic_comparison_v3.json"
BUTLER_CROSSWALK_PATH = (
    CORPUS_DIR.parents[1] / "reference" / "butler_crosswalk.json")
COMPARISON_PROTOCOL_ID = "verity-butler-semantic-head-to-head"
COMPARISON_PROTOCOL_VERSION = "3.0.0"
OBSERVATIONS = {"present", "absent", "inconclusive", "error"}
INDEPENDENT_LABEL_STATUS = "independent_ai_review"
DEFAULT_COMPARISON_MAX_TOTAL_CALLS = 340
BUTLER_REFERENCE_SKILL_MAP_VERSION = "2.0.0"

# Read-only Butler v6 reference mapping. These are comparison routes, not
# Verity labels and not claims that Butler fully covers the associated risk.
BUTLER_REFERENCE_SKILLS = {
    "semantic.prompt.instruction_conflict": (
        "01_clarity_contradiction",),
    "semantic.prompt.missing_output_contract": (
        "02_contract_output_format",),
    "semantic.skill.declared_behavior_mismatch": (
        "05_robustness_skill_dangerous_pattern",),
    "semantic.prompt.trust_boundary_ambiguity": (
        "05_robustness_injection_defense",),
    "semantic.prompt.excessive_tool_scope": (
        "03_resource_function_call_contract",
        "05_robustness_skill_dangerous_pattern"),
    "semantic.skill.permission_capability_mismatch": (
        "05_robustness_skill_dangerous_pattern",),
    "semantic.skill.external_instruction_trust_gap": (
        "05_robustness_injection_defense",
        "05_robustness_skill_dangerous_pattern"),
    "semantic.prompt.output_budget_pressure": (
        "03_resource_token_budget",),
    "semantic.prompt.authority_boundary_ambiguity": (
        "01_clarity_task_boundary", "05_robustness_safety_policy"),
    "semantic.prompt.failure_strategy_gap": (
        "03_resource_function_call_contract",
        "05_robustness_abnormal_input"),
    "semantic.prompt.ambiguous_operational_criteria": (
        "01_clarity_ambiguity",),
    "semantic.prompt.grounding_requirement_gap": (
        "05_robustness_hallucination_control",
        "02_contract_reference_integrity"),
    "semantic.prompt.sensitive_reasoning_exposure": (
        "03_resource_reasoning_isolation",
        "05_robustness_secret_leak"),
    "semantic.prompt.verification_step_gap": (
        "06_quality_self_check",),
    "semantic.prompt.input_and_default_contract_gap": (
        "02_contract_input_completeness",
        "05_robustness_abnormal_input"),
    "semantic.prompt.example_contract_mismatch": (
        "01_clarity_example_consistency",
        "06_quality_few_shot"),
    "semantic.prompt.tool_call_contract_gap": (
        "03_resource_function_call_contract",),
    "semantic.prompt.capability_dependency_gap": (
        "04_interop_portability",
        "06_quality_model_capability"),
    "semantic.prompt.sensitive_data_handling_gap": (
        "07_compliance_privacy",),
}

COMPARISON_THRESHOLDS = {
    "minimumCaseCount": 76,
    "minimumRiskCount": 18,
    "minimumFindingTypeCount": 19,
    "minimumRepetitions": 2,
    "minimumRecall": 0.90,
    "maximumSafeFalsePositiveRate": 0.20,
    "minimumStabilityRate": 0.80,
    "maximumErrorRate": 0.05,
    "maximumInconclusiveRate": 0.10,
}


def _strict_json(path: Path, *,
                 label: str = "semantic comparison manifest") -> Dict[str, Any]:
    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise CorpusError(f"duplicate JSON key: {key}")
            value[key] = item
        return value
    try:
        value = json.loads(path.read_text("utf-8"),
                           object_pairs_hook=no_duplicates)
    except CorpusError:
        raise
    except Exception as exc:
        raise CorpusError(f"cannot read {label}") from exc
    if not isinstance(value, dict):
        raise CorpusError(f"{label} must be an object")
    return value


def _validate_butler_crosswalk(value: Dict[str, Any]) -> Dict[str, Any]:
    if set(value) != {
            "schemaVersion", "referenceSystem", "referenceCommit",
            "referenceSourceFingerprint", "inventorySource",
            "inventoryCount", "policy", "entries"}:
        raise CorpusError("Butler crosswalk schema invalid")
    if (value.get("schemaVersion") != 1
            or value.get("referenceSystem") != "butler"
            or not isinstance(value.get("referenceCommit"), str)
            or not re.fullmatch(r"[0-9a-f]{40}", value["referenceCommit"])
            or not isinstance(value.get("referenceSourceFingerprint"), str)
            or not re.fullmatch(
                r"[0-9a-f]{64}", value["referenceSourceFingerprint"])
            or value.get("inventorySource")
            != "src/core/skillLoader/loadBuiltinSkills.ts"):
        raise CorpusError("Butler crosswalk identity invalid")
    count = value.get("inventoryCount")
    entries = value.get("entries")
    if (not isinstance(count, int) or isinstance(count, bool) or count != 45
            or not isinstance(entries, list) or len(entries) != count):
        raise CorpusError("Butler crosswalk inventory invalid")
    policy = value.get("policy")
    if (not isinstance(policy, dict) or set(policy) != {
            "claimRequiresNoOpenGaps", "maximumNotAdopted",
            "coveredDefinition"}
            or policy.get("claimRequiresNoOpenGaps") is not True
            or not isinstance(policy.get("maximumNotAdopted"), int)
            or isinstance(policy.get("maximumNotAdopted"), bool)
            or not 0 <= policy["maximumNotAdopted"] <= 5
            or not isinstance(policy.get("coveredDefinition"), str)
            or not 40 <= len(policy["coveredDefinition"]) <= 300):
        raise CorpusError("Butler crosswalk policy invalid")

    known = {
        detector_id
        for detector_type, detector_id in load_detector_mappings(load_risks())
        if detector_type in {"deterministic_rule", "semantic_finding_type"}
    }
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
                "butlerSkillId", "status", "verityFindingTypes",
                "plannedFindingType", "rationale"}:
            raise CorpusError("Butler crosswalk entry schema invalid")
        skill_id = entry.get("butlerSkillId")
        status = entry.get("status")
        finding_types = entry.get("verityFindingTypes")
        planned = entry.get("plannedFindingType")
        rationale = entry.get("rationale")
        if (not isinstance(skill_id, str)
                or not re.fullmatch(r"\d{2}_[a-z0-9_]{3,80}", skill_id)
                or skill_id in seen
                or status not in {"covered", "not_adopted", "open_gap"}
                or not isinstance(finding_types, list)
                or len(finding_types) != len(set(finding_types))
                or any(item not in known for item in finding_types)
                or not isinstance(rationale, str) or len(rationale) < 24
                or len(rationale) > 500):
            raise CorpusError("Butler crosswalk entry invalid")
        if status == "covered":
            if not finding_types or planned is not None:
                raise CorpusError("covered Butler entry lacks Verity coverage")
        elif status == "open_gap":
            if (not isinstance(planned, str)
                    or not re.fullmatch(
                        r"(?:semantic\.)?(?:prompt|skill)\.[a-z0-9_.]{3,100}",
                        planned)):
                raise CorpusError("open Butler gap lacks a planned Finding Type")
        elif finding_types or planned is not None:
            raise CorpusError("not-adopted Butler entry cannot claim coverage")
        seen.add(skill_id)
    return value


def load_butler_crosswalk(
        path: Path = BUTLER_CROSSWALK_PATH) -> Dict[str, Any]:
    return _validate_butler_crosswalk(_strict_json(
        path, label="Butler reference crosswalk"))


def butler_breadth_summary(
        crosswalk: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    value = _validate_butler_crosswalk(
        crosswalk) if crosswalk is not None else load_butler_crosswalk()
    counts = {
        status: sum(entry["status"] == status for entry in value["entries"])
        for status in ("covered", "not_adopted", "open_gap")
    }
    claim_ready = (
        counts["open_gap"] == 0
        and counts["not_adopted"] <= value["policy"]["maximumNotAdopted"]
    )
    return {
        "referenceCommit": value["referenceCommit"],
        "referenceSourceFingerprint": value["referenceSourceFingerprint"],
        "inventoryCount": value["inventoryCount"],
        "coveredCount": counts["covered"],
        "notAdoptedCount": counts["not_adopted"],
        "openGapCount": counts["open_gap"],
        "claimReady": claim_ready,
    }


def load_semantic_comparison_manifest(
        path: Path = COMPARISON_MANIFEST_PATH) -> Dict[str, Any]:
    value = _strict_json(path)
    if set(value) != {
            "schemaVersion", "protocolId", "protocolVersion", "status",
            "license", "provenance", "labelStatus", "description", "cases"}:
        raise CorpusError("semantic comparison manifest schema invalid")
    if (value.get("schemaVersion") != 1
            or value.get("protocolId") != COMPARISON_PROTOCOL_ID
            or value.get("protocolVersion") != COMPARISON_PROTOCOL_VERSION
            or value.get("status") != "development_calibration"
            or value.get("license") != "Apache-2.0"
            or value.get("provenance") != "verity_synthetic"
            or value.get("labelStatus") != "provisional_single_review"):
        raise CorpusError("semantic comparison manifest identity invalid")
    if not isinstance(value.get("description"), str) or not value["description"]:
        raise CorpusError("semantic comparison description required")
    cases = value.get("cases")
    if not isinstance(cases, list) or not cases:
        raise CorpusError("semantic comparison cases invalid")

    mappings = load_detector_mappings(load_risks())
    seen_ids = set()
    seen_digests = set()
    coverage = defaultdict(set)
    base = {
        "caseId", "objectType", "language", "path", "findingType",
        "riskId", "authorAssessment", "labelStatus",
    }
    for case in cases:
        expected = set(base)
        if isinstance(case, dict) and case.get("objectType") == "prompt":
            expected.add("promptKind")
        if not isinstance(case, dict) or set(case) != expected:
            raise CorpusError("semantic comparison case schema invalid")
        case_id = case.get("caseId")
        if (not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id)
                or case_id in seen_ids):
            raise CorpusError("semantic comparison case id invalid")
        seen_ids.add(case_id)
        object_type = case.get("objectType")
        if object_type not in {"prompt", "skill"}:
            raise CorpusError("semantic comparison object type invalid")
        if (object_type == "prompt"
                and case.get("promptKind") not in {"user_prompt", "system_prompt"}):
            raise CorpusError("semantic comparison prompt kind invalid")
        finding_type = case.get("findingType")
        if (finding_type not in CATALOG
                or CATALOG[finding_type][0].engine != object_type):
            raise CorpusError("semantic comparison finding type invalid")
        mapping = mappings.get(("semantic_finding_type", finding_type))
        if not mapping or case.get("riskId") not in mapping["riskIds"]:
            raise CorpusError("semantic comparison risk mapping invalid")
        if case.get("authorAssessment") not in {"present", "absent"}:
            raise CorpusError("semantic comparison assessment invalid")
        if case.get("labelStatus") != "provisional_single_review":
            raise CorpusError("semantic comparison labels must remain provisional")
        if not isinstance(case.get("language"), str) or not case["language"]:
            raise CorpusError("semantic comparison language invalid")
        case_path = _safe_case_path(case["path"])
        if ((object_type == "prompt" and not case_path.is_file())
                or (object_type == "skill" and not case_path.is_dir())):
            raise CorpusError("semantic comparison path kind mismatch")
        digest = _case_payload_digest(case_path)
        if digest in seen_digests:
            raise CorpusError("semantic comparison duplicate payload")
        seen_digests.add(digest)
        case["payloadDigest"] = digest
        coverage[finding_type].add(case["authorAssessment"])
    if set(coverage) != set(CATALOG):
        raise CorpusError("semantic comparison lacks controlled finding types")
    if any(states != {"present", "absent"} for states in coverage.values()):
        raise CorpusError("semantic comparison lacks present/absent pair")
    return value


def validate_semantic_comparison_seed_coverage(
        path: Path = COMPARISON_MANIFEST_PATH) -> int:
    """Prove every answer-hidden case reaches its controlled extractor."""
    manifest = load_semantic_comparison_manifest(path)
    checked = 0
    for case in manifest["cases"]:
        case_path = _safe_case_path(case["path"])
        if case["objectType"] == "prompt":
            snapshot, file_bytes = intake_text(
                case_path.read_text("utf-8"), prompt_kind=case["promptKind"])
            review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
        else:
            snapshot, file_bytes = intake_directory(
                case_path, budget=IntakeBudget(
                    max_files=64, max_file_size=512 * 1024,
                    max_total_size=2 * 1024 * 1024))
            review = run_review(ReviewInputs(
                "skill", snapshot, file_bytes, profile="minimal"))
        extractor = CATALOG[case["findingType"]][1]
        if not extractor(review_to_dict(review), file_bytes):
            raise CorpusError(
                f"semantic comparison case has no seed: {case['caseId']}")
        checked += 1
    return checked


def _digest(seed: str, value: str) -> str:
    return hashlib.sha256((seed + "\0" + value).encode()).hexdigest()


def _corpus_fingerprint(manifest: Dict[str, Any]) -> str:
    payload = [
        {
            "caseId": case["caseId"],
            "payloadDigest": case["payloadDigest"],
            "findingType": case["findingType"],
        }
        for case in sorted(manifest["cases"], key=lambda row: row["caseId"])
    ]
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _packet_item_digest(item: Dict[str, Any]) -> str:
    raw = json.dumps(
        item, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def build_semantic_comparison_packet(
        *, system_id: str, seed: str,
        manifest_path: Path = COMPARISON_MANIFEST_PATH
        ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if (not isinstance(system_id, str) or not system_id.strip()
            or len(system_id) > 80 or len(seed) < 16):
        raise CorpusError("semantic comparison system id/seed invalid")
    manifest = load_semantic_comparison_manifest(manifest_path)
    risks = load_risks()
    rows = list(manifest["cases"])
    rows.sort(key=lambda row: _digest(seed, "order:" + row["caseId"]))
    items = []
    aliases = {}
    for index, case in enumerate(rows, 1):
        alias = f"SC-{index:03d}-{_digest(seed, 'alias:' + case['caseId'])[:6]}"
        root_name, files = _safe_content_files(case["path"])
        display_root, files = _anonymous_identity(
            case["caseId"], seed, root_name, files)
        definition = CATALOG[case["findingType"]][0]
        item = {
            "itemId": alias,
            "objectType": case["objectType"],
            "language": case["language"],
            "targetRisk": {
                "title": risks[case["riskId"]]["title"],
                "definition": risks[case["riskId"]]["definition"],
                "reviewBoundary": risks[case["riskId"]]["layerBoundaries"][
                    "L1_semantic"],
                "falsificationQuestion": definition.falsificationQuestion,
            },
            "artifact": {
                "displayRootName": display_root or None,
                "files": [{"path": path, "content": content}
                          for path, content in files],
            },
        }
        if case["objectType"] == "prompt":
            item["promptKind"] = case["promptKind"]
        items.append(item)
        aliases[alias] = {
            "caseId": case["caseId"],
            "findingType": case["findingType"],
            "riskId": case["riskId"],
            "authorAssessment": case["authorAssessment"],
            "payloadDigest": case["payloadDigest"],
            "packetItemDigest": _packet_item_digest(item),
        }
    packet = {
        "schemaVersion": 1,
        "protocolId": COMPARISON_PROTOCOL_ID,
        "protocolVersion": COMPARISON_PROTOCOL_VERSION,
        "systemId": system_id,
        "itemCount": len(items),
        "corpusFingerprint": _corpus_fingerprint(manifest),
        "instructions": {
            "question": (
                "For each item and only its target risk, report present or "
                "absent; use inconclusive or error instead of guessing."),
            "independence": (
                "Do not seek labels, another system's output, or Verity output."),
            "repetitions": (
                "Run every item independently at least twice under one frozen "
                "system configuration."),
        },
        "items": items,
    }
    mapping = {
        "schemaVersion": 1,
        "protocolId": COMPARISON_PROTOCOL_ID,
        "protocolVersion": COMPARISON_PROTOCOL_VERSION,
        "systemId": system_id,
        "corpusFingerprint": packet["corpusFingerprint"],
        "aliases": aliases,
    }
    _validate_packet(packet)
    _validate_mapping(mapping, packet)
    return packet, mapping


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _validate_packet(packet: Dict[str, Any]) -> None:
    if not isinstance(packet, dict) or set(packet) != {
            "schemaVersion", "protocolId", "protocolVersion", "systemId",
            "itemCount", "corpusFingerprint", "instructions", "items"}:
        raise CorpusError("semantic comparison packet schema invalid")
    system_id = packet.get("systemId")
    fingerprint = packet.get("corpusFingerprint")
    if (packet.get("schemaVersion") != 1
            or packet.get("protocolId") != COMPARISON_PROTOCOL_ID
            or packet.get("protocolVersion") != COMPARISON_PROTOCOL_VERSION
            or not isinstance(system_id, str) or not system_id.strip()
            or len(system_id) > 80
            or not isinstance(fingerprint, str)
            or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)):
        raise CorpusError("semantic comparison packet identity invalid")
    instructions = packet.get("instructions")
    if (not isinstance(instructions, dict)
            or set(instructions) != {
                "question", "independence", "repetitions"}
            or any(not isinstance(value, str) or not value
                   for value in instructions.values())):
        raise CorpusError("semantic comparison packet instructions invalid")
    items = packet.get("items")
    if (not isinstance(items, list) or packet.get("itemCount") != len(items)
            or not items):
        raise CorpusError("semantic comparison packet size invalid")
    if any(not isinstance(item, dict) for item in items):
        raise CorpusError("semantic comparison packet item invalid")
    forbidden = {
        "caseId", "findingType", "riskId", "authorAssessment",
        "expectedAssessment", "labelStatus", "payloadDigest",
    }
    leaked = forbidden & set(_walk_keys(packet))
    if leaked:
        raise CorpusError("semantic comparison packet leaks answer metadata")
    ids = [item.get("itemId") for item in items]
    if len(ids) != len(set(ids)) or not all(
            isinstance(item, str)
            and re.fullmatch(r"SC-\d{3}-[0-9a-f]{6}", item)
            for item in ids):
        raise CorpusError("semantic comparison aliases invalid")
    for item in items:
        object_type = item.get("objectType")
        expected = {
            "itemId", "objectType", "language", "targetRisk", "artifact"}
        if object_type == "prompt":
            expected.add("promptKind")
        if (not isinstance(item, dict) or set(item) != expected
                or object_type not in {"prompt", "skill"}
                or (object_type == "prompt"
                    and item.get("promptKind")
                    not in {"user_prompt", "system_prompt"})
                or not isinstance(item.get("language"), str)
                or not item["language"] or len(item["language"]) > 40):
            raise CorpusError("semantic comparison packet item invalid")
        target = item.get("targetRisk")
        if (not isinstance(target, dict) or set(target) != {
                "title", "definition", "reviewBoundary",
                "falsificationQuestion"}
                or any(not isinstance(value, str) or not value
                       or len(value) > 2000 for value in target.values())):
            raise CorpusError("semantic comparison target risk invalid")
        artifact = item.get("artifact")
        if not isinstance(artifact, dict) or set(artifact) != {
                "displayRootName", "files"}:
            raise CorpusError("semantic comparison artifact invalid")
        display_root = artifact.get("displayRootName")
        if (display_root is not None
                and (not isinstance(display_root, str)
                     or not display_root or len(display_root) > 256
                     or "/" in display_root or "\\" in display_root
                     or display_root in {".", ".."})):
            raise CorpusError("semantic comparison display root invalid")
        files = artifact.get("files")
        if not isinstance(files, list) or not 1 <= len(files) <= 64:
            raise CorpusError("semantic comparison artifact files invalid")
        seen_paths = set()
        total_bytes = 0
        for file_info in files:
            if (not isinstance(file_info, dict)
                    or set(file_info) != {"path", "content"}):
                raise CorpusError("semantic comparison artifact file invalid")
            path = file_info.get("path")
            content = file_info.get("content")
            parts = path.split("/") if isinstance(path, str) else []
            if (not isinstance(path, str) or not path or len(path) > 512
                    or path.startswith(("/", "\\"))
                    or "\\" in path or any(part in {"", ".", ".."}
                                          for part in parts)
                    or path in seen_paths
                    or not isinstance(content, str)):
                raise CorpusError("semantic comparison artifact file invalid")
            content_bytes = len(content.encode("utf-8"))
            if content_bytes > 512 * 1024:
                raise CorpusError("semantic comparison artifact file too large")
            total_bytes += content_bytes
            seen_paths.add(path)
        if total_bytes > 2 * 1024 * 1024:
            raise CorpusError("semantic comparison artifact too large")


def _validate_mapping(mapping: Dict[str, Any],
                      packet: Dict[str, Any]) -> Dict[str, Any]:
    # Packet validation must happen again at every trust boundary. Otherwise
    # a caller could add answer metadata and recompute packetItemDigest in the
    # local alias map before comparison.
    _validate_packet(packet)
    if set(mapping) != {
            "schemaVersion", "protocolId", "protocolVersion", "systemId",
            "corpusFingerprint", "aliases"}:
        raise CorpusError("semantic comparison alias map schema invalid")
    if (mapping.get("schemaVersion") != 1
            or mapping.get("protocolId") != COMPARISON_PROTOCOL_ID
            or mapping.get("protocolVersion") != COMPARISON_PROTOCOL_VERSION
            or mapping.get("systemId") != packet.get("systemId")
            or mapping.get("corpusFingerprint")
            != packet.get("corpusFingerprint")):
        raise CorpusError("semantic comparison alias map identity invalid")
    aliases = mapping.get("aliases")
    if not isinstance(aliases, dict):
        raise CorpusError("semantic comparison aliases invalid")
    items = {
        item.get("itemId"): item
        for item in packet.get("items") or []
        if isinstance(item, dict)
    }
    if set(aliases) != set(items):
        raise CorpusError("semantic comparison alias map is incomplete")
    case_ids = set()
    payload_digests = set()
    for alias, metadata in aliases.items():
        if not isinstance(metadata, dict) or set(metadata) != {
                "caseId", "findingType", "riskId", "authorAssessment",
                "payloadDigest", "packetItemDigest"}:
            raise CorpusError("semantic comparison alias metadata invalid")
        case_id = metadata.get("caseId")
        payload_digest = metadata.get("payloadDigest")
        item_digest = metadata.get("packetItemDigest")
        if (not isinstance(case_id, str) or not CASE_ID_RE.fullmatch(case_id)
                or case_id in case_ids
                or not isinstance(metadata.get("findingType"), str)
                or not isinstance(metadata.get("riskId"), str)
                or metadata.get("authorAssessment") not in {"present", "absent"}
                or not isinstance(payload_digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", payload_digest)
                or payload_digest in payload_digests
                or item_digest != _packet_item_digest(items[alias])):
            raise CorpusError("semantic comparison alias metadata mismatch")
        case_ids.add(case_id)
        payload_digests.add(payload_digest)
    return mapping


def validate_observations(observations: Dict[str, Any],
                          packet: Dict[str, Any]) -> Dict[str, Any]:
    _validate_packet(packet)
    if set(observations) != {
            "schemaVersion", "protocolId", "protocolVersion", "systemId",
            "configurationFingerprint", "corpusFingerprint", "repetitions",
            "observations"}:
        raise CorpusError("semantic observations schema invalid")
    if (observations.get("schemaVersion") != 1
            or observations.get("protocolId") != COMPARISON_PROTOCOL_ID
            or observations.get("protocolVersion") != COMPARISON_PROTOCOL_VERSION
            or observations.get("systemId") != packet.get("systemId")
            or observations.get("corpusFingerprint")
            != packet.get("corpusFingerprint")):
        raise CorpusError("semantic observations identity invalid")
    fingerprint = observations.get("configurationFingerprint")
    if not isinstance(fingerprint, str) or not re.fullmatch(
            r"[0-9a-f]{64}", fingerprint):
        raise CorpusError("semantic observations configuration invalid")
    repetitions = observations.get("repetitions")
    if (not isinstance(repetitions, int) or isinstance(repetitions, bool)
            or not 2 <= repetitions <= 10):
        raise CorpusError("semantic observations repetitions invalid")
    expected = {item["itemId"] for item in packet["items"]}
    rows = observations.get("observations")
    if not isinstance(rows, list):
        raise CorpusError("semantic observations rows invalid")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"itemId", "runs"}:
            raise CorpusError("semantic observation row schema invalid")
        item_id = row.get("itemId")
        runs = row.get("runs")
        if item_id not in expected or item_id in seen:
            raise CorpusError("semantic observation item invalid")
        seen.add(item_id)
        if (not isinstance(runs, list) or len(runs) != repetitions
                or any(run not in OBSERVATIONS for run in runs)):
            raise CorpusError("semantic observation runs invalid")
    if seen != expected:
        raise CorpusError("semantic observations incomplete")
    return observations


def _canonical_runs(mapping: Dict[str, Any],
                    observations: Dict[str, Any]) -> Dict[str, List[str]]:
    rows = {
        row["itemId"]: row["runs"]
        for row in observations["observations"]
    }
    return {
        metadata["caseId"]: rows[alias]
        for alias, metadata in mapping["aliases"].items()
    }


def _canonical_case_metadata(mapping: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    return {
        metadata["caseId"]: {
            key: metadata[key]
            for key in ("findingType", "riskId", "payloadDigest")
        }
        for metadata in mapping["aliases"].values()
    }


def _review_artifact_digest(packet: Dict[str, Any],
                            observations: Dict[str, Any]) -> str:
    raw = json.dumps(
        {"packet": packet, "observations": observations},
        ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def build_independent_label_attestation(
        *, reviewer_a_packet: Dict[str, Any],
        reviewer_a_mapping: Dict[str, Any],
        reviewer_a_observations: Dict[str, Any],
        reviewer_b_packet: Dict[str, Any],
        reviewer_b_mapping: Dict[str, Any],
        reviewer_b_observations: Dict[str, Any]) -> Dict[str, Any]:
    """Derive labels only from two stable, agreeing answer-hidden reviews."""
    reviewer_rows = (
        (reviewer_a_packet, reviewer_a_mapping, reviewer_a_observations),
        (reviewer_b_packet, reviewer_b_mapping, reviewer_b_observations),
    )
    for packet, mapping, observations in reviewer_rows:
        _validate_mapping(mapping, packet)
        validate_observations(observations, packet)
    if (reviewer_a_packet.get("corpusFingerprint")
            != reviewer_b_packet.get("corpusFingerprint")):
        raise CorpusError("independent reviewers used different corpora")
    system_ids = [
        reviewer_a_packet.get("systemId"), reviewer_b_packet.get("systemId")]
    if (len(set(system_ids)) != 2
            or any(not isinstance(item, str) or not item for item in system_ids)
            or set(system_ids) & {"verity", "butler"}):
        raise CorpusError("label reviewers must be distinct from evaluated systems")
    fingerprints = [
        reviewer_a_observations["configurationFingerprint"],
        reviewer_b_observations["configurationFingerprint"],
    ]
    if len(set(fingerprints)) != 2:
        raise CorpusError("label reviewer configurations must be distinct")
    metadata_a = _canonical_case_metadata(reviewer_a_mapping)
    metadata_b = _canonical_case_metadata(reviewer_b_mapping)
    if metadata_a != metadata_b:
        raise CorpusError("independent reviewer canonical metadata differs")
    runs_a = _canonical_runs(
        reviewer_a_mapping, reviewer_a_observations)
    runs_b = _canonical_runs(
        reviewer_b_mapping, reviewer_b_observations)
    labels = []
    for case_id in sorted(metadata_a):
        left = runs_a[case_id]
        right = runs_b[case_id]
        if (not left or not right
                or any(value not in {"present", "absent"} for value in left + right)
                or len(set(left)) != 1 or len(set(right)) != 1):
            raise CorpusError(
                "independent label review must be decisive and stable")
        if left[0] != right[0]:
            raise CorpusError("independent label reviewers disagree")
        labels.append({
            "caseId": case_id,
            "payloadDigest": metadata_a[case_id]["payloadDigest"],
            "assessment": left[0],
        })
    attestation = {
        "schemaVersion": 1,
        "protocolId": COMPARISON_PROTOCOL_ID,
        "protocolVersion": COMPARISON_PROTOCOL_VERSION,
        "corpusFingerprint": reviewer_a_packet["corpusFingerprint"],
        "labelStatus": INDEPENDENT_LABEL_STATUS,
        "reviewers": [
            {
                "reviewerId": packet["systemId"],
                "systemId": packet["systemId"],
                "configurationFingerprint": observations[
                    "configurationFingerprint"],
                "reviewArtifactDigest": _review_artifact_digest(
                    packet, observations),
            }
            for packet, _mapping, observations in reviewer_rows
        ],
        "labels": labels,
    }
    status, _labels = _label_map(
        attestation, mapping=reviewer_a_mapping)
    if status != "independent":
        raise CorpusError("independent label attestation construction failed")
    return attestation


def evaluate_verity_comparison_observations(
        *, packet: Dict[str, Any], mapping: Dict[str, Any],
        repetitions: int, generator, validator,
        generator_config: ProviderConfig, validator_config: ProviderConfig,
        temperature: float = 0.0, max_output_tokens: int = 800,
        max_total_calls: int = DEFAULT_COMPARISON_MAX_TOTAL_CALLS,
        role_prompt_version: str = "unspecified",
        manifest_path: Path = COMPARISON_MANIFEST_PATH) -> Dict[str, Any]:
    """Run Verity on the answer-free v3 calibration and emit observations.

    Labels and author assessments are never read into the Provider path.
    """
    from .semantic_quality import _config_fingerprint, _run_case

    if (packet.get("systemId") != mapping.get("systemId")
            or packet.get("corpusFingerprint")
            != mapping.get("corpusFingerprint")):
        raise CorpusError("semantic comparison packet/map identity mismatch")
    if packet.get("systemId") != "verity":
        raise CorpusError(
            "Verity runner requires a system-id=verity packet")
    _validate_mapping(mapping, packet)
    if (not isinstance(repetitions, int) or isinstance(repetitions, bool)
            or not 2 <= repetitions <= 10):
        raise CorpusError("semantic comparison repetitions invalid")
    if (generator_config.role != "candidate_generator"
            or validator_config.role != "validator"):
        raise CorpusError("semantic comparison Provider roles invalid")
    if (not generator_config.credentials.resolve()
            or not validator_config.credentials.resolve()):
        raise CorpusError("semantic comparison credentials missing before run")
    manifest = load_semantic_comparison_manifest(manifest_path)
    if packet.get("corpusFingerprint") != _corpus_fingerprint(manifest):
        raise CorpusError("semantic comparison packet corpus is stale")
    required_calls = len(manifest["cases"]) * repetitions * 2
    if (not isinstance(max_total_calls, int) or max_total_calls < required_calls):
        raise CorpusError(
            f"semantic comparison call budget requires {required_calls}, "
            f"configured {max_total_calls}")
    case_by_id = {case["caseId"]: case for case in manifest["cases"]}
    alias_by_case = {
        metadata["caseId"]: alias
        for alias, metadata in mapping["aliases"].items()
    }
    if set(alias_by_case) != set(case_by_id):
        raise CorpusError("semantic comparison alias map is incomplete")

    rows = []
    for case_id in sorted(case_by_id):
        case = case_by_id[case_id]
        runtime_case = {
            key: case[key] for key in (
                "caseId", "objectType", "language", "path", "findingType")
        }
        if case["objectType"] == "prompt":
            runtime_case["promptKind"] = case["promptKind"]
        runs = []
        for _ in range(repetitions):
            observed, _detail = _run_case(
                runtime_case, generator=generator, validator=validator,
                generator_config=generator_config,
                validator_config=validator_config)
            runs.append({
                "confirmed": "present",
                "rejected": "absent",
                "no_candidate": "absent",
                "insufficient_evidence": "inconclusive",
                "error": "error",
            }[observed])
        rows.append({"itemId": alias_by_case[case_id], "runs": runs})
    fingerprint = _config_fingerprint(
        generator_config, validator_config, temperature=temperature,
        max_output_tokens=max_output_tokens, repetitions=repetitions,
        role_prompt_version=role_prompt_version,
        protocol_version=COMPARISON_PROTOCOL_VERSION,
        corpus_fingerprint=packet["corpusFingerprint"])
    observations = {
        "schemaVersion": 1,
        "protocolId": COMPARISON_PROTOCOL_ID,
        "protocolVersion": COMPARISON_PROTOCOL_VERSION,
        "systemId": packet["systemId"],
        "configurationFingerprint": fingerprint,
        "corpusFingerprint": packet["corpusFingerprint"],
        "repetitions": repetitions,
        "observations": rows,
    }
    return validate_observations(observations, packet)


def _label_map(label_attestation: Optional[Dict[str, Any]],
               *, mapping: Dict[str, Any]) -> Tuple[str, Dict[str, str]]:
    if label_attestation is None:
        return "missing", {}
    if set(label_attestation) != {
            "schemaVersion", "protocolId", "protocolVersion",
            "corpusFingerprint", "labelStatus", "reviewers", "labels"}:
        raise CorpusError("semantic label attestation schema invalid")
    if (label_attestation.get("schemaVersion") != 1
            or label_attestation.get("protocolId") != COMPARISON_PROTOCOL_ID
            or label_attestation.get("protocolVersion")
            != COMPARISON_PROTOCOL_VERSION
            or label_attestation.get("corpusFingerprint")
            != mapping.get("corpusFingerprint")):
        raise CorpusError("semantic label attestation identity invalid")
    status = label_attestation.get("labelStatus")
    reviewers = label_attestation.get("reviewers")
    if (status != INDEPENDENT_LABEL_STATUS
            or not isinstance(reviewers, list) or len(reviewers) != 2):
        return "not_independent", {}
    reviewer_ids = set()
    system_ids = set()
    configuration_fingerprints = set()
    artifact_digests = set()
    for reviewer in reviewers:
        if not isinstance(reviewer, dict) or set(reviewer) != {
                "reviewerId", "systemId", "configurationFingerprint",
                "reviewArtifactDigest"}:
            return "not_independent", {}
        reviewer_id = reviewer.get("reviewerId")
        system_id = reviewer.get("systemId")
        config = reviewer.get("configurationFingerprint")
        artifact = reviewer.get("reviewArtifactDigest")
        if (not isinstance(reviewer_id, str) or not reviewer_id
                or not isinstance(system_id, str) or not system_id
                or system_id in {"verity", "butler"}
                or not isinstance(config, str)
                or not re.fullmatch(r"[0-9a-f]{64}", config)
                or not isinstance(artifact, str)
                or not re.fullmatch(r"[0-9a-f]{64}", artifact)):
            return "not_independent", {}
        reviewer_ids.add(reviewer_id)
        system_ids.add(system_id)
        configuration_fingerprints.add(config)
        artifact_digests.add(artifact)
    if min(
            len(reviewer_ids), len(system_ids),
            len(configuration_fingerprints), len(artifact_digests)) < 2:
        return "not_independent", {}
    labels = label_attestation.get("labels")
    if not isinstance(labels, list):
        raise CorpusError("semantic label rows invalid")
    expected = {
        metadata["caseId"]: metadata["payloadDigest"]
        for metadata in mapping["aliases"].values()
    }
    out = {}
    for row in labels:
        if not isinstance(row, dict) or set(row) != {
                "caseId", "payloadDigest", "assessment"}:
            raise CorpusError("semantic label row schema invalid")
        case_id = row.get("caseId")
        if (case_id not in expected or case_id in out
                or row.get("payloadDigest") != expected[case_id]
                or row.get("assessment") not in {"present", "absent"}):
            raise CorpusError("semantic label row invalid")
        out[case_id] = row["assessment"]
    if set(out) != set(expected):
        raise CorpusError("semantic labels incomplete")
    return "independent", out


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return None if denominator == 0 else round(numerator / denominator, 6)


def _metrics(observations: Dict[str, Any],
             labels: Dict[str, str]) -> Dict[str, Any]:
    rows = {
        row["itemId"]: row["runs"]
        for row in observations["observations"]
    }
    tp = fp = tn = fn = inconclusive = errors = 0
    stable = 0
    for item_id, expected in labels.items():
        runs = rows[item_id]
        stable += int(all(run == runs[0] for run in runs[1:]))
        for observed in runs:
            if observed == "error":
                errors += 1
            elif observed == "inconclusive":
                inconclusive += 1
            elif expected == "present":
                if observed == "present":
                    tp += 1
                else:
                    fn += 1
            elif observed == "present":
                fp += 1
            else:
                tn += 1
    run_count = len(labels) * observations["repetitions"]
    return {
        "caseCount": len(labels),
        "runCount": run_count,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "inconclusive": inconclusive,
        "errors": errors,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "safeFalsePositiveRate": _ratio(fp, fp + tn),
        "stabilityRate": _ratio(stable, len(labels)),
        "inconclusiveRate": _ratio(inconclusive, run_count),
        "errorRate": _ratio(errors, run_count),
    }


def compare_semantic_systems(
        *, verity_packet: Dict[str, Any], verity_mapping: Dict[str, Any],
        verity_observations: Dict[str, Any],
        butler_packet: Dict[str, Any], butler_mapping: Dict[str, Any],
        butler_observations: Dict[str, Any],
        label_attestation: Optional[Dict[str, Any]],
        butler_crosswalk: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _validate_mapping(verity_mapping, verity_packet)
    _validate_mapping(butler_mapping, butler_packet)
    validate_observations(verity_observations, verity_packet)
    validate_observations(butler_observations, butler_packet)
    if (verity_packet.get("systemId") != "verity"
            or butler_packet.get("systemId") != "butler"):
        raise CorpusError(
            "semantic comparison requires verity and butler system ids")
    if (verity_packet["corpusFingerprint"]
            != butler_packet["corpusFingerprint"]):
        raise CorpusError("semantic comparison corpora differ")

    breadth = butler_breadth_summary(butler_crosswalk)
    status, canonical_labels = _label_map(
        label_attestation, mapping=verity_mapping)
    eligibility_reasons = []
    if not breadth["claimReady"]:
        eligibility_reasons.append("butler_breadth_gaps_open")
    if status != "independent":
        eligibility_reasons.append(
            "labels_missing" if status == "missing"
            else "labels_not_independently_reviewed")
    if eligibility_reasons:
        return {
            "schemaVersion": 1,
            "protocolId": COMPARISON_PROTOCOL_ID,
            "protocolVersion": COMPARISON_PROTOCOL_VERSION,
            "status": "not_eligible",
            "reasonCodes": eligibility_reasons,
            "claim": None,
            "butlerBreadth": breadth,
        }

    verity_cases = {
        metadata["caseId"] for metadata in verity_mapping["aliases"].values()
    }
    butler_cases = {
        metadata["caseId"] for metadata in butler_mapping["aliases"].values()
    }
    if (verity_cases != butler_cases
            or verity_cases != set(canonical_labels)):
        raise CorpusError("semantic comparison canonical case sets differ")
    verity_case_metadata = _canonical_case_metadata(verity_mapping)
    butler_case_metadata = _canonical_case_metadata(butler_mapping)
    if verity_case_metadata != butler_case_metadata:
        raise CorpusError("semantic comparison canonical metadata differs")

    def remap(packet_mapping, observations):
        rows = {
            row["itemId"]: row["runs"]
            for row in observations["observations"]
        }
        canonical = {}
        for alias, metadata in packet_mapping["aliases"].items():
            canonical[metadata["caseId"]] = rows[alias]
        return {
            **observations,
            "observations": [
                {"itemId": case_id, "runs": runs}
                for case_id, runs in sorted(canonical.items())
            ],
        }

    verity_canonical = remap(verity_mapping, verity_observations)
    butler_canonical = remap(butler_mapping, butler_observations)
    verity_metrics = _metrics(verity_canonical, canonical_labels)
    butler_metrics = _metrics(butler_canonical, canonical_labels)
    risk_count = len({
        metadata["riskId"] for metadata in verity_mapping["aliases"].values()
    })
    finding_type_count = len({
        metadata["findingType"]
        for metadata in verity_mapping["aliases"].values()
    })
    thresholds = COMPARISON_THRESHOLDS
    absolute_checks = {
        "minimumCaseCount": len(canonical_labels)
        >= thresholds["minimumCaseCount"],
        "minimumRiskCount": risk_count >= thresholds["minimumRiskCount"],
        "minimumFindingTypeCount": (
            finding_type_count >= thresholds["minimumFindingTypeCount"]),
        "minimumRepetitions": (
            verity_observations["repetitions"]
            >= thresholds["minimumRepetitions"]),
        "recall": (verity_metrics["recall"] is not None
                   and verity_metrics["recall"] >= thresholds["minimumRecall"]),
        "safeFalsePositiveRate": (
            verity_metrics["safeFalsePositiveRate"] is not None
            and verity_metrics["safeFalsePositiveRate"]
            <= thresholds["maximumSafeFalsePositiveRate"]),
        "stabilityRate": (
            verity_metrics["stabilityRate"] is not None
            and verity_metrics["stabilityRate"]
            >= thresholds["minimumStabilityRate"]),
        "errorRate": (
            verity_metrics["errorRate"] is not None
            and verity_metrics["errorRate"] <= thresholds["maximumErrorRate"]),
        "inconclusiveRate": (
            verity_metrics["inconclusiveRate"] is not None
            and verity_metrics["inconclusiveRate"]
            <= thresholds["maximumInconclusiveRate"]),
    }
    relative_checks = {
        "recallNonInferior": (
            verity_metrics["recall"] is not None
            and butler_metrics["recall"] is not None
            and verity_metrics["recall"] >= butler_metrics["recall"]),
        "safeFalsePositiveRateLower": (
            verity_metrics["safeFalsePositiveRate"] is not None
            and butler_metrics["safeFalsePositiveRate"] is not None
            and verity_metrics["safeFalsePositiveRate"]
            < butler_metrics["safeFalsePositiveRate"]),
        "errorRateNonInferior": (
            verity_metrics["errorRate"] is not None
            and butler_metrics["errorRate"] is not None
            and verity_metrics["errorRate"] <= butler_metrics["errorRate"]),
    }
    failed = sorted(
        [name for name, passed in absolute_checks.items() if not passed]
        + [name for name, passed in relative_checks.items() if not passed])
    passed = not failed
    return {
        "schemaVersion": 1,
        "protocolId": COMPARISON_PROTOCOL_ID,
        "protocolVersion": COMPARISON_PROTOCOL_VERSION,
        "status": "passed" if passed else "failed",
        "reasonCodes": failed,
        "claim": (
            "verity_exceeds_butler_on_this_independently_labelled_benchmark"
            if passed else None),
        "thresholds": dict(thresholds),
        "riskCount": risk_count,
        "findingTypeCount": finding_type_count,
        "butlerBreadth": breadth,
        "verity": verity_metrics,
        "butler": butler_metrics,
        "absoluteChecks": absolute_checks,
        "relativeChecks": relative_checks,
        "limitations": [
            "benchmark_specific_not_universal",
            "independent_ai_review_not_human_expert_review",
            "no_claim_outside_measured_risks_and_configurations",
            "butler_reference_uses_targeted_checks_without_final_consolidation",
        ],
    }
