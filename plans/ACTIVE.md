# Round 16 — Standards-driven static detection breadth

## Status

- Product scope approved: **yes**
- Implementation: not started; begins only after Round-15 CI is green
- Owner: main maintainer agent

## Objective

Improve L0 static breadth from the Round-14 gap matrix using Round-15 paired
corpus measurement. Do not chase rule count. Prioritize authoritative,
deterministic, high-value coverage and mature maintained detectors where their
license/containment/output boundaries are acceptable.

## Ordered workstreams

### 1. Agent Skills specification conformance

Correct and version Skill metadata validation against the official Agent Skills
specification, including at minimum:

- exact `name` syntax/length/consecutive-hyphen and parent-directory matching;
- `description` required/length boundaries;
- `compatibility`, `metadata`, `allowed-tools` type/length constraints;
- supported spec version in report/plan metadata;
- body/reference limits that can be proved statically.

Existing historical Rule identity must be migrated deliberately (`supersedes`)
or preserved when only control metadata changes. Every change gets paired
Corpus cases, including legacy values that the old implementation accepted.

### 2. Capability fact model

Build deterministic normalized facts for file, process, network, credential,
configuration, installation and tool capabilities. Facts are not Findings.
They become evidence for least-privilege comparisons and Round-17 semantic
review. Unsupported languages remain explicit.

### 3. Mature detector candidate decisions

Evaluate, then accept or reject with evidence:

- OSV-Scanner for dependency vulnerabilities;
- ShellCheck for shell analysis (GPL/distribution boundary must be resolved);
- Semgrep open-source engine for cross-language/taint breadth (separate from
  hosted platform features and terms);
- a JavaScript/TypeScript path if Semgrep is not acceptable.

For every accepted external tool: pin version/hash, scan only staged snapshot
copies, no shell invocation, strict structured output, budgets/timeouts,
redaction, Coverage failure semantics, Finding dedup and positive/safe corpus.
No automatic installation outside the repository.

### 4. Prompt deterministic contract expansion

Only standards/corpus-backed deterministic checks: structured contracts,
variable definition/use, trust-boundary markers, declared tool names and
machine-parseable conflicts. Contextual judgment remains L1; absence of an
optional best practice is not automatically a vulnerability.

## Acceptance and measurement

- Every new/changed detector maps to Round-14 risk ids and causes no registry
  drift.
- Every detector has independent positive/safe Corpus cases, boundary cases,
  language/object declaration and visible unsupported scope.
- Per-risk baseline changes are reviewed; High/Critical FN cannot be hidden by
  averages. No aggregate safety score.
- Agent Skills spec mismatch identified in Round 14 is closed or documented
  with an explicit compatibility reason.
- No risk is promoted to `substantial`/`evaluated` while labels remain
  `provisional_single_review` or corpus breadth is inadequate.
- Full pytest, corpus reproduction, `verify_repo.py --require-clean`, push and
  green CI.

## Explicit non-goals

- Expanding semantic Finding Types (Round 17).
- Real Provider/OpenRouter/model calls or Web API-key/model UI.
- V1.5 Prompt black-box execution.
- V2 Skill execution/sandbox.
- ZIP/GitHub URL intake.
- Automatically applying fixes or installing reviewed Skill dependencies.
