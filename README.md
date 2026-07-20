# Verity — Prompt & Skill Auditor

> Phase 0 core contracts + a first pass of high-confidence deterministic
> Prompt rules. Read-only static V1. **Not** a sandbox, **not** a runtime
> evaluator.

## Product roadmap (must not be lost)

Verity is planned as a three-layer audit tool:

| Version | Layer | Status |
|---|---|---|
| **V1** (this repo) | Static checks + controlled semantic review of Prompts and Skills | **Phase 0 + walking skeleton + first prompt rules implemented** |
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
- `intake.py` — Safe intake (text + local directory) with path escape / symlink / budget / NUL guards
- `review.py` — Orchestrator; `not_applicable` gate counts as OK for coverage.
- `baseline.py` — Baseline compare, coverage-aware (§10.2)
- `report.py` — JSON + static HTML report with CSP, HTML escape, per-finding evidence block (dual-evidence traceable)
- `schema.py` — JSON Schema (Draft 2020-12) for the core objects
- `cli.py` — CLI entry point

## Install / run (clean environment, reproducible)

Requires Python 3.9+ (tested on 3.9.6; supported through 3.13; declared `requires-python = ">=3.9,<3.14"`).

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

## Prompt rule inventory (this round)

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

Three demonstration fixtures live under `tests/fixtures/`:

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
| jsonschema-specifications | 2025.9.1 | MIT |
| referencing | 0.36.2 | MIT |
| rpds-py | 0.27.1 | MIT |
| attrs | 26.1.0 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |

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
- Skill side still text-level only; no AST parser matrix yet.
- No semantic candidate generation or Validator LLM calls (Phase 4).
- No PatchSet apply — proposal shape only (Phase 6).
- No SARIF output yet (Phase 5).
- Real secret detection still uses only the synthetic fixture token in
  the walking skeleton. gitleaks integration is planned.

## License

Apache License 2.0 — see [`LICENSE`](./LICENSE).
