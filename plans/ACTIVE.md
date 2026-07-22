# No active implementation round — deterministic V1 is a release candidate

## Status

Stopped after Round 25 (closure policy v2.0.0). The **deterministic static
auditor** is now `release_candidate` (engineering preview, no evaluated-accuracy
claim). The **controlled semantic / evaluated-accuracy track** is separate,
experimental, default-off, `experimental_not_ready`, and NOT in the release
gate. Independent dual-AI review covers every non-sealed label but is not human
expert review. Protocol-v1 Selection is invalidated; the first frozen
protocol-v2 Selection returned `not_eligible`; sealed Test is unconsumed.

## Round 25 (done) — scope the release decision

Rewrote `verity/closure.py` to policy v2.0.0 so the release decision covers only
the deterministic static auditor and turns `release_candidate` on green
engineering acceptance. Semantic/accuracy blockers moved to a separate
`semanticQualityTrack` (`inReleaseGate=false`) and are still fully disclosed.
Regenerated the closure report, updated tests/README/PROGRESS. No product code
path, rule, corpus or security boundary changed.

## Round 24 (done) — protocol-v2 first frozen Selection

Ran real protocol-v2 evaluation with `openai/gpt-4o-2024-11-20` (both roles,
temp 0, role Prompt v2.0.0, redacted_evidence, 2 reps). Calibration passed
strongly (recall 0.929, safe FP 0.0) but the frozen Selection returned
`not_eligible` against predeclared gate v1.0.0: recall 0.857 (<0.90) and safe
false-positive rate 0.429 (>0.20). tp=12/fn=2/tn=8/fp=6. Sealed Test untouched.
The consumed Selection result must not be used to tune protocol v2; improving
quality requires a NEW protocol version with fresh unseen splits.

## Round 23 (done) — gate determinism fix

Fixed a non-deterministic pytest failure that made `verify_repo.py` flake:
`bandit_runner.py` now removes its staging tmpdir with a retrying helper instead
of a single `shutil.rmtree(..., ignore_errors=True)`, and
`test_bandit_tmpdir_is_removed_after_run` scopes its leak check to dirs created
by the current run. Suite 451 → 453 passed. This did not touch the evidence
blockers below, which still require a human decision.

## Evidence now available

- 26/26 L0 cases: digest-bound `independent_ai_review`;
- 28/28 semantic Calibration/Selection cases: digest-bound
  `independent_ai_review`;
- initial valid reviewer agreement 46/54; eight exceptions independently
  adjudicated; two mislabeled artifacts corrected and independently re-reviewed;
- one initial reviewer invalidated after decision counts changed during JSON
  repair and excluded from all comparison;
- 14/14 fixed semantic contract replays remain reproducible and provisional;
- 14 sealed-Test cases remain provisional, unexposed and unconsumed;
- semantic-quality protocol v2 fingerprints the selected Corpus payloads;
- protocol-v1 Selection is `invalidated_by_label_adjudication` and may not be
  re-scored or used as release evidence.

## Remaining release blockers

- the controlled semantic path FAILED its frozen protocol-v2 Selection gate as
  configured (recall 0.857, safe FP 0.429); it is not release-quality now;
- AI cross-model review is not a substitute for human/domain-expert review if a
  public production-quality claim requires one;
- protocol v2 Selection is now consumed and cannot be re-scored or tuned;
- sealed Test remains provisional/unconsumed;
- no unified risk layer has approved substantial/evaluated breadth.

## Next authorized decision sequence

1. decide whether to invest in semantic-path quality at all; if yes, design a
   NEW protocol version (v3) with fresh, unseen Calibration/Selection splits —
   do NOT reuse or tune from the consumed v2 Selection cases;
2. decide whether public release requires human/domain-expert review of the 54
   non-sealed labels and arrange it independently if required;
3. for any new protocol version, run Calibration, freeze role Prompt/model/
   budget against a dated immutable revision, then run one Selection against
   predeclared gates;
4. only after an accepted frozen Selection, separately approve sealed Test
   consumption for final reporting;
5. promote risk breadth only under approved per-risk thresholds, then recompute
   V1 closure.

## Not authorized

- reviving or reinterpreting protocol-v1 Selection metrics;
- tuning protocol v1/v2 from the invalidated or consumed Selection cases;
- re-running protocol-v2 Selection to "retry" for a better score;
- exposing or consuming sealed Test before a new approval;
- calling AI blind review “human expert review”;
- Provider Web productization, V1.5, V2 sandbox or automatic remediation.
