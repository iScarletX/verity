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


# Skill review profiles. `standard` requires gitleaks; `minimal` explicitly
# opts out and marks Secret coverage as user-declined in the ReviewPlan.
SKILL_PROFILES = ("standard", "minimal")


@dataclass
class ReviewInputs:
    engine: str  # "prompt" or "skill"
    snapshot: ArtifactSnapshot
    file_bytes: Dict[str, bytes]
    profile: str = "standard"  # skill-engine only
    # Optional semantic-review switch. ``None`` = default (off).  This is
    # kept as ``Any`` to avoid an import cycle; run_review re-imports the
    # real type.
    semantic_config: Optional[object] = None


def _build_engine(name: str, *, bandit_runner=None, gitleaks_runner=None,
                  profile: str = "standard") -> Engine:
    ftr = build_finding_type_registry()
    parser = None
    analyzers = []
    if name == "prompt":
        rr = build_prompt_rule_registry(ftr)
    elif name == "skill":
        rr = build_skill_rule_registry(ftr)
        from .parser import parse_skill
        parser = parse_skill
        # Bandit analyzer. Default: a real BanditRunner subprocess call.
        # Tests may inject a stub via ``bandit_runner``.
        if bandit_runner is None:
            from .bandit_runner import BanditRunner
            bandit_runner = BanditRunner()

        def _run_bandit(snapshot, file_bytes):
            br = bandit_runner.run_on_snapshot(snapshot, file_bytes)
            updates = {"banditRun": {
                "status": br.status,
                "toolName": br.toolName,
                "toolVersion": br.toolVersion,
                "exitCode": br.exitCode,
                "durationSeconds": br.durationSeconds,
                "stagedFileCount": br.stagedFileCount,
                "pathMap": br.pathMap,
                "results": br.results,
                "reasonCode": br.reasonCode,
            }}
            if br.status == "completed":
                return updates, "completed", None
            if br.status == "timeout":
                return updates, "failed", f"bandit:{br.reasonCode}"
            if br.status == "version_mismatch":
                return updates, "failed", f"bandit:{br.reasonCode}"
            return updates, "failed", f"bandit:{br.reasonCode or 'unknown'}"

        analyzers.append({
            "componentId": "bandit",
            "componentVersion": "1.7.10",
            "gatingClass": "normal",
            "run": _run_bandit,
        })

        if profile == "minimal":
            # Explicit user opt-out. We STILL record the analyzer as a
            # plan item so its absence is visible in Coverage/reports.
            def _run_gitleaks_skipped(snapshot, file_bytes):
                updates = {"gitleaksRun": {
                    "status": "not_requested_by_profile",
                    "toolName": "gitleaks",
                    "toolVersion": "",
                    "reasonCode": "minimal_profile_selected",
                }}
                return updates, "not_applicable", "minimal_profile:secret_scan_skipped"

            analyzers.append({
                "componentId": "gitleaks",
                "componentVersion": "required-when-standard",
                "gatingClass": "critical",
                "run": _run_gitleaks_skipped,
            })
        else:
            if gitleaks_runner is None:
                from .gitleaks_runner import GitleaksRunner
                gitleaks_runner = GitleaksRunner()

            def _run_gitleaks(snapshot, file_bytes):
                gr = gitleaks_runner.run_on_snapshot(snapshot, file_bytes)
                updates = {"gitleaksRun": {
                    "status": gr.status,
                    "toolName": gr.toolName,
                    "toolVersion": gr.toolVersion,
                    "toolPath": gr.toolPath,
                    "toolSha256": gr.toolSha256,
                    "exitCode": gr.exitCode,
                    "durationSeconds": gr.durationSeconds,
                    "stagedFileCount": gr.stagedFileCount,
                    "pathMap": gr.pathMap,
                    "results": gr.results,   # already redacted by runner
                    "reasonCode": gr.reasonCode,
                }}
                if gr.status == "completed":
                    return updates, "completed", None
                return updates, "failed", f"gitleaks:{gr.reasonCode or gr.status}"

            analyzers.append({
                "componentId": "gitleaks",
                "componentVersion": "8.28.0",
                "gatingClass": "critical",
                "run": _run_gitleaks,
            })
    else:
        raise ValueError(f"unknown engine: {name}")
    return Engine(name, rr, ftr, DEFAULT_IMPLEMENTATIONS, parser=parser,
                  analyzers=analyzers)


def run_review(ri: ReviewInputs, *, bandit_runner=None,
               gitleaks_runner=None,
               candidate_generator=None, validator=None) -> Review:
    if ri.profile not in SKILL_PROFILES:
        raise ValueError(f"unknown profile: {ri.profile}")
    engine = _build_engine(ri.engine, bandit_runner=bandit_runner,
                           gitleaks_runner=gitleaks_runner,
                           profile=ri.profile)
    evidences, events, findings, plan_items, executions, artifact_model = engine.run(
        ri.snapshot, ri.file_bytes
    )
    if ri.engine == "skill":
        from .capabilities import extract_capability_facts
        artifact_model["capabilityFacts"] = extract_capability_facts(
            ri.snapshot, ri.file_bytes, artifact_model.get("manifest"))
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
    # Semantic sub-pipeline (default OFF; reads deterministic projection
    # only, never mutates it). Import inline so deterministic engine can
    # continue to run in environments where semantic isn't wanted.
    semantic_view: Optional[Dict[str, Any]] = None
    if ri.semantic_config is not None:
        from .semantic import SemanticConfig  # type: ignore
        from .semantic.orchestrator import SemanticOrchestrator
        cfg = ri.semantic_config
        if not isinstance(cfg, SemanticConfig):
            raise TypeError("semantic_config must be a SemanticConfig instance")
        # Build a lightweight review-dict projection for the orchestrator.
        # We intentionally do NOT reuse report.review_to_dict here to keep
        # the deterministic module free of any semantic dependency.
        from dataclasses import asdict as _asdict
        proj = {
            "reviewId": review_id,
            "engine": ri.engine,
            "snapshot": _asdict(ri.snapshot),
            "artifactModel": artifact_model,
        }
        # Attach evidences (dict form) for the orchestrator's extractors.
        # They produce their own Evidence anyway; but downstream may want.
        proj["evidences"] = [_asdict(e) for e in evidences]
        orch = SemanticOrchestrator(cfg)
        sem_result = orch.run(proj, ri.file_bytes,
                              generator=candidate_generator,
                              validator=validator)
        semantic_view = _semantic_view(sem_result)

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
        semantic=semantic_view,
    )


def _semantic_view(sem_result) -> Dict[str, Any]:
    from dataclasses import asdict as _asdict
    return {
        "status": sem_result.status,
        "reasonCode": sem_result.reasonCode,
        "egressPolicy": sem_result.egressPolicy,
        "callCounts": dict(sem_result.callCounts),
        "candidates": [_asdict(c) for c in sem_result.candidates],
        "assessments": [_asdict(a) for a in sem_result.assessments],
        "findings": [_asdict(f) for f in sem_result.findings],
        "planItems": [_asdict(p) for p in sem_result.planItems],
        "payloadAudit": [_asdict(a) for a in sem_result.payloadAudit],
    }
