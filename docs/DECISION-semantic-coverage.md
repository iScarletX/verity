# Decision Record: Butler reference coverage and the semantic boundary

Status: **RESOLVED FOR ROUND 54**
Last updated: 2026-07-23
Owner of decision: repo owner

## Decision

Butler is a read-only source of possible failure shapes, not a source of
labels, architecture or acceptance truth. Verity owns its risk definitions,
evidence requirements, precision boundaries and evaluation cases.

Round 54 adds bounded deterministic signals for the three Butler-shaped areas
that were previously listed as wholly missing. This closes the stale "7/10
and blocked on a local model" decision. It does **not** claim complete semantic
judgment or broad accuracy:

| Reference shape | Verity signal | Deliberate boundary |
|---|---|---|
| Output demand exceeds budget | `prompt.output_budget_conflict` | Only proves explicit item-count x explicit per-item minimum > explicit total maximum in the same unit. No estimated average or unit conversion. |
| Vague autonomous authority | `prompt.autonomy_without_approval` | Requires explicit autonomy plus a closed-list high-impact side effect and no approval boundary. Generic proactive assistance stays quiet. |
| Missing error/edge handling | `prompt.failure_strategy_missing` | Covers explicit external-call, retrieval and parsing operations with no supported failure strategy. It is not a general completeness judge. |

Round 54 also adds `prompt.output_format_conflict`, because mutually exclusive
top-level response contracts are a common real failure that should not wait
for a model.

## Why Butler is not the target

Butler's archived checks mix static estimates, generic LLM judgments and
large overlapping checklists. Its own reports contain model disagreement,
inapplicable checks and contradictory conclusions. Copying those outputs
would reproduce the problem the owner wanted Verity to leave behind.

The acceptance rule is therefore:

1. A Butler finding may start an investigation.
2. A Verity risk must be independently defined and mapped to authoritative
   controls.
3. A deterministic claim needs inspectable evidence plus positive and safe
   counterexamples.
4. Anything requiring inferred intent, likely model behavior or broad
   completeness remains semantic or dynamic and is reported as such.

## Local model decision

No `torch`, `transformers` or model weights were installed in Round 54.
An optional local specialist-model adapter remains a valid future project,
but it is no longer a blocker for the bounded static signals above and is
not required to enter V1.5 black-box evaluation.

If that adapter is pursued later, it must remain optional and isolated,
degrade gracefully when absent, pin model/version/digest, and receive its own
fresh benchmark. It must not be described as a repair for the consumed,
failed generic-LLM semantic Selection.

## What remains open

- Broad task-complexity-versus-budget judgment when lower bounds are implicit.
- General role clarity, authority graphs and subtle approval conditions.
- Per-operation edge-case completeness and whether a declared fallback is
  actually sufficient.
- Model behavior under truncation, malformed inputs, prompt injection and
  conflicting instructions.

The first three require stronger semantic evidence or dynamic measurement.
The fourth is the purpose of the owner-authorized V1.5 Prompt black-box phase.

## Guardrails

- The deterministic core does not import model or network libraries.
- Provider/model configuration comes only from trusted operator input, never
  from the reviewed prompt.
- The consumed protocol-v2 Selection is not retried or tuned against.
- The sealed semantic Test split remains unconsumed.
- Every new label remains provisional until its declared review process is
  completed.
- Full pytest, repository verification, commit, push and green CI remain the
  release conditions.
