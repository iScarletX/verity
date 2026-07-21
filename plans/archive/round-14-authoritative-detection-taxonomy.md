# Round 14 — Authoritative standards baseline and detection taxonomy

## Status

- Product scope approved: **yes**
- Implementation: in progress
- Owner: main maintainer agent

## Why this round exists

Verity has a strong execution/safety architecture, but its detection breadth
has not yet been measured against an explicit authoritative risk taxonomy.
`completed` currently means that the checks planned for one review executed;
it must not be mistaken for complete detection coverage.

Round 14 establishes the source-controlled capability baseline before any
further rule growth, Provider production work, V1.5 black-box work, or V2
sandbox work.

## Objective

Build a public-safe, machine-checkable chain:

```
authoritative source
→ source control / threat item
→ Verity unified risk id
→ applicable object and detection layer
→ current detector mapping
→ honest current coverage
→ documented gap
```

## Authoritative-source policy

1. Prefer primary official sources over summaries and blogs.
2. Record source title, publisher, version/date, canonical URL, source kind,
   usage basis, and retrieval date.
3. Reference identifiers and paraphrase requirements; do not copy large
   copyrighted passages.
4. A Verity-original risk is allowed only when marked `verity_original` with
   rationale; it cannot masquerade as a standards requirement.
5. Standards mappings do not prove detection coverage.
6. A mature detector may be listed as a candidate only after maintenance,
   license, output, containment, and version-pinning review.

## Deliverables

### 1. Source registry

Add a machine-readable registry covering, at minimum:

- OWASP Top 10 for LLM Applications;
- OWASP Top 10 for Agentic Applications / Agentic Security Initiative;
- NIST AI RMF 1.0 and NIST AI 600-1 Generative AI Profile;
- MITRE ATLAS;
- CWE and CAPEC;
- SLSA and OpenSSF Scorecard;
- official Agent Skills specification;
- Model Context Protocol specification/security guidance;
- the official documentation of mature detector candidates considered for
  later rounds.

Each source must be official and independently checked in a browser.

### 2. Unified risk taxonomy

Create controlled Prompt, Skill, and shared/governance risk ids. Each entry
must declare:

- risk id, title, object scope and concise falsifiable definition;
- authoritative source/control mappings;
- applicable detection layers: `L0_static`, `L1_semantic`,
  `V1_5_blackbox`, `V2_sandbox`;
- the strongest conclusion each layer is allowed to make;
- current coverage and known gaps.

### 3. Coverage semantics

Separate two axes:

- execution status: whether this review's planned components ran;
- capability coverage: how broadly and accurately Verity detects a risk.

Controlled capability-coverage values:

- `none`: no supported detector;
- `signal`: narrow heuristic or warning only;
- `partial`: one or more useful subcases, with material known gaps;
- `substantial`: broad implementation plus corpus measurements, still not a
  universal proof;
- `evaluated`: threshold-backed release claim against a versioned corpus.

Round 14 may assign only `none`, `signal`, or `partial`; stronger claims require
Round-15 corpus evidence.

### 4. Existing-detector mapping

Map every registered deterministic Rule and all three current semantic Finding
Types to one or more unified risk ids. Record unmapped/unsupported risks
explicitly. Mapping is not deduplication and does not change Finding identity.

### 5. Machine validation

Tests must fail when:

- a risk references an unknown source/control;
- a source lacks mandatory provenance fields or uses a non-HTTPS URL;
- a risk has no object scope or detection-layer boundary;
- an existing Rule or semantic Finding Type is absent from detector mapping;
- coverage exceeds `partial` before an evaluation reference exists;
- runtime capabilities are confused with V1.5/V2 implementations.

### 6. Product and roadmap correction

Update front-page/architecture/progress copy so ordinary users can distinguish
"all planned checks ran" from "broad risk coverage". Record the new gated
sequence:

```
Round 14 standards/taxonomy
→ Round 15 corpus/metrics
→ Round 16 static breadth
→ Round 17 semantic breadth
→ stop for maintainer decision
```

## Acceptance criteria

- Every source entry is verified against an official primary page.
- Every risk has a traceable source or an explicit Verity-original rationale.
- Every existing deterministic Rule and semantic Finding Type is mapped.
- Current gaps remain visible; no `full`, `complete coverage`, `substantial`, or
  `evaluated` claim is made without corpus evidence.
- No detector behavior, Provider behavior, V1.5 or V2 runtime is added.
- Full pytest and `verify_repo.py --require-clean` pass.
- Commit is pushed and GitHub CI is green.

## Explicit non-goals

- Adding or changing detection rules.
- Building the Round-15 corpus or choosing release thresholds.
- Installing or integrating Semgrep, ShellCheck, OSV-Scanner, YARA, or other
  detector candidates.
- Real OpenRouter calls, model selection, API-key UI, retries, caching, cost
  controls, or default semantic enablement.
- Prompt black-box execution.
- Skill execution or sandboxing.
