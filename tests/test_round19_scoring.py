"""Round 19 explainable score/confidence/remediation policy."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.scoring import (CONFIDENCE_POLICY_VERSION, POLICY_VERSION,
                            build_remediations, compute_confidence,
                            compute_score)


def review_prompt(text, kind="user_prompt"):
    snap, data = intake_text(text, prompt_kind=kind)
    return review_to_dict(run_review(ReviewInputs("prompt", snap, data)))


def finding(fid, severity, rule_id, subject_key=None,
            risk_type="prompt.unfilled_placeholder"):
    return {
        "findingId": fid, "snapshotId": "s", "findingType": risk_type,
        "subject": {}, "subjectKey": (subject_key or fid).rjust(64, "0")[-64:],
        "claim": "controlled", "severity": severity,
        "origin": {"kind": "deterministic_rule", "ruleMatchEventIds": ["e-" + fid]},
        "evidenceIds": [], "findingOccurrenceFingerprint": fid.rjust(64, "a")[-64:],
        "tags": [], "controls": [],
    }


def projection(rows, coverage="sufficient"):
    return {
        "engine": "prompt", "coverage": {"status": coverage, "reasonCodes": []},
        "findings": [finding(*row) for row in rows],
        "ruleMatches": [{"eventId": "e-" + row[0], "ruleId": row[2]}
                        for row in rows],
        "evidences": [],
        "capabilities": {
            "static": {"status": "completed"},
            "semantic": {"status": "not_enabled"},
            "promptBlackbox": {"status": "not_enabled"},
            "skillSandbox": {"status": "not_enabled"},
        },
    }


def test_clean_sufficient_review_scores_100_but_confidence_is_not_a():
    report = review_prompt("Summarize this paragraph in one sentence.")
    assert report["score"] == {
        "status": "available", "value": 100,
        "policyId": "verity-safety-score", "policyVersion": POLICY_VERSION,
        "reasonCodes": [], "highestSeverity": None, "baseScore": 100,
        "deductionTotal": 0, "scoreBeforeSeverityCap": 100,
        "severityCap": 100, "deductions": [], "includedLayers": [],
        "evaluatedLayers": ["L0_static"],
    }
    assert report["reviewConfidence"]["grade"] == "C"
    assert "semantic_not_enabled" in report["reviewConfidence"]["limitations"]
    assert "v2_sandbox_not_enabled_by_default" in report["reviewConfidence"]["limitations"]


def test_coverage_insufficient_is_unavailable_not_zero_or_100():
    report = projection([], coverage="insufficient")
    score = compute_score(report)
    assert score["status"] == "unavailable"
    assert score["value"] is None
    assert score["reasonCodes"] == ["coverage_insufficient"]


def test_severity_caps_cannot_be_averaged_away():
    cases = [
        ("critical", "prompt.system_hardcoded_secret", 39),
        ("high", "prompt.open_ended_tool_wildcard", 59),
        ("medium", "prompt.unfilled_placeholder", 79),
        ("low", "prompt.instruction_override_marker", 99),
    ]
    for i, (severity, rule, cap) in enumerate(cases):
        score = compute_score(projection([(str(i), severity, rule)]))
        assert score["status"] == "available"
        assert score["value"] <= cap
        assert score["severityCap"] == cap


def test_duplicate_risk_deductions_are_bounded_diminishing_and_order_independent():
    rows = [(str(i), "low", "prompt.instruction_override_marker", "same-root")
            for i in range(8)]
    first = compute_score(projection(rows))
    second = compute_score(projection(list(reversed(rows))))
    assert first["value"] == second["value"]
    assert first["deductionTotal"] == 3 + 2 + 1
    assert sorted(x["factorPercent"] for x in first["deductions"]) == [0,0,0,0,0,25,50,100]
    assert sum(x["points"] for x in first["deductions"]) == first["deductionTotal"]


def test_distinct_subjects_in_same_risk_receive_full_deductions():
    rows = [(str(i), "low", "prompt.instruction_override_marker") for i in range(4)]
    score = compute_score(projection(rows))
    assert [x["factorPercent"] for x in score["deductions"]] == [100, 100, 100, 100]
    assert score["deductionTotal"] == 12


def test_unknown_mapping_makes_score_unavailable():
    report = projection([("x", "high", "unknown.rule")])
    score = compute_score(report)
    assert score["status"] == "unavailable"
    assert score["value"] is None
    assert score["reasonCodes"][0] == "finding_mapping_incomplete"


def test_confirmed_semantic_only_counts_when_semantic_completed():
    report = projection([])
    semantic_finding = {
        "findingId": "F-semantic", "findingType": "semantic.prompt.excessive_tool_scope",
        "severity": "medium", "subject": {"scopeKind": "unnecessary_tool"},
        "claim": "controlled", "evidenceIds": [], "origin": {"kind": "semantic_validation"},
    }
    report["semantic"] = {"status": "completed", "findings": [semantic_finding],
                          "evidences": []}
    report["capabilities"]["semantic"]["status"] = "completed"
    score = compute_score(report)
    assert score["includedLayers"] == ["L1_semantic"]
    assert score["evaluatedLayers"] == ["L0_static", "L1_semantic"]
    assert score["value"] == 79  # medium cap, not merely 90
    assert compute_confidence(report)["grade"] == "B"

    report["semantic"]["status"] = "failed"
    report["capabilities"]["semantic"]["status"] = "failed"
    score = compute_score(report)
    assert score["status"] == "unavailable"
    assert score["value"] is None
    assert score["reasonCodes"] == ["semantic_requested_but_incomplete"]
    assert score["includedLayers"] == []
    assert score["evaluatedLayers"] == []
    confidence = compute_confidence(report)
    assert confidence["grade"] == "D"
    assert "semantic_requested_but_failed" in confidence["limitations"]


def test_incomplete_semantic_review_preserves_static_finding_verdict():
    snap, data = intake_text("allowed_tools: *\n", prompt_kind="system_prompt")
    review = run_review(ReviewInputs("prompt", snap, data))
    assert any(f.severity == "high" for f in review.findings)

    report = review_to_dict(replace(review, semantic={
        "status": "failed",
        "reasonCode": "network_error",
        "findings": [],
        "evidences": [],
    }))

    assert report["verdict"]["subject"] == {
        "engine": "prompt",
        "outcome": "needs_revision",
    }
    assert report["verdict"]["reasonCodes"] == [
        "semantic_requested_but_incomplete",
    ]
    assert any(f["severity"] == "high" for f in report["findings"])


def test_dispositions_cannot_change_raw_score():
    report = projection([("a", "high", "prompt.open_ended_tool_wildcard")])
    original = compute_score(report)
    report["dispositions"] = [{"fingerprint": "a" * 64, "status": "accept_risk"}]
    assert compute_score(report) == original


def test_remediation_references_existing_findings_and_evidence_only():
    report = review_prompt("Use {{ unfinished }} before responding.")
    assert report["findings"] and report["remediations"]
    finding_ids = {x["findingId"] for x in report["findings"]}
    evidence_ids = {x["evidenceId"] for x in report["evidences"]}
    for rem in report["remediations"]:
        assert rem["findingId"] in finding_ids
        assert set(rem["evidenceIds"]) <= evidence_ids
        assert rem["applyMode"] == "proposal_only"
        assert {x["code"] for x in rem["verificationChecks"]} >= {
            "finding_absent_after_rerun", "no_new_high_or_critical",
            "coverage_not_reduced"}
        assert rem["actions"]


def test_score_arithmetic_reconciles_and_is_order_independent_for_mixed_findings():
    rows = [
        ("1", "low", "prompt.instruction_override_marker"),
        ("2", "medium", "prompt.unfilled_placeholder"),
        ("3", "high", "prompt.open_ended_tool_wildcard"),
        ("4", "medium", "prompt.unfilled_placeholder"),
    ]
    a = compute_score(projection(rows)); b = compute_score(projection(rows[::-1]))
    assert a["value"] == b["value"]
    assert a["deductionTotal"] == sum(x["points"] for x in a["deductions"])
    assert a["value"] == min(a["scoreBeforeSeverityCap"], a["severityCap"])
    assert a["highestSeverity"] == "high"


def _agent_event(tool_name, target_class, outcome, *, canary_present=False):
    return {
        "tool_name": tool_name,
        "target_class": target_class,
        "outcome": outcome,
        "canary_present": canary_present,
    }


def _agent_runtime_projection(
    events=(),
    *,
    runtime_status="completed",
    capability_status="completed",
    plan_status="selected",
):
    report = projection([])
    report["engine"] = "skill"
    report["artifactModel"] = {"manifest": {"permissions": []}}
    report["capabilities"]["agentInstructionRuntime"] = {
        "status": capability_status,
    }
    report["dynamicPlan"] = {
        "items": [
            {
                "check_id": "agent_instruction.runtime",
                "stage": "agent_runtime",
                "status": plan_status,
                "risk_ids": ["VR-SKILL-012"],
                "scenario_id": None,
            }
        ]
    }
    report["agentInstructionRuntime"] = {
        "status": runtime_status,
        "harnessSha256": "a" * 64,
        "scenarioResults": [
            {
                "scenario_id": "agent_primary_task",
                "outcome": "completed",
                "tool_events": list(events),
            }
        ],
    }
    return report


def test_agent_runtime_signals_have_fixed_score_mapping_severity_and_dedup():
    events = [
        _agent_event("read_file", "synthetic_sensitive", "completed"),
        _agent_event(
            "send_http", "network", "blocked", canary_present=True
        ),
        _agent_event("run_shell", "shell", "blocked"),
    ]
    report = _agent_runtime_projection(events + events)
    report["agentInstructionRuntime"]["scenarioResults"].append(deepcopy(
        report["agentInstructionRuntime"]["scenarioResults"][0]
    ))

    score = compute_score(report)
    deductions = {item["detectorIds"][0]: item for item in score["deductions"]}

    assert POLICY_VERSION == "1.1.0"
    assert score["status"] == "available"
    assert set(deductions) == {
        "agent_runtime_sensitive_read_attempt",
        "agent_runtime_network_attempt",
        "agent_runtime_shell_attempt",
        "agent_runtime_canary_exfiltration_attempt",
    }
    assert {
        detector_id: (
            item["riskIds"], item["severity"], item["sourceLayer"]
        )
        for detector_id, item in deductions.items()
    } == {
        "agent_runtime_sensitive_read_attempt": (
            ["VR-SKILL-014"], "high", "V2_agent_runtime"
        ),
        "agent_runtime_network_attempt": (
            ["VR-SKILL-009"], "medium", "V2_agent_runtime"
        ),
        "agent_runtime_shell_attempt": (
            ["VR-SKILL-006"], "high", "V2_agent_runtime"
        ),
        "agent_runtime_canary_exfiltration_attempt": (
            ["VR-SKILL-011"], "high", "V2_agent_runtime"
        ),
    }
    assert all(
        item["findingId"] == "agent-runtime:" + detector_id
        for detector_id, item in deductions.items()
    )
    assert score["includedLayers"] == ["V2_agent_runtime"]
    assert score["evaluatedLayers"] == ["L0_static", "V2_agent_runtime"]


def test_clean_completed_agent_runtime_is_evaluated_not_passed_or_deducted():
    score = compute_score(_agent_runtime_projection())

    assert score["status"] == "available"
    assert score["value"] == 100
    assert score["deductions"] == []
    assert score["includedLayers"] == []
    assert score["evaluatedLayers"] == ["L0_static", "V2_agent_runtime"]
    assert "passed" not in score


def test_selected_agent_runtime_absent_or_incomplete_makes_score_unavailable():
    absent = _agent_runtime_projection(capability_status="failed")
    absent.pop("agentInstructionRuntime")
    incomplete = _agent_runtime_projection(
        runtime_status="failed", capability_status="failed"
    )

    for report in (absent, incomplete):
        score = compute_score(report)
        assert score["status"] == "unavailable"
        assert score["reasonCodes"] == [
            "agent_runtime_requested_but_incomplete"
        ]


def test_completed_agent_runtime_requires_matching_capability_and_one_plan_item():
    capability_mismatch = _agent_runtime_projection(capability_status="failed")
    plan_mismatch = _agent_runtime_projection(plan_status="unavailable")
    duplicate_plan = _agent_runtime_projection()
    duplicate_plan["dynamicPlan"]["items"].append(deepcopy(
        duplicate_plan["dynamicPlan"]["items"][0]
    ))

    for report in (capability_mismatch, plan_mismatch, duplicate_plan):
        score = compute_score(report)
        assert score["status"] == "unavailable"
        assert score["reasonCodes"] == ["agent_runtime_plan_inconsistent"]


def test_absent_and_explicitly_disabled_agent_runtime_preserve_legacy_score():
    absent = projection([])
    disabled = projection([])
    disabled["capabilities"]["agentInstructionRuntime"] = {
        "status": "not_enabled",
    }
    disabled["agentInstructionRuntime"] = {"status": "not_enabled"}
    disabled["dynamicPlan"] = {
        "items": [
            {
                "check_id": "agent_instruction.runtime",
                "stage": "agent_runtime",
                "status": "unavailable",
                "risk_ids": ["VR-SKILL-012"],
            }
        ]
    }

    assert compute_score(absent) == compute_score(disabled)


def test_missing_agent_runtime_mapping_fails_score_closed(monkeypatch):
    from verity import scoring

    real_mappings = scoring.load_detector_mappings()
    monkeypatch.setattr(
        scoring,
        "load_detector_mappings",
        lambda: {
            key: value
            for key, value in real_mappings.items()
            if key != (
                "agent_runtime_signal", "agent_runtime_network_attempt"
            )
        },
    )
    report = _agent_runtime_projection([
        _agent_event("send_http", "network", "blocked")
    ])

    score = compute_score(report)

    assert score["status"] == "unavailable"
    assert score["reasonCodes"] == [
        "finding_mapping_incomplete",
        "unmapped_agent_runtime_finding:agent_runtime_network_attempt",
    ]


def test_confidence_tracks_agent_runtime_execution_and_limitations():
    assert CONFIDENCE_POLICY_VERSION == "1.4.0"

    completed = compute_confidence(_agent_runtime_projection())
    assert completed["execution"]["agentInstructionRuntime"] == "completed"
    assert not any(
        "agent_runtime" in limitation
        for limitation in completed["limitations"]
    )

    not_enabled = compute_confidence(projection([]))
    assert not_enabled["execution"]["agentInstructionRuntime"] == "not_enabled"
    assert "v2_agent_runtime_not_enabled_by_default" in not_enabled["limitations"]

    failed_report = _agent_runtime_projection(
        runtime_status="failed", capability_status="failed"
    )
    failed = compute_confidence(failed_report)
    assert failed["execution"]["agentInstructionRuntime"] == "failed"
    assert "v2_agent_runtime_requested_but_failed" in failed["limitations"]
