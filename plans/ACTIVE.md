# Active implementation: artifact-aware dynamic audit + Agent Harness + Evidence Console

Status: **implementation and owner-authorized documentation round complete;
release gate passed; publication in progress**. The controller measured `4163/4163`
passing tests after adding the interactive Chinese manual contracts. The
combined dynamic, Harness, and Evidence Console release and this documentation
round both passed all 19 normal `python3 tools/verify_repo.py` checks.

Owner authorization: 2026-08-10

## Goal

Make dynamic Prompt review exercise the reviewed artifact's own declared task
inside a controlled Provider evaluation, instead of applying every generic
attack to every artifact. Static, semantic, and black-box evidence must be
reported together without erasing their independent identities. Keep Skill
execution unavailable until its isolation boundary is independently hardened.

Add a distinct, experimental Agent-instruction Harness for instruction-only
Skills without pretending that executable-Skill sandboxing covers them.

## Implemented scope

1. Deterministic, evidence-cited `ArtifactBehaviorProfile` extraction for
   runtime kind, domain, inputs, outputs, constraints, tools, state,
   side effects, sensitive data, and external content.
2. A versioned dynamic registry that records every check as `selected`,
   `not_applicable`, or `unavailable`, with reason codes and supporting facts.
3. `BlackboxConfig.scenario_policy`: default `artifact_aware`, historical
   research override `all`, and reproducible caller-selected `explicit`.
4. Five director/storyboard and four art-style text-contract scenarios using
   fixed templates and deterministic structure, numeric, and trace oracles.
5. Artifact-aware Skill environment planning and a dormant research signal
   registry. The former in-process `sandbox-exec` prototype is not a supported
   product capability because it does not yet provide a defensible host-read,
   output, disk, process-tree, or observer-integrity boundary.
6. Unified issue projection grouped by stable risk id while preserving every
   static, semantic, black-box, and sandbox occurrence.
7. Issue-first JSON, HTML, Web, and SARIF presentation, followed by dynamic
   coverage and then raw layer-specific details.
8. A CLI-only, caller-enabled, default-OFF `agentInstructionRuntime` stage for
   external `@deepseek-ai/dsh` 0.1.1-rc.2, with caller-supplied absolute Node
   and DSH entry paths plus SHA-256 pins. Each pinned entry is streamed once in
   bounded chunks into an owner-only private snapshot while the same bytes are
   hashed. Version and scenarios run only from those snapshots. The adjacent
   npm closure linked for module resolution remains unpinned and
   unauthenticated. At most two Skill-loader result markers are written;
   exactly one successful marker is required, otherwise parsing fails closed.
9. Four policy-mapped attempt signals over simulated in-memory read, blocked
   HTTP, blocked shell, and denied approval tools; no model-facing tool has a
   host side effect.
10. Disposable per-scenario processes/roots, bounded streams/traces, a clean
    allowlisted environment, process-group cleanup, and allowlisted report
    retention. No Web enable surface was added.
11. A responsive local Evidence Console with a compact intake rail, an honest
    pre-run audit plan, persistent processing/network boundary language,
    keyboard-operable tabs, and a findings/evidence-first result surface. This
    redesign changes presentation only; it does not broaden review authority
    or add a Web enable surface for the Agent Harness.

## Safety and honesty boundaries

- The default V1 path remains read-only. Prompt black-box remains an explicit,
  caller-controlled, off-by-default opt-in. Skill execution is unavailable on
  supported Review, CLI, Web, and standalone-command paths; an explicit request
  fails closed with `sandbox_isolation_hardening_required`.
- Reviewed content cannot set Provider, model, credentials, policy, fixture,
  severity, or sandbox controls.
- A bounded dynamic pass never deletes a static Finding. It may add only a
  `not_reproduced` observation for that exact run.
- Image fidelity is `unavailable:image_runtime_not_configured` until a real
  image Provider/evaluator adapter exists. Agent-instruction Harness simulation
  is unavailable by default and selected only with trusted complete CLI config.
- Harness sends Skill instructions/scenario prompts to a real configured
  Provider. It is not an OS/process/network sandbox; `setsid()` descendants,
  adjacent npm dependencies outside the two entry-file hashes, and unrestricted
  Provider egress remain explicit residuals. Strong use needs an outer
  container/microVM, destination allowlist, and fuller image/dependency pinning.
- No real Provider/model/scenario E2E ran in this round. A clean completion is
  not a universal pass, safety proof, cross-Agent claim, or accuracy result.
- Current detector breadth remains signal/partial and not evaluated. This work
  makes no accuracy, safety, or Butler-superiority claim.

## Verification state

The prior artifact-aware round's unit/integration/report, black-box, sandbox,
scoring, standards, Web, static, normal `verify_repo.py`, and full-suite gates
passed; its last measured total was `3777/3777`.

For the combined Harness and Evidence Console round, Tasks 1–5 focused gates,
offline DSH composition smoke, JavaScript syntax, compile, release-contract,
responsive Web checks, and scoped baseline audits passed without a real
Provider/model/scenario call. The controller's fresh full suite passed
(`4151/4151`, one existing deprecation warning). Agent-runtime cleanup also
passed all 120 focused tests plus repeated real-process stress runs. The final
normal repository verification passed all 19 checks before release staging.

## Next work after this round

1. Run a separately authorized real-Provider evaluation only inside outer
   container/microVM isolation with destination-allowlisted egress.
2. Pin the DSH/npm dependency closure or a complete runtime image, not only the
   two JavaScript entry files.
3. Rebuild Skill execution around separately enforced host-read, output, disk,
   process-count/tree, observer-integrity, and report-projection boundaries;
   keep all supported surfaces fail-closed until adversarial macOS tests prove
   those properties.
4. Design any Web enable surface as a separately reviewed security feature;
   the Agent Harness adapter stays CLI-only.
5. Build an image generation/evaluation adapter with fixed seeds and human
   review, rather than using text output as visual proof.
6. Expand domain profiles and oracles only with positive and safe
   counterexamples plus registry mappings.
7. Run the already-frozen semantic quality program independently; do not use
   this feature round as evidence for a superiority claim.

## Owner-authorized documentation round — 2026-08-29

Status: **implementation and final repository gate complete; publication in
progress**.

The owner requested one detailed, interactive, all-Chinese project manual and
explicitly requested that the completed result be pushed to GitHub. This round
upgrades the existing canonical `docs/verity-manual-zh.html` instead of adding
a competing explainer. It covers first use, current Prompt/Skill flows,
artifact-aware dynamic planning, report interpretation, trust boundaries,
project history, troubleshooting, searchable FAQ, glossary, and accessible
interaction behavior. It also corrects founder-facing ZIP, semantic-status,
Skill-sandbox, and source-citation drift. It does not broaden any runtime
capability or authorize Provider/model/Skill execution.
