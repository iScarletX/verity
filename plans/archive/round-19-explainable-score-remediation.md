# Round 19 — Explainable score, remediation, and re-review loop

## Status

Approved under the maintainer's continuing-execution authorization. Implement
directly and independently; no concurrent writer may modify this repository.

## User outcome

A non-engineer can answer four separate questions without reading code:

1. How serious are the findings in checks that actually completed?
2. How trustworthy/complete was this review?
3. What should be changed first, and how will the change be verified?
4. Compared with the previous compatible version, what was resolved, added,
   left unchanged, or made unknowable by missing coverage?

## In scope

1. Add a deterministic, versioned score policy owned by Verity. Models and
   reviewed artifacts cannot set weights, caps, risk ids, score, confidence,
   remediation priority or comparison eligibility.
2. Produce a 0–100 `safetyScore` only when deterministic Coverage is
   sufficient. Severity caps: any unresolved Critical <=39, High <=59,
   Medium <=79, Low <=99. Duplicate evidence/root causes use bounded
   diminishing deductions; they cannot drive an unbounded penalty.
3. Map every deduction to unified risk id(s), Finding id(s), severity, policy
   weight/cap and arithmetic. Unknown detector mappings fail scoring rather
   than being silently ignored.
4. Keep review confidence separate from safety score. Grade A–D from executed
   scope and declared capability breadth; explicitly list semantic disabled /
   failed, gitleaks/profile gaps, and V1.5/V2 not implemented. Confidence never
   upgrades safety and no `static: completed` wording implies full breadth.
5. Include confirmed semantic Findings only when semantic status is
   `completed`; rejected/inconclusive candidates never deduct. The report
   states which layers contributed. Semantic disabled does not make static
   Coverage fail, but is visible in confidence limitations.
6. Extend controlled guidance into remediation records: priority, exact
   Finding/Evidence references, controlled actions, and deterministic
   `verificationChecks`. No free-form patch generation and no automatic file
   writes in this round.
7. Add score/confidence/remediation to JSON, static HTML and Web view. Preserve
   CSP, escaping, no-innerHTML and public-safe projections.
8. Persist the score-policy version and safe score projection in new Skill
   history records. Older schema-v1 records remain readable and explicitly
   `scoreUnavailable`; never backfill a historical score from a newer formula.
9. Add version score comparison only when artifact identity, profile/scope,
   score-policy version and relevant coverage are compatible. Otherwise return
   `not_comparable` with controlled reason codes. Diff finding states remain
   authoritative for remediation progress.
10. Dispositions remain advisory: they do not change severity or raw safety
    score. Show disposition counts beside remediation progress only.

## Out of scope

- real-model calibration/selection/test run without an intentionally supplied
  trusted credential;
- automatic PatchSet generation/application or editing user files;
- model-authored score, severity, risk mapping or remediation;
- scoring when Coverage is insufficient;
- averaging away Critical/High findings;
- Web Provider/API-key/model configuration;
- V1.5 Prompt black-box or V2 Skill sandbox execution.

## Acceptance

- Policy arithmetic, caps, deduplication and order independence are property /
  adversarial tested.
- Coverage-insufficient reports have `score.status=unavailable` and no numeric
  value; UI says “暂不评分”, never zero or 100.
- Every deduction reconciles to the reported score and to controlled detector
  mappings. No unknown Finding silently disappears.
- Confirmed semantic Findings affect score only on completed semantic runs;
  deterministic-only runs remain clearly labelled.
- Confidence grade and limitations are deterministic, explainable and separate.
- Remediation records cite existing Findings/Evidence and include re-review
  checks; no raw Secret or host path enters them.
- Historical comparisons refuse incompatible profile/policy/coverage and show
  resolved/new/existing/changed/unknown counts for compatible versions.
- Existing exit codes and default disposition semantics do not change.
- Full pytest, `verify_repo.py --require-clean`, and GitHub CI pass.

## Stop condition

Archive this round and stop before Provider productization or V1.5. If no real
model credential exists, preserve that fact; do not fabricate Round-18 results.
