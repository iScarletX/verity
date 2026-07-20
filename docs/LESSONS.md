# LESSONS — Verity pitfalls ledger

Append-only. Every entry uses the fixed template below. When
adding, put the most recent entry at the TOP.

```
### YYYY-MM-DD — <one-line title>
- **Symptom**: what did we observe going wrong
- **Root cause**: what actually caused it
- **Fix**: how it was resolved (or worked around)
- **Prevention**: what future agents should do to avoid recurrence
- **Evidence**: commit(s) and/or test id(s)
```

---

### 2026-07-20 — An explicitly requested optional stage must gate success

- **Symptom**: `verity review --semantic` could return exit 0 when static
  coverage passed even though the semantic Provider was missing or the
  semantic stage failed.
- **Root cause**: The CLI exit ladder only considered deterministic
  coverage and High/Critical deterministic Findings. Semantic status was
  projected in the report but was not part of the command's requested
  acceptance contract.
- **Fix**: When `--semantic` is explicit, only semantic status
  `completed` is eligible for `gate=pass`; other semantic states produce
  exit 3 unless a High/Critical Finding already produces exit 1.
- **Prevention**: Any future optional execution layer (Prompt black-box,
  Skill sandbox, Agent runtime trace) must distinguish “not requested”
  from “requested but incomplete” in both reports and process exit codes.
- **Evidence**: Round 11 CLI E2E and
  `TestCliSemantic::test_cli_opt_in_reports_provider_not_configured`.

### 2026-07-20 — Documents can drift from reality faster than code

- **Symptom**: A `README.md` line said "277 tests" while a later
  round added or removed a test class. New agents took the doc at
  face value and did not re-run pytest.
- **Root cause**: Test counts were written directly into files that
  outlive them. There was no single machine-checked source of truth.
- **Fix**: Numbers live only in the top summary of
  `docs/PROGRESS.md` (the `verified_against` block + capability
  matrix), checked by `tools/verify_repo.py`. README links to that
  block.
- **Prevention**: Do not write easily-drifting numbers into any file
  other than the top of `docs/PROGRESS.md`. Extend `verify_repo.py`
  to complain when it finds one.
- **Evidence**: this round (handover system).

### 2026-07-20 — GitHub Push Protection can misclassify fake fixtures

- **Symptom**: Push refused because a test fixture contained the
  literal ``ghp_`` followed by 36 hex-alnum characters as a single
  string, which GitHub read as a real GitHub Personal Access Token.
- **Root cause**: The fixture was a single string literal in the
  test file, matching upstream secret-scanner patterns exactly.
- **Fix**: In tests, assemble such synthetic tokens with runtime
  string concatenation (e.g. ``"ghp_" + "1234..."``) so the source
  file has no matching literal, and add a NOTICE beside external
  fixtures. `verify_repo.py` runs the same check locally.
- **Prevention**: Never commit a full-literal secret pattern in
  source, even if it is clearly synthetic. See
  `tests/fixtures/*/NOTICE`.
- **Evidence**: commit `aedbeb7` (Round 8.1).

### 2026-07-20 — "The subagent said done" is not the same as "verified done"

- **Symptom**: A previous round declared a task complete because
  the sub-agent's tests passed locally; the main agent's
  independent check found a mismatch.
- **Root cause**: There was no shared machine gate. Each side
  measured different things.
- **Fix**: `tools/verify_repo.py` and `.github/workflows/ci.yml` are
  the only gates that count.
- **Prevention**: If `verify_repo.py` did not run, the round is not
  done. If CI did not pass, the change is not merged.
- **Evidence**: this round.

### 2026-07-20 — Coverage-insufficient must NOT exit zero

- **Symptom**: Under `--profile standard` with gitleaks missing,
  Verity previously printed `coverage=insufficient` and still
  exited 0. In CI that would count as "green" and let a skill
  with unscanned secrets through.
- **Root cause**: The exit-code policy only considered
  High/Critical findings, not coverage.
- **Fix**: Explicit gate ladder documented in
  `verity/cli.py`. `0=pass`, `1=findings_block`, `3=coverage_block`
  (chosen instead of `2` which argparse reserves for usage error).
- **Prevention**: Every CI-visible gate ladders through the
  `gate=...` marker on stdout; the marker is asserted by tests.
- **Evidence**: commit `4a42b8b` (Round 5 hotfix).

### 2026-07-20 — SARIF flat keys look "missing" to nested-key scripts

- **Symptom**: An external verification script looked for
  `run.properties.coverage` and reported "SARIF field missing".
- **Root cause**: Verity uses namespaced keys
  (`run.properties["verity.coverage"]` etc.), which is valid SARIF
  2.1.0 §3.8. The consuming script was wrong.
- **Fix**: Docstring in `verity/sarif.py` explicitly documents the
  flat-key convention. README calls it out. Test
  `test_run_properties_uses_flat_verity_keys` guards against a
  wrong migration.
- **Prevention**: Do not "helpfully" migrate to nested properties
  without a full consumer survey.
- **Evidence**: commit `4a42b8b`.

### 2026-07-20 — Archive SHA ≠ binary SHA (gitleaks two-layer verify)

- **Symptom**: Naive attempts to verify the installed gitleaks
  binary against the SHA in `tools/gitleaks_release.json` always
  failed after installation.
- **Root cause**: The release descriptor records the SHA-256 of the
  published tar.gz. The extracted binary has a different SHA.
- **Fix**: Two-layer policy: installer verifies archive SHA before
  extraction; then computes the extracted binary SHA and writes it
  to `.tools/gitleaks/<version>/manifest.json`. Runtime re-checks
  the binary SHA against the install manifest.
- **Prevention**: Any future external-binary integration should use
  the same two-layer pattern. Do not vendor binaries.
- **Evidence**: commit `cd2209b` (Round 5b).

### 2026-07-20 — Long-lived background services after a task

- **Symptom**: A live-smoke `uvicorn` server from a previous task
  was still running when a later task started, causing port
  conflicts and noise in `ps`.
- **Root cause**: The task used `&` to background the process for
  a curl smoke test and did not always kill it.
- **Fix**: Every smoke test captures the PID and kills it in a
  `finally` step. `verify_repo.py` refuses to trust a state where
  a residual server is observable.
- **Prevention**: If you start a listener, kill it before ending
  the round. Prefer `TestClient` (in-memory) over live uvicorn
  when the point is not to exercise the socket.
- **Evidence**: Round 6/7 smoke sections in `docs/PROGRESS.md`.

### 2026-07-20 — Fake OWASP AST10 mapping is worse than none

- **Symptom**: Early draft mapped every Skill rule to some AST10
  category to make coverage look better.
- **Root cause**: Marketing pressure, not evidence.
- **Fix**: `owasp.py` returns only `partial` or `none` — never
  `full` — and only for rules that actually address that
  category.
- **Prevention**: Never claim "we cover OWASP AST10" without
  citing which specific rule addresses which category.
- **Evidence**: `verity/owasp.py`, tests
  `test_owasp_never_full` in `tests/test_skill_rules.py`.
