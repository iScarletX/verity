"""Round 11: controlled real JSON-over-HTTP semantic Provider."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

import pytest

from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.config import (ProviderConfig, ProviderCredentials,
                                    SemanticConfig)
from verity.semantic.http_provider import (JsonCandidateGeneratorProvider,
                                           JsonValidatorProvider)
from verity.semantic.provider import ProviderCall


class _Response:
    def __init__(self, body=b"{}", status=200):
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


class _Opener:
    def __init__(self, response=None, error=None):
        self.response = response or _Response()
        self.error = error
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error:
            raise self.error
        return self.response


def _config(role="candidate_generator", **kwargs):
    values = dict(
        role=role,
        provider_id="test-json",
        model_id="test-model",
        base_url="https://provider.example",
        credentials=ProviderCredentials(),
    )
    values.update(kwargs)
    return ProviderConfig(**values)


def _call(role="candidate_generator"):
    return ProviderCall(
        review_id="r-test", egress_policy="metadata_only",
        call_role=role, call_id="call-1", request_bytes=10,
        request_digest_sha256="0" * 64,
    )


class TestProviderConfig:
    @pytest.mark.parametrize("url", [
        "http://provider.example", "file:///etc/passwd",
        "https://user:pass@provider.example", "https://provider.example?q=x",
        "https://provider.example/#fragment",
    ])
    def test_rejects_untrusted_url_shapes(self, url):
        with pytest.raises(ValueError):
            _config(base_url=url)

    @pytest.mark.parametrize("url", [
        "https://provider.example", "http://127.0.0.1:8080",
        "http://localhost:8080", "http://[::1]:8080",
    ])
    def test_allows_https_or_loopback_http(self, url):
        assert _config(base_url=url).base_url == url

    def test_rejects_invalid_credential_environment_name(self):
        with pytest.raises(ValueError):
            ProviderCredentials(api_key_env="KEY=value")


class TestJsonHttpProvider:
    def test_sends_bounded_json_and_environment_credential(self, monkeypatch):
        secret = "VERITY_TEST_ONLY_PROVIDER_VALUE"
        monkeypatch.setenv("VERITY_TEST_PROVIDER_KEY", secret)
        opener = _Opener(_Response(json.dumps({"candidates": []}).encode()))
        cfg = _config(credentials=ProviderCredentials(
            api_key_env="VERITY_TEST_PROVIDER_KEY"))
        provider = JsonCandidateGeneratorProvider(cfg, opener=opener)

        result = provider.generate_candidates(
            call=_call(), request={"evidence": [], "instruction": "safe"})

        assert result.ok
        request, timeout = opener.requests[0]
        assert request.full_url.endswith("/v1/verity/candidate-generator")
        assert request.get_header("Authorization") == "Bearer " + secret
        wire = json.loads(request.data)
        assert wire["role"] == "candidate_generator"
        assert wire["model"] == "test-model"
        assert wire["input"]["instruction"] == "safe"
        # Secret is transport metadata only, never serialized in JSON body.
        assert secret.encode() not in request.data

    def test_missing_named_credential_is_visible_but_not_leaked(self, monkeypatch):
        monkeypatch.delenv("VERITY_TEST_PROVIDER_KEY", raising=False)
        provider = JsonCandidateGeneratorProvider(
            _config(credentials=ProviderCredentials(
                api_key_env="VERITY_TEST_PROVIDER_KEY")), opener=_Opener())
        result = provider.generate_candidates(call=_call(), request={})
        assert not result.ok
        assert result.reason_code == "credential_missing"

    def test_role_mismatch_is_refused_before_network(self):
        opener = _Opener()
        with pytest.raises(ValueError):
            JsonCandidateGeneratorProvider(
                _config(role="validator"), opener=opener)
        with pytest.raises(ValueError):
            JsonValidatorProvider(_config(), opener=opener)
        assert opener.requests == []

    def test_request_cap_is_enforced(self):
        opener = _Opener()
        provider = JsonCandidateGeneratorProvider(
            _config(max_request_bytes=1024), opener=opener)
        result = provider.generate_candidates(
            call=_call(), request={"text": "x" * 5000})
        assert not result.ok
        assert result.reason_code == "request_too_large"
        assert opener.requests == []

    def test_response_cap_is_enforced(self):
        opener = _Opener(_Response(b"{" + b"x" * 5000))
        provider = JsonCandidateGeneratorProvider(
            _config(max_response_bytes=1024), opener=opener)
        result = provider.generate_candidates(call=_call(), request={})
        assert not result.ok
        assert result.reason_code == "response_too_large"

    @pytest.mark.parametrize("raw", [
        b"not-json",
        b'{"candidates":[],"candidates":[]}',
        b'{"value":NaN}',
    ])
    def test_invalid_or_ambiguous_json_is_controlled(self, raw):
        provider = JsonCandidateGeneratorProvider(
            _config(), opener=_Opener(_Response(raw)))
        result = provider.generate_candidates(call=_call(), request={})
        assert not result.ok
        assert result.reason_code == "invalid_json"

    def test_non_object_json_root_is_refused(self):
        provider = JsonCandidateGeneratorProvider(
            _config(), opener=_Opener(_Response(b"[]")))
        result = provider.generate_candidates(call=_call(), request={})
        assert not result.ok
        assert result.reason_code == "invalid_json_shape"

    def test_timeout_is_controlled(self):
        provider = JsonCandidateGeneratorProvider(
            _config(), opener=_Opener(error=socket.timeout("slow")))
        result = provider.generate_candidates(call=_call(), request={})
        assert not result.ok
        assert result.reason_code == "provider_timeout"

    def test_http_error_body_is_not_reflected(self):
        error = urllib.error.HTTPError(
            "https://provider.example", 500, "server exploded SECRET", {},
            BytesIO(b"reflected-user-content"),
        )
        provider = JsonCandidateGeneratorProvider(
            _config(), opener=_Opener(error=error))
        result = provider.generate_candidates(call=_call(), request={})
        assert not result.ok
        assert result.reason_code == "http_error"
        assert "SECRET" not in result.reason_code
        assert "reflected" not in result.reason_code

    def test_redirect_is_refused(self):
        error = urllib.error.HTTPError(
            "https://provider.example", 302, "Found",
            {"Location": "https://other.example"}, None,
        )
        provider = JsonCandidateGeneratorProvider(
            _config(), opener=_Opener(error=error))
        result = provider.generate_candidates(call=_call(), request={})
        assert not result.ok
        assert result.reason_code == "redirect_refused"

    def test_transport_failure_marks_semantic_axis_failed(self):
        gen_cfg = _config()
        val_cfg = _config(role="validator")
        cfg = SemanticConfig(
            enabled=True, egress_policy="metadata_only",
            provider_config={"candidate_generator": gen_cfg,
                             "validator": val_cfg},
        )
        network_error = urllib.error.URLError("deliberately offline")
        gen = JsonCandidateGeneratorProvider(
            gen_cfg, opener=_Opener(error=network_error))
        val = JsonValidatorProvider(val_cfg, opener=_Opener())
        snap, file_bytes = intake_text("Return JSON.\nAlso answer in prose.")
        review = run_review(
            ReviewInputs(engine="prompt", snapshot=snap,
                         file_bytes=file_bytes, semantic_config=cfg),
            candidate_generator=gen, validator=val,
        )
        report = review_to_dict(review)
        assert review.semantic["status"] == "failed"
        assert review.semantic["reasonCode"] == "network_error"
        assert report["capabilities"]["semantic"]["status"] == "failed"
        # Deterministic coverage remains independent and sufficient.
        assert report["coverage"]["status"] == "sufficient"


class _SemanticHandler(BaseHTTPRequestHandler):
    seen_paths = []
    seen_authorization = []

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        wire = json.loads(self.rfile.read(size))
        self.__class__.seen_paths.append(self.path)
        self.__class__.seen_authorization.append(
            self.headers.get("Authorization", ""))
        inp = wire["input"]
        if self.path.endswith("candidate-generator"):
            evidence = inp.get("evidence") or []
            if inp["findingType"] == "semantic.prompt.instruction_conflict" and len(evidence) >= 2:
                subject = {"conflictKind": "contradictory_directive"}
                evidence_ids = [evidence[0]["evidenceId"], evidence[1]["evidenceId"]]
            elif inp["findingType"] == "semantic.prompt.missing_output_contract" and evidence:
                subject = {"expectedFormat": "json"}
                evidence_ids = [evidence[0]["evidenceId"]]
            else:
                subject = None
                evidence_ids = []
            payload = {"candidates": []}
            if subject:
                payload["candidates"].append({
                    "proposedCandidateId": "remote-proposal",
                    "findingType": inp["findingType"],
                    "subject": subject,
                    "claim": "Controlled semantic candidate.",
                    "evidenceIds": evidence_ids,
                })
        else:
            payload = {
                "candidateId": inp["candidate"]["candidateId"],
                "decision": "confirmed",
                "reasonCodes": ["evidence_supports_claim"],
            }
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *_args):
        pass


def test_cli_real_provider_end_to_end_without_public_network(tmp_path):
    """Full CLI → two role endpoints → strict semantic Finding path."""
    _SemanticHandler.seen_paths = []
    _SemanticHandler.seen_authorization = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SemanticHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    secret = "VERITY_TEST_ONLY_E2E_PROVIDER_VALUE"
    try:
        root = Path(__file__).parent.parent
        base = f"http://127.0.0.1:{server.server_port}"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(root / "src")
        env["VERITY_TEST_PROVIDER_KEY"] = secret
        proc = subprocess.run([
            sys.executable, "-m", "verity.cli", "review",
            "--engine", "prompt", "--semantic",
            "--egress-policy", "redacted_evidence",
            "--semantic-generator-url", base,
            "--semantic-generator-model", "generator-test",
            "--semantic-generator-api-key-env", "VERITY_TEST_PROVIDER_KEY",
            "--semantic-validator-url", base,
            "--semantic-validator-model", "validator-test",
            "--semantic-validator-api-key-env", "VERITY_TEST_PROVIDER_KEY",
            "--text", "Return only JSON.\nAlso answer in prose, never JSON.",
            "--out", str(tmp_path),
        ], cwd=root, env=env, capture_output=True, text=True, timeout=30)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert proc.returncode == 0, proc.stderr
    assert "semantic=completed" in proc.stdout
    assert "gate=pass" in proc.stdout
    report_text = (tmp_path / "report.json").read_text()
    report = json.loads(report_text)
    assert report["semantic"]["status"] == "completed"
    assert report["semantic"]["findings"]
    assert report["capabilities"]["semantic"]["status"] == "completed"
    assert any(p.endswith("candidate-generator") for p in _SemanticHandler.seen_paths)
    assert any(p.endswith("validator") for p in _SemanticHandler.seen_paths)
    assert set(_SemanticHandler.seen_authorization) == {"Bearer " + secret}
    assert secret not in proc.stdout
    assert secret not in proc.stderr
    assert secret not in report_text
