# Verity Evidence Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status (2026-08-27): implemented and independently reviewed.** The checklist
below records the completed TDD and visual-verification sequence. V2 Skill
execution was subsequently made unavailable pending isolation hardening; the
Evidence Console now presents that boundary as fail-closed.

**Goal:** Replace the generic Web MVP styling with a premium, evidence-first local Agent-audit console while preserving every existing intake, Provider, review, and safety contract.

**Architecture:** Keep the loopback Starlette API and the existing DOM IDs/renderers intact. Restructure only the static HTML shell, layer a compact forensic-workbench design system over the existing result components, and add a small presentation-state controller for the initial, loading, error, and result surfaces. Verification combines string-level security/DOM contracts, existing browser-harness tests, and real in-app browser inspection at desktop and narrow widths.

**Tech Stack:** Static HTML5, CSS custom properties and responsive media queries, dependency-free ES5-compatible JavaScript, pytest, Node syntax checks, Codex in-app browser.

## Global Constraints

- No external fonts, images, scripts, CDNs, imports, or `http(s)` literals in Web static assets.
- Preserve strict CSP compatibility: no inline handlers, no `innerHTML`, and no new persistence surface.
- Preserve every existing element ID, request field, `profile=standard`, and `egress_policy=redacted_evidence`. Preserve the Prompt black-box two-signal authorization gate; keep the V2 sandbox surface disabled and fail-closed.
- Keep raw Provider keys outside normal review payloads and keep the current Keychain/transient-env behavior unchanged.
- Keep severity, evidence origin, coverage, and runtime state as separate visual meanings; never make color the only state signal.
- Retain local-only, read-only intake language and the current “not a safety guarantee” disclosure.
- Use only system fonts and existing project dependencies.
- Preserve unrelated dirty-worktree changes; do not stage, commit, reset, or overwrite them.

---

### Task 1: Lock the workbench and safety contracts

**Files:**
- Modify: `tests/test_web_mvp.py`
- Modify: `src/verity/web/static/index.html`
- Modify: `src/verity/web/static/app.css`
- Modify: `src/verity/web/static/app.js`

**Interfaces:**
- Consumes: `/` HTML plus `/static/app.css` and `/static/app.js` served by `verity.web.create_app`.
- Produces: stable `#review-empty`, labeled tab/panel relationships, custom local intake surfaces, and `setWorkspaceState(state)` with `idle | loading | error | result`.

- [x] **Step 1: Extend the existing static-asset test with failing shell assertions**

```python
html = client.get("/").text
assert 'id="review-empty"' in html
assert 'class="review-empty"' in html
assert 'id="tab-mode-prompt"' in html
assert 'aria-labelledby="tab-mode-prompt"' in html
assert 'id="skill-folder-drop"' in html
assert 'id="skill-zip-drop"' in html
assert 'id="prompt-file-drop"' in html

css = client.get("/static/app.css").text
assert "--chrome:" in css
assert ".review-empty" in css
assert ".intake-drop" in css

js = client.get("/static/app.js").text
assert "function setWorkspaceState" in js
assert '"ArrowRight"' in js
```

- [x] **Step 2: Run the focused test and confirm RED**

Run: `python3 -m pytest -q tests/test_web_mvp.py::TestIndexAndAssets`

Expected: failures for the missing evidence-console shell selectors and workspace-state function.

- [x] **Step 3: Add the semantic shell without renaming existing contracts**

Add a compact command bar, `#review-empty` audit-plan canvas, accessible `id`/`aria-labelledby` tab wiring, and labeled custom drop targets around the existing file inputs. Keep all existing API-bound input IDs and result containers exactly unchanged.

- [x] **Step 4: Implement the calm forensic design system**

Define local tokens headed by `--chrome`, `--canvas`, `--panel`, `--ink`, and `--brand`; restyle the command bar, input rail, drop targets, run authorization cards, empty audit plan, loading timeline, verdict, findings, evidence, and diagnostics. Use a light evidence canvas inside a restrained graphite shell, hairline borders, 8px spacing, system fonts, and no decorative asset downloads.

- [x] **Step 5: Add the minimal presentation-state and tab-keyboard controller**

Implement `setWorkspaceState(state)` so idle shows `#review-empty`, loading/result/error hide it, and wire ArrowLeft/ArrowRight/Home/End plus roving `tabindex` for the existing tabs. Do not alter request construction or review logic.

- [x] **Step 6: Run focused tests and syntax validation**

Run: `python3 -m pytest -q tests/test_web_mvp.py::TestIndexAndAssets`

Run: `node --check src/verity/web/static/app.js`

Expected: both commands exit 0.

---

### Task 2: Reduce disclosure noise and polish responsive behavior

**Files:**
- Modify: `src/verity/web/static/app.css`
- Modify: `src/verity/web/static/app.js`
- Modify: `tests/test_web_mvp.py`

**Interfaces:**
- Consumes: existing `renderResult`, `renderFindingCard`, `renderFullDocument`, `renderDiagnostics`, and source-location components.
- Produces: one-primary-disclosure-at-a-time density, coverage-aware diagnostics expansion, reduced-motion-safe scrolling, and layouts with no page-level horizontal overflow.

- [x] **Step 1: Add failing assertions for safety-relevant presentation behavior**

Add assertions to the existing Node browser harness that a blocked or unscored result opens diagnostics, that only the first finding evidence disclosure opens initially, and that `prefers-reduced-motion` avoids smooth scrolling.

- [x] **Step 2: Run the focused browser-harness scenarios and confirm RED**

Run: `python3 -m pytest -q tests/test_web_mvp.py -k 'browser or render or static_assets'`

Expected: failures identify the current all-open disclosure and smooth-scroll behavior.

- [x] **Step 3: Implement minimal disclosure and motion fixes**

Open only the first finding’s source evidence, keep full-document files collapsed initially, open diagnostics when semantic failure, blocked checks, or unavailable score requires attention, and route scrolling through a helper that selects `auto` under reduced motion.

- [x] **Step 4: Add responsive console rules**

At wide widths keep a 320px intake rail beside the evidence canvas; below 960px use one column without nested rail scrolling; below 640px use 44px controls, stacked drop targets, compact command metadata, and locally scrollable code/tables. Assert `document.documentElement.scrollWidth === window.innerWidth` during real-browser QA.

- [x] **Step 5: Run all Web tests**

Run: `python3 -m pytest -q tests/test_web*.py`

Expected: all tests pass without real Provider calls, sandbox execution, or host-network side effects.

---

### Task 3: Real-browser visual acceptance and independent review

**Files:**
- Modify only if verification exposes a defect: `src/verity/web/static/index.html`
- Modify only if verification exposes a defect: `src/verity/web/static/app.css`
- Modify only if verification exposes a defect: `src/verity/web/static/app.js`
- Modify only if verification exposes a missing contract: `tests/test_web_mvp.py`

**Interfaces:**
- Consumes: the already-running loopback page at `http://127.0.0.1:8765/`.
- Produces: visually verified Prompt and Skill intake states, Provider disclosure, idle/loading/error/result hierarchy, and an independent review verdict.

- [x] **Step 1: Reload and inspect the real local page**

Reload after each static-file edit, capture a fresh DOM snapshot and full-page screenshot, and confirm the page visibly presents the Evidence Console rather than the previous generic card stack.

- [x] **Step 2: Verify both intake modes without submitting untrusted content**

Exercise the Prompt/Skill tabs with pointer and keyboard, open and close Provider, black-box, sandbox, and project disclosures, and confirm their states and warnings remain legible. Do not enable or submit black-box/sandbox runs.

- [x] **Step 3: Verify responsive and accessibility essentials**

Inspect approximately 1280px, 802px, and 390px widths; confirm no document-level horizontal overflow, no clipped primary action, visible focus, meaningful non-color status text, reduced-motion support, and at least 44px touch targets at the narrow breakpoint.

- [x] **Step 4: Run fresh verification gates**

Run: `python3 -m pytest -q tests/test_web*.py`

Run: `node --check src/verity/web/static/app.js`

Run: `python3 -m pytest -q`

Run: `python3 tools/verify_repo.py`

Expected: all commands exit 0. If the repository count contract changes, update only the current `docs/PROGRESS.md` summary after the full suite produces the new measured count.

- [x] **Step 5: Request a read-only independent review**

Ask the reviewer to compare the final HTML/CSS/JS delta against security contracts, current Web tests, responsive screenshots, and the user’s request for a premium Agent-style interface. Resolve each actionable finding through a reproduced failure and minimal fix.

- [x] **Step 6: Open the finished local page for the user**

Open `http://127.0.0.1:8765/` in the Codex browser panel and include the final screenshot plus verification evidence in the handoff.
