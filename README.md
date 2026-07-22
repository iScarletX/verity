# Verity — Prompt & Skill Auditor

> **New agents / new chats — start here:** read `AGENTS.md`, then run
> `python3 tools/verify_repo.py`. The current test count and
> capability matrix live at the top of `docs/PROGRESS.md`.
>
> **V1 deterministic static auditor — release decision: `release_candidate`
> (engineering preview).** Closure policy v2.0.0 scopes the release to the
> deterministic static auditor (rules + Bandit + gitleaks + JSON/HTML/SARIF +
> Web/CLI + explainable score/coverage), whose engineering acceptance is green
> and reproducible. This is an honest engineering preview: it does **not** claim
> evaluated detection accuracy, and its breadth limits are disclosed in every
> review.
>
> The **controlled semantic (LLM-assisted) review is a separate, experimental,
> default-OFF track and is NOT part of this release gate.** It remains
> `experimental_not_ready`: 54 non-sealed Corpus labels have independent dual-AI
> review (not human expert review), protocol-v1 Selection was invalidated after
> that review found two mislabeled artifacts, the first frozen protocol-v2
> Selection returned `not_eligible`, the sealed split is unconsumed, and no
> unified risk has substantial/evaluated evidence. See the reproducible
> [`evals/reports/v1-closure.json`](evals/reports/v1-closure.json)
> (`decision` covers only the deterministic scope; `semanticQualityTrack` lists
> the open experimental blockers).
>
> High-confidence deterministic Prompt/Skill rules + controlled Bandit and
> gitleaks integration + SARIF 2.1.0
> export + a local Web MVP for non-technical users + an **experimental,
> default-OFF controlled semantic-review path** (Evidence →
> SemanticCandidate → Validator → CandidateAssessment → semantic Finding)
> with seven controlled semantic risk types, fixed contract replays, and an
> optional bounded JSON-over-HTTPS Provider adapter, plus a deterministic
> explainable safety score / separate review-confidence grade / controlled
> remediation-and-re-review projection.
> Read-only V1. **Not** a sandbox, **not** a runtime evaluator. Semantic
> calls occur only after explicit opt-in and trusted caller configuration;
> opting in without complete configuration honestly returns
> `provider_not_configured`.

## Product roadmap (must not be lost)

Verity is planned as a three-layer audit tool:

| Version | Layer | Status |
|---|---|---|
| **V1** (this repo) | Deterministic static checks (release scope) + a separate experimental controlled semantic review | **Deterministic static auditor: `release_candidate` engineering preview (no evaluated-accuracy claim). Semantic review: experimental, default-off, `experimental_not_ready`, not in the release gate.** |
| **V1.5** | Black-box Prompt evaluation (run prompts against a model, score outputs) | **Not implemented.** Blocked until standards, corpus, static breadth and semantic breadth foundations are complete. |
| **V2** | Isolated, one-shot Skill sandbox with fake filesystem, fake credentials, controlled network — observing process/file/network/exfiltration behaviour of the Skill under audit | **Not implemented.** Later phase. |

### Detection breadth is not execution status

`static: completed` in a report means the checks planned for that review
executed. It does **not** mean Verity detects every static risk. Likewise, a
future `semantic: completed` means the controlled semantic stage ran, not that
semantic coverage is complete.

The machine-readable [`standards/`](standards/README.md) baseline separates
these axes. It records 27 unified risks and rates current breadth only as
`none`, `signal`, or `partial`. Current L1 breadth is still only 17 none / 9
signal / 1 partial: seven semantic Finding Types do not make semantic review
complete. The versioned [`evals/`](evals/README.md)
minimal paired corpus reproduces per-risk L0 confusion matrices and fixed
semantic pipeline contract replays. A separate 42-case synthetic three-split
protocol can measure one explicitly chosen real-model configuration. Protocol
v2 binds the Corpus digest into the configuration fingerprint; 28
Calibration/Selection labels are `independent_ai_review`, while 14 sealed-Test
labels remain `provisional_single_review`. A historical v1 Selection was
invalidated after review-driven Corpus correction and is not production
accuracy evidence. The
gated sequence is:

```text
standards/taxonomy → corpus/metrics → static breadth → semantic breadth
→ synthetic real-model quality protocol → explainable score/remediation
→ stop before Provider productization or V1.5 without a new decision
```

### Score is not a safety guarantee

A numeric 0–100 safety score is shown only when deterministic Coverage is
sufficient. Critical/High/Medium/Low findings cap it at 39/59/79/99, and every
deduction is traceable to a unified risk id and Finding. `100` means only “no
deduction in checks that actually completed”, never “100% safe”. Missing or
failed critical checks produce **no numeric score**. A separate A–D review
confidence grade lists semantic/profile/breadth/runtime limitations; A is not
currently reachable because V1.5/V2 and evaluated breadth are absent.
Remediation is proposal-only and must pass a same-scope re-review. Advisory
`accept_risk`/`false_positive` dispositions never rewrite severity or raw score.

**V1 is strictly read-only.** It does NOT execute the skill under review,
install its dependencies, start unknown services, call into review-target
code, or recursively expand unknown nested archives. External semantic
Provider calls are default-OFF and allowed only after explicit user opt-in,
trusted endpoint/model/credential configuration, a non-`off` egress policy,
and schema/payload/budget gates. ZIP and GitHub intake remain later gates.

**Scope invariants (from `01-Verity工程规格-v0.3.md`):**

- **One `Review` corresponds to exactly one Artifact per Review** — batch scans are deferred.
- Deterministic Findings are **physically isolated** from any LLM path.
- Uncovered/failed/skipped checks are **not** reported as "no problem."

## Architecture at a glance

```
SourceReceipt
  → ArtifactSnapshot          (safe intake; carries controlled prompt_kind)
  → ReviewPlan                (one AnalysisPlanItem per Rule)
  → EvidenceRecord[]          (Evidence-first; secret evidence is redacted)
  → RuleMatchEvent[]          (deterministic; eventDedupKey stable across runs)
  → deterministic Finding[]   (pure code path, no LLM, no filter)
  → CoverageAssessment        (Plan ⇢ Execution reconciliation;
                                 `not_applicable` gate is explicit,
                                 `blocked_by_upstream_failure` is not silent)
  → ReportProjection          (JSON + single-file static HTML, CSP-protected)
```

Two independent engines (Prompt, Skill) share the same data model and
report infrastructure but have separate rule registries. See
`src/verity/`:

- `canonical.py` — canonical serialization + fingerprints (§2.2, §4.2, §5.1)
- `models.py` — Core data types (Artifact, Snapshot, Evidence, RuleMatch, Candidate, Assessment, Finding, Plan, Coverage, PatchSet). Snapshot has a controlled `promptKind` enum for Prompt engine.
- `registry.py` — FindingType + Rule registries (§6 supersedes, §8 subject_key, §18.1 supply-chain). Rules declare `applicablePromptKinds` for prompt-kind gating.
- `engine.py` — Rule execution + deterministic Finding pipeline (§7.4). Rules return `RuleHit(evidences=[...], subject=...)`; multi-evidence findings (e.g. duplicate assignment) are first-class.
- `validation_policy.py` — Validator containment contract (§7.2, §7.3)
- `builtins.py` — Built-in FindingTypes and Rules
- `parser.py` — Safe SKILL.md / YAML frontmatter parser with resource budgets (safe_load only)
- `skill_rules.py` — Skill Auditor rule implementations
- `owasp.py` — OWASP AST10 taxonomy + honest coverage matrix
- `bandit_runner.py` — Controlled subprocess adapter for PyCQA Bandit (Apache-2.0). No shell, fixed timeout, output-size cap, tmpdir staging + cleanup, JSON shape validation, pinned-version check.
- `bandit_adapter.py` — Bandit result -> Evidence/RuleMatch/Finding normalisation; Bandit severity/confidence/CWE preserved as controlled metadata; identity only from `(artifactPath, testId, lineNumber)`.
- `gitleaks_runner.py` — Controlled subprocess adapter for gitleaks (MIT, external binary, pinned 8.28.0). No shell, controlled env, JSON-file report, version + optional SHA-256 gate, tmpdir staging, user config confinement, all raw Secret / Match / Line values scrubbed at parse time.
- `gitleaks_adapter.py` — Redacted gitleaks results -> secret-sensitivity Evidence (§5.1 secret path). `redactedPreview = "[gitleaks:<ruleId>]"`; the raw secret never enters `occurrenceFingerprint`, subjectKey, JSON, HTML, SARIF or exceptions.
- `sarif.py` — SARIF 2.1.0 exporter with byte-offset regions, stable partialFingerprints, no secret leakage. Coverage and other Verity-specific fields live in the run's properties bag under flat, namespaced keys (`run.properties["verity.coverage"]`, `run.properties["verity.reviewId"]`, `run.properties["verity.verdict.subject"]`, etc.) — not as a nested `run.properties.coverage` object.
- `web/` — Local Web MVP (Starlette ASGI app). `python -m verity.web` binds `127.0.0.1` only. UI is Chinese-first, no external assets, no `innerHTML`, strict CSP. Every request routes into the same `run_review` pipeline.
- `semantic/` — Experimental, default-OFF semantic-review scaffold. Two-role Provider protocol (candidate generator + validator), strict output schemas, controlled subject taxonomy, egress gate + payload audit, budgets. The deterministic engine never imports this module.
- `intake.py` — Safe intake (text + local directory) with path escape / symlink / budget / NUL guards
- `review.py` — Orchestrator; `not_applicable` gate counts as OK for coverage.
- `baseline.py` — Cross-version five-state diff; resolution requires the relevant parser/analyzer/rule execution scope, not merely global coverage.
- `history.py` — Trusted Skill project registry and bounded immutable local history. Stores only an allowlisted safe projection in the gitignored `.verity-data/` directory using owner-only permissions, strict JSON and atomic writes.
- `report.py` — JSON + static HTML report with CSP, HTML escape, per-finding evidence block (dual-evidence traceable)
- `schema.py` — JSON Schema (Draft 2020-12) for the core objects
- `cli.py` — CLI entry point

## Web MVP for non-technical users

### 小白 3 步开始

1. 在 项目目录 `Verity/` 下打开 macOS 终端（或直接双击 `start-verity.command`）
2. 运行 `./start-verity.command`（或命令行 `python3 tools/start_local_web.py`）
3. 浏览器自动打开 `http://127.0.0.1:8765/`：可继续做 standalone Prompt/Skill 检查，也可在“Skill 项目与版本历史”中新建项目，从项目页选择文件夹并点击“检查新版本”，随后查看历史和五状态版本差异。

项目身份只由 Verity 注册表及当前项目上下文决定。Skill 名称、路径、digest、相似度及被审内容中的字段都不能选择或覆盖身份。项目历史保存在本机 gitignored `.verity-data/`；不保存原始文件内容、Secret、Provider payload/response、API key、RedactionMap 或宿主/临时/工具路径。项目版本审查默认使用含 gitleaks 的 `standard` profile；选择 `minimal` 属于用户明确降级。版本差异同时显示五状态总数和可展开的问题详情。用户可对发现添加处置标记（确认/接受风险/误报/不修复），纯建议性，不改变严重度或默认退出码；CLI 传 `--respect-dispositions` 时才让接受风险的高危问题不阻塞 CI。停止服务：在启动它的终端按 `Ctrl+C`。不会后台留守进程。

### 常见错误

| 现象 | 含义 | 处理 |
|---|---|---|
| `refusing to bind non-loopback host` | 你传了 `--host 0.0.0.0` 之类的非 loopback 地址 | 换回 `127.0.0.1` |
| `port 127.0.0.1:8765 is already in use` | 端口被其他进程占用 | 用 `--port` 换个端口，或自行关掉占用方（启动器不会 kill 其他进程） |
| `Missing dependency: starlette` 等 | 未按锻文件安装依赖 | `pip install -r requirements.lock` |
| `gitleaks: NOT available` 提示 | 希望含 Secret 扫描但 gitleaks 未安装 | 一次性执行 `python3 tools/install_gitleaks.py`（仅一次，启动器从不自动安装） |

### 命令行多种启动方式

```bash
# 1. 推荐：安全启动器（含预飞行检查 + 浏览器自动打开）
python3 tools/start_local_web.py
python3 tools/start_local_web.py --port 9000
python3 tools/start_local_web.py --no-browser        # 不自动打开浏览器
python3 tools/start_local_web.py --check-only        # 仅预飞行检查，不启动

# 2. 直接调用 uvicorn（不含预飞行检查，适合 CI 或已知环境）
python3 -m verity.web --port 8765
```

One command starts a local web page:

```bash
python3 -m verity.web              # binds 127.0.0.1:8765
python3 -m verity.web --port 9000  # different port
```

Open `http://127.0.0.1:8765/` in a browser. Two tabs:

- **检查 Prompt**: paste a user or system prompt, choose the type,
  click “开始审查”.
- **检查 Agent Skill**: pick a local folder (uses
  `<input type="file" webkitdirectory>`), pick `standard` (with
  gitleaks) or `minimal` (no secret scan; the UI explicitly warns).

The result page shows, in this order:

1. Plain-language headline verdict.
2. "建议处理顺序" — structured next steps (P0 findings first, then
   Coverage gap, then P1/P2).
3. Three summary cards (Coverage / 问题数量 / Secret 扫描).
4. Finding cards with plain-language title, `P0/P1/P2` badge,
   "为什么重要" paragraph, "建议怎么处理" numbered actions, and a
   folded "技术详情" block with the Rule id, OWASP mapping, byte
   ranges, and redacted preview.
5. "未完成的检查" list and analyzer status.
6. OWASP AST10 folded matrix and download links for the `report.json`,
   `report.html`, and `report.sarif` files.

Guidance is generated from a controlled catalog (`verity/guidance.py`)
keyed by Rule id / Bandit test_id / gitleaks rule id. Unknown ids get
a safe neutral fallback; no LLM is involved and guidance text never
enters Finding identity.

Security properties of the Web MVP:

- Binds `127.0.0.1` only. `python -m verity.web --host 0.0.0.0` is
  refused; there is intentionally no override flag in this round.
- `Host` and `Origin` headers must resolve to a loopback address.
- Strict CSP: `default-src 'none'; script-src 'self'; style-src 'self'`.
  No CDN, no external fonts, no `unsafe-eval`. Frontend uses only
  DOM APIs and `textContent`; there are no `innerHTML` assignments.
- All uploads go into a per-request temporary directory that is
  removed in a `finally` block. Path sanitiser rejects absolute paths,
  `..`, backslashes, NUL, drive letters, empty segments.
- Reports live in an in-process, size- and TTL-bounded LRU store with
  128-bit random IDs. Restarting the server invalidates every URL.
- No subprocess use inside the Web layer itself; skill execution
  never happens. The Skill Auditor's Bandit / gitleaks analyzers still
  run as subprocesses — those were audited in rounds 4 and 5.
- To stop the server, press `Ctrl+C` in the terminal that started it.

Exit codes / gate semantics are the same as the CLI; the UI headline
maps them into plain-language Chinese.

## Install / run (clean environment, reproducible)

Requires Python 3.9+ (tested on 3.9.6; supported through 3.13; declared `requires-python = ">=3.9,<3.14"`).

### Installing gitleaks (external binary, one command, not vendored)

gitleaks is a Go binary (MIT). Verity requires **exactly gitleaks 8.28.0**
under the `standard` skill-review profile. The binary is NOT committed
to this repository.

```bash
# One-time install into the project-local directory. The installer:
#   * downloads the official Release tarball from the URL pinned in
#     tools/gitleaks_release.json,
#   * verifies the archive SHA-256 against the pinned value,
#   * safely extracts only the `gitleaks` regular-file entry (no
#     symlinks, no absolute paths, no .. escapes, size-capped),
#   * computes the binary's own SHA-256 and writes it to a per-install
#     manifest at .tools/gitleaks/<version>/manifest.json.
python3 tools/install_gitleaks.py
```

The default install path is `<repo>/.tools/gitleaks/8.28.0/gitleaks`, which
is in `.gitignore`. Verity auto-discovers this location — you do not need
to modify your global `PATH`. To install elsewhere, pass `--target`.

**Two-layer SHA-256 policy** (why two hashes):

1. *archive SHA-256*: recorded in `tools/gitleaks_release.json`; this is
   the SHA published by the gitleaks project on their Release page. The
   installer enforces it before extraction.
2. *binary SHA-256*: computed at install time and stored in the install
   manifest. Every subsequent Verity run re-computes the binary hash on
   disk and rejects any drift. (The archive hash and the binary hash are
   different bytes; we do not re-download at runtime.)

Tool path resolution (only trusted sources are considered):

1. `VERITY_GITLEAKS_PATH` environment variable, if set.
2. Project-local install manifest under `.tools/gitleaks/<pinned>/`.
3. `gitleaks` on the system `PATH`.

Skill content is NEVER a source of the tool path or config.

If gitleaks is missing, mis-versioned, or its binary SHA-256 no longer
matches the install manifest, Verity marks the analyzer failed and
Coverage insufficient. It never silently falls back to a weaker
scanner and claims completion.

```bash
# Clean install using pinned locks
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip setuptools wheel  # older venvs ship an old pip
pip install -r requirements.lock       # runtime deps
pip install -r requirements-dev.lock   # dev/test deps
pip install -e .                       # install package in editable mode
pytest -q                              # run tests   (count in docs/PROGRESS.md top block)
```

Or without a venv (uses ``--user``):

```bash
python3 -m pip install --user -r requirements.lock -r requirements-dev.lock
PYTHONPATH=src python3 -m pytest -q
```

Both dependency locks are committed and their licenses are documented
in `THIRD_PARTY_LICENSES.md`. No network calls at runtime.

## Skill rule inventory (round 3)

All rules are deterministic, text/AST-level, and never execute the skill
under review. Rules that depend on `SKILL.md` metadata declare
`requiresManifest=True`; when the manifest parser fails, they are
recorded as `blocked_by_upstream_failure` in the ReviewPlan, never
silently absent.

| Rule ID | Severity | OWASP AST | Boundaries |
|---|---|---|---|
| `skill.missing_skill_md` | high | AST04 | Anchors the finding at an existing file (or a synthetic root location if the artifact is empty). |
| `skill.manifest_parse_failure` | high | AST04 | Emits one Finding per parser diagnostic: `frontmatter_not_closed`, `yaml_parse_error`, `yaml_root_not_mapping`, `yaml_too_deep`, `yaml_too_many_keys`, `frontmatter_over_budget`, `frontmatter_too_many_lines`, `frontmatter_alias_bomb_suspected`. |
| `skill.manifest_name_issue` | medium | AST04 | Agent Skills spec snapshot `retrieved-2026-07-21`: required string, 1–64 lowercase ASCII letters/digits/hyphens, no leading/trailing/consecutive hyphen, exact package-directory match. |
| `skill.manifest_description_missing` | medium | AST04 | Required non-empty string, maximum 1024 characters. No subjective "quality" judgement. |
| `skill.manifest_optional_field_issue` | medium | AST04 | Validates official optional `compatibility` (non-empty string ≤500), `metadata` (string→string mapping), and `allowed-tools` (non-empty space-separated string) field shapes. |
| `skill.manifest_missing_reference` | medium | AST04 | Local script/file referenced in `scripts`/`files`/`refs`/`entrypoints` does not exist. Suppressed when the suffix-mismatch rule already covers the case. |
| `skill.manifest_unsafe_reference_path` | high | AST04 | Reference is an absolute path, contains `..`, or uses back-slash separators. |
| `skill.manifest_unpinned_dependency` | medium | AST02 + AST07 | Only pinned versions like `1.2.3` or `==1.2.3` are accepted; ranges, `latest`, `*`, missing versions are flagged. |
| `skill.manifest_permission_wildcard` | high | AST03 | Only strict wildcard values in `permissions`/`allowed_tools`/`tools`: `*`, `/`, `**`, `.../*`. |
| `skill.manifest_external_instructions` | high | AST05 | Only when `external_instructions.mode ∈ {fetch_and_follow, runtime_fetch}`. Documentation-link URLs are NOT flagged. |
| `skill.manifest_script_suffix_mismatch` | medium | AST04 | Declared script `.py` but only `.js`/`.sh`/etc. present with same stem. |
| `skill.python_subprocess_shell_true` | high | AST01 | Python AST-level; keyword `shell=True` on any `subprocess.<x>` call. **Superseded at (file, line) by Bandit `B602` when Bandit ran successfully** (no double-report). Verity never executes the code. |
| `skill.bandit_finding` (rules `skill.bandit.<test_id>`) | varies | AST01/AST02/AST05 depending on test | 14 curated Bandit test_ids: B102 (exec) / B105/B106/B107 (hardcoded passwords) / B301 (pickle load) / B303 (weak hash) / B310 (unsafe urlopen) / B501 (TLS certificate verification disabled) / B506 (yaml.load unsafe) / B602 (subprocess shell=True) / B605 (os.system) / B607 (partial exec path) / B608 (SQL query string concatenation) / B701 (jinja2 autoescape). Bandit's `issue_text` never contributes to identity; Verity's severity is the policy value, not Bandit's raw severity. |
| `skill.gitleaks_finding` | high | AST02 | Secret detected by gitleaks 8.28.0 (external subprocess). The raw secret is redacted BEFORE the adapter sees it; identity = `(artifactPath, gitleaksRuleId, lineNumber)`. Only rendered when gitleaks completed; when gitleaks failed, Coverage is insufficient and the report says so. |
| `skill.fake_secret_fixture` (limited fallback) | high | AST02 | Detects only the synthetic `VERITY_FAKE_SECRET_*` fixture token used by Verity's own tests. **This is NOT a substitute for real secret scanning** — gitleaks provides that under `--profile standard`. |
| `skill.dangerous_shell_pattern` (legacy) | high | AST01 | Text-level pattern only; the shell is NOT executed. |
| `skill.sensitive_path_access` | high | AST06 | Text-level literal-path match only (SSH keys/AWS credentials/GnuPG/`.netrc`/Docker+Kube config/`/etc/passwd`+`/etc/shadow`/shell history/`.env`). Proves the path string is present, not that it is actually read/exfiltrated. |

Honest OWASP AST10 status (shown in every skill report as a matrix):

| OWASP | Status | Notes |
|---|---|---|
| AST01 malicious code / dangerous runtime | partial | Text patterns + selected Bandit Python AST checks. No taint/cross-language analysis or sandbox. |
| AST02 supply chain | partial | Unpinned dependency checks + pinned gitleaks secret detection. No vulnerability/provenance verification. |
| AST03 excessive authorisation | partial | Permission wildcard only. |
| AST04 insecure metadata | partial | Versioned Agent Skills field/shape validation, unsafe reference paths, suffix mismatch and parse failure. Broader body/reference semantics remain partial. |
| AST05 untrusted external instructions | partial | Strict-mode `fetch_and_follow` URLs only. |
| AST06 weak isolation | partial | Text-level detection of literal references to well-known sensitive host paths (SSH keys, cloud credentials, shell history, system password files). Cannot prove actual runtime access/exfiltration — that requires V2 sandbox observation, not yet implemented. |
| AST07 update drift / integrity | partial | Unpinned dep also maps here (versioning drift). |
| AST08 insufficient scanning | none | Meta-observation, requires product runtime not present in V1. |
| AST09 lack of governance | partial | Trusted history, coverage-aware diff and expiring dispositions exist; corpus-backed measurements and broader governance controls remain absent. |
| AST10 cross-platform reuse | none | Would require multi-runtime declaration matrix. |

We never claim `full` coverage. The report enumerates only `partial` and
`none` per category.

## Prompt rule inventory (round 2)

Prompt Auditor and Skill Auditor use **separate** rule registries. The
prompt registry now contains the following deterministic rules:

| Rule ID | Severity | Applicable prompt kinds | Boundaries |
|---|---|---|---|
| `prompt.instruction_override_marker` | low (risk signal, not a proven attack) | any | Excludes fenced/inline code; only well-known override phrases. |
| `prompt.unfilled_placeholder` | medium | any | Detects `{{...}}`, `${...}`, `<TODO ...>`/`<INSERT ...>`, `[INSERT ... HERE]`. Excludes fenced/inline code and legitimate JSON. |
| `prompt.system_hardcoded_secret` | high | `system_prompt` only | Uses the synthetic `VERITY_FAKE_SECRET_*` token in this phase. Later phases will delegate real secret detection to gitleaks. Redacted preview only; raw value never persisted. |
| `prompt.duplicate_numeric_assignment` | medium | any | Same key given two different numeric values on strict `key: N` or `key = N` lines. Dual-evidence: both assignment sites are cited. Identical repeats are not flagged. |
| `prompt.control_character` | medium | any | ASCII control characters (except \t, \n, \r) and Unicode bidi overrides (U+202A–U+202E, U+2066–U+2069). NUL is rejected at intake, not here. |
| `prompt.empty_or_whitespace` | medium | any | Empty or whitespace-only prompt content. |
| `prompt.open_ended_tool_wildcard` | high | `system_prompt` only | Only strict-form matches: `allowed_tools: *`, `permissions: ["*"]`, `tools: ["*"]`. Narrative star is not matched. |
| `prompt.untrusted_input_boundary_undeclared` | medium | `system_prompt` only | Declares acceptance of external/user-supplied content (English/Chinese phrase list) with no trust-boundary or anti-injection-override statement anywhere in the document. Literal phrase presence/absence only — cannot judge whether a present mitigation is actually effective. Maps to VR-PROMPT-008. |
| `prompt.dangling_section_reference` | medium | any | "see section N" / "见第N节" whose target number has no matching heading anywhere in the document. Only strict numbered-section forms are matched; free-form prose pointers ("see the rules above") are not. Maps to VR-PROMPT-010. |

Severity discipline (also visible in the HTML report):

- **low** = risk signal, context-dependent; may be a benign quotation.
- **medium** = quality / consistency issue with precise, mechanically verifiable evidence.
- **high / critical** = mechanically-provable policy violation.

`prompt.system_hardcoded_secret` and `prompt.open_ended_tool_wildcard`
are **system-only**. When a user prompt is scanned, they appear as
`not_applicable` executions in the ReviewPlan with a reason code
containing the required kind — they are never silently skipped.

## CLI demos

### Prompt demos

```bash
# 1. Clean user prompt — no findings expected
python3 -m verity.cli review --engine prompt --prompt-kind user_prompt \
  --input-file tests/fixtures/prompt_clean/prompt.txt --out /tmp/verity_out/clean

# 2. Broken user prompt — unfilled placeholders + duplicate numeric assignment
python3 -m verity.cli review --engine prompt --prompt-kind user_prompt \
  --input-file tests/fixtures/prompt_broken_user/prompt.txt --out /tmp/verity_out/broken

# 3. Risky system prompt — synthetic secret + wildcard tool authorisation
python3 -m verity.cli review --engine prompt --prompt-kind system_prompt \
  --input-file tests/fixtures/prompt_risky_system/system.txt --out /tmp/verity_out/risky

# Export the core JSON Schema
python3 -m verity.cli export-schema --out /tmp/verity_out/schema.json
```

### Skill project automation

Web 是普通用户主流程；CLI 提供同一 registry/history core 的最小自动化面：

```bash
python3 -m verity.cli project create --name "My Skill" --alias my-skill
python3 -m verity.cli project list
python3 -m verity.cli project review --project my-skill --input-dir ./skill --profile minimal
python3 -m verity.cli project diff --project my-skill
```

可用 `--data-dir` 指定可信数据目录；未知任意 artifact ID 不会被当作已注册项目。

### Skill demos

Every skill review also writes `report.sarif` (SARIF 2.1.0) next to
`report.json` / `report.html`.

### Skill review profiles

```bash
# standard (default): gitleaks required for secret coverage
python3 -m verity.cli review --engine skill --profile standard \
  --input-dir tests/fixtures/clean-skill --out /tmp/verity_out/std

# minimal: explicit opt-out; report says "not_requested_by_profile"
python3 -m verity.cli review --engine skill --profile minimal \
  --input-dir tests/fixtures/clean-skill --out /tmp/verity_out/min
```

### CLI exit codes and gate marker

Every `review` run prints a `gate=...` marker on stdout and returns one
of the following exit codes. **Coverage-insufficient runs never exit 0.**

| Exit | `gate=` marker | Meaning |
|---:|---|---|
| 0 | `pass` | Coverage sufficient AND no High/Critical findings. Medium/Low findings do NOT block by design; use downstream tooling for stricter gates. |
| 1 | `findings_block` | At least one High/Critical Finding is present. Wins over the coverage gate: if both are triggered the exit code is 1. |
| 3 | `coverage_block` | Coverage insufficient, or an explicitly requested semantic review did not complete, AND no High/Critical Finding. Chosen instead of 2 so it does not collide with argparse's usage-error exit 2. |
| 2 | (argparse) | Reserved by argparse for CLI usage errors (POSIX convention). |

Recorded exit codes with gitleaks 8.28.0 installed via
`tools/install_gitleaks.py` (the default developer setup):

| Fixture | profile | gitleaks status | coverage | gate | exit |
|---|---|---|---|---|---:|
| `clean-skill` | standard | completed (0 leaks) | sufficient | `pass` | 0 |
| synthetic leaky skill (`ghp_...`, `xoxb-...`) | standard | completed (3 leaks) | sufficient | `findings_block` | 1 |
| `clean-skill` | standard, `VERITY_GITLEAKS_PATH=/nonexistent` | not_installed | insufficient | `coverage_block` | 3 |
| `clean-skill` | minimal | not_requested_by_profile | sufficient | `pass` | 0 |
| `python_shell_true_skill` | standard | completed | sufficient | `findings_block` (Bandit high wins) | 1 |


```bash
# clean skill: 0 findings, coverage sufficient, exit 0
python3 -m verity.cli review --engine skill \
  --input-dir tests/fixtures/clean-skill --out /tmp/verity_out/clean-skill

# malformed manifest: file-level rules still run; manifest-dependent
# rules are blocked_by_upstream_failure; coverage insufficient;
# High-severity `skill.manifest_parse_failure` triggers `gate=findings_block`
# (exit 1). On a clean-manifest fixture without High findings the same
# missing-gitleaks condition would produce `gate=coverage_block` (exit 3).
python3 -m verity.cli review --engine skill \
  --input-dir tests/fixtures/malformed_manifest_skill \
  --out /tmp/verity_out/malformed_manifest

# missing refs / unsafe paths: precise reference issues
python3 -m verity.cli review --engine skill \
  --input-dir tests/fixtures/missing_refs_skill \
  --out /tmp/verity_out/missing_refs

# risky permissions + unpinned deps
python3 -m verity.cli review --engine skill \
  --input-dir tests/fixtures/risky_permissions_skill \
  --out /tmp/verity_out/risky_perms

# strict external_instructions mode
python3 -m verity.cli review --engine skill \
  --input-dir tests/fixtures/external_instructions_skill \
  --out /tmp/verity_out/external_instructions

# python AST: subprocess.run(..., shell=True)
python3 -m verity.cli review --engine skill \
  --input-dir tests/fixtures/python_shell_true_skill \
  --out /tmp/verity_out/python_shell_true
```

Recorded findings on the checked-in fixtures (see the exit-code section
below for `gate=` semantics; a `standard`-profile run on a machine where
gitleaks is not installed adds a `coverage_block` gate on top of the
findings gate, but a `findings_block` always wins in the exit code):

| Fixture | findings | high/critical |
|---|---:|---:|
| `clean-skill` | 0 | 0 |
| `malformed_manifest_skill` | 2 | 2 |
| `missing_refs_skill` | 5 | 3 |
| `risky_permissions_skill` | 5 | 2 |
| `external_instructions_skill` | 2 | 1 |
| `python_shell_true_skill` | 4 | 1 |

(These counts were re-verified against the current runtime in Round 36; the
table had already drifted from actual behaviour in earlier rounds — e.g.
`missing_refs_skill` was already 4/2 at commit `3e854ec`, not the documented
3/2, due to an undocumented `directory_mismatch` finding — independently of
Round 30's new `skill.sensitive_path_access` rule, which correctly adds one
more high finding because the fixture's manifest literally references
`/etc/passwd`.)

(`python_shell_true_skill`: Bandit B602 high + B607 medium x2. The hand-
written `subprocess shell=True` rule is suppressed on that (file, line).)

Each command writes `report.json`, `report.html` and `report.sarif` under
the target directory. Coverage-insufficient runs
show an explicit warning banner in the HTML report and refuse to say
"ready" / "low_detected_risk".

## Dependencies (locked)

Runtime (`requirements.lock`):

| Package | Version | License |
|---|---|---|
| jsonschema | 4.25.1 | MIT |
| PyYAML | 6.0.3 | MIT |
| bandit | 1.7.10 | Apache-2.0 |
| stevedore | 5.5.0 | Apache-2.0 |
| rich | 15.0.0 | MIT |
| markdown-it-py | 3.0.0 | MIT |
| mdurl | 0.1.2 | MIT |
| Pygments | 2.20.0 | BSD-2-Clause |
| jsonschema-specifications | 2025.9.1 | MIT |
| referencing | 0.36.2 | MIT |
| rpds-py | 0.27.1 | MIT |
| attrs | 26.1.0 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| starlette | 0.41.3 | BSD-3-Clause |
| python-multipart | 0.0.20 | Apache-2.0 |
| anyio | 4.12.1 | MIT |
| sniffio | 1.3.1 | MIT-0 / Apache-2.0 |
| uvicorn | 0.32.1 | BSD-3-Clause |
| click | 8.1.8 | BSD-3-Clause |
| h11 | 0.16.0 | MIT |

**Not** integrated (yet — spec constraint: integrate only with running tests):

- Semgrep (planned)
- YARA (planned)

Dev/test (`requirements-dev.lock`): pytest 8.4.2 (MIT) and its transitive
deps; tomli/exceptiongroup only on Python < 3.11.

Full attribution: `THIRD_PARTY_LICENSES.md`. Project itself: Apache-2.0
(`LICENSE`).

## Contract-level vs behavioural acceptance

Some acceptance items from spec §20 remain contract-level in this
phase (they define the taxonomy / shape but do not exercise a runtime
behaviour that only exists in later phases). Each such test is labelled
in its docstring. Behavioural coverage is expected to grow as later
phases land (bandit/semgrep/gitleaks integration, LLM egress, patch
apply, etc.).

## Semantic review (experimental, default OFF)

Round 8 introduces the plumbing for controlled LLM-assisted review that
never mutates deterministic results:

- Two Provider roles (`candidate_generator`, `validator`) with strict
  JSON schemas and controlled subject taxonomy.
- Data-egress policies:
  - `metadata_only` — send location + finding-type shape only, no snippet.
  - `redacted_evidence` — also include a short byte-range snippet from
    non-sensitive Evidence. `raw_full_artifact` is intentionally NOT
    implemented in this round.
  - `off` — default; also refuses to be paired with `enabled=True`.
- Verity re-derives `candidateId` from subject + evidence occurrence +
  snapshot id, so the provider cannot pin identity or downgrade a
  deterministic finding.
- Severity in confirmed semantic findings comes only from the semantic
  catalog's policy; the validator has no severity input.
- Every outbound request is size-capped and its digest goes into a
  payload-audit trail. Sensitive Evidence and RedactionMap NEVER reach
  a Provider payload.
- Deterministic findings are UNAFFECTED under every semantic anomaly
  (bad JSON, extra fields, id spoofing, prompt injection targeting the
  pipeline). This is asserted by tests.

CLI opt-in without Provider configuration remains an honest failed
semantic axis (`provider_not_configured`):

```bash
python3 -m verity.cli review --engine prompt --text "..." \
    --semantic --egress-policy metadata_only
```

To use the bounded JSON Provider, configure both roles explicitly. API
keys are read from named environment variables and must not be placed on
the command line:

```bash
export VERITY_GENERATOR_KEY='...'
export VERITY_VALIDATOR_KEY='...'
python3 -m verity.cli review --engine prompt --text "..." \
  --semantic --egress-policy redacted_evidence \
  --semantic-generator-url https://trusted-provider.example \
  --semantic-generator-model generator-model \
  --semantic-generator-api-key-env VERITY_GENERATOR_KEY \
  --semantic-validator-url https://trusted-provider.example \
  --semantic-validator-model validator-model \
  --semantic-validator-api-key-env VERITY_VALIDATOR_KEY
```

Provider wire contract:

- `POST <base_url>/v1/verity/candidate-generator`
- `POST <base_url>/v1/verity/validator`
- body: `{ "model": "...", "role": "...", "input": { ... } }`
- response: the strict candidate-list or validation-result JSON object.

Remote URLs must be HTTPS; loopback HTTP is allowed for a trusted local
Provider. Redirects, streaming, retries, arbitrary headers, tool calls,
and raw full-artifact egress are not supported. Request/response sizes
and timeouts are bounded. Provider error bodies are discarded rather
than copied into reports.

The local Web UI now has a loopback-only Provider configuration surface for
the **experimental** semantic path: paste an OpenAI-compatible base URL
(default OpenRouter) + API key, list models, and pick generator/validator
models. The key is held only in a random, transient environment variable and
cleared after the review; it never enters config serialization, reports,
SARIF, the payload audit, logs, or responses. Enabling semantic without
Provider fields still returns `provider_not_configured`. Semantic results are
advisory and EXPERIMENTAL (they have not passed the frozen protocol quality
gate); the deterministic outcome, coverage, gate and score are unchanged.

## Known limitations

- No ZIP / GitHub URL intake yet.
- Only Python has an AST-level scanner (Bandit + one hand-picked rule);
  other languages (Shell/JS/TS/Ruby/Go) are still text-level only.
- The Web UI configures the experimental semantic path via an OpenAI-
  compatible adapter (e.g. OpenRouter `/chat/completions`); the bounded
  JSON-over-HTTPS `JsonHttpProvider` uses Verity's explicit contract and is
  not a claim of universal vendor-SDK compatibility. Semantic quality is
  experimental and below its frozen Selection gate.
- No PatchSet apply — proposal shape only.
- Semgrep / YARA are not integrated. Real secret scanning is provided by
  pinned gitleaks under the `standard` Skill profile; the synthetic-token
  rule is only a limited deterministic fallback.
- SARIF is produced and repository CI runs `verify_repo.py`; automatic
  upload to GitHub Code Scanning is still the user's responsibility.
- V1.5 Prompt black-box evaluation and V2 Skill sandbox execution remain
  explicitly not implemented.

### verdict.subject on insufficient coverage

When coverage is insufficient the JSON, HTML and SARIF reports all
emit `verdict.subject = null` (SARIF: `run.properties.verity.verdict.subject`
is `null`). This is intentional: Verity refuses to say "ready" /
"low_detected_risk" when it does not know whether the required checks
were actually completed. Consumers must handle `subject == null`
safely; the checked-in HTML template shows a *COVERAGE INSUFFICIENT*
banner in that case.

## License

Apache License 2.0 — see [`LICENSE`](./LICENSE).
