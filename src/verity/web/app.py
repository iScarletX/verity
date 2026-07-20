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

import json
import mimetypes
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from starlette.applications import Starlette
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
from ..review import ReviewInputs, SKILL_PROFILES, run_review
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


def _sanitize_upload_path(raw: str) -> str:
    """Normalise a browser-supplied relative path. Rejects the same
    unsafe cases as ``intake._normalize_relative`` and adds a length cap.
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
    parts = raw.split("/")
    # Drop the leading "root" directory produced by the browser folder
    # picker; the intake layer keys off relative paths, and every file
    # already shares the same first segment.
    for p in parts:
        if p in _FORBIDDEN_PATH_SEGMENTS:
            raise MultipartPathError(f"forbidden path segment: {p!r}")
    if len(parts) < 2:
        raise MultipartPathError(
            "expected a folder upload (relative path with subdirectory)")
    normalised = "/".join(parts[1:])
    if not normalised:
        raise MultipartPathError("empty normalized path after root strip")
    return normalised


# ---------------- Endpoints --------------------------------------------

def _maybe_semantic_config(payload: Dict[str, Any]):
    """Parse an optional semantic opt-in from a Prompt (JSON) or Skill
    (form) payload. Returns either ``None``, a ``SemanticConfig``, or a
    ready-to-return ``JSONResponse`` describing the config error.
    """
    enabled = payload.get("semantic_enabled")
    if enabled in (None, False, "", "false", "off", 0, "0"):
        return None
    if enabled not in (True, "true", "on", 1, "1"):
        return _error_response("bad_semantic",
                               "semantic_enabled must be a boolean-ish flag",
                               400)
    policy = payload.get("egress_policy") or "metadata_only"
    from ..semantic import SemanticConfig
    try:
        return SemanticConfig(enabled=True, egress_policy=str(policy))
    except ValueError as exc:
        return _error_response("bad_semantic", str(exc), 400)


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

    sem_cfg = _maybe_semantic_config(payload)
    if isinstance(sem_cfg, JSONResponse):
        return sem_cfg
    review = run_review(ReviewInputs(engine="prompt", snapshot=snap,
                                      file_bytes=byts,
                                      semantic_config=sem_cfg))
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

    profile = str(form.get("profile", "standard"))
    if profile not in SKILL_PROFILES:
        return _error_response("bad_profile",
                               "profile must be standard or minimal", 400)

    files = [v for v in form.getlist("files") if isinstance(v, UploadFile)]
    if not files:
        return _error_response("no_files", "no files uploaded", 400)
    if len(files) > MAX_SKILL_FILES:
        return _error_response("too_many_files",
                               f"more than {MAX_SKILL_FILES} files", 413)

    tmpdir = tempfile.mkdtemp(prefix="verity-web-skill-")
    try:
        total = 0
        seen_upload_paths: set[str] = set()
        seen_upload_paths_lower: set[str] = set()
        for uf in files:
            try:
                rel = _sanitize_upload_path(uf.filename or "")
            except MultipartPathError as e:
                return _error_response("bad_path", str(e), 400)
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
            snap, byts = intake_directory(tmpdir, budget=IntakeBudget(
                max_files=MAX_SKILL_FILES,
                max_file_size=MAX_SKILL_FILE_BYTES,
                max_total_size=MAX_SKILL_TOTAL_BYTES,
            ))
        except IntakeError as e:
            return _error_response("intake_error", str(e), 400)
        sem_cfg_or_err = _maybe_semantic_config(
            {"semantic_enabled": form.get("semantic_enabled"),
             "egress_policy": form.get("egress_policy")}
        )
        if isinstance(sem_cfg_or_err, JSONResponse):
            return sem_cfg_or_err
        try:
            review = run_review(ReviewInputs(engine="skill", snapshot=snap,
                                             file_bytes=byts, profile=profile,
                                             semantic_config=sem_cfg_or_err))
        except ValueError as e:
            # e.g. unknown profile (already guarded, but be safe)
            return _error_response("review_error", str(e), 400)

        stored = _make_report(review, "skill")
        rid = request.app.state.store.put(stored)
        view = _view_for(review, "skill", rid)
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
    profile=str(form.get("profile","standard"))
    if profile not in SKILL_PROFILES: return _error_response("bad_profile","profile must be standard or minimal",400)
    files=[v for v in form.getlist("files") if isinstance(v,UploadFile)]
    if not files or len(files)>MAX_SKILL_FILES: return _error_response("bad_files","Choose a bounded Skill folder.",400)
    tmpdir=tempfile.mkdtemp(prefix="verity-web-project-")
    try:
        total = 0
        seen_upload_paths: set[str] = set()
        seen_upload_paths_lower: set[str] = set()
        for uf in files:
            try:
                rel = _sanitize_upload_path(uf.filename or "")
            except MultipartPathError as e:
                return _error_response("bad_path", str(e), 400)
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
        snap,byts=intake_directory(tmpdir,artifact_id=project["artifactId"],budget=IntakeBudget(max_files=MAX_SKILL_FILES,max_file_size=MAX_SKILL_FILE_BYTES,max_total_size=MAX_SKILL_TOTAL_BYTES))
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
               history_root=None) -> Starlette:
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
        Route("/api/projects", list_projects, methods=["GET"]),
        Route("/api/projects", create_project, methods=["POST"]),
        Route("/api/projects/{project_ref}", project_detail, methods=["GET"]),
        Route("/api/projects/{project_ref}/versions", project_version, methods=["POST"]),
        Route("/api/projects/{project_ref}/diff", project_diff, methods=["GET"]),
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
    return app
