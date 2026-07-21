"""Data-egress gateway.

Every outbound provider request goes through here. The gate:

- drops sensitive evidence (``sensitivity != "normal"``);
- strips host absolute paths (defense-in-depth; upstream already keeps
  paths relative);
- caps every content field length;
- writes a payload audit record with only sizes + SHA-256, never
  content.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


MAX_CONTENT_LEN = 2000       # chars per string field on outbound payloads
MAX_LOCATIONS_PER_EV = 4


@dataclass(frozen=True)
class PayloadAudit:
    """What we RECORD about a provider call. NOTE: no payload contents."""
    call_id: str
    call_role: str
    egress_policy: str
    request_bytes: int
    request_digest_sha256: str
    response_bytes: int
    response_ok: bool
    reason_code: Optional[str] = None


def _capped(s: Any) -> str:
    """Force to str, truncate to MAX_CONTENT_LEN, no NUL."""
    if not isinstance(s, str):
        s = "" if s is None else str(s)
    if "\x00" in s:
        s = s.replace("\x00", "")
    if len(s) > MAX_CONTENT_LEN:
        s = s[:MAX_CONTENT_LEN]
    return s


def _relativize(p: str) -> str:
    """Absolute paths never leave the process. Anything that starts with
    ``/`` gets stripped of its first component; anything with ``/Users/``
    or ``/tmp/`` in the middle gets collapsed to its trailing component.
    Upstream should already prevent these — this is a belt-and-braces
    check specifically for outbound payloads.
    """
    if not p:
        return p
    if p.startswith("/"):
        parts = [x for x in p.split("/") if x]
        return "/".join(parts[-2:]) if parts else ""
    if "/Users/" in p or "/private/" in p or "/tmp/" in p:
        return p.rsplit("/", 1)[-1]
    return p


def _location_view(loc: Dict[str, Any]) -> Dict[str, Any]:
    br = loc.get("sourceByteRange") or {}
    return {
        "artifactPath": _capped(_relativize(loc.get("artifactPath", ""))),
        "startByte": int(br.get("start", 0)) if br else 0,
        "endByte": int(br.get("end", 0)) if br else 0,
    }


def _evidence_view(ev: Dict[str, Any], *, egress_policy: str,
                   snippet_bytes: bytes = b"") -> Optional[Dict[str, Any]]:
    """Whitelisted evidence view. Returns None if this evidence is
    forbidden to send under the current egress policy.
    """
    sens = ev.get("sensitivity") or "normal"
    if sens != "normal":
        return None
    kind = ev.get("kind") or "source_span"
    view = {
        "evidenceId": _capped(ev.get("evidenceId", "")),
        "kind": _capped(kind),
        "locations": [_location_view(l) for l in (ev.get("locations") or [])
                      ][:MAX_LOCATIONS_PER_EV],
    }
    # Only controlled extractor facts cross the boundary. Arbitrary Evidence
    # metadata is never forwarded.
    metadata = ev.get("metadata") or {}
    safe_metadata = {}
    if metadata.get("evidenceRole") in {
            "manifest_declaration", "capability_fact"}:
        safe_metadata["evidenceRole"] = metadata["evidenceRole"]
    if safe_metadata.get("evidenceRole") == "capability_fact":
        safe_metadata["capabilityCategory"] = _capped(
            metadata.get("capabilityCategory", ""))
        safe_metadata["capabilityOperation"] = _capped(
            metadata.get("capabilityOperation", ""))
    if safe_metadata:
        view["metadata"] = safe_metadata
    if egress_policy == "redacted_evidence":
        # A short snippet (already scrubbed of NUL, capped).
        if snippet_bytes:
            try:
                text = snippet_bytes.decode("utf-8", errors="replace")
            except Exception:
                text = ""
            view["textSnippet"] = _capped(text)
    return view


def build_generator_request(
    *,
    review_id: str,
    engine: str,
    finding_type: str,
    evidences: List[Dict[str, Any]],
    file_bytes: Dict[str, bytes],
    egress_policy: str,
    subject_taxonomy: Dict[str, Any],
    max_evidence: int,
    prompt_kind: Optional[str] = None,
) -> Dict[str, Any]:
    """Assemble the JSON body a Candidate Generator will see.

    The response schema (below) forbids the generator from providing
    Evidence text back; we only send the allowlisted view of the
    evidence bundle it can reference.
    """
    ev_views: List[Dict[str, Any]] = []
    for ev in evidences[:max_evidence]:
        snippet = b""
        if egress_policy == "redacted_evidence":
            # Pull the raw bytes for the first location only, capped.
            locs = ev.get("locations") or []
            if locs:
                br = (locs[0] or {}).get("sourceByteRange") or {}
                fid = (locs[0] or {}).get("fileId")
                if fid and br:
                    raw = file_bytes.get(fid, b"")
                    start = int(br.get("start", 0))
                    end = int(br.get("end", start))
                    end = min(end, start + MAX_CONTENT_LEN)
                    snippet = raw[start:end]
        v = _evidence_view(ev, egress_policy=egress_policy,
                           snippet_bytes=snippet)
        if v is not None:
            ev_views.append(v)
    return {
        "reviewId": _capped(review_id),
        "engine": _capped(engine),
        "findingType": _capped(finding_type),
        "egressPolicy": egress_policy,
        "subjectTaxonomy": subject_taxonomy,     # already schema-shaped
        "evidence": ev_views,
        "promptKind": _capped(prompt_kind or ""),
        "instruction": (
            "You may only propose semantic candidates whose evidence "
            "references are drawn from the list above. Do not invent "
            "new evidence, do not set severity or ruleId. Return JSON "
            "matching the candidate response schema."
        ),
    }


def build_validator_request(
    *,
    review_id: str,
    candidate: Dict[str, Any],
    evidences: List[Dict[str, Any]],
    file_bytes: Dict[str, bytes],
    egress_policy: str,
    falsification_question: str,
) -> Dict[str, Any]:
    """Assemble the JSON body a Validator sees. It contains a single
    candidate identity and the subset of Evidence that the generator
    was allowed to see for that candidate; nothing else."""
    ev_views: List[Dict[str, Any]] = []
    for ev in evidences:
        snippet = b""
        if egress_policy == "redacted_evidence":
            locs = ev.get("locations") or []
            if locs:
                br = (locs[0] or {}).get("sourceByteRange") or {}
                fid = (locs[0] or {}).get("fileId")
                if fid and br:
                    raw = file_bytes.get(fid, b"")
                    start = int(br.get("start", 0))
                    end = int(br.get("end", start))
                    end = min(end, start + MAX_CONTENT_LEN)
                    snippet = raw[start:end]
        v = _evidence_view(ev, egress_policy=egress_policy,
                           snippet_bytes=snippet)
        if v is not None:
            ev_views.append(v)
    return {
        "reviewId": _capped(review_id),
        "candidate": {
            "candidateId": _capped(candidate["candidateId"]),
            "findingType": _capped(candidate["findingType"]),
            "subject": candidate["subject"],
            "claim": _capped(candidate.get("claim", "")),
            "evidenceIds": [_capped(e) for e in candidate.get("evidenceIds", [])],
        },
        "evidence": ev_views,
        "falsificationQuestion": _capped(falsification_question),
        "egressPolicy": egress_policy,
        "instruction": (
            "You MUST NOT modify the candidate, invent new evidence, "
            "change the finding type, set severity, or return a "
            "different candidateId. Reply with decision in "
            "{confirmed, rejected, insufficient_evidence}."
        ),
    }


def audit_call(
    *,
    call_id: str,
    call_role: str,
    egress_policy: str,
    request_obj: Dict[str, Any],
    response_bytes: int,
    response_ok: bool,
    reason_code: Optional[str] = None,
) -> PayloadAudit:
    body = json.dumps(request_obj, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return PayloadAudit(
        call_id=call_id,
        call_role=call_role,
        egress_policy=egress_policy,
        request_bytes=len(body),
        request_digest_sha256=hashlib.sha256(body).hexdigest(),
        response_bytes=int(response_bytes),
        response_ok=bool(response_ok),
        reason_code=reason_code,
    )


def scan_payload_for_leaks(payload: Any) -> List[str]:
    """Sanity check: return a list of forbidden substrings that appear
    in ``payload``. Used by tests. Not called on the hot path in
    production (the payload has already been assembled from whitelisted
    fields)."""
    forbidden = [
        "/Users/", "/private/", "/tmp/verity-",
        "VERITY_FAKE_SECRET_",
        # gitleaks-style tokens
        "AKIAIOSFODNN", "ghp_1234567890", "xoxb-0000",
    ]
    text = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
    return [f for f in forbidden if f in text]
