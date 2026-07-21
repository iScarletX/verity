# Round 18 — Real-model semantic quality evaluation protocol

## Status

Approved by maintainer after Rounds 14–17 foundation completion. Implementation
may proceed. This round measures semantic-review model quality; it does not
productize a Provider or implement a user-facing score.

## User outcome

Answer, with reproducible and coverage-honest evidence: “For the seven closed
semantic Finding Types, how often does a pinned real model confirm unsafe cases,
reject safe counterexamples, and repeat the same decision?”

## In scope

1. Add a strict, versioned real-model evaluation manifest derived only from
   public synthetic Verity cases. Split cases into `calibration`, `selection`
   and sealed `test`; the test split must never be used to edit prompts or pick
   a model.
2. Add an eval-only OpenAI-compatible chat-completions client with trusted
   endpoint/model config, environment-variable credentials, HTTPS/loopback
   policy, redirect refusal, request/response/time/call budgets, strict JSON
   parsing and no tools/streaming/retries.
3. Use two independent model roles (candidate and validator) and the existing
   closed semantic schemas/catalog. Model output cannot author Finding type,
   severity, identity, or Evidence.
4. Run each case at least twice and report, per split/type/language/object:
   TP/FP/TN/FN, precision, recall, safe false-positive rate, decision stability,
   invalid/error rate and call-budget usage. No aggregate safety score.
5. Store only a scrubbed report: case ids, expected/observed decisions, controlled
   reason codes, model/config fingerprints, counts and digests. Do not store raw
   prompts, source snippets, Provider payloads/responses, API keys, chain of
   thought, absolute paths, prices or private account metadata.
6. Provide an explicit CLI research command. Without credentials it must fail
   honestly before network calls. Unit tests use local stubs only; CI remains
   offline and deterministic.
7. Document an evaluation freeze protocol inspired by validation-gated skill
   optimization and verifiable game benchmarks: calibration may tune prompts,
   selection may choose a frozen config, test is final-report only; consuming
   test results invalidates that test version for future tuning.
8. Record Round 19 as the approved next proposal only: explainable safety score,
   review confidence and remediation/re-review loop. Do not implement it here.

## Out of scope

- Web API-key/model selector or default semantic enablement;
- use of real user Prompt/Skill content;
- committing API keys, raw Provider traffic, or mutable real-model outputs as a
  required CI baseline;
- changing deterministic Findings, severity, exit codes, dispositions or history;
- user-facing 0–100 score or automatic file modification;
- V1.5 Prompt black-box execution or V2 Skill sandbox execution;
- direct dependency on SkillOpt or any game benchmark.

## Acceptance

- Strict manifest rejects overlap, duplicate payloads, unsafe paths, unknown
  fields/types/splits and non-synthetic provenance.
- A case exists in exactly one split; all seven Finding Types retain unsafe/safe
  coverage across the protocol, with the sealed test split separately reported.
- Client rejects redirects, non-HTTPS remote endpoints, duplicate/oversized JSON,
  missing credentials, tools/streaming and budget overflow.
- Reports never contain raw case text, source bytes, response text, key values or
  host paths and explicitly state whether model quality was actually measured.
- Repeated stub runs prove metric and stability calculations, including Provider
  errors and invalid schema.
- Existing semantic contract replay remains `modelQualityMeasured=false`.
- `python3 -m pytest`, `python3 tools/verify_repo.py --require-clean` and GitHub CI
  all pass.

## Stop condition

Archive this plan after implementation and stop. Do not start Round 19 until the
maintainer reviews the Round-18 protocol/results and approves the scoring design.
