"""Semantic-review configuration (default OFF).

Configuration is a plain immutable object. It comes from the caller
(CLI / Web MVP settings / an app that embeds Verity) and NEVER from the
artifact under review. The reviewed prompt or skill cannot flip
``enabled``, change ``base_url``, override ``model``, choose the egress
policy or supply headers.

The `ProviderCredentials` type never contains the raw API-key value;
callers supply an ``api_key_env`` NAME and the concrete secret is
resolved from ``os.environ`` at call time. This keeps API keys out of
serialised config, out of ReviewInputs, and out of any report.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Tuple
from urllib.parse import urlsplit


# --- Egress policy ---------------------------------------------------

EgressPolicy = Literal["off", "metadata_only", "redacted_evidence"]

EGRESS_POLICIES: Tuple[str, ...] = ("off", "metadata_only", "redacted_evidence")


# --- Credentials -----------------------------------------------------

@dataclass(frozen=True)
class ProviderCredentials:
    """Reference to a provider's credentials WITHOUT the secret value.

    ``api_key_env`` is the environment-variable name from which the
    concrete key will be read at call time; ``None`` means the provider
    does not need an API key (e.g. a local mock).
    """
    api_key_env: Optional[str] = None

    def __post_init__(self) -> None:
        if self.api_key_env and not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]{0,127}", self.api_key_env):
            raise ValueError("api_key_env must be a valid environment-variable name")

    def resolve(self) -> Optional[str]:
        if not self.api_key_env:
            return None
        return os.environ.get(self.api_key_env)


# --- Provider config -------------------------------------------------

@dataclass(frozen=True)
class ProviderConfig:
    """Trusted per-role provider configuration.

    - ``role``: ``candidate_generator`` or ``validator``. The two roles
      are always instantiated as separate provider objects even when
      pointing at the same underlying model, so that Candidate and
      Validator sides can never share state via a shared client.
    - ``base_url``: only ``https://`` URLs and localhost are allowed.
      Enforced at construction time.
    """
    role: Literal["candidate_generator", "validator"]
    provider_id: str
    model_id: str
    base_url: str = ""
    credentials: ProviderCredentials = field(default_factory=ProviderCredentials)
    timeout_seconds: float = 30.0
    max_request_bytes: int = 200 * 1024
    max_response_bytes: int = 128 * 1024

    def __post_init__(self) -> None:
        if self.role not in ("candidate_generator", "validator"):
            raise ValueError(f"unknown role: {self.role!r}")
        if not self.provider_id or len(self.provider_id) > 80:
            raise ValueError("provider_id is required and must be at most 80 characters")
        if not self.model_id or len(self.model_id) > 200:
            raise ValueError("model_id is required and must be at most 200 characters")
        if not (0 < self.timeout_seconds <= 120):
            raise ValueError("timeout_seconds must be in (0, 120]")
        if not (1024 <= self.max_request_bytes <= 2 * 1024 * 1024):
            raise ValueError("max_request_bytes must be between 1 KiB and 2 MiB")
        if not (1024 <= self.max_response_bytes <= 2 * 1024 * 1024):
            raise ValueError("max_response_bytes must be between 1 KiB and 2 MiB")
        if self.base_url:
            u = self.base_url.strip()
            parsed = urlsplit(u)
            if (parsed.username or parsed.password or parsed.query or parsed.fragment
                    or not parsed.hostname):
                raise ValueError(
                    "provider base_url must not contain credentials, query, or fragment")
            if parsed.scheme == "https":
                pass
            elif parsed.scheme == "http" and parsed.hostname in {
                    "127.0.0.1", "localhost", "::1"}:
                pass
            else:
                raise ValueError(
                    "provider base_url must be https:// or a loopback http URL")
            if u.endswith("/"):
                object.__setattr__(self, "base_url", u.rstrip("/"))


# --- Budget ---------------------------------------------------------

@dataclass(frozen=True)
class SemanticBudget:
    """Hard limits per Review. Reaching any of these ends semantic
    processing with an explicit reason code; deterministic results are
    unaffected."""
    max_candidate_generation_calls: int = 4
    max_validation_calls_per_candidate: int = 1
    max_total_validation_calls: int = 32
    max_candidates_per_extractor: int = 20
    max_candidates_total: int = 64
    max_evidence_per_candidate: int = 8


# --- SemanticConfig -------------------------------------------------

@dataclass(frozen=True)
class SemanticConfig:
    """Top-level knob. ``enabled=False`` (default) means the semantic
    plan items are recorded as ``not_requested_by_profile`` and no
    Provider is even instantiated.
    """
    enabled: bool = False
    egress_policy: EgressPolicy = "off"
    provider_config: Dict[str, ProviderConfig] = field(default_factory=dict)
    budget: SemanticBudget = field(default_factory=SemanticBudget)
    # Which semantic FindingTypes to attempt. Empty = every registered
    # entry from the semantic catalog.
    enabled_finding_types: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.egress_policy not in EGRESS_POLICIES:
            raise ValueError(
                f"unknown egress_policy: {self.egress_policy!r}. "
                f"Expected one of {EGRESS_POLICIES}.")
        # raw_full_artifact is intentionally not implemented in this round.
        # Enabling semantic without a compatible egress policy is a config
        # error, not a silent no-op:
        if self.enabled and self.egress_policy == "off":
            raise ValueError(
                "SemanticConfig.enabled=True requires egress_policy != 'off'")

    def has_provider(self, role: str) -> bool:
        return role in self.provider_config


SEMANTIC_DEFAULT = SemanticConfig()
