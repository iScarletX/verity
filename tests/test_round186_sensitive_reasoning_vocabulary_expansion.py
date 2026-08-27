"""Round 186: semantic.prompt.sensitive_reasoning_exposure _REASONING_TERMS
trigger-vocabulary expansion, third touch (standing initiative #1).

Re-running the systematic trigger-tuple-size scan after Round 185 closed
`_FAILURE_OPERATION_TERMS` (29->37) found `_REASONING_TERMS` (29 phrases,
touched twice: Round 142 then Round 165) as the sole sparsest tuple in the
whole scan -- no tie to resolve this round.

`extract_sensitive_reasoning_exposure`'s (`VR-PROMPT-015`) candidate-hint
cascade (`_reasoning_candidate_hints`, built from `_reasoning_metadata`) is
a triple-AND-gate check: (1) `reasoningSignalCount` from `_REASONING_TERMS`
itself, trivially satisfied whenever the extractor seeds at all; (2)
`exposureSignalCount` from the separate `_REASONING_EXPOSURE_TERMS` group
(show/reveal/print/include/display and Chinese equivalents, with a
`boundary_terms=_REASONING_EXPOSURE_BOUNDARY_TERMS=frozenset({"print"})`
guard against "footprint"/"fingerprint"/"blueprint" false hits); (3)
`uncoveredReasoningExposureCount` from `_scoped_gap_count` over both
signal groups, minus any local rule window also covered by
`_REASONING_CONTAINMENT_TERMS` (do-not-reveal/keep-internal/
final-answer-only/brief-rationale/private/etc.). A hint
(`{"exposureKind": "chain_of_thought"}`) is returned only when all three
are nonzero -- the same shape Round 165 established, unchanged this round.
Confirmed interactively, the three cascade rungs relevant to this tuple
are unchanged from Round 165's own verification:
  1. A bare new-vocabulary phrase alone (no exposure request) seeds with
     no hint, `modelCandidatePolicy: "skip_without_catalog_hint"` /
     `modelCandidateSkipReason: "reasoning_containment_present_or_no_
     exposure"`.
  2. The same phrase plus an exposure request (e.g. "show your ...") with
     no containment rule seeds with a `{"exposureKind": "chain_of_
     thought"}` hint.
  3. The same phrase plus an exposure request AND an evidenced
     containment rule (e.g. "do not reveal ... keep internal ... final
     answer only") seeds with no hint, the same skip reason as rung 1.

This is the FOURTH touch overall of the standing second-touch regression
rule applied in this run of rounds (after Rounds 183/184/185), and both
halves were verified/fixed this round:
(a) `tests/test_round165_sensitive_reasoning_vocabulary_expansion.py`'s
    `test_vocabulary_grew_from_21_to_29_with_no_duplicates` asserted
    `len(_REASONING_TERMS) == 29` -- a stale exact-total check. Rewritten
    to assert only Round 165's own historical diff via a
    `round_165_state = ORIGINAL_PHRASES + NEW_ENGLISH_PHRASES +
    NEW_CHINESE_PHRASES` list, forward-referencing this file for the
    current-total assertion.
(b) `VR-PROMPT-015`'s dedicated vocabulary `knownGaps` bullet ("Trigger
    vocabulary (29 phrases after Round 165, up from 21 phrases after
    Round 142, up from 13 originally...)") was rewritten in place,
    chaining the count history, while its three OTHER pre-existing
    bullets (no policy-sensitivity classifier, no provider-hidden vs
    application-owned distinction, black-box probing scenario-count
    caveat) were left untouched.
Confirmed `tests/test_round142_sensitive_reasoning_vocabulary_expansion.py`
needs no further edit this round: its own
`test_vocabulary_grew_from_13_to_21_with_no_duplicates` was already
converted to the historical-diff pattern by Round 165, and its gap-text
substring checks ("21 phrases"/"Round 142") still survive verbatim inside
the newly chained bullet.

**Change.** Added 4 concepts (8 phrases: 4 English + 4 Chinese) as
further paraphrases of the same "chain-of-thought/scratchpad/
internal-policy reasoning process" trigger concept: `silent deliberation
trail`/`静默推演轨迹`, `unspoken chain of inference`/`未言明的推理链条`,
`backstage decision logic`/`幕后决策逻辑`, `unrecorded internal
calculus`/`未记录的内部演算`. This takes `_REASONING_TERMS` from 29 to 37
fixed phrases (18 English + 19 Chinese).

One Chinese candidate was deliberately avoided during design, mirroring
Round 165's own two dropped-candidate lessons: a first-considered
"不公开的分析步骤" was dropped before it was ever written to `catalog.py`,
because it contains the bare `_REASONING_EXPOSURE_TERMS` entry "公开"
verbatim -- a prompt using that phrase as its *trigger* would
simultaneously and unintentionally satisfy the *exposure* signal for the
same paragraph, which would flip rung-1 fixtures unpredictably depending
on window scoping. Dropped in favor of the four clean candidates above.

All eight final phrases were live-fire-grepped across `tests/`, `evals/`,
`src/`, `standards/`, and `docs/` (zero hits) and collision-screened
programmatically in both substring directions against the full existing
29-phrase tuple, plus the sibling `_REASONING_EXPOSURE_TERMS` (10 terms)
and `_REASONING_CONTAINMENT_TERMS` (10 terms) gated groups, plus the
`_REASONING_EXPOSURE_BOUNDARY_TERMS` guard ({"print"}), plus self-screened
among the 8 new candidates and confirmed all-lowercase per the Round 176
casing lesson -- zero collisions found on the final set (one Chinese
candidate dropped during design, described above). Interactively
confirmed, mirroring Round 165's exact fixture structure: each new phrase
alone seeds without a hint; the same phrase plus an exposure request
seeds with a `chain_of_thought` hint; the same phrase plus exposure and
containment seeds without a hint again; each new phrase increments
`reasoningSignalCount`; the plain-prompt baseline returns no seed. No
`detector_mappings.json` change: this is a pure vocabulary expansion of
an existing signal-level finding type, not a new detector.

**Tests.** 44 tests in this file (parametrized across the 8 new phrases),
plus the fixed Round 165 file. Combined regression run across
`test_round142_sensitive_reasoning_vocabulary_expansion.py` (31) +
`test_round165_sensitive_reasoning_vocabulary_expansion.py` (42) +
`test_round186_sensitive_reasoning_vocabulary_expansion.py` (44) = 117
tests, all passing.
"""
import pytest

from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (_REASONING_CONTAINMENT_TERMS,
                                      _REASONING_EXPOSURE_BOUNDARY_TERMS,
                                      _REASONING_EXPOSURE_TERMS,
                                      _REASONING_TERMS, _reasoning_metadata,
                                      extract_sensitive_reasoning_exposure)
from verity.standards import load_detector_mappings, load_risks

NEW_ENGLISH_PHRASES = [
    "silent deliberation trail", "unspoken chain of inference",
    "backstage decision logic", "unrecorded internal calculus",
]
NEW_CHINESE_PHRASES = [
    "静默推演轨迹", "未言明的推理链条", "幕后决策逻辑", "未记录的内部演算",
]
ROUND_165_STATE = [
    "chain of thought", "reasoning", "scratchpad", "internal policy",
    "hidden rule", "decision rule", "思维链", "推理过程", "思考过程",
    "内部策略", "隐藏规则", "内部规则", "判断规则",
    "internal deliberation", "working notes", "concealed logic",
    "thought process",
    "内部推演", "工作笔记", "隐藏逻辑", "思考轨迹",
    "internal thought record", "step-by-step rationale",
    "unstated internal logic", "confidential deliberation notes",
    "内部思考记录", "逐步推理依据", "未言明的内在逻辑", "保密推演记录",
]


def _seed_from_text(text):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    return extract_sensitive_reasoning_exposure(
        review_to_dict(review), file_bytes)


def test_vocabulary_grew_from_29_to_37_with_no_duplicates():
    assert len(ROUND_165_STATE) == 29
    round_186_state = ROUND_165_STATE + NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    assert len(round_186_state) == 37
    assert len(set(round_186_state)) == 37
    assert len(_REASONING_TERMS) == 37
    for phrase in round_186_state:
        assert phrase in _REASONING_TERMS
    english = [t for t in _REASONING_TERMS if t.isascii()]
    chinese = [t for t in _REASONING_TERMS if not t.isascii()]
    assert len(english) == 18
    assert len(chinese) == 19


def test_round_165_phrases_are_all_still_present():
    for phrase in ROUND_165_STATE:
        assert phrase in _REASONING_TERMS


def test_new_phrase_is_not_a_redundant_superset_of_an_existing_entry():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in ROUND_165_STATE:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains original term {term!r}")
            assert phrase not in term, (
                f"original term {term!r} unexpectedly contains {phrase!r}")


def test_new_phrase_shares_no_substring_with_the_gated_groups():
    """Checked against the EXACT terms as stored (no `.strip()`), matching
    how the production matcher (`text.count`/`in`) actually compares
    text -- guards against the exact defect caught during design (the
    "不公开的分析步骤" / "公开" exposure-term overlap)."""
    other_groups = _REASONING_EXPOSURE_TERMS + _REASONING_CONTAINMENT_TERMS
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in other_groups:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains {term!r}")


def test_new_phrase_does_not_touch_the_boundary_guard_terms():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in _REASONING_EXPOSURE_BOUNDARY_TERMS:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains boundary term {term!r}")


def test_new_phrase_self_screen_has_no_internal_collision():
    all_new = NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    for i, a in enumerate(all_new):
        for j, b in enumerate(all_new):
            if i == j:
                continue
            assert a not in b, f"{a!r} unexpectedly contains {b!r}"


def test_new_english_phrase_is_all_lowercase_to_match_lowercased_prompt_text():
    for phrase in NEW_ENGLISH_PHRASES:
        assert phrase == phrase.lower(), (
            f"{phrase!r} contains uppercase characters and would never "
            f"match the lowercased prompt text")


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES)
def test_new_english_phrase_alone_seeds_without_a_hint(phrase):
    seeds = _seed_from_text(
        f"The assistant relies on {phrase} to answer questions.")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert (source.get("modelCandidateSkipReason")
            == "reasoning_containment_present_or_no_exposure")


@pytest.mark.parametrize("phrase", NEW_CHINESE_PHRASES)
def test_new_chinese_phrase_alone_seeds_without_a_hint(phrase):
    seeds = _seed_from_text(f"助手依靠{phrase}来回答问题。")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert (source.get("modelCandidateSkipReason")
            == "reasoning_containment_present_or_no_exposure")


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES)
def test_new_english_phrase_with_exposure_seeds_with_a_hint(phrase):
    seeds = _seed_from_text(
        f"Please show your {phrase} to the user in the final response.")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    hints = seeds[0][0]["candidateHints"]
    assert hints
    assert hints[0]["subject"]["exposureKind"] == "chain_of_thought"


@pytest.mark.parametrize("phrase", NEW_CHINESE_PHRASES)
def test_new_chinese_phrase_with_exposure_seeds_with_a_hint(phrase):
    seeds = _seed_from_text(f"请在最终回复中向用户展示你的{phrase}。")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    hints = seeds[0][0]["candidateHints"]
    assert hints
    assert hints[0]["subject"]["exposureKind"] == "chain_of_thought"


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES)
def test_new_english_phrase_with_exposure_and_containment_seeds_without_a_hint(
        phrase):
    seeds = _seed_from_text(
        f"Please show your {phrase}. Do not reveal it though -- keep "
        f"internal, final answer only.")
    assert seeds, f"expected {phrase!r} to still trigger a seed"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert (source.get("modelCandidateSkipReason")
            == "reasoning_containment_present_or_no_exposure")


@pytest.mark.parametrize("phrase", NEW_CHINESE_PHRASES)
def test_new_chinese_phrase_with_exposure_and_containment_seeds_without_a_hint(
        phrase):
    seeds = _seed_from_text(
        f"请展示你的{phrase}。不要透露，仅内部使用，只输出最终答案。")
    assert seeds, f"expected {phrase!r} to still trigger a seed"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert (source.get("modelCandidateSkipReason")
            == "reasoning_containment_present_or_no_exposure")


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES)
def test_new_phrase_increments_the_reasoning_signal_count(phrase):
    text = f"{phrase} now." if phrase.isascii() else f"{phrase}。"
    metadata = _reasoning_metadata(text)
    assert metadata["reasoningSignalCount"] >= 1


def test_plain_prompt_without_any_reasoning_term_does_not_seed():
    seeds = _seed_from_text(
        "Please write a haiku about the ocean waves at sunset.")
    assert seeds == []


def test_gap_text_discloses_the_new_fixed_count():
    risks = load_risks()
    gaps = risks["VR-PROMPT-015"]["knownGaps"]
    assert any("37 phrases" in g and "Round 186" in g for g in gaps)


def test_gap_text_keeps_the_prior_rounds_counts_in_the_chained_history():
    risks = load_risks()
    gaps = risks["VR-PROMPT-015"]["knownGaps"]
    assert any("29 phrases after Round 165" in g for g in gaps)
    assert any("21 phrases after Round 142" in g for g in gaps)
    assert any("13 originally" in g for g in gaps)


def test_risk_coverage_unchanged_by_a_vocabulary_only_expansion():
    risks = load_risks()
    coverage = risks["VR-PROMPT-015"]["currentCoverage"]
    assert coverage["L0_static"] == "none"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "signal"
    assert coverage["V2_sandbox"] == "none"


def test_detector_mapping_count_is_unchanged_by_a_pure_vocabulary_round():
    assert len(load_detector_mappings()) == 156
