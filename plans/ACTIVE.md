# No active implementation round — Round 12 proposal pending approval

## Current position

Round 11 is complete and archived. Verity now has:

- L0 deterministic static Prompt/Skill review;
- controlled Bandit and gitleaks analyzers;
- JSON / HTML / SARIF reports and local Web MVP;
- an experimental L1 semantic pipeline with a bounded real
  JSON-over-HTTPS Provider transport, default OFF and CLI-configured.

V1.5 Prompt black-box evaluation and V2 Skill sandbox remain explicitly
`not_implemented`.

## Owner recommendation for Round 12

**Close the V1 product loop before starting V1.5:** add trusted review
history, Baseline/Diff, and explicit Disposition/Suppression behavior.

Why this is the next priority:

- `baseline.py` exists only as a core matcher; it is not exposed as a
  usable CLI/Web workflow and is not persisted.
- Users can inspect one review, but cannot reliably answer “what is new,
  existing, changed, resolved, or unknown because coverage regressed?”
- Safe Agent adoption later needs exactly this audit-history foundation:
  append-only review records, coverage-aware comparisons, and explicit
  human decisions. Runtime interception without trustworthy history would
  produce events but weak accountability.
- Starting Prompt black-box now would add another execution axis before
  the current V1 findings can be governed over time.

## Proposed goals

- Define a trusted local review-history store that is never controlled by
  the reviewed artifact.
- Persist only report-safe projections; do not persist raw secrets,
  Provider payloads, host absolute paths, or RedactionMap.
- Add coverage-aware Baseline/Diff for the same logical artifact and
  compatible profile/scope.
- Surface `new`, `existing`, `changed`, `resolved`, and
  `unknown_due_to_coverage`; insufficient coverage must never create a
  false `resolved` result.
- Add append-only Disposition records for acknowledge / accept risk /
  suppress / remove suppression, with reason, scope, policy, timestamp,
  and optional expiry.
- Keep Baseline matching separate from Suppression: an ambiguous or
  heuristic match must not silently carry suppression forward.
- Expose the workflow first through CLI and report JSON; add Web history
  only if the local storage and security contracts pass acceptance.

## Proposed non-goals

- Prompt black-box execution.
- Skill execution or sandboxing.
- Agent runtime interception.
- Cloud sync, multi-user service, account system, or remote database.
- Automatic remediation/PatchSet apply.
- Fuzzy semantic deletion/merging of Findings.

## Proposed acceptance

- Tests demonstrate all five baseline states, including coverage
  regression producing `unknown_due_to_coverage`, never `resolved`.
- Artifact-supplied baseline or suppression files cannot hide Findings.
- Dispositions are append-only, expire according to policy, and never
  rewrite original Findings.
- Baseline scope prevents cross-artifact or incompatible-profile matches.
- Secret/path leak tests cover on-disk history and exported reports.
- Existing 312 tests remain green; new behavior has end-to-end tests.
- Full pytest, `verify_repo.py --require-clean`, and GitHub CI pass.

## Stage gate

This is a proposal only. Do not modify product code for Round 12 until the
maintainer explicitly approves this scope. If approved, replace this file
with the full implementation plan before coding or delegation.

## Status

- Proposed: 2026-07-20
- Approval: pending
