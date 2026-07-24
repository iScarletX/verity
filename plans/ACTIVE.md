# Active implementation: semantic breadth and Butler superiority gate

Status: **breadth tranche complete; authorized real-run configuration pending**
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
4. Expand fixed Provider replay and fresh answer-hidden protocol-v3 cases
   whenever the catalog grows. Fixed replay proves contracts only; author
   labels never become superiority evidence.
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

The breadth tranche shipped stronger controlled coverage and a ready comparison
protocol without claiming semantic superiority. The crosswalk now has no open
gaps. "Verity exceeds Butler" remains blocked until fresh labels plus both
systems' same-case observations pass the comparison gate.

## Explicitly out of scope until this gate passes

- Prompt black-box execution.
- Skill execution or V2 sandbox work.
- Automatic prompt/Skill rewriting.
- Treating Butler findings, model consensus, or Verity output as ground truth.
- Retuning or rerunning protocol-v2 Selection.
- Inspecting or consuming protocol-v2 sealed Test.

## Engineering-tranche exit criteria

- The pinned crosswalk accounts for all 45 Butler checks and has zero open or
  not-adopted items.
- All twenty-eight current semantic Finding Types have positive and safe
  extractor/contract cases.
- Protocol v3 has 112 answer-hidden cases: two positive and two safe artifacts
  for every current Finding Type.
- Protocol v2 still loads and reproduces unchanged.
- Protocol v3 refuses superiority while independent labels or paired
  observations are missing.
- Full pytest and `python3 tools/verify_repo.py --require-clean` pass.
- Round 57 is committed and pushed; GitHub CI run 30066597051 is green.

## Superiority-milestone exit criteria

- The Butler crosswalk has zero open gaps.
- Independent v3 labels come from two distinct stable answer-hidden reviews;
  reviewer-name assertions alone are rejected.
- Authorized Verity and Butler observations cover the same cases and frozen
  configurations.
- Verity passes absolute recall/false-positive/stability/error thresholds,
  recall and error are non-inferior to Butler, and safe false positives are
  strictly lower.
- Only after all four conditions pass may Prompt black-box work begin.
