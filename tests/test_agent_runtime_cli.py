"""Task 4: explicit CLI opt-in and agent-runtime exit-gate contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

from verity.cli import main as cli_main


def _instruction_skill(tmp_path: Path, *, risky: bool = False) -> Path:
    root = tmp_path / "runtime-fixture"
    root.mkdir()
    permissions = "permissions:\n  - '*'\n" if risky else ""
    (root / "SKILL.md").write_text(
        "---\n"
        "name: runtime-fixture\n"
        "description: Review a synthetic agent instruction fixture when testing runtime behavior.\n"
        f"{permissions}"
        "---\n"
        "Follow the caller's instructions using only declared tools.\n",
        encoding="utf-8",
    )
    return root


def _bare_skill_args(skill: Path, out: Path) -> list[str]:
    return [
        "review",
        "--engine",
        "skill",
        "--input-dir",
        str(skill),
        "--profile",
        "minimal",
        "--no-semantic",
        "--out",
        str(out),
    ]


def _enabled_runtime_args() -> list[str]:
    return [
        "--enable-agent-runtime",
        "--agent-runtime-node-path",
        "/synthetic/runtime/node",
        "--agent-runtime-node-sha256",
        "1" * 64,
        "--agent-runtime-dsh-path",
        "/synthetic/runtime/dsh.mjs",
        "--agent-runtime-dsh-sha256",
        "2" * 64,
        "--agent-runtime-base-url",
        "https://runtime.invalid/v1",
        "--agent-runtime-model",
        "fixture-model",
        "--agent-runtime-api-key-env",
        "VERITY_RUNTIME_TEST_KEY",
    ]


def _replace_flag(args: list[str], flag: str, *replacement: str) -> list[str]:
    values = list(args)
    index = values.index(flag)
    del values[index:index + 2]
    values.extend(replacement)
    return values


class _DeterministicRuntimeRunner:
    def __init__(self, *, status: str = "completed", events=(), raw_fields=None):
        self.status = status
        self.events = tuple(events)
        self.raw_fields = dict(raw_fields or {})
        self.calls = []

    def run(self, **kwargs):
        from verity.agent_runtime import (
            AgentRuntimeObservation,
            AgentRuntimeScenarioResult,
        )

        self.calls.append(kwargs)
        config = kwargs["config"]
        if self.status == "failed":
            return AgentRuntimeObservation(
                status="failed",
                reasonCode="agent_runtime_process_failed",
            )
        if self.status == "timeout":
            return AgentRuntimeObservation(
                status="timeout",
                reasonCode="agent_runtime_wall_clock_exceeded",
            )
        scenarios = tuple(
            AgentRuntimeScenarioResult(
                scenario_id=scenario_id,
                outcome="completed",
                response_digest=f"{index + 3:x}" * 64,
                tool_events=self.events if index == 0 else (),
            )
            for index, scenario_id in enumerate(config.scenario_ids)
        )
        observation = AgentRuntimeObservation(
            status="completed",
            harnessName="dsh",
            harnessVersion=config.expected_version,
            harnessSha256=config.dsh_sha256.lower(),
            durationSeconds=0.01,
            scenarioResults=scenarios,
            stdoutBytes=0,
            stderrBytes=0,
            truncated={
                "stdout": False,
                "stderr": False,
                "traceEvents": False,
            },
        )
        for name, value in self.raw_fields.items():
            object.__setattr__(observation, name, value)
        return observation


def _install_runtime_runner(monkeypatch, runner):
    monkeypatch.setattr(
        "verity.agent_runtime.runner.HarnessAgentRuntimeRunner",
        lambda: runner,
    )


def _forbid_runtime_runner_construction(monkeypatch):
    def forbidden():
        raise AssertionError("invalid CLI config constructed the runtime runner")

    monkeypatch.setattr(
        "verity.agent_runtime.runner.HarnessAgentRuntimeRunner",
        forbidden,
    )


def _runtime_event(tool_name, target_class, outcome, *, canary_present=False):
    from verity.agent_runtime import AgentRuntimeToolEvent

    return AgentRuntimeToolEvent(
        tool_name=tool_name,
        target_class=target_class,
        outcome=outcome,
        canary_present=canary_present,
    )


def _run_enabled_cli(monkeypatch, skill, out, runner, *, extra_args=()):
    _install_runtime_runner(monkeypatch, runner)
    return cli_main([
        *_bare_skill_args(skill, out),
        *_enabled_runtime_args(),
        *extra_args,
    ])


def _report_texts(out: Path) -> dict[str, str]:
    return {
        name: (out / name).read_text(encoding="utf-8")
        for name in ("report.json", "report.html", "report.sarif")
    }


def test_bare_skill_cli_stays_runtime_import_inert_and_keeps_stdout_marker_absent(
    tmp_path,
):
    skill = _instruction_skill(tmp_path)
    root = Path(__file__).resolve().parents[1]
    script = """
import builtins
import sys

real_import = builtins.__import__

def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if name == "verity.agent_runtime" or name.startswith("verity.agent_runtime."):
        raise AssertionError("bare CLI imported the agent runtime")
    return real_import(name, globals, locals, fromlist, level)

builtins.__import__ = guarded_import
from verity.cli import main
raise SystemExit(main([
    "review", "--engine", "skill", "--input-dir", sys.argv[1],
    "--profile", "minimal", "--no-semantic", "--out", sys.argv[2],
]))
"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(skill),
            str(tmp_path / "out"),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "gate=pass" in result.stdout
    assert "agentInstructionRuntime=" not in result.stdout
    assert result.stderr == ""


def test_legacy_review_flag_abbreviations_keep_their_prior_behavior(
    tmp_path, capsys,
):
    skill = _instruction_skill(tmp_path)

    code = cli_main([
        "review",
        "--eng",
        "skill",
        "--input-d",
        str(skill),
        "--prof",
        "minimal",
        "--no-sem",
        "--out",
        str(tmp_path / "out"),
    ])

    assert code == 0
    captured = capsys.readouterr()
    assert "gate=pass" in captured.out
    assert "agentInstructionRuntime=" not in captured.out
    assert captured.err == ""


def test_end_of_options_sentinel_keeps_its_legacy_parser_error(
    tmp_path, capsys,
):
    skill = _instruction_skill(tmp_path)

    with pytest.raises(SystemExit) as exc:
        cli_main([
            *_bare_skill_args(skill, tmp_path / "out"),
            "--",
        ])

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments: --" in captured.err
    assert "unrecognized agent-runtime flag" not in captured.err


def test_runtime_looking_token_after_end_of_options_keeps_parser_error(
    tmp_path, capsys,
):
    skill = _instruction_skill(tmp_path)

    with pytest.raises(SystemExit) as exc:
        cli_main([
            *_bare_skill_args(skill, tmp_path / "out"),
            "--",
            "--agent-runtime-api-key",
        ])

    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments:" in captured.err
    assert "unrecognized agent-runtime flag" not in captured.err


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--agent-runtime-node-path", "/synthetic/node"),
        ("--agent-runtime-node-sha256", "1" * 64),
        ("--agent-runtime-dsh-path", "/synthetic/dsh.mjs"),
        ("--agent-runtime-dsh-sha256", "2" * 64),
        ("--agent-runtime-version", "0.1.1-rc.2"),
        ("--agent-runtime-base-url", "https://runtime.invalid/v1"),
        ("--agent-runtime-model", "fixture-model"),
        ("--agent-runtime-api-key-env", "VERITY_RUNTIME_TEST_KEY"),
        ("--agent-runtime-scenario-id", "agent_primary_task"),
        ("--agent-runtime-timeout", "30"),
    ],
)
def test_each_agent_runtime_value_flag_requires_enable(
    tmp_path, capsys, flag, value,
):
    skill = _instruction_skill(tmp_path)

    code = cli_main([
        *_bare_skill_args(skill, tmp_path / "out"),
        flag,
        value,
    ])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "agent runtime flags require --enable-agent-runtime\n"
    )


def test_agent_runtime_enable_is_rejected_for_prompt_before_config_construction(
    monkeypatch, capsys,
):
    import builtins

    real_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if "agent_runtime" in name:
            raise AssertionError("Prompt rejection constructed runtime config")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    code = cli_main([
        "review",
        "--engine",
        "prompt",
        "--text",
        "hello",
        "--no-semantic",
        "--enable-agent-runtime",
    ])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "--enable-agent-runtime is only applicable to --engine skill\n"
    )


def test_agent_runtime_help_exposes_no_untrusted_or_permission_knobs(capsys):
    with pytest.raises(SystemExit) as exc:
        cli_main(["review", "--help"])
    assert exc.value.code == 0

    help_text = capsys.readouterr().out
    for prohibited in (
        "--agent-runtime-plugin-path",
        "--agent-runtime-cordis-patch",
        "--agent-runtime-tool-policy",
        "--agent-runtime-raw-prompt",
        "--agent-runtime-dsh-home",
        "--agent-runtime-api-key",
        "--agent-runtime-max-stdout-bytes",
        "--agent-runtime-max-stderr-bytes",
        "--agent-runtime-max-trace-events",
        "--agent-runtime-permission",
    ):
        assert re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(prohibited)}(?![A-Za-z0-9_-])",
            help_text,
        ) is None


def test_review_help_describes_skill_sandbox_as_unavailable(capsys):
    with pytest.raises(SystemExit) as exc:
        cli_main(["review", "--help"])
    assert exc.value.code == 0

    help_text = capsys.readouterr().out
    assert "sandbox_isolation_hardening_required" in help_text
    assert "does not execute the reviewed Skill" in help_text
    assert "EXECUTES" not in help_text
    assert "sandbox-exec isolation boundary" not in help_text


@pytest.mark.parametrize(
    ("exact_flag", "abbreviation"),
    [
        ("--enable-agent-runtime", "--enable-agent-runt"),
        ("--agent-runtime-node-path", "--agent-runtime-node-p"),
        ("--agent-runtime-api-key-env", "--agent-runtime-api-key"),
    ],
)
def test_review_parser_rejects_agent_runtime_flag_abbreviations(
    tmp_path, monkeypatch, capsys, exact_flag, abbreviation,
):
    skill = _instruction_skill(tmp_path)
    runner = _DeterministicRuntimeRunner()
    _install_runtime_runner(monkeypatch, runner)
    runtime_args = _enabled_runtime_args()
    runtime_args[runtime_args.index(exact_flag)] = abbreviation

    with pytest.raises(SystemExit) as exc:
        cli_main([
            *_bare_skill_args(skill, tmp_path / "out"),
            *runtime_args,
        ])

    assert exc.value.code == 2
    assert runner.calls == []
    captured = capsys.readouterr()
    assert captured.out == ""
    assert abbreviation in captured.err


@pytest.mark.parametrize(
    "missing_flag",
    [
        "--agent-runtime-node-path",
        "--agent-runtime-node-sha256",
        "--agent-runtime-dsh-path",
        "--agent-runtime-dsh-sha256",
        "--agent-runtime-base-url",
        "--agent-runtime-model",
        "--agent-runtime-api-key-env",
    ],
)
def test_enabled_agent_runtime_requires_every_trusted_cli_value(
    tmp_path, monkeypatch, capsys, missing_flag,
):
    skill = _instruction_skill(tmp_path)
    _forbid_runtime_runner_construction(monkeypatch)
    runtime_args = _replace_flag(_enabled_runtime_args(), missing_flag)

    code = cli_main([
        *_bare_skill_args(skill, tmp_path / "out"),
        *runtime_args,
    ])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "--enable-agent-runtime requires Node path/hash, DSH path/hash, "
        "base URL, model, and API-key environment-variable name\n"
    )


@pytest.mark.parametrize(
    ("flag", "bad_values"),
    [
        ("--agent-runtime-node-path", ("relative/node",)),
        ("--agent-runtime-node-sha256", ("not-a-sha256",)),
        ("--agent-runtime-dsh-path", ("relative/dsh.mjs",)),
        ("--agent-runtime-dsh-sha256", ("not-a-sha256",)),
        ("--agent-runtime-version", ("0.1.1-rc.1",)),
        ("--agent-runtime-base-url", ("http://runtime.invalid/v1",)),
        ("--agent-runtime-api-key-env", ("invalid-env-name",)),
        ("--agent-runtime-scenario-id", ("unknown_scenario",)),
        (
            "--agent-runtime-scenario-id",
            ("agent_primary_task", "--agent-runtime-scenario-id", "agent_primary_task"),
        ),
        ("--agent-runtime-timeout", ("0",)),
        ("--agent-runtime-timeout", ("nan",)),
    ],
)
def test_invalid_agent_runtime_values_are_controlled_before_runner_construction(
    tmp_path, monkeypatch, capsys, flag, bad_values,
):
    skill = _instruction_skill(tmp_path)
    _forbid_runtime_runner_construction(monkeypatch)
    runtime_args = _enabled_runtime_args()
    if flag in runtime_args:
        runtime_args = _replace_flag(runtime_args, flag, flag, *bad_values)
    else:
        runtime_args.extend((flag, *bad_values))

    code = cli_main([
        *_bare_skill_args(skill, tmp_path / "out"),
        *runtime_args,
    ])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "invalid --agent-runtime configuration\n"


def test_non_numeric_agent_runtime_timeout_is_a_controlled_usage_error(
    tmp_path, monkeypatch, capsys,
):
    skill = _instruction_skill(tmp_path)
    _forbid_runtime_runner_construction(monkeypatch)

    code = cli_main([
        *_bare_skill_args(skill, tmp_path / "out"),
        *_enabled_runtime_args(),
        "--agent-runtime-timeout",
        "not-a-number",
    ])

    assert code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "invalid --agent-runtime configuration\n"


def test_valid_agent_runtime_config_reaches_review_inputs_with_safe_defaults(
    tmp_path, monkeypatch,
):
    import verity.cli as cli_module

    secret_value = "RAW_API_KEY_VALUE_SENTINEL"
    monkeypatch.setenv("VERITY_RUNTIME_TEST_KEY", secret_value)
    skill = _instruction_skill(tmp_path)
    runner = _DeterministicRuntimeRunner()
    _install_runtime_runner(monkeypatch, runner)
    captured_inputs = []
    real_run_review = cli_module.run_review

    def recording_run_review(review_inputs, **kwargs):
        captured_inputs.append(review_inputs)
        return real_run_review(review_inputs, **kwargs)

    monkeypatch.setattr(cli_module, "run_review", recording_run_review)

    code = cli_main([
        *_bare_skill_args(skill, tmp_path / "out"),
        *_enabled_runtime_args(),
    ])

    assert code == 0
    assert len(captured_inputs) == 1
    config = captured_inputs[0].agent_runtime_config
    assert config.enabled is True
    assert config.node_executable == "/synthetic/runtime/node"
    assert config.node_sha256 == "1" * 64
    assert config.dsh_executable == "/synthetic/runtime/dsh.mjs"
    assert config.dsh_sha256 == "2" * 64
    assert config.expected_version == "0.1.1-rc.2"
    assert config.base_url == "https://runtime.invalid/v1"
    assert config.model_id == "fixture-model"
    assert config.credentials.api_key_env == "VERITY_RUNTIME_TEST_KEY"
    assert config.scenario_ids == (
        "agent_primary_task",
        "agent_untrusted_content",
    )
    assert config.timeout_seconds == 90.0
    assert secret_value not in repr(config)
    assert runner.calls[0]["config"] is config


def test_repeated_scenarios_replace_defaults_and_preserve_caller_order(
    tmp_path, monkeypatch,
):
    import verity.cli as cli_module

    skill = _instruction_skill(tmp_path)
    runner = _DeterministicRuntimeRunner()
    _install_runtime_runner(monkeypatch, runner)
    captured_inputs = []
    real_run_review = cli_module.run_review

    def recording_run_review(review_inputs, **kwargs):
        captured_inputs.append(review_inputs)
        return real_run_review(review_inputs, **kwargs)

    monkeypatch.setattr(cli_module, "run_review", recording_run_review)
    runtime_args = [
        *_enabled_runtime_args(),
        "--agent-runtime-version",
        "0.1.1-rc.2",
        "--agent-runtime-scenario-id",
        "agent_untrusted_content",
        "--agent-runtime-scenario-id",
        "agent_primary_task",
        "--agent-runtime-timeout",
        "12.5",
    ]

    assert cli_main([
        *_bare_skill_args(skill, tmp_path / "out"),
        *runtime_args,
    ]) == 0

    config = captured_inputs[0].agent_runtime_config
    assert config.scenario_ids == (
        "agent_untrusted_content",
        "agent_primary_task",
    )
    assert config.timeout_seconds == 12.5


def test_completed_no_hit_runtime_exits_zero_writes_reports_and_adds_marker(
    tmp_path, monkeypatch, capsys,
):
    skill = _instruction_skill(tmp_path)
    out = tmp_path / "out"

    code = _run_enabled_cli(
        monkeypatch,
        skill,
        out,
        _DeterministicRuntimeRunner(),
    )

    assert code == 0
    captured = capsys.readouterr()
    assert "agentInstructionRuntime=completed" in captured.out
    assert "gate=pass" in captured.out
    assert captured.err == ""
    assert set(_report_texts(out)) == {
        "report.json",
        "report.html",
        "report.sarif",
    }


def test_medium_network_attempt_alone_is_counted_but_does_not_block(
    tmp_path, monkeypatch, capsys,
):
    skill = _instruction_skill(tmp_path)
    out = tmp_path / "out"
    runner = _DeterministicRuntimeRunner(events=(
        _runtime_event("send_http", "network", "blocked"),
    ))

    code = _run_enabled_cli(monkeypatch, skill, out, runner)

    assert code == 0
    captured = capsys.readouterr()
    assert "findings=1 high_or_critical=0" in captured.out
    assert "agentInstructionRuntime=completed" in captured.out
    assert "gate=pass" in captured.out
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["verdict"]["subject"] == {
        "engine": "skill",
        "outcome": "review_required",
    }
    from verity.web.view import build_view_model

    view = build_view_model(report, "runtime-medium")
    assert view["headline"]["code"] == "review_required_skill"
    assert view["counts"]["medium"] == 1


@pytest.mark.parametrize("runtime_status", ["failed", "timeout"])
def test_requested_incomplete_runtime_exits_three_after_writing_all_reports(
    tmp_path, monkeypatch, capsys, runtime_status,
):
    skill = _instruction_skill(tmp_path)
    out = tmp_path / "out"

    code = _run_enabled_cli(
        monkeypatch,
        skill,
        out,
        _DeterministicRuntimeRunner(status=runtime_status),
    )

    assert code == 3
    captured = capsys.readouterr()
    assert "agentInstructionRuntime=failed" in captured.out
    assert "gate=coverage_block" in captured.out
    assert captured.err == ""
    reports = _report_texts(out)
    report = json.loads(reports["report.json"])
    assert report["agentInstructionRuntime"]["status"] == "failed"
    assert report["capabilities"]["agentInstructionRuntime"]["status"] == "failed"
    assert report["verdict"]["subject"] is None
    assert (
        "agent_runtime_requested_but_incomplete"
        in report["verdict"]["reasonCodes"]
    )
    assert "AGENT RUNTIME INCOMPLETE" in reports["report.html"]
    from verity.web.view import build_view_model

    view = build_view_model(report, "runtime-incomplete")
    assert view["headline"]["code"] == "agent_runtime_block"
    assert view["headline"]["tone"] == "warning"
    if runtime_status == "timeout":
        assert report["agentInstructionRuntime"]["observationStatus"] == "timeout"


def test_requested_inconsistent_runtime_projection_exits_three_after_reports(
    tmp_path, monkeypatch, capsys,
):
    import verity.cli as cli_module

    skill = _instruction_skill(tmp_path)
    out = tmp_path / "out"
    _install_runtime_runner(monkeypatch, _DeterministicRuntimeRunner())
    real_review_to_dict = cli_module.review_to_dict

    def inconsistent_projection(review):
        report = real_review_to_dict(review)
        report["capabilities"]["agentInstructionRuntime"]["status"] = "failed"
        return report

    monkeypatch.setattr(cli_module, "review_to_dict", inconsistent_projection)

    code = cli_main([
        *_bare_skill_args(skill, out),
        *_enabled_runtime_args(),
    ])

    assert code == 3
    captured = capsys.readouterr()
    assert "gate=coverage_block" in captured.out
    assert set(_report_texts(out)) == {
        "report.json",
        "report.html",
        "report.sarif",
    }


@pytest.mark.parametrize(
    "event_specs",
    [
        (("read_file", "synthetic_sensitive", "completed", False),),
        (("run_shell", "shell", "blocked", False),),
        (("send_http", "network", "blocked", True),),
    ],
    ids=("synthetic-sensitive-read", "blocked-shell", "fake-credential-marker"),
)
def test_high_agent_runtime_signal_exits_one(
    tmp_path, monkeypatch, capsys, event_specs,
):
    skill = _instruction_skill(tmp_path)
    events = tuple(
        _runtime_event(
            tool_name,
            target_class,
            outcome,
            canary_present=canary_present,
        )
        for tool_name, target_class, outcome, canary_present in event_specs
    )

    out = tmp_path / "out"
    code = _run_enabled_cli(
        monkeypatch,
        skill,
        out,
        _DeterministicRuntimeRunner(events=events),
    )

    assert code == 1
    captured = capsys.readouterr()
    assert "high_or_critical=1" in captured.out
    assert "agentInstructionRuntime=completed" in captured.out
    assert "gate=findings_block" in captured.out
    reports = _report_texts(out)
    report = json.loads(reports["report.json"])
    assert report["verdict"]["subject"] == {
        "engine": "skill",
        "outcome": "do_not_install",
    }
    assert "high_or_critical_finding_present" in report["verdict"]["reasonCodes"]
    assert 'class="banner bad"' in reports["report.html"]
    assert "DO_NOT_INSTALL" in reports["report.html"]
    from verity.web.view import build_view_model

    view = build_view_model(report, "runtime-high")
    assert view["headline"]["code"] == "findings_block_skill_high"
    assert view["counts"]["high"] == 1


def test_static_high_finding_keeps_priority_over_runtime_failure(
    tmp_path, monkeypatch, capsys,
):
    skill = _instruction_skill(tmp_path, risky=True)

    code = _run_enabled_cli(
        monkeypatch,
        skill,
        tmp_path / "out",
        _DeterministicRuntimeRunner(status="failed"),
    )

    assert code == 1
    captured = capsys.readouterr()
    assert "high_or_critical=1" in captured.out
    assert "agentInstructionRuntime=failed" in captured.out
    assert "gate=findings_block" in captured.out


def test_runtime_secrets_and_raw_fields_never_reach_any_cli_or_report_surface(
    tmp_path, monkeypatch, capsys,
):
    raw_key = "RAW_API_KEY_VALUE_SENTINEL"
    raw_model = "RAW_MODEL_RESPONSE_SENTINEL"
    raw_tool = "RAW_TOOL_ARGUMENT_SENTINEL"
    raw_canary = "RAW_CREDENTIAL_CANARY_SENTINEL"
    monkeypatch.setenv("VERITY_RUNTIME_TEST_KEY", raw_key)
    event = _runtime_event("send_http", "network", "blocked")
    object.__setattr__(event, "arguments", raw_tool)
    object.__setattr__(event, "credentialCanary", raw_canary)
    runner = _DeterministicRuntimeRunner(
        events=(event,),
        raw_fields={"rawResponse": raw_model},
    )
    skill = _instruction_skill(tmp_path)
    out = tmp_path / "out"

    assert _run_enabled_cli(monkeypatch, skill, out, runner) == 0

    captured = capsys.readouterr()
    surfaces = captured.out + captured.err + "".join(_report_texts(out).values())
    for sentinel in (raw_key, raw_model, raw_tool, raw_canary):
        assert sentinel not in surfaces
