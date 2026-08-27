"""Report projection — JSON and a single-file static HTML report.

Rules for the report (spec §15, §16):
- User/model content is rendered as plain text via HTML escaping only.
- No script/iframe/event attributes; strict CSP meta tag.
- RedactionMap MUST NOT appear here — this module has no access to it.
- Uncovered checks are shown as uncovered, NEVER as "no problems".
- Verdict is dual-axis: subject decision + coverage decision.
"""

from __future__ import annotations

import html
import json
from dataclasses import asdict
from typing import Any, Dict

from .dynamic.report_projection import (
    project_prompt_blackbox,
    project_skill_sandbox,
)
from .models import Review


def review_to_dict(review: Review) -> Dict[str, Any]:
    d = {
        "reviewId": review.reviewId,
        "engine": review.engine,
        "snapshot": asdict(review.artifactSnapshot),
        "plan": asdict(review.plan),
        "executions": [asdict(e) for e in review.executions],
        "coverage": asdict(review.coverage),
        "evidences": [asdict(e) for e in review.evidences],
        "ruleMatches": [asdict(e) for e in review.ruleMatches],
        "findings": [asdict(f) for f in review.findings],
        "verdict": compute_verdict(review),
    }
    if review.behaviorProfile is not None:
        d["behaviorProfile"] = json.loads(json.dumps(asdict(review.behaviorProfile)))
    if review.dynamicPlan is not None:
        d["dynamicPlan"] = json.loads(json.dumps(asdict(review.dynamicPlan)))
    if review.artifactModel:
        # Do not leak raw YAML — only compact fields needed for the report.
        am = review.artifactModel
        br = am.get("banditRun") or {}
        gr = am.get("gitleaksRun") or {}
        d["artifactModel"] = {
            "hasSkillMd": am.get("hasSkillMd"),
            "agentSkillsSpec": am.get("agentSkillsSpec"),
            "manifestFile": am.get("manifestFile"),
            "manifest": am.get("manifest"),
            "capabilityFacts": am.get("capabilityFacts"),
            "parserDiagnostics": am.get("parserDiagnostics") or [],
            "banditRun": {
                "status": br.get("status"),
                "toolName": br.get("toolName"),
                "toolVersion": br.get("toolVersion"),
                "exitCode": br.get("exitCode"),
                "durationSeconds": br.get("durationSeconds"),
                "stagedFileCount": br.get("stagedFileCount"),
                "reasonCode": br.get("reasonCode"),
                # NOTE: do NOT include pathMap (contains absolute host paths).
                # Raw results also omitted; the SARIF export uses artifactModel
                # from the Review directly, not from this projection.
            } if am.get("banditRun") else None,
            "gitleaksRun": {
                "status": gr.get("status"),
                "toolName": gr.get("toolName"),
                "toolVersion": gr.get("toolVersion"),
                "toolSha256": gr.get("toolSha256"),
                "exitCode": gr.get("exitCode"),
                "durationSeconds": gr.get("durationSeconds"),
                "stagedFileCount": gr.get("stagedFileCount"),
                "reasonCode": gr.get("reasonCode"),
                # pathMap / raw results NOT included (host paths / already
                # redacted results are attached to the Review directly).
            } if am.get("gitleaksRun") else None,
        }
    if review.engine == "skill":
        from .builtins import build_finding_type_registry, build_skill_rule_registry
        from .owasp import coverage_matrix
        ftr = build_finding_type_registry()
        rr = build_skill_rule_registry(ftr)
        d["owaspCoverage"] = coverage_matrix(rr.all())
    # Capability matrix (static / semantic / promptBlackbox / skillSandbox /
    # agentInstructionRuntime).
    # Static is always driven by deterministic results; semantic reflects
    # the optional sub-pipeline; promptBlackbox reflects the explicit V1.5
    # opt-in. Skill sandbox execution is unavailable on supported product
    # paths and an explicit V2 request is projected as failed/unavailable.
    # All four fields share one vocabulary: not_enabled (never requested),
    # completed (stage ran to completion), failed (stage was requested but
    # could not complete).
    static_status = "completed"
    if (d.get("coverage") or {}).get("status") != "sufficient":
        static_status = "failed" if any(
            e.get("status") in ("failed", "blocked_by_upstream_failure")
            for e in d.get("executions") or []
        ) else "completed"
    if review.semantic:
        sem = review.semantic
        if sem["status"] == "off":
            semantic_status = "not_enabled"
        elif sem["status"] == "provider_not_configured":
            semantic_status = "failed"
        elif sem["status"] == "budget_exhausted":
            semantic_status = "failed"
        elif sem["status"] == "completed":
            semantic_status = "completed"
        elif sem["status"] == "failed":
            semantic_status = "failed"
        else:
            semantic_status = "failed"
        d["semantic"] = sem
    else:
        semantic_status = "not_enabled"
    # Dynamic runners retain raw payloads only long enough to judge them.
    # Reports cross a stricter one-way projection that carries controlled
    # outcomes, counts, digests and lengths -- never prompt/response text or
    # sandbox exception/path/argv/SQL material.
    if review.promptBlackbox:
        pb = project_prompt_blackbox(review.promptBlackbox)
        prompt_blackbox_status = pb.get("status", "failed")
        d["promptBlackbox"] = pb
    else:
        prompt_blackbox_status = "not_enabled"
    if review.skillSandbox:
        ss = project_skill_sandbox(review.skillSandbox)
        skill_sandbox_status = ss.get("status", "failed")
        d["skillSandbox"] = ss
    else:
        skill_sandbox_status = "not_enabled"
    if review.agentInstructionRuntime is not None:
        agent_runtime = review.agentInstructionRuntime
        agent_instruction_runtime_status = agent_runtime.get("status", "failed")
        if agent_instruction_runtime_status not in {
            "not_enabled", "completed", "failed",
        }:
            agent_instruction_runtime_status = "failed"
        d["agentInstructionRuntime"] = agent_runtime
    else:
        agent_instruction_runtime_status = "not_enabled"
    d["capabilities"] = {
        "static": {"status": static_status,
                    "note": ("execution status only; current detection breadth "
                             "is signal/partial in the standards taxonomy")},
        "semantic": {"status": semantic_status,
                      "note": ("experimental, attempted by default when a "
                               "Provider is configured; execution status "
                               "does not imply semantic breadth or "
                               "evaluated accuracy")},
        "promptBlackbox": {"status": prompt_blackbox_status,
                            "note": ("V1.5 experimental research stage; "
                                     "integrated but OFF by default, and "
                                     "requires an explicit caller-supplied "
                                     "BlackboxConfig(enabled=True) with a "
                                     "trusted model Provider -- the "
                                     "reviewed prompt can never turn this "
                                     "on itself. Not part of the "
                                     "deterministic V1 release scope.")},
        "skillSandbox": {"status": skill_sandbox_status,
                           "note": ("V2 Skill execution is unavailable on "
                                    "supported product paths. An explicit "
                                    "request fails closed with "
                                    "sandbox_isolation_hardening_required "
                                    "without executing the reviewed Skill.")},
        "agentInstructionRuntime": {
            "status": agent_instruction_runtime_status,
            "note": (
                "Experimental agent-instruction runtime stage; OFF by "
                "default and available only through explicit trusted "
                "caller configuration."
            ),
        },
    }
    from .scoring import enrich_review
    enrich_review(d)
    from .issues import project_unified_issues
    d["issues"] = project_unified_issues(d)
    _reconcile_agent_runtime_verdict(d)
    return d


def _runtime_occurrence_severities(report: Dict[str, Any]) -> list[str]:
    """Return controlled severities for every projected runtime layer."""
    from .issues import controlled_runtime_occurrence_severities
    return controlled_runtime_occurrence_severities(report)


def _agent_runtime_occurrence_severities(report: Dict[str, Any]) -> list[str]:
    """Compatibility alias retained for older internal callers/tests."""
    return _runtime_occurrence_severities(report)


def _reconcile_agent_runtime_verdict(report: Dict[str, Any]) -> None:
    """Prevent the source-Finding verdict contradicting runtime facts.

    The core ``Review`` verdict remains a deterministic/semantic projection;
    dynamic signals exist only after scoring and unified-issue mapping.  This
    final report-level reconciliation covers Prompt black-box, Skill sandbox,
    and Agent-instruction runtime without pretending their observations are
    source-anchored Findings.  High/Critical evidence retains priority over an
    incomplete-stage warning, matching the CLI gate.
    """
    verdict = report.get("verdict")
    if not isinstance(verdict, dict):
        return
    reason_codes = verdict.get("reasonCodes")
    if not isinstance(reason_codes, list):
        reason_codes = []
        verdict["reasonCodes"] = reason_codes

    stage_reasons = (
        ("promptBlackbox", "prompt_blackbox_requested_but_incomplete"),
        ("skillSandbox", "skill_sandbox_requested_but_incomplete"),
        ("agentInstructionRuntime", "agent_runtime_requested_but_incomplete"),
    )
    runtime_incomplete = False
    for stage_key, code in stage_reasons:
        if stage_key not in report or report.get(stage_key) is None:
            continue
        stage = report.get(stage_key)
        status = stage.get("status") if isinstance(stage, dict) else None
        if status in {"not_enabled", "completed"}:
            continue
        runtime_incomplete = True
        if code not in reason_codes:
            reason_codes.append(code)
    if runtime_incomplete:
        subject = verdict.get("subject")
        outcome = subject.get("outcome") if isinstance(subject, dict) else None
        if outcome in {None, "ready", "low_detected_risk"}:
            verdict["subject"] = None

    runtime_severities = _runtime_occurrence_severities(report)
    if not runtime_severities:
        return
    if any(level in {"high", "critical"} for level in runtime_severities):
        engine = report.get("engine")
        if engine == "skill":
            verdict["subject"] = {
                "engine": "skill", "outcome": "do_not_install",
            }
        elif engine == "prompt":
            verdict["subject"] = {
                "engine": "prompt", "outcome": "needs_revision",
            }
        code = "high_or_critical_finding_present"
        if code not in reason_codes:
            reason_codes.append(code)
        return

    subject = verdict.get("subject")
    outcome = subject.get("outcome") if isinstance(subject, dict) else None
    if report.get("engine") == "skill" and outcome == "low_detected_risk":
        verdict["subject"] = {"engine": "skill", "outcome": "review_required"}
    elif report.get("engine") == "prompt" and outcome == "ready":
        verdict["subject"] = {"engine": "prompt", "outcome": "needs_revision"}


def compute_verdict(review: Review) -> Dict[str, Any]:
    """Dual-axis product verdict over all Findings from completed stages.

    Deterministic Findings are always included. Semantic Findings are included
    only after the controlled semantic stage completed; rejected/inconclusive/
    failed candidates cannot affect the subject verdict. Coverage insufficiency
    still suppresses a subject decision rather than pretending to be safe.
    """
    semantic_findings = []
    if review.semantic and review.semantic.get("status") == "completed":
        semantic_findings = review.semantic.get("findings") or []
    has_any = bool(review.findings or semantic_findings)
    has_high = (any(f.severity in ("high", "critical")
                    for f in review.findings)
                or any(f.get("severity") in ("high", "critical")
                       for f in semantic_findings))
    coverage_status = review.coverage.status
    reason_codes = []
    subject = None
    semantic_incomplete = bool(
        review.semantic
        and review.semantic.get("status") not in {"off", "completed"}
    )
    if semantic_incomplete:
        reason_codes.append("semantic_requested_but_incomplete")
    if coverage_status == "sufficient":
        if semantic_incomplete and not has_any:
            subject = None
        elif review.engine == "prompt":
            subject = {
                "engine": "prompt",
                "outcome": "needs_revision" if has_any else "ready",
            }
        else:
            if has_high:
                subject = {"engine": "skill", "outcome": "do_not_install"}
                reason_codes.append("high_or_critical_finding_present")
            elif has_any:
                subject = {"engine": "skill", "outcome": "review_required"}
            else:
                subject = {"engine": "skill", "outcome": "low_detected_risk"}
    else:
        if has_high:
            reason_codes.append("high_or_critical_finding_present")
        reason_codes.append("coverage_insufficient")
    return {
        "subject": subject,
        "coverage": coverage_status,
        "reasonCodes": reason_codes,
        "policyId": "verdict-policy-v1", "policyVersion": "2",
    }


def to_json(review: Review) -> str:
    return json.dumps(review_to_dict(review), indent=2, ensure_ascii=False, sort_keys=True)


_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)


def to_html(review: Review) -> str:
    """Single-file static HTML. All user/model content is escaped."""
    d = review_to_dict(review)
    verdict = d["verdict"]
    from .findings_view import completed_findings
    findings, all_ev_by_id = completed_findings(d)
    coverage = d["coverage"]
    executions = d["executions"]

    banner_kind = "warn"
    # If the Skill manifest parser failed, mark coverage-insufficient in
    # the banner explicitly — even if all other rules ran.
    parser_failed = any(
        e["status"] == "failed" and e["planItemId"] == "pi-parser-manifest"
        for e in d["executions"]
    )
    subj = verdict["subject"] or {}
    outcome = subj.get("outcome", "unknown")
    has_high = any(
        finding.get("severity") in {"high", "critical"}
        for finding in findings
    ) or any(
        severity in {"high", "critical"}
        for severity in _runtime_occurrence_severities(d)
    )
    semantic_incomplete = (
        "semantic_requested_but_incomplete" in verdict["reasonCodes"]
    )
    agent_runtime_incomplete = (
        "agent_runtime_requested_but_incomplete" in verdict["reasonCodes"]
    )
    prompt_blackbox_incomplete = (
        "prompt_blackbox_requested_but_incomplete" in verdict["reasonCodes"]
    )
    skill_sandbox_incomplete = (
        "skill_sandbox_requested_but_incomplete" in verdict["reasonCodes"]
    )
    if outcome == "do_not_install":
        banner_msg = "Subject outcome: DO_NOT_INSTALL — do not use as-is."
        banner_kind = "bad"
    elif has_high:
        if outcome == "needs_revision":
            banner_msg = "Subject outcome: NEEDS_REVISION — do not use as-is."
        else:
            banner_msg = "HIGH/CRITICAL FINDINGS PRESENT — do not use as-is."
        banner_kind = "bad"
    elif verdict["coverage"] != "sufficient":
        banner_msg = "COVERAGE INSUFFICIENT — uncovered checks are NOT the same as no findings."
        banner_kind = "warn"
    elif outcome == "needs_revision":
        banner_msg = "Subject outcome: NEEDS_REVISION — do not use as-is."
        banner_kind = "bad"
    elif prompt_blackbox_incomplete:
        banner_msg = (
            "PROMPT BLACK-BOX INCOMPLETE — completed results are shown, "
            "but the explicitly requested dynamic review is not complete."
        )
        banner_kind = "warn"
    elif skill_sandbox_incomplete:
        banner_msg = (
            "SKILL SANDBOX INCOMPLETE — completed results are shown, but "
            "the explicitly requested dynamic review is not complete."
        )
        banner_kind = "warn"
    elif agent_runtime_incomplete:
        banner_msg = (
            "AGENT RUNTIME INCOMPLETE — completed static results are "
            "shown, but the requested runtime review is not complete."
        )
        banner_kind = "warn"
    elif outcome in ("review_required",):
        banner_msg = "Subject outcome: REVIEW REQUIRED — human review needed before use."
        banner_kind = "warn"
    elif semantic_incomplete:
        banner_msg = (
            "SEMANTIC REVIEW INCOMPLETE — completed static results are "
            "shown, but this review is not complete."
        )
        banner_kind = "warn"
    else:
        banner_msg = f"Subject outcome: {outcome.upper()} (no known findings; not a safety guarantee)."
        banner_kind = "ok"

    # Build a lookup so findings can render every evidence they cite.
    ev_by_id = all_ev_by_id

    def _evidence_block(f) -> str:
        parts = []
        for eid in f.get("evidenceIds", []):
            ev = ev_by_id.get(eid)
            if ev is None:
                continue
            for loc in ev.get("locations", []):
                rng = loc.get("sourceByteRange") or {}
                start = rng.get("start", "?")
                end = rng.get("end", "?")
                path = loc.get("artifactPath", "")
                snippet = ev.get("redactedPreview") or ""
                parts.append(
                    "<div class='ev'><code>"
                    f"{html.escape(path)}:[{html.escape(str(start))}–{html.escape(str(end))}]"
                    "</code>"
                    + (f" <span class='muted'>{html.escape(snippet)}</span>" if snippet else "")
                    + "</div>"
                )
        return "".join(parts) or "<em class='muted'>(no evidence)</em>"

    from .guidance import lookup as _guidance_lookup

    def _guidance_cell(f):
        g = _guidance_lookup(f)
        title = html.escape(g.get("plainTitle") or "")
        why = html.escape(g.get("whyItMatters") or "")
        prio = html.escape(g.get("priority") or "")
        actions = g.get("whatToDo") or []
        actions_html = "".join(
            f"<li>{html.escape(a)}</li>" for a in actions
        )
        return (
            f"<div class='guidance'>"
            f"<div class='g-title'><strong>{title}</strong> "
            f"<span class='g-prio'>{prio}</span></div>"
            f"<div class='g-why'>{why}</div>"
            f"<ol class='g-actions'>{actions_html}</ol>"
            f"</div>"
        )

    def _findings_rows() -> str:
        if not findings:
            return "<tr><td colspan='7'><em>No findings recorded. This is NOT proof of safety — see Coverage.</em></td></tr>"
        rows = []
        for f in findings:
            rows.append(
                "<tr>"
                f"<td>{html.escape(f['severity'])}</td>"
                f"<td>{html.escape(f['findingType'])}</td>"
                f"<td>{_guidance_cell(f)}</td>"
                f"<td>{html.escape(f['claim'])}</td>"
                f"<td>{html.escape(f['origin'].get('kind',''))}</td>"
                f"<td>{html.escape(f['subject'].get('artifactPath',''))}</td>"
                f"<td>{_evidence_block(f)}</td>"
                "</tr>"
            )
        return "".join(rows)

    def _exec_rows() -> str:
        rows = []
        for e in executions:
            rows.append(
                "<tr>"
                f"<td>{html.escape(e['planItemId'])}</td>"
                f"<td>{html.escape(e['status'])}</td>"
                f"<td>{html.escape(e.get('reasonCode') or '')}</td>"
                "</tr>"
            )
        return "".join(rows) or "<tr><td colspan='3'><em>No executions.</em></td></tr>"

    reason_codes = coverage.get("reasonCodes") or []
    critical_gaps = coverage.get("criticalGapPlanItemIds") or []

    parser_diags = (d.get("artifactModel") or {}).get("parserDiagnostics") or []
    owasp = d.get("owaspCoverage") or {}
    score = d.get("score") or {"status": "unavailable", "value": None}
    confidence = d.get("reviewConfidence") or {}
    remediations = d.get("remediations") or []
    issues = d.get("issues") or []
    dynamic_plan = d.get("dynamicPlan") or {}

    def _score_block() -> str:
        if score.get("status") != "available":
            reason = ", ".join(score.get("reasonCodes") or ["unknown"])
            score_text = ("<strong>暂不评分</strong> — 关键检查或评分映射不完整："
                          + html.escape(reason))
        else:
            score_text = (
                f"<strong>{html.escape(str(score.get('value')))} / 100</strong> "
                f"(policy {html.escape(str(score.get('policyVersion') or ''))})")
            if score.get("highestSeverity"):
                score_text += (
                    f"；最高严重度 {html.escape(str(score['highestSeverity']))}"
                    f"；上限 {html.escape(str(score.get('severityCap')))}")
        deductions = "".join(
            "<li>扣 " + html.escape(str(x.get("points"))) + " 分 · "
            + html.escape(", ".join(x.get("riskIds") or [])) + " · "
            + html.escape(str(x.get("severity") or ""))
            + ("（同类重复按 " + html.escape(str(x.get("factorPercent"))) + "% 递减）"
               if x.get("factorPercent", 100) < 100 else "") + "</li>"
            for x in (score.get("deductions") or []) if x.get("points", 0) > 0)
        limits = "".join(
            f"<li>{html.escape(str(x))}</li>"
            for x in (confidence.get("limitations") or []))
        return (
            "<h2>安全分与审查可信度</h2>"
            f"<p>{score_text}</p>"
            f"<p>审查可信度：<strong>{html.escape(str(confidence.get('grade','D')))}</strong>。"
            "安全分只评价本次实际完成的检查，可信度单独说明能力限制；二者都不是安全保证。</p>"
            + (f"<ul>{deductions}</ul>" if deductions else "")
            + ("<details><summary>审查可信度限制</summary><ul>"
               + limits + "</ul></details>" if limits else ""))

    def _remediation_block() -> str:
        if not remediations:
            body = "<p class='muted'>当前没有受控整改项；仍需结合审查可信度判断。</p>"
        else:
            parts = []
            for item in remediations:
                actions = "".join(f"<li>{html.escape(str(x))}</li>"
                                  for x in item.get("actions") or [])
                checks = "".join(
                    f"<li>{html.escape(str(x.get('label') or ''))}</li>"
                    for x in item.get("verificationChecks") or [])
                parts.append(
                    "<details><summary>"
                    f"{html.escape(str(item.get('priority') or 'P1'))} · "
                    f"{html.escape(str(item.get('title') or ''))}</summary>"
                    f"<ol>{actions}</ol><strong>改完后这样验证：</strong><ul>{checks}</ul>"
                    "<p class='muted'>仅提供建议，不会自动改写文件。风险："
                    f"{html.escape(', '.join(item.get('riskIds') or []))}</p></details>")
            body = "".join(parts)
        return f"<h2>整改与复查（{len(remediations)}）</h2>{body}"

    def _issues_block() -> str:
        if not issues:
            return (
                "<h2>Unified issues</h2>"
                "<p class='muted'>No confirmed issue groups. This is not a safety guarantee.</p>"
            )
        cards = []
        for issue in issues:
            checks = "".join(
                "<li><code>" + html.escape(str(item.get("detectorId") or ""))
                + "</code>: " + html.escape(str(item.get("outcome") or ""))
                + "</li>"
                for item in issue.get("runtimeChecks") or []
            )
            cards.append(
                "<article class='issue-card'>"
                f"<h3>{html.escape(str(issue.get('title') or issue.get('riskId') or ''))}</h3>"
                f"<p><code>{html.escape(str(issue.get('riskId') or ''))}</code> · "
                f"<strong>{html.escape(str(issue.get('status') or 'unverified'))}</strong> · "
                f"{html.escape(str(issue.get('severity') or ''))}</p>"
                "<p class='muted'>Layers: "
                + html.escape(", ".join(issue.get("sourceLayers") or []))
                + "; occurrences: "
                + html.escape(str(len(issue.get("occurrences") or [])))
                + "</p>"
                + (f"<ul>{checks}</ul>" if checks else "")
                + "</article>"
            )
        return "<h2>Unified issues</h2><div class='issue-list'>" + "".join(cards) + "</div>"

    def _dynamic_plan_block() -> str:
        items = dynamic_plan.get("items") or []
        if not items:
            return "<h2>Dynamic check coverage</h2><p class='muted'>No dynamic plan.</p>"
        rows = []
        for item in items:
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(item.get('check_id') or ''))}</td>"
                f"<td>{html.escape(str(item.get('stage') or ''))}</td>"
                f"<td>{html.escape(str(item.get('status') or ''))}</td>"
                f"<td>{html.escape(', '.join(item.get('reason_codes') or []))}</td>"
                f"<td>{html.escape(', '.join(item.get('risk_ids') or []))}</td>"
                "</tr>"
            )
        return (
            "<h2>Dynamic check coverage</h2>"
            "<p class='muted'>Selected checks are content-specific; not_applicable and unavailable do not mean passed.</p>"
            "<table><tr><th>Check</th><th>Stage</th><th>Status</th><th>Reason</th><th>Risks</th></tr>"
            + "".join(rows) + "</table>"
        )

    def _parser_rows() -> str:
        if not parser_diags:
            return "<tr><td colspan='2'><em>No parser diagnostics.</em></td></tr>"
        return "".join(
            f"<tr><td><code>{html.escape(x['code'])}</code></td>"
            f"<td>{html.escape(x['message'])}</td></tr>"
            for x in parser_diags
        )

    def _owasp_rows() -> str:
        if not owasp:
            return ""
        parts = ["<tr><th>Category</th><th>Title</th><th>Coverage</th><th>Rules</th></tr>"]
        for code, info in owasp.items():
            parts.append(
                "<tr>"
                f"<td>{html.escape(code)}</td>"
                f"<td>{html.escape(info['title'])}</td>"
                f"<td>{html.escape(info['status'])}</td>"
                f"<td>{html.escape(', '.join(info['rules']) or '(none)')}</td>"
                "</tr>"
            )
        return "".join(parts)

    owasp_block = (
        f"\n<h2>OWASP AST10 coverage</h2>"
        f"<p class='muted'>Only categories with declared deterministic rules "
        f"are shown as <code>partial</code>; the rest are honest "
        f"<code>none</code>. Verity never claims full coverage of any "
        f"OWASP category.</p>"
        f"<table>{_owasp_rows()}</table>"
    ) if owasp else ""

    parser_block = (
        f"\n<h2>Manifest parser</h2>"
        f"<table><tr><th>Code</th><th>Message</th></tr>{_parser_rows()}</table>"
    ) if d.get("engine") == "skill" else ""

    # Secret-scanner block
    am2 = d.get("artifactModel") or {}
    gr_view = am2.get("gitleaksRun") or {}
    br_view = am2.get("banditRun") or {}

    def _tool_row(name, view):
        if not view:
            return ""
        return (
            "<tr>"
            f"<td>{html.escape(name)}</td>"
            f"<td>{html.escape(str(view.get('status') or ''))}</td>"
            f"<td>{html.escape(str(view.get('toolVersion') or ''))}</td>"
            f"<td>{html.escape(str(view.get('reasonCode') or ''))}</td>"
            "</tr>"
        )

    analyzers_block = (
        "\n<h2>Analyzers</h2>"
        "<table><tr><th>Analyzer</th><th>Status</th><th>Version</th><th>Reason / notes</th></tr>"
        f"{_tool_row('bandit', br_view)}"
        f"{_tool_row('gitleaks', gr_view)}"
        "</table>"
        + (
            "<p class='muted'><strong>Secret coverage note.</strong> gitleaks was "
            "not run in this review. Do not read the absence of Secret "
            "findings as evidence that no secret is present.</p>"
            if (gr_view.get("status") or "") != "completed" else ""
        )
    ) if d.get("engine") == "skill" else ""

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta http-equiv="Content-Security-Policy" content="{_CSP}">
<title>Verity Report</title>
<style>
  body {{ font-family: -apple-system, sans-serif; margin: 2rem; color:#222 }}
  .banner {{ padding: 1rem; border-radius: 6px; margin-bottom: 1rem; }}
  .banner.ok {{ background:#e6f6ea; border:1px solid #6c6; }}
  .banner.warn {{ background:#fff3cd; border:1px solid #d6b656; }}
  .banner.bad {{ background:#fde2e2; border:1px solid #c96; }}
  table {{ border-collapse: collapse; width:100%; margin-bottom:1.5rem }}
  th, td {{ border:1px solid #ccc; padding:.4rem .6rem; text-align:left; font-size:.9rem }}
  th {{ background:#f5f5f5 }}
  code {{ background:#f5f5f5; padding:.1rem .3rem; border-radius:3px }}
  .muted {{ color:#666 }}
  .ev {{ margin: .1rem 0; font-size:.85rem }}
  .guidance {{ font-size:.85rem; max-width: 380px }}
  .guidance .g-title {{ margin-bottom: .2rem }}
  .guidance .g-prio {{ background:#eef; border:1px solid #99b; padding:.05rem .3rem; border-radius: 999px; font-size:.75rem }}
  .guidance .g-why {{ color:#333; margin-bottom: .2rem }}
  .guidance .g-actions {{ margin: .2rem 0 0 1.2rem; padding: 0 }}
  .issue-list {{ display:grid; gap:.75rem; margin-bottom:1.5rem }}
  .issue-card {{ border:1px solid #ccc; border-left:4px solid #667; padding:.75rem 1rem; border-radius:6px }}
  .issue-card h3 {{ margin:.1rem 0 .4rem }}
</style></head>
<body>
<h1>Verity Report</h1>
<div class="banner {banner_kind}"><strong>{html.escape(banner_msg)}</strong></div>

<h2>Verdict</h2>
<p>Coverage: <code>{html.escape(verdict['coverage'])}</code>
   &nbsp;Engine: <code>{html.escape(d['engine'])}</code>
   &nbsp;Prompt kind: <code>{html.escape(str(d['snapshot'].get('promptKind') or 'n/a'))}</code>
   &nbsp;Snapshot: <code>{html.escape(d['snapshot']['snapshotId'])}</code>
</p>
<p class="muted">V1 engineering preview: this report covers only checks that actually completed. Controlled semantic review is attempted by default when a Provider is configured (experimental, unevaluated accuracy). V1.5 Prompt black-box evaluation remains an explicit caller-controlled opt-in. The current product release does not execute a reviewed Skill: any explicit V2 request fails closed with <code>sandbox_isolation_hardening_required</code> until the isolation boundary is hardened. A numeric score is not a safety guarantee.</p>

{_score_block()}
{_issues_block()}
{_dynamic_plan_block()}
{_remediation_block()}

<h2>Raw layer findings</h2>
<p class="muted">Severity notes: <code>low</code> = context-dependent risk marker; <code>medium</code> = quality/consistency risk with bounded evidence; <code>high</code>/<code>critical</code> = blocking risk under Verity-owned policy. Check <code>sourceLayer</code>: L0 is deterministic; L1 is a confirmed controlled-semantic assessment and is not described as mechanical proof.</p>
<table><tr><th>Severity</th><th>Type</th><th>Guidance</th><th>Claim</th><th>Origin</th><th>Path</th><th>Evidence</th></tr>
{_findings_rows()}
</table>

<h2>Coverage</h2>
<p>Status: <code>{html.escape(coverage['status'])}</code></p>
<p>Critical gaps: <code>{html.escape(json.dumps(critical_gaps))}</code></p>
<p>Reason codes: <code>{html.escape(json.dumps(reason_codes))}</code></p>

<h2>Executions</h2>
<table><tr><th>Plan item</th><th>Status</th><th>Reason</th></tr>
{_exec_rows()}
</table>

<h2>Reason codes (verdict)</h2>
<code>{html.escape(json.dumps(verdict['reasonCodes']))}</code>
{parser_block}
{analyzers_block}
{owasp_block}

</body></html>
"""
