"""Convert redacted gitleaks results into Verity Evidence / Finding.

The runner has already dropped the raw Secret/Match/Line values before
this module sees anything. This adapter therefore:

- creates a **secret-sensitivity** Evidence: the occurrenceFingerprint
  path used for these records excludes original bytes (spec §5.1);
- attaches only rule id, byte range (derived from staged line number),
  gitleaks description (capped), length bucket, entropy (if numeric);
- sets a fixed redactedPreview ("[gitleaks:<ruleId>]") so no secret
  fragment leaks.

Identity: ``subject = (artifactPath, gitleaksRuleId, lineNumber)``.
"""

from __future__ import annotations

from typing import Dict, List

from .engine import RuleContext, RuleHit, make_source_span_evidence
from .models import Producer


def gitleaks_result_to_hits(ctx: RuleContext) -> List[RuleHit]:
    gr = (ctx.artifact_model or {}).get("gitleaksRun")
    if not gr or gr.get("status") != "completed":
        return []
    path_map: Dict[str, str] = gr.get("pathMap") or {}
    results = gr.get("results") or []
    if not results:
        return []
    files_by_id = {f.fileId: f for f in ctx.snapshot.files}
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)

    hits: List[RuleHit] = []
    for r in results:
        staged_path = r.get("file")
        file_id = path_map.get(staged_path)
        if file_id is None:
            continue
        f = files_by_id.get(file_id)
        if f is None:
            continue
        data = ctx.file_bytes.get(file_id, b"")
        ln = r.get("startLine") or 1
        end_ln = r.get("endLine") or ln
        start, end = _line_range_to_byte_range(data, int(ln), int(end_ln))
        rule_id = r.get("ruleID") or "unknown"
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id=file_id, artifact_path=f.normalizedPath,
            file_digest=f.contentDigest or "",
            byte_range=(start, end),
            raw_bytes=b"",                           # sensitive path: no raw hash
            producer=prod,
            sensitivity="secret",
            redacted_preview=f"[gitleaks:{rule_id}]",
            evidence_kind_tag=f"gitleaks:{rule_id}",
        )
        subject = {
            "artifactPath": f.normalizedPath,
            "gitleaksRuleId": rule_id,
            "lineNumber": int(ln),
            "secretLengthBucket": r.get("secretLengthBucket") or "0",
        }
        # entropy is optional metadata; kept only if numeric.
        if isinstance(r.get("entropy"), (int, float)):
            subject["entropy"] = float(r["entropy"])
        hits.append(RuleHit(evidences=[ev], subject=subject))
    return hits


def _line_range_to_byte_range(data: bytes, start_line: int, end_line: int):
    if start_line < 1:
        start_line = 1
    if end_line < start_line:
        end_line = start_line
    lines = data.splitlines(keepends=True)
    if start_line > len(lines):
        start_line = len(lines) or 1
    if end_line > len(lines):
        end_line = len(lines) or 1
    start = sum(len(l) for l in lines[:start_line - 1])
    end = sum(len(l) for l in lines[:end_line])
    if end <= start:
        end = start + (len(lines[start_line - 1]) if lines else 0)
    return start, end
