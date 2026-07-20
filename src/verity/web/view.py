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


# The four top-level headlines the UI shows. Coverage insufficient wins.
_HEADLINES = {
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
        "detail": "本次静态检查发现中低危问题，建议人工过一遍再决定是否安装。",
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
        "title": "本次静态检查未发现阻断项",
        "detail": "已完成的检查未发现高危问题。仍请自行确认，静态检查不能替代运行时验证。",
        "tone": "ok",
    },
    "pass_prompt": {
        "code": "pass_prompt",
        "title": "本次静态检查未发现阻断项",
        "detail": "已完成的检查未发现高危问题；仍建议在真实使用前小范围试运行。",
        "tone": "ok",
    },
}


def headline_for(review_dict: Dict[str, Any]) -> Dict[str, str]:
    verdict = review_dict.get("verdict") or {}
    coverage = verdict.get("coverage") or "unknown"
    engine = review_dict.get("engine")
    if coverage != "sufficient":
        return _HEADLINES["coverage_block"]
    subject = verdict.get("subject") or {}
    outcome = subject.get("outcome") or ""
    has_high = any(
        f["severity"] in ("high", "critical")
        for f in review_dict.get("findings") or []
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


def _finding_view(f: Dict[str, Any], ev_by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
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
            })
    origin = f.get("origin") or {}
    return {
        "id": f["findingId"],
        "type": f["findingType"],
        "severity": f["severity"],
        "claim": f.get("claim", ""),
        "originKind": origin.get("kind", ""),
        "artifactPath": subject.get("artifactPath", ""),
        "controls": f.get("controls") or [],
        "evidences": evidences_view,
        # Subject fields are already schema-validated; we surface only the
        # scalar entries so the UI can render them as key/value chips.
        "subject": {k: v for k, v in subject.items()
                    if k not in ("artifactPath",)},
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


def build_view_model(review_dict: Dict[str, Any], review_id: str) -> Dict[str, Any]:
    ev_by_id = {e["evidenceId"]: e for e in review_dict.get("evidences") or []}
    findings = [_finding_view(f, ev_by_id)
                for f in review_dict.get("findings") or []]
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
        "findings": findings,
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
    }
