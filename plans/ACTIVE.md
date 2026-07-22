# Round 21 — Calibrate semantic decision contract before Selection

## Status

Approved under maintainer continuing-execution authorization. Calibration-only
model observations may inform this round; Selection contents and sealed Test
remain unread and unconsumed.

## Evidence prompting the round

OpenRouter Calibration on protocol v1 produced:

- Claude Sonnet 4.5: 28/28 generator runs failed strict JSON parsing;
- GPT-4.1-mini: recall 1.0, safe false-positive rate 0.285714, stability
  0.785714;
- GPT-4.1: recall 1.0, safe false-positive rate 0.5, stability 0.642857;
- one GPT-4.1-mini validator response combined `confirmed` with
  `evidence_contradicts_claim`, exposing a missing decision/reason consistency
  constraint.

Reports are local and gitignored. No Selection or Test result was inspected.

## Goal

Make validator decisions internally coherent and sharpen the already-defined
materiality/falsification boundary using only Calibration observations. Then
rerun Calibration and freeze one eligible configuration for Selection.

## In scope

- enforce decision/reason-code consistency in the strict validator schema;
- require at least one controlled reason code;
- clarify in the eval-only validator role prompt that `confirmed` requires
  material support after applying the falsification question, while equivalent
  narrow wording/capabilities and explicit least-privilege boundaries require
  rejection;
- regression tests for contradictory decision/reason combinations;
- rerun Calibration and compare error, precision, recall, safe false-positive
  rate and stability;
- if quality is acceptable, freeze one configuration and run Selection without
  modifying the protocol afterward.

## Out of scope

- accepting Markdown-fenced or otherwise non-strict JSON without captured
  evidence of that exact transport failure;
- reading or changing Selection cases before the frozen Selection run;
- consuming sealed Test;
- changing labels, Finding severities, taxonomy, score policy, product Web/CLI
  Provider behavior, or user artifacts;
- committing Provider traffic, credentials, raw case text or local reports.

## Acceptance

- contradictory decision/reason payloads fail schema validation;
- existing fixed replay remains reproducible;
- Calibration has zero protocol errors for the selected configuration;
- selected configuration improves or clearly dominates the current
  GPT-4.1-mini Calibration baseline without sacrificing unsafe-case recall;
- pytest, clean verify_repo and GitHub CI pass before any Selection claim;
- Selection is run only once for the frozen Round-21 configuration; Test remains
  unconsumed.
