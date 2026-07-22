"""Verity — local, read-only Prompt & Skill Auditor.

V1 engineering preview. Implemented engineering paths are acceptance-tested,
but the V1 release decision remains ``not_ready`` until the quality-evidence
blockers in ``evals/reports/v1-closure.json`` are resolved.

Explicit scope (see README):
- V1 does NOT execute skills, install their dependencies, or start unknown
  services. Controlled semantic review is default-OFF and explicit opt-in.
- V1.5 Prompt black-box evaluation and V2 isolated Skill sandbox are not
  implemented.
"""

__version__ = "0.1.0"
CANONICAL_FINGERPRINT_SPEC_VERSION = "1"
LOCATION_SCHEMA_VERSION = "1"
CANONICALIZATION_VERSION = "1"
