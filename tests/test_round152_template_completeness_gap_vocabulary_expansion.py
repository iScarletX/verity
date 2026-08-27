"""Round 152: semantic.prompt.template_completeness_gap _TEMPLATE_GAP_TERMS
trigger-vocabulary expansion, first touch (standing initiative #1).

Re-running the systematic trigger-tuple-size scan after Round 151 closed
`_AUTONOMY_TERMS` surfaced a tied 19-phrase tier: `_MULTI_TURN_TERMS`,
`_TEMPLATE_GAP_TERMS` (`VR-PROMPT-002`'s
`extract_template_completeness_gap`), and `_TOOL_CALL_TERMS`.
`_TEMPLATE_GAP_TERMS` was chosen over the other two because it is the
simplest of the three: `extract_template_completeness_gap` is a single-line
call to `_whole_prompt_seed` with only `triggers`/`producer_id` -- no
`metadata_builder`, `candidate_hint_builder`, `model_candidate_gate`, or
`require_all_groups` cascade at all (unlike `_MULTI_TURN_TERMS` and
`_TOOL_CALL_TERMS`, both already second-generation-expanded tuples with
their own cascades). It is also a genuine FIRST touch: the tuple carries no
"Round N" comment from any prior expansion (created in Round 94 and never
widened since), and its original detector test file
(`tests/test_round94_template_completeness_gap.py`) asserts no
`len(_TEMPLATE_GAP_TERMS)` anywhere, so no second-touch regression fix is
needed to any existing file this round -- unlike Rounds 150/151.

This round adds 4 concepts (8 phrases: 4 English + 4 Chinese) as paraphrases
of the same "authoring-time template incompleteness expressed in free-form
prose" trigger concept that the tuple's own in-file comment describes (the
concept is explicitly disjoint from the deterministic
`prompt.unfilled_placeholder` rule's mustache/dollar-brace/angle-bracket/
square-bracket syntax coverage): `not yet finalized`/`尚未定稿`, `requires
further input from the author`/`需要作者进一步补充信息`, `replace before
publishing`/`发布前请替换`, `first draft pending content`/`初稿待定`. This
takes `_TEMPLATE_GAP_TERMS` from 19 to 27 fixed phrases (14 English + 13
Chinese).

All eight final phrases were live-fire-grepped across `tests/` and
`evals/corpus/` (zero hits) and collision-screened in both substring
directions against the full existing 19-phrase tuple, plus self-screened
among the 8 new candidates (zero collisions found on the first attempt, no
design-time fix needed this round). Because every phrase here (old and new)
is a multi-word/multi-character qualified phrase, the file's own header
comment notes bare-substring collisions are not a structural concern for
this tuple -- the screen was still run explicitly as a verification step,
consistent with every prior round's discipline.

`extract_template_completeness_gap` has a bare-trigger shape (no cascade,
no companion term group, no AND-gate) -- each new phrase is verified purely
via seed-without-hint behavior, mirroring Round 94's own fixture style
(embedding the phrase in a short prose sentence). The disjointness guard
against the deterministic bracket/mustache-syntax rule is re-confirmed
after the vocabulary expansion to ensure no regression in that boundary.
Still a fixed, finite set, disclosed honestly via a brand-new `knownGaps`
bullet (no prior vocabulary bullet existed for `VR-PROMPT-002` to
preserve/rewrite -- this is an additive, first-touch bullet, matching
Round 149's pattern). No `detector_mappings.json` change: this is a pure
vocabulary expansion of an existing signal-level finding type, not a new
detector.
"""
import pytest

from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (_TEMPLATE_GAP_TERMS,
                                      extract_template_completeness_gap)
from verity.standards import load_detector_mappings, load_risks

NEW_ENGLISH_PHRASES = [
    "not yet finalized", "requires further input from the author",
    "replace before publishing", "first draft pending content",
]
NEW_CHINESE_PHRASES = [
    "尚未定稿", "需要作者进一步补充信息", "发布前请替换", "初稿待定",
]
ORIGINAL_PHRASES = [
    "lorem ipsum", "placeholder text", "to be filled in", "to be completed",
    "still under construction", "content coming soon", "insert your own",
    "fill in your own", "replace this with your own", "add your content here",
    "占位符", "占位内容", "待补充", "待完善", "待填写", "此处填写", "此处插入",
    "尚未完成", "施工中",
]


def _seed_from_text(text):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    return extract_template_completeness_gap(
        review_to_dict(review), file_bytes)


def test_vocabulary_grew_from_19_to_27_with_no_duplicates():
    """Round 180 touched `_TEMPLATE_GAP_TERMS` again (27->35), so this now
    asserts only Round 152's own historical diff -- see
    test_round180_template_completeness_gap_vocabulary_expansion.py for
    the current-total assertion."""
    round_152_state = ORIGINAL_PHRASES + NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    assert len(round_152_state) == 27
    assert len(set(round_152_state)) == 27
    for phrase in round_152_state:
        assert phrase in _TEMPLATE_GAP_TERMS
    english = [t for t in round_152_state if t.isascii()]
    chinese = [t for t in round_152_state if not t.isascii()]
    assert len(english) == 14
    assert len(chinese) == 13


def test_original_phrases_are_all_still_present():
    for phrase in ORIGINAL_PHRASES:
        assert phrase in _TEMPLATE_GAP_TERMS


def test_new_phrase_is_not_a_redundant_superset_of_an_existing_entry():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in ORIGINAL_PHRASES:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains original term {term!r}")
            assert phrase not in term, (
                f"original term {term!r} unexpectedly contains {phrase!r}")


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES)
def test_new_english_phrase_seeds(phrase):
    seeds = _seed_from_text(f"Escalation contact: {phrase}.")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    assert seeds[0][0]["triggerCount"] >= 1


@pytest.mark.parametrize("phrase", NEW_CHINESE_PHRASES)
def test_new_chinese_phrase_seeds(phrase):
    seeds = _seed_from_text(f"联系方式：{phrase}。")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    assert seeds[0][0]["triggerCount"] >= 1


def test_plain_prompt_without_any_template_gap_term_does_not_seed():
    seeds = _seed_from_text(
        "Answer the user question directly and cite sources when "
        "possible.")
    assert seeds == []


def test_deterministic_bracket_syntax_alone_still_does_not_seed():
    """Disjointness guard vs. the deterministic prompt.unfilled_placeholder
    rule: mustache-wrapped syntax alone must not trigger this prose-level
    extractor, even after the vocabulary expansion."""
    seeds = _seed_from_text(
        "Send the report to {{ recipient_email }} every Friday.")
    assert seeds == []


def test_gap_text_discloses_the_new_fixed_count():
    risks = load_risks()
    gaps = risks["VR-PROMPT-002"]["knownGaps"]
    assert any("27 phrases" in g for g in gaps)
    assert any("Round 152" in g for g in gaps)


def test_risk_coverage_unchanged_by_a_vocabulary_only_expansion():
    risks = load_risks()
    coverage = risks["VR-PROMPT-002"]["currentCoverage"]
    assert coverage["L0_static"] == "partial"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"
    assert coverage["V2_sandbox"] == "none"


def test_detector_mapping_count_is_unchanged_by_a_pure_vocabulary_round():
    assert len(load_detector_mappings()) == 156
