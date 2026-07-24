# evals — Verity's tests read as an AI evaluation suite

This directory now holds the first versioned offline detection corpus in
addition to describing how Verity's test suite acts as a pipeline gate.

## What is already an eval, and where

| Aspect                                     | Where it lives                                    |
|--------------------------------------------|---------------------------------------------------|
| Deterministic-rule correctness             | `tests/test_prompt_rules.py`, `tests/test_skill_rules.py` |
| Safe intake / path escape / budgets        | `tests/test_walking_skeleton.py`, `tests/test_web_mvp.py` |
| Bandit integration + stubs                 | `tests/test_round4.py` (`TestBanditStubs`, `TestBanditReal`) |
| gitleaks install + runtime                 | `tests/test_gitleaks_install.py`, `tests/test_round5.py` |
| CLI exit-code / gate marker                | `tests/test_round5_hotfix.py`                     |
| Web MVP + safety headers + view model      | `tests/test_web_mvp.py`, `tests/test_round7_guidance.py` |
| Guidance catalog + next-step summary       | `tests/test_round7_guidance.py`                   |
| Semantic scaffold (schemas, containment,   | `tests/test_semantic.py`                          |
|   provider mocks, deterministic invariant) |                                                   |
| 19-item Phase-0 acceptance matrix          | `tests/test_acceptance_19.py`                     |
| Standards/taxonomy provenance + detector mapping | `tests/test_round14_standards.py`, `standards/` |
| V1 cross-format closure + offline package install | `tests/test_round20_closure.py`                |

## What these tests are NOT

- They do **not** measure the quality of any specific model. There
  is no scored LLM benchmark, no head-to-head comparison, no
  win/loss table.
- They **do** measure that Verity's pipeline behaves correctly under
  every input the semantic path can receive from a Provider,
  including adversarial ones (invalid schema, forged identity,
  smuggled findings, extra fields, prompt injection in the reviewed
  content).

## Versioned corpus baseline

`evals/corpus/v1/manifest.json` contains 80 labelled synthetic
L0 cases across 24 current risk classes. Most risks retain one positive/safe
pair; Agent Skills specification conformance has four pairs after Round 16.
Every case records provenance, license, object/language, assessed
risks, expected risks/severity, and rationale. Answer keys contain risk ids,
not Rule ids. Exact-byte duplicates of existing developer fixtures are
rejected to reduce test/corpus leakage. 26 L0 labels carry `independent_ai_review`, bound to current payload
digests by `evals/reviews/corpus-v1-independent-ai-review.json`. This is a
cross-model blind AI second opinion, not human expert review. The other 54 L0
cases, including Round 54's output-format conflict, explicit output-budget
conflict, autonomy/approval boundary and failure-strategy pairs, remain
`provisional_single_review`; they are intentionally excluded from the frozen
attestation until a future review round covers them.

`evals/corpus/v1/semantic_replay.json` contains 56 fixed Provider replay cases
(confirmed/rejected pairs for all twenty-eight semantic Finding Types). These
measure only Candidate → Validation → Assessment contract behavior. They
explicitly set `modelQualityMeasured: false` and do not call any external
model.

`tools/run_corpus.py --check` reruns each case twice and reproduces separate
committed reports:

- `evals/reports/corpus-v1-l0.json`
- `evals/reports/corpus-v1-semantic-contract.json`

The L0 report provides per-risk TP/FP/TN/FN, precision, recall, safe false-
positive rate, deterministic stability, language/object coverage, and explicit
`unsupported`/`unmeasured` statuses. High/Critical positives are reported
separately. There is deliberately no aggregate safety score.

The initial paired cases currently pass, but **one positive plus one safe
counterexample is not broad accuracy evidence**. This is a measurement
foundation, not a 100% accuracy claim. No risk becomes `substantial` or
`evaluated` until a later, larger, versioned and leakage-controlled corpus
meets an approved threshold.

When real-Provider integration or V1.5 Prompt black-box arrives, its
data assets live under `evals/`:

- `evals/prompts/`      — corpus of prompts used by black-box scoring
- `evals/adversarial/`  — inputs intended to defeat the semantic
                          Provider containment; each must have a
                          matching test in `tests/test_semantic.py`
- `evals/reports/`      — recorded outputs (with PII / secrets
                          scrubbed) for regression tracking

## Frozen protocol-v2 semantic quality history

`evals/corpus/v1/semantic_quality.json` v2 contains 42 independent synthetic
cases split into 14 calibration, 14 selection and 14 sealed-test cases. Every
split has one unsafe and one safe counterexample for each of the seven closed
semantic Finding Types that existed when v2 was frozen, and every case must
produce a deterministic extractor seed before it is eligible for model-quality
metrics. The 28 Calibration/
Selection labels have digest-bound `independent_ai_review`; 14 sealed-Test
labels remain `provisional_single_review` and were not exposed to review Agents.

The protocol follows a strict freeze rule:

1. `calibration` may be used to develop the role prompts;
2. `selection` may choose one frozen generator/validator model configuration;
3. `test` is final-report only. Running it requires
   `--acknowledge-sealed-test`; using its results to tune anything consumes and
   invalidates protocol v1 as sealed evidence.

The explicit research command is:

```bash
export VERITY_EVAL_API_KEY='<set locally; never commit>'
python3 tools/run_semantic_model_eval.py \
  --split calibration \
  --base-url https://trusted-provider.example/v1 \
  --generator-model '<pinned-model-id>' \
  --validator-model '<pinned-model-id>' \
  --api-key-env VERITY_EVAL_API_KEY
```

It accepts only the versioned synthetic corpus, uses the existing closed
SemanticOrchestrator, defaults to two repetitions and a 60-call hard preflight
cap, and writes a scrubbed report to gitignored
`.verity-data/model-evals/`. The report has per-type/language/object confusion
matrices, inconclusive/error rates and decision stability. It has no aggregate
safety score and stores no case text, source snippets, claims, subjects, raw
Provider traffic, endpoint, credential name/value, account metadata or host
path. Real model outputs are mutable research records and are not required CI
baselines. The report includes an eval role-Prompt version in its frozen
configuration fingerprint. Selection gate v1.0.0 is declared before Selection:
recall >=0.90, safe false-positive rate <=0.20, stability >=0.80, error rate
<=0.05 and inconclusive rate <=0.10.

Round 21 produced local OpenRouter Calibration research records. The best
Calibration configuration was GPT-4.1-mini for both roles, temperature 0, two
repetitions and role Prompt v2.0: recall 1.0, precision 0.875, safe false-
positive rate 0.142857, stability 0.857143, zero errors/inconclusives. After
commit `b52eb8d` passed CI, one frozen v1 Selection run returned `eligible` with
recall 1.0, precision 0.875, safe false-positive rate 0.153846, stability
0.928571, error rate 0.035714 and zero inconclusives. No tuning followed.

Round-22 dual-AI blind review then found that two supposed safe external-trust
artifacts used `fetch_and_follow`, contradicting their data-only policy. Both
were corrected to `fetch_as_data` and independently re-reviewed. Therefore the
historical v1 Selection is explicitly
`invalidated_by_label_adjudication`; it must not be re-scored after the fact.
Protocol v2 includes the selected Corpus payload digest in the configuration
fingerprint.

Round 24 ran the first real protocol-v2 evaluation against a dated immutable
revision, `openai/gpt-4o-2024-11-20` (both roles, temperature 0, role Prompt
v2.0.0, `redacted_evidence`, 2 repetitions). Calibration passed strongly
(recall 0.929, precision 1.0, safe false-positive rate 0.0, stability 0.929),
but the frozen Selection returned `not_eligible` under predeclared gate v1.0.0:
recall 0.857 (<0.90) and safe false-positive rate 0.429 (>0.20), with
tp=12/fn=2/tn=8/fp=6. The consumed protocol-v2 Selection must not be re-scored
or used to tune this protocol version; a quality improvement requires a new
protocol version with fresh, unseen splits. Sealed Test was not exposed or
consumed. Scrubbed reports live only in gitignored `.verity-data/model-evals/`.

## Fresh protocol-v3 Verity/Butler comparison

`evals/corpus/v1/semantic_comparison_v3.json` contains 112 fresh cases: two
unsafe and two safe counterexamples for each of the twenty-eight current semantic
Finding Types. Every case has a deterministic extractor seed, but all labels
remain `provisional_single_review`. This committed development corpus therefore
meets the minimum size for the comparison plumbing while remaining ineligible
for a superiority claim until its payload-digest-bound labels are independently
reviewed.

`evals/reference/butler_crosswalk.json` freezes the complete 45-check Butler
built-in inventory at the reference commit and classifies every item as
covered, open, or deliberately not adopted. The current inventory has all 45
checks covered, with zero open or deliberately omitted. Independent labels and
paired real observations still block a superiority claim.
`covered` means at least one mapped detector materially addresses the check;
it is not a complete-recall or evaluated-accuracy assertion.

`tools/semantic_head_to_head.py` has six deliberately separate operations:

```bash
export VERITY_COMPARISON_SEED='<private random seed>'
python3 tools/semantic_head_to_head.py packet \
  --system-id verity \
  --seed-env VERITY_COMPARISON_SEED

# Build separately shuffled, answer-hidden packets for the two label reviewers.
export VERITY_LABEL_REVIEWER_A_SEED='<private random seed>'
export VERITY_LABEL_REVIEWER_B_SEED='<different private random seed>'
python3 tools/semantic_head_to_head.py packet \
  --system-id label-reviewer-a \
  --seed-env VERITY_LABEL_REVIEWER_A_SEED
python3 tools/semantic_head_to_head.py packet \
  --system-id label-reviewer-b \
  --seed-env VERITY_LABEL_REVIEWER_B_SEED

python3 tools/semantic_head_to_head.py run-label-reviewer \
  --packet .verity-data/semantic-comparison/label-reviewer-a/packet.json \
  --alias-map .verity-data/semantic-comparison/label-reviewer-a/alias-map.json \
  --base-url https://trusted-provider.example/v1 \
  --model '<pinned-independent-reviewer-a>' \
  --api-key-env VERITY_EVAL_API_KEY \
  --repetitions 2 \
  --max-output-tokens 256 \
  --max-total-calls 300 \
  --max-total-tokens '<approved-total-token-cap>' \
  --max-spend-usd '<approved-USD-cap>' \
  --input-price-per-million '<provider-price>' \
  --output-price-per-million '<provider-price>' \
  --output .verity-data/semantic-comparison/label-reviewer-a/observations.json

# Repeat with label-reviewer-b's packet, a distinct frozen configuration,
# and a second approved run budget before deriving the attestation.

python3 tools/semantic_head_to_head.py run-verity \
  --packet .verity-data/semantic-comparison/verity/packet.json \
  --alias-map .verity-data/semantic-comparison/verity/alias-map.json \
  --base-url https://trusted-provider.example/v1 \
  --generator-model '<pinned-generator-model>' \
  --validator-model '<pinned-validator-model>' \
  --api-key-env VERITY_EVAL_API_KEY \
  --repetitions 2 \
  --max-output-tokens 800 \
  --max-total-calls 500 \
  --max-total-tokens '<approved-total-token-cap>' \
  --max-spend-usd '<approved-USD-cap>' \
  --generator-input-price-per-million '<provider-price>' \
  --generator-output-price-per-million '<provider-price>' \
  --validator-input-price-per-million '<provider-price>' \
  --validator-output-price-per-million '<provider-price>' \
  --output .verity-data/semantic-comparison/verity/observations.json

python3 tools/semantic_head_to_head.py run-butler \
  --packet .verity-data/semantic-comparison/butler/packet.json \
  --alias-map .verity-data/semantic-comparison/butler/alias-map.json \
  --butler-root '<read-only-Butler-root>' \
  --base-url https://trusted-provider.example/v1 \
  --model '<pinned-model-a>' \
  --model '<pinned-model-b>' \
  --input-price-per-million '<model-a-price>' \
  --input-price-per-million '<model-b-price>' \
  --output-price-per-million '<model-a-price>' \
  --output-price-per-million '<model-b-price>' \
  --api-key-env VERITY_EVAL_API_KEY \
  --repetitions 2 \
  --max-output-tokens 800 \
  --max-total-calls '<approved-call-cap>' \
  --max-total-tokens '<approved-total-token-cap>' \
  --max-spend-usd '<approved-USD-cap>' \
  --timeout 30 \
  --wall-timeout-seconds '<approved-wall-clock-cap>' \
  --output .verity-data/semantic-comparison/butler/observations.json

python3 tools/semantic_head_to_head.py attest-labels \
  --reviewer-a-packet '<reviewer A answer-hidden packet>' \
  --reviewer-a-map '<reviewer A local alias map>' \
  --reviewer-a-observations '<reviewer A observations>' \
  --reviewer-b-packet '<reviewer B answer-hidden packet>' \
  --reviewer-b-map '<reviewer B local alias map>' \
  --reviewer-b-observations '<reviewer B observations>'

python3 tools/semantic_head_to_head.py compare \
  --verity-packet '<verity packet>' \
  --verity-map '<verity local alias map>' \
  --verity-observations '<scrubbed Verity observations>' \
  --butler-packet '<Butler packet>' \
  --butler-map '<Butler local alias map>' \
  --butler-observations '<scrubbed Butler observations>' \
  --labels '<independent label attestation>'
```

Packet output contains artifacts and one target-risk definition, but no case
id, Finding Type, risk id, author answer, label status, source path, or payload
digest. Verity, Butler and the two label reviewers receive separately shuffled
aliases; each local map row is bound to its packet-item digest.
`run-label-reviewer` accepts only a non-`verity`/non-`butler` packet, sends its
packet rather than its local map, and writes a scrubbed observation plus a
budget audit. `attest-labels` requires exactly two reviewer systems and
configuration fingerprints distinct from each other. The comparator also
refuses an otherwise valid attestation when either reviewer configuration
matches Verity or Butler. The built-in `run-verity` command accepts only a
`system-id=verity` packet and therefore cannot be used to manufacture either
independent review. Every review must be stable, decisive and unanimous across
reviewers; names alone cannot establish independence. Every packet is
rechecked for answer metadata when its alias map or observations cross a
comparison boundary. The comparator requires at least
112 cases, all twenty-eight Finding Types, at least twenty-seven mapped risk ids, two
repetitions, recall >=0.90, safe false-positive rate <=0.20, stability >=0.80,
error rate <=0.05 and inconclusive rate <=0.10. It then additionally
requires Verity recall to be non-inferior to Butler, Verity safe false
positives to be strictly lower, and Verity errors to be no worse. Only that
scoped, independently labelled benchmark may emit
`verity_exceeds_butler_on_this_independently_labelled_benchmark`.

No real v3 Provider run has been made in the repository. It still requires the
operator to name the trusted provider endpoint, exact model ids, credential
environment variable, repetitions, call/token/spend limits and local report
path. Butler observations must come from Butler itself under a frozen
configuration; Butler output is never used as the answer key. The read-only
adapter builds a temporary Node bundle from the supplied Butler source and its
already-installed dependencies, records a source fingerprint, and never writes
to or installs into Butler. It reuses Butler's document profiler, static
checker, selected LLM checks and vote aggregator. It deliberately omits
Butler's final consolidation/deduplication presentation stages because those
can contact a separate embeddings endpoint and can introduce findings outside
the one target risk. This limitation remains explicit in every comparison
report.

Both system runners reserve a conservative upper bound before every HTTP
attempt: UTF-8 request bytes plus fixed message overhead plus the configured
maximum output. Retries consume fresh call/token/spend reservation. They write
a scrubbed sidecar budget audit beside the observation file and stop before a
request that would exceed any approved cap. The Butler adapter also reads
Provider responses incrementally and aborts as soon as the response-byte cap
would be exceeded.

## Binary V1 closure report

`tools/run_v1_closure.py --check` recomputes
`evals/reports/v1-closure.json` entirely offline under closure policy v2.0.0.
The `decision` (`release_candidate` or `not_ready`) is scoped to the
**deterministic static auditor** and turns `release_candidate` on green
engineering acceptance; it makes no evaluated-accuracy claim and keeps breadth
limits in `disclosedLimitations`. The current decision is `release_candidate`.
The controlled semantic / evaluated-accuracy work is a separate
`semanticQualityTrack` with `inReleaseGate=false`, currently
`experimental_not_ready`: labels remain single-review, the first frozen
protocol-v2 Selection returned `not_eligible`, the sealed split is unconsumed,
no unified risk has `substantial`/`evaluated` evidence, and human/domain-expert
review has not been obtained. This is not an aggregate accuracy score and does
not call or configure a Provider.

Method references such as SkillOpt, GameWorld, VideoGameQA-Bench, DSGBench,
TALES, TextWorld, VideoGameBench, Orak, BALROG, Jericho and ViStoryBench informed
split isolation, bounded updates, state-verifiable outcomes, trajectory replay
and multi-dimensional reporting. None is a Verity dependency or detection
standard, and their task/game scores are not security evidence.

## How to run everything today

```bash
python3 -m pytest                       # authoritative test runner
python3 tools/run_corpus.py --check      # reproduce both corpus reports
python3 tools/verify_repo.py             # includes corpus reproduction gate
```
