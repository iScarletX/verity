"""Round 164: semantic.prompt.attention_dilution _ATTENTION_STRUCTURE_TERMS
trigger-vocabulary expansion, second touch (standing initiative #1).

Re-running the systematic trigger-tuple-size scan after Round 163 closed
`_VAGUE_CRITERIA_TERMS` (25->33) found that every primary single-trigger
tuple discovered by the `triggers=` scan now carries at least one prior
"Round N" touch comment -- the first-touch tie-break precedent Rounds
137/159-163 used has run out of untouched candidates. Several tuples in
this series have already been touched more than once (e.g. `_AUTONOMY_
TERMS` in Rounds 137 and 151, `_EXAMPLE_TERMS` in Rounds 140 and 150,
`_MULTI_TURN_TERMS` in Rounds 139 and 158, `_TOOL_CALL_TERMS` in Rounds
138 and 153), so the established continuation once first-touch candidates
are exhausted is to pick the globally sparsest tuple and add another
touch. `_ATTENTION_STRUCTURE_TERMS` (20 phrases, touched once in Round
141) is now the sparsest tuple in the whole scan.

This extractor's shape (`extract_attention_dilution`) is a bare
`_whole_prompt_seed` on `_ATTENTION_STRUCTURE_TERMS` alone (no AND-gate
partner) -- unlike Round 163's `allow_without_trigger=True` shape, this
one still requires at least one structure term to seed at all. Its
metadata builder (`_attention_dilution_metadata`) counts
`structureSignalCount` purely informationally: the candidate-hint gate
(`_attention_dilution_candidate_hints`) only checks document shape
(`promptLineCount>=12`, `promptCharacterCount>=500`,
`criticalRuleLineIndex` positioned in the back third of the document) and
`hierarchySignalCount==0` (driven by the separately-gated
`_ATTENTION_HIERARCHY_TERMS`, untouched by this round). Confirmed
interactively, the three cascade rungs relevant to this tuple are:
  1. A bare new-vocabulary phrase in a short/unstructured prompt seeds
     with no hint, `modelCandidatePolicy: "skip_without_catalog_hint"` /
     `modelCandidateSkipReason: "attention_hierarchy_present_or_not_
     buried"`.
  2. The same phrase in a long document (>=12 lines, >=500 chars) with a
     "critical rule" buried in the back third and zero hierarchy-term
     hits seeds with a `buried_critical_rule` hint.
  3. The same long-document setup plus one hierarchy term (e.g.
     "priority") present anywhere seeds with no hint, the same skip
     reason as rung 1 (the hierarchy signal suppresses the hint).

This round adds 4 concepts (8 phrases: 4 English + 4 Chinese) as
paraphrases of the same "large document structure with a
background/appendix/reference/critical-rule section" trigger concept,
taking `_ATTENTION_STRUCTURE_TERMS` from 20 to 28 fixed phrases (15
English + 13 Chinese): `background context`/`背景信息`, `sprawling
multi-section document`/`篇幅冗长的多章节文档`, `crucial requirement`/
`关键要求`, `unwieldy documentation bundle`/`臃肿的文档合集`.

All eight final phrases were live-fire-grepped across `tests/`,
`evals/corpus/`, and `src/` (one incidental hit -- unrelated generic
filler text in a different finding type's test, `test_round60_
semantic_recall.py`, which does not exercise `attention_dilution` at
all) and collision-screened in both substring directions against
`_ATTENTION_STRUCTURE_TERMS` itself, the metadata-only
`_ATTENTION_HIERARCHY_TERMS`/`_ATTENTION_REPETITION_TERMS` groups, plus
self-screened among the 8 new candidates -- using the exact unstripped
terms as stored, matching production matching exactly -- zero collisions
found. VR-PROMPT-025's existing Round-141 knownGaps bullet was updated in
place (not appended as a second bullet), chaining the count history,
mirroring the exact convention Round 151 used for `_AUTONOMY_TERMS`'s
own second touch on VR-PROMPT-012. No `detector_mappings.json` change:
pure vocabulary expansion of an existing signal-level finding type, not a
new detector.
"""
from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (_ATTENTION_HIERARCHY_TERMS,
                                      _ATTENTION_REPETITION_TERMS,
                                      _ATTENTION_STRUCTURE_TERMS,
                                      _attention_dilution_metadata,
                                      extract_attention_dilution)
from verity.standards import load_detector_mappings, load_risks

import pytest

NEW_ENGLISH_PHRASES = [
    "background context", "sprawling multi-section document",
    "crucial requirement", "unwieldy documentation bundle",
]
NEW_CHINESE_PHRASES = [
    "背景信息", "篇幅冗长的多章节文档", "关键要求", "臃肿的文档合集",
]
ORIGINAL_PHRASES = [
    "## background", "## appendix", "background material", "appendix",
    "long prompt", "reference material", "critical rule", "背景材料",
    "附录", "长提示词", "参考资料", "关键规则",
    "supporting material", "extended documentation",
    "extensive instructions", "supplementary notes",
    "支持性材料", "扩展文档", "详尽指令", "补充说明",
]


def _seed_from_text(text):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    return extract_attention_dilution(review_to_dict(review), file_bytes)


def _long_buried_rule_text(phrase, extra_hierarchy_line=""):
    lines = [f"line {i} filler padding padding padding padding"
              for i in range(1, 16)]
    header = f"Doc mentions {phrase} here."
    if extra_hierarchy_line:
        header += f" {extra_hierarchy_line}"
    lines.insert(0, header)
    lines.append(
        "critical rule: do the thing exactly as specified without "
        "deviation at all")
    return "\n".join(lines)


def test_vocabulary_grew_from_20_to_28_with_no_duplicates():
    """Round 184 touched `_ATTENTION_STRUCTURE_TERMS` again (28->36), so
    this now asserts only Round 164's own historical diff -- see
    test_round184_attention_structure_vocabulary_expansion.py for the
    current-total assertion."""
    round_164_state = ORIGINAL_PHRASES + NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    assert len(round_164_state) == 28
    assert len(set(round_164_state)) == 28
    for phrase in round_164_state:
        assert phrase in _ATTENTION_STRUCTURE_TERMS
    english = [t for t in round_164_state if t.isascii()]
    chinese = [t for t in round_164_state if not t.isascii()]
    assert len(english) == 15
    assert len(chinese) == 13


def test_original_phrases_are_all_still_present():
    for phrase in ORIGINAL_PHRASES:
        assert phrase in _ATTENTION_STRUCTURE_TERMS


def test_new_phrase_is_not_a_redundant_superset_of_an_existing_entry():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in ORIGINAL_PHRASES:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains original term {term!r}")
            assert phrase not in term, (
                f"original term {term!r} unexpectedly contains {phrase!r}")


def test_new_phrase_shares_no_substring_with_the_metadata_only_groups():
    """Checked against the EXACT terms as stored (no `.strip()`), matching
    how the production matcher (`text.count`/`in`, which never strips)
    actually compares text."""
    other_groups = _ATTENTION_HIERARCHY_TERMS + _ATTENTION_REPETITION_TERMS
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
    seeds = _seed_from_text(f"This prompt mentions {phrase} somewhere.")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert (source.get("modelCandidateSkipReason")
            == "attention_hierarchy_present_or_not_buried")


@pytest.mark.parametrize("phrase", NEW_CHINESE_PHRASES)
def test_new_chinese_phrase_alone_seeds_without_a_hint(phrase):
    seeds = _seed_from_text(f"这份提示词中提到了{phrase}。")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert (source.get("modelCandidateSkipReason")
            == "attention_hierarchy_present_or_not_buried")


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES)
def test_new_phrase_with_buried_critical_rule_and_no_hierarchy_seeds_with_a_hint(
        phrase):
    text = _long_buried_rule_text(phrase)
    seeds = _seed_from_text(text)
    assert seeds, f"expected {phrase!r} to trigger a seed"
    hints = seeds[0][0]["candidateHints"]
    assert hints
    assert hints[0]["subject"]["dilutionKind"] == "buried_critical_rule"


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES)
def test_new_phrase_with_buried_critical_rule_and_a_hierarchy_term_seeds_without_a_hint(
        phrase):
    text = _long_buried_rule_text(phrase, "This is the priority summary.")
    seeds = _seed_from_text(text)
    assert seeds, f"expected {phrase!r} to still trigger a seed"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert (source.get("modelCandidateSkipReason")
            == "attention_hierarchy_present_or_not_buried")


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES)
def test_new_phrase_increments_the_structure_signal_count(phrase):
    text = f"{phrase} now." if phrase.isascii() else f"{phrase}。"
    metadata = _attention_dilution_metadata(text)
    assert metadata["structureSignalCount"] >= 1


def test_plain_prompt_without_any_structure_term_does_not_seed():
    seeds = _seed_from_text(
        "Please write a haiku about the ocean waves at sunset.")
    assert seeds == []


def test_gap_text_discloses_the_new_fixed_count():
    risks = load_risks()
    gaps = risks["VR-PROMPT-025"]["knownGaps"]
    assert any("28 phrases" in g and "Round 164" in g for g in gaps)


def test_gap_text_keeps_the_prior_round_141_count_in_the_chained_history():
    risks = load_risks()
    gaps = risks["VR-PROMPT-025"]["knownGaps"]
    assert any("20 phrases after Round 141" in g for g in gaps)


def test_risk_coverage_unchanged_by_a_vocabulary_only_expansion():
    risks = load_risks()
    coverage = risks["VR-PROMPT-025"]["currentCoverage"]
    assert coverage["L0_static"] == "none"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"
    assert coverage["V2_sandbox"] == "none"


def test_detector_mapping_count_is_unchanged_by_a_pure_vocabulary_round():
    assert len(load_detector_mappings()) == 156
