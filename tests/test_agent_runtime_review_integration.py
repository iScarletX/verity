from __future__ import annotations

import builtins
from dataclasses import replace
import importlib
import json
from pathlib import Path

import pytest

from verity.bandit_runner import BanditRunResult
from verity.intake import intake_directory
from verity.report import review_to_dict, to_html
from verity.review import ReviewInputs, run_review


class _CompletedBanditRunner:
    def run_on_snapshot(self, snapshot, file_bytes):
        return BanditRunResult(status="completed", toolVersion="1.7.10")


class _RecordingAgentRuntimeRunner:
    def __init__(self, observation):
        self.observation = observation
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return self.observation


class _ExplodingAgentRuntimeRunner:
    def run(self, **kwargs):
        raise RuntimeError(
            "RAW_RUNTIME_EXCEPTION_SENTINEL credential-value patch-body"
        )


def _skill_snapshot(
    tmp_path: Path,
    *,
    manifest: str | None = None,
    extra_files: dict[str, bytes] | None = None,
):
    root = tmp_path / "runtime-fixture"
    root.mkdir()
    (root / "SKILL.md").write_text(
        manifest
        or (
            "---\n"
            "name: runtime-fixture\n"
            "description: Runtime orchestration fixture.\n"
            "---\n"
            "Follow the caller's instructions.\n"
        )
    )
    for relative_path, content in (extra_files or {}).items():
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    return intake_directory(str(root))


def _dynamic_item(report, check_id: str):
    return next(
        item for item in report["dynamicPlan"]["items"]
        if item["check_id"] == check_id
    )


def _enabled_config():
    from verity.agent_runtime import AgentRuntimeConfig

    return AgentRuntimeConfig(
        enabled=True,
        dsh_executable="/synthetic/runtime/dsh.mjs",
        dsh_sha256="1" * 64,
        node_executable="/synthetic/runtime/node",
        node_sha256="2" * 64,
        base_url="https://runtime.invalid/v1",
        model_id="fixture-model",
    )


def _completed_observation(config, **overrides):
    from verity.agent_runtime import (
        AgentRuntimeObservation,
        AgentRuntimeScenarioResult,
        AgentRuntimeToolEvent,
    )

    scenario_results = tuple(
        AgentRuntimeScenarioResult(
            scenario_id=scenario_id,
            outcome="completed",
            response_digest=str(index + 3) * 64,
            tool_events=(
                AgentRuntimeToolEvent(
                    tool_name="read_file",
                    target_class="project_public",
                    outcome="completed",
                ),
            ) if index == 0 else (),
        )
        for index, scenario_id in enumerate(config.scenario_ids)
    )
    values = {
        "status": "completed",
        "harnessName": "dsh",
        "harnessVersion": config.expected_version,
        "harnessSha256": config.dsh_sha256.lower(),
        "durationSeconds": 0.25,
        "scenarioResults": scenario_results,
        "stdoutBytes": 12,
        "stderrBytes": 0,
        "truncated": {
            "stdout": False,
            "stderr": False,
            "traceEvents": False,
        },
    }
    values.update(overrides)
    return AgentRuntimeObservation(**values)


def _forbid_default_runner_construction(monkeypatch):
    def forbidden_constructor():
        raise AssertionError("agent runtime runner must not be constructed")

    monkeypatch.setattr(
        "verity.agent_runtime.runner.HarnessAgentRuntimeRunner",
        forbidden_constructor,
    )


def test_default_review_never_imports_constructs_or_calls_agent_runtime(
    tmp_path, monkeypatch,
):
    import verity.review as review_module

    snapshot, file_bytes = _skill_snapshot(tmp_path)
    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if "agent_runtime" in name:
            raise AssertionError("default review imported the agent runtime")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    review_module = importlib.reload(review_module)
    review = review_module.run_review(
        review_module.ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
        ),
        bandit_runner=_CompletedBanditRunner(),
    )
    report = review_to_dict(review)

    assert review.agentInstructionRuntime is None
    assert "agentInstructionRuntime" not in report
    assert report["capabilities"]["agentInstructionRuntime"]["status"] == (
        "not_enabled"
    )
    runtime = _dynamic_item(report, "agent_instruction.runtime")
    assert runtime["status"] == "unavailable"
    assert runtime["reason_codes"] == ["agent_runtime_not_configured"]


def test_disabled_config_is_inert_and_reported_not_enabled(tmp_path, monkeypatch):
    from verity.agent_runtime import AgentRuntimeConfig

    snapshot, file_bytes = _skill_snapshot(tmp_path)
    _forbid_default_runner_construction(monkeypatch)
    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=AgentRuntimeConfig(),
        ),
        bandit_runner=_CompletedBanditRunner(),
    )
    report = review_to_dict(review)

    assert review.agentInstructionRuntime == {
        "status": "not_enabled",
        "reasonCode": "disabled_by_config",
    }
    assert report["agentInstructionRuntime"] == review.agentInstructionRuntime
    assert report["capabilities"]["agentInstructionRuntime"]["status"] == (
        "not_enabled"
    )
    runtime = _dynamic_item(report, "agent_instruction.runtime")
    assert runtime["status"] == "unavailable"
    assert runtime["reason_codes"] == ["agent_runtime_not_configured"]


def test_enabled_config_invokes_injected_runner_once_and_reports_completed(tmp_path):
    snapshot, file_bytes = _skill_snapshot(tmp_path)
    config = _enabled_config()
    observation = _completed_observation(config)
    runner = _RecordingAgentRuntimeRunner(observation)

    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=config,
        ),
        bandit_runner=_CompletedBanditRunner(),
        agent_runtime_runner=runner,
    )
    report = review_to_dict(review)

    assert len(runner.calls) == 1
    assert runner.calls[0] == {
        "config": config,
        "snapshot": snapshot,
        "file_bytes": file_bytes,
        "skill_name": "runtime-fixture",
    }
    runtime = _dynamic_item(report, "agent_instruction.runtime")
    assert runtime["status"] == "selected"
    assert runtime["reason_codes"] == ["runtime_adapter_available"]
    assert report["agentInstructionRuntime"]["status"] == "completed"
    assert report["agentInstructionRuntime"]["observationStatus"] == "completed"
    assert set(report["agentInstructionRuntime"]) == {
        "status",
        "observationStatus",
        "reasonCode",
        "harnessName",
        "harnessVersion",
        "harnessSha256",
        "durationSeconds",
        "scenarioResults",
        "stdoutBytes",
        "stderrBytes",
        "truncated",
    }
    assert report["capabilities"]["agentInstructionRuntime"]["status"] == (
        "completed"
    )


def test_runtime_high_outweighs_insufficient_coverage_in_static_html(tmp_path):
    from verity.agent_runtime import AgentRuntimeToolEvent

    snapshot, file_bytes = _skill_snapshot(tmp_path)
    config = _enabled_config()
    base_observation = _completed_observation(config)
    first_scenario = replace(
        base_observation.scenarioResults[0],
        tool_events=(AgentRuntimeToolEvent(
            tool_name="read_file",
            target_class="synthetic_sensitive",
            outcome="completed",
        ),),
    )
    observation = _completed_observation(
        config,
        scenarioResults=(first_scenario, *base_observation.scenarioResults[1:]),
    )
    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=config,
        ),
        bandit_runner=_CompletedBanditRunner(),
        agent_runtime_runner=_RecordingAgentRuntimeRunner(observation),
    )
    review = replace(
        review,
        coverage=replace(
            review.coverage,
            status="insufficient",
            reasonCodes=["synthetic_gap"],
        ),
    )

    report = review_to_dict(review)
    rendered = to_html(review)

    assert report["verdict"]["subject"]["outcome"] == "do_not_install"
    assert (
        '<div class="banner bad"><strong>Subject outcome: DO_NOT_INSTALL'
        in rendered
    )
    assert "Coverage: <code>insufficient</code>" in rendered


@pytest.mark.parametrize(
    "invalid_field",
    [
        "empty_scenarios",
        "partial_scenarios",
        "missing_harness_name",
        "missing_harness_version",
        "missing_harness_digest",
        "mismatched_harness_digest",
        "missing_duration",
        "missing_response_digest",
        "incomplete_truncation_shape",
        "stdout_truncated",
        "stderr_truncated",
        "trace_events_truncated",
        "stdout_budget_exceeded",
        "stderr_budget_exceeded",
    ],
)
def test_completed_observation_must_match_runner_completion_contract(
    tmp_path, invalid_field,
):
    from verity.agent_runtime import AgentRuntimeScenarioResult

    config = _enabled_config()
    valid = _completed_observation(config)
    overrides = {}
    if invalid_field == "empty_scenarios":
        overrides["scenarioResults"] = ()
    elif invalid_field == "partial_scenarios":
        overrides["scenarioResults"] = valid.scenarioResults[:1]
    elif invalid_field == "missing_harness_name":
        overrides["harnessName"] = None
    elif invalid_field == "missing_harness_version":
        overrides["harnessVersion"] = None
    elif invalid_field == "missing_harness_digest":
        overrides["harnessSha256"] = None
    elif invalid_field == "mismatched_harness_digest":
        overrides["harnessSha256"] = "4" * 64
    elif invalid_field == "missing_duration":
        overrides["durationSeconds"] = None
    elif invalid_field == "missing_response_digest":
        first = valid.scenarioResults[0]
        overrides["scenarioResults"] = (
            AgentRuntimeScenarioResult(
                scenario_id=first.scenario_id,
                outcome=first.outcome,
                reason_codes=first.reason_codes,
                response_digest=None,
                tool_events=first.tool_events,
            ),
            *valid.scenarioResults[1:],
        )
    elif invalid_field == "incomplete_truncation_shape":
        overrides["truncated"] = {"stdout": False, "stderr": False}
    elif invalid_field == "stdout_truncated":
        overrides["truncated"] = {**valid.truncated, "stdout": True}
    elif invalid_field == "stderr_truncated":
        overrides["truncated"] = {**valid.truncated, "stderr": True}
    elif invalid_field == "trace_events_truncated":
        overrides["truncated"] = {**valid.truncated, "traceEvents": True}
    elif invalid_field == "stdout_budget_exceeded":
        overrides["stdoutBytes"] = (
            len(config.scenario_ids) * config.max_stdout_bytes + 1
        )
    elif invalid_field == "stderr_budget_exceeded":
        overrides["stderrBytes"] = (
            len(config.scenario_ids) * config.max_stderr_bytes + 1
        )
    observation = _completed_observation(config, **overrides)
    snapshot, file_bytes = _skill_snapshot(tmp_path)

    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=config,
        ),
        bandit_runner=_CompletedBanditRunner(),
        agent_runtime_runner=_RecordingAgentRuntimeRunner(observation),
    )
    report = review_to_dict(review)

    assert review.agentInstructionRuntime == {
        "status": "failed",
        "reasonCode": "agent_runtime_invalid_observation",
    }
    assert report["capabilities"]["agentInstructionRuntime"]["status"] == "failed"


def test_agent_runtime_config_wrong_type_is_rejected(tmp_path):
    snapshot, file_bytes = _skill_snapshot(tmp_path)

    with pytest.raises(TypeError, match="AgentRuntimeConfig"):
        run_review(
            ReviewInputs(
                engine="skill",
                snapshot=snapshot,
                file_bytes=file_bytes,
                profile="minimal",
                agent_runtime_config=object(),
            ),
            bandit_runner=_CompletedBanditRunner(),
        )


def test_agent_runtime_config_is_rejected_for_prompt_engine():
    from verity.agent_runtime import AgentRuntimeConfig
    from verity.intake import intake_text

    snapshot, file_bytes = intake_text("A harmless prompt.")

    with pytest.raises(ValueError, match="engine='skill'"):
        run_review(
            ReviewInputs(
                engine="prompt",
                snapshot=snapshot,
                file_bytes=file_bytes,
                agent_runtime_config=AgentRuntimeConfig(),
            )
        )


def test_enabled_config_for_executable_skill_fails_without_calling_runner(
    tmp_path, monkeypatch,
):
    snapshot, file_bytes = _skill_snapshot(
        tmp_path,
        extra_files={"scripts/main.py": b"print('fixture')\n"},
    )
    _forbid_default_runner_construction(monkeypatch)

    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=_enabled_config(),
        ),
        bandit_runner=_CompletedBanditRunner(),
    )
    report = review_to_dict(review)

    assert review.behaviorProfile.runtime_kind == "executable_skill"
    assert review.agentInstructionRuntime == {
        "status": "failed",
        "observationStatus": "not_applicable",
        "reasonCode": "not_applicable_to_runtime_kind",
    }
    assert _dynamic_item(report, "agent_instruction.runtime")["status"] == (
        "not_applicable"
    )


@pytest.mark.parametrize(
    "manifest",
    [
        "---\ndescription: Missing name.\n---\n",
        "---\nname: 42\ndescription: Non-string name.\n---\n",
        '---\nname: "valid\\u0001name"\ndescription: Control name.\n---\n',
        "---\nname: Bad_Name\ndescription: Invalid syntax.\n---\n",
        "---\nname: different-fixture\ndescription: Wrong directory name.\n---\n",
    ],
    ids=(
        "missing",
        "non_string",
        "control",
        "invalid_syntax",
        "directory_mismatch",
    ),
)
def test_invalid_manifest_name_fails_before_calling_runner(
    tmp_path, manifest, monkeypatch,
):
    snapshot, file_bytes = _skill_snapshot(tmp_path, manifest=manifest)
    _forbid_default_runner_construction(monkeypatch)

    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=_enabled_config(),
        ),
        bandit_runner=_CompletedBanditRunner(),
    )
    report = review_to_dict(review)

    assert review.agentInstructionRuntime == {
        "status": "failed",
        "reasonCode": "agent_runtime_manifest_name_invalid",
    }
    assert report["agentInstructionRuntime"] == review.agentInstructionRuntime
    assert _dynamic_item(report, "agent_instruction.runtime")["status"] == (
        "selected"
    )


def test_runner_exception_uses_generic_reason_without_raw_text(tmp_path):
    snapshot, file_bytes = _skill_snapshot(tmp_path)

    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=_enabled_config(),
        ),
        bandit_runner=_CompletedBanditRunner(),
        agent_runtime_runner=_ExplodingAgentRuntimeRunner(),
    )
    report = review_to_dict(review)
    serialized = json.dumps(report, sort_keys=True)

    assert review.agentInstructionRuntime == {
        "status": "failed",
        "reasonCode": "agent_runtime_adapter_failed",
    }
    assert "RAW_RUNTIME_EXCEPTION_SENTINEL" not in serialized
    assert "credential-value" not in serialized
    assert "patch-body" not in serialized


def test_non_observation_runner_result_fails_closed_without_raw_fields(tmp_path):
    class InvalidObservation:
        status = "completed"
        rawResponse = "RAW_RUNTIME_RESPONSE_SENTINEL"

    snapshot, file_bytes = _skill_snapshot(tmp_path)
    runner = _RecordingAgentRuntimeRunner(InvalidObservation())

    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=_enabled_config(),
        ),
        bandit_runner=_CompletedBanditRunner(),
        agent_runtime_runner=runner,
    )
    report = review_to_dict(review)
    serialized = json.dumps(report, sort_keys=True)

    assert review.agentInstructionRuntime == {
        "status": "failed",
        "reasonCode": "agent_runtime_invalid_observation",
    }
    assert "RAW_RUNTIME_RESPONSE_SENTINEL" not in serialized
    assert "rawResponse" not in report["agentInstructionRuntime"]


def test_timeout_observation_is_normalized_to_failed_and_drops_extra_fields(
    tmp_path,
):
    from verity.agent_runtime import AgentRuntimeObservation

    snapshot, file_bytes = _skill_snapshot(tmp_path)
    observation = AgentRuntimeObservation(
        status="timeout",
        reasonCode="agent_runtime_wall_clock_exceeded",
        stdoutBytes=17,
        truncated={"stdout": True},
    )
    object.__setattr__(
        observation,
        "rawResponse",
        "RAW_TIMEOUT_RESPONSE_SENTINEL",
    )
    runner = _RecordingAgentRuntimeRunner(observation)

    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=_enabled_config(),
        ),
        bandit_runner=_CompletedBanditRunner(),
        agent_runtime_runner=runner,
    )
    report = review_to_dict(review)
    serialized = json.dumps(report, sort_keys=True)

    assert report["agentInstructionRuntime"]["status"] == "failed"
    assert report["agentInstructionRuntime"]["observationStatus"] == "timeout"
    assert report["agentInstructionRuntime"]["reasonCode"] == (
        "agent_runtime_wall_clock_exceeded"
    )
    assert report["capabilities"]["agentInstructionRuntime"]["status"] == "failed"
    assert "RAW_TIMEOUT_RESPONSE_SENTINEL" not in serialized
    assert "rawResponse" not in report["agentInstructionRuntime"]


def test_default_runner_is_constructed_only_after_enabled_agent_gates(
    tmp_path, monkeypatch,
):
    snapshot, file_bytes = _skill_snapshot(tmp_path)
    config = _enabled_config()
    runner = _RecordingAgentRuntimeRunner(_completed_observation(config))
    constructions = []

    def runner_factory():
        constructions.append(True)
        return runner

    monkeypatch.setattr(
        "verity.agent_runtime.runner.HarnessAgentRuntimeRunner",
        runner_factory,
    )

    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=config,
        ),
        bandit_runner=_CompletedBanditRunner(),
    )

    assert constructions == [True]
    assert len(runner.calls) == 1
    assert review.agentInstructionRuntime["status"] == "completed"


def test_default_runner_constructor_exception_is_redacted(tmp_path, monkeypatch):
    snapshot, file_bytes = _skill_snapshot(tmp_path)

    def exploding_factory():
        raise RuntimeError("RAW_RUNTIME_CONSTRUCTOR_SENTINEL credential-value")

    monkeypatch.setattr(
        "verity.agent_runtime.runner.HarnessAgentRuntimeRunner",
        exploding_factory,
    )

    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=_enabled_config(),
        ),
        bandit_runner=_CompletedBanditRunner(),
    )
    report = review_to_dict(review)
    serialized = json.dumps(report, sort_keys=True)

    assert review.agentInstructionRuntime == {
        "status": "failed",
        "reasonCode": "agent_runtime_adapter_failed",
    }
    assert "RAW_RUNTIME_CONSTRUCTOR_SENTINEL" not in serialized
    assert "credential-value" not in serialized


@pytest.mark.parametrize(
    "invalid_field",
    [
        "reason_code",
        "harness_name",
        "harness_version",
        "harness_sha256",
        "scenario_id",
        "scenario_outcome",
        "scenario_reason",
        "response_digest",
        "tool_name",
        "target_class",
        "tool_outcome",
        "duration_nan",
        "stdout_negative",
        "truncated_extra_key",
        "scenario_container_list",
    ],
)
def test_declared_observation_fields_are_validated_before_projection(
    tmp_path, invalid_field,
):
    from verity.agent_runtime import (
        AgentRuntimeObservation,
        AgentRuntimeScenarioResult,
        AgentRuntimeToolEvent,
    )

    sentinel = (
        "RAW_DECLARED_FIELD_SENTINEL credential-value host-path "
        "patch-body tool-argument"
    )
    observation_status = "completed"
    observation_reason = None
    harness_name = "dsh"
    harness_version = "0.1.1-rc.2"
    harness_sha256 = "1" * 64
    duration_seconds = 0.1
    stdout_bytes = 1
    truncated = {"stdout": False, "stderr": False, "traceEvents": False}
    scenario_id = "agent_primary_task"
    scenario_outcome = "completed"
    scenario_reasons = ()
    response_digest = "3" * 64
    tool_name = "read_file"
    target_class = "project_public"
    tool_outcome = "completed"

    if invalid_field == "reason_code":
        observation_status = "failed"
        observation_reason = sentinel
    elif invalid_field == "harness_name":
        harness_name = sentinel
    elif invalid_field == "harness_version":
        harness_version = sentinel
    elif invalid_field == "harness_sha256":
        harness_sha256 = sentinel
    elif invalid_field == "scenario_id":
        scenario_id = sentinel
    elif invalid_field == "scenario_outcome":
        scenario_outcome = sentinel
    elif invalid_field == "scenario_reason":
        observation_status = "failed"
        observation_reason = "agent_runtime_failed"
        scenario_outcome = "failed"
        scenario_reasons = (sentinel,)
        response_digest = None
    elif invalid_field == "response_digest":
        response_digest = sentinel
    elif invalid_field == "tool_name":
        tool_name = sentinel
    elif invalid_field == "target_class":
        target_class = sentinel
    elif invalid_field == "tool_outcome":
        tool_outcome = sentinel
    elif invalid_field == "duration_nan":
        duration_seconds = float("nan")
    elif invalid_field == "stdout_negative":
        stdout_bytes = -1
    elif invalid_field == "truncated_extra_key":
        truncated["rawResponse"] = True

    event = AgentRuntimeToolEvent(
        tool_name=tool_name,
        target_class=target_class,
        outcome=tool_outcome,
    )
    scenario = AgentRuntimeScenarioResult(
        scenario_id=scenario_id,
        outcome=scenario_outcome,
        reason_codes=scenario_reasons,
        response_digest=response_digest,
        tool_events=(event,),
    )
    scenario_results = [scenario] if invalid_field == "scenario_container_list" else (
        scenario,
    )
    observation = AgentRuntimeObservation(
        status=observation_status,
        reasonCode=observation_reason,
        harnessName=harness_name,
        harnessVersion=harness_version,
        harnessSha256=harness_sha256,
        durationSeconds=duration_seconds,
        scenarioResults=scenario_results,
        stdoutBytes=stdout_bytes,
        stderrBytes=0,
        truncated=truncated,
    )
    snapshot, file_bytes = _skill_snapshot(tmp_path)

    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=_enabled_config(),
        ),
        bandit_runner=_CompletedBanditRunner(),
        agent_runtime_runner=_RecordingAgentRuntimeRunner(observation),
    )
    report = review_to_dict(review)
    serialized = json.dumps(report, sort_keys=True)

    assert review.agentInstructionRuntime == {
        "status": "failed",
        "reasonCode": "agent_runtime_invalid_observation",
    }
    assert "RAW_DECLARED_FIELD_SENTINEL" not in serialized
    assert report["capabilities"]["agentInstructionRuntime"]["status"] == "failed"


def test_hostile_observation_container_exception_fails_closed(tmp_path):
    from verity.agent_runtime import AgentRuntimeObservation

    class HostileTuple(tuple):
        def __iter__(self):
            raise RuntimeError("RAW_HOSTILE_CONTAINER_SENTINEL")

    snapshot, file_bytes = _skill_snapshot(tmp_path)
    observation = AgentRuntimeObservation(
        status="completed",
        scenarioResults=HostileTuple(),
    )

    review = run_review(
        ReviewInputs(
            engine="skill",
            snapshot=snapshot,
            file_bytes=file_bytes,
            profile="minimal",
            agent_runtime_config=_enabled_config(),
        ),
        bandit_runner=_CompletedBanditRunner(),
        agent_runtime_runner=_RecordingAgentRuntimeRunner(observation),
    )

    assert review.agentInstructionRuntime == {
        "status": "failed",
        "reasonCode": "agent_runtime_invalid_observation",
    }
