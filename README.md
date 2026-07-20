# Verity — Prompt & Skill Auditor

> Phase 0 core contracts + high-confidence deterministic Prompt/Skill
> rules + controlled Bandit and gitleaks integration + SARIF 2.1.0
> export. Read-only static V1. **Not** a sandbox, **not** a runtime
> evaluator.

## Product roadmap (must not be lost)

Verity is planned as a three-layer audit tool:

| Version | Layer | Status |
|---|---|---|
| **V1** (this repo) | Static checks + controlled semantic review of Prompts and Skills | **Phase 0 + walking skeleton + Prompt rules + Skill Auditor + Bandit + SARIF implemented** |
| **V1.5** | Black-box Prompt evaluation (run prompts against a model, score outputs) | **Not implemented.** Later phase. |
| **V2** | Isolated, one-shot Skill sandbox with fake filesystem, fake credentials, controlled network — observing process/file/network/exfiltration behaviour of the Skill under audit | **Not implemented.** Later phase. |

**V1 is strictly read-only.** It does NOT execute the skill under review,
install its dependencies, start unknown services, call into review-target
code, recursively expand unknown nested archives, or contact external
LLM providers. This round also does not fetch from GitHub or open ZIPs;
those gates come later (Phase 2/3 in the spec).

**Scope invariants (from `01-Verity工程规格-v0.3.md`):**

- **One `Review` corresponds to exactly one Artifact per Review** — batch scans are deferred.
- Deterministic Findings are **physically isolated** from any LLM path.
- Uncovered/failed/skipped checks are **not** reported as "no problem."

## Architecture at a glance

```
SourceReceipt
  → ArtifactSnapshot          (safe intake; carries controlled prompt_kind)
  → ReviewPlan                (one AnalysisPlanItem per Rule)
  → EvidenceRecord[]          (Evidence-first; secret evidence is redacted)
  → RuleMatchEvent[]          (deterministic; eventDedupKey stable across runs)
  → deterministic Finding[]   (pure code path, no LLM, no filter)
  → CoverageAssessment        (Plan ⇢ Execution reconciliation;
                                 `not_applicable` gate is explicit,
                                 `blocked_by_upstream_failure` is not silent)
  → ReportProjection          (JSON + single-file static HTML, CSP-protected)
```

Two independent engines (Prompt, Skill) share the same data model and
report infrastructure but have separate rule registries. See
`src/verity/`:

- `canonical.py` — canonical serialization + fingerprints (§2.2, §4.2, §5.1)
- `models.py` — Phase 0 data types (Artifact, Snapshot, Evidence, RuleMatch, Candidate, Assessment, Finding, Plan, Coverage, PatchSet). Snapshot has a controlled `promptKind` enum for Prompt engine.
- `registry.py` — FindingType + Rule registries (§6 supersedes, §8 subject_key, §18.1 supply-chain). Rules declare `applicablePromptKinds` for prompt-kind gating.
- `engine.py` — Rule execution + deterministic Finding pipeline (§7.4). Rules return `RuleHit(evidences=[...], subject=...)`; multi-evidence findings (e.g. duplicate assignment) are first-class.
- `validation_policy.py` — Validator containment contract (§7.2, §7.3)
- `builtins.py` — Built-in FindingTypes and Rules
- `parser.py` — Safe SKILL.md / YAML frontmatter parser with resource budgets (safe_load only)
- `skill_rules.py` — Skill Auditor rule implementations
- `owasp.py` — OWASP AST10 taxonomy + honest coverage matrix
- `bandit_runner.py` — Controlled subprocess adapter for PyCQA Bandit (Apache-2.0). No shell, fixed timeout, output-size cap, tmpdir staging + cleanup, JSON shape validation, pinned-version check.
- `bandit_adapter.py` — Bandit result -> Evidence/RuleMatch/Finding normalisation; Bandit severity/confidence/CWE preserved as controlled metadata; identity only from `(artifactPath, testId, lineNumber)`.
- `gitleaks_runner.py` — Controlled subprocess adapter for gitleaks (MIT, external binary, pinned 8.28.0). No shell, controlled env, JSON-file report, version + optional SHA-256 gate, tmpdir staging, user config confinement, all raw Secret / Match / Line values scrubbed at parse time.
- `gitleaks_adapter.py` — Redacted gitleaks results -> secret-sensitivity Evidence (§5.1 secret path). `redactedPreview = "[gitleaks:<ruleId>]"`; the raw secret never enters `occurrenceFingerprint`, subjectKey, JSON, HTML, SARIF or exceptions.
- `sarif.py` — SARIF 2.1.0 exporter with byte-offset regions, stable partialFingerprints, coverage in run.properties, no secret leakage.
- `intake.py` — Safe intake (text + local directory) with path escape / symlink / budget / NUL guards
- `review.py` — Orchestrator; `not_applicable` gate counts as OK for coverage.
- `baseline.py` — Baseline compare, coverage-aware (§10.2)
- `report.py` — JSON + static HTML report with CSP, HTML escape, per-finding evidence block (dual-evidence traceable)
- `schema.py` — JSON Schema (Draft 2020-12) for the core objects
- `cli.py` — CLI entry point

## Install / run (clean environment, reproducible)

Requires Python 3.9+ (tested on 3.9.6; supported through 3.13; declared `requires-python = ">=3.9,<3.14"`).

### Installing gitleaks (external binary)

gitleaks is a Go binary (MIT). Verity requires **exactly gitleaks 8.28.0**
under the `standard` skill-review profile. The binary is not vendored
in this repo.

```bash
# Interactive install with SHA-256 verification (developer utility;
# performs network IO):
python3 tools/install_gitleaks.py --target ~/.local/bin
# Or install by any other means to the same version, then either:
#   * add it to PATH, or
#   * set VERITY_GITLEAKS_PATH=/absolute/path/to/gitleaks
```

If gitleaks is missing, mis-versioned, or its SHA-256 does not match
the pinned release descriptor (`tools/gitleaks_release.json`), Verity
marks the analyzer failed and Coverage insufficient. It never silently
falls back to a weaker scanner and claims completion.

```bash
# Clean install using pinned locks
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip setuptools wheel  # older venvs ship an old pip
pip install -r requirements.lock       # runtime deps
pip install -r requirements-dev.lock   # dev/test deps
pip install -e .                       # install package in editable mode
pytest -q                              # run tests   -> 80 passed
```

Or without a venv (uses ``--user``):

```bash
python3 -m pip install --user -r requirements.lock -r requirements-dev.lock
PYTHONPATH=src python3 -m pytest -q
```

Both dependency locks are committed and their licenses are documented
in `THIRD_PARTY_LICENSES.md`. No network calls at runtime.

## Skill rule inventory (round 3)

All rules are deterministic, text/AST-level, and never execute the skill
under review. Rules that depend on `SKILL.md` metadata declare
`requiresManifest=True`; when the manifest parser fails, they are
recorded as `blocked_by_upstream_failure` in the ReviewPlan, never
silently absent.

| Rule ID | Severity | OWASP AST | Boundaries |
|---|---|---|---|
| `skill.missing_skill_md` | high | AST04 | Anchors the finding at an existing file (or a synthetic root location if the artifact is empty). |
| `skill.manifest_parse_failure` | high | AST04 | Emits one Finding per parser diagnostic: `frontmatter_not_closed`, `yaml_parse_error`, `yaml_root_not_mapping`, `yaml_too_deep`, `yaml_too_many_keys`, `frontmatter_over_budget`, `frontmatter_too_many_lines`, `frontmatter_alias_bomb_suspected`. |
| `skill.manifest_name_issue` | medium | AST04 | `missing` / `blank` / `invalid_syntax`. Syntax is `[A-Za-z0-9][A-Za-z0-9._\- ]{0,62}[A-Za-z0-9]`. |
| `skill.manifest_description_missing` | medium | AST04 | `missing` / `blank`. No subjective "quality" judgement. |
| `skill.manifest_missing_reference` | medium | AST04 | Local script/file referenced in `scripts`/`files`/`refs`/`entrypoints` does not exist. Suppressed when the suffix-mismatch rule already covers the case. |
| `skill.manifest_unsafe_reference_path` | high | AST04 | Reference is an absolute path, contains `..`, or uses back-slash separators. |
| `skill.manifest_unpinned_dependency` | medium | AST02 + AST07 | Only pinned versions like `1.2.3` or `==1.2.3` are accepted; ranges, `latest`, `*`, missing versions are flagged. |
| `skill.manifest_permission_wildcard` | high | AST03 | Only strict wildcard values in `permissions`/`allowed_tools`/`tools`: `*`, `/`, `**`, `.../*`. |
| `skill.manifest_external_instructions` | high | AST05 | Only when `external_instructions.mode ∈ {fetch_and_follow, runtime_fetch}`. Documentation-link URLs are NOT flagged. |
| `skill.manifest_script_suffix_mismatch` | medium | AST04 | Declared script `.py` but only `.js`/`.sh`/etc. present with same stem. |
| `skill.python_subprocess_shell_true` | high | AST01 | Python AST-level; keyword `shell=True` on any `subprocess.<x>` call. **Superseded at (file, line) by Bandit `B602` when Bandit ran successfully** (no double-report). Verity never executes the code. |
| `skill.bandit_finding` (rules `skill.bandit.<test_id>`) | varies | AST01/AST02/AST05 depending on test | 12 curated Bandit test_ids: B102 (exec) / B105/B106/B107 (hardcoded passwords) / B301 (pickle load) / B303 (weak hash) / B310 (unsafe urlopen) / B506 (yaml.load unsafe) / B602 (subprocess shell=True) / B605 (os.system) / B607 (partial exec path) / B701 (jinja2 autoescape). Bandit's `issue_text` never contributes to identity; Verity's severity is the policy value, not Bandit's raw severity. |
| `skill.gitleaks_finding` | high | AST02 | Secret detected by gitleaks 8.28.0 (external subprocess). The raw secret is redacted BEFORE the adapter sees it; identity = `(artifactPath, gitleaksRuleId, lineNumber)`. Only rendered when gitleaks completed; when gitleaks failed, Coverage is insufficient and the report says so. |
| `skill.fake_secret_fixture` (limited fallback) | high | AST02 | Detects only the synthetic `VERITY_FAKE_SECRET_*` fixture token used by Verity's own tests. **This is NOT a substitute for real secret scanning** — gitleaks provides that under `--profile standard`. |
| `skill.dangerous_shell_pattern` (legacy) | high | AST01 | Text-level pattern only; the shell is NOT executed. |

Honest OWASP AST10 status (shown in every skill report as a matrix):

| OWASP | Status | Notes |
|---|---|---|
| AST01 malicious code / dangerous runtime | partial | Text patterns + Python AST `shell=True`. No sandbox, no bandit/semgrep integration yet. |
| AST02 supply chain | partial | Unpinned dependency + synthetic secret. Real secret detection deferred to gitleaks integration. |
| AST03 excessive authorisation | partial | Permission wildcard only. |
| AST04 insecure metadata | partial | Missing/blank fields, unsafe reference paths, suffix mismatch, parse failure. |
| AST05 untrusted external instructions | partial | Strict-mode `fetch_and_follow` URLs only. |
| AST06 weak isolation | none | Requires V2 sandbox. |
| AST07 update drift / integrity | partial | Unpinned dep also maps here (versioning drift). |
| AST08 insufficient scanning | none | Meta-observation, requires product runtime not present in V1. |
| AST09 lack of governance | none | Requires review workflow features (Baseline, Disposition history in a UI) not built here. |
| AST10 cross-platform reuse | none | Would require multi-runtime declaration matrix. |

We never claim `full` coverage. The report enumerates only `partial` and
`none` per category.

## Prompt rule inventory (round 2)

Prompt Auditor and Skill Auditor use **separate** rule registries. The
prompt registry now contains the following deterministic rules:

| Rule ID | Severity | Applicable prompt kinds | Boundaries |
|---|---|---|---|
| `prompt.instruction_override_marker` | low (risk signal, not a proven attack) | any | Excludes fenced/inline code; only well-known override phrases. |
| `prompt.unfilled_placeholder` | medium | any | Detects `{{...}}`, `${...}`, `<TODO ...>`/`<INSERT ...>`, `[INSERT ... HERE]`. Excludes fenced/inline code and legitimate JSON. |
| `prompt.system_hardcoded_secret` | high | `system_prompt` only | Uses the synthetic `VERITY_FAKE_SECRET_*` token in this phase. Later phases will delegate real secret detection to gitleaks. Redacted preview only; raw value never persisted. |
| `prompt.duplicate_numeric_assignment` | medium | any | Same key given two different numeric values on strict `key: N` or `key = N` lines. Dual-evidence: both assignment sites are cited. Identical repeats are not flagged. |
| `prompt.control_character` | medium | any | ASCII control characters (except \t, \n, \r) and Unicode bidi overrides (U+202A–U+202E, U+2066–U+2069). NUL is rejected at intake, not here. |
| `prompt.empty_or_whitespace` | medium | any | Empty or whitespace-only prompt content. |
| `prompt.open_ended_tool_wildcard` | high | `system_prompt` only | Only strict-form matches: `allowed_tools: *`, `permissions: ["*"]`, `tools: ["*"]`. Narrative star is not matched. |

Severity discipline (also visible in the HTML report):

- **low** = risk signal, context-dependent; may be a benign quotation.
- **medium** = quality / consistency issue with precise, mechanically verifiable evidence.
- **high / critical** = mechanically-provable policy violation.

`prompt.system_hardcoded_secret` and `prompt.open_ended_tool_wildcard`
are **system-only**. When a user prompt is scanned, they appear as
`not_applicable` executions in the ReviewPlan with a reason code
containing the required kind — they are never silently skipped.

## CLI demos

### Prompt demos

```bash
# 1. Clean user prompt — no findings expected
python3 -m verity.cli review --engine prompt --prompt-kind user_prompt \
  --input-file tests/fixtures/prompt_clean/prompt.txt --out /tmp/verity_out/clean

# 2. Broken user prompt — unfilled placeholders + duplicate numeric assignment
python3 -m verity.cli review --engine prompt --prompt-kind user_prompt \
  --input-file tests/fixtures/prompt_broken_user/prompt.txt --out /tmp/verity_out/broken

# 3. Risky system prompt — synthetic secret + wildcard tool authorisation
python3 -m verity.cli review --engine prompt --prompt-kind system_prompt \
  --input-file tests/fixtures/prompt_risky_system/system.txt --out /tmp/verity_out/risky

# Export the core JSON Schema
python3 -m verity.cli export-schema --out /tmp/verity_out/schema.json
```

### Skill demos

Every skill review also writes `report.sarif` (SARIF 2.1.0) next to
`report.json` / `report.html`.

### Skill review profiles

```bash
# standard (default): gitleaks required for secret coverage
python3 -m verity.cli review --engine skill --profile standard \
  --input-dir tests/fixtures/clean_skill --out /tmp/verity_out/std

# minimal: explicit opt-out; report says "not_requested_by_profile"
python3 -m verity.cli review --engine skill --profile minimal \
  --input-dir tests/fixtures/clean_skill --out /tmp/verity_out/min
```

Recorded exit codes on an environment where **gitleaks is NOT installed**
(as of the environment where this README was refreshed):

| Fixture | profile | gitleaks status | coverage | exit |
|---|---|---|---|---:|
| `clean_skill` | standard | not_installed | insufficient | 0 |
| `clean_skill` | minimal | not_requested_by_profile | sufficient | 0 |
| `python_shell_true_skill` | standard | not_installed | insufficient | 1 (Bandit high) |

Install gitleaks and the same commands should print `coverage=sufficient`
on `clean_skill --profile standard`.


```bash
# clean skill: 0 findings, coverage sufficient, exit 0
python3 -m verity.cli review --engine skill \
  --input-dir tests/fixtures/clean_skill --out /tmp/verity_out/clean_skill

# malformed manifest: file-level rules still run; manifest-dependent
# rules are blocked_by_upstream_failure; coverage insufficient; exit 1
python3 -m verity.cli review --engine skill \
  --input-dir tests/fixtures/malformed_manifest_skill \
  --out /tmp/verity_out/malformed_manifest

# missing refs / unsafe paths: precise reference issues
python3 -m verity.cli review --engine skill \
  --input-dir tests/fixtures/missing_refs_skill \
  --out /tmp/verity_out/missing_refs

# risky permissions + unpinned deps
python3 -m verity.cli review --engine skill \
  --input-dir tests/fixtures/risky_permissions_skill \
  --out /tmp/verity_out/risky_perms

# strict external_instructions mode
python3 -m verity.cli review --engine skill \
  --input-dir tests/fixtures/external_instructions_skill \
  --out /tmp/verity_out/external_instructions

# python AST: subprocess.run(..., shell=True)
python3 -m verity.cli review --engine skill \
  --input-dir tests/fixtures/python_shell_true_skill \
  --out /tmp/verity_out/python_shell_true
```

Recorded exit codes / findings on the checked-in fixtures:

| Fixture | findings | high/critical | coverage | exit |
|---|---:|---:|---|---:|
| `clean_skill` | 0 | 0 | sufficient | 0 |
| `malformed_manifest_skill` | 2 | 2 | insufficient | 1 |
| `missing_refs_skill` | 3 | 2 | sufficient | 1 |
| `risky_permissions_skill` | 4 | 2 | sufficient | 1 |
| `external_instructions_skill` | 1 | 1 | sufficient | 1 |
| `python_shell_true_skill` | 3 | 1 | sufficient | 1 |

(`python_shell_true_skill`: Bandit B602 high + B607 medium x2. The hand-
written `subprocess shell=True` rule is suppressed on that (file, line).)

Each command writes `report.json` and `report.html` under the target
directory. When high/critical findings are present, exit code is 1 so
future CI integration can use it as a gate. Coverage-insufficient runs
show an explicit warning banner in the HTML report and refuse to say
"ready" / "low_detected_risk".

## Dependencies (locked)

Runtime (`requirements.lock`):

| Package | Version | License |
|---|---|---|
| jsonschema | 4.25.1 | MIT |
| PyYAML | 6.0.3 | MIT |
| bandit | 1.7.10 | Apache-2.0 |
| stevedore | 5.5.0 | Apache-2.0 |
| rich | 15.0.0 | MIT |
| markdown-it-py | 3.0.0 | MIT |
| mdurl | 0.1.2 | MIT |
| Pygments | 2.20.0 | BSD-2-Clause |
| jsonschema-specifications | 2025.9.1 | MIT |
| referencing | 0.36.2 | MIT |
| rpds-py | 0.27.1 | MIT |
| attrs | 26.1.0 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |

**Not** integrated (yet — spec constraint: integrate only with running tests):

- Semgrep (planned)
- YARA (planned)

Dev/test (`requirements-dev.lock`): pytest 8.4.2 (MIT) and its transitive
deps; tomli/exceptiongroup only on Python < 3.11.

Full attribution: `THIRD_PARTY_LICENSES.md`. Project itself: Apache-2.0
(`LICENSE`).

## Contract-level vs behavioural acceptance

Some acceptance items from spec §20 remain contract-level in this
phase (they define the taxonomy / shape but do not exercise a runtime
behaviour that only exists in later phases). Each such test is labelled
in its docstring. Behavioural coverage is expected to grow as later
phases land (bandit/semgrep/gitleaks integration, LLM egress, patch
apply, etc.).

## Known limitations

- No ZIP / GitHub URL intake yet (Phase 2/3 gate).
- Only Python has an AST-level scanner (Bandit + one hand-picked rule);
  other languages (Shell/JS/TS/Ruby/Go) are still text-level only.
- No semantic candidate generation or Validator LLM calls (Phase 4).
- No PatchSet apply — proposal shape only (Phase 6).
- Real secret detection still uses only the synthetic fixture token.
  gitleaks integration is planned but not present.
- Semgrep / YARA not integrated yet.
- SARIF file is produced, but the repo does not ship a GitHub Actions
  workflow. Uploading `report.sarif` to GitHub Code Scanning is the
  user's responsibility.

### verdict.subject on insufficient coverage

When coverage is insufficient the JSON, HTML and SARIF reports all
emit `verdict.subject = null` (SARIF: `run.properties.verity.verdict.subject`
is `null`). This is intentional: Verity refuses to say "ready" /
"low_detected_risk" when it does not know whether the required checks
were actually completed. Consumers must handle `subject == null`
safely; the checked-in HTML template shows a *COVERAGE INSUFFICIENT*
banner in that case.

## License

Apache License 2.0 — see [`LICENSE`](./LICENSE).
