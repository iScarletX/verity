"""Provider protocol.

Two distinct Protocols so that Candidate-generator and Validator can
never accidentally be the same object (types diverge on the request
payload types too).

The protocols stay transport-independent. ``http_provider.py`` supplies
the first bounded JSON-over-HTTPS implementation; tests may still inject
in-memory implementations that record every call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Protocol


@dataclass(frozen=True)
class ProviderCall:
    """Fully described provider call context (injected by the orchestrator).
    Contents are safe for the Provider implementation to log; sizes only,
    no secrets, no absolute paths.
    """
    review_id: str
    egress_policy: str
    call_role: Literal["candidate_generator", "validator"]
    call_id: str
    request_bytes: int
    request_digest_sha256: str


@dataclass(frozen=True)
class ProviderResponse:
    """Raw provider output BEFORE Verity's own schema validation.
    ``ok=False`` means the underlying provider raised or timed out; the
    orchestrator translates this into a ``failed`` semantic execution
    and never touches deterministic Findings.
    """
    ok: bool
    payload: Optional[Any] = None            # decoded JSON dict/list
    response_bytes: int = 0
    reason_code: Optional[str] = None
    duration_seconds: float = 0.0


class CandidateGeneratorProvider(Protocol):
    """Given a whitelisted evidence bundle, propose one or more candidates.

    The provider MUST NOT invent new Evidence; it can only reference
    evidence IDs that appear in the input bundle. The orchestrator will
    reject any candidate that references an unknown evidence id or that
    tries to fake a candidate identity.
    """

    def generate_candidates(
        self, *,
        call: ProviderCall,
        request: Dict[str, Any],
    ) -> ProviderResponse:
        ...


class ValidatorProvider(Protocol):
    """Given a SINGLE candidate + its evidence bundle, return confirm /
    reject / insufficient_evidence.

    The provider MUST NOT modify the candidate, change severity, invent
    new Evidence, or return a candidateId different from the one it was
    asked about.
    """

    def validate_candidate(
        self, *,
        call: ProviderCall,
        request: Dict[str, Any],
    ) -> ProviderResponse:
        ...
