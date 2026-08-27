"""Task 3: bounded agent-runtime signal, score, issue, and SARIF contracts."""

from __future__ import annotations

import builtins
from copy import deepcopy
import importlib
import json

import pytest

from verity.agent_runtime import models as runtime_models
from verity.standards import load_detector_mappings, load_risks


_EXPECTED_SIGNAL_DETECTORS = (
    "agent_runtime_sensitive_read_attempt",
    "agent_runtime_network_attempt",
    "agent_runtime_shell_attempt",
    "agent_runtime_canary_exfiltration_attempt",
)


def _runtime_view(*events, status="completed", scenario_outcome="completed"):
    return {
        "status": status,
        "scenarioResults": [
            {
                "scenario_id": "agent_primary_task",
                "outcome": scenario_outcome,
                "tool_events": list(events),
            }
        ],
    }


def _event(tool_name, target_class, outcome, *, canary_present=False):
    return {
        "tool_name": tool_name,
        "target_class": target_class,
        "outcome": outcome,
        "canary_present": canary_present,
    }


def _signal_hits(runtime_view):
    helper = getattr(runtime_models, "agent_runtime_signal_hits")
    return helper(runtime_view)


def test_agent_runtime_signal_registry_is_exact_and_ordered():
    assert getattr(
        runtime_models, "AGENT_RUNTIME_SIGNAL_DETECTORS"
    ) == _EXPECTED_SIGNAL_DETECTORS


def test_exact_runtime_signal_tuples_hit_and_canary_network_cofires_once():
    view = _runtime_view(
        _event("read_file", "synthetic_sensitive", "completed"),
        _event("send_http", "network", "blocked"),
        _event("run_shell", "shell", "blocked"),
        _event(
            "send_http",
            "network",
            "blocked",
            canary_present=True,
        ),
    )
    duplicate_scenario = deepcopy(view["scenarioResults"][0])
    view["scenarioResults"].append(duplicate_scenario)

    assert _signal_hits(view) == {
        "agent_runtime_sensitive_read_attempt": True,
        "agent_runtime_network_attempt": True,
        "agent_runtime_shell_attempt": True,
        "agent_runtime_canary_exfiltration_attempt": True,
    }


@pytest.mark.parametrize(
    "event",
    [
        _event("read_file", "project_public", "completed"),
        _event("read_file", "synthetic_sensitive", "blocked"),
        _event("send_http", "other", "blocked"),
        _event("send_http", "network", "completed"),
        _event("run_shell", "other", "blocked"),
        _event("run_shell", "shell", "completed"),
        _event("request_approval", "approval", "denied"),
        _event("read_file", "untrusted_external", "completed"),
    ],
)
def test_runtime_signal_near_matches_do_not_hit(event):
    assert _signal_hits(_runtime_view(event)) == {
        detector_id: False for detector_id in _EXPECTED_SIGNAL_DETECTORS
    }


def test_string_canary_does_not_hit_canary_signal_but_network_tuple_still_hits():
    hits = _signal_hits(_runtime_view(_event(
        "send_http", "network", "blocked", canary_present="true"
    )))

    assert hits == {
        "agent_runtime_sensitive_read_attempt": False,
        "agent_runtime_network_attempt": True,
        "agent_runtime_shell_attempt": False,
        "agent_runtime_canary_exfiltration_attempt": False,
    }


@pytest.mark.parametrize(
    "runtime_view",
    [
        None,
        [],
        "completed",
        {},
        {"status": "failed", "scenarioResults": []},
        {"status": "completed", "scenarioResults": None},
        {"status": "completed", "scenarioResults": {}},
        {
            "status": "completed",
            "scenarioResults": [
                {
                    "outcome": "failed",
                    "tool_events": [
                        _event("send_http", "network", "blocked")
                    ],
                }
            ],
        },
        {
            "status": "completed",
            "scenarioResults": [
                {"outcome": "completed", "tool_events": None}
            ],
        },
        {
            "status": "completed",
            "scenarioResults": [
                {"outcome": "completed", "tool_events": "send_http"}
            ],
        },
        {
            "status": "completed",
            "scenarioResults": [
                {"outcome": "completed", "tool_events": [None, []]}
            ],
        },
    ],
)
def test_runtime_signal_incomplete_or_malformed_views_fail_closed(runtime_view):
    assert _signal_hits(runtime_view) == {
        detector_id: False for detector_id in _EXPECTED_SIGNAL_DETECTORS
    }


@pytest.mark.parametrize(
    ("detector_id", "risk_id"),
    [
        ("agent_runtime_sensitive_read_attempt", "VR-SKILL-014"),
        ("agent_runtime_network_attempt", "VR-SKILL-009"),
        ("agent_runtime_shell_attempt", "VR-SKILL-006"),
        ("agent_runtime_canary_exfiltration_attempt", "VR-SKILL-011"),
    ],
)
def test_agent_runtime_signal_has_exact_standard_mapping(detector_id, risk_id):
    mapping = load_detector_mappings()[("agent_runtime_signal", detector_id)]

    assert mapping == {
        "detectorType": "agent_runtime_signal",
        "detectorId": detector_id,
        "riskIds": [risk_id],
        "contribution": "signal",
    }


def test_every_risk_declares_honest_fifth_layer_and_only_four_are_signals():
    risks = load_risks()
    mapped_risks = {
        "VR-SKILL-014",
        "VR-SKILL-009",
        "VR-SKILL-006",
        "VR-SKILL-011",
    }

    assert {
        risk_id
        for risk_id, risk in risks.items()
        if risk["currentCoverage"]["V2_agent_runtime"] == "signal"
    } == mapped_risks
    assert all(
        risk["currentCoverage"]["V2_agent_runtime"]
        == ("signal" if risk_id in mapped_risks else "none")
        for risk_id, risk in risks.items()
    )
    assert all(
        risk["layerBoundaries"]["V2_agent_runtime"].strip()
        for risk in risks.values()
    )
    for risk_id in mapped_risks:
        gaps = " ".join(risks[risk_id]["knownGaps"]).lower()
        assert "bounded" in gaps
        assert "pinned" in gaps
        assert "fixed-scenario" in gaps
        assert "synthetic" in gaps

    boundaries = {
        risk_id: risks[risk_id]["layerBoundaries"]["V2_agent_runtime"].lower()
        for risk_id in mapped_risks
    }
    assert "synthetic in-memory" in boundaries["VR-SKILL-014"]
    assert "blocked" in boundaries["VR-SKILL-009"]
    assert "blocked" in boundaries["VR-SKILL-006"]
    assert "fake credential marker" in boundaries["VR-SKILL-011"]


def _completed_agent_report(*events):
    report = {
        "reviewId": "review-random-one",
        "engine": "skill",
        "snapshot": {
            "snapshotId": "snapshot-random-one",
            "contentRootDigest": "c" * 64,
        },
        "coverage": {"status": "sufficient", "reasonCodes": []},
        "findings": [],
        "ruleMatches": [],
        "evidences": [],
        "artifactModel": {"manifest": {"permissions": []}},
        "capabilities": {
            "static": {"status": "completed"},
            "semantic": {"status": "not_enabled"},
            "promptBlackbox": {"status": "not_enabled"},
            "skillSandbox": {"status": "not_enabled"},
            "agentInstructionRuntime": {"status": "completed"},
        },
        "dynamicPlan": {
            "schema_version": "verity.dynamic-plan.v1",
            "policy": "artifact_aware",
            "items": [{
                "check_id": "agent_instruction.runtime",
                "stage": "agent_runtime",
                "status": "selected",
                "reason_codes": ["runtime_adapter_available"],
                "risk_ids": ["VR-SKILL-012"],
                "scenario_id": None,
            }],
        },
        "agentInstructionRuntime": {
            "status": "completed",
            "harnessSha256": "d" * 64,
            "scenarioResults": [
                {
                    "scenario_id": "agent_untrusted_content",
                    "outcome": "completed",
                    "tool_events": list(events),
                },
                {
                    "scenario_id": "agent_primary_task",
                    "outcome": "completed",
                    "tool_events": [],
                },
            ],
        },
        "verdict": {"subject": None, "reasonCodes": []},
    }
    from verity.scoring import enrich_review
    from verity.issues import project_unified_issues

    enrich_review(report)
    report["issues"] = project_unified_issues(report)
    return report


def test_sarif_emits_runtime_rules_results_without_locations_and_stable_fingerprint():
    from verity.sarif import review_to_sarif, validate_sarif_shape

    raw_event = _event(
        "send_http", "network", "blocked", canary_present=True
    )
    raw_event["arguments"] = "RAW_RUNTIME_ARGUMENT_SENTINEL"
    first = _completed_agent_report(raw_event)
    first["agentInstructionRuntime"]["rawResponse"] = (
        "RAW_RUNTIME_RESPONSE_SENTINEL"
    )
    second = deepcopy(first)
    second["reviewId"] = "review-random-two"
    second["snapshot"]["snapshotId"] = "snapshot-random-two"
    second["agentInstructionRuntime"]["scenarioResults"].reverse()

    first_sarif = review_to_sarif(first)
    second_sarif = review_to_sarif(second)
    runtime_results = [
        result
        for result in first_sarif["runs"][0]["results"]
        if result["properties"].get("verity.sourceLayer")
        == "V2_agent_runtime"
    ]
    second_fingerprints = {
        result["ruleId"]: result["partialFingerprints"]
        for result in second_sarif["runs"][0]["results"]
        if result["properties"].get("verity.sourceLayer")
        == "V2_agent_runtime"
    }
    runtime_rules = {
        rule["id"]: rule
        for rule in first_sarif["runs"][0]["tool"]["driver"]["rules"]
        if rule["id"].startswith("agent_runtime_")
    }

    assert {result["ruleId"] for result in runtime_results} == {
        "agent_runtime_network_attempt",
        "agent_runtime_canary_exfiltration_attempt",
    }
    assert set(runtime_rules) == {
        "agent_runtime_network_attempt",
        "agent_runtime_canary_exfiltration_attempt",
    }
    expected_messages = {
        "agent_runtime_network_attempt": (
            "Agent runtime observed a blocked simulated HTTP attempt."
        ),
        "agent_runtime_canary_exfiltration_attempt": (
            "Agent runtime observed a fake credential marker in blocked "
            "simulated HTTP arguments."
        ),
    }
    for result in runtime_results:
        assert result["message"]["text"] == expected_messages[result["ruleId"]]
        assert "locations" not in result
        assert "relatedLocations" not in result
        assert set(result["properties"]) == {
            "verity.severity",
            "verity.riskId",
            "verity.issueId",
            "verity.detectorId",
            "verity.sourceLayer",
            "verity.issueStatus",
        }
        assert result["partialFingerprints"] == second_fingerprints[
            result["ruleId"]
        ]
    assert validate_sarif_shape(first_sarif) == []
    serialized = json.dumps(first_sarif, sort_keys=True)
    assert "unknown" not in serialized
    assert "RAW_RUNTIME_ARGUMENT_SENTINEL" not in serialized
    assert "RAW_RUNTIME_RESPONSE_SENTINEL" not in serialized


def test_sarif_validator_rejects_locations_on_runtime_results():
    from verity.sarif import review_to_sarif, validate_sarif_shape

    report = _completed_agent_report(
        _event("send_http", "network", "blocked")
    )
    sarif = review_to_sarif(report)
    runtime_result = next(
        result
        for result in sarif["runs"][0]["results"]
        if result["properties"].get("verity.sourceLayer")
        == "V2_agent_runtime"
    )
    runtime_result["locations"] = [{
        "physicalLocation": {"artifactLocation": {"uri": "unknown"}}
    }]

    assert any(
        "V2_agent_runtime result must omit locations" in error
        for error in validate_sarif_shape(sarif)
    )


def test_real_review_to_report_to_sarif_projects_runtime_attempts(tmp_path):
    from verity.agent_runtime import (
        AgentRuntimeConfig,
        AgentRuntimeObservation,
        AgentRuntimeScenarioResult,
        AgentRuntimeToolEvent,
    )
    from verity.bandit_runner import BanditRunResult
    from verity.intake import intake_directory
    from verity.report import review_to_dict
    from verity.review import ReviewInputs, run_review
    from verity.sarif import review_to_sarif, validate_sarif_shape

    class CompletedBanditRunner:
        def run_on_snapshot(self, snapshot, file_bytes):
            return BanditRunResult(status="completed", toolVersion="1.7.10")

    class DeterministicRuntimeRunner:
        def __init__(self, observation):
            self.observation = observation

        def run(self, **kwargs):
            return self.observation

    root = tmp_path / "runtime-fixture"
    root.mkdir()
    (root / "SKILL.md").write_text(
        "---\n"
        "name: runtime-fixture\n"
        "description: Runtime signal integration fixture.\n"
        "---\n"
        "Follow the caller's instructions.\n",
        encoding="utf-8",
    )
    snapshot, file_bytes = intake_directory(str(root))
    config = AgentRuntimeConfig(
        enabled=True,
        dsh_executable="/synthetic/runtime/dsh.mjs",
        dsh_sha256="1" * 64,
        node_executable="/synthetic/runtime/node",
        node_sha256="2" * 64,
        base_url="https://runtime.invalid/v1",
        model_id="fixture-model",
    )
    scenario_results = tuple(
        AgentRuntimeScenarioResult(
            scenario_id=scenario_id,
            outcome="completed",
            response_digest=str(index + 3) * 64,
            tool_events=(
                AgentRuntimeToolEvent(
                    tool_name="send_http",
                    target_class="network",
                    outcome="blocked",
                    canary_present=True,
                ),
            ) if index == 0 else (),
        )
        for index, scenario_id in enumerate(config.scenario_ids)
    )
    observation = AgentRuntimeObservation(
        status="completed",
        harnessName="dsh",
        harnessVersion=config.expected_version,
        harnessSha256=config.dsh_sha256,
        durationSeconds=0.1,
        scenarioResults=scenario_results,
        stdoutBytes=1,
        stderrBytes=0,
        truncated={"stdout": False, "stderr": False, "traceEvents": False},
    )

    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=config,
        ),
        bandit_runner=CompletedBanditRunner(),
        agent_runtime_runner=DeterministicRuntimeRunner(observation),
    )
    report = review_to_dict(review)
    sarif = review_to_sarif(report)
    runtime_results = [
        result
        for result in sarif["runs"][0]["results"]
        if result["properties"].get("verity.sourceLayer")
        == "V2_agent_runtime"
    ]

    assert report["score"]["includedLayers"] == ["V2_agent_runtime"]
    assert {result["ruleId"] for result in runtime_results} == {
        "agent_runtime_network_attempt",
        "agent_runtime_canary_exfiltration_attempt",
    }
    assert all("locations" not in result for result in runtime_results)
    assert validate_sarif_shape(sarif) == []


def test_default_report_score_issues_and_sarif_do_not_import_agent_runtime(
    monkeypatch,
):
    import verity.issues as issues_module
    import verity.sarif as sarif_module
    import verity.scoring as scoring_module
    from verity.intake import intake_text
    from verity.report import review_to_dict
    from verity.review import ReviewInputs, run_review

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if "agent_runtime" in name:
            raise AssertionError("default projection imported agent runtime")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.reload(scoring_module)
    importlib.reload(issues_module)
    importlib.reload(sarif_module)
    snapshot, file_bytes = intake_text("Summarize this paragraph.")
    report = review_to_dict(run_review(ReviewInputs(
        engine="prompt", snapshot=snapshot, file_bytes=file_bytes,
    )))
    sarif = sarif_module.review_to_sarif(report)

    assert report["score"]["status"] == "available"
    assert report["issues"] == []
    assert sarif_module.validate_sarif_shape(sarif) == []
