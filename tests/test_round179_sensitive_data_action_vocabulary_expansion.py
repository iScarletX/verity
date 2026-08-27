"""Round 179: semantic.prompt.sensitive_data_handling_gap
_SENSITIVE_DATA_ACTION_TERMS trigger-vocabulary expansion, second touch
(standing initiative #1).

Re-running the systematic trigger-tuple-size scan after Round 178 closed
the two-way tie (`_AUTONOMY_TERMS` vs `_SENSITIVE_DATA_ACTION_TERMS`) in
favor of `_AUTONOMY_TERMS` (oldest last-touch) leaves
`_SENSITIVE_DATA_ACTION_TERMS` as the sole sparsest primary trigger tuple
at 26 phrases, with no remaining tie -- it is selected outright.

This is the SECOND touch of `_SENSITIVE_DATA_ACTION_TERMS` (Round 157 was
the first). `extract_sensitive_data_handling_gap` still requires BOTH
`_SENSITIVE_DATA_TERMS` AND `_SENSITIVE_DATA_ACTION_TERMS`
(`require_all_groups=(_SENSITIVE_DATA_TERMS, _SENSITIVE_DATA_ACTION_
TERMS)`, no `allow_without_trigger`) -- unchanged by this round. The
sibling `_SENSITIVE_DATA_TERMS` (Round 131's own tuple) and the
finer-grained metadata-only subsets (`_SENSITIVE_OUTBOUND_ACTION_TERMS`/
`_SENSITIVE_COLLECTION_ACTION_TERMS`) are untouched, per the established
methodology of only touching the PRIMARY trigger tuple.

Both halves of the standing second-touch regression rule apply and were
verified/fixed this round:
(a) `tests/test_round157_sensitive_data_action_vocabulary_expansion.py`'s
    `test_vocabulary_grew_from_18_to_26_with_no_duplicates` asserted
    `len(_SENSITIVE_DATA_ACTION_TERMS) == 26` -- a stale exact-total
    check. Rewritten to assert only Round 157's own historical diff via a
    `round_157_state = ORIGINAL_PHRASES + NEW_ENGLISH_PHRASES +
    NEW_CHINESE_PHRASES` list, forward-referencing this file for the
    current-total assertion. Re-ran
    `test_round157_sensitive_data_action_vocabulary_expansion.py`
    standalone after the fix: 41/41 passed.
(b) `VR-PROMPT-020`'s dedicated action-vocabulary `knownGaps` bullet
    (distinct from Round 131's own data-classification bullet, per Round
    157's own design note) was checked by Round 157's own
    `test_gap_text_discloses_the_new_fixed_count` /
    `test_gap_text_still_discloses_round_131s_data_classification_
    disclosure`, which inspect the literal substrings "26 phrases"/"Round
    157" (own bullet) and "39 terms"/"Round 131" (the OTHER, untouched
    bullet). The action-vocabulary bullet was rewritten in place to
    preserve "26 phrases"/"Round 157" inside the new chained-history
    sentence alongside this round's own "34 phrases"/"Round 179"
    disclosure; the unrelated data-classification bullet was left
    untouched.

This round adds 4 concepts (8 phrases: 4 English + 4 Chinese) as further
paraphrases of the same "perform an action on the sensitive data" trigger
concept, taking `_SENSITIVE_DATA_ACTION_TERMS` from 26 to 34 fixed
phrases (17 English + 17 Chinese): `compile the information into a
report`/`将信息汇总成报告`, `cross-reference the records with another
database`/`将记录与另一数据库进行交叉核对`, `duplicate the records into a
backup`/`将记录复制备份`, `aggregate the data across multiple
sources`/`跨多个来源整合数据`.

None of the 8 new phrases were added to the finer-grained
`_SENSITIVE_OUTBOUND_ACTION_TERMS`/`_SENSITIVE_COLLECTION_ACTION_TERMS`
metadata subsets (both untouched) -- so, exactly as Round 157 already
established for its own new phrases, a prompt containing only a new
action phrase (no bare "send"/"collect"/etc.) reports
`outboundDisclosureSignalCount == 0` and `collectionStorageSignalCount ==
0`, meaning only the unconditional "authorization" candidate hint can
fire for it, not the "redaction"/"minimization"/"retention" hints. This
was reconfirmed empirically for all 8 new phrases before writing these
tests.

All eight final phrases were live-fire-grepped across `tests/`, `evals/`,
`src/`, `standards/`, and `docs/` (zero hits) and collision-screened in
both substring directions against every group feeding this extractor
(`_SENSITIVE_DATA_ACTION_TERMS`, `_SENSITIVE_DATA_TERMS`,
`_SENSITIVE_DATA_CONTROL_TERMS`, `_SENSITIVE_MINIMIZATION_TERMS`,
`_SENSITIVE_REDACTION_TERMS`, `_SENSITIVE_AUTHORIZATION_TERMS`,
`_SENSITIVE_RETENTION_TERMS`, `_SENSITIVE_OUTBOUND_ACTION_TERMS`,
`_SENSITIVE_COLLECTION_ACTION_TERMS`), plus self-screened among the 8 new
candidates -- using the exact unstripped terms as stored (matching how
the production matcher actually compares text), zero collisions found.
All 4 English candidates were also confirmed all-lowercase per the
casing-bug lesson caught in Round 176. Still a fixed, finite set,
disclosed honestly in the updated knownGaps text. No
`detector_mappings.json` change: this is a pure vocabulary expansion of
an existing signal-level finding type, not a new detector.
"""
import pytest

from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (_SENSITIVE_AUTHORIZATION_TERMS,
                                      _SENSITIVE_DATA_ACTION_TERMS,
                                      _SENSITIVE_DATA_CONTROL_TERMS,
                                      _SENSITIVE_DATA_TERMS,
                                      _SENSITIVE_MINIMIZATION_TERMS,
                                      _SENSITIVE_RETENTION_TERMS,
                                      _SENSITIVE_REDACTION_TERMS,
                                      _SENSITIVE_COLLECTION_ACTION_TERMS,
                                      _SENSITIVE_OUTBOUND_ACTION_TERMS,
                                      _sensitive_data_metadata,
                                      extract_sensitive_data_handling_gap)
from verity.standards import load_detector_mappings, load_risks

NEW_ENGLISH_PHRASES = [
    "compile the information into a report",
    "cross-reference the records with another database",
    "duplicate the records into a backup",
    "aggregate the data across multiple sources",
]
NEW_CHINESE_PHRASES = [
    "将信息汇总成报告", "将记录与另一数据库进行交叉核对",
    "将记录复制备份", "跨多个来源整合数据",
]
ROUND_157_STATE = [
    "collect", "store", "retain", "send", "share", "display", "output",
    "process", "summarize", "收集", "存储", "保留", "发送", "共享",
    "展示", "输出", "处理", "总结",
    "transmit the information externally", "forward the details to a third party",
    "log this information for later use", "archive the records long term",
    "对外传输相关信息", "将信息转发给第三方",
    "将这些信息记录下来备查", "将记录长期归档保存",
]


def _seed_from_text(text):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    return extract_sensitive_data_handling_gap(
        review_to_dict(review), file_bytes)


def test_vocabulary_grew_from_26_to_34_with_no_duplicates():
    assert len(ROUND_157_STATE) == 26
    round_179_state = ROUND_157_STATE + NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    assert len(round_179_state) == 34
    assert len(set(round_179_state)) == 34
    assert len(_SENSITIVE_DATA_ACTION_TERMS) == 34
    for phrase in round_179_state:
        assert phrase in _SENSITIVE_DATA_ACTION_TERMS
    english = [t for t in _SENSITIVE_DATA_ACTION_TERMS if t.isascii()]
    chinese = [t for t in _SENSITIVE_DATA_ACTION_TERMS if not t.isascii()]
    assert len(english) == 17
    assert len(chinese) == 17


def test_round_157_phrases_are_all_still_present():
    for phrase in ROUND_157_STATE:
        assert phrase in _SENSITIVE_DATA_ACTION_TERMS


def test_new_phrase_is_not_a_redundant_superset_of_an_existing_entry():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in ROUND_157_STATE:
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


def test_new_phrase_shares_no_substring_with_a_sibling_gate_group():
    """Checked against the EXACT terms as stored (no `.strip()`), matching
    how the production matcher (`text.count`/`_sum_term_hits`, which never
    strips) actually compares text."""
    sibling_groups = (
        _SENSITIVE_DATA_TERMS + _SENSITIVE_DATA_CONTROL_TERMS
        + _SENSITIVE_MINIMIZATION_TERMS + _SENSITIVE_REDACTION_TERMS
        + _SENSITIVE_AUTHORIZATION_TERMS + _SENSITIVE_RETENTION_TERMS
        + _SENSITIVE_OUTBOUND_ACTION_TERMS
        + _SENSITIVE_COLLECTION_ACTION_TERMS)
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in sibling_groups:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains sibling term {term!r}")


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


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES)
def test_new_phrase_does_not_increment_outbound_or_collection_subset_counts(
        phrase):
    """The finer-grained metadata-only subsets are deliberately untouched
    -- confirming only the unconditional "authorization" hint branch can
    fire for a genuinely new action phrase, exactly as Round 157
    established for its own phrases."""
    text = (f"Please {phrase} regarding personal information."
            if phrase.isascii() else f"请针对个人信息{phrase}。")
    metadata = _sensitive_data_metadata(text)
    assert metadata["outboundDisclosureSignalCount"] == 0
    assert metadata["collectionStorageSignalCount"] == 0


def test_plain_prompt_without_any_sensitive_action_term_does_not_seed():
    seeds = _seed_from_text(
        "The system must protect the user personal information at all "
        "times.")
    assert seeds == []


def test_gap_text_discloses_the_new_fixed_count():
    risks = load_risks()
    gaps = risks["VR-PROMPT-020"]["knownGaps"]
    assert any("34 phrases" in g for g in gaps)
    assert any("Round 179" in g for g in gaps)


def test_gap_text_keeps_the_prior_rounds_counts_in_the_chained_history():
    risks = load_risks()
    gaps = risks["VR-PROMPT-020"]["knownGaps"]
    assert any("26 phrases" in g and "Round 157" in g for g in gaps)


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
