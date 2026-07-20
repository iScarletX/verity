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
- Coverage status is placed in ``run.properties.coverage`` so downstream
  consumers can distinguish "0 findings and everything ran" from
  "0 findings and half the analyzers failed".
"""

from __future__ import annotations

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
                "tags": [f"engine:{('prompt' if rid.startswith('prompt.') else 'skill')}"],
            },
        }
    return list(seen.values())


def _security_severity(sev: str) -> str:
    # Rough numeric mapping (GitHub Code Scanning convention).
    return {"low": "3.0", "medium": "5.5",
            "high": "7.5", "critical": "9.5"}.get(sev, "5.0")


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
        },
    }
    if related:
        result["relatedLocations"] = related
    return result


def review_to_sarif(review_dict: Dict[str, Any]) -> Dict[str, Any]:
    """Convert the JSON view produced by ``report.review_to_dict``."""
    ev_by_id = {e["evidenceId"]: e for e in review_dict.get("evidences", [])}

    tool_driver = {
        "name": "verity",
        "version": _VERITY_VERSION,
        "informationUri": "https://verity.dev/",
        "rules": _rule_descriptors(review_dict),
    }

    # Adjunct tools (parsers / analyzers) go under ``run.tool.extensions``
    # so downstream consumers know which secondary tools contributed.
    extensions: List[Dict[str, Any]] = []
    am = review_dict.get("artifactModel") or {}
    br = am.get("banditRun") or {}
    if br.get("toolVersion"):
        extensions.append({
            "name": "bandit",
            "version": br.get("toolVersion"),
            "informationUri": "https://bandit.readthedocs.io/",
        })

    coverage = review_dict.get("coverage", {}).get("status", "unknown")
    verdict = review_dict.get("verdict", {})
    subject = verdict.get("subject")  # may be None on insufficient coverage

    results = [_finding_to_result(f, ev_by_id) for f in review_dict["findings"]]

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
            if "locations" not in res:
                errors.append(f"runs[{i}].results[{j}] missing locations")
            elif not isinstance(res["locations"], list) or not res["locations"]:
                errors.append(f"runs[{i}].results[{j}].locations must be non-empty")
    return errors
