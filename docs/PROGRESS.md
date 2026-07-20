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

## Round 5b (2026-07-20)  →  this commit
- One-command project-local install of the official gitleaks 8.28.0
  binary via ``tools/install_gitleaks.py`` (darwin_arm64 verified):
    * archive SHA-256 `d942f3ad147250c9edbaab3fed9e482f98d3b59ba10ae97b8d75647e3ade492c`
    * binary SHA-256 `5588b5d942dffa048720f7e6e1d274283219fb5722a2c7564d22e83ba39087d7`
    * installed at `.tools/gitleaks/8.28.0/gitleaks` (gitignored)
    * install manifest at `.tools/gitleaks/8.28.0/manifest.json`
- Safe tar extraction:
    * refuses anything other than the exact entry name ``gitleaks``
    * refuses non-regular files, symlinks, hardlinks
    * caps archive size (40 MiB) and extracted binary size (200 MiB)
    * downloads to a size-capped temp file, verifies SHA-256 BEFORE
      handing bytes to ``tarfile``
- Runtime discovery + two-layer SHA:
    * ``VERITY_GITLEAKS_PATH`` env var takes precedence
    * then the project-local install manifest
    * then PATH
    * Skill content is never a source of the tool path or config
    * `check_binary` re-hashes the binary on every invocation and
      compares against the install manifest's binarySha256; drift is
      surfaced as `gitleaks_hash_mismatch`
- E2E tests flipped from skip to pass:
    * `TestGitleaksRealBinary::test_clean_scan_completes`
    * `TestGitleaksRealBinary::test_synthetic_leak_detected`
      (uses gitleaks' own `github-pat` + `slack-bot-token` default rules;
      the deliberately-non-functional `ghp_1234...` and
      `xoxb-000000000000-...` tokens are detectable by upstream rules
      but useless as credentials.)
- Nine new install-machinery tests (release descriptor pinned, manifest
  shape, runner discovery, two-layer SHA policy, tamper rejection).
- Total tests: 168 -> 177 passing (0 skipped when gitleaks is installed).

## Round 5 (2026-07-20)  →  commit `25986ca`
- Controlled gitleaks integration (external binary, MIT):
    * Pinned version: **gitleaks 8.28.0** (Verity fails the analyzer when
      any other version is installed).
    * `tools/gitleaks_release.json` records SHA-256 for darwin/linux
      x64/arm64 tarballs; `tools/install_gitleaks.py` fetches the
      official Release and verifies SHA-256 before installing.
    * The binary is NOT vendored in the git repo.
    * `verity/gitleaks_runner.py`: no-shell subprocess, 45 s timeout,
      controlled env, output cap, JSON report file (not stdout), version
      + optional SHA-256 gate, tmpdir staging, symlink/special/excluded
      never staged, user-supplied `.gitleaks.toml` never staged (config
      confinement), tmpdir removed in finally.
    * `verity/gitleaks_adapter.py`: converts redacted gitleaks results
      to secret-sensitivity Evidence (§5.1 secret path):
      `occurrenceFingerprint` never hashes raw Secret / Match bytes.
      Raw Secret / Match / Line values are dropped in the runner before
      the adapter sees them; the retained metadata is rule id, relative
      file, line/column, entropy (if numeric), a coarse length bucket,
      and a fixed redactedPreview `"[gitleaks:<ruleId>]"`.
- New Skill FindingType `skill.gitleaks_finding` (default severity high;
  OWASP-AST02). Identity = (artifactPath, gitleaksRuleId, lineNumber).
- Skill review PROFILES:
    * `standard` (default): gitleaks is required. Missing/timeout/
      version_mismatch/hash_mismatch/malformed_json all mark the
      analyzer failed and Coverage insufficient.
    * `minimal`: explicit user opt-out. The gitleaks plan item still
      appears in the ReviewPlan with status `not_applicable` and reason
      `minimal_profile:secret_scan_skipped`; the report says
      "not_requested_by_profile" so "0 secret findings" cannot be read
      as "safe".
- `skill.fake_secret_fixture` retained explicitly as a LIMITED fallback
  for the fixture token used in Verity's own tests; the RuleDefinition
  title documents it as not a full-secret-scanning replacement.
- Report: JSON exposes a redacted `gitleaksRun` block (no host paths,
  no raw results). HTML gets an Analyzers section that lists bandit
  and gitleaks status with a **Secret coverage note** when gitleaks did
  not complete. SARIF `tool.extensions` includes gitleaks **only** when
  it actually completed.
- CLI: `--profile standard|minimal`.
- 21 new tests (139 -> 158 passing, 2 skipped E2E when binary absent).

## Round 4 (2026-07-20)  →  commit `581c830`
- Controlled Bandit 1.7.10 (Apache-2.0) integration:
    * `verity/bandit_runner.py`: subprocess with fixed timeout,
      no-shell, controlled env, output-size cap, JSON shape validation,
      version pin check, tmpdir staging + cleanup, staging only
      already-intake'd `.py` files, ignoring symlinks and non-file entries.
    * `verity/bandit_adapter.py`: normalise Bandit results to Evidence
      / RuleMatch / Finding. Bandit's own severity/confidence/CWE kept
      as controlled metadata; identity only depends on
      (artifactPath, testId, lineNumber).
    * 12 curated `skill.bandit.<test_id>` Rules with explicit Verity
      severities and OWASP AST10 mapping (B102/B105/B106/B107/B301/
      B303/B310/B506/B602/B605/B607/B701).
    * De-duplication with the hand-written
      `skill.python_subprocess_shell_true` rule at the RuleMatch stage:
      when Bandit's B602 fires on the same (file, line), the hand rule
      is suppressed. The RuleDefinition title documents the supersedes
      relationship.
    * Engine gained a first-class Analyzer step: each analyzer is a
      distinct AnalysisPlanItem with its own ExecutionRecord. Timeout /
      malformed JSON / wrong version / oversized output all become
      `failed` executions with a specific reasonCode; Coverage reflects
      the failure.
- SARIF 2.1.0 exporter (`verity/sarif.py`):
    * `report.sarif` is written by every CLI review, in addition to
      JSON and HTML.
    * Byte-offset regions (no fabricated line/column); dual-evidence
      finds use `relatedLocations`.
    * `partialFingerprints.verityFindingOccurrence/v1` for stable
      identity across runs.
    * `run.properties.verity.coverage` explicitly says `insufficient`
      when coverage is not sufficient — so "0 results" cannot be mis-
      interpreted as safety.
    * Bandit tool appears as `run.tool.extensions[0]`.
    * No secret raw values, no host absolute paths in the output.
    * Offline structural validator `validate_sarif_shape` for tests.
- Round-3 gap fixes:
    * Unclosed frontmatter is now treated as **failed** (untrustworthy);
      dependent manifest rules become `blocked_by_upstream_failure`
      instead of firing on an empty synthesised manifest.
    * `verdict.subject == null` on insufficient coverage is explicitly
      documented, tested through JSON / HTML / SARIF projections.
- Dependencies: bandit 1.7.10 + its transitive deps (stevedore, rich,
  markdown-it-py, mdurl, Pygments) added to `requirements.lock` and
  `THIRD_PARTY_LICENSES.md`.
- Tests: 139 total (117 -> 139, +22 new).

## Round 3 (2026-07-18)  →  commit `d170954`
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
- No semgrep / YARA integration (bandit + gitleaks are now integrated as
  of rounds 4 and 5)
- No PatchSet apply — only proposal shape (Phase 6)
- No GitHub Action yet; SARIF file is produced but no CI workflow is
  bundled with the repo.
