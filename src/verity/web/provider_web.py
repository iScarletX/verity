"""Web-side, trusted Provider configuration surface for the semantic path.

This module intentionally lives in the Web layer. It lets a local, loopback
user paste an OpenAI-compatible (e.g. OpenRouter) base URL + API key, list the
available models, and pick a generator/validator model for an EXPERIMENTAL
semantic review.

Hard safety rules enforced here:

- The user's API key is accepted only over the already-loopback-guarded Web
  request. It is placed into a *random, per-call* environment-variable name so
  the existing audited "credentials = env-var NAME, resolved at call time"
  provider path is reused unchanged. The env var is deleted in a ``finally``.
- The key value never enters: SemanticConfig, ProviderConfig, any report,
  SARIF, the payload audit, logs, or an HTTP response body. Only the env-var
  NAME is ever held by config objects, and that name is random and transient.
- The model-list call is a bounded GET to ``<base_url>/models`` with the same
  https-or-loopback rule the provider transport enforces. Response is size- and
  shape-capped; provider error bodies are reduced to a code.
- This surface configures ONLY the experimental semantic axis. It cannot change
  the deterministic pipeline, coverage, gate or score.
"""
from __future__ import annotations

import json
import os
import secrets
import ssl
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit


# Bounds for the model-listing call.
_MODELS_TIMEOUT_SECONDS = 20.0
_MODELS_MAX_RESPONSE_BYTES = 4 * 1024 * 1024   # 4 MiB
_MAX_MODELS_RETURNED = 2000
_MAX_KEY_BYTES = 8 * 1024
_MAX_BASE_URL_LEN = 300


class ProviderWebError(Exception):
    """Carries a stable machine code for a JSON error envelope."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def validate_base_url(base_url: str) -> str:
    """Return a normalized base URL or raise ProviderWebError.

    Same rule the provider transport enforces: https:// or loopback http,
    no credentials/query/fragment, hostname required.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        raise ProviderWebError("bad_base_url", "base_url is required")
    u = base_url.strip()
    if len(u) > _MAX_BASE_URL_LEN:
        raise ProviderWebError("bad_base_url", "base_url is too long")
    parsed = urlsplit(u)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.hostname):
        raise ProviderWebError(
            "bad_base_url",
            "base_url must not contain credentials, query, or fragment")
    if parsed.scheme == "https":
        pass
    elif parsed.scheme == "http" and parsed.hostname in {
            "127.0.0.1", "localhost", "::1"}:
        pass
    else:
        raise ProviderWebError(
            "bad_base_url", "base_url must be https:// or a loopback http URL")
    return u.rstrip("/")


def _models_url(base_url: str) -> str:
    return validate_base_url(base_url) + "/models"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderWebError("provider_redirect_refused",
                               "provider attempted a redirect; refused")


def list_models(base_url: str, api_key: str) -> List[Dict[str, str]]:
    """Fetch the model list from an OpenAI-compatible ``/models`` endpoint.

    Returns a list of ``{"id": ..., "name": ...}`` dicts. The API key is used
    only for this outbound request and is never returned or stored.
    """
    url = validate_base_url(base_url) + "/models"
    if not isinstance(api_key, str) or not api_key.strip():
        raise ProviderWebError("api_key_required", "api_key is required")
    if len(api_key.encode("utf-8")) > _MAX_KEY_BYTES:
        raise ProviderWebError("api_key_too_large", "api_key is too large")

    req = urllib.request.Request(url, method="GET")
    req.add_header("Authorization", "Bearer " + api_key.strip())
    req.add_header("Accept", "application/json")

    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=ctx))
    try:
        with opener.open(req, timeout=_MODELS_TIMEOUT_SECONDS) as resp:
            raw = resp.read(_MODELS_MAX_RESPONSE_BYTES + 1)
    except ProviderWebError:
        raise
    except urllib.error.HTTPError as exc:
        # Do not reflect the provider's error body into our response.
        raise ProviderWebError("provider_http_error",
                               f"provider returned HTTP {exc.code}")
    except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError):
        raise ProviderWebError("provider_unreachable",
                               "could not reach the provider")
    if len(raw) > _MODELS_MAX_RESPONSE_BYTES:
        raise ProviderWebError("provider_response_too_large",
                               "provider response too large")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ProviderWebError("provider_bad_json",
                               "provider returned invalid JSON")
    data = parsed.get("data") if isinstance(parsed, dict) else None
    if not isinstance(data, list):
        raise ProviderWebError("provider_bad_shape",
                               "provider model list has unexpected shape")
    out: List[Dict[str, str]] = []
    for m in data[:_MAX_MODELS_RETURNED]:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not isinstance(mid, str) or not mid:
            continue
        name = m.get("name")
        out.append({"id": mid[:200],
                    "name": (name[:200] if isinstance(name, str) and name else mid[:200])})
    if not out:
        raise ProviderWebError("provider_no_models",
                               "provider returned no usable models")
    out.sort(key=lambda x: x["id"])
    return out


def build_semantic_config_with_ephemeral_key(
        *, base_url: str, api_key: str,
        generator_model: str, validator_model: str,
        egress_policy: str) -> Tuple[Any, Any, Any, str]:
    """Build (SemanticConfig, generator_provider, validator_provider, env_name).

    The key is stored ONLY in a random transient environment variable whose
    NAME is placed on ProviderCredentials. The caller MUST call
    ``clear_ephemeral_key(env_name)`` in a ``finally`` block after the review.
    """
    from ..semantic import (ProviderConfig, ProviderCredentials,
                            SemanticConfig)
    from ..semantic.eval_provider import OpenAICompatibleEvalProvider

    url = validate_base_url(base_url)
    if not isinstance(api_key, str) or not api_key.strip():
        raise ProviderWebError("api_key_required", "api_key is required")
    if len(api_key.encode("utf-8")) > _MAX_KEY_BYTES:
        raise ProviderWebError("api_key_too_large", "api_key is too large")
    for label, model in (("generator", generator_model),
                         ("validator", validator_model)):
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ProviderWebError("bad_model",
                                   f"{label}_model is required and must be <=200 chars")

    # Random, unguessable, valid env-var name; holds the key transiently.
    env_name = "VERITY_WEB_KEY_" + secrets.token_hex(16).upper()
    os.environ[env_name] = api_key.strip()
    try:
        gen_cfg = ProviderConfig(
            role="candidate_generator", provider_id="json_http",
            model_id=generator_model.strip(), base_url=url,
            credentials=ProviderCredentials(api_key_env=env_name))
        val_cfg = ProviderConfig(
            role="validator", provider_id="json_http",
            model_id=validator_model.strip(), base_url=url,
            credentials=ProviderCredentials(api_key_env=env_name))
        sem_cfg = SemanticConfig(
            enabled=True, egress_policy=egress_policy,
            provider_config={"candidate_generator": gen_cfg,
                             "validator": val_cfg})
        # OpenAI-compatible (OpenRouter etc.) speaks /chat/completions, which
        # is what the audited eval adapter uses. Distinct role-bound objects.
        generator = OpenAICompatibleEvalProvider(config=gen_cfg)
        validator = OpenAICompatibleEvalProvider(config=val_cfg)
    except ValueError as exc:
        clear_ephemeral_key(env_name)
        raise ProviderWebError("bad_semantic_config", str(exc))
    except Exception:
        clear_ephemeral_key(env_name)
        raise
    return sem_cfg, generator, validator, env_name


def clear_ephemeral_key(env_name: Optional[str]) -> None:
    """Remove the transient key env var. Safe to call multiple times."""
    if env_name:
        os.environ.pop(env_name, None)
