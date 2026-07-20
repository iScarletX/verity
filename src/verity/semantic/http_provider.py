"""Bounded real HTTP Provider for controlled semantic review.

The wire contract is intentionally small and explicit instead of claiming
compatibility with every vendor SDK:

POST ``<base_url>/v1/verity/candidate-generator`` or
``<base_url>/v1/verity/validator`` with a JSON body containing
``model``, ``role`` and Verity's already-sanitized ``input`` object.
The response body is the strict candidate/validation JSON object consumed
by :mod:`verity.semantic.orchestrator`.

Security properties:
- trusted config only; never derived from the reviewed artifact;
- HTTPS for remote hosts, loopback HTTP allowed for local/test Providers;
- redirects disabled;
- API key resolved from an environment-variable *name* at call time;
- bounded request/response bytes and timeout;
- error bodies are discarded, never reflected into reports or logs;
- no retries, streaming, tools, arbitrary headers, or endpoint discovery.
"""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .config import ProviderConfig
from .provider import ProviderCall, ProviderResponse


_PATHS = {
    "candidate_generator": "/v1/verity/candidate-generator",
    "validator": "/v1/verity/validator",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


@dataclass
class _JsonHttpTransport:
    """Shared bounded transport; concrete role classes expose one protocol."""

    config: ProviderConfig
    opener: Optional[Any] = None

    def __post_init__(self) -> None:
        if not self.config.base_url:
            raise ValueError("real HTTP Provider requires base_url")
        if self.opener is None:
            self.opener = urllib.request.build_opener(
                _NoRedirect(),
                urllib.request.HTTPSHandler(context=ssl.create_default_context()),
            )

    def _call(self, *, call: ProviderCall,
              request: Dict[str, Any]) -> ProviderResponse:
        if call.call_role != self.config.role:
            return ProviderResponse(ok=False, reason_code="provider_role_mismatch")

        key = self.config.credentials.resolve()
        if self.config.credentials.api_key_env and not key:
            return ProviderResponse(ok=False, reason_code="credential_missing")

        wire = {
            "model": self.config.model_id,
            "role": self.config.role,
            "input": request,
        }
        body = json.dumps(
            wire, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(body) > self.config.max_request_bytes:
            return ProviderResponse(ok=False, reason_code="request_too_large")

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Verity-Semantic/1",
            "X-Verity-Call-Id": call.call_id,
        }
        if key:
            headers["Authorization"] = "Bearer " + key

        req = urllib.request.Request(
            self.config.base_url + _PATHS[self.config.role],
            data=body,
            headers=headers,
            method="POST",
        )
        try:
            with self.opener.open(req, timeout=self.config.timeout_seconds) as resp:
                status = int(getattr(resp, "status", resp.getcode()))
                if status < 200 or status >= 300:
                    return ProviderResponse(ok=False, reason_code="http_error")
                raw = resp.read(self.config.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            # Redirects arrive here too because _NoRedirect returns None.
            if 300 <= exc.code < 400:
                return ProviderResponse(ok=False, reason_code="redirect_refused")
            return ProviderResponse(ok=False, reason_code="http_error")
        except (TimeoutError, socket.timeout):
            return ProviderResponse(ok=False, reason_code="provider_timeout")
        except (urllib.error.URLError, ssl.SSLError, OSError):
            return ProviderResponse(ok=False, reason_code="network_error")

        if len(raw) > self.config.max_response_bytes:
            return ProviderResponse(
                ok=False, response_bytes=len(raw),
                reason_code="response_too_large",
            )
        try:
            payload = json.loads(
                raw.decode("utf-8"),
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError("non-finite JSON number")),
                object_pairs_hook=_object_without_duplicate_keys,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return ProviderResponse(
                ok=False, response_bytes=len(raw), reason_code="invalid_json"
            )
        if not isinstance(payload, dict):
            return ProviderResponse(
                ok=False, response_bytes=len(raw),
                reason_code="invalid_json_shape",
            )
        return ProviderResponse(ok=True, payload=payload,
                                response_bytes=len(raw))


def _object_without_duplicate_keys(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate JSON key")
        out[key] = value
    return out


class JsonCandidateGeneratorProvider(_JsonHttpTransport):
    """Real Provider implementing only CandidateGeneratorProvider."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.config.role != "candidate_generator":
            raise ValueError("candidate provider requires candidate_generator config")

    def generate_candidates(self, *, call: ProviderCall,
                            request: Dict[str, Any]) -> ProviderResponse:
        return self._call(call=call, request=request)


class JsonValidatorProvider(_JsonHttpTransport):
    """Real Provider implementing only ValidatorProvider."""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.config.role != "validator":
            raise ValueError("validator provider requires validator config")

    def validate_candidate(self, *, call: ProviderCall,
                           request: Dict[str, Any]) -> ProviderResponse:
        return self._call(call=call, request=request)
