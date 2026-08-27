"""Round 74: integration tests for the V1.5 Prompt black-box and V2 Skill
sandbox stages now that both are wired into ``review.run_review`` behind
an explicit two-gate opt-in (``ReviewInputs.blackbox_config`` /
``.sandbox_config``, both default ``None``, and each config dataclass
defaults ``enabled=False``).

No real HTTP calls and no real ``sandbox-exec`` subprocess are made here
(that coverage already lives in ``test_blackbox.py`` / ``test_sandbox.py``
via injectable stubs and macOS-gated integration tests). These tests only
exercise the NEW wiring: that a bare/absent config leaves the default
review path byte-for-byte unaffected, that a caller-enabled config is
correctly invoked and its result correctly aggregated into
``Review.promptBlackbox`` / ``.skillSandbox`` and from there into
``report.py``'s capability matrix and ``scoring.py``'s confidence
limitations.
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import patch

import pytest

from verity.blackbox.config import BlackboxConfig, BlackboxCredentials
from verity.intake import intake_directory, intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.sandbox.config import SandboxConfig
from verity.sandbox.models import SandboxObservation
from verity.scoring import compute_confidence


# --------------------------------------------------------------------- #
# Fixtures / helpers                                                    #
# --------------------------------------------------------------------- #

def _prompt_snapshot(text: str = "You are a helpful assistant."):
    return intake_text(text)


def _skill_snapshot(tmp_path: Path, entry_relpath: str = "scripts/main.py",
                     body: str = "print('hi')\n"):
    (tmp_path / "SKILL.md").write_text(
        "---\nname: t\ndescription: t\nversion: 1.0.0\n---\n")
    entry = tmp_path / entry_relpath
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(body)
    return intake_directory(str(tmp_path))


def _stub_bb_response(content: str) -> Any:
    body = json.dumps({
        "choices": [{"message": {"content": content, "role": "assistant"}}]
    }).encode()

    class _Resp:
        def __init__(self):
            self._io = BytesIO(body)

        def read(self, n=-1):
            return self._io.read(n)

        def getcode(self):
            return 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return _Resp()


class _StubOpener:
    def __init__(self, responses: List[str]):
        self._responses = list(responses)

    def open(self, request, timeout):
        text = self._responses.pop(0)
        return _stub_bb_response(text)


class _FakeSandboxRunner:
    """Drop-in replacement for ``verity.sandbox.runner.SandboxRunner`` that
    never spawns a real process; returns a canned observation."""

    def __init__(self, observation: SandboxObservation, *args, **kwargs):
        self._observation = observation
        self.calls: List[Dict[str, Any]] = []

    def run(self, request, *, snapshot, file_bytes):
        self.calls.append({"entry_point": request.entry_point,
                           "snapshot": snapshot, "file_bytes": file_bytes})
        return self._observation


def _make_fake_runner_factory(observation: SandboxObservation):
    holder: Dict[str, _FakeSandboxRunner] = {}

    def _factory(*args, **kwargs):
        inst = _FakeSandboxRunner(observation, *args, **kwargs)
        holder["instance"] = inst
        return inst

    return _factory, holder


# --------------------------------------------------------------------- #
# A. Default path (no config) is byte-for-byte unaffected               #
# --------------------------------------------------------------------- #

class TestDefaultPathUnaffected:
    def test_prompt_review_without_any_config_leaves_new_fields_none(self):
        snap, byts = _prompt_snapshot()
        review = run_review(ReviewInputs(engine="prompt", snapshot=snap,
                                         file_bytes=byts))
        assert review.promptBlackbox is None
        assert review.skillSandbox is None
        d = review_to_dict(review)
        assert "promptBlackbox" not in d
        assert "skillSandbox" not in d
        assert d["capabilities"]["promptBlackbox"]["status"] == "not_enabled"
        assert d["capabilities"]["skillSandbox"]["status"] == "not_enabled"

    def test_skill_review_without_any_config_leaves_new_fields_none(self, tmp_path):
        snap, byts = _skill_snapshot(tmp_path)
        review = run_review(ReviewInputs(engine="skill", snapshot=snap,
                                         file_bytes=byts))
        assert review.promptBlackbox is None
        assert review.skillSandbox is None
        d = review_to_dict(review)
        assert d["capabilities"]["promptBlackbox"]["status"] == "not_enabled"
        assert d["capabilities"]["skillSandbox"]["status"] == "not_enabled"

    def test_default_constructed_configs_are_a_safe_noop(self, tmp_path):
        """Passing a config OBJECT that is not None but has enabled=False
        (the dataclass default) must behave identically to not passing one
        at all -- this is the first of the two opt-in gates."""
        snap, byts = _prompt_snapshot()
        review = run_review(ReviewInputs(
            engine="prompt", snapshot=snap, file_bytes=byts,
            blackbox_config=BlackboxConfig()))
        assert review.promptBlackbox == {
            "status": "not_enabled", "reasonCode": "disabled_by_config"}

        snap2, byts2 = _skill_snapshot(tmp_path)
        review2 = run_review(ReviewInputs(
            engine="skill", snapshot=snap2, file_bytes=byts2,
            sandbox_config=SandboxConfig()))
        assert review2.skillSandbox == {
            "status": "not_enabled", "reasonCode": "disabled_by_config"}


# --------------------------------------------------------------------- #
# B. Type / engine guardrails                                           #
# --------------------------------------------------------------------- #

class TestGuardrails:
    def test_blackbox_config_wrong_engine_raises(self, tmp_path):
        snap, byts = _skill_snapshot(tmp_path)
        with pytest.raises(ValueError, match="engine='prompt'"):
            run_review(ReviewInputs(
                engine="skill", snapshot=snap, file_bytes=byts,
                blackbox_config=BlackboxConfig(enabled=True)))

    def test_blackbox_config_wrong_type_raises(self):
        snap, byts = _prompt_snapshot()
        with pytest.raises(TypeError, match="BlackboxConfig"):
            run_review(ReviewInputs(
                engine="prompt", snapshot=snap, file_bytes=byts,
                blackbox_config=object()))

    def test_sandbox_config_wrong_engine_raises(self):
        snap, byts = _prompt_snapshot()
        with pytest.raises(ValueError, match="engine='skill'"):
            run_review(ReviewInputs(
                engine="prompt", snapshot=snap, file_bytes=byts,
                sandbox_config=SandboxConfig(enabled=True, entry_point="x.py")))

    def test_sandbox_config_wrong_type_raises(self, tmp_path):
        snap, byts = _skill_snapshot(tmp_path)
        with pytest.raises(TypeError, match="SandboxConfig"):
            run_review(ReviewInputs(
                engine="skill", snapshot=snap, file_bytes=byts,
                sandbox_config=object()))


# --------------------------------------------------------------------- #
# C. Enabled but unconfigured/misconfigured -> honest "failed"          #
# --------------------------------------------------------------------- #

class TestEnabledButUnconfigured:
    def test_blackbox_enabled_without_provider_reports_failed(self):
        snap, byts = _prompt_snapshot()
        review = run_review(ReviewInputs(
            engine="prompt", snapshot=snap, file_bytes=byts,
            blackbox_config=BlackboxConfig(enabled=True)))
        assert review.promptBlackbox["status"] == "failed"
        assert review.promptBlackbox["reasonCode"] == "provider_not_configured"

    def test_blackbox_enabled_without_api_key_env_set_reports_failed(self, monkeypatch):
        monkeypatch.delenv("VERITY_TEST_UNSET_BB_KEY", raising=False)
        snap, byts = _prompt_snapshot()
        cfg = BlackboxConfig(
            enabled=True, base_url="https://stub.example/v1", model_id="stub-model",
            credentials=BlackboxCredentials(api_key_env="VERITY_TEST_UNSET_BB_KEY"))
        review = run_review(ReviewInputs(
            engine="prompt", snapshot=snap, file_bytes=byts, blackbox_config=cfg))
        assert review.promptBlackbox["status"] == "failed"
        assert review.promptBlackbox["reasonCode"] == "api_key_env_not_set"

    def test_blackbox_unknown_scenario_id_reports_failed(self, monkeypatch):
        monkeypatch.setenv("VERITY_TEST_BB_KEY", "stub-key-value")
        snap, byts = _prompt_snapshot()
        cfg = BlackboxConfig(
            enabled=True, base_url="https://stub.example/v1", model_id="stub-model",
            credentials=BlackboxCredentials(api_key_env="VERITY_TEST_BB_KEY"),
            scenario_ids=("does_not_exist",))
        review = run_review(ReviewInputs(
            engine="prompt", snapshot=snap, file_bytes=byts, blackbox_config=cfg))
        assert review.promptBlackbox["status"] == "failed"
        assert review.promptBlackbox["reasonCode"] == "unknown_scenario:does_not_exist"

    def test_sandbox_enabled_without_entry_point_reports_failed(self, tmp_path):
        snap, byts = _skill_snapshot(tmp_path)
        review = run_review(ReviewInputs(
            engine="skill", snapshot=snap, file_bytes=byts,
            sandbox_config=SandboxConfig(enabled=True)))
        assert review.skillSandbox["status"] == "failed"
        assert review.skillSandbox["observationStatus"] == "unavailable"
        assert review.skillSandbox["reasonCode"] == \
            "sandbox_isolation_hardening_required"


# --------------------------------------------------------------------- #
# D. Enabled + stubbed success -> correctly invoked and aggregated      #
# --------------------------------------------------------------------- #

class TestEnabledAndAggregated:
    def test_blackbox_enabled_success_is_aggregated_into_report(self, monkeypatch):
        monkeypatch.setenv("VERITY_TEST_BB_KEY", "stub-key-value")
        snap, byts = _prompt_snapshot()
        cfg = BlackboxConfig(
            enabled=True, base_url="https://stub.example/v1", model_id="stub-model",
            credentials=BlackboxCredentials(api_key_env="VERITY_TEST_BB_KEY"),
            scenario_ids=("injection_override_simple",))
        stub_opener = _StubOpener(["I'm sorry, I cannot do that."])
        with patch("verity.blackbox.runner._build_opener", return_value=stub_opener):
            review = run_review(ReviewInputs(
                engine="prompt", snapshot=snap, file_bytes=byts, blackbox_config=cfg))
        assert review.promptBlackbox["status"] == "completed"
        assert review.promptBlackbox["model"] == "stub-model"
        assert review.promptBlackbox["scenarioResults"]

        d = review_to_dict(review)
        assert d["promptBlackbox"]["status"] == "completed"
        assert d["capabilities"]["promptBlackbox"]["status"] == "completed"

        conf = compute_confidence(d)
        assert conf["execution"]["promptBlackbox"] == "completed"
        # Round 88: a completed black-box run's failures now feed the
        # score directly, so it carries no confidence limitation code at
        # all (unlike the pre-Round-88 blanket "results_not_scored").
        assert "v1_5_blackbox_not_enabled_by_default" not in conf["limitations"]
        assert "v1_5_blackbox_requested_but_failed" not in conf["limitations"]

    def test_probe_and_response_text_are_replaced_by_lengths(self, monkeypatch):
        # The runner needs raw text for deterministic judging and multi-turn
        # state.  The report boundary must retain only controlled outcomes,
        # digests, timings, and lengths.
        monkeypatch.setenv("VERITY_TEST_BB_KEY", "stub-key-value")
        snap, byts = _prompt_snapshot()
        cfg = BlackboxConfig(
            enabled=True, base_url="https://stub.example/v1", model_id="stub-model",
            credentials=BlackboxCredentials(api_key_env="VERITY_TEST_BB_KEY"),
            scenario_ids=("injection_override_simple",))
        stub_opener = _StubOpener(["I'm sorry, I cannot do that."])
        with patch("verity.blackbox.runner._build_opener", return_value=stub_opener):
            review = run_review(ReviewInputs(
                engine="prompt", snapshot=snap, file_bytes=byts, blackbox_config=cfg))
        d = review_to_dict(review)
        probes = d["promptBlackbox"]["scenarioResults"][0]["probe_results"]
        assert probes
        assert "probe_text" not in probes[0]
        assert "response_text" not in probes[0]
        assert probes[0]["probe_length"] > 0
        assert probes[0]["response_length"] == len(
            "I'm sorry, I cannot do that."
        )
        assert probes[0]["safe"] is True
        assert probes[0]["probe_index"] == 0

    def test_probe_duration_and_response_digest_survive_to_report_dict(
            self, monkeypatch):
        # Round 132: the same asdict pass-through already proven above for
        # probe_text/response_text also carries ProbeResult.duration_seconds
        # and .response_digest -- both were already computed by
        # runner.py's _call_model (duration measured around the call,
        # digest = sha256 of the raw response bytes) but, until this
        # round's matching app.js change, never rendered anywhere. This
        # confirms the data side of that gap, independent of the frontend.
        monkeypatch.setenv("VERITY_TEST_BB_KEY", "stub-key-value")
        snap, byts = _prompt_snapshot()
        cfg = BlackboxConfig(
            enabled=True, base_url="https://stub.example/v1", model_id="stub-model",
            credentials=BlackboxCredentials(api_key_env="VERITY_TEST_BB_KEY"),
            scenario_ids=("injection_override_simple",))
        stub_opener = _StubOpener(["I'm sorry, I cannot do that."])
        with patch("verity.blackbox.runner._build_opener", return_value=stub_opener):
            review = run_review(ReviewInputs(
                engine="prompt", snapshot=snap, file_bytes=byts, blackbox_config=cfg))
        d = review_to_dict(review)
        probe = d["promptBlackbox"]["scenarioResults"][0]["probe_results"][0]
        assert isinstance(probe["duration_seconds"], float)
        assert probe["duration_seconds"] >= 0
        assert probe["response_digest"]
        assert len(probe["response_digest"]) == 64  # sha256 hex digest
        assert probe["call_id"]

    def test_product_sandbox_cannot_be_reenabled_by_a_success_stub(self, tmp_path):
        snap, byts = _skill_snapshot(tmp_path)
        obs = SandboxObservation(
            status="completed", isolationMechanism="sandbox-exec",
            entryPoint="scripts/main.py", exitCode=0, durationSeconds=0.01)
        factory, holder = _make_fake_runner_factory(obs)
        cfg = SandboxConfig(enabled=True, entry_point="scripts/main.py")
        with patch("verity.sandbox.runner.SandboxRunner", factory):
            review = run_review(ReviewInputs(
                engine="skill", snapshot=snap, file_bytes=byts, sandbox_config=cfg))
        assert holder == {}
        assert review.skillSandbox == {
            "status": "failed",
            "observationStatus": "unavailable",
            "reasonCode": "sandbox_isolation_hardening_required",
        }

        d = review_to_dict(review)
        assert d["skillSandbox"]["status"] == "failed"
        assert d["capabilities"]["skillSandbox"]["status"] == "failed"

        conf = compute_confidence(d)
        assert conf["execution"]["skillSandbox"] == "failed"
        assert "v2_sandbox_requested_but_failed" in conf["limitations"]

    def test_product_sandbox_cannot_emit_stubbed_file_event_payloads(self, tmp_path):
        snap, byts = _skill_snapshot(tmp_path)
        obs = SandboxObservation(
            status="completed", isolationMechanism="sandbox-exec",
            entryPoint="scripts/main.py", exitCode=0, durationSeconds=0.01,
            fileEvents=[{"op": "write", "path": "/etc/passwd",
                         "insideSandbox": False}])
        factory, holder = _make_fake_runner_factory(obs)
        cfg = SandboxConfig(enabled=True, entry_point="scripts/main.py")
        with patch("verity.sandbox.runner.SandboxRunner", factory):
            review = run_review(ReviewInputs(
                engine="skill", snapshot=snap, file_bytes=byts, sandbox_config=cfg))
        d = review_to_dict(review)
        assert holder == {}
        assert "fileEvents" not in d["skillSandbox"]
        assert "eventCounts" not in d["skillSandbox"]

    def test_product_sandbox_cannot_emit_stubbed_stream_counts(self, tmp_path):
        snap, byts = _skill_snapshot(tmp_path)
        obs = SandboxObservation(
            status="completed", isolationMechanism="sandbox-exec",
            entryPoint="scripts/main.py", exitCode=0, durationSeconds=0.01,
            stdoutBytes=123, stderrBytes=45)
        factory, holder = _make_fake_runner_factory(obs)
        cfg = SandboxConfig(enabled=True, entry_point="scripts/main.py")
        with patch("verity.sandbox.runner.SandboxRunner", factory):
            review = run_review(ReviewInputs(
                engine="skill", snapshot=snap, file_bytes=byts, sandbox_config=cfg))
        d = review_to_dict(review)
        assert holder == {}
        assert "stdoutBytes" not in d["skillSandbox"]
        assert "stderrBytes" not in d["skillSandbox"]

    def test_product_sandbox_cannot_be_reenabled_by_a_timeout_stub(self, tmp_path):
        snap, byts = _skill_snapshot(tmp_path)
        obs = SandboxObservation(
            status="timeout", reasonCode="wall_clock_exceeded",
            isolationMechanism="sandbox-exec", entryPoint="scripts/main.py")
        factory, holder = _make_fake_runner_factory(obs)
        cfg = SandboxConfig(enabled=True, entry_point="scripts/main.py")
        with patch("verity.sandbox.runner.SandboxRunner", factory):
            review = run_review(ReviewInputs(
                engine="skill", snapshot=snap, file_bytes=byts, sandbox_config=cfg))
        assert holder == {}
        assert review.skillSandbox["status"] == "failed"
        assert review.skillSandbox["observationStatus"] == "unavailable"

    def test_product_sandbox_uses_one_hardening_reason_on_every_host(self, tmp_path):
        snap, byts = _skill_snapshot(tmp_path)
        obs = SandboxObservation(status="not_available",
                                 reasonCode="sandbox_exec_missing")
        factory, holder = _make_fake_runner_factory(obs)
        cfg = SandboxConfig(enabled=True, entry_point="scripts/main.py")
        with patch("verity.sandbox.runner.SandboxRunner", factory):
            review = run_review(ReviewInputs(
                engine="skill", snapshot=snap, file_bytes=byts, sandbox_config=cfg))
        assert holder == {}
        assert review.skillSandbox["status"] == "failed"
        assert review.skillSandbox["observationStatus"] == "unavailable"
        assert review.skillSandbox["reasonCode"] == \
            "sandbox_isolation_hardening_required"
