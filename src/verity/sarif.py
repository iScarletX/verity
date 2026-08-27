"""SARIF 2.1.0 report exporter.

We emit the smallest, spec-compliant SARIF that lets Verity findings be
consumed by tools like GitHub Code Scanning (integration NOT wired up
yet — see README). The exporter is deliberately conservative:

- Level mapping (spec §16 severity vs SARIF ``level``):
    low     -> note
    medium  -> warning
    high    -> error
    critical-> error
- Locations use ``artifactLocation.uri`` + ``region`` with
  ``byteOffset`` / ``byteLength``. We do NOT compute line/column
  because our internal identity is byte-anchored and lying about lines
  would break the fingerprint contract (spec §4).
- Dual-evidence findings put the first evidence in ``locations`` and the
  rest in ``relatedLocations``.
- ``partialFingerprints`` include the deterministic
  ``findingOccurrenceFingerprint`` so the SARIF file is stable across
  runs (spec §5.1).
- Secret / sensitive evidence never leaks: the raw bytes are never
  copied here; we only publish redacted previews and byte ranges.
- Coverage status and other Verity-specific fields are exposed as flat,
  namespaced properties on the run: ``run.properties["verity.coverage"]``,
  ``run.properties["verity.reviewId"]``, ``run.properties["verity.engine"]``,
  ``run.properties["verity.snapshotId"]``,
  ``run.properties["verity.verdict.subject"]``,
  ``run.properties["verity.verdict.reasonCodes"]``,
  ``run.properties["verity.owaspCoverage"]``.
  Flat namespaced keys are valid SARIF ``properties`` bag members
  (SARIF 2.1.0 §3.8) and let CI tooling filter without walking a nested
  structure. Do NOT expect a nested ``run.properties.coverage`` object;
  the value lives under the exact key ``verity.coverage``.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List

from . import __version__ as _VERITY_VERSION


SARIF_VERSION = "2.1.0"
SARIF_SCHEMA_URI = (
    "https://schemastore.azurewebsites.net/schemas/json/sarif-2.1.0-rtm.5.json"
)


_LEVEL_MAP = {
    "low": "note",
    "medium": "warning",
    "high": "error",
    "critical": "error",
}

_AGENT_RUNTIME_MESSAGES = {
    "agent_runtime_sensitive_read_attempt": (
        "Agent runtime observed a synthetic in-memory sensitive read attempt."
    ),
    "agent_runtime_network_attempt": (
        "Agent runtime observed a blocked simulated HTTP attempt."
    ),
    "agent_runtime_shell_attempt": (
        "Agent runtime observed a blocked simulated shell attempt."
    ),
    "agent_runtime_canary_exfiltration_attempt": (
        "Agent runtime observed a fake credential marker in blocked simulated "
        "HTTP arguments."
    ),
}


def _rule_descriptors(review_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    """One SARIF rule per distinct (ruleId, ruleVersion) actually referenced."""
    seen = {}
    for f in review_dict["findings"]:
        # We use findingType as the SARIF ruleId so consumers see a stable
        # semantic id even when several internal rules map to one type.
        rid = f["findingType"]
        if rid in seen:
            continue
        seen[rid] = {
            "id": rid,
            "name": rid,
            "shortDescription": {"text": rid},
            "fullDescription": {"text": f.get("claim", rid)},
            "defaultConfiguration": {"level": _LEVEL_MAP.get(f["severity"], "warning")},
            "properties": {
                "security-severity": _security_severity(f["severity"]),
                "tags": [f"engine:{review_dict.get('engine', 'unknown')}"],
            },
        }
    return list(seen.values())


def _security_severity(sev: str) -> str:
    # Rough numeric mapping (GitHub Code Scanning convention).
    return {"low": "3.0", "medium": "5.5",
            "high": "7.5", "critical": "9.5"}.get(sev, "5.0")


def _agent_runtime_occurrences(review_dict: Dict[str, Any]) -> List[Dict[str, str]]:
    """Allowlisted issue occurrences for the source-less runtime SARIF path."""
    result = []
    for issue in review_dict.get("issues") or []:
        if type(issue) is not dict:
            continue
        risk_id = issue.get("riskId")
        issue_id = issue.get("issueId")
        issue_status = issue.get("status")
        if not all(type(value) is str and value for value in (
            risk_id, issue_id, issue_status
        )):
            continue
        for occurrence in issue.get("occurrences") or []:
            if (
                type(occurrence) is not dict
                or occurrence.get("sourceLayer") != "V2_agent_runtime"
            ):
                continue
            severity = occurrence.get("severity")
            if severity not in _LEVEL_MAP:
                severity = "medium"
            detector_ids = occurrence.get("detectorIds")
            if type(detector_ids) is not list:
                continue
            for detector_id in detector_ids:
                if detector_id not in _AGENT_RUNTIME_MESSAGES:
                    continue
                result.append({
                    "detectorId": detector_id,
                    "riskId": risk_id,
                    "issueId": issue_id,
                    "issueStatus": issue_status,
                    "severity": severity,
                    "sourceLayer": "V2_agent_runtime",
                })
    result.sort(key=lambda item: (
        item["detectorId"], item["riskId"], item["issueId"]
    ))
    return result


def _agent_runtime_fingerprint(
    review_dict: Dict[str, Any], detector_id: str, risk_id: str
) -> str:
    runtime = review_dict.get("agentInstructionRuntime")
    if type(runtime) is not dict:
        runtime = {}
    scenario_ids = sorted({
        scenario.get("scenario_id")
        for scenario in runtime.get("scenarioResults") or []
        if type(scenario) is dict
        and type(scenario.get("scenario_id")) is str
    })
    stable_inputs = {
        "contentRootDigest": (
            (review_dict.get("snapshot") or {}).get("contentRootDigest")
        ),
        "detectorId": detector_id,
        "riskId": risk_id,
        "harnessSha256": runtime.get("harnessSha256"),
        "scenarioIds": scenario_ids,
    }
    payload = json.dumps(
        stable_inputs,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _agent_runtime_rule_descriptors(
    occurrences: List[Dict[str, str]],
) -> List[Dict[str, Any]]:
    seen = {}
    for occurrence in occurrences:
        detector_id = occurrence["detectorId"]
        if detector_id in seen:
            continue
        severity = occurrence["severity"]
        message = _AGENT_RUNTIME_MESSAGES[detector_id]
        seen[detector_id] = {
            "id": detector_id,
            "name": detector_id,
            "shortDescription": {"text": message},
            "fullDescription": {"text": message},
            "defaultConfiguration": {
                "level": _LEVEL_MAP.get(severity, "warning")
            },
            "properties": {
                "security-severity": _security_severity(severity),
                "tags": ["engine:skill", "layer:V2_agent_runtime"],
            },
        }
    return list(seen.values())


def _agent_runtime_result(
    review_dict: Dict[str, Any], occurrence: Dict[str, str]
) -> Dict[str, Any]:
    detector_id = occurrence["detectorId"]
    return {
        "ruleId": detector_id,
        "level": _LEVEL_MAP.get(occurrence["severity"], "warning"),
        "message": {"text": _AGENT_RUNTIME_MESSAGES[detector_id]},
        "partialFingerprints": {
            "verityAgentRuntimeOccurrence/v1": _agent_runtime_fingerprint(
                review_dict, detector_id, occurrence["riskId"]
            ),
        },
        "properties": {
            "verity.severity": occurrence["severity"],
            "verity.riskId": occurrence["riskId"],
            "verity.issueId": occurrence["issueId"],
            "verity.detectorId": detector_id,
            "verity.sourceLayer": occurrence["sourceLayer"],
            "verity.issueStatus": occurrence["issueStatus"],
        },
    }


def _uri(loc: Dict[str, Any]) -> str:
    # We rely on intake's normalisation: paths are already relative and
    # POSIX-style. Reject anything absolute; SARIF requires relative URIs.
    p = loc.get("artifactPath") or ""
    if p.startswith("/"):
        # Should never happen (intake rejects absolute paths), but be
        # defensive rather than leak host filesystem info.
        p = p.lstrip("/")
    return p


def _sarif_location(ev_locations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for loc in ev_locations:
        rng = loc.get("sourceByteRange") or {}
        start = int(rng.get("start", 0))
        end = int(rng.get("end", start))
        out.append({
            "physicalLocation": {
                "artifactLocation": {"uri": _uri(loc)},
                "region": {
                    "byteOffset": start,
                    "byteLength": max(0, end - start),
                },
            }
        })
    return out


def _finding_to_result(f: Dict[str, Any],
                       ev_by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    from .findings_view import source_layer
    from .guidance import lookup as _guidance_lookup
    ev_ids = f.get("evidenceIds", [])
    all_locations: List[Dict[str, Any]] = []
    for eid in ev_ids:
        ev = ev_by_id.get(eid)
        if not ev:
            continue
        all_locations.extend(_sarif_location(ev.get("locations", [])))

    primary = all_locations[:1] or [{"physicalLocation": {"artifactLocation": {"uri": "unknown"}}}]
    related = all_locations[1:]

    origin_kind = (f.get("origin") or {}).get("kind", "")
    g = _guidance_lookup(f)
    result: Dict[str, Any] = {
        "ruleId": f["findingType"],
        "level": _LEVEL_MAP.get(f["severity"], "warning"),
        "message": {"text": f.get("claim", "")},
        "locations": primary,
        "partialFingerprints": {
            "verityFindingOccurrence/v1": f["findingOccurrenceFingerprint"],
        },
        "properties": {
            "verity.origin": origin_kind,
            "verity.subjectKey": f["subjectKey"],
            "verity.subject": f.get("subject"),
            "verity.severity": f["severity"],
            "verity.sourceLayer": source_layer(f),
            "verity.guidance.id": g.get("id"),
            "verity.guidance.priority": g.get("priority"),
            # Full text kept out of identity by design; it's only
            # ancillary display metadata here.
            "verity.guidance.plainTitle": g.get("plainTitle"),
        },
    }
    if related:
        result["relatedLocations"] = related
    return result


def review_to_sarif(review_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Convert the JSON view produced by ``report.review_to_dict``."""
    from .findings_view import completed_findings
    all_findings, ev_by_id = completed_findings(review_dict)
    sarif_review = dict(review_dict)
    sarif_review["findings"] = all_findings
    runtime_occurrences = _agent_runtime_occurrences(review_dict)
    rules = _rule_descriptors(sarif_review)
    existing_rule_ids = {rule["id"] for rule in rules}
    rules.extend(
        rule
        for rule in _agent_runtime_rule_descriptors(runtime_occurrences)
        if rule["id"] not in existing_rule_ids
    )

    tool_driver = {
        "name": "verity",
        "version": _VERITY_VERSION,
        "informationUri": "https://verity.dev/",
        "rules": rules,
    }

    # Adjunct tools (parsers / analyzers) go under ``run.tool.extensions``
    # so downstream consumers know which secondary tools contributed.
    extensions: List[Dict[str, Any]] = []
    am = review_dict.get("artifactModel") or {}
    br = am.get("banditRun") or {}
    if br.get("toolVersion") and br.get("status") == "completed":
        extensions.append({
            "name": "bandit",
            "version": br.get("toolVersion"),
            "informationUri": "https://bandit.readthedocs.io/",
        })
    gr = am.get("gitleaksRun") or {}
    if gr.get("toolVersion") and gr.get("status") == "completed":
        extensions.append({
            "name": "gitleaks",
            "version": gr.get("toolVersion"),
            "informationUri": "https://github.com/gitleaks/gitleaks",
        })

    coverage = review_dict.get("coverage", {}).get("status", "unknown")
    verdict = review_dict.get("verdict", {})
    subject = verdict.get("subject")  # may be None on insufficient coverage

    results = [_finding_to_result(f, ev_by_id) for f in all_findings]
    results.extend(
        _agent_runtime_result(review_dict, occurrence)
        for occurrence in runtime_occurrences
    )

    run = {
        "tool": {"driver": tool_driver},
        "results": results,
        "columnKind": "utf16CodeUnits",   # required by SARIF for regions
        "properties": {
            "verity.reviewId": review_dict.get("reviewId"),
            "verity.snapshotId": review_dict.get("snapshot", {}).get("snapshotId"),
            "verity.engine": review_dict.get("engine"),
            "verity.coverage": coverage,
            "verity.verdict.subject": subject,
            "verity.verdict.reasonCodes": verdict.get("reasonCodes", []),
            "verity.owaspCoverage": review_dict.get("owaspCoverage"),
            "verity.score.status": (review_dict.get("score") or {}).get("status"),
            "verity.score.value": (review_dict.get("score") or {}).get("value"),
            "verity.score.policyVersion": (review_dict.get("score") or {}).get("policyVersion"),
            "verity.reviewConfidence.grade": (review_dict.get("reviewConfidence") or {}).get("grade"),
            "verity.reviewConfidence.policyVersion": (review_dict.get("reviewConfidence") or {}).get("policyVersion"),
            "verity.issues": [
                {
                    "issueId": item.get("issueId"),
                    "riskId": item.get("riskId"),
                    "status": item.get("status"),
                    "severity": item.get("severity"),
                    "sourceLayers": item.get("sourceLayers") or [],
                    "detectorIds": item.get("detectorIds") or [],
                    "occurrenceIds": item.get("occurrenceIds") or [],
                    "runtimeChecks": [
                        {
                            "detectorId": check.get("detectorId"),
                            "sourceLayer": check.get("sourceLayer"),
                            "outcome": check.get("outcome"),
                        }
                        for check in item.get("runtimeChecks") or []
                        if type(check) is dict
                    ],
                }
                for item in review_dict.get("issues") or []
            ],
            "verity.dynamicPlan": {
                "schemaVersion": (review_dict.get("dynamicPlan") or {}).get(
                    "schema_version"),
                "policy": (review_dict.get("dynamicPlan") or {}).get("policy"),
                "items": [
                    {
                        "checkId": item.get("check_id"),
                        "stage": item.get("stage"),
                        "status": item.get("status"),
                        "reasonCodes": item.get("reason_codes") or [],
                        "riskIds": item.get("risk_ids") or [],
                    }
                    for item in (review_dict.get("dynamicPlan") or {}).get("items") or []
                ],
            },
        },
    }
    if extensions:
        run["tool"]["extensions"] = extensions

    return {
        "$schema": SARIF_SCHEMA_URI,
        "version": SARIF_VERSION,
        "runs": [run],
    }


def to_sarif_json(review_dict: Dict[str, Any]) -> str:
    return json.dumps(review_to_sarif(review_dict), indent=2,
                      ensure_ascii=False, sort_keys=True)


# Minimal structural validator (offline, no schema file needed).

_REQUIRED_TOP = ("$schema", "version", "runs")
_REQUIRED_RUN = ("tool", "results")


def validate_sarif_shape(obj: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for k in _REQUIRED_TOP:
        if k not in obj:
            errors.append(f"missing top-level key: {k}")
    if obj.get("version") != SARIF_VERSION:
        errors.append(f"version must be {SARIF_VERSION!r}, got {obj.get('version')!r}")
    runs = obj.get("runs") or []
    if not isinstance(runs, list) or not runs:
        errors.append("runs must be a non-empty array")
    for i, run in enumerate(runs):
        for k in _REQUIRED_RUN:
            if k not in run:
                errors.append(f"runs[{i}]: missing key {k}")
        tool = run.get("tool") or {}
        driver = tool.get("driver") or {}
        if not driver.get("name"):
            errors.append(f"runs[{i}].tool.driver.name missing")
        for j, res in enumerate(run.get("results") or []):
            if "ruleId" not in res:
                errors.append(f"runs[{i}].results[{j}] missing ruleId")
            properties = res.get("properties")
            agent_runtime_result = (
                type(properties) is dict
                and properties.get("verity.sourceLayer")
                == "V2_agent_runtime"
            )
            if agent_runtime_result:
                if "locations" in res:
                    errors.append(
                        f"runs[{i}].results[{j}]: V2_agent_runtime result "
                        "must omit locations"
                    )
                if "relatedLocations" in res:
                    errors.append(
                        f"runs[{i}].results[{j}]: V2_agent_runtime result "
                        "must omit relatedLocations"
                    )
            elif "locations" not in res:
                errors.append(f"runs[{i}].results[{j}] missing locations")
            elif not isinstance(res["locations"], list) or not res["locations"]:
                errors.append(f"runs[{i}].results[{j}].locations must be non-empty")
    return errors
