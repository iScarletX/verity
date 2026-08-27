"""Round 180: semantic.prompt.template_completeness_gap _TEMPLATE_GAP_TERMS
trigger-vocabulary expansion, second touch (standing initiative #1).

Re-running the systematic trigger-tuple-size scan after Round 179 closed
`_SENSITIVE_DATA_ACTION_TERMS` surfaced a three-way tie at 27 phrases:
`_MULTI_TURN_TERMS` (last touched Round 158), `_TEMPLATE_GAP_TERMS`
(`VR-PROMPT-002`'s `extract_template_completeness_gap`, last touched
Round 152), and `_TOOL_CALL_TERMS` (last touched Round 153). Applying the
tied-size tie-break rule (oldest last-touch round wins),
`_TEMPLATE_GAP_TERMS` (152, the oldest of the three) is picked.

This is the SECOND touch of `_TEMPLATE_GAP_TERMS` (created Round 94,
first expanded Round 152). `extract_template_completeness_gap` remains a
bare-trigger shape -- a single-line call to `_whole_prompt_seed` with only
`triggers`/`producer_id`, no `metadata_builder`, `candidate_hint_builder`,
`model_candidate_gate`, or `require_all_groups` cascade -- unchanged by
this round.

Both halves of the standing second-touch regression rule apply and were
verified/fixed this round:
(a) `tests/test_round152_template_completeness_gap_vocabulary_
    expansion.py`'s `test_vocabulary_grew_from_19_to_27_with_no_
    duplicates` asserted `len(_TEMPLATE_GAP_TERMS) == 27` -- a stale
    exact-total check. Rewritten to assert only Round 152's own
    historical diff via a `round_152_state = ORIGINAL_PHRASES +
    NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES` list, forward-referencing
    this file for the current-total assertion. Re-ran both
    `test_round152_template_completeness_gap_vocabulary_expansion.py` and
    `test_round94_template_completeness_gap.py` standalone after the fix:
    29/29 passed.
(b) `VR-PROMPT-002`'s `knownGaps` vocabulary bullet was checked by Round
    152's own `test_gap_text_discloses_the_new_fixed_count`, which
    inspects the literal substrings "27 phrases" and "Round 152". The
    bullet was rewritten in place to preserve both of those substrings
    alongside this round's own "35 phrases"/"Round 180" disclosure.

This round adds 4 concepts (8 phrases: 4 English + 4 Chinese) as further
paraphrases of the same "authoring-time template incompleteness expressed
in free-form prose" trigger concept, taking `_TEMPLATE_GAP_TERMS` from 27
to 35 fixed phrases (18 English + 17 Chinese): `work in progress, do not
distribute`/`内容正在编写中，请勿分发`, `sample content, update prior to
launch`/`此为示例内容，上线前需要更新`, `author to complete this
section`/`作者需在此处补充内容`, `boilerplate text pending
revision`/`样板文字待修订`.

All eight final phrases were live-fire-grepped across `tests/`, `evals/`,
`src/`, `standards/`, and `docs/` (zero hits) and collision-screened in
both substring directions against the full existing 27-phrase tuple, plus
self-screened among the 8 new candidates (zero collisions found). All 4
English candidates were also confirmed all-lowercase per the casing-bug
lesson caught in Round 176. Because every phrase here (old and new) is a
multi-word/multi-character qualified phrase, bare-substring collisions
are not a structural concern for this tuple, but the screen was still run
explicitly, consistent with every prior round's discipline.

Each new phrase is verified purely via seed-without-hint behavior,
mirroring Round 94's and Round 152's own fixture style (embedding the
phrase in a short prose sentence). The disjointness guard against the
deterministic `prompt.unfilled_placeholder` bracket/mustache-syntax rule
is re-confirmed after this vocabulary expansion. Still a fixed, finite
set, disclosed honestly in the updated knownGaps text. No
`detector_mappings.json` change: this is a pure vocabulary expansion of
an existing signal-level finding type, not a new detector.
"""
import pytest

from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (_TEMPLATE_GAP_TERMS,
                                      extract_template_completeness_gap)
from verity.standards import load_detector_mappings, load_risks

NEW_ENGLISH_PHRASES = [
    "work in progress, do not distribute",
    "sample content, update prior to launch",
    "author to complete this section",
    "boilerplate text pending revision",
]
NEW_CHINESE_PHRASES = [
    "内容正在编写中，请勿分发",
    "此为示例内容，上线前需要更新",
    "作者需在此处补充内容",
    "样板文字待修订",
]
ROUND_152_STATE = [
    "lorem ipsum", "placeholder text", "to be filled in", "to be completed",
    "still under construction", "content coming soon", "insert your own",
    "fill in your own", "replace this with your own", "add your content here",
    "占位符", "占位内容", "待补充", "待完善", "待填写", "此处填写", "此处插入",
    "尚未完成", "施工中",
    "not yet finalized", "requires further input from the author",
    "replace before publishing", "first draft pending content",
    "尚未定稿", "需要作者进一步补充信息", "发布前请替换", "初稿待定",
]


def _seed_from_text(text):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    return extract_template_completeness_gap(
        review_to_dict(review), file_bytes)


def test_vocabulary_grew_from_27_to_35_with_no_duplicates():
    assert len(ROUND_152_STATE) == 27
    round_180_state = ROUND_152_STATE + NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    assert len(round_180_state) == 35
    assert len(set(round_180_state)) == 35
    assert len(_TEMPLATE_GAP_TERMS) == 35
    for phrase in round_180_state:
        assert phrase in _TEMPLATE_GAP_TERMS
    english = [t for t in _TEMPLATE_GAP_TERMS if t.isascii()]
    chinese = [t for t in _TEMPLATE_GAP_TERMS if not t.isascii()]
    assert len(english) == 18
    assert len(chinese) == 17


def test_round_152_phrases_are_all_still_present():
    for phrase in ROUND_152_STATE:
        assert phrase in _TEMPLATE_GAP_TERMS


def test_new_phrase_is_not_a_redundant_superset_of_an_existing_entry():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in ROUND_152_STATE:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains original term {term!r}")
            assert phrase not in term, (
                f"original term {term!r} unexpectedly contains {phrase!r}")


def test_new_phrase_self_screen_has_no_internal_collision():
    all_new = NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    for phrase in all_new:
        for other in all_new:
            if phrase is other:
                continue
            assert other not in phrase, (
                f"{phrase!r} unexpectedly contains {other!r}")


def test_new_english_phrase_is_all_lowercase_to_match_lowercased_prompt_text():
    """Regression guard for the casing bug caught in Round 176: `_whole_
    prompt_seed` lowercases the decoded prompt text before substring
    matching, so any trigger term containing an uppercase character could
    never match."""
    for phrase in NEW_ENGLISH_PHRASES:
        assert phrase == phrase.lower(), (
            f"{phrase!r} contains uppercase characters and would never "
            f"match the lowercased prompt text")


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
    assert any("35 phrases" in g for g in gaps)
    assert any("Round 180" in g for g in gaps)


def test_gap_text_keeps_the_prior_rounds_counts_in_the_chained_history():
    risks = load_risks()
    gaps = risks["VR-PROMPT-002"]["knownGaps"]
    assert any("27 phrases" in g and "Round 152" in g for g in gaps)


def test_risk_coverage_unchanged_by_a_vocabulary_only_expansion():
    risks = load_risks()
    coverage = risks["VR-PROMPT-002"]["currentCoverage"]
    assert coverage["L0_static"] == "partial"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"
    assert coverage["V2_sandbox"] == "none"


def test_detector_mapping_count_is_unchanged_by_a_pure_vocabulary_round():
    assert len(load_detector_mappings()) == 156
