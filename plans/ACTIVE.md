# Active implementation round: Round 55 V1.5 Prompt black-box entry

Status: **authorized, implementation not started**
Owner authorization: 2026-07-23

## Goal

Build the first default-off, isolated V1.5 runner that executes a reviewed
Prompt against an explicitly selected model and scores observable output
behavior. This is a new dynamic capability, separate from both deterministic
Findings and the experimental semantic-review judge.

## Required scope

1. Define versioned `BlackboxRunConfig`, test-case, observation and result
   schemas. Configuration must include a trusted Provider/model identity,
   test-set digest, call/token/cost budget and recording location.
2. Add an isolated Provider adapter with HTTPS-or-loopback transport,
   redirect refusal, environment-variable credential resolution, strict
   timeout/response-size/call budgets and payload audit. Reviewed content
   must never choose the endpoint, model, key name or budget.
3. Start with deterministic scorers whose truth can be inspected: JSON/schema
   validity, required/forbidden output markers, length/finish reason and
   repeated-run stability. Behavioral or model-judge scorers require their
   own explicit evidence contract and may not silently become truth.
4. Keep black-box observations and scores in a separate report section.
   They must not mutate, suppress or manufacture deterministic Findings.
   Reports must distinguish `not_enabled`, `provider_not_configured`,
   `completed`, `failed` and `budget_exhausted`.
5. Expose an explicit CLI entry point and a fake-Provider path for tests.
   Ordinary `review` remains offline and must never trigger a black-box call.
6. Add adversarial tests for configuration injection, credential leakage,
   redirects, malformed responses, timeouts, budget exhaustion and partial
   results. CI uses fake providers only.
7. Update standards, architecture, README, progress, eval documentation and
   machine gates only after the runtime contract is implemented and tested.

## First real-run gate

Do not make a real model call until the operator has supplied and approved:

- the exact test set or test-set file;
- a dated model/provider configuration;
- maximum calls, output tokens, wall time and spend;
- the local recording directory and retention choice.

These values are trusted operator inputs and are never read from the reviewed
Prompt. A dry run may validate the configuration without network access.

## Explicitly out of scope

- Skill execution or V2 sandbox work.
- Installing `torch`/`transformers` or downloading local model weights.
- Reusing the semantic Candidate Generator/Validator result as a black-box
  score.
- Retrying the consumed protocol-v2 Selection or exposing sealed Test labels.
- Automatic prompt rewriting or applying patches.

## Exit criteria

- Full pytest and `python3 tools/verify_repo.py` pass.
- Default review paths prove zero black-box network calls.
- Fake-Provider end-to-end tests produce a reproducible, schema-valid report.
- One operator-approved real smoke run completes within its declared budget,
  or the round remains honestly blocked at the first-real-run gate.
- The capability matrix changes from `not_implemented` only when the shipped
  runtime actually supports the stated status.
