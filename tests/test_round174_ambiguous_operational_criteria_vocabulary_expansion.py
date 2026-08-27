"""Round 174: semantic.prompt.ambiguous_operational_criteria
_VISUAL_STYLE_TERMS trigger-vocabulary expansion, second touch (standing
initiative #1).

Re-running the systematic trigger-tuple-size scan after Round 173 closed
`_FIELD_CONTRACT_TERMS` (which resolved the prior two-way tie with this
tuple in favor of the older Round 147) leaves `_VISUAL_STYLE_TERMS`
(Round 156) as the sole sparsest primary trigger tuple at 25 phrases, no
tie this round.

**Shape, unchanged from Round 156.** `extract_ambiguous_operational_
criteria`'s `triggers=_VAGUE_CRITERIA_TERMS + _VISUAL_STYLE_TERMS` is a
simple OR-concatenation (no `require_all_groups`), called with
`allow_without_trigger=True` -- every reviewed prompt produces a seed
regardless of whether any trigger phrase is present. What varies is only
the seed's annotation:
- `visualStyleSignalCount >= 3` with no task directive and no subject
  anchor -> `candidateHints` with `{"criterionKind": "missing_task_anchor"}`;
- otherwise, with a task directive AND a subject anchor present alongside
  3+ visual-style hits -> `modelCandidatePolicy: "skip_without_catalog_hint"`,
  `modelCandidateSkipReason: "visual_task_anchors_present"`;
- otherwise, if `promptCharacterCount >= 24` -> the general fallback gate
  returns True, no `modelCandidatePolicy` field set;
- otherwise (short prompt) -> `modelCandidateSkipReason:
  "prompt_too_short_for_general_ambiguity_review"`.
All rungs verified interactively for every new phrase in both languages
before writing this file.

This round adds 4 concepts (8 phrases: 4 English + 4 Chinese) as further
paraphrases of the same "detailed photorealistic/cinematic visual style
description" trigger concept: `high-fidelity render`/`高保真渲染`,
`ray-traced lighting`/`光线追踪光照`, `cinema-grade color grading`/`电影级调色`,
`hyper-realistic texture rendering`/`超逼真纹理渲染`. This takes
`_VISUAL_STYLE_TERMS` from 25 to 33 fixed phrases (16 English + 17
Chinese). No change to `_VAGUE_CRITERIA_TERMS`, `_VISUAL_TASK_DIRECTIVES`,
`_VISUAL_SUBJECT_ANCHORS`, `_BOUNDARY_CRITERIA_TERMS`, or the
`_ambiguity_model_gate` logic.

**Collision screening.** An earlier draft candidate, "hyper-detailed
texture rendering", was rejected: it bare-contains "detailed", itself a
listed `_VAGUE_CRITERIA_TERMS` entry, which would have leaked a hit into
the sibling OR-trigger's `vagueCriterionCount` whenever the new phrase
matched -- a real cross-group collision, not a false-positive artifact of
an over-eager screen. It was replaced with "hyper-realistic texture
rendering"/"超逼真纹理渲染", which screens clean. All eight final phrases
were live-fire-grepped across `tests/`, `evals/corpus/`, and `src/` (zero
hits) and collision-screened programmatically in both substring
directions against `_VISUAL_STYLE_TERMS` itself and the three sibling
groups feeding this extractor's metadata, plus self-screened among the 8
new candidates -- zero collisions found on the corrected set.

**Verification.** `VR-PROMPT-014`'s existing Round-156 `knownGaps` bullet
was updated in place, chaining the count history -- "33 phrases after
Round 174, up from 25 phrases after Round 156, up from 17 originally" --
mirroring the exact convention Rounds 151/164-173 used. The separate
sibling bullet for `_VAGUE_CRITERIA_TERMS` (Round 163) is untouched. Per
the same precedent,
`tests/test_round156_ambiguous_operational_criteria_vocabulary_expansion.py`'s
`test_vocabulary_grew_from_17_to_25_with_no_duplicates` -- a now-stale
exact-total check -- was rewritten to assert only Round 156's own
historical diff via a `round_156_state` list, forward-referencing this
round's test file for the current-total assertion; its own gap-text
substring checks (`"25 phrases"`/`"Round 156"`) still pass since both
substrings survive verbatim inside the newly chained bullet. No
`detector_mappings.json` change: pure vocabulary expansion of an existing
signal-level finding type, not a new detector.
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
    "high-fidelity render", "ray-traced lighting",
    "cinema-grade color grading", "hyper-realistic texture rendering",
]
NEW_CHINESE_PHRASES = [
    "高保真渲染", "光线追踪光照", "电影级调色", "超逼真纹理渲染",
]
ORIGINAL_PHRASES = [
    "photorealistic", "cinematic", "film still", "realistic actor",
    "natural lighting", "skin texture", "fabric detail", "visual style",
    "真人写实", "电影剧照", "真实演员", "实景光源", "皮肤纹理",
    "布料细节", "环境质感", "画风", "视觉风格",
    "ultra-realistic rendering", "movie-grade visual quality",
    "studio-quality lighting setup", "lifelike material texture",
    "超写实渲染", "电影级画质", "专业级摄影棚布光", "逼真材质质感",
]


def _seed_from_text(text):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    return extract_ambiguous_operational_criteria(
        review_to_dict(review), file_bytes)


def test_vocabulary_grew_from_25_to_33_with_no_duplicates():
    assert len(_VISUAL_STYLE_TERMS) == 33
    assert len(set(_VISUAL_STYLE_TERMS)) == 33
    english = [t for t in _VISUAL_STYLE_TERMS if t.isascii()]
    chinese = [t for t in _VISUAL_STYLE_TERMS if not t.isascii()]
    assert len(english) == 16
    assert len(chinese) == 17


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
    production matcher actually compares text. This is the exact check
    that caught the rejected "hyper-detailed texture rendering" draft."""
    sibling_groups = (
        _VAGUE_CRITERIA_TERMS + _VISUAL_TASK_DIRECTIVES
        + _VISUAL_SUBJECT_ANCHORS + _BOUNDARY_CRITERIA_TERMS)
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in sibling_groups:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains sibling term {term!r}")


def test_new_phrase_self_screen_has_no_internal_collision():
    all_new = NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    for i, a in enumerate(all_new):
        for j, b in enumerate(all_new):
            if i == j:
                continue
            assert a not in b, f"{a!r} unexpectedly contains {b!r}"


def test_new_english_phrase_alone_seeds_without_a_hint_at_general_review_length():
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
        "The output should use high-fidelity render, ray-traced "
        "lighting, and cinema-grade color grading.")
    seeds = _seed_from_text(text)
    assert seeds
    source = seeds[0][0]
    assert source["candidateHints"]
    assert source["candidateHints"][0]["subject"] == {
        "criterionKind": "missing_task_anchor"}


def test_three_new_chinese_phrases_without_task_or_subject_seed_with_missing_task_anchor_hint():
    text = "效果需要高保真渲染、光线追踪光照与电影级调色。"
    seeds = _seed_from_text(text)
    assert seeds
    source = seeds[0][0]
    assert source["candidateHints"]
    assert source["candidateHints"][0]["subject"] == {
        "criterionKind": "missing_task_anchor"}


def test_new_english_phrases_with_task_directive_and_subject_do_not_get_a_hint():
    text = (
        "Generate a photo of a person using high-fidelity render, "
        "ray-traced lighting, and cinema-grade color grading.")
    seeds = _seed_from_text(text)
    assert seeds, "still expected a seed even when fully anchored"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert source.get("modelCandidateSkipReason") == "visual_task_anchors_present"


def test_new_chinese_phrases_with_task_directive_and_subject_do_not_get_a_hint():
    text = "生成一位人物的照片，使用高保真渲染、光线追踪光照与电影级调色。"
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
    assert any("33 phrases" in g and "Round 174" in g for g in gaps)


def test_gap_text_keeps_the_prior_round_156_count_in_the_chained_history():
    risks = load_risks()
    gaps = risks["VR-PROMPT-014"]["knownGaps"]
    assert any("25 phrases after Round 156" in g for g in gaps)


def test_risk_coverage_unchanged_by_a_vocabulary_only_expansion():
    risks = load_risks()
    coverage = risks["VR-PROMPT-014"]["currentCoverage"]
    assert coverage["L0_static"] == "none"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"
    assert coverage["V2_sandbox"] == "none"


def test_detector_mapping_count_is_unchanged_by_a_pure_vocabulary_round():
    assert len(load_detector_mappings()) == 156
