"""Round 189: semantic.prompt.input_and_default_contract_gap
_INPUT_DEPENDENCY_TERMS trigger-vocabulary expansion, third touch
(standing initiative #1).

Re-running the systematic trigger-tuple-size scan after Round 188 closed
`_GROUNDING_TASK_TERMS` (30->38) left a three-way tie at 30 phrases:
`_INPUT_DEPENDENCY_TERMS` (last touched Round 166), `_ERROR_RESPONSE_TERMS`
(Round 167), `_BUDGET_PRESSURE_TERMS` (Round 168). Applying the standing
oldest-last-touch tie-break rule, Round 166 is the oldest, so this round
takes on `_INPUT_DEPENDENCY_TERMS` (`VR-PROMPT-016`'s
`extract_input_and_default_contract_gap`). The other two tied tuples
remain available untouched for future rounds.

**Shape.** A single-trigger-group finding type (no `require_all_groups`):
any input-dependency phrase alone always produces a seed. Its
candidate-hint cascade (`_input_contract_candidate_hints`) checks four
completeness groups in sequence and stops at the first gap found:
`requirednessSignalCount` (missing -> `missing_input` hint),
`defaultSignalCount` (missing -> `default_behavior` hint),
`invalidInputSignalCount`/`handlingSignalCount` (either missing ->
`invalid_input` hint), else no hint at all (not merely an empty list).
The two cascade rungs relevant to a pure vocabulary round are: (1) a bare
new phrase alone (no requiredness/default/invalid/handling term) seeds
with a `missing_input` hint, since the cascade stops at its first rung;
(2) the same phrase combined with one term from each of the four
completeness groups still seeds (the trigger still fired) but the
`candidateHints` key is absent from the seed dict.

**Change.** Added 4 concepts (8 phrases: 4 English + 4 Chinese) as
further paraphrases of the same "declared input dependency" trigger
concept: `externally provided identifier`/`外部提供的标识符`,
`caller-specified argument`/`调用方指定的参数`, `end-user submitted
content`/`终端用户提交的内容`, `third-party supplied dataset`/`第三方提供的数据集`.
This takes `_INPUT_DEPENDENCY_TERMS` from 30 to 38 fixed phrases (20
English + 18 Chinese). The four separately-gated completeness groups
(`_INPUT_REQUIREDNESS_TERMS`/`_INPUT_DEFAULT_TERMS`/`_INPUT_INVALID_TERMS`/
`_INPUT_HANDLING_TERMS`) remain untouched.

**Regression fix (standing second-touch rule).** Both halves applied:
(a) `tests/test_round166_input_contract_vocabulary_expansion.py`'s stale
exact-total check rewritten to assert only Round 166's own historical
diff via a `round_166_state` list, forward-referencing this file for the
current-total assertion. (b) `VR-PROMPT-016`'s vocabulary `knownGaps`
bullet rewritten in place, chaining the count history to "38 phrases
after Round 189, up from 30 phrases after Round 166, up from 22 phrases
after Round 135, up from 14 originally". Round 166's own
`test_gap_text_discloses_the_new_fixed_count` (checking for "30 phrases"
and "Round 166" substrings) still passes unmodified since both survive
verbatim inside the newly chained bullet, and its
`test_gap_text_keeps_the_prior_round_135_count_in_the_chained_history`
(checking for "22 phrases after Round 135") also still passes.

**Verification.** All 8 new phrases were live-fire-grepped across
`tests/`, `evals/`, `src/`, `standards/`, and `docs/` (zero hits) and
collision-screened programmatically in both substring directions against
the full existing 30-phrase `_INPUT_DEPENDENCY_TERMS` tuple and the four
gated completeness groups, plus self-screened among the 8 new candidates
and confirmed all-lowercase per the Round 176 casing lesson -- zero
collisions found. Interactively confirmed, mirroring Round 166's exact
fixture structure: each new phrase alone seeds with a `missing_input`
hint; the same phrase combined with full contract coverage still seeds
but with no `candidateHints` key; each increments `inputSignalCount`; the
plain-prompt baseline returns no seed. No `detector_mappings.json`
change: this is a pure vocabulary expansion of an existing signal-level
finding type, not a new detector.

**Tests.** 35 tests in this file (parametrize-expanded across the 8 new
phrases), plus the fixed Round 166 file. Combined regression run across
`test_round135_input_contract_vocabulary_expansion.py` +
`test_round166_input_contract_vocabulary_expansion.py` +
`test_round189_input_contract_vocabulary_expansion.py` confirms all pass.
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
                                      _input_contract_metadata,
                                      extract_input_and_default_contract_gap)
from verity.standards import load_detector_mappings, load_risks

NEW_ENGLISH_PHRASES = [
    "externally provided identifier", "caller-specified argument",
    "end-user submitted content", "third-party supplied dataset",
]
NEW_CHINESE_PHRASES = [
    "外部提供的标识符", "调用方指定的参数", "终端用户提交的内容", "第三方提供的数据集",
]
ROUND_166_STATE = [
    "required input", "input field", "input fields", "request parameter",
    "user provides", "user-provided", "form field", "request body",
    "必填输入", "输入字段", "请求参数", "用户提供", "表单字段", "请求体",
    "uploaded file", "query parameter", "path parameter", "attached file",
    "上传的文件", "查询参数", "路径参数", "附加文件",
    "submitted parameter", "incoming payload field", "user-supplied value",
    "client-submitted data",
    "提交的参数", "传入的负载字段", "用户填写的值", "客户端提交的数据",
]


def _seed_from_text(text):
    snapshot, file_bytes = intake_text(text, prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    return extract_input_and_default_contract_gap(
        review_to_dict(review), file_bytes)


def test_vocabulary_grew_from_30_to_38_with_no_duplicates():
    assert len(ROUND_166_STATE) == 30
    round_189_state = ROUND_166_STATE + NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES
    assert len(round_189_state) == 38
    assert len(set(round_189_state)) == 38
    assert len(_INPUT_DEPENDENCY_TERMS) == 38
    for phrase in round_189_state:
        assert phrase in _INPUT_DEPENDENCY_TERMS
    english = [t for t in _INPUT_DEPENDENCY_TERMS if t.isascii()]
    chinese = [t for t in _INPUT_DEPENDENCY_TERMS if not t.isascii()]
    assert len(english) == 20
    assert len(chinese) == 18


def test_round_166_phrases_are_all_still_present():
    for phrase in ROUND_166_STATE:
        assert phrase in _INPUT_DEPENDENCY_TERMS


def test_new_phrase_is_not_a_redundant_superset_of_an_existing_entry():
    for phrase in NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES:
        for term in ROUND_166_STATE:
            assert term not in phrase, (
                f"{phrase!r} unexpectedly contains original term {term!r}")
            assert phrase not in term, (
                f"original term {term!r} unexpectedly contains {phrase!r}")


def test_new_phrase_shares_no_substring_with_the_gated_completeness_groups():
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


def test_new_english_phrase_is_all_lowercase_to_match_lowercased_prompt_text():
    for phrase in NEW_ENGLISH_PHRASES:
        assert phrase == phrase.lower(), (
            f"{phrase!r} contains uppercase characters and would never "
            f"match the lowercased prompt text")


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


@pytest.mark.parametrize("phrase", NEW_ENGLISH_PHRASES + NEW_CHINESE_PHRASES)
def test_new_phrase_increments_the_input_signal_count(phrase):
    text = f"{phrase} now." if phrase.isascii() else f"{phrase}。"
    metadata = _input_contract_metadata(text)
    assert metadata["inputSignalCount"] >= 1


def test_plain_prompt_without_any_input_dependency_term_does_not_seed():
    seeds = _seed_from_text(
        "Please write a haiku about the ocean waves at sunset.")
    assert seeds == []


def test_gap_text_discloses_the_new_fixed_count():
    risks = load_risks()
    gaps = risks["VR-PROMPT-016"]["knownGaps"]
    assert any("38 phrases" in g and "Round 189" in g for g in gaps)


def test_gap_text_keeps_the_prior_rounds_counts_in_the_chained_history():
    risks = load_risks()
    gaps = risks["VR-PROMPT-016"]["knownGaps"]
    assert any("30 phrases after Round 166" in g for g in gaps)
    assert any("22 phrases after Round 135" in g for g in gaps)
    assert any("14 originally" in g for g in gaps)


def test_risk_coverage_unchanged_by_a_vocabulary_only_expansion():
    risks = load_risks()
    coverage = risks["VR-PROMPT-016"]["currentCoverage"]
    assert coverage["L0_static"] == "none"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "signal"
    assert coverage["V2_sandbox"] == "none"


def test_detector_mapping_count_is_unchanged_by_a_pure_vocabulary_round():
    assert len(load_detector_mappings()) == 156
