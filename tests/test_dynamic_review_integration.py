import json
from io import BytesIO
from unittest.mock import patch

import pytest

from verity.blackbox import BlackboxConfig, BlackboxCredentials
from verity.cli import main
from verity.intake import intake_text
from verity.report import review_to_dict, to_html
from verity.review import ReviewInputs, run_review


ART_STYLE_PROMPT = """
你是画风转换器。保持主体身份，输出 positive_prompt 和 negative_prompt。
把用户主体转换为水墨画风，不得改变主体。
"""


class _UnlimitedStubOpener:
    def open(self, request, timeout):
        content = (
            "- positive_prompt: 水墨画风，一名女性手持红色雨伞\n"
            "- negative_prompt: photorealistic, 3d"
        )
        body = json.dumps({
            "choices": [{"message": {"content": content}}]
        }).encode()

        class Response:
            status = 200

            def __init__(self):
                self.stream = BytesIO(body)

            def read(self, size=-1):
                return self.stream.read(size)

            def getcode(self):
                return self.status

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        return Response()


def _run_art_review(monkeypatch, *, policy="artifact_aware", scenario_ids=()):
    monkeypatch.setenv("VERITY_TEST_BLACKBOX_KEY", "not-a-real-secret")
    snapshot, file_bytes = intake_text(ART_STYLE_PROMPT)
    config = BlackboxConfig(
        enabled=True,
        base_url="https://stub.example/v1",
        model_id="stub-model",
        credentials=BlackboxCredentials(
            api_key_env="VERITY_TEST_BLACKBOX_KEY"
        ),
        scenario_policy=policy,
        scenario_ids=scenario_ids,
        max_calls=64,
    )
    with patch(
        "verity.blackbox.runner._build_opener",
        return_value=_UnlimitedStubOpener(),
    ):
        return run_review(ReviewInputs(
            engine="prompt",
            snapshot=snapshot,
            file_bytes=file_bytes,
            blackbox_config=config,
            semantic_config=None,
        ))


def test_review_always_reports_behavior_profile_and_dynamic_plan():
    snapshot, file_bytes = intake_text(ART_STYLE_PROMPT)
    report = review_to_dict(run_review(ReviewInputs(
        engine="prompt", snapshot=snapshot, file_bytes=file_bytes,
    )))

    assert report["behaviorProfile"]["domain_tags"] == ["art_style"]
    visual = next(
        item for item in report["dynamicPlan"]["items"]
        if item["check_id"] == "art_style.visual_fidelity"
    )
    assert visual["status"] == "unavailable"
    assert visual["reason_codes"] == ["image_runtime_not_configured"]


def test_html_report_describes_skill_execution_as_unavailable():
    snapshot, file_bytes = intake_text(ART_STYLE_PROMPT)
    review = run_review(ReviewInputs(
        engine="prompt", snapshot=snapshot, file_bytes=file_bytes,
    ))
    report = review_to_dict(review)
    html = to_html(review)

    assert "sandbox_isolation_hardening_required" in html
    assert "does not execute a reviewed Skill" in html
    assert "V2 isolated Skill sandbox" not in html
    sandbox_note = report["capabilities"]["skillSandbox"]["note"]
    assert "unavailable" in sandbox_note
    assert "sandbox_isolation_hardening_required" in sandbox_note
    assert "integrated but OFF by default" not in sandbox_note


def test_artifact_aware_blackbox_runs_only_selected_scenarios(monkeypatch):
    report = review_to_dict(_run_art_review(monkeypatch))
    blackbox = report["promptBlackbox"]
    executed = {item["scenario_id"] for item in blackbox["scenarioResults"]}
    plan = {item["check_id"]: item for item in report["dynamicPlan"]["items"]}

    assert blackbox["scenarioPolicy"] == "artifact_aware"
    assert blackbox["plannedScenarioCount"] < 26
    assert "art_style.prompt_contract" in executed
    assert plan["embedded_credential_extraction_request"]["status"] == "not_applicable"
    assert "embedded_credential_extraction_request" not in executed


def test_all_policy_preserves_historical_builtin_scenario_set(monkeypatch):
    report = review_to_dict(_run_art_review(monkeypatch, policy="all"))

    assert report["promptBlackbox"]["scenarioPolicy"] == "all"
    assert report["promptBlackbox"]["plannedScenarioCount"] == 26


def test_unknown_explicit_scenario_is_rejected_before_credentials(monkeypatch):
    monkeypatch.delenv("VERITY_MISSING_BLACKBOX_KEY", raising=False)
    snapshot, file_bytes = intake_text(ART_STYLE_PROMPT)
    config = BlackboxConfig(
        enabled=True,
        base_url="https://stub.example/v1",
        model_id="stub-model",
        credentials=BlackboxCredentials(
            api_key_env="VERITY_MISSING_BLACKBOX_KEY"
        ),
        scenario_ids=("does_not_exist",),
    )

    review = run_review(ReviewInputs(
        engine="prompt",
        snapshot=snapshot,
        file_bytes=file_bytes,
        blackbox_config=config,
    ))

    assert review.promptBlackbox["reasonCode"] == "unknown_scenario:does_not_exist"


def test_cli_exposes_blackbox_scenario_policy(capsys):
    with pytest.raises(SystemExit):
        main(["review", "--help"])

    assert "--blackbox-scenario-policy" in capsys.readouterr().out


def test_cli_requested_blackbox_failure_is_a_coverage_block(tmp_path, capsys):
    out = tmp_path / "blackbox-out"

    code = main([
        "review",
        "--engine", "prompt",
        "--text", "Summarize the supplied article.",
        "--no-semantic",
        "--enable-prompt-blackbox",
        "--out", str(out),
    ])

    assert code == 3
    captured = capsys.readouterr()
    assert "promptBlackbox=failed" in captured.out
    assert "gate=coverage_block" in captured.out
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["promptBlackbox"]["status"] == "failed"
    assert report["capabilities"]["promptBlackbox"]["status"] == "failed"


def test_cli_requested_sandbox_failure_is_a_coverage_block(tmp_path, capsys):
    skill = tmp_path / "clean-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\n"
        "name: clean-skill\n"
        "description: Summarize local text when a concise summary is requested.\n"
        "---\n"
        "# Clean skill\n\n"
        "Read the supplied text and return a concise summary.\n",
        encoding="utf-8",
    )
    out = tmp_path / "sandbox-out"

    code = main([
        "review",
        "--engine", "skill",
        "--input-dir", str(skill),
        "--profile", "minimal",
        "--no-semantic",
        "--enable-skill-sandbox",
        "--out", str(out),
    ])

    assert code == 3
    captured = capsys.readouterr()
    assert "skillSandbox=failed" in captured.out
    assert "gate=coverage_block" in captured.out
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["skillSandbox"]["status"] == "failed"
    assert report["capabilities"]["skillSandbox"]["status"] == "failed"
