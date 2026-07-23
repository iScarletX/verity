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
from itertools import combinations
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
                  file_bytes: Dict[str, bytes]) -> List[Tuple[Dict[str, Any], int, int, bytes]]:
    """Return [(location, start, end, raw_line_bytes)] for every non-empty
    line of the single prompt file. ``raw_line_bytes`` (stripped of line
    endings) lets callers cheaply inspect line content without re-reading
    the file, e.g. to anchor on strong-constraint markers in long prompts.
    """
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
            }, offset, offset + len(stripped), stripped))
        offset += len(line)
    return out


def _make_evidence_records(locations, *, snapshot_id: str,
                           producer_id: str, kind: str = "source_span",
                           metadata_by_index: Optional[List[Dict[str, Any]]] = None):
    """Build the small in-memory Evidence dicts the orchestrator hands
    to Providers. These are NOT Verity Evidence objects — they are
    projection dicts sufficient for the semantic layer."""
    from ..canonical import occurrence_fingerprint, domain_tag, sha256_hex
    out = []
    for index, loc in enumerate(locations):
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
            "metadata": ((metadata_by_index or [])[index]
                         if metadata_by_index and index < len(metadata_by_index)
                         else {}),
        })
    return out


# Strong-constraint markers used to anchor candidate lines in long
# documents (see below). Deliberately narrow: words that typically
# introduce an absolute, falsifiable behavioural rule rather than prose.
# Chinese and English covered; both directions (positive obligation /
# negative prohibition) so a "must X" line can be paired against a
# "never X" / "must not X" line anywhere else in the document.
_STRONG_CONSTRAINT_MARKERS = (
    # English
    "must always", "must never", "always ", "never ", "must not",
    "you must", "required to", "shall not", "forbidden", "prohibited",
    "only ", "exactly ", "strictly",
    # Chinese
    "必须", "绝不", "绝对不", "禁止", "不得", "只能", "仅", "一律",
    "永远不", "从不", "只允许", "严禁",
)


def _select_conflict_candidate_lines(lines, *, max_total: int):
    """Pick a bounded set of line indices to compare for instruction
    conflicts, WITHOUT truncating to only the document's opening lines.

    The default Provider payload can carry eight Evidence records. Selection
    therefore returns at most ``max_total`` lines so the extractor cannot
    create apparently valid seeds whose evidence is later truncated before
    the model sees it.

    Strong-constraint lines are selected first and sampled from both the
    beginning and end of that set. Opening prose fills only the remaining
    slots. This preserves deep-document conflicts while keeping the outbound
    evidence bundle bounded and honest.
    """
    n = len(lines)
    if n <= max_total:
        return list(range(n))
    anchored = []
    for i, entry in enumerate(lines):
        raw = entry[3] if len(entry) > 3 else b""
        try:
            text = raw.decode("utf-8", errors="ignore").lower()
        except Exception:
            text = ""
        if any(marker in text for marker in _STRONG_CONSTRAINT_MARKERS):
            anchored.append(i)

    if len(anchored) > max_total:
        left = (max_total + 1) // 2
        right = max_total - left
        anchored = anchored[:left] + (anchored[-right:] if right else [])

    head = list(range(min(max_total, n)))
    combined = []
    seen = set()
    for i in anchored + head:
        if i not in seen:
            seen.add(i)
            combined.append(i)
        if len(combined) >= max_total:
            break
    return combined


def extract_instruction_conflict(review_dict, file_bytes):
    """For prompt engine: pair up candidate lines as a possible conflict
    seed. This is intentionally noisy on purpose: the semantic Validator
    is what decides whether the pair actually conflicts. Bounded by
    ``max_candidates_per_extractor`` upstream.

    Line selection is bounded to the Provider evidence budget. Documents with
    at most eight non-empty lines remain exhaustive; longer documents
    prioritize lines carrying a strong-constraint marker (see
    ``_STRONG_CONSTRAINT_MARKERS``), including markers deep in the document.
    See docs/LESSONS.md for the motivating gaps.
    """
    if review_dict.get("engine") != "prompt":
        return []
    lines = _prompt_lines(review_dict, file_bytes)
    if len(lines) < 2:
        return []
    snap = review_dict.get("snapshot") or {}
    sid = snap.get("snapshotId", "")
    out = []
    # The semantic egress contract defaults to eight Evidence records.
    # Build records only for the lines that can actually cross that boundary.
    selected = _select_conflict_candidate_lines(lines, max_total=8)
    selected_locs = [lines[i][0] for i in selected]
    evs = _make_evidence_records(
        selected_locs, snapshot_id=sid,
        producer_id="extractor.prompt.instruction_conflict")
    for left, right in combinations(range(len(selected)), 2):
        i, j = selected[left], selected[right]
        a, b = evs[left], evs[right]
        out.append((
            {"lineAIndex": i, "lineBIndex": j},
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


def _whole_prompt_seed(review_dict, file_bytes, *, triggers, producer_id):
    if review_dict.get("engine") != "prompt":
        return []
    snap = review_dict.get("snapshot") or {}
    prompt_file = next((f for f in (snap.get("files") or [])
                        if f.get("status") == "included"), None)
    if prompt_file is None:
        return []
    data = file_bytes.get(prompt_file["fileId"], b"")
    text = data.decode("utf-8", errors="replace").lower()
    found = [t for t in triggers if t in text]
    if not found:
        return []
    loc = {"fileId": prompt_file["fileId"],
           "artifactPath": prompt_file["normalizedPath"],
           "fileDigest": prompt_file.get("contentDigest") or "",
           "sourceByteRange": {"start": 0, "end": len(data)},
           "locationSchemaVersion": "1"}
    ev = _make_evidence_records([loc], snapshot_id=snap.get("snapshotId", ""),
                                producer_id=producer_id)[0]
    return [({"triggerCount": len(found)}, [ev["evidenceId"]], [ev])]


def extract_trust_boundary_ambiguity(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes,
        triggers=("external content", "retrieved", "user input", "tool output",
                  "网页内容", "检索内容", "用户输入", "工具输出"),
        producer_id="extractor.prompt.trust_boundary")


def extract_tool_necessity(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes,
        triggers=("allowed_tools", "allowed-tools", "permissions:", "tools:",
                  "工具权限", "允许工具"),
        producer_id="extractor.prompt.tool_necessity")


def _skill_manifest_and_capability_seed(review_dict, file_bytes, *,
                                        producer_id, require_external=False):
    if review_dict.get("engine") != "skill":
        return []
    am = review_dict.get("artifactModel") or {}
    manifest_file = am.get("manifestFile")
    manifest = am.get("manifest") or {}
    facts = ((am.get("capabilityFacts") or {}).get("facts") or [])
    if not manifest_file:
        return []
    if require_external and not manifest.get("external_reference_count"):
        return []
    if not require_external and not facts and not manifest.get("permissions"):
        return []
    snap = review_dict.get("snapshot") or {}
    files = {f.get("normalizedPath"): f for f in (snap.get("files") or [])
             if f.get("status") == "included"}
    locations = [{"fileId": manifest_file["fileId"],
                  "artifactPath": manifest_file["normalizedPath"],
                  "fileDigest": "", "sourceByteRange": {"start": 0,
                  "end": min(500, len(file_bytes.get(manifest_file["fileId"], b"")))},
                  "locationSchemaVersion": "1"}]
    metadata = [{"evidenceRole": "manifest_declaration"}]
    for fact in facts[:7]:
        f = files.get(fact.get("artifactPath"))
        if f:
            locations.append({"fileId": f["fileId"],
                              "artifactPath": f["normalizedPath"],
                              "fileDigest": f.get("contentDigest") or "",
                              "sourceByteRange": {"start": 0,
                                  "end": min(600, len(file_bytes.get(f["fileId"], b"")))},
                              "locationSchemaVersion": "1"})
            metadata.append({
                "evidenceRole": "capability_fact",
                "capabilityCategory": str(fact.get("category", ""))[:80],
                "capabilityOperation": str(fact.get("operation", ""))[:160],
            })
    evs = _make_evidence_records(locations,
                                  snapshot_id=snap.get("snapshotId", ""),
                                  producer_id=producer_id,
                                  metadata_by_index=metadata)
    source = {"declaredPermissionCount": len(manifest.get("permissions") or []),
              "observedCapabilityCount": len(facts)}
    return [(source, [e["evidenceId"] for e in evs], evs)]


def extract_permission_capability_mismatch(review_dict, file_bytes):
    return _skill_manifest_and_capability_seed(
        review_dict, file_bytes,
        producer_id="extractor.skill.permission_capability")


def extract_external_instruction_trust_gap(review_dict, file_bytes):
    return _skill_manifest_and_capability_seed(
        review_dict, file_bytes,
        producer_id="extractor.skill.external_instruction_trust",
        require_external=True)


def extract_declared_behavior_mismatch(review_dict, file_bytes):
    """Pair a Manifest declaration with bounded deterministic capability facts."""
    am = review_dict.get("artifactModel") or {}
    description = ((am.get("manifest") or {}).get("description") or "")
    if not isinstance(description, str) or not description.strip():
        return []
    return _skill_manifest_and_capability_seed(
        review_dict, file_bytes,
        producer_id="extractor.skill.declared_vs_observed")


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
                "Given the cited manifest declaration and implementation "
                "capability evidence, is the stated behaviour materially "
                "incompatible with what is statically observed?"
            ),
            guidanceId="semantic.skill.declared_behavior_mismatch",
            owaspAst10=["OWASP-AST04"],
        ),
        extract_declared_behavior_mismatch,
    ),

    "semantic.prompt.trust_boundary_ambiguity": (
        SemanticFindingType(
            findingType="semantic.prompt.trust_boundary_ambiguity",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "boundaryKind", "enum",
                enum=["user_input", "retrieved_content", "tool_output"])],
            subjectKeyFields=["boundaryKind"],
            falsificationQuestion=(
                "Does the cited prompt fail to distinguish untrusted data "
                "from trusted instructions, rather than already defining a "
                "clear quoting, delimiting, or non-execution rule?"),
            guidanceId="semantic.prompt.trust_boundary_ambiguity",
        ), extract_trust_boundary_ambiguity,
    ),

    "semantic.prompt.excessive_tool_scope": (
        SemanticFindingType(
            findingType="semantic.prompt.excessive_tool_scope",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "scopeKind", "enum",
                enum=["unnecessary_tool", "overbroad_permission",
                      "missing_approval_boundary"])],
            subjectKeyFields=["scopeKind"],
            falsificationQuestion=(
                "Are the cited tools or permissions materially broader than "
                "the stated task requires, after considering explicit "
                "least-privilege and human-approval constraints?"),
            guidanceId="semantic.prompt.excessive_tool_scope",
        ), extract_tool_necessity,
    ),

    "semantic.skill.permission_capability_mismatch": (
        SemanticFindingType(
            findingType="semantic.skill.permission_capability_mismatch",
            engine="skill", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "mismatchKind", "enum",
                enum=["undeclared_capability", "overbroad_permission",
                      "declared_capability_absent"])],
            subjectKeyFields=["mismatchKind"],
            falsificationQuestion=(
                "Do the cited declared permissions and deterministic static "
                "capability facts materially disagree, rather than merely "
                "using different names for the same narrow capability?"),
            guidanceId="semantic.skill.permission_capability_mismatch",
            owaspAst10=["OWASP-AST03"],
        ), extract_permission_capability_mismatch,
    ),

    "semantic.skill.external_instruction_trust_gap": (
        SemanticFindingType(
            findingType="semantic.skill.external_instruction_trust_gap",
            engine="skill", defaultSeverity="high",
            subjectFields=[SemanticSubjectField(
                "trustGapKind", "enum",
                enum=["unverified_source", "instruction_data_confusion",
                      "missing_integrity_boundary"])],
            subjectKeyFields=["trustGapKind"],
            falsificationQuestion=(
                "Does the cited Skill treat external material as executable "
                "instructions without a clear provenance, integrity, "
                "validation, or data-only boundary?"),
            guidanceId="semantic.skill.external_instruction_trust_gap",
            owaspAst10=["OWASP-AST05"],
        ), extract_external_instruction_trust_gap,
    ),
}


def entry(finding_type: str) -> Optional[Tuple[SemanticFindingType, Extractor]]:
    return CATALOG.get(finding_type)
