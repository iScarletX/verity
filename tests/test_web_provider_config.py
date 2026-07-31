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

import json
import os

import pytest

from verity.web import provider_web as pw
from verity.web.provider_settings import (
    ProviderPreferenceStore,
    ProviderPreferences,
    ProviderSettingsStore,
)


class _MemoryCredentials:
    def __init__(self):
        self.value = None

    def save_key(self, value):
        self.value = value

    def load_key(self):
        return self.value

    def has_key(self):
        return self.value is not None

    def delete_key(self):
        self.value = None


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


class TestMultiValidatorEphemeralKey:
    """Multi-validator vote feature: 2-3 independently configured validator
    Providers sharing one base_url/api_key. Configuring only one validator
    model must keep using the singular helper unchanged (covered above);
    these tests cover the plural sibling and its extra safety checks.
    """

    def _snapshot_verity_web_keys(self):
        return {k for k in os.environ if k.startswith("VERITY_WEB_KEY_")}

    def test_two_validator_models_share_one_key_and_vote_independently(self):
        before = self._snapshot_verity_web_keys()
        sem_cfg, gen, vals, env_name = \
            pw.build_semantic_config_with_multi_validators_key(
                base_url="https://openrouter.ai/api/v1",
                api_key="sk-shared-key",
                generator_model="gen-model",
                validator_models=["val-model-a", "val-model-b"],
                egress_policy="redacted_evidence")
        try:
            assert isinstance(vals, list) and len(vals) == 2
            assert vals[0] is not vals[1]
            assert gen not in vals
            # All three providers share the SAME ephemeral env var; the key
            # is never duplicated into a second env var.
            assert env_name.startswith("VERITY_WEB_KEY_")
            assert env_name not in before
            assert os.environ[env_name] == "sk-shared-key"
            gen_cfg = sem_cfg.provider_config["candidate_generator"]
            assert gen_cfg.credentials.api_key_env == env_name
            for v in vals:
                assert v.config.credentials.api_key_env == env_name
            assert vals[0].config.model_id == "val-model-a"
            assert vals[1].config.model_id == "val-model-b"
            assert "sk-shared-key" not in repr(sem_cfg)
        finally:
            pw.clear_ephemeral_key(env_name)
        assert env_name not in os.environ
        assert self._snapshot_verity_web_keys() == before

    def test_three_validator_models_is_the_max_allowed(self):
        sem_cfg, gen, vals, env_name = \
            pw.build_semantic_config_with_multi_validators_key(
                base_url="https://openrouter.ai/api/v1", api_key="k",
                generator_model="g",
                validator_models=["v1", "v2", "v3"],
                egress_policy="metadata_only")
        try:
            assert len(vals) == 3
        finally:
            pw.clear_ephemeral_key(env_name)

    def test_four_validator_models_is_rejected(self):
        with pytest.raises(pw.ProviderWebError):
            pw.build_semantic_config_with_multi_validators_key(
                base_url="https://openrouter.ai/api/v1", api_key="k",
                generator_model="g",
                validator_models=["v1", "v2", "v3", "v4"],
                egress_policy="metadata_only")

    def test_single_validator_model_rejected_by_plural_helper(self):
        # The plural helper is only for 2+ votes; one model must go through
        # build_semantic_config_with_ephemeral_key instead.
        with pytest.raises(pw.ProviderWebError):
            pw.build_semantic_config_with_multi_validators_key(
                base_url="https://openrouter.ai/api/v1", api_key="k",
                generator_model="g", validator_models=["only-one"],
                egress_policy="metadata_only")

    def test_bad_model_in_list_clears_key_and_raises(self):
        before = self._snapshot_verity_web_keys()
        with pytest.raises(pw.ProviderWebError):
            pw.build_semantic_config_with_multi_validators_key(
                base_url="https://openrouter.ai/api/v1", api_key="k",
                generator_model="g", validator_models=["ok-model", ""],
                egress_policy="metadata_only")
        after = self._snapshot_verity_web_keys()
        assert after == before  # no leaked env var


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
            "capabilities": {
                "semantic": {
                    "status": (
                        "completed"
                        if semantic.get("status") == "completed"
                        else "failed"
                    ),
                },
            },
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
            "evidences": [{
                "evidenceId": "EV-1",
                "locations": [{
                    "artifactPath": "prompt.txt",
                    "sourceByteRange": {"start": 3, "end": 11},
                }],
            }],
            "findings": [
                {"findingId": "F-1", "findingType": "semantic.prompt.instruction_conflict",
                 "severity": "medium", "claim": "conflict",
                 "evidenceIds": ["EV-1"],
                 "origin": {"kind": "semantic_validation"}},
            ],
            "stageStats": {
                "semantic.prompt.instruction_conflict": {
                    "extractorSeedCount": 1,
                    "catalogHintProposedCount": 1,
                    "generatorAcceptedCandidateCount": 0,
                    "queuedCandidateCount": 1,
                    "validatorStates": {"confirmed": 1},
                },
            },
            "planItems": [],
        }
        view = build_view_model(self._base_report(sem), "rid")
        assert view["semantic"]["status"] == "failed"
        assert view["semantic"]["partial"] is True
        assert len(view["semantic"]["findings"]) == 1
        assert view["semantic"]["findings"][0]["evidences"][0] == {
            "artifactPath": "prompt.txt",
            "startByte": 3,
            "endByte": 11,
            "redactedPreview": None,
            "sensitivity": "normal",
        }
        assert view["semantic"]["stageStats"][0][
            "catalogHintProposedCount"] == 1
        assert view["headline"]["code"] == "semantic_block"
        assert view["nextSteps"]["steps"][0]["code"] == "rerun_semantic"
        assert view["score"]["status"] == "unavailable"
        assert view["score"]["value"] is None
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

    def test_invalid_json_is_retried_up_to_max_attempts(self):
        """Round 69 fix: invalid_json was added to _RETRYABLE_REASONS because
        Anthropic and other providers occasionally return HTTP 200 with a
        non-JSON body under transient load. We now retry up to max_attempts.
        This test was previously named test_logical_error_is_not_retried and
        asserted calls["n"] == 1; after the fix it must use all 3 attempts."""
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
                                                max_attempts=3,
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
            # After the fix invalid_json is retried up to max_attempts (3)
            assert calls["n"] == 3, (
                "invalid_json should be retried up to max_attempts; "
                f"got {calls['n']} attempts instead of 3")
        finally:
            os.environ.pop("VERITY_TEST_KEY_RETRY2", None)

    def test_web_providers_bound_and_report_every_outbound_attempt(self):
        import urllib.error

        from verity.semantic.provider import ProviderCall

        class CountingFailureOpener:
            def __init__(self):
                self.requests = []

            def open(self, request, timeout):
                self.requests.append((request, timeout))
                raise urllib.error.URLError("synthetic provider outage")

        sem_cfg, generator, validator, env_name = \
            pw.build_semantic_config_with_ephemeral_key(
                base_url="https://provider.example/v1",
                api_key="synthetic-key",
                generator_model="generator-model",
                validator_model="validator-model",
                egress_policy="redacted_evidence",
            )
        try:
            cases = (
                (
                    generator,
                    generator.generate_candidates,
                    "candidate_generator",
                    sem_cfg.budget.max_candidate_generation_calls,
                ),
                (
                    validator,
                    validator.validate_candidate,
                    "validator",
                    sem_cfg.budget.max_total_validation_calls,
                ),
            )
            for provider, invoke, role, max_calls in cases:
                opener = CountingFailureOpener()
                provider.opener = opener
                provider.retry_backoff_seconds = 0.0
                responses = []
                for index in range(max_calls + 2):
                    responses.append(invoke(
                        call=ProviderCall(
                            review_id="review",
                            egress_policy="redacted_evidence",
                            call_role=role,
                            call_id=f"{role}-{index}",
                            request_bytes=2,
                            request_digest_sha256="0" * 64,
                        ),
                        request={},
                    ))

                reported_attempts = sum(
                    len(getattr(response, "attempts", ()))
                    for response in responses
                )
                assert len(opener.requests) == max_calls
                assert reported_attempts == max_calls
                assert responses[-1].reason_code == "run_budget_exhausted"
        finally:
            pw.clear_ephemeral_key(env_name)


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


class TestPersistentProviderSettings:
    def _client(self, tmp_path):
        from starlette.testclient import TestClient
        from verity.web import create_app
        credentials = _MemoryCredentials()
        settings = ProviderSettingsStore(
            ProviderPreferenceStore(tmp_path / "settings"), credentials)
        app = create_app(
            history_root=tmp_path / "history",
            provider_settings_store=settings,
        )
        return (TestClient(app, base_url="http://127.0.0.1"),
                settings, credentials)

    def test_save_load_and_clear_never_return_key(self, tmp_path):
        client, settings, credentials = self._client(tmp_path)
        key = "saved-" + "provider-key"
        with client as c:
            saved = c.put("/api/provider-settings", json={
                "baseUrl": "https://openrouter.ai/api/v1/",
                "apiKey": key,
                "generatorModel": "openai/generator",
                "validatorModel": "anthropic/validator",
            })
            assert saved.status_code == 200
            assert saved.json() == {
                "baseUrl": "https://openrouter.ai/api/v1",
                "generatorModel": "openai/generator",
                "validatorModel": "anthropic/validator",
                "keySaved": True,
            }
            assert key not in saved.text
            loaded = c.get("/api/provider-settings")
            assert loaded.json() == saved.json()
            assert key not in loaded.text
            assert credentials.value == key

            cleared = c.delete("/api/provider-settings")
            assert cleared.status_code == 200
            assert cleared.json() == {
                "baseUrl": "",
                "generatorModel": "",
                "validatorModel": "",
                "keySaved": False,
            }
            assert settings.public_settings() == (
                ProviderPreferences(), False)

    @pytest.mark.parametrize("payload,code", [
        ({"baseUrl": 123}, "bad_base_url"),
        ({"baseUrl": "https://user:pass@example.com"}, "bad_base_url"),
        ({"generatorModel": []}, "bad_model"),
    ])
    def test_invalid_saved_preferences_are_client_errors(
            self, tmp_path, payload, code):
        client, _settings, _credentials = self._client(tmp_path)
        with client as c:
            response = c.put("/api/provider-settings", json=payload)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == code

    def test_saved_key_requires_new_key_when_provider_changes(
            self, tmp_path):
        client, _settings, _credentials = self._client(tmp_path)
        with client as c:
            assert c.put("/api/provider-settings", json={
                "baseUrl": "https://saved.example",
                "apiKey": "saved-key",
                "generatorModel": "saved-g",
                "validatorModel": "saved-v",
            }).status_code == 200
            changed = c.put("/api/provider-settings", json={
                "baseUrl": "https://other.example",
                "apiKey": "",
                "generatorModel": "other-g",
                "validatorModel": "other-v",
            })
            restored = c.get("/api/provider-settings")

        assert changed.status_code == 400
        assert changed.json()["error"]["code"] == "api_key_required"
        assert restored.json() == {
            "baseUrl": "https://saved.example",
            "generatorModel": "saved-g",
            "validatorModel": "saved-v",
            "keySaved": True,
        }

    def test_model_listing_can_use_saved_address_and_key(
            self, tmp_path, monkeypatch):
        client, _settings, _credentials = self._client(tmp_path)
        seen = {}

        def fake_list(base_url, api_key):
            seen.update(base_url=base_url, api_key=api_key)
            return [{"id": "model-a", "name": "Model A"}]

        monkeypatch.setattr(pw, "list_models", fake_list)
        with client as c:
            assert c.put("/api/provider-settings", json={
                "baseUrl": "https://openrouter.ai/api/v1",
                "apiKey": "saved-key",
                "generatorModel": "model-a",
                "validatorModel": "model-a",
            }).status_code == 200
            response = c.post("/api/models", json={})
        assert response.status_code == 200
        assert response.json()["models"][0]["id"] == "model-a"
        assert seen == {
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "saved-key",
        }

    def test_saved_semantic_settings_force_maximum_egress(
            self, tmp_path, monkeypatch):
        from verity.web import app as web_app

        credentials = _MemoryCredentials()
        store = ProviderSettingsStore(
            ProviderPreferenceStore(tmp_path), credentials)
        store.save(ProviderPreferences(
            base_url="https://openrouter.ai/api/v1",
            generator_model="generator",
            validator_model="validator",
        ), api_key="saved-key")
        seen = {}

        def fake_build(**kwargs):
            seen.update(kwargs)
            return ("config", "generator", "validator", "env")

        monkeypatch.setattr(
            pw, "build_semantic_config_with_ephemeral_key", fake_build)
        # No on/off flag any more: a saved Provider config is enough to
        # attempt semantic automatically; the request need not (and does
        # not) set any semantic-enabled-style field at all.
        plan = web_app._maybe_semantic_run({
            "egress_policy": "metadata_only",
        }, store)

        assert plan == ("config", "generator", "validator", "env")
        assert seen == {
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "saved-key",
            "generator_model": "generator",
            "validator_model": "validator",
            "egress_policy": "redacted_evidence",
        }

    def test_request_validator_models_routes_to_multi_validator_builder(
            self, tmp_path, monkeypatch):
        from verity.web import app as web_app

        credentials = _MemoryCredentials()
        store = ProviderSettingsStore(
            ProviderPreferenceStore(tmp_path), credentials)
        store.save(ProviderPreferences(
            base_url="https://openrouter.ai/api/v1",
            generator_model="generator",
            validator_model="validator-fallback",
        ), api_key="saved-key")
        seen = {}

        def fake_build(**kwargs):
            seen.update(kwargs)
            return ("config", "generator", ["val-a", "val-b"], "env")

        monkeypatch.setattr(
            pw, "build_semantic_config_with_multi_validators_key", fake_build)
        plan = web_app._maybe_semantic_run({
            "egress_policy": "metadata_only",
            "validator_models": json.dumps(["val-model-a", "val-model-b"]),
        }, store)

        assert plan == ("config", "generator", ["val-a", "val-b"], "env")
        assert seen == {
            "base_url": "https://openrouter.ai/api/v1",
            "api_key": "saved-key",
            "generator_model": "generator",
            "validator_models": ["val-model-a", "val-model-b"],
            "egress_policy": "redacted_evidence",
        }

    def test_request_single_entry_validator_models_uses_singular_builder(
            self, tmp_path, monkeypatch):
        # A validator_models list with exactly one entry must behave
        # identically to today's single-validator_model field: it routes
        # through the singular builder, not the plural one.
        from verity.web import app as web_app

        credentials = _MemoryCredentials()
        store = ProviderSettingsStore(
            ProviderPreferenceStore(tmp_path), credentials)
        store.save(ProviderPreferences(
            base_url="https://openrouter.ai/api/v1",
            generator_model="generator",
            validator_model="validator-fallback",
        ), api_key="saved-key")
        seen = {}

        def fake_build(**kwargs):
            seen.update(kwargs)
            return ("config", "generator", "validator", "env")

        monkeypatch.setattr(
            pw, "build_semantic_config_with_ephemeral_key", fake_build)
        plan = web_app._maybe_semantic_run({
            "egress_policy": "metadata_only",
            "validator_models": json.dumps(["only-model"]),
        }, store)

        assert plan == ("config", "generator", "validator", "env")
        assert seen["validator_model"] == "only-model"

    def test_request_too_many_validator_models_is_rejected(
            self, tmp_path):
        from starlette.responses import JSONResponse
        from verity.web import app as web_app

        credentials = _MemoryCredentials()
        store = ProviderSettingsStore(
            ProviderPreferenceStore(tmp_path), credentials)
        store.save(ProviderPreferences(
            base_url="https://openrouter.ai/api/v1",
            generator_model="generator",
            validator_model="validator",
        ), api_key="saved-key")

        plan = web_app._maybe_semantic_run({
            "egress_policy": "metadata_only",
            "validator_models": json.dumps(["v1", "v2", "v3", "v4"]),
        }, store)

        assert isinstance(plan, JSONResponse)
        assert plan.status_code == 400
        assert json.loads(plan.body)["error"]["code"] == "bad_model"

    def test_request_url_cannot_reuse_key_saved_for_another_provider(
            self, tmp_path):
        from starlette.responses import JSONResponse
        from verity.web import app as web_app

        credentials = _MemoryCredentials()
        store = ProviderSettingsStore(
            ProviderPreferenceStore(tmp_path), credentials)
        store.save(ProviderPreferences(
            base_url="https://saved.example",
            generator_model="saved-g",
            validator_model="saved-v",
        ), api_key="saved-key")

        plan = web_app._maybe_semantic_run({
            "semantic_enabled": True,
            "provider_base_url": "https://other.example",
            "generator_model": "other-g",
            "validator_model": "other-v",
        }, store)

        assert isinstance(plan, JSONResponse)
        assert plan.status_code == 400
        assert json.loads(plan.body)["error"]["code"] == "api_key_required"
