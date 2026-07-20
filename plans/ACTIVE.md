# Round 11 — First controlled real semantic Provider

## Why this is next

Verity's intended product path is not limited to static files. Its
approved layers are L0 deterministic static review, L1 controlled
semantic review, V1.5 Prompt black-box evaluation, and V2 isolated
Skill execution. At the start of this round L0 was usable and the L1
containment pipeline existed, but no real Provider transport existed,
so users could not use L1 outside the in-memory test suite.

This round closes that specific gap without crossing into black-box
Prompt execution, Skill execution, or Agent runtime interception.

## Goals

- Add the first real HTTPS JSON Provider adapter behind the existing
  separate Candidate Generator and Validator protocols.
- Keep the two roles as distinct Provider instances with distinct
  trusted configuration, even if a user chooses the same remote model.
- Resolve credentials only from explicitly named environment variables;
  never serialize or report the value.
- Enforce trusted endpoint validation, no redirects, bounded request and
  response sizes, timeouts, strict JSON decoding, and controlled error
  reason codes.
- Wire trusted Provider configuration into the CLI without allowing the
  reviewed artifact to influence endpoint, model, credentials, role,
  egress policy, or budgets.
- Preserve the deterministic path byte-for-byte under every Provider
  failure mode.
- Correct front-page documentation that still describes already shipped
  gitleaks / semantic scaffolding as absent.

## Non-goals

- Prompt black-box execution (V1.5).
- Skill execution or sandboxing (V2).
- Agent runtime step interception; this remains a later product layer
  built on explicit event/tool gateways.
- Raw full-artifact egress.
- Automatic endpoint discovery, artifact-supplied Provider settings,
  arbitrary headers, redirects, retries, tools/function calling, or
  streaming responses.
- Provider-specific SDK dependencies when the Python standard library is
  sufficient for the bounded JSON transport.
- Web entry of API keys or arbitrary Provider URLs in this round. The
  local Web UI may use only trusted process configuration after CLI
  behavior and tests are proven.

## Plan

1. Add transport-level tests first: HTTPS-only/loopback policy, role
   separation, environment-only credential resolution, redirect refusal,
   request/response caps, timeout/network/HTTP/JSON/schema failures, and
   no secret/path leakage in reports or exceptions.
2. Implement a small bounded HTTPS JSON Provider adapter using the
   existing `ProviderCall` / `ProviderResponse` protocols.
3. Add a trusted configuration loader and CLI flags/env references. Do
   not place secret values in argparse values, dataclasses, reports, or
   logs.
4. Exercise the complete Candidate → Validator → Finding path with a
   local fake HTTP server in tests; no external network is required by
   the test suite.
5. Update README, architecture, progress, and any newly discovered
   pitfall entry.

## Acceptance

- Existing 288 tests remain green and new tests cover behavior that
  would fail before this round.
- Default runs perform zero Provider calls.
- `--semantic` without complete trusted Provider config continues to
  report `provider_not_configured`; it must not silently downgrade to a
  static-only success.
- Candidate Generator and Validator use separate objects/config records.
- API-key values do not appear in Review inputs, Provider config
  serialization, stdout/stderr, JSON, HTML, SARIF, or payload audit.
- Remote redirects are refused. Requests and responses are hard-capped.
- Invalid JSON, oversized output, HTTP error, timeout, TLS/network error,
  and schema violation are visible as semantic failures while
  deterministic Findings remain unchanged.
- `python3 -m pytest` passes.
- After the round commit, `python3 tools/verify_repo.py --require-clean`
  passes and GitHub CI is green.

## Stage gate

This round may contact an external Provider only after an explicit
`--semantic` opt-in, a non-`off` egress policy, complete trusted config,
and all egress/schema/budget controls. No reviewed content may configure
or weaken those controls.

## Risks

- Generic Provider APIs are not perfectly uniform. The first adapter will
  support one explicitly documented JSON contract instead of pretending
  every OpenAI-compatible endpoint behaves identically.
- Redirects can bypass endpoint trust decisions. They are disabled.
- Error bodies can contain reflected sensitive content. They are not
  persisted or surfaced verbatim.
- A real network integration can make tests flaky. Acceptance uses a
  local deterministic fake server; no public Provider is called in CI.

## Status

- Started: 2026-07-20
- Implementation complete: 2026-07-20
- Ended: pending commit + GitHub CI
- Commit(s): pending
