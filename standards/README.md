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
- `detector_mappings.json`: exact mapping of all runtime Rules, capability
  extractors and semantic Finding Types to the taxonomy.
- `detector_candidates.json`: evidence-based adopt/defer/reassess decisions and
  mandatory safety controls for mature external detector candidates. A
  candidate entry does not mean the tool is installed or integrated.

`verity.standards` validates all three. Round-14 tests fail on unknown sources,
unknown controls, unmapped runtime detectors, layer/schema drift, or unsupported
pre-corpus coverage claims.

## Round-14 baseline

The first baseline contained 25 unified risks and 36 mapped detectors. Round
16 added one official Agent Skills field Rule and one non-Finding capability
fact extractor; the exact current count is enforced from runtime registries.

| Layer | none | signal | partial | substantial | evaluated |
|---|---:|---:|---:|---:|---:|
| L0 static | 6 | 17 | 9 | 0 | 0 |
| L1 semantic | 16 | 15 | 1 | 0 | 0 |
| V1.5 black-box | 32 | 0 | 0 | 0 | 0 |
| V2 sandbox | 32 | 0 | 0 | 0 | 0 |

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
