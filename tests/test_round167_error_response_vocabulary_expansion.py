"""Round 167: semantic.prompt.error_response_contract_gap
_ERROR_RESPONSE_TERMS trigger-vocabulary expansion, second touch
(standing initiative #1).

Re-running the systematic trigger-tuple-size scan after Round 166 closed
`_INPUT_DEPENDENCY_TERMS` (22->30) surfaced a new tie at 22 phrases
between this tuple (`_ERROR_RESPONSE_TERMS`, Round 143) and
`_BUDGET_PRESSURE_TERMS` (Round 154). Applying the tied-size tie-break
rule established in Round 166 (oldest last-touch round wins, to spread
touches evenly rather than repeatedly favoring recently-touched tuples):
143 < 154, so `_ERROR_RESPONSE_TERMS` is picked over the other one.

`extract_error_response_contract_gap` (`VR-PROMPT-024`) has a two-part
entry gate followed by a priority-ordered, at-most-one-hint check
(`_error_response_candidate_hints`, built from `_error_response_
metadata`): (1) `errorResponseSignalCount > 0` (this tuple itself,
trivially true whenever the extractor seeds) AND `machineConsumerSignal
Count > 0` (the separate `_FIELD_MACHINE_CONSUMER_TERMS` group --
json/schema/parser/downstream/automation/api/request body/csv/database
and Chinese equivalents); if either is zero, no hint is returned at all.
(2) Past that entry gate, three independent gap conditions are checked in
a fixed order -- schema (`_ERROR_SCHEMA_TERMS`), recoverability
(`_ERROR_RECOVERY_TERMS`), format_consistency (`_ERROR_FORMAT_TERMS`) --
and at most one hint is returned (`hints[:1]`). Interactively confirmed
three rungs for every new phrase in both languages: (1) trigger alone, no
machine-consumer term -> seeds with no hint (entry gate fails); (2)
trigger + machine-consumer term, no schema/recovery/format term -> seeds
with the `schema` hint (the fixed-priority first gap); (3) trigger +
machine-consumer term + all three completeness signals -> seeds with no
hint (fully covered).

This round adds 4 concepts (8 phrases: 4 English + 4 Chinese) as
paraphrases of the same "declared failure/error-handling response"
trigger concept: `operation failed`/`操作失败`, `request rejected`/
`请求驳回`, `unable to fulfill`/`无法满足`, `flags the failure`/`标记失败`.
This takes `_ERROR_RESPONSE_TERMS` from 22 to 30 fixed phrases (15
English + 15 Chinese). One candidate was corrected during design: a
first-considered ZH pairing "拒绝继续" for a "declines to proceed" concept
contained the bare `_ERROR_RESPONSE_TERMS` entry "拒绝" verbatim -- a
redundant superset adding zero recall; a first-considered EN/ZH pair
"returns an error"/"返回错误" was dropped because "返回错误" is already a
verbatim entry in the unrelated `_INPUT_HANDLING_TERMS` group (a
different tuple gating a different extractor, `VR-PROMPT-016`) -- not a
same-tuple collision that any established test would catch, but avoided
anyway to keep the whole-file vocabulary maximally distinct and
unambiguous, and replaced with "flags the failure"/"标记失败" instead. The
separately-gated `_ERROR_SCHEMA_TERMS`/`_ERROR_RECOVERY_TERMS`/
`_ERROR_FORMAT_TERMS`/`_FIELD_MACHINE_CONSUMER_TERMS` groups remain
untouched.

All eight final phrases were live-fire-grepped across `tests/`,
`evals/corpus/`, and `src/` (zero hits for the final draft; the dropped
"返回错误" candidate's one hit inside `_INPUT_HANDLING_TERMS` is exactly
why it was replaced) and collision-screened programmatically in both
substring directions against `_ERROR_RESPONSE_TERMS` itself and the four
related groups (`_ERROR_SCHEMA_TERMS`/`_ERROR_RECOVERY_TERMS`/
`_ERROR_FORMAT_TERMS`/`_FIELD_MACHINE_CONSUMER_TERMS`), plus self-screened
among the 8 new candidates -- using unstripped terms as stored, matching
production matching exactly -- zero collisions found on the final draft.
`VR-PROMPT-024`'s existing Round-143 knownGaps bullet was updated in
place (not appended as a second bullet), chaining the count history,
mirroring the exact convention Rounds 151/164/165/166 used. Per that same
precedent, `tests/test_round143_error_response_vocabulary_expansion.py`'s
stale exact-total check was rewritten to assert only Round 143's own
historical diff, forward-referencing this file for the current-total
assertion. No `detector_mappings.json` change: pure vocabulary expansion
of an existing signal-level finding type, not a new detector.
"""
import pytest

from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (_ERROR_FORMAT_TERMS,
                                      _ERROR_RECOVERY_TERMS,
                                      _ERROR_RESPONSE_TERMS,
                                      _ERROR_SCHEMA_TERMS,
                                      _FIELD_MACHINE_CONSUMER_TERMS,
                                      extract_error_response_contract_gap)
from verity.standards import load_detector_mappings, load_risks

NEW_ENGLISH_PHRASES = [
    "operation failed", "request rejected", "unable to fulfill",
    "flags the failure",
]
NEW_CHINESE_PHRASES = [
    "操作失败", "请求驳回", "无法满足", "标记失败",
]
ORIGINAL_PHRASES = [
    "error response", "on error", "if invalid", "cannot complete",
    "permission denied", "refuse", "failure response", "错误响应", "出错时",
    "无效时", "无法完成", "权限不足", "拒绝", "失败响应",
    "unable to proceed", "access denied", "decline the request",
    "failure handling", "无法处理", "访问受限", "婉拒请求", "失败处理",
]


def _seed_from_text(text):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    return extract_error_response_contract_gap(
        review_to_dict(review), file_bytes)


def test_vocabulary_grew_from_22_to_30_with_no_duplicates():
    assert len(_ERROR_RESPONSE_TERMS) == 30
    assert len(set(_ERROR_RESPONSE_TERMS)) == 30
    english = [t for t in _ERROR_RESPONSE_TERMS if t.isascii()]
    chinese = [t for t in _ERROR_RESPONSE_TERMS if not t.isascii()]
    assert len(english) == 15
    assert len(chinese) == 15


def test_original_phrases_are_all_still_present():
    for phrase in ORIGINAL_PHRASES:
        assert phrase in _ERROR_RESPONSE_TERMS


def test_new_phrase_is_not_a_redundant_superset_of_an_existing_entry():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in ORIGINAL_PHRASES:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains original term {term!r}")
            assert phrase not in term, (
                f"original term {term!r} unexpectedly contains {phrase!r}")


def test_new_phrase_shares_no_substring_with_the_gated_sibling_groups():
    other_groups = (_ERROR_SCHEMA_TERMS + _ERROR_RECOVERY_TERMS
                     + _ERROR_FORMAT_TERMS + _FIELD_MACHINE_CONSUMER_TERMS)
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in other_groups:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains {term!r}")
            assert phrase not in term, (
                f"{term!r} unexpectedly contains {phrase!r}")


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
        f"If the request is invalid, the assistant will state {phrase} "
        f"to the user.")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    assert "candidateHints" not in seeds[0][0]


@pytest.mark.parametrize("phrase", NEW_CHINESE_PHRASES)
def test_new_chinese_phrase_alone_seeds_without_a_hint(phrase):
    seeds = _seed_from_text(f"如果请求无效，助手会向用户说明{phrase}。")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    assert "candidateHints" not in seeds[0][0]


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES)
def test_new_english_phrase_with_machine_consumer_seeds_with_schema_hint(
        phrase):
    seeds = _seed_from_text(
        f"If the request is invalid, the api will return {phrase} in the "
        f"json response body.")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    hints = seeds[0][0]["candidateHints"]
    assert hints
    assert hints[0]["subject"]["errorGapKind"] == "schema"


@pytest.mark.parametrize("phrase", NEW_CHINESE_PHRASES)
def test_new_chinese_phrase_with_machine_consumer_seeds_with_schema_hint(
        phrase):
    seeds = _seed_from_text(f"如果请求无效，接口会在 json 响应体中返回{phrase}。")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    hints = seeds[0][0]["candidateHints"]
    assert hints
    assert hints[0]["subject"]["errorGapKind"] == "schema"


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES)
def test_new_english_phrase_with_full_contract_coverage_seeds_without_a_hint(
        phrase):
    seeds = _seed_from_text(
        f"If the request is invalid, the api returns {phrase} as a json "
        f"error with an error code, a reason field, and tells the caller "
        f"to retry; all failures use the same format.")
    assert seeds, f"expected {phrase!r} to still trigger a seed"
    assert "candidateHints" not in seeds[0][0]


@pytest.mark.parametrize("phrase", NEW_CHINESE_PHRASES)
def test_new_chinese_phrase_with_full_contract_coverage_seeds_without_a_hint(
        phrase):
    seeds = _seed_from_text(
        f"如果请求无效，接口会返回{phrase}，包含错误码和原因字段，并提示调用方"
        f"重试；所有失败都使用相同格式。")
    assert seeds, f"expected {phrase!r} to still trigger a seed"
    assert "candidateHints" not in seeds[0][0]


def test_plain_prompt_without_any_error_response_term_does_not_seed():
    seeds = _seed_from_text(
        "Please write a haiku about the ocean waves at sunset.")
    assert seeds == []


def test_gap_text_discloses_the_new_fixed_count():
    risks = load_risks()
    gaps = risks["VR-PROMPT-024"]["knownGaps"]
    assert any("30 phrases" in g and "Round 167" in g for g in gaps)


def test_gap_text_keeps_the_prior_round_143_count_in_the_chained_history():
    risks = load_risks()
    gaps = risks["VR-PROMPT-024"]["knownGaps"]
    assert any("22 phrases after Round 143" in g for g in gaps)


def test_risk_coverage_unchanged_by_a_vocabulary_only_expansion():
    risks = load_risks()
    coverage = risks["VR-PROMPT-024"]["currentCoverage"]
    assert coverage["L0_static"] == "none"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "signal"
    assert coverage["V2_sandbox"] == "none"


def test_detector_mapping_count_is_unchanged_by_a_pure_vocabulary_round():
    assert len(load_detector_mappings()) == 156
