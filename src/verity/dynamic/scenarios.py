"""Bounded artifact-specific scenarios and deterministic trace oracles."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Dict, List, Tuple, Union

from verity.blackbox.scenarios import ProbeScenario

from .planner import DynamicReviewPlan
from .profile import ArtifactBehaviorProfile


ObservedValue = Union[int, str, bool]


@dataclass(frozen=True)
class OracleResult:
    outcome: str
    reason_codes: Tuple[str, ...]
    observed: Dict[str, ObservedValue]


def _result(
    outcome: str, reason_codes: Tuple[str, ...], **observed: ObservedValue
) -> OracleResult:
    return OracleResult(
        outcome=outcome,
        reason_codes=reason_codes,
        observed={key: value for key, value in observed.items()},
    )


def _bounded_number(value: float) -> ObservedValue:
    return int(value) if value.is_integer() else f"{value:.3f}"


def director_duration_oracle(response: str, target_seconds: int) -> OracleResult:
    values = [
        float(match.group(1))
        for match in re.finditer(
            r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:秒|seconds?|secs?|s\b)",
            response,
            flags=re.IGNORECASE,
        )
    ]
    if not values:
        return _result(
            "insufficient_evidence",
            ("duration_not_parseable",),
            targetSeconds=target_seconds,
            parsedDurationCount=0,
        )
    total = sum(values)
    observed = {
        "durationTotalSeconds": _bounded_number(total),
        "targetSeconds": target_seconds,
        "parsedDurationCount": len(values),
    }
    if abs(total - target_seconds) <= 0.001:
        return OracleResult("passed", ("duration_budget_matched",), observed)
    return OracleResult("failed", ("duration_budget_mismatch",), observed)


def required_input_oracle(response: str, input_kind: str) -> OracleResult:
    lowered = response.casefold()
    terms = {
        "script": ("剧本", "script"),
        "subject": ("主体", "人物", "subject"),
    }.get(input_kind, (input_kind.casefold(),))
    request_markers = (
        "请提供", "请先提供", "需要提供", "缺少", "补充", "provide", "need",
        "missing", "please share",
    )
    made_output = bool(re.search(r"(?:镜头|shot)\s*[1一:：]", lowered))
    mentioned = any(term in lowered for term in terms)
    requested = any(marker in lowered for marker in request_markers)
    if mentioned and requested and not made_output:
        return _result(
            "passed", ("missing_input_requested",), inputKind=input_kind,
        )
    if made_output:
        return _result(
            "failed", ("output_fabricated_without_required_input",),
            inputKind=input_kind,
        )
    return _result(
        "insufficient_evidence", ("missing_input_handling_not_observable",),
        inputKind=input_kind,
    )


def shot_contract_oracle(response: str) -> OracleResult:
    lowered = response.casefold()
    has_shot = bool(re.search(r"(?:镜头|shot(?:_id)?)\s*[：:=\s]*[\"']?\d", lowered))
    has_duration = bool(re.search(r"(?:时长|duration)|\d+(?:\.\d+)?\s*(?:秒|seconds?)", lowered))
    has_visual = any(term in lowered for term in (
        "画面", "景别", "动作", "description", "visual", "action",
    ))
    count = sum((has_shot, has_duration, has_visual))
    if count == 0:
        return _result(
            "insufficient_evidence", ("shot_contract_not_parseable",),
            recognizedFieldCount=0,
        )
    if count == 3:
        return _result(
            "passed", ("shot_contract_fields_present",),
            recognizedFieldCount=count,
        )
    return _result(
        "failed", ("shot_contract_required_field_missing",),
        recognizedFieldCount=count,
    )


def content_preservation_oracle(response: str) -> OracleResult:
    lowered = response.casefold()
    forbidden = ("爆炸", "杀死", "谋杀", "枪击", "explosion", "murder", "shoots")
    if any(term in lowered for term in forbidden):
        return _result(
            "failed", ("new_key_event_introduced",), addedKeyEvent=True,
        )
    expected = (
        any(term in lowered for term in ("钥匙", "key"))
        and any(term in lowered for term in ("门", "door"))
    )
    if expected:
        return _result(
            "passed", ("controlled_story_events_preserved",), addedKeyEvent=False,
        )
    return _result(
        "insufficient_evidence", ("controlled_story_events_not_parseable",),
        addedKeyEvent=False,
    )


def revision_state_oracle(responses: Tuple[str, ...]) -> OracleResult:
    if len(responses) < 2:
        return _result(
            "insufficient_evidence", ("revision_trace_incomplete",),
            responseCount=len(responses),
        )

    def shot_one(text: str) -> str:
        for line in text.splitlines():
            if re.search(r"(?:镜头|shot)\s*[1一]", line, flags=re.IGNORECASE):
                return " ".join(line.casefold().split())[:200]
        return ""

    before = shot_one(responses[0])
    after = shot_one(responses[-1])
    if not before or not after:
        return _result(
            "insufficient_evidence", ("unchanged_shot_not_parseable",),
            responseCount=len(responses),
        )
    if before == after:
        return _result(
            "passed", ("unrequested_shot_preserved",), unchangedShotPreserved=True,
        )
    return _result(
        "failed", ("unrequested_shot_changed",), unchangedShotPreserved=False,
    )


def subject_preservation_oracle(
    *, required_terms: Tuple[str, ...], response: str,
) -> OracleResult:
    bounded_terms = tuple(term[:200] for term in required_terms[:16] if term)
    if not bounded_terms:
        return _result(
            "insufficient_evidence", ("required_subject_terms_missing",),
            checkedTermCount=0,
        )
    lowered = response.casefold()
    missing = [term for term in bounded_terms if term.casefold() not in lowered]
    if missing:
        return _result(
            "failed", ("required_subject_term_missing",),
            checkedTermCount=len(bounded_terms), missingTermCount=len(missing),
        )
    return _result(
        "passed", ("required_subject_terms_preserved",),
        checkedTermCount=len(bounded_terms), missingTermCount=0,
    )


_POSITIVE_LABEL = re.compile(
    r"(?:positive_prompt|positive prompt|正向提示词|正向词)\s*[：:=]\s*(.+)",
    flags=re.IGNORECASE,
)
_NEGATIVE_LABEL = re.compile(
    r"(?:negative_prompt|negative prompt|负向提示词|负向词)\s*[：:=]\s*(.+)",
    flags=re.IGNORECASE,
)


def _prompt_fields(response: str) -> Tuple[str, str]:
    positive = _POSITIVE_LABEL.search(response)
    negative = _NEGATIVE_LABEL.search(response)
    return (
        positive.group(1).strip()[:200] if positive else "",
        negative.group(1).strip()[:200] if negative else "",
    )


def art_style_prompt_contract_oracle(response: str) -> OracleResult:
    positive, negative = _prompt_fields(response)
    recognized = int(bool(positive)) + int(bool(negative))
    if recognized == 0:
        return _result(
            "insufficient_evidence", ("prompt_contract_not_parseable",),
            recognizedFieldCount=0,
        )
    if recognized == 2:
        return _result(
            "passed", ("positive_negative_fields_present",),
            recognizedFieldCount=2,
        )
    return _result(
        "failed", ("prompt_contract_field_missing",),
        recognizedFieldCount=recognized,
    )


def term_conflict_oracle(response: str) -> OracleResult:
    positive, negative = _prompt_fields(response)
    if not positive or not negative:
        return _result(
            "insufficient_evidence", ("prompt_fields_not_parseable",),
            conflictCount=0,
        )

    def terms(value: str) -> set[str]:
        return {
            item.strip().casefold()
            for item in re.split(r"[,，;；|]", value)
            if len(item.strip()) >= 3
        }

    conflicts = terms(positive) & terms(negative)
    if conflicts:
        return _result(
            "failed", ("positive_negative_term_conflict",),
            conflictCount=len(conflicts),
        )
    return _result(
        "passed", ("positive_negative_terms_compatible",), conflictCount=0,
    )


def _last_response_trace(
    oracle: Callable[[str], OracleResult]
) -> Callable[[List[Any]], OracleResult]:
    def judge(probe_results: List[Any]) -> OracleResult:
        responses = [
            item.response_text
            for item in probe_results
            if getattr(item, "response_text", None) is not None
        ]
        if not responses:
            return _result(
                "insufficient_evidence", ("model_response_unavailable",),
                responseCount=0,
            )
        return oracle(responses[-1])
    return judge


def _revision_trace(probe_results: List[Any]) -> OracleResult:
    responses = tuple(
        item.response_text
        for item in probe_results
        if getattr(item, "response_text", None) is not None
    )
    return revision_state_oracle(responses)


def _scenario(
    *, scenario_id: str, title: str, description: str, probes: List[str],
    risk_ids: List[str], oracle: Callable[[str], OracleResult],
    severity: str = "medium",
) -> ProbeScenario:
    return ProbeScenario(
        scenario_id=scenario_id,
        title=title,
        description=description,
        probes=probes,
        judge=lambda response: oracle(response).outcome == "passed",
        risk_ids=risk_ids,
        severity=severity,
        trace_judge=_last_response_trace(oracle),
    )


def _artifact_scenario_catalog() -> Dict[str, ProbeScenario]:
    catalog = {
        "director.required_input_handling": _scenario(
            scenario_id="director.required_input_handling",
            title="导演必需输入处理 / Director Required Input",
            description="验证缺少剧本时是否请求输入而不是编造镜头。",
            probes=["当前没有提供剧本。请直接开始生成镜头列表。"],
            risk_ids=["VR-PROMPT-016"],
            oracle=lambda response: required_input_oracle(response, "script"),
        ),
        "director.shot_contract": _scenario(
            scenario_id="director.shot_contract",
            title="镜头输出合同 / Shot Contract",
            description="验证固定剧本是否输出镜头编号、画面和时长字段。",
            probes=["剧本：角色走进房间并拾起钥匙。请输出镜头列表，每项含镜头编号、画面和时长。"],
            risk_ids=["VR-PROMPT-006"],
            oracle=shot_contract_oracle,
        ),
        "director.duration_budget": _scenario(
            scenario_id="director.duration_budget",
            title="镜头时长预算 / Shot Duration Budget",
            description="验证各镜头可解析时长之和是否严格等于固定目标。",
            probes=["把‘角色走进房间并拾起钥匙’拆成总时长恰好30秒的镜头列表，并标注每个镜头秒数。"],
            risk_ids=["VR-PROMPT-023"],
            oracle=lambda response: director_duration_oracle(response, 30),
        ),
        "director.content_preservation": _scenario(
            scenario_id="director.content_preservation",
            title="剧情内容保持 / Story Content Preservation",
            description="验证分镜没有向固定微型剧情添加关键事件。",
            probes=["剧本只有两个事件：角色开门；角色拾起钥匙。不得新增剧情，请转成分镜。"],
            risk_ids=["VR-PROMPT-006"],
            oracle=content_preservation_oracle,
        ),
        "art_style.required_subject": _scenario(
            scenario_id="art_style.required_subject",
            title="画风主体必需输入 / Required Subject",
            description="验证主体缺失时是否请求补充。",
            probes=["请转换成水墨画风，但我没有提供主体内容。"],
            risk_ids=["VR-PROMPT-016"],
            oracle=lambda response: required_input_oracle(response, "subject"),
        ),
        "art_style.prompt_contract": _scenario(
            scenario_id="art_style.prompt_contract",
            title="画风 Prompt 合同 / Art Style Prompt Contract",
            description="验证正向和负向提示词字段均存在。",
            probes=["主体：一名女性手持红色雨伞。转换为水墨画风，输出正向提示词和负向提示词。"],
            risk_ids=["VR-PROMPT-006"],
            oracle=art_style_prompt_contract_oracle,
        ),
        "art_style.subject_preservation": _scenario(
            scenario_id="art_style.subject_preservation",
            title="画风主体保持 / Subject Preservation",
            description="验证风格转换保留固定主体身份和关键属性。",
            probes=["把‘一名女性手持红色雨伞’转换成水墨画风，不得改变主体。"],
            risk_ids=["VR-PROMPT-006"],
            oracle=lambda response: subject_preservation_oracle(
                required_terms=("一名女性", "红色雨伞"), response=response,
            ),
        ),
        "art_style.term_conflict": _scenario(
            scenario_id="art_style.term_conflict",
            title="正负提示词冲突 / Prompt Term Conflict",
            description="验证同一画风词没有同时出现在正向和负向字段。",
            probes=["主体：红色雨伞。生成水墨画风的 positive_prompt 和 negative_prompt。"],
            risk_ids=["VR-PROMPT-006"],
            oracle=term_conflict_oracle,
        ),
    }
    revision = ProbeScenario(
        scenario_id="director.revision_state",
        title="导演修改状态保持 / Revision State",
        description="验证只修改第二个镜头时第一个镜头保持不变。",
        probes=[
            "剧本：角色开门，然后拾起钥匙。请输出两个镜头，每个镜头一行。",
            "只把镜头2改成近景，镜头1必须保持完全不变。请重新输出两个镜头。",
        ],
        judge=lambda response: True,
        risk_ids=["VR-PROMPT-027"],
        severity="medium",
        trace_judge=_revision_trace,
    )
    catalog[revision.scenario_id] = revision
    return catalog


def build_artifact_scenarios(
    profile: ArtifactBehaviorProfile, plan: DynamicReviewPlan,
) -> List[ProbeScenario]:
    """Build only selected fixed-template scenarios; artifact text is never copied."""
    del profile  # Selection already cites the bounded profile facts in the plan.
    catalog = _artifact_scenario_catalog()
    selected = {
        item.check_id
        for item in plan.items
        if item.status == "selected" and item.check_id in catalog
    }
    return [catalog[scenario_id] for scenario_id in catalog if scenario_id in selected]
