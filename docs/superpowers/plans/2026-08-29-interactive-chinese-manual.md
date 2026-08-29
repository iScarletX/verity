# Interactive Chinese Manual Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing canonical Chinese explainer into a fact-checked, self-contained, interactive Verity manual that lets a non-technical owner understand, operate, explain, and troubleshoot the whole project, then publish it on the existing GitHub branch.

**Architecture:** Keep `docs/verity-manual-zh.html` as the single Chinese manual rather than creating a competing document. Preserve its existing rule/risk inventories and release-contract tokens, but add task-oriented navigation, a global search index, real Prompt/Skill workflow explorers, trust-boundary diagrams, operation and report references, searchable FAQ/glossary, accessible interaction states, and explicit source citations. Correct current documentation drift in every founder-facing release document that asserts the same facts.

**Tech Stack:** Self-contained semantic HTML, CSS, and dependency-free browser JavaScript; Python `pytest` structural/contract tests; Node syntax checks; Codex in-app browser for real interaction, responsive, console, and accessibility smoke testing.

## Global Constraints

- Preserve the current release-contract statements required by `tools/verify_repo.py`.
- Do not add external assets, CDN requests, fonts, analytics, storage, service workers, or runtime `fetch()` calls.
- The manual must work from `file://`, a local static HTTP server, and a GitHub Pages subpath.
- Keep all reviewed-artifact, Provider-egress, sandbox, Harness, score, and accuracy claims consistent with runtime code and current SSOT files.
- The default static path must never be described as a universal safety proof; `completed` must never be equated with evaluated breadth.
- Do not expose secrets, real keys, private paths, raw Provider payloads, or host-specific data in examples or docs.
- All meaningful interactive controls must be keyboard operable, have visible focus, expose text status, respect reduced motion, and retain readable no-JavaScript core content.
- Use TDD for interaction/structure contracts: observe the new tests fail before changing production documentation.
- One implementation stream edits the manual; independent agents remain read-only reviewers.

---

### Task 1: Lock the manual contract with failing tests

**Files:**
- Create: `tests/test_verity_manual_zh.py`
- Inspect: `docs/verity-manual-zh.html`

- [ ] Add an HTML parser test that asserts `lang=zh-Hans`, skip links, one `<main>`, landmark navigation, a progress bar, unique IDs, and valid internal anchors.
- [ ] Assert the manual exposes the canonical sections: quick start, capability truth matrix, workflow, architecture/trust boundary, Web/CLI operations, report reference, history/dispositions, troubleshooting, FAQ, glossary, and sources.
- [ ] Assert at least 60 static `<details>` FAQ entries and at least 20 static glossary entries exist without JavaScript.
- [ ] Assert no external resource requests or browser persistence APIs are present and all inline scripts pass `node --check`.
- [ ] Assert global search, role/path filters, Prompt/Skill workflow toggles, command-copy controls, live regions, and FAQ deep links have explicit DOM contracts.
- [ ] Run `python3 -m pytest -q tests/test_verity_manual_zh.py` and record the expected RED failure against the old manual.

### Task 2: Correct cross-document factual drift

**Files:**
- Modify: `README.md`
- Modify: `docs/project-explainer.html`
- Modify: `docs/verity-manual-zh.html`
- Modify: `plans/ACTIVE.md`

- [ ] Add a visible Chinese-manual entry and local-open command to the README without claiming GitHub Pages is deployed.
- [ ] Correct ZIP intake wording everywhere: Web supports one guarded Skill ZIP; CLI Skill review remains directory-only; GitHub URL intake remains absent.
- [ ] Correct semantic status wording: explicit off is `not_enabled`; attempted without a complete Provider is `failed/provider_not_configured`, while an unconfigured Provider alone does not force a CI gate failure.
- [ ] Correct Skill sandbox wording: supported paths fail closed before the research runner is constructed; it is not an enable-able product feature.
- [ ] Replace nonexistent `tests/test_architecture.py` citations with the actual architecture test location.
- [ ] Record the authorized manual round in `plans/ACTIVE.md` without erasing prior implementation history.
- [ ] Run the focused release-document contract tests and confirm GREEN.

### Task 3: Rebuild the manual as a task-oriented interactive handbook

**Files:**
- Modify: `docs/verity-manual-zh.html`

- [ ] Redesign the top shell with skip links, compact desktop/mobile navigation, global search, reading progress, theme control, and four first-screen paths: understand, review Prompt, review Skill, answer a question.
- [ ] Add a plain-language 30-second product definition and a five-capability truth ledger that distinguishes readiness, default state, Provider need, egress, execution, Web/CLI availability, proof, and non-proof.
- [ ] Add a five-minute quick start from installation through first report, including guarded Skill ZIP, stop instructions, and copyable commands with clipboard fallback.
- [ ] Add a decision assistant for local static Prompt, semantic + black-box Prompt, standard Skill, Agent Harness, and unavailable executable-Skill sandbox scenarios.
- [ ] Replace the stale nine-step diagram with the current end-to-end sequence: intake, snapshot, deterministic plan/execution, static coverage, behavior profile, dynamic plan, semantic, black-box/sandbox/Harness branches, score/confidence/remediation, unified issues, verdict/gate/consumers.
- [ ] Add Prompt and Skill workflow toggles; every step must expose input, action, output, failure state, data boundary, and source path.
- [ ] Add an architecture and trust-boundary explorer that makes local original content, redacted semantic egress, explicit raw Prompt egress, Harness egress, and prohibited Skill execution visibly distinct in text as well as color.
- [ ] Add current Web Evidence Console and CLI operation chapters, including Provider setup, projects/history, five-state diffs, dispositions, downloads, and exit codes.
- [ ] Add an interactive report-field dictionary covering the actual top-level report projection and explain score, confidence, coverage, verdict, capabilities, dynamic plan, and unified issues separately.
- [ ] Add data/privacy matrix, troubleshooting guide, and version/history section tied to current SSOT rather than claiming old rounds are “this round.”
- [ ] Add at least 60 verified Chinese FAQ entries and at least 20 glossary entries, each statically readable and searchable; include the user’s original Harness/Agent questions.
- [ ] Preserve and integrate the existing rules, semantic types, risk inventory, evaluation evidence, and source appendix without duplicating a second data catalog.

### Task 4: Implement accessible interaction behavior

**Files:**
- Modify: `docs/verity-manual-zh.html`
- Test: `tests/test_verity_manual_zh.py`

- [ ] Implement in-page global search across chapters, FAQ, glossary, commands, risks, and source paths with grouped results and an `aria-live` count.
- [ ] Support `/` and `Ctrl/Cmd+K` search focus, Escape close/clear, URL hash deep links, and search-empty suggestions without intercepting text inputs.
- [ ] Implement scenario/path selection and Prompt/Skill workflow selection with `aria-pressed`/`aria-current` state.
- [ ] Implement robust copy buttons with Clipboard API and local-file fallback plus a live success/failure status.
- [ ] Add scrollspy `aria-current`, progressbar updates, back-to-top, mobile navigation focus management, modal focus trapping/restoration, and background inert handling.
- [ ] Add `prefers-reduced-motion`, high-contrast state colors, 44px mobile controls, print rules, and no-page-level horizontal overflow.
- [ ] Keep primary content available without JavaScript; use `<noscript>` only to describe unavailable enhancements.
- [ ] Run the focused manual tests and Node syntax checks until GREEN.

### Task 5: Browser and adversarial content verification

**Files:**
- Modify as needed: `docs/verity-manual-zh.html`

- [ ] Serve `docs/` on a loopback port and open the manual with the in-app browser.
- [ ] Test global search hits for “Agent”, “黑盒”, “ZIP”, “score”, source paths, and a no-result query.
- [ ] Exercise role/path selection, Prompt/Skill workflows, architecture nodes, FAQ deep links, glossary, theme, copy fallback, modal keyboard behavior, and back-to-top.
- [ ] Inspect desktop 1440×900, tablet 768×1024, and mobile 390×844; confirm no page-level horizontal overflow and usable 44px controls.
- [ ] Check console errors and confirm no external resource requests or persistence writes.
- [ ] Run an adversarial review for ambiguous capability claims, stale counts/rounds, false sandbox safety, Provider-egress omissions, and statements that could be mistaken for accuracy guarantees.
- [ ] Fix all P0/P1 review findings and re-run the focused verification.

### Task 6: Repository integration, full verification, and GitHub publication

**Files:**
- Modify: `docs/PROGRESS.md`
- Modify as required: `plans/ACTIVE.md`
- Verify: repository-wide

- [ ] Update the PROGRESS top verified block only from fresh measurements and append a new round entry without rewriting history.
- [ ] Mark the manual round complete in the active plan, preserving next technical work.
- [ ] Run `python3 -m pytest -q` outside the restricted tool sandbox and read the complete result.
- [ ] Run `python3 tools/verify_repo.py` and then the clean-mode gate at the correct staging point.
- [ ] Request an independent final code/content review and address all material findings.
- [ ] Inspect `git diff --check`, `git status`, and the final diff for secrets, private paths, accidental generated files, and scope drift.
- [ ] Commit with a change-log-style message and push `codex/artifact-aware-dynamic` to `origin`.
- [ ] Confirm the pushed commit exists remotely and wait for the corresponding GitHub Actions run to finish green before reporting completion.
