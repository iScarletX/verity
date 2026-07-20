# MEMORY — Verity collaboration preferences

Public-safe project preferences. Do NOT add anything that would be
embarrassing to publish on a public GitHub repo — no real names,
credentials, private URLs, or personal chat logs.

## Working style

- The maintainer is a non-engineer end user. Explain trade-offs in
  plain language and recommend a specific option; do not present a
  menu of three "options" hoping the maintainer will choose.
- One decision at a time. If two decisions are needed, ask the more
  consequential one first and pause.
- The maintainer independently verifies every round. Optimise for
  verification reliability, not the appearance of progress.

## Scope discipline

- Work is round-based. `plans/ACTIVE.md` names the current round.
  Do not silently expand it. If a change is needed outside scope,
  say so and stop.
- Every round ends with the maintainer being able to say "yes/no"
  without reading code.

## Deliverables per round

- Tests that would have failed before this round (or an explicit
  reason none can be written).
- `docs/PROGRESS.md` top summary refreshed + a new append-only
  history entry.
- Repository green on CI. `verify_repo.py --require-clean` passes.

## The three lines the maintainer always checks

- "Did the tests actually pass?" — pytest output.
- "Did `verify_repo.py` pass?" — exit code / summary.
- "Does the front-page story still match reality?" — the top
  summary block in `docs/PROGRESS.md` is the answer.

## Roadmap discipline

- Verity's roadmap is V1 → V1.5 → V2, in that order. V1.5 (prompt
  black-box) and V2 (skill sandbox) are distinct features; do not
  quietly conflate them.
- V2 is a sandbox, not "let the LLM run the skill". Never suggest
  running a reviewed skill outside a sandbox.

## Prohibited in this file

- Real names, emails, phone numbers, chat handles, cloud account
  ids, private URLs, or content unrelated to Verity.
