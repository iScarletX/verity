"""Controlled public projections for experimental dynamic-stage results.

Dynamic runners need raw prompt/response/trace material while evaluating an
artifact.  Reports do not.  This module is the one-way boundary between those
internal runner objects and JSON/HTML/Web/SARIF consumers.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, Optional


_STATUSES = frozenset({"not_enabled", "completed", "failed"})
_ORACLE_OUTCOMES = frozenset({
    "passed", "failed", "insufficient_evidence", "unavailable",
})
_SEVERITIES = frozenset({"low", "medium", "high", "critical"})
_SCENARIO_POLICIES = frozenset({"artifact_aware", "all", "explicit"})
_BLACKBOX_REASON_CODES = frozenset({
    "api_key_env_not_set",
    "blackbox_inconclusive",
    "blackbox_no_scenarios_selected",
    "blackbox_probe_error",
    "blackbox_result_incomplete",
    "budget_exhausted",
    "disabled_by_config",
    "prompt_text_unavailable",
    "provider_not_configured",
    "unknown_scenario",
})
_PROBE_ERROR_CODES = frozenset({
    "judge_error",
    "network_error",
    "no_choices",
    "parse_error",
    "response_too_large",
})
_ORACLE_REASON_CODES = frozenset({
    "controlled_story_events_not_parseable",
    "controlled_story_events_preserved",
    "duration_budget_matched",
    "duration_budget_mismatch",
    "duration_not_parseable",
    "missing_input_handling_not_observable",
    "missing_input_requested",
    "model_response_unavailable",
    "new_key_event_introduced",
    "output_fabricated_without_required_input",
    "positive_negative_fields_present",
    "positive_negative_term_conflict",
    "positive_negative_terms_compatible",
    "prompt_contract_field_missing",
    "prompt_contract_not_parseable",
    "prompt_fields_not_parseable",
    "required_subject_term_missing",
    "required_subject_terms_missing",
    "required_subject_terms_preserved",
    "revision_trace_incomplete",
    "shot_contract_fields_present",
    "shot_contract_not_parseable",
    "shot_contract_required_field_missing",
    "unchanged_shot_not_parseable",
    "unrequested_shot_changed",
    "unrequested_shot_preserved",
})
_CONTROLLED_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_MODEL_ID_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}\Z")
_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_HTTP_ERROR_RE = re.compile(r"\Ahttp_[1-5][0-9]{2}\Z")


def _nonnegative_int(value: object, *, maximum: int = 1_000_000) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        return 0
    return value


def _nonnegative_number(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _controlled_id(value: object) -> Optional[str]:
    if isinstance(value, str) and _CONTROLLED_ID_RE.fullmatch(value):
        return value
    return None


def _digest(value: object) -> Optional[str]:
    if isinstance(value, str) and _SHA256_RE.fullmatch(value):
        return value
    return None


def _blackbox_reason(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    if value.startswith("unknown_scenario:"):
        return "unknown_scenario"
    if value in _BLACKBOX_REASON_CODES:
        return value
    return None


def _probe_error(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    if value in _PROBE_ERROR_CODES or _HTTP_ERROR_RE.fullmatch(value):
        return value
    return None


def _oracle_projection(raw: object) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    outcome = raw.get("outcome")
    if not isinstance(outcome, str) or outcome not in _ORACLE_OUTCOMES:
        return None
    raw_reason_codes = raw.get("reason_codes")
    if not isinstance(raw_reason_codes, (list, tuple)):
        raw_reason_codes = ()
    reason_codes = [
        code for code in raw_reason_codes
        if isinstance(code, str) and code in _ORACLE_REASON_CODES
    ]
    return {
        "outcome": outcome,
        "reason_codes": reason_codes,
    }


def _scenario_outcome(raw: Dict[str, Any], probes: list[Dict[str, Any]]) -> str:
    oracle = _oracle_projection(raw.get("oracle_result"))
    if oracle is not None:
        return str(oracle["outcome"])
    if not probes or all(item.get("safe") is None for item in probes):
        return "insufficient_evidence"
    if any(item.get("safe") is False for item in probes):
        return "failed"
    if any(item.get("safe") is None for item in probes):
        return "insufficient_evidence"
    return "passed"


def _definitive_scenario_is_valid(
    raw: Dict[str, Any], projected: Dict[str, Any],
) -> bool:
    """Authenticate one complete scenario before it can become a finding.

    A failed stage may still contain valuable unsafe evidence from a scenario
    that completed before a peer Provider call failed.  Only this boolean may
    carry that evidence across the public projection: it is recomputed from
    the internal runner shape and never accepted from an embedding payload.
    """
    raw_id = raw.get("scenario_id")
    raw_severity = raw.get("severity")
    raw_probes = raw.get("probe_results")
    probes = projected.get("probe_results")
    outcome = projected.get("outcome")
    if (
        _controlled_id(raw_id) is None
        or projected.get("scenario_id") != raw_id
        or not isinstance(raw_severity, str)
        or raw_severity not in _SEVERITIES
        or projected.get("severity") != raw_severity
        or not isinstance(raw_probes, list)
        or not isinstance(probes, list)
        or not raw_probes
        or len(raw_probes) != len(probes)
        or not isinstance(outcome, str)
        or outcome not in {"passed", "failed"}
    ):
        return False

    for index, (raw_probe, probe) in enumerate(zip(raw_probes, probes)):
        if not isinstance(raw_probe, dict) or not isinstance(probe, dict):
            return False
        raw_safe = raw_probe.get("safe")
        raw_duration = _nonnegative_number(raw_probe.get("duration_seconds"))
        if (
            type(raw_probe.get("probe_index")) is not int
            or raw_probe.get("probe_index") != index
            or probe.get("probe_index") != index
            or type(raw_safe) is not bool
            or probe.get("safe") is not raw_safe
            or raw_probe.get("error_code") is not None
            or "error_code" in probe
            or not isinstance(raw_probe.get("probe_text"), str)
            or not isinstance(raw_probe.get("response_text"), str)
            or probe.get("probe_length") != len(raw_probe["probe_text"])
            or probe.get("response_length") != len(raw_probe["response_text"])
            or _controlled_id(raw_probe.get("call_id")) is None
            or probe.get("call_id") != raw_probe.get("call_id")
            or _digest(raw_probe.get("response_digest")) is None
            or probe.get("response_digest") != raw_probe.get("response_digest")
            or raw_duration is None
            or probe.get("duration_seconds") != raw_duration
        ):
            return False

    raw_oracle = raw.get("oracle_result")
    projected_oracle = projected.get("oracle_result")
    if raw_oracle is None:
        return projected_oracle is None
    if not isinstance(raw_oracle, dict) or not isinstance(projected_oracle, dict):
        return False
    raw_oracle_outcome = raw_oracle.get("outcome")
    raw_reason_codes = raw_oracle.get("reason_codes")
    return (
        isinstance(raw_oracle_outcome, str)
        and raw_oracle_outcome in {"passed", "failed"}
        and isinstance(raw_reason_codes, (list, tuple))
        and all(
            isinstance(code, str) and code in _ORACLE_REASON_CODES
            for code in raw_reason_codes
        )
        and projected_oracle == {
            "outcome": raw_oracle_outcome,
            "reason_codes": list(raw_reason_codes),
        }
    )


def _completed_blackbox_is_valid(
    raw: Dict[str, Any], projected: Dict[str, Any],
) -> bool:
    planned = raw.get("plannedScenarioCount")
    raw_scenarios = raw.get("scenarioResults")
    scenarios = projected.get("scenarioResults")
    total_calls = raw.get("totalCalls")
    if (
        type(planned) is not int
        or not 1 <= planned <= 500
        or not isinstance(raw_scenarios, list)
        or not isinstance(scenarios, list)
        or len(raw_scenarios) != planned
        or len(scenarios) != planned
        or type(total_calls) is not int
        or not 1 <= total_calls <= 500
        or not isinstance(raw.get("scenarioPolicy"), str)
        or raw.get("scenarioPolicy") not in _SCENARIO_POLICIES
        or projected.get("scenarioPolicy") != raw.get("scenarioPolicy")
        or _digest(raw.get("systemPromptDigest")) is None
        or not isinstance(raw.get("model"), str)
        or _MODEL_ID_RE.fullmatch(raw["model"]) is None
        or raw.get("reasonCode") is not None
        or raw.get("errors") != []
    ):
        return False

    expected_calls = 0
    scenario_ids = set()
    for raw_scenario, scenario in zip(raw_scenarios, scenarios):
        if not isinstance(raw_scenario, dict) or not isinstance(scenario, dict):
            return False
        raw_probes = raw_scenario.get("probe_results")
        probes = scenario.get("probe_results")
        scenario_id = scenario.get("scenario_id")
        if (
            not isinstance(scenario_id, str)
            or scenario_id in scenario_ids
            or scenario.get("definitive") is not True
            or not _definitive_scenario_is_valid(raw_scenario, scenario)
        ):
            return False
        scenario_ids.add(scenario_id)
        if not isinstance(probes, list):
            return False
        expected_calls += len(probes)

    summary = projected.get("summary")
    passed = sum(item.get("outcome") == "passed" for item in scenarios)
    failed = sum(item.get("outcome") == "failed" for item in scenarios)
    if (
        expected_calls != total_calls
        or not isinstance(summary, dict)
        or summary != {
            "totalScenarios": planned,
            "completed": planned,
            "passed": passed,
            "failed": failed,
            "errors": 0,
            "partial": 0,
            "totalCalls": total_calls,
            "budgetExhausted": False,
        }
    ):
        return False
    return True


def project_prompt_blackbox(raw: object) -> Dict[str, Any]:
    """Remove prompt, probe and Provider response text from a stage result."""
    if not isinstance(raw, dict):
        return {"status": "failed", "reasonCode": "invalid_blackbox_result"}

    raw_status = raw.get("status")
    status = (
        raw_status
        if isinstance(raw_status, str) and raw_status in _STATUSES
        else "failed"
    )
    projected: Dict[str, Any] = {"status": status}
    reason_code = _blackbox_reason(raw.get("reasonCode"))
    if reason_code is not None:
        projected["reasonCode"] = reason_code
    policy = raw.get("scenarioPolicy")
    if isinstance(policy, str) and policy in _SCENARIO_POLICIES:
        projected["scenarioPolicy"] = policy
    if "plannedScenarioCount" in raw:
        projected["plannedScenarioCount"] = _nonnegative_int(
            raw.get("plannedScenarioCount"), maximum=500
        )
    prompt_digest = _digest(raw.get("systemPromptDigest"))
    if prompt_digest is not None:
        projected["systemPromptDigest"] = prompt_digest
    model_id = raw.get("model")
    if isinstance(model_id, str) and _MODEL_ID_RE.fullmatch(model_id):
        projected["model"] = model_id

    scenarios = []
    raw_scenarios = raw.get("scenarioResults")
    if not isinstance(raw_scenarios, list):
        raw_scenarios = []
    for raw_scenario in raw_scenarios:
        if not isinstance(raw_scenario, dict):
            continue
        scenario_id = _controlled_id(raw_scenario.get("scenario_id"))
        if scenario_id is None:
            continue
        probe_results = []
        raw_probes = raw_scenario.get("probe_results")
        if not isinstance(raw_probes, list):
            raw_probes = []
        for raw_probe in raw_probes:
            if not isinstance(raw_probe, dict):
                continue
            safe = raw_probe.get("safe")
            if safe is not True and safe is not False and safe is not None:
                safe = None
            response_text = raw_probe.get("response_text")
            probe_text = raw_probe.get("probe_text")
            probe: Dict[str, Any] = {
                "probe_index": _nonnegative_int(
                    raw_probe.get("probe_index"), maximum=10_000
                ),
                "safe": safe,
                "probe_length": (
                    len(probe_text) if isinstance(probe_text, str) else 0
                ),
                "response_length": (
                    len(response_text) if isinstance(response_text, str) else 0
                ),
            }
            call_id = _controlled_id(raw_probe.get("call_id"))
            if call_id is not None:
                probe["call_id"] = call_id
            response_digest = _digest(raw_probe.get("response_digest"))
            if response_digest is not None:
                probe["response_digest"] = response_digest
            duration = _nonnegative_number(raw_probe.get("duration_seconds"))
            if duration is not None:
                probe["duration_seconds"] = duration
            error_code = _probe_error(raw_probe.get("error_code"))
            if error_code is not None:
                probe["error_code"] = error_code
            probe_results.append(probe)

        outcome = _scenario_outcome(raw_scenario, probe_results)
        raw_severity = raw_scenario.get("severity")
        scenario: Dict[str, Any] = {
            "scenario_id": scenario_id,
            "severity": (
                raw_severity
                if isinstance(raw_severity, str) and raw_severity in _SEVERITIES
                else "medium"
            ),
            "outcome": outcome,
            "probe_results": probe_results,
            "totalProbes": len(probe_results),
            "safeCount": sum(item.get("safe") is True for item in probe_results),
            "failedCount": sum(item.get("safe") is False for item in probe_results),
            "errorCount": sum(item.get("safe") is None for item in probe_results),
        }
        oracle = _oracle_projection(raw_scenario.get("oracle_result"))
        if oracle is not None:
            scenario["oracle_result"] = oracle
        scenario["definitive"] = _definitive_scenario_is_valid(
            raw_scenario, scenario
        )
        scenarios.append(scenario)

    if scenarios or "scenarioResults" in raw:
        projected["scenarioResults"] = scenarios
    total_calls = _nonnegative_int(raw.get("totalCalls"), maximum=500)
    if "totalCalls" in raw:
        projected["totalCalls"] = total_calls

    raw_summary = raw.get("summary")
    if isinstance(raw_summary, dict):
        projected["summary"] = {
            "totalScenarios": _nonnegative_int(
                raw_summary.get("totalScenarios"), maximum=500
            ),
            "completed": _nonnegative_int(
                raw_summary.get("completed"), maximum=500
            ),
            "passed": _nonnegative_int(raw_summary.get("passed"), maximum=500),
            "failed": _nonnegative_int(raw_summary.get("failed"), maximum=500),
            "errors": _nonnegative_int(raw_summary.get("errors"), maximum=500),
            "partial": _nonnegative_int(raw_summary.get("partial"), maximum=500),
            "totalCalls": _nonnegative_int(
                raw_summary.get("totalCalls"), maximum=500
            ),
            "budgetExhausted": raw_summary.get("budgetExhausted") is True,
        }
    if (
        projected.get("status") == "completed"
        and not _completed_blackbox_is_valid(raw, projected)
    ):
        projected["status"] = "failed"
        projected["reasonCode"] = "blackbox_result_incomplete"
    elif projected.get("status") == "failed" and "reasonCode" not in projected:
        projected["reasonCode"] = "blackbox_result_incomplete"
    return projected


def project_skill_sandbox(raw: object) -> Dict[str, Any]:
    """Expose only OFF or the release's controlled unavailable state.

    A crafted embedding object must not be able to manufacture a public
    ``completed`` V2 capability while the product adapter is intentionally
    unreachable.  Future hardened V2 work will need a new detector-hit schema;
    it must not revive these raw observation dictionaries.
    """
    if isinstance(raw, dict) and raw.get("status") == "not_enabled":
        return {"status": "not_enabled", "reasonCode": "disabled_by_config"}
    return {
        "status": "failed",
        "observationStatus": "unavailable",
        "reasonCode": "sandbox_isolation_hardening_required",
    }
