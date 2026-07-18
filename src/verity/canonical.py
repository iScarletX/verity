"""Canonical serialization and fingerprint helpers.

All fingerprints in Verity are computed on strictly canonicalised inputs
with an explicit domain-separation tag. This module concentrates those
primitives so that different call sites cannot silently diverge.

See spec §2.2, §4.2, §5.1, §5.2, §6, §8.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, Mapping, Sequence

from . import CANONICAL_FINGERPRINT_SPEC_VERSION

ABSENT = "ABSENT"


def canonical_json(value: Any) -> bytes:
    """Deterministic JSON serialization: sorted keys, no extra whitespace,
    ensure_ascii=False so multi-byte content is stable.
    """
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(*parts: bytes) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.hexdigest()


def domain_tag(name: str) -> bytes:
    """Domain separation tag. Bytes representation is stable and unambiguous."""
    return (f"verity:{name}:v1").encode("utf-8") + b"\x00"


def canonical_location(loc: Mapping[str, Any]) -> dict:
    """Canonical form of a single Location.

    Fixed field order, explicit ABSENT placeholder for missing fields; the
    output is a plain dict that will be serialized with `canonical_json`,
    which enforces sorted keys — combined with the explicit placeholder
    this makes serialization bit-stable regardless of insertion order.
    """
    byte_range = loc.get("sourceByteRange")
    if byte_range is not None:
        byte_range = {
            "start": int(byte_range["start"]),
            "end": int(byte_range["end"]),
        }
    return {
        "fileId": loc["fileId"],
        "sourceByteRange": byte_range if byte_range is not None else ABSENT,
        "structuralPath": loc.get("structuralPath", ABSENT) or ABSENT,
        "locationSchemaVersion": loc["locationSchemaVersion"],
    }


def canonical_locations(locs: Sequence[Mapping[str, Any]]) -> list:
    """Sort locations by (fileId, byteRange.start) to defeat array-order drift."""
    prepared = [canonical_location(l) for l in locs]

    def _key(cl):
        br = cl["sourceByteRange"]
        start = br["start"] if isinstance(br, dict) else -1
        return (cl["fileId"], start, cl["structuralPath"] if cl["structuralPath"] != ABSENT else "")

    return sorted(prepared, key=_key)


def raw_byte_range_digest(raw_bytes: bytes) -> str:
    return sha256_hex(domain_tag("raw-byte-range"), raw_bytes)


def occurrence_fingerprint(
    *,
    sensitivity: str,
    locations: Sequence[Mapping[str, Any]],
    raw_bytes: bytes | None = None,
    evidence_kind_tag: str | None = None,
    producer_component_version: str | None = None,
    identity_policy_id: str | None = None,
) -> str:
    """Compute EvidenceRecord.occurrenceFingerprint (spec §5.1).

    - normal, source_span: hash of canonical locations + raw byte range digest.
    - secret: hash of canonical locations + kind tag + producer version + policy;
      the raw secret value is NOT hashed and MUST NOT be persisted (§12.4).
    """
    cl = canonical_locations(locations)
    if sensitivity == "secret":
        payload = {
            "locations": cl,
            "evidenceKindTag": evidence_kind_tag or "",
            "producerComponentVersion": producer_component_version or "",
            "identityPolicyId": identity_policy_id or "",
            "spec": CANONICAL_FINGERPRINT_SPEC_VERSION,
        }
        return sha256_hex(domain_tag("evidence-occurrence"), canonical_json(payload))
    # non-secret path: raw byte range digest is required for stability
    rbrd = raw_byte_range_digest(raw_bytes or b"")
    payload = {
        "locations": cl,
        "rawByteRangeDigest": rbrd,
        "spec": CANONICAL_FINGERPRINT_SPEC_VERSION,
    }
    return sha256_hex(domain_tag("evidence-occurrence"), canonical_json(payload))


def event_dedup_key(
    *,
    rule_id: str,
    rule_version: str,
    rule_config_digest: str,
    occurrence_fingerprints: Iterable[str],
    locations: Sequence[Mapping[str, Any]],
) -> str:
    """RuleMatchEvent.eventDedupKey (spec §5.2).

    Inputs reference occurrence fingerprints only — NEVER evidenceId — so
    that the key is stable across independent runs that observe identical
    content. Scope is limited to a single Snapshot at the caller level.
    """
    payload = {
        "ruleId": rule_id,
        "ruleVersion": rule_version,
        "ruleConfigDigest": rule_config_digest,
        "canonicalEvidenceOccurrences": sorted(set(occurrence_fingerprints)),
        "canonicalLocations": canonical_locations(locations),
    }
    return sha256_hex(domain_tag("rule-match"), canonical_json(payload))


def subject_key(finding_type: str, subject: Mapping[str, Any], subject_key_fields: Sequence[str]) -> str:
    """Finding.subjectKey (spec §8). Only fields declared in
    FindingTypeDefinition.subjectKeyFields participate; sorted by name.
    """
    picked = {k: subject[k] for k in sorted(subject_key_fields) if k in subject}
    payload = {"findingType": finding_type, "subject": picked}
    return sha256_hex(domain_tag("subject-key"), canonical_json(payload))


def snapshot_manifest_digest(entries: Sequence[Mapping[str, Any]]) -> str:
    """Snapshot manifest digest (spec §2.2)."""
    # Sort by normalized path; reject duplicates upstream.
    sorted_entries = sorted(entries, key=lambda e: e["normalizedPath"])
    return sha256_hex(domain_tag("snapshot-manifest"), canonical_json(sorted_entries))


def content_root_digest(entries: Sequence[Mapping[str, Any]]) -> str:
    """contentRootDigest — only included, safely-readable entries with contentDigest."""
    included = [
        {"normalizedPath": e["normalizedPath"], "contentDigest": e["contentDigest"]}
        for e in entries
        if e.get("status") == "included" and e.get("contentDigest")
    ]
    included.sort(key=lambda e: e["normalizedPath"])
    return sha256_hex(domain_tag("content-root"), canonical_json(included))
