"""Validate committed, scrubbed independent-review attestations."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


REPO_ROOT = Path(__file__).resolve().parents[2]
ATTESTATION_PATH = REPO_ROOT / "evals" / "reviews" / "corpus-v1-independent-ai-review.json"


class ReviewEvidenceError(ValueError):
    pass


def load_independent_ai_attestation(path: Path = ATTESTATION_PATH) -> Dict[str, Dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"),
                           object_pairs_hook=_no_duplicates)
    except ReviewEvidenceError:
        raise
    except Exception as exc:
        raise ReviewEvidenceError("cannot read independent review attestation") from exc
    top = {"schemaVersion", "protocolVersion", "reviewClass", "reviewedScope",
           "reviewProcess", "cases", "limitations"}
    if not isinstance(value, dict) or set(value) != top:
        raise ReviewEvidenceError("independent review attestation schema invalid")
    if (value.get("schemaVersion") != 1
            or value.get("protocolVersion") != "1.0.0"
            or value.get("reviewClass") != "independent_ai_review"):
        raise ReviewEvidenceError("independent review attestation identity invalid")
    scope = value.get("reviewedScope")
    if scope != {"l0CaseCount": 26, "semanticNonTestCaseCount": 28,
                 "sealedTestReviewed": False, "totalCaseCount": 54}:
        raise ReviewEvidenceError("independent review scope invalid")
    process = value.get("reviewProcess")
    if (not isinstance(process, dict)
            or process.get("validInitialReviewerCount") != 2
            or process.get("adjudicatedCaseCount") != 8
            or process.get("revisedCaseReReviewCount") != 2
            or process.get("invalidatedReviewerCount") != 1):
        raise ReviewEvidenceError("independent review process invalid")
    limitations = value.get("limitations")
    if (not isinstance(limitations, list) or not limitations
            or not all(isinstance(x, str) and x.strip() for x in limitations)):
        raise ReviewEvidenceError("independent review limitations invalid")
    cases = value.get("cases")
    if not isinstance(cases, list) or len(cases) != 54:
        raise ReviewEvidenceError("independent review case count invalid")
    result = {}
    for case in cases:
        if not isinstance(case, dict) or set(case) != {
                "caseId", "sourceClass", "payloadDigest", "finalDecision",
                "reviewStatus"}:
            raise ReviewEvidenceError("independent review case shape invalid")
        cid = case.get("caseId")
        if (not isinstance(cid, str) or not cid or cid in result
                or case.get("sourceClass") not in {"l0", "semantic_quality_non_test"}
                or case.get("finalDecision") not in {"present", "absent"}
                or case.get("reviewStatus") != "independent_ai_review"
                or not isinstance(case.get("payloadDigest"), str)
                or len(case["payloadDigest"]) != 64):
            raise ReviewEvidenceError("independent review case value invalid")
        result[cid] = case
    return result


def require_independent_ai_case(*, case_id: str, source_class: str,
                                payload_digest: str, expected_decision: str,
                                attestation: Dict[str, Dict[str, Any]]) -> None:
    item = attestation.get(case_id)
    if (not item or item["sourceClass"] != source_class
            or item["payloadDigest"] != payload_digest
            or item["finalDecision"] != expected_decision):
        raise ReviewEvidenceError(
            f"independent review evidence missing/stale for {case_id}")


def _no_duplicates(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ReviewEvidenceError("duplicate independent review key")
        out[key] = value
    return out
