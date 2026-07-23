# AGENTS.md — canonical rules for any AI agent working on Verity

This file is **the** rulebook for any AI agent (Claude, GPT, Gemini,
scripted worker, whatever) that opens a session in this repository.
It is model-agnostic and short on purpose.

If any other file in the repo appears to contradict this one, this
file wins — fix the other file, do not silently deviate.

Last stable revision: `AGENTS-v2` (round 10, minimal handover set).

---

## 0. About Verity

**Mission (north star).** Verity is being built to become a
maximally-capable auditor that catches *all* the problems in an LLM
Prompt or Agent Skill — and, over time, to check *dynamic / execution*
behaviour (black-box prompt runs, sandboxed skill execution), and
ultimately to be **embedded into other agents** so it can vet things in
real time. Butler was the founder's earlier tool; Verity exists to
**surpass** it. Parity with Butler's findings is a floor, not a ceiling.

**Where we are now (V1).** Today Verity ships a *deterministic static*
core plus a separate *experimental* semantic path. "Deterministic,
offline, light-dependency" is the **current-phase discipline that keeps
V1 shippable and reproducible — it is NOT Verity's permanent identity or
its goal.** Growing toward the mission (a local specialist-model layer,
dynamic/black-box analyzers, an embeddable service) is expected and
on-roadmap, not a deviation.

**The one property that must survive every phase.** The core that
ingests the **untrusted reviewed artifact** must stay *physically
isolated* from any model/network/execution layer — an audit tool must
never let malicious reviewed content reach out to the network, a model,
or the host. This is not "static for its own sake"; it is basic auditor
safety. New capability is added by extending the existing
**isolated-adapter** pattern (Bandit and gitleaks already run as
controlled, out-of-process adapters) to model-based and
dynamic/execution analyzers — not by dissolving the isolation.

See `README.md` for what a user does with it. Do not restate the
product here.

---

## 1. Single Source of Truth (SSOT) map

Every fact about Verity lives in exactly one place.

| Fact                                  | Authoritative file            |
|---------------------------------------|-------------------------------|
| What Verity is / how to launch it     | `README.md`                   |
| **Rules for AI agents (this file)**   | `AGENTS.md`                   |
| Current state + history               | `docs/PROGRESS.md` (top summary + append-only history) |
| Active plan                           | `plans/ACTIVE.md`             |
| Archived plans                        | `plans/archive/*.md`          |
| One-page architecture map             | `docs/ARCHITECTURE.md`        |
| Known pitfalls                        | `docs/LESSONS.md` (append-only) |
| Project collaboration preferences     | `docs/MEMORY.md`              |
| Detection sources, taxonomy, breadth  | `standards/*.json`            |
| Evaluation suite (tests as eval)      | `evals/README.md`             |
| Machine acceptance gate               | `tools/verify_repo.py`        |
| CI gate                               | `.github/workflows/ci.yml`    |

Git commits are the authoritative record of *code* changes. No
document can override commit history.

**Never write** into this repo: production secrets, real API keys or
tokens, personal contact information, private chat logs, host absolute
paths, or anything embarrassing to publish. This repository is public.

---

## 2. Session Start (short flow)

Before touching anything:

1. Read `README.md`, this file, `docs/PROGRESS.md` (top summary),
   `plans/ACTIVE.md`, and any `docs/LESSONS.md` entry that concerns
   the area you plan to touch.
2. Run the two gates:
   ```bash
   python3 -m pytest -q
   python3 tools/verify_repo.py
   ```
   Both must return `0` before you make changes. If either fails,
   that's the first thing you fix.
3. In your first reply to the user, briefly restate the task in your
   own words, list the files you plan to touch, note anything that
   would exceed `plans/ACTIVE.md`, and wait for confirmation before
   large work.

## 3. Session End (short flow)

Before committing:

1. Full test suite passes: `python3 -m pytest`.
2. `python3 tools/verify_repo.py --require-clean` passes (CI mode).
3. Update `docs/PROGRESS.md`:
   - refresh the top summary (state, test count, next step,
     `verified_against` commit — the parent of the commit you are
     about to make, since a doc cannot know its own future commit
     hash).
   - append a new history entry for this round.
4. Update `plans/ACTIVE.md` — either mark the round done and move it
   to `plans/archive/`, or roll forward to the next active plan.
5. Add a `docs/LESSONS.md` entry if anything surprising happened.
6. Commit with a change-log-style message. Push. Confirm CI is green.

---

## 4. Phase gates (correct wording)

These are conditional gates that stage capability safely. They protect
the reviewed artifact and the reviewing environment — they are **NOT
permanent bans, and they are NOT a statement that Verity is meant to
stay static.** Each later phase is on the roadmap (see §0); a phase is
"not implemented" only until it is built under its own safety
conditions, not forbidden. "Do not fake progress" below means: don't
*claim* a phase works before it does — it does not mean don't build it.

**Static (V1 default).** Verity may parse and pattern-match the
artifact. On this default path it MUST NOT execute the reviewed skill's
code, install its dependencies, or contact an external LLM Provider.
Other phases below relax specific parts of this under explicit,
audited conditions.

**Controlled semantic (V1 experimental).** Verity may call an
external LLM Provider **only** when: the user explicitly enables it
(`--semantic` on the CLI, or the loopback-only Web Provider-config
surface where the user pastes a trusted base URL + key and picks
models), the Provider config comes from a trusted source (never from
the reviewed artifact), a non-`off` egress policy is chosen, the JSON
schemas + payload audit + budget gates are enforced, and the
deterministic pipeline path is unaffected. A bounded JSON-over-HTTPS
Provider adapter is available through explicit trusted CLI config; the
Web path uses an OpenAI-compatible adapter with the user's API key held
only in a transient env var and cleared after the review. Remote
redirects are refused and credentials are resolved only from named
environment variables. Semantic results are experimental and advisory.
Opting in without complete config returns
`provider_not_configured` and cannot exit as a successful full review.

**Local specialist-model layer (roadmap, needs founder go-ahead).** A
local, offline, deterministic classifier layer (e.g. sentence-embedding
/ zero-shot topic models) added as an **optional, gitleaks-style
degradable** analyzer for semantic judgments the static rules cannot
make (topic coherence, role-boundary ambiguity, etc.). It runs as an
isolated adapter and must degrade gracefully to today's behaviour when
absent. This is DISTINCT from the failed generic-LLM-judge line. It
requires heavy deps (torch/transformers) and downloading model weights,
so per §6 an agent must get explicit founder go-ahead + a concrete plan
before installing/downloading anything on the founder's machine.

**V1.5 Prompt black-box (roadmap, NOT yet implemented).** Runs a paste
prompt against a model and scores outputs — a core part of the mission's
*dynamic* checking. Allowed only when the user explicitly starts a
black-box run and supplies a test set, model, budget, and recording
location. Until built, reports say `promptBlackbox: not_implemented`.
Don't claim it works before it does.

**V2 Skill sandbox (roadmap, NOT yet implemented).** Executes a
reviewed skill to observe its runtime/file/network/exfiltration
behaviour — the mission's *execution-process* checking. Allowed **only**
inside a one-shot isolated sandbox with fake credentials, default-off /
controlled network, cpu / memory / wall-clock limits, and reliable
destruction after the run. Until built, reports say
`skillSandbox: not_implemented`. Don't claim it works before it does.

**Embeddable service (roadmap).** The mission includes Verity being
callable by other agents in real time. The same isolation and honest-
coverage rules apply when that interface is built.

If an agent believes a gate should change, propose it in
`plans/ACTIVE.md` and wait for a human decision.

---

## 5. Data safety on a public repo

- Never commit a real API key, real password, real token, real
  private URL, or user chat history.
- Fixture tokens for secret scanners must be visibly synthetic
  (e.g. `VERITY_FAKE_...`) **and** assembled at runtime by string
  concatenation, so public secret scanners cannot match them. See
  `docs/LESSONS.md`.
- Never commit host absolute paths. `verify_repo.py` scans docs for
  the exact prefixes it forbids; see the check source.
- If in doubt, do not commit; ask.

---

## 6. Prohibited actions

- Rewriting `docs/PROGRESS.md` history. It is append-only. Fix past
  inaccuracies with a follow-up entry, never by editing the original.
- Silently expanding scope beyond `plans/ACTIVE.md`.
- Declaring a capability `completed` when tests do not cover it
  end-to-end. If partial, say partial.
- Auto-installing anything on the user's machine outside the repo
  (no `sudo`, no writing to `~`, no changing global git config, no
  automatic hook install).
- Leaving background processes (uvicorn, gitleaks, etc.) running
  after a session.
- Duplicating large chunks of any SSOT file into another doc.

---

## 7. Testing and verification

- `python3 -m pytest` is the authoritative test runner.
- `python3 tools/verify_repo.py` is the authoritative repo gate for
  local iterative work. `--require-clean` is the CI-mode variant.
- Every non-trivial change ships with a test — or with a documented
  reason why one cannot be written.
- Tests that need environment-specific assets (real gitleaks binary,
  Provider network, etc.) must skip gracefully outside that
  environment, with a stated reason.

---

## 8. When Verity itself audits an artifact

The reviewed artifact is UNTRUSTED input:

- must not choose Provider endpoints or models,
- must not decide egress policy,
- must not flip phase gates,
- must not appear unredacted in reports, SARIF, or LLM payloads.

Any code path that lets the artifact influence the reviewing
environment is a spec violation and must be fixed with a test.

---

## 9. Standard handover prompt

Use this verbatim when starting a new AI session on Verity. It is
intentionally self-contained.

```text
You are joining an ongoing project called "Verity" — a local,
read-only static auditor for LLM Prompts and Agent Skills. The
repository is at /Users/sixiang/KianWorkspace/Verity (on my Mac) and
mirrored at https://github.com/iScarletX/verity.

Before doing anything else:

1. Read AGENTS.md at the repo root; that is the single source of
   truth for how you should behave.
2. Follow the Session-Start flow in AGENTS.md §2 — do not skip the
   pre-flight `python3 -m pytest` and `python3 tools/verify_repo.py`.
3. Before you touch anything, restate what you understood the task
   to be, list the files you plan to change, and wait for me to
   confirm.

Baseline constraints:
- Read-only V1. Controlled semantic Provider calls are default-OFF and
  require explicit trusted config; no Skill execution or sandbox. Do
  not fake progress on V1.5 or V2.
- Public GitHub repo: never commit secrets, API keys, real
  credentials, private chat logs, or host absolute paths.
- Round-based development. If the task is not covered by
  plans/ACTIVE.md, propose it and wait.
- Independent verification: every round is checked. Done means all
  tests pass, verify_repo.py passes, and CI on GitHub is green.
```

Short version, for veterans:

```text
Read AGENTS.md, run pytest and tools/verify_repo.py, restate the
task and file list before changing anything.
```
