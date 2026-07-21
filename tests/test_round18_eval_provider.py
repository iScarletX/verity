"""Round 18 eval-only OpenAI-compatible Provider containment."""
import json
import socket
import urllib.error
from io import BytesIO

import pytest

from verity.semantic.config import ProviderConfig, ProviderCredentials
from verity.semantic.eval_provider import OpenAICompatibleEvalProvider
from verity.semantic.provider import ProviderCall


class Response:
    def __init__(self, body=b"{}", status=200):
        self.body = BytesIO(body); self.status = status
    def read(self, n=-1): return self.body.read(n)
    def getcode(self): return self.status
    def __enter__(self): return self
    def __exit__(self, *_): return False


class Opener:
    def __init__(self, response=None, error=None):
        self.response = response or Response(); self.error = error; self.requests = []
    def open(self, request, timeout):
        self.requests.append((request, timeout))
        if self.error: raise self.error
        return self.response


def cfg(role="candidate_generator", **updates):
    values = dict(role=role, provider_id="eval-stub", model_id="pinned-model",
                  base_url="https://eval.example/v1",
                  credentials=ProviderCredentials("VERITY_TEST_EVAL_KEY"),
                  max_request_bytes=64 * 1024, max_response_bytes=16 * 1024)
    values.update(updates)
    return ProviderConfig(**values)


def call(role="candidate_generator"):
    return ProviderCall("review", "redacted_evidence", role, "call-1", 1, "0" * 64)


def envelope(payload):
    return json.dumps({"choices": [{"message": {"content": json.dumps(payload)}}]}).encode()


def test_eval_provider_sends_closed_no_tool_json_request(monkeypatch):
    secret = "VERITY_SYNTHETIC_TEST_SECRET"
    monkeypatch.setenv("VERITY_TEST_EVAL_KEY", secret)
    opener = Opener(Response(envelope({"candidates": []})))
    provider = OpenAICompatibleEvalProvider(cfg(), opener=opener,
                                            temperature=0, max_output_tokens=300)
    response = provider.generate_candidates(call=call(), request={
        "findingType": "semantic.prompt.instruction_conflict", "evidence": []})
    assert response.ok and response.payload == {"candidates": []}
    request, timeout = opener.requests[0]
    assert request.full_url == "https://eval.example/v1/chat/completions"
    assert request.get_header("Authorization") == "Bearer " + secret
    assert secret.encode() not in request.data
    wire = json.loads(request.data)
    assert wire["stream"] is False
    assert wire["response_format"] == {"type": "json_object"}
    assert "tools" not in wire and "functions" not in wire
    assert [m["role"] for m in wire["messages"]] == ["system", "user"]
    assert "untrusted data" in wire["messages"][0]["content"]
    assert timeout == cfg().timeout_seconds


def test_eval_provider_requires_named_present_credential(monkeypatch):
    monkeypatch.delenv("VERITY_TEST_EVAL_KEY", raising=False)
    opener = Opener()
    provider = OpenAICompatibleEvalProvider(cfg(), opener=opener)
    out = provider.generate_candidates(call=call(), request={})
    assert not out.ok and out.reason_code == "credential_missing"
    assert opener.requests == []
    no_name = ProviderConfig(role="candidate_generator", provider_id="x",
                             model_id="m", base_url="https://eval.example/v1")
    out = OpenAICompatibleEvalProvider(no_name, opener=opener).generate_candidates(
        call=call(), request={})
    assert not out.ok and out.reason_code == "credential_missing"


@pytest.mark.parametrize("raw", [
    b"not-json",
    b'{"choices":[],"choices":[]}',
    json.dumps({"choices": [{"message": {"content": "[]"}}]}).encode(),
    json.dumps({"choices": [{"message": {"content": '{"candidates":[],"candidates":[]}'}}]}).encode(),
])
def test_eval_provider_rejects_ambiguous_or_non_object_json(monkeypatch, raw):
    monkeypatch.setenv("VERITY_TEST_EVAL_KEY", "x")
    out = OpenAICompatibleEvalProvider(cfg(), opener=Opener(Response(raw))).generate_candidates(
        call=call(), request={})
    assert not out.ok and out.reason_code == "invalid_json"


def test_eval_provider_refuses_redirect_timeout_and_oversize(monkeypatch):
    monkeypatch.setenv("VERITY_TEST_EVAL_KEY", "x")
    redirect = urllib.error.HTTPError("https://eval.example", 302, "Found", {}, None)
    p = OpenAICompatibleEvalProvider(cfg(), opener=Opener(error=redirect))
    assert p.generate_candidates(call=call(), request={}).reason_code == "redirect_refused"
    p = OpenAICompatibleEvalProvider(cfg(), opener=Opener(error=socket.timeout()))
    assert p.generate_candidates(call=call(), request={}).reason_code == "provider_timeout"
    small = cfg(max_response_bytes=1024)
    p = OpenAICompatibleEvalProvider(small, opener=Opener(Response(b"x" * 1025)))
    assert p.generate_candidates(call=call(), request={}).reason_code == "response_too_large"


def test_role_and_parameter_bounds(monkeypatch):
    monkeypatch.setenv("VERITY_TEST_EVAL_KEY", "x")
    val = OpenAICompatibleEvalProvider(cfg(role="validator"),
                                       opener=Opener(Response(envelope({}))))
    assert val.generate_candidates(call=call(), request={}).reason_code == "provider_role_mismatch"
    assert val.validate_candidate(call=call("validator"), request={}).ok
    with pytest.raises(ValueError): OpenAICompatibleEvalProvider(cfg(), temperature=1.1)
    with pytest.raises(ValueError): OpenAICompatibleEvalProvider(cfg(), max_output_tokens=10)
