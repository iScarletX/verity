# No active implementation round — deterministic V1 is a release candidate

## Status

Stopped after Round 25 (closure policy v2.0.0). The **deterministic static
auditor** is now `release_candidate` (engineering preview, no evaluated-accuracy
claim). The **controlled semantic / evaluated-accuracy track** is separate,
experimental, default-off, `experimental_not_ready`, and NOT in the release
gate. Independent dual-AI review covers every non-sealed label but is not human
expert review. Protocol-v1 Selection is invalidated; the first frozen
protocol-v2 Selection returned `not_eligible`; sealed Test is unconsumed.

## Round 44 (done) — fix stale VR-SKILL-001 knownGaps

Three of four claimed gaps were already fixed by Round 16 (name syntax,
directory match, spec version) and never updated in the taxonomy. Fourth
claim narrowed to what's actually still missing (license field only;
compatibility/metadata already validated). Docs-only fix. 510 tests.
decision stays release_candidate.

## Round 43 (done) — close the audit campaign

Audited the remaining surfaces (9 Prompt rules, 7 semantic extractors,
semantic contract coverage, OWASP matrix, CLI exit codes): all clean.
Campaign summary (Rounds 39-43): instrumented 63 total claimed capabilities
across the whole repo; found exactly 2 real gaps (B303 dead since Round 4;
two untested-but-correct paths). Remaining unmeasured/unsupported risks
genuinely need new architecture (MCP intake, dataflow analysis), not more
corpus work -- already honestly documented. No product/rule/corpus change.
510 tests. decision stays release_candidate.

## Round 42 (done) — audit extended to capability facts

Instrumented capabilities.py's 7 fact categories across the full suite:
'configuration' had zero test coverage anywhere (same class of gap as
Rounds 39/41). Confirmed it works correctly when exercised, extended the
existing capability-facts test with a settings.yaml fixture + explicit
assertion. No product/rule change. 510 tests (extended existing test).
decision stays release_candidate.

## Round 41 (done) — audit extended to non-Bandit rules

Instrumented all 25 non-Bandit rules across the full test suite: 24/25 fired
at least once. The exception (skill.python_subprocess_shell_true) is a
designed Bandit-B602 fallback whose OWN failure-path had never been tested
(same class of gap as B303). Verified it fires correctly when Bandit fails
and added a permanent regression test. No product/rule change (logic was
already correct). 510 tests. decision stays release_candidate.

## Round 40 (done) — full Bandit id audit + permanent regression gate

Audited all 15 curated Bandit ids end to end; B303 was the only dead
mapping (fixed in Round 39). Added TestAllCuratedBanditIdsFireOnRealTrigger
(one real trigger per curated id + a self-consistency check against the
curated set) so a future silent mapping drift fails immediately. No
product/rule/corpus change. 509 tests. decision stays release_candidate.

## Round 39 (done) — CORRECTNESS FIX: B303 was dead configuration since Round 4

Most consequential fix of the session. skill.bandit.B303 (weak-hash rule,
since Round 4) never actually fired on any Python 3.9+ input -- Bandit's
real test id for hashlib.md5() is B324, not B303. No test ever exercised a
real hashlib call through real Bandit in 35 rounds. Replaced B303 -> B324
everywhere (registry/adapter/guidance/tests/mappings), added a real-
subprocess regression test + corpus pair. VR-SKILL-008 stays measured
(precision/recall 1.0, now 2 pairs). 508 tests. decision stays
release_candidate.

## Round 38 (done) — regression sweep clean + Bandit B314 (unsafe XML parser)

Swept every session-added rule against every checked-in fixture (incl. 3 not
previously checked): clean, only the already-documented missing_refs_skill
hit. Added B314 (VR-SKILL-007's parser-config sub-pattern, distinct from
pickle/yaml) with detector mapping, guidance, tests, and a 2nd corpus pair.
15 curated Bandit ids, 48 mapped components. 506 tests. decision stays
release_candidate.

## Round 37 (done) — new risk VR-SKILL-015 (SQL injection via string-built queries)

No existing risk fit Bandit B608 (CWE-89) precisely, so registered a new
risk rather than force-fitting. Added B608 to curated set (13->14),
detector mapping, guidance, tests, and a corpus pair (precision/recall
1.0). Extended CWE-4.20's pre-registered controls to include CWE-89 (the
loader rejects unregistered control citations by design). 27 risks total.
504 tests. decision stays release_candidate.

## Round 36 (done) — regression sweep + stale README table fix

Replayed all session-added rules against every checked-in fixture: no
unintended false positives; missing_refs_skill correctly gains one true
high finding (skill.sensitive_path_access on /etc/passwd). Found the
README exit-code table had ALREADY drifted before this session
(undocumented directory_mismatch finding from an earlier round). Fixed the
table and added a regression test locking in exact counts. No rule/corpus
change. 502 tests total. decision stays release_candidate.

## Round 35 (done) — corpus evidence for four more existing Skill rules

Added corpus pairs for VR-SKILL-005 (external instructions), VR-SKILL-007
(pickle/yaml), VR-SKILL-009 (urllib), VR-SKILL-010 (jinja2 autoescape) --
all previously unmeasured despite real detectors. All four now measured
(precision/recall 1.0 each), no rule changes needed. Corpus at 46 cases
(23/23), corpusVersion 1.5.0. decision stays release_candidate. 501 tests
total (no new test functions this round).

## Round 34 (done) — corpus evidence for two existing secret rules

Added corpus pairs for prompt.system_hardcoded_secret (VR-PROMPT-003) and
skill.fake_secret_fixture (VR-SKILL-011), both previously unmeasured despite
having detectors. Both now measured (precision/recall 1.0), no rule changes
needed. Corpus at 38 cases (19/19), corpusVersion 1.4.0. decision stays
release_candidate. 501 tests total (no new test functions this round).

## Round 33 (done) — close the TLS-verification known gap (Bandit B501)

VR-SKILL-008's own declared knownGaps said "No TLS verification/transport
matrix" -- added Bandit B501 (request_with_no_cert_validation, CWE-295,
high) to the curated set (12->13), + detector mapping + corpus pair + 2
real-subprocess tests + guidance entry. VR-SKILL-008 now measured
(precision/recall 1.0). decision stays release_candidate. 501 tests total.

## Round 32 (done) — close remaining evidence gaps (VR-SKILL-014, VR-PROMPT-010)

Added corpus positive/safe pairs for Round 30's sensitive-path rule and
Round 29's dangling-reference rule; both now `measured` (precision/recall
1.0), no rule-code changes needed. Corpus at 32 cases (16/16), corpusVersion
1.2.0. decision stays `release_candidate`. This closes the evidence-gap
follow-up started in Round 31 for all three rules added this session.

## Round 31 (done) — real corpus evidence for VR-PROMPT-008

Added a genuine positive/safe corpus pair for Round 29's new rule; writing it
honestly found and fixed two precision bugs in the rule (marker phrasing,
regex gap too tight). VR-PROMPT-008 now `measured` (precision/recall 1.0).
Fixed three call sites that assumed "every L0 case is independent_ai_review"
(blind_review packet builder, verify_repo gate, 2 tests) so the new
provisional case is honestly excluded from the frozen 54-item attestation.
decision stays `release_candidate`. No test-count change (499 total).

## Round 30 (done) — close a Skill-side coverage gap

Added `skill.sensitive_path_access` (maps VR-SKILL-014), closing a gap shared
with AgentLinter's `sensitive-paths` check that Verity had no equivalent for.
Corrected a stale README claim (AST06 was `none`, now honestly `partial`).
+6 tests (499 total). decision stays `release_candidate`.

## Round 29 (done) — close a real deterministic coverage gap

User reported Verity found zero issues on a real production system prompt
while an external reviewer found real ones. Root-caused two honest gaps: (1)
no rule existed for "declares external input acceptance, no trust-boundary
statement" (VR-PROMPT-008) — added `prompt.untrusted_input_boundary_undeclared`;
(2) the semantic instruction-conflict extractor hard-capped at the first 16
lines, silently blind on long real documents — fixed with marker-anchored
candidate selection, short-document behaviour unchanged. Also added
`prompt.dangling_section_reference` (new risk VR-PROMPT-010). +16 tests (493
total). decision stays `release_candidate`; this only strengthens the
deterministic scope. This is round 1 of an ongoing, user-directed push for
broader/more accurate detection — more rounds to follow in this session.

## Round 28 (done) — semantic UX: partial findings + transient retry

Web view now shows confirmed semantic findings + a `partial` flag when a run
fails midway (e.g. network_error), under a clear advisory banner; they never
leak into the main findings/score. Eval provider retries transient transport
errors (network/timeout/http) up to 3x, never logical errors. +5 tests
(477 total). Deterministic pipeline and release decision unchanged.

## Round 27 (done) — Web Provider-config surface (experimental semantic)

Owner-approved. Added a loopback-only Web surface to paste an OpenAI-compatible
base URL + key, list models, pick generator/validator models, and run an
EXPERIMENTAL semantic review. Key held in a transient env var, cleared after
use, never serialized/logged/echoed. Prominent UI warning that results are
advisory and below the frozen quality gate. Deterministic pipeline/gate/score
and the release decision are unchanged. +19 tests (472 total). Verified end to
end against real OpenRouter (gpt-5.6-sol).

## Round 26 (done) — v0.1.0 release prep + Web walkthrough

Added `CHANGELOG.md`, prepared tag `v0.1.0` (deterministic static auditor
engineering preview). Ran a real-user Web MVP walkthrough end to end (prompt
review, skill folder review, all report downloads, non-loopback refusal); no
residual process left. Documented one non-blocking API note: skill upload needs
folder-style relative paths. No product/rule/closure/security change.

## Round 25 (done) — scope the release decision

Rewrote `verity/closure.py` to policy v2.0.0 so the release decision covers only
the deterministic static auditor and turns `release_candidate` on green
engineering acceptance. Semantic/accuracy blockers moved to a separate
`semanticQualityTrack` (`inReleaseGate=false`) and are still fully disclosed.
Regenerated the closure report, updated tests/README/PROGRESS. No product code
path, rule, corpus or security boundary changed.

## Round 24 (done) — protocol-v2 first frozen Selection

Ran real protocol-v2 evaluation with `openai/gpt-4o-2024-11-20` (both roles,
temp 0, role Prompt v2.0.0, redacted_evidence, 2 reps). Calibration passed
strongly (recall 0.929, safe FP 0.0) but the frozen Selection returned
`not_eligible` against predeclared gate v1.0.0: recall 0.857 (<0.90) and safe
false-positive rate 0.429 (>0.20). tp=12/fn=2/tn=8/fp=6. Sealed Test untouched.
The consumed Selection result must not be used to tune protocol v2; improving
quality requires a NEW protocol version with fresh unseen splits.

## Round 23 (done) — gate determinism fix

Fixed a non-deterministic pytest failure that made `verify_repo.py` flake:
`bandit_runner.py` now removes its staging tmpdir with a retrying helper instead
of a single `shutil.rmtree(..., ignore_errors=True)`, and
`test_bandit_tmpdir_is_removed_after_run` scopes its leak check to dirs created
by the current run. Suite 451 → 453 passed. This did not touch the evidence
blockers below, which still require a human decision.

## Evidence now available

- 26/26 L0 cases: digest-bound `independent_ai_review`;
- 28/28 semantic Calibration/Selection cases: digest-bound
  `independent_ai_review`;
- initial valid reviewer agreement 46/54; eight exceptions independently
  adjudicated; two mislabeled artifacts corrected and independently re-reviewed;
- one initial reviewer invalidated after decision counts changed during JSON
  repair and excluded from all comparison;
- 14/14 fixed semantic contract replays remain reproducible and provisional;
- 14 sealed-Test cases remain provisional, unexposed and unconsumed;
- semantic-quality protocol v2 fingerprints the selected Corpus payloads;
- protocol-v1 Selection is `invalidated_by_label_adjudication` and may not be
  re-scored or used as release evidence.

## Remaining release blockers

- the controlled semantic path FAILED its frozen protocol-v2 Selection gate as
  configured (recall 0.857, safe FP 0.429); it is not release-quality now;
- AI cross-model review is not a substitute for human/domain-expert review if a
  public production-quality claim requires one;
- protocol v2 Selection is now consumed and cannot be re-scored or tuned;
- sealed Test remains provisional/unconsumed;
- no unified risk layer has approved substantial/evaluated breadth.

## Next authorized decision sequence

1. decide whether to invest in semantic-path quality at all; if yes, design a
   NEW protocol version (v3) with fresh, unseen Calibration/Selection splits —
   do NOT reuse or tune from the consumed v2 Selection cases;
2. decide whether public release requires human/domain-expert review of the 54
   non-sealed labels and arrange it independently if required;
3. for any new protocol version, run Calibration, freeze role Prompt/model/
   budget against a dated immutable revision, then run one Selection against
   predeclared gates;
4. only after an accepted frozen Selection, separately approve sealed Test
   consumption for final reporting;
5. promote risk breadth only under approved per-risk thresholds, then recompute
   V1 closure.

## Not authorized

- reviving or reinterpreting protocol-v1 Selection metrics;
- tuning protocol v1/v2 from the invalidated or consumed Selection cases;
- re-running protocol-v2 Selection to "retry" for a better score;
- exposing or consuming sealed Test before a new approval;
- calling AI blind review “human expert review”;
- V1.5, V2 sandbox or automatic remediation.

(Note: the loopback-only Web Provider-config surface for the EXPERIMENTAL
semantic path was owner-approved and shipped in Round 27. It is advisory only,
below its frozen quality gate, and does not change the deterministic pipeline,
coverage, gate or score, nor the release decision.)
