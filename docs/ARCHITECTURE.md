# ARCHITECTURE — one-page map

```
                             REVIEWED ARTIFACT
                           (prompt text  OR  skill folder)
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │   Safe intake (V1)    │  no-follow symlinks
                         │  verity/intake.py     │  path escape reject
                         │                       │  size / count budgets
                         └───────────┬───────────┘
                                     │
                                     ▼
                       ArtifactSnapshot (immutable)
                                     │
                    ┌────────────────┴──────────────────┐
                    │                                   │
                    ▼                                   ▼
       ┌──────────────────────┐             ┌─────────────────────────┐
       │  DETERMINISTIC (V1)  │             │  SEMANTIC (V1 exp'l)    │
       │  verity/engine.py    │             │  verity/semantic/       │
       │  verity/skill_rules  │             │  DEFAULT OFF            │
       │  verity/parser.py    │             │  Provider protocols +   │
       │  verity/capabilities │             │  bounded HTTPS JSON     │
       │  verity/gitleaks_*   │             │                         │
       │  verity/bandit_*     │             └─────────┬───────────────┘
       │                      │                       │
       │  Rules → Evidence    │            Extractor  │
       │  → RuleMatchEvent    │            Candidate  │
       │  → deterministic     │            Generator  │
       │       Finding        │            Validator  │
       └──────────┬───────────┘            Assessment │
                  │                        semantic   │
                  │                        Finding    │
                  │                                   │
                  ▼                                   ▼
       ┌────────────────────────────────────────────────────┐
       │  Completed-Finding consumer projection             │
       │  verdict / gate / score / Web / JSON / HTML / SARIF│
       │                                                    │
       │  capabilities:                                     │
       │    static:         completed / failed              │
       │    semantic:       not_enabled / completed / fail  │
       │    promptBlackbox: not_implemented                 │
       │    skillSandbox:   not_implemented                 │
       └──────────┬────────────────────┬────────────────────┘
                  │                    │
                  ▼                    ▼
              CLI (verity.cli)   Web MVP (verity/web/)
                    │             loopback only / no external assets
                    └──────────────┬───────────────┘
                                   ▼
                     Trusted Skill project context
                       verity/history.py
                 safe immutable projection + atomic JSON
                   gitignored .verity-data/ (0700/0600)
                                   │
                                   ▼
                    scope-aware five-state baseline diff
```

## Two independent coverage axes

- **Execution status** lives in ReviewPlan / Execution / Coverage and answers:
  did the checks planned for this review run? Runtime capability words such as
  `completed`, `failed`, `not_enabled`, and `not_implemented` belong here.
- **Detection breadth** lives in the machine-readable `standards/` taxonomy
  and answers: how broadly and accurately can Verity detect this risk class?
  Its controlled levels are `none`, `signal`, `partial`, `substantial`, and
  `evaluated`.
- A run may be `completed` while detection breadth remains `signal` or
  `partial`. Before the Round-15 corpus exists, no risk may exceed `partial`.
- `verity.standards.validate_runtime_detector_coverage()` binds all runtime
  deterministic Rules and semantic Finding Types to the taxonomy and fails on
  registry drift.
- `verity.findings_view.completed_findings` is the read-only consumer boundary:
  deterministic Findings are always present, while semantic Findings enter
  verdict/gate/score/Web/HTML/SARIF only after the semantic stage completed.
  This does not let semantic code write to or filter the deterministic engine.
- `verity.scoring` is a pure policy projection after report capabilities are
  known. It maps Findings through the standards detector map, applies bounded
  diminishing deductions and severity caps, and refuses a numeric score on
  Coverage/mapping failure. Review confidence and remediation are separate;
  neither changes Finding identity, severity, gate exit codes or dispositions.
- `verity.corpus` reads an independent risk-id answer key and measures the
  current L0 pipeline twice per case. Fourteen fixed semantic Provider replays
  cover confirmed/rejected pairs for seven controlled Finding Types; they
  exercise contracts only and explicitly do not measure model quality.
  `verity.semantic_quality` separately validates a 42-case synthetic
  calibration/selection/sealed-test protocol and may drive an eval-only
  OpenAI-compatible adapter through the same SemanticOrchestrator. Its mutable
  scrubbed reports are local research records, not deterministic CI baselines.
  Neither path contains an aggregate safety score.
- `verity.closure` separately computes a binary V1 release decision. Its
  committed offline baseline may say `not_ready` while engineering acceptance
  is green, because missing independent labels/model-quality/sealed-test/
  substantial-coverage evidence cannot be averaged away by passing tests.

## Bright lines

- **Deterministic → Semantic**: only. The deterministic engine
  never imports `verity.semantic` (see architectural test in
  `tests/test_semantic.py`).
- **Semantic → Deterministic**: never writes. It reads a
  projection dict.
- **Reviewed artifact → project identity/history**: forbidden. Opaque artifact identity is minted by the trusted registry; only an existing Web project page or CLI alias/registered ID can add a version. No name/path/digest/similarity linking.
- **Skill root name**: intake retains only one bounded final directory/browser-root component for official Agent Skills name matching. It is not a host path, does not select project identity, is not part of content digests, and is not persisted in history.
- **Capability facts**: deterministic Manifest/Python-AST observations only. They are not Findings, never change gates, expose limitations, and provide evidence for later least-privilege/semantic work.
- **History safety**: allowlisted projection only; strict schema/version, budgets, symlink/owner/mode checks and atomic writes. Schema v2 stores the score/confidence projection created at review time; v1 stays readable but is never backfilled. No raw content/evidence, Secret, Provider wire data, credentials, RedactionMap, or host/temp/tool paths.
- **Score comparison**: requires compatible scope, sufficient Coverage, available scores and the same score-policy version. It is always secondary to the five-state Finding diff and cannot itself prove remediation.
- **Diff resolution**: exact occurrence and controlled stable subject remain distinct. A disappearance is `resolved` only when its relevant current parser/analyzer/rule plan items succeeded; otherwise `unknown_due_to_coverage`.
- **Reviewed artifact → Provider config**: forbidden. Provider config
  is only accepted from a trusted caller / env var name / CLI arg.
- **Eval-only Provider**: `semantic/eval_provider.py` is reachable only from
  `tools/run_semantic_model_eval.py`, accepts only the versioned synthetic
  corpus, has a whole-run call-budget preflight, and is not wired into product
  CLI/Web review. It stores no raw Provider request or response.
- **Provider payload**: passes through the egress gate
  (`verity/semantic/egress.py`) which drops sensitive Evidence, caps
  string lengths, and records only sizes + SHA-256 in the payload
  audit. Capability evidence may expose only allowlisted category/operation
  metadata; arbitrary metadata, raw values and model-authored severity do not
  cross the boundary.
- **Provider transport**: `verity/semantic/http_provider.py` binds one
  trusted config to one role, allows remote HTTPS or loopback HTTP only,
  disables redirects, resolves keys from environment-variable names at
  call time, bounds request/response bytes and time, and discards error
  bodies. Candidate Generator and Validator remain separate instances.

## Mature-component reuse

- **Bandit 1.7.10** (Apache-2.0) — Python AST security static
  analysis. Runs as a controlled subprocess against a staged copy of
  intake'd Python files. Timeout / no-shell / JSON schema validated.
  Never scans the user's original folder.
- **gitleaks 8.28.0** (MIT) — Secret scanner. Not vendored; installer
  in `tools/install_gitleaks.py` verifies archive SHA-256 against
  `tools/gitleaks_release.json` and records the extracted binary's own
  SHA-256 in the install manifest. Runtime re-verifies the binary
  SHA-256 on every call.
- **PyYAML** (MIT), **jsonschema** (MIT), **Starlette 0.41.3** +
  **Uvicorn 0.32.1** (BSD-3-Clause), **python-multipart** (Apache-2.0),
  **anyio / sniffio / h11 / click**.
- Full list + licenses: `THIRD_PARTY_LICENSES.md`.

## SSOT map

For every fact that could drift, look here:

- Behaviour policy → `AGENTS.md`
- Detection sources, taxonomy, breadth, gaps → `standards/*.json`
- Current state + append-only history → `docs/PROGRESS.md`
  (top summary block + round-by-round history below it)
- Active plan → `plans/ACTIVE.md`
- Known pitfalls → `docs/LESSONS.md`
- Collaboration preferences → `docs/MEMORY.md`
- Machine gate → `tools/verify_repo.py`
- CI gate → `.github/workflows/ci.yml`

The upstream engineering spec and mature-project reuse decisions live
outside this repository (in the maintainer's design docs). This
repository does not carry a spec snapshot; if the spec is revised, the
change lands in code and shows up in `docs/PROGRESS.md`.
