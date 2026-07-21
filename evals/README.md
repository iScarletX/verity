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

Real Provider and V1.5 assets remain absent. `docs/PROGRESS.md` reflects any
future change.

## How to run everything today

```bash
python3 -m pytest                       # authoritative test runner
python3 tools/run_corpus.py --check      # reproduce both corpus reports
python3 tools/verify_repo.py             # includes corpus reproduction gate
```
