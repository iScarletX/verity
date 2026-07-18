# Verity — Prompt & Skill Auditor

> Phase 0 core contracts + minimal vertical walking skeleton.
> Read-only static V1. **Not** a sandbox, **not** a runtime evaluator.

## Scope of this repository right now

Verity is planned as a three-layer audit tool:

| Version | Layer | Status in this repo |
|---|---|---|
| **V1** (this repo) | Static checks + controlled semantic review of Prompts and Skills | **Phase 0 + Walking Skeleton implemented** |
| V1.5 | Black-box Prompt evaluation (run prompts against a model, score outputs) | **Not implemented.** Later phase. |
| V2 | Isolated, one-shot Skill sandbox with fake filesystem, fake credentials, controlled network — observing process/file/network/exfiltration behaviour of the Skill under audit | **Not implemented.** Later phase. |

**V1 is strictly read-only.** It does NOT execute the skill under review,
install its dependencies, start unknown services, call into review-target
code, recursively expand unknown nested archives, or contact external
LLM providers. The Phase-0 walking skeleton also does not fetch from
GitHub or open ZIPs; those gates come later (see `Phase 3` in the spec).

**Scope invariants (from `01-Verity工程规格-v0.3.md`):**

- One `Review` corresponds to exactly **one Artifact per Review** — batch scans are deferred.
- Deterministic Findings are **physically isolated** from any LLM path.
- Uncovered/failed/skipped checks are **not** reported as “no problem.”

## Architecture at a glance

```
SourceReceipt
  → ArtifactSnapshot (safe intake)
  → ReviewPlan (per rule)
  → EvidenceRecord[] + RuleMatchEvent[]
  → deterministic Finding[]   (pure code path, no LLM)
  → CoverageAssessment (Plan ⇢ Execution reconciliation)
  → ReportProjection (JSON + single-file static HTML)
```

Two independent engines (Prompt, Skill) share the same data model and
report infrastructure but have separate rule registries. See
`src/verity/`:

- `canonical.py` — canonical serialization + fingerprints (§2.2, §4.2, §5.1)
- `models.py` — Phase 0 data types (Artifact, Snapshot, Evidence, RuleMatch, Candidate, Assessment, Finding, Plan, Coverage, PatchSet)
- `registry.py` — FindingType + Rule registries (§6 supersedes, §8 subject_key, §18.1 supply-chain)
- `engine.py` — Rule execution + deterministic Finding pipeline (§7.4)
- `validation_policy.py` — Validator containment contract (§7.2, §7.3)
- `builtins.py` — Built-in FindingTypes and Rules (walking-skeleton set)
- `intake.py` — Safe intake (text + local directory) with path escape / symlink / budget guards (§13)
- `review.py` — Orchestrator
- `baseline.py` — Baseline compare, coverage-aware (§10.2)
- `report.py` — JSON + static HTML report with CSP and HTML-escape (§15)
- `schema.py` — JSON Schema (Draft 2020-12) for the core objects
- `cli.py` — CLI entry point

## Install / run

```bash
# From this repo root:
python3 -m pip install --user jsonschema pytest
# Run tests:
PYTHONPATH=src python3 -m pytest -q
# Or install as a package:
python3 -m pip install --user -e .
```

### CLI examples

```bash
# Review a text prompt
python3 -m verity.cli review --engine prompt --text "Please summarise this article."

# Review a text prompt from a file
python3 -m verity.cli review --engine prompt --input-file docs/prompt.txt --out out/

# Review a local Skill folder (read-only)
python3 -m verity.cli review --engine skill --input-dir tests/fixtures/skill_bad --out out/

# Export JSON Schema
python3 -m verity.cli export-schema --out out/schema.json
```

The report is emitted as:

- `out/report.json`
- `out/report.html` (single-file, CSP-restricted, all user/model content HTML-escaped)

The HTML banner at the top gives a **cautious** verdict (subject decision
+ coverage). We refuse to say “ready” / “low_detected_risk” when
coverage is insufficient, even when no Findings were produced.

## Dependencies

Locked minimum:

- Python ≥ 3.9 (`pyproject.toml`). Preferred: 3.12. Current dev environment: 3.9.6.
- `jsonschema` ≥ 4.20 (MIT). Used for Draft 2020-12 validation.
- `pytest` ≥ 7 (MIT). Test framework.

No other runtime dependency. In particular, this Phase-0 skeleton
deliberately does **not** integrate gitleaks / YARA / bandit / semgrep
yet (they are called out in `02-成熟项目复用决策表-v0.2.md` for later
phases). Adding them means adding rules, not rewriting the pipeline.

## Contract-level vs behavioural acceptance

Some acceptance items from spec §20 are contract-level in Phase 0
(they define the taxonomy / shape but do not exercise a runtime
behaviour that only exists in later phases). Each such test is
labelled in its docstring. See the delivery report / commit message
for the full 19-item matrix and their status.

## Known limitations (Phase 0)

- No ZIP / GitHub URL intake yet (Phase 2/3 gate).
- Only text-level Skill scanning; no AST parser matrix (Phase 3).
- No semantic candidate generation or Validator LLM calls (Phase 4).
- No PatchSet apply — only proposal shape (Phase 6).
- SARIF output not yet emitted (Phase 5).
- TOCTOU / expansion-depth / bandit / semgrep integration hooks are
  present at the model / plan-item level but not exercised.

## License

Apache-2.0. See `LICENSE` (to be added).
