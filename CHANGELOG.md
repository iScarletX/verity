# Changelog

All notable changes to Verity are recorded here. This file summarizes
user-facing and release-relevant changes; the authoritative, append-only
engineering record lives in `docs/PROGRESS.md`, and code history lives in git.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/),
and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Nine controlled semantic Prompt checks covering role scope, workflow
  dependencies, field and boundary constraints, error responses, attention
  dilution, streaming recovery, multi-turn state, dangerous-domain safety,
  and third-party source use.
- A deterministic structured-quote check for parse-breaking smart,
  single-quoted, or backtick JSON keys with explicit JSON-context and
  negative-example guards.
- Fifty-six fixed semantic contract replays and a 112-case answer-hidden
  Verity/Butler comparison corpus spanning all 28 semantic Finding Types.
- An eval-only `run-label-reviewer` operation that produces scrubbed,
  answer-hidden independent-review observations and a conservative budget
  audit for each shuffled reviewer packet.
- Catalog-owned, Validator-required hypotheses for nine historically
  zero/weak-recall semantic checks, plus scrubbed per-stage diagnostics.
- Backward-compatible protocol-v4 hidden-holdout support with versioned
  judgment policies shared across evaluated systems and label reviewers.
- A responsive Chinese review workbench with source-byte highlighting, Prompt
  draft editing, direct re-review, score/confidence, and report downloads.
- Evaluation-only `model_only` candidate generation, distinct from the
  product's catalog-first strategy.
- A hidden-holdout label-quality quarantine that blocks comparison claims when
  blind independent-AI consensus contradicts precommitted provisional labels.
- A strict local v6 holdout composer, product-path catalog audit, atomic hidden
  artifact builder, and freezer for five independently shuffled blind packets.
- A bounded full-prompt semantic recall sweep over only registered Prompt
  Finding Types and subjects when deterministic extractors produce no seed.
- Workbench stage diagnostics showing extractor, catalog, generator, and
  Validator counts without exposing source text or Provider responses.
- Owner-only Web Provider preference persistence with API credentials stored
  in the current macOS user's Keychain and never returned to the browser.
- Packet/map fingerprint binding for new hidden-holdout freezes and anonymous
  packet-driven Verity evaluation.

### Changed

- Independent answer-hidden label reviews now require two or three distinct
  configurations, an odd repetition count of at least three, and two-thirds
  decisive within-reviewer agreement across all planned runs. Non-decisive
  Provider results never contribute a vote.
  Two reviewers must agree; three reviewers use per-case majority
  adjudication. Transient Provider failures may be retried only under
  fingerprinted per-repetition and worst-case whole-run call limits.
- The pinned 45-check Butler crosswalk now has material Verity coverage for
  every item and no open breadth gaps.
- The taxonomy now contains 46 risks and 83 runtime mappings. The comparison
  gate requires 112 cases, 28 Finding Types, and 27 distinct risk ids.
- The comparison gate now rejects a purported independent reviewer whose
  frozen configuration matches Verity or Butler.
- The read-only Butler comparison adapter can run independent item/repetition
  tasks with a fingerprinted concurrency limit while retaining synchronous
  per-call budget reservation.
- Positive Provider errors and inconclusive decisions now count as false
  negatives in semantic recall. Butler comparisons require explicit
  non-exhausted run health, at most 5% errors, and at least 95% successful-run
  coverage before any relative metric or superiority claim is computed.
- Catalog hypotheses take precedence over competing model hypotheses for the
  same extractor seed, preventing duplicate same-type Findings and redundant
  Validator calls.
- Completed semantic Evidence is preserved through every report consumer, so
  semantic findings and remediation items can be positioned in the original
  Prompt or Skill file.
- Instruction-conflict generation now requires structured opposing constraints
  on the same target/stage; safe field contracts, HTTPS-valued output fields,
  explicit tool prohibitions, and natural-language tool grants are parsed with
  tighter precision.
- Comparison configuration fingerprints and budget sidecars now bind the
  selected `catalog_first` or `model_only` candidate strategy.
- Structured example-contract mismatches now normalize to the controlled
  subject taxonomy before validation. Compatible examples, conversational
  refusals, and short reference appendices gate off unsupported product-path
  hypotheses instead of allowing stochastic false positives.
- Matched conditional exceptions, documentation-only external references, and
  complete sensitive-data controls now suppress safe catalog-first cases before
  any model call. Fresh local v6 has 56 positive catalog hypotheses and 56 safe
  pre-model suppressions.
- Semantic mitigation controls are scoped to the paragraph that contains the
  risky operation, so an unrelated safe section cannot suppress a finding.
- Requested semantic reviews that fail or remain incomplete no longer display
  a numeric score or pass verdict; semantic claims and source positions are
  retained, static blocking outcomes remain visible, and HTML reports cannot
  fall through to a green banner.
- Web evidence rendering preserves sensitivity and never reconstructs secret
  evidence from the selected local source. Provider URL/key resolution is
  synchronized, and real HTTP retries are included in semantic budgets and
  payload audit counts.
- The Web workbench no longer exposes egress-policy or Skill-profile
  downgrade controls. Both standalone and project Skill reviews force the
  gitleaks-enabled `standard` profile, and semantic review forces bounded
  `redacted_evidence` egress even for stale clients.

### Security

- A superiority claim remains impossible without independent digest-bound
  label reviews, agreement with the precommitted hidden-holdout labels or
  explicit adjudication, and accepted repeated real observations for both
  systems.
- Butler budget snapshots are schema-validated and propagated into strict
  observation health metadata; an exhausted or error-heavy reference run is
  never presented as a valid high-recall baseline.
- The v6 freeze binds its manifest, corpus fingerprint, candidate strategy,
  label gate, and product-path contract while explicitly recording that remote
  payload egress is unauthorized and no remote observation has started.
- Catalog-sweep responses fail closed on unknown types, subjects, evidence ids,
  duplicate types, or schema violations; all accepted candidates still require
  an independent Validator decision.
- Saved Web API keys never enter persisted JSON, browser storage, command
  arguments, reports, logs, or responses; Keychain failures do not fall back
  to plaintext. A saved key cannot be reused after changing the Provider URL
  unless the user supplies a new key.

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
