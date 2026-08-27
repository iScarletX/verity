"""Round 157: semantic.prompt.sensitive_data_handling_gap
_SENSITIVE_DATA_ACTION_TERMS trigger-vocabulary expansion, first touch
(standing initiative #1).

Re-running the systematic trigger-tuple-size scan after Round 156 closed
`_VISUAL_STYLE_TERMS` surfaced `_SENSITIVE_DATA_ACTION_TERMS` as the new
true sparsest primary trigger tuple at only 18 phrases -- sparser than the
19-phrase `_MULTI_TURN_TERMS`.

`_SENSITIVE_DATA_ACTION_TERMS` is an AND-gate half: reading
`extract_sensitive_data_handling_gap` confirms
`triggers=_SENSITIVE_DATA_TERMS + _SENSITIVE_DATA_ACTION_TERMS`,
`require_all_groups=(_SENSITIVE_DATA_TERMS, _SENSITIVE_DATA_ACTION_TERMS)`
-- both a sensitive-data-kind term AND an action term must be present for
a seed to exist at all (no `allow_without_trigger`). This is a genuine
FIRST touch of `_SENSITIVE_DATA_ACTION_TERMS` itself: the sibling
`_SENSITIVE_DATA_TERMS` was expanded once before, in Round 131, and
`VR-PROMPT-020`'s existing knownGaps bullet about vocabulary size names
only that data-classification tuple ("39 terms after Round 131 ..."), not
the action tuple -- so this round appends its own new, distinct bullet
rather than rewriting Round 131's.

This round adds 4 concepts (8 phrases: 4 English + 4 Chinese) as
paraphrases of the same "perform an action on the sensitive data" trigger
concept, taking `_SENSITIVE_DATA_ACTION_TERMS` from 18 to 26 fixed phrases
(13 English + 13 Chinese): `transmit the information
externally`/`对外传输相关信息`, `forward the details to a third
party`/`将信息转发给第三方`, `log this information for later
use`/`将这些信息记录下来备查`, `archive the records long term`/`将记录长期归档保存`.

None of the 8 new phrases were added to the finer-grained
`_SENSITIVE_OUTBOUND_ACTION_TERMS`/`_SENSITIVE_COLLECTION_ACTION_TERMS`
metadata subsets (both untouched, per the established methodology of only
touching the PRIMARY trigger tuple) -- so a prompt containing only a new
action phrase (no bare "send"/"collect"/etc.) reports
`outboundDisclosureSignalCount == 0` and `collectionStorageSignalCount ==
0`, meaning only the unconditional "authorization" candidate hint can
fire for it, not the "redaction"/"minimization"/"retention" hints. This
was confirmed empirically for all 8 new phrases before writing these
tests.

All eight final phrases were live-fire-grepped across `tests/` and
`evals/corpus/` (zero hits) and collision-screened in both substring
directions against every group feeding this extractor
(`_SENSITIVE_DATA_ACTION_TERMS`, `_SENSITIVE_DATA_TERMS`,
`_SENSITIVE_DATA_CONTROL_TERMS`, `_SENSITIVE_MINIMIZATION_TERMS`), plus
self-screened among the 8 new candidates -- using the exact unstripped
terms as stored (matching how the production matcher actually compares
text), zero collisions found. Still a fixed, finite set, disclosed
honestly in the updated knownGaps text. No `detector_mappings.json`
change: this is a pure vocabulary expansion of an existing signal-level
finding type, not a new detector.
"""
import pytest

from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (_SENSITIVE_DATA_ACTION_TERMS,
                                      _SENSITIVE_DATA_CONTROL_TERMS,
                                      _SENSITIVE_DATA_TERMS,
                                      _SENSITIVE_MINIMIZATION_TERMS,
                                      _sensitive_data_metadata,
                                      extract_sensitive_data_handling_gap)
from verity.standards import load_detector_mappings, load_risks

NEW_ENGLISH_PHRASES = [
    "transmit the information externally",
    "forward the details to a third party",
    "log this information for later use",
    "archive the records long term",
]
NEW_CHINESE_PHRASES = [
    "对外传输相关信息", "将信息转发给第三方",
    "将这些信息记录下来备查", "将记录长期归档保存",
]
ORIGINAL_PHRASES = [
    "collect", "store", "retain", "send", "share", "display", "output",
    "process", "summarize", "收集", "存储", "保留", "发送", "共享",
    "展示", "输出", "处理", "总结",
]


def _seed_from_text(text):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    return extract_sensitive_data_handling_gap(
        review_to_dict(review), file_bytes)


def test_vocabulary_grew_from_18_to_26_with_no_duplicates():
    """Round 179 touched `_SENSITIVE_DATA_ACTION_TERMS` again (26->34), so
    this now asserts only Round 157's own historical diff -- see
    test_round179_sensitive_data_action_vocabulary_expansion.py for the
    current-total assertion."""
    round_157_state = ORIGINAL_PHRASES + NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    assert len(round_157_state) == 26
    assert len(set(round_157_state)) == 26
    for phrase in round_157_state:
        assert phrase in _SENSITIVE_DATA_ACTION_TERMS
    english = [t for t in round_157_state if t.isascii()]
    chinese = [t for t in round_157_state if not t.isascii()]
    assert len(english) == 13
    assert len(chinese) == 13


def test_original_phrases_are_all_still_present():
    for phrase in ORIGINAL_PHRASES:
        assert phrase in _SENSITIVE_DATA_ACTION_TERMS


def test_new_phrase_is_not_a_redundant_superset_of_an_existing_entry():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in ORIGINAL_PHRASES:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains original term {term!r}")
            assert phrase not in term, (
                f"original term {term!r} unexpectedly contains {phrase!r}")


def test_new_phrase_shares_no_substring_with_a_sibling_gate_group():
    """Checked against the EXACT terms as stored (no `.strip()`), matching
    how the production matcher (`text.count`/`_sum_term_hits`, which never
    strips) actually compares text."""
    sibling_groups = (
        _SENSITIVE_DATA_TERMS + _SENSITIVE_DATA_CONTROL_TERMS
        + _SENSITIVE_MINIMIZATION_TERMS)
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in sibling_groups:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains sibling term {term!r}")


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES)
def test_new_english_phrase_alone_without_a_data_kind_term_does_not_seed(
        phrase):
    seeds = _seed_from_text(f"Please {phrase}.")
    assert seeds == []


@pytest.mark.parametrize("phrase", NEW_CHINESE_PHRASES)
def test_new_chinese_phrase_alone_without_a_data_kind_term_does_not_seed(
        phrase):
    seeds = _seed_from_text(f"请{phrase}。")
    assert seeds == []


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES)
def test_new_english_phrase_seeds_with_an_authorization_hint_when_paired(
        phrase):
    seeds = _seed_from_text(
        f"Please {phrase} regarding the user personal information.")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    hints = seeds[0][0]["candidateHints"]
    assert hints
    assert hints[0]["subject"]["dataPolicyKind"] == "authorization"


@pytest.mark.parametrize("phrase", NEW_CHINESE_PHRASES)
def test_new_chinese_phrase_seeds_with_an_authorization_hint_when_paired(
        phrase):
    seeds = _seed_from_text(f"请针对用户的个人信息{phrase}。")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    hints = seeds[0][0]["candidateHints"]
    assert hints
    assert hints[0]["subject"]["dataPolicyKind"] == "authorization"


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES)
def test_new_english_phrase_with_authorization_control_seeds_without_a_hint(
        phrase):
    seeds = _seed_from_text(
        f"Please {phrase} regarding the user personal information, but "
        f"only if authorized.")
    assert seeds, f"expected {phrase!r} to still trigger a seed"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert (source.get("modelCandidateSkipReason")
            == "sensitive_data_controls_complete_or_action_unproven")


@pytest.mark.parametrize("phrase", NEW_CHINESE_PHRASES)
def test_new_chinese_phrase_with_authorization_control_seeds_without_a_hint(
        phrase):
    seeds = _seed_from_text(f"请针对用户的个人信息{phrase}，但仅在获得授权后进行。")
    assert seeds, f"expected {phrase!r} to still trigger a seed"
    source = seeds[0][0]
    assert "candidateHints" not in source
    assert source.get("modelCandidatePolicy") == "skip_without_catalog_hint"
    assert (source.get("modelCandidateSkipReason")
            == "sensitive_data_controls_complete_or_action_unproven")


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES)
def test_new_phrase_increments_the_data_action_signal_count(phrase):
    text = (f"Please {phrase} regarding personal information."
            if phrase.isascii() else f"请针对个人信息{phrase}。")
    metadata = _sensitive_data_metadata(text)
    assert metadata["dataActionSignalCount"] >= 1
    assert metadata["sensitiveDataSignalCount"] >= 1


def test_plain_prompt_without_any_sensitive_action_term_does_not_seed():
    seeds = _seed_from_text(
        "The system must protect the user personal information at all "
        "times.")
    assert seeds == []


def test_gap_text_discloses_the_new_fixed_count():
    risks = load_risks()
    gaps = risks["VR-PROMPT-020"]["knownGaps"]
    assert any("26 phrases" in g for g in gaps)
    assert any("Round 157" in g for g in gaps)


def test_gap_text_still_discloses_round_131s_data_classification_disclosure():
    risks = load_risks()
    gaps = risks["VR-PROMPT-020"]["knownGaps"]
    assert any("39 terms" in g for g in gaps)
    assert any("Round 131" in g for g in gaps)


def test_risk_coverage_unchanged_by_a_vocabulary_only_expansion():
    risks = load_risks()
    coverage = risks["VR-PROMPT-020"]["currentCoverage"]
    assert coverage["L0_static"] == "none"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "signal"
    assert coverage["V2_sandbox"] == "none"


def test_detector_mapping_count_is_unchanged_by_a_pure_vocabulary_round():
    assert len(load_detector_mappings()) == 156
