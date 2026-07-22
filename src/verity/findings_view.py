"""Read-only unified projection of completed Findings for consumers.

The deterministic engine remains physically isolated from semantic code. This
module operates only on the already-built report dict: deterministic Findings
are always present; semantic Findings are included only when the semantic stage
completed successfully. Rejected, pending, insufficient or failed candidates
never appear here.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple


def source_layer(finding: Dict[str, Any]) -> str:
    """Return the controlled product layer for one completed Finding."""
    origin = finding.get("origin") or {}
    return ("L1_semantic" if origin.get("kind") == "semantic_validation"
            else "L0_static")


def completed_findings(review_dict: Dict[str, Any]
                       ) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    findings = list(review_dict.get("findings") or [])
    evidence = {e.get("evidenceId"): e
                for e in (review_dict.get("evidences") or [])
                if e.get("evidenceId")}
    semantic = review_dict.get("semantic") or {}
    if semantic.get("status") == "completed":
        findings.extend(semantic.get("findings") or [])
        for item in semantic.get("evidences") or []:
            eid = item.get("evidenceId")
            if eid:
                evidence.setdefault(eid, item)
    return findings, evidence
