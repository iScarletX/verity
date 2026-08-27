"""Round 74 (Web UI layer): opt-in gating tests for the V1.5 Prompt
black-box card and the V2 Skill sandbox card exposed through the local
Web MVP (``/api/review/prompt``, ``/api/review/skill``).

Scope: only the NEW web-layer wiring added in this round --
``_maybe_blackbox_run``/``_maybe_sandbox_run``'s two-independent-signal
gate (``<stage>_enabled`` AND ``<stage>_confirm``, both required and
never satisfied by mere field presence), and that the request/response
plumbing in ``review_prompt``/``review_skill`` correctly threads
``BlackboxConfig``/``SandboxConfig`` through to ``run_review`` and cleans
up the ephemeral API-key env var. The underlying engine-level behaviour
(stage results, aggregation into ``Review.promptBlackbox``/
``.skillSandbox``, capability matrix, confidence limitations) is already
covered by ``test_blackbox_sandbox_integration.py`` and is not re-tested
here.

No real network call and no real ``sandbox-exec`` subprocess is made
anywhere in this file: the black-box HTTP transport is monkeypatched at
``verity.blackbox.runner._build_opener`` (same technique as
``test_blackbox_sandbox_integration.py``) and the sandbox execution at
``verity.sandbox.runner.SandboxRunner`` with a fake in-memory runner.
"""
from __future__ import annotations

import io
import json
import os
from typing import Any, Dict, List
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from verity.sandbox.config import SandboxConfig
from verity.sandbox.models import SandboxObservation
from verity.web import app as web_app
from verity.web import create_app


class _EmptyWebCredentials:
    """Never-real-Keychain credential store for tests. See test_web_mvp.py's
    identical class for the full rationale: without this, a test app
    instance could inherit whatever the current machine's real macOS
    Keychain happens to hold from an unrelated manual session."""

    def save_key(self, value):
        raise AssertionError("this test credential store must remain empty")

    def load_key(self):
        return None

    def has_key(self):
        return False

    def delete_key(self):
        return None


@pytest.fixture
def client(tmp_path):
    from verity.web.provider_settings import (
        ProviderPreferenceStore, ProviderSettingsStore)
    provider_settings = ProviderSettingsStore(
        ProviderPreferenceStore(tmp_path / "provider"), _EmptyWebCredentials())
    app = create_app(store_capacity=8, store_ttl_seconds=60,
                     history_root=tmp_path / "history",
                     provider_settings_store=provider_settings)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def _skill_multipart_fields(extra=None):
    fields = [
        ("profile", (None, "standard")),
        ("files", ("skill/SKILL.md",
                   b"---\nname: t\ndescription: t\n---\n",
                   "application/octet-stream")),
        ("files", ("skill/scripts/run.py", b"print('hi')\n",
                   "application/octet-stream")),
    ]
    if extra:
        fields.extend(extra)
    return fields


def _bb_env_leaks():
    return {k for k in os.environ if k.startswith("VERITY_WEB_BLACKBOX_KEY_")}


# --------------------------------------------------------------------- #
# A. _maybe_blackbox_run: unit-level gating                             #
# --------------------------------------------------------------------- #

class TestMaybeBlackboxRun:
    def test_not_requested_returns_none(self):
        assert web_app._maybe_blackbox_run({}) is None
        # Presence of OTHER blackbox_* fields alone (no blackbox_enabled)
        # must still be a no-op -- mere field presence is never enough,
        # unlike the semantic Provider panel's convention.
        assert web_app._maybe_blackbox_run({
            "blackbox_base_url": "https://x.example/v1",
            "blackbox_model": "m",
            "blackbox_api_key": "k",
        }) is None

    def test_enabled_without_confirm_is_rejected(self):
        result = web_app._maybe_blackbox_run({"blackbox_enabled": True})
        assert result.status_code == 400
        assert json.loads(result.body)["error"]["code"] == \
            "blackbox_confirmation_required"

    def test_confirm_string_true_is_not_accepted_over_json(self):
        # JSON body: confirm must be the JSON boolean true. The literal
        # string "true" is the multipart/sandbox convention only, and
        # must not satisfy this gate.
        result = web_app._maybe_blackbox_run({
            "blackbox_enabled": True, "blackbox_confirm": "true",
        })
        assert result.status_code == 400
        assert json.loads(result.body)["error"]["code"] == \
            "blackbox_confirmation_required"

    @pytest.mark.parametrize("missing_field,code", [
        ("blackbox_base_url", "blackbox_base_url_required"),
        ("blackbox_model", "blackbox_model_required"),
        ("blackbox_api_key", "blackbox_api_key_required"),
    ])
    def test_missing_required_field_is_rejected(self, missing_field, code):
        payload = {
            "blackbox_enabled": True, "blackbox_confirm": True,
            "blackbox_base_url": "https://x.example/v1",
            "blackbox_model": "m",
            "blackbox_api_key": "k",
        }
        del payload[missing_field]
        before = _bb_env_leaks()
        result = web_app._maybe_blackbox_run(payload)
        assert result.status_code == 400
        assert json.loads(result.body)["error"]["code"] == code
        assert _bb_env_leaks() == before

    def test_bad_base_url_is_rejected_without_leaking_env_var(self):
        before = _bb_env_leaks()
        result = web_app._maybe_blackbox_run({
            "blackbox_enabled": True, "blackbox_confirm": True,
            "blackbox_base_url": "http://evil.example/v1",
            "blackbox_model": "m", "blackbox_api_key": "k",
        })
        assert result.status_code == 400
        assert _bb_env_leaks() == before

    def test_bad_scenario_ids_type_is_rejected(self):
        result = web_app._maybe_blackbox_run({
            "blackbox_enabled": True, "blackbox_confirm": True,
            "blackbox_base_url": "https://x.example/v1",
            "blackbox_model": "m", "blackbox_api_key": "k",
            "blackbox_scenario_ids": "not-a-list",
        })
        assert result.status_code == 400
        assert json.loads(result.body)["error"]["code"] == \
            "bad_blackbox_scenario_ids"

    def test_too_many_scenario_ids_is_rejected(self):
        before = _bb_env_leaks()
        result = web_app._maybe_blackbox_run({
            "blackbox_enabled": True, "blackbox_confirm": True,
            "blackbox_base_url": "https://x.example/v1",
            "blackbox_model": "m", "blackbox_api_key": "k",
            "blackbox_scenario_ids": [f"s{i}" for i in range(65)],
        })
        assert result.status_code == 400
        assert json.loads(result.body)["error"]["code"] == "bad_blackbox_config"
        assert _bb_env_leaks() == before

    def test_scenario_id_too_long_is_rejected(self):
        before = _bb_env_leaks()
        result = web_app._maybe_blackbox_run({
            "blackbox_enabled": True, "blackbox_confirm": True,
            "blackbox_base_url": "https://x.example/v1",
            "blackbox_model": "m", "blackbox_api_key": "k",
            "blackbox_scenario_ids": ["s" * 101],
        })
        assert result.status_code == 400
        assert json.loads(result.body)["error"]["code"] == "bad_blackbox_config"
        assert _bb_env_leaks() == before

    def test_non_numeric_budget_field_is_rejected(self):
        result = web_app._maybe_blackbox_run({
            "blackbox_enabled": True, "blackbox_confirm": True,
            "blackbox_base_url": "https://x.example/v1",
            "blackbox_model": "m", "blackbox_api_key": "k",
            "blackbox_max_calls": "50",  # string, not a JSON number
        })
        assert result.status_code == 400
        assert json.loads(result.body)["error"]["code"] == \
            "bad_blackbox_max_calls"

    def test_full_valid_request_builds_config_with_ephemeral_key(self):
        before = _bb_env_leaks()
        result = web_app._maybe_blackbox_run({
            "blackbox_enabled": True, "blackbox_confirm": True,
            "blackbox_base_url": "https://x.example/v1/",
            "blackbox_model": "stub-model",
            "blackbox_api_key": "sk-secret-value",
            "blackbox_scenario_ids": ["injection_override_simple"],
            "blackbox_max_calls": 5,
            "blackbox_timeout_seconds": 12.5,
            "blackbox_max_tokens": 400,
        })
        assert isinstance(result, tuple)
        cfg, env_name = result
        try:
            assert cfg.enabled is True
            assert cfg.base_url == "https://x.example/v1"
            assert cfg.model_id == "stub-model"
            assert cfg.scenario_ids == ("injection_override_simple",)
            assert cfg.max_calls == 5
            assert cfg.timeout_seconds == 12.5
            assert cfg.max_tokens_per_response == 400
            assert env_name.startswith("VERITY_WEB_BLACKBOX_KEY_")
            assert os.environ[env_name] == "sk-secret-value"
            assert "sk-secret-value" not in repr(cfg)
        finally:
            from verity.web.provider_web import clear_ephemeral_key
            clear_ephemeral_key(env_name)
        assert env_name not in os.environ
        assert _bb_env_leaks() == before


# --------------------------------------------------------------------- #
# B. _maybe_sandbox_run: unit-level gating                              #
# --------------------------------------------------------------------- #

class TestMaybeSandboxRun:
    def test_not_requested_returns_none(self):
        assert web_app._maybe_sandbox_run({}) is None
        assert web_app._maybe_sandbox_run({
            "sandbox_entry_point": "scripts/run.py",
        }) is None

    def test_enabled_without_confirm_is_rejected(self):
        result = web_app._maybe_sandbox_run({"sandbox_enabled": "true"})
        assert result.status_code == 400
        error = json.loads(result.body)["error"]
        assert error["code"] == "sandbox_confirmation_required"
        assert "currently unavailable" in error["message"]
        assert "does not execute" in error["message"]
        assert "really executes" not in error["message"]

    def test_confirm_python_bool_is_not_accepted_over_form(self):
        # Multipart form values are always plain strings; only the exact
        # literal "true" satisfies this gate.
        result = web_app._maybe_sandbox_run({
            "sandbox_enabled": "true", "sandbox_confirm": True,
        })
        assert result.status_code == 400
        assert json.loads(result.body)["error"]["code"] == \
            "sandbox_confirmation_required"

    def test_missing_entry_point_is_rejected(self):
        result = web_app._maybe_sandbox_run({
            "sandbox_enabled": "true", "sandbox_confirm": "true",
        })
        assert result.status_code == 400
        assert json.loads(result.body)["error"]["code"] == \
            "sandbox_entry_point_required"

    def test_bad_argv_json_is_rejected(self):
        result = web_app._maybe_sandbox_run({
            "sandbox_enabled": "true", "sandbox_confirm": "true",
            "sandbox_entry_point": "scripts/run.py",
            "sandbox_argv": "not json",
        })
        assert result.status_code == 400
        assert json.loads(result.body)["error"]["code"] == "bad_sandbox_argv"

    def test_too_many_argv_entries_is_rejected(self):
        result = web_app._maybe_sandbox_run({
            "sandbox_enabled": "true", "sandbox_confirm": "true",
            "sandbox_entry_point": "scripts/run.py",
            "sandbox_argv": json.dumps([f"--flag{i}" for i in range(65)]),
        })
        assert result.status_code == 400
        assert json.loads(result.body)["error"]["code"] == "bad_sandbox_config"

    def test_argv_entry_too_long_is_rejected(self):
        result = web_app._maybe_sandbox_run({
            "sandbox_enabled": "true", "sandbox_confirm": "true",
            "sandbox_entry_point": "scripts/run.py",
            "sandbox_argv": json.dumps(["a" * 4097]),
        })
        assert result.status_code == 400
        assert json.loads(result.body)["error"]["code"] == "bad_sandbox_config"

    def test_bad_int_field_is_rejected(self):
        result = web_app._maybe_sandbox_run({
            "sandbox_enabled": "true", "sandbox_confirm": "true",
            "sandbox_entry_point": "scripts/run.py",
            "sandbox_cpu_seconds": "not-an-int",
        })
        assert result.status_code == 400
        assert json.loads(result.body)["error"]["code"] == \
            "bad_sandbox_cpu_seconds"

    def test_full_valid_request_builds_config(self):
        cfg = web_app._maybe_sandbox_run({
            "sandbox_enabled": "true", "sandbox_confirm": "true",
            "sandbox_entry_point": "scripts/run.py",
            "sandbox_argv": json.dumps(["--flag", "value"]),
            "sandbox_cpu_seconds": "5",
            "sandbox_memory_mb": "128",
            "sandbox_wall_seconds": "9",
        })
        assert isinstance(cfg, SandboxConfig)
        assert cfg.enabled is True
        assert cfg.entry_point == "scripts/run.py"
        assert cfg.argv == ("--flag", "value")
        assert cfg.cpu_seconds == 5
        assert cfg.memory_mb == 128
        assert cfg.wall_seconds == 9


# --------------------------------------------------------------------- #
# C. End-to-end: default "开始审查" path is byte-for-byte unaffected     #
# --------------------------------------------------------------------- #

class TestDefaultReviewUnaffected:
    def test_prompt_review_without_blackbox_fields_has_no_blackbox_view(
            self, client):
        r = client.post("/api/review/prompt", json={
            "text": "You are a helpful assistant.",
            "prompt_kind": "system_prompt",
        })
        assert r.status_code == 200, r.text
        view = r.json()
        assert view.get("promptBlackbox") is None
        assert view["capabilities"]["promptBlackbox"]["status"] == "not_enabled"

    def test_skill_review_without_sandbox_fields_has_no_sandbox_view(
            self, client):
        r = client.post("/api/review/skill", files=_skill_multipart_fields())
        assert r.status_code == 200, r.text
        view = r.json()
        assert view.get("skillSandbox") is None
        assert view["capabilities"]["skillSandbox"]["status"] == "not_enabled"


# --------------------------------------------------------------------- #
# D. End-to-end: opt-in requested but incomplete -> clean 400, no run    #
# --------------------------------------------------------------------- #

class TestEndToEndRejections:
    def test_prompt_review_blackbox_enabled_without_confirm_is_400(
            self, client):
        r = client.post("/api/review/prompt", json={
            "text": "hi", "prompt_kind": "user_prompt",
            "blackbox_enabled": True,
            "blackbox_base_url": "https://x.example/v1",
            "blackbox_model": "m",
            "blackbox_api_key": "k",
        })
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "blackbox_confirmation_required"

    def test_skill_review_sandbox_enabled_without_confirm_is_400(self, client):
        r = client.post("/api/review/skill", files=_skill_multipart_fields([
            ("sandbox_enabled", (None, "true")),
            ("sandbox_entry_point", (None, "scripts/run.py")),
        ]))
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "sandbox_confirmation_required"


# --------------------------------------------------------------------- #
# E. End-to-end: fully confirmed opt-in actually runs (mocked transport) #
# --------------------------------------------------------------------- #

def _stub_bb_response(content: str):
    body = json.dumps({
        "choices": [{"message": {"content": content, "role": "assistant"}}]
    }).encode()

    class _Resp:
        def __init__(self):
            self._io = io.BytesIO(body)

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
    """Drop-in replacement for verity.sandbox.runner.SandboxRunner that
    never spawns a real process."""

    def __init__(self, observation: SandboxObservation, *args, **kwargs):
        self._observation = observation
        self.calls: List[Dict[str, Any]] = []

    def run(self, request, *, snapshot, file_bytes):
        self.calls.append({"entry_point": request.entry_point})
        return self._observation


class TestEndToEndConfirmedRunIsAggregated:
    def test_prompt_blackbox_confirmed_run_is_reflected_in_view(self, client):
        stub_opener = _StubOpener(["I'm sorry, I cannot do that."])
        before = _bb_env_leaks()
        with patch("verity.blackbox.runner._build_opener",
                   return_value=stub_opener):
            r = client.post("/api/review/prompt", json={
                "text": "You are a helpful assistant.",
                "prompt_kind": "system_prompt",
                "blackbox_enabled": True,
                "blackbox_confirm": True,
                "blackbox_base_url": "https://x.example/v1",
                "blackbox_model": "stub-model",
                "blackbox_api_key": "sk-secret-value",
                "blackbox_scenario_ids": ["injection_override_simple"],
            })
        assert r.status_code == 200, r.text
        view = r.json()
        assert view["promptBlackbox"]["status"] == "completed"
        assert view["promptBlackbox"]["model"] == "stub-model"
        assert view["capabilities"]["promptBlackbox"]["status"] == "completed"
        # The ephemeral API-key env var must be cleared after the request
        # completes, and the raw key must never appear in the response.
        assert _bb_env_leaks() == before
        assert "sk-secret-value" not in r.text

    def test_skill_sandbox_confirmed_request_fails_closed_without_runner(
            self, client):
        obs = SandboxObservation(
            status="completed", isolationMechanism="sandbox-exec",
            entryPoint="scripts/run.py", exitCode=0, durationSeconds=0.01)

        constructions = []

        def factory(*args, **kwargs):
            constructions.append((args, kwargs))
            raise AssertionError("Web product path must not construct SandboxRunner")

        with patch("verity.sandbox.runner.SandboxRunner", factory):
            r = client.post("/api/review/skill", files=_skill_multipart_fields([
                ("sandbox_enabled", (None, "true")),
                ("sandbox_confirm", (None, "true")),
                ("sandbox_entry_point", (None, "scripts/run.py")),
            ]))
        assert r.status_code == 200, r.text
        view = r.json()
        assert constructions == []
        assert view["skillSandbox"]["status"] == "failed"
        assert view["skillSandbox"]["observationStatus"] == "unavailable"
        assert view["skillSandbox"]["reasonCode"] == \
            "sandbox_isolation_hardening_required"
        assert view["capabilities"]["skillSandbox"]["status"] == "failed"
