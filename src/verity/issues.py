"""Read-only projection combining static, semantic, and runtime evidence."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .scoring import (
    SEVERITY_ORDER,
    _agent_runtime_score_state,
    mapped_finding_rows,
)
from .standards import load_detector_mappings, load_risks


_LAYER_ORDER = {
    "L0_static": 0,
    "L1_semantic": 1,
    "V1_5_blackbox": 2,
    "V2_sandbox": 3,
    "V2_agent_runtime": 4,
}

_RUNTIME_SOURCE_LAYERS = frozenset({
    "V1_5_blackbox",
    "V2_sandbox",
    "V2_agent_runtime",
})
_ALL_SOURCE_LAYERS = frozenset(_LAYER_ORDER)
_CONTROLLED_SEVERITIES = frozenset({"low", "medium", "high", "critical"})


def controlled_runtime_occurrence_projection(
        report: Dict[str, Any]) -> Tuple[List[Tuple[str, str, str]], bool]:
    """Validate the issue projection and return deduplicated runtime rows.

    The boolean is a schema-integrity signal for security gates. A requested
    dynamic stage must not silently pass when its only verdict input is
    malformed. Duplicate risk mappings for the same logical runtime finding
    collapse by ``(sourceLayer, findingId)`` so counts describe observations,
    not the number of taxonomy rows to which one observation maps.
    """
    issues = report.get("issues")
    if not isinstance(issues, list):
        return [], False
    unique: Dict[Tuple[str, str], str] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            return [], False
        occurrences = issue.get("occurrences")
        if not isinstance(occurrences, list):
            return [], False
        for occurrence in occurrences:
            if not isinstance(occurrence, dict):
                return [], False
            layer = occurrence.get("sourceLayer")
            severity = occurrence.get("severity")
            finding_id = occurrence.get("findingId")
            if (
                not isinstance(layer, str)
                or layer not in _ALL_SOURCE_LAYERS
                or not isinstance(severity, str)
                or severity not in _CONTROLLED_SEVERITIES
                or not isinstance(finding_id, str)
                or not finding_id
            ):
                return [], False
            if layer not in _RUNTIME_SOURCE_LAYERS:
                continue
            identity = (layer, finding_id)
            previous = unique.get(identity)
            if previous is not None and previous != severity:
                return [], False
            unique[identity] = severity
    return [
        (layer, finding_id, severity)
        for (layer, finding_id), severity in unique.items()
    ], True


def controlled_runtime_occurrence_severities(
        report: Dict[str, Any]) -> List[str]:
    """Return only controlled severities from projected runtime evidence.

    Runtime observations intentionally are location-free unified-issue
    occurrences rather than source-anchored Findings.  Final verdict, CLI,
    HTML, and Web consumers must nevertheless count all three runtime layers
    consistently.  Reject malformed containers, unknown layers, and unknown
    severities rather than letting arbitrary report data control a verdict.
    """
    rows, valid = controlled_runtime_occurrence_projection(report)
    if not valid:
        return []
    return [severity for _, _, severity in rows]


def _blackbox_outcome(scenario_result: Dict[str, Any]) -> str:
    oracle = scenario_result.get("oracle_result") or {}
    if oracle.get("outcome") in {
        "passed", "failed", "insufficient_evidence", "unavailable"
    }:
        return oracle["outcome"]
    probes = scenario_result.get("probe_results") or []
    if not probes or all(item.get("safe") is None for item in probes):
        return "insufficient_evidence"
    if any(item.get("safe") is False for item in probes):
        return "failed"
    if any(item.get("safe") is None for item in probes):
        return "insufficient_evidence"
    return "passed"


def _plan_risks(report: Dict[str, Any]) -> Dict[str, Tuple[str, ...]]:
    result = {}
    for item in (report.get("dynamicPlan") or {}).get("items") or []:
        check_id = item.get("check_id")
        if check_id:
            result[str(check_id)] = tuple(item.get("risk_ids") or ())
        scenario_id = item.get("scenario_id")
        if scenario_id:
            result[str(scenario_id)] = tuple(item.get("risk_ids") or ())
    return result


def project_unified_issues(report: Dict[str, Any]) -> List[Dict[str, Any]]:
    risks = load_risks()
    mappings = load_detector_mappings()
    rows, _ = mapped_finding_rows(report)
    evidence_by_id = {
        item.get("evidenceId"): item
        for item in report.get("evidences") or []
        if item.get("evidenceId")
    }
    semantic = report.get("semantic") or {}
    for item in semantic.get("evidences") or []:
        if item.get("evidenceId"):
            evidence_by_id.setdefault(item["evidenceId"], item)

    grouped: Dict[str, Dict[str, Any]] = {}

    def group(risk_id: str) -> Dict[str, Any]:
        if risk_id not in grouped:
            catalog = risks.get(risk_id) or {}
            grouped[risk_id] = {
                "issueId": "issue:" + risk_id,
                "riskId": risk_id,
                "title": catalog.get("title") or risk_id,
                "status": "unverified",
                "severity": "low",
                "sourceLayers": [],
                "detectorIds": [],
                "evidenceRefs": [],
                "occurrenceIds": [],
                "remediationIds": [],
                "occurrences": [],
                "evidence": [],
                "runtimeChecks": [],
            }
        return grouped[risk_id]

    runtime_failed: Dict[str, bool] = {}
    nonruntime_present: Dict[str, bool] = {}

    for row in rows:
        finding = row["finding"]
        layer = row["layer"]
        finding_id = str(finding.get("findingId") or "unknown")
        severity = str(finding.get("severity") or "medium")
        for risk_id in row["riskIds"]:
            issue = group(risk_id)
            occurrence_id = f"{layer}:{finding_id}:{risk_id}"
            occurrence = {
                "occurrenceId": occurrence_id,
                "sourceLayer": layer,
                "detectorIds": list(row["detectorIds"]),
                "findingId": finding_id,
                "severity": severity,
            }
            evidence_ids = list(finding.get("evidenceIds") or [])
            if evidence_ids:
                occurrence["evidenceRefs"] = evidence_ids
            issue["occurrences"].append(occurrence)
            issue["occurrenceIds"].append(occurrence_id)
            issue["sourceLayers"].append(layer)
            issue["detectorIds"].extend(row["detectorIds"])
            if SEVERITY_ORDER.get(severity, 99) < SEVERITY_ORDER.get(
                    issue["severity"], 99):
                issue["severity"] = severity
            if layer in {
                "V1_5_blackbox", "V2_sandbox", "V2_agent_runtime"
            }:
                runtime_failed[risk_id] = True
                evidence_id = f"runtime:{layer}:{row['detectorIds'][0]}"
                issue["evidence"].append({
                    "evidenceId": evidence_id,
                    "kind": "runtime_trace",
                    "sourceLayer": layer,
                    "detectorId": row["detectorIds"][0],
                    "outcome": (
                        "observed_attempt"
                        if layer == "V2_agent_runtime"
                        else "failed"
                    ),
                })
                issue["evidenceRefs"].append(evidence_id)
            else:
                nonruntime_present[risk_id] = True
                for evidence_id in evidence_ids:
                    evidence = evidence_by_id.get(evidence_id)
                    if evidence is not None:
                        issue["evidence"].append(evidence)
                    issue["evidenceRefs"].append(evidence_id)

    plan_risks = _plan_risks(report)
    runtime_checks: Dict[str, List[Dict[str, Any]]] = {}

    blackbox = report.get("promptBlackbox") or {}
    blackbox_status = blackbox.get("status")
    if blackbox_status in {"completed", "failed"}:
        for result in blackbox.get("scenarioResults") or []:
            if not isinstance(result, dict):
                continue
            if (
                blackbox_status == "failed"
                and result.get("definitive") is not True
            ):
                continue
            detector_id = str(result.get("scenario_id") or "")
            outcome = _blackbox_outcome(result)
            risk_ids = plan_risks.get(detector_id)
            if not risk_ids:
                mapping = mappings.get(("blackbox_scenario", detector_id))
                risk_ids = tuple(mapping["riskIds"]) if mapping else ()
            for risk_id in risk_ids:
                runtime_checks.setdefault(risk_id, []).append({
                    "detectorId": detector_id,
                    "sourceLayer": "V1_5_blackbox",
                    "outcome": outcome,
                })
                if outcome != "failed":
                    continue
                issue = group(risk_id)
                already_present = any(
                    occurrence["sourceLayer"] == "V1_5_blackbox"
                    and detector_id in occurrence["detectorIds"]
                    for occurrence in issue["occurrences"]
                )
                if already_present:
                    continue
                severity = str(result.get("severity") or "medium")
                occurrence_id = f"V1_5_blackbox:blackbox:{detector_id}:{risk_id}"
                issue["occurrences"].append({
                    "occurrenceId": occurrence_id,
                    "sourceLayer": "V1_5_blackbox",
                    "detectorIds": [detector_id],
                    "findingId": "blackbox:" + detector_id,
                    "severity": severity,
                })
                issue["occurrenceIds"].append(occurrence_id)
                issue["sourceLayers"].append("V1_5_blackbox")
                issue["detectorIds"].append(detector_id)
                evidence_id = f"runtime:V1_5_blackbox:{detector_id}"
                issue["evidence"].append({
                    "evidenceId": evidence_id,
                    "kind": "runtime_trace",
                    "sourceLayer": "V1_5_blackbox",
                    "detectorId": detector_id,
                    "outcome": "failed",
                })
                issue["evidenceRefs"].append(evidence_id)
                runtime_failed[risk_id] = True
                if SEVERITY_ORDER.get(severity, 99) < SEVERITY_ORDER.get(
                        issue["severity"], 99):
                    issue["severity"] = severity

    sandbox = report.get("skillSandbox") or {}
    failed_sandbox_detectors = {
        detector_id
        for row in rows if row["layer"] == "V2_sandbox"
        for detector_id in row["detectorIds"]
    }
    if sandbox.get("status") == "completed":
        for item in (report.get("dynamicPlan") or {}).get("items") or []:
            if item.get("stage") != "skill_sandbox" or item.get("status") != "selected":
                continue
            detector_id = str(item.get("check_id"))
            outcome = "failed" if detector_id in failed_sandbox_detectors else "passed"
            for risk_id in item.get("risk_ids") or []:
                runtime_checks.setdefault(risk_id, []).append({
                    "detectorId": detector_id,
                    "sourceLayer": "V2_sandbox",
                    "outcome": outcome,
                })

    if _agent_runtime_score_state(report) == "completed":
        for item in (report.get("dynamicPlan") or {}).get("items") or []:
            if (
                type(item) is not dict
                or item.get("check_id") != "agent_instruction.runtime"
                or item.get("stage") != "agent_runtime"
                or item.get("status") != "selected"
            ):
                continue
            for risk_id in item.get("risk_ids") or []:
                runtime_checks.setdefault(risk_id, []).append({
                    "detectorId": "agent_instruction.runtime",
                    "sourceLayer": "V2_agent_runtime",
                    "outcome": "insufficient_evidence",
                })

    for risk_id, issue in grouped.items():
        checks = runtime_checks.get(risk_id, [])
        issue["runtimeChecks"] = checks
        has_nonruntime = nonruntime_present.get(risk_id, False)
        has_runtime_failure = runtime_failed.get(risk_id, False)
        # A single passing probe must not label the whole risk as
        # ``not_reproduced`` while another applicable peer probe is
        # inconclusive.  That status is reserved for a bounded run in which
        # every selected runtime check attached to this risk produced a pass.
        all_runtime_checks_passed = bool(checks) and all(
            item["outcome"] == "passed" for item in checks
        )
        if has_runtime_failure and has_nonruntime:
            issue["status"] = "runtime_confirmed"
        elif has_runtime_failure:
            issue["status"] = "runtime_only"
        elif has_nonruntime and all_runtime_checks_passed:
            issue["status"] = "not_reproduced"
        elif has_nonruntime:
            issue["status"] = "static_only"
        else:
            issue["status"] = "unverified"
        issue["sourceLayers"] = sorted(
            set(issue["sourceLayers"]), key=lambda value: _LAYER_ORDER.get(value, 99))
        issue["detectorIds"] = sorted(set(issue["detectorIds"]))
        issue["evidenceRefs"] = sorted(set(issue["evidenceRefs"]))
        issue["occurrenceIds"] = sorted(set(issue["occurrenceIds"]))
        issue["remediationIds"] = sorted({
            item.get("remediationId")
            for item in report.get("remediations") or []
            if risk_id in (item.get("riskIds") or []) and item.get("remediationId")
        })

    return sorted(grouped.values(), key=lambda issue: (
        SEVERITY_ORDER.get(issue["severity"], 99), issue["riskId"]
    ))
