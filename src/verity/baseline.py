"""Baseline matching (cross-snapshot).

STRICTLY separated from single-run exact dedup (§5.2 vs §10).
Uses subjectKey / findingOccurrenceFingerprint / supersedes for match tiers.
Coverage-insufficient cases MUST NOT be marked 'resolved'; instead they
become 'unknown_due_to_coverage' (§10.2).
"""

from __future__ import annotations

import uuid
from typing import List

from .models import Finding, FindingMatchRecord, CoverageAssessment


def compare(previous: List[Finding], current: List[Finding],
            *, previous_snapshot_id: str, current_snapshot_id: str,
            baseline_scope_id: str,
            current_coverage: CoverageAssessment) -> List[FindingMatchRecord]:
    prev_by_fp = {f.findingOccurrenceFingerprint: f for f in previous}
    prev_by_sk = {f.subjectKey: f for f in previous}
    cur_by_fp = {f.findingOccurrenceFingerprint: f for f in current}
    matched_prev: set[str] = set()

    records: List[FindingMatchRecord] = []
    # exact occurrence match
    for fp, cur in cur_by_fp.items():
        if fp in prev_by_fp:
            prev = prev_by_fp[fp]
            matched_prev.add(prev.findingId)
            records.append(FindingMatchRecord(
                findingMatchId=f"m-{uuid.uuid4().hex[:12]}",
                baselineScopeId=baseline_scope_id,
                previousSnapshotId=previous_snapshot_id,
                currentSnapshotId=current_snapshot_id,
                previousFindingIds=[prev.findingId],
                currentFindingIds=[cur.findingId],
                state="existing", method="exact",
            ))
            continue
        if cur.subjectKey in prev_by_sk and prev_by_sk[cur.subjectKey].findingId not in matched_prev:
            prev = prev_by_sk[cur.subjectKey]
            matched_prev.add(prev.findingId)
            records.append(FindingMatchRecord(
                findingMatchId=f"m-{uuid.uuid4().hex[:12]}",
                baselineScopeId=baseline_scope_id,
                previousSnapshotId=previous_snapshot_id,
                currentSnapshotId=current_snapshot_id,
                previousFindingIds=[prev.findingId],
                currentFindingIds=[cur.findingId],
                state="changed", method="stable_subject",
            ))
            continue
        records.append(FindingMatchRecord(
            findingMatchId=f"m-{uuid.uuid4().hex[:12]}",
            baselineScopeId=baseline_scope_id,
            previousSnapshotId=previous_snapshot_id,
            currentSnapshotId=current_snapshot_id,
            previousFindingIds=[],
            currentFindingIds=[cur.findingId],
            state="new", method="exact",
        ))

    # unmatched previous -> resolved OR unknown_due_to_coverage
    coverage_ok = current_coverage.status == "sufficient"
    for prev in previous:
        if prev.findingId in matched_prev:
            continue
        state = "resolved" if coverage_ok else "unknown_due_to_coverage"
        records.append(FindingMatchRecord(
            findingMatchId=f"m-{uuid.uuid4().hex[:12]}",
            baselineScopeId=baseline_scope_id,
            previousSnapshotId=previous_snapshot_id,
            currentSnapshotId=current_snapshot_id,
            previousFindingIds=[prev.findingId],
            currentFindingIds=[],
            state=state, method="exact",
            reasonCodes=[] if coverage_ok else ["coverage_insufficient"],
        ))
    return records
