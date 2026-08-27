"""V1.5 Prompt black-box evaluation layer.

This module runs a reviewed prompt against a real model under a set of
controlled attack/probe scenarios and records whether the model's outputs
exhibit the vulnerabilities the static/semantic layers flagged or suspected.

Design principles
-----------------
- **Explicit opt-in only.** Black-box runs never start automatically. The
  caller must supply a ``BlackboxConfig`` (see ``config.py``) with
  ``enabled=True``, a base URL/model, credentials env-var name, and a
  call budget. Since Round 74, ``review.run_review`` MAY invoke this
  stage -- but only when the caller passes a non-``None``,
  ``enabled=True`` ``ReviewInputs.blackbox_config``. Without that
  explicit config, the default review path never touches this module
  (``ReviewInputs.blackbox_config`` defaults to ``None``, and
  ``BlackboxConfig()`` itself defaults ``enabled=False``) -- two
  independent gates, so no accidental wiring can turn this on.
- **Physical isolation from the reviewed artifact path.** The static/semantic
  engine path never contacts a model; this module adds a strictly separate
  execution track. The reviewed prompt is the *target* of the black-box test,
  never the source of provider configuration.
- **Bounded and recorded.** Every outbound request goes through the same
  ProviderConfig/budget machinery already used in the semantic layer. Every
  call and its response digest are recorded before the session ends.
- **Honest reporting.** Results report observed pass/fail rates per scenario
  with exact counts. No aggregate "safety score" is derived; the caller decides
  what the numbers mean for their risk tolerance.
"""

from .config import BLACKBOX_DEFAULT, BlackboxConfig, BlackboxCredentials

__all__ = ["BlackboxConfig", "BlackboxCredentials", "BLACKBOX_DEFAULT"]
