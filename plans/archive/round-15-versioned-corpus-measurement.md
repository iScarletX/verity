# Round 15 — Versioned Golden Corpus and measurement foundation

## Status

- Product scope approved: **yes**
- Implementation: not started; begins only after Round-14 CI is green
- Owner: main maintainer agent

## Objective

Turn the Round-14 risk taxonomy into a reproducible evaluation baseline before
adding more detectors. Build a public-safe versioned corpus and offline
measurement harness that can answer, per risk and detection layer:

- how many labelled positive cases are detected (recall);
- how many emitted detections are correct (precision);
- how often safe counterexamples are incorrectly reported;
- whether repeated runs are stable;
- which languages and artifact forms are represented;
- whether a result is unsupported because a detector/layer is absent.

## Source and reuse policy

- Prefer official standards examples and mature-project test concepts, but
  copy corpus content only when license and attribution permit it.
- Otherwise write minimal synthetic fixtures that reproduce the risk without
  carrying third-party source text.
- Every corpus case records provenance, license, expected risks, safe/unsafe
  label, language, object type and rationale.
- No production secret, personal data, private URL, malware payload, executable
  exploit, or reviewed Skill execution.
- Detect and fail on corpus duplicates and likely train/test leakage within
  this repository.

## Deliverables

1. Versioned corpus manifest/schema tied to Round-14 risk ids.
2. Initial balanced representative slice covering existing detectors plus
   high-priority safe counterexamples; unsupported risks remain explicitly
   unsupported rather than receiving fake samples.
3. Offline deterministic runner and report with per-risk confusion matrices.
4. Separate semantic-case contracts and stubbed/replay evaluation support;
   no real Provider calls and no model-quality release claim this round.
5. Metrics: precision, recall, safe false-positive rate, deterministic
   stability, language/object coverage and unsupported-case counts.
6. Machine gates for provenance, license, hygiene, duplicate content,
   deterministic expectations and taxonomy drift.
7. A baseline report committed only if it is deterministic, public-safe and
   reproducible from the repository.

## Acceptance constraints

- Corpus metrics never become a single safety score.
- High-severity risks are reported separately; averaging cannot hide misses.
- A risk cannot become `substantial` or `evaluated` merely because it has one
  fixture or because existing unit tests pass.
- Corpus expected labels are independent of detector output; detectors cannot
  write their own answer key.
- No Rule, analyzer, Provider, OpenRouter integration, V1.5 runner, or V2
  sandbox behavior is added.
- Full pytest, `verify_repo.py --require-clean`, push, and green CI.

## Explicitly deferred

- Round 16 static detector/tool additions and specification corrections.
- Round 17 semantic catalog expansion and real model measurement.
- Provider/OpenRouter production work (no Round 18 in the approved sequence).
- V1.5 Prompt black-box and V2 Skill sandbox.
