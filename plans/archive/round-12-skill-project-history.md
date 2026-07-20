# Round 12 — Skill project identity, local history, and coverage-aware diff

## User-approved scope

Approved on 2026-07-20. This round makes Verity able to remember a Skill
as a logical project, accept later snapshots explicitly inside that
project, and compare findings across those snapshots without guessing
identity from names or content similarity.

The ordinary-user path is Web-first. CLI remains an automation surface,
not the primary interaction the maintainer must use.

## Product meaning

This round has two ordered parts:

1. **Project identity + version history** — establish which reviews belong
   to the same logical Skill. A new version belongs to an existing Skill
   only when submitted from that trusted project context; Verity does not
   infer identity from `SKILL.md` name, folder path, or similarity.
2. **Coverage-aware version diff** — after identity is established,
   compare the latest snapshot with a selected previous snapshot and show
   `new`, `existing`, `changed`, `resolved`, or
   `unknown_due_to_coverage`.

The second part cannot safely exist without the first.

## Goals

### A. Trusted project identity

- Add a trusted local project registry owned by Verity, not by reviewed
  content.
- Create a project with a generated opaque `artifactId` and a bounded
  human display name.
- Add a new Skill version only through an existing project context.
- Ignore/reject any artifact-supplied attempt to choose an `artifactId`,
  baseline, history record, or trusted project metadata.
- Treat a standalone upload as a new project unless the user explicitly
  selects an existing trusted project.
- Do not auto-link by Skill name, directory name/path, content digest,
  similarity, or embedding.

### B. Safe local review history

- Store history in a dedicated project-local, gitignored Verity data
  directory with owner-only permissions where the platform supports it.
- Use atomic writes and strict JSON parsing/schema validation.
- Persist report-safe projections needed for identity, coverage, and
  comparison; do not persist raw file contents, raw secrets, Provider
  payloads/responses, API keys, RedactionMap, host absolute input paths,
  temporary paths, or tool paths.
- Bound project count, versions per project, record size, and total local
  history size. Fail visibly rather than silently dropping records.
- Make corrupted/unknown-version records fail closed and leave existing
  records untouched.
- Keep every submitted Snapshot record immutable. A repeated identical
  content digest is another Review of the same content, not an overwrite.

### C. Coverage-aware baseline/diff

- Compare only snapshots under the same trusted `artifactId` and a
  compatible engine/profile/policy scope.
- Support and explain:
  - `new`: current finding has no reliable prior match;
  - `existing`: exact occurrence remains;
  - `changed`: stable controlled subject remains but occurrence changed;
  - `resolved`: prior finding disappeared and its relevant scope was
    successfully covered in the current review;
  - `unknown_due_to_coverage`: disappearance cannot be trusted because
    required current coverage is missing or failed.
- Never use content similarity or LLM text to delete/merge findings.
- Never carry a baseline relationship across different artifacts.
- Extend the current coarse `baseline.py` logic so resolution is checked
  against relevant plan/execution scope, not only global coverage.

### D. Web-first workflow

- Add a local project list and project creation flow.
- From a project page, let the user choose a Skill folder and submit
  “检查新版本”. This project context supplies the trusted `artifactId`.
- Show version history with time, content digest summary, coverage,
  finding counts, and review status—never host paths or raw content.
- Show a plain-language comparison summary and expandable finding-level
  changes.
- Preserve existing standalone Prompt/Skill review behavior; history is
  explicit, not silently enabled for every upload.
- Keep all current Web security properties: loopback only, strict CSP,
  no external assets, no `innerHTML`, bounded uploads, and temporary
  directory cleanup.

### E. Automation surface

- Provide a minimal CLI project workflow for CI/advanced users using a
  project alias or trusted local ID resolved from the Verity registry.
- CLI and Web must use the same history/core implementation.
- The CLI must not accept an unknown arbitrary `artifactId` as proof that
  a project exists.

## Non-goals

- Disposition actions (`acknowledge`, `accept_risk`).
- Suppression or removal/expiry of suppression. These are deferred to a
  separate Round 13 after identity/history is independently verified.
- Prompt project history in the first slice; Round 12 focuses on Skill
  projects. The storage model must not preclude Prompt support later.
- Prompt black-box execution (V1.5).
- Skill execution or sandbox (V2).
- Agent runtime interception or MCP/tool-call integration.
- ZIP/GitHub URL intake.
- Cloud sync, multi-user accounts, remote database, or cross-machine
  identity.
- Automatic PatchSet application.
- Fuzzy/embedding matching or automatic project-link suggestions.

## Implementation order

1. Tests and contracts for trusted project identity and safe storage.
2. Local project/history store with atomic bounded records.
3. Version submission through the existing Skill review core.
4. Scope-aware baseline matcher and serialized diff projection.
5. Minimal CLI project commands for automation.
6. Web project list → project page → check new version → comparison.
7. Security regression tests, documentation, full machine acceptance.

## Acceptance

### Identity

- Two standalone uploads with identical name/content are not silently
  linked.
- Two versions submitted through one trusted project share one
  `artifactId` and have distinct immutable `snapshotId` / `reviewId`.
- Reviewed content cannot select or override project identity.
- Unknown project IDs/aliases fail visibly.

### Diff correctness

- End-to-end tests cover all five states: `new`, `existing`, `changed`,
  `resolved`, `unknown_due_to_coverage`.
- A relevant analyzer/parser/rule failure makes a disappearing prior
  finding `unknown_due_to_coverage`, never `resolved`.
- Different artifacts and incompatible scopes do not match.
- Exact and controlled stable-subject matching remain separate from
  single-snapshot event deduplication.

### Storage and safety

- History exports/on-disk records contain no raw secret, Provider payload,
  API key, RedactionMap, host input path, temp path, or analyzer tool path.
- Corrupt, oversized, symlinked, wrong-owner/unsafe-permission where
  detectable, and unknown-schema records fail closed.
- Atomic-write interruption tests leave the last valid record readable.
- Storage and version budgets are enforced visibly.

### UX and regression

- A non-technical user can create a Skill project, submit version 1,
  submit version 2 from the same project, and understand the diff without
  seeing an internal ID.
- Existing standalone Prompt/Skill Web review and downloads still work.
- Existing 312 tests remain green; every new behavior has tests that
  would fail before this round.
- `python3 -m pytest` passes.
- `python3 tools/verify_repo.py --require-clean` passes after commit.
- GitHub CI is green.

## Risks

- Incorrect identity linking can inherit the wrong history. Mitigation:
  explicit trusted project context only; no automatic similarity linking.
- Global coverage can falsely mark a finding resolved even if its specific
  analyzer failed. Mitigation: add relevant-scope coverage tests before
  exposing `resolved`.
- Local history can become a sensitive data sink. Mitigation: safe
  projection allowlist, leak tests, atomic bounded storage, no raw input.
- Suppression could hide findings before identity is trustworthy.
  Mitigation: explicitly deferred to Round 13.

## Stage gates

- Product scope approved: yes, 2026-07-20.
- Session Start gates: pytest 312/312 and `verify_repo.py` PASS before
  implementation.
- Programming executor: initial implementation by Coding Agent 程砚
  (`p-2026-07-18-3`), followed by independent main-Agent review and
  verification fixes. The agents did not modify the directory concurrently.

## Status

- Started (planning): 2026-07-20
- Implementation: complete
- Ended: 2026-07-20
- Commit(s): `ccfeafc`, `a00bb45`, plus owner-verification follow-up
