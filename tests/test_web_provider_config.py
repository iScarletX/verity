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
