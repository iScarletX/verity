# Verity in-repo progress log

This file tracks Verity's own implementation progress. It is separate from
the main-agent design docs (spec / reuse decision table / CHANGELOG),
which live outside this repository and are only referenced.

## Round 1 (2026-07-18)  →  commit `291f1ae`
- Phase 0 core contracts, canonical serialization/fingerprint (§2.2 §4.2 §5.1 §5.2 §8)
- Safe intake (text, local directory), no-follow, path escape, budgets
- Two independent engines: Prompt (1 rule) + Skill (2 rules)
- Deterministic Finding pipeline physically isolated from any LLM path (§7.4)
- Validator containment contract shape (§7.2 §7.3) — enforcement without a live validator
- JSON + single-file static HTML report with CSP and HTML escape
- JSON Schema (Draft 2020-12) export
- 19-item acceptance-test matrix (13 behavioural, 6 contract-level)
- 34 tests

## Round 2 (2026-07-18)  →  commit `b3f3b93`
- Apache-2.0 LICENSE + `THIRD_PARTY_LICENSES.md`
- Pinned dependency locks (`requirements.lock`, `requirements-dev.lock`)
- Controlled PromptKind enum + CLI `--prompt-kind`; rule applicability gate;
  `not_applicable` recorded in ReviewPlan, OK for Coverage
- Multi-evidence rule support via `RuleHit(evidences=[...], subject=...)`
- +6 Prompt rules (unfilled placeholder, system hardcoded secret,
  duplicate numeric assignment (dual-evidence), control character,
  empty/whitespace, open-ended tool wildcard)
- HTML report: per-finding evidence block, prompt-kind row, severity legend
- 3 prompt fixtures (clean / broken_user / risky_system)
- 80 tests

## Round 3 (2026-07-18)  →  this commit
- Safe SKILL.md / YAML frontmatter parser with resource budgets
  (byte, line, depth, key count, alias/anchor tokens); alias-bomb rejected;
  `yaml.safe_load` only, never `Loader`
- Engine now supports a Parser step (Skill engine); Parser is a first-class
  `AnalysisPlanItem` and its failure flips `parser_ok`
- New `requiresManifest` gate on `RuleDefinition`; rules that depend on
  the manifest become `blocked_by_upstream_failure` on parser failure —
  never silently absent (spec §9.2, item #9 of the 19-list → behavioural)
- File-level rules continue to run when the manifest parser fails
  (partial failure isolation)
- +11 Skill rules (missing SKILL.md, manifest parse failure,
  name/description issue, missing reference, unsafe reference path,
  unpinned dependency, permission wildcard, external instructions,
  script suffix mismatch, Python `subprocess.*(shell=True)`), plus the
  two pre-existing file-level rules re-tagged with OWASP AST10 mapping
- Real OWASP AST10 coverage matrix in the JSON report and HTML report
- 7 new Skill fixtures (clean / malformed / missing_refs / risky_perms /
  external_instructions / python_shell_true / doc_url) plus NOTICE files
- +37 tests; total 117 passing
- Dependency: PyYAML 6.0.3 (MIT) pinned

## What is NOT in this repo (deliberate)
- No LLM egress, no candidate generator, no live validator (Phase 4)
- No ZIP / GitHub URL intake (Phase 2/3)
- No sandbox (V2)
- No gitleaks / bandit / semgrep / YARA integration yet
- No SARIF output (Phase 5)
- No PatchSet apply — only proposal shape (Phase 6)
