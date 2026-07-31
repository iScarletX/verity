"""V1.5 Prompt black-box evaluation layer.

This module runs a reviewed prompt against a real model under a set of
controlled attack/probe scenarios and records whether the model's outputs
exhibit the vulnerabilities the static/semantic layers flagged or suspected.

Design principles
-----------------
- **Explicit opt-in only.** Black-box runs never start automatically. The caller
  must supply a test-set path, model configuration, spend/call budget, and
  output recording location. There is no implicit trigger from the main review
  pipeline.
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
