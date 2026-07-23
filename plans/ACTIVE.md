# Active implementation round: Round 55 semantic capability and Butler floor

Status: **authorized, implementation in progress**
Owner authorization: 2026-07-23

## Goal

Make controlled semantic review the current product-development priority.
Verity must cover the meaningful semantic problem classes represented by the
read-only Butler reference, fix the known protocol-v2 model failure modes, and
ship a neutral same-case comparison gate. Prompt black-box and Skill sandbox
work begin only after the semantic gate is honestly ready.

Butler output is a hypothesis and comparison baseline, never a label source.
"Exceeds Butler" means Verity meets absolute quality thresholds and performs
better on the same independently labelled cases; architecture size, Finding
count, or a self-authored fixture score does not establish that claim.

## Required scope

1. Preserve the seven existing controlled Finding Types and add semantic
   coverage for output-budget pressure, autonomous authority boundaries,
   failure/edge handling, ambiguous operational criteria, grounding
   requirements, sensitive-reasoning exposure, and verification-step gaps.
   Broad absence claims must have an applicability test and safe
   counterexamples.
2. Replace the generic one-question judge input with catalog-controlled,
   per-type applicability, confirmation, rejection, and insufficiency policy.
   Reviewed content cannot alter this policy.
3. Add bounded structured Evidence facts for output stages/contracts and
   Skill declaration/capability equivalence. The Provider must receive the
   exact cited source span plus only allowlisted metadata; absolute paths,
   credentials, arbitrary metadata, and severity remain forbidden.
4. Repair the four observed protocol-v2 failures without retrying its consumed
   Selection: separate opening-summary from later-detail requirements, treat a
   bare JSON/YAML container as no field contract, recognize declared network
   behavior matching observed network access, and recognize a narrow
   `Bash(command:*)` declaration matching that fixed command.
5. Ensure the default semantic budget can attempt every applicable controlled
   type that produces evidence, while retaining explicit hard call,
   validation, response-size, timeout, and egress limits.
6. Keep protocol v2 immutable and reproducible. Create a fresh protocol-v3
   development/label-review path; never reuse consumed Selection as a new
   Selection and never expose sealed Test labels.
7. Add a scrubbed head-to-head comparator that accepts answer-free system
   observations and independently reviewed labels. Verity passes the Butler
   floor only when it meets absolute recall/false-positive/stability/error
   thresholds and is non-inferior on recall with lower false positives on the
   same cases.

## Real-run and claim gate

No real Provider/model run occurs until trusted operator configuration names
the provider, exact generator and validator models, credential environment
variable, split, repetitions, call/token/spend budget, and local report path.
No local model dependency or weight is installed in this round without the
separate founder approval required by `AGENTS.md`.

Round 55 may ship engineering capability and a ready comparison protocol
without claiming semantic superiority. "Verity exceeds Butler" remains
blocked until fresh labels and both systems' same-case observations pass the
comparison gate.

## Explicitly out of scope until this gate passes

- Prompt black-box execution.
- Skill execution or V2 sandbox work.
- Automatic prompt/Skill rewriting.
- Treating Butler findings, model consensus, or Verity output as ground truth.
- Retuning or rerunning protocol-v2 Selection.
- Inspecting or consuming protocol-v2 sealed Test.

## Exit criteria

- Every semantic Finding Type has positive and safe extractor/contract cases.
- The fresh comparison corpus has at least 56 answer-hidden cases with two
  positive and two safe artifacts for every controlled Finding Type.
- Known v2 failure shapes have request-boundary regression tests.
- Protocol v2 still loads and reproduces unchanged.
- Protocol v3 refuses superiority/Selection claims while labels or paired
  observations are provisional or missing.
- Independent v3 labels are derived from two distinct stable answer-hidden
  reviews; reviewer-name assertions alone are rejected.
- Full pytest and `python3 tools/verify_repo.py --require-clean` pass.
- Changes are committed, pushed, and GitHub CI is green.
