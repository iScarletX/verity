"""Verity — local Prompt & Skill Auditor.

The deterministic engineering-preview scope is a release candidate; optional
semantic and dynamic stages remain experimental.

Explicit scope (see README):
- The default review path is read-only and does not execute Skills, install
  dependencies, start unknown services, or make Provider calls.
- Controlled semantic review is attempted by CLI/Web only when a trusted
  Provider is configured; its evaluated-accuracy track is not release-ready.
- Prompt black-box is integrated as an explicit opt-in. Skill execution is
  unavailable on supported product paths until V2 isolation is hardened; an
  explicit request fails closed without constructing the research runner. The
  separate Agent-instruction runtime is CLI-only and OFF by default. Reviewed
  artifacts cannot enable or configure these stages.
"""

__version__ = "0.1.0"
CANONICAL_FINGERPRINT_SPEC_VERSION = "1"
LOCATION_SCHEMA_VERSION = "1"
CANONICALIZATION_VERSION = "1"
