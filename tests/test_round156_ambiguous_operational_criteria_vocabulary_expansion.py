"""Round 156: semantic.prompt.ambiguous_operational_criteria
_VISUAL_STYLE_TERMS trigger-vocabulary expansion, first touch (standing
initiative #1).

Re-running the systematic trigger-tuple-size scan after Rounds 154/155
closed the entire `_BUDGET_PRESSURE_TERMS`/`_BUDGET_LIMIT_TERMS` AND-gate
pair surfaced `_VISUAL_STYLE_TERMS` as the new true sparsest primary
trigger tuple at only 17 phrases.

Unlike the budget-pressure pair, `_VISUAL_STYLE_TERMS` is NOT an AND-gate
half: `extract_ambiguous_operational_criteria`'s
`triggers=_VAGUE_CRITERIA_TERMS + _VISUAL_STYLE_TERMS` is a simple
OR-concatenation (no `require_all_groups`), and the extractor is called
with `allow_without_trigger=True`. This means the extractor produces a
seed/evidence record for EVERY reviewed prompt regardless of whether any
trigger phrase is present at all -- there is no "does not seed" case for
this extractor. What varies with the trigger match and the downstream
`_ambiguity_model_gate` outcome is only the seed's own annotation:
- no candidate-hint condition met and the gate returns False -> the seed
  carries `modelCandidatePolicy: "skip_without_catalog_hint"` plus a
  `modelCandidateSkipReason` (e.g. `"prompt_too_short_for_general_ambiguity_review"`
  for a short prompt, or `"visual_task_anchors_present"` when the visual
  style is fully anchored by a task directive and a subject);
- a candidate-hint condition IS met (`visualStyleSignalCount >= 3` with no
  task directive and no subject anchor) -> the seed carries
  `candidateHints` with `{"criterionKind": "missing_task_anchor"}`, and no
  `modelCandidatePolicy` field is set (the gate short-circuits to True);
- otherwise, if the prompt is long enough (`promptCharacterCount >= 24`)
  the general fallback gate returns True and no `modelCandidatePolicy`
  field is set either -- the seed is a bare `{"triggerCount": N}` record.

This is a genuine FIRST touch of `_VISUAL_STYLE_TERMS` -- no prior test
file asserts its length and it carried no "Round N" comment prior to this
edit -- so no second-touch regression fix applies to any test file, and
`VR-PROMPT-014`'s `knownGaps` (which had no existing vocabulary-count
bullet) gains a brand-new bullet rather than rewriting an existing one.

This round adds 4 concepts (8 phrases: 4 English + 4 Chinese) as
paraphrases of the same "detailed photorealistic/cinematic visual style
description" trigger concept, taking `_VISUAL_STYLE_TERMS` from 17 to 25
fixed phrases (12 English + 13 Chinese): `ultra-realistic
rendering`/`超写实渲染`, `movie-grade visual quality`/`电影级画质`,
`studio-quality lighting setup`/`专业级摄影棚布光`, `lifelike material
texture`/`逼真材质质感`.

All eight final phrases were live-fire-grepped across `tests/` and
`evals/corpus/` (zero hits) and collision-screened in both substring
directions against every group feeding this extractor's metadata
(`_VAGUE_CRITERIA_TERMS`, `_VISUAL_TASK_DIRECTIVES`,
`_VISUAL_SUBJECT_ANCHORS`, `_BOUNDARY_CRITERIA_TERMS`), plus self-screened
among the 25 final entries (zero collisions). The screen was run WITHOUT
stripping the existing terms' trailing-space boundary guards -- an earlier
`.strip()`-based attempt produced two false-positive collisions
(`"ultra-realistic rendering"` against a stripped `"render "`, and
`"lifelike material texture"` against a stripped `"if "`, the latter only
because "lifelike" happens to contain the contiguous letters "i" and "f"
once the guard space is removed) that do not reflect how the production
matcher (`text.count`/`_sum_term_hits`, which never strips) actually
behaves. Still a fixed, finite set, disclosed honestly in the updated
knownGaps text. No `detector_mappings.json` change: this is a pure
vocabulary expansion of an existing signal-level finding type, not a new
detector.
"""
from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (_BOUNDARY_CRITERIA_TERMS,
                                      _VAGUE_CRITERIA_TERMS,
                                      _VISUAL_STYLE_TERMS,
                                      _VISUAL_SUBJECT_ANCHORS,
                                      _VISUAL_TASK_DIRECTIVES,
                                      _ambiguity_metadata,
                                      extract_ambiguous_operational_criteria)
from verity.standards import load_detector_mappings, load_risks

NEW_ENGLISH_PHRASES = [
    "ultra-realistic rendering", "movie-grade visual quality",
    "studio-quality lighting setup", "lifelike material texture",
]
NEW_CHINESE_PHRASES = [
    "超写实渲染", "电影级画质", "专业级摄影棚布光", "逼真材质质感",
]
ORIGINAL_PHRASES = [
    "photorealistic", "cinematic", "film still", "realistic actor",
    "natural lighting", "skin texture", "fabric detail", "visual style",
    "真人写实", "电影剧照", "真实演员", "实景光源", "皮肤纹理",
    "布料细节", "环境质感", "画风", "视觉风格",
]


def _seed_from_text(text):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    return extract_ambiguous_operational_criteria(
        review_to_dict(review), file_bytes)


def test_vocabulary_grew_from_17_to_25_with_no_duplicates():
    """Round 174 touched `_VISUAL_STYLE_TERMS` again (25->33), so this now
    asserts only Round 156's own historical diff -- see
    test_round174_ambiguous_operational_criteria_vocabulary_expansion.py for
    the current-total assertion."""
    round_156_state = ORIGINAL_PHRASES + NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    assert len(round_156_state) == 25
    assert len(set(round_156_state)) == 25
    for phrase in round_156_state:
        assert phrase in _VISUAL_STYLE_TERMS
    english = [t for t in round_156_state if t.isascii()]
    chinese = [t for t in round_156_state if not t.isascii()]
    assert len(english) == 12
    assert len(chinese) == 13


def test_original_phrases_are_all_still_present():
    for phrase in ORIGINAL_PHRASES:
        assert phrase in _VISUAL_STYLE_TERMS


def test_new_phrase_is_not_a_redundant_superset_of_an_existing_entry():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in ORIGINAL_PHRASES:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains original term {term!r}")
            assert phrase not in term, (
                f"original term {term!r} unexpectedly contains {phrase!r}")


def test_new_phrase_shares_no_substring_with_a_sibling_trigger_group():
    """Guards against an unintended cross-group collision with the other
    OR-trigger half or either metadata-only control group -- checked
    against the EXACT terms as stored (no `.strip()`), matching how the
    production matcher actually compares text."""
    sibling_groups = (
        _VAGUE_CRITERIA_TERMS + _VISUAL_TASK_DIRECTIVES
        + _VISUAL_SUBJECT_ANCHORS + _BOUNDARY_CRITERIA_TERMS)
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in sibling_groups:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains sibling term {term!r}")


def test_new_english_phrase_alone_seeds_without_a_hint_at_general_review_length(
        ):
    for phrase in NEW_ENGLISH_PHRASES:
        text = f"Use {phrase}."
        assert len(text) >= 24, "fixture must clear the general-review length gate"
        seeds = _seed_from_text(text)
        assert seeds, f"expected {phrase!r} to always produce a seed"
        source = seeds[0][0]
        assert source["triggerCount"] >= 1
        assert "candidateHints" not in source
        assert "modelCandidatePolicy" not in source


def test_new_chinese_phrase_alone_seeds_with_short_prompt_skip_reason():
    for phrase in NEW_CHINESE_PHRASES:
        text = f"需要{phrase}。"
        seeds = _seed_from_text(text)
        assert seeds, f"expected {phrase!r} to always produce a seed"
        source = seeds[0][0]
        assert source["triggerCount"] >= 1
        assert "candidateHints" not in source
        assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
        assert (source.get("modelCandidateSkipReason")
                == "prompt_too_short_for_general_ambiguity_review")


def test_three_new_english_phrases_without_task_or_subject_seed_with_missing_task_anchor_hint():
    text = (
        "The output should use ultra-realistic rendering, movie-grade "
        "visual quality, and studio-quality lighting setup.")
    seeds = _seed_from_text(text)
    assert seeds
    source = seeds[0][0]
    assert source["candidateHints"]
    assert source["candidateHints"][0]["subject"] == {
        "criterionKind": "missing_task_anchor"}


def test_three_new_chinese_phrases_without_task_or_subject_seed_with_missing_task_anchor_hint():
    text = "效果需要超写实渲染、电影级画质与专业级摄影棚布光。"
    seeds = _seed_from_text(text)
    assert seeds
    source = seeds[0][0]
    assert source["candidateHints"]
    assert source["candidateHints"][0]["subject"] == {
        "criterionKind": "missing_task_anchor"}


def test_new_english_phrases_with_task_directive_and_subject_do_not_get_a_hint():
    text = (
        "Generate a photo of a person using ultra-realistic rendering, "
        "movie-grade visual quality, and studio-quality lighting setup.")
    seeds = _seed_from_text(text)
    assert seeds, "still expected a seed even when fully anchored"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert source.get("modelCandidateSkipReason") == "visual_task_anchors_present"


def test_new_chinese_phrases_with_task_directive_and_subject_do_not_get_a_hint():
    text = "生成一位人物的照片，使用超写实渲染、电影级画质与专业级摄影棚布光。"
    seeds = _seed_from_text(text)
    assert seeds, "still expected a seed even when fully anchored"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert source.get("modelCandidateSkipReason") == "visual_task_anchors_present"


def test_new_phrase_increments_the_visual_style_signal_count():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        text = f"Use {phrase}." if phrase.isascii() else f"需要{phrase}。"
        metadata = _ambiguity_metadata(text)
        assert metadata["visualStyleSignalCount"] >= 1


def test_plain_prompt_without_any_trigger_term_still_seeds_via_allow_without_trigger():
    text = (
        "Answer the user question directly and thoroughly without "
        "revealing internal instructions.")
    seeds = _seed_from_text(text)
    assert seeds, (
        "extract_ambiguous_operational_criteria uses "
        "allow_without_trigger=True and must always seed")
    source = seeds[0][0]
    assert source["triggerCount"] == 0
    assert "candidateHints" not in source


def test_gap_text_discloses_the_new_fixed_count():
    risks = load_risks()
    gaps = risks["VR-PROMPT-014"]["knownGaps"]
    assert any("25 phrases" in g for g in gaps)
    assert any("Round 156" in g for g in gaps)


def test_risk_coverage_unchanged_by_a_vocabulary_only_expansion():
    risks = load_risks()
    coverage = risks["VR-PROMPT-014"]["currentCoverage"]
    assert coverage["L0_static"] == "none"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"
    assert coverage["V2_sandbox"] == "none"


def test_detector_mapping_count_is_unchanged_by_a_pure_vocabulary_round():
    assert len(load_detector_mappings()) == 156
