"""Web-side, trusted Provider configuration surface for the semantic path.

This module intentionally lives in the Web layer. It lets a local, loopback
user paste an OpenAI-compatible (e.g. OpenRouter) base URL + API key, list the
available models, and pick a generator model plus one or more (up to
``_MAX_VALIDATOR_MODELS``) validator models for an EXPERIMENTAL semantic
review. When more than one validator model is picked, every candidate is
judged by all of them and the outcome is decided by majority vote (see
``SemanticOrchestrator.run``'s ``validators=`` parameter); configuring a
single validator model keeps today's exact single-vote behaviour.

Hard safety rules enforced here:

- The user's API key is accepted only over the already-loopback-guarded Web
  request. It is placed into a *random, per-call* environment-variable name so
  the existing audited "credentials = env-var NAME, resolved at call time"
  provider path is reused unchanged. The env var is deleted in a ``finally``.
  Every additional validator provider built for the multi-vote feature shares
  this SAME transient env var — they are all bound to the one base_url +
  api_key the user configured, and the key is still never held anywhere else.
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
_SEMANTIC_MAX_OUTPUT_TOKENS = 800
# Recommended range for the multi-validator vote feature is 2-3 models
# (see AGENTS.md); this is the hard cap enforced here.
_MAX_VALIDATOR_MODELS = 3


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


def _build_semantic_config_with_ephemeral_key_impl(
        *, base_url: str, api_key: str,
        generator_model: str, validator_models: List[str],
        egress_policy: str) -> Tuple[Any, Any, List[Any], str]:
    """Shared builder: (SemanticConfig, generator_provider, [validator_provider,
    ...], env_name). ``validator_models`` must have 1-``_MAX_VALIDATOR_MODELS``
    entries; every validator provider votes independently on every candidate
    (see ``SemanticOrchestrator.run``'s ``validators=`` majority vote) and all
    of them share the SAME ephemeral key env var, since they are all bound to
    the one base_url + api_key the user configured.

    The key is stored ONLY in a random transient environment variable whose
    NAME is placed on ProviderCredentials. The caller MUST call
    ``clear_ephemeral_key(env_name)`` in a ``finally`` block after the review.
    """
    from ..semantic import (ProviderConfig, ProviderCredentials,
                            SemanticConfig)
    from ..semantic.eval_provider import (
        EvalRunBudget,
        OpenAICompatibleEvalProvider,
    )

    url = validate_base_url(base_url)
    if not isinstance(api_key, str) or not api_key.strip():
        raise ProviderWebError("api_key_required", "api_key is required")
    if len(api_key.encode("utf-8")) > _MAX_KEY_BYTES:
        raise ProviderWebError("api_key_too_large", "api_key is too large")
    if (not isinstance(validator_models, list) or not validator_models
            or len(validator_models) > _MAX_VALIDATOR_MODELS):
        raise ProviderWebError(
            "bad_model",
            f"validator_models must have 1-{_MAX_VALIDATOR_MODELS} entries")
    for label, model in (
        [("generator", generator_model)]
        + [("validator", m) for m in validator_models]
    ):
        if not isinstance(model, str) or not model.strip() or len(model) > 200:
            raise ProviderWebError("bad_model",
                                   f"{label}_model is required and must be <=200 chars")

    # Random, unguessable, valid env-var name; holds the key transiently.
    # Shared across every provider built here (generator + all validators):
    # they are all bound to the one base_url + api_key the user configured.
    env_name = "VERITY_WEB_KEY_" + secrets.token_hex(16).upper()
    os.environ[env_name] = api_key.strip()
    try:
        gen_cfg = ProviderConfig(
            role="candidate_generator", provider_id="json_http",
            model_id=generator_model.strip(), base_url=url,
            credentials=ProviderCredentials(api_key_env=env_name))
        val_cfgs = [
            ProviderConfig(
                role="validator", provider_id="json_http",
                model_id=model.strip(), base_url=url,
                credentials=ProviderCredentials(api_key_env=env_name))
            for model in validator_models
        ]
        sem_cfg = SemanticConfig(
            enabled=True, egress_policy=egress_policy,
            provider_config={"candidate_generator": gen_cfg,
                             # Product orchestrator reads validators= (the
                             # full list, below); this single entry only
                             # keeps has_provider("validator") true for
                             # callers/tests that check mere presence.
                             "validator": val_cfgs[0]})
        # OpenAI-compatible (OpenRouter etc.) speaks /chat/completions, which
        # is what the audited eval adapter uses. Distinct role-bound objects
        # get distinct attempt budgets so retries cannot borrow from the other
        # role's semantic allowance. Every validator provider — even when
        # voting alongside others — gets the SAME per-provider budget shape
        # the single-validator path always used; the orchestrator's own
        # ``max_total_validation_calls`` counter is the real shared cap
        # across the whole voting pool.
        generator = OpenAICompatibleEvalProvider(
            config=gen_cfg,
            max_output_tokens=_SEMANTIC_MAX_OUTPUT_TOKENS,
            run_budget=EvalRunBudget(
                max_calls=sem_cfg.budget.max_candidate_generation_calls,
                max_total_tokens=(
                    sem_cfg.budget.max_candidate_generation_calls
                    * (gen_cfg.max_request_bytes + 1024
                       + _SEMANTIC_MAX_OUTPUT_TOKENS)
                ),
                max_spend_usd=0.0,
            ),
        )
        validators = [
            OpenAICompatibleEvalProvider(
                config=val_cfg,
                max_output_tokens=_SEMANTIC_MAX_OUTPUT_TOKENS,
                run_budget=EvalRunBudget(
                    max_calls=sem_cfg.budget.max_total_validation_calls,
                    max_total_tokens=(
                        sem_cfg.budget.max_total_validation_calls
                        * (val_cfg.max_request_bytes + 1024
                           + _SEMANTIC_MAX_OUTPUT_TOKENS)
                    ),
                    max_spend_usd=0.0,
                ),
            )
            for val_cfg in val_cfgs
        ]
    except ValueError as exc:
        clear_ephemeral_key(env_name)
        raise ProviderWebError("bad_semantic_config", str(exc))
    except Exception:
        clear_ephemeral_key(env_name)
        raise
    return sem_cfg, generator, validators, env_name


def build_semantic_config_with_ephemeral_key(
        *, base_url: str, api_key: str,
        generator_model: str, validator_model: str,
        egress_policy: str) -> Tuple[Any, Any, Any, str]:
    """Build (SemanticConfig, generator_provider, validator_provider, env_name).

    Single-validator path, unchanged in shape and behaviour: returns exactly
    ONE validator provider object (not a list), matching every existing
    caller/test. See :func:`build_semantic_config_with_multi_validators_key`
    for the sibling that builds 2-3 independently-voting validator providers.
    """
    sem_cfg, generator, validators, env_name = (
        _build_semantic_config_with_ephemeral_key_impl(
            base_url=base_url, api_key=api_key,
            generator_model=generator_model,
            validator_models=[validator_model],
            egress_policy=egress_policy))
    return sem_cfg, generator, validators[0], env_name


def build_semantic_config_with_multi_validators_key(
        *, base_url: str, api_key: str,
        generator_model: str, validator_models: List[str],
        egress_policy: str) -> Tuple[Any, Any, List[Any], str]:
    """Sibling of :func:`build_semantic_config_with_ephemeral_key` for the
    multi-validator vote feature: same ephemeral-key discipline, but returns
    a LIST of 2-``_MAX_VALIDATOR_MODELS`` independently-voting validator
    providers (pass as ``validators=`` to ``run_review``) instead of one.
    """
    if not isinstance(validator_models, list) or len(validator_models) < 2:
        raise ProviderWebError(
            "bad_model", "validator_models must have at least 2 entries; "
                        "use build_semantic_config_with_ephemeral_key for one")
    return _build_semantic_config_with_ephemeral_key_impl(
        base_url=base_url, api_key=api_key,
        generator_model=generator_model,
        validator_models=validator_models,
        egress_policy=egress_policy)


def clear_ephemeral_key(env_name: Optional[str]) -> None:
    """Remove the transient key env var. Safe to call multiple times."""
    if env_name:
        os.environ.pop(env_name, None)
