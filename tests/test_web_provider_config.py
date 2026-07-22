"""Web Provider-config surface for the experimental semantic path.

These tests cover the safety-critical behaviour of exposing a Provider
base-url + API key + model picker in the local Web UI:

- the model-list proxy validates the base URL like the transport does;
- the API key is placed in a random, transient env var and cleared after use;
- the key never enters SemanticConfig serialization or a ProviderConfig field;
- offline behaviour (no network) is deterministic and never raises out of a
  ``finally``.

No real network call is made here; the OpenRouter listing is exercised
separately by hand during walkthroughs, never in CI.
"""
from __future__ import annotations

import os

import pytest

from verity.web import provider_web as pw


class TestBaseUrlValidation:
    def test_https_ok(self):
        assert pw.validate_base_url("https://openrouter.ai/api/v1") == \
            "https://openrouter.ai/api/v1"

    def test_trailing_slash_stripped(self):
        assert pw.validate_base_url("https://x.example/v1/") == "https://x.example/v1"

    def test_loopback_http_ok(self):
        assert pw.validate_base_url("http://127.0.0.1:9000/v1") == \
            "http://127.0.0.1:9000/v1"

    @pytest.mark.parametrize("bad", [
        "", "   ", "ftp://x.example", "http://evil.example/v1",
        "https://user:pass@x.example/v1", "https://x.example/v1?q=1",
        "https://x.example/v1#frag", "not a url",
    ])
    def test_rejected(self, bad):
        with pytest.raises(pw.ProviderWebError):
            pw.validate_base_url(bad)


class TestEphemeralKey:
    def _snapshot_verity_web_keys(self):
        return {k for k in os.environ if k.startswith("VERITY_WEB_KEY_")}

    def test_key_lives_in_random_env_and_is_cleared(self):
        before = self._snapshot_verity_web_keys()
        sem_cfg, gen, val, env_name = pw.build_semantic_config_with_ephemeral_key(
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-secret-KEY-VALUE",
            generator_model="openai/gpt-5.6-sol",
            validator_model="openai/gpt-5.6-sol",
            egress_policy="metadata_only")
        try:
            # A new, random env var now holds the key.
            assert env_name.startswith("VERITY_WEB_KEY_")
            assert env_name not in before
            assert os.environ[env_name] == "sk-secret-KEY-VALUE"
            # Config carries only the NAME, never the value.
            gen_cfg = sem_cfg.provider_config["candidate_generator"]
            assert gen_cfg.credentials.api_key_env == env_name
            assert "sk-secret-KEY-VALUE" not in repr(sem_cfg)
            assert "sk-secret-KEY-VALUE" not in repr(gen_cfg)
            # resolve() reads the transient env var.
            assert gen_cfg.credentials.resolve() == "sk-secret-KEY-VALUE"
        finally:
            pw.clear_ephemeral_key(env_name)
        # After clearing, the key is gone from the environment.
        assert env_name not in os.environ
        assert self._snapshot_verity_web_keys() == before

    def test_two_roles_are_distinct_objects_sharing_one_key(self):
        sem_cfg, gen, val, env_name = pw.build_semantic_config_with_ephemeral_key(
            base_url="https://openrouter.ai/api/v1",
            api_key="k",
            generator_model="m1", validator_model="m2",
            egress_policy="redacted_evidence")
        try:
            assert gen is not val
            assert sem_cfg.provider_config["candidate_generator"].model_id == "m1"
            assert sem_cfg.provider_config["validator"].model_id == "m2"
            assert sem_cfg.enabled is True
            assert sem_cfg.egress_policy == "redacted_evidence"
        finally:
            pw.clear_ephemeral_key(env_name)

    def test_bad_model_clears_key_and_raises(self):
        before = {k for k in os.environ if k.startswith("VERITY_WEB_KEY_")}
        with pytest.raises(pw.ProviderWebError):
            pw.build_semantic_config_with_ephemeral_key(
                base_url="https://openrouter.ai/api/v1", api_key="k",
                generator_model="", validator_model="m",
                egress_policy="metadata_only")
        after = {k for k in os.environ if k.startswith("VERITY_WEB_KEY_")}
        assert after == before  # no leaked env var

    def test_missing_key_rejected(self):
        with pytest.raises(pw.ProviderWebError):
            pw.build_semantic_config_with_ephemeral_key(
                base_url="https://openrouter.ai/api/v1", api_key="",
                generator_model="m", validator_model="m",
                egress_policy="metadata_only")

    def test_clear_is_idempotent_and_noop_on_none(self):
        pw.clear_ephemeral_key(None)
        pw.clear_ephemeral_key("VERITY_WEB_KEY_NONEXISTENT")


class TestPartialSemanticView:
    """When a semantic run fails midway but confirmed some candidates, the
    view must surface those advisory findings with a ``partial`` flag, without
    merging them into the main completed-findings list or the score.
    """

    def _base_report(self, semantic):
        return {
            "engine": "prompt",
            "findings": [],
            "evidences": [],
            "analyzerModel": {},
            "coverage": {"status": "sufficient", "reasonCodes": []},
            "capabilities": {},
            "score": {"status": "available", "value": 100},
            "reviewConfidence": {"grade": "C"},
            "remediations": [],
            "semantic": semantic,
        }

    def test_failed_run_with_confirmed_findings_is_partial(self):
        from verity.web.view import build_view_model
        sem = {
            "status": "failed", "reasonCode": "network_error",
            "egressPolicy": "redacted_evidence",
            "callCounts": {"generator": 2, "validator": 2},
            "candidates": [{}, {}],
            "assessments": [{"state": "confirmed"}, {"state": "confirmed"}],
            "findings": [
                {"findingId": "F-1", "findingType": "semantic.prompt.instruction_conflict",
                 "severity": "medium", "claim": "conflict", "origin": {"kind": "semantic_validation"}},
            ],
            "planItems": [],
        }
        view = build_view_model(self._base_report(sem), "rid")
        assert view["semantic"]["status"] == "failed"
        assert view["semantic"]["partial"] is True
        assert len(view["semantic"]["findings"]) == 1
        # The partial semantic finding must NOT leak into the main list/score.
        assert view["findings"] == []
        assert view["counts"]["medium"] == 0

    def test_completed_run_is_not_partial(self):
        from verity.web.view import build_view_model
        sem = {
            "status": "completed", "reasonCode": None,
            "egressPolicy": "metadata_only",
            "callCounts": {"generator": 1, "validator": 1},
            "candidates": [{}],
            "assessments": [{"state": "confirmed"}],
            "findings": [
                {"findingId": "F-1", "findingType": "semantic.prompt.instruction_conflict",
                 "severity": "medium", "claim": "c", "origin": {"kind": "semantic_validation"}},
            ],
            "planItems": [],
        }
        view = build_view_model(self._base_report(sem), "rid")
        assert view["semantic"]["partial"] is False

    def test_failed_run_without_findings_is_not_partial(self):
        from verity.web.view import build_view_model
        sem = {"status": "failed", "reasonCode": "network_error",
               "egressPolicy": "off", "callCounts": {}, "candidates": [],
               "assessments": [], "findings": [], "planItems": []}
        view = build_view_model(self._base_report(sem), "rid")
        assert view["semantic"]["partial"] is False


class TestEvalProviderRetry:
    def test_transient_network_error_is_retried_then_succeeds(self):
        from verity.semantic.eval_provider import OpenAICompatibleEvalProvider
        from verity.semantic.provider import ProviderCall, ProviderResponse
        from verity.semantic.config import ProviderConfig, ProviderCredentials
        import os
        os.environ["VERITY_TEST_KEY_RETRY"] = "k"
        try:
            cfg = ProviderConfig(
                role="validator", provider_id="p", model_id="m",
                base_url="https://x.example/v1",
                credentials=ProviderCredentials(api_key_env="VERITY_TEST_KEY_RETRY"))
            prov = OpenAICompatibleEvalProvider(config=cfg,
                                                retry_backoff_seconds=0.0)
            calls = {"n": 0}

            def fake_once(*, call, request):
                calls["n"] += 1
                if calls["n"] < 2:
                    return ProviderResponse(ok=False, reason_code="network_error")
                return ProviderResponse(ok=True, payload={"ok": True})

            prov._call_once = fake_once
            call = ProviderCall(review_id="r", egress_policy="metadata_only",
                                call_role="validator", call_id="c",
                                request_bytes=1, request_digest_sha256="x")
            resp = prov._call(call=call, request={})
            assert resp.ok is True
            assert calls["n"] == 2
        finally:
            os.environ.pop("VERITY_TEST_KEY_RETRY", None)

    def test_logical_error_is_not_retried(self):
        from verity.semantic.eval_provider import OpenAICompatibleEvalProvider
        from verity.semantic.provider import ProviderCall, ProviderResponse
        from verity.semantic.config import ProviderConfig, ProviderCredentials
        import os
        os.environ["VERITY_TEST_KEY_RETRY2"] = "k"
        try:
            cfg = ProviderConfig(
                role="validator", provider_id="p", model_id="m",
                base_url="https://x.example/v1",
                credentials=ProviderCredentials(api_key_env="VERITY_TEST_KEY_RETRY2"))
            prov = OpenAICompatibleEvalProvider(config=cfg,
                                                retry_backoff_seconds=0.0)
            calls = {"n": 0}

            def fake_once(*, call, request):
                calls["n"] += 1
                return ProviderResponse(ok=False, reason_code="invalid_json")

            prov._call_once = fake_once
            call = ProviderCall(review_id="r", egress_policy="metadata_only",
                                call_role="validator", call_id="c",
                                request_bytes=1, request_digest_sha256="x")
            resp = prov._call(call=call, request={})
            assert resp.ok is False
            assert resp.reason_code == "invalid_json"
            assert calls["n"] == 1  # not retried
        finally:
            os.environ.pop("VERITY_TEST_KEY_RETRY2", None)


class TestModelsEndpoint:
    def _client(self):
        from starlette.testclient import TestClient
        from verity.web import create_app
        return TestClient(create_app(), base_url="http://127.0.0.1")

    def test_models_requires_json(self):
        with self._client() as c:
            r = c.post("/api/models", data="x",
                       headers={"Content-Type": "text/plain"})
            assert r.status_code == 415

    def test_models_bad_base_url_is_clean_error(self):
        with self._client() as c:
            r = c.post("/api/models",
                       json={"provider_base_url": "http://evil.example",
                             "provider_api_key": "k"})
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "bad_base_url"

    def test_models_missing_key_is_error(self):
        with self._client() as c:
            r = c.post("/api/models",
                       json={"provider_base_url": "https://openrouter.ai/api/v1",
                             "provider_api_key": ""})
            assert r.status_code == 400
            assert r.json()["error"]["code"] == "api_key_required"
