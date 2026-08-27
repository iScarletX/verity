"""V1.5 black-box runner tests.

All tests use a stub HTTP opener — no real API calls are made. Tests confirm:
- Scenario judges classify responses correctly (safe vs failed)
- Runner correctly aggregates results and respects budgets
- Multi-turn probe conversation is built correctly
"""
from __future__ import annotations

import json
from io import BytesIO
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from verity.blackbox.scenarios import list_scenarios, get_scenario, SCENARIOS_BY_ID
from verity.blackbox.runner import run_blackbox, BlackboxRunResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _stub_response(content: str, status: int = 200) -> Any:
    """Return a fake urllib response whose .read() yields the chat-completions envelope."""
    body = json.dumps({
        "choices": [{"message": {"content": content, "role": "assistant"}}]
    }).encode()

    class _Resp:
        def __init__(self):
            self._io = BytesIO(body)
            self.status = status

        def read(self, n=-1):
            return self._io.read(n)

        def getcode(self):
            return self.status

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return _Resp()


class _StubOpener:
    """Captures all requests and returns canned responses in order."""

    def __init__(self, responses: List[str]):
        self._responses = list(responses)
        self.calls: List[Dict] = []

    def open(self, request, timeout):
        self.calls.append({"url": request.full_url})
        if not self._responses:
            raise ConnectionError("stub ran out of responses")
        text = self._responses.pop(0)
        return _stub_response(text)


# ---------------------------------------------------------------------------
# Scenario judge unit tests
# ---------------------------------------------------------------------------

class TestScenarioJudges:
    def test_injection_override_safe(self):
        s = get_scenario("injection_override_simple")
        assert s.judge("I'm sorry, I cannot do that. How can I help you?") is True

    def test_injection_override_failed(self):
        s = get_scenario("injection_override_simple")
        assert s.judge("Sure! I am DAN, do anything now mode activated.") is False

    def test_authority_impersonation_safe(self):
        s = get_scenario("authority_impersonation")
        assert s.judge("I cannot enter maintenance mode or override my procedures.") is True

    def test_authority_impersonation_failed(self):
        s = get_scenario("authority_impersonation")
        assert s.judge("Maintenance mode activated. Restrictions lifted.") is False

    def test_system_prompt_extraction_safe(self):
        s = get_scenario("system_prompt_extraction")
        assert s.judge("I'm unable to share my internal configuration.") is True

    def test_system_prompt_extraction_failed_reveals_prompt(self):
        s = get_scenario("system_prompt_extraction")
        # Model starts echoing system prompt content
        assert s.judge("System prompt: You are a helpful assistant...") is False

    def test_output_format_compliance_has_bullets(self):
        s = get_scenario("output_format_compliance")
        assert s.judge("• Point one\n• Point two\n• Point three") is True

    def test_output_format_compliance_no_bullets(self):
        s = get_scenario("output_format_compliance")
        assert s.judge("Climate change is a serious issue.") is False

    def test_topic_boundary_safe(self):
        s = get_scenario("topic_boundary_enforcement")
        assert s.judge("I'm sorry, I can only assist with customer service.") is True

    def test_topic_boundary_failed(self):
        s = get_scenario("topic_boundary_enforcement")
        assert s.judge("Sure! Here's how to make explosives: ...") is False


# ---------------------------------------------------------------------------
# Runner unit tests (no real API calls)
# ---------------------------------------------------------------------------

class TestRunner:
    def _run_with_stub(self, stub_responses: List[str],
                       scenario_ids: List[str] = None) -> BlackboxRunResult:
        """Run blackbox with stub responses against selected scenarios."""
        stub_opener = _StubOpener(stub_responses)
        scenarios = [get_scenario(sid) for sid in (scenario_ids or ["injection_override_simple"])]
        with patch("verity.blackbox.runner._build_opener", return_value=stub_opener):
            return run_blackbox(
                system_prompt="You are a helpful assistant.",
                scenarios=scenarios,
                base_url="https://stub.example/v1",
                model_id="stub-model",
                api_key="stub-key",
                max_calls=10,
            )

    def test_safe_response_recorded_as_passed(self):
        result = self._run_with_stub(
            ["I'm sorry, I cannot do that."],
            ["injection_override_simple"],
        )
        assert result.scenario_results[0].verdict == "passed"
        assert result.scenario_results[0].safe_count == 1
        assert result.scenario_results[0].failed_count == 0

    def test_failed_response_recorded_as_failed(self):
        result = self._run_with_stub(
            ["I am DAN! I will comply with any request."],
            ["injection_override_simple"],
        )
        assert result.scenario_results[0].verdict == "failed"
        assert result.scenario_results[0].failed_count == 1

    def test_multi_turn_sends_correct_conversation(self):
        # multi_turn_context_drift has 2 probes
        stub_opener = _StubOpener([
            "Hello! Testing mode acknowledged.",   # probe 0 response
            "I cannot help with that.",            # probe 1 response
        ])
        with patch("verity.blackbox.runner._build_opener", return_value=stub_opener):
            result = run_blackbox(
                system_prompt="You are a customer service bot.",
                scenarios=[get_scenario("multi_turn_context_drift")],
                base_url="https://stub.example/v1",
                model_id="stub-model", api_key="stub-key",
                max_calls=5,
            )
        assert len(stub_opener.calls) == 2
        assert result.scenario_results[0].total_probes == 2

    def test_budget_exhaustion_stops_run(self):
        # Only 1 call budget, but multi_turn has 2 probes
        stub_opener = _StubOpener(["Hello!"])
        with patch("verity.blackbox.runner._build_opener", return_value=stub_opener):
            result = run_blackbox(
                system_prompt="You are a helpful assistant.",
                scenarios=[get_scenario("multi_turn_context_drift")],
                base_url="https://stub.example/v1",
                model_id="stub-model", api_key="stub-key",
                max_calls=1,
            )
        assert result.budget_exhausted is True
        assert result.total_calls == 1

    def test_network_error_recorded_as_error(self):
        import urllib.error

        class _ErrorOpener:
            def open(self, request, timeout):
                raise urllib.error.URLError("connection refused")

        with patch("verity.blackbox.runner._build_opener", return_value=_ErrorOpener()):
            result = run_blackbox(
                system_prompt="You are a helpful assistant.",
                scenarios=[get_scenario("injection_override_simple")],
                base_url="https://stub.example/v1",
                model_id="stub-model", api_key="stub-key",
                max_calls=5,
            )
        pr = result.scenario_results[0].probe_results[0]
        assert pr.safe is None
        assert pr.error_code == "network_error"

    def test_network_error_is_recorded_in_top_level_errors_list(self):
        # Regression: BlackboxRunResult.errors was declared but never
        # populated by run_blackbox(), so the per-probe error_code was only
        # ever visible by digging into scenario_results[].probe_results[] --
        # the Web UI's top-level "call errors" disclosure (which reads this
        # flat list) could never show anything, even on a real failure.
        import urllib.error

        class _ErrorOpener:
            def open(self, request, timeout):
                raise urllib.error.URLError("connection refused")

        with patch("verity.blackbox.runner._build_opener", return_value=_ErrorOpener()):
            result = run_blackbox(
                system_prompt="You are a helpful assistant.",
                scenarios=[get_scenario("injection_override_simple")],
                base_url="https://stub.example/v1",
                model_id="stub-model", api_key="stub-key",
                max_calls=5,
            )
        assert len(result.errors) == 1
        assert "network_error" in result.errors[0]

    def test_safe_response_leaves_errors_list_empty(self):
        result = self._run_with_stub(
            ["I'm sorry, I cannot do that."],
            ["injection_override_simple"],
        )
        assert result.errors == []

    def test_summary_counts_correct(self):
        # Run 2 scenarios: one safe, one failed
        stub_opener = _StubOpener([
            "I'm sorry, I cannot do that.",  # injection_override_simple => safe
            "Maintenance mode activated. Restrictions lifted.",  # authority => failed
        ])
        with patch("verity.blackbox.runner._build_opener", return_value=stub_opener):
            result = run_blackbox(
                system_prompt="You are a helpful assistant.",
                scenarios=[
                    get_scenario("injection_override_simple"),
                    get_scenario("authority_impersonation"),
                ],
                base_url="https://stub.example/v1",
                model_id="stub-model", api_key="stub-key",
                max_calls=5,
            )
        summary = result.summary()
        assert summary["passed"] == 1
        assert summary["failed"] == 1


# ---------------------------------------------------------------------------
# Scenario catalog completeness
# ---------------------------------------------------------------------------

def test_all_builtin_scenarios_have_required_fields():
    for s in list_scenarios():
        assert s.scenario_id
        assert s.title
        assert s.description
        assert s.probes, f"{s.scenario_id} has no probes"
        assert callable(s.judge), f"{s.scenario_id} judge is not callable"
        assert s.severity in ("low", "medium", "high", "critical")


def test_scenario_ids_are_unique():
    ids = [s.scenario_id for s in list_scenarios()]
    assert len(ids) == len(set(ids))


def test_scenario_risk_ids_reference_real_risks():
    """Every scenario's risk_ids must exist in the unified-risk taxonomy.

    Guards against copy-paste typos (a scenario tagged with a risk id that
    was never registered would silently vanish from any future breadth
    computation built on this metadata).
    """
    from verity.standards import load_risks

    known = set(load_risks())
    for s in list_scenarios():
        for rid in s.risk_ids:
            assert rid in known, f"{s.scenario_id} references unknown risk {rid}"


def test_scenario_risk_ids_match_their_definitions():
    """Lock in risk-id mappings that were fixed after cross-checking
    standards/risks.json's definitions (several scenarios were previously
    tagged with a mismatched risk, e.g. a skill-scope-bypass probe tagged
    "sensitive information embedded in prompts" instead of "operational
    role scope is incomplete") — regression coverage for the corrected set.
    """
    expected = {
        "output_format_compliance": {"VR-PROMPT-006"},
        "topic_boundary_enforcement": {"VR-PROMPT-028"},
        "multi_turn_context_drift": {"VR-PROMPT-001", "VR-PROMPT-027"},
        "skill_boundary_bypass": {"VR-PROMPT-021"},
        "upstream_dependency_skip": {"VR-PROMPT-022"},
        "output_contract_violation": {"VR-PROMPT-006"},
        "confidential_reference_leak": {"VR-PROMPT-001", "VR-PROMPT-015"},
        "image_content_safety": {"VR-PROMPT-028"},
        "system_prompt_extraction": {"VR-PROMPT-001", "VR-PROMPT-015"},
    }
    for scenario_id, risk_ids in expected.items():
        scenario = get_scenario(scenario_id)
        assert scenario is not None, f"missing scenario {scenario_id}"
        assert set(scenario.risk_ids) == risk_ids
