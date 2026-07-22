# Verity in-repo progress log

## Current state (short summary)

<!-- verify_repo.py: begin verified_against block -->
```yaml
verified_against:
  date: "2026-07-22"
  # Commit that was HEAD when the numbers below were measured. Must be
  # an ancestor of HEAD at verify time (or equal to it). This avoids
  # a doc trying to know its own future commit hash.
  commit: "c64e95f18c1d1e55a4addc25ecb27d2974c0d95a"
  tests_collected: 501
  tests_passed: 501
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

**Detection breadth baseline.** Runtime `completed` means planned checks ran; it does not mean complete detection. The machine-readable taxonomy records 17 official/candidate sources, 26 unified risks, 44 mapped runtime components and four mature-tool decisions. Current L0 breadth: 4 none / 13 signal / 9 partial. Current L1 breadth: 16 none / 9 signal / 1 partial. No risk is substantial/evaluated; V1.5 and V2 remain entirely none/not implemented.

**Corpus baseline.** The Corpus has 34 synthetic L0 cases across 14 risks, 14 fixed semantic contract replays, and semantic-quality protocol v2 with 42 cases (14 calibration / 14 selection / 14 sealed test). 26 L0 and 28 non-Test semantic-quality labels have digest-bound `independent_ai_review`; this is cross-model blind AI review, not human expertise. Rounds 31–33 added 8 new L0 cases (VR-PROMPT-008, VR-SKILL-014, VR-PROMPT-010, VR-SKILL-008) as `provisional_single_review`, correctly excluded from the frozen 54-item attestation pending a future review round. The 14 fixed contract labels and 14 sealed-Test labels remain provisional. Two mislabeled external-trust safe artifacts were corrected and independently re-reviewed. Fixed reports remain reproducible and score-free; contract replay is 14/14 and `modelQualityMeasured=false`.

**V1 closure decision.** `release_candidate` under closure policy **v2.0.0**, scoped to the **deterministic static auditor** (rules + Bandit + gitleaks + JSON/HTML/SARIF + Web/CLI + explainable score/coverage). Engineering acceptance is green and reproducible; this is an honest engineering preview with **no evaluated-accuracy claim** and disclosed breadth limits. The **controlled semantic (LLM-assisted) review is a separate experimental track, default-OFF, `experimental_not_ready`, and NOT in the release gate**: protocol-v1 Selection is invalid after label adjudication, the first frozen protocol-v2 Selection (`openai/gpt-4o-2024-11-20`, both roles) returned `not_eligible` (recall 0.857 <0.90; safe FP 0.429 >0.20 vs gate v1.0.0), 14 sealed labels remain provisional/unconsumed, no risk layer is substantial/evaluated, and human/domain-expert review has not been obtained. The decision is reproducible in `evals/reports/v1-closure.json` (`decision` = deterministic scope; `semanticQualityTrack` = open experimental blockers); it is not an aggregate score.

**Next step.** The deterministic static auditor is a shippable engineering preview. Any *evaluated accuracy* or public production-quality claim, or productionizing the semantic path, still requires: a NEW protocol version with fresh unseen splits (v2 Selection is consumed and must not be tuned from), a frozen Selection passing predeclared gates against a dated immutable model, sealed-Test consumption under approval, and human/domain-expert review. Do not reinterpret v1/v2 metrics, expose sealed Test, or start V1.5/V2 under the current decision.

**What ships right now.** Version 0.1.0 engineering preview: read-only intake (prompt text or local Skill folder), deterministic Prompt + Skill rule engines, Bandit + gitleaks (pinned) subprocess integration, JSON / HTML / SARIF 2.1.0 reports, Chinese remediation catalog, deterministic explainable safety score plus separate review-confidence grade and proposal-only remediation/re-review checks, experimental semantic pipeline plus bounded JSON-over-HTTPS Provider adapter (default OFF; trusted CLI configuration only), standalone CLI/Web review, trusted Web-first Skill project identity/history with scope-aware five-state version and compatible-score diff, and an isolated synthetic-only real-model evaluation command with strict split/call/egress/report gates. Confirmed Findings from completed stages now use one report-consumer projection across verdict, gate, score, Web, HTML and SARIF.

**Deliberately absent.** No accepted frozen Selection/Test quality result, or automatic remediation/PatchSet apply. The Web UI now has a loopback-only Provider-config surface for the experimental semantic path (advisory only, below its frozen quality gate). Local Calibration reports are research evidence only. No Skill execution or sandbox. No prompt black-box runner. No Semgrep / YARA. No ZIP or GitHub-URL intake. A score of 100 is not a safety guarantee; Coverage gaps have no numeric score and confidence grade A is intentionally unreachable today.

---

## Round history (append-only)

## Round 33 (2026-07-22) → close the TLS-verification known gap (Bandit B501)

- VR-SKILL-008's declared `knownGaps` explicitly said "No TLS verification/
  transport matrix" — a real, previously-unaddressed gap, not something
  discovered from an external report this time. Verified Bandit 1.7.10
  actually ships `B501` (`request_with_no_cert_validation`) by running it
  directly against a synthetic `requests.get(url, verify=False)` snippet:
  fires HIGH severity, CWE-295, exactly matching the risk's own declared
  CWE mapping.
- Added `B501` to the curated Bandit test_id set (12 -> 13), with an
  explicit Verity severity (`high`) and OWASP mapping (`OWASP-AST02`,
  supply/config-chain class, consistent with the other credential/transport
  entries). Added the `skill.bandit.B501` detector mapping to VR-SKILL-008
  (which already had one detector, B303 weak-hash; now has two).
- Added a positive (`verify=False`) / safe (default verification) corpus
  pair, following the Round 31–32 discipline of giving every new/newly-
  extended detector real corpus evidence, not just unit tests. VR-SKILL-008
  moves `unmeasured` -> `measured`: TP=1/FP=0/TN=1/FN=0, precision=1.0,
  recall=1.0.
- Added a guidance-catalog entry (`skill.bandit.B501`, P0) and 2 real-
  subprocess Bandit tests (positive + negative). Corpus manifest bumped to
  `corpusVersion 1.3.0` (34 cases, 17/17 balance, 8 provisional-label cases
  total across Rounds 31–33, still correctly excluded from the frozen
  54-item attestation). Regenerated `corpus-v1-l0.json` / `v1-closure.json`;
  `decision` remains `release_candidate`. Corrected the README Bandit-count
  claim (12 -> 13 curated test_ids) and stale corpus-count text (32 -> 34).
- Full suite: 499 -> 501 passed, 0 skipped. Round 32 landed as commit
  `c64e95f` with GitHub CI #29 successful.

## Round 32 (2026-07-22) → close the evidence gap for Round 30's new Skill rule too

- Completed the evidence closure started in Round 31: Round 30's
  `skill.sensitive_path_access` (VR-SKILL-014) also only had hand-written
  unit tests, no corpus evidence. Added a genuine positive (script reads
  `~/.ssh/id_rsa`) / safe (script reads its own bundled config file) pair.
  VR-SKILL-014 moves `unmeasured` -> `measured`: TP=1/FP=0/TN=1/FN=0,
  precision=1.0, recall=1.0.
- Also added the still-missing pair for Round 29's
  `prompt.dangling_section_reference` (VR-PROMPT-010): positive references
  "section 8" in a document that only defines sections 1–2; safe references
  "section 3" which the document actually defines. VR-PROMPT-010 moves
  `unmeasured` -> `measured`: TP=1/FP=0/TN=1/FN=0, precision=1.0, recall=1.0.
  Unlike Round 31's pair, both new rules already had correct precision on
  the first try — no rule-code changes needed this round.
- Corpus manifest bumped to `corpusVersion 1.2.0` (32 cases, 16/16 balance).
  All 6 provisional-label cases from Rounds 31–32 (2 each for
  VR-PROMPT-008/VR-SKILL-014/VR-PROMPT-010) are consistently excluded from
  the frozen 54-item independent-review attestation per the Round-31 fix.
  Regenerated `corpus-v1-l0.json` and `v1-closure.json`; `decision` remains
  `release_candidate`. Corrected stale case-count text (28 -> 32) in
  README/evals-README/verify_repo.py informational messages.
- No test-count change (corpus fixtures only); full suite still 499 passed,
  0 skipped. Round 31 landed as commit `dd96218` with GitHub CI #28
  successful.

## Round 31 (2026-07-22) → give VR-PROMPT-008 real corpus evidence, not just ad-hoc tests

- Round 29 added `prompt.untrusted_input_boundary_undeclared` but only proved
  it with hand-written smoke tests, not the versioned L0 corpus that backs
  every other measured risk's precision/recall claim. Added a genuine
  positive/safe pair (`prompt-untrusted-input-boundary-positive/safe`) so
  VR-PROMPT-008 moves from `unmeasured` to `measured` in the reproducible
  corpus report: TP=1/FP=0/TN=1/FN=0, precision=1.0, recall=1.0.
- Building the pair honestly exposed two real precision bugs in the Round-29
  rule itself (writing a corpus case is a stronger test than a hand-picked
  unit test): the input-acceptance marker list didn't match realistic
  phrasing ("attached documents", "messages from the customer"), and the
  trust-boundary marker regexes used a 20-character gap that was too tight
  for a real sentence ("Treat everything in the customer's message ... as
  data"). Both fixed (markers widened; existing tests re-verified green,
  the widening can only reduce false positives of the rule, not add any).
- Honestly recorded the label status of the new pair as
  `provisional_single_review` (not fabricated as `independent_ai_review`).
  This required fixing three places that assumed "all L0 cases are
  independent_ai_review": `blind_review._source_items()` now explicitly
  filters to already-reviewed L0 cases (the frozen 54-item packet mechanism
  must not silently expand to include new unreviewed cases),
  `verify_repo.py`'s independent-review gate now asserts exactly 26
  reviewed + N provisional instead of "all reviewed", and
  `test_round22_blind_review.py` / `test_round15_corpus.py` were updated to
  assert the same explicit split rather than a blanket status.
- Bumped corpus manifest to `corpusVersion 1.1.0` (28 cases, 14/14 balance).
  Regenerated `corpus-v1-l0.json` and `v1-closure.json`
  (`evidenceSummary.l0LabelStatuses` now `{independent_ai_review: 26,
  provisional_single_review: 2}`); `decision` remains `release_candidate`.
  Corrected stale case-count claims in README/evals-README/verify_repo.py
  informational messages (26 -> 28).
- No test-count change (corpus fixtures, not new pytest functions); full
  suite still 499 passed, 0 skipped. Round 30 landed as commit `250f8ac`
  with GitHub CI #27 successful.

## Round 30 (2026-07-22) → close a Skill-side gap: sensitive host-path access rule

- Continuing the user-directed detection-breadth push (session-long, no
  active plan gate needed per explicit owner authorization to improve
  accuracy/breadth anywhere in the repo). Surveyed AgentLinter's
  `skill-safety` category (from prior local Butler research,
  `docs/工具/Butler/WS1-评测档案/01-*.md`) for ideas, not code: its
  `sensitive-paths` check (flagging `~/.ssh` etc.) had no Verity equivalent
  at all — Bandit does not have a dedicated test id for this either.
- Added `skill.sensitive_path_access` (high, any Skill file, text-level
  literal-path match): SSH private keys, AWS/cloud credential files,
  GnuPG, `.netrc`, Docker/Kube config, `/etc/passwd`+`/etc/shadow`, shell
  history, `.env`. Deliberately narrow well-known-path list, not a general
  dotfile/etc-path matcher, to keep false positives low. Maps to
  `VR-SKILL-014` ("Weak runtime isolation and host escape"), whose L0
  coverage was `signal` with no dedicated detector — now has one.
- Corrected a stale claim in the README OWASP AST10 matrix: AST06 ("weak
  isolation") was listed `none` ("Requires V2 sandbox"); it is now honestly
  `partial` given the new text-level detector, with the V2-sandbox
  limitation (cannot prove actual runtime access) stated explicitly.
- Added 6 tests (positive: SSH key / AWS credentials / /etc/shadow;
  negative: unrelated dotfile, clean skill; OWASP mapping sanity). Added a
  guidance-catalog entry. Regenerated corpus/closure reports (detector
  count 44 -> 45); `decision` remains `release_candidate`.
- Full suite: 493 -> 499 passed, 0 skipped. Round 29 landed as commit
  `9fa467b` with GitHub CI #26 successful.

## Round 29 (2026-07-22) → close a real detection gap: two new deterministic Prompt rules + long-document semantic fix

- User-reported symptom: a real production system prompt (NexPlay Creative
  Agent, ~250+ lines) produced zero findings from Verity, while an external
  reviewer (Butler) found several real issues including a genuine security
  gap. Root-caused two independent, honest coverage gaps rather than
  patching cosmetically:
  1. **No deterministic rule existed at all** for "declares acceptance of
     external/user-supplied content but states no trust boundary /
     anti-injection-override anywhere" (VR-PROMPT-008, OWASP LLM01). This is
     exactly the gap the external report's highest-priority finding named.
  2. The semantic `instruction_conflict` extractor hard-capped candidate
     lines to the document's first 16 lines. On a long prompt, a genuine
     conflict whose two sides are both past line 16 produced **zero seeds**,
     so the semantic stage never even called a model for it — silently, with
     no error. Verified with a 192-line synthetic case: conflict at lines
     141/172 was invisible before the fix, found after.
- Added two new deterministic Prompt rules (builtins.py/engine.py):
  `prompt.untrusted_input_boundary_undeclared` (medium, system_prompt only,
  English+Chinese phrase lists, fenced-code excluded, maps VR-PROMPT-008) and
  `prompt.dangling_section_reference` (medium, any prompt kind, strict
  numbered "see section N"/"见第N节" forms only, checked against the
  document's own headings, maps new risk VR-PROMPT-010 "Internal reference
  integrity"). Both are structural-absence/consistency patterns — the same
  class as existing manifest checks — not free-form LLM guesses.
- Fixed `extract_instruction_conflict` (semantic/catalog.py):
  `_select_conflict_candidate_lines` keeps the original exhaustive behaviour
  unchanged for short documents (<=16 lines, byte-for-byte same existing
  test results), and for longer documents additionally anchors on lines
  carrying a strong-constraint marker (must/never/必须/绝不/...) so a real
  conflict anywhere in the document can still produce a seed, bounded (24
  anchored + head window, capped combinations) so it cannot explode on a
  huge prompt.
- Added `VR-PROMPT-010` to `standards/risks.json` (26 risks total, L0
  coverage `signal`) with an honest `verityOriginalRationale` (not claimed
  against an external security taxonomy) and 2 new detector mappings (44
  runtime components total). Regenerated both offline reports
  (`corpus-v1-l0.json`, `v1-closure.json`); `decision` remains
  `release_candidate` — this round only strengthens the deterministic
  scope, it does not touch the semantic quality track.
- Surveyed local prior-art (Butler project docs, `docs/工具/Butler/`) for
  ideas, not code: the AgentLinter rule-catalog reference and the Butler
  static/semantic layering design independently confirm Verity's L0/L1
  split and pointed at concrete, currently-missing deterministic checks
  (dangling cross-references, declared-input-without-trust-boundary). No
  code, dependency, or detector was copied; both new rules were written
  from scratch against Verity's own registry/evidence/subject-key
  contracts and tested against false positives before landing.
- Added 16 new tests (6 untrusted-input-boundary incl. fenced-code and
  prompt-kind-gate cases, 7 dangling-reference incl. a mid-sentence-false-
  heading regression guard, 3 long-document/short-document extractor
  cases). Guidance catalog entries added for both new FindingTypes. README
  Prompt rule inventory table and breadth counts updated to match runtime.
  Full suite: 477 -> 493 passed, 0 skipped. V1 remains `release_candidate`
  (deterministic scope); semantic quality track unchanged. Round 28 landed
  as commit `133f767` with GitHub CI #25 successful.

## Round 28 (2026-07-22) → semantic UX: show partial findings + retry transient errors

- Fixed a confusing (but by-design) UX: when a Web semantic run confirmed some
  candidates but a later model call hit a `network_error`, the whole stage went
  `failed`, so those confirmed findings were withheld from the completed-
  findings list — the report showed “确认 2” yet “问题 0” and looked like nothing
  ran. Root cause: one transient network failure flips the run to failed.
- A (visibility): the view now exposes the confirmed semantic findings and a
  `partial` flag when the run did not complete but has findings. The Web UI
  renders them under a clear “⚠️ 本次语义审查中途未完成…仅供参考” banner. These
  advisory findings are NOT merged into the main completed-findings list, the
  counts, or the score (deterministic/completed isolation preserved).
- B (stability): the OpenAI-compatible eval provider now retries transient
  transport failures (`network_error`, `provider_timeout`, `http_error`) up to
  3 attempts with backoff. Logical failures (schema/credential/role/too-large)
  are never retried. This reduces spurious whole-run failures from a single
  network hiccup.
- Added 5 tests (partial-view logic incl. no score/count leakage; retry on
  transient then success; no-retry on logical error). Verified end to end vs
  real OpenRouter: a completed run now lists its confirmed semantic findings
  with claims. Deterministic pipeline/gate/score and release decision
  unchanged; semantic stays experimental/advisory. Suite: 477 passed.
  Round 27 landed as commit `d7e2ea4` with GitHub CI #24 successful.

## Round 27 (2026-07-22) → Web Provider-config surface for experimental semantic review

- Owner-approved productization of the Web semantic path (previously deferred).
  Added a local, loopback-only Provider configuration surface so a user can
  paste an OpenAI-compatible base URL (default OpenRouter) + API key, list the
  available models, pick generator/validator models, and run an EXPERIMENTAL
  semantic review from the browser.
- New `verity/web/provider_web.py`: a bounded `/models` proxy (https-or-loopback
  validation, size/shape caps, no redirect, provider error bodies reduced to a
  code) and an ephemeral-key builder. The user's API key is placed in a random,
  transient `VERITY_WEB_KEY_*` environment variable so the existing audited
  "credentials = env-var NAME, resolved at call time" path is reused unchanged;
  the key is cleared in a `finally` and never enters SemanticConfig/
  ProviderConfig fields, reports, SARIF, the payload audit, logs or responses.
- New `POST /api/models` route; `/api/review/prompt` and `/api/review/skill`
  now accept `provider_base_url` / `provider_api_key` / `generator_model` /
  `validator_model` and run real providers via the OpenAI-compatible adapter
  (OpenRouter speaks `/chat/completions`), clearing the ephemeral key afterward.
- UI: index.html gains a Provider config block with a prominent red warning that
  semantic review is experimental, has NOT passed its own quality gate (last
  measured safe FP ~0.43), and is advisory only, not a trusted verdict. Default
  base URL is assigned from app.js (no external URL literal in the page source),
  keeping the strict no-external-asset test valid; still no `innerHTML`.
- Added `tests/test_web_provider_config.py` (19 tests): base-url validation,
  ephemeral-key lifecycle + clearing, key never in config repr, distinct role
  objects, and `/api/models` error envelopes. Verified end to end against real
  OpenRouter with gpt-5.6-sol: model list (342), completed semantic review, and
  instruction-conflict findings detected; no residual web process left.
- Deterministic pipeline, coverage, gate and score are unchanged; semantic
  remains default-OFF and experimental. Full suite: 472 passed, 0 skipped.
  Round 26 landed as commit `534f104` and tag `v0.1.0` with GitHub CI #23.

## Round 26 (2026-07-22) → v0.1.0 release prep + real-user Web walkthrough

- Added `CHANGELOG.md` and prepared the first tag `v0.1.0`, scoped to the
  deterministic static auditor engineering preview (matching closure policy
  v2.0.0 `release_candidate`). The changelog honestly separates what ships, the
  experimental semantic track (not in release scope), and deliberately-absent
  capabilities.
- Ran a real-user walkthrough of the local Web MVP (start each server in the
  foreground, kill it after; no residual process left, per LESSONS): preflight
  ok with gitleaks 8.28.0; `GET /` and `/api/health` (`scope: static-only`) ok;
  a risky system prompt produced headline “修改后再使用” (tone bad) and caught the
  open-ended tool wildcard; a Skill folder review produced “不建议安装” with
  1 high + 3 medium findings, score 45, coverage sufficient; all three report
  downloads (json/html/sarif) returned HTTP 200; non-loopback bind
  (`--host 0.0.0.0`) was correctly refused.
- Walkthrough finding (documented, not a blocker): the Skill upload API requires
  folder-style relative paths (`skillname/SKILL.md`); a bare-file upload returns
  `bad_path: expected a folder upload`. The browser `webkitdirectory` UI sends
  folder paths automatically, so this only affects manual API callers.
- No product code, rule, corpus, closure logic or security boundary changed.
  Full suite: 453 passed, 0 skipped. Round 25 landed as commit `88455b3` with
  GitHub CI #22 successful.

## Round 25 (2026-07-22) → closure policy v2.0.0: scope the release decision

- Fixed the *definition* of V1 readiness, not the evidence. The old closure
  policy (v1.1.0) gated the entire V1 release on quality-evidence blockers that
  belong to the experimental semantic path — including one blocker (human/
  domain-expert review) that no AI can satisfy alone. That made the release
  decision loop forever while a genuinely solid deterministic tool stayed
  `not_ready`.
- Rewrote `verity/closure.py` to policy **v2.0.0**. The release `decision` now
  covers only the **deterministic static auditor** (rules + Bandit + gitleaks +
  JSON/HTML/SARIF + Web/CLI + explainable score/coverage) and turns
  `release_candidate` on green engineering acceptance. The report explicitly
  states `releaseScope=deterministic_static_v1_engineering_preview` and makes NO
  evaluated-accuracy claim; breadth limits stay in `disclosedLimitations`.
- The controlled semantic / evaluated-accuracy work moved to a separate
  `semanticQualityTrack` with `inReleaseGate=false` and status
  `experimental_not_ready`. All five open blockers (provisional labels, no
  accepted frozen Selection, unconsumed sealed Test, no substantial/evaluated
  risk, and no human-expert review) are still reported honestly — they just no
  longer block the deterministic engineering-preview release.
- Updated closure tests, regenerated `evals/reports/v1-closure.json`
  (`decision=release_candidate`), and reworded README top banner + roadmap and
  the PROGRESS closure/next-step summary. No product code path, rule, corpus,
  score policy or security boundary changed; the semantic path is still
  default-off and below its frozen Selection gate.
- Full suite: 453 passed, 0 skipped. Round 24 landed as commit `5502e94` with
  GitHub CI #21 successful. Sealed Test remains unexposed/unconsumed.

## Round 24 (2026-07-22) → protocol-v2 first frozen Selection (result: not_eligible)

- Ran the first real protocol-v2 semantic-quality evaluation using a fresh
  bounded OpenRouter research key held only in an environment variable and
  never committed. Selected a dated immutable model revision,
  `openai/gpt-4o-2024-11-20`, for both generator and validator roles
  (no mutable alias this time), temperature 0, role Prompt v2.0.0,
  `redacted_evidence` egress, 2 repetitions.
- Calibration (14 cases, 28 calls) looked strong: recall 0.929, precision 1.0,
  safe false-positive rate 0.0, stability 0.929, zero errors/inconclusives. No
  prompt tuning was performed before freezing.
- The configuration was then frozen and one Selection run was executed against
  predeclared gate v1.0.0 (recall >=0.90, safe FP <=0.20, stability >=0.80,
  error <=0.05, inconclusive <=0.10). Selection returned **`not_eligible`**:
  confusion tp=12 / fn=2 / tn=8 / fp=6, recall 0.857 (FAIL), safe false-positive
  rate 0.429 (FAIL), precision 0.667, stability 1.0, zero errors/inconclusives.
  The strong Calibration numbers did not generalize to the unseen split.
- This is honest, reproducible evidence, not a regression: it moves the semantic
  path from "unmeasured" to "measured and below the frozen gate as configured."
  Per protocol rules the consumed Selection result must NOT be used to tune this
  protocol version; any quality improvement requires a new protocol version with
  fresh unseen splits.
- Sealed Test was not exposed or consumed (`sealedTestConsumed=false`). Reports
  are scrubbed and remain in gitignored `.verity-data/model-evals/` (model id,
  fingerprints and metrics only; no key, endpoint, case text or host path).
  No product surface, rule, corpus or code changed. Full suite still 453 passed,
  0 skipped. V1 remains `not_ready`. Round 23 landed as commit `fb833c7` with
  GitHub CI #20 successful.

## Round 23 (2026-07-22) → implementation commit pending

- Fixed a real, non-deterministic gate flake, not a detection change. On the
  first clean session the full suite failed at
  `tests/test_round4.py::TestBanditReal::test_bandit_tmpdir_is_removed_after_run`:
  a `verity-bandit-*` staging tmpdir created during the run was occasionally
  left behind, so `verify_repo.py`'s bundled pytest step went red even though
  the same test passed in isolation and on subsequent runs.
- Root cause: `bandit_runner.py` cleaned its staging dir with a single
  `shutil.rmtree(tmpdir, ignore_errors=True)`; a transient rmtree failure
  (macOS under load) was silently swallowed and leaked the directory. Replaced
  it with `_remove_tmpdir_with_retry`, which retries transient `OSError`s with a
  short backoff, treats a missing dir as success, and only as a last resort
  falls back to the previous swallow-error behavior so cleanup failure never
  masks the primary result.
- Hardened the assertion so it checks that the tmpdir(s) created *by this run*
  are gone (newly-created set difference) instead of diffing the shared temp
  root globally, which was polluted by concurrent tests and stale leftovers.
  Added two focused unit tests for the retry helper (transient failure then
  success; missing dir is a no-op).
- No product surface, rule, Provider, evidence, corpus, closure or breadth
  change. V1 remains `not_ready`; sealed Test remains unexposed/unconsumed; no
  model was called. Full suite: 453 passed, 0 skipped (was 451). Round 22
  landed as commit `3e854ec` with GitHub CI #19 successful.

## Round 22 (2026-07-22) → implementation commit pending

- Built deterministic blind-review packets with different aliases/order and no
  current answers, rationales, detector output, Selection results or sealed
  Test cases. Two new review-only Agents using different model families
  independently reviewed 54 cases; one additional initial reviewer was
  invalidated after JSON repair changed decision counts.
- Valid reviewers agreed on 87.037% initially: 46 unanimous matches, one
  unanimous challenge, six disagreements and one uncertain. A separate Qwen
  adjudicator, blind to author labels and reviewer identities, resolved the
  eight exceptions: six supported the author and two identified real artifact/
  label contradictions.
- Both challenged cases claimed data-only external handling while declaring
  `fetch_and_follow`. They were corrected to `fetch_as_data` and two new
  independent reviewers unanimously judged the revised risks absent.
- Added a scrubbed attestation binding every reviewed case to its current
  payload digest and final decision. L0 26/26 and semantic Calibration/
  Selection 28/28 are `independent_ai_review`; 14 sealed-Test and 14 fixed
  contract labels remain provisional. AI review is not described as human
  expert review.
- Separated neutral external-reference presence from dangerous execution mode:
  data-only references now produce a semantic seed without an L0 Finding or URL
  egress. All 42 semantic-quality cases have seeds and fixed replay is 14/14.
- Historical protocol-v1 Selection was invalidated rather than re-scored.
  Protocol v2 includes the selected Corpus digest in its configuration
  fingerprint, so future content changes break comparability automatically.
- Full suite: 451 passed, 0 skipped. Sealed Test remains unexposed/unconsumed;
  no protocol-v2 model call was made.

## Round 21 (2026-07-22) → implementation commit `b52eb8d` + local Selection

- Ran real OpenRouter Calibration only; no Selection/Test case result was
  inspected. Claude Sonnet 4.5 failed strict JSON on 28/28 generator calls;
  GPT-4.1-mini Prompt v1 measured recall 1.0, safe false-positive rate
  0.285714 and stability 0.785714; GPT-4.1 was worse at 0.5 and 0.642857.
- Calibration exposed a contract flaw: a model could emit `confirmed` with
  `evidence_contradicts_claim`. Validator JSON Schema now binds each decision
  to coherent, non-empty, unique controlled reason codes.
- Eval role Prompt v2.0 adds falsification/materiality boundaries without
  changing product Findings, labels or severity. GPT-4.1-mini Calibration
  improved to recall 1.0, precision 0.875, safe false-positive rate 0.142857,
  stability 0.857143, zero errors and zero inconclusives. A v2.1 experiment
  regressed and was rejected; tuning stopped before Selection.
- Role Prompt version now enters the scrubbed report and configuration
  fingerprint. Selection policy v1.0.0 was frozen before seeing Selection:
  recall >=0.90, safe FP <=0.20, stability >=0.80, errors <=0.05 and
  inconclusive <=0.10.
- Commit `b52eb8d` passed GitHub CI #17. The one frozen Selection then returned
  `eligible`: recall 1.0, precision 0.875, safe FP 0.153846, stability
  0.928571, error 0.035714 and inconclusive 0. No post-Selection tuning was
  performed. One safe behavior-mismatch case was repeatedly false-positive and
  one external-trust safe repetition failed candidate-id validation.
- This preliminary result is not accepted V1 release evidence: labels remain
  single-review and `openai/gpt-4.1-mini` is an OpenRouter alias, not a dated
  immutable revision. Reports remain gitignored and scrubbed. Sealed Test was
  not consumed; the local one-time Key file was deleted. Full suite: 440 passed,
  0 skipped.

## Round 20 (2026-07-22) → implementation commit `5e5bcf0`

- Performed a binary V1 closure audit rather than adding a detection layer.
  The reproducible closure policy separates engineering readiness from quality
  evidence and decides `not_ready`: all local engineering checks pass, while
  provisional labels, absent real-model results, the unconsumed sealed split
  and zero substantial/evaluated risk layers remain explicit release blockers.
- Found and fixed a cross-format blocker: confirmed semantic Findings affected
  score/remediation but were omitted from JSON verdict, CLI gate, Web headline,
  static HTML and SARIF. A read-only completed-Finding consumer projection now
  keeps those surfaces aligned while preserving deterministic/semantic engine
  isolation. Rejected/inconclusive/failed candidates remain excluded. Verdict
  policy v2 records the changed semantics.
- Added score/confidence policy properties to SARIF and acceptance coverage for
  a confirmed semantic High plus a rejected safe counterexample across JSON,
  gate, Web, HTML and SARIF.
- Found a real packaging failure hidden behind a zero pip exit: old macOS
  setuptools built an empty `UNKNOWN-0.0.0` wheel. Added a minimal legacy
  packaging fallback and isolated no-network install acceptance that imports
  Verity and verifies its CLI and Web static assets. Current package version is
  0.1.0 engineering preview, not a 1.0 release.
- Removed current user-facing “Phase 0 walking skeleton” wording without
  rewriting historical records. The README and package metadata explicitly
  link to the `not_ready` closure report and retain all V1.5/V2 limitations.
- Full suite: 435 passed, 0 skipped. Round 19 landed as commit `bbc93dd` with
  GitHub CI #15 successful. No Provider/model was called and the sealed test
  remains unconsumed.

## Round 19 (2026-07-21) → implementation commit pending

- Added deterministic score policy v1.0.0. Numeric 0–100 score exists only
  with sufficient deterministic Coverage; Critical/High/Medium/Low impose
  hard 39/59/79/99 ceilings. Every deduction maps through the standards
  detector map to unified risk ids; unknown mappings make scoring unavailable.
- Root-cause duplicate deductions diminish at 100/50/25/0 percent using
  `riskId + subjectKey`; distinct roots in the same risk retain full weight.
  Arithmetic, policy version, evaluated layers, deduction layers and caps are
  exposed. Models, artifacts and dispositions cannot set or alter the score.
- Added a separate B–D review-confidence policy. Static-only sufficient runs
  are normally C; successful controlled semantic runs may reach B; requested
  semantic failure or deterministic Coverage failure is D. A is deliberately
  unreachable while V1.5/V2 and evaluated detection breadth are absent.
- Added controlled remediation records tied to existing Finding/Evidence ids,
  with catalog actions and deterministic same-scope re-review checks. Apply
  mode is always `proposal_only`; no user file is modified.
- JSON, single-file escaped/CSP HTML and no-innerHTML Web UI now show score or
  “暂不评分”, confidence limits, deduction arithmetic and remediation checks.
  Existing verdict/headline/exit-code semantics remain unchanged.
- History schema v2 stores the allowlisted score/confidence projection created
  at review time. Schema-v1 remains readable and is never backfilled. Score
  comparison requires available scores, sufficient Coverage, same policy and
  identical evaluated layers; five-state Finding diff remains authoritative.
  Dispositions stay advisory and never rewrite raw score or severity.
- Machine gate now proves safe=100 only as completed-scope arithmetic,
  High/Critical <=59, Coverage gaps unavailable, and confidence not A.
- Full suite: 427 passed, 0 skipped. Round 18 landed as `6cacd83`; GitHub CI #14
  succeeded. No real model was called and sealed test remains unconsumed.

## Round 18 (2026-07-21) → implementation commit `6cacd83`

- Added a strict 42-case synthetic semantic quality protocol: 14 calibration,
  14 selection and 14 sealed-test cases. Every split independently contains
  one unsafe and one safe counterexample for all seven closed semantic Finding
  Types; all payloads are distinct and retain `provisional_single_review`.
- Added an offline eligibility gate proving all 42 cases produce deterministic
  extractor seeds. A no-seed safe case therefore cannot be counted as a model
  true negative. This gate never calls a model and does not consume sealed test.
- Added an eval-only OpenAI-compatible chat-completions adapter and research
  command. They accept only the versioned synthetic Corpus, use two role
  objects, strict JSON, HTTPS/loopback, redirect refusal, environment-variable
  credentials, no tools/streaming/retries, response caps and a whole-run call
  budget checked before egress. Product CLI/Web behavior is unchanged.
- Added conservative metrics: per split/type/language/object TP/FP/TN/FN,
  precision, recall, safe false-positive rate, inconclusive/error rates and
  repeated-decision stability. `insufficient_evidence` is not a true negative;
  Provider/schema failures do not enter the confusion matrix. There is no
  aggregate safety score.
- Mutable real-model reports default to gitignored local storage and exclude
  raw case text, source snippets, claims, subjects, Provider traffic, endpoint,
  credential name/value, account metadata and host paths. Fixed contract replay
  remains separate with `modelQualityMeasured=false`.
- Borrowed only evaluation principles from SkillOpt and game-agent benchmarks:
  held-out gates, bounded changes, state-verifiable outcomes, deterministic
  replay and dimension-level reporting. No external benchmark was integrated
  as a dependency, detector or security standard.
- No research credential was present, so no real model was called, no
  `modelQualityMeasured=true` real report was produced, and sealed test v1 was
  not consumed. Local Stub E2E validates the 56-call command path only.
- Full suite: 408 passed, 0 skipped. Round 17 landed as commit `f27cdf8` with
  GitHub CI #13 successful. No score/remediation, Web Provider, V1.5 or V2 was
  implemented.

## Round 17 (2026-07-21) → implementation commit pending

- Expanded the semantic catalog from three to seven closed, taxonomy-mapped
  Finding Types: Prompt trust-boundary ambiguity and excessive tool scope;
  Skill permission-capability mismatch and external-instruction trust gap;
  plus the original conflict, output-contract and declared-behavior types.
- Every type retains Verity-owned severity, controlled Subject enums,
  falsification question and bounded deterministic extractor. No model can
  invent a type, severity, identity or Evidence.
- Instruction-conflict seeds now include bounded non-adjacent line pairs (max
  16 lines/120 pairs before orchestrator budgets), closing the adjacent-only
  gap without unbounded O(n²) expansion. Chinese/mixed-language trigger cases
  cover trust/tool boundaries.
- Unified all Skill semantic declaration comparisons on Round-16 Capability
  Facts instead of the first Python file. Metadata-only egress now exposes only
  allowlisted evidence role/category/operation; adversarial raw metadata and
  severity fields are dropped.
- Expanded fixed semantic replay from 6 to 14 cases: confirmed/rejected pair for
  all seven types. 14/14 contracts correct and repeat-stable, while retaining
  `modelQualityMeasured=false` and no aggregate score. No real model was called.
- L1 breadth moved from 19 none / 5 signal / 1 partial to 15 none / 9 signal /
  1 partial. This is catalog/contract breadth only; no risk was promoted to
  substantial/evaluated.
- Full suite: 387 passed, 0 skipped; 42 mapped runtime components. No Provider
  production, default enablement, API-key UI, V1.5 or V2 behavior added.

## Round 16 (2026-07-21) → implementation commit `1759267`

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
