"""Round 163: semantic.prompt.ambiguous_operational_criteria
_VAGUE_CRITERIA_TERMS trigger-vocabulary expansion, first touch (standing
initiative #1).

Re-running the systematic trigger-tuple-size scan after Round 162 closed
`_ENCODING_INSTRUCTION_TERMS` (25->33) surfaced the same 25-phrase tier's
remaining two candidates: `_FIELD_CONTRACT_TERMS` ("Round 147", a second
touch) and `_VISUAL_STYLE_TERMS` ("Round 156", also a second touch, per its
own "First touch of this tuple" comment recording that Round 156 WAS its
first touch). `_VAGUE_CRITERIA_TERMS` -- the sibling OR-trigger half of the
same concatenated `triggers=_VAGUE_CRITERIA_TERMS + _VISUAL_STYLE_TERMS`
expression powering `extract_ambiguous_operational_criteria` -- carries no
such comment: a genuine first touch, extending the same tie-break precedent
Rounds 137/159/160/161/162 used.

This extractor's shape differs from every prior vocabulary-expansion round
in this series: it sets `allow_without_trigger=True`, so it seeds on EVERY
sufficiently-long prompt regardless of whether any `_VAGUE_CRITERIA_TERMS`/
`_VISUAL_STYLE_TERMS` term appears at all, and its metadata builder
(`_ambiguity_metadata`) computes whole-document term counts directly (no
`_scoped_gap_count`/local-rule-window scoping). Confirmed interactively,
the cascade rungs relevant to `_VAGUE_CRITERIA_TERMS` are:
  1. A bare vague-criteria phrase anywhere in the prompt, with fewer than 2
     `_BOUNDARY_CRITERIA_TERMS` hits anywhere in the whole document, seeds
     with an `undefined_boundary` hint.
  2. The same phrase plus >=2 boundary-marker hits anywhere in the document
     (not window-scoped, unlike Rounds 160-162) seeds with no hint,
     `modelCandidatePolicy: "skip_without_catalog_hint"` /
     `modelCandidateSkipReason: "vague_criterion_has_local_boundary"`.
(A third rung -- prompts with zero vague-criteria/visual-style signal at
all -- falls through to a prompt-length-based fallback gate untouched by
this round's vocabulary change, and is not exercised by these tests.)

This round adds 4 concepts (8 phrases: 4 English + 4 Chinese) as
paraphrases of the same "vague operational criterion lacking a concrete
threshold, referent, example, or decision rule" trigger concept, taking
`_VAGUE_CRITERIA_TERMS` from 25 to 33 fixed phrases (17 English + 16
Chinese): `to your best judgment`/`凭你的判断`, `keep it succinct`/
`力求精炼`, `as polished as possible`/`尽善尽美`, `to a suitable degree`/
`适度处理`.

All eight final phrases were live-fire-grepped across `tests/`,
`evals/corpus/`, and `src/` (zero hits) and collision-screened in both
substring directions against `_VAGUE_CRITERIA_TERMS` itself, the sibling
`_VISUAL_STYLE_TERMS` OR-trigger group, `_BOUNDARY_CRITERIA_TERMS`,
`_VISUAL_TASK_DIRECTIVES`, and `_VISUAL_SUBJECT_ANCHORS`, plus
self-screened among the 8 new candidates -- using the exact unstripped
terms as stored, matching production matching exactly -- zero collisions
found. None of the new phrases were added to `_VAGUE_CRITERIA_BOUNDARY_
TERMS` since none are bare words at risk of a negation-prefix collision
(that guard exists only for "appropriate"/"reasonable"/"sufficiently").
VR-PROMPT-014's `knownGaps` already carries a Round-156 bullet disclosing
`_VISUAL_STYLE_TERMS`'s own count; the new bullet appended this round is
scoped explicitly to the separate `_VAGUE_CRITERIA_TERMS` tuple so the two
disclosures don't conflate two different vocabularies sharing one risk
mapping. No `detector_mappings.json` change: pure vocabulary expansion of
an existing signal-level finding type, not a new detector.
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

import pytest

NEW_ENGLISH_PHRASES = [
    "to your best judgment", "keep it succinct", "as polished as possible",
    "to a suitable degree",
]
NEW_CHINESE_PHRASES = [
    "凭你的判断", "力求精炼", "尽善尽美", "适度处理",
]
ORIGINAL_PHRASES = [
    "appropriate", "reasonable", "as needed", "when necessary",
    "sufficiently", "high quality", "brief", "concise", "detailed",
    "comprehensive", "complex", "long content", "content is long",
    "适当", "合理", "酌情", "必要时", "尽量", "足够", "高质量", "简洁",
    "详细", "详尽", "复杂", "内容较长",
]


def _seed_from_text(text):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    return extract_ambiguous_operational_criteria(
        review_to_dict(review), file_bytes)


def test_vocabulary_grew_from_25_to_33_with_no_duplicates():
    assert len(_VAGUE_CRITERIA_TERMS) == 33
    assert len(set(_VAGUE_CRITERIA_TERMS)) == 33
    english = [t for t in _VAGUE_CRITERIA_TERMS if t.isascii()]
    chinese = [t for t in _VAGUE_CRITERIA_TERMS if not t.isascii()]
    assert len(english) == 17
    assert len(chinese) == 16


def test_original_phrases_are_all_still_present():
    for phrase in ORIGINAL_PHRASES:
        assert phrase in _VAGUE_CRITERIA_TERMS


def test_new_phrase_is_not_a_redundant_superset_of_an_existing_entry():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in ORIGINAL_PHRASES:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains original term {term!r}")
            assert phrase not in term, (
                f"original term {term!r} unexpectedly contains {phrase!r}")


def test_new_phrase_shares_no_substring_with_the_visual_style_sibling_group():
    """Checked against the EXACT terms as stored (no `.strip()`), matching
    how the production matcher (`text.count`/`in`, which never strips)
    actually compares text."""
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in _VISUAL_STYLE_TERMS:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains visual-style term "
                f"{term!r}")


def test_new_phrase_does_not_touch_the_other_related_groups():
    other_groups = (_BOUNDARY_CRITERIA_TERMS + _VISUAL_TASK_DIRECTIVES
                     + _VISUAL_SUBJECT_ANCHORS)
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
def test_new_english_phrase_alone_seeds_with_an_undefined_boundary_hint(
        phrase):
    seeds = _seed_from_text(f"Respond {phrase} in your answer to this task.")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    hints = seeds[0][0]["candidateHints"]
    assert hints
    assert hints[0]["subject"]["criterionKind"] == "undefined_boundary"


@pytest.mark.parametrize("phrase", NEW_CHINESE_PHRASES)
def test_new_chinese_phrase_alone_seeds_with_an_undefined_boundary_hint(
        phrase):
    seeds = _seed_from_text(f"请{phrase}地回答这个问题，不要遗漏任何要点。")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    hints = seeds[0][0]["candidateHints"]
    assert hints
    assert hints[0]["subject"]["criterionKind"] == "undefined_boundary"


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES)
def test_new_english_phrase_with_two_boundary_markers_seeds_without_a_hint(
        phrase):
    seeds = _seed_from_text(
        f"Respond {phrase}, using at least 3 items and at most 200 words.")
    assert seeds, f"expected {phrase!r} to still trigger a seed"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert (source.get("modelCandidateSkipReason")
            == "vague_criterion_has_local_boundary")


@pytest.mark.parametrize("phrase", NEW_CHINESE_PHRASES)
def test_new_chinese_phrase_with_two_boundary_markers_seeds_without_a_hint(
        phrase):
    seeds = _seed_from_text(
        f"请{phrase}地回答，至少使用3条要点，字数至多200字。")
    assert seeds, f"expected {phrase!r} to still trigger a seed"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert (source.get("modelCandidateSkipReason")
            == "vague_criterion_has_local_boundary")


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES)
def test_new_phrase_increments_the_vague_criterion_count(phrase):
    text = f"{phrase} now." if phrase.isascii() else f"{phrase}。"
    metadata = _ambiguity_metadata(text)
    assert metadata["vagueCriterionCount"] >= 1


def test_gap_text_discloses_the_new_fixed_count():
    risks = load_risks()
    gaps = risks["VR-PROMPT-014"]["knownGaps"]
    assert any("33 phrases" in g and "Round 163" in g for g in gaps)


def test_gap_text_keeps_the_prior_visual_style_gap_disclosure_distinct():
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
