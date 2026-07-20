# AGENTS.md — canonical rules for any AI agent working on Verity

> This file is the **single source of truth for Agent behaviour** in
> this repository. It is model-agnostic. Any model (Claude, GPT,
> Gemini, an open-source model, a scripted worker) that opens a session
> in this repo must follow the rules below.
>
> If any other file in the repo appears to contradict this one, this
> file wins. Fix the other file — do not silently deviate.

Last stable revision: `AGENTS-v1` (round 9 handover).

---

## 0. About Verity

Verity is a local, read-only, static auditor for LLM Prompts and Agent
Skills. See `README.md` for the product introduction and how a user
runs it. Do NOT re-explain the product here.

---

## 1. Single Source of Truth (SSOT) map

Every fact about Verity lives in exactly one place. Everywhere else,
link — do not copy easily-drifting numbers.

| Fact                                  | Authoritative file                          |
|---------------------------------------|---------------------------------------------|
| What Verity is; how a user launches   | `README.md`                                 |
| **Rules for agents (this file)**      | `AGENTS.md`                                 |
| Current runtime state / test count    | `docs/CURRENT_STATE.md`                     |
| Chronological history of each round   | `docs/PROGRESS.md` (append-only)            |
| Active plan (this round or next)      | `plans/ACTIVE.md`                           |
| Archived plans                        | `plans/archive/*.md`                        |
| One-page architecture map             | `docs/ARCHITECTURE.md`                      |
| Engineering spec (authoritative)      | `docs/spec/ENGINEERING_SPEC-v0.3.md`        |
| Mature-project reuse decisions        | `docs/spec/REUSE_DECISIONS-v0.2.md`         |
| Known pitfalls / lessons              | `docs/LESSONS.md`                           |
| Public collaboration preferences      | `docs/COLLABORATION.md`                     |
| Evaluation suite (tests as eval)      | `evals/README.md`                           |
| Machine acceptance gate               | `tools/verify_repo.py`                      |
| CI gate                               | `.github/workflows/ci.yml`                  |
| Session-onboarding steps              | `docs/SESSION_START.md`                     |
| Standard handover prompts             | `docs/SESSION_START.md` (\u00a7 handover prompts) |

**Git commits are the authoritative record of code changes.** No
document can override commit history.

**Never write** into any of these files: production secrets, real API
keys or tokens, personal contact information, private chat logs, host
absolute paths (see §5 for the exact prefixes `verify_repo.py`
refuses), or any content that would be embarrassing to publish.
This repository is public.

---

## 2. Session Start (hard flow)

Before making any change:

1. Read `docs/SESSION_START.md` in full, and every file it points at.
2. Run `python3 tools/verify_repo.py`. It must pass (`exit 0`) before
   you touch anything.  If it fails, that is the first thing you fix.
3. Read the "verified_against_commit" block in
   `docs/CURRENT_STATE.md`. If the current HEAD is descended from that
   commit and the recorded test count matches ``pytest -q``, the state
   file is accurate.
4. Read `plans/ACTIVE.md`. That is your scope. Do not silently expand
   it.
5. Read the relevant sections of `docs/LESSONS.md` — several previous
   pitfalls have specific "how to prevent recurrence" recipes.
6. In your first message back to the user, briefly:
   - restate what you understood the task to be, and what is out of
     scope;
   - list the file(s) you plan to change;
   - stop and wait for the user to confirm before doing large work.

---

## 3. Session End (hard flow)

Before committing:

1. Run the full test suite: `python3 -m pytest`. Every test must pass
   (skipped tests are fine when the reason is documented).
2. Run `python3 tools/verify_repo.py`.
3. Update `docs/CURRENT_STATE.md`:
   - test counts and the last-verified block
   - the capability matrix if any capability status changed
   - any new blockers or next-step notes
4. Append a `docs/PROGRESS.md` entry for the round: what was actually
   done, what was NOT done, evidence link, commit hash after commit.
5. Update `plans/ACTIVE.md` (either mark the round done and archive it,
   or roll forward to the next active plan).
6. Add a `docs/LESSONS.md` entry if anything surprising happened.
7. Commit. Write a message that reads like a change log for a stranger.
8. Push. Verify CI ran on GitHub. If CI failed, fix or roll back.

---

## 4. Phase gates (correct wording)

These gates describe when Verity is **allowed** to take a given
action, not permanent bans. They protect the reviewed artifact and
the reviewing environment.

**Static path (V1, default):**
- Verity may parse and pattern-match the artifact under review.
- Verity MUST NOT execute the reviewed skill's code.
- Verity MUST NOT install the reviewed skill's dependencies.
- Verity MUST NOT contact any external LLM Provider on this path.

**Controlled semantic review (V1, experimental):**
- May call an external LLM Provider only when ALL of the following
  are true: the user explicitly enables it (`--semantic` / Web
  opt-in), the Provider config comes from a trusted source (not from
  the reviewed artifact), a non-`off` egress policy is chosen, the
  strict JSON schemas + payload audit + budget gates are enforced,
  and the deterministic pipeline path is unaffected.
- **No real Provider is bundled in this repo.** Opting in without a
  Provider honestly returns `provider_not_configured` — never a
  silent success.

**V1.5 — Prompt black-box (planned, NOT implemented):**
- May run a paste-in prompt against a model only when the user
  explicitly starts a black-box run and supplies a test set, a target
  model, a budget, and a recording location.
- Not implemented in this repo. Reports show `promptBlackbox:
  not_implemented`. Do not claim otherwise.

**V2 — Skill sandbox (planned, NOT implemented):**
- May execute a reviewed skill only inside a one-shot isolated
  sandbox with fake credentials, default-off / controlled network,
  cpu / memory / wall-clock limits, and reliable destruction after
  the run.
- Not implemented in this repo. Reports show `skillSandbox:
  not_implemented`. Do not claim otherwise.

If an agent believes a phase gate should change, propose the change
in `plans/ACTIVE.md` and wait for a human decision. Do not "quickly
enable" a gate to unblock work.

---

## 5. Data-safety rules (public repo)

- Never commit a real API key, real password, real token, real
  private URL, or any user's real chat history.
- Test fixtures for secret scanners must use tokens that are
  visibly synthetic (e.g. `VERITY_FAKE_...`) AND assembled at
  runtime with string concatenation, so pattern-matchers on public
  hosting do not misclassify them (see `docs/LESSONS.md`).
- Never commit host absolute paths. `verify_repo.py` scans docs
  for the exact host-path prefixes it forbids (macOS user-home,
  the `private` realpath prefix, and Verity's tmp-dir prefix).
  See `tools/verify_repo.py::_looks_like_secret_literal` and
  ``check_no_absolute_paths_in_docs`` for the exact patterns.
- Reports emitted at runtime already redact host paths and secrets;
  do not weaken that behaviour.
- If in doubt, do not commit; ask the user.

---

## 6. Prohibited actions

- Rewriting `docs/PROGRESS.md` history. It is append-only. If a past
  round is inaccurate, add a follow-up entry that corrects the
  record; do not edit the original.
- Silently expanding scope beyond `plans/ACTIVE.md`.
- Marking a capability `completed` when tests do not yet cover it
  end-to-end. If the coverage is partial, say so.
- Auto-installing / auto-modifying anything on the user's machine
  beyond the repo (no `sudo`, no writing to `~`, no changing global
  git config, no writing PATH entries).
- Leaving background processes (uvicorn, gitleaks, etc.) running
  after your session ends.
- Copying a large chunk of `docs/spec/*` into another doc, thereby
  duplicating the spec.

---

## 7. Testing and verification

- Every non-trivial change ships with a test that exercises the
  change or documents why one cannot be written.
- `python3 -m pytest` is the authoritative test runner. If a test
  requires an environment-specific asset (e.g. real gitleaks
  binary), it should skip gracefully outside that environment; the
  skip must be justified in the test.
- `python3 tools/verify_repo.py` is the authoritative repo gate. If
  you cannot pass it, do not commit — fix the failure first, or
  amend `verify_repo.py` explicitly (a rare event that requires a
  test of its own).
- `tools/verify_repo.py --require-clean` is the CI-mode gate; it
  also asserts a clean working tree. Local iterative development
  does not require this flag.

---

## 8. Documentation drift protection

- `docs/CURRENT_STATE.md` contains a `verified_against` YAML block.
  The commit stored there must be an ancestor of `HEAD` at
  verification time (or equal). This deliberately avoids the
  self-reference problem of writing "the current commit is X" while
  producing that very commit.
- `README.md` must not carry easily-drifting numbers such as "277
  tests"; link to `docs/CURRENT_STATE.md` for exact counts.
- `docs/PROGRESS.md` records what a round shipped as of the commit
  named at the bottom of that round's entry. If the state changes
  later, a new round entry supersedes it (never rewrite).

---

## 9. When Verity itself audits an artifact

The reviewed artifact is UNTRUSTED input to Verity, always. It:
- must not choose Provider endpoints,
- must not supply model ids,
- must not decide egress policy,
- must not turn phase gates on or off,
- must not appear unredacted in reports, SARIF, or LLM payloads.

Any code path that lets the artifact influence the reviewing
environment is a spec violation and must be fixed with a test.

---

## 10. Where to look next

- New agent onboarding: `docs/SESSION_START.md`
- What's the codebase actually doing right now:
  `docs/CURRENT_STATE.md` → `docs/ARCHITECTURE.md`
- What ships to users: `README.md`
- Copy-paste handover prompts (for the human):
  `docs/SESSION_START.md#handover-prompts`
