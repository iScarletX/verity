# OSS detection-pattern mining notes — Round 2

This file is the persistent record the Round 46-48 mining pass was supposed
to leave behind and did not (its analysis lived only at
`/tmp/oss_audit/EXTRACTION.md` and was lost when the session ended). This
round mined a **different, non-overlapping** set of 5 authoritative
open-source LLM-security projects, cloned fresh into `/tmp/oss_audit_round2/`
(not committed — that directory is scratch space, this file is the durable
record). For each project: what was actually inspected (source code, not
README marketing copy), what was ported (with the resulting `ruleId`), and
what was deliberately NOT ported, with a specific reason.

Methodology follows the Round 46 precedent in `docs/LESSONS.md`
("Port authoritative OSS detection signatures instead of hand-rolling narrow
ones"): read the project's actual rules/regex/probe definitions, adapt
(never copy-paste) anything purely deterministic that Verity does not
already cover, attribute the source + license in the ported rule's
docstring/title, and back every port with unit tests + a corpus
positive/safe pair.

Before mining, the existing 23 `prompt_*` rules and 12 `skill_*` rules
(`src/verity/engine.py` + `src/verity/skill_rules.py`) were read in full so
that duplicate proposals could be ruled out up front.

## Summary of what was ported

| Source project | Pattern | ruleId | riskId |
|---|---|---|---|
| Microsoft PyRIT (`converter/token_smuggling/`) | Variation-selector + "sneaky bits" invisible-channel smuggling runs | extended `prompt.control_character` | VR-PROMPT-005 |
| Microsoft PyRIT (`score/true_false/regex/static_prompt_injection_scorer.py`) | System-prompt / hidden-instruction extraction request | new `prompt.system_prompt_extraction_request` | VR-PROMPT-001 |

2 patterns ported, both from PyRIT, both MIT-licensed (compatible, no
license concerns). 3 of the 5 projects (rebuff, promptfoo, guardrails-ai)
yielded nothing to port; NeMo-Guardrails yielded one candidate
(context-bloat detection) that was evaluated and explicitly rejected for a
documented false-positive reason (see its section below), plus several
other candidates rejected because they require a model/network dependency.

---

## 1. rebuff (protectai/rebuff) — MIT license

**What was inspected**: `python-sdk/rebuff/detect_pi_heuristics.py` (the
`generate_injection_keywords`/`detect_prompt_injection_using_heuristic_on_input`
heuristic scorer), the mirrored TypeScript `javascript-sdk/src/tactics/
Heuristic.ts`, `detect_pi_openai.py`, `detect_pi_vectorbase.py`, and
`sdk.py`'s canary-word leak-detection API.

**What was NOT ported, and why**:

- **The heuristic keyword generator itself (verb x adjective x preposition x
  object combinatorial phrase list)**: byte-for-byte identical in structure
  and near-identical in the actual verb/object word lists to what Verity
  **already ported in Round 46** into `prompt.instruction_override_marker`'s
  `_JAILBREAK_TERMS` grammar (vigil-llm InstructionBypass + garak). Mining
  rebuff's version added nothing new — reason: **already covered by
  existing rule** `prompt.instruction_override_marker`.
- **`detect_pi_openai.py`** (`call_openai_to_detect_pi`): sends the input to
  an OpenAI chat completion and asks it to score injection likelihood 0.0-1.0.
  Reason: **requires an LLM call** — explicitly out of scope for the
  deterministic engine (AGENTS.md phase gates).
- **`detect_pi_vectorbase.py`** (Pinecone embedding similarity search against
  known attack strings): reason: **requires a network call and an
  embedding model** — out of scope.
- **Canary-word leak detection** (`generate_canary_word`/`add_canary_word`/
  `is_canary_word_leaked` in `sdk.py`): this is a *black-box execution*
  technique — you embed a random token into the live prompt and then check
  whether it leaks into the model's *output*. It requires actually running
  the prompt against a model. Reason: **not a static-artifact check at
  all** — this is the V1.5 prompt-black-box roadmap item (AGENTS.md §4),
  not something a deterministic reviewer of the artifact text can do.

**Overall**: rebuff contributes essentially zero *new* deterministic surface
to a static reviewer; its value (heuristic pre-filter + LLM classifier +
vector similarity + live canary-leak check) is designed to run inline in a
serving pipeline, not to audit a prompt file offline.

## 2. Microsoft PyRIT (microsoft/PyRIT — note: the `Azure/PyRIT` GitHub path
is an archived redirect; the live repo is `microsoft/PyRIT`) — MIT license

**What was inspected**: `pyrit/converter/token_smuggling/*.py` (ASCII/tag,
variation-selector, and "sneaky bits" smugglers), `pyrit/converter/
ansi_escape/ansi_attack_converter.py`, `pyrit/score/true_false/regex/*.py`
(13 regex-based `TrueFalseScorer` subclasses: credential leak, markdown
injection, static prompt injection, SQL/XSS/XXE/SSTI/SSRF/shell/path-
traversal/open-redirect/LDAP-injection output scorers, plus 4 CBRN-keyword
scorers), and `pyrit/library/jailbreak_detection/heuristics/checks.py`.

**What was ported**:

1. **Invisible-channel smuggling runs** (extends `prompt.control_character`,
   `controlCategory: invisible_char`, VR-PROMPT-005). PyRIT's
   `VariationSelectorSmugglerConverter` hides an arbitrary UTF-8 byte
   payload using Unicode Variation Selectors (VS1-16, U+FE00-FE0F, and the
   VS supplement U+E0100-E01EF — one selector encodes one byte), and its
   `SneakyBitsSmugglerConverter` hides a byte payload using two invisible
   "math operator" characters (U+2062/U+2064) as a binary alphabet (8
   characters per byte). Verity's existing Round-47 `invisible_char`
   coverage (zero-width space/joiner, BOM, Unicode TAG block) did **not**
   cover either of these codepoint ranges — confirmed empirically before
   porting. The port is a RUN detector (>=4 consecutive channel characters)
   rather than a single-character match, specifically because a *single*
   variation selector on a visible emoji (e.g. "❤️" = U+2764 U+FE0F) is
   completely ordinary text; only a long run has real smuggling capacity.
   Verified with a negative test (`test_negative_single_variation_selector_
   on_emoji`).
2. **`prompt.system_prompt_extraction_request`** (new rule, VR-PROMPT-001).
   PyRIT's `StaticPromptInjectionScorer` includes a "System Prompt
   Extraction" / "Prompt Leaking" pattern pair that Verity had zero coverage
   for — a request that asks the model to *disclose* its system
   prompt/hidden instructions (OWASP LLM07 system-prompt-leakage), which is
   a different attack shape from `prompt.instruction_override_marker`
   (which detects requests to *override* the instruction hierarchy, not
   requests to *read* it). **Adapted, not copied**: PyRIT's own docstring
   admits its version "favors recall over precision and has a known high
   false-positive rate" because its verb/object gaps (`.{0,40}`) are
   independent and can span unrelated clauses ("I had to ignore the spam
   folder. Previous emails contained setup instructions." false-positives
   on PyRIT's pattern). Verity's rewrite requires **verb-object adjacency**
   (a request verb directly governing a system/hidden-prompt noun within a
   single bounded span) rather than two independently-gapped clauses, and
   adds a **negation-lookback guard** so the prompt's own defensive
   instruction ("do not reveal your system prompt") is not mis-flagged as
   an attack — the same precision discipline Round 49 established for
   `prompt.instruction_override_marker`. Verified against 6 positive
   phrasings, 3 unrelated-request negatives, and 4 defensive-phrasing
   negatives before committing to the regex.

**What was NOT ported, and why**:

- **13 `RegexScorer` subclasses in `score/true_false/regex/`** (credential
  leak, markdown injection, SQL/XSS/XXE/SSTI/SSRF/shell/path-traversal/
  open-redirect/LDAP-injection scorers, CBRN keyword scorers): these are all
  designed to grade a **model's output** for red-team purposes (e.g. "did
  the model's response contain a working SQL injection payload"), not to
  find a vulnerability *in the reviewed artifact's own text*. Reason:
  **wrong target — output-scanning, not artifact-scanning** (Verity reviews
  the prompt/skill file itself, not a model's live response to it).
  - Partial exception: `MarkdownInjectionScorer`'s markdown-image/exfil-link
    patterns are structurally the same shape as Verity's existing
    `prompt.markdown_data_exfiltration` (Round 46, from vigil-llm) — reason:
    **already covered by existing rule**.
  - Partial exception: `CredentialLeakScorer`'s AWS/GitHub/Slack/JWT regex
    set overlaps with what gitleaks (Verity's controlled external adapter,
    see AGENTS.md §0) already covers at higher fidelity — reason: **already
    covered, and by a more authoritative tool** (gitleaks is purpose-built
    and actively maintained for this exact problem; duplicating a narrower
    hand-rolled subset would be a regression per the Round 46 lesson about
    not reinventing what a mature tool already does better).
- **`ansi_attack_converter.py`** (ANSI/VT100 escape-sequence attacks, from
  garak's `ansiescape` probe): Verity's existing `prompt.control_character`
  already flags the raw ESC byte (`\x1b`) as a `control_char` — confirmed
  empirically (`\x1b[32mTHIS IS GREEN\x1b[0m\x07` already produces 3
  findings under the existing rule). Reason: **already covered by existing
  rule**.
- **`library/jailbreak_detection/heuristics/checks.py`** (perplexity-based
  jailbreak detection via a GPT-2 model, "length per perplexity" and
  "prefix/suffix perplexity" checks): reason: **requires an LLM
  (torch/transformers, downloads model weights)** — explicitly excluded by
  AGENTS.md §4 without founder go-ahead for the local-specialist-model
  layer, and out of scope for this round regardless.

## 3. promptfoo (promptfoo/promptfoo) — MIT license

**What was inspected**: `src/redteam/plugins/*.ts` (all ~70 red-team attack
plugins, including `asciiSmuggling.ts`, `promptExtraction.ts`,
`dataExfil.ts`, `indirectPromptInjection.ts`, `shellInjection.ts`,
`sqlInjection.ts`, `ssrf.ts`, and the entire `harmful/graders.ts` catalog of
~20 harm-category graders), `src/assertions/*.ts` (all ~40 assertion types
including `regex.ts`, `skill.ts`, `guardrails.ts`), `src/codeScan/scanner/`
(promptfoo's own static code-scan tool for agent skills/plugins), and
`src/redteam/strategies/indirectWebPwn.ts`.

**What was NOT ported, and why — every single plugin inspected**:

- **Every `*Grader` class in `src/redteam/plugins/`** (AsciiSmugglingGrader,
  PromptExtractionGrader, DataExfilGrader, all `harmful/graders.ts` classes,
  etc.): each one's `getResult()`/`rubric` is an **LLM-graded rubric** — the
  grader hands the model's live output plus a natural-language rubric to
  another LLM call and asks for a pass/fail judgment. This is promptfoo's
  entire design: it is a **dynamic red-teaming / eval framework** that runs
  attacks against a live target and grades live responses, not a static
  analyzer of an artifact's text. Reason: **requires an LLM call** for
  every single grader inspected, with no exception found.
  - One near-exception: `DataExfilGrader.getResult()` has a "deterministic"
    fast path that checks server-side exfiltration-tracking records
    (`gradingContext.wasExfiltrated`) instead of calling an LLM — but this
    requires promptfoo's own live tracking server infrastructure recording
    real HTTP callbacks during a live run. Reason: **requires network
    execution infrastructure**, not applicable to reviewing a static file.
- **`src/codeScan/scanner/`** (`output.ts`, `request.ts`, `cleanup.ts`):
  this is promptfoo's wrapper for invoking an *external* static analyzer
  (their own hosted "code-scan-action" product) over a codebase; it
  contains no detection logic of its own to mine — it is a thin
  process/HTTP orchestration layer. Reason: **no detection signature
  present to extract** — it delegates entirely to an external service.
- **`src/assertions/regex.ts`, `skill.ts`**: generic user-configurable
  assertion helpers (`new RegExp(userSuppliedPattern)`, skill-name/pattern
  matchers against live tool-call logs) — infrastructure for the eval
  framework's own config language, not a specific security detection
  pattern. Reason: **not a detection signature, generic plumbing**.
- **`test/agentSkills/`**: this is promptfoo's own test suite for its own
  `plugins/promptfoo` Agent-Skills packaging (a marketplace plugin, not a
  security scanner) — confirmed by reading `promptfooPlugin.test.ts`.
  Reason: **not a security scanner at all**, unrelated to the mining goal.

**Overall**: promptfoo's entire security-relevant surface (~70 attack
plugins + ~20 harm graders) is designed for dynamic red-teaming against a
live model endpoint with LLM-based grading of live responses. This round
found **zero** deterministic, artifact-text-level detection signatures to
port from promptfoo — its value to Verity's *future* V1.5 black-box phase
(AGENTS.md §4, "not yet implemented") is real, but nothing here is portable
into the current deterministic L0 engine.

## 4. NVIDIA NeMo-Guardrails (NVIDIA/NeMo-Guardrails) — Apache-2.0 license

**What was inspected**: `nemoguardrails/library/jailbreak_detection/
heuristics/checks.py`, `library/injection_detection/` (actions.py +
`yara_rules/{sqli,xss,code,template}.yara`), `library/context_bloat_
detection/actions.py` + `rail_config.py`, `library/sensitive_data_
detection/actions.py`, `library/attention/actions.py`, `library/regex/
rail_config.py`.

**What was evaluated and explicitly rejected (not merely skipped)**:

- **`context_bloat_detection`** (Shannon entropy + longest-repeated-run +
  n-gram repetition-ratio checks, meant to catch "pad the context to bury
  an instruction" attacks): this is genuinely deterministic and dependency-
  free, so it got a real trial rather than an automatic pass. It was
  **rejected** after false-positive testing: a legitimate long system
  prompt with many short, similarly-worded bullet rules (a completely
  normal real-world shape — "Rule: Always confirm the user's identity
  before rule N actions." repeated with a different N each time) measured
  a 3-gram repetition ratio of **0.69-0.87**, well above NeMo's own default
  `max_repetition_ratio` threshold of 0.4, alongside an actual padding
  attack measuring **0.97-0.98**. The two distributions overlap too closely
  for a "near-zero false-positive by construction" bar (the standard
  Verity holds its own ported rules to, e.g.
  `prompt.encoded_injection_payload`'s decode-then-match design). The
  `longest_run_ratio` check has the same problem: an ordinary markdown
  horizontal rule / section separator line (`----------`) is a legitimate,
  common prompt-authoring pattern that produces the same long-single-
  character-run signature the check is designed to catch. Reason:
  **evaluated and found too high a false-positive risk on realistic,
  legitimate structured prompts** — recorded here explicitly per the
  request that "too narrow/synthetic to matter" rejections be specific;
  this one is the opposite problem (too broad on realistic *safe* text),
  which is arguably worse for a tool whose credibility depends on
  precision.
- **`library/jailbreak_detection/heuristics/checks.py`** (`get_perplexity`,
  `check_jailbreak_length_per_perplexity`, `check_jailbreak_prefix_suffix_
  perplexity`): loads a GPT2LMHeadModel via `torch`/`transformers` at
  import time. Reason: **requires an LLM/heavy model dependency** —
  explicitly gated behind founder go-ahead per AGENTS.md §4's "local
  specialist-model layer" roadmap item, out of scope here.
- **`library/injection_detection/yara_rules/{sqli,xss,code,template}.yara`**:
  these are designed to scan a **model's generated output** for SQL
  injection / XSS / shell-import / Jinja-template-injection shapes before
  that output is rendered/executed downstream (the `injection_detection`
  action wires as an output rail). Reason: **wrong target — output
  rendering safety, not artifact review** (this maps conceptually to
  VR-SKILL-010 "unsafe output rendering," but the YARA rules themselves
  scan runtime model output text, not a Skill's own source files, so there
  is nothing here to apply to Verity's artifact-snapshot model without
  fabricating a scenario NeMo never intended).
- **`library/sensitive_data_detection/actions.py`**: imports
  `presidio_analyzer`/`presidio_anonymizer` (Microsoft Presidio, an NLP-
  based PII detector). Reason: **requires an NLP/ML model dependency** —
  out of scope for the deterministic engine.
- **`library/attention/actions.py`**: tracks live user speech/attention
  telemetry timestamps during a voice conversation (not a text-detection
  rule at all). Reason: **not applicable — runtime telemetry, not static
  detection**.
- **`library/regex/rail_config.py`**: a generic user-configurable
  regex-pattern-list config schema (bring-your-own-patterns), not a
  specific detection signature. Reason: **no detection signature to
  extract, generic config plumbing**.

**Overall**: NeMo-Guardrails's deterministic surface exists but is either
(a) targeted at output/response safety rather than artifact review, (b)
dependent on a heavy ML model, or (c) — in the one case that was genuinely
applicable and dependency-free (context bloat) — too imprecise on
realistic legitimate prompts to meet Verity's precision bar.

## 5. guardrails-ai (guardrails-ai/guardrails) — Apache-2.0 license

**What was inspected**: `guardrails/validator_base.py` (the `Validator`
base class + `register_validator` framework), the reference validator
implementations under `tests/integration_tests/test_assets/validators/`
(`regex_match.py`, `valid_url.py`, `valid_choices.py`, `valid_length.py`,
`detect_pii.py`, `reading_time.py`, `two_words.py`, `upper_case.py`,
`lower_case.py`, `ends_with.py`, `one_line.py`), `guardrails/utils/
sql_utils.py`, `guardrails/utils/regex_utils.py`, `guardrails/schema/
validator.py`, and `guardrails/formatters/`.

**What was NOT ported, and why**:

- **The `Validator`/`register_validator` framework itself**: this is a
  generic plugin architecture (bring-your-own-validator, install from
  "Guardrails Hub"), not a detection pattern. The actual security-relevant
  validators (PII, toxicity, competitor-mentions, etc.) are **not in this
  repository at all** — they live in the separate, closed/hub-distributed
  `guardrails-ai/hub` ecosystem and are installed at runtime via
  `guardrails hub install`; the base repo ships only trivial reference
  validators for its own test suite. Reason: **no actual detection
  signatures present in the cloned repository to mine** — confirmed by
  grep across the whole `guardrails/` package for `class.*Validator`
  outside of framework/telemetry code and finding none with a real
  security check.
- **`detect_pii.py`** (the one PII-related file found, in the test-assets
  directory): explicitly a `MockDetectPII` that does a literal string
  replace-map lookup, not real PII detection — its own docstring says
  "Instead of using Microsoft Presidio, it accepts a map of PII text to
  their replacements." The real guardrails-ai PII validator (on the Hub)
  wraps Presidio. Reason: **the only implementation present is a test
  mock with no real detection logic; the real one requires Presidio (an
  NLP model)**, so nothing is portable either way.
- **`valid_url.py`, `valid_choices.py`, `valid_length.py`, `regex_match.py`,
  `ends_with.py`, `one_line.py`, `two_words.py`, `upper_case.py`,
  `lower_case.py`, `reading_time.py`**: all are generic output-format/
  schema-shape validators (is this a URL? is this in an enum? is the
  string N words?) with no security content at all — they are examples for
  guardrails-ai's own test suite, not a security-detection catalog.
  Reason: **not security-relevant; generic format validation**.
- **`utils/sql_utils.py`**: delegates SQL syntax validation to the external
  `sqlvalidator` package or a live `sqlalchemy` database connection — it
  has no security-specific SQL-injection detection logic of its own (it
  checks *syntax validity*, not *injection risk*). Reason: **delegates to
  an external dependency/live DB connection, no portable signature
  present**.

**License note**: guardrails-ai's core repository is Apache-2.0
(compatible), so there was no license concern here — the finding is simply
that the repository contains no mineable detection signatures of its own;
its actual value proposition is the Hub ecosystem, which is out of scope
(separate distribution, would need its own review, and most Hub validators
wrap ML models rather than being deterministic).

---

## Cross-cutting notes

- **No `defer_license_review` entries this round.** All 5 projects (rebuff,
  PyRIT, promptfoo, NeMo-Guardrails, guardrails-ai) are MIT or Apache-2.0,
  same as Round 46's set — no GPL/LGPL concern like `standards/
  detector_candidates.json`'s ShellCheck/Semgrep entries.
- **Nothing in this round touched `src/verity/semantic/`, `src/verity/
  web/`, or `src/verity/cli.py`** — all patterns are pure static
  regex/structural detection in `src/verity/engine.py`, registered in
  `src/verity/builtins.py`, consistent with the isolated-adapter,
  no-model, no-network discipline in AGENTS.md §0/§4.
- **Scratch workspace**: `/tmp/oss_audit_round2/` (the 5 cloned repos) is
  ephemeral and was never committed, per the task's instruction to keep it
  separate from the original (lost) `/tmp/oss_audit/`. If a future round
  wants to re-verify a specific file cited above, the clone command is
  simply `git clone --depth 1 <url>` against: `github.com/protectai/rebuff`,
  `github.com/microsoft/PyRIT` (NOT the archived `Azure/PyRIT` redirect),
  `github.com/promptfoo/promptfoo`, `github.com/NVIDIA/NeMo-Guardrails`,
  `github.com/guardrails-ai/guardrails`.
