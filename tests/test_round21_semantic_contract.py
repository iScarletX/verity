"""Round 21 calibration-only validator coherence and config freezing."""
from jsonschema import Draft202012Validator

from verity.semantic.config import ProviderConfig, ProviderCredentials
from verity.semantic.eval_provider import (EVAL_ROLE_PROMPT_VERSION,
                                           _system_prompt)
from verity.semantic.provider import ProviderResponse
from verity.semantic.schemas import VALIDATION_RESULT_SCHEMA
from verity.semantic_quality import (_selection_gate,
                                     evaluate_semantic_model_quality)
from test_round18_semantic_quality import (Generator, Validator, configs,
                                           decisions_for_split)


def _errors(decision, reasons):
    value = {"candidateId": "c", "decision": decision,
             "reasonCodes": reasons}
    return list(Draft202012Validator(VALIDATION_RESULT_SCHEMA).iter_errors(value))


def test_coherent_validator_decision_reasons_are_accepted():
    assert not _errors("confirmed", ["evidence_supports_claim"])
    assert not _errors("rejected", ["evidence_contradicts_claim"])
    assert not _errors("rejected", ["candidate_out_of_scope"])
    assert not _errors("insufficient_evidence", ["not_enough_evidence"])


def test_contradictory_or_empty_validator_reasons_are_rejected():
    assert _errors("confirmed", ["evidence_contradicts_claim"])
    assert _errors("confirmed", ["evidence_supports_claim",
                                  "not_enough_evidence"])
    assert _errors("rejected", ["evidence_supports_claim"])
    assert _errors("insufficient_evidence", ["evidence_supports_claim"])
    assert _errors("confirmed", [])
    assert _errors("rejected", ["candidate_claim_unclear"])
    assert _errors("confirmed", ["evidence_supports_claim",
                                  "evidence_supports_claim"])


def test_eval_validator_prompt_states_materiality_and_coherence_boundary():
    prompt = _system_prompt("validator")
    assert EVAL_ROLE_PROMPT_VERSION == "3.0.0"
    assert "materially supports" in prompt
    assert "keyword overlap" in prompt
    assert "applicability first" in prompt
    assert "rejection condition defeats" in prompt
    assert "match booleans" in prompt
    assert "Decision and reasonCodes must agree" in prompt


def test_role_prompt_version_is_reported_and_changes_fingerprint(monkeypatch):
    gen_cfg, val_cfg = configs(monkeypatch)
    kwargs = dict(
        split="calibration", repetitions=2, generator=Generator(),
        validator=Validator(decisions_for_split("calibration", 2)),
        generator_config=gen_cfg, validator_config=val_cfg)
    first = evaluate_semantic_model_quality(
        **kwargs, role_prompt_version="1.0.0")
    kwargs["validator"] = Validator(decisions_for_split("calibration", 2))
    second = evaluate_semantic_model_quality(
        **kwargs, role_prompt_version=EVAL_ROLE_PROMPT_VERSION)
    assert second["configuration"]["rolePromptVersion"] == "3.0.0"
    assert len(second["configuration"]["corpusFingerprint"]) == 64
    assert (first["configuration"]["configurationFingerprint"]
            != second["configuration"]["configurationFingerprint"])
    assert second["selectionGate"]["status"] == "not_applicable"


def test_selection_gate_is_frozen_and_explains_failures():
    eligible = _selection_gate(
        "selection",
        {"recall": .9, "safeFalsePositiveRate": .2, "errorRate": .05,
         "inconclusiveRate": .1},
        {"rate": .8})
    assert eligible["status"] == "eligible"
    assert eligible["policyVersion"] == "1.0.0"
    assert eligible["failedMetrics"] == []

    failed = _selection_gate(
        "selection",
        {"recall": .89, "safeFalsePositiveRate": .21, "errorRate": .051,
         "inconclusiveRate": .101},
        {"rate": .79})
    assert failed["status"] == "not_eligible"
    assert failed["failedMetrics"] == [
        "errorRate", "inconclusiveRate", "recall",
        "safeFalsePositiveRate", "stabilityRate"]
