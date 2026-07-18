"""Verity — Prompt & Skill Auditor.

Phase 0 core contracts + minimal vertical walking skeleton (V1, read-only).

Explicit scope (see README):
- V1 is static, read-only. Does NOT execute skills, install dependencies, or
  start unknown services.
- V1.5 (prompt black-box eval) and V2 (isolated skill sandbox with fake
  files / fake credentials / controlled network) are deliberately NOT
  implemented in this phase.
"""

__version__ = "0.0.1"
CANONICAL_FINGERPRINT_SPEC_VERSION = "1"
LOCATION_SCHEMA_VERSION = "1"
CANONICALIZATION_VERSION = "1"
