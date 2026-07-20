# SESSION_START — new agent / new chat onboarding

Follow this list in order. Do not start writing code until every step
is done.

## Read (in this order)

1. `README.md` — what Verity is for the end user.
2. `AGENTS.md` — the rules for any AI agent (SSOT).
3. `docs/CURRENT_STATE.md` — what's actually shipped right now.
4. `docs/ARCHITECTURE.md` — one-page map of components.
5. `plans/ACTIVE.md` — what this round is supposed to accomplish.
6. `docs/LESSONS.md` — known pitfalls (skim titles; read anything
   related to the round you are about to touch).
7. The specific engineering spec section that governs the code you
   plan to change: `docs/spec/ENGINEERING_SPEC-v0.3.md`.

## Verify (before you touch anything)

```bash
# 1. tests must pass on the current tip
python3 -m pytest -q

# 2. repo self-check must pass
python3 tools/verify_repo.py
```

If either of these fails, stop and fix it. Do not build on a broken
baseline.

## Understand before you propose

In your first message back to the user, briefly:

- State what you understood the task to be, in your own words.
- List the files you plan to change.
- Call out anything that would expand scope beyond `plans/ACTIVE.md`.
- Ask the user to confirm before doing large work.

## Session end

Follow `AGENTS.md §3`:

1. Full pytest passes.
2. `verify_repo.py` (and, before pushing, `--require-clean`) passes.
3. `docs/CURRENT_STATE.md` updated (test counts + capability matrix if
   changed + verified_against block if you have a new baseline commit).
4. `docs/PROGRESS.md` gains a new round entry (append-only).
5. `plans/ACTIVE.md` updated or archived.
6. `docs/LESSONS.md` extended if anything surprising happened.
7. Commit with a change-log-style message.
8. Push. Confirm CI runs on GitHub.

---

## Handover prompts (canonical copy — use these verbatim)

The **long form** below is the one to give a new AI agent when starting
a session on Verity. It is intentionally self-contained so the user
does not need to explain the project again.

```text
You are joining an ongoing project called "Verity" — a local,
read-only static auditor for LLM Prompts and Agent Skills. The
repository is at /Users/sixiang/KianWorkspace/Verity (on my Mac) and
mirrored on GitHub at https://github.com/iScarletX/verity.

Before doing anything else:

1. Read `AGENTS.md` at the repo root. It is the single source of
   truth for how you should behave. Every other file about
   process (SESSION_START, LESSONS, etc.) is subordinate to it.
2. Read `docs/SESSION_START.md`, then follow the Session-Start
   flow in `AGENTS.md §2`.
3. Do not skip the pre-flight `python3 -m pytest` and
   `python3 tools/verify_repo.py`.
4. Before you touch anything, restate what you understood the task
   to be, list the files you plan to change, and wait for me to
   confirm.

Constraints that apply regardless of the task:
- Read-only static V1. No real LLM Provider is bundled; no Skill
  execution; no sandbox. Do not fake progress on V1.5 or V2.
- Public GitHub repo: never commit secrets, API keys, real
  credentials, private chat logs, or host absolute paths.
- Round-based development. If the task is not covered by
  `plans/ACTIVE.md`, propose it and wait.
- Independent verification: I check every round independently.
  "The task is done" means all tests pass, `verify_repo.py`
  passes, and CI on GitHub passes.
```

The **short form** for veteran chats is:

```text
Read AGENTS.md, then docs/SESSION_START.md, then run pytest and
tools/verify_repo.py. Restate the task and file list before you
change anything.
```

Do not maintain a second copy of these two prompts anywhere else in
the repo — link here.
