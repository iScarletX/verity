"""Round 184: semantic.prompt.attention_dilution _ATTENTION_STRUCTURE_TERMS
trigger-vocabulary expansion, third touch (standing initiative #1).

Re-running the systematic trigger-tuple-size scan after Round 183 closed
`_SOURCE_USE_TERMS` (28->36) found `_ATTENTION_STRUCTURE_TERMS`
(`VR-PROMPT-025`'s `extract_attention_dilution`, last touched Round 164) as
the new sole sparsest single primary-vocabulary tuple at 28 phrases -- no
tie to resolve this round.

This is the THIRD touch of `_ATTENTION_STRUCTURE_TERMS` (created
originally with 12 phrases, first expanded Round 141 to 20, second
expanded Round 164 to 28), so both halves of the standing second-touch
regression rule apply and were verified/fixed this round:
(a) `tests/test_round164_attention_structure_vocabulary_expansion.py`'s
    `test_vocabulary_grew_from_20_to_28_with_no_duplicates` asserted
    `len(_ATTENTION_STRUCTURE_TERMS) == 28` -- a stale exact-total check.
    Rewritten to assert only Round 164's own historical diff via a
    `round_164_state = ORIGINAL_PHRASES + NEW_ENGLISH_PHRASES +
    NEW_CHINESE_PHRASES` list, forward-referencing this file for the
    current-total assertion. (Round 141's own file was already converted
    to a historical-diff assertion by Round 164 and needs no further
    change.)
(b) `VR-PROMPT-025`'s `knownGaps` vocabulary bullet was checked by Round
    164's own `test_gap_text_discloses_the_new_fixed_count` and
    `test_gap_text_keeps_the_prior_round_141_count_in_the_chained_history`,
    which inspect the literal substrings "28 phrases"/"Round 164" and "20
    phrases after Round 141". The bullet was rewritten in place to
    preserve all of those substrings alongside this round's own "36
    phrases"/"Round 184" disclosure.

`extract_attention_dilution` is a bare `_whole_prompt_seed` on
`_ATTENTION_STRUCTURE_TERMS` alone (no AND-gate partner): any structure
phrase alone always produces a seed, but its candidate-hint gate
(`_attention_dilution_candidate_hints`) depends purely on document SHAPE
(`promptLineCount>=12`, `promptCharacterCount>=500`,
`criticalRuleLineIndex` positioned in the back third) and
`hierarchySignalCount==0` (driven by the separately-gated
`_ATTENTION_HIERARCHY_TERMS`/`_ATTENTION_REPETITION_TERMS`, both untouched
by this round) -- a distinct structural/positional-gate shape, unlike
Round 181's single-signal-then-cascade shape, Round 182's two-signal
AND-gate shape, or Round 183's three-branch sibling-group cascade. This
round adds 4 concepts (8 phrases: 4 English + 4 Chinese) as further
paraphrases of the same "large document structure with a
background/appendix/reference/critical-rule section" trigger concept,
taking `_ATTENTION_STRUCTURE_TERMS` from 28 to 36 fixed phrases (19
English + 17 Chinese): `hefty supplementary annex`/`篇幅厚重的附属说明`,
`voluminous instruction manual`/`内容庞杂的操作手册`, `sizable reference
dossier`/`体量庞大的参考档案`, `bulky exhibit of attached
materials`/`堆积如山的附件材料`.

All eight final phrases were live-fire-grepped across `tests/`, `evals/`,
`src/`, `standards/`, and `docs/` (zero hits) and collision-screened
programmatically in both substring directions against the full existing
28-phrase tuple, plus the two sibling metadata-only groups
(`_ATTENTION_HIERARCHY_TERMS`/`_ATTENTION_REPETITION_TERMS`), plus
self-screened among the 8 new candidates and confirmed all-lowercase per
the Round 176 casing lesson -- zero collisions found on the first attempt,
no design-time fix needed this round. Still a fixed, finite set,
disclosed honestly in the updated knownGaps text. No
`detector_mappings.json` change: this is a pure vocabulary expansion of an
existing signal-level finding type, not a new detector.
"""
import pytest

from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (_ATTENTION_HIERARCHY_TERMS,
                                      _ATTENTION_REPETITION_TERMS,
                                      _ATTENTION_STRUCTURE_TERMS,
                                      _attention_dilution_metadata,
                                      extract_attention_dilution)
from verity.standards import load_detector_mappings, load_risks

NEW_ENGLISH_PHRASES = [
    "hefty supplementary annex", "voluminous instruction manual",
    "sizable reference dossier", "bulky exhibit of attached materials",
]
NEW_CHINESE_PHRASES = [
    "篇幅厚重的附属说明", "内容庞杂的操作手册", "体量庞大的参考档案", "堆积如山的附件材料",
]
ROUND_164_STATE = [
    "## background", "## appendix", "background material", "appendix",
    "long prompt", "reference material", "critical rule", "背景材料",
    "附录", "长提示词", "参考资料", "关键规则",
    "supporting material", "extended documentation",
    "extensive instructions", "supplementary notes",
    "支持性材料", "扩展文档", "详尽指令", "补充说明",
    "background context", "sprawling multi-section document",
    "crucial requirement", "unwieldy documentation bundle",
    "背景信息", "篇幅冗长的多章节文档", "关键要求", "臃肿的文档合集",
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


def test_vocabulary_grew_from_28_to_36_with_no_duplicates():
    assert len(ROUND_164_STATE) == 28
    round_184_state = ROUND_164_STATE + NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    assert len(round_184_state) == 36
    assert len(set(round_184_state)) == 36
    assert len(_ATTENTION_STRUCTURE_TERMS) == 36
    for phrase in round_184_state:
        assert phrase in _ATTENTION_STRUCTURE_TERMS
    english = [t for t in _ATTENTION_STRUCTURE_TERMS if t.isascii()]
    chinese = [t for t in _ATTENTION_STRUCTURE_TERMS if not t.isascii()]
    assert len(english) == 19
    assert len(chinese) == 17


def test_round_164_phrases_are_all_still_present():
    for phrase in ROUND_164_STATE:
        assert phrase in _ATTENTION_STRUCTURE_TERMS


def test_new_phrase_is_not_a_redundant_superset_of_an_existing_entry():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in ROUND_164_STATE:
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
    assert any("36 phrases" in g and "Round 184" in g for g in gaps)


def test_gap_text_keeps_the_prior_rounds_counts_in_the_chained_history():
    risks = load_risks()
    gaps = risks["VR-PROMPT-025"]["knownGaps"]
    assert any("28 phrases after Round 164" in g for g in gaps)
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
