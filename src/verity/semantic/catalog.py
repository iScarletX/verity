"""Semantic FindingType catalog (initial 3 entries).

Each entry declares:
- ``findingType`` — controlled id
- ``engine`` (``prompt``|``skill``)
- ``defaultSeverity`` — POLICY value; a Validator cannot override it.
- ``requiresEvidenceKinds`` — evidence kinds that must be present for a
  candidate to be considered.
- ``subjectFields`` — taxonomy-controlled subject shape. Providers CAN
  only fill in these fields; extra fields cause rejection.
- ``subjectKeyFields`` — subject fields that contribute to identity.
- ``owaspAst10`` — real, honest mapping (empty for prompt-only types).
- ``guidanceId`` — key into ``verity.guidance`` catalog.
- ``falsificationQuestion`` — fixed prompt string the Validator sees.
- ``extractor`` — callable(review_dict, file_bytes) -> list of
  (candidate_source_dict, evidence_ids) pairs. Extractors are strictly
  deterministic; they never call any LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SemanticSubjectField:
    fieldName: str
    valueKind: str          # "enum" or "string"
    enum: Optional[List[str]] = None


@dataclass(frozen=True)
class SemanticFindingType:
    findingType: str
    engine: str
    defaultSeverity: str
    subjectFields: List[SemanticSubjectField]
    subjectKeyFields: List[str]
    falsificationQuestion: str
    guidanceId: str
    owaspAst10: List[str] = field(default_factory=list)


# ------------------------------------------------------------------- #
# Extractors: deterministic seed evidence for the Candidate Generator #
# ------------------------------------------------------------------- #

def _prompt_lines(review_dict: Dict[str, Any],
                  file_bytes: Dict[str, bytes]) -> List[Tuple[Dict[str, Any], int, int]]:
    """Return [(location, start, end)] for every non-empty line of the
    single prompt file."""
    snap = review_dict.get("snapshot") or {}
    files = snap.get("files") or []
    prompt_file = next((f for f in files if f.get("status") == "included"), None)
    if prompt_file is None:
        return []
    data = file_bytes.get(prompt_file["fileId"], b"")
    out = []
    offset = 0
    for line in data.splitlines(keepends=True):
        stripped = line.rstrip(b"\r\n")
        if stripped.strip():
            out.append(({
                "fileId": prompt_file["fileId"],
                "artifactPath": prompt_file["normalizedPath"],
                "fileDigest": prompt_file.get("contentDigest") or "",
                "sourceByteRange": {"start": offset,
                                     "end": offset + len(stripped)},
                "locationSchemaVersion": "1",
            }, offset, offset + len(stripped)))
        offset += len(line)
    return out


def _make_evidence_records(locations, *, snapshot_id: str,
                           producer_id: str, kind: str = "source_span"):
    """Build the small in-memory Evidence dicts the orchestrator hands
    to Providers. These are NOT Verity Evidence objects — they are
    projection dicts sufficient for the semantic layer."""
    from ..canonical import occurrence_fingerprint, domain_tag, sha256_hex
    out = []
    for loc in locations:
        # Non-secret path: use minimal fingerprint (canonical location +
        # a synthetic raw digest based on the location itself so
        # extractor-produced evidence has a stable id).
        fp = occurrence_fingerprint(sensitivity="normal",
                                     locations=[loc],
                                     raw_bytes=b"")
        eid = f"ev-sem-{sha256_hex(domain_tag('semantic-evidence'), fp.encode())[:16]}"
        out.append({
            "evidenceId": eid,
            "snapshotId": snapshot_id,
            "kind": kind,
            "locations": [loc],
            "sensitivity": "normal",
            "occurrenceFingerprint": fp,
            "producer": {"componentId": producer_id,
                          "componentVersion": "1.0.0",
                          "executionId": "sem-static-extract"},
        })
    return out


def extract_instruction_conflict(review_dict, file_bytes):
    """For prompt engine: pair up every two non-empty lines as a possible
    conflict candidate seed. This is intentionally noisy on purpose:
    the semantic Validator is what decides whether the pair actually
    conflicts. Bounded by ``max_candidates_per_extractor`` upstream.
    """
    if review_dict.get("engine") != "prompt":
        return []
    lines = _prompt_lines(review_dict, file_bytes)
    if len(lines) < 2:
        return []
    snap = review_dict.get("snapshot") or {}
    sid = snap.get("snapshotId", "")
    locs = [l[0] for l in lines]
    evs = _make_evidence_records(locs, snapshot_id=sid,
                                  producer_id="extractor.prompt.instruction_conflict")
    out = []
    # Compare consecutive line pairs; the generator can decide to skip.
    for i in range(len(evs) - 1):
        a, b = evs[i], evs[i + 1]
        out.append((
            {"lineAIndex": i, "lineBIndex": i + 1},
            [a["evidenceId"], b["evidenceId"]],
            [a, b],
        ))
    return out


def extract_missing_output_contract(review_dict, file_bytes):
    """Very narrow trigger: prompt asks for structured output (mentions
    'JSON', 'YAML', 'schema', or 'format') but contains no explicit
    field list. We just surface it as one candidate seed; Validator
    decides."""
    if review_dict.get("engine") != "prompt":
        return []
    snap = review_dict.get("snapshot") or {}
    files = snap.get("files") or []
    prompt_file = next((f for f in files if f.get("status") == "included"), None)
    if prompt_file is None:
        return []
    data = file_bytes.get(prompt_file["fileId"], b"")
    text = data.decode("utf-8", errors="replace").lower()
    triggers = ("json", "yaml", "schema", "structured", "格式", "字段")
    if not any(t in text for t in triggers):
        return []
    # single evidence covering the whole prompt
    loc = {
        "fileId": prompt_file["fileId"],
        "artifactPath": prompt_file["normalizedPath"],
        "fileDigest": prompt_file.get("contentDigest") or "",
        "sourceByteRange": {"start": 0, "end": len(data)},
        "locationSchemaVersion": "1",
    }
    evs = _make_evidence_records([loc],
                                  snapshot_id=snap.get("snapshotId", ""),
                                  producer_id="extractor.prompt.missing_output_contract")
    return [({"triggers": [t for t in triggers if t in text]},
             [evs[0]["evidenceId"]], evs)]


def extract_declared_behavior_mismatch(review_dict, file_bytes):
    """For skill engine: pair the manifest declaration with any Python
    subprocess.* call in the skill. If both exist the Candidate
    Generator can reason about whether the declared behavior matches
    what the code actually does. Deterministic: no LLM decides here.
    """
    if review_dict.get("engine") != "skill":
        return []
    am = review_dict.get("artifactModel") or {}
    manifest_file = am.get("manifestFile")
    manifest = am.get("manifest") or {}
    if not manifest_file:
        return []
    description = manifest.get("description") or ""
    if not description.strip():
        return []
    # find at least one included .py file (declaration evidence)
    snap = review_dict.get("snapshot") or {}
    files = snap.get("files") or []
    py_file = next((f for f in files
                    if f.get("status") == "included"
                    and f.get("normalizedPath", "").lower().endswith(".py")),
                   None)
    if py_file is None:
        return []
    # declaration location
    decl_loc = {
        "fileId": manifest_file["fileId"],
        "artifactPath": manifest_file["normalizedPath"],
        "fileDigest": "",
        "sourceByteRange": {"start": 0, "end": min(200, len(file_bytes.get(manifest_file["fileId"], b"")))},
        "locationSchemaVersion": "1",
    }
    impl_loc = {
        "fileId": py_file["fileId"],
        "artifactPath": py_file["normalizedPath"],
        "fileDigest": py_file.get("contentDigest") or "",
        "sourceByteRange": {"start": 0,
                             "end": min(600, len(file_bytes.get(py_file["fileId"], b"")))},
        "locationSchemaVersion": "1",
    }
    evs = _make_evidence_records([decl_loc, impl_loc],
                                  snapshot_id=snap.get("snapshotId", ""),
                                  producer_id="extractor.skill.declared_vs_observed")
    return [({"declaredScript": py_file["normalizedPath"],
              "manifestDescriptionPreview": description[:200]},
             [evs[0]["evidenceId"], evs[1]["evidenceId"]],
             evs)]


# ------------------------------------------------------------------- #
# Catalog                                                             #
# ------------------------------------------------------------------- #

Extractor = Callable[[Dict[str, Any], Dict[str, bytes]],
                     List[Tuple[Dict[str, Any], List[str], List[Dict[str, Any]]]]]


CATALOG: Dict[str, Tuple[SemanticFindingType, Extractor]] = {

    "semantic.prompt.instruction_conflict": (
        SemanticFindingType(
            findingType="semantic.prompt.instruction_conflict",
            engine="prompt",
            defaultSeverity="medium",
            subjectFields=[
                SemanticSubjectField("conflictKind", "enum",
                                     enum=["contradictory_directive",
                                           "conflicting_style",
                                           "conflicting_scope"]),
            ],
            subjectKeyFields=["conflictKind"],
            falsificationQuestion=(
                "Do the two cited prompt lines contain instructions that "
                "the model cannot satisfy simultaneously without violating "
                "either of them?"
            ),
            guidanceId="semantic.prompt.instruction_conflict",
            owaspAst10=[],   # no honest AST10 mapping for prompt quality
        ),
        extract_instruction_conflict,
    ),

    "semantic.prompt.missing_output_contract": (
        SemanticFindingType(
            findingType="semantic.prompt.missing_output_contract",
            engine="prompt",
            defaultSeverity="low",
            subjectFields=[
                SemanticSubjectField("expectedFormat", "enum",
                                     enum=["json", "yaml", "structured_text"]),
            ],
            subjectKeyFields=["expectedFormat"],
            falsificationQuestion=(
                "Does the prompt ask for a structured output (JSON / YAML "
                "/ tabular) yet fail to state the required field names or "
                "schema?"
            ),
            guidanceId="semantic.prompt.missing_output_contract",
        ),
        extract_missing_output_contract,
    ),

    "semantic.skill.declared_behavior_mismatch": (
        SemanticFindingType(
            findingType="semantic.skill.declared_behavior_mismatch",
            engine="skill",
            defaultSeverity="medium",
            subjectFields=[
                SemanticSubjectField("mismatchKind", "enum",
                                     enum=["capability_undeclared",
                                           "declared_but_absent",
                                           "scope_broader_than_declared"]),
            ],
            subjectKeyFields=["mismatchKind"],
            falsificationQuestion=(
                "Given the cited manifest declaration and the cited "
                "implementation excerpt, do they describe compatible "
                "runtime behaviour?"
            ),
            guidanceId="semantic.skill.declared_behavior_mismatch",
            owaspAst10=["OWASP-AST04"],
        ),
        extract_declared_behavior_mismatch,
    ),
}


def entry(finding_type: str) -> Optional[Tuple[SemanticFindingType, Extractor]]:
    return CATALOG.get(finding_type)
