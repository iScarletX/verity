# Round 54 archive: independent review capability baseline

Status: **done**
Date: 2026-07-23
Parent commit measured: `0ad9c2c`

## Goal

Make Verity surface useful Prompt problems quickly without inheriting
Butler's tangled framework or treating Butler output as ground truth, then
leave the repository ready to enter V1.5 black-box work.

## Delivered

- Four high-confidence deterministic Prompt findings:
  `prompt.output_format_conflict`, `prompt.output_budget_conflict`,
  `prompt.autonomy_without_approval`, and
  `prompt.failure_strategy_missing`.
- Four positive/safe Corpus pairs, Chinese/English and precision-boundary
  tests, guidance, taxonomy risks and exact detector mappings.
- A repaired long-document semantic instruction-conflict evidence path:
  deep constraints now survive the eight-Evidence Provider egress cap.
- Updated public capability claims and a resolved Butler/semantic decision
  record. Butler remains read-only reference evidence, never a label source.

## Verification

- 590 pytest cases pass, 0 skipped.
- Corpus v1.15.0: 80 balanced L0 cases across 24 measured risks.
- Runtime taxonomy: 30 risks and 61 mapped components.
- `python3 tools/verify_repo.py` passes.

## Explicit non-deliverables

- No local model dependency or weight download.
- No retry of consumed semantic protocol-v2 Selection.
- No sealed-Test consumption.
- No Skill execution.
- No claim of complete semantic coverage or evaluated detection accuracy.

## Handoff

Round 55 starts V1.5 Prompt black-box implementation. The first real external
run still requires an operator-supplied test set, dated model, bounded
call/token/cost budget and recording location.
