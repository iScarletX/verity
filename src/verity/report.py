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
    if review.artifactModel:
        # Do not leak raw YAML — only compact fields needed for the report.
        am = review.artifactModel
        br = am.get("banditRun") or {}
        gr = am.get("gitleaksRun") or {}
        d["artifactModel"] = {
            "hasSkillMd": am.get("hasSkillMd"),
            "manifestFile": am.get("manifestFile"),
            "manifest": am.get("manifest"),
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
    # Capability matrix (static / semantic / runtime).  Static is
    # always driven by deterministic results; semantic reflects the
    # optional sub-pipeline; runtime (prompt black-box + skill sandbox)
    # is intentionally NOT implemented in V1.
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
    d["capabilities"] = {
        "static": {"status": static_status,
                    "note": ("execution status only; current detection breadth "
                             "is signal/partial in the standards taxonomy")},
        "semantic": {"status": semantic_status,
                      "note": ("experimental, default OFF; execution status "
                               "does not imply semantic breadth")},
        "promptBlackbox": {"status": "not_implemented",
                            "note": "V1.5 planned; not part of V1"},
        "skillSandbox": {"status": "not_implemented",
                           "note": "V2 planned; not part of V1"},
    }
    return d


def compute_verdict(review: Review) -> Dict[str, Any]:
    """Dual-axis product verdict.

    - subject decision only produced if coverage is 'sufficient' AND no
      high/critical deterministic findings.
    - If coverage is insufficient, subject decision is not emitted — we
      refuse to say "ready" / "low_detected_risk".
    - High/Critical findings are ALWAYS reported (§16), independent of
      coverage state.
    """
    has_high = any(f.severity in ("high", "critical") for f in review.findings)
    coverage_status = review.coverage.status
    reason_codes = []
    subject = None
    if coverage_status == "sufficient":
        if review.engine == "prompt":
            subject = {
                "engine": "prompt",
                "outcome": "needs_revision" if has_high or review.findings else "ready",
            }
        else:
            if has_high:
                subject = {"engine": "skill", "outcome": "do_not_install"}
                reason_codes.append("high_or_critical_finding_present")
            elif review.findings:
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
        "policyId": "verdict-policy-v1", "policyVersion": "1",
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
    findings = d["findings"]
    coverage = d["coverage"]
    executions = d["executions"]

    banner_kind = "warn"
    # If the Skill manifest parser failed, mark coverage-insufficient in
    # the banner explicitly — even if all other rules ran.
    parser_failed = any(
        e["status"] == "failed" and e["planItemId"] == "pi-parser-manifest"
        for e in d["executions"]
    )
    if verdict["coverage"] != "sufficient":
        banner_msg = "COVERAGE INSUFFICIENT — uncovered checks are NOT the same as no findings."
        banner_kind = "warn"
    else:
        subj = verdict["subject"] or {}
        outcome = subj.get("outcome", "unknown")
        if outcome in ("do_not_install", "needs_revision"):
            banner_msg = f"Subject outcome: {outcome.upper()} — do not use as-is."
            banner_kind = "bad"
        elif outcome in ("review_required",):
            banner_msg = "Subject outcome: REVIEW REQUIRED — human review needed before use."
            banner_kind = "warn"
        else:
            banner_msg = f"Subject outcome: {outcome.upper()} (no known findings; not a safety guarantee)."
            banner_kind = "ok"

    # Build a lookup so findings can render every evidence they cite.
    ev_by_id = {e["evidenceId"]: e for e in d["evidences"]}

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
<p class="muted">This report is generated by the Phase 0 walking skeleton. V1 performs static, read-only checks only. V1.5 (prompt black-box eval) and V2 (isolated skill sandbox) are NOT implemented and are explicit later phases.</p>

<h2>Findings</h2>
<p class="muted">Severity notes: <code>low</code> = risk marker (context-dependent, may be benign quotation); <code>medium</code> = quality/consistency issue with precise evidence; <code>high</code>/<code>critical</code> = mechanically proven policy violation.</p>
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
