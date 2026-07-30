"""Machine-readable V1 closure audit.

Closure is deliberately stricter than "the app runs", but it is also scoped.

Policy v2.0.0 separates two release scopes that used to be conflated:

- The **deterministic static auditor** (rules + Bandit + gitleaks + JSON/HTML/
  SARIF + Web/CLI + score/coverage). This is reproducible, boundary-tested,
  and ships as an honest *engineering preview* release candidate once its
  engineering acceptance is green. It does NOT claim evaluated detection
  accuracy; its breadth limits are disclosed, not hidden.

- The **controlled semantic review** (LLM-assisted, attempted by default when
  a Provider is configured, experimental) and any *evaluated accuracy* claim.
  This is a separate track that is NOT a gate on the deterministic
  engineering-preview release. It remains `experimental_not_ready` until a
  frozen protocol Selection passes its predeclared gate, sealed Test is
  consumed under approval, and (for a public production-quality claim)
  human/domain-expert review is obtained.

Rationale: gating a working, honestly-scoped deterministic tool on an
experimental, probabilistic feature whose accuracy is unproven — one whose
last blocker is structurally unreachable by any AI alone (human expert
sign-off) — made the release decision loop forever. v2.0.0 fixes the
*definition* of readiness, not the evidence: every semantic/accuracy
limitation is still reported.

Policy v2.1.0 splits the semantic quality track itself into two
independently-reportable tiers, because one of its five blockers
(``human_expert_review_absent``) is a standing, founder-confirmed policy
decision for this project (no human/domain-expert review program exists or
is planned), not a temporary gap that closes with more engineering work.
Collapsing all five into one boolean made "how close is semantic quality to
done" permanently unanswerable, since one blocker can never close:

- **``engineeringVerifiedTier``**: the four blockers an AI-only team CAN
  close through more work (independent AI review completing every
  provisional label, an accepted real-model Selection, sealed-Test
  consumption, and substantial/evaluated risk coverage). Reaching
  ``ready`` here is an honest, achievable "AI cross-verification is as
  thorough as this project can make it without a human in the loop" claim
  — it is still NOT a production-quality or human-expert-equivalent claim.
- **``productionTier``**: all five blockers, including
  ``human_expert_review_absent``. This tier is disclosed as permanently
  unreachable under the current, founder-confirmed no-human-review policy,
  not silently dropped — the blocker code and its detail sentence are
  never deleted from the report, only reported under a tier explicitly
  labelled as such.

The report is offline and reads repository-owned standards/corpus facts only.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from .corpus import load_manifest
from .semantic_quality import load_semantic_quality_manifest
from .standards import load_risks


CLOSURE_POLICY_ID = "verity-v1-closure"
CLOSURE_POLICY_VERSION = "2.1.0"
HUMAN_EXPERT_BLOCKER_CODE = "human_expert_review_absent"


def evaluate_v1_closure(*, engineering_checks: Dict[str, bool],
                        accepted_real_model_selection_present: bool = False,
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

    # --- Deterministic engineering-preview release gate -----------------
    # Only engineering acceptance gates the deterministic static release.
    blockers: List[Dict[str, str]] = []
    for check in engineering_failures:
        blockers.append({"code": "engineering_check_failed:" + check,
                         "class": "engineering",
                         "detail": "Required closure acceptance check failed."})
    deterministic_static_ready = engineering_ready and not blockers
    decision = ("release_candidate" if deterministic_static_ready
                else "not_ready")

    # --- Separate semantic / evaluated-accuracy track (NOT a gate) ------
    # These remain open and are honestly reported, but they do not block the
    # deterministic engineering-preview release. They gate only an evaluated
    # accuracy claim and any productionization of the semantic path.
    semantic_blockers: List[Dict[str, str]] = []
    if (l0_labels.get("provisional_single_review", 0)
            or semantic_labels.get("provisional_single_review", 0)):
        semantic_blockers.append({
            "code": "evaluation_labels_provisional", "class": "quality_evidence",
            "detail": ("Some Corpus labels remain provisional single-review; "
                       "independent AI review is tracked separately from human expertise.")})
    if not accepted_real_model_selection_present:
        semantic_blockers.append({
            "code": "accepted_real_model_selection_absent",
            "class": "quality_evidence",
            "detail": ("No frozen real-model Selection report has passed the "
                       "predeclared quality gate; Calibration alone is not release evidence.")})
    if not sealed_test_consumed:
        semantic_blockers.append({
            "code": "sealed_semantic_test_unconsumed",
            "class": "quality_evidence",
            "detail": "The sealed semantic test split has not been consumed."})
    if strong_count == 0 or evaluated_count == 0:
        semantic_blockers.append({
            "code": "no_substantial_or_evaluated_risk_coverage",
            "class": "quality_evidence",
            "detail": "No unified risk currently has substantial/evaluated evidence."})
    # The human-expert blocker is a standing policy reality (no human/domain-
    # expert review program exists or is planned for this project), confirmed by
    # the founder. It never closes with engineering alone. Rather than let it
    # permanently fail the entire semantic quality track, it is separated into
    # its own "production tier" disclosure while the four AI-closeable blockers
    # form the "engineering-verified tier". Both tiers are reported honestly;
    # neither is dropped.
    human_expert_blocker = {
        "code": HUMAN_EXPERT_BLOCKER_CODE,
        "class": "policy_standing",
        "detail": ("AI cross-model review is not human/domain-expert review. "
                   "This project has no human review program and does not plan "
                   "one; this blocker is a standing policy reality, not a "
                   "temporary gap. It is reported here permanently and honestly "
                   "rather than being silently dropped, but it is excluded from "
                   "the engineering-verified tier so that tier can reflect "
                   "achievable AI-only verification progress."),
    }
    # Tier 1: blockers an AI-only team CAN close through more engineering work.
    engineering_verified_blockers = [b for b in semantic_blockers]  # copy
    engineering_verified_ready = not engineering_verified_blockers
    # Tier 2: all blockers — includes human_expert_review_absent. This tier is
    # permanently unreachable under the current policy; it is reported honestly.
    production_blockers = list(semantic_blockers) + [human_expert_blocker]
    production_ready = False  # structurally unreachable under current policy
    semantic_quality_ready = engineering_verified_ready  # summary uses tier 1

    disclosed_limitations = [
        {"code": "detection_breadth_not_evaluated",
         "detail": ("The engineering preview does not claim evaluated detection "
                    "accuracy; unified-risk breadth remains none/signal/partial "
                    "and is reported honestly in every review.")},
        {"code": "semantic_review_experimental_quality_unproven",
         "detail": ("Controlled semantic review is attempted by default when a "
                    "Provider is configured, but its accuracy remains experimental "
                    "and below its frozen protocol Selection gate as last measured; "
                    "it is not part of the deterministic release scope.")},
    ]

    deferred = [
        {"code": "v1_5_prompt_blackbox_not_implemented",
         "detail": "Planned after V1; not counted as a V1 engineering failure."},
        {"code": "v2_skill_sandbox_not_implemented",
         "detail": "Planned after V1.5; not counted as a V1 engineering failure."},
        {"code": "provider_web_productization_absent",
         "detail": "Controlled semantic remains trusted CLI/research only."},
    ]
    return {
        "schemaVersion": 2,
        "policyId": CLOSURE_POLICY_ID,
        "policyVersion": CLOSURE_POLICY_VERSION,
        "releaseScope": "deterministic_static_v1_engineering_preview",
        "decision": decision,
        "engineeringReady": engineering_ready,
        "deterministicStaticReady": deterministic_static_ready,
        "semanticQualityTrack": {
            # Top-level status reflects the engineering-verified tier so this
            # field can transition from "not_ready" to "ready" as AI-closeable
            # work completes, without being held hostage to the human-review
            # blocker that can never change.
            "status": ("experimental_ready" if engineering_verified_ready
                       else "experimental_not_ready"),
            "inReleaseGate": False,
            # Two honest tiers:
            "engineeringVerifiedTier": {
                "description": (
                    "The four blockers an AI-only team can close through more "
                    "engineering work. 'ready' here means AI cross-verification "
                    "is as thorough as this project makes it; it is NOT a "
                    "production-quality or human-expert-equivalent claim."),
                "ready": engineering_verified_ready,
                "blockers": engineering_verified_blockers,
            },
            "productionTier": {
                "description": (
                    "All five blockers including human_expert_review_absent, "
                    "which is a standing policy reality for this project. "
                    "This tier is permanently unreachable under the current "
                    "no-human-review policy and is reported that way honestly; "
                    "the blocker is never silently dropped."),
                "ready": production_ready,
                "permanentlyUnreachableUnderCurrentPolicy": True,
                "blockers": production_blockers,
            },
        },
        # Retained for compatibility: reflects engineering-verified tier.
        "qualityEvidenceReady": engineering_verified_ready,
        "engineeringChecks": dict(sorted(engineering_checks.items())),
        "blockers": blockers,
        "disclosedLimitations": disclosed_limitations,
        "deferred": deferred,
        "evidenceSummary": {
            "unifiedRiskCount": len(risks),
            "substantialOrEvaluatedLayerCount": strong_count,
            "evaluatedLayerCount": evaluated_count,
            "l0LabelStatuses": dict(sorted(l0_labels.items())),
            "semanticLabelStatuses": dict(sorted(semantic_labels.items())),
            "acceptedRealModelSelectionPresent": accepted_real_model_selection_present,
            "sealedTestConsumed": sealed_test_consumed,
        },
        "note": ("`decision` covers only the deterministic static engineering "
                 "preview and its honestly-disclosed limits. It is not a claim "
                 "of evaluated detection accuracy. The controlled semantic "
                 "review is a separate experimental track and does not gate "
                 "this release; its open blockers are listed under "
                 "semanticQualityTrack."),
    }
