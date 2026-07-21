# Verity detection standards baseline

This directory is the machine-readable source of truth for **what Verity aims
to detect and how honest its current breadth claim is**. It does not replace
runtime ReviewPlan / Execution / Coverage records.

## The two axes

1. **Execution status** asks whether the checks planned for one review ran.
   Runtime words include `completed`, `failed`, `not_enabled`, and
   `not_implemented`.
2. **Capability coverage** asks how broadly and accurately Verity detects a
   risk class. Taxonomy words are `none`, `signal`, `partial`, `substantial`,
   and `evaluated`.

A review can have execution status `completed` while the relevant capability
coverage remains only `signal` or `partial`.

## Files

- `sources.json`: official primary sources and controlled identifiers used by
  Verity. Requirements are paraphrased; no large standards text is copied.
- `risks.json`: stable Verity risk ids, source crosswalks, layer boundaries,
  current breadth, and known gaps.
- `detector_mappings.json`: exact mapping of all runtime deterministic rules
  and semantic Finding Types to the taxonomy.

`verity.standards` validates all three. Round-14 tests fail on unknown sources,
unknown controls, unmapped runtime detectors, layer/schema drift, or unsupported
pre-corpus coverage claims.

## Round-14 baseline

The first baseline contains 25 unified risks and 36 mapped detectors (33
deterministic rules and three semantic Finding Types):

| Layer | none | signal | partial | substantial | evaluated |
|---|---:|---:|---:|---:|---:|
| L0 static | 5 | 11 | 9 | 0 | 0 |
| L1 semantic | 19 | 5 | 1 | 0 | 0 |
| V1.5 black-box | 25 | 0 | 0 | 0 | 0 |
| V2 sandbox | 25 | 0 | 0 | 0 | 0 |

These counts are a prioritization baseline, **not a safety score**. Round 15
added the versioned minimal paired corpus under `evals/corpus/v1/` and
reproducible reports under `evals/reports/`. It is sufficient to expose
TP/FP/TN/FN, stability, unsupported and unmeasured states, but not broad enough
to promote any risk to `substantial` or `evaluated`.

## Source-use policy

- Prefer official primary sources.
- Record provenance and retrieval date.
- Reference identifiers and paraphrase; respect source terms.
- External mappings do not prove detector coverage.
- Verity-original risks require an explicit rationale.
- Mature detector candidates are not integrations until license, maintenance,
  containment, output, and version-pinning review is complete.
