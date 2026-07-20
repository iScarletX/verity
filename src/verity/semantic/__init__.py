"""Semantic review scaffolding (V1, experimental, default OFF).

This subpackage implements the ``Evidence → SemanticCandidate → Validator
→ CandidateAssessment → semantic Finding`` chain from Verity spec v0.3.

Bright-line architectural rules (enforced by tests and by convention):

- **Semantic never modifies deterministic results.** It only reads a
  deterministic Review projection. The deterministic engine
  (``verity/engine.py``, ``verity/skill_rules.py``, ``verity/parser.py``)
  MUST NOT import anything from ``verity.semantic``.
- **Provider config never comes from the reviewed artifact.** Only from
  CLI arguments, environment variables (``VERITY_SEMANTIC_*``), or a
  trusted app config surface. The reviewed skill / prompt cannot change
  base URL, model id, headers, system prompt, or API key.
- **Default off.** Without an explicit ``SemanticConfig`` the orchestrator
  runs in ``off`` mode and every semantic plan item is recorded as
  ``not_requested_by_profile``; no Provider is instantiated.
- **Egress policies:** ``off``, ``metadata_only``, ``redacted_evidence``.
  ``raw_full_artifact`` is intentionally NOT implemented in this round.
- **Payload audit:** every outbound request records only sizes, field
  names and SHA-256 of the serialised payload; the payload itself is
  never persisted.
- **Validator containment (\u00a77.2 spec):** Validator sees ONE candidate
  and a whitelisted subset of Evidence; its output can only confirm /
  reject / declare insufficient_evidence, never introduce a new
  candidate, new Finding, new severity, or new evidence reference.
"""

from .config import (SemanticConfig, EgressPolicy, ProviderCredentials,
                     SEMANTIC_DEFAULT)
from .orchestrator import SemanticOrchestrator, SemanticRunResult
from .provider import CandidateGeneratorProvider, ValidatorProvider

__all__ = [
    "SemanticConfig", "EgressPolicy", "ProviderCredentials", "SEMANTIC_DEFAULT",
    "SemanticOrchestrator", "SemanticRunResult",
    "CandidateGeneratorProvider", "ValidatorProvider",
]
