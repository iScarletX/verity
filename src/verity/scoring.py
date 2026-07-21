"""Deterministic explainable safety score and remediation projection.

Scoring is a policy projection, never a detector and never model-authored.  It
uses only completed-review Findings plus the independently validated detector
mapping.  Coverage failure or an unmapped Finding makes the numeric score
unavailable rather than optimistic.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List, Tuple

from .guidance import lookup as guidance_lookup
from .standards import load_detector_mappings, summarize_coverage


POLICY_ID = "verity-safety-score"
POLICY_VERSION = "1.0.0"
CONFIDENCE_POLICY_ID = "verity-review-confidence"
CONFIDENCE_POLICY_VERSION = "1.0.0"

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEVERITY_WEIGHTS = {"critical": 45, "high": 25, "medium": 10, "low": 3}
SEVERITY_CAPS = {"critical": 39, "high": 59, "medium": 79, "low": 99}
# Integer percentages.  The fourth and later duplicate root causes do not
# deduct further points, but remain visible remediation items.
DUPLICATE_FACTORS = (100, 50, 25)


def _unavailable(reason: str) -> Dict[str, Any]:
    return {
        "status": "unavailable", "value": None,
        "policyId": POLICY_ID, "policyVersion": POLICY_VERSION,
        "reasonCodes": [reason], "highestSeverity": None,
        "baseScore": 100, "deductionTotal": None, "severityCap": None,
        "deductions": [], "includedLayers": [], "evaluatedLayers": [],
    }


def _mapped_findings(review: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    mappings = load_detector_mappings()
    event_to_rule = {e.get("eventId"): e.get("ruleId")
                     for e in review.get("ruleMatches") or []}
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    for finding in review.get("findings") or []:
        event_ids = (finding.get("origin") or {}).get("ruleMatchEventIds") or []
        risk_ids = set()
        rule_ids = set()
        for event_id in event_ids:
            rule_id = event_to_rule.get(event_id)
            if rule_id:
                rule_ids.add(rule_id)
                mapping = mappings.get(("deterministic_rule", rule_id))
                if mapping:
                    risk_ids.update(mapping["riskIds"])
        if not risk_ids:
            errors.append("unmapped_finding:" + str(finding.get("findingId", "")))
            continue
        rows.append({"finding": finding, "riskIds": sorted(risk_ids),
                     "detectorIds": sorted(rule_ids), "layer": "L0_static"})

    semantic = review.get("semantic") or {}
    if semantic.get("status") == "completed":
        for finding in semantic.get("findings") or []:
            finding_type = finding.get("findingType")
            mapping = mappings.get(("semantic_finding_type", finding_type))
            if not mapping:
                errors.append("unmapped_semantic_finding:" + str(
                    finding.get("findingId", "")))
                continue
            rows.append({"finding": finding,
                         "riskIds": sorted(mapping["riskIds"]),
                         "detectorIds": [finding_type],
                         "layer": "L1_semantic"})
    return rows, errors


def _ceil_percent(value: int, percent: int) -> int:
    return (value * percent + 99) // 100


def compute_score(review: Dict[str, Any]) -> Dict[str, Any]:
    coverage = review.get("coverage") or {}
    if coverage.get("status") != "sufficient":
        return _unavailable("coverage_insufficient")
    rows, errors = _mapped_findings(review)
    if errors:
        result = _unavailable("finding_mapping_incomplete")
        result["reasonCodes"] = ["finding_mapping_incomplete", *sorted(errors)]
        return result

    rows.sort(key=lambda row: (
        SEVERITY_ORDER.get(row["finding"].get("severity"), 99),
        row["riskIds"][0], str(row["finding"].get("findingId", ""))))
    root_counts: Dict[Tuple[str, str], int] = defaultdict(int)
    deductions = []
    total = 0
    included_layers = set()
    severities = []
    for row in rows:
        finding = row["finding"]
        severity = finding.get("severity")
        if severity not in SEVERITY_WEIGHTS:
            return _unavailable("invalid_finding_severity")
        # One finding can support several risk mappings but is deducted once.
        # The lexicographically first stable unified risk id is the arithmetic
        # root; every mapped risk remains visible in the explanation.
        root = row["riskIds"][0]
        stable_subject = str(finding.get("subjectKey") or finding.get("findingId") or "")
        root_key = (root, stable_subject)
        occurrence = root_counts[root_key]
        root_counts[root_key] += 1
        factor = DUPLICATE_FACTORS[occurrence] if occurrence < len(
            DUPLICATE_FACTORS) else 0
        points = _ceil_percent(SEVERITY_WEIGHTS[severity], factor) if factor else 0
        total += points
        included_layers.add(row["layer"])
        severities.append(severity)
        deductions.append({
            "findingId": finding.get("findingId"),
            "findingType": finding.get("findingType"),
            "severity": severity, "riskIds": row["riskIds"],
            "primaryRiskId": root, "detectorIds": row["detectorIds"],
            "sourceLayer": row["layer"], "baseWeight": SEVERITY_WEIGHTS[severity],
            "duplicateIndex": occurrence, "factorPercent": factor,
            "points": points,
        })
    highest = min(severities, key=lambda x: SEVERITY_ORDER[x]) if severities else None
    cap = SEVERITY_CAPS.get(highest) if highest else 100
    before_cap = max(0, 100 - min(total, 100))
    value = min(before_cap, cap)
    semantic_status = (review.get("semantic") or {}).get("status")
    evaluated_layers = ["L0_static"]
    if semantic_status == "completed":
        evaluated_layers.append("L1_semantic")
    return {
        "status": "available", "value": value,
        "policyId": POLICY_ID, "policyVersion": POLICY_VERSION,
        "reasonCodes": [], "highestSeverity": highest,
        "baseScore": 100, "deductionTotal": total,
        "scoreBeforeSeverityCap": before_cap, "severityCap": cap,
        "deductions": deductions,
        "includedLayers": sorted(included_layers),
        "evaluatedLayers": evaluated_layers,
    }


def compute_confidence(review: Dict[str, Any]) -> Dict[str, Any]:
    limitations = []
    coverage = (review.get("coverage") or {}).get("status")
    capabilities = review.get("capabilities") or {}
    semantic_status = (capabilities.get("semantic") or {}).get(
        "status", "not_enabled")
    static_status = (capabilities.get("static") or {}).get("status", "failed")
    if coverage != "sufficient" or static_status == "failed":
        grade = "D"
        limitations.append("deterministic_coverage_incomplete")
    elif semantic_status == "completed":
        grade = "B"
    elif semantic_status == "failed":
        grade = "D"
        limitations.append("semantic_requested_but_failed")
    else:
        grade = "C"
        limitations.append("semantic_not_enabled")
    if review.get("engine") == "skill":
        gitleaks = (((review.get("artifactModel") or {}).get("gitleaksRun")
                     or {}).get("status"))
        if gitleaks not in {None, "completed"}:
            if "secret_scan_incomplete" not in limitations:
                limitations.append("secret_scan_incomplete")
            if grade in {"A", "B"}:
                grade = "C"
    limitations.extend(["v1_5_blackbox_not_implemented",
                        "v2_sandbox_not_implemented",
                        "capability_breadth_not_evaluated"])
    breadth = summarize_coverage()
    return {
        "grade": grade,
        "policyId": CONFIDENCE_POLICY_ID,
        "policyVersion": CONFIDENCE_POLICY_VERSION,
        "limitations": limitations,
        "execution": {"static": static_status, "semantic": semantic_status},
        "breadthSummary": breadth,
        "note": ("Grade describes review scope and known capability limits; "
                 "it is separate from the safety score and is not a guarantee."),
    }


def build_remediations(review: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows, errors = _mapped_findings(review)
    if errors:
        return []
    ev_ids = {e.get("evidenceId") for e in review.get("evidences") or []}
    semantic_ev_ids = {e.get("evidenceId")
                       for e in (review.get("semantic") or {}).get("evidences") or []}
    result = []
    for row in rows:
        finding = row["finding"]
        guidance = guidance_lookup(finding)
        evidence = [eid for eid in finding.get("evidenceIds") or []
                    if eid in ev_ids or eid in semantic_ev_ids]
        checks = [
            {"code": "finding_absent_after_rerun",
             "label": "使用相同审查范围复查后，该问题不再出现。"},
            {"code": "no_new_high_or_critical",
             "label": "复查没有新增 High 或 Critical 问题。"},
            {"code": "coverage_not_reduced",
             "label": "复查 Coverage 不低于本次，相关检查均成功完成。"},
        ]
        if row["layer"] == "L1_semantic":
            checks.append({
                "code": "same_semantic_configuration",
                "label": "使用同一语义模型、契约和出境策略复查，避免不可比。"})
        result.append({
            "remediationId": "rem-" + str(finding.get("findingId", "unknown")),
            "findingId": finding.get("findingId"),
            "findingType": finding.get("findingType"),
            "severity": finding.get("severity"),
            "riskIds": row["riskIds"], "sourceLayer": row["layer"],
            "priority": guidance.get("priority", "P1"),
            "title": guidance.get("plainTitle", "需要人工复核"),
            "actions": list(guidance.get("whatToDo") or []),
            "evidenceIds": evidence,
            "verificationChecks": checks,
            "applyMode": "proposal_only",
        })
    result.sort(key=lambda item: (
        {"P0": 0, "P1": 1, "P2": 2}.get(item["priority"], 9),
        SEVERITY_ORDER.get(item["severity"], 9), item["remediationId"]))
    return result


def enrich_review(review: Dict[str, Any]) -> Dict[str, Any]:
    """Mutate and return a report projection after capabilities are present."""
    review["score"] = compute_score(review)
    review["reviewConfidence"] = compute_confidence(review)
    review["remediations"] = build_remediations(review)
    return review
