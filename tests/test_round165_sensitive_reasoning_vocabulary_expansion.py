"""Round 165: semantic.prompt.sensitive_reasoning_exposure _REASONING_TERMS
trigger-vocabulary expansion, second touch (standing initiative #1).

Re-running the systematic trigger-tuple-size scan after Round 164 closed
`_ATTENTION_STRUCTURE_TERMS` (20->28) confirmed the same exhaustion Round
164 first identified: every primary single-trigger tuple discovered by
the `triggers=` scan carries at least one prior "Round N" touch comment,
so the established continuation is to pick the globally sparsest tuple
(regardless of touch count) and add another touch. `_REASONING_TERMS` (21
phrases, touched once in Round 142) is now the sparsest tuple in the whole
scan, since Round 164 moved `_ATTENTION_STRUCTURE_TERMS` up to 28.

`extract_sensitive_reasoning_exposure`'s (`VR-PROMPT-015`) candidate-hint
cascade (`_reasoning_candidate_hints`, built from `_reasoning_metadata`) is
a three-gate check: (1) `reasoningSignalCount` from `_REASONING_TERMS`
itself, trivially satisfied whenever the extractor seeds at all; (2)
`exposureSignalCount` from the separate `_REASONING_EXPOSURE_TERMS` group
(show/reveal/print/include/display and Chinese equivalents); (3)
`uncoveredReasoningExposureCount` from `_scoped_gap_count` over both
signal groups, minus any paragraph also covered by
`_REASONING_CONTAINMENT_TERMS` (do-not-reveal/keep-internal/
final-answer-only/brief-rationale/private/etc.). A hint
(`{"exposureKind": "chain_of_thought"}`) is returned only when all three
are nonzero. Confirmed interactively, the three cascade rungs relevant to
this tuple are:
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

This round adds 4 concepts (8 phrases: 4 English + 4 Chinese) as
paraphrases of the same "chain-of-thought/scratchpad/internal-policy
reasoning process" trigger concept, taking `_REASONING_TERMS` from 21 to
29 fixed phrases (14 English + 15 Chinese): `internal thought record`/
`内部思考记录`, `step-by-step rationale`/`逐步推理依据`, `unstated internal
logic`/`未言明的内在逻辑`, `confidential deliberation notes`/`保密推演记录`.

One phrase was deliberately avoided during design, mirroring Round 142's
own "private notes" lesson: a first-considered "private deliberation
notes" was dropped before it was ever written to `catalog.py`, because it
contains the bare `_REASONING_CONTAINMENT_TERMS` entry "private" verbatim
-- a prompt using that phrase as its *trigger* would simultaneously and
unintentionally satisfy the *containment* gate for the same paragraph,
silently suppressing the very hint the new phrase was meant to help
surface. Replaced with "confidential deliberation notes", verified to
share no substring with any `_REASONING_TERMS`/`_REASONING_EXPOSURE_
TERMS`/`_REASONING_CONTAINMENT_TERMS` entry in either substring direction.
Also avoided: a first-considered "unstated internal reasoning" was dropped
because it contains the bare `_REASONING_TERMS` entry "reasoning"
verbatim, a redundant superset adding zero actual recall -- replaced with
"unstated internal logic".

All eight final phrases were live-fire-grepped across `tests/`,
`evals/corpus/`, and `src/` (zero hits) and collision-screened in both
substring directions against `_REASONING_TERMS` itself, the separately-
gated `_REASONING_EXPOSURE_TERMS`/`_REASONING_CONTAINMENT_TERMS` groups,
plus self-screened among the 8 new candidates -- using unstripped terms as
stored, matching production matching exactly -- zero collisions found.
`VR-PROMPT-015`'s existing Round-142 knownGaps bullet was updated in place
(not appended as a second bullet), chaining the count history, mirroring
the exact convention Round 151 used for `_AUTONOMY_TERMS`'s own second
touch on `VR-PROMPT-012`, and Round 164's for `_ATTENTION_STRUCTURE_
TERMS`'s own second touch on `VR-PROMPT-025`. Per that same precedent,
`tests/test_round142_sensitive_reasoning_vocabulary_expansion.py`'s
`test_vocabulary_grew_from_13_to_21_with_no_duplicates` -- a now-stale
exact-total check -- was rewritten to assert only Round 142's own
historical diff via a `ROUND_142_STATE` list, forward-referencing this
file for the current-total assertion; its own gap-text substring check
("21 phrases"/"Round 142") still passes since both substrings survive
verbatim inside the newly chained bullet. No `detector_mappings.json`
change: pure vocabulary expansion of an existing signal-level finding
type, not a new detector.
"""
import pytest

from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (_REASONING_CONTAINMENT_TERMS,
                                      _REASONING_EXPOSURE_TERMS,
                                      _REASONING_TERMS, _reasoning_metadata,
                                      extract_sensitive_reasoning_exposure)
from verity.standards import load_detector_mappings, load_risks

NEW_ENGLISH_PHRASES = [
    "internal thought record", "step-by-step rationale",
    "unstated internal logic", "confidential deliberation notes",
]
NEW_CHINESE_PHRASES = [
    "内部思考记录", "逐步推理依据", "未言明的内在逻辑", "保密推演记录",
]
ORIGINAL_PHRASES = [
    "chain of thought", "reasoning", "scratchpad", "internal policy",
    "hidden rule", "decision rule", "思维链", "推理过程", "思考过程",
    "内部策略", "隐藏规则", "内部规则", "判断规则",
    "internal deliberation", "working notes", "concealed logic",
    "thought process",
    "内部推演", "工作笔记", "隐藏逻辑", "思考轨迹",
]


def _seed_from_text(text):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    return extract_sensitive_reasoning_exposure(
        review_to_dict(review), file_bytes)


def test_vocabulary_grew_from_21_to_29_with_no_duplicates():
    """Round 186 touched `_REASONING_TERMS` again (29->37), so this now
    asserts only Round 165's own historical diff -- see
    test_round186_sensitive_reasoning_vocabulary_expansion.py for the
    current-total assertion."""
    round_165_state = ORIGINAL_PHRASES + NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    assert len(round_165_state) == 29
    assert len(set(round_165_state)) == 29
    for phrase in round_165_state:
        assert phrase in _REASONING_TERMS
    english = [t for t in round_165_state if t.isascii()]
    chinese = [t for t in round_165_state if not t.isascii()]
    assert len(english) == 14
    assert len(chinese) == 15


def test_original_phrases_are_all_still_present():
    for phrase in ORIGINAL_PHRASES:
        assert phrase in _REASONING_TERMS


def test_new_phrase_is_not_a_redundant_superset_of_an_existing_entry():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in ORIGINAL_PHRASES:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains original term {term!r}")
            assert phrase not in term, (
                f"original term {term!r} unexpectedly contains {phrase!r}")


def test_new_phrase_shares_no_substring_with_the_gated_groups():
    """Checked against the EXACT terms as stored (no `.strip()`), matching
    how the production matcher (`text.count`/`in`, which never strips)
    actually compares text -- guards against the exact defect caught
    during design (the "private deliberation notes" / "private"
    containment-term overlap, and the "unstated internal reasoning" /
    "reasoning" trigger-term overlap)."""
    other_groups = _REASONING_EXPOSURE_TERMS + _REASONING_CONTAINMENT_TERMS
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in other_groups:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains {term!r}")


def test_new_phrase_self_screen_has_no_internal_collision():
    all_new = NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    for i, a in enumerate(all_new):
        for j, b in enumerate(all_new):
            if i == j:
                continue
            assert a not in b, f"{a!r} unexpectedly contains {b!r}"


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
    assert any("29 phrases" in g and "Round 165" in g for g in gaps)


def test_gap_text_keeps_the_prior_round_142_count_in_the_chained_history():
    risks = load_risks()
    gaps = risks["VR-PROMPT-015"]["knownGaps"]
    assert any("21 phrases after Round 142" in g for g in gaps)


def test_risk_coverage_unchanged_by_a_vocabulary_only_expansion():
    risks = load_risks()
    coverage = risks["VR-PROMPT-015"]["currentCoverage"]
    assert coverage["L0_static"] == "none"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "signal"
    assert coverage["V2_sandbox"] == "none"


def test_detector_mapping_count_is_unchanged_by_a_pure_vocabulary_round():
    assert len(load_detector_mappings()) == 156
