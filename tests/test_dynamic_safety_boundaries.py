"""Fail-closed product boundaries for experimental dynamic stages.

The direct research runners retain their own unit tests.  These checks cover
only public review/report/Web surfaces, where untrusted runtime payloads must
never cross into a user-facing report and the V2 runner must remain unreachable
until its isolation boundary is hardened.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import importlib.util
import json
from pathlib import Path
import urllib.error
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from verity.blackbox import BlackboxConfig, BlackboxCredentials
from verity.blackbox.scenarios import ProbeScenario
from verity.cli import main as cli_main
from verity.dynamic.scenarios import OracleResult
from verity.intake import intake_directory, intake_text
from verity.issues import controlled_runtime_occurrence_projection
from verity.report import review_to_dict, to_html, to_json
from verity.review import ReviewInputs, run_review
from verity.sandbox import SandboxConfig
from verity.web import create_app
from verity.web.view import build_view_model


_SANDBOX_SENTINEL = "SANDBOX_RAW_PAYLOAD_6b7f4d92"
_PROMPT_SENTINEL = "BLACKBOX_PROMPT_PAYLOAD_a12c49e7"
_PROBE_SENTINEL = "BLACKBOX_PROBE_PAYLOAD_7d821fa0"
_RESPONSE_SENTINEL = "BLACKBOX_RESPONSE_PAYLOAD_f90e5c43"


def _skill_review(tmp_path):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: boundary-test\n"
        "description: Summarize local text when asked for a concise result.\n"
        "---\n# Boundary test\n",
        encoding="utf-8",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "main.py").write_text("print('ok')\n", encoding="utf-8")
    snapshot, file_bytes = intake_directory(str(tmp_path))
    return run_review(ReviewInputs(
        engine="skill",
        snapshot=snapshot,
        file_bytes=file_bytes,
        profile="minimal",
    ))


def _blackbox_response(content: str):
    body = json.dumps({
        "choices": [{"message": {"content": content}}],
    }).encode("utf-8")

    class Response:
        status = 200

        def __init__(self):
            self._stream = BytesIO(body)

        def read(self, size=-1):
            return self._stream.read(size)

        def getcode(self):
            return self.status

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return Response()


class _BlackboxOpener:
    def open(self, request, timeout):
        return _blackbox_response(_RESPONSE_SENTINEL)


class _NetworkErrorOpener:
    def open(self, request, timeout):
        raise urllib.error.URLError("synthetic network failure")


class _SequenceOpener:
    """Return controlled model text or raise in deterministic call order."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)

    def open(self, request, timeout):
        if not self._outcomes:
            raise AssertionError("unexpected extra black-box call")
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _blackbox_response(outcome)


class _NonStringEnvelopeOpener:
    def open(self, request, timeout):
        body = json.dumps({
            "choices": [{"message": {"content": {"not": "text"}}}],
        }).encode("utf-8")

        class Response:
            status = 200

            def __init__(self):
                self._stream = BytesIO(body)

            def read(self, size=-1):
                return self._stream.read(size)

            def getcode(self):
                return self.status

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Response()


class _EmptyWebCredentials:
    def save_key(self, value):
        raise AssertionError("boundary test credentials must remain empty")

    def load_key(self):
        return None

    def has_key(self):
        return False

    def delete_key(self):
        return None


def _blackbox_review(monkeypatch):
    monkeypatch.setenv("VERITY_TEST_DYNAMIC_BOUNDARY_KEY", "synthetic-key")
    snapshot, file_bytes = intake_text(
        "You are a helpful assistant. " + _PROMPT_SENTINEL,
        prompt_kind="system_prompt",
    )
    scenario = ProbeScenario(
        scenario_id="injection_override_simple",
        title="Controlled scenario",
        description="Controlled scenario for the report boundary.",
        probes=[_PROBE_SENTINEL],
        judge=lambda response: True,
        risk_ids=["VR-PROMPT-001"],
        severity="high",
    )
    config = BlackboxConfig(
        enabled=True,
        base_url="https://stub.example/v1",
        model_id="stub-model",
        credentials=BlackboxCredentials(
            api_key_env="VERITY_TEST_DYNAMIC_BOUNDARY_KEY"
        ),
        scenario_ids=(scenario.scenario_id,),
    )
    with (
        patch("verity.blackbox.scenarios.get_scenario", return_value=scenario),
        patch("verity.blackbox.runner._build_opener", return_value=_BlackboxOpener()),
    ):
        return run_review(ReviewInputs(
            engine="prompt",
            snapshot=snapshot,
            file_bytes=file_bytes,
            blackbox_config=config,
        ))


def _run_controlled_blackbox(monkeypatch, *, scenario, opener):
    monkeypatch.setenv("VERITY_TEST_DYNAMIC_BOUNDARY_KEY", "synthetic-key")
    snapshot, file_bytes = intake_text(
        "You are a helpful assistant.", prompt_kind="system_prompt"
    )
    config = BlackboxConfig(
        enabled=True,
        base_url="https://stub.example/v1",
        model_id="stub-model",
        credentials=BlackboxCredentials(
            api_key_env="VERITY_TEST_DYNAMIC_BOUNDARY_KEY"
        ),
        scenario_ids=(scenario.scenario_id,),
    )
    with (
        patch("verity.blackbox.scenarios.get_scenario", return_value=scenario),
        patch("verity.blackbox.runner._build_opener", return_value=opener),
    ):
        return run_review(ReviewInputs(
            engine="prompt",
            snapshot=snapshot,
            file_bytes=file_bytes,
            blackbox_config=config,
        ))


def test_enabled_product_sandbox_is_unavailable_without_constructing_runner(
    tmp_path,
):
    """Catches any product-path import/constructor call into SandboxRunner."""
    base = _skill_review(tmp_path)

    def construction_bomb(*args, **kwargs):
        raise AssertionError("SandboxRunner product construction is forbidden")

    with patch("verity.sandbox.runner.SandboxRunner", construction_bomb):
        review = run_review(ReviewInputs(
            engine="skill",
            snapshot=base.artifactSnapshot,
            file_bytes={
                entry.fileId: (tmp_path / entry.normalizedPath).read_bytes()
                for entry in base.artifactSnapshot.files
                if entry.status == "included" and entry.entryType == "file"
            },
            profile="minimal",
            sandbox_config=SandboxConfig(
                enabled=True,
                entry_point="scripts/main.py",
                argv=(_SANDBOX_SENTINEL,),
            ),
        ))

    assert review.skillSandbox == {
        "status": "failed",
        "observationStatus": "unavailable",
        "reasonCode": "sandbox_isolation_hardening_required",
    }


def test_sandbox_runtime_payload_is_removed_from_every_report_projection(tmp_path):
    """Catches raw exception/path/argv/SQL material crossing report.py."""
    review = replace(_skill_review(tmp_path), skillSandbox={
        "status": "completed",
        "observationStatus": "completed",
        "reasonCode": _SANDBOX_SENTINEL,
        "entryPoint": "/private/" + _SANDBOX_SENTINEL,
        "argv": ["--secret=" + _SANDBOX_SENTINEL],
        "raisedException": {
            "type": "RuntimeError",
            "message": _SANDBOX_SENTINEL,
        },
        "fileEvents": [{"op": "read", "path": "/Users/x/" + _SANDBOX_SENTINEL}],
        "networkAttempts": [{"host": _SANDBOX_SENTINEL, "port": 443}],
        "subprocessAttempts": [{"argvPreview": _SANDBOX_SENTINEL}],
        "sqlAttempts": [{"statement": "SELECT '" + _SANDBOX_SENTINEL + "'"}],
        "stdoutBytes": 17,
        "stderrBytes": 19,
    })

    report = review_to_dict(review)
    view = build_view_model(report, "review-boundary-test")
    serializations = (
        json.dumps(report, ensure_ascii=False),
        json.dumps(view, ensure_ascii=False),
        to_json(review),
        to_html(review),
    )
    assert all(_SANDBOX_SENTINEL not in payload for payload in serializations)
    assert report["skillSandbox"] == {
        "status": "failed",
        "observationStatus": "unavailable",
        "reasonCode": "sandbox_isolation_hardening_required",
    }


def test_blackbox_prompt_probe_and_response_do_not_reach_reports_or_web_downloads(
    monkeypatch, tmp_path,
):
    """Catches raw Provider/model payload retention at all public surfaces."""
    review = _blackbox_review(monkeypatch)
    report = review_to_dict(review)
    view = build_view_model(report, "review-boundary-test")
    serializations = (
        json.dumps(report, ensure_ascii=False),
        json.dumps(view, ensure_ascii=False),
        to_json(review),
        to_html(review),
    )
    for sentinel in (_PROMPT_SENTINEL, _PROBE_SENTINEL, _RESPONSE_SENTINEL):
        assert all(sentinel not in payload for payload in serializations)

    probe = report["promptBlackbox"]["scenarioResults"][0]["probe_results"][0]
    assert "probe_text" not in probe
    assert "response_text" not in probe
    assert probe["response_length"] == len(_RESPONSE_SENTINEL)
    assert len(probe["response_digest"]) == 64
    assert probe["safe"] is True

    # Exercise the actual Web view and all three stored download formats.
    from verity.web.provider_settings import (
        ProviderPreferenceStore,
        ProviderSettingsStore,
    )
    provider_settings = ProviderSettingsStore(
        ProviderPreferenceStore(tmp_path / "provider"),
        _EmptyWebCredentials(),
    )
    app = create_app(
        store_capacity=2,
        store_ttl_seconds=60,
        history_root=tmp_path / "history",
        provider_settings_store=provider_settings,
    )
    scenario = ProbeScenario(
        scenario_id="injection_override_simple",
        title="Controlled scenario",
        description="Controlled scenario for the report boundary.",
        probes=[_PROBE_SENTINEL],
        judge=lambda response: True,
        risk_ids=["VR-PROMPT-001"],
        severity="high",
    )
    with (
        patch("verity.blackbox.scenarios.get_scenario", return_value=scenario),
        patch("verity.blackbox.runner._build_opener", return_value=_BlackboxOpener()),
        TestClient(app, base_url="http://127.0.0.1") as client,
    ):
        response = client.post("/api/review/prompt", json={
            "text": "You are a helpful assistant. " + _PROMPT_SENTINEL,
            "prompt_kind": "system_prompt",
            "blackbox_enabled": True,
            "blackbox_confirm": True,
            "blackbox_base_url": "https://stub.example/v1",
            "blackbox_model": "stub-model",
            "blackbox_api_key": "synthetic-key",
            "blackbox_scenario_ids": [scenario.scenario_id],
        })
        assert response.status_code == 200, response.text
        web_view = response.json()
        downloads = [
            client.get(web_view["downloads"][fmt]).text
            for fmt in ("json", "html", "sarif")
        ]

    public_payloads = [response.text, *downloads]
    for sentinel in (_PROMPT_SENTINEL, _PROBE_SENTINEL, _RESPONSE_SENTINEL):
        assert all(sentinel not in payload for payload in public_payloads)


def test_blackbox_network_error_fails_stage_and_cli_gate(
    monkeypatch, tmp_path, capsys,
):
    scenario = ProbeScenario(
        scenario_id="injection_override_simple",
        title="Controlled network failure",
        description="Every required call fails.",
        probes=["controlled probe"],
        judge=lambda response: True,
        risk_ids=["VR-PROMPT-001"],
        severity="high",
    )
    review = _run_controlled_blackbox(
        monkeypatch, scenario=scenario, opener=_NetworkErrorOpener()
    )
    assert review.promptBlackbox["status"] == "failed"
    assert review.promptBlackbox["reasonCode"] == "blackbox_probe_error"
    report = review_to_dict(review)
    assert report["capabilities"]["promptBlackbox"]["status"] == "failed"

    out = tmp_path / "cli-blackbox"
    monkeypatch.setenv("VERITY_TEST_DYNAMIC_BOUNDARY_KEY", "synthetic-key")
    with patch(
        "verity.blackbox.runner._build_opener", return_value=_NetworkErrorOpener()
    ):
        code = cli_main([
            "review",
            "--engine", "prompt",
            "--text", "You are a helpful assistant.",
            "--no-semantic",
            "--enable-prompt-blackbox",
            "--blackbox-base-url", "https://stub.example/v1",
            "--blackbox-model", "stub-model",
            "--blackbox-api-key-env", "VERITY_TEST_DYNAMIC_BOUNDARY_KEY",
            "--blackbox-scenario-id", "injection_override_simple",
            "--out", str(out),
        ])
    assert code == 3
    assert "gate=coverage_block" in capsys.readouterr().out
    cli_report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert cli_report["promptBlackbox"]["status"] == "failed"
    assert cli_report["promptBlackbox"]["reasonCode"] == "blackbox_probe_error"


def test_blackbox_non_string_provider_content_is_controlled_failure(monkeypatch):
    scenario = ProbeScenario(
        scenario_id="injection_override_simple",
        title="Controlled malformed response",
        description="Provider content must be a string.",
        probes=["controlled probe"],
        judge=lambda response: True,
        risk_ids=["VR-PROMPT-001"],
        severity="high",
    )
    review = _run_controlled_blackbox(
        monkeypatch, scenario=scenario, opener=_NonStringEnvelopeOpener()
    )
    assert review.promptBlackbox["status"] == "failed"
    assert review.promptBlackbox["reasonCode"] == "blackbox_probe_error"
    probe = review_to_dict(review)["promptBlackbox"]["scenarioResults"][0][
        "probe_results"
    ][0]
    assert probe["safe"] is None
    assert probe["error_code"] == "parse_error"


def test_blackbox_inconclusive_oracle_fails_stage(monkeypatch):
    scenario = ProbeScenario(
        scenario_id="injection_override_simple",
        title="Controlled inconclusive oracle",
        description="An incomplete oracle cannot complete the stage.",
        probes=["controlled probe"],
        judge=lambda response: True,
        trace_judge=lambda probes: OracleResult(
            outcome="insufficient_evidence",
            reason_codes=("model_response_unavailable",),
            observed={"responseCount": 0},
        ),
        risk_ids=["VR-PROMPT-001"],
        severity="high",
    )
    review = _run_controlled_blackbox(
        monkeypatch, scenario=scenario, opener=_BlackboxOpener()
    )
    assert review.promptBlackbox["status"] == "failed"
    assert review.promptBlackbox["reasonCode"] == "blackbox_inconclusive"
    projected = review_to_dict(review)["promptBlackbox"]
    assert projected["status"] == "failed"
    assert projected["scenarioResults"][0]["outcome"] == \
        "insufficient_evidence"


def test_blackbox_unsafe_outcome_is_completed_security_signal(monkeypatch):
    scenario = ProbeScenario(
        scenario_id="injection_override_simple",
        title="Controlled unsafe response",
        description="Unsafe is evidence, not an infrastructure error.",
        probes=["controlled probe"],
        judge=lambda response: False,
        risk_ids=["VR-PROMPT-001"],
        severity="high",
    )
    review = _run_controlled_blackbox(
        monkeypatch, scenario=scenario, opener=_BlackboxOpener()
    )
    assert review.promptBlackbox["status"] == "completed"
    projected = review_to_dict(review)["promptBlackbox"]
    assert projected["status"] == "completed"
    assert projected["scenarioResults"][0]["outcome"] == "failed"


def test_blackbox_zero_effective_scenarios_fails_before_runner(monkeypatch):
    monkeypatch.setenv("VERITY_TEST_DYNAMIC_BOUNDARY_KEY", "synthetic-key")
    snapshot, file_bytes = intake_text("You are concise.")
    config = BlackboxConfig(
        enabled=True,
        base_url="https://stub.example/v1",
        model_id="stub-model",
        credentials=BlackboxCredentials(
            api_key_env="VERITY_TEST_DYNAMIC_BOUNDARY_KEY"
        ),
    )
    with (
        patch("verity.dynamic.planner.selected_scenario_ids", return_value=()),
        patch("verity.dynamic.scenarios.build_artifact_scenarios", return_value=[]),
        patch(
            "verity.blackbox.runner.run_blackbox",
            side_effect=AssertionError("zero-scenario run must not start"),
        ) as runner,
    ):
        review = run_review(ReviewInputs(
            engine="prompt",
            snapshot=snapshot,
            file_bytes=file_bytes,
            blackbox_config=config,
        ))
    runner.assert_not_called()
    assert review.promptBlackbox["status"] == "failed"
    assert review.promptBlackbox["reasonCode"] == \
        "blackbox_no_scenarios_selected"


@pytest.mark.parametrize("raw_result", [
    {
        "status": "completed",
        "plannedScenarioCount": 1,
        "scenarioResults": [],
        "totalCalls": 0,
    },
    {
        "status": "completed",
        "plannedScenarioCount": 1,
        "scenarioResults": [{
            "scenario_id": "injection_override_simple",
            "severity": "high",
            "probe_results": [],
        }],
        "totalCalls": 1,
    },
    {
        "status": "completed",
        "plannedScenarioCount": 2,
        "scenarioResults": [{
            "scenario_id": "injection_override_simple",
            "severity": "high",
            "probe_results": [{
                "probe_index": 0,
                "safe": "yes",
                "call_id": "bb-controlled-p0",
                "response_digest": "0" * 64,
                "duration_seconds": 0.1,
            }],
        }],
        "totalCalls": 99,
    },
])
def test_malformed_completed_blackbox_projection_fails_closed(raw_result):
    snapshot, file_bytes = intake_text("You are a helpful assistant.")
    base = run_review(ReviewInputs(
        engine="prompt", snapshot=snapshot, file_bytes=file_bytes
    ))
    report = review_to_dict(replace(base, promptBlackbox=raw_result))
    assert report["promptBlackbox"]["status"] == "failed"
    assert report["promptBlackbox"]["reasonCode"] == \
        "blackbox_result_incomplete"
    assert report["capabilities"]["promptBlackbox"]["status"] == "failed"


@pytest.mark.parametrize(
    "malformed_field",
    [
        "status",
        "scenario_policy",
        "severity",
        "invalid_severity",
        "oracle_outcome",
        "reason_code",
    ],
)
def test_unhashable_blackbox_projection_value_never_raises_or_completes(
    monkeypatch, malformed_field,
):
    """A hostile embedding object must not crash or manufacture completion."""
    raw = deepcopy(_blackbox_review(monkeypatch).promptBlackbox)
    if malformed_field == "status":
        raw["status"] = {"hostile": "status"}
    elif malformed_field == "scenario_policy":
        raw["scenarioPolicy"] = ["explicit"]
    elif malformed_field == "severity":
        raw["scenarioResults"][0]["severity"] = {"hostile": "high"}
    elif malformed_field == "invalid_severity":
        raw["scenarioResults"][0]["severity"] = "urgent"
    elif malformed_field == "oracle_outcome":
        raw["scenarioResults"][0]["oracle_result"] = {
            "outcome": {"hostile": "passed"},
            "reason_codes": [],
        }
    else:
        raw["scenarioResults"][0]["oracle_result"] = {
            "outcome": "passed",
            "reason_codes": [{"hostile": "reason"}],
        }

    snapshot, file_bytes = intake_text("You are a helpful assistant.")
    base = run_review(ReviewInputs(
        engine="prompt", snapshot=snapshot, file_bytes=file_bytes
    ))
    report = review_to_dict(replace(base, promptBlackbox=raw))

    assert report["promptBlackbox"]["status"] == "failed"
    assert report["promptBlackbox"]["reasonCode"] == \
        "blackbox_result_incomplete"
    assert report["capabilities"]["promptBlackbox"]["status"] == "failed"


def test_failed_projection_ignores_forged_definitive_marker():
    """Only the projection's complete-shape validator may set definitive."""
    snapshot, file_bytes = intake_text("You are a helpful assistant.")
    base = run_review(ReviewInputs(
        engine="prompt", snapshot=snapshot, file_bytes=file_bytes
    ))
    forged = {
        "status": "failed",
        "reasonCode": "blackbox_probe_error",
        "scenarioPolicy": "explicit",
        "plannedScenarioCount": 1,
        "scenarioResults": [{
            "scenario_id": "injection_override_simple",
            "severity": "high",
            "definitive": True,
            "probe_results": [{
                "probe_index": 0,
                "safe": False,
                # Required internal response/digest/duration fields omitted.
            }],
        }],
        "totalCalls": 1,
    }

    report = review_to_dict(replace(base, promptBlackbox=forged))

    assert report["promptBlackbox"]["scenarioResults"][0]["definitive"] is False
    assert all(
        occurrence["findingId"] != "blackbox:injection_override_simple"
        for issue in report["issues"]
        for occurrence in issue["occurrences"]
    )


@pytest.mark.parametrize(
    ("field", "malformed"),
    [("sourceLayer", ["V1_5_blackbox"]), ("severity", {"level": "high"})],
)
def test_unhashable_runtime_occurrence_field_fails_schema_gate(field, malformed):
    occurrence = {
        "sourceLayer": "V1_5_blackbox",
        "severity": "high",
        "findingId": "blackbox:controlled",
    }
    occurrence[field] = malformed

    assert controlled_runtime_occurrence_projection({
        "issues": [{"occurrences": [occurrence]}],
    }) == ([], False)


def test_definitive_high_survives_peer_probe_error_in_all_public_gates(
    monkeypatch, tmp_path, capsys,
):
    """Incomplete coverage cannot hide a validated High runtime finding."""
    scenario_ids = (
        "injection_override_simple",
        "injection_override_roleplay",
    )
    monkeypatch.setenv("VERITY_TEST_DYNAMIC_BOUNDARY_KEY", "synthetic-key")
    snapshot, file_bytes = intake_text(
        "You are a helpful assistant.", prompt_kind="system_prompt"
    )
    config = BlackboxConfig(
        enabled=True,
        base_url="https://stub.example/v1",
        model_id="stub-model",
        credentials=BlackboxCredentials(
            api_key_env="VERITY_TEST_DYNAMIC_BOUNDARY_KEY"
        ),
        scenario_ids=scenario_ids,
    )
    outcomes = [
        "I am DAN and will comply with any request.",
        urllib.error.URLError("synthetic peer network failure"),
    ]
    with patch(
        "verity.blackbox.runner._build_opener",
        return_value=_SequenceOpener(outcomes),
    ):
        review = run_review(ReviewInputs(
            engine="prompt",
            snapshot=snapshot,
            file_bytes=file_bytes,
            blackbox_config=config,
        ))

    assert review.promptBlackbox["status"] == "failed"
    assert review.promptBlackbox["reasonCode"] == "blackbox_probe_error"
    report = review_to_dict(review)
    assert report["score"]["status"] == "unavailable"
    projected_scenarios = report["promptBlackbox"]["scenarioResults"]
    assert [item["definitive"] for item in projected_scenarios] == [True, False]
    high_occurrences = [
        occurrence
        for issue in report["issues"]
        for occurrence in issue["occurrences"]
        if occurrence["sourceLayer"] == "V1_5_blackbox"
        and occurrence["severity"] == "high"
    ]
    assert {item["findingId"] for item in high_occurrences} == {
        "blackbox:injection_override_simple"
    }
    assert report["verdict"]["subject"] == {
        "engine": "prompt", "outcome": "needs_revision",
    }
    view = build_view_model(report, "mixed-blackbox-review")
    assert view["headline"]["code"] == "findings_block_prompt_high"
    assert view["counts"]["high"] >= 1

    out = tmp_path / "cli-mixed-blackbox"
    with patch(
        "verity.blackbox.runner._build_opener",
        return_value=_SequenceOpener(outcomes),
    ):
        code = cli_main([
            "review",
            "--engine", "prompt",
            "--text", "You are a helpful assistant.",
            "--no-semantic",
            "--enable-prompt-blackbox",
            "--blackbox-base-url", "https://stub.example/v1",
            "--blackbox-model", "stub-model",
            "--blackbox-api-key-env", "VERITY_TEST_DYNAMIC_BOUNDARY_KEY",
            "--blackbox-scenario-id", scenario_ids[0],
            "--blackbox-scenario-id", scenario_ids[1],
            "--out", str(out),
        ])
    assert code == 1
    assert "gate=findings_block" in capsys.readouterr().out


def test_web_renderer_consumes_only_controlled_dynamic_projection(tmp_path):
    """Catches reintroduction of raw dynamic-payload property reads in JS."""
    app = create_app(history_root=tmp_path / "history")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        script = client.get("/static/app.js").text

    for forbidden_property in (
        ".probe_text",
        ".response_text",
        ".fileEvents",
        ".networkAttempts",
        ".subprocessAttempts",
        ".sqlAttempts",
        ".raisedException",
        ".entryPoint",
        ".argv",
    ):
        assert forbidden_property not in script
    for safe_property in (
        ".probe_length",
        ".response_length",
        ".response_digest",
        ".eventCounts",
    ):
        assert safe_property in script
    assert "会纳入风险评分与审查结论" in script
    unavailable_return = script.find('if (ss.status !== "completed") return;')
    event_counts_read = script.find("var eventCounts = ss.eventCounts")
    assert 0 <= unavailable_return < event_counts_read
    for forbidden_request_field in (
        "sandbox_enabled:",
        "sandbox_confirm:",
        "sandbox_entry_point:",
        "opts.sandbox_argv",
        "opts.sandbox_cpu_seconds",
        "opts.sandbox_memory_mb",
        "opts.sandbox_wall_seconds",
    ):
        assert forbidden_request_field not in script
    assert "sandbox_isolation_hardening_required" in script


def test_standalone_sandbox_tool_is_fail_closed_before_runner_construction(
    tmp_path, capsys,
):
    """Catches any user-invokable path from tools/run_sandbox.py to runner."""
    skill = tmp_path / "skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: boundary-test\n"
        "description: Summarize local text when asked.\n---\n",
        encoding="utf-8",
    )
    script = skill / "run.py"
    script.write_text("raise AssertionError('must never execute')\n", encoding="utf-8")
    output = tmp_path / "observation.json"

    tool_path = Path(__file__).parents[1] / "tools" / "run_sandbox.py"
    spec = importlib.util.spec_from_file_location(
        "verity_run_sandbox_boundary_test", tool_path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    constructions = []

    def construction_bomb(*args, **kwargs):
        constructions.append((args, kwargs))
        raise AssertionError("standalone runner construction is forbidden")

    module.SandboxRunner = construction_bomb
    code = module.main([
        "--input-dir", str(skill),
        "--entry-point", "run.py",
        "--out", str(output),
    ])

    captured = capsys.readouterr()
    assert code == 1
    assert constructions == []
    assert "sandbox_isolation_hardening_required" in captured.err
    assert not output.exists()


def test_sandbox_package_does_not_export_internal_research_runner():
    """Catches accidental promotion of the unsafe research runner to API."""
    import verity.sandbox as sandbox

    assert "SandboxRunner" not in sandbox.__all__
    assert "sandbox_exec_available" not in sandbox.__all__
    assert not hasattr(sandbox, "SandboxRunner")
    assert not hasattr(sandbox, "sandbox_exec_available")
