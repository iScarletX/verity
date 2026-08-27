"""Round 88: V1.5 black-box scenario failures feed the numeric score.

Companion to test_round19_scoring.py's projection() template, extended with
a promptBlackbox block. Sandbox behavior is untouched by this round and is
not re-tested here.
"""
from __future__ import annotations

from verity.scoring import compute_confidence, compute_score


def projection(coverage="sufficient"):
    return {
        "engine": "prompt", "coverage": {"status": coverage, "reasonCodes": []},
        "findings": [], "ruleMatches": [], "evidences": [],
        "capabilities": {
            "static": {"status": "completed"},
            "semantic": {"status": "not_enabled"},
            "promptBlackbox": {"status": "not_enabled"},
            "skillSandbox": {"status": "not_enabled"},
        },
    }


def scenario_result(scenario_id, severity, safe_flags):
    return {
        "scenario_id": scenario_id, "title": scenario_id, "severity": severity,
        "probe_results": [{"safe": flag} for flag in safe_flags],
    }


def test_failed_scenario_is_deducted_with_correct_risk_and_layer():
    report = projection()
    report["promptBlackbox"] = {
        "status": "completed",
        "scenarioResults": [
            scenario_result("injection_override_simple", "high", [False, True]),
        ],
    }
    report["capabilities"]["promptBlackbox"]["status"] = "completed"
    score = compute_score(report)
    assert score["status"] == "available"
    assert score["includedLayers"] == ["V1_5_blackbox"]
    assert score["evaluatedLayers"] == ["L0_static", "V1_5_blackbox"]
    assert len(score["deductions"]) == 1
    deduction = score["deductions"][0]
    assert deduction["severity"] == "high"
    assert deduction["riskIds"] == ["VR-PROMPT-001"]
    assert deduction["sourceLayer"] == "V1_5_blackbox"
    assert deduction["detectorIds"] == ["injection_override_simple"]
    assert score["value"] <= 59  # high severity cap


def test_passed_scenario_is_not_deducted():
    report = projection()
    report["promptBlackbox"] = {
        "status": "completed",
        "scenarioResults": [
            scenario_result("injection_override_simple", "high", [True, True]),
        ],
    }
    report["capabilities"]["promptBlackbox"]["status"] = "completed"
    score = compute_score(report)
    assert score["status"] == "available"
    assert score["value"] == 100
    assert score["deductions"] == []
    assert score["includedLayers"] == []
    assert score["evaluatedLayers"] == ["L0_static", "V1_5_blackbox"]


def test_partial_and_error_scenarios_are_not_deducted_conservatively():
    report = projection()
    report["promptBlackbox"] = {
        "status": "completed",
        "scenarioResults": [
            scenario_result("injection_override_simple", "high", [True, None]),  # partial
            scenario_result("system_prompt_extraction", "medium", [None, None]),  # error
        ],
    }
    report["capabilities"]["promptBlackbox"]["status"] = "completed"
    score = compute_score(report)
    assert score["status"] == "available"
    assert score["value"] == 100
    assert score["deductions"] == []


def test_requested_but_failed_blackbox_makes_score_unavailable():
    report = projection()
    report["promptBlackbox"] = {"status": "failed", "reasonCode": "provider_not_configured"}
    report["capabilities"]["promptBlackbox"]["status"] = "failed"
    score = compute_score(report)
    assert score["status"] == "unavailable"
    assert score["value"] is None
    assert score["reasonCodes"] == ["blackbox_requested_but_incomplete"]
    confidence = compute_confidence(report)
    assert "v1_5_blackbox_requested_but_failed" in confidence["limitations"]
    assert "v1_5_blackbox_not_enabled_by_default" not in confidence["limitations"]


def test_completed_blackbox_carries_no_limitation_code():
    report = projection()
    report["promptBlackbox"] = {
        "status": "completed",
        "scenarioResults": [
            scenario_result("output_format_compliance", "low", [True]),
        ],
    }
    report["capabilities"]["promptBlackbox"]["status"] = "completed"
    confidence = compute_confidence(report)
    assert "v1_5_blackbox_requested_but_failed" not in confidence["limitations"]
    assert "v1_5_blackbox_not_enabled_by_default" not in confidence["limitations"]


def test_not_enabled_blackbox_keeps_prior_limitation_code():
    report = projection()
    confidence = compute_confidence(report)
    assert "v1_5_blackbox_not_enabled_by_default" in confidence["limitations"]
    assert "v1_5_blackbox_requested_but_failed" not in confidence["limitations"]
    # Sandbox limitation vocabulary is untouched by this round.
    assert "v2_sandbox_not_enabled_by_default" in confidence["limitations"]


def test_unmapped_scenario_id_makes_score_unavailable_not_silently_dropped():
    report = projection()
    report["promptBlackbox"] = {
        "status": "completed",
        "scenarioResults": [
            scenario_result("some_future_scenario_not_yet_mapped", "high", [False]),
        ],
    }
    report["capabilities"]["promptBlackbox"]["status"] = "completed"
    score = compute_score(report)
    assert score["status"] == "unavailable"
    assert score["reasonCodes"][0] == "finding_mapping_incomplete"
    assert "unmapped_blackbox_finding:some_future_scenario_not_yet_mapped" in score["reasonCodes"]
