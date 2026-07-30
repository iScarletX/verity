"""Independent multi-model review for `provisional_single_review` Corpus
labels — a route separate from `blind_review.py`'s frozen 54-item packet.

`blind_review.py::_source_items` is deliberately locked to the already-
completed independent-review round (docstring: "reproduces the frozen,
already-completed independent-review round, not a moving target"). This
module targets a DIFFERENT, disjoint set: L0 and semantic-quality cases
still carrying `labelStatus == "provisional_single_review"`. It reuses
`blind_review`'s safe content/anonymity helpers but never touches or
extends the locked 54-item set, and never writes into the frozen 54-item
evidence trail.

Design, confirmed by a real-model pilot before this module was written: a
single cheap model (e.g. gpt-4o-mini) can be systematically wrong on
trust-boundary-shaped risks even with an explicit judgment rubric in the
prompt, while a different model (e.g. claude-3-haiku) gets the same case
right. This module therefore REQUIRES at least 2 independently configured
`label_reviewer` Providers (different models) and takes a majority vote
per case, mirroring the project's existing odd-repetition consensus
discipline in `semantic_benchmark.py::_independent_review_consensus` (which
is binary present/absent; this module is three-state present/absent/
uncertain, so it does not reuse that function directly).

Reviewed artifacts are read as text only and are never executed. The
Provider(s) never see the current corpus label, another reviewer's vote, or
repository/case-id context.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .blind_review import _risk_projection, _safe_content_files
from .corpus import CorpusError, load_manifest
from .semantic.provider import ProviderCall
from .semantic_quality import load_semantic_quality_manifest
from .standards import load_risks


PROVISIONAL_REVIEW_PROTOCOL_VERSION = "1.0.0"
DECISIONS = {"present", "absent", "uncertain"}
MIN_REVIEWERS = 2
MAX_REVIEWERS = 5

_INSTRUCTIONS = {
    "question": (
        "Decide whether the targetRisk (defined by title + definition + "
        "reviewBoundary) is PRESENT in the artifact. present = the artifact "
        "exhibits exactly the condition in `definition` with no explicit "
        "mitigating statement anywhere in the text (e.g. no trust-boundary "
        "declaration, no anti-injection-override rule, no least-privilege "
        "scoping). absent = the artifact clearly does NOT exhibit that "
        "condition, or it does but explicitly declares a mitigating "
        "boundary/rule. uncertain = genuinely cannot tell from the text "
        "alone."
    ),
    "decisions": sorted(DECISIONS),
    "independence": (
        "Do not seek Verity outputs, the current Corpus label, another "
        "reviewer's decision, or repository context. Judge only the "
        "artifact text against the stated risk definition."
    ),
    "requiredOutput": (
        'Return strict JSON: {"assessment": "present"|"absent"|"uncertain", '
        '"evidence": "short quote from the artifact", '
        '"reason": "one sentence"}'
    ),
}


@dataclass
class ProvisionalCaseRef:
    caseId: str
    sourceClass: str          # "l0" | "semantic_quality_non_test"
    objectType: str
    language: str
    promptKind: Optional[str]
    riskId: str
    targetRisk: Dict[str, Any]
    content: str
    authorDecision: str       # "present" | "absent", from the existing label


def list_provisional_cases() -> List[ProvisionalCaseRef]:
    """Every case still `provisional_single_review` in L0 or semantic-quality
    manifests. Disjoint from `blind_review._source_items()`'s frozen 54."""
    risks = load_risks()
    out: List[ProvisionalCaseRef] = []
    for case in load_manifest()["cases"]:
        if case["labelStatus"] != "provisional_single_review":
            continue
        risk_id = case["assessedRiskIds"][0]
        _root, files = _safe_content_files(case["path"])
        out.append(ProvisionalCaseRef(
            caseId=case["caseId"], sourceClass="l0",
            objectType=case["objectType"], language=case["language"],
            promptKind=case.get("promptKind"), riskId=risk_id,
            targetRisk=_risk_projection(risks[risk_id]),
            content=files[0][1],
            authorDecision=(
                "present" if risk_id in case["expectedRiskIds"] else "absent"),
        ))
    for case in load_semantic_quality_manifest()["cases"]:
        if case["labelStatus"] != "provisional_single_review":
            continue
        if case["split"] == "test":
            continue  # sealed test split is never touched by this module
        risk_id = case["riskId"]
        _root, files = _safe_content_files(case["path"])
        out.append(ProvisionalCaseRef(
            caseId=case["caseId"], sourceClass="semantic_quality_non_test",
            objectType=case["objectType"], language=case["language"],
            promptKind=case.get("promptKind"), riskId=risk_id,
            targetRisk=_risk_projection(
                risks[risk_id], semantic_type=case["findingType"]),
            content=files[0][1],
            authorDecision=(
                "present" if case["expectedAssessment"] == "confirmed"
                else "absent"),
        ))
    return out


def _build_request(case: ProvisionalCaseRef) -> Dict[str, Any]:
    item = {
        "itemId": case.caseId,
        "objectType": case.objectType,
        "language": case.language,
        "targetRisk": case.targetRisk,
    }
    if case.objectType == "prompt":
        item["promptKind"] = case.promptKind
    item["artifact"] = {
        "displayRootName": None,
        "files": [{"path": "prompt.txt", "content": case.content}],
    }
    return {"item": item, "reviewProtocol": _INSTRUCTIONS}


@dataclass
class ReviewVote:
    reviewerLabel: str         # caller-chosen identifier, e.g. model id
    state: str                 # present | absent | uncertain | call_failed
    evidence: Optional[str] = None
    reason: Optional[str] = None
    reasonCode: Optional[str] = None   # set only when state == call_failed


@dataclass
class CaseReviewResult:
    caseId: str
    riskId: str
    authorDecision: str
    votes: List[ReviewVote] = field(default_factory=list)
    consensus: Optional[str] = None    # present | absent | uncertain | None
    agreesWithAuthor: Optional[bool] = None


def _parse_vote(reviewer_label: str, payload: Any) -> ReviewVote:
    if not isinstance(payload, dict):
        return ReviewVote(reviewer_label, "call_failed",
                          reasonCode="malformed_payload")
    assessment = payload.get("assessment")
    if assessment not in DECISIONS:
        return ReviewVote(reviewer_label, "call_failed",
                          reasonCode="invalid_assessment_enum")
    return ReviewVote(
        reviewer_label, assessment,
        evidence=str(payload.get("evidence") or "")[:240],
        reason=str(payload.get("reason") or "")[:500],
    )


def _aggregate(votes: List[ReviewVote]) -> Optional[str]:
    """Majority vote over decisive votes only (call_failed never counts,
    mirroring the "provider errors don't vote" rule already established for
    semantic Validator aggregation). No strict majority -> None (undecided),
    never guessed either way."""
    decisive = [v for v in votes if v.state in DECISIONS]
    if not decisive:
        return None
    counts: Dict[str, int] = {}
    for v in decisive:
        counts[v.state] = counts.get(v.state, 0) + 1
    winner, top = max(counts.items(), key=lambda kv: kv[1])
    runner_up = max((c for s, c in counts.items() if s != winner), default=0)
    if top <= runner_up:
        return None
    return winner


def run_provisional_review(
    cases: List[ProvisionalCaseRef], *, reviewers: List[Tuple[str, Any]],
) -> List[CaseReviewResult]:
    """``reviewers`` is a list of (label, provider) pairs, each provider
    exposing ``review_label(call=..., request=...) -> ProviderResponse``
    with role ``label_reviewer``. Requires >=2 reviewers (different models),
    per this module's design rationale in its docstring."""
    if not (MIN_REVIEWERS <= len(reviewers) <= MAX_REVIEWERS):
        raise CorpusError(
            f"provisional review requires {MIN_REVIEWERS}..{MAX_REVIEWERS} "
            "independently configured reviewers")
    results: List[CaseReviewResult] = []
    for case in cases:
        request = _build_request(case)
        raw = json.dumps(request, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
        votes: List[ReviewVote] = []
        for label, provider in reviewers:
            call = ProviderCall(
                review_id="provisional-review",
                egress_policy="redacted_evidence",
                call_role="label_reviewer",
                call_id=f"provisional-{case.caseId}-{label}",
                request_bytes=len(raw),
                request_digest_sha256=hashlib.sha256(raw).hexdigest(),
            )
            try:
                resp = provider.review_label(call=call, request=request)
            except Exception as exc:  # pragma: no cover
                votes.append(ReviewVote(
                    label, "call_failed",
                    reasonCode=f"provider_raised:{type(exc).__name__}"))
                continue
            if not resp.ok:
                votes.append(ReviewVote(
                    label, "call_failed", reasonCode=resp.reason_code))
                continue
            votes.append(_parse_vote(label, resp.payload))
        consensus = _aggregate(votes)
        results.append(CaseReviewResult(
            caseId=case.caseId, riskId=case.riskId,
            authorDecision=case.authorDecision, votes=votes,
            consensus=consensus,
            agreesWithAuthor=(
                (consensus == case.authorDecision) if consensus else None),
        ))
    return results
