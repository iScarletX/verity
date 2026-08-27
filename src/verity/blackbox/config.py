"""Trusted, explicit opt-in configuration for the V1.5 Prompt black-box stage.

Mirrors the discipline of ``verity.semantic.config``: a plain immutable
object that comes ONLY from the caller (CLI flags / an embedding app's
trusted config surface) and NEVER from the reviewed artifact. The
reviewed prompt cannot flip ``enabled``, change ``base_url``/``model_id``,
or supply its own credentials.

``BlackboxConfig.enabled`` defaults to ``False`` -- unlike
``SemanticConfig`` (which defaults ``enabled=True`` and is attempted
whenever the caller wires *any* config in), the black-box stage sends
real probe traffic to a real model on the caller's behalf and must stay
inert unless the caller explicitly turns it on. ``review.run_review``
enforces a second, independent gate on top of this: the stage only runs
when ``ReviewInputs.blackbox_config`` is not ``None`` AND
``config.enabled`` is ``True``. Passing a default-constructed
``BlackboxConfig()`` is therefore a safe no-op.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional, Tuple
from urllib.parse import urlsplit


@dataclass(frozen=True)
class BlackboxCredentials:
    """Reference to an API key WITHOUT the secret value.

    ``api_key_env`` is the environment-variable name the concrete key is
    read from at call time; the key itself never appears in this object,
    in ``ReviewInputs``, or in any report.
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


@dataclass(frozen=True)
class BlackboxConfig:
    """Trusted configuration for one black-box run against one reviewed
    prompt. See module docstring for the opt-in discipline.

    ``scenario_policy`` controls how scenarios are selected. The default,
    ``artifact_aware``, delegates selection to the reviewed artifact's
    behavior profile. ``all`` preserves the historical research mode, and
    ``explicit`` runs only ``scenario_ids``. Supplying any ``scenario_ids``
    makes the effective policy explicit.
    """

    enabled: bool = False
    base_url: str = ""
    model_id: str = ""
    credentials: BlackboxCredentials = field(default_factory=BlackboxCredentials)
    scenario_policy: str = "artifact_aware"
    scenario_ids: Tuple[str, ...] = ()
    max_calls: int = 50
    timeout_seconds: float = 30.0
    max_tokens_per_response: int = 800

    def __post_init__(self) -> None:
        if self.scenario_policy not in {"artifact_aware", "all", "explicit"}:
            raise ValueError(
                "scenario_policy must be artifact_aware, all, or explicit")
        if self.scenario_ids:
            object.__setattr__(self, "scenario_policy", "explicit")
        elif self.scenario_policy == "explicit":
            raise ValueError("explicit scenario_policy requires scenario_ids")
        if not (0 < self.max_calls <= 500):
            raise ValueError("max_calls must be in (0, 500]")
        if not (0 < self.timeout_seconds <= 120):
            raise ValueError("timeout_seconds must be in (0, 120]")
        if not (1 <= self.max_tokens_per_response <= 4000):
            raise ValueError("max_tokens_per_response must be in [1, 4000]")
        if len(self.scenario_ids) > 64:
            raise ValueError("scenario_ids must have at most 64 entries")
        if any(len(s) > 100 for s in self.scenario_ids):
            raise ValueError("each scenario_id must be at most 100 characters")
        if self.base_url:
            u = self.base_url.strip()
            parsed = urlsplit(u)
            if (parsed.username or parsed.password or parsed.query or parsed.fragment
                    or not parsed.hostname):
                raise ValueError(
                    "base_url must not contain credentials, query, or fragment")
            if parsed.scheme == "https":
                pass
            elif parsed.scheme == "http" and parsed.hostname in {
                    "127.0.0.1", "localhost", "::1"}:
                pass
            else:
                raise ValueError(
                    "base_url must be https:// or a loopback http URL")
            if u.endswith("/"):
                object.__setattr__(self, "base_url", u.rstrip("/"))

    def is_provider_configured(self) -> bool:
        return bool(self.base_url and self.model_id)


BLACKBOX_DEFAULT = BlackboxConfig()
