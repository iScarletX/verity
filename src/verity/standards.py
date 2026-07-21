"""Machine-checkable authoritative-source and detection-capability baseline.

This module validates repository-owned metadata. It does not run detectors,
contact sources, or make release-quality accuracy claims. Runtime execution
status and capability breadth are deliberately separate concepts.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable
from urllib.parse import urlsplit


STANDARDS_DIR = Path(__file__).resolve().parents[2] / "standards"
COVERAGE_LEVELS = ("none", "signal", "partial", "substantial", "evaluated")
DETECTION_LAYERS = ("L0_static", "L1_semantic", "V1_5_blackbox", "V2_sandbox")
SCOPES = {
    "prompt", "system_prompt", "skill", "agent_config", "mcp",
    "supply_chain", "governance",
}
CANDIDATE_DECISIONS = {
    "adopt_next", "defer_license_review", "defer_boundary_review",
    "keep_pinned_reassess",
}
SOURCE_KINDS = {
    "threat_taxonomy", "threat_model", "risk_framework", "risk_profile",
    "adversary_knowledge_base", "weakness_catalog", "attack_pattern_catalog",
    "supply_chain_specification", "supply_chain_assessment",
    "artifact_specification", "protocol_security_guidance",
    "detector_documentation", "detector_candidate_documentation",
}
DETECTOR_TYPES = {
    "deterministic_rule", "semantic_finding_type", "capability_extractor"
}


class StandardsError(ValueError):
    pass


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise StandardsError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load(name: str) -> Dict[str, Any]:
    path = STANDARDS_DIR / name
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"),
                           object_pairs_hook=_no_duplicate_keys)
    except StandardsError:
        raise
    except Exception as exc:
        raise StandardsError(f"cannot read {name}") from exc
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise StandardsError(f"unsupported {name} schema")
    return value


def load_sources() -> Dict[str, Dict[str, Any]]:
    value = _load("sources.json")
    sources = value.get("sources")
    if not isinstance(sources, list):
        raise StandardsError("sources must be a list")
    result = {}
    exact = {"sourceId", "title", "publisher", "version", "publishedDate",
             "url", "kind", "usageBasis", "controls"}
    for source in sources:
        if not isinstance(source, dict) or set(source) != exact:
            raise StandardsError("source violates strict schema")
        sid = source.get("sourceId")
        if not isinstance(sid, str) or not sid or sid in result:
            raise StandardsError("invalid or duplicate sourceId")
        for field in ("title", "publisher", "version", "publishedDate",
                      "usageBasis"):
            if not isinstance(source[field], str) or not source[field].strip():
                raise StandardsError(f"source {sid} has invalid {field}")
        if source["kind"] not in SOURCE_KINDS:
            raise StandardsError(f"source {sid} has invalid kind")
        url = urlsplit(source["url"])
        if url.scheme != "https" or not url.netloc or url.username or url.password:
            raise StandardsError(f"source {sid} must use public HTTPS")
        controls = source["controls"]
        if (not isinstance(controls, list) or not controls
                or len(set(controls)) != len(controls)
                or not all(isinstance(c, str) and c for c in controls)):
            raise StandardsError(f"source {sid} has invalid controls")
        result[sid] = source
    return result


def load_risks(sources: Dict[str, Dict[str, Any]] | None = None
               ) -> Dict[str, Dict[str, Any]]:
    sources = sources or load_sources()
    value = _load("risks.json")
    if value.get("coverageScale") != list(COVERAGE_LEVELS):
        raise StandardsError("coverage scale changed")
    risks = value.get("risks")
    if not isinstance(risks, list):
        raise StandardsError("risks must be a list")
    result = {}
    required = {"riskId", "title", "scopes", "definition", "sourceRefs",
                "layerBoundaries", "currentCoverage", "knownGaps",
                "evaluationReference"}
    for risk in risks:
        if not isinstance(risk, dict):
            raise StandardsError("risk must be an object")
        extra = set(risk) - required
        missing = required - set(risk)
        if extra - {"verityOriginalRationale"} or missing:
            raise StandardsError("risk violates strict schema")
        rid = risk.get("riskId")
        if (not isinstance(rid, str) or not rid.startswith("VR-")
                or rid in result):
            raise StandardsError("invalid or duplicate riskId")
        if not all(isinstance(risk.get(k), str) and risk[k].strip()
                   for k in ("title", "definition")):
            raise StandardsError(f"risk {rid} lacks title/definition")
        scopes = risk["scopes"]
        if (not isinstance(scopes, list) or not scopes
                or not set(scopes) <= SCOPES
                or len(scopes) != len(set(scopes))):
            raise StandardsError(f"risk {rid} has invalid scopes")
        refs = risk["sourceRefs"]
        if not isinstance(refs, list):
            raise StandardsError(f"risk {rid} sourceRefs must be a list")
        if not refs and not (isinstance(risk.get("verityOriginalRationale"), str)
                             and risk["verityOriginalRationale"].strip()):
            raise StandardsError(f"risk {rid} has no source or rationale")
        for ref in refs:
            if (not isinstance(ref, dict)
                    or set(ref) != {"sourceId", "controlIds"}
                    or ref["sourceId"] not in sources):
                raise StandardsError(f"risk {rid} has unknown source")
            controls = ref["controlIds"]
            source_controls = set(sources[ref["sourceId"]]["controls"])
            if (not isinstance(controls, list) or not controls
                    or not set(controls) <= source_controls):
                raise StandardsError(f"risk {rid} has unknown source control")
        boundaries = risk["layerBoundaries"]
        coverage = risk["currentCoverage"]
        if (not isinstance(boundaries, dict)
                or set(boundaries) != set(DETECTION_LAYERS)
                or not all(isinstance(v, str) and v.strip()
                           for v in boundaries.values())):
            raise StandardsError(f"risk {rid} has invalid layer boundaries")
        if (not isinstance(coverage, dict)
                or set(coverage) != set(DETECTION_LAYERS)
                or not all(v in COVERAGE_LEVELS for v in coverage.values())):
            raise StandardsError(f"risk {rid} has invalid current coverage")
        if any(COVERAGE_LEVELS.index(v) > COVERAGE_LEVELS.index("partial")
               for v in coverage.values()) and not risk["evaluationReference"]:
            raise StandardsError(
                f"risk {rid} exceeds partial without corpus evidence")
        if (not isinstance(risk["knownGaps"], list) or not risk["knownGaps"]
                or not all(isinstance(g, str) and g.strip()
                           for g in risk["knownGaps"])):
            raise StandardsError(f"risk {rid} must expose known gaps")
        result[rid] = risk
    return result


def load_detector_candidates(
        sources: Dict[str, Dict[str, Any]] | None = None,
        risks: Dict[str, Dict[str, Any]] | None = None
        ) -> Dict[str, Dict[str, Any]]:
    sources = sources or load_sources()
    risks = risks or load_risks(sources)
    value = _load("detector_candidates.json")
    if not isinstance(value.get("evaluatedAt"), str):
        raise StandardsError("candidate evaluation date missing")
    candidates = value.get("candidates")
    if not isinstance(candidates, list):
        raise StandardsError("detector candidates must be a list")
    exact = {"candidateId", "sourceId", "decision", "license",
             "maintenance", "structuredOutput", "targetRiskIds",
             "requiredControls", "rationale"}
    result = {}
    for item in candidates:
        if not isinstance(item, dict) or set(item) != exact:
            raise StandardsError("detector candidate violates strict schema")
        cid = item.get("candidateId")
        if not isinstance(cid, str) or not cid or cid in result:
            raise StandardsError("invalid or duplicate detector candidate")
        if item.get("sourceId") not in sources:
            raise StandardsError(f"candidate {cid} has unknown source")
        if item.get("decision") not in CANDIDATE_DECISIONS:
            raise StandardsError(f"candidate {cid} has invalid decision")
        if (not isinstance(item.get("targetRiskIds"), list)
                or not item["targetRiskIds"]
                or not set(item["targetRiskIds"]) <= set(risks)):
            raise StandardsError(f"candidate {cid} has invalid target risks")
        for key in ("license", "maintenance", "rationale"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise StandardsError(f"candidate {cid} has invalid {key}")
        for key in ("structuredOutput", "requiredControls"):
            if (not isinstance(item.get(key), list) or not item[key]
                    or not all(isinstance(x, str) and x.strip()
                               for x in item[key])):
                raise StandardsError(f"candidate {cid} has invalid {key}")
        result[cid] = item
    return result


def load_detector_mappings(
        risks: Dict[str, Dict[str, Any]] | None = None
        ) -> Dict[tuple[str, str], Dict[str, Any]]:
    risks = risks or load_risks()
    value = _load("detector_mappings.json")
    detectors = value.get("detectors")
    if not isinstance(detectors, list):
        raise StandardsError("detectors must be a list")
    result = {}
    exact = {"detectorType", "detectorId", "riskIds", "contribution"}
    for detector in detectors:
        if not isinstance(detector, dict) or set(detector) != exact:
            raise StandardsError("detector mapping violates strict schema")
        dtype = detector["detectorType"]
        did = detector["detectorId"]
        key = (dtype, did)
        if (dtype not in DETECTOR_TYPES or not isinstance(did, str) or not did
                or key in result):
            raise StandardsError("invalid or duplicate detector mapping")
        risk_ids = detector["riskIds"]
        if (not isinstance(risk_ids, list) or not risk_ids
                or len(risk_ids) != len(set(risk_ids))
                or not set(risk_ids) <= set(risks)):
            raise StandardsError(f"detector {did} has unknown risks")
        if detector["contribution"] not in {"signal", "partial"}:
            raise StandardsError(f"detector {did} has invalid contribution")
        layer = ("L1_semantic" if dtype == "semantic_finding_type"
                 else "L0_static")
        contradictory = [
            rid for rid in risk_ids
            if risks[rid]["currentCoverage"][layer] == "none"
        ]
        if contradictory:
            raise StandardsError(
                f"detector {did} maps to {layer}=none risks: {contradictory}")
        result[key] = detector
    return result


def validate_runtime_detector_coverage() -> None:
    """Fail if runtime detector registries drift from the standards map."""
    from .builtins import (build_finding_type_registry,
                           build_prompt_rule_registry,
                           build_skill_rule_registry)
    from .semantic.catalog import CATALOG

    mappings = load_detector_mappings()
    finding_types = build_finding_type_registry()
    rule_ids = {
        r.ruleId
        for registry in (build_prompt_rule_registry(finding_types),
                         build_skill_rule_registry(finding_types))
        for r in registry.all()
    }
    mapped_rules = {
        did for dtype, did in mappings if dtype == "deterministic_rule"
    }
    semantic_ids = set(CATALOG)
    mapped_semantic = {
        did for dtype, did in mappings if dtype == "semantic_finding_type"
    }
    capability_ids = {"skill.capability_facts.v1"}
    mapped_capabilities = {
        did for dtype, did in mappings if dtype == "capability_extractor"
    }
    if rule_ids != mapped_rules:
        missing = sorted(rule_ids - mapped_rules)
        stale = sorted(mapped_rules - rule_ids)
        raise StandardsError(
            f"deterministic mapping drift: missing={missing} stale={stale}")
    if semantic_ids != mapped_semantic:
        missing = sorted(semantic_ids - mapped_semantic)
        stale = sorted(mapped_semantic - semantic_ids)
        raise StandardsError(
            f"semantic mapping drift: missing={missing} stale={stale}")
    if capability_ids != mapped_capabilities:
        missing = sorted(capability_ids - mapped_capabilities)
        stale = sorted(mapped_capabilities - capability_ids)
        raise StandardsError(
            f"capability mapping drift: missing={missing} stale={stale}")


def summarize_coverage() -> Dict[str, Any]:
    """Public-safe aggregate for documentation/UI work in later rounds."""
    risks = load_risks()
    totals = {layer: {level: 0 for level in COVERAGE_LEVELS}
              for layer in DETECTION_LAYERS}
    for risk in risks.values():
        for layer, level in risk["currentCoverage"].items():
            totals[layer][level] += 1
    return {"riskCount": len(risks), "byLayer": totals}
