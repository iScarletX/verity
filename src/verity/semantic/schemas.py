"""Strict output schemas for Candidate Generator and Validator payloads.

Both schemas are Draft 2020-12 with ``additionalProperties: false`` so
that unknown fields cause a rejection at the schema-validation step.
"""

from __future__ import annotations

from typing import Any, Dict


CANDIDATE_LIST_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["candidates"],
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": 64,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["proposedCandidateId", "findingType",
                              "subject", "claim", "evidenceIds"],
                "properties": {
                    # Providers propose an id; Verity re-derives the real
                    # candidateId. This field is only useful for the
                    # provider's own reference.
                    "proposedCandidateId": {"type": "string", "maxLength": 128},
                    "findingType": {"type": "string", "maxLength": 128},
                    "subject": {"type": "object"},
                    "claim": {"type": "string", "maxLength": 400},
                    "evidenceIds": {"type": "array",
                                     "items": {"type": "string", "maxLength": 128},
                                     "maxItems": 16},
                    "confidence": {"type": "number",
                                    "minimum": 0.0, "maximum": 1.0},
                },
            },
        },
    },
}


VALIDATION_RESULT_SCHEMA: Dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["candidateId", "decision", "reasonCodes"],
    "properties": {
        "candidateId": {"type": "string", "maxLength": 128},
        "decision": {"enum": ["confirmed", "rejected", "insufficient_evidence"]},
        "reasonCodes": {
            "type": "array",
            "items": {
                "enum": [
                    "evidence_supports_claim",
                    "evidence_contradicts_claim",
                    "candidate_out_of_scope",
                    "not_enough_evidence",
                    "candidate_claim_unclear",
                    "candidate_shape_invalid",
                    "biased_evidence_selection",
                    "insufficient_context",
                ],
            },
            "maxItems": 8,
        },
        "rationale": {"type": "string", "maxLength": 400},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
}


# Allowed subject-field enum values for the semantic catalog.  The
# orchestrator loads these dynamically from ``semantic.catalog`` — they
# are not hard-coded here.
