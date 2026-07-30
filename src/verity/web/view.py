"""Front-end friendly view model.

Rules:
- Never surface RedactionMap, raw Secret bytes, host absolute paths, or
  internal object graphs (RuleMatch/Evidence chains).
- Every string is either a controlled taxonomy value or comes from a
  redactedPreview / ruleID that the pipeline has already scrubbed.
- Coverage-insufficient takes the highest display priority (spec §16).
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..findings_view import completed_findings, source_layer
from ..guidance import lookup as _lookup_guidance, next_steps_summary


# The four top-level headlines the UI shows. Coverage insufficient wins.
_HEADLINES = {
    "semantic_block": {
        "code": "semantic_block",
        "title": "语意检查未完成，暂不能下结论",
        "detail": "已配置模型 Provider，但本次调用、预算或验证链路没有完整完成。本次不显示安全分，请查看语意状态后重试。",
        "tone": "warning",
    },
    "coverage_block": {
        "code": "coverage_block",
        "title": "检查不完整，暂不能下结论",
        "detail": "部分关键检查未完成或未执行，无法给出安全结论。请查看下方“未完成的检查”并按提示补齐后重试。",
        "tone": "warning",
    },
    "findings_block_skill_high": {
        "code": "findings_block_skill_high",
        "title": "不建议安装",
        "detail": "检测出高危问题，请先按证据修复后再考虑安装。",
        "tone": "bad",
    },
    "findings_block_prompt_high": {
        "code": "findings_block_prompt_high",
        "title": "修改后再使用",
        "detail": "Prompt 中存在高置信问题，请按证据修改后再交给模型。",
        "tone": "bad",
    },
    "review_required_skill": {
        "code": "review_required_skill",
        "title": "需要人工复核后再安装",
        "detail": "本次检查发现中低危问题，建议人工过一遍再决定是否安装。",
        "tone": "warning",
    },
    "needs_revision_prompt": {
        "code": "needs_revision_prompt",
        "title": "建议修改后再使用",
        "detail": "本次检查发现中低危问题，建议按证据修改后再用。",
        "tone": "warning",
    },
    "pass_skill": {
        "code": "pass_skill",
        "title": "本次审查未发现阻断项",
        "detail": "已完成的检查未发现高危问题。仍请自行确认，本次审查不能替代运行时验证。",
        "tone": "ok",
    },
    "pass_prompt": {
        "code": "pass_prompt",
        "title": "本次审查未发现阻断项",
        "detail": "已完成的检查未发现高危问题；仍建议在真实使用前小范围试运行。",
        "tone": "ok",
    },
}


def headline_for(review_dict: Dict[str, Any]) -> Dict[str, str]:
    verdict = review_dict.get("verdict") or {}
    coverage = verdict.get("coverage") or "unknown"
    engine = review_dict.get("engine")
    # Use the RAW semantic status here, not capabilities.semantic.status --
    # the latter deliberately collapses provider_not_configured into
    # "failed" so the CLI's exit-code ladder treats "never configured" and
    # "configured but broke" alike. The headline must NOT make the same
    # collapse: an unconfigured Provider is an honest non-event (semantic
    # was simply never attempted with real credentials) and must never
    # outrank a High/Critical deterministic finding's own headline. Only a
    # Provider that WAS configured and then genuinely failed/ran out of
    # budget mid-run is worth its own warning headline.
    semantic_status = ((review_dict.get("semantic") or {}).get("status"))
    if semantic_status in ("failed", "budget_exhausted"):
        return _HEADLINES["semantic_block"]
    if coverage != "sufficient":
        return _HEADLINES["coverage_block"]
    subject = verdict.get("subject") or {}
    outcome = subject.get("outcome") or ""
    all_findings, _ = completed_findings(review_dict)
    has_high = any(
        f["severity"] in ("high", "critical") for f in all_findings
    )
    if engine == "skill":
        if has_high or outcome == "do_not_install":
            return _HEADLINES["findings_block_skill_high"]
        if outcome == "review_required":
            return _HEADLINES["review_required_skill"]
        return _HEADLINES["pass_skill"]
    # prompt
    if has_high:
        return _HEADLINES["findings_block_prompt_high"]
    if outcome == "needs_revision":
        return _HEADLINES["needs_revision_prompt"]
    return _HEADLINES["pass_prompt"]


def _finding_view(f: Dict[str, Any], ev_by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:  # noqa: E501
    guidance = _lookup_guidance(f)
    """Trim a Finding to the fields the UI needs.

    ``artifactPath`` and ``sourceByteRange`` are relative and safe; the
    intake layer forbids absolute paths for both Prompt and Skill flows.
    """
    subject = f.get("subject") or {}
    evidences_view: List[Dict[str, Any]] = []
    for eid in f.get("evidenceIds", []):
        ev = ev_by_id.get(eid)
        if not ev:
            continue
        for loc in ev.get("locations", []) or []:
            rng = loc.get("sourceByteRange") or {}
            evidences_view.append({
                "artifactPath": loc.get("artifactPath", ""),
                "startByte": rng.get("start"),
                "endByte": rng.get("end"),
                "redactedPreview": ev.get("redactedPreview"),
                "sensitivity": ev.get("sensitivity") or "normal",
            })
    origin = f.get("origin") or {}
    return {
        "id": f["findingId"],
        "type": f["findingType"],
        "severity": f["severity"],
        "claim": f.get("claim", ""),
        "originKind": origin.get("kind", ""),
        "sourceLayer": source_layer(f),
        "artifactPath": subject.get("artifactPath", ""),
        "controls": f.get("controls") or [],
        "evidences": evidences_view,
        # Subject fields are already schema-validated; we surface only the
        # scalar entries so the UI can render them as key/value chips.
        "subject": {k: v for k, v in subject.items()
                    if k not in ("artifactPath",)},
        "guidance": guidance,
    }


def _analyzer_view(am: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    br = am.get("banditRun")
    if br:
        out.append({
            "name": "bandit",
            "status": br.get("status") or "unknown",
            "version": br.get("toolVersion") or "",
            "reasonCode": br.get("reasonCode") or "",
        })
    gr = am.get("gitleaksRun")
    if gr:
        out.append({
            "name": "gitleaks",
            "status": gr.get("status") or "unknown",
            "version": gr.get("toolVersion") or "",
            "reasonCode": gr.get("reasonCode") or "",
        })
    return out


def _blocked_view(review_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for e in review_dict.get("executions") or []:
        status = e.get("status")
        if status in ("blocked_by_upstream_failure", "failed"):
            out.append({
                "planItemId": e.get("planItemId"),
                "status": status,
                "reasonCode": e.get("reasonCode") or "",
            })
    return out


_ORIGIN_TAGS = {
    "deterministic_rule": "确定性规则",
    "semantic_validation": "模型建议",
}


def _findings_display(findings: List[Dict[str, Any]],
                      semantic_view: Dict[str, Any] | None
                      ) -> List[Dict[str, Any]]:
    """Build ONE merged, display-only list of deterministic + semantic
    findings for the unified findings section in the Web UI.

    Safety invariant this must NOT break (see ``findings_view.completed_findings``
    and the ``semantic_view["partial"]`` comment above): an incomplete/failed
    semantic run's findings never affect score/pass. ``findings`` (passed in)
    already encodes that — it only ever contains semantic findings when the
    semantic run fully ``completed``. This function is purely a DISPLAY-layer
    merge: it never changes counts/score, it only adds an origin tag to each
    already-scored finding, and — for a partial/incomplete semantic run only —
    ALSO appends that run's findings with an explicit "not scored" badge so
    the user still sees them without them silently counting anywhere.
    """
    display: List[Dict[str, Any]] = []
    for f in findings:
        origin_kind = f.get("originKind") or "deterministic_rule"
        display.append({
            **f,
            "originTag": _ORIGIN_TAGS.get(origin_kind, origin_kind),
            "notScored": False,
        })
    if semantic_view and semantic_view.get("partial"):
        for f in semantic_view.get("findings") or []:
            origin_kind = f.get("originKind") or "semantic_validation"
            display.append({
                **f,
                "originTag": _ORIGIN_TAGS.get(origin_kind, origin_kind),
                "notScored": True,
            })
    return display


def build_view_model(review_dict: Dict[str, Any], review_id: str) -> Dict[str, Any]:
    all_findings, ev_by_id = completed_findings(review_dict)
    findings = [_finding_view(f, ev_by_id) for f in all_findings]
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    verdict = review_dict.get("verdict") or {}
    coverage = verdict.get("coverage") or "unknown"
    coverage_reason_codes = review_dict.get("coverage", {}).get("reasonCodes") or []

    # UI-visible Secret coverage flag: gitleaks did NOT complete, or the
    # user chose the minimal profile.
    am = review_dict.get("artifactModel") or {}
    gr = am.get("gitleaksRun") or {}
    secret_scan_status = gr.get("status") or "unknown"
    secret_scan_ok = secret_scan_status == "completed"
    # For prompt engine there is no gitleaks step, but we still fake
    # a placeholder so the UI can render consistent chips.
    if review_dict.get("engine") == "prompt":
        secret_scan_status = "not_applicable_engine"
        secret_scan_ok = False

    next_steps = next_steps_summary(findings, coverage, secret_scan_status)
    # Semantic sub-pipeline projection (may be absent when semantic=off).
    semantic_view = None
    sem = review_dict.get("semantic") or None
    if sem is not None:
        sem_status = sem.get("status") or "unknown"
        sem_ev_by_id = {
            item.get("evidenceId"): item
            for item in (sem.get("evidences") or [])
            if item.get("evidenceId")
        }
        sem_findings = [
            _finding_view(f, sem_ev_by_id)
            for f in sem.get("findings") or []
        ]
        semantic_view = {
            "status": sem_status,
            "reasonCode": sem.get("reasonCode"),
            "egressPolicy": sem.get("egressPolicy") or "off",
            "callCounts": sem.get("callCounts") or {},
            "candidateCount": len(sem.get("candidates") or []),
            "assessmentCounts": _assessment_counts(sem.get("assessments") or []),
            "findings": sem_findings,
            "stageStats": [
                {
                    "findingType": finding_type,
                    "extractorSeedCount": stats.get(
                        "extractorSeedCount", 0),
                    "catalogHintProposedCount": stats.get(
                        "catalogHintProposedCount", 0),
                    "generatorAcceptedCandidateCount": stats.get(
                        "generatorAcceptedCandidateCount", 0),
                    "queuedCandidateCount": stats.get(
                        "queuedCandidateCount", 0),
                    "validatorStates": dict(
                        stats.get("validatorStates") or {}),
                }
                for finding_type, stats in sorted(
                    (sem.get("stageStats") or {}).items())
            ],
            # True when the run did not fully complete but still confirmed
            # some candidates. Those findings are advisory + possibly
            # incomplete; the UI must label them clearly and they are NOT
            # merged into the main completed-findings list or the score
            # (see ``findings_display`` below, which surfaces them for
            # DISPLAY ONLY with an explicit not-scored marker).
            "partial": bool(sem_status != "completed" and sem_findings),
            "planItems": [
                {"planItemId": p.get("planItemId"),
                 "status": p.get("status"),
                 "reasonCode": p.get("reasonCode")}
                for p in (sem.get("planItems") or [])
            ],
        }
    capabilities = review_dict.get("capabilities") or {}
    if ((capabilities.get("semantic") or {}).get("status") == "failed"):
        next_steps["steps"].insert(0, {
            "code": "rerun_semantic",
            "label": "先处理语意 Provider、预算或验证失败，再使用相同配置重新审查",
        })
    score = review_dict.get("score") or {"status": "unavailable", "value": None}
    if ((capabilities.get("semantic") or {}).get("status") == "failed"):
        score = {
            **score,
            "status": "unavailable",
            "value": None,
            "reasonCodes": ["semantic_requested_but_incomplete"],
            "includedLayers": [],
            "evaluatedLayers": [],
            "deductions": [],
        }
    confidence = review_dict.get("reviewConfidence") or {}
    remediations = review_dict.get("remediations") or []
    return {
        "reviewId": review_id,
        "engine": review_dict.get("engine"),
        "createdAt": None,
        "headline": headline_for(review_dict),
        "coverage": {
            "status": coverage,
            "reasonCodes": coverage_reason_codes,
        },
        "counts": counts,
        "score": {
            "status": score.get("status"), "value": score.get("value"),
            "policyVersion": score.get("policyVersion"),
            "highestSeverity": score.get("highestSeverity"),
            "severityCap": score.get("severityCap"),
            "reasonCodes": score.get("reasonCodes") or [],
            "includedLayers": score.get("includedLayers") or [],
            "evaluatedLayers": score.get("evaluatedLayers") or [],
            "deductions": [
                {"findingId": x.get("findingId"),
                 "riskIds": x.get("riskIds") or [],
                 "severity": x.get("severity"), "points": x.get("points"),
                 "factorPercent": x.get("factorPercent")}
                for x in (score.get("deductions") or [])
            ],
        },
        "reviewConfidence": {
            "grade": confidence.get("grade", "D"),
            "policyVersion": confidence.get("policyVersion"),
            "limitations": confidence.get("limitations") or [],
            "note": confidence.get("note") or "",
        },
        "remediations": [
            {"remediationId": x.get("remediationId"),
             "findingId": x.get("findingId"), "severity": x.get("severity"),
             "riskIds": x.get("riskIds") or [], "priority": x.get("priority"),
             "title": x.get("title"), "actions": x.get("actions") or [],
             "verificationChecks": x.get("verificationChecks") or [],
             "applyMode": x.get("applyMode")}
            for x in remediations
        ],
        "nextSteps": next_steps,
        "findings": findings,
        "findingsDisplay": _findings_display(findings, semantic_view),
        "blocked": _blocked_view(review_dict),
        "analyzers": _analyzer_view(am),
        "secretScan": {
            "status": secret_scan_status,
            "ok": secret_scan_ok,
        },
        "owaspCoverage": review_dict.get("owaspCoverage") or {},
        "downloads": {
            "json": f"/api/report/{review_id}/report.json",
            "html": f"/api/report/{review_id}/report.html",
            "sarif": f"/api/report/{review_id}/report.sarif",
        },
        "scopeNote": (
            "本地静态检查 V1：不执行 Skill、不安装依赖、不联网。"
            "Prompt 黑盒（V1.5）与 Skill 隔离沙箱（V2）尚未启用。"
        ),
        "capabilities": capabilities,
        "semantic": semantic_view,
    }


def _assessment_counts(assessments):
    counts = {"confirmed": 0, "rejected": 0, "insufficient_evidence": 0,
              "validation_failed": 0, "pending": 0}
    for a in assessments:
        st = a.get("state")
        if st in counts:
            counts[st] += 1
    return counts
