# Round 20 — V1 closure audit (no release claim yet)

## Status

Approved under continuing-execution authorization, but implementation starts
only after Round 19 is committed, pushed and green on CI.

## Goal

Independently verify that the current V1 shipping story is coherent for a
non-engineer across Web, CLI, JSON, HTML, SARIF, project history, score,
confidence, remediation and failure states. Fix closure blockers only; do not
add a new detection layer or call V1 “production complete” without evidence.

## In scope

- end-to-end acceptance cases for Prompt and Skill: safe, findings, Coverage
  failure, score unavailable, score cap, remediation, downloads and history;
- schema-v1/v2 history compatibility and startup/migration failure messages;
- report projection/schema/documentation consistency for Round-19 fields;
- Web-first plain-language review, accessibility and no-JavaScript-injection
  invariants;
- CLI/Web exit/gate parity and explicit semantic-not-configured behavior;
- packaging/install/start-local preflight and public-repo hygiene;
- produce a V1 closure checklist with pass/fail/deferred facts and decide
  honestly whether V1 can be labelled release candidate.

## Out of scope

- external real-model call without supplied credential;
- sealed-test consumption;
- Web Provider/OpenRouter settings;
- new scanner/tool integration or detection breadth promotion;
- automatic file modification;
- V1.5 black-box or V2 sandbox.

## Acceptance

- all closure checklist items are machine or test evidenced;
- no capability wording contradicts runtime or standards breadth;
- score/confidence/remediation parity holds across JSON/HTML/Web/history;
- legacy history fails safe and remains readable;
- pytest, clean verify_repo and GitHub CI pass;
- closure result is one of `release_candidate` or `not_ready` with explicit
  blockers; no ambiguous “mostly done”.
