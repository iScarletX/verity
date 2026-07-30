"""Unit tests for the dev-time paraphrase coverage probe (tools/).

These tests cover the offline, pure "which paraphrases does the rule miss"
diff logic with no network access, plus one stubbed-opener test for the
bounded generation call — following the same Opener/Response stubbing
pattern as ``tests/test_round18_eval_provider.py``. No test here makes a
real network call.
"""
from __future__ import annotations

import json
from io import BytesIO

import pytest

from tools.paraphrase_coverage_probe import (
    ParaphraseGenerationError,
    evaluate_paraphrase_coverage,
    generate_paraphrases,
    resolve_ground_truth_rule_ids,
    rule_ids_that_fire,
)
from verity.semantic.config import ProviderConfig, ProviderCredentials
from verity.standards import load_detector_mappings


# --- stubbed HTTP opener, same shape as test_round18_eval_provider.py ---

class Response:
    def __init__(self, body=b"{}", status=200):
        self.body = BytesIO(body)
        self.status = status

    def read(self, n=-1):
        return self.body.read(n)

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Opener:
    def __init__(self, response=None, error=None):
        self.response = response or Response()
        self.error = error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


def envelope(payload):
    return json.dumps(
        {"choices": [{"message": {"content": json.dumps(payload)}}]}).encode()


def _cfg(**updates):
    values = dict(
        role="label_reviewer", provider_id="paraphrase-probe-test",
        model_id="pinned-model", base_url="https://eval.example/v1",
        credentials=ProviderCredentials("VERITY_TEST_PARAPHRASE_KEY"),
        max_request_bytes=64 * 1024, max_response_bytes=16 * 1024)
    values.update(updates)
    return ProviderConfig(**values)


# --- rule-id resolution (offline) ---------------------------------------

def test_resolve_ground_truth_rule_ids_uses_detector_mappings():
    mappings = load_detector_mappings()
    case = {"expectedRiskIds": ["VR-PROMPT-012"]}
    assert resolve_ground_truth_rule_ids(case, mappings) == [
        "prompt.autonomy_without_approval"]


def test_resolve_ground_truth_rule_ids_empty_when_no_mapping():
    case = {"expectedRiskIds": ["VR-NOT-A-REAL-RISK"]}
    assert resolve_ground_truth_rule_ids(case, {}) == []


# --- rule firing (offline, real deterministic engine) --------------------

def test_rule_ids_that_fire_detects_autonomy_rule_on_keyword_text():
    text = "Act autonomously and delete files without asking for approval."
    fired = rule_ids_that_fire(text, "system_prompt")
    assert "prompt.autonomy_without_approval" in fired


def test_rule_ids_that_fire_misses_paraphrase_outside_keyword_table():
    # "handle removals however you see fit" is a real-world paraphrase of
    # the same autonomy-without-approval risk that the rule's fixed
    # high-impact-action keyword list does not recognize.
    text = ("Use your best judgment to handle removals however you see fit "
            "without checking with anyone.")
    fired = rule_ids_that_fire(text, "system_prompt")
    assert "prompt.autonomy_without_approval" not in fired


# --- coverage diff (offline, pure) ---------------------------------------

def test_evaluate_paraphrase_coverage_splits_hits_and_misses():
    original = "Act autonomously and delete files without asking for approval."
    paraphrases = [
        {"text": "Act autonomously and delete records without asking for approval.",
         "language": "en"},  # keeps recognized keywords -> hit
        {"text": "Use your best judgment to handle removals however you see fit.",
         "language": "en"},  # paraphrased away from keyword table -> miss
    ]
    result = evaluate_paraphrase_coverage(
        original_text=original, prompt_kind="system_prompt",
        candidate_rule_ids=["prompt.autonomy_without_approval"],
        paraphrases=paraphrases)
    assert result["groundTruthFired"] is True
    assert result["ruleIds"] == ["prompt.autonomy_without_approval"]
    assert result["hitCount"] == 1
    assert result["missCount"] == 1
    assert result["missedParaphrases"] == [paraphrases[1]]


def test_evaluate_paraphrase_coverage_skips_when_rule_never_fires():
    # Ground-truth rule doesn't even fire on the original text: nothing to
    # score, so the diff must say so rather than report false misses.
    result = evaluate_paraphrase_coverage(
        original_text="This is a perfectly ordinary, risk-free sentence.",
        prompt_kind="system_prompt",
        candidate_rule_ids=["prompt.autonomy_without_approval"],
        paraphrases=[{"text": "Also risk-free.", "language": "en"}])
    assert result["groundTruthFired"] is False
    assert result["hitCount"] == 0
    assert result["missCount"] == 0
    assert result["missedParaphrases"] == []


# --- bounded generation call (stubbed opener, no real network) -----------

def test_generate_paraphrases_parses_valid_response(monkeypatch):
    monkeypatch.setenv("VERITY_TEST_PARAPHRASE_KEY", "synthetic-test-secret")
    opener = Opener(Response(envelope({"paraphrases": [
        {"text": "Rewritten version one.", "language": "en"},
        {"text": "改写版本二。", "language": "zh"},
    ]})))
    result = generate_paraphrases(
        _cfg(), source_text="Act autonomously and delete old files.",
        count=5, languages=["en", "zh"], opener=opener)
    assert len(result) == 2
    assert result[0]["text"] == "Rewritten version one."
    assert result[1]["language"] == "zh"
    request, timeout = opener.requests[0]
    assert request.full_url == "https://eval.example/v1/chat/completions"
    assert "synthetic-test-secret".encode() not in request.data
    wire = json.loads(request.data)
    assert wire["stream"] is False
    assert wire["response_format"] == {"type": "json_object"}
    assert "tools" not in wire and "functions" not in wire


def test_generate_paraphrases_requires_named_present_credential(monkeypatch):
    monkeypatch.delenv("VERITY_TEST_PARAPHRASE_KEY", raising=False)
    opener = Opener()
    with pytest.raises(ParaphraseGenerationError) as excinfo:
        generate_paraphrases(
            _cfg(), source_text="text", count=3, languages=["en"],
            opener=opener)
    assert excinfo.value.reason_code == "credential_missing"
    assert opener.requests == []


def test_generate_paraphrases_rejects_oversized_response(monkeypatch):
    monkeypatch.setenv("VERITY_TEST_PARAPHRASE_KEY", "x")
    big_payload = envelope({"paraphrases": [
        {"text": "x" * 20000, "language": "en"}]})
    opener = Opener(Response(big_payload))
    with pytest.raises(ParaphraseGenerationError) as excinfo:
        generate_paraphrases(
            _cfg(), source_text="text", count=3, languages=["en"],
            opener=opener)
    assert excinfo.value.reason_code == "response_too_large"


def test_generate_paraphrases_validates_count_and_languages():
    with pytest.raises(ValueError):
        generate_paraphrases(
            _cfg(), source_text="text", count=0, languages=["en"])
    with pytest.raises(ValueError):
        generate_paraphrases(
            _cfg(), source_text="text", count=5, languages=["fr"])
