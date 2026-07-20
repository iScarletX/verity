# evals — Verity's tests read as an AI evaluation suite

This directory does not currently hold any executable content. It
exists to describe how Verity's own test suite functions as a
model-agnostic evaluation gate for the pipeline.

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

## What these tests are NOT

- They do **not** measure the quality of any specific model. There
  is no scored LLM benchmark, no head-to-head comparison, no
  win/loss table.
- They **do** measure that Verity's pipeline behaves correctly under
  every input the semantic path can receive from a Provider,
  including adversarial ones (invalid schema, forged identity,
  smuggled findings, extra fields, prompt injection in the reviewed
  content).

## What goes here later

When real-Provider integration or V1.5 Prompt black-box arrives, its
data assets live under `evals/`:

- `evals/prompts/`      — corpus of prompts used by black-box scoring
- `evals/adversarial/`  — inputs intended to defeat the semantic
                          Provider containment; each must have a
                          matching test in `tests/test_semantic.py`
- `evals/reports/`      — recorded outputs (with PII / secrets
                          scrubbed) for regression tracking

None of the above is present yet. `docs/CURRENT_STATE.md` will
reflect any change.

## How to run everything today

```bash
python3 -m pytest                 # authoritative test runner
python3 tools/verify_repo.py      # authoritative repo gate
```
