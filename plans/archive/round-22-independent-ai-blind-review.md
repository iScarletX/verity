# Round 22 — Independent dual-AI blind Corpus label review

## Status

Approved by the maintainer. Two new review-only Agents with no prior Verity
workspace memory are assigned different model families and isolated packets.

## Goal

Obtain a reproducible independent-AI second opinion on currently provisional
Corpus labels without exposing current answers, Verity outputs, model-quality
results, detector Rule ids, answer rationales or the other reviewer's work.

## Scope

- Blind-review 26 L0 cases and the 28 semantic Calibration/Selection cases.
- Keep all 14 sealed-Test cases out of review packets; exposing them to review
  models would consume the sealed boundary.
- Generate two packets with different deterministic aliases and order.
- Each item supplies only object type, language/prompt kind, one target risk
  definition/boundary and anonymized artifact contents.
- Reviewers return `present`, `absent` or `uncertain`, confidence 0..1, cited
  file/range description and a short reason.
- Validate reviewer JSON strictly and compare only after both finish.
- Mark the outcome `independent_ai_review`, never human/expert review.

## Independence and safety

- Reviewers do not receive repository access tasks or original case paths.
- No current label, expected assessment/risk, rationale, severity, split,
  labelStatus, Verity Finding, Selection output or detector id enters packets.
- Skill frontmatter identity names are replaced with neutral aliases; semantic
  body text is otherwise preserved even when it contains words such as safe.
- Packets, alias maps and raw reviewer responses are local under gitignored
  `.verity-data/blind-review/`; only aggregate, scrubbed adjudication facts may
  be committed.
- Main Agent does not make first-pass labels. Reviewer disagreement or any
  `uncertain` result cannot automatically upgrade a case.

## Acceptance

- machine test proves 54 items per packet, zero sealed-Test inclusion, no answer
  fields/identifiers and different aliases/order;
- both reviewer outputs pass strict schema and contain exactly one decision per
  assigned alias;
- agreement/disagreement metrics are reproducible after alias remapping;
- only unanimous non-uncertain cases may become candidates for
  `independent_ai_review`; original author labels are not silently overwritten;
- pytest, clean verify_repo and GitHub CI pass before any label-status change;
- sealed Test remains unconsumed.
