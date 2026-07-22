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

`evals/corpus/v1/manifest.json` contains 26 independently labelled synthetic
L0 cases across 10 current risk classes. Nine risks retain one positive/safe
pair; Agent Skills specification conformance has four pairs after Round 16.
Every case records provenance, license, object/language, assessed
risks, expected risks/severity, and rationale. Answer keys contain risk ids,
not Rule ids. Exact-byte duplicates of existing developer fixtures are
rejected to reduce test/corpus leakage. Initial labels are explicitly
`provisional_single_review`; they require independent review before supporting
any stronger release claim.

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

Round 18 adds `evals/corpus/v1/semantic_quality.json`: 42 independent synthetic
cases split into 14 calibration, 14 selection and 14 sealed-test cases. Every
split has one unsafe and one safe counterexample for each of the seven closed
semantic Finding Types, and every case must produce a deterministic extractor
seed before it is eligible for model-quality metrics. Labels remain
`provisional_single_review`.

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
positive rate 0.142857, stability 0.857143, zero errors/inconclusives. These
provisional single-review results are not a frozen Selection/Test result and
not a product Provider integration. The sealed test split remains unconsumed.

## Binary V1 closure report

`tools/run_v1_closure.py --check` recomputes
`evals/reports/v1-closure.json` entirely offline. It separates tested
engineering delivery from quality evidence and permits only two decisions:
`release_candidate` or `not_ready`. The current decision is `not_ready` even
though the local engineering checks pass, because labels remain single-review,
no trusted real-model quality report exists, the sealed split is unconsumed,
and no unified risk has `substantial`/`evaluated` evidence. This is not an
aggregate accuracy score and does not call or configure a Provider.

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
