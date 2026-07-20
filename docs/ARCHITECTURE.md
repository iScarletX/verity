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
       │  verity/parser.py    │             │  Provider protocol only │
       │  verity/gitleaks_*   │             │  no Provider bundled    │
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
       │  Report projection (verity/report.py)              │
       │  JSON  +  single-file HTML (CSP)  +  SARIF 2.1.0   │
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
                                 loopback only
                                 no external assets
```

## Bright lines

- **Deterministic → Semantic**: only. The deterministic engine
  never imports `verity.semantic` (see architectural test in
  `tests/test_semantic.py`).
- **Semantic → Deterministic**: never writes. It reads a
  projection dict.
- **Reviewed artifact → Provider config**: forbidden. Provider config
  is only accepted from a trusted caller / env var name / CLI arg.
- **Provider payload**: passes through the egress gate
  (`verity/semantic/egress.py`) which drops sensitive Evidence, caps
  string lengths, and records only sizes + SHA-256 in the payload
  audit.

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
- What's actually running now → `docs/CURRENT_STATE.md`
- History → `docs/PROGRESS.md`
- Active plan → `plans/ACTIVE.md`
- Engineering spec (authoritative) →
  `docs/spec/ENGINEERING_SPEC-v0.3.md`
- Mature-project reuse → `docs/spec/REUSE_DECISIONS-v0.2.md`
- Known pitfalls → `docs/LESSONS.md`
