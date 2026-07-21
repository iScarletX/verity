# Verity in-repo progress log

## Current state (short summary)

<!-- verify_repo.py: begin verified_against block -->
```yaml
verified_against:
  date: "2026-07-21"
  # Commit that was HEAD when the numbers below were measured. Must be
  # an ancestor of HEAD at verify time (or equal to it). This avoids
  # a doc trying to know its own future commit hash.
  commit: "f90b193b3142d7d167259482e599a367d89d5ee5"
  tests_collected: 379
  tests_passed: 379
  tests_skipped: 0
  verify_command: "python3 tools/verify_repo.py"
```
<!-- verify_repo.py: end verified_against block -->

**Capability matrix.** Kept in sync with `verity/report.py::review_to_dict`.
Strings below MUST match the runtime literals.

| Capability                          | Status            |
|-------------------------------------|-------------------|
| Static (deterministic) auditing     | `completed`       |
| Semantic (LLM-assisted) auditing    | `not_enabled`     |
| V1.5 Prompt black-box               | `not_implemented` |
| V2 Skill isolated sandbox           | `not_implemented` |

**Detection breadth baseline.** Runtime `completed` means planned checks ran; it does not mean complete detection. The machine-readable taxonomy records 17 official/candidate sources, 25 unified risks, 38 mapped runtime components and four mature-tool decisions. Current L0 breadth: 4 none / 12 signal / 9 partial. Current L1 breadth: 19 none / 5 signal / 1 partial. No risk is substantial/evaluated; V1.5 and V2 remain entirely none/not implemented.

**Corpus baseline.** The Corpus now has 26 synthetic L0 cases across 10 risks and six fixed semantic contract replays. Agent Skills conformance has four positive/safe pairs; other measured risks retain one pair. Reports remain reproducible, per-risk and score-free; labels remain `provisional_single_review`. This is a measurement foundation, not a broad accuracy claim.

**Next step.** Round 17 expands the controlled semantic risk catalog and replay/corpus breadth using the taxonomy and capability facts. It does not connect a real Provider, change Web API-key/model settings, implement V1.5, or execute Skills.

**What ships right now.** Read-only intake (prompt text or local Skill folder), deterministic Prompt + Skill rule engines, Bandit + gitleaks (pinned) subprocess integration, JSON / HTML / SARIF 2.1.0 reports, Chinese remediation catalog, experimental semantic pipeline plus bounded JSON-over-HTTPS Provider adapter (default OFF; trusted CLI configuration only), standalone CLI/Web review, and trusted Web-first Skill project identity/history with scope-aware five-state version diff.

**Deliberately absent.** No Web Provider-config surface. No Skill
execution or sandbox. No prompt black-box runner. No Semgrep / YARA. No
ZIP or GitHub-URL intake. No PatchSet apply (proposals only).

---

## Round history (append-only)

## Round 16 (2026-07-21) → implementation commit pending

- Corrected deterministic Skill metadata validation to the official Agent
  Skills living-spec snapshot retrieved 2026-07-21: exact root `SKILL.md`,
  1–64 lowercase/digit/hyphen name grammar, no edge/consecutive hyphens,
  exact package-directory match, description length/type, compatibility,
  string→string metadata and space-separated `allowed-tools` shapes.
- Preserved host-path privacy: Snapshot retains only a bounded final
  directory/browser-upload root component. It does not enter content digests,
  project identity or persisted history. Web rejects mixed upload roots and
  does not compare against a temp-directory name.
- Versioned name/description Rules to 2.0.0 with explicit v1 supersedes and
  added one controlled optional-field Rule. Safe legacy fixtures were migrated
  to spec-conformant directory names rather than weakening the rule.
- Added non-Finding Skill Capability Facts for narrow Manifest/Python-AST
  observations of file, process, network, credential, configuration,
  installation and tool capability. Facts never change severity/Coverage/gate,
  retain only relative paths and declare no-dataflow/no-runtime limitations.
- Expanded L0 Corpus from 20 to 26 cases. `VR-SKILL-001` now has four
  positive/four safe provisional cases with 4 TP/4 TN, 0 FP/FN; no breadth
  level was promoted. High/Critical positive baseline remains 5/5 detected.
- Evaluated mature tools without installing them: OSV-Scanner `adopt_next`
  only after offline advisory snapshot design; ShellCheck deferred for GPLv3
  distribution review; Semgrep OSS deferred pending a deny-by-construction
  local-rules/metrics-off/no-build wrapper and LGPL boundary; pinned Gitleaks
  retained but marked feature-complete/security-fix maintenance.
- L0 taxonomy moved from 5 none / 11 signal / 9 partial to 4 none / 12 signal /
  9 partial due only to capability facts. No Provider/V1.5/V2 behavior added.
- Full suite: 379 passed, 0 skipped; 38 mapped runtime components and four
  machine-validated detector candidate decisions.

## Round 15 (2026-07-21) → implementation commit `f90b193`

- Added a strict versioned Corpus manifest with 20 synthetic L0 cases: one
  risk-positive and one safe counterexample for each of ten currently
  measurable risks. Every case carries independent risk-id answers,
  object/language, rationale, expected severity, provenance, licence and
  `provisional_single_review` label status.
- Added exact-byte duplicate/payload/path/symlink/budget/Schema hygiene gates;
  existing developer fixtures cannot be copied verbatim into the Corpus.
- Added a real offline evaluator that runs Verity twice per case and reports
  per-risk TP/FP/TN/FN, precision, recall, safe false-positive rate,
  deterministic stability, language/object coverage, explicit unsupported vs
  unmeasured states, and separate High/Critical misses. It intentionally emits
  no aggregate safety score.
- Added six fixed Provider replays (confirmed/rejected pairs for all three
  current semantic Finding Types). They exercise Candidate → Validation →
  Assessment → Finding contracts while declaring `modelQualityMeasured=false`;
  no network/model call is made.
- Added separate reproducible L0 and semantic-contract reports plus
  `tools/run_corpus.py --check`. `verify_repo.py` now reruns and verifies both
  baselines on every local/CI gate.
- First minimal paired baseline: 20/20 deterministic runs stable; ten measured
  risks each pass their one positive/one safe pair; 5 High/Critical positive
  cases detected. This is explicitly too small and single-reviewed for broad
  accuracy claims; 10 risks remain unmeasured and 5 unsupported at L0.
- Artificial cross-scope mapping discovered during report audit was removed:
  the Skill wildcard Rule now maps only to the Skill capability risk, not the
  parallel Prompt risk.
- Full suite: 361 passed, 0 skipped. No detector, Provider, V1.5 or V2 behavior
  was added.

## Round 14 (2026-07-21) → plan `9dc88f2` + implementation commit `2831270`

- Established a primary-source-first baseline: OWASP LLM 2025, the 2025
  Agentic threat paper and separate 2026 Agentic Top 10 framework, NIST AI RMF
  and GenAI Profile, MITRE ATLAS/CWE/CAPEC, SLSA, OpenSSF Scorecard, Agent
  Skills, MCP security guidance, and mature detector documentation/candidates.
  Sources carry version/date/URL/usage basis and controlled identifiers; text
  is paraphrased, not copied wholesale.
- Added 25 stable Verity risk ids spanning Prompt, Skill, MCP and audit
  governance. Every risk declares source crosswalks (or one explicit
  Verity-original rationale), layer-specific conclusion boundaries, honest
  current breadth and visible gaps.
- Separated execution status from capability breadth. Round-14 breadth is
  capped at `none`/`signal`/`partial`; code rejects `substantial` or
  `evaluated` without a corpus reference.
- Mapped all 33 deterministic Rules and three semantic Finding Types exactly;
  runtime registry drift now fails both tests and `verify_repo.py` through the
  new `detection_standards` gate.
- Corrected stale bare OWASP Prompt control ids to explicit 2025 mappings.
  Recorded that Agent Skills name validation is currently looser than the
  official specification and that Gitleaks upstream is now feature-complete/
  security-fix maintenance; these are Round-16 gaps, not hidden hotfixes.
- Front-page/report/architecture/eval docs now state that `completed` is one
  review's execution status, not complete detection. Provider production,
  new rules, V1.5 and V2 were not added.
- Full suite: 348 passed, 0 skipped; machine standards baseline: 17 sources,
  25 risks, 36 runtime detector mappings.

## Round 13 (2026-07-21) → commit `4e0b845`

- **Objective**: Add user-controlled advisory annotations to finding
  occurrences (fingerprints) within a project, without changing severity,
  counts, or default exit codes.
- **Design**: Dispositions are append-only metadata with mandatory expiry
  (max 180 days). Four statuses: `acknowledged`, `accept_risk`,
  `false_positive`, `wont_fix`. Default behavior unchanged; opt-in via
  `--respect-dispositions` for CI integration.
- **Implementation**:
  * Extended `history.py` with disposition storage, validation, rate
    limiting, and diff enrichment
  * Added CLI commands: `project dispose`, `project dispositions`
  * Added Web API: `POST/GET /api/projects/{ref}/dispositions`
  * Web UI shows disposition badges and inline form on diff
  * 9 new tests covering lifecycle, validation, gate behavior, and safety
- **Safety**: Dispositions cannot affect resolved/unknown_due_to_coverage
  findings. Symlinks, corruption, excessive events rejected. Notes sanitized.
- Full suite: 339 passed. V1.5 Prompt black-box and V2 Skill sandbox remain
  unimplemented.

## Round 12 (2026-07-20) → commits `ccfeafc`, `a00bb45` + owner-verification follow-up
- Added a Verity-owned Skill project registry. Opaque artifact identity is minted locally and inherited only from an existing trusted Web/CLI project context; reviewed names, paths, digests and content cannot establish identity.
- Added bounded immutable history under the gitignored `.verity-data/` directory with owner-only modes, strict schema/version parsing, duplicate-key/corruption/symlink/unsafe-mode rejection, atomic writes, record/project/version/total budgets, and an allowlisted projection that excludes raw content, evidence/Secret data, Provider wire data, credentials, RedactionMap, and host/temp/tool paths.
- Reworked baseline disappearance semantics to consult relevant parser/analyzer/rule executions. Five states (`new`, `existing`, `changed`, `resolved`, `unknown_due_to_coverage`) are exposed; relevant failures cannot become resolved, and artifact/scope mismatch is rejected.
- Added Web-first project list/create/project version submission/history/diff APIs and UI while preserving standalone Prompt/Skill review, loopback/CSP/no-`innerHTML`, upload budgets and temporary cleanup. Added the shared-core minimal CLI project create/list/review/diff surface.
- Initial implementation added six Round-12 tests. Independent owner
  verification then added twelve more adversarial/behavioral checks and
  fixed the issues they exposed:
    * history root symlinks are rejected before any chmod can affect their
      targets; project metadata, opaque version ids, nested review
      projections, counts, enums, and the versions directory are all
      validated before use;
    * project/version/total-size budgets and interrupted atomic writes are
      behaviorally tested; concurrent version appends are serialized;
    * the persisted-history workflow proves all five diff states, including
      relevant Bandit failure producing `unknown_due_to_coverage`;
    * same-name/same-content projects stay distinct, and artifact-supplied
      project/baseline fields cannot override the trusted Web project URL;
    * project uploads use the same path-escape defense as standalone
      uploads; both reject duplicate/case-colliding paths before write,
      and project review defaults to the `standard` secret-scan profile;
    * CLI project review now preserves the normal 0/1/3 gate semantics;
    * Web diff renders safe per-finding expandable details, not only counts.
- Full owner-verified suite: 330 passed, 0 skipped. Disposition/Suppression,
  V1.5 Prompt black-box, V2 Skill sandbox, Agent runtime and MCP remain
  unimplemented and outside Round 12.

## Round 11 (2026-07-20) → commit `0c582bc`
- **First controlled real semantic Provider transport**, closing the gap
  between the Round-8 containment scaffold and a usable opt-in L1 path:
    * separate role-bound `JsonCandidateGeneratorProvider` and
      `JsonValidatorProvider` classes behind the existing protocols;
    * explicit wire contract at `/v1/verity/candidate-generator` and
      `/v1/verity/validator` with model, role, and sanitized input;
    * remote HTTPS only (loopback HTTP allowed for trusted local/test
      Providers), URL credential/query/fragment rejection, redirects
      refused, system TLS validation, bounded timeout/request/response;
    * credential values resolved only from validated environment-variable
      names at call time; values never enter config serialization, JSON
      body, stdout/stderr, report, SARIF, or payload audit;
    * strict JSON parser rejects invalid UTF-8/JSON, duplicate object
      keys, non-finite numbers, and non-object roots before the existing
      candidate/validator JSON Schemas run;
    * Provider HTTP/network/error bodies are reduced to controlled reason
      codes and are never reflected into reports.
- CLI trusted configuration for both roles. All four URL/model values are
  required together; incomplete configuration is a usage error. API keys
  cannot be passed as CLI values, only as environment-variable names.
- Gate correction discovered during owner review: when a user explicitly
  requests `--semantic`, only semantic status `completed` may exit 0.
  `provider_not_configured`, transport/schema failure, or budget exhaustion
  produces `gate=coverage_block` / exit 3 unless a High/Critical finding
  already produces the stricter exit 1.
- Semantic orchestrator now marks generator/validator transport failures
  and schema violations as top-level semantic `failed` instead of leaving
  a misleading `completed` status around failed plan items.
- Web remains intentionally unconfigured for real Providers this round;
  its copy now states that real Provider use requires trusted CLI config.
- Corrected stale README limitations that incorrectly said gitleaks,
  semantic generation/validation, and repository CI were absent.
- Round 10 was formally archived with commit `3451b3b`.
- Tests: 288 → 312 passing (+24 Provider/config/transport/CLI E2E and
  failure-containment tests), 0 skipped. The E2E test uses a local fake
  HTTP Provider; CI does not call a public network Provider.

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

## Round 10 (2026-07-20) → this commit
- **Collapse handover set** to the minimal 8 files, per user request:
    * removed: `CLAUDE.md`, `docs/SESSION_START.md`,
      `docs/CURRENT_STATE.md`, `docs/COLLABORATION.md`,
      `docs/spec/*` (both snapshot copies + empty dir),
      `.githooks/*` (opt-in hook + README + empty dir),
      `plans/TEMPLATE.md`.
    * merged into `AGENTS.md`: the Session-Start / Session-End flows
      and the standard handover prompts that previously lived in
      `docs/SESSION_START.md`.
    * merged into `docs/PROGRESS.md`: the `verified_against` block,
      capability matrix, and short state summary that previously
      lived in `docs/CURRENT_STATE.md`. History remains append-only.
    * moved into `docs/MEMORY.md`: the public-safe collaboration
      preferences that previously lived in `docs/COLLABORATION.md`.
- `tools/verify_repo.py` updated to reflect the new file layout:
    * `REQUIRED_FILES` now names the minimal 8-item set
      (no CLAUDE, no SESSION_START, no CURRENT_STATE, no spec/*,
      no plans/TEMPLATE, no .githooks/*).
    * `check_current_state_block` renamed to
      `check_progress_verified_block`, reads the block from
      `docs/PROGRESS.md` top.
    * `capability_matrix_matches_runtime` reads the matrix from the
      PROGRESS top block.
    * `check_agents_md_has_ssot` updated section titles.
    * self-tests in `tests/test_verify_repo.py` follow.
- README / ARCHITECTURE / evals README updated to point at PROGRESS
  and MEMORY instead of the removed files.
- No product surface change; no new Python dependencies.
- Tests: 288 -> 288 passing (same count; the verify-repo self-tests
  were renamed/adjusted, none added or removed).

## Round 9 (2026-07-20)  →  commit `c8175e9`
- **Handover system + machine gates** (no product functionality
  change). See `plans/archive/round-9-handover.md` (this round's
  plan, filed by rule at end of round).
- New / substantially rewritten SSOT files:
    * `AGENTS.md` — canonical rules for any AI agent working on Verity
    * `CLAUDE.md` — thin pointer at `AGENTS.md` (no rule duplication)
    * `docs/CURRENT_STATE.md` — machine-readable snapshot with a
      ``verified_against`` YAML block (commit + test counts); the
      commit is required to be an *ancestor* of `HEAD` at verify time
      so no self-reference trap
    * `docs/SESSION_START.md` — new-agent onboarding + the canonical
      handover prompts (long + short)
    * `docs/ARCHITECTURE.md` — one-page component + bright-line map
    * `docs/LESSONS.md` — append-only pitfall ledger seeded with
      seven concrete lessons from earlier rounds
    * `docs/COLLABORATION.md` — public-safe collaboration preferences
    * `docs/spec/ENGINEERING_SPEC-v0.3.md` — in-repo snapshot of the
      external spec, with a snapshot-header explaining the sync rule
    * `docs/spec/REUSE_DECISIONS-v0.2.md` — same treatment for the
      mature-project reuse decisions table
    * `plans/ACTIVE.md` and `plans/TEMPLATE.md`; archived plans live
      under `plans/archive/` with an explicit README saying we do NOT
      fabricate archived plans for Rounds 1–8
    * `evals/README.md` — how tests read as an AI eval suite; the
      directory is otherwise empty until V1.5 / real Provider work
- New machine gate `tools/verify_repo.py`:
    * 10 static checks (required files, AGENTS SSOT headers,
      CLAUDE-md-is-thin, CURRENT_STATE verified_against block,
      capability matrix agrees with runtime, no host absolute paths
      in docs, no full-literal secret patterns, pyproject + README
      pointers, .gitignore covers `.tools` + caches, CI YAML shape)
    * Runs full pytest by default; `--skip-tests` for doc-only edits;
      `--require-clean` for CI mode
    * `capability_matrix_matches_runtime` cross-checks that the
      status strings in `CURRENT_STATE` also appear as literals in
      `verity/report.py` — stops docs from drifting from code
    * Has its own tests (`tests/test_verify_repo.py`, 11 tests) that
      exercise each individual check against a fabricated failing
      scratch repo
- New CI gate `.github/workflows/ci.yml`:
    * runs on `push` and `pull_request`,
    * `permissions: contents: read` (no write, no secrets, no
      artifact uploads),
    * concurrency-cancel on same ref,
    * installs pinned deps + gitleaks 8.28.0 (verified SHA-256 via
      the checked-in installer),
    * finally runs `python tools/verify_repo.py --require-clean`
- Optional `.githooks/pre-push` + README explaining opt-in
  enablement (`git config core.hooksPath .githooks`). The project
  does NOT auto-install hooks or touch user git config.
- README no longer carries drifting test counts; it links to
  `docs/CURRENT_STATE.md` and to `AGENTS.md` / `SESSION_START.md`.
- Tests: 277 -> 288 passing (+11 for `tools/verify_repo.py`).
- No new Python dependencies.

## Round 8 (2026-07-20)  →  commit `4f421f9`
- **Semantic-review V1 scaffolding** (Evidence → SemanticCandidate →
  Validator → CandidateAssessment → semantic Finding), default OFF:
    * New package `verity/semantic/` isolated from the deterministic
      engine by convention AND by tests (architectural test asserts no
      deterministic module imports `verity.semantic`).
    * `SemanticConfig`: default `enabled=False`; enabling requires an
      explicit `egress_policy ∈ {metadata_only, redacted_evidence}`.
      `raw_full_artifact` is intentionally NOT implemented in V1.
    * Provider protocol split into two roles
      (`CandidateGeneratorProvider`, `ValidatorProvider`) that are
      always instantiated as separate objects (no shared state).
    * `base_url` restricted to `https://` or loopback `http`; API keys
      referenced by env-var name only (`ProviderCredentials.api_key_env`).
    * Semantic catalog with 3 controlled FindingTypes
      (`semantic.prompt.instruction_conflict`,
      `semantic.prompt.missing_output_contract`,
      `semantic.skill.declared_behavior_mismatch`) each with
      subject taxonomy, POLICY severity, fixed falsification question,
      OWASP AST10 mapping (honest empty when none), guidance entry.
    * Deterministic Evidence extractors seed each type; providers can
      only *reference* extractor Evidence, never invent new evidence.
    * Strict JSON Schema (`additionalProperties: false`) for candidate
      list and validation result. Extra fields => reject; unknown
      reason codes => reject; oversized rationale => reject.
    * Verity re-derives `candidateId` from subject + evidence
      occurrences + snapshot id; the provider cannot pin identity.
      Validator replies whose `candidateId` doesn't match fail the
      whole assessment (state = `validation_failed`).
    * Severity in confirmed findings comes from the semantic catalog's
      policy; Validators have no severity input at all.
    * **Data-egress gateway** drops sensitive Evidence, strips absolute
      paths, caps every string field, and records only sizes +
      SHA-256 in the payload audit — never the payload itself.
    * Hard budgets: max candidate-generation calls, max validation
      calls, max candidates per extractor / total, max evidence per
      candidate. Exhaustion is surfaced as `budget_exhausted` in the
      semantic run status; deterministic findings are unaffected.
    * Capability matrix in reports: static / semantic / promptBlackbox /
      skillSandbox = {completed, not_enabled, failed, not_implemented}.
    * CLI `--semantic --egress-policy …` opt-in.
    * Web MVP `POST /api/review/prompt` and `/skill` accept
      `semantic_enabled` + `egress_policy`; UI has a folded
      “实验性：语义审查（默认关闭）” block. Result page shows the
      capability matrix and semantic sub-block.
    * No real Provider is bundled. Opt-in without a Provider honestly
      returns `provider_not_configured` (status/finding view /
      capability matrix all say "semantic axis failed") — no silent
      success.
    * 38 new tests covering: default off, deterministic invariant
      under bad JSON / extra field / evidence forgery / candidate id
      spoofing / validator schema violation / rationale-too-long,
      confirmed vs rejected vs insufficient_evidence semantics,
      policy severity is enforced, no smuggled Finding via extra keys,
      egress metadata_only vs redacted_evidence, sensitive Evidence
      dropped by the gate, payload audit records only sizes/digest,
      budget exhaustion, provider role isolation, capability matrix
      projection, CLI + Web opt-in with provider_not_configured,
      architectural test that no deterministic module imports
      `verity.semantic`.
- No new Python dependencies.
- Total tests: 239 -> 277 passing.

## Round 7 (2026-07-20)  →  commit `8040bac`
- Controlled remediation catalog (`verity/guidance.py`): human-readable
  Chinese `plainTitle` / `whyItMatters` / `whatToDo` / `priority`
  (`P0`/`P1`/`P2`) for every built-in Prompt rule, Skill Manifest rule,
  hand-written Skill rule, curated Bandit `test_id` (12), and a
  gitleaks default. Unknown ids fall back to a neutral "please review
  manually" entry; PatchSet remains proposal-only.
- Guidance text is never part of subjectKey / fingerprint / identity.
  Registered coverage-check test asserts every FindingType (or an
  explicit dynamic entry) has a catalog record.
- View model / HTML report / SARIF export all carry the guidance:
    * view model: full `guidance` block per finding
    * SARIF: `verity.guidance.id`, `verity.guidance.priority`,
      `verity.guidance.plainTitle` under result.properties
    * HTML report: new Guidance column with title + priority pill +
      why-it-matters + numbered actions
- Structured next-step summary (`next_steps_summary`):
    P0 -> coverage_gap -> secret_scan_gap -> P1 -> P2 -> monitor.
    Coverage insufficient still wins the top-of-page headline.
- Web UI redesigned for non-technical users:
    * top-of-page tagline "能不能用、为什么、先改什么" and 3-step
      onboarding list
    * per-finding cards now show plain-language title + P0/P1/P2
      badge + why-it-matters + numbered action list; Rule id / OWASP /
      byte range moved into a folded "technical details" block
    * `#loading` region with `aria-live`; keyboard focus outlines
    * error messages translated to Chinese (machine `code` kept English)
    * findings client-side re-sorted by priority (P0 first)
- `GET /api/health` endpoint: booleans + versions + scope. No path,
  hash, env-var leaks.
- Launcher `tools/start_local_web.py` + `start-verity.command`:
    * resolves the project root from the script's own directory
    * Python version + package import pre-flight
    * refuses non-loopback host, refuses to kill port-in-use holders
    * runs uvicorn in the foreground; `Ctrl+C` stops it cleanly
    * `--no-browser` and `--check-only` flags; does NOT pip install
- 27 new tests (239 total). Covers catalog completeness, gitleaks
  guidance mentions rotate/revoke and never a secret, Bandit per-
  test_id specificity, next-step ordering, HTML/SARIF projection,
  frontend safety (still no innerHTML, no external URLs, aria
  attributes present), health endpoint shape and no-leak invariants,
  launcher check-only + non-loopback rejection + port-in-use error.
- No new Python dependencies.

## Round 6 (2026-07-20)  →  commit `455fd06`
- Local Web MVP for non-technical users (`python -m verity.web`):
    * Starlette 0.41.3 ASGI app + Uvicorn 0.32.1 runner
    * Loopback-only bind (`127.0.0.1` default; refuses other hosts)
    * Host/Origin allow-list, CSP `default-src 'none'; script-src 'self'`,
      `X-Content-Type-Options`, `Referrer-Policy: no-referrer`,
      `X-Frame-Options: DENY`, `Cache-Control: no-store`
    * Endpoints: `GET /`, `POST /api/review/prompt`,
      `POST /api/review/skill`, `GET /api/report/<id>/report.{json,html,sarif}`
    * Bounded LRU report store (capacity + TTL); random 128-bit review IDs
    * Every request path terminates in `verity.review.run_review`
      — no separate execution path, no LLM, no subprocess in the web
      layer itself; skill execution / sandboxing remain not implemented.
- Chinese-language web UI (`static/index.html` + `app.css` + `app.js`):
    * No CDNs, no external fonts, no `unsafe-eval`
    * All rendering via `textContent` / DOM APIs; no `innerHTML`
      assignments (architectural test enforces this)
    * Prompt tab + Skill folder-upload tab; explicit warning when
      `minimal` profile is selected
    * Result view maps to the CLI verdict + coverage + gate policy
- Safe multipart handling:
    * Path sanitiser mirrors intake rules (no `..`, no absolute path,
      no backslash, no NUL, no drive-letter, length cap)
    * Server writes upload into `verity-web-skill-<random>` tmpdir and
      removes it in a `finally` block
    * Per-file, per-request and total-size budgets (500 files, 512 KiB
      each, 8 MiB total, 12 MiB request wrapper)
- Errors:
    * JSON envelope `{ error: { code, message } }` for every failure
    * No stack traces, host paths, or Secret bytes ever reach the client
- 35 new tests (index/static assets, security headers, Prompt + Skill
  endpoints, path guards, budgets, tmpdir cleanup, report download
  including LRU eviction, view-model absolute-path/Secret leak scan,
  architectural no-subprocess test). Total tests: 177 -> 212.
- Dependencies added and pinned: starlette 0.41.3, python-multipart
  0.0.20, anyio 4.12.1, sniffio 1.3.1, uvicorn 0.32.1, click 8.1.8,
  h11 0.16.0 (all permissive licenses).
- Test-only additions: httpx 0.28.1 + httpcore 1.0.9 + certifi + idna.

## Round 5b (2026-07-20)  →  commit `cd2209b`
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
