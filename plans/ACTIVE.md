# Round 9 — Handover system + machine gates (active plan)

## Goals

- Ship a canonical, single-source-of-truth handover system so any AI
  agent (or human) can pick up the project from cold and be
  productive without asking the maintainer to re-explain context.
- Introduce a machine acceptance gate (`tools/verify_repo.py`) and a
  GitHub Actions CI workflow so "the round is done" is a computable
  claim, not a subjective one.
- Do NOT expand product surface: no Provider, no black-box, no
  sandbox, no new rules.

## Non-goals

- Real LLM Provider client.
- Any change to Prompt / Skill rule catalog.
- Any change to Bandit / gitleaks version pins.
- Any change to CLI exit-code policy or SARIF shape.
- Retroactive rewriting of `docs/PROGRESS.md` history.

## Gates that must hold before starting

- Round 8.1 committed at `aedbeb7` and pushed to `origin/main`.
- 277 tests passing locally (measured).
- Working tree clean.

## Plan

1. Create canonical handover docs (see `AGENTS.md §1` SSOT map).
2. Copy `docs/spec/*` snapshots of the engineering spec + reuse
   decisions.
3. Add `tools/verify_repo.py` (offline, read-only, exit-code-clean,
   with its own tests).
4. Add `.github/workflows/ci.yml` that installs the pinned deps,
   installs gitleaks 8.28.0 with SHA-256 verification, and runs
   `verify_repo.py --require-clean`.
5. Add a `.githooks/pre-push` sample plus opt-in instructions (not
   an automatic install; hooks must be enabled explicitly).
6. Update `README.md` to link to `CURRENT_STATE` for exact counts,
   and to `AGENTS.md` / `SESSION_START.md` for onboarding.
7. Update `docs/PROGRESS.md` (append-only) with a round-9 entry.
8. Push and confirm CI ran.

## Acceptance

- `python3 -m pytest` → all pass.
- `python3 tools/verify_repo.py` → PASS.
- `python3 tools/verify_repo.py --require-clean` → PASS after commit.
- `.github/workflows/ci.yml` parses under `yaml.safe_load` and
  declares `permissions: { contents: read }` at minimum.
- GitHub Actions run on the push is green (or, if unable to observe,
  the URL is reported to the maintainer).
- `docs/CURRENT_STATE.md`, `docs/PROGRESS.md`, `plans/ACTIVE.md` all
  updated in the same commit set.

## Risks

- CI's Linux runner has different gitleaks assets than the local
  darwin-arm64 install. Fix: `tools/install_gitleaks.py` selects the
  right asset per platform and verifies the SHA that appears in the
  release descriptor. Tests around the local install must gracefully
  skip if the darwin-arm64 install is not present on the CI runner.
- Documents can go out of sync with reality. Fix: `verify_repo.py`
  and CI make drift a build failure.

## Status

- Started: 2026-07-20
- Ended: (fill in at commit time)
- Commit(s): (fill in)
- Test count moved from 277 to (fill in)

## After this round

The next active plan is undecided. Do NOT begin real-Provider
integration, black-box runner, or Skill sandbox without an explicit
new round description from the maintainer. Draft a proposal in a
`plans/proposal-<slug>.md` file first.
