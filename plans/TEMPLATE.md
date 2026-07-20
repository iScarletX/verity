# Round <N> — <one-line title>

## Goals

- ...

## Non-goals (out of scope, do not silently expand)

- ...

## Gates that MUST hold before starting

- Round <N-1> committed and green on CI.
- `docs/CURRENT_STATE.md` verified_against block matches an ancestor
  of `HEAD`.
- Every constraint in `AGENTS.md §4 Phase gates` is respected.

## Plan

- ...

## Acceptance

- All pytest tests pass locally.
- `python3 tools/verify_repo.py --require-clean` passes.
- GitHub Actions run for the push is green.
- `docs/CURRENT_STATE.md`, `docs/PROGRESS.md`, `plans/ACTIVE.md` all
  updated in the same round's commit.

## Risks

- ...

## Status

- Started: <date>
- Ended: <date>
- Commit(s): <hash>
- Test count moved from X to Y

## Deliverables

- Committed files:
  - ...
- Documented lessons (if any): `docs/LESSONS.md#<anchor>`
