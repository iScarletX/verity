# Round 10 — Collapse handover set to minimal 8 files

## Goals

- Reduce the round-9 handover surface to the minimal set the
  maintainer approved: `AGENTS.md`, `docs/PROGRESS.md` (with a top
  summary block), `plans/ACTIVE.md` + `plans/archive/`, `docs/LESSONS.md`,
  `docs/MEMORY.md`, `docs/ARCHITECTURE.md`, `evals/README.md`,
  `tools/verify_repo.py` + `.github/workflows/ci.yml`.
- Preserve every substantive rule from round 9 (phase gates, SSOT
  concept, machine gate, drift protection) — only the file count
  changes.

## Non-goals

- Any product-surface change (no Provider, no black-box, no sandbox,
  no rule catalog change).
- Any change to `docs/PROGRESS.md` history entries below the new top
  summary block (append-only).

## Plan

- Delete: `CLAUDE.md`, `docs/SESSION_START.md`,
  `docs/CURRENT_STATE.md`, `docs/COLLABORATION.md`,
  `docs/spec/ENGINEERING_SPEC-v0.3.md`,
  `docs/spec/REUSE_DECISIONS-v0.2.md`, `docs/spec/`,
  `.githooks/README.md`, `.githooks/pre-push`, `.githooks/`,
  `plans/TEMPLATE.md`.
- Merge into `AGENTS.md`: Session-Start / Session-End flow and the
  standard handover prompts.
- Merge into `docs/PROGRESS.md`: `verified_against` block +
  capability matrix + short state summary, above the existing
  history (history remains append-only).
- New: `docs/MEMORY.md` with the public-safe collaboration
  preferences.
- Update `tools/verify_repo.py`: new REQUIRED_FILES list; move the
  verified-block reader to `docs/PROGRESS.md`; update
  capability-matrix check to read from the PROGRESS top block.
- Update `tests/test_verify_repo.py` to the new file names.
- Update `README.md` and `docs/ARCHITECTURE.md` links.

## Acceptance

- `python3 -m pytest` all pass (target count 288; the verify-repo
  tests are renamed, not increased or reduced).
- `python3 tools/verify_repo.py --require-clean` passes after the
  commit.
- GitHub CI green on the push.
- No leftover empty directories (`docs/spec`, `.githooks`).

## Risks

- Silent test-suite divergence during file rename; mitigated by
  running pytest after every intermediate edit.

## Status

- Started: 2026-07-20
- Ended: 2026-07-20
- Commit(s): `3451b3b`

## After this round

The next active plan was intentionally left undecided. Round 11 was
opened only after the maintainer explicitly authorised the project to
continue under a single accountable owner.
