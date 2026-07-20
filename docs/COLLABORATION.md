# COLLABORATION — public, safe preferences

This file records how the maintainer prefers to collaborate with any
AI agent, in a form that is safe to publish. Anything private — real
name, credentials, private chats, personal contact info — MUST NOT be
added here.

## Working style

- The maintainer is a "vibe-coding" non-engineer end-user. Explain
  trade-offs in plain language before asking for a decision; do not
  hand-wave technical terms.
- One decision at a time. If two things need to be decided, ask the
  more consequential one first and pause.
- Do not present a menu of three "options" hoping the maintainer will
  pick one. Recommend the specific option you would ship, and
  explain the alternative only if there is a real reason.
- The maintainer independently verifies every round. Do not
  optimise for the appearance of progress; optimise for verification
  reliability.

## Scope discipline

- Work is round-based. `plans/ACTIVE.md` names the current round.
  A round has a scope, a set of gates, an acceptance test, and a
  place to record what was actually shipped.
- Do not silently expand a round. If the request cannot be met inside
  the round scope, say so and stop, do not "just also fix X while I
  am here".
- Every round must end with the maintainer being able to say "yes /
  no" without reading code.

## Deliverables

- Every round ships tests that would have failed before this round.
- Every round updates `docs/CURRENT_STATE.md` and appends to
  `docs/PROGRESS.md`.
- Every round leaves the repo pushable to `main` and green on CI.

## The three lines the maintainer will always ask about

- "Did the tests actually pass?" — link to the pytest output.
- "Did `verify_repo.py` pass?" — link to the exit code / summary.
- "Does the story on the front page still match the reality?" —
  `docs/CURRENT_STATE.md` is the answer.

## What the roadmap is NOT

- Verity's roadmap is V1 → V1.5 → V2, not V1 → V2. V1.5 prompt
  black-box is a distinct feature from V2 skill sandbox. Do not
  quietly conflate the two.
- V2 is a sandbox, not "let the LLM run the skill". Never suggest
  running a reviewed skill outside a sandbox.

## Prohibited in this file

- Real names, email, phone, chat platform handles, cloud account
  ids, project names not related to Verity, private URLs.
- Any content that would be embarrassing to publish on the public
  GitHub repo. If in doubt, do not commit and ask.
