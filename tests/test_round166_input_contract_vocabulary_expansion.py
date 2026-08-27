"""Round 166: semantic.prompt.input_and_default_contract_gap
_INPUT_DEPENDENCY_TERMS trigger-vocabulary expansion, second touch
(standing initiative #1).

Re-running the systematic trigger-tuple-size scan after Round 165 closed
`_REASONING_TERMS` (21->29) confirmed the same exhaustion Round 164 first
identified: every primary single-trigger tuple discovered by the
`triggers=` scan carries at least one prior "Round N" touch comment, so
the established continuation is another touch on the globally sparsest
tuple. Three tuples tied at 22 phrases: this one (`_INPUT_DEPENDENCY_
TERMS`, Round 135), `_ERROR_RESPONSE_TERMS` (Round 143), and
`_BUDGET_PRESSURE_TERMS` (Round 154). Tie-broken by oldest last-touch
round (135 < 143 < 154), so `_INPUT_DEPENDENCY_TERMS` is picked over the
other two.

`extract_input_and_default_contract_gap` (`VR-PROMPT-016`) has a single
trigger group only (no `require_all_groups` AND-gate): any input-
dependency phrase alone always produces a seed. Its candidate-hint
cascade (`_input_contract_candidate_hints`) checks four completeness
groups in sequence and stops at the first gap found:
`requirednessSignalCount` (missing -> `missing_input` hint),
`defaultSignalCount` (missing -> `default_behavior` hint),
`invalidInputSignalCount`/`handlingSignalCount` (either missing ->
`invalid_input` hint), else no hint at all (not merely an empty list).
Confirmed interactively, the two cascade rungs relevant to this tuple are:
(1) a bare new phrase alone (no requiredness/default/invalid/handling
term) seeds with a `missing_input` hint, since the cascade stops at its
first rung; (2) the same phrase combined with one term from each of the
four completeness groups still seeds (the trigger still fired) but the
`candidateHints` key is absent from the seed dict.

This round adds 4 concepts (8 phrases: 4 English + 4 Chinese) as
paraphrases of the same "declared input dependency" trigger concept:
`submitted parameter`/`提交的参数`, `incoming payload field`/`传入的负载
字段`, `user-supplied value`/`用户填写的值`, `client-submitted data`/
`客户端提交的数据`. This takes `_INPUT_DEPENDENCY_TERMS` from 22 to 30
fixed phrases (16 English + 14 Chinese). One phrase was deliberately
adjusted during design: a first-considered ZH pairing "用户提供的值" for
"user-supplied value" was dropped because it contains the bare
`_INPUT_DEPENDENCY_TERMS` entry "用户提供" verbatim -- a redundant
superset adding zero recall -- replaced with "用户填写的值". The four
separately-gated completeness groups (`_INPUT_REQUIREDNESS_TERMS`/
`_INPUT_DEFAULT_TERMS`/`_INPUT_INVALID_TERMS`/`_INPUT_HANDLING_TERMS`)
remain untouched.

All eight final phrases were live-fire-grepped across `tests/`,
`evals/corpus/`, and `src/` (zero hits) and collision-screened in both
substring directions against `_INPUT_DEPENDENCY_TERMS` itself and the
four gated completeness groups, plus self-screened among the 8 new
candidates -- using unstripped terms as stored, matching production
matching exactly -- zero collisions found on the final draft.
`VR-PROMPT-016`'s existing Round-135 knownGaps bullet was updated in
place (not appended as a second bullet), chaining the count history,
mirroring the exact convention Rounds 151/164/165 used. Per that same
precedent, `tests/test_round135_input_contract_vocabulary_expansion.py`'s
`test_vocabulary_grew_from_14_to_22_with_no_duplicates` -- a now-stale
exact-total check -- was rewritten to assert only Round 135's own
historical diff via a `ROUND_135_STATE` list, forward-referencing this
file for the current-total assertion; its own gap-text substring check
(`"22 phrases"`/`"Round 135"`) still passes since both substrings survive
verbatim inside the newly chained bullet. No `detector_mappings.json`
change: pure vocabulary expansion of an existing signal-level finding
type, not a new detector.
"""
import pytest

from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import (_INPUT_DEFAULT_TERMS,
                                      _INPUT_DEPENDENCY_TERMS,
                                      _INPUT_HANDLING_TERMS,
                                      _INPUT_INVALID_TERMS,
                                      _INPUT_REQUIREDNESS_TERMS,
                                      extract_input_and_default_contract_gap)
from verity.standards import load_detector_mappings, load_risks

NEW_ENGLISH_PHRASES = [
    "submitted parameter", "incoming payload field", "user-supplied value",
    "client-submitted data",
]
NEW_CHINESE_PHRASES = [
    "提交的参数", "传入的负载字段", "用户填写的值", "客户端提交的数据",
]
ORIGINAL_PHRASES = [
    "required input", "input field", "input fields", "request parameter",
    "user provides", "user-provided", "form field", "request body",
    "必填输入", "输入字段", "请求参数", "用户提供", "表单字段", "请求体",
    "uploaded file", "query parameter", "path parameter", "attached file",
    "上传的文件", "查询参数", "路径参数", "附加文件",
]


def _seed_from_text(text):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    return extract_input_and_default_contract_gap(
        review_to_dict(review), file_bytes)


def test_vocabulary_grew_from_22_to_30_with_no_duplicates():
    """Round 189 touched `_INPUT_DEPENDENCY_TERMS` again (30->38), so this
    now asserts only Round 166's own historical diff -- see
    test_round189_input_contract_vocabulary_expansion.py for the
    current-total assertion."""
    round_166_state = ORIGINAL_PHRASES + NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    assert len(round_166_state) == 30
    assert len(set(round_166_state)) == 30
    for phrase in round_166_state:
        assert phrase in _INPUT_DEPENDENCY_TERMS
    english = [t for t in round_166_state if t.isascii()]
    chinese = [t for t in round_166_state if not t.isascii()]
    assert len(english) == 16
    assert len(chinese) == 14


def test_original_phrases_are_all_still_present():
    for phrase in ORIGINAL_PHRASES:
        assert phrase in _INPUT_DEPENDENCY_TERMS


def test_new_phrase_is_not_a_redundant_superset_of_an_existing_entry():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in ORIGINAL_PHRASES:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains original term {term!r}")
            assert phrase not in term, (
                f"original term {term!r} unexpectedly contains {phrase!r}")


def test_new_phrase_shares_no_substring_with_the_gated_completeness_groups():
    """Checked against the EXACT terms as stored (no `.strip()`), matching
    how the production matcher (`text.count`/`in`, which never strips)
    actually compares text -- guards against the exact defect caught
    during design (the "用户提供的值" / "用户提供" overlap)."""
    other_groups = (_INPUT_REQUIREDNESS_TERMS + _INPUT_DEFAULT_TERMS
                     + _INPUT_INVALID_TERMS + _INPUT_HANDLING_TERMS)
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


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES)
def test_new_phrase_alone_seeds_with_a_missing_input_hint(phrase):
    seeds = _seed_from_text(
        f"The task depends on the {phrase} provided by the user.")
    assert seeds, f"expected {phrase!r} to trigger a seed"
    hints = seeds[0][0]["candidateHints"]
    assert hints
    assert hints[0]["subject"]["gapKind"] == "missing_input"


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES)
def test_new_phrase_with_full_contract_coverage_seeds_without_a_hint(phrase):
    seeds = _seed_from_text(
        f"The task depends on the {phrase} provided by the user. This "
        f"field is required. If missing, ask the user for clarification. "
        f"If the input is malformed, return an error.")
    assert seeds, f"expected {phrase!r} to still trigger a seed"
    assert "candidateHints" not in seeds[0][0]


def test_plain_prompt_without_any_input_dependency_term_does_not_seed():
    seeds = _seed_from_text(
        "Please write a haiku about the ocean waves at sunset.")
    assert seeds == []


def test_gap_text_discloses_the_new_fixed_count():
    risks = load_risks()
    gaps = risks["VR-PROMPT-016"]["knownGaps"]
    assert any("30 phrases" in g and "Round 166" in g for g in gaps)


def test_gap_text_keeps_the_prior_round_135_count_in_the_chained_history():
    risks = load_risks()
    gaps = risks["VR-PROMPT-016"]["knownGaps"]
    assert any("22 phrases after Round 135" in g for g in gaps)


def test_risk_coverage_unchanged_by_a_vocabulary_only_expansion():
    risks = load_risks()
    coverage = risks["VR-PROMPT-016"]["currentCoverage"]
    assert coverage["L0_static"] == "none"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "signal"
    assert coverage["V2_sandbox"] == "none"


def test_detector_mapping_count_is_unchanged_by_a_pure_vocabulary_round():
    assert len(load_detector_mappings()) == 156
