# Active implementation: semantic breadth and Butler superiority gate

Status: **Round 67 complete: semantic review attempted by default (Provider
still required), per-Finding-Type independent sweep calls, multi-Provider
Validator majority voting, 2 more OSS ports, paraphrase-coverage probe tool.
Remote v6 and dynamic execution not started.**
Owner authorization: 2026-07-23

## Goal

Make controlled semantic review the current product-development priority.
Verity must account for every built-in check in the read-only Butler reference,
close meaningful semantic gaps without cherry-picking, and pass a neutral
same-case comparison gate. Prompt black-box and Skill sandbox work begin only
after the semantic gate is honestly ready.

Butler output is a hypothesis and comparison baseline, never a label source.
"Exceeds Butler" means Verity meets absolute quality thresholds and performs
better on the same independently labelled cases; architecture size, Finding
count, or a self-authored fixture score does not establish that claim.

## Required scope

1. Freeze Butler's complete 45-check built-in inventory at a pinned commit.
   Every item must be classified as covered, open, or explicitly not adopted;
   the comparison gate must reject any open gap.
2. Preserve the fourteen Round-55 controlled Finding Types and the five
   Round-56 additions, then close the remaining inventory gaps with bounded
   policies for role scope, workflow dependencies, field constraints, error
   responses, attention dilution, streaming recovery, multi-turn state,
   dangerous-domain safety, and source use.
3. Give every type an applicability test, confirm/reject/insufficient policy,
   structured allowlisted Evidence, and positive plus safe counterexamples.
   Reviewed content cannot alter policy, severity, or Provider configuration.
4. Keep consumed protocols v3, v4, and v5 immutable. Measure the current
   repaired product formally only on the fresh answer-hidden v6 holdout now
   frozen with `catalog_first` before its first remote observation. Fixed
   replay and consumed-v5 diagnostics prove contracts and guide repairs only;
   author labels never become superiority evidence.
5. Keep protocol v2 immutable and reproducible. Never retry its consumed
   Selection or expose its sealed Test labels.
6. Retain explicit call, token, spend, response-size, timeout, and egress
   limits for Verity, Butler, and label-review runs. Butler remains read-only
   and is fingerprinted against the crosswalk commit.
7. Permit a scoped superiority claim only after zero breadth gaps,
   independently derived digest-bound labels, and paired same-case observations
   pass both absolute and relative gates.

## Real-run and claim gate

No real Provider/model run occurs until trusted operator configuration names
the provider, exact generator and validator models, credential environment
variable, split, repetitions, call/token/spend budget, and local report path.
No local model dependency or weight is installed in this round without the
separate founder approval required by `AGENTS.md`.

Round 60 removes the Candidate Generator as a recall veto for nine historically
zero/weak-recall types by introducing one strongest catalog-owned hypothesis
only when bounded structured facts support it. Every hypothesis still requires
the independent Validator. Stage diagnostics now expose extractor, catalog,
generator and validator counts without source text or answer metadata.

Protocols v4 and v5 are consumed diagnostic evidence. v5 completed authorized
remote label, Verity, and read-only Butler runs, but its first Verity command
measured `model_only`, its legacy label attestation disagreed with 18
precommitted labels, and Butler failed the health gate. A stronger GPT-OSS/Qwen
diagnostic agreed on 108/112 cases; the repaired `catalog_first` product reached
precision 1.0, recall 0.990566, safe false-positive rate 0.0, and stability
0.990741 on that incomplete shared-consensus subset. These tuned-v5 numbers are
not formal evidence. The comparator now emits `labels_require_adjudication`
whenever blind consensus conflicts with precommitted hidden-holdout labels.

Round 58 closes an operational gap in the evidence path: two explicitly
separate, answer-hidden label-reviewer packets can now each be run through a
bounded eval-only runner that emits validated observations plus a budget audit.
The runner will not accept the `verity` or `butler` identities, sends no local
alias map or author labels, and the comparator rejects a reviewer configuration
that matches either evaluated system. This does not run a model or produce an
accepted label set by itself; trusted real-run authorization remains required.

Round 63 freezes local v6 at corpus fingerprint
`07f8ea85f39d5653554cce48bc037226c44779da10c369b755d9e7ecf3b73df4`.
All 112 cases reach their controlled extractor; the product-path contract is
exactly 56 catalog hypotheses for positive cases and 56 pre-model suppressions
for safe cases. Five blind packets exist locally and contain no answer or risk
metadata. The freeze explicitly records that remote v6 payload egress is not
authorized and no remote v6 observation has started.

Round 64 closes the product-path no-seed hole without consuming v6. Prompt
types with no deterministic extractor seed receive one bounded full-prompt
Candidate Generator call over only the registered type/subject catalog.
Unknown or duplicate output fails closed and every candidate still requires
the independent Validator. Risk controls now apply within their paragraph
rather than vetoing unrelated sections. The local v6 contract remains exactly
56 positive hypotheses, 56 safe suppressions, and zero unreachable positives.

Round 65 removes Web-only downgrade choices without changing CLI/evaluation
contracts. Web Skill reviews force `standard`; semantic review remains
explicitly enabled but forces `redacted_evidence`. Non-secret Provider
preferences persist in owner-only local JSON and the API credential persists
only in macOS Keychain. The browser receives only a `keySaved` boolean.

## Separately gated work not yet started

- Prompt black-box execution.
- Skill execution or V2 sandbox work.
- Automatic prompt/Skill rewriting.
- Treating Butler findings, model consensus, or Verity output as ground truth.
- Retuning or rerunning protocol-v2 Selection.
- Inspecting or consuming protocol-v2 sealed Test.
- Treating any additional v5 run as a fresh or formal holdout result.

## Ordered release sequence after the semantic gate

1. Keep the frozen v6 manifest, payloads, product strategy, and packets
   immutable. After separate remote authorization, derive independent
   strong-reasoning labels that either agree with the precommitted labels or
   enter adjudication.
2. Require a healthy Butler baseline before computing relative checks. Budget
   exhaustion, error rate above 5%, or successful-run coverage below 95%
   makes the baseline `not_eligible`; failed calls are never filtered out to
   inflate recall.
3. Build and verify the formal Butler-inspired user workflow on Verity's
   architecture. This workbench is complete; publish one trial release for
   external users only after founder acceptance.
4. After founder acceptance of the current workbench, start Prompt black-box
   and Skill sandbox work in parallel. Neither dynamic track may rewrite the
   consumed v5 evidence or the frozen v6 result.

## Round-64 engineering exit criteria

- The pinned crosswalk accounts for all 45 Butler checks and has zero open or
  not-adopted items.
- All twenty-eight current semantic Finding Types have positive and safe
  extractor/contract cases.
- Consumed v5 remains fingerprinted and explicitly non-formal after tuning.
- The hidden-holdout comparator blocks all 18 legacy v5 label disagreements.
- Final product-path diagnostics retain the four strong-reviewer disagreements
  as exclusions and expose the one Provider error as a false negative.
- Protocol v2 still loads and reproduces unchanged.
- Frozen v6 refuses superiority while independent labels, label-quality
  agreement/adjudication, or paired observations are missing.
- Frozen v6 has 112/112 extractor coverage, a 56/56 positive catalog path,
  a 56/56 safe suppression path, and zero payload overlap with v3/v4/v5.
- Two independent label-reviewer runs can be produced from answer-hidden
  packets without hand-authoring observation files, and cannot reuse the
  evaluated systems' configuration fingerprints.
- Full pytest and `python3 tools/verify_repo.py --require-clean` pass.

## Superiority-milestone exit criteria

- The Butler crosswalk has zero open gaps.
- Independent v6 labels come from distinct stable answer-hidden reviews;
  reviewer-name assertions alone are rejected and all use the same versioned
  catalog judgment policy.
- Blind reviewer consensus agrees with the precommitted labels on every case,
  or every disagreement has an explicit independent adjudication.
- Authorized Verity and Butler observations cover the same cases and frozen
  configurations.
- Verity passes absolute recall/false-positive/stability/error thresholds,
  recall and error are non-inferior to Butler, and safe false positives are
  strictly lower.
- Only after all criteria pass may Verity make a formal superiority
  claim. External release still requires founder acceptance of the workbench.
