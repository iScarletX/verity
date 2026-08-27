"""``headline_for`` and final runtime verdicts must be self-consistent.

A Provider-configured-but-failed/budget-exhausted semantic run only shows
"something went wrong" text unless the real reasonCode is folded in via
``_SEMANTIC_REASON_HINTS``. This had no direct test coverage: the only
existing assertion (tests/test_web_provider_config.py) checks
``headline["code"] == "semantic_block"`` but never inspects ``detail``.
"""
import pytest

from verity.report import _reconcile_agent_runtime_verdict
from verity.web.view import build_view_model, headline_for


def test_known_reason_code_appends_plain_language_hint():
    h = headline_for({"semantic": {"status": "failed",
                                    "reasonCode": "credential_missing"}})
    assert h["code"] == "semantic_block"
    assert "credential_missing" in h["detail"]
    assert "API Key" in h["detail"]


def test_unknown_reason_code_still_shown_without_crash():
    h = headline_for({"semantic": {"status": "failed",
                                    "reasonCode": "some_future_reason_code"}})
    assert h["code"] == "semantic_block"
    assert "some_future_reason_code" in h["detail"]


def test_missing_reason_code_leaves_base_detail_unchanged():
    h = headline_for({"semantic": {"status": "failed"}})
    assert h["code"] == "semantic_block"
    assert "失败原因" not in h["detail"]


def test_budget_exhausted_status_also_gets_the_hint():
    h = headline_for({"semantic": {"status": "budget_exhausted",
                                    "reasonCode": "run_budget_exhausted"}})
    assert h["code"] == "semantic_block"
    assert "run_budget_exhausted" in h["detail"]


def test_completed_semantic_status_does_not_trigger_block_headline():
    h = headline_for({
        "semantic": {"status": "completed", "reasonCode": "credential_missing"},
        "verdict": {"coverage": "sufficient", "subject": {"outcome": "pass"}},
        "engine": "prompt",
    })
    assert h["code"] != "semantic_block"


def test_runtime_high_outweighs_semantic_and_runtime_incomplete_blocks():
    h = headline_for({
        "engine": "skill",
        "semantic": {"status": "failed"},
        "verdict": {
            "coverage": "sufficient",
            "reasonCodes": ["agent_runtime_requested_but_incomplete"],
        },
        "capabilities": {
            "agentInstructionRuntime": {"status": "failed"},
        },
        "issues": [{
            "occurrences": [{
                "sourceLayer": "V2_agent_runtime",
                "severity": "high",
                "findingId": "agent:high",
            }],
        }],
    })
    assert h["code"] == "findings_block_skill_high"
    assert h["tone"] == "bad"


def test_static_high_outweighs_semantic_failure():
    h = headline_for({
        "engine": "skill",
        "semantic": {"status": "failed"},
        "verdict": {"coverage": "insufficient"},
        "findings": [{"severity": "high"}],
    })
    assert h["code"] == "findings_block_skill_high"
    assert h["tone"] == "bad"


@pytest.mark.parametrize(
    ("engine", "layer", "initial_outcome", "expected_outcome",
     "expected_headline"),
    [
        ("prompt", "V1_5_blackbox", "ready", "needs_revision",
         "findings_block_prompt_high"),
        ("skill", "V2_sandbox", "low_detected_risk", "do_not_install",
         "findings_block_skill_high"),
    ],
)
def test_completed_dynamic_high_reconciles_verdict_headline_and_counts(
        engine, layer, initial_outcome, expected_outcome, expected_headline):
    report = {
        "engine": engine,
        "semantic": {"status": "off"},
        "coverage": {"status": "sufficient", "reasonCodes": []},
        "verdict": {
            "coverage": "sufficient",
            "subject": {"engine": engine, "outcome": initial_outcome},
            "reasonCodes": [],
        },
        "issues": [{
            "occurrences": [{
                "sourceLayer": layer,
                "severity": "high",
                "findingId": "dynamic:high",
            }],
        }],
        "capabilities": {},
    }

    _reconcile_agent_runtime_verdict(report)

    assert report["verdict"]["subject"]["outcome"] == expected_outcome
    assert "high_or_critical_finding_present" in report["verdict"][
        "reasonCodes"]
    assert headline_for(report)["code"] == expected_headline
    assert build_view_model(report, "runtime-review")["counts"]["high"] == 1


@pytest.mark.parametrize(
    ("engine", "stage_key", "reason_code", "expected_headline"),
    [
        ("prompt", "promptBlackbox",
         "prompt_blackbox_requested_but_incomplete", "prompt_blackbox_block"),
        ("skill", "skillSandbox",
         "skill_sandbox_requested_but_incomplete", "skill_sandbox_block"),
    ],
)
def test_requested_dynamic_failure_withholds_optimistic_verdict_and_headline(
        engine, stage_key, reason_code, expected_headline):
    initial_outcome = "ready" if engine == "prompt" else "low_detected_risk"
    report = {
        "engine": engine,
        "semantic": {"status": "off"},
        "coverage": {"status": "sufficient", "reasonCodes": []},
        "verdict": {
            "coverage": "sufficient",
            "subject": {"engine": engine, "outcome": initial_outcome},
            "reasonCodes": [],
        },
        stage_key: {"status": "failed", "reasonCode": "synthetic_failure"},
        "capabilities": {stage_key: {"status": "failed"}},
        "issues": [],
    }

    _reconcile_agent_runtime_verdict(report)

    assert report["verdict"]["subject"] is None
    assert reason_code in report["verdict"]["reasonCodes"]
    assert headline_for(report)["code"] == expected_headline


def test_view_scope_note_does_not_claim_skill_sandbox_execution():
    view = build_view_model({
        "engine": "skill",
        "semantic": {"status": "off"},
        "coverage": {"status": "sufficient", "reasonCodes": []},
        "verdict": {
            "coverage": "sufficient",
            "subject": {"engine": "skill", "outcome": "low_detected_risk"},
            "reasonCodes": [],
        },
        "capabilities": {"skillSandbox": {"status": "not_enabled"}},
        "issues": [],
    }, "scope-note-review")

    assert "产品路径暂不可用" in view["scopeNote"]
    assert "sandbox_isolation_hardening_required" in view["scopeNote"]
    assert "确认后才会运行" not in view["scopeNote"]
