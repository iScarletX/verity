# Archived plans

Round-by-round planning was not part of the workflow before Round 9.
The historical rounds are recorded in `docs/PROGRESS.md` (append-only)
against the commits that shipped them; there is no attempt to
retroactively reconstruct a formal "plan" file for Rounds 1–8.

Starting with Round 9, every round's plan is drafted in
`../ACTIVE.md`. When a round ships, the `ACTIVE.md` file is moved into
this directory under the name `round-<N>-<slug>.md`. The commit that
made the move is the authoritative history of "which plan was
active".

Do not fabricate archived plans for rounds that predate this
convention.
