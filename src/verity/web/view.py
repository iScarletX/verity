"""Front-end friendly view model.

Rules:
- Never surface RedactionMap, raw Secret bytes, host absolute paths, or
  internal object graphs (RuleMatch/Evidence chains).
- Every string is either a controlled taxonomy value or comes from a
  redactedPreview / ruleID that the pipeline has already scrubbed.
- Match the CLI gate priority: High/Critical signals outrank incomplete checks.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..findings_view import completed_findings, source_layer
from ..guidance import lookup as _lookup_guidance, next_steps_summary
from ..issues import controlled_runtime_occurrence_severities


# Controlled top-level headlines shown by the UI.
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
    "agent_runtime_block": {
        "code": "agent_runtime_block",
        "title": "Agent 运行时检查未完成，暂不能下结论",
        "detail": "本次显式请求的 Agent 运行时检查失败或未完整完成。静态结果仍然有效，请检查运行时配置后重试。",
        "tone": "warning",
    },
    "prompt_blackbox_block": {
        "code": "prompt_blackbox_block",
        "title": "Prompt 黑盒检查未完成，暂不能下结论",
        "detail": "本次显式请求的 Prompt 黑盒检查失败或未完整完成。已完成的静态结果仍然有效，请检查 Provider 与场景状态后重试。",
        "tone": "warning",
    },
    "skill_sandbox_block": {
        "code": "skill_sandbox_block",
        "title": "Skill 沙箱检查未完成，暂不能下结论",
        "detail": "本次显式请求的 Skill 沙箱检查失败、不可用或未完整完成。已完成的静态结果仍然有效，请查看隔离能力状态。",
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


_SEMANTIC_REASON_HINTS = {
    "credential_missing": "未检测到已保存的 Provider API Key（或 Key 未生效）。请在下方“语意检查设置”中重新输入并保存后再试。",
    "provider_timeout": "调用模型 Provider 超时。请检查网络连通性或稍后重试。",
    "network_error": "调用模型 Provider 时网络请求失败。请检查网络连通性或 Base URL 配置。",
    "http_error": "模型 Provider 返回了错误状态码（例如鉴权失败或模型名不存在）。请检查 API Key 与模型名配置。",
    "redirect_refused": "模型 Provider 返回了重定向，出于安全考虑已被拒绝。请检查 Base URL 配置是否正确。",
    "request_too_large": "发给模型 Provider 的请求超出大小限制。",
    "response_too_large": "模型 Provider 返回内容超出大小限制。",
    "invalid_json": "模型 Provider 返回的内容不是合法 JSON，可能是模型输出格式异常。",
    "invalid_json_shape": "模型 Provider 返回的 JSON 结构不符合预期，可能是模型输出格式异常。",
    "provider_role_mismatch": "配置的模型角色（生成/验证）与实际返回不一致。",
    "budget_generation_exhausted": "本次语意检查的调用预算已用尽（候选生成阶段）。",
    "budget_candidates_total_exhausted": "本次语意检查的调用预算已用尽（候选总量上限）。",
    "run_budget_exhausted": "本次语意检查的整体调用预算已用尽。",
    "catalog_candidate_hint_invalid": "内置候选提示数据异常，这是程序内部问题，请反馈给维护者。",
    "generator_output_schema_violation": "模型生成结果不符合预期结构，可能是模型输出格式异常。",
    "catalog_sweep_output_violation": "模型候选扫描结果不符合预期结构，可能是模型输出格式异常。",
}


def _semantic_block_headline(review_dict: Dict[str, Any]) -> Dict[str, str]:
    """Fill in the actual reasonCode so a Provider failure is self-diagnosing.

    The base ``semantic_block`` headline text alone reads as "something went
    wrong" with no next action. Most failures here are Provider/credential
    misconfiguration (see ``_SEMANTIC_REASON_HINTS``), not a code bug -- the
    reasonCode plus a plain-language hint lets the reader fix it themselves
    without needing to ask why the run "fails at the very end": semantic
    validation runs as its own sub-pipeline after the deterministic rules on
    purpose (it can reuse their evidence as seeds), so its own failure is
    only known once that sub-pipeline itself finishes or gives up.
    """
    base = dict(_HEADLINES["semantic_block"])
    sem = review_dict.get("semantic") or {}
    reason_code = sem.get("reasonCode")
    if reason_code:
        hint = _SEMANTIC_REASON_HINTS.get(reason_code, "")
        base["detail"] = (
            f"{base['detail']}\n失败原因（reasonCode）：{reason_code}。{hint}"
        ).strip()
    return base


def _runtime_occurrence_severities(
        review_dict: Dict[str, Any]) -> List[str]:
    """Project controlled severities for all dynamic runtime layers."""
    return controlled_runtime_occurrence_severities(review_dict)


def headline_for(review_dict: Dict[str, Any]) -> Dict[str, str]:
    verdict = review_dict.get("verdict") or {}
    coverage = verdict.get("coverage") or "unknown"
    engine = review_dict.get("engine")
    subject = verdict.get("subject") or {}
    outcome = subject.get("outcome") or ""
    all_findings, _ = completed_findings(review_dict)
    runtime_severities = _runtime_occurrence_severities(review_dict)
    has_high = any(
        f["severity"] in ("high", "critical") for f in all_findings
    ) or any(level in ("high", "critical") for level in runtime_severities)
    if engine == "skill" and (has_high or outcome == "do_not_install"):
        return _HEADLINES["findings_block_skill_high"]
    if engine == "prompt" and has_high:
        return _HEADLINES["findings_block_prompt_high"]
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
        return _semantic_block_headline(review_dict)
    if coverage != "sufficient":
        return _HEADLINES["coverage_block"]
    if engine == "skill":
        if (
            "skill_sandbox_requested_but_incomplete"
            in (verdict.get("reasonCodes") or [])
            or ((review_dict.get("capabilities") or {}).get(
                "skillSandbox") or {}).get("status") == "failed"
        ):
            return _HEADLINES["skill_sandbox_block"]
        runtime_capability = (
            (review_dict.get("capabilities") or {}).get(
                "agentInstructionRuntime"
            ) or {}
        )
        if (
            "agent_runtime_requested_but_incomplete"
            in (verdict.get("reasonCodes") or [])
            or runtime_capability.get("status") == "failed"
        ):
            return _HEADLINES["agent_runtime_block"]
        if outcome == "review_required":
            return _HEADLINES["review_required_skill"]
        return _HEADLINES["pass_skill"]
    # prompt
    if (
        "prompt_blackbox_requested_but_incomplete"
        in (verdict.get("reasonCodes") or [])
        or ((review_dict.get("capabilities") or {}).get(
            "promptBlackbox") or {}).get("status") == "failed"
    ):
        return _HEADLINES["prompt_blackbox_block"]
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
        "subjectKey": f.get("subjectKey", ""),
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
    return _merge_same_subject(display)


def _merge_same_subject(display: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Collapse same-rule hits on the same subject into one display card.

    The same underlying issue matched at several source locations produces
    one Finding per occurrence upstream (by design -- score, SARIF, and
    history all key off that). For the reader-facing list this reads as
    duplicated cards, so group by (findingType, subjectKey, notScored) --
    keeping deterministic and not-scored-semantic findings from ever mixing
    -- and union their evidence lists so every hit location still shows up
    under the one merged card.
    """
    groups: Dict[Any, Dict[str, Any]] = {}
    for f in display:
        key = (f.get("type"), f.get("subjectKey"), f.get("notScored"))
        merged = groups.get(key)
        if merged is None:
            merged = dict(f)
            merged["evidences"] = list(f.get("evidences") or [])
            merged["hitCount"] = 1
            groups[key] = merged
        else:
            merged["evidences"] = merged["evidences"] + list(f.get("evidences") or [])
            merged["hitCount"] += 1
    return list(groups.values())


def _merge_remediations(remediations: List[Dict[str, Any]],
                         subject_key_by_finding_id: Dict[str, Any]
                         ) -> List[Dict[str, Any]]:
    """Collapse one-per-occurrence remediations into one checklist entry.

    ``build_remediations`` (scoring.py) emits one remediation per scored
    Finding -- the same one-Finding-per-occurrence granularity the findings
    list has, and for the same reason (score/history key off individual
    occurrences). Group by (findingType, subjectKey), the identity scoring
    itself already uses for diminishing-weight duplicates, and union
    evidenceIds/findingIds so the fix-workbench shows one action item per
    underlying issue instead of one per hit location.
    """
    groups: Dict[Any, Dict[str, Any]] = {}
    for r in remediations:
        key = (r.get("findingType"),
               subject_key_by_finding_id.get(r.get("findingId")))
        merged = groups.get(key)
        if merged is None:
            merged = dict(r)
            merged["evidenceIds"] = list(r.get("evidenceIds") or [])
            merged["findingIds"] = [r.get("findingId")]
            merged["hitCount"] = 1
            merged["subjectKey"] = key[1]
            groups[key] = merged
        else:
            merged["evidenceIds"] = merged["evidenceIds"] + [
                e for e in (r.get("evidenceIds") or [])
                if e not in merged["evidenceIds"]]
            merged["findingIds"].append(r.get("findingId"))
            merged["hitCount"] += 1
    return list(groups.values())


def build_view_model(review_dict: Dict[str, Any], review_id: str) -> Dict[str, Any]:
    all_findings, ev_by_id = completed_findings(review_dict)
    subject_key_by_finding_id = {
        f["findingId"]: f.get("subjectKey") for f in all_findings
    }
    findings = [_finding_view(f, ev_by_id) for f in all_findings]
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    for severity in _runtime_occurrence_severities(review_dict):
        counts[severity] = counts.get(severity, 0) + 1
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
    issues_view = [
        {
            "issueId": item.get("issueId"),
            "riskId": item.get("riskId"),
            "title": item.get("title"),
            "status": item.get("status"),
            "severity": item.get("severity"),
            "sourceLayers": item.get("sourceLayers") or [],
            "detectorIds": item.get("detectorIds") or [],
            "occurrenceCount": len(item.get("occurrences") or []),
            "occurrences": item.get("occurrences") or [],
            "runtimeChecks": item.get("runtimeChecks") or [],
            "remediationIds": item.get("remediationIds") or [],
        }
        for item in review_dict.get("issues") or []
    ]
    raw_dynamic_plan = review_dict.get("dynamicPlan") or {}
    dynamic_items = []
    dynamic_counts = {
        "selected": 0, "not_applicable": 0, "unavailable": 0,
    }
    for item in raw_dynamic_plan.get("items") or []:
        status = item.get("status") or "not_applicable"
        dynamic_counts[status] = dynamic_counts.get(status, 0) + 1
        dynamic_items.append({
            "checkId": item.get("check_id"),
            "stage": item.get("stage"),
            "status": status,
            "mode": item.get("mode"),
            "reasonCodes": item.get("reason_codes") or [],
            "supportingFactIds": item.get("supporting_fact_ids") or [],
            "riskIds": item.get("risk_ids") or [],
            "scenarioId": item.get("scenario_id"),
        })
    dynamic_plan_view = {
        "policy": raw_dynamic_plan.get("policy") or "artifact_aware",
        "schemaVersion": raw_dynamic_plan.get("schema_version"),
        "counts": dynamic_counts,
        "items": dynamic_items,
    }
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
             "findingType": x.get("findingType"),
             "subjectKey": x.get("subjectKey"),
             "riskIds": x.get("riskIds") or [], "priority": x.get("priority"),
             "title": x.get("title"), "actions": x.get("actions") or [],
             "verificationChecks": x.get("verificationChecks") or [],
             "applyMode": x.get("applyMode"), "hitCount": x.get("hitCount", 1)}
            for x in _merge_remediations(remediations, subject_key_by_finding_id)
        ],
        "nextSteps": next_steps,
        "issues": issues_view,
        "behaviorProfile": review_dict.get("behaviorProfile") or {},
        "dynamicPlan": dynamic_plan_view,
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
            "本地静态检查 V1：默认不执行 Skill、不安装依赖、不联网。"
            "Prompt 黑盒（V1.5）只有在显式配置并确认后才会联网。"
            "Skill 执行沙箱（V2）在产品路径暂不可用；任何显式请求都会"
            "以 sandbox_isolation_hardening_required 失败关闭且不会执行 Skill。"
        ),
        "capabilities": capabilities,
        "semantic": semantic_view,
        # Controlled public promptBlackbox/skillSandbox projections. Raw
        # Provider/runtime payloads have already been removed by report.py.
        # Present ONLY when the caller actually supplied a blackbox_config /
        # sandbox_config for this run (see ReviewInputs) -- absent (None)
        # for every ordinary review, which is what lets the front-end decide
        # whether to render a detail block at all.
        "promptBlackbox": review_dict.get("promptBlackbox"),
        "skillSandbox": review_dict.get("skillSandbox"),
    }


def _assessment_counts(assessments):
    counts = {"confirmed": 0, "rejected": 0, "insufficient_evidence": 0,
              "validation_failed": 0, "pending": 0}
    for a in assessments:
        st = a.get("state")
        if st in counts:
            counts[st] += 1
    return counts
