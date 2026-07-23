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

### 2026-07-23 — Port authoritative OSS detection signatures instead of hand-rolling narrow ones

- **Symptom**: Verity's instruction-override detector matched only ~3
  hardcoded phrases and missed most real-world bypass phrasing; several
  well-known injection vectors (embedded chat-template tokens, markdown
  image exfil) had zero coverage — despite mature open-source projects
  having battle-tested signatures for exactly these.
- **Root cause**: Rules were written from first principles rather than
  ported from the de-facto reference corpora (NVIDIA garak, ProtectAI
  llm-guard, vigil-llm YARA). Reinventing a narrow subset guarantees
  lower recall than adapting a maintained one.
- **Fix**: Cloned 8 authoritative projects, extracted their real
  signatures, and adapted (not copied) the highest-value deterministic
  ones into Verity rules with attribution, tests, and corpus evidence.
- **Prevention**: For any detection class that a popular security tool
  already covers, start by mining that tool's actual rules/regexes/probe
  taxonomy; only hand-roll what none of them provide. Record the source
  project + license in the rule title.
- **Evidence**: Round 46 `prompt.embedded_system_role_marker`,
  `prompt.markdown_data_exfiltration`, upgraded
  `prompt.instruction_override_marker`; `/tmp/oss_audit/EXTRACTION.md`.

### 2026-07-23 — An architectural substring scan can false-positive on legitimate rule content

- **Symptom**: The item-7 isolation test asserted the bare word
  "requests" never appears in engine.py (to prove no `import requests`).
  A broadened bypass regex legitimately needed the word "requests" (as in
  "ignore previous requests"), breaking the test.
- **Root cause**: The test approximated "does not import X" with "substring
  X absent", which conflates import statements with ordinary rule text.
- **Fix**: Rewrote it to match actual `import`/`from ... import`
  statements via regex; also extended the forbidden set to
  httpx/urllib.request/socket. Still fully enforces the real invariant.
- **Prevention**: Assert the actual construct (import statement), not a
  substring proxy, when the substring can legitimately occur in data.
- **Evidence**: Round 46 `test_item_07_deterministic_finding_path_has_no_llm_import`.

### 2026-07-22 — A "suppressed by design" fallback rule needs its OWN failure-path test

- **Symptom**: Instrumenting every deterministic rule and running the full
  suite showed `skill.python_subprocess_shell_true` never produced a hit
  anywhere. It is a documented Bandit-B602 fallback, always correctly
  suppressed while Bandit succeeds -- but no test ever simulated Bandit
  failing to check the fallback path itself actually works.
- **Root cause**: Every existing test for this rule exercised the
  "suppressed" branch (Bandit succeeds) because that is the default/normal
  case in the test environment; nothing exercised the "active" branch
  (Bandit fails) that is the entire reason the fallback rule exists.
- **Fix**: Simulated a Bandit `timeout` failure with a stub runner and
  confirmed the hand-written rule fires at the documented severity; added
  a permanent test for it. The logic was already correct -- only the proof
  was missing.
- **Prevention**: For any "A supersedes/suppresses B" relationship, write
  tests for BOTH directions: A-active-so-B-suppressed, and
  A-fails-so-B-active. Proving only the common-path direction leaves the
  fallback's own correctness as an unverified assumption.
- **Evidence**: Round 41
  `test_python_subprocess_shell_true_fallback_fires_when_bandit_fails`.

### 2026-07-22 — A risk's knownGaps list can go stale the same way a README table can

- **Symptom**: `VR-SKILL-001`'s `knownGaps` claimed "name syntax is looser
  than spec", "no parent-directory match", and "no explicit spec version"
  — but all three were fixed by Round 16 and the taxonomy entry was never
  updated afterward.
- **Root cause**: Fixing a gap in code does not automatically update the
  standards taxonomy's prose description of that gap; nothing checks
  `knownGaps` text against runtime behavior.
- **Fix**: Verified each claim against current code/tests, then rewrote
  the list to only the parts still genuinely true (narrowed the fourth
  claim from a broad "no license/compatibility/metadata validation" to
  the accurate "no license field validation" — compatibility/metadata
  are already checked).
- **Prevention**: When using a risk's `knownGaps` as a backlog (Round 37's
  method), first verify each claim is still true before either acting on
  it or leaving it standing — the same document-drift risk as an exact-
  count table (Round 36) applies to any prose claim about current
  behavior.
- **Evidence**: Round 44 `standards/risks.json` VR-SKILL-001 update.

### 2026-07-22 — A curated mature-tool test id can be dead configuration for years if never exercised end-to-end

- **Symptom**: While adding a new Bandit-based rule, discovered that
  `skill.bandit.B303` ("weak MD5/SHA-1 hash", curated since Round 4) had
  **never actually fired on any input** on this repo's Python version.
  `hashlib.md5(...)` empirically produces Bandit test id `B324`, not
  `B303`, on Python 3.9+ — confirmed by reading
  `bandit.plugins.hashlib_insecure_functions`'s own docstring: "For Python
  versions prior to 3.9, this check is similar to B303 blacklist". B303's
  blacklist-based implementation is effectively retired for the Python
  range this project supports (3.9–13).
- **Root cause**: The rule was registered by reading Bandit's documentation
  and choosing a plausible test id, but no fixture or test ever ran a real
  `hashlib.md5()`/`hashlib.new('md5', ...)` call through the real Bandit
  subprocess to confirm the mapping. It shipped, passed every test for 35
  rounds, and simply never produced a Finding on real weak-hash code.
- **Fix**: Replaced `B303` with `B324` everywhere (registry, adapter map,
  guidance, required-set test). Added a real-subprocess regression test
  that hashes with MD5 and asserts the resulting testId is `B324`, plus an
  explicit assertion that `B303` never appears. Added a versioned corpus
  pair so the mapping has independent evidence, not only a unit test.
- **Prevention**: Any curated "mature external tool test id" mapping must be
  proven with at least one real-subprocess test that actually triggers that
  exact id on the target artifact — reading the tool's docs or changelog is
  not sufficient, because blacklist-vs-AST-based check migrations (as
  happened here between Bandit versions/Python versions) can silently swap
  which id fires for the same source pattern.
- **Evidence**: Round 39 `test_b324_weak_hashlib_md5_is_medium`,
  `skill-weak-hash-{positive,safe}` corpus pair.

### 2026-07-22 — A documented exact-count table with no asserting test will silently drift

- **Symptom**: README's "Recorded findings on the checked-in fixtures" table
  claimed `missing_refs_skill` produces 3 findings/2 high, and
  `python_shell_true_skill` produces 3/1. Actual runtime already produced
  4/2 and 4/1 respectively at a commit from *before* this session even
  started — an earlier round added a `directory_mismatch` Finding (likely
  via a fixture rename) without updating this table, and it went unnoticed
  for multiple rounds.
- **Root cause**: The table's exact counts were asserted nowhere in the test
  suite. Individual tests checked for the *presence* of specific finding
  types but never the *total* count, so an extra Finding from an unrelated
  change had nothing to fail.
- **Fix**: Corrected the table to current, re-verified counts with an
  explicit note distinguishing pre-existing drift from a new session's
  genuine addition (don't let a new true positive hide inside an
  already-stale number). Added a dedicated regression test asserting the
  exact (findings, high/critical) tuple per fixture.
- **Prevention**: Any doc claiming an exact reproducible number from running
  code needs a test that asserts that exact number, not just a related
  behavior. When adding a new rule, always replay it against every existing
  checked-in fixture and diff the counts before touching any doc table —
  this is how the pre-existing drift was found in the first place.
- **Evidence**: Round 36 `test_documented_fixture_finding_counts_do_not_silently_drift`.

### 2026-07-22 — A risk's own declared knownGaps is a ready-made backlog

- **Symptom**: Looking for the next detection gap to close required manual
  guessing about what might be missing.
- **Root cause**: Every risk in `standards/risks.json` already declares an
  explicit, honest `knownGaps` list (e.g. VR-SKILL-008 said "No TLS
  verification/transport matrix"). These were written when the risk was
  first registered but nothing forced anyone to revisit and close them.
- **Fix**: Treated a risk's own `knownGaps` text as a literal backlog item;
  verified the concrete mature-tool test id it named (Bandit B501) actually
  exists and fires as expected before adding it, rather than assuming.
- **Prevention**: Before inventing a new detector idea from scratch, first
  check whether the risk it would address already names the exact gap in
  its own `knownGaps` field -- it may already point at the specific mature-
  tool check id to adopt.
- **Evidence**: Round 33 `skill.bandit.B501`, VR-SKILL-008 knownGaps entry.

### 2026-07-22 — A corpus case is a stronger test than a hand-picked unit test

- **Symptom**: A new deterministic rule (Round 29's untrusted-input-boundary
  check) passed all its hand-written unit tests, but writing a realistic
  corpus positive/safe pair immediately found it silently missed a realistic
  positive case and silently false-positived on the realistic safe case.
- **Root cause**: Hand-written unit tests unconsciously use phrasing the
  author already knows the regex matches. An independently-written "realistic
  scenario" fixture is not biased that way and exposes marker-list/regex-gap
  precision bugs a same-author unit test cannot catch.
- **Fix**: Widened the input-acceptance marker list and the trust-boundary
  regex gap (20 -> 80 chars) to match realistic sentence structure; re-
  verified all existing unit tests stayed green (widening only reduces false
  positives of the rule, it cannot introduce new true positives elsewhere).
- **Prevention**: Any new deterministic rule should get a real corpus
  positive/safe pair (not only unit tests) before being called "measured" --
  writing the corpus case is itself a design-quality check on the rule.
- **Evidence**: Round 31 `prompt-untrusted-input-boundary-{positive,safe}`,
  VR-PROMPT-008 moved `unmeasured` -> `measured` (precision/recall 1.0).

### 2026-07-22 — Adding a corpus case must not silently break a frozen review-evidence assumption

- **Symptom**: Adding two new L0 corpus cases broke three call sites that had
  hard-coded "every L0 case is `independent_ai_review`" (the blind-review
  packet builder, the `verify_repo.py` evidence gate, and two tests).
- **Root cause**: The Round-22 independent-review attestation is a frozen,
  already-completed record over a fixed 54-item set. Nothing in the codebase
  distinguished "the reviewed set" from "the current corpus", so adding a
  case silently tried to widen the frozen set instead of being correctly
  excluded from it.
- **Fix**: `blind_review._source_items()` now explicitly filters L0 cases to
  `labelStatus == "independent_ai_review"`; the verify_repo gate asserts an
  explicit reviewed-count + provisional-count split instead of a blanket
  status; the new case is honestly `provisional_single_review`, never
  fabricated as reviewed.
- **Prevention**: When a corpus/evidence set is described as "frozen" or
  "attested", any code that iterates it must filter by review status, not
  assume every current member has already been reviewed. Growing a corpus
  must never silently expand a sealed evidence claim.
- **Evidence**: Round 31 `blind_review.py` docstring + filter,
  `verify_repo.py::check_independent_review_evidence`,
  `test_committed_attestation_binds_exactly_54_nonsealed_current_payloads`.

### 2026-07-22 — A candidate-line cap silently blinds an extractor on long real documents

- **Symptom**: A ~250-line real system prompt produced zero deterministic or
  semantic findings from Verity, while an external reviewer found a real
  security gap and a real instruction conflict in the same document.
- **Root cause**: `extract_instruction_conflict` hard-capped candidate lines
  to the document's first 16 lines before pairing them. Every unit test used
  short synthetic prompts, so the cap never showed a symptom until a long
  real-world document was actually tried. No error was raised — the stage
  just quietly produced zero seeds and zero model calls for that document.
- **Fix**: Anchor candidate selection on strong-constraint markers
  (must/never/必须/绝不/...) so long documents contribute in-bounds candidates
  from anywhere in the text, not only its opening lines; short documents keep
  the exact prior exhaustive behaviour (regression-tested byte-for-byte).
- **Prevention**: Any "cap to the first N units" bound on a variable-length
  real artifact needs at least one test using a realistically long document,
  not only short synthetic fixtures. A rule/extractor that can silently
  produce zero candidates on real input is a coverage risk even when its unit
  tests are green.
- **Evidence**: Round 29 `_select_conflict_candidate_lines`,
  `test_instruction_conflict_finds_seed_far_from_document_start`.

### 2026-07-22 — A Web API-key surface must reuse the audited env-var path, not hold raw keys

- **Symptom**: Exposing a Provider API-key field in the Web UI risks the key
  leaking into SemanticConfig serialization, reports, SARIF, the payload audit,
  logs, or an echoed response.
- **Root cause**: The provider stack was designed so credentials are referenced
  by env-var NAME and resolved at call time; a naive Web feature would instead
  carry the raw key value through config objects.
- **Fix**: The Web layer places the user's key in a random, transient
  `VERITY_WEB_KEY_*` env var, puts only that NAME on ProviderCredentials, and
  clears the var in a `finally`. Config objects never hold the value; tests
  assert the key is absent from config repr and that the env var is cleared
  (including on error paths). The default provider URL is assigned from JS so
  the page source keeps no external URL literal.
- **Prevention**: Never thread a raw secret through config/report objects. Reuse
  the env-var-name indirection, scope the secret to one call, and prove cleanup
  with tests — including the failure branch.
- **Evidence**: Round 27 `web/provider_web.py`, `test_web_provider_config.py`.

### 2026-07-22 — An OpenAI-compatible endpoint is not Verity's custom JSON contract

- **Symptom**: Wiring the Web semantic path to `JsonHttpProvider` produced
  `semantic: failed` against OpenRouter, because that provider POSTs to
  Verity-custom `/v1/verity/candidate-generator`, which OpenRouter does not
  serve.
- **Root cause**: Two provider adapters exist — the custom-contract
  `JsonHttpProvider` and the OpenAI-compatible `OpenAICompatibleEvalProvider`
  (`/chat/completions`). Only the latter matches OpenRouter and was the one
  used by the working protocol-v2 eval.
- **Fix**: The Web surface builds `OpenAICompatibleEvalProvider` objects; a real
  end-to-end review then completed and returned findings.
- **Prevention**: Match the adapter to the target's wire contract; smoke-test a
  real call before shipping a provider integration.
- **Evidence**: Round 27 web semantic E2E against gpt-5.6-sol.

### 2026-07-22 — Don't gate a solid deterministic release on an experimental, AI-unreachable blocker

- **Symptom**: V1 was stuck `not_ready` indefinitely. The deterministic static
  auditor was fully engineering-green, but the release decision was gated on
  semantic-path quality evidence — including "human/domain-expert review", which
  no AI agent can satisfy alone. Every extra model run looked like progress but
  could never flip the decision.
- **Root cause**: The closure policy conflated two scopes: a reproducible
  deterministic tool, and an experimental, default-off, probabilistic semantic
  feature with an accuracy-evidence program. One structurally-unreachable
  blocker in the second scope froze the first forever.
- **Fix**: Closure policy v2.0.0 scopes `decision` to the deterministic static
  auditor (release_candidate on green engineering acceptance, no evaluated-
  accuracy claim) and moves all semantic/accuracy blockers to a separate
  `semanticQualityTrack` with `inReleaseGate=false`. Every limitation is still
  reported; nothing was hidden or faked.
- **Prevention**: Scope a release decision to what is actually being shipped.
  Keep experimental, default-off features and their evidence programs on a
  separate, clearly-labelled track that does not gate the shippable core. Fix
  the *definition* of readiness openly; never fix it by relaxing evidence.
- **Evidence**: Round 25 `closure.py` v2.0.0, `evals/reports/v1-closure.json`
  `decision=release_candidate`, updated `test_round20_closure.py`.

### 2026-07-22 — Strong Calibration does not survive a frozen held-out Selection

- **Symptom**: `openai/gpt-4o-2024-11-20` scored recall 0.929 / safe FP 0.0 on
  protocol-v2 Calibration, but the frozen Selection returned `not_eligible`:
  recall 0.857 and safe false-positive rate 0.429 (tp=12/fn=2/tn=8/fp=6).
- **Root cause**: Calibration is the split you are allowed to look at, so it
  flatters a configuration. Only an unseen, gate-frozen Selection measures
  generalization; the two are different facts.
- **Fix**: Recorded the honest `not_eligible` result, kept V1 `not_ready`, and
  did NOT tune the model/prompt against the consumed Selection. Any quality
  improvement now requires a brand-new protocol version with fresh splits.
- **Prevention**: Freeze the gate before looking at Selection; treat one
  Selection run as one-shot and non-repeatable; never "retry for a better
  score"; never promote Calibration numbers as accuracy evidence.
- **Evidence**: Round 24 scrubbed selection report; `selectionGate.status=
  not_eligible`, policy v1.0.0.

### 2026-07-22 — Best-effort tmpdir cleanup can silently leak and flake the gate

- **Symptom**: On a fresh session the full suite failed once at
  `test_bandit_tmpdir_is_removed_after_run`, then passed in isolation and on
  reruns. `verify_repo.py`'s bundled pytest step went red intermittently.
- **Root cause**: `bandit_runner.py` removed its staging tmpdir with a single
  `shutil.rmtree(tmpdir, ignore_errors=True)`. A transient rmtree failure was
  swallowed and leaked a `verity-bandit-*` dir; the test then correctly saw a
  newly-leaked directory. The assertion also diffed the whole shared temp root,
  so unrelated leftovers could pollute it.
- **Fix**: Added `_remove_tmpdir_with_retry` (retry transient `OSError` with
  backoff, treat missing dir as success, swallow only as a last resort) and
  changed the test to check only dirs created by the current run.
- **Prevention**: In a `finally` cleanup, do not use `ignore_errors=True` as the
  first line of defense for a resource that a test/gate asserts is gone. Retry
  transient failures, and scope leak assertions to what the run itself created.
- **Evidence**: Round 23 `_remove_tmpdir_with_retry` + two retry unit tests.

### 2026-07-22 — Label review can invalidate a green model Selection

- **Symptom**: A frozen Selection passed every predeclared metric, but later
  blind review found two “safe” artifacts declared `fetch_and_follow` while
  claiming data-only handling.
- **Root cause**: The author label captured intended safety, not the actual
  artifact semantics; configuration fingerprints also omitted Corpus bytes.
- **Fix**: Corrected the artifacts, independently re-reviewed them, invalidated
  rather than re-scored protocol-v1 Selection, and added the selected Corpus
  digest to protocol-v2 configuration fingerprints.
- **Prevention**: Review labels before sealed reporting; bind every reviewed
  label to payload digest; never preserve a favorable score over a Corpus
  correction.
- **Evidence**: Round 22 digest-bound attestation and Selection invalidation.

### 2026-07-22 — Format repair must not mutate blind-review decisions

- **Symptom**: One blind reviewer produced invalid hand-written JSON, then
  reported changing present/absent counts during a supposedly format-only fix.
- **Root cause**: Decisions and serialization were not frozen separately; JSON
  repair became an untracked second review pass.
- **Fix**: Invalidated the entire reviewer result, replaced the reviewer with a
  new model family/new aliases, and required subsequent reviewers to use
  programmatic `json.dump` plus strict local validation.
- **Prevention**: Treat changed counts/hashes during format-only repair as
  evidence invalidation, not a clerical correction. Never hand-repair reviewer
  decisions in the main workspace.
- **Evidence**: Round 22 reviewer invalidation and replacement review.

### 2026-07-22 — Successful pip exit does not prove an installable package

- **Symptom**: Offline `pip install --target` returned zero but produced only an
  empty `UNKNOWN-0.0.0` distribution; `import verity` failed.
- **Root cause**: macOS system Python's old setuptools ignored modern PEP 621
  `[project]` metadata under `--no-build-isolation` without treating it as an
  error.
- **Fix**: Added a minimal legacy `setup.py` fallback aligned with
  `pyproject.toml`; an isolated offline test now imports the installed package,
  checks version/CLI and confirms Web static assets are packaged.
- **Prevention**: Installation acceptance must verify installed behavior and
  assets, not only the package manager's exit code; keep duplicate compatibility
  metadata locked by tests.
- **Evidence**: Round 20 offline package-install preflight.

### 2026-07-22 — Every completed Finding consumer needs one projection

- **Symptom**: Confirmed semantic High Findings affected score/remediation but
  were absent from CLI gate, Web headline, HTML table, SARIF and JSON verdict.
- **Root cause**: Each consumer independently read the deterministic
  `findings` list while semantic Findings remained in a separate report field.
- **Fix**: Added a read-only completed-Finding projection and routed all
  consumer surfaces through it; JSON verdict policy v2 includes semantic
  Findings only when the controlled semantic stage completed.
- **Prevention**: Keep engine isolation, but test one confirmed and one rejected
  semantic case across verdict/gate/score/Web/HTML/SARIF whenever a new report
  consumer is added.
- **Evidence**: Round 20 cross-format semantic parity acceptance test.

### 2026-07-21 — A safety score needs a separate confidence axis and hard severity caps

- **Symptom**: A single 0–100 number can make missing checks look safe, average
  away a Critical/High finding, or imply that `100` means complete safety.
- **Root cause**: Detected risk, execution Coverage and detector breadth are
  different facts but dashboards often collapse them into one score.
- **Fix**: Numeric score exists only with sufficient deterministic Coverage;
  Critical/High/Medium/Low impose 39/59/79/99 caps and deductions reconcile to
  unified risks. A separate B–D confidence grade lists semantic/profile/
  breadth/runtime gaps; A is deliberately unreachable today.
- **Prevention**: Never substitute zero or 100 for unavailable, never let a
  model author weights/severity, and never hide confidence limitations behind
  a score.
- **Evidence**: Round 19 score policy, Web/static-HTML parity and machine gate.

### 2026-07-21 — Historical scores must be recorded, not recomputed with a new formula

- **Symptom**: Recalculating an old version with today's policy can manufacture
  an apparent score improvement or decline that never existed at review time.
- **Root cause**: Findings may persist while mappings, weights, profile and
  capability scope evolve.
- **Fix**: History schema v2 stores an allowlisted score/confidence projection
  at creation. Schema-v1 remains readable but `scoreComparison` is explicitly
  not comparable; policy/Coverage mismatches are also refused.
- **Prevention**: Version every score policy, do not backfill old records, and
  keep five-state Finding diff authoritative over numeric movement.
- **Evidence**: Round 19 history compatibility/comparison tests.

### 2026-07-21 — Model-quality evaluation needs a sealed split and error-aware denominator

- **Symptom**: A fixed Provider replay proves schema/orchestrator contracts but
  can be mistaken for model accuracy; a safe case with no candidate or an
  `insufficient_evidence` answer can also be accidentally counted as a correct
  safe decision.
- **Root cause**: Contract correctness, extractor eligibility, model judgment
  and final product safety are different measurements.
- **Fix**: Added 42 disjoint synthetic cases across calibration, selection and
  explicit-consumption sealed test. Every case must produce a deterministic
  seed. Inconclusive and Provider/schema errors are reported separately and do
  not become true negatives; whole-run call budgets are checked before egress.
- **Prevention**: Never tune on sealed test results, never score a no-seed case
  as model quality, and never turn a mutable real-model run into a required CI
  baseline or aggregate safety score.
- **Evidence**: Round 18 protocol, eval-only adapter and scrubbed report tests.

### 2026-07-21 — Evaluation methods can transfer without importing execution frameworks

- **Symptom**: Skill optimization and game-agent benchmarks offer useful
  validation, replay and multidimensional scoring ideas, but directly adding
  them would execute games/skills, add mixed licences and cross Verity's V1
  read-only boundary.
- **Root cause**: Methodological relevance was being conflated with dependency
  or detector relevance.
- **Fix**: Adopted only protocol principles: held-out gates, bounded proposals,
  state-verifiable outcomes, deterministic replay and dimension-level reports.
  No external benchmark became a dependency or security standard.
- **Prevention**: For every research reference, separately record “method to
  borrow”, “code to reuse”, and “security evidence supplied”; do not infer the
  latter two from the first.
- **Evidence**: Round 18 eval documentation and unchanged dependency locks.

### 2026-07-21 — Semantic breadth must remain a closed catalog

- **Symptom**: Expanding from three semantic examples creates pressure to ask
  the model to “find anything suspicious”, which would let it invent category,
  severity, identity and evidence.
- **Root cause**: A free-form reviewer appears broad but has no falsifiable
  per-risk contract or stable evaluation boundary.
- **Fix**: Added only taxonomy-mapped Finding Types with fixed subject enums,
  policy severity, deterministic bounded seeds, falsification questions and
  confirmed/rejected replay pairs. L1 breadth remains mostly none/signal.
- **Prevention**: No semantic catch-all. Every future type needs a risk mapping,
  extractor, evidence sufficiency, safe counterexample and containment tests.
- **Evidence**: Round 17 catalog and 14-case semantic contract report.

### 2026-07-21 — Metadata-only review still needs controlled facts

- **Symptom**: Capability-based semantic review received file locations but not
  capability categories/operations under `metadata_only`, making meaningful
  comparison impossible without sending source snippets.
- **Root cause**: Egress allowed locations only and had no safe fact taxonomy.
- **Fix**: Round-16 Capability Facts feed Evidence metadata; egress forwards
  only `evidenceRole`, controlled category and operation, dropping every other
  field. Raw values and arbitrary metadata remain local.
- **Prevention**: Add semantic context through typed allowlisted facts, never by
  forwarding an entire ArtifactModel or Evidence metadata dict.
- **Evidence**: Round 17 egress whitelist/adversarial tests.

### 2026-07-21 — Official living specs need a dated snapshot boundary

- **Symptom**: Verity accepted uppercase/space/underscore Skill names and a
  case-insensitive or nested `Skill.md`, while the current Agent Skills spec
  requires exact root `SKILL.md`, a narrow lowercase-hyphen name grammar and
  directory-name equality.
- **Root cause**: Early rules used a locally invented permissive grammar and
  did not record which living specification snapshot they implemented.
- **Fix**: Parser/report declare `agentskills.io/specification` at
  `retrieved-2026-07-21`; required/optional fields and root-name intake were
  tightened. Rule versions explicitly supersede v1 and safe fixtures were
  migrated rather than weakening the official boundary.
- **Prevention**: Every living specification integration needs a source id,
  retrieval snapshot, migration declaration, positive/safe corpus cases and
  Web/CLI parity.
- **Evidence**: Round 16 Agent Skills tests and expanded Corpus.

### 2026-07-21 — Mature tools still need deny-by-construction wrappers

- **Symptom**: Semgrep appears to solve cross-language gaps, but its convenient
  defaults can fetch registry config/send metrics, and an explicit local-build
  option can execute reviewed project code. ShellCheck has GPLv3 distribution
  obligations; OSV results depend on mutable network advisory data.
- **Root cause**: Tool reputation was being considered before license,
  reproducibility and hostile-input containment.
- **Fix**: Added machine-readable adopt/defer decisions and mandatory controls.
  OSV is next only after an offline database-snapshot design; ShellCheck and
  Semgrep remain deferred pending license/boundary review.
- **Prevention**: Never integrate a scanner via its quick-start/default command.
  Pin local rules/data, disable network/metrics/build/autofix, scan staged
  copies, parse strict output and expose failures as Coverage gaps.
- **Evidence**: `standards/detector_candidates.json`.

### 2026-07-21 — A tiny passing corpus is not 100% detection accuracy

- **Symptom**: The initial paired corpus produced perfect TP/TN results for ten
  measured risks, which could be marketed as 100% precision/recall despite
  containing only one positive and one safe counterexample per risk.
- **Root cause**: Correct arithmetic does not make a small, author-labelled,
  detector-adjacent corpus representative.
- **Fix**: Reports declare `minimal_pair_baseline`, have no aggregate safety
  score, distinguish unsupported/unmeasured risks, report High/Critical misses
  separately, and mark every label `provisional_single_review`. Semantic fixed
  replays declare `modelQualityMeasured: false`.
- **Prevention**: Stronger coverage claims require larger independently
  reviewed, provenance/licence controlled, leakage-checked corpora and approved
  per-risk thresholds. Never turn a small green fixture suite into a product
  accuracy percentage.
- **Evidence**: Round 15 corpus manifest, reports and machine gate.

### 2026-07-21 — Evaluation labels must not be detector-owned

- **Symptom**: Existing unit tests are written around specific Rule ids and
  implementation boundaries, so reusing them as an accuracy benchmark would
  let detectors define their own answer key.
- **Root cause**: Pipeline regression testing and independent detection
  evaluation solve different problems.
- **Fix**: Corpus cases name stable risk ids only; detector output reaches the
  same ids through the independent Round-14 mapping. Exact-byte duplicates of
  existing developer fixtures are refused.
- **Prevention**: Keep case labels/rationales separate from Rule ids and retain
  train/test leakage gates as the corpus grows.
- **Evidence**: `verity.corpus`, Round-15 hygiene tests.

### 2026-07-21 — Execution completion is not detection completeness

- **Symptom**: Runtime and documentation used `static: completed`, which
  correctly meant planned checks ran but could be read as broad or complete
  risk coverage. The semantic pipeline also had strong safety tests while its
  catalog covered only three Finding Types.
- **Root cause**: Execution status and detector breadth shared one informal
  word instead of independent controlled axes.
- **Fix**: Added a machine-readable standards/taxonomy baseline with
  `none`/`signal`/`partial`/`substantial`/`evaluated`; runtime reports now say
  that `completed` is execution status only. Pre-corpus claims are capped at
  `partial` by code.
- **Prevention**: New detectors must map to a unified risk id; stronger breadth
  claims require a versioned corpus measurement.
- **Evidence**: Round 14 `standards/`, `verity.standards`, machine gate.

### 2026-07-21 — Standards identifiers require explicit versions

- **Symptom**: A Prompt secret rule still used `OWASP-LLM-06`, inherited from
  an older OWASP list, while OWASP 2025 defines LLM06 as Excessive Agency and
  uses LLM02/LLM07 for sensitive disclosure/system-prompt leakage.
- **Root cause**: A bare control number survived a standards revision.
- **Fix**: Source versions and retrieval dates are registered; Prompt control
  ids now use explicit 2025 labels. The 2025 Agentic threat paper and 2026
  Agentic Top 10 are recorded as separate sources.
- **Prevention**: Never map a detector to an unversioned mutable Top-10 number.
  Do not infer unavailable official subcontrols from secondary articles.
- **Evidence**: Round 14 source registry and Prompt registry correction.

### 2026-07-20 — Agent completion reports can race with Git visibility

- **Symptom**: The first independent check after a sub-agent report saw
  `HEAD` still at the planning commit and no code diff; minutes later two
  already-created Round-12 commits became visible at `HEAD`/`origin/main`.
- **Root cause**: The asynchronous task report and repository inspection
  were not observed as one atomic event. A status label also briefly said
  `completed` while the detailed report correctly said `blocked`.
- **Fix**: Re-check `git reflog`, `git log --all`, file object hashes, and
  `origin/main` before deciding whether work is absent or merely racing;
  then independently inspect and test the final commit.
- **Prevention**: Never accept or reject delegated work from the transport
  status alone. Require commit hash + push + CI evidence, and perform a
  second repository read when the report and Git state disagree.
- **Evidence**: Round 12 commits `ccfeafc`, `a00bb45` and owner follow-up.

### 2026-07-20 — Relevant coverage, not global coverage, decides resolution

- **Symptom**: The original baseline matcher could mark an old Finding
  `resolved` whenever overall current coverage was sufficient, even if
  the exact analyzer/rule needed for that Finding had failed.
- **Root cause**: Coverage was treated as one review-wide boolean rather
  than a mapping from each prior Finding to its required current plan
  items.
- **Fix**: Persist controlled required-plan-item ids per Finding and require
  those specific executions to be completed/not-applicable before
  `resolved`; otherwise emit `unknown_due_to_coverage`.
- **Prevention**: Future black-box, sandbox, and Agent-runtime comparisons
  must attach relevant execution scope to every historical result.
- **Evidence**: Round 12 persisted-history five-state E2E test.

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
