# Changelog

All notable changes to Verity are recorded here. This file summarizes
user-facing and release-relevant changes; the authoritative, append-only
engineering record lives in `docs/PROGRESS.md`, and code history lives in git.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/),
and this project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-07-22 — Engineering preview (deterministic static auditor)

First tagged release. **Scope of this release: the deterministic static
auditor**, which the machine closure report (`evals/reports/v1-closure.json`,
policy v2.0.0) rates `release_candidate`. This is an honest engineering
preview: it does **not** claim evaluated detection accuracy, and its breadth
limits are disclosed in every review. The controlled semantic (LLM-assisted)
review is a **separate, experimental, default-OFF track** that is not part of
this release gate (see "Experimental / not in release scope" below).

### What ships

- **Read-only intake** — prompt text or a local Skill folder. No execution of
  the reviewed artifact, no dependency install, no network calls to the target.
- **Deterministic Prompt engine** — 7 rules (instruction-override marker,
  unfilled placeholder, system hardcoded secret, duplicate numeric assignment,
  control characters / bidi overrides, empty/whitespace, open-ended tool
  wildcard) with prompt-kind gating.
- **Deterministic Skill engine** — Agent Skills metadata validation (spec
  snapshot `retrieved-2026-07-21`), unsafe reference paths, unpinned
  dependencies, permission wildcards, external-instruction trust mode, script
  suffix mismatch, and Python `subprocess(shell=True)` AST detection.
- **Controlled Bandit integration** (pinned 1.7.10) — 12 curated test ids as
  subprocess, no shell, timeout + output caps, tmpdir staging with reliable
  cleanup.
- **Controlled gitleaks integration** (pinned 8.28.0, external binary, two-layer
  SHA-256) — raw secrets redacted before the adapter; never enter identity,
  reports, SARIF or exceptions. `standard` profile requires it; `minimal` is an
  explicit, warned opt-out.
- **Reports** — JSON, single-file CSP-protected static HTML, and SARIF 2.1.0
  with byte-offset regions and stable partial fingerprints.
- **Coverage & gate semantics** — coverage-insufficient never exits 0; CLI
  `gate=` marker with exit codes 0 / 1 / 3.
- **Explainable safety score** (0–100, deterministic, severity-capped) plus a
  separate A–D review-confidence grade and proposal-only remediation/re-review.
  A score of 100 is not a safety guarantee; grade A is intentionally unreachable.
- **Local Web MVP** for non-technical users — binds `127.0.0.1` only, strict
  CSP, no external assets, no `innerHTML`; plain-language Chinese verdict, next
  steps, finding cards and downloads. Skill project registry + bounded local
  history with five-state version diff.
- **Chinese remediation catalog** keyed by rule / Bandit test id / gitleaks rule
  id, with a safe neutral fallback.

### Experimental / not in release scope

- **Controlled semantic (LLM-assisted) review** — default-OFF, opt-in only with
  trusted CLI configuration; a bounded JSON-over-HTTPS Provider adapter. Status
  `experimental_not_ready`: the first frozen protocol-v2 Selection returned
  `not_eligible`, sealed Test is unconsumed, corpus labels remain single-review
  / independent-AI-review (not human expert review), and no unified risk has
  substantial/evaluated evidence. It does not gate this release.

### Deliberately absent

- No Skill execution or sandbox (V2), no Prompt black-box runner (V1.5).
- No ZIP / GitHub-URL intake. No Semgrep / YARA. No automatic PatchSet apply.
- No Web Provider-config surface. No accepted frozen Selection/Test accuracy
  result.

### Requirements

- Python 3.9+ (tested on 3.9.6; supported through 3.13). Pinned dependency
  locks; no runtime network calls. gitleaks 8.28.0 installed via
  `tools/install_gitleaks.py` for the `standard` Skill profile.

[0.1.0]: https://github.com/iScarletX/verity/releases/tag/v0.1.0
