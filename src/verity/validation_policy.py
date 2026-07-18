"""Validator containment contract (spec §7.2 §7.3).

V1 does NOT actually invoke an LLM validator; but the contract must exist
because tests exercise it: given a set of ValidationRecords for a candidate,
compute a CandidateAssessment, refusing to create new problems or replace
identity.

Enforced invariants:
- Only evaluates candidates already produced by a candidate generator.
- Rationale text is treated as OPAQUE display text; NOT parsed for new
  findings/candidates (see engine.py — no code path takes rationale as
  input to any parser).
- Any Schema-extra field in a validation payload marks it failed with
  SCHEMA_VIOLATION_EXTRA_FIELD.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Set

from .models import CandidateAssessment, SemanticCandidate, ValidationRecord


ALLOWED_VALIDATION_FIELDS: Set[str] = {
    "verdict", "rationale", "evidenceSufficiencyChallenge",
}
ALLOWED_ESC_FIELDS: Set[str] = {
    "challengeType", "missingContextDescription",
}


class ContainmentError(Exception):
    pass


def build_validation_record_from_payload(
    *, validation_id: str, candidate_id: str, execution_id: str,
    checked_evidence_ids: List[str], validator_id: str, validator_version: str,
    payload: Dict[str, Any],
) -> ValidationRecord:
    """Parse a raw validator payload while enforcing schema containment."""
    extra = set(payload.keys()) - ALLOWED_VALIDATION_FIELDS
    if extra:
        return ValidationRecord(
            validationId=validation_id, candidateId=candidate_id,
            executionId=execution_id, checkedEvidenceIds=checked_evidence_ids,
            validatorId=validator_id, validatorVersion=validator_version,
            status="failed", errorCode="SCHEMA_VIOLATION_EXTRA_FIELD",
        )
    esc = payload.get("evidenceSufficiencyChallenge")
    if esc is not None:
        if not isinstance(esc, dict) or (set(esc.keys()) - ALLOWED_ESC_FIELDS):
            return ValidationRecord(
                validationId=validation_id, candidateId=candidate_id,
                executionId=execution_id, checkedEvidenceIds=checked_evidence_ids,
                validatorId=validator_id, validatorVersion=validator_version,
                status="failed", errorCode="SCHEMA_VIOLATION_EXTRA_FIELD",
            )
    verdict = payload.get("verdict")
    if verdict not in ("confirmed", "rejected", "insufficient_evidence"):
        return ValidationRecord(
            validationId=validation_id, candidateId=candidate_id,
            executionId=execution_id, checkedEvidenceIds=checked_evidence_ids,
            validatorId=validator_id, validatorVersion=validator_version,
            status="failed", errorCode="SCHEMA_VIOLATION_VERDICT",
        )
    return ValidationRecord(
        validationId=validation_id, candidateId=candidate_id,
        executionId=execution_id, checkedEvidenceIds=checked_evidence_ids,
        validatorId=validator_id, validatorVersion=validator_version,
        status="completed", verdict=verdict, rationale=payload.get("rationale"),
        evidenceSufficiencyChallenge=esc,
    )


def assess_candidate(candidate: SemanticCandidate, records: List[ValidationRecord],
                     *, policy_id: str = "validation-policy-v1",
                     policy_version: str = "1") -> CandidateAssessment:
    """Aggregate records → assessment. Never creates new candidates.

    - all failed/cancelled → validation_failed
    - any confirmed and no rejected → confirmed
    - any rejected and no confirmed → rejected
    - any insufficient_evidence → insufficient_evidence
    - mixed confirmed+rejected → insufficient_evidence with reason
    """
    reason: List[str] = []
    completed = [r for r in records if r.status == "completed"]
    if not completed:
        state = "validation_failed"
        reason.append("no_completed_validations")
    else:
        verdicts = {r.verdict for r in completed}
        if any(r.evidenceSufficiencyChallenge for r in completed):
            reason.append("evidence_sufficiency_challenge_raised")
        if verdicts == {"confirmed"}:
            state = "confirmed"
        elif verdicts == {"rejected"}:
            state = "rejected"
        elif "insufficient_evidence" in verdicts or (
            "confirmed" in verdicts and "rejected" in verdicts):
            state = "insufficient_evidence"
        else:
            state = "confirmed" if "confirmed" in verdicts else "rejected"
    return CandidateAssessment(
        candidateAssessmentId=f"ca-{uuid.uuid4().hex[:12]}",
        candidateId=candidate.candidateId,
        validationPolicyId=policy_id, validationPolicyVersion=policy_version,
        validationIds=[r.validationId for r in records],
        state=state,  # type: ignore[arg-type]
        reasonCodes=reason,
    )
