from verity.dynamic.planner import build_dynamic_plan
from verity.dynamic.profile import ArtifactBehaviorProfile, ProfileFact
from verity.dynamic.scenarios import (
    art_style_prompt_contract_oracle,
    build_artifact_scenarios,
    director_duration_oracle,
    required_input_oracle,
    subject_preservation_oracle,
    term_conflict_oracle,
)


def _facts(entries):
    return tuple(
        ProfileFact(
            fact_id=f"pf-{index}",
            kind=kind,
            value=value,
            source_path="SKILL.md",
            start_byte=index,
            end_byte=index + 1,
        )
        for index, (kind, value) in enumerate(entries, 1)
    )


def _director_profile():
    return ArtifactBehaviorProfile(
        runtime_kind="agent_instruction",
        domain_tags=("director_storyboard",),
        inputs=("script",),
        outputs=("shot_list",),
        constraints=("content_preservation",),
        state_requirements=("continuity",),
        facts=_facts((
            ("domain", "director_storyboard"),
            ("input", "script"),
            ("output", "shot_list"),
            ("constraint", "content_preservation"),
            ("state_requirement", "continuity"),
        )),
    )


def _art_profile():
    return ArtifactBehaviorProfile(
        runtime_kind="prompt",
        domain_tags=("art_style",),
        outputs=("positive_prompt", "negative_prompt"),
        constraints=("subject_preservation",),
        facts=_facts((
            ("domain", "art_style"),
            ("output", "positive_prompt"),
            ("output", "negative_prompt"),
            ("constraint", "subject_preservation"),
        )),
    )


def test_director_missing_script_scenario_is_generated_from_cited_input():
    profile = _director_profile()
    scenarios = build_artifact_scenarios(profile, build_dynamic_plan(profile))
    scenario = next(
        item for item in scenarios
        if item.scenario_id == "director.required_input_handling"
    )

    assert "剧本" in scenario.probes[0]
    assert scenario.risk_ids == ["VR-PROMPT-016"]


def test_director_duration_oracle_checks_sum_not_keywords():
    result = director_duration_oracle("镜头1：8秒\n镜头2：9秒", target_seconds=30)

    assert result.outcome == "failed"
    assert result.observed["durationTotalSeconds"] == 17


def test_director_duration_oracle_accepts_exact_budget():
    result = director_duration_oracle(
        "镜头1：8秒\n镜头2：9秒\n镜头3：13秒", target_seconds=30)

    assert result.outcome == "passed"
    assert result.observed["durationTotalSeconds"] == 30


def test_director_duration_oracle_reports_unparseable_output():
    result = director_duration_oracle("这里是一些分镜建议", target_seconds=30)

    assert result.outcome == "insufficient_evidence"
    assert "duration_not_parseable" in result.reason_codes


def test_art_style_subject_preservation_oracle_reports_changed_identity():
    result = subject_preservation_oracle(
        required_terms=("一名女性", "红色雨伞"),
        response="水墨风，两名男性站在桥上",
    )

    assert result.outcome == "failed"
    assert "required_subject_term_missing" in result.reason_codes


def test_art_style_subject_preservation_accepts_style_only_change():
    result = subject_preservation_oracle(
        required_terms=("一名女性", "红色雨伞"),
        response="positive_prompt: 水墨画风，一名女性手持红色雨伞",
    )

    assert result.outcome == "passed"


def test_required_input_oracle_accepts_request_for_missing_script():
    result = required_input_oracle("请先提供剧本内容，我再生成镜头列表。", "script")

    assert result.outcome == "passed"


def test_art_style_contract_supports_chinese_and_english_labels():
    chinese = art_style_prompt_contract_oracle(
        "正向提示词：水墨，柔光\n负向提示词：照片感，3D"
    )
    english = art_style_prompt_contract_oracle(
        "positive_prompt: ink wash, soft light\nnegative_prompt: photorealistic, 3d"
    )

    assert chinese.outcome == "passed"
    assert english.outcome == "passed"


def test_art_style_contract_does_not_pass_malformed_output():
    result = art_style_prompt_contract_oracle("给你一个很漂亮的画面")

    assert result.outcome == "insufficient_evidence"


def test_art_style_term_conflict_detects_same_style_in_both_fields():
    result = term_conflict_oracle(
        "positive_prompt: watercolor, oil painting\n"
        "negative_prompt: photorealistic, oil painting"
    )

    assert result.outcome == "failed"
    assert "positive_negative_term_conflict" in result.reason_codes


def test_factories_emit_all_selected_director_and_art_style_checks():
    director = _director_profile()
    art = _art_profile()

    director_ids = {
        scenario.scenario_id
        for scenario in build_artifact_scenarios(
            director, build_dynamic_plan(director)
        )
    }
    art_ids = {
        scenario.scenario_id
        for scenario in build_artifact_scenarios(art, build_dynamic_plan(art))
    }

    assert director_ids == {
        "director.required_input_handling",
        "director.shot_contract",
        "director.duration_budget",
        "director.content_preservation",
        "director.revision_state",
    }
    assert art_ids == {
        "art_style.required_subject",
        "art_style.prompt_contract",
        "art_style.subject_preservation",
        "art_style.term_conflict",
    }


def test_blackbox_runner_uses_trace_oracle_for_multiturn_revision():
    profile = _director_profile()
    scenario = next(
        item for item in build_artifact_scenarios(
            profile, build_dynamic_plan(profile)
        )
        if item.scenario_id == "director.revision_state"
    )
    responses = iter((
        "镜头1：角色开门\n镜头2：角色拾起钥匙",
        "镜头1：角色走进房间\n镜头2：近景，角色拾起钥匙",
    ))

    class StubOpener:
        def open(self, request, timeout):
            content = next(responses)
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

    with patch("verity.blackbox.runner._build_opener", return_value=StubOpener()):
        result = run_blackbox(
            system_prompt="导演 Skill",
            scenarios=[scenario],
            base_url="https://stub.example/v1",
            model_id="stub-model",
            api_key="stub-key",
            max_calls=2,
        )

    scenario_result = result.scenario_results[0]
    assert scenario_result.oracle_result.outcome == "failed"
    assert scenario_result.verdict == "failed"
    assert scenario_result.oracle_result.observed["unchangedShotPreserved"] is False
import json
from io import BytesIO
from unittest.mock import patch

from verity.blackbox.runner import run_blackbox
