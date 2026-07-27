# Persistent Provider And Maximum Scan Design

Date: 2026-07-27
Status: approved by the maintainer

## Goal

Keep the local Web Provider configuration across Verity restarts and source
updates, while removing the egress-policy and Skill-profile choices from the
ordinary UI. Reviews started from the Web workbench use the broadest current
controlled settings.

## User Experience

- The semantic panel keeps the explicit enable switch required by the phase
  gate.
- The Provider URL, generator model, and Validator model are restored when the
  page opens.
- The API Key input stays blank. The page shows whether a key is saved, but the
  server never returns the key value.
- A Save command stores the current configuration. A Clear command removes
  both the non-secret settings and the saved key.
- The data-egress selector is removed. Semantic review always uses
  `redacted_evidence`.
- Standalone Skill review and trusted-project version review always use the
  `standard` profile. The profile selectors and minimal-profile warning are
  removed.
- Existing trusted Skill projects and their bounded version histories continue
  to use the existing `.verity-data` history store.

## Storage Architecture

Create a focused Web-settings module with two stores:

1. `ProviderPreferenceStore` writes only the normalized Provider URL and two
   model ids to `.verity-data/web-provider.json`. It reuses the history
   directory's owner-only directory checks and atomic `0600` JSON writes.
2. `MacOSKeychainCredentialStore` stores one API Key in the macOS login
   keychain under a fixed Verity service/account identity. The key is supplied
   to the `security` command through stdin, never through argv. Reads return
   the key only to backend memory.

The Web app creates one combined `ProviderSettingsStore`. Tests inject a fake
credential store, so CI never writes to a real keychain. On a host without the
macOS `security` command, saving a key fails with a stable
`credential_store_unavailable` response rather than falling back to plaintext.

## Web API

- `GET /api/provider-settings` returns:
  `baseUrl`, `generatorModel`, `validatorModel`, and `keySaved`.
- `PUT /api/provider-settings` validates and saves the three non-secret
  fields. A non-empty `apiKey` replaces the keychain item; an empty value keeps
  an already-saved key.
- `DELETE /api/provider-settings` deletes both stores.
- `/api/models` and semantic review endpoints may use the saved configuration
  when the request omits Provider fields. Request-supplied values remain
  accepted for one-off compatibility but are treated as one complete
  configuration: a request-supplied URL never inherits the saved key. Changing
  the saved URL requires a new key. Neither path can weaken the fixed maximum
  settings.

No endpoint returns the API Key. No report, history record, log, exception,
payload audit, or Provider configuration contains it.

## Maximum Review Policy

The Web layer owns two constants:

- semantic egress: `redacted_evidence`
- Skill profile: `standard`

The server applies these constants regardless of stale browser fields. This is
an ordinary-workbench policy only; controlled CLI and evaluation tools retain
their explicit options.

## Errors

- Missing saved settings produce the existing honest
  `provider_not_configured` semantic state.
- A saved preference without a key produces `api_key_required`.
- A keychain failure produces a stable safe code and never reflects command
  output.
- Corrupt, symlinked, oversized, or incorrectly permissioned preference files
  are refused using the existing local-history safety rules.

## Verification

- Unit tests cover preference permissions, no-secret JSON, keychain stdin
  transport, safe failure, and delete behavior.
- Web integration tests cover save/load/clear, response redaction, saved
  settings used by model listing and semantic setup, and forced maximum
  settings.
- UI tests verify the two selectors are absent and saved configuration is
  restored.
- Full pytest, `tools/verify_repo.py`, and desktop/mobile browser walkthrough
  must pass.

## Non-Goals

- No browser `localStorage` or session storage.
- No plaintext API Key file.
- No automatic semantic opt-in.
- No new Prompt review-history subsystem.
- No changes to CLI/evaluation egress or profile controls.
