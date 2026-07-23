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

`evals/corpus/v1/manifest.json` contains 66 independently labelled synthetic
L0 cases across 21 current risk classes. Most risks retain one positive/safe
pair; Agent Skills specification conformance has four pairs after Round 16.
Every case records provenance, license, object/language, assessed
risks, expected risks/severity, and rationale. Answer keys contain risk ids,
not Rule ids. Exact-byte duplicates of existing developer fixtures are
rejected to reduce test/corpus leakage. 26 L0 labels carry `independent_ai_review`, bound to current payload
digests by `evals/reviews/corpus-v1-independent-ai-review.json`. This is a
cross-model blind AI second opinion, not human expert review. Rounds 31–37
added eleven new pairs (VR-PROMPT-008 untrusted-input trust-boundary,
VR-SKILL-014 sensitive host-path access, VR-PROMPT-010 dangling section
reference, VR-SKILL-008 TLS certificate verification, VR-PROMPT-003
hardcoded prompt secret, VR-SKILL-011 hardcoded skill credential,
VR-SKILL-005 external instructions, VR-SKILL-007 unsafe deserialization,
VR-SKILL-009 untrusted network destination, VR-SKILL-010 unsafe output
rendering, VR-SKILL-015 SQL injection via string-built queries) as
`provisional_single_review`; they are intentionally excluded from the frozen
attestation until a future review round covers them.

`evals/corpus/v1/semantic_replay.json` contains 14 fixed Provider replay cases
(confirmed/rejected pairs for all seven semantic Finding Types). These measure
only Candidate → Validation → Assessment contract behavior. They explicitly
set `modelQualityMeasured: false` and do not call any external model.

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

## Synthetic real-model semantic quality protocol

`evals/corpus/v1/semantic_quality.json` v2 contains 42 independent synthetic
cases split into 14 calibration, 14 selection and 14 sealed-test cases. Every
split has one unsafe and one safe counterexample for each of the seven closed
semantic Finding Types, and every case must produce a deterministic extractor
seed before it is eligible for model-quality metrics. The 28 Calibration/
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
