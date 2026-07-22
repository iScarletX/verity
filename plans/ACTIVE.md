# No active implementation round — protocol-v2 evidence decision required

## Status

Stopped after Round 22. Independent dual-AI review now covers every non-sealed
L0 and semantic-quality label, but it is explicitly not human expert review.
Historical protocol-v1 Selection is invalidated; protocol v2 has not called a
model. V1 remains `not_ready`.

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

- AI cross-model review is not a substitute for human/domain-expert review if a
  public production-quality claim requires one;
- protocol v2 has no Calibration/Selection result;
- the prior OpenRouter model id was a mutable alias rather than an immutable
  revision;
- sealed Test remains provisional/unconsumed;
- no unified risk layer has approved substantial/evaluated breadth.

## Next authorized decision sequence

1. decide whether public release requires human/domain-expert review of the 54
   non-sealed labels and arrange it independently if required;
2. choose an immutable Provider model revision and a fresh bounded research Key;
3. run protocol-v2 Calibration, freeze role Prompt/model/budget, then run one
   Selection against predeclared gates;
4. only after accepted labels and frozen v2 Selection, separately approve sealed
   Test consumption for final reporting;
5. promote risk breadth only under approved per-risk thresholds, then recompute
   V1 closure.

## Not authorized

- reviving or reinterpreting protocol-v1 Selection metrics;
- tuning protocol v1/v2 from the invalidated Selection cases;
- exposing or consuming sealed Test before a new approval;
- calling AI blind review “human expert review”;
- Provider Web productization, V1.5, V2 sandbox or automatic remediation.
