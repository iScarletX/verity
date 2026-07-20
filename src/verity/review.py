"""Review orchestration — Snapshot → Plan → Execute → Coverage → Findings.

Deterministic-only in the walking skeleton. Semantic candidate/validator
paths are declared in models but intentionally NOT executed in V1
(spec §17, §21 Phase 4 gate not yet passed).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Dict, List

from .builtins import (
    build_finding_type_registry,
    build_prompt_rule_registry,
    build_skill_rule_registry,
)
from .engine import DEFAULT_IMPLEMENTATIONS, Engine
from .models import (
    ArtifactSnapshot, CoverageAssessment, Finding, ReviewPlan,
    Review, EvidenceRecord, RuleMatchEvent, ExecutionRecord,
)


@dataclass
class ReviewInputs:
    engine: str  # "prompt" or "skill"
    snapshot: ArtifactSnapshot
    file_bytes: Dict[str, bytes]


def _build_engine(name: str) -> Engine:
    ftr = build_finding_type_registry()
    parser = None
    if name == "prompt":
        rr = build_prompt_rule_registry(ftr)
    elif name == "skill":
        rr = build_skill_rule_registry(ftr)
        from .parser import parse_skill
        parser = parse_skill
    else:
        raise ValueError(f"unknown engine: {name}")
    return Engine(name, rr, ftr, DEFAULT_IMPLEMENTATIONS, parser=parser)


def run_review(ri: ReviewInputs) -> Review:
    engine = _build_engine(ri.engine)
    evidences, events, findings, plan_items, executions, artifact_model = engine.run(
        ri.snapshot, ri.file_bytes
    )
    review_id = f"r-{uuid.uuid4().hex[:12]}"
    plan_id = f"rp-{uuid.uuid4().hex[:12]}"
    plan = ReviewPlan(
        reviewPlanId=plan_id, reviewId=review_id,
        revision=1, phase="initial", expansionDepth=0,
        items=plan_items,
    )

    # Coverage: check every required, critical plan item has completed status.
    reason_codes: List[str] = []
    critical_gaps: List[str] = []
    # §9.2: `completed` OR `not_applicable` (with declared gate reason) both
    # satisfy the plan; `failed` / `blocked_by_upstream_failure` do not.
    ok_statuses = {"completed", "not_applicable"}
    ok_ids = {e.planItemId for e in executions if e.status in ok_statuses}
    for pi in plan_items:
        if pi.planItemId not in ok_ids:
            if pi.gatingClass == "critical":
                critical_gaps.append(pi.planItemId)
                reason_codes.append(f"critical_plan_item_not_completed:{pi.planItemId}")
            else:
                reason_codes.append(f"plan_item_not_completed:{pi.planItemId}")
    status = "sufficient" if not critical_gaps and not reason_codes else (
        "insufficient" if critical_gaps or reason_codes else "sufficient")
    coverage = CoverageAssessment(
        coverageAssessmentId=f"cov-{uuid.uuid4().hex[:12]}",
        reviewId=review_id, reviewPlanId=plan_id, reviewPlanRevision=1,
        status=status,  # type: ignore[arg-type]
        criticalGapPlanItemIds=critical_gaps,
        reasonCodes=reason_codes,
    )
    return Review(
        reviewId=review_id,
        artifactSnapshot=ri.snapshot,
        engine=ri.engine,  # type: ignore[arg-type]
        plan=plan,
        executions=executions,
        coverage=coverage,
        evidences=evidences,
        ruleMatches=events,
        findings=findings,
        artifactModel=artifact_model,
    )
