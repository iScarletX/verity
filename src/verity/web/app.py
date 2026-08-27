"""Starlette ASGI app for the Verity local Web MVP.

Design rules:

- Every response gets a strict CSP + a small set of hardening headers.
- Every state-changing request has its ``Host`` header restricted to
  loopback names, and (if present) the ``Origin`` header must also
  point at the loopback host. This is defence against DNS rebinding
  when Verity is left running on a laptop.
- All static assets are served from this package's ``static/`` folder;
  no CDN.
- The API surface is intentionally tiny; failures return a JSON error
  envelope with a code + user-safe message. Stack traces / internal
  reason strings never reach the client.
- Store lifetime is process-scoped and bounded (LRU).
"""

from __future__ import annotations

import io
import json
import mimetypes
import os
import secrets
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ..intake import (IntakeBudget, IntakeError, intake_directory,
                      intake_text, PROMPT_KINDS)
from ..history import HistoryError, HistoryStore
from ..models import PROMPT_KINDS as _PROMPT_KINDS  # kept for clarity
from ..report import review_to_dict, to_html as report_html, to_json as report_json
from ..review import ReviewInputs, run_review
from ..sarif import to_sarif_json
from .store import ReportStore, StoredReport
from .view import build_view_model


HERE = Path(__file__).parent
STATIC_DIR = HERE / "static"

# --- Request budgets ---------------------------------------------------

MAX_PROMPT_BYTES = 256 * 1024                # matches intake.MAX_PROMPT_BYTES
MAX_SKILL_FILES = 500
MAX_SKILL_FILE_BYTES = 512 * 1024
MAX_SKILL_TOTAL_BYTES = 8 * 1024 * 1024
MAX_REQUEST_BYTES = 12 * 1024 * 1024         # multipart wrapper overhead
MAX_WEB_EGRESS_POLICY = "redacted_evidence"
MAX_WEB_SKILL_PROFILE = "standard"
WEB_PROVIDER_FIELD_NAMES = {
    "provider_base_url",
    "provider_api_key",
    "generator_model",
    "validator_model",
}
# Separate from WEB_PROVIDER_FIELD_NAMES on purpose: this field only adds
# EXTRA validator votes on top of whichever base_url/api_key/generator_model/
# validator_model end up resolved (from the request OR the persisted
# settings store). It must never gate that resolution the way the four
# connection fields above do -- a request that supplies only
# ``validator_models`` (no explicit base_url etc.) should still fall back
# to the persisted settings store for the connection details.
WEB_VALIDATOR_MODELS_FIELD_NAME = "validator_models"
MAX_WEB_VALIDATOR_MODELS = 3

# --- V1.5 Prompt black-box / V2 Skill sandbox compatibility fields ------
#
# Deliberately NOT modelled on WEB_PROVIDER_FIELD_NAMES's "any field present
# means the caller wants this" convention. Prompt black-box makes a real
# outbound model call, so it requires two independent explicit signals. The
# sandbox fields are retained only for API compatibility: a fully confirmed
# request is passed to run_review, which fails closed before any runner import
# or construction. The reviewed artifact cannot activate either path.
WEB_BLACKBOX_FIELD_NAMES = {
    "blackbox_enabled",
    "blackbox_confirm",
    "blackbox_base_url",
    "blackbox_api_key",
    "blackbox_model",
    "blackbox_scenario_policy",
    "blackbox_scenario_ids",
    "blackbox_max_calls",
    "blackbox_timeout_seconds",
    "blackbox_max_tokens",
}
_MAX_BLACKBOX_KEY_BYTES = 8 * 1024

WEB_SANDBOX_FIELD_NAMES = {
    "sandbox_enabled",
    "sandbox_confirm",
    "sandbox_entry_point",
    "sandbox_argv",
    "sandbox_cpu_seconds",
    "sandbox_memory_mb",
    "sandbox_wall_seconds",
}


# --- Middleware --------------------------------------------------------

_ALLOWED_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


class LoopbackAndHeadersMiddleware(BaseHTTPMiddleware):
    """Reject non-loopback Host / cross-origin Origin; add hardening headers."""

    async def dispatch(self, request: Request, call_next):
        host_header = request.headers.get("host", "")
        host_only = host_header.split(":")[0].strip("[]")
        if host_only and host_only.lower() not in {h.lower() for h in _ALLOWED_HOSTS}:
            return _with_security_headers(JSONResponse({"error": {
                "code": "host_not_allowed",
                "message": "This server accepts only loopback hosts.",
            }}, status_code=421))

        origin = request.headers.get("origin")
        if origin:
            # Origin like http://127.0.0.1:8765 — hostname must be loopback.
            try:
                from urllib.parse import urlsplit
                oh = urlsplit(origin).hostname or ""
            except Exception:
                oh = ""
            if oh.lower() not in {h.lower() for h in _ALLOWED_HOSTS}:
                return _with_security_headers(JSONResponse({"error": {
                    "code": "origin_not_allowed",
                    "message": "This server accepts only loopback origins.",
                }}, status_code=403))

        # Body-size cap: if the client sent a Content-Length larger than
        # our budget, reject immediately. (Starlette's stream reader would
        # otherwise materialise the whole payload.)
        cl = request.headers.get("content-length")
        if cl:
            try:
                if int(cl) > MAX_REQUEST_BYTES:
                    return _error_response("request_too_large",
                                           "Request body exceeds server budget.",
                                           413)
            except ValueError:
                return _error_response("bad_content_length",
                                       "Invalid Content-Length header.", 400)

        response = await call_next(request)
        return _with_security_headers(response)


def _with_security_headers(response: Response) -> Response:
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        "script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "font-src 'self'; connect-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Cache-Control"] = "no-store"
    return response


# --- Helpers -----------------------------------------------------------

def _error_response(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}},
                        status_code=status)


def _make_report(review, engine: str) -> StoredReport:
    d = review_to_dict(review)
    json_text = report_json(review)
    html_text = report_html(review)
    sarif_text = to_sarif_json(d)
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in d.get("findings") or []:
        sev = f.get("severity")
        counts[sev] = counts.get(sev, 0) + 1
    return StoredReport(
        review_id="",
        engine=engine,
        verdict=d.get("verdict") or {},
        coverage=(d.get("coverage") or {}).get("status") or "unknown",
        findings_by_severity=counts,
        json_text=json_text,
        html_text=html_text,
        sarif_text=sarif_text,
        created_at=__import__("time").time(),
    )


def _view_for(review, engine: str, review_id: str) -> dict:
    d = review_to_dict(review)
    return build_view_model(d, review_id)


# ---------------- Path guards for skill upload -------------------------

_FORBIDDEN_PATH_SEGMENTS = {"", ".", ".."}


class MultipartPathError(IntakeError):
    pass


def _reject_unsafe_path(raw: str) -> None:
    """Shared zip-slip / NUL / backslash / absolute-path guard for both
    browser folder-upload paths and ZIP archive entry names.
    """
    if not raw:
        raise MultipartPathError("empty file path")
    if len(raw) > 512:
        raise MultipartPathError("file path too long")
    if "\x00" in raw:
        raise MultipartPathError("NUL in path")
    if "\\" in raw:
        raise MultipartPathError("backslash not allowed")
    if raw.startswith("/") or ":" in raw:
        raise MultipartPathError("absolute or drive-letter path not allowed")
    for p in raw.split("/"):
        if p in _FORBIDDEN_PATH_SEGMENTS:
            raise MultipartPathError(f"forbidden path segment: {p!r}")


def _sanitize_upload_path(raw: str) -> str:
    """Normalise a browser-supplied relative path. Rejects the same
    unsafe cases as ``intake._normalize_relative`` and adds a length cap.
    """
    _reject_unsafe_path(raw)
    parts = raw.split("/")
    # Drop the leading "root" directory produced by the browser folder
    # picker; the intake layer keys off relative paths, and every file
    # already shares the same first segment.
    if len(parts) < 2:
        raise MultipartPathError(
            "expected a folder upload (relative path with subdirectory)")
    normalised = "/".join(parts[1:])
    if not normalised:
        raise MultipartPathError("empty normalized path after root strip")
    return normalised


def _sanitize_zip_entry_path(raw: str) -> str:
    """Validate a raw ZIP entry name for zip-slip / NUL / drive-letter
    tricks. Unlike ``_sanitize_upload_path`` this does not require or
    strip a leading root segment -- a ZIP archive may or may not wrap its
    contents in one folder; the caller decides whether to strip one.
    """
    _reject_unsafe_path(raw)
    return raw


class _ZipUploadError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


_ZIP_READ_CHUNK = 65536


def _extract_skill_zip(data: bytes, tmpdir: str, *, fallback_root_name: str
                       ) -> tuple[str, Dict[str, str]]:
    """Safely extract a Skill ZIP upload into ``tmpdir``.

    Applies the same protections as the folder-upload path (zip-slip /
    absolute / forbidden path segment rejection, per-file and total size
    budgets, max entry count) plus a zip-bomb guard: entries are read
    incrementally with a hard cap rather than trusting the archive's own
    declared (and forgeable) uncompressed-size metadata. Returns the
    artifact root name to label the review with -- the ZIP's own wrapping
    folder if every entry shares one, otherwise ``fallback_root_name`` --
    plus a path -> decoded-text map so the Web UI can still show
    original-text evidence locations (the folder-upload flow gets this
    for free by reading File objects directly in the browser; a ZIP
    upload has no per-entry File objects for the client to read).
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise _ZipUploadError("bad_zip", "not a valid ZIP file")
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if not infos:
        raise _ZipUploadError("no_files", "ZIP archive contains no files")
    if len(infos) > MAX_SKILL_FILES:
        raise _ZipUploadError(
            "too_many_files", f"more than {MAX_SKILL_FILES} files", 413)

    rel_by_info: Dict[Any, str] = {}
    common_root: Any = None
    for info in infos:
        try:
            rel = _sanitize_zip_entry_path(info.filename)
        except MultipartPathError as e:
            raise _ZipUploadError("bad_path", str(e))
        rel_by_info[info] = rel
        segments = rel.split("/")
        if len(segments) < 2:
            common_root = False
        elif common_root is None:
            common_root = segments[0]
        elif common_root is not False and common_root != segments[0]:
            common_root = False

    strip_root = bool(common_root)
    seen: set = set()
    seen_lower: set = set()
    total = 0
    source_files: Dict[str, str] = {}
    for info in infos:
        rel = rel_by_info[info]
        if strip_root:
            rel = rel.split("/", 1)[1]
        if not rel:
            raise _ZipUploadError("bad_path", "empty path after root strip")
        if rel in seen or rel.lower() in seen_lower:
            raise _ZipUploadError(
                "bad_path", "duplicate or case-colliding path in ZIP")
        seen.add(rel)
        seen_lower.add(rel.lower())

        dst = Path(tmpdir) / rel
        try:
            dst.resolve().relative_to(Path(tmpdir).resolve())
        except ValueError:
            raise _ZipUploadError("bad_path", "path escapes upload directory")

        chunks = []
        file_total = 0
        with zf.open(info) as src:
            while True:
                chunk = src.read(_ZIP_READ_CHUNK)
                if not chunk:
                    break
                file_total += len(chunk)
                if file_total > MAX_SKILL_FILE_BYTES:
                    raise _ZipUploadError(
                        "file_too_large", f"{rel} exceeds per-file budget", 413)
                total += len(chunk)
                if total > MAX_SKILL_TOTAL_BYTES:
                    raise _ZipUploadError(
                        "total_too_large",
                        "total archive contents exceed budget", 413)
                chunks.append(chunk)
        file_data = b"".join(chunks)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(file_data)
        try:
            source_files[rel] = file_data.decode("utf-8")
        except UnicodeDecodeError:
            source_files[rel] = ""

    root_name = common_root if strip_root else fallback_root_name
    return root_name, source_files


# ---------------- Endpoints --------------------------------------------

def _resolved_provider_settings(store):
    preferences, key_saved = store.public_settings()
    api_key = store.resolve_key() or "" if key_saved else ""
    return preferences, api_key


def _validator_models_from_payload(payload: Dict[str, Any],
                                   fallback_model: str) -> list:
    """Return the requested list of validator model ids from a Prompt/Skill
    payload's ``validator_models`` field (a JSON array of model id strings),
    or ``[fallback_model]`` when absent/empty/singular — this keeps today's
    exact single-``validator=`` behaviour for anyone not using the new
    multi-vote control.
    """
    raw = payload.get("validator_models")
    if raw in (None, "", []):
        return [fallback_model]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            raise _BadSemanticPayload(
                "bad_model", "validator_models must be a JSON array of model ids")
    if not isinstance(raw, list) or not all(isinstance(m, str) for m in raw):
        raise _BadSemanticPayload(
            "bad_model", "validator_models must be a JSON array of model ids")
    models = [m for m in raw if m.strip()]
    return models if models else [fallback_model]


class _BadSemanticPayload(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _maybe_semantic_run(
        payload: Dict[str, Any], settings_store=None,
        resolved_settings=None):
    """Resolve the semantic execution plan for a review request.

    Semantic review is now attempted automatically whenever a trusted
    Provider config can be resolved — from the request payload, or else
    from the persisted settings store — with no separate on/off flag to
    gate it. If no Provider config can be resolved at all, this still
    returns the honest ``provider_not_configured`` legacy path below rather
    than silently skipping.

    Returns one of:
      * ``(sem_cfg, generator, validator_or_validators, env_name)`` — real
        provider run using an EPHEMERAL key env var the caller MUST clear
        afterwards. The third element is a single validator provider unless
        more than one ``validator_models`` entry was requested, in which
        case it is a list (pass through as ``validators=``).
      * ``(sem_cfg, None, None, None)`` — no provider config resolved
        anywhere (honest ``provider_not_configured`` axis);
      * a ``JSONResponse`` error.
    """
    policy = MAX_WEB_EGRESS_POLICY
    has_request_provider_config = bool(
        WEB_PROVIDER_FIELD_NAMES.intersection(payload))
    base_url = str(payload.get("provider_base_url") or "")
    api_key = str(payload.get("provider_api_key") or "")
    generator_model = str(payload.get("generator_model") or "")
    validator_model = str(payload.get("validator_model") or "")
    if resolved_settings is not None and not has_request_provider_config:
        preferences, saved_key = resolved_settings
        base_url = preferences.base_url
        generator_model = preferences.generator_model
        validator_model = preferences.validator_model
        api_key = saved_key
    elif settings_store is not None and not has_request_provider_config:
        from .provider_settings import ProviderSettingsError
        try:
            preferences, api_key = _resolved_provider_settings(settings_store)
            base_url = preferences.base_url
            generator_model = preferences.generator_model
            validator_model = preferences.validator_model
        except ProviderSettingsError as exc:
            return _error_response(exc.code, exc.message, 409)
    if not any((base_url, api_key, generator_model, validator_model)):
        # Legacy honest path: no provider config resolved anywhere.
        from ..semantic import SemanticConfig
        try:
            return (SemanticConfig(enabled=True, egress_policy=policy),
                    None, None, None)
        except ValueError as exc:
            return _error_response("bad_semantic", str(exc), 400)
    try:
        validator_models = _validator_models_from_payload(
            payload, validator_model)
    except _BadSemanticPayload as exc:
        return _error_response(exc.code, exc.message, 400)
    if len(validator_models) > MAX_WEB_VALIDATOR_MODELS:
        return _error_response(
            "bad_model",
            f"validator_models must have at most {MAX_WEB_VALIDATOR_MODELS} "
            "entries", 400)
    from .provider_web import ProviderWebError
    try:
        if len(validator_models) > 1:
            from .provider_web import build_semantic_config_with_multi_validators_key
            sem_cfg, gen, vals, env_name = (
                build_semantic_config_with_multi_validators_key(
                    base_url=base_url,
                    api_key=api_key,
                    generator_model=generator_model,
                    validator_models=validator_models,
                    egress_policy=policy))
            return (sem_cfg, gen, vals, env_name)
        from .provider_web import build_semantic_config_with_ephemeral_key
        sem_cfg, gen, val, env_name = build_semantic_config_with_ephemeral_key(
            base_url=base_url,
            api_key=api_key,
            generator_model=generator_model,
            validator_model=validator_models[0],
            egress_policy=policy)
    except ProviderWebError as exc:
        return _error_response(exc.code, exc.message, 400)
    return (sem_cfg, gen, val, env_name)


async def _maybe_semantic_run_for_request(payload, settings_store):
    if WEB_PROVIDER_FIELD_NAMES.intersection(payload):
        return _maybe_semantic_run(payload)
    from .provider_settings import ProviderSettingsError
    try:
        resolved = await run_in_threadpool(
            _resolved_provider_settings, settings_store)
    except ProviderSettingsError as exc:
        return _error_response(exc.code, exc.message, 409)
    return _maybe_semantic_run(
        payload, resolved_settings=resolved)


def _validator_kwargs(validator_or_validators) -> Dict[str, Any]:
    """Translate the third element of a semantic plan tuple into the
    ``run_review`` kwarg it belongs in: ``validator=`` for the single-model
    (today's exact behaviour, unchanged) case, ``validators=`` for the
    multi-model vote case.
    """
    if isinstance(validator_or_validators, list):
        return {"validators": validator_or_validators}
    return {"validator": validator_or_validators}


def _numeric_payload_field(payload: Dict[str, Any], name: str, default):
    """Return ``payload[name]`` as a bare number, or ``default`` if
    absent/empty — never coerces a string, so a stray ``"30"`` gives a clear
    400 instead of silently working. Returns ``(value, error_response)``;
    caller must check ``error_response`` before using ``value``.
    """
    v = payload.get(name, default)
    if v in (None, ""):
        return default, None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None, _error_response(
            "bad_" + name, f"{name} must be a number", 400)
    return v, None


def _maybe_blackbox_run(payload: Dict[str, Any]):
    """Resolve an explicit, user-confirmed V1.5 black-box run request.

    Returns one of:
      * ``None`` — black-box was not requested; ``ReviewInputs.blackbox_config``
        must stay ``None`` (the only default path).
      * ``(BlackboxConfig, env_name)`` — a real run. ``env_name`` is an
        ephemeral API-key env var (same discipline as
        ``provider_web.build_semantic_config_with_ephemeral_key``); the
        caller MUST call ``clear_ephemeral_key(env_name)`` in a ``finally``.
      * a ``JSONResponse`` error — anything requested-but-incomplete/invalid.
        Never silently falls back to a default/no-op config.

    This stage sends the reviewed prompt to a real model to run live attack
    scenarios, so — unlike the semantic Provider panel — mere field
    presence is never enough: both ``blackbox_enabled`` and
    ``blackbox_confirm`` must be exactly ``True``, and it always uses its
    OWN base_url/model/api_key from this payload, never a value silently
    borrowed from the persisted semantic Provider settings store.
    """
    if payload.get("blackbox_enabled") is not True:
        return None
    if payload.get("blackbox_confirm") is not True:
        return _error_response(
            "blackbox_confirmation_required",
            "blackbox_confirm must be true to run the V1.5 black-box stage "
            "(it sends the reviewed prompt to a real model).", 400)

    base_url = payload.get("blackbox_base_url")
    model_id = payload.get("blackbox_model")
    api_key = payload.get("blackbox_api_key")
    if not isinstance(base_url, str) or not base_url.strip():
        return _error_response("blackbox_base_url_required",
                               "blackbox_base_url is required", 400)
    if (not isinstance(model_id, str) or not model_id.strip()
            or len(model_id) > 200):
        return _error_response("blackbox_model_required",
                               "blackbox_model is required (<=200 chars)", 400)
    if not isinstance(api_key, str) or not api_key.strip():
        return _error_response("blackbox_api_key_required",
                               "blackbox_api_key is required", 400)
    if len(api_key.encode("utf-8")) > _MAX_BLACKBOX_KEY_BYTES:
        return _error_response("blackbox_api_key_too_large",
                               "blackbox_api_key is too large", 400)

    from .provider_web import ProviderWebError, validate_base_url
    try:
        url = validate_base_url(base_url)
    except ProviderWebError as exc:
        return _error_response(exc.code, exc.message, 400)

    scenario_ids_raw = payload.get("blackbox_scenario_ids")
    scenario_ids: tuple = ()
    if scenario_ids_raw not in (None, "", []):
        if (not isinstance(scenario_ids_raw, list)
                or not all(isinstance(s, str) for s in scenario_ids_raw)):
            return _error_response(
                "bad_blackbox_scenario_ids",
                "blackbox_scenario_ids must be an array of scenario id "
                "strings", 400)
        scenario_ids = tuple(s.strip() for s in scenario_ids_raw if s.strip())
    scenario_policy = payload.get("blackbox_scenario_policy") or "artifact_aware"
    if not isinstance(scenario_policy, str):
        return _error_response(
            "bad_blackbox_scenario_policy",
            "blackbox_scenario_policy must be a string", 400)

    max_calls, err = _numeric_payload_field(payload, "blackbox_max_calls", 50)
    if err is not None:
        return err
    timeout_seconds, err = _numeric_payload_field(
        payload, "blackbox_timeout_seconds", 30.0)
    if err is not None:
        return err
    max_tokens, err = _numeric_payload_field(
        payload, "blackbox_max_tokens", 800)
    if err is not None:
        return err

    from .provider_web import clear_ephemeral_key
    env_name = "VERITY_WEB_BLACKBOX_KEY_" + secrets.token_hex(16).upper()
    os.environ[env_name] = api_key.strip()
    try:
        from ..blackbox import BlackboxConfig, BlackboxCredentials
        cfg = BlackboxConfig(
            enabled=True,
            base_url=url,
            model_id=model_id.strip(),
            credentials=BlackboxCredentials(api_key_env=env_name),
            scenario_policy=scenario_policy,
            scenario_ids=scenario_ids,
            max_calls=max_calls,
            timeout_seconds=timeout_seconds,
            max_tokens_per_response=max_tokens,
        )
    except (ValueError, TypeError) as exc:
        clear_ephemeral_key(env_name)
        return _error_response("bad_blackbox_config", str(exc), 400)
    except Exception:
        clear_ephemeral_key(env_name)
        raise
    return (cfg, env_name)


def _maybe_sandbox_run(form: Dict[str, Any]):
    """Resolve an explicit, user-confirmed V2 sandbox run request from a
    Skill review multipart form.

    Mirrors ``_maybe_blackbox_run``'s two-signal (``sandbox_enabled`` +
    ``sandbox_confirm``) discipline, but multipart fields are always plain
    strings, so both must equal the literal string ``"true"``. Returns
    ``None`` (not requested), a ``SandboxConfig`` ready to pass as
    ``ReviewInputs.sandbox_config``, or a ``JSONResponse`` error. The product
    review stage currently fails closed before runner construction; parsing
    these legacy fields does not make Skill execution available.
    """
    if form.get("sandbox_enabled") != "true":
        return None
    if form.get("sandbox_confirm") != "true":
        return _error_response(
            "sandbox_confirmation_required",
            'sandbox_confirm must be "true" to record an explicit V2 request. '
            "Skill execution is currently unavailable and the product path "
            "does not execute uploaded code.", 400)

    entry_point = form.get("sandbox_entry_point") or ""
    if not isinstance(entry_point, str) or not entry_point.strip():
        return _error_response("sandbox_entry_point_required",
                               "sandbox_entry_point is required", 400)

    argv: tuple = ()
    argv_raw = form.get("sandbox_argv")
    if argv_raw not in (None, ""):
        try:
            parsed = json.loads(argv_raw)
        except (json.JSONDecodeError, ValueError, TypeError):
            return _error_response(
                "bad_sandbox_argv",
                "sandbox_argv must be a JSON array of strings", 400)
        if (not isinstance(parsed, list)
                or not all(isinstance(a, str) for a in parsed)):
            return _error_response(
                "bad_sandbox_argv",
                "sandbox_argv must be a JSON array of strings", 400)
        argv = tuple(parsed)

    def _int_form_field(name: str, default: int):
        v = form.get(name)
        if v in (None, ""):
            return default, None
        try:
            return int(v), None
        except (ValueError, TypeError):
            return None, _error_response(
                "bad_" + name, f"{name} must be an integer", 400)

    cpu_seconds, err = _int_form_field("sandbox_cpu_seconds", 10)
    if err is not None:
        return err
    memory_mb, err = _int_form_field("sandbox_memory_mb", 256)
    if err is not None:
        return err
    wall_seconds, err = _int_form_field("sandbox_wall_seconds", 20)
    if err is not None:
        return err

    from ..sandbox import SandboxConfig
    try:
        cfg = SandboxConfig(
            enabled=True,
            entry_point=entry_point.strip(),
            argv=argv,
            cpu_seconds=cpu_seconds,
            memory_mb=memory_mb,
            wall_seconds=wall_seconds,
        )
    except ValueError as exc:
        return _error_response("bad_sandbox_config", str(exc), 400)
    return cfg


async def list_models(request: Request) -> Response:
    """Proxy an OpenAI-compatible /models listing using a user-supplied key.

    The key is used only for this outbound request and is never stored or
    echoed back. Loopback-only, like every endpoint.
    """
    ct = request.headers.get("content-type", "")
    if not ct.startswith("application/json"):
        return _error_response("bad_content_type",
                               "expects application/json", 415)
    try:
        raw = await request.body()
    except Exception:
        return _error_response("read_error", "could not read request body", 400)
    if len(raw) > 64 * 1024:
        return _error_response("request_too_large", "request too large", 413)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error_response("bad_json", "invalid JSON body", 400)
    if not isinstance(payload, dict):
        return _error_response("bad_shape", "expected an object", 400)
    from .provider_settings import ProviderSettingsError
    from .provider_web import ProviderWebError, list_models as _list
    base_url = str(payload.get("provider_base_url") or "")
    api_key = str(payload.get("provider_api_key") or "")
    if (
        "provider_base_url" not in payload
        and "provider_api_key" not in payload
    ):
        try:
            preferences, api_key = await run_in_threadpool(
                _resolved_provider_settings,
                request.app.state.provider_settings,
            )
            base_url = preferences.base_url
        except ProviderSettingsError as exc:
            return _error_response(exc.code, exc.message, 409)
    try:
        models = _list(base_url, api_key)
    except ProviderWebError as exc:
        return _error_response(exc.code, exc.message, 400)
    return JSONResponse({"models": models, "count": len(models)})


def _provider_settings_body(store) -> Dict[str, Any]:
    preferences, key_saved = store.public_settings()
    return {
        "baseUrl": preferences.base_url,
        "generatorModel": preferences.generator_model,
        "validatorModel": preferences.validator_model,
        "keySaved": key_saved,
    }


async def provider_settings(request: Request) -> Response:
    from .provider_settings import (ProviderPreferences,
                                    ProviderSettingsError)
    store = request.app.state.provider_settings
    try:
        if request.method == "GET":
            body = await run_in_threadpool(_provider_settings_body, store)
            return JSONResponse(body)
        if request.method == "DELETE":
            await run_in_threadpool(store.clear)
            return JSONResponse({
                "baseUrl": "",
                "generatorModel": "",
                "validatorModel": "",
                "keySaved": False,
            })
        if request.headers.get("content-type", "").split(
                ";", 1)[0].strip() != "application/json":
            return _error_response(
                "bad_content_type", "expects application/json", 415)
        raw = await request.body()
        if len(raw) > 64 * 1024:
            return _error_response(
                "request_too_large", "request too large", 413)
        payload = json.loads(raw.decode("utf-8"))
        if (
            not isinstance(payload, dict)
            or not set(payload) <= {
                "baseUrl", "apiKey", "generatorModel", "validatorModel"}
        ):
            return _error_response(
                "bad_provider_settings",
                "Provider settings have an invalid shape", 400)
        preferences = ProviderPreferences(
            base_url=payload.get("baseUrl", ""),
            generator_model=payload.get("generatorModel", ""),
            validator_model=payload.get("validatorModel", ""),
        )
        api_key = payload.get("apiKey", "")
        if not isinstance(api_key, str):
            return _error_response(
                "bad_provider_settings", "apiKey must be a string", 400)
        await run_in_threadpool(
            store.save, preferences, api_key=api_key)
        body = await run_in_threadpool(_provider_settings_body, store)
        return JSONResponse(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error_response("bad_json", "invalid JSON body", 400)
    except ProviderSettingsError as exc:
        status = 400 if exc.code in {
            "bad_base_url",
            "bad_model",
            "bad_provider_settings",
            "api_key_required",
            "api_key_too_large",
        } else 409
        return _error_response(exc.code, exc.message, status)


async def index(request: Request) -> Response:
    text = (STATIC_DIR / "index.html").read_text()
    return Response(text, media_type="text/html; charset=utf-8")


async def health(request: Request) -> Response:
    """Minimal health endpoint. Reports only booleans/versions/scope;
    never leaks binary paths, SHA-256 values, temp dirs, or env vars.
    """
    from .. import __version__ as verity_version
    body: dict = {
        "ok": True,
        "verity": verity_version,
        "scope": "static-only",
        "bandit": {"available": False, "version": None},
        "gitleaks": {"available": False, "version": None},
    }
    # Bandit availability (best-effort, no external processes; just try import)
    try:
        import bandit  # noqa: F401
        import importlib.metadata as _im
        try:
            body["bandit"] = {"available": True,
                              "version": _im.version("bandit")}
        except Exception:
            body["bandit"] = {"available": True, "version": None}
    except Exception:
        pass
    # Gitleaks availability (via runner discovery; NO path/hash leaked)
    try:
        from ..gitleaks_runner import GitleaksRunner
        ok, _reason, version, _sha = GitleaksRunner().check_binary()
        body["gitleaks"] = {"available": bool(ok),
                             "version": version or None}
    except Exception:
        pass
    return JSONResponse(body)


async def review_prompt(request: Request) -> Response:
    if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
        return _error_response("bad_content_type",
                               "prompt review expects application/json", 415)
    try:
        raw = await request.body()
    except Exception:
        return _error_response("read_error", "could not read request body", 400)
    if len(raw) > MAX_REQUEST_BYTES:
        return _error_response("request_too_large", "request too large", 413)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error_response("bad_json", "invalid JSON body", 400)
    if not isinstance(payload, dict):
        return _error_response("bad_shape", "expected an object", 400)
    text = payload.get("text")
    kind = payload.get("prompt_kind", "user_prompt")
    if not isinstance(text, str):
        return _error_response("text_required", "'text' must be a string", 400)
    if kind not in PROMPT_KINDS:
        return _error_response("bad_prompt_kind",
                               "prompt_kind must be user_prompt or system_prompt", 400)
    if len(text.encode("utf-8")) > MAX_PROMPT_BYTES:
        return _error_response("prompt_too_large",
                               "prompt exceeds server budget", 413)
    try:
        snap, byts = intake_text(text, prompt_kind=kind)
    except IntakeError as e:
        return _error_response("intake_error", str(e), 400)

    plan = await _maybe_semantic_run_for_request(
        payload, request.app.state.provider_settings)
    if isinstance(plan, JSONResponse):
        return plan
    sem_cfg = generator = validator_or_validators = env_name = None
    if plan is not None:
        sem_cfg, generator, validator_or_validators, env_name = plan
    validator_kwargs = _validator_kwargs(validator_or_validators)

    blackbox_plan = _maybe_blackbox_run(payload)
    if isinstance(blackbox_plan, JSONResponse):
        if env_name:
            from .provider_web import clear_ephemeral_key
            clear_ephemeral_key(env_name)
        return blackbox_plan
    blackbox_config = blackbox_env_name = None
    if blackbox_plan is not None:
        blackbox_config, blackbox_env_name = blackbox_plan

    try:
        review = run_review(ReviewInputs(engine="prompt", snapshot=snap,
                                          file_bytes=byts,
                                          semantic_config=sem_cfg,
                                          blackbox_config=blackbox_config),
                            candidate_generator=generator,
                            **validator_kwargs)
    finally:
        if env_name:
            from .provider_web import clear_ephemeral_key
            clear_ephemeral_key(env_name)
        if blackbox_env_name:
            from .provider_web import clear_ephemeral_key
            clear_ephemeral_key(blackbox_env_name)
    stored = _make_report(review, "prompt")
    rid = request.app.state.store.put(stored)
    view = _view_for(review, "prompt", rid)
    return JSONResponse(view)


async def review_skill(request: Request) -> Response:
    ct = request.headers.get("content-type", "")
    if not ct.startswith("multipart/form-data"):
        return _error_response("bad_content_type",
                               "skill review expects multipart/form-data", 415)
    try:
        form = await request.form(
            max_files=MAX_SKILL_FILES + 4,
            max_fields=MAX_SKILL_FILES + 32,
        )
    except Exception:
        return _error_response("bad_multipart", "invalid multipart body", 400)

    requested_profiles = form.getlist("profile")
    if (
        len(requested_profiles) > 1
        or (
            requested_profiles
            and requested_profiles[0] not in {"", "minimal", "standard"}
        )
    ):
        return _error_response(
            "bad_profile", "profile must be minimal or standard", 400)
    profile = MAX_WEB_SKILL_PROFILE

    files = [v for v in form.getlist("files") if isinstance(v, UploadFile)]
    if not files:
        return _error_response("no_files", "no files uploaded", 400)

    archive_format = form.get("archive_format") or ""
    if archive_format not in ("", "zip"):
        return _error_response(
            "bad_archive_format", "archive_format must be zip", 400)

    tmpdir = tempfile.mkdtemp(prefix="verity-web-skill-")
    try:
        upload_root_name: Optional[str] = None
        source_files: Dict[str, str] = {}
        if archive_format == "zip":
            if len(files) != 1:
                return _error_response(
                    "bad_archive", "ZIP upload expects exactly one file", 400)
            uf = files[0]
            raw_name = uf.filename or ""
            if not raw_name.lower().endswith(".zip"):
                return _error_response(
                    "bad_archive", "expected a .zip file", 400)
            data = await uf.read()
            if len(data) > MAX_SKILL_TOTAL_BYTES:
                return _error_response(
                    "total_too_large", "ZIP file exceeds upload budget", 413)
            fallback_root_name = Path(raw_name).stem or "skill"
            try:
                upload_root_name, source_files = _extract_skill_zip(
                    data, tmpdir, fallback_root_name=fallback_root_name)
            except _ZipUploadError as e:
                return _error_response(e.code, e.message, e.status)
        else:
            if len(files) > MAX_SKILL_FILES:
                return _error_response("too_many_files",
                                       f"more than {MAX_SKILL_FILES} files", 413)
            total = 0
            seen_upload_paths: set[str] = set()
            seen_upload_paths_lower: set[str] = set()
            for uf in files:
                raw_name = uf.filename or ""
                try:
                    rel = _sanitize_upload_path(raw_name)
                except MultipartPathError as e:
                    return _error_response("bad_path", str(e), 400)
                root_name = raw_name.split("/", 1)[0]
                if upload_root_name is None:
                    upload_root_name = root_name
                elif upload_root_name != root_name:
                    return _error_response(
                        "bad_path", "all files must share one upload root", 400)
                if rel in seen_upload_paths or rel.lower() in seen_upload_paths_lower:
                    return _error_response(
                        "bad_path", "duplicate or case-colliding upload path", 400)
                seen_upload_paths.add(rel)
                seen_upload_paths_lower.add(rel.lower())
                # Read with size cap.
                data = await uf.read()
                if len(data) > MAX_SKILL_FILE_BYTES:
                    return _error_response("file_too_large",
                                           f"{rel} exceeds per-file budget", 413)
                total += len(data)
                if total > MAX_SKILL_TOTAL_BYTES:
                    return _error_response("total_too_large",
                                           "total upload exceeds budget", 413)
                dst = Path(tmpdir) / rel
                # Second-line defence: ensure dst.resolve() is inside tmpdir.
                try:
                    dst.resolve().relative_to(Path(tmpdir).resolve())
                except ValueError:
                    return _error_response("bad_path",
                                           "path escapes upload directory", 400)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(data)

        # Now run the SAME safe intake path as the CLI.
        try:
            snap, byts = intake_directory(
                tmpdir, artifact_root_name=upload_root_name,
                budget=IntakeBudget(
                    max_files=MAX_SKILL_FILES,
                    max_file_size=MAX_SKILL_FILE_BYTES,
                    max_total_size=MAX_SKILL_TOTAL_BYTES,
                ))
        except IntakeError as e:
            return _error_response("intake_error", str(e), 400)
        semantic_payload = {
            "egress_policy": form.get("egress_policy"),
        }
        for field_name in (
            WEB_PROVIDER_FIELD_NAMES | {WEB_VALIDATOR_MODELS_FIELD_NAME}
        ):
            field_value = form.get(field_name)
            if field_value not in (None, ""):
                semantic_payload[field_name] = field_value
        plan = await _maybe_semantic_run_for_request(
            semantic_payload, request.app.state.provider_settings)
        if isinstance(plan, JSONResponse):
            return plan
        sem_cfg = generator = validator_or_validators = env_name = None
        if plan is not None:
            sem_cfg, generator, validator_or_validators, env_name = plan
        validator_kwargs = _validator_kwargs(validator_or_validators)

        sandbox_plan = _maybe_sandbox_run(form)
        if isinstance(sandbox_plan, JSONResponse):
            if env_name:
                from .provider_web import clear_ephemeral_key
                clear_ephemeral_key(env_name)
            return sandbox_plan
        sandbox_config = sandbox_plan  # None or a SandboxConfig

        try:
            review = run_review(ReviewInputs(engine="skill", snapshot=snap,
                                             file_bytes=byts, profile=profile,
                                             semantic_config=sem_cfg,
                                             sandbox_config=sandbox_config),
                                candidate_generator=generator,
                                **validator_kwargs)
        except ValueError as e:
            # e.g. unknown profile (already guarded, but be safe)
            return _error_response("review_error", str(e), 400)
        finally:
            if env_name:
                from .provider_web import clear_ephemeral_key
                clear_ephemeral_key(env_name)

        stored = _make_report(review, "skill")
        rid = request.app.state.store.put(stored)
        view = _view_for(review, "skill", rid)
        if source_files:
            # ZIP upload only: the client has no per-entry File objects of
            # its own to read for the "original text" evidence view, so
            # echo back what was already uploaded (the user's own content,
            # already decoded during extraction above).
            view["sourceFiles"] = source_files
        return JSONResponse(view)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


async def list_projects(request: Request) -> Response:
    try:
        projects = request.app.state.history.list_projects()
        # Internal IDs are needed by API navigation but never rendered on the
        # ordinary page; names are the primary user identity.
        return JSONResponse({"projects": projects})
    except HistoryError:
        return _error_response("history_unavailable", "Project history is unavailable.", 409)


async def create_project(request: Request) -> Response:
    try:
        payload = await request.json()
        if not isinstance(payload, dict): raise ValueError
        p = request.app.state.history.create_project(payload.get("displayName", ""), payload.get("alias"))
        return JSONResponse({"project": p}, status_code=201)
    except (ValueError, json.JSONDecodeError):
        return _error_response("bad_json", "Expected project displayName.", 400)
    except HistoryError as e:
        return _error_response("project_error", str(e), 409)


async def project_detail(request: Request) -> Response:
    try:
        p = request.app.state.history.get_project(request.path_params["project_ref"])
        return JSONResponse({"project": p, "versions": request.app.state.history.versions(p["artifactId"])})
    except HistoryError as e:
        return _error_response("project_error", str(e), 404)


async def project_diff(request: Request) -> Response:
    try:
        q=request.query_params
        d=request.app.state.history.diff(request.path_params["project_ref"], q.get("previous"), q.get("current"))
        return JSONResponse({"diff": d})
    except HistoryError as e:
        return _error_response("diff_error", str(e), 409)


async def project_dispositions(request: Request) -> Response:
    try:
        ref = request.path_params["project_ref"]
        disps = request.app.state.history.list_dispositions(ref)
        return JSONResponse({"dispositions": disps})
    except HistoryError as e:
        return _error_response("project_error", str(e), 404)


async def add_disposition(request: Request) -> Response:
    try:
        ref = request.path_params["project_ref"]
        fingerprint = request.path_params["fingerprint"]
        payload = await request.json()
        
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        
        status = payload.get("status")
        if status not in {"acknowledged", "accept_risk", "false_positive",
                          "wont_fix"}:
            return _error_response(
                "invalid_status", "Invalid disposition status", 400)
        
        expiry_days = payload.get("expiryDays", 30)
        if not isinstance(expiry_days, int) or not 1 <= expiry_days <= 180:
            return _error_response(
                "invalid_expiry", "Expiry days must be 1-180", 400)
        
        note = payload.get("note")
        if note is not None and (not isinstance(note, str)
                                  or len(note) > 200):
            return _error_response(
                "invalid_note", "Note must be <= 200 characters", 400)
        
        from datetime import datetime, timedelta, timezone
        expiry = datetime.now(timezone.utc) + timedelta(days=expiry_days)
        
        event = request.app.state.history.add_disposition(
            ref, fingerprint, status, expiry, note, created_by="web")
        
        return JSONResponse({"disposition": event}, status_code=201)
    
    except (ValueError, json.JSONDecodeError):
        return _error_response("bad_json", "Invalid request body", 400)
    except HistoryError as e:
        return _error_response("disposition_error", str(e), 409)


async def project_version(request: Request) -> Response:
    """Trusted project URL supplies identity; multipart content cannot."""
    try:
        project=request.app.state.history.get_project(request.path_params["project_ref"])
    except HistoryError as e:
        return _error_response("project_error", str(e), 404)
    try:
        form=await request.form(max_files=MAX_SKILL_FILES+4,max_fields=MAX_SKILL_FILES+32)
    except Exception:
        return _error_response("bad_multipart","invalid multipart body",400)
    requested_profiles = form.getlist("profile")
    if (
        len(requested_profiles) > 1
        or (
            requested_profiles
            and requested_profiles[0] not in {"", "minimal", "standard"}
        )
    ):
        return _error_response(
            "bad_profile", "profile must be minimal or standard", 400)
    profile = MAX_WEB_SKILL_PROFILE
    files=[v for v in form.getlist("files") if isinstance(v,UploadFile)]
    if not files or len(files)>MAX_SKILL_FILES: return _error_response("bad_files","Choose a bounded Skill folder.",400)
    tmpdir=tempfile.mkdtemp(prefix="verity-web-project-")
    try:
        total = 0
        seen_upload_paths: set[str] = set()
        seen_upload_paths_lower: set[str] = set()
        upload_root_name: Optional[str] = None
        for uf in files:
            raw_name = uf.filename or ""
            try:
                rel = _sanitize_upload_path(raw_name)
            except MultipartPathError as e:
                return _error_response("bad_path", str(e), 400)
            root_name = raw_name.split("/", 1)[0]
            if upload_root_name is None:
                upload_root_name = root_name
            elif upload_root_name != root_name:
                return _error_response(
                    "bad_path", "all files must share one upload root", 400)
            if rel in seen_upload_paths or rel.lower() in seen_upload_paths_lower:
                return _error_response(
                    "bad_path", "duplicate or case-colliding upload path", 400)
            seen_upload_paths.add(rel)
            seen_upload_paths_lower.add(rel.lower())
            data = await uf.read()
            total += len(data)
            if (len(data) > MAX_SKILL_FILE_BYTES
                    or total > MAX_SKILL_TOTAL_BYTES):
                return _error_response(
                    "upload_too_large", "Upload exceeds budget.", 413)
            dst = Path(tmpdir) / rel
            try:
                dst.resolve().relative_to(Path(tmpdir).resolve())
            except ValueError:
                return _error_response(
                    "bad_path", "path escapes upload directory", 400)
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
        snap, byts = intake_directory(
            tmpdir, artifact_id=project["artifactId"],
            artifact_root_name=upload_root_name,
            budget=IntakeBudget(
                max_files=MAX_SKILL_FILES,
                max_file_size=MAX_SKILL_FILE_BYTES,
                max_total_size=MAX_SKILL_TOTAL_BYTES))
        review=run_review(ReviewInputs("skill",snap,byts,profile=profile))
        rec=request.app.state.history.add_review(project["artifactId"],review,profile=profile)
        stored=_make_report(review,"skill"); rid=request.app.state.store.put(stored)
        return JSONResponse({"version":rec,"review":_view_for(review,"skill",rid)},status_code=201)
    except (HistoryError,IntakeError) as e:
        return _error_response("version_error",str(e),409)
    finally:
        shutil.rmtree(tmpdir,ignore_errors=True)


async def download_report(request: Request) -> Response:
    review_id = request.path_params["review_id"]
    fmt = request.path_params["fmt"]
    if fmt not in ("json", "html", "sarif"):
        return _error_response("bad_format", "unknown report format", 404)
    if not _is_valid_review_id(review_id):
        return _error_response("bad_review_id", "malformed review id", 400)
    entry = request.app.state.store.get(review_id)
    if entry is None:
        return _error_response("not_found",
                               "report expired or unknown", 404)
    if fmt == "json":
        return Response(entry.json_text,
                        media_type="application/json; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="report.json"'})
    if fmt == "sarif":
        return Response(entry.sarif_text,
                        media_type="application/sarif+json; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="report.sarif"'})
    # html
    return Response(entry.html_text,
                    media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="report.html"'})


def _is_valid_review_id(rid: str) -> bool:
    return (
        isinstance(rid, str) and 1 <= len(rid) <= 64
        and all(c.isalnum() or c in "-_" for c in rid)
    )


# --- App factory -------------------------------------------------------

def create_app(*, store_capacity: int = 32, store_ttl_seconds: int = 24 * 3600,
               history_root=None, provider_settings_store=None) -> Starlette:
    """Build the ASGI app. Tests call this and drive it with httpx."""
    # Force mimetypes for CSS/JS/HTML to what we serve; older Pythons may
    # otherwise return application/octet-stream on some systems.
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("text/css", ".css")
    mimetypes.add_type("text/html", ".html")

    routes = [
        Route("/", index, methods=["GET"]),
        Route("/api/health", health, methods=["GET"]),
        Route("/api/review/prompt", review_prompt, methods=["POST"]),
        Route("/api/review/skill", review_skill, methods=["POST"]),
        Route("/api/models", list_models, methods=["POST"]),
        Route("/api/provider-settings", provider_settings,
              methods=["GET", "PUT", "DELETE"]),
        Route("/api/projects", list_projects, methods=["GET"]),
        Route("/api/projects", create_project, methods=["POST"]),
        Route("/api/projects/{project_ref}", project_detail, methods=["GET"]),
        Route("/api/projects/{project_ref}/versions", project_version, methods=["POST"]),
        Route("/api/projects/{project_ref}/diff", project_diff, methods=["GET"]),
        Route("/api/projects/{project_ref}/dispositions", project_dispositions, methods=["GET"]),
        Route("/api/projects/{project_ref}/dispositions/{fingerprint}", add_disposition, methods=["POST"]),
        Route("/api/report/{review_id}/report.{fmt}", download_report, methods=["GET"]),
        Mount("/static", app=StaticFiles(directory=str(STATIC_DIR)), name="static"),
    ]
    app = Starlette(
        debug=False,
        routes=routes,
        middleware=[Middleware(LoopbackAndHeadersMiddleware)],
    )
    app.state.store = ReportStore(capacity=store_capacity,
                                   ttl_seconds=store_ttl_seconds)
    app.state.history = HistoryStore(history_root)
    if provider_settings_store is None:
        from .provider_settings import create_provider_settings_store
        provider_settings_store = create_provider_settings_store(
            root=history_root)
    app.state.provider_settings = provider_settings_store
    return app
