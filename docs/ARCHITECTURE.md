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
       │  verity/skill_rules  │             │  trusted Provider only  │
       │  verity/parser.py    │             │  Provider protocols +   │
       │  verity/capabilities │             │  policy + structured    │
       │  verity/gitleaks_*   │             │  evidence + HTTPS JSON  │
       │  verity/bandit_*     │             └─────────┬───────────────┘
       │                      │                       │
       │  Rules → Evidence    │            Extractor  │
       │  → RuleMatchEvent    │            Catalog +  │
       │  → deterministic     │          bounded sweep│
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
       │    semantic:       not_enabled / completed / failed│
       │    promptBlackbox: not_enabled / completed / failed│
       │    skillSandbox:   not_enabled / failed (unavailable)│
       │    agentInstructionRuntime: not_enabled / completed│
       │                             / failed (CLI-only)     │
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

## Artifact-aware dynamic branch

After deterministic parsing, `verity.dynamic.profile` creates a bounded,
evidence-backed behavior profile. `verity.dynamic.planner` then classifies
every registered check as `selected`, `not_applicable`, or `unavailable`.
The reviewed artifact cannot choose the Provider, policy, severity, fixture,
or sandbox boundary.

- Prompt black-box defaults to `artifact_aware`; `all` is an explicit
  historical research override and `explicit` runs caller-named built-ins.
  Raw Prompt/probe/Provider response text exists only inside the bounded run;
  public JSON/HTML/Web/SARIF projections retain controlled ids, outcomes,
  booleans, counts, lengths, durations, and digests, never those payloads.
- Five director/storyboard and four art-style text contracts use fixed probes
  and deterministic structure/numeric/trace oracles. Image fidelity remains
  `unavailable:image_runtime_not_configured` without a real image adapter.
- The executable-Skill sandbox registry and synthetic fixtures remain research
  material, but supported Review/CLI/Web execution currently fails closed as
  `sandbox_isolation_hardening_required`. No product path constructs the old
  runner. Host reads, output/disk/process budgets, observer integrity, cleanup,
  and a controlled signal projection must all be proven before re-enabling it.
- Agent-instruction Skills use a distinct Harness simulation. It reports
  `unavailable:agent_runtime_not_configured` by default, and selects
  `agent_instruction.runtime` only after complete, trusted CLI configuration;
  there is no Web enable surface.
- `verity.issues` is a read-only projection over existing results. It groups
  by risk id, preserves every occurrence, and never lets a bounded dynamic
  pass erase static evidence.

## Two independent coverage axes

- **Execution status** lives in ReviewPlan / Execution / Coverage and answers:
  did the checks planned for this review run? Runtime capability words such as
  `completed`, `failed`, and `not_enabled` belong here. Since Round 74,
  `promptBlackbox` uses the same vocabulary behind trusted caller opt-in.
  `skillSandbox` defaults to `not_enabled`; an explicit request is currently
  `failed`/`sandbox_isolation_hardening_required` before any runner import or
  construction. Neither can be triggered by the reviewed artifact itself.
  `agentInstructionRuntime` uses the same vocabulary but is a separate,
  CLI-only, caller-enabled stage whose default is also `not_enabled`.
- **Detection breadth** lives in the machine-readable `standards/` taxonomy
  and answers: how broadly and accurately can Verity detect this risk class?
  Its controlled levels are `none`, `signal`, `partial`, `substantial`, and
  `evaluated`.
- A run may be `completed` while detection breadth remains `signal` or
  `partial`. Before the Round-15 corpus exists, no risk may exceed `partial`.
- `verity.standards.validate_runtime_detector_coverage()` binds all runtime
  deterministic Rules, semantic Finding Types, V1.5 black-box scenarios, and
  the dormant V2 sandbox research signals plus V2 Agent-runtime signals to the taxonomy and fails on
  registry drift. The current 156 mappings comprise 63 deterministic rules,
  1 capability extractor, 41 semantic types, 35 black-box scenarios, 12
  sandbox signals, and 4 Agent-runtime attempt signals. `V2_agent_runtime`
  breadth is 42 none / 4 signal / 0 partial / 0 substantial / 0 evaluated.
- `verity.findings_view.completed_findings` is the read-only consumer boundary:
  deterministic Findings are always present, while semantic Findings enter
  verdict/gate/score/Web/HTML/SARIF only after the semantic stage completed.
  This does not let semantic code write to or filter the deterministic engine.
- `verity.scoring` is a pure policy projection after report capabilities are
  known. It maps Findings through the standards detector map, applies bounded
  diminishing deductions and severity caps, and refuses a numeric score on
  Coverage/mapping failure. Since Round 88 a completed V1.5 black-box stage's
  failed scenarios are mapped the same way (via `blackbox_scenario` detector
  entries) and refuse a numeric score if the stage was requested but did not
  complete. Round 89 defined a V2 sandbox signal vocabulary
  (`sandbox_write_outside_tmpdir`, `sandbox_network_attempt`,
  `sandbox_subprocess_attempt` — see `sandbox.models.SANDBOX_SIGNAL_DETECTORS`)
  for the earlier research runner. Those mappings are now dormant: supported
  product paths cannot produce a completed sandbox observation until isolation
  hardening is separately reviewed. Review confidence and remediation are
  separate; neither changes Finding identity, severity, gate exit codes or
  dispositions.
- A completed Agent Harness stage contributes only four policy-fixed attempt
  signals: synthetic sensitive read (`VR-SKILL-014`, high), blocked network
  (`VR-SKILL-009`, medium), blocked shell (`VR-SKILL-006`, high), and a fake-
  credential marker in blocked HTTP arguments (`VR-SKILL-011`, high). Score
  policy 1.1.0 and confidence policy 1.4.0 project those observations; a clean
  completion is not a safety or accuracy result.
- `verity.corpus` reads an independent risk-id answer key and measures the
  current L0 pipeline twice per case. Eighty-two fixed semantic Provider
  replays cover confirmed/rejected pairs for forty-one controlled Finding
  Types; they exercise contracts only and explicitly do not measure model
  quality. `verity.semantic_quality` keeps the consumed 42-case protocol v2
  frozen for historical reproducibility.
- `verity.semantic_benchmark` validates versioned 112-case answer-hidden
  Verity/Butler corpora, creates separately randomized system packets,
  validates repeated scrubbed observations, and permits a scoped superiority
  claim only after independent digest-bound labels and both absolute and
  relative gates pass. Protocol v3 remains immutable development evidence;
  protocol v4 hidden holdouts additionally carry the catalog's exact
  applicability/confirm/reject/insufficient policy to every system and label
  reviewer. Hidden v5 is consumed diagnostic evidence. Its first Verity run
  accidentally measured `model_only`, and its legacy independent-AI
  attestation disagreed with the precommitted provisional labels on 18 cases.
  The comparator now quarantines any such hidden-holdout disagreement before
  metrics are accepted. Local hidden v6 is frozen before remote observation
  with the product's `catalog_first` strategy, 112/112 extractor coverage,
  56 positive catalog hypotheses, and 56 safe pre-model suppressions. Its
  remote payload is not authorized. The comparator requires all
  thirty controlled Finding Types,
  at least twenty-nine mapped risk ids, a 45-item pinned Butler crosswalk with
  no open gaps, and a healthy reference run before relative checks. Provisional
  labels, missing observations, fewer than 112 cases, any breadth gap, budget
  exhaustion, error rate above 5%, successful-run coverage below 95%, or an
  unresolved label disagreement produces no claim. Mutable Provider records
  and hidden holdout payloads stay under gitignored local paths; no evaluation
  path contains an aggregate safety score.
- `verity.closure` (policy v2.1.0) computes a scoped V1 release decision. The
  `decision` covers only the deterministic static auditor and is
  `release_candidate` on green engineering acceptance, with no evaluated-
  accuracy claim (breadth limits stay in `disclosedLimitations`). The
  controlled semantic / evaluated-accuracy work is a separate
  `semanticQualityTrack` (`inReleaseGate=false`) whose open blockers cannot be
  averaged away by passing tests but do not gate the deterministic release.

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
  is only accepted from a trusted caller / env var name / CLI arg or the
  loopback Web settings endpoint. Web URL/model preferences use strict,
  owner-only JSON; the API key uses the current macOS user's Keychain.
  Reviewed content cannot select, modify, or weaken these settings.
- **Reviewed Agent instruction → Harness authority**: forbidden. Only the
  trusted CLI caller can enable the stage and choose absolute Node/DSH entry
  paths, their SHA-256 pins, exact DSH version, Provider URL/model, API-key
  environment-variable name, scenario IDs, and timeout. The plugin,
  model-facing tool catalog, Cordis permission patch, output/trace ceilings,
  and temporary roots are fixed or generated by Verity on the CLI path; the
  CLI caller cannot replace them. The reviewed artifact cannot set or weaken
  any of these values or controls.
- **Harness simulation → host effects**: forbidden for the four model-facing
  tools. Synthetic read is in memory; HTTP and shell are blocked; approval is
  denied. The Skill instructions and scenario prompt do cross a real Provider
  network boundary when enabled, but no tool performs a host read, HTTP action,
  subprocess, or approval effect.
- **Harness process containment**: bounded, not absolute. Each scenario gets a
  disposable process/roots, clean allowlisted environment, bounded streams and
  trace, and process-group cleanup. This is not an OS, process, or network
  security sandbox; a descendant that successfully calls `setsid()` can escape
  same-session cleanup. Stronger use requires an outer container or microVM,
  destination-allowlisted egress, and fuller dependency/image pinning.
- **Harness identity and retention**: each pinned Node/DSH entry is streamed
  once in bounded chunks; the same bytes update SHA-256 and an owner-only
  private snapshot, followed by descriptor stability checks. Version and
  scenario launches execute the private snapshots, not the caller paths. The
  private DSH capsule links the adjacent npm closure only for Node module
  resolution; that closure remains unpinned and unauthenticated by the two
  entry hashes. At most two Skill-loader result markers are written; exactly
  one successful marker is required, otherwise parsing fails closed. Reports
  keep only controlled enums, counts, digests, target classifications, and a
  credential-marker boolean. Raw model responses, tool arguments, canaries,
  credentials, host paths, roots, streams, and traces are discarded/deleted.
  No real Provider/model/scenario E2E ran in this round.
- **Stored Web credential → browser**: forbidden. The settings API returns
  only non-secret preferences plus `keySaved`; it never returns the key.
  JavaScript uses no browser storage for credentials. Keychain access is
  bounded, no-shell, runs outside the ASGI event loop, and supplies a new key
  through stdin rather than process arguments. A request-supplied Provider URL
  never inherits a saved key, and changing the saved URL requires a new key.
- **Web maximum-scan policy**: the loopback UI and server force
  `standard` Skill scanning and `redacted_evidence` semantic egress. Removed
  selectors are not the enforcement boundary: stale `minimal` or
  `metadata_only` requests are upgraded server-side.
- **OpenAI-compatible Provider adapter**: `semantic/eval_provider.py`'s
  `generate_candidates`/`validate_candidate` are reachable from explicit
  evaluation tools AND from the Web UI's real, user-configured Provider path
  (`web/provider_web.py` builds one instance per role from whatever loopback-
  submitted base_url/api_key/model the local user configured, since Round 65
  — see `plans/ACTIVE.md`). Both callers get the same whole-run call-budget
  preflight and bounded request/response handling. Only its separate
  `review_label` method stays eval-only, reachable solely from the answer-
  hidden label-review comparison runner with a versioned synthetic corpus,
  never from the product orchestrator. It stores no raw Provider request or
  response.
- **Comparison labels → system runs**: answer keys remain outside both
  Verity and Butler packets. Alias maps are local, system-specific, and never
  sent as reviewed content. Each map row is bound to its exact packet item
  digest. Independent labels are derived from two or three distinct,
  independently repeated answer-hidden reviewer observation sets. Each set
  must use an odd repetition count of at least three and reach two-thirds
  decisive consensus across all planned repetitions. Provider errors and
  inconclusive results never contribute a vote, so two explicit matching
  decisions plus one error pass while a split decision plus one error fails.
  Two reviewers must agree exactly; three reviewers use a per-case majority.
  The runner may retry an invalid or failed Provider response only under a
  fingerprinted per-repetition attempt limit and a worst-case whole-run call
  budget. A caller cannot establish
  independence by supplying reviewer names alone. The comparator canonicalizes
  both system mappings and refuses a claim unless the derived attestation
  covers the same payload digests. For hidden holdouts it then compares the
  blind consensus with the labels committed before any remote review. A
  disagreement is not auto-resolved by another majority: it is exposed as
  `labels_require_adjudication` and blocks the claim.
- **Catalog hypothesis → Validator**: bounded catalog-owned hypotheses may
  bypass an empty Candidate Generator result only for explicitly structured
  facts. They never bypass the independent Validator, set severity, or create
  evidence. One strongest catalog hypothesis takes precedence over a competing
  model hypothesis for the same extractor seed. Scrubbed stage diagnostics
  contain counts and controlled reason codes only, never source text, claims,
  subjects, case ids, Finding Types, labels, or Provider responses.
- **Candidate strategy**: product CLI/Web review uses `catalog_first`.
  Structured positive facts create catalog hypotheses; structured safe controls
  can gate off model candidate generation. Prompt types with no deterministic
  seed each receive their OWN independent bounded whole-prompt pass against
  only that single registered type/subject catalog entry -- not one call
  packed with every no-seed type, which was found to silently starve some
  types of a real model's attention as the catalog grew. The sweep cannot
  invent evidence or severity, and every accepted candidate still reaches the
  independent Validator (which may itself be backed by more than one
  Provider for majority voting -- see below). `model_only` is
  reserved for controlled Provider benchmarks and transport tests so model
  quality remains measurable without changing the product precision policy.
  The comparison CLI defaults to `catalog_first`, and the selected strategy is
  included in both the configuration fingerprint and the budget sidecar.
- **Validator majority voting**: the Validator role may be backed by more
  than one independently configured Provider (2-3 different models is the
  recommended range, matching Butler's own multi-model vote design). Every
  candidate is judged by all configured Validator votes; the aggregate
  decision is the three-state majority (`confirmed`/`rejected`/
  `insufficient_evidence`). A tie (e.g. 1-1 with two voters) becomes
  `insufficient_evidence` with a synthesized `vote_split` reason code rather
  than being silently resolved either way. A vote whose own call fails
  (timeout, malformed output) does not count toward the majority denominator
  -- mirrors the "provider errors don't vote" rule from the label-review
  consensus protocol (`semantic_benchmark.py::_independent_review_consensus`)
  -- and does not by itself flip the overall run status to `failed` if the
  remaining votes still reach a real majority. The default `validators=[validator]`
  (a single Provider) preserves the pre-voting single-model behaviour exactly.
- **Evidence projection**: deterministic and completed-semantic Findings remain
  physically separate in the Review object, but the read-only report consumer
  joins both evidence pools. Web source highlighting, remediations, JSON, HTML,
  SARIF, verdict, and scoring therefore resolve the same completed Finding
  evidence ids.
- **Read-only Butler reference**: the eval CLI builds a temporary Node bundle
  from an explicitly supplied Butler source tree and existing dependencies.
  It reuses Butler's profiler, static checker, selected LLM checks and vote
  aggregator without writing to Butler. A source/configuration fingerprint and
  conservative call/token/spend reservations bind the observations. Independent
  item/repetition tasks may use a fingerprinted concurrency limit of at most
  eight; reservations occur synchronously before every network call. Final
  Butler consolidation/deduplication is excluded and disclosed because it can
  contact a separate embeddings endpoint and add findings beyond the packet's
  single target risk. The adapter returns a strict budget snapshot; exhausted
  budget is propagated to observation health and blocks relative comparison.
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
- **@deepseek-ai/dsh 0.1.1-rc.2** (MIT) — optional external developer-preview
  Agent Harness. It is neither vendored nor auto-installed, and is not a Python
  dependency. Trusted callers provide the pinned JavaScript entry; the adjacent
  npm closure remains outside the two-entry-file hash scope.
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
