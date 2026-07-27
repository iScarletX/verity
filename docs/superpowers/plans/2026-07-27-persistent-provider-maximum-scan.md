# Persistent Provider And Maximum Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist Web Provider configuration securely and make the ordinary Web workbench always use the broadest current semantic and Skill scan settings.

**Architecture:** Add a focused settings store that separates non-secret owner-only JSON preferences from a macOS Keychain credential. Wire it through small loopback-only Web endpoints and make review routes enforce `redacted_evidence` plus `standard` server-side.

**Tech Stack:** Python 3.9, Starlette, macOS `security`, vanilla JavaScript, pytest.

## Global Constraints

- The API Key never enters files, argv, browser responses, reports, logs, or history.
- CI uses injected credential-store doubles and never accesses the real keychain.
- The Web UI retains explicit semantic opt-in.
- CLI and evaluation options remain unchanged.
- Existing dirty-worktree changes are preserved.

---

### Task 1: Secure Provider Settings Store

**Files:**
- Create: `src/verity/web/provider_settings.py`
- Test: `tests/test_web_provider_settings.py`

**Interfaces:**
- Produces: `ProviderPreferences`, `ProviderPreferenceStore`,
  `MacOSKeychainCredentialStore`, and `ProviderSettingsStore`.

- [x] **Step 1: Write failing storage tests**

Cover normalized save/load, `0600` permissions, no key in JSON, key supplied to
`security ... -w` through stdin, missing key, and clear.

- [x] **Step 2: Verify RED**

Run: `python3 -m pytest -q tests/test_web_provider_settings.py`

Expected: collection fails because `verity.web.provider_settings` is absent.

- [x] **Step 3: Implement the minimal stores**

Use the existing history `_mkdir`, `_check_safe`, `_strict_load`, and
`_atomic_json` primitives for the preference file. Use bounded
`subprocess.run` calls with `shell=False`, captured output, timeout, and stable
errors for Keychain access.

- [x] **Step 4: Verify GREEN**

Run: `python3 -m pytest -q tests/test_web_provider_settings.py`

Expected: PASS.

### Task 2: Settings API And Maximum Server Policy

**Files:**
- Modify: `src/verity/web/app.py`
- Modify: `src/verity/web/provider_web.py`
- Test: `tests/test_web_provider_config.py`

**Interfaces:**
- Consumes: `ProviderSettingsStore`.
- Produces: `GET`, `PUT`, and `DELETE /api/provider-settings`; saved-settings
  fallback for `/api/models` and semantic reviews.

- [x] **Step 1: Write failing route tests**

Test save/load/clear, key redaction, invalid input, saved-key fallback, and
server-side enforcement of `redacted_evidence` and `standard`.

- [x] **Step 2: Verify RED**

Run: `python3 -m pytest -q tests/test_web_provider_config.py`

Expected: new route tests fail with 404 or old policy values.

- [x] **Step 3: Implement routes and policy**

Create the settings store in `create_app`, add the three routes, resolve saved
values only from trusted app state, and pass fixed maximum constants into the
review pipeline.

- [x] **Step 4: Verify GREEN**

Run: `python3 -m pytest -q tests/test_web_provider_config.py`

Expected: PASS.

### Task 3: Simplified Persistent UI

**Files:**
- Modify: `src/verity/web/static/index.html`
- Modify: `src/verity/web/static/app.js`
- Modify: `src/verity/web/static/app.css`
- Test: `tests/test_web_mvp.py`
- Test: `tests/test_round12.py`

**Interfaces:**
- Consumes: `/api/provider-settings`.
- Produces: save/clear controls and automatic safe preference restoration.

- [x] **Step 1: Write failing Web behavior tests**

Assert that the rendered page omits egress/profile selectors, restores
preferences through the settings endpoint, and submits broad server defaults.

- [x] **Step 2: Verify RED**

Run: `python3 -m pytest -q tests/test_web_mvp.py tests/test_round12.py`

Expected: selectors are still present and the settings flow is absent.

- [x] **Step 3: Implement the UI**

Remove the two option groups, add Save/Clear commands and status text, restore
URL/model ids on load, keep the password field blank, and never store
credentials in browser storage.

- [x] **Step 4: Verify GREEN**

Run: `python3 -m pytest -q tests/test_web_mvp.py tests/test_round12.py`

Expected: PASS.

### Task 4: Documentation And Final Verification

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/PROGRESS.md`
- Modify: `docs/LESSONS.md`
- Modify: `plans/ACTIVE.md`

**Interfaces:**
- Consumes: completed behavior and measured test count.
- Produces: truthful Round 65 handoff.

- [x] **Step 1: Update user and architecture documentation**

Document Keychain persistence, fixed Web maximum settings, retained Skill
history, and the absence of plaintext/browser credential storage.

- [x] **Step 2: Run focused and full verification**

Run:

```text
python3 -m pytest -q
python3 tools/run_corpus.py --check
python3 tools/verify_repo.py
git diff --check
```

Expected: every command exits `0`.

- [x] **Step 3: Browser walkthrough**

Start a temporary loopback server, verify save/restore/clear without exposing
the key, confirm both selectors are absent, check desktop/mobile overflow and
console errors, then stop the server.
