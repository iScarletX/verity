# CURRENT_STATE

A machine-readable snapshot of what Verity actually does **right now**.
Update this file at the end of every round (see `AGENTS.md §3`).

The `verified_against` block records the last commit at which the
numbers below were verified. `verify_repo.py` requires that this commit
is an **ancestor of `HEAD`** (or equal to it), which avoids the
self-reference problem of a document declaring "this commit is X" while
producing that very commit.

<!-- verify_repo.py: begin verified_against block -->
```yaml
verified_against:
  date: "2026-07-20"
  # Commit that was the tip when the numbers below were measured. Must
  # be an ancestor of the current HEAD when verify_repo.py runs.
  commit: "aedbeb7f3e5299ad9a41ee63be1d835354ba6989"
  tests_collected: 288
  tests_passed: 288
  tests_skipped: 0
  verify_command: "python3 tools/verify_repo.py"
  python_version: "3.9+"
  gitleaks_binary_version: "8.28.0"
  bandit_version: "1.7.10"
```
<!-- verify_repo.py: end verified_against block -->

---

## Capability matrix

Kept in sync with `verity/report.py::review_to_dict` — the strings
below MUST match those produced at runtime.

| Capability                          | Status            | Notes                                                                                 |
|-------------------------------------|-------------------|---------------------------------------------------------------------------------------|
| Static (deterministic) auditing     | `completed`       | Prompt rules + Skill rules + parser + Bandit 1.7.10 + gitleaks 8.28.0 + SARIF export. |
| Semantic (LLM-assisted) auditing    | `not_enabled`     | Interface + gates + schema + mock tests only; **no bundled Provider**.                |
| V1.5 Prompt black-box               | `not_implemented` | Planned; nothing in repo.                                                             |
| V2 Skill isolated sandbox           | `not_implemented` | Planned; nothing in repo.                                                             |

If any row above changes state, both this file **and** the runtime
code must change together — `verify_repo.py` checks the runtime string.

---

## What ships right now

- Read-only intake for prompt text and local skill folders.
- Deterministic rule engines (Prompt + Skill, separate registries).
- Bandit subprocess integration; gitleaks subprocess integration
  (external binary, pinned 8.28.0, two-layer SHA-256 verification).
- CLI (`python -m verity.cli`), local Web MVP
  (`python tools/start_local_web.py`).
- JSON, HTML, SARIF 2.1.0 reports.
- Chinese-language remediation catalog + priority-ordered next-step
  summary.
- Experimental semantic scaffold (default OFF; provider-not-configured
  when opted in without a real Provider).

## What is deliberately absent

- No LLM Provider client (HTTPS or otherwise). Semantic path is
  interface + mocks only.
- No Skill execution / sandbox / VM / container.
- No prompt black-box runner.
- No Semgrep / YARA integration.
- No ZIP or GitHub URL intake.
- No PatchSet apply (only proposal shapes).

## Blockers / open questions

- **Real Provider integration** requires a user decision: which
  Provider, what egress policy, what budget defaults.  Do not begin
  that work without an explicit round assignment.

## Next natural step

See `plans/ACTIVE.md`. Nothing new should start without a user-approved
round description.
