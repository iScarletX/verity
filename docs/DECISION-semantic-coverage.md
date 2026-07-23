# Decision Needed: How to cover the remaining "semantic" findings

Status: **OPEN — awaiting owner decision**
Last updated: 2026-07-23 (after Round 51, commit `bc51895`, CI #48 green)
Owner of decision: repo owner (Lei Sun / iScarletX)

---

## 1. Context — where we are

Verity today is a **deterministic, offline, dependency-light** static +
semantic auditor for LLM prompts and Agent Skills. Its clean architecture
is a deliberate feature:

- pure Python, no ML stack
- no network at analysis time
- deterministic (same input → same output), so results are reproducible
  and CI can gate on them
- small dependency surface

We benchmarked Verity finding-by-finding against a Butler audit report on
a real system prompt (the "NexPlay SP"), which produced **10 findings**
(5 major + 5 minor). Current parity:

| Butler finding | Verity now | How |
|---|---|---|
| #1 topic splice (image-style head glued onto agent SP) | ✅ detected | `prompt.topic_splice` — deterministic, **zero dependency** (Round 51) |
| #2 missing prompt-injection defense declaration | ✅ detected | `prompt.untrusted_input_boundary_undeclared` (Round 49) |
| #4 named dangling reference (见回复规则…) | ✅ detected | `prompt.named_dangling_reference` (Round 50) |
| minor #2 duplicate content line | ✅ detected | `prompt.duplicate_content_line` (Round 50) |
| minor #4 full-width / half-width mixing | ✅ detected | `prompt.fullwidth_mixed` (Round 50) |
| minor #1 version-naming inconsistency | ✅ detected | `prompt.version_naming_inconsistent` — deterministic (Round 52) |
| minor #5 model endpoint has no fallback | ✅ detected | `prompt.model_endpoint_no_fallback` — deterministic structural-absence (Round 52) |
| #3 task complexity exceeds token budget | ❌ not yet | needs judgment (see below) |
| #5 vague "主动工作" role/authority boundary | ❌ not yet | needs judgment (see below) |
| minor #3 missing edge-case / error strategy | ❌ not yet | needs judgment (see below) |

**Score: 7 / 10 detected, 3 remain genuinely judgment-level.**

> **Round 52 update.** The 2 previously-"portable" items are now built and
> shipped, reaching the ~7/10 target of Option A. Separately, Round 52 fixed
> a real "detects nothing" bug the owner reported: the VR-PROMPT-008
> input-boundary rule only matched EXACT literal phrases and so returned 0
> findings on realistic support/RAG/email prompts; it was broadened to a
> multi-signal precision gate (still deterministic, dependency-free). The
> question below (Option B) is unchanged and still the blocked decision.

---

## 2. The core problem

The remaining 3 findings (#3 token budget vs task, #5 role-boundary
ambiguity, minor #3 edge-case strategy) are **quality / judgment**
findings, not pattern findings. A regex or heuristic cannot reliably
decide "this role definition is *vague*" or "this task *probably* exceeds
the budget" without understanding meaning.

### Important correction we made this session
We previously over-claimed that "any semantic finding REQUIRES an AI
model." **That was wrong** — Round 51 proved Butler #1 (topic splice) is
detectable **deterministically with zero dependencies** using a targeted
char-n-gram + cross-domain-vocabulary heuristic. So the boundary is:

- **Concrete, shape-specific "semantic" patterns** → often reachable with
  clever deterministic heuristics (like #1). Keep mining these.
- **Open-ended quality judgment** (#3/#5/minor#3) → genuinely need a model.

### How mature OSS actually does the judgment ones
We audited 8 projects (garak, llm-guard, rebuff, PyRIT, promptfoo,
NeMo-Guardrails, vigil-llm, guardrails-ai). For topic-coherence /
relevance / ban-topics / gibberish they **do not use deterministic
rules** — they **load specialist neural models**, e.g.:

- `BAAI/bge-*` sentence-embedding models (semantic similarity / relevance)
- `DeBERTa` / `RoBERTa` zero-shot classifiers (topic / ban-topics)
- dedicated gibberish / coherence classifiers

Crucially, these are **NOT** the same thing as Verity's abandoned
"generic-LLM-judge" semantic line (which asked a general chat model like
gpt-4o to free-form judge, and failed its protocol-v2 gate at 43% FP,
non-deterministic, paid, online). Specialist classifiers are:

- **deterministic** (temperature-free, same input → same output)
- **local / offline** (no API key, no per-call cost, no network)
- **benchmarkable** (public eval numbers exist)

i.e. they fit Verity's philosophy far better than the line we abandoned.

### The one real cost
Running specialist classifiers requires `torch` + `transformers` + model
weights (hundreds of MB to ~1 GB), which breaks the current
"pure-Python / no-ML / tiny-deps" architecture. Per `AGENTS.md`, an agent
must **not** auto-install heavy dependencies or download model weights on
the owner's machine without approval. **This is the decision that is
blocked on the owner.**

---

## 3. The three options

### Option A — Keep mining deterministic, dependency-free heuristics
**What:** Continue the Round 51 approach — find concrete, shape-specific
patterns and write targeted deterministic detectors (with strict
multi-signal precision gates so we never false-positive on normal
prompts). Also finish the 2 cleanly-portable items (minor #1 version
naming, minor #5 endpoint fallback).

- ✅ Pros: zero new deps, offline, deterministic, CI stays green, no
  architecture change, no owner risk. Ship immediately.
- ❌ Cons: cannot reach open-ended judgment findings (#3/#5/minor#3).
  Diminishing returns — each new pattern is narrower.
- **Reaches:** ~7/10 of Butler (5 done + 2 portable). Not the last 3.
- **Owner approval needed:** none.

### Option B — Add an OPTIONAL local specialist-model layer (gitleaks-style)
**What:** Introduce `torch` + `transformers` + a small local classifier
(e.g. `BAAI/bge-small`, a zero-shot topic classifier) as an **optional
extra** (`pip install verity[semantic-local]`). If the extra isn't
installed, Verity degrades gracefully to today's deterministic behavior —
exactly how gitleaks treats optional integrations. Deterministic (temp=0),
offline after the one-time model download, benchmarkable.

- ✅ Pros: unlocks real topic-coherence, role-ambiguity classification,
  gibberish, etc. Keeps the core clean (opt-in). Deterministic + offline —
  keeps Verity's philosophy. This is the OSS-proven path.
- ❌ Cons: heavy optional dep (hundreds of MB), one-time model download,
  bigger test/CI matrix (a "with-model" job), model-versioning discipline
  needed for reproducibility. Real engineering.
- **Reaches:** most/all of the remaining 3, plus a whole new class of
  detectors portable from llm-guard.
- **Owner approval needed:** YES — adds heavy deps + downloads weights +
  changes architecture. This is the main blocked decision.

### Option C — Restart the semantic track, but with specialist models
**What:** The semantic track (protocol-v2) failed as `not_eligible` when
built on a generic LLM judge. Rebuild it on **specialist classifiers**
(the Option-B models) instead of a chat model, define a fresh protocol v3
with new blind splits, and re-run the eligibility gate.

- ✅ Pros: the only route for truly open-ended judgment (#5 role
  ambiguity, minor #3 edge-case strategy) that no single classifier
  covers; gives Verity a principled, gated semantic layer.
- ❌ Cons: largest effort. Needs a new protocol v3, fresh corpus splits,
  new eligibility evidence, and (if it uses local models) all of Option B
  on top. Highest risk of re-failing the gate.
- **Reaches:** potentially all 10, but with the most work and risk.
- **Owner approval needed:** YES — owner decision + protocol design +
  everything in Option B.

---

## 4. Recommendation

1. **Do Option A now** (no approval needed): finish minor #1 + minor #5,
   keep mining shape-specific deterministic heuristics. Gets Verity to
   ~7/10 with zero risk.
2. **Get an owner decision on Option B** — it is the highest-leverage,
   OSS-proven, philosophy-consistent step, and it is the thing currently
   *blocked* purely on "may I add heavy optional deps + download models?".
3. **Treat Option C as a later, larger bet** — only worth starting after
   B exists, since C's judgment findings need B's models anyway.

**The single question for the owner:** *Do we allow an optional,
gitleaks-style, degradable local-model layer (torch/transformers + local
weights) so Verity can do deterministic offline semantic classification?*
Yes → start Option B. No → we cap at Option A (~7/10) and document the
remaining 3 as explicitly out of scope.

---

## 5. Guardrails that must hold regardless of option

- The **deterministic core must never import LLM/network libs**
  (openai/anthropic/requests/httpx/urllib.request/socket). Any model layer
  is a separate, optional module.
- No secrets/keys in the repo, ever.
- Honest evidence only: no fabricated `independent_ai_review`; new corpus
  cases use `provisional_single_review`.
- Every change: `python3 -m pytest` + `python3 tools/verify_repo.py`
  green, commit, push, confirm GitHub CI green.
- Append-only `docs/PROGRESS.md` and `docs/LESSONS.md`; never edit
  historical round entries.
