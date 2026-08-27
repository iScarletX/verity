from copy import deepcopy
import json

from verity.issues import project_unified_issues


def _base_report():
    return {
        "coverage": {"status": "sufficient"},
        "findings": [],
        "ruleMatches": [],
        "evidences": [],
        "dynamicPlan": {"items": []},
        "artifactModel": {"manifest": {"permissions": []}},
    }


def _with_static_injection(report):
    report["ruleMatches"].append({
        "eventId": "event-1",
        "ruleId": "prompt.instruction_override_marker",
    })
    report["evidences"].append({
        "evidenceId": "evidence-1",
        "kind": "source_span",
    })
    report["findings"].append({
        "findingId": "finding-1",
        "findingType": "prompt_instruction_override",
        "severity": "medium",
        "subjectKey": "prompt.txt:10",
        "origin": {
            "kind": "deterministic_rule",
            "ruleMatchEventIds": ["event-1"],
        },
        "evidenceIds": ["evidence-1"],
    })


def _with_blackbox_injection(report, *, failed):
    report["dynamicPlan"]["items"].append({
        "check_id": "injection_override_simple",
        "stage": "prompt_blackbox",
        "status": "selected",
        "risk_ids": ["VR-PROMPT-001"],
        "scenario_id": "injection_override_simple",
    })
    report["promptBlackbox"] = {
        "status": "completed",
        "scenarioResults": [{
            "scenario_id": "injection_override_simple",
            "severity": "high",
            "probe_results": [{"safe": not failed, "call_id": "call-1"}],
        }],
    }


def _issue(issues, risk_id):
    return next(item for item in issues if item["riskId"] == risk_id)


def _with_agent_runtime(report, *events):
    report.setdefault("capabilities", {})["agentInstructionRuntime"] = {
        "status": "completed",
    }
    report["dynamicPlan"]["items"].append({
        "check_id": "agent_instruction.runtime",
        "stage": "agent_runtime",
        "status": "selected",
        "risk_ids": ["VR-SKILL-012"],
        "scenario_id": None,
    })
    report["agentInstructionRuntime"] = {
        "status": "completed",
        "harnessSha256": "a" * 64,
        "scenarioResults": [{
            "scenario_id": "agent_primary_task",
            "outcome": "completed",
            "tool_events": list(events),
        }],
    }


def _with_static_skill_finding(report, *, rule_id, finding_id):
    event_id = "event-" + finding_id
    evidence_id = "evidence-" + finding_id
    report["ruleMatches"].append({"eventId": event_id, "ruleId": rule_id})
    report["evidences"].append({
        "evidenceId": evidence_id,
        "kind": "source_span",
    })
    report["findings"].append({
        "findingId": finding_id,
        "findingType": rule_id,
        "severity": "high",
        "subjectKey": finding_id,
        "origin": {
            "kind": "deterministic_rule",
            "ruleMatchEventIds": [event_id],
        },
        "evidenceIds": [evidence_id],
    })


def test_same_risk_keeps_occurrences_and_combines_source_layers():
    report = _base_report()
    _with_static_injection(report)
    _with_blackbox_injection(report, failed=True)

    issue = _issue(project_unified_issues(report), "VR-PROMPT-001")

    assert issue["status"] == "runtime_confirmed"
    assert issue["sourceLayers"] == ["L0_static", "V1_5_blackbox"]
    assert len(issue["occurrences"]) == 2


def test_dynamic_pass_does_not_remove_static_finding():
    report = _base_report()
    _with_static_injection(report)
    _with_blackbox_injection(report, failed=False)

    issue = _issue(project_unified_issues(report), "VR-PROMPT-001")

    assert issue["status"] == "not_reproduced"
    assert issue["severity"] == "medium"
    assert issue["occurrences"][0]["sourceLayer"] == "L0_static"
    assert any(item["outcome"] == "passed" for item in issue["runtimeChecks"])


def test_one_pass_does_not_claim_not_reproduced_when_peer_check_is_inconclusive():
    report = _base_report()
    _with_static_injection(report)
    _with_blackbox_injection(report, failed=False)
    report["dynamicPlan"]["items"].append({
        "check_id": "injection_override_roleplay",
        "stage": "prompt_blackbox",
        "status": "selected",
        "risk_ids": ["VR-PROMPT-001"],
        "scenario_id": "injection_override_roleplay",
    })
    report["promptBlackbox"]["scenarioResults"].append({
        "scenario_id": "injection_override_roleplay",
        "severity": "medium",
        "probe_results": [{"safe": None, "call_id": "call-2"}],
        "oracle_result": {
            "outcome": "insufficient_evidence",
            "reason_codes": ["response_not_parseable"],
            "observed": {},
        },
    })

    issue = _issue(project_unified_issues(report), "VR-PROMPT-001")

    assert issue["status"] == "static_only"
    assert [item["outcome"] for item in issue["runtimeChecks"]] == [
        "passed", "insufficient_evidence",
    ]


def test_runtime_only_behavior_is_visible_without_fabricated_source_span():
    report = _base_report()
    report["skillSandbox"] = {
        "status": "completed",
        "networkAttempts": [{"host": "example.invalid", "port": 443}],
        "subprocessAttempts": [],
        "fileEvents": [],
        "sqlAttempts": [],
    }
    report["dynamicPlan"]["items"].append({
        "check_id": "sandbox_undeclared_network_attempt",
        "stage": "skill_sandbox",
        "status": "selected",
        "risk_ids": ["VR-SKILL-004", "VR-SKILL-012", "VR-PROMPT-007"],
        "scenario_id": None,
    })

    issue = _issue(project_unified_issues(report), "VR-SKILL-012")

    assert issue["status"] == "runtime_only"
    assert issue["evidence"][0]["kind"] == "runtime_trace"
    assert "sourceSpan" not in issue["evidence"][0]


def test_generated_artifact_scenario_uses_plan_risk_mapping():
    report = _base_report()
    report["dynamicPlan"]["items"].append({
        "check_id": "art_style.prompt_contract",
        "stage": "prompt_blackbox",
        "status": "selected",
        "risk_ids": ["VR-PROMPT-006"],
        "scenario_id": None,
    })
    report["promptBlackbox"] = {
        "status": "completed",
        "scenarioResults": [{
            "scenario_id": "art_style.prompt_contract",
            "severity": "medium",
            "probe_results": [{"safe": False, "call_id": "call-art"}],
            "oracle_result": {
                "outcome": "failed",
                "reason_codes": ["prompt_contract_field_missing"],
                "observed": {"recognizedFieldCount": 1},
            },
        }],
    }

    issue = _issue(project_unified_issues(report), "VR-PROMPT-006")

    assert issue["status"] == "runtime_only"
    assert issue["detectorIds"] == ["art_style.prompt_contract"]


def test_agent_runtime_attempt_is_runtime_only_without_source_or_raw_data():
    report = _base_report()
    _with_agent_runtime(report, {
        "tool_name": "send_http",
        "target_class": "network",
        "outcome": "blocked",
        "canary_present": False,
    })

    issue = _issue(project_unified_issues(report), "VR-SKILL-009")
    serialized = json.dumps({
        "occurrences": issue["occurrences"],
        "evidence": issue["evidence"],
    }, sort_keys=True)

    assert issue["status"] == "runtime_only"
    assert issue["sourceLayers"] == ["V2_agent_runtime"]
    assert issue["runtimeChecks"] == []
    assert issue["evidence"] == [{
        "evidenceId": (
            "runtime:V2_agent_runtime:agent_runtime_network_attempt"
        ),
        "kind": "runtime_trace",
        "sourceLayer": "V2_agent_runtime",
        "detectorId": "agent_runtime_network_attempt",
        "outcome": "observed_attempt",
    }]
    for forbidden in (
        "sourceSpan",
        "artifactPath",
        "sourceByteRange",
        "arguments",
        "canary_present",
        "modelResponse",
    ):
        assert forbidden not in serialized


def test_agent_runtime_attempt_confirms_same_static_risk():
    report = _base_report()
    _with_static_skill_finding(
        report,
        rule_id="skill.sensitive_path_access",
        finding_id="static-sensitive-read",
    )
    _with_agent_runtime(report, {
        "tool_name": "read_file",
        "target_class": "synthetic_sensitive",
        "outcome": "completed",
        "canary_present": False,
    })

    issue = _issue(project_unified_issues(report), "VR-SKILL-014")

    assert issue["status"] == "runtime_confirmed"
    assert issue["sourceLayers"] == ["L0_static", "V2_agent_runtime"]
    assert len(issue["occurrences"]) == 2


def test_agent_runtime_plan_check_is_insufficient_and_never_not_reproduced():
    report = _base_report()
    _with_static_skill_finding(
        report,
        rule_id="skill.strict_output_contract_prose_only",
        finding_id="static-contract-gap",
    )
    _with_agent_runtime(report)

    issue = _issue(project_unified_issues(report), "VR-SKILL-012")

    assert issue["status"] == "static_only"
    assert issue["runtimeChecks"] == [{
        "detectorId": "agent_instruction.runtime",
        "sourceLayer": "V2_agent_runtime",
        "outcome": "insufficient_evidence",
    }]
