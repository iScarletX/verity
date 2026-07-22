"""Machine-readable V1 closure audit.

Closure is deliberately stricter than "the app runs". Engineering readiness
and quality evidence are independent; V1 can be a release candidate only when
both pass. The report is offline and reads repository-owned standards/corpus
facts only.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from .corpus import load_manifest
from .semantic_quality import load_semantic_quality_manifest
from .standards import load_risks


CLOSURE_POLICY_ID = "verity-v1-closure"
CLOSURE_POLICY_VERSION = "1.0.0"


def evaluate_v1_closure(*, engineering_checks: Dict[str, bool],
                        real_model_report_present: bool = False,
                        sealed_test_consumed: bool = False) -> Dict[str, Any]:
    required_engineering = {
        "prompt_web_cli", "skill_web_cli", "json_html_sarif",
        "coverage_failure", "score_confidence_remediation",
        "history_v1_v2_diff", "security_boundaries", "install_start_preflight",
        "tests_and_ci",
    }
    unknown = set(engineering_checks) - required_engineering
    missing = required_engineering - set(engineering_checks)
    if unknown or missing or not all(isinstance(v, bool)
                                     for v in engineering_checks.values()):
        raise ValueError(f"invalid engineering closure checks missing={sorted(missing)} unknown={sorted(unknown)}")
    engineering_failures = sorted(k for k, ok in engineering_checks.items()
                                  if not ok)
    engineering_ready = not engineering_failures

    risks = load_risks()
    all_levels = [level for risk in risks.values()
                  for level in risk["currentCoverage"].values()]
    strong_count = sum(level in {"substantial", "evaluated"}
                       for level in all_levels)
    evaluated_count = sum(level == "evaluated" for level in all_levels)
    l0_labels = Counter(c["labelStatus"] for c in load_manifest()["cases"])
    semantic_labels = Counter(
        c["labelStatus"] for c in load_semantic_quality_manifest()["cases"])

    blockers: List[Dict[str, str]] = []
    for check in engineering_failures:
        blockers.append({"code": "engineering_check_failed:" + check,
                         "class": "engineering",
                         "detail": "Required closure acceptance check failed."})
    if set(l0_labels) != {"independently_reviewed"} or set(semantic_labels) != {
            "independently_reviewed"}:
        blockers.append({
            "code": "evaluation_labels_provisional", "class": "quality_evidence",
            "detail": "Corpus labels remain provisional single-review labels."})
    if not real_model_report_present:
        blockers.append({
            "code": "real_semantic_model_quality_unmeasured",
            "class": "quality_evidence",
            "detail": "No trusted real-model calibration/selection report exists."})
    if not sealed_test_consumed:
        blockers.append({
            "code": "sealed_semantic_test_unconsumed",
            "class": "quality_evidence",
            "detail": "The sealed semantic test split has not been consumed."})
    if strong_count == 0 or evaluated_count == 0:
        blockers.append({
            "code": "no_substantial_or_evaluated_risk_coverage",
            "class": "quality_evidence",
            "detail": "No unified risk currently has substantial/evaluated evidence."})

    deferred = [
        {"code": "v1_5_prompt_blackbox_not_implemented",
         "detail": "Planned after V1; not counted as a V1 engineering failure."},
        {"code": "v2_skill_sandbox_not_implemented",
         "detail": "Planned after V1.5; not counted as a V1 engineering failure."},
        {"code": "provider_web_productization_absent",
         "detail": "Controlled semantic remains trusted CLI/research only."},
    ]
    decision = "release_candidate" if engineering_ready and not blockers else "not_ready"
    return {
        "schemaVersion": 1,
        "policyId": CLOSURE_POLICY_ID,
        "policyVersion": CLOSURE_POLICY_VERSION,
        "decision": decision,
        "engineeringReady": engineering_ready,
        "qualityEvidenceReady": not any(
            x["class"] == "quality_evidence" for x in blockers),
        "engineeringChecks": dict(sorted(engineering_checks.items())),
        "blockers": blockers,
        "deferred": deferred,
        "evidenceSummary": {
            "unifiedRiskCount": len(risks),
            "substantialOrEvaluatedLayerCount": strong_count,
            "evaluatedLayerCount": evaluated_count,
            "l0LabelStatuses": dict(sorted(l0_labels.items())),
            "semanticLabelStatuses": dict(sorted(semantic_labels.items())),
            "realModelReportPresent": real_model_report_present,
            "sealedTestConsumed": sealed_test_consumed,
        },
        "note": ("Engineering readiness does not override missing quality "
                 "evidence. not_ready is an honest release decision, not a "
                 "claim that implemented features are broken."),
    }
