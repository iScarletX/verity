# Round 17 — Taxonomy-driven semantic detection breadth

## Status

- Product scope approved: **yes**
- Implementation: not started; begins only after Round-16 CI is green
- Owner: main maintainer agent
- Stop condition: after Round 17, do not start Provider production work;
  return to maintainer for a new decision.

## Objective

Expand the controlled L1 semantic catalog from three pipeline examples into a
standards/taxonomy-driven set of reviewable semantic risks. Use deterministic
seed evidence and Round-16 capability facts. Do not let a model invent risk
classes, severity, identity, evidence, or assessment policy.

## Priority semantic families

1. Prompt instruction consistency beyond adjacent-line pairs.
2. Prompt output/error/verification contract adequacy.
3. Prompt trusted-instruction vs untrusted-content boundary clarity.
4. Prompt task-to-tool necessity and excessive agency.
5. Skill declared behavior vs deterministic capability facts.
6. Skill permission/capability mismatch and undeclared network/process/
   credential/file access.
7. External instruction/tool-content trust and provenance gaps.

A risk remains L0/V1.5/V2-only if reading static evidence cannot support a
falsifiable semantic conclusion.

## Required catalog contract per semantic Finding Type

- stable FindingType and Round-14 risk mapping;
- object scope and policy severity;
- controlled Subject schema and identity fields;
- deterministic extractor with bounded, allowlisted Evidence;
- falsification question written to disprove the claim;
- required evidence kinds and minimum evidence sufficiency;
- confirmed/rejected fixed Provider replay pair;
- English and Chinese/mixed-language coverage where meaningful;
- explicit known blind spots and no model-authored category/severity.

## Evaluation boundary

- Expand fixed replay/contract corpus first.
- Add adversarial responses: missing evidence, injected category/severity,
  forged ids, refusal, invalid JSON, unstable output.
- Fixed replays measure only pipeline contracts and keep
  `modelQualityMeasured=false`.
- No taxonomy risk may become `substantial`/`evaluated` from replay results.
- Real model precision/recall remains unavailable until a future explicitly
  approved Provider evaluation round.

## Acceptance

- Every new semantic type maps to an existing risk and passes standards drift.
- Deterministic Findings remain byte-for-byte/identity invariant under every
  semantic case.
- Evidence payloads contain no secrets, absolute paths, raw full artifacts or
  Provider credentials.
- Candidate and Validator roles remain physically separate.
- Unsupported risks remain unsupported; no semantic catch-all.
- Full pytest, corpus reproduction, `verify_repo.py --require-clean`, push and
  green CI.

## Explicit non-goals

- Real Provider/OpenRouter/API-key/model calls or Web configuration.
- Default semantic enablement or egress-policy changes.
- Additional static tools beyond Round-16 decisions.
- V1.5 Prompt black-box execution.
- V2 Skill execution/sandbox.
- Round 18 or any later work without a new maintainer decision.
