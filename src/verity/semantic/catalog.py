"""Controlled semantic FindingType catalog and deterministic seed extractors.

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

import hashlib
import json
import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any, Callable, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class SemanticSubjectField:
    fieldName: str
    valueKind: str          # "enum" or "string"
    enum: Optional[List[str]] = None


@dataclass(frozen=True)
class SemanticJudgmentPolicy:
    """Catalog-owned adjudication policy sent to both semantic roles.

    These strings are trusted Verity configuration, not reviewed artifact
    content.  Keeping applicability and counterexamples beside each Finding
    Type makes the Validator falsify a concrete claim instead of applying one
    generic "looks risky" instruction to every semantic class.
    """
    appliesWhen: List[str]
    confirmWhen: List[str]
    rejectWhen: List[str]
    insufficientWhen: List[str]


@dataclass(frozen=True)
class SemanticFindingType:
    findingType: str
    engine: str
    defaultSeverity: str
    subjectFields: List[SemanticSubjectField]
    subjectKeyFields: List[str]
    falsificationQuestion: str
    guidanceId: str
    judgmentPolicy: SemanticJudgmentPolicy
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
        metadata = ((metadata_by_index or [])[index]
                    if metadata_by_index and index < len(metadata_by_index)
                    else {})
        # Non-secret path: use minimal fingerprint (canonical location +
        # a synthetic raw digest based on the location itself so
        # extractor-produced evidence has a stable id).
        fp = occurrence_fingerprint(sensitivity="normal",
                                     locations=[loc],
                                     raw_bytes=b"")
        # The same source span can legitimately feed several controlled
        # extractors or several facts from one extractor. Include producer and
        # bounded structured metadata in Evidence identity so the global pool
        # cannot collapse one semantic role/fact into whichever ran first.
        metadata_fingerprint = json.dumps(
            metadata, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()
        eid_digest = sha256_hex(
            domain_tag("semantic-evidence"), producer_id.encode(), fp.encode(),
            metadata_fingerprint)
        eid = f"ev-sem-{eid_digest[:16]}"
        out.append({
            "evidenceId": eid,
            "snapshotId": snapshot_id,
            "kind": kind,
            "locations": [loc],
            "sensitivity": "normal",
            "occurrenceFingerprint": fp,
            "producer": {"componentId": producer_id,
                          "componentVersion": "2.0.0",
                          "executionId": "sem-static-extract"},
            "metadata": metadata,
        })
    return out


def _contains_any(text: str, terms: Tuple[str, ...]) -> List[str]:
    return [term for term in terms if term in text]


def _constraint_line_metadata(raw: bytes, line_index: int) -> Dict[str, Any]:
    """Return bounded, non-conclusive structure facts for one prompt line."""
    text = raw.decode("utf-8", errors="ignore").lower()
    stages = []
    if _contains_any(text, (
            "start with", "begin with", "first ", "opening", "开头", "首先",
            "先给", "先输出")):
        stages.append("opening_segment")
    if _contains_any(text, (
            "then ", "after that", "follow with", "next ", "然后", "随后",
            "接着", "再给", "再输出")):
        stages.append("later_segment")
    if _contains_any(text, (
            "final answer", "final response", "final output", "最终回答",
            "final explanation", "最终答复", "最终输出", "最终解释")):
        stages.append("final_output")
    if not stages:
        stages.append("unspecified")

    targets = []
    target_terms = (
        ("summary", ("summary", "摘要", "总结")),
        ("explanation", ("explanation", "explain", "说明", "解释")),
        ("reasoning", ("reasoning", "chain of thought", "思考过程", "推理过程")),
        ("answer", ("answer", "response", "reply", "回答", "答复", "回复")),
        ("structured_output", ("json", "yaml", "schema", "表格", "字段")),
    )
    for name, terms in target_terms:
        if _contains_any(text, terms):
            targets.append(name)
    if not targets:
        targets.append("unspecified")

    signals = []
    signal_terms = (
        ("maximum_length", (
            "under ", "at most", "no more than", "fewer than", "以内",
            "不超过", "至多", "少于")),
        ("minimum_length", (
            "at least", "no fewer than", "more than", "不少于", "至少",
            "多于")),
        ("prohibition", (
            "never ", "must not", "do not", "不得", "禁止", "绝不")),
        ("requirement", (
            "must ", "required", "shall ", "always ", "必须", "务必",
            "需要", "始终")),
    )
    for name, terms in signal_terms:
        if _contains_any(text, terms):
            signals.append(name)
    if re.match(
            r"\s*(?:return|provide|include|answer|output)\b", text,
            flags=re.IGNORECASE):
        signals.append("requirement")
    directive = re.sub(
        r"^\s*(?:(?:you\s+)?must\s+(?:always|never|not)|"
        r"(?:you\s+)?must|always|never|do\s+not|required\s+to|"
        r"shall\s+not|必须|务必|始终|永远不|绝不|不得|禁止)\s+",
        "", text, count=1, flags=re.IGNORECASE)
    directive = re.sub(
        r"[^a-z0-9\u4e00-\u9fff]+", " ", directive).strip()
    directive_digest = (
        hashlib.sha256(directive.encode()).hexdigest()[:16]
        if len(directive) >= 8 else "")
    condition_mode = ""
    condition_digest = ""
    condition_match = re.search(
        r"\b(when|unless|if)\s+(.+?)(?:[.;]|$)", text,
        flags=re.IGNORECASE)
    if condition_match:
        condition_mode = condition_match.group(1).lower()
        condition = re.sub(
            r"[^a-z0-9\u4e00-\u9fff]+", " ",
            condition_match.group(2)).strip()
        if len(condition) >= 8:
            condition_digest = hashlib.sha256(
                condition.encode()).hexdigest()[:16]
    return {
        "evidenceRole": "prompt_constraint",
        "lineIndex": line_index,
        "outputStages": stages[:3],
        "contentTargets": targets[:4],
        "constraintSignals": signals[:4],
        "directiveTargetDigest": directive_digest,
        "conditionMode": condition_mode,
        "conditionClauseDigest": condition_digest,
    }


_FORMAT_TERMS = {
    "json": ("json",),
    "yaml": ("yaml", "yml"),
    "tabular": ("table", "tabular", "csv", "表格"),
    "structured_text": ("schema", "structured", "格式", "字段"),
}
_TYPE_TERMS = (
    "string", "integer", "number", "boolean", "array", "object", "list",
    "字符串", "整数", "数字", "布尔", "数组", "对象", "列表",
)
_REQUIRED_TERMS = (
    "required", "optional", "must include", "必填", "选填", "必须包含",
)
_ENUM_TERMS = (
    "enum", "one of", "allowed values", "可选值", "枚举", "只能是",
)
_UNIT_TERMS = (
    "unit", "decimal", "yyyy-mm-dd", "单位", "小数", "日期格式",
)


def _output_contract_metadata(text: str) -> Dict[str, Any]:
    requested = [
        name for name, terms in _FORMAT_TERMS.items()
        if any(term in text for term in terms)
    ]
    # Count only declaration-like names, not every noun in prose.
    field_patterns = (
        r"\bfields?\s*[:=]\s*[a-z_][a-z0-9_-]*",
        r"\bcolumns?\s+[a-z_][a-z0-9_-]*",
        r"(?m)^\s*[-*]\s*[a-z_][a-z0-9_-]*\s*:",
        r"\b[a-z_][a-z0-9_-]*\s*\((?:string|integer|number|boolean|array|object|list)",
        r'["\'][a-z_][a-z0-9_-]*["\']\s*:',
        r"(?:字段|包含)\s*[:：]?\s*[A-Za-z_\u4e00-\u9fff][^。\n]{0,80}",
    )
    named_fields = sum(len(re.findall(pattern, text, flags=re.IGNORECASE))
                       for pattern in field_patterns)
    return {
        "evidenceRole": "output_contract",
        "requestedFormats": requested[:4],
        "namedFieldSignalCount": min(named_fields, 32),
        "typeMarkerCount": min(
            sum(text.count(term) for term in _TYPE_TERMS), 32),
        "requirednessMarkerCount": min(
            sum(text.count(term) for term in _REQUIRED_TERMS), 32),
        "enumMarkerCount": min(
            sum(text.count(term) for term in _ENUM_TERMS), 32),
        "unitMarkerCount": min(
            sum(text.count(term) for term in _UNIT_TERMS), 32),
    }


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


def _instruction_conflict_candidate_hints(left_metadata, right_metadata):
    left_signals = set(left_metadata.get("constraintSignals") or [])
    right_signals = set(right_metadata.get("constraintSignals") or [])
    opposed = (
        ("maximum_length" in left_signals
         and "minimum_length" in right_signals)
        or ("minimum_length" in left_signals
            and "maximum_length" in right_signals)
        or ("prohibition" in left_signals
            and "requirement" in right_signals)
        or ("requirement" in left_signals
            and "prohibition" in right_signals)
    )
    if not opposed:
        return []
    condition_digest = left_metadata.get("conditionClauseDigest")
    matched_exception_scopes = (
        condition_digest
        and condition_digest == right_metadata.get("conditionClauseDigest")
        and "unless" in {
            left_metadata.get("conditionMode"),
            right_metadata.get("conditionMode"),
        }
    )
    if matched_exception_scopes:
        return []
    left_targets = set(left_metadata.get("contentTargets") or []) - {
        "unspecified"}
    right_targets = set(right_metadata.get("contentTargets") or []) - {
        "unspecified"}
    left_stages = set(left_metadata.get("outputStages") or []) - {
        "unspecified"}
    right_stages = set(right_metadata.get("outputStages") or []) - {
        "unspecified"}
    same_target = bool(left_targets & right_targets)
    same_target = same_target or bool(
        left_metadata.get("directiveTargetDigest")
        and left_metadata.get("directiveTargetDigest")
        == right_metadata.get("directiveTargetDigest"))
    same_final_stage = "final_output" in (left_stages & right_stages)
    if not (same_target or same_final_stage):
        return []
    return [_candidate_hint(
        {"conflictKind": "contradictory_directive"},
        "Two directives impose opposing constraints on the same output "
        "target or final-output stage.")]


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
    selected_metadata = [
        _constraint_line_metadata(lines[i][3], i) for i in selected
    ]
    evs = _make_evidence_records(
        selected_locs, snapshot_id=sid,
        producer_id="extractor.prompt.instruction_conflict",
        metadata_by_index=selected_metadata)
    for left, right in combinations(range(len(selected)), 2):
        i, j = selected[left], selected[right]
        a, b = evs[left], evs[right]
        source = {"lineAIndex": i, "lineBIndex": j}
        candidate_hints = _instruction_conflict_candidate_hints(
            selected_metadata[left], selected_metadata[right])
        if candidate_hints:
            source["candidateHints"] = candidate_hints
        else:
            source["modelCandidatePolicy"] = "skip_without_catalog_hint"
            source["modelCandidateSkipReason"] = (
                "no_structured_opposing_constraint")
        out.append((
            source,
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
    triggers = (
        "json", "yaml", "schema", "structured", "csv", "table", "tabular",
        "格式", "字段", "表格",
    )
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
                                  producer_id="extractor.prompt.missing_output_contract",
                                  metadata_by_index=[
                                      _output_contract_metadata(text)])
    metadata = evs[0]["metadata"]
    expected = "structured_text"
    requested = metadata.get("requestedFormats") or []
    if "json" in requested:
        expected = "json"
    elif "yaml" in requested:
        expected = "yaml"
    source = {"triggers": [t for t in triggers if t in text]}
    if metadata.get("namedFieldSignalCount", 0) == 0:
        source["candidateHints"] = [_candidate_hint(
            {"expectedFormat": expected, "gapKind": "missing_fields"},
            "A machine-structured output format is requested without "
            "evidenced required fields or schema.")]
    else:
        source["modelCandidatePolicy"] = "skip_without_catalog_hint"
        source["modelCandidateSkipReason"] = "structured_fields_declared"
    return [(source, [evs[0]["evidenceId"]], evs)]


def _whole_prompt_seed(review_dict, file_bytes, *, triggers, producer_id,
                       metadata_builder=None, candidate_hint_builder=None,
                       model_candidate_gate=None, require_all_groups=None,
                       system_prompt_only=False,
                       allow_without_trigger=False):
    if review_dict.get("engine") != "prompt":
        return []
    snap = review_dict.get("snapshot") or {}
    if system_prompt_only and snap.get("promptKind") != "system_prompt":
        return []
    prompt_file = next((f for f in (snap.get("files") or [])
                        if f.get("status") == "included"), None)
    if prompt_file is None:
        return []
    data = file_bytes.get(prompt_file["fileId"], b"")
    text = data.decode("utf-8", errors="replace").lower()
    found = [t for t in triggers if t in text]
    if not found and not allow_without_trigger:
        return []
    if require_all_groups and not all(
            any(term in text for term in group) for group in require_all_groups):
        return []
    loc = {"fileId": prompt_file["fileId"],
           "artifactPath": prompt_file["normalizedPath"],
           "fileDigest": prompt_file.get("contentDigest") or "",
           "sourceByteRange": {"start": 0, "end": len(data)},
           "locationSchemaVersion": "1"}
    metadata = (metadata_builder(text) if metadata_builder else {
        "evidenceRole": "prompt_analysis",
        "signalFamilies": ["trigger_present"],
        "evidenceScope": "complete_reviewed_prompt",
    })
    candidate_hints = (
        candidate_hint_builder(metadata) if candidate_hint_builder else [])
    ev = _make_evidence_records([loc], snapshot_id=snap.get("snapshotId", ""),
                                producer_id=producer_id,
                                metadata_by_index=[metadata])[0]
    source = {"triggerCount": len(found)}
    if candidate_hints:
        source["candidateHints"] = candidate_hints
    if model_candidate_gate:
        gate = model_candidate_gate(metadata)
        if isinstance(gate, tuple):
            allow_model_candidates, reason = gate
        else:
            allow_model_candidates, reason = bool(gate), ""
        if not allow_model_candidates:
            source["modelCandidatePolicy"] = "skip_without_catalog_hint"
            if reason:
                source["modelCandidateSkipReason"] = str(reason)[:120]
    return [(source, [ev["evidenceId"]], [ev])]


def extract_prompt_catalog_sweep(review_dict, file_bytes):
    """Return one whole-prompt Evidence record for the bounded catalog sweep.

    This extractor deliberately proposes no Finding. It gives the Candidate
    Generator up to eight source-positioned chunks from which it may propose
    an already registered Finding Type. Short and medium prompts are covered
    completely; longer prompts are sampled across the full byte range and
    explicitly marked as sampled so omission claims cannot treat them as full
    evidence. The orchestrator still enforces every type's subject taxonomy
    and independent Validator policy.
    """
    if review_dict.get("engine") != "prompt":
        return []
    snap = review_dict.get("snapshot") or {}
    prompt_file = next(
        (item for item in (snap.get("files") or [])
         if item.get("status") == "included"),
        None,
    )
    if prompt_file is None:
        return []
    data = file_bytes.get(prompt_file["fileId"], b"")
    if not data:
        return []

    chunk_size = 1800
    max_chunks = 8
    if len(data) <= chunk_size:
        starts = [0]
    else:
        last_start = max(0, len(data) - chunk_size)
        starts = sorted({
            round(last_start * index / (max_chunks - 1))
            for index in range(max_chunks)
        })
    locations = []
    for raw_start in starts:
        start = raw_start
        while start > 0 and data[start] & 0xC0 == 0x80:
            start -= 1
        end = min(len(data), start + chunk_size)
        while end < len(data) and end > start and data[end] & 0xC0 == 0x80:
            end -= 1
        locations.append({
            "fileId": prompt_file["fileId"],
            "artifactPath": prompt_file["normalizedPath"],
            "fileDigest": prompt_file.get("contentDigest") or "",
            "sourceByteRange": {"start": start, "end": end},
            "locationSchemaVersion": "1",
        })

    covered_intervals = sorted(
        (loc["sourceByteRange"]["start"], loc["sourceByteRange"]["end"])
        for loc in locations
    )
    covered_until = 0
    coverage_complete = True
    for start, end in covered_intervals:
        if start > covered_until:
            coverage_complete = False
            break
        covered_until = max(covered_until, end)
    coverage_complete = coverage_complete and covered_until >= len(data)
    text = data.decode("utf-8", errors="replace")
    metadata = [
        _prompt_analysis_metadata(
            signal_families=["catalog_sweep"],
            promptCharacterCount=len(text),
            promptLineCount=max(1, len(text.splitlines())),
            sweepChunkIndex=index,
            sweepChunkCount=len(locations),
            sweepCoverageCompleteCount=int(coverage_complete),
        )
        for index in range(len(locations))
    ]
    scope = (
        "complete_reviewed_prompt"
        if coverage_complete else "sampled_reviewed_prompt"
    )
    for item in metadata:
        item["evidenceScope"] = scope
    evidences = _make_evidence_records(
        locations,
        snapshot_id=snap.get("snapshotId", ""),
        producer_id="extractor.prompt.catalog_sweep",
        metadata_by_index=metadata,
    )
    return [(
        {"triggerCount": 0, "coverageComplete": coverage_complete},
        [item["evidenceId"] for item in evidences],
        evidences,
    )]


def _prompt_analysis_metadata(*, signal_families, **counts):
    metadata: Dict[str, Any] = {
        "evidenceRole": "prompt_analysis",
        "signalFamilies": list(signal_families)[:12],
        "evidenceScope": "complete_reviewed_prompt",
    }
    for key, value in counts.items():
        if isinstance(value, bool):
            metadata[key] = value
        elif isinstance(value, int):
            maximum = 8192 if key == "promptCharacterCount" else 128
            metadata[key] = min(max(value, 0), maximum)
        elif isinstance(value, list):
            metadata[key] = value[:12]
    return metadata


def _candidate_hint(subject: Dict[str, str], claim: str) -> Dict[str, Any]:
    """Return one catalog-owned hypothesis for independent validation."""
    return {"subject": dict(subject), "claim": claim}


def _scoped_gap_count(text, *, signal_groups, control_terms,
                      defeating_terms=()):
    """Count locally unsupported signal windows without cross-section vetoes.

    Whole-document counts are useful routing facts, but a control in one
    paragraph must not silently cancel a risky operation in another. Treat a
    paragraph as one authored rule block so a trailing exception, continuation
    rule, or anti-invention clause can govern its preceding sentences, but
    never cross a blank-line section boundary.
    """
    total = 0
    uncovered = 0
    paragraphs = re.split(r"\n\s*\n", text)
    for paragraph in paragraphs:
        window = paragraph.strip()
        if not window:
            continue
        if not all(any(term in window for term in group)
                   for group in signal_groups):
            continue
        total += 1
        has_control = any(term in window for term in control_terms)
        defeated = any(term in window for term in defeating_terms)
        if not has_control or defeated:
            uncovered += 1
    return total, uncovered


_TRUST_SOURCE_TERMS = (
    "external content", "retrieved", "user input", "tool output",
    "web page", "document content", "网页内容", "检索内容", "用户输入",
    "工具输出", "外部内容",
)
_TRUST_BOUNDARY_TERMS = (
    "treat as data", "not instructions", "untrusted data", "do not follow",
    "delimiter", "quote", "只作为数据", "不是指令", "不可信数据",
    "不要遵循", "分隔符", "引用",
)


def _trust_boundary_metadata(text):
    _total, uncovered = _scoped_gap_count(
        text,
        signal_groups=(_TRUST_SOURCE_TERMS,),
        control_terms=_TRUST_BOUNDARY_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["untrusted_content_boundary"],
        sourceSignalCount=sum(text.count(x) for x in _TRUST_SOURCE_TERMS),
        mitigationSignalCount=sum(text.count(x) for x in _TRUST_BOUNDARY_TERMS),
        unboundedSourceSegmentCount=uncovered,
    )


def _trust_boundary_candidate_hints(metadata):
    if metadata.get("sourceSignalCount", 0) == 0:
        return []
    if metadata.get("unboundedSourceSegmentCount", 0) == 0:
        return []
    return [_candidate_hint(
        {"boundaryKind": "retrieved_content"},
        "Untrusted external, retrieved, user, or tool-produced content lacks "
        "an evidenced data-only instruction boundary.")]


def _trust_boundary_model_gate(metadata):
    if _trust_boundary_candidate_hints(metadata):
        return True, "untrusted_content_without_data_boundary"
    return False, "trust_boundary_controls_present_or_unproven"


_TOOL_SCOPE_TERMS = (
    "allowed_tools", "allowed-tools", "permissions:", "tools:",
    "use read", "use write", "use edit", "use bash", "use shell",
    "use delete", "use webfetch", "use websearch",
    "工具权限", "允许工具",
)
_TOOL_BOUNDARY_TERMS = (
    "least privilege", "only when needed", "approval", "confirm before",
    "do not write", "do not execute", "do not use", "never use",
    "must not use", "human approval", "draft only",
    "最小权限", "仅在需要时", "批准", "确认后", "禁止写入", "禁止执行",
    "禁止使用", "不得使用",
)
_TOOL_HIGH_IMPACT_TERMS = (
    "delete", "write", "edit", "bash", "shell", "terminal", "execute",
    "send ", "publish", "deploy", "network", "webfetch", "websearch",
    "删除", "写入", "编辑", "命令", "执行", "发送", "发布", "部署", "网络",
)
_TOOL_READ_ONLY_TASK_TERMS = (
    "only read", "read the", "read supplied", "summarize", "summarization",
    "summary task", "classify",
    "只需读取", "读取摘要", "只读", "摘要", "分类",
)
_TOOL_NO_APPROVAL_TERMS = (
    "without approval", "without asking", "act immediately", "无需批准",
    "无需询问", "立即执行",
)


def _tool_scope_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=["declared_tool_scope"],
        toolDeclarationCount=sum(text.count(x) for x in _TOOL_SCOPE_TERMS),
        approvalSignalCount=sum(text.count(x) for x in _TOOL_BOUNDARY_TERMS),
        highImpactToolSignalCount=sum(
            text.count(x) for x in _TOOL_HIGH_IMPACT_TERMS),
        readOnlyTaskSignalCount=sum(
            text.count(x) for x in _TOOL_READ_ONLY_TASK_TERMS),
        noApprovalSignalCount=sum(
            text.count(x) for x in _TOOL_NO_APPROVAL_TERMS),
    )


def _tool_scope_candidate_hints(metadata):
    if metadata.get("toolDeclarationCount", 0) == 0:
        return []
    if metadata.get("highImpactToolSignalCount", 0) == 0:
        return []
    if (metadata.get("approvalSignalCount", 0) > 0
            and metadata.get("noApprovalSignalCount", 0) == 0):
        return []
    if metadata.get("readOnlyTaskSignalCount", 0) == 0:
        return []
    return [_candidate_hint(
        {"scopeKind": "unnecessary_tool"},
        "A read-only task declares high-impact tools without an evidenced "
        "human approval or least-privilege boundary.")]


def _tool_scope_model_gate(metadata):
    if _tool_scope_candidate_hints(metadata):
        return True, "high_impact_tool_for_read_only_task"
    return False, "tool_scope_controls_or_no_high_impact_tool"


_BUDGET_PRESSURE_TERMS = (
    "detailed", "comprehensive", "exhaustive", "every ", "all ", "each ",
    "step-by-step", "逐一", "详细", "全面", "完整", "所有", "每个", "逐步",
)
_BUDGET_LIMIT_TERMS = (
    "brief", "concise", "short", "under ", "at most", "no more than",
    "token", "words", "characters", "简洁", "精简", "不超过", "以内",
    "字", "字符",
)
_PRIORITY_TERMS = (
    "prioritize", "priority", "omit first", "if space", "优先", "空间不足",
    "无法全部", "可省略",
)
_CONTINUATION_TERMS = (
    "continue", "continuation", "next response", "分段", "续写", "下一轮",
)


def _budget_metadata(text):
    _total, uncovered = _scoped_gap_count(
        text,
        signal_groups=(_BUDGET_PRESSURE_TERMS, _BUDGET_LIMIT_TERMS),
        control_terms=_PRIORITY_TERMS + _CONTINUATION_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["output_volume", "output_limit"],
        pressureSignalCount=sum(text.count(x) for x in _BUDGET_PRESSURE_TERMS),
        limitSignalCount=sum(text.count(x) for x in _BUDGET_LIMIT_TERMS),
        prioritySignalCount=sum(text.count(x) for x in _PRIORITY_TERMS),
        continuationSignalCount=sum(text.count(x) for x in _CONTINUATION_TERMS),
        uncoveredBudgetTradeoffCount=uncovered,
    )


def _budget_candidate_hints(metadata):
    if (metadata.get("pressureSignalCount", 0) == 0
            or metadata.get("limitSignalCount", 0) == 0):
        return []
    if metadata.get("uncoveredBudgetTradeoffCount", 0) == 0:
        return []
    return [_candidate_hint(
        {"pressureKind": "missing_priority"},
        "A detailed or exhaustive output is bounded by a short limit without "
        "an evidenced priority or continuation rule.")]


def _budget_model_gate(metadata):
    if _budget_candidate_hints(metadata):
        return True, "volume_limit_without_tradeoff_controls"
    return False, "budget_tradeoff_controls_present_or_unproven"


def extract_output_budget_pressure(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes,
        triggers=_BUDGET_PRESSURE_TERMS + _BUDGET_LIMIT_TERMS,
        require_all_groups=(_BUDGET_PRESSURE_TERMS, _BUDGET_LIMIT_TERMS),
        producer_id="extractor.prompt.output_budget_pressure",
        metadata_builder=_budget_metadata,
        candidate_hint_builder=_budget_candidate_hints,
        model_candidate_gate=_budget_model_gate)


_AUTONOMY_TERMS = (
    "autonomously", "without asking", "do not ask", "take initiative",
    "act immediately", "自行", "自主", "无需询问", "不要询问", "立即执行",
)
_SIDE_EFFECT_TERMS = (
    "send ", "publish", "deploy", "purchase", "delete", "transfer",
    "approve", "reject", "modify account", "发出", "发布", "部署", "购买",
    "删除", "转账", "批准", "拒绝", "修改账户",
)
_APPROVAL_TERMS = (
    "ask for approval", "require approval", "confirm with the user",
    "human approval", "draft only", "用户确认", "人工批准", "先请求批准",
    "仅生成草稿", "确认后",
)
_NO_APPROVAL_TERMS = (
    "without approval", "without asking", "do not ask", "immediately",
    "无需批准", "无需询问", "不要询问", "立即",
)


def _authority_metadata(text):
    actions = [
        name for name, terms in (
            ("communication", ("send ", "发出", "发送")),
            ("publication", ("publish", "发布")),
            ("deployment", ("deploy", "部署")),
            ("financial", ("purchase", "transfer", "购买", "转账")),
            ("destructive", ("delete", "删除")),
            ("access_control", ("approve", "reject", "修改账户", "批准", "拒绝")),
        ) if any(term in text for term in terms)
    ]
    _total, uncovered = _scoped_gap_count(
        text,
        signal_groups=(_AUTONOMY_TERMS, _SIDE_EFFECT_TERMS),
        control_terms=_APPROVAL_TERMS,
        defeating_terms=_NO_APPROVAL_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["autonomous_action", "external_side_effect"],
        autonomySignalCount=sum(text.count(x) for x in _AUTONOMY_TERMS),
        sideEffectSignalCount=sum(text.count(x) for x in _SIDE_EFFECT_TERMS),
        approvalSignalCount=sum(text.count(x) for x in _APPROVAL_TERMS),
        noApprovalSignalCount=sum(text.count(x) for x in _NO_APPROVAL_TERMS),
        uncoveredAutonomousActionCount=uncovered,
        operationKinds=actions,
    )


def _authority_candidate_hints(metadata):
    if (metadata.get("autonomySignalCount", 0) == 0
            or metadata.get("sideEffectSignalCount", 0) == 0):
        return []
    if metadata.get("uncoveredAutonomousActionCount", 0) == 0:
        return []
    return [_candidate_hint(
        {"authorityKind": "approval_boundary"},
        "A consequential autonomous side effect lacks an evidenced approval "
        "and scope boundary.")]


def _authority_model_gate(metadata):
    if _authority_candidate_hints(metadata):
        return True, "autonomous_side_effect_without_approval"
    return False, "approval_boundary_present_or_not_consequential"


def extract_authority_boundary_ambiguity(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes,
        triggers=_AUTONOMY_TERMS + _SIDE_EFFECT_TERMS,
        require_all_groups=(_AUTONOMY_TERMS, _SIDE_EFFECT_TERMS),
        producer_id="extractor.prompt.authority_boundary",
        metadata_builder=_authority_metadata,
        candidate_hint_builder=_authority_candidate_hints,
        model_candidate_gate=_authority_model_gate,
        system_prompt_only=True)


_FAILURE_OPERATION_TERMS = (
    "api", "http request", "http call", "http endpoint", "fetch",
    "retrieve", "search", "parse", "decode",
    "database", "tool call", "external service", "接口", "请求", "检索",
    "搜索", "解析", "解码", "数据库", "工具调用", "外部服务",
)
_FAILURE_STRATEGY_TERMS = (
    "timeout", "retry", "backoff", "fallback", "empty result",
    "malformed", "structured error", "partial failure", "超时", "重试",
    "退避", "回退", "空结果", "格式错误", "结构化错误", "部分失败",
)


def _failure_metadata(text):
    operations = [
        name for name, terms in (
            ("network_call", ("api", "http request", "http call",
                              "http endpoint", "fetch", "接口", "请求")),
            ("retrieval", ("retrieve", "search", "检索", "搜索")),
            ("parsing", ("parse", "decode", "解析", "解码")),
            ("database", ("database", "数据库")),
            ("tool_call", ("tool call", "工具调用")),
        ) if any(term in text for term in terms)
    ]
    strategies = [
        name for name, terms in (
            ("timeout", ("timeout", "超时")),
            ("retry", ("retry", "backoff", "重试", "退避")),
            ("fallback", ("fallback", "回退")),
            ("empty_result", ("empty result", "空结果")),
            ("malformed_input", ("malformed", "格式错误")),
            ("structured_error", ("structured error", "结构化错误")),
            ("partial_failure", ("partial failure", "部分失败")),
        ) if any(term in text for term in terms)
    ]
    _total, uncovered = _scoped_gap_count(
        text,
        signal_groups=(_FAILURE_OPERATION_TERMS,),
        control_terms=_FAILURE_STRATEGY_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["failure_prone_operation"],
        operationKinds=operations,
        strategyKinds=strategies,
        operationSignalCount=sum(text.count(x) for x in _FAILURE_OPERATION_TERMS),
        strategySignalCount=sum(text.count(x) for x in _FAILURE_STRATEGY_TERMS),
        uncoveredFailureOperationCount=uncovered,
    )


def _failure_candidate_hints(metadata):
    if metadata.get("operationSignalCount", 0) == 0:
        return []
    if metadata.get("uncoveredFailureOperationCount", 0) == 0:
        return []
    return [_candidate_hint(
        {"gapKind": "fallback"},
        "A required failure-prone operation lacks evidenced timeout, retry, "
        "fallback, malformed-input, or structured-error behavior.")]


def _failure_model_gate(metadata):
    if _failure_candidate_hints(metadata):
        return True, "failure_prone_operation_without_strategy"
    return False, "failure_strategy_present_or_unproven"


def extract_failure_strategy_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_FAILURE_OPERATION_TERMS,
        producer_id="extractor.prompt.failure_strategy_gap",
        metadata_builder=_failure_metadata,
        candidate_hint_builder=_failure_candidate_hints,
        model_candidate_gate=_failure_model_gate)


_VAGUE_CRITERIA_TERMS = (
    "appropriate", "reasonable", "as needed", "when necessary",
    "sufficiently", "high quality", "brief", "concise", "detailed",
    "comprehensive", "complex",
    "long content", "content is long", "适当", "合理", "酌情", "必要时",
    "尽量", "足够", "高质量", "简洁", "详细", "详尽", "复杂", "内容较长",
)
_BOUNDARY_CRITERIA_TERMS = (
    "at least", "at most", "exactly", "between ", "if ", "when ",
    "characters", "words", "items", "至少", "至多", "恰好", "介于",
    "如果", "当", "字", "条",
)
_VISUAL_STYLE_TERMS = (
    "photorealistic", "cinematic", "film still", "realistic actor",
    "natural lighting", "skin texture", "fabric detail", "visual style",
    "真人写实", "电影剧照", "真实演员", "实景光源", "皮肤纹理",
    "布料细节", "环境质感", "画风", "视觉风格",
)
_VISUAL_TASK_DIRECTIVES = (
    "create ", "generate ", "depict ", "show ", "render ",
    "生成", "创作", "描绘", "展示", "画出", "制作",
)
_VISUAL_SUBJECT_ANCHORS = (
    "subject:", "main subject", "a person", "a woman", "a man", "a child",
    "a product", "an object", "主体", "主角", "人物：", "角色：", "产品：",
    "一位", "一名", "一个", "一只", "一辆", "一座",
)


def _ambiguity_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=[
            "vague_operational_criterion",
            "task_context_completeness",
        ],
        vagueCriterionCount=sum(text.count(x) for x in _VAGUE_CRITERIA_TERMS),
        boundaryMarkerCount=sum(text.count(x) for x in _BOUNDARY_CRITERIA_TERMS),
        visualStyleSignalCount=sum(text.count(x) for x in _VISUAL_STYLE_TERMS),
        visualTaskDirectiveCount=sum(
            text.count(x) for x in _VISUAL_TASK_DIRECTIVES),
        visualSubjectAnchorCount=sum(
            text.count(x) for x in _VISUAL_SUBJECT_ANCHORS),
        promptCharacterCount=len(text),
    )


def _ambiguity_candidate_hints(metadata):
    hints = []
    if (
        metadata.get("visualStyleSignalCount", 0) >= 3
        and metadata.get("visualTaskDirectiveCount", 0) == 0
        and metadata.get("visualSubjectAnchorCount", 0) == 0
    ):
        hints.append(_candidate_hint(
            {"criterionKind": "missing_task_anchor"},
            "The reviewed prompt specifies a detailed visual style but does "
            "not identify a concrete generation task or primary subject."))
    if metadata.get("vagueCriterionCount", 0) == 0:
        return hints
    if metadata.get("boundaryMarkerCount", 0) < 2:
        hints.append(_candidate_hint(
            {"criterionKind": "undefined_boundary"},
            "A vague operational criterion controls behavior without an "
            "evidenced threshold, referent, example, or decision rule."))
    return hints


def _ambiguity_model_gate(metadata):
    if _ambiguity_candidate_hints(metadata):
        return True, "bounded_ambiguity_hypothesis"
    if (
        metadata.get("vagueCriterionCount", 0) > 0
        and metadata.get("boundaryMarkerCount", 0) >= 2
    ):
        return False, "vague_criterion_has_local_boundary"
    if (
        metadata.get("visualStyleSignalCount", 0) >= 3
        and metadata.get("visualTaskDirectiveCount", 0) > 0
        and metadata.get("visualSubjectAnchorCount", 0) > 0
    ):
        return False, "visual_task_anchors_present"
    if metadata.get("promptCharacterCount", 0) >= 24:
        return True, "general_ambiguity_review"
    return False, "prompt_too_short_for_general_ambiguity_review"


def extract_ambiguous_operational_criteria(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes,
        triggers=_VAGUE_CRITERIA_TERMS + _VISUAL_STYLE_TERMS,
        producer_id="extractor.prompt.ambiguous_operational_criteria",
        metadata_builder=_ambiguity_metadata,
        candidate_hint_builder=_ambiguity_candidate_hints,
        model_candidate_gate=_ambiguity_model_gate,
        allow_without_trigger=True)


_GROUNDING_TASK_TERMS = (
    "law", "legal", "medical", "health", "financial", "tax", "fact",
    "statistics", "citation", "source", "research", "法律", "医疗", "健康",
    "金融", "财务", "税务", "事实", "统计", "引用", "来源", "研究",
)
_GROUNDING_CONTROL_TERMS = (
    "cite", "verify", "source", "uncertain", "do not guess", "do not invent",
    "human review", "核实", "引用", "来源", "不确定", "不要猜测", "不得编造",
    "人工复核",
)


def _grounding_metadata(text):
    domains = [
        name for name, terms in (
            ("legal", ("law", "legal", "法律")),
            ("medical", ("medical", "health", "医疗", "健康")),
            ("financial", ("financial", "tax", "金融", "财务", "税务")),
            ("factual", ("fact", "statistics", "research", "事实", "统计", "研究")),
            ("citations", ("citation", "source", "引用", "来源")),
        ) if any(term in text for term in terms)
    ]
    _total, uncovered = _scoped_gap_count(
        text,
        signal_groups=(_GROUNDING_TASK_TERMS,),
        control_terms=_GROUNDING_CONTROL_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["consequential_or_verifiable_claim"],
        operationKinds=domains,
        groundingSignalCount=sum(text.count(x) for x in _GROUNDING_TASK_TERMS),
        mitigationSignalCount=sum(text.count(x) for x in _GROUNDING_CONTROL_TERMS),
        uncoveredGroundingTaskCount=uncovered,
    )


def _grounding_candidate_hints(metadata):
    if metadata.get("groundingSignalCount", 0) == 0:
        return []
    if metadata.get("uncoveredGroundingTaskCount", 0) == 0:
        return []
    return [_candidate_hint(
        {"groundingKind": "verification_required"},
        "A consequential factual task lacks evidenced source verification, "
        "uncertainty, anti-invention, or human-review controls.")]


def _grounding_model_gate(metadata):
    if _grounding_candidate_hints(metadata):
        return True, "grounding_task_without_controls"
    return False, "grounding_controls_present_or_unproven"


def extract_grounding_requirement_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_GROUNDING_TASK_TERMS,
        producer_id="extractor.prompt.grounding_requirement_gap",
        metadata_builder=_grounding_metadata,
        candidate_hint_builder=_grounding_candidate_hints,
        model_candidate_gate=_grounding_model_gate)


_REASONING_TERMS = (
    "chain of thought", "reasoning", "scratchpad", "internal policy",
    "hidden rule", "decision rule", "思维链", "推理过程", "思考过程",
    "内部策略", "隐藏规则", "内部规则", "判断规则",
)
_REASONING_EXPOSURE_TERMS = (
    "show", "reveal", "print", "include", "display", "展示", "公开",
    "输出", "透露", "包含",
)
_REASONING_CONTAINMENT_TERMS = (
    "do not reveal", "keep internal", "final answer only", "brief rationale",
    "private", "brief evidence-based rationale", "不要透露", "仅内部",
    "只输出最终", "简短理由",
)


def _reasoning_metadata(text):
    _total, uncovered = _scoped_gap_count(
        text,
        signal_groups=(_REASONING_TERMS, _REASONING_EXPOSURE_TERMS),
        control_terms=_REASONING_CONTAINMENT_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["reasoning_or_internal_policy"],
        reasoningSignalCount=sum(text.count(x) for x in _REASONING_TERMS),
        exposureSignalCount=sum(text.count(x) for x in _REASONING_EXPOSURE_TERMS),
        containmentSignalCount=sum(
            text.count(x) for x in _REASONING_CONTAINMENT_TERMS),
        uncoveredReasoningExposureCount=uncovered,
    )


def _reasoning_candidate_hints(metadata):
    if metadata.get("reasoningSignalCount", 0) == 0:
        return []
    if metadata.get("exposureSignalCount", 0) == 0:
        return []
    if metadata.get("uncoveredReasoningExposureCount", 0) == 0:
        return []
    return [_candidate_hint(
        {"exposureKind": "chain_of_thought"},
        "The prompt asks to expose chain-of-thought, scratchpad, or hidden "
        "internal policy without an evidenced containment rule.")]


def _reasoning_model_gate(metadata):
    if _reasoning_candidate_hints(metadata):
        return True, "reasoning_exposure_without_containment"
    return False, "reasoning_containment_present_or_no_exposure"


def extract_sensitive_reasoning_exposure(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_REASONING_TERMS,
        producer_id="extractor.prompt.sensitive_reasoning_exposure",
        metadata_builder=_reasoning_metadata,
        candidate_hint_builder=_reasoning_candidate_hints,
        model_candidate_gate=_reasoning_model_gate)


_VERIFICATION_TASK_TERMS = (
    "fields", "steps", "requirements", "must include", "schema",
    "title", "summary", "tags", "字段", "步骤", "要求", "必须包含",
    "标题", "摘要", "标签",
)
_VERIFICATION_CONTROL_TERMS = (
    "verify", "validate", "check before", "self-check", "checklist",
    "核对", "验证", "输出前检查", "自检", "检查清单",
)
_VERIFICATION_BYPASS_TERMS = (
    "without another review", "without another check", "applied directly",
    "publish the result directly", "直接应用", "直接执行", "无需复核",
    "不再检查", "直接发布",
)
_DOWNSTREAM_TERMS = (
    "downstream", "parser", "automation", "production", "decision",
    "下游", "解析器", "自动化", "生产", "决策",
)


def _verification_metadata(text):
    _total, uncovered = _scoped_gap_count(
        text,
        signal_groups=(
            _VERIFICATION_TASK_TERMS,
            _DOWNSTREAM_TERMS + _VERIFICATION_BYPASS_TERMS,
        ),
        control_terms=_VERIFICATION_CONTROL_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["multi_constraint_output"],
        requirementSignalCount=sum(text.count(x) for x in _VERIFICATION_TASK_TERMS),
        verificationSignalCount=sum(
            text.count(x) for x in _VERIFICATION_CONTROL_TERMS),
        downstreamSignalCount=sum(text.count(x) for x in _DOWNSTREAM_TERMS),
        bypassReviewSignalCount=sum(
            text.count(x) for x in _VERIFICATION_BYPASS_TERMS),
        uncoveredVerificationRequirementCount=uncovered,
    )


def _verification_candidate_hints(metadata):
    consequential = (
        metadata.get("downstreamSignalCount", 0) > 0
        or metadata.get("bypassReviewSignalCount", 0) > 0)
    if (metadata.get("requirementSignalCount", 0) > 0
            and consequential
            and metadata.get("uncoveredVerificationRequirementCount", 0) > 0):
        return [_candidate_hint(
            {"verificationKind": "downstream_validity"},
            "A constrained output is used downstream without an evidenced "
            "validation step in the complete reviewed prompt.")]
    return []


def _verification_model_gate(metadata):
    if metadata.get("uncoveredVerificationRequirementCount", 0) > 0:
        return True, "missing_downstream_validation_controls"
    consequential = (
        metadata.get("downstreamSignalCount", 0) > 0
        or metadata.get("bypassReviewSignalCount", 0) > 0)
    if not consequential or metadata.get("requirementSignalCount", 0) == 0:
        return False, "not_consequential_constrained_output"
    return False, "verification_controls_present"


def extract_verification_step_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_VERIFICATION_TASK_TERMS,
        producer_id="extractor.prompt.verification_step_gap",
        metadata_builder=_verification_metadata,
        candidate_hint_builder=_verification_candidate_hints,
        model_candidate_gate=_verification_model_gate)


_INPUT_DEPENDENCY_TERMS = (
    "required input", "input field", "input fields", "request parameter",
    "user provides", "user-provided", "form field", "request body",
    "必填输入", "输入字段", "请求参数", "用户提供", "表单字段", "请求体",
)
_INPUT_REQUIREDNESS_TERMS = (
    "required", "optional", "must provide", "may omit", "必填", "选填",
    "必须提供", "可以省略",
)
_INPUT_DEFAULT_TERMS = (
    "default", "assume", "clarify", "ask the user", "if missing",
    "use null", "reject the request", "默认", "假设", "追问", "询问用户",
    "缺失时", "使用 null", "拒绝请求",
)
_INPUT_INVALID_TERMS = (
    "empty", "malformed", "invalid", "oversized", "too long",
    "unsupported", "空输入", "格式错误", "无效", "超长", "不支持",
)
_INPUT_HANDLING_TERMS = (
    "return an error", "structured error", "request clarification",
    "do not guess", "normalize", "validate", "返回错误", "结构化错误",
    "请求补充", "不得猜测", "规范化", "校验",
)


def _input_contract_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=["declared_input_dependency"],
        inputSignalCount=sum(text.count(x) for x in _INPUT_DEPENDENCY_TERMS),
        requirednessSignalCount=sum(
            text.count(x) for x in _INPUT_REQUIREDNESS_TERMS),
        defaultSignalCount=sum(text.count(x) for x in _INPUT_DEFAULT_TERMS),
        invalidInputSignalCount=sum(
            text.count(x) for x in _INPUT_INVALID_TERMS),
        handlingSignalCount=sum(
            text.count(x) for x in _INPUT_HANDLING_TERMS),
    )


def _input_contract_candidate_hints(metadata):
    if metadata.get("inputSignalCount", 0) == 0:
        return []
    if metadata.get("requirednessSignalCount", 0) == 0:
        return [_candidate_hint(
            {"gapKind": "missing_input"},
            "A required input dependency lacks evidenced required or "
            "optional status.")]
    if metadata.get("defaultSignalCount", 0) == 0:
        return [_candidate_hint(
            {"gapKind": "default_behavior"},
            "A required input dependency lacks evidenced missing-value or "
            "default behavior.")]
    if (metadata.get("invalidInputSignalCount", 0) == 0
            or metadata.get("handlingSignalCount", 0) == 0):
        return [_candidate_hint(
            {"gapKind": "invalid_input"},
            "A required input dependency lacks evidenced invalid-input "
            "handling.")]
    return []


def _input_contract_model_gate(metadata):
    if _input_contract_candidate_hints(metadata):
        return True, "input_dependency_missing_contract_controls"
    return False, "input_contract_controls_complete_or_unproven"


def extract_input_and_default_contract_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_INPUT_DEPENDENCY_TERMS,
        producer_id="extractor.prompt.input_and_default_contract_gap",
        metadata_builder=_input_contract_metadata,
        candidate_hint_builder=_input_contract_candidate_hints,
        model_candidate_gate=_input_contract_model_gate)


_EXAMPLE_TERMS = (
    "example", "examples", "few-shot", "few shot", "sample input",
    "sample output", "示例", "样例", "输入样本", "输出样本",
)
_EXAMPLE_RULE_TERMS = (
    "must", "required", "always", "never", "schema", "format",
    "field", "enum", "必须", "应当", "始终", "不得", "结构", "格式",
    "字段", "枚举",
)
_EXAMPLE_BOUNDARY_TERMS = (
    "boundary", "edge case", "minimum", "maximum", "empty",
    "边界", "极值", "最小", "最大", "空输入",
)
_EXAMPLE_FAILURE_TERMS = (
    "error example", "failure example", "invalid example", "rejection example",
    "错误示例", "失败示例", "无效示例", "拒绝示例",
)
_EXAMPLE_QUALITY_TERMS = (
    "representative", "input distribution", "real distribution", "outdated",
    "stale example", "positive example", "negative example", "counterexample",
    "有代表性", "输入分布", "真实分布", "过时", "陈旧", "正例", "反例",
)


def _required_example_fields(text):
    fields = set()
    plural = re.search(r"\brequired\s+fields?\s+([^.\n]+)", text)
    if plural:
        ignored = {
            "a", "an", "and", "field", "fields", "json", "or", "the",
        }
        fields.update(
            token for token in re.findall(
                r"\b[a-z_][a-z0-9_-]*\b", plural.group(1))
            if token not in ignored
        )
    fields.update(re.findall(
        r"\brequired\s+[\"']([a-z_][a-z0-9_-]*)[\"']\s+field\b",
        text))
    return fields


def _first_example_object_keys(text):
    marker = re.search(r"\b(?:example|sample output)\b", text)
    if not marker:
        return set()
    for raw in re.findall(r"\{[^{}\n]{1,2000}\}", text[marker.end():]):
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            return {
                key for key in value
                if isinstance(key, str) and re.fullmatch(
                    r"[a-z_][a-z0-9_-]*", key)
            }
    return set()


def _example_contract_metadata(text):
    violations = []
    if (re.search(r"\b(?:never|do not|must not)\s+output\b.{0,40}\bemail\b",
                  text)
            and re.search(
                r"\b[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
                r"[a-z0-9.-]+\.[a-z]{2,}\b",
                text)):
        violations.append("prohibited_email_disclosed")
    enum_rule = re.search(
        r"(?:field\s+)?([a-z_][a-z0-9_-]*)\s+must\s+be\s+one\s+of\s+"
        r"([^.\n]+)",
        text)
    if enum_rule:
        field_name = enum_rule.group(1)
        allowed = {
            value for value in re.findall(
                r"\b[a-z][a-z0-9_-]*\b", enum_rule.group(2))
            if value not in {"or", "and"}
        }
        example = re.search(
            rf'["\']{re.escape(field_name)}["\']\s*:\s*'
            r'["\']([^"\']+)["\']',
            text)
        if example and example.group(1) not in allowed:
            violations.append("enum_value_outside_allowed_set")
    required_fields = _required_example_fields(text)
    example_fields = _first_example_object_keys(text)
    if required_fields and example_fields and not required_fields <= example_fields:
        violations.append("required_fields_omitted")
    return _prompt_analysis_metadata(
        signal_families=["normative_examples"],
        strategyKinds=violations,
        normativeExampleViolationCount=len(violations),
        exampleSignalCount=sum(text.count(x) for x in _EXAMPLE_TERMS),
        ruleSignalCount=sum(text.count(x) for x in _EXAMPLE_RULE_TERMS),
        boundaryExampleSignalCount=sum(
            text.count(x) for x in _EXAMPLE_BOUNDARY_TERMS),
        failureExampleSignalCount=sum(
            text.count(x) for x in _EXAMPLE_FAILURE_TERMS),
        exampleQualitySignalCount=sum(
            text.count(x) for x in _EXAMPLE_QUALITY_TERMS),
    )


def _example_contract_candidate_hints(metadata):
    kinds = metadata.get("strategyKinds") or []
    if not kinds:
        return []
    gap_kind = (
        "schema_mismatch"
        if kinds[0] in {
            "enum_value_outside_allowed_set",
            "required_fields_omitted",
        }
        else "rule_mismatch"
    )
    return [_candidate_hint(
        {"exampleGapKind": gap_kind},
        "A normative example directly violates an evidenced prohibition "
        "or allowed-value contract.")]


def _example_contract_model_gate(metadata):
    if _example_contract_candidate_hints(metadata):
        return True, "structured_example_rule_violation"
    return False, "no_structured_example_rule_violation"


def extract_example_contract_mismatch(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes,
        triggers=_EXAMPLE_TERMS,
        producer_id="extractor.prompt.example_contract_mismatch",
        metadata_builder=_example_contract_metadata,
        candidate_hint_builder=_example_contract_candidate_hints,
        model_candidate_gate=_example_contract_model_gate)


_TOOL_CALL_TERMS = (
    "tool call", "function call", "call the api", "api call",
    "invoke the tool", "invoke the function", "工具调用", "函数调用",
    "调用 api", "调用工具", "调用函数",
)
_TOOL_INVOCATION_TERMS = (
    "when to call", "call only when", "precondition", "trigger condition",
    "whenever", "only when", "何时调用", "仅当", "前置条件", "触发条件",
)
_TOOL_PARAMETER_TERMS = (
    "parameter", "argument", "json schema", "parameter source",
    "参数", "入参", "参数 schema", "参数来源",
)
_TOOL_PARAMETER_CONTROL_TERMS = (
    "validated request", "validate the parameter", "validate parameters",
    "trusted source", "registered json schema", "allowlist",
    "经过校验的请求", "校验参数", "验证参数", "可信来源", "已注册 schema",
    "白名单",
)
_TOOL_RESULT_TERMS = (
    "return schema", "result schema", "tool result", "response field",
    "返回结构", "结果结构", "工具结果", "响应字段",
)


def _tool_contract_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=["required_tool_invocation"],
        toolCallSignalCount=sum(text.count(x) for x in _TOOL_CALL_TERMS),
        invocationSignalCount=sum(
            text.count(x) for x in _TOOL_INVOCATION_TERMS),
        parameterSignalCount=sum(
            text.count(x) for x in _TOOL_PARAMETER_TERMS),
        parameterControlSignalCount=sum(
            text.count(x) for x in _TOOL_PARAMETER_CONTROL_TERMS),
        resultContractSignalCount=sum(
            text.count(x) for x in _TOOL_RESULT_TERMS),
        strategySignalCount=sum(
            text.count(x) for x in _FAILURE_STRATEGY_TERMS),
    )


def _tool_contract_candidate_hints(metadata):
    if metadata.get("toolCallSignalCount", 0) == 0:
        return []
    hints = []
    if metadata.get("invocationSignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"contractGapKind": "invocation_condition"},
            "A required tool invocation has no bounded invocation condition "
            "in the complete reviewed prompt."))
    if metadata.get("parameterControlSignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"contractGapKind": "parameter_provenance"},
            "A required tool invocation lacks validated parameter provenance "
            "in the complete reviewed prompt."))
    if metadata.get("resultContractSignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"contractGapKind": "result_schema"},
            "A required tool invocation has no downstream result contract "
            "in the complete reviewed prompt."))
    if metadata.get("strategySignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"contractGapKind": "error_handling"},
            "A required tool invocation has no bounded failure behavior "
            "in the complete reviewed prompt."))
    return hints[:1]


def _tool_contract_model_gate(metadata):
    if _tool_contract_candidate_hints(metadata):
        return True, "tool_call_missing_contract_controls"
    return False, "tool_call_contract_controls_complete_or_unproven"


def extract_tool_call_contract_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_TOOL_CALL_TERMS,
        producer_id="extractor.prompt.tool_call_contract_gap",
        metadata_builder=_tool_contract_metadata,
        candidate_hint_builder=_tool_contract_candidate_hints,
        model_candidate_gate=_tool_contract_model_gate)


_CAPABILITY_DEPENDENCY_TERMS = (
    "real-time", "latest information", "current price", "browse the web",
    "web access", "vision", "analyze the image", "audio input",
    "persistent memory", "context window", "plugin", "browser tool",
    "实时", "最新信息", "当前价格", "浏览网页", "联网", "视觉",
    "分析图片", "音频输入", "持久记忆", "上下文窗口", "插件", "浏览器工具",
)
_CAPABILITY_PROVISION_TERMS = (
    "provided tool", "tool is available", "using the supplied",
    "input includes", "attached image", "retrieved results", "提供的工具",
    "工具可用", "使用给定", "输入包含", "已附图片", "检索结果",
)
_CAPABILITY_FALLBACK_TERMS = (
    "if unavailable", "fallback", "ask the user to provide",
    "state that it is unavailable", "无法使用时", "回退", "请用户提供",
    "说明无法获取",
)


def _capability_dependency_metadata(text):
    kinds = [
        name for name, terms in (
            ("realtime", ("real-time", "latest information", "current price",
                          "实时", "最新信息", "当前价格")),
            ("web", ("browse the web", "web access", "browser tool",
                     "浏览网页", "联网", "浏览器工具")),
            ("vision", ("vision", "analyze the image", "视觉", "分析图片")),
            ("audio", ("audio input", "音频输入")),
            ("memory", ("persistent memory", "持久记忆")),
            ("context", ("context window", "上下文窗口")),
            ("plugin", ("plugin", "插件")),
        ) if any(term in text for term in terms)
    ]
    return _prompt_analysis_metadata(
        signal_families=["non_intrinsic_model_capability"],
        operationKinds=kinds,
        capabilitySignalCount=sum(
            text.count(x) for x in _CAPABILITY_DEPENDENCY_TERMS),
        provisionSignalCount=sum(
            text.count(x) for x in _CAPABILITY_PROVISION_TERMS),
        fallbackSignalCount=sum(
            text.count(x) for x in _CAPABILITY_FALLBACK_TERMS),
    )


def _capability_dependency_candidate_hints(metadata):
    if metadata.get("capabilitySignalCount", 0) == 0:
        return []
    if (metadata.get("provisionSignalCount", 0) > 0
            or metadata.get("fallbackSignalCount", 0) > 0):
        return []
    kinds = metadata.get("operationKinds") or []
    dependency = "web_access"
    if "realtime" in kinds:
        dependency = "realtime_data"
    elif "vision" in kinds:
        dependency = "vision"
    elif "audio" in kinds:
        dependency = "audio"
    elif "memory" in kinds:
        dependency = "persistent_memory"
    elif "context" in kinds:
        dependency = "context_capacity"
    elif "plugin" in kinds:
        dependency = "plugin"
    return [_candidate_hint(
        {"dependencyKind": dependency},
        "The task requires a non-intrinsic model capability without an "
        "evidenced supplied tool, input, platform guarantee, or fallback.")]


def _capability_dependency_model_gate(metadata):
    if _capability_dependency_candidate_hints(metadata):
        return True, "required_capability_without_provider_or_fallback"
    return False, "capability_provider_or_fallback_present"


def extract_capability_dependency_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_CAPABILITY_DEPENDENCY_TERMS,
        producer_id="extractor.prompt.capability_dependency_gap",
        metadata_builder=_capability_dependency_metadata,
        candidate_hint_builder=_capability_dependency_candidate_hints,
        model_candidate_gate=_capability_dependency_model_gate)


_SENSITIVE_DATA_TERMS = (
    "personal data", "personal information", "pii", "email address",
    "phone number", "home address", "medical record", "financial account",
    "credential", "api key", "个人数据", "个人信息", "邮箱地址", "电话号码",
    "家庭住址", "医疗记录", "金融账户", "凭据", "密钥",
)
_SENSITIVE_DATA_ACTION_TERMS = (
    "collect", "store", "retain", "send", "share", "display", "output",
    "process", "summarize", "收集", "存储", "保留", "发送", "共享",
    "展示", "输出", "处理", "总结",
)
_SENSITIVE_DATA_CONTROL_TERMS = (
    "minimize", "redact", "mask", "consent", "authorized", "access control",
    "retention period", "do not expose", "最小化", "脱敏", "掩码", "同意",
    "授权", "访问控制", "保留期限", "不得泄露",
)
_SENSITIVE_MINIMIZATION_TERMS = (
    "minimum", "minimize", "only needed", "最少", "最小化", "仅收集必要",
)
_SENSITIVE_REDACTION_TERMS = (
    "redact", "mask", "do not expose", "脱敏", "掩码", "不得泄露",
)
_SENSITIVE_AUTHORIZATION_TERMS = (
    "consent", "authorized", "access control", "同意", "授权", "访问控制",
)
_SENSITIVE_RETENTION_TERMS = (
    "retention period", "delete after", "retention limit",
    "保留期限", "到期删除", "保留限制",
)
_SENSITIVE_OUTBOUND_ACTION_TERMS = (
    "send", "share", "display", "output", "发送", "共享", "展示", "输出",
)
_SENSITIVE_COLLECTION_ACTION_TERMS = (
    "collect", "store", "retain", "process", "收集", "存储", "保留", "处理",
)


def _sensitive_data_metadata(text):
    kinds = [
        name for name, terms in (
            ("identity", ("personal data", "personal information", "pii",
                          "个人数据", "个人信息")),
            ("contact", ("email address", "phone number", "home address",
                         "邮箱地址", "电话号码", "家庭住址")),
            ("medical", ("medical record", "医疗记录")),
            ("financial", ("financial account", "金融账户")),
            ("credential", ("credential", "api key", "凭据", "密钥")),
        ) if any(term in text for term in terms)
    ]
    return _prompt_analysis_metadata(
        signal_families=["sensitive_data_handling"],
        operationKinds=kinds,
        sensitiveDataSignalCount=sum(
            text.count(x) for x in _SENSITIVE_DATA_TERMS),
        dataActionSignalCount=sum(
            text.count(x) for x in _SENSITIVE_DATA_ACTION_TERMS),
        dataControlSignalCount=sum(
            text.count(x) for x in _SENSITIVE_DATA_CONTROL_TERMS),
        minimizationSignalCount=sum(
            text.count(x) for x in _SENSITIVE_MINIMIZATION_TERMS),
        redactionSignalCount=sum(
            text.count(x) for x in _SENSITIVE_REDACTION_TERMS),
        authorizationControlSignalCount=sum(
            text.count(x) for x in _SENSITIVE_AUTHORIZATION_TERMS),
        retentionControlSignalCount=sum(
            text.count(x) for x in _SENSITIVE_RETENTION_TERMS),
        outboundDisclosureSignalCount=sum(
            text.count(x) for x in _SENSITIVE_OUTBOUND_ACTION_TERMS),
        collectionStorageSignalCount=sum(
            text.count(x) for x in _SENSITIVE_COLLECTION_ACTION_TERMS),
    )


def _sensitive_data_candidate_hints(metadata):
    if (metadata.get("sensitiveDataSignalCount", 0) == 0
            or metadata.get("dataActionSignalCount", 0) == 0):
        return []
    hints = []
    if (metadata.get("outboundDisclosureSignalCount", 0) > 0
            and metadata.get("redactionSignalCount", 0) == 0):
        hints.append(_candidate_hint(
            {"dataPolicyKind": "redaction"},
            "The prompt directs sensitive-data disclosure without an "
            "evidenced masking or redaction boundary."))
    if (metadata.get("collectionStorageSignalCount", 0) > 0
            and metadata.get("minimizationSignalCount", 0) == 0):
        hints.append(_candidate_hint(
            {"dataPolicyKind": "minimization"},
            "The prompt directs sensitive-data collection or storage without "
            "an evidenced minimization boundary."))
    if metadata.get("authorizationControlSignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"dataPolicyKind": "authorization"},
            "The prompt directs sensitive-data handling without an evidenced "
            "authorization boundary."))
    if (metadata.get("collectionStorageSignalCount", 0) > 0
            and metadata.get("retentionControlSignalCount", 0) == 0):
        hints.append(_candidate_hint(
            {"dataPolicyKind": "retention"},
            "The prompt directs sensitive-data storage without an evidenced "
            "retention boundary."))
    return hints[:1]


def _sensitive_data_model_gate(metadata):
    if _sensitive_data_candidate_hints(metadata):
        return True, "sensitive_data_handling_missing_controls"
    return False, "sensitive_data_controls_complete_or_action_unproven"


def extract_sensitive_data_handling_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes,
        triggers=_SENSITIVE_DATA_TERMS + _SENSITIVE_DATA_ACTION_TERMS,
        require_all_groups=(_SENSITIVE_DATA_TERMS,
                            _SENSITIVE_DATA_ACTION_TERMS),
        producer_id="extractor.prompt.sensitive_data_handling_gap",
        metadata_builder=_sensitive_data_metadata,
        candidate_hint_builder=_sensitive_data_candidate_hints,
        model_candidate_gate=_sensitive_data_model_gate)


_ROLE_IDENTITY_TERMS = (
    "you are", "act as", "your role", "persona", "assistant for",
    "你是", "作为", "你的角色", "角色身份", "助手",
)
_ROLE_AUDIENCE_TERMS = (
    "audience", "serve", "for users", "customer", "operator",
    "learner", "learners", "account holders", "retail learners",
    "面向", "服务对象", "用户", "客户", "操作员", "学习者",
)
_ROLE_DUTY_TERMS = (
    "responsible for", "duties", "responsibility", "can help", "must handle",
    "explain", "draft", "负责", "职责", "责任", "可以帮助", "必须处理",
    "解释", "起草",
)
_ROLE_EXCLUSION_TERMS = (
    "out of scope", "cannot", "must not", "do not", "refuse", "escalate",
    "范围外", "不能", "不得", "不要", "拒绝", "转交",
)


def _role_scope_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=["operational_role"],
        roleSignalCount=sum(text.count(x) for x in _ROLE_IDENTITY_TERMS),
        audienceSignalCount=sum(text.count(x) for x in _ROLE_AUDIENCE_TERMS),
        dutySignalCount=sum(text.count(x) for x in _ROLE_DUTY_TERMS),
        exclusionSignalCount=sum(
            text.count(x) for x in _ROLE_EXCLUSION_TERMS),
    )


def _role_scope_candidate_hints(metadata):
    if metadata.get("roleSignalCount", 0) == 0:
        return []
    if metadata.get("exclusionSignalCount", 0) == 0:
        return [_candidate_hint(
            {"roleGapKind": "exclusions"},
            "A persistent operational role lacks evidenced out-of-scope, "
            "refusal, or escalation boundaries.")]
    if metadata.get("audienceSignalCount", 0) == 0:
        return [_candidate_hint(
            {"roleGapKind": "audience"},
            "A persistent operational role lacks an evidenced audience or "
            "request-routing boundary.")]
    if metadata.get("dutySignalCount", 0) == 0:
        return [_candidate_hint(
            {"roleGapKind": "duties"},
            "A persistent operational role lacks evidenced material duties.")]
    return []


def _role_scope_model_gate(metadata):
    if _role_scope_candidate_hints(metadata):
        return True, "operational_role_missing_scope_controls"
    return False, "role_scope_controls_complete_or_unproven"


def extract_role_scope_contract_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_ROLE_IDENTITY_TERMS,
        producer_id="extractor.prompt.role_scope_contract_gap",
        metadata_builder=_role_scope_metadata,
        candidate_hint_builder=_role_scope_candidate_hints,
        model_candidate_gate=_role_scope_model_gate)


_WORKFLOW_TERMS = (
    "step 1", "step one", "first,", "then ", "finally", "workflow",
    "process", "pipeline", "步骤 1", "第一步", "首先", "然后", "最后",
    "流程", "工作流",
)
_WORKFLOW_DEPENDENCY_TERMS = (
    "before", "after", "depends on", "requires", "prerequisite",
    "using the result", "前置", "之前", "之后", "依赖", "需要", "使用结果",
)
_WORKFLOW_RESULT_TERMS = (
    "intermediate result", "pass to", "feed into", "use the output",
    "中间结果", "传递给", "输入下一步", "使用输出",
)
_WORKFLOW_BRANCH_TERMS = (
    "otherwise", "if it fails", "skip", "stop", "else",
    "否则", "失败时", "跳过", "停止",
)
_WORKFLOW_SIDE_EFFECT_TERMS = (
    "publish", "deploy", "notify", "send ", "delete", "transfer",
    "发布", "部署", "通知", "发送", "删除", "转账",
)
_WORKFLOW_VALIDATION_TERMS = (
    "validate", "verify", "acceptance test", "acceptance tests",
    "checksum", "checksums", "test", "tests", "检查", "验证", "校验", "测试",
)
_WORKFLOW_PREPARATION_TERMS = (
    "build", "import", "generate", "produce", "calculate", "create",
    "构建", "导入", "生成", "计算", "创建",
)


def _first_term_index(text: str, terms: Tuple[str, ...]) -> int:
    indexes = [text.find(term) for term in terms if text.find(term) >= 0]
    return min(indexes) if indexes else -1


def _workflow_dependency_metadata(text):
    side_effect_index = _first_term_index(text, _WORKFLOW_SIDE_EFFECT_TERMS)
    validation_index = _first_term_index(text, _WORKFLOW_VALIDATION_TERMS)
    preparation_index = _first_term_index(text, _WORKFLOW_PREPARATION_TERMS)
    side_effect_before_validation = (
        side_effect_index >= 0 and validation_index >= 0
        and side_effect_index < validation_index)
    side_effect_before_preparation = (
        side_effect_index >= 0 and preparation_index >= 0
        and side_effect_index < preparation_index)
    return _prompt_analysis_metadata(
        signal_families=["multi_step_workflow"],
        workflowSignalCount=sum(text.count(x) for x in _WORKFLOW_TERMS),
        dependencySignalCount=sum(
            text.count(x) for x in _WORKFLOW_DEPENDENCY_TERMS),
        intermediateResultSignalCount=sum(
            text.count(x) for x in _WORKFLOW_RESULT_TERMS),
        workflowBranchSignalCount=sum(
            text.count(x) for x in _WORKFLOW_BRANCH_TERMS),
        sideEffectBeforeValidationSignalCount=(
            1 if side_effect_before_validation else 0),
        sideEffectBeforePreparationSignalCount=(
            1 if side_effect_before_preparation else 0),
    )


def _workflow_dependency_candidate_hints(metadata):
    if metadata.get("workflowSignalCount", 0) == 0:
        return []
    if metadata.get("sideEffectBeforeValidationSignalCount", 0) > 0:
        return [_candidate_hint(
            {"dependencyGapKind": "reversed_order"},
            "A side-effect step appears before the validation or acceptance "
            "step that should gate it.")]
    if metadata.get("sideEffectBeforePreparationSignalCount", 0) > 0:
        return [_candidate_hint(
            {"dependencyGapKind": "missing_prerequisite"},
            "A side-effect step appears before the preparation step that "
            "should produce its required input.")]
    return []


def _workflow_dependency_model_gate(metadata):
    if _workflow_dependency_candidate_hints(metadata):
        return True, "workflow_side_effect_before_prerequisite"
    return False, "workflow_dependencies_appear_ordered_or_unproven"


def extract_workflow_dependency_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_WORKFLOW_TERMS,
        producer_id="extractor.prompt.workflow_dependency_gap",
        metadata_builder=_workflow_dependency_metadata,
        candidate_hint_builder=_workflow_dependency_candidate_hints,
        model_candidate_gate=_workflow_dependency_model_gate)


_FIELD_CONTRACT_TERMS = (
    "field", "fields", "amount", "date", "timestamp", "status", "enum",
    "integer", "decimal", "字段", "金额", "日期", "时间戳", "状态",
    "枚举", "整数", "小数",
)
_FIELD_TYPE_TERMS = (
    "string", "number", "integer", "boolean", "array", "list", "object",
    "type", "类型", "字符串", "数字", "整数", "布尔", "数组", "列表",
    "对象",
)
_FIELD_UNIT_PRECISION_TERMS = (
    "unit", "precision", "decimal places", "currency", "timezone",
    "单位", "精度", "小数位", "币种", "时区",
)
_FIELD_RANGE_TERMS = (
    "range", "minimum", "maximum", "between", "one of", "enum",
    "at most", "up to", "no more than", "范围", "最小", "最大", "介于",
    "取值", "枚举", "不超过", "至多",
)
_FIELD_BOUNDARY_TERMS = (
    "empty", "null", "duplicate", "unique", "missing", "omitted",
    "rollover", "overflow", "zero",
    "空值", "空输入", "重复", "跨日", "溢出", "零",
)
_FIELD_MACHINE_CONSUMER_TERMS = (
    "json", "schema", "parser", "downstream", "automation", "api",
    "request body", "csv", "database", "机器", "解析器", "下游", "自动化",
    "接口", "数据库",
)


def _field_constraint_metadata(text):
    numeric_ranges = len(re.findall(
        r"\b\d+\s*(?:-|–|to)\s*\d+\b", text, flags=re.IGNORECASE))
    return _prompt_analysis_metadata(
        signal_families=["typed_or_bounded_field"],
        fieldSignalCount=sum(text.count(x) for x in _FIELD_CONTRACT_TERMS),
        machineConsumerSignalCount=sum(
            text.count(x) for x in _FIELD_MACHINE_CONSUMER_TERMS),
        fieldTypeSignalCount=sum(text.count(x) for x in _FIELD_TYPE_TERMS),
        unitPrecisionSignalCount=sum(
            text.count(x) for x in _FIELD_UNIT_PRECISION_TERMS),
        rangeSignalCount=(
            sum(text.count(x) for x in _FIELD_RANGE_TERMS) + numeric_ranges),
        boundaryValueSignalCount=sum(
            text.count(x) for x in _FIELD_BOUNDARY_TERMS),
    )


def _field_constraint_candidate_hints(metadata):
    material_field = (
        metadata.get("fieldSignalCount", 0) >= 2
        or metadata.get("machineConsumerSignalCount", 0) > 0)
    if not material_field:
        return []
    hints = []
    if (metadata.get("fieldTypeSignalCount", 0) == 0
            and metadata.get("unitPrecisionSignalCount", 0) == 0):
        hints.append(_candidate_hint(
            {"fieldGapKind": "type_or_unit"},
            "Named machine-consumed fields lack type or unit constraints "
            "in the complete reviewed prompt."))
    if metadata.get("rangeSignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"fieldGapKind": "enum_or_range"},
            "Named machine-consumed fields lack enum or range constraints "
            "in the complete reviewed prompt."))
    if metadata.get("boundaryValueSignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"fieldGapKind": "boundary_behavior"},
            "Named machine-consumed fields lack material boundary behavior "
            "in the complete reviewed prompt."))
    return hints[:1]


def _field_constraint_model_gate(metadata):
    if _field_constraint_candidate_hints(metadata):
        return True, "material_field_missing_constraints"
    return False, "field_constraints_complete_or_not_machine_consumed"


def extract_field_constraint_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_FIELD_CONTRACT_TERMS,
        producer_id="extractor.prompt.field_constraint_gap",
        metadata_builder=_field_constraint_metadata,
        candidate_hint_builder=_field_constraint_candidate_hints,
        model_candidate_gate=_field_constraint_model_gate)


_ERROR_RESPONSE_TERMS = (
    "error response", "on error", "if invalid", "cannot complete",
    "permission denied", "refuse", "failure response", "错误响应", "出错时",
    "无效时", "无法完成", "权限不足", "拒绝", "失败响应",
)
_ERROR_SCHEMA_TERMS = (
    "error code", "reason field", "error field", "json error", "schema",
    "错误码", "原因字段", "error 字段", "错误结构",
)
_ERROR_RECOVERY_TERMS = (
    "retry", "recover", "next action", "request clarification",
    "重试", "恢复", "下一步", "请求补充",
)
_ERROR_FORMAT_TERMS = (
    "same format", "consistent format", "uniform", "stable format",
    "相同格式", "一致格式", "统一", "稳定格式",
)


def _error_response_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=["declared_failure_response"],
        errorResponseSignalCount=sum(
            text.count(x) for x in _ERROR_RESPONSE_TERMS),
        machineConsumerSignalCount=sum(
            text.count(x) for x in _FIELD_MACHINE_CONSUMER_TERMS),
        errorSchemaSignalCount=sum(text.count(x) for x in _ERROR_SCHEMA_TERMS),
        recoverySignalCount=sum(text.count(x) for x in _ERROR_RECOVERY_TERMS),
        errorFormatSignalCount=sum(text.count(x) for x in _ERROR_FORMAT_TERMS),
    )


def _error_response_candidate_hints(metadata):
    if (metadata.get("errorResponseSignalCount", 0) == 0
            or metadata.get("machineConsumerSignalCount", 0) == 0):
        return []
    hints = []
    if metadata.get("errorSchemaSignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"errorGapKind": "schema"},
            "Declared failure behavior lacks a stable response schema "
            "in the complete reviewed prompt."))
    if metadata.get("recoverySignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"errorGapKind": "recoverability"},
            "Declared failure behavior does not tell the caller whether to "
            "retry, clarify, or stop."))
    if metadata.get("errorFormatSignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"errorGapKind": "format_consistency"},
            "Declared failure classes lack a consistent output format "
            "in the complete reviewed prompt."))
    return hints[:1]


def _error_response_model_gate(metadata):
    if _error_response_candidate_hints(metadata):
        return True, "failure_response_missing_contract_controls"
    return False, "error_response_controls_complete_or_unproven"


def extract_error_response_contract_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_ERROR_RESPONSE_TERMS,
        producer_id="extractor.prompt.error_response_contract_gap",
        metadata_builder=_error_response_metadata,
        candidate_hint_builder=_error_response_candidate_hints,
        model_candidate_gate=_error_response_model_gate)


_ATTENTION_STRUCTURE_TERMS = (
    "## background", "## appendix", "background material", "appendix",
    "long prompt", "reference material", "critical rule", "背景材料",
    "附录", "长提示词", "参考资料", "关键规则",
)
_ATTENTION_HIERARCHY_TERMS = (
    "summary", "priority", "must follow", "non-negotiable", "摘要", "优先级",
    "authoritative rules", "ordered procedure", "cannot override",
    "必须遵守", "不可覆盖", "权威规则",
)
_ATTENTION_REPETITION_TERMS = (
    "repeated", "duplicate", "again", "重复", "反复", "再次",
)


def _attention_dilution_metadata(text):
    lines = text.splitlines()
    critical_indexes = [
        index for index, line in enumerate(lines, start=1)
        if any(term in line for term in ("critical rule", "关键规则"))
    ]
    return _prompt_analysis_metadata(
        signal_families=["long_or_multi_section_prompt"],
        structureSignalCount=sum(
            text.count(x) for x in _ATTENTION_STRUCTURE_TERMS),
        hierarchySignalCount=sum(
            text.count(x) for x in _ATTENTION_HIERARCHY_TERMS),
        repetitionSignalCount=sum(
            text.count(x) for x in _ATTENTION_REPETITION_TERMS),
        promptLineCount=text.count("\n") + 1,
        promptCharacterCount=len(text),
        criticalRuleLineIndex=max(critical_indexes, default=0),
    )


def _attention_dilution_candidate_hints(metadata):
    line_count = metadata.get("promptLineCount", 0)
    character_count = metadata.get("promptCharacterCount", 0)
    critical_line = metadata.get("criticalRuleLineIndex", 0)
    if (line_count >= 12 and character_count >= 500
            and critical_line >= max(10, line_count * 2 // 3)
            and metadata.get("hierarchySignalCount", 0) == 0):
        return [_candidate_hint(
            {"dilutionKind": "buried_critical_rule"},
            "A critical rule appears late in a long prompt without an "
            "authoritative priority summary.")]
    return []


def extract_attention_dilution(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_ATTENTION_STRUCTURE_TERMS,
        producer_id="extractor.prompt.attention_dilution",
        metadata_builder=_attention_dilution_metadata,
        candidate_hint_builder=_attention_dilution_candidate_hints,
        model_candidate_gate=lambda metadata: (
            True, "buried_critical_rule_without_hierarchy")
        if _attention_dilution_candidate_hints(metadata) else (
            False, "attention_hierarchy_present_or_not_buried"))


_STREAMING_TERMS = (
    "streaming", "streamed", "stream response", "incremental", "chunked",
    "resume", "server-sent events", "sse", "流式", "增量", "分块",
    "断点续传",
)
_STREAM_FRAMING_TERMS = (
    "frame", "delimiter", "sequence number", "event type", "分帧", "分隔符",
    "序号", "事件类型",
)
_STREAM_COMPLETION_TERMS = (
    "completion marker", "done event", "end marker", "完成标记", "结束标记",
)
_STREAM_RESUME_TERMS = (
    "resume token", "cursor", "checkpoint", "last event id", "恢复令牌",
    "游标", "检查点", "最后事件",
)
_STREAM_PARTIAL_TERMS = (
    "partial", "interrupted", "truncated", "parse partial", "部分", "中断",
    "截断", "解析不完整",
)


def _streaming_recovery_metadata(text):
    def explicitly_missing(terms):
        for term in terms:
            start = 0
            while True:
                index = text.find(term, start)
                if index < 0:
                    break
                prefix = text[max(0, index - 120):index]
                if re.search(
                        r"(?:do not define|does not define|without|omit(?:s|ted)?|"
                        r"missing|lacks?|no)\b.{0,100}$",
                        prefix):
                    return 1
                start = index + len(term)
        return 0

    framing_missing = explicitly_missing(_STREAM_FRAMING_TERMS)
    completion_missing = explicitly_missing(_STREAM_COMPLETION_TERMS)
    resume_missing = explicitly_missing(_STREAM_RESUME_TERMS)
    partial_missing = explicitly_missing(_STREAM_PARTIAL_TERMS)
    return _prompt_analysis_metadata(
        signal_families=["streaming_output"],
        streamingSignalCount=sum(text.count(x) for x in _STREAMING_TERMS),
        framingSignalCount=(
            0 if framing_missing
            else sum(text.count(x) for x in _STREAM_FRAMING_TERMS)),
        completionSignalCount=sum(
            text.count(x) for x in _STREAM_COMPLETION_TERMS)
        if not completion_missing else 0,
        resumeSignalCount=(
            0 if resume_missing
            else sum(text.count(x) for x in _STREAM_RESUME_TERMS)),
        partialStreamSignalCount=sum(
            text.count(x) for x in _STREAM_PARTIAL_TERMS)
        if not partial_missing else 0,
        explicitMissingFramingCount=framing_missing,
        explicitMissingCompletionCount=completion_missing,
        explicitMissingResumeCount=resume_missing,
        explicitMissingPartialCount=partial_missing,
    )


def _streaming_recovery_candidate_hints(metadata):
    if metadata.get("streamingSignalCount", 0) == 0:
        return []
    hints = []
    if metadata.get("framingSignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"streamingGapKind": "framing"},
            "Streamed output lacks an evidenced frame or ordering contract."))
    if metadata.get("completionSignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"streamingGapKind": "completion"},
            "Streamed output lacks an evidenced completion contract."))
    if metadata.get("resumeSignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"streamingGapKind": "resume"},
            "Streamed output lacks an evidenced interruption and resume contract."))
    if metadata.get("partialStreamSignalCount", 0) == 0:
        hints.append(_candidate_hint(
            {"streamingGapKind": "partial_parse"},
            "Streamed output lacks an evidenced partial-parse rule."))
    return hints[:1]


def _streaming_recovery_model_gate(metadata):
    if _streaming_recovery_candidate_hints(metadata):
        return True, "streaming_output_missing_recovery_controls"
    return False, "streaming_controls_complete_or_not_streamed"


def extract_streaming_recovery_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_STREAMING_TERMS,
        producer_id="extractor.prompt.streaming_recovery_gap",
        metadata_builder=_streaming_recovery_metadata,
        candidate_hint_builder=_streaming_recovery_candidate_hints,
        model_candidate_gate=_streaming_recovery_model_gate)


_MULTI_TURN_TERMS = (
    "multi-turn", "multiple turns", "conversation", "session",
    "previous turn", "conversation memory", "多轮", "多次对话", "会话",
    "上一轮", "对话记忆",
)
_STATE_INHERITANCE_TERMS = (
    "inherit", "carry forward", "persist", "remember",
    "conversation memory", "previous turn", "继承", "沿用",
    "保持", "记住", "对话记忆", "上一轮",
)
_STATE_UPDATE_TERMS = (
    "update preference", "change preference", "override", "latest request",
    "updates", "later request", "after confirmation", "confirmed preferences",
    "更新偏好", "修改偏好", "覆盖", "最新请求",
)
_STATE_RESET_TERMS = (
    "reset", "new session", "forget", "clear state", "重置", "新会话",
    "忘记", "清除状态",
)
_STATE_INVARIANT_TERMS = (
    "cannot be overridden", "must always", "non-overridable", "system rule",
    "不可覆盖", "始终必须", "系统规则",
)


def _multi_turn_state_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=["multi_turn_state"],
        multiTurnSignalCount=sum(text.count(x) for x in _MULTI_TURN_TERMS),
        stateInheritanceSignalCount=sum(
            text.count(x) for x in _STATE_INHERITANCE_TERMS),
        stateUpdateSignalCount=sum(text.count(x) for x in _STATE_UPDATE_TERMS),
        stateResetSignalCount=sum(text.count(x) for x in _STATE_RESET_TERMS),
        stateInvariantSignalCount=sum(
            text.count(x) for x in _STATE_INVARIANT_TERMS),
    )


def _multi_turn_state_candidate_hints(metadata):
    if metadata.get("multiTurnSignalCount", 0) == 0:
        return []
    if metadata.get("stateInheritanceSignalCount", 0) == 0:
        return []
    if metadata.get("stateResetSignalCount", 0) == 0:
        return [_candidate_hint(
            {"stateGapKind": "reset"},
            "A multi-turn task carries state forward but lacks an evidenced "
            "reset or new-session boundary.")]
    if metadata.get("stateUpdateSignalCount", 0) == 0:
        return [_candidate_hint(
            {"stateGapKind": "update"},
            "A multi-turn task carries state forward but lacks an evidenced "
            "update precedence rule for later turns.")]
    if metadata.get("stateInvariantSignalCount", 0) == 0:
        return [_candidate_hint(
            {"stateGapKind": "non_overridable_rule"},
            "A multi-turn task carries state forward but lacks an evidenced "
            "non-overridable-rule boundary.")]
    return []


def _multi_turn_state_model_gate(metadata):
    if _multi_turn_state_candidate_hints(metadata):
        return True, "multi_turn_state_missing_contract"
    return False, "multi_turn_state_controls_complete_or_unproven"


def extract_multi_turn_state_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_MULTI_TURN_TERMS,
        producer_id="extractor.prompt.multi_turn_state_gap",
        metadata_builder=_multi_turn_state_metadata,
        candidate_hint_builder=_multi_turn_state_candidate_hints,
        model_candidate_gate=_multi_turn_state_model_gate)


_SAFETY_DOMAIN_TERMS = (
    "dangerous", "high-risk", "illegal", "self-harm", "weapon", "malware",
    "violence", "explosive", "危险", "高风险", "违法", "自残", "武器",
    "恶意软件", "暴力", "爆炸物",
)
_SAFETY_REFUSAL_TERMS = (
    "refuse", "do not provide", "decline", "block", "拒绝", "不得提供",
    "不予回答", "阻止",
)
_SAFETY_ALTERNATIVE_TERMS = (
    "safe alternative", "safer help", "benign", "安全替代", "安全帮助",
    "prevention guidance", "storage and disposal", "allowed prevention",
    "无害", "预防指导", "存储和处置",
)
_SAFETY_ESCALATION_TERMS = (
    "emergency", "professional help", "escalate", "human review", "紧急",
    "专业帮助", "转交", "人工复核",
)


def _safety_policy_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=["high_risk_content_or_action"],
        safetyDomainSignalCount=sum(
            text.count(x) for x in _SAFETY_DOMAIN_TERMS),
        refusalSignalCount=sum(text.count(x) for x in _SAFETY_REFUSAL_TERMS),
        safeAlternativeSignalCount=sum(
            text.count(x) for x in _SAFETY_ALTERNATIVE_TERMS),
        escalationSignalCount=sum(
            text.count(x) for x in _SAFETY_ESCALATION_TERMS),
    )


def _safety_policy_candidate_hints(metadata):
    if metadata.get("safetyDomainSignalCount", 0) == 0:
        return []
    if metadata.get("refusalSignalCount", 0) == 0:
        return [_candidate_hint(
            {"safetyGapKind": "refusal_boundary"},
            "A high-risk content domain lacks an evidenced refusal boundary.")]
    if metadata.get("safeAlternativeSignalCount", 0) == 0:
        return [_candidate_hint(
            {"safetyGapKind": "safe_alternative"},
            "A high-risk refusal boundary lacks an evidenced safe alternative.")]
    if metadata.get("escalationSignalCount", 0) == 0:
        return [_candidate_hint(
            {"safetyGapKind": "escalation"},
            "A high-risk refusal boundary lacks an evidenced escalation path.")]
    return []


def _safety_policy_model_gate(metadata):
    if _safety_policy_candidate_hints(metadata):
        return True, "high_risk_domain_missing_safety_controls"
    return False, "safety_controls_complete_or_unproven"


def extract_safety_policy_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_SAFETY_DOMAIN_TERMS,
        producer_id="extractor.prompt.safety_policy_gap",
        metadata_builder=_safety_policy_metadata,
        candidate_hint_builder=_safety_policy_candidate_hints,
        model_candidate_gate=_safety_policy_model_gate)


_SOURCE_USE_TERMS = (
    "copyright", "licensed", "source text", "article", "book", "long passage",
    "quote", "reproduce", "copy", "verbatim", "版权", "许可", "来源文本",
    "文章", "书籍", "长段落", "引用", "复刻", "复制", "逐字",
)
_SOURCE_ATTRIBUTION_TERMS = (
    "attribute", "attribution", "citation", "credit", "name the source",
    "标注来源", "引用", "署名", "出处",
)
_SOURCE_TRANSFORMATION_TERMS = (
    "summarize", "transform", "paraphrase", "extract", "摘要", "转换", "改写",
    "提取",
)
_SOURCE_LIMIT_TERMS = (
    "short excerpt", "limit quotation", "do not reproduce", "public domain",
    "public-domain", "licensed", "license", "user-provided",
    "bounded excerpt", "brief excerpt", "短摘录", "限制引用", "不得复刻",
    "公版", "用户提供", "许可",
)


def _source_use_policy_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=["third_party_source_use"],
        sourceUseSignalCount=sum(text.count(x) for x in _SOURCE_USE_TERMS),
        attributionSignalCount=sum(
            text.count(x) for x in _SOURCE_ATTRIBUTION_TERMS),
        transformationSignalCount=sum(
            text.count(x) for x in _SOURCE_TRANSFORMATION_TERMS),
        sourceLimitSignalCount=sum(text.count(x) for x in _SOURCE_LIMIT_TERMS),
    )


def _source_use_candidate_hints(metadata):
    if metadata.get("sourceUseSignalCount", 0) == 0:
        return []
    if metadata.get("sourceLimitSignalCount", 0) == 0:
        return [_candidate_hint(
            {"sourceGapKind": "reproduction_limit"},
            "A third-party source task lacks an evidenced short-excerpt, "
            "license, user-provided, or public-domain boundary.")]
    if metadata.get("transformationSignalCount", 0) == 0:
        return [_candidate_hint(
            {"sourceGapKind": "transformation"},
            "A third-party source task lacks an evidenced summary, paraphrase, "
            "or transformation boundary.")]
    if metadata.get("attributionSignalCount", 0) == 0:
        return [_candidate_hint(
            {"sourceGapKind": "attribution"},
            "A third-party source task lacks evidenced attribution or source "
            "identity requirements.")]
    return []


def _source_use_model_gate(metadata):
    if _source_use_candidate_hints(metadata):
        return True, "third_party_source_missing_use_controls"
    return False, "source_use_controls_complete_or_unproven"


def extract_source_use_policy_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_SOURCE_USE_TERMS,
        producer_id="extractor.prompt.source_use_policy_gap",
        metadata_builder=_source_use_policy_metadata,
        candidate_hint_builder=_source_use_candidate_hints,
        model_candidate_gate=_source_use_model_gate)


def extract_trust_boundary_ambiguity(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes,
        triggers=("external content", "retrieved", "user input", "tool output",
                  "网页内容", "检索内容", "用户输入", "工具输出"),
        producer_id="extractor.prompt.trust_boundary",
        metadata_builder=_trust_boundary_metadata,
        candidate_hint_builder=_trust_boundary_candidate_hints,
        model_candidate_gate=_trust_boundary_model_gate)


def extract_tool_necessity(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes,
        triggers=("allowed_tools", "allowed-tools", "permissions:", "tools:",
                  "use read", "use write", "use edit", "use bash",
                  "use shell", "use delete", "use webfetch", "use websearch",
                  "工具权限", "允许工具"),
        producer_id="extractor.prompt.tool_necessity",
        metadata_builder=_tool_scope_metadata,
        candidate_hint_builder=_tool_scope_candidate_hints,
        model_candidate_gate=_tool_scope_model_gate)


def _capability_family(category: str, operation: str) -> str:
    category = category.lower()
    operation = operation.lower()
    if category == "network":
        return "network_access"
    if category == "process":
        return "process_execution"
    if category == "credential":
        return "credential_access"
    if category == "installation":
        return "dependency_installation"
    if category == "configuration":
        return "configuration_access"
    if category == "file":
        if any(term in operation for term in ("write", "append")):
            return "file_write"
        if any(term in operation for term in ("read",)):
            return "file_read"
        return "file_access"
    return category[:80] or "unknown"


def _permission_descriptor(permission: str) -> Tuple[str, str]:
    value = permission.strip()
    lower = value.lower()
    target = ""
    if lower.startswith(("bash", "shell", "terminal")):
        match = re.search(r"\(([^:()]+)", value)
        if match:
            target = match.group(1).strip().rsplit("/", 1)[-1].lower()[:80]
        return "process_execution", target
    if lower.startswith(("webfetch", "websearch", "http", "network")):
        return "network_access", target
    if lower.startswith(("read", "grep", "search")):
        return "file_read", target
    if lower.startswith(("write", "edit", "delete", "move")):
        return "file_write", target
    if lower.startswith(("credential", "secret", "env")):
        return "credential_access", target
    return "unknown", target


def _declared_behavior_families(description: str) -> Tuple[List[str], List[str]]:
    text = description.lower()
    declared = []
    denied = []
    definitions = (
        ("network_access", (
            "network", "endpoint", "api", "url", "web", "fetch", "retrieve",
            "网络", "接口", "网址", "网页", "获取", "检索")),
        ("process_execution", (
            "command", "shell", "subprocess", "execute", "process",
            "命令", "进程", "执行")),
        ("file_read", (
            "read file", "reads ", "read-only", "读取文件", "只读")),
        ("file_write", (
            "write file", "writes ", "edit file", "写入文件", "编辑文件")),
        ("credential_access", (
            "credential", "secret", "environment variable", "凭据", "密钥",
            "环境变量")),
    )
    negations = (
        "without {term}", "no {term}", "never {term}",
        "does not use {term}", "不使用{term}", "无{term}", "禁止{term}",
    )
    for family, terms in definitions:
        present = any(term in text for term in terms)
        negative = any(
            pattern.format(term=term) in text
            for term in terms for pattern in negations
        )
        negative = negative or any(
            re.search(
                r"(?:without|no|never|does not|doesn't)\b.{0,32}\b"
                + re.escape(term), text)
            for term in terms if term.isascii()
        )
        negative = negative or any(
            re.search(r"(?:不|无|禁止).{0,16}" + re.escape(term), text)
            for term in terms if not term.isascii()
        )
        if family == "network_access" and any(
                marker in text for marker in ("local-only", "offline only",
                                               "仅本地", "仅离线")):
            negative = True
        if negative:
            denied.append(family)
        elif present:
            declared.append(family)
    return sorted(set(declared)), sorted(set(denied))


def _permission_matches(family: str, target: str,
                        descriptors: List[Tuple[str, str]]) -> bool:
    for declared_family, declared_target in descriptors:
        family_match = (
            declared_family == family
            or (declared_family == "file_read" and family == "file_access")
            or (declared_family == "file_write" and family == "file_access")
        )
        if not family_match:
            continue
        if family == "process_execution" and declared_target:
            if (target and target.lower().rsplit("/", 1)[-1]
                    == declared_target):
                return True
            continue
        return True
    return False


def _fact_location(file_info: Dict[str, Any], fact: Dict[str, Any],
                   file_bytes: Dict[str, bytes]) -> Dict[str, Any]:
    data = file_bytes.get(file_info["fileId"], b"")
    start = 0
    end = min(600, len(data))
    line_number = fact.get("sourceLine")
    if isinstance(line_number, int) and line_number > 0:
        lines = data.splitlines(keepends=True)
        if line_number <= len(lines):
            start = sum(len(line) for line in lines[:line_number - 1])
            end = start + len(lines[line_number - 1].rstrip(b"\r\n"))
    return {
        "fileId": file_info["fileId"],
        "artifactPath": file_info["normalizedPath"],
        "fileDigest": file_info.get("contentDigest") or "",
        "sourceByteRange": {"start": start, "end": end},
        "locationSchemaVersion": "1",
    }


def _skill_manifest_and_capability_seed(review_dict, file_bytes, *,
                                        producer_id, require_external=False):
    if review_dict.get("engine") != "skill":
        return []
    am = review_dict.get("artifactModel") or {}
    manifest_file = am.get("manifestFile")
    manifest = am.get("manifest") or {}
    facts = ((am.get("capabilityFacts") or {}).get("facts") or [])
    observed_facts = [
        fact for fact in facts if fact.get("sourceKind") != "manifest"
    ]
    if not manifest_file:
        return []
    if require_external and not manifest.get("external_reference_count"):
        return []
    if (not require_external and not observed_facts
            and not manifest.get("permissions")):
        return []
    snap = review_dict.get("snapshot") or {}
    files = {f.get("normalizedPath"): f for f in (snap.get("files") or [])
             if f.get("status") == "included"}
    locations = [{"fileId": manifest_file["fileId"],
                  "artifactPath": manifest_file["normalizedPath"],
                  "fileDigest": "", "sourceByteRange": {"start": 0,
                  "end": min(500, len(file_bytes.get(manifest_file["fileId"], b"")))},
                  "locationSchemaVersion": "1"}]
    permissions = [
        str(item)[:160] for item in (manifest.get("permissions") or [])
        if isinstance(item, str)
    ]
    descriptors = [_permission_descriptor(item) for item in permissions]
    declared_permission_families = sorted({
        family for family, _target in descriptors if family != "unknown"
    })
    declared_behavior, denied_behavior = _declared_behavior_families(
        str(manifest.get("description") or ""))
    manifest_metadata = manifest.get("metadata") or {}
    trust_policy_text = (
        " ".join(str(value) for value in manifest_metadata.values())
        if isinstance(manifest_metadata, dict) else "")
    trust_policy_text = trust_policy_text.lower()
    external_instruction_urls = [
        item for item in (manifest.get("external_instruction_urls") or [])
        if isinstance(item, str)
    ]
    external_trust_control_count = sum(
        trust_policy_text.count(term) for term in (
            "verify", "digest", "parse as data", "untrusted data",
            "never execute", "do not execute", "not instructions",
            "not fetched", "not followed", "documentation only",
            "for humans only",
            "校验", "摘要", "仅作为数据", "不执行", "不是指令"))
    metadata = [{
        "evidenceRole": "manifest_declaration",
        "evidenceScope": "bounded_static_skill_snapshot",
        "declaredPermissionFamilies": declared_permission_families[:12],
        "declaredProcessTargets": sorted({
            target for family, target in descriptors
            if family == "process_execution" and target
        })[:12],
        "declaredCapabilityFamilies": sorted(set(
            declared_permission_families + declared_behavior))[:12],
        "deniedCapabilityFamilies": denied_behavior[:12],
        "observedCapabilityFactCount": min(len(observed_facts), 128),
        "includedCapabilityFactCount": min(len(observed_facts[:7]), 128),
        "capabilityFactsTruncated": len(observed_facts) > 7,
        "externalReferenceCount": min(
            int(manifest.get("external_reference_count") or 0), 128),
        "externalInstructionUrlCount": min(
            len(external_instruction_urls), 128),
        "externalTrustControlCount": min(external_trust_control_count, 128),
    }]
    behavior_mismatch_count = 0
    permission_mismatch_count = 0
    for fact in observed_facts[:7]:
        f = files.get(fact.get("artifactPath"))
        if f:
            locations.append(_fact_location(f, fact, file_bytes))
            family = _capability_family(
                str(fact.get("category", "")),
                str(fact.get("operation", "")))
            target = str(fact.get("target", ""))[:80]
            behavior_denied = family in denied_behavior
            behavior_match = (
                family in declared_behavior and not behavior_denied)
            permission_match = _permission_matches(
                family, target, descriptors)
            behavior_mismatch_count += int(behavior_denied)
            permission_mismatch_count += int(
                not permission_match
                and (family != "process_execution" or bool(target)))
            metadata.append({
                "evidenceRole": "capability_fact",
                "evidenceScope": "bounded_static_skill_snapshot",
                "capabilityCategory": str(fact.get("category", ""))[:80],
                "capabilityOperation": str(fact.get("operation", ""))[:160],
                "capabilityFamily": family,
                "capabilityTarget": target,
                "declaredBehaviorMatch": behavior_match,
                "declaredBehaviorDenied": behavior_denied,
                "declaredPermissionMatch": permission_match,
            })
    evs = _make_evidence_records(locations,
                                  snapshot_id=snap.get("snapshotId", ""),
                                  producer_id=producer_id,
                                  metadata_by_index=metadata)
    source = {
        "declaredPermissionCount": len(permissions),
        "observedCapabilityCount": len(observed_facts),
    }
    candidate_hints = []
    if (producer_id == "extractor.skill.declared_vs_observed"
            and behavior_mismatch_count):
        candidate_hints.append(_candidate_hint(
            {"mismatchKind": "capability_undeclared"},
            "A statically observed capability is explicitly denied by the "
            "Skill behavior declaration."))
    if (producer_id == "extractor.skill.permission_capability"
            and permission_mismatch_count):
        candidate_hints.append(_candidate_hint(
            {"mismatchKind": "undeclared_capability"},
            "A statically observed capability has no matching declared "
            "permission family or fixed command target."))
    if producer_id == "extractor.skill.external_instruction_trust":
        if external_instruction_urls and external_trust_control_count == 0:
            candidate_hints.append(_candidate_hint(
                {"trustGapKind": "instruction_data_confusion"},
                "The Skill declares fetched external runtime instructions "
                "without an evidenced data-only trust boundary."))
        elif (manifest.get("external_reference_count")
              and external_trust_control_count == 0):
            candidate_hints.append(_candidate_hint(
                {"trustGapKind": "missing_integrity_boundary"},
                "The Skill references external material without evidenced "
                "integrity or data-only parsing controls."))
    if candidate_hints:
        source["candidateHints"] = candidate_hints
    elif producer_id in {
            "extractor.skill.declared_vs_observed",
            "extractor.skill.permission_capability",
            "extractor.skill.external_instruction_trust",
    }:
        source["modelCandidatePolicy"] = "skip_without_catalog_hint"
        source["modelCandidateSkipReason"] = (
            "static_skill_capability_controls_match")
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


def _policy(*, applies, confirm, reject, insufficient):
    return SemanticJudgmentPolicy(
        appliesWhen=list(applies),
        confirmWhen=list(confirm),
        rejectWhen=list(reject),
        insufficientWhen=list(insufficient),
    )


CATALOG: Dict[str, Tuple[SemanticFindingType, Extractor]] = {

    "semantic.prompt.instruction_conflict": (
        SemanticFindingType(
            findingType="semantic.prompt.instruction_conflict",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "conflictKind", "enum",
                enum=["contradictory_directive", "conflicting_style",
                      "conflicting_scope"])],
            subjectKeyFields=["conflictKind"],
            falsificationQuestion=(
                "Do the two cited prompt lines contain instructions that "
                "cannot both be satisfied in their actual scopes?"),
            guidanceId="semantic.prompt.instruction_conflict",
            judgmentPolicy=_policy(
                applies=[
                    "At least two cited directives constrain the same response.",
                    "Their target, stage, condition, and exception scopes can be compared.",
                ],
                confirm=[
                    "The directives govern the same target and scope.",
                    "Satisfying either directive necessarily violates the other.",
                ],
                reject=[
                    "One directive governs an opening segment and the other a later segment.",
                    "One rule governs an outer format and the other content inside a field.",
                    "A stated exception or condition makes both directives satisfiable.",
                ],
                insufficient=[
                    "Reject or mark insufficient when the shared target or scope is not evidenced.",
                ]),
            owaspAst10=[],
        ), extract_instruction_conflict,
    ),

    "semantic.prompt.missing_output_contract": (
        SemanticFindingType(
            findingType="semantic.prompt.missing_output_contract",
            engine="prompt", defaultSeverity="low",
            subjectFields=[
                SemanticSubjectField(
                    "expectedFormat", "enum",
                    enum=["json", "yaml", "structured_text"]),
                SemanticSubjectField(
                    "gapKind", "enum",
                    enum=["missing_fields", "missing_types",
                          "missing_requiredness", "missing_value_constraints"]),
            ],
            subjectKeyFields=["expectedFormat"],
            falsificationQuestion=(
                "Does the prompt request machine-structured output while "
                "omitting a material field or schema contract?"),
            guidanceId="semantic.prompt.missing_output_contract",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt requests JSON, YAML, tabular, or another machine-structured result.",
                ],
                confirm=[
                    "Only a container format is named and required fields or schema are absent.",
                    "For direct downstream use, material types, requiredness, units, or value constraints are absent.",
                ],
                reject=[
                    "Required fields and their usable structure are explicitly declared.",
                    "The requested output is free-form prose for a human, not a machine contract.",
                ],
                insufficient=[
                    "Mark insufficient when the output consumer or cited schema reference is unavailable.",
                ]),
        ), extract_missing_output_contract,
    ),

    "semantic.skill.declared_behavior_mismatch": (
        SemanticFindingType(
            findingType="semantic.skill.declared_behavior_mismatch",
            engine="skill", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "mismatchKind", "enum",
                enum=["capability_undeclared", "declared_but_absent",
                      "scope_broader_than_declared"])],
            subjectKeyFields=["mismatchKind"],
            falsificationQuestion=(
                "Is the manifest behavior materially incompatible with the "
                "statically observed capability family?"),
            guidanceId="semantic.skill.declared_behavior_mismatch",
            judgmentPolicy=_policy(
                applies=[
                    "A manifest behavior declaration and at least one implementation capability fact are cited.",
                ],
                confirm=[
                    "An observed capability is denied or materially outside the declared behavior.",
                    "The observed scope is materially broader than the declaration.",
                    "An explicit declaredBehaviorMatch=false caused by a denied capability plus a cited call-site fact supports a static behavior mismatch.",
                ],
                reject=[
                    "The normalized declared capability family matches the observed family.",
                    "An explicit declaredBehaviorMatch=true falsifies the corresponding mismatch.",
                    "A declaration to retrieve a public endpoint is compatible with observed network access.",
                    "Different wording for the same narrow operation is not a mismatch.",
                ],
                insufficient=[
                    "Mark insufficient when the fact is import-only, the behavior declaration is not explicit enough to normalize, or runtime reachability is required.",
                ]),
            owaspAst10=["OWASP-AST04"],
        ), extract_declared_behavior_mismatch,
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
                "Does untrusted content lack a clear data-only instruction boundary?"),
            guidanceId="semantic.prompt.trust_boundary_ambiguity",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt consumes user, retrieved, external, or tool-produced content.",
                ],
                confirm=[
                    "That content can be interpreted as instructions and no data-only boundary is declared.",
                ],
                reject=[
                    "The content is clearly delimited, quoted, or declared untrusted data.",
                    "The prompt explicitly forbids following instructions found in that content.",
                ],
                insufficient=[
                    "Mark insufficient when the content insertion boundary is not shown.",
                ]),
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
                "Are declared tools materially broader than the evidenced task requires?"),
            guidanceId="semantic.prompt.excessive_tool_scope",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt declares tools or permissions and states a task that permits necessity comparison.",
                ],
                confirm=[
                    "A high-impact or unrelated capability is available but unnecessary for the task.",
                    "A necessary capability is granted at materially broader scope without an approval boundary.",
                ],
                reject=[
                    "Every cited capability is task-necessary and narrowly bounded.",
                    "High-impact use is draft-only or requires explicit human approval.",
                ],
                insufficient=[
                    "Mark insufficient when task scope or tool semantics are not evidenced.",
                ]),
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
                "Do declared permissions and observed static capabilities "
                "materially disagree after normalized family and target matching?"),
            guidanceId="semantic.skill.permission_capability_mismatch",
            judgmentPolicy=_policy(
                applies=[
                    "A permission declaration and implementation capability fact are cited.",
                ],
                confirm=[
                    "An observed capability family has no matching declared permission.",
                    "A command-restricted permission names a different fixed command target.",
                    "An explicit declaredPermissionMatch=false with a resolved static capability family or fixed command target supports a mismatch.",
                ],
                reject=[
                    "The normalized permission family matches the observed capability family.",
                    "An explicit declaredPermissionMatch=true falsifies the corresponding mismatch.",
                    "Bash(command:*) matches a fixed invocation of that same command.",
                    "Different API names for the same narrow capability are equivalent.",
                ],
                insufficient=[
                    "Mark insufficient when a dynamic command target cannot be resolved statically.",
                ]),
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
                "Does the Skill treat external material as executable "
                "instructions without provenance, integrity, or a data-only boundary?"),
            guidanceId="semantic.skill.external_instruction_trust_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The Skill declares an external instruction or content reference.",
                ],
                confirm=[
                    "Remote material can alter instructions or behavior without integrity and trust controls.",
                ],
                reject=[
                    "Content is digest-pinned or signature-verified and handled as data only.",
                    "A reference is documentation for humans and is not fetched or followed at runtime.",
                ],
                insufficient=[
                    "Mark insufficient when the reference mode or trust controls are not evidenced.",
                ]),
            owaspAst10=["OWASP-AST05"],
        ), extract_external_instruction_trust_gap,
    ),

    "semantic.prompt.output_budget_pressure": (
        SemanticFindingType(
            findingType="semantic.prompt.output_budget_pressure",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "pressureKind", "enum",
                enum=["implicit_lower_bound", "missing_priority",
                      "missing_continuation"])],
            subjectKeyFields=["pressureKind"],
            falsificationQuestion=(
                "Are requested detail and output limits materially unlikely "
                "to fit, or under-specified when trade-offs are required?"),
            guidanceId="semantic.prompt.output_budget_pressure",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt combines a volume/detail obligation with an output limit.",
                ],
                confirm=[
                    "The requested coverage is materially infeasible even though one lower bound is implicit.",
                    "A real trade-off is required but no priority or continuation behavior is defined.",
                ],
                reject=[
                    "The limit and requested content are plausibly compatible.",
                    "Different limits govern different output segments.",
                    "Priorities or a bounded continuation protocol resolve the pressure.",
                ],
                insufficient=[
                    "Do not infer exact token conversions or average item sizes without evidence.",
                ]),
        ), extract_output_budget_pressure,
    ),

    "semantic.prompt.authority_boundary_ambiguity": (
        SemanticFindingType(
            findingType="semantic.prompt.authority_boundary_ambiguity",
            engine="prompt", defaultSeverity="high",
            subjectFields=[SemanticSubjectField(
                "authorityKind", "enum",
                enum=["external_side_effect", "delegated_decision",
                      "approval_boundary"])],
            subjectKeyFields=["authorityKind"],
            falsificationQuestion=(
                "Does a system prompt authorize consequential autonomous "
                "action without a clear approval and scope boundary?"),
            guidanceId="semantic.prompt.authority_boundary_ambiguity",
            judgmentPolicy=_policy(
                applies=[
                    "A system prompt combines autonomous initiative with an external side effect or consequential decision.",
                ],
                confirm=[
                    "The model may execute the action without identifying who approves it or where authority ends.",
                ],
                reject=[
                    "The prompt permits analysis or drafting only.",
                    "An explicit user or human approval is required before the side effect.",
                    "Proactive low-impact information gathering alone is not consequential authority.",
                ],
                insufficient=[
                    "Mark insufficient when enforcement may exist only in an unseen application layer.",
                ]),
        ), extract_authority_boundary_ambiguity,
    ),

    "semantic.prompt.failure_strategy_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.failure_strategy_gap",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "gapKind", "enum",
                enum=["timeout", "retry", "fallback", "empty_result",
                      "malformed_input", "partial_failure"])],
            subjectKeyFields=["gapKind"],
            falsificationQuestion=(
                "Does a required failure-prone operation lack a strategy for "
                "a material failure or edge case?"),
            guidanceId="semantic.prompt.failure_strategy_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt requires an external call, retrieval, parsing, database, or tool operation.",
                ],
                confirm=[
                    "A material timeout, empty, malformed, or partial-failure path has no defined behavior.",
                    "A strategy exists but applies to a different operation or failure mode.",
                ],
                reject=[
                    "The cited operation has an explicit bounded failure, retry, fallback, or structured-error path.",
                    "The operation is optional and its absence cannot invalidate the task result.",
                ],
                insufficient=[
                    "Do not require every possible edge case; identify one material uncovered path.",
                ]),
        ), extract_failure_strategy_gap,
    ),

    "semantic.prompt.ambiguous_operational_criteria": (
        SemanticFindingType(
            findingType="semantic.prompt.ambiguous_operational_criteria",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "criterionKind", "enum",
                enum=["vague_degree", "undefined_boundary",
                      "ambiguous_referent", "missing_task_anchor",
                      "missing_required_context",
                      "missing_success_criteria",
                      "unverifiable_quality_bar"])],
            subjectKeyFields=["criterionKind"],
            falsificationQuestion=(
                "Does the complete prompt omit a task anchor, required "
                "context, success criterion, usable threshold, referent, or "
                "decision rule needed for materially consistent execution?"),
            guidanceId="semantic.prompt.ambiguous_operational_criteria",
            judgmentPolicy=_policy(
                applies=[
                    "A missing task anchor, required context, success criterion, vague degree, condition, or referent materially affects execution or output acceptance.",
                ],
                confirm=[
                    "Reasonable implementations can produce materially different task interpretations or acceptance decisions because the required anchor, context, boundary, or criterion is absent.",
                ],
                reject=[
                    "The term is locally defined by examples, thresholds, or an explicit decision rule.",
                    "The term is a non-binding style preference with no material behavioral effect.",
                    "The prompt explicitly declares itself to be a reusable style-only fragment or preset rather than a complete standalone task.",
                    "The task is intentionally open-ended but still identifies the requested operation and primary subject or object.",
                    "The alleged omission is merely optional creative detail and does not prevent a materially consistent task interpretation.",
                ],
                insufficient=[
                    "Mark insufficient when the prompt's intended composition context or surrounding definition is outside the cited evidence.",
                ]),
        ), extract_ambiguous_operational_criteria,
    ),

    "semantic.prompt.grounding_requirement_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.grounding_requirement_gap",
            engine="prompt", defaultSeverity="high",
            subjectFields=[SemanticSubjectField(
                "groundingKind", "enum",
                enum=["source_required", "uncertainty_required",
                      "verification_required"])],
            subjectKeyFields=["groundingKind"],
            falsificationQuestion=(
                "Does the prompt request consequential or verifiable claims "
                "without proportionate grounding, uncertainty, or verification?"),
            guidanceId="semantic.prompt.grounding_requirement_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The task requests legal, medical, financial, statistical, cited, or otherwise consequential factual claims.",
                ],
                confirm=[
                    "It encourages exact claims while allowing unsupported invention or silent certainty.",
                    "Sources or numbers are required but no reality/verification constraint is stated.",
                ],
                reject=[
                    "The prompt requires attributable sources, uncertainty disclosure, and no guessing.",
                    "The task is creative or subjective and does not claim factual authority.",
                ],
                insufficient=[
                    "Mark insufficient when the use case consequence or available source boundary is unknown.",
                ]),
        ), extract_grounding_requirement_gap,
    ),

    "semantic.prompt.sensitive_reasoning_exposure": (
        SemanticFindingType(
            findingType="semantic.prompt.sensitive_reasoning_exposure",
            engine="prompt", defaultSeverity="high",
            subjectFields=[SemanticSubjectField(
                "exposureKind", "enum",
                enum=["chain_of_thought", "internal_policy",
                      "hidden_decision_rule"])],
            subjectKeyFields=["exposureKind"],
            falsificationQuestion=(
                "Does the prompt require user-visible disclosure of hidden "
                "reasoning or sensitive internal policy?"),
            guidanceId="semantic.prompt.sensitive_reasoning_exposure",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt discusses chain-of-thought, scratchpads, internal policy, or hidden decision rules.",
                ],
                confirm=[
                    "It requires that sensitive internal material be shown in user-visible output.",
                ],
                reject=[
                    "It keeps internal reasoning private and asks only for the final result.",
                    "A concise evidence-based rationale or audit summary is not hidden chain-of-thought.",
                    "The cited policy is intentionally public and not sensitive.",
                ],
                insufficient=[
                    "Mark insufficient when output visibility or policy sensitivity is not evidenced.",
                ]),
        ), extract_sensitive_reasoning_exposure,
    ),

    "semantic.prompt.verification_step_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.verification_step_gap",
            engine="prompt", defaultSeverity="low",
            subjectFields=[SemanticSubjectField(
                "verificationKind", "enum",
                enum=["required_fields", "constraint_consistency",
                      "downstream_validity"])],
            subjectKeyFields=["verificationKind"],
            falsificationQuestion=(
                "Does a materially constrained or consequential output lack "
                "a concrete validation step where omission would be costly?"),
            guidanceId="semantic.prompt.verification_step_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The output has multiple mandatory constraints, feeds automation, or supports a consequential decision.",
                ],
                confirm=[
                    "No model-side or external validation step checks the material constraints before use.",
                    "The prompt explicitly sends the result to production or downstream automation without another review or check.",
                ],
                reject=[
                    "The task is simple or open-ended enough that an explicit self-check is unnecessary.",
                    "A concrete checklist, schema validator, or downstream validation already covers the constraints.",
                    "A generic request for quality alone does not make self-check mandatory.",
                ],
                insufficient=[
                    "Mark insufficient when downstream validation is unseen and the prompt does not explicitly bypass or deny another review or check.",
                ]),
        ), extract_verification_step_gap,
    ),

    "semantic.prompt.input_and_default_contract_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.input_and_default_contract_gap",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "gapKind", "enum",
                enum=["requiredness", "missing_input", "invalid_input",
                      "default_behavior"])],
            subjectKeyFields=["gapKind"],
            falsificationQuestion=(
                "Does a task with explicit input dependencies omit a "
                "material requiredness, missing-input, invalid-input, or "
                "default behavior contract?"),
            guidanceId="semantic.prompt.input_and_default_contract_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt names structured, required, or operationally necessary user inputs.",
                    "The task can materially change or fail when such input is absent or invalid.",
                ],
                confirm=[
                    "Necessary inputs are named but required versus optional status is materially unclear.",
                    "A missing, empty, malformed, or unsupported input can occur and no clarification, safe default, validation, or refusal path is defined.",
                ],
                reject=[
                    "The task accepts arbitrary conversational input and has no fixed input dependency.",
                    "Requiredness, defaults, validation, and missing-input behavior are explicit for the material fields.",
                    "A declared upstream schema owns the complete contract and the prompt names it unambiguously.",
                ],
                insufficient=[
                    "Mark insufficient when the required input schema exists only in an unseen application layer.",
                ]),
        ), extract_input_and_default_contract_gap,
    ),

    "semantic.prompt.example_contract_mismatch": (
        SemanticFindingType(
            findingType="semantic.prompt.example_contract_mismatch",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "exampleGapKind", "enum",
                enum=["rule_mismatch", "schema_mismatch",
                      "missing_boundary_example", "missing_failure_example",
                      "stale_example", "distribution_mismatch"])],
            subjectKeyFields=["exampleGapKind"],
            falsificationQuestion=(
                "Do normative examples materially contradict declared rules, "
                "rely on stale assumptions, misrepresent the stated input "
                "distribution, or omit a material boundary/failure branch?"),
            guidanceId="semantic.prompt.example_contract_mismatch",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt contains examples that are presented as normative guidance or executable output shape.",
                    "A rule, schema, boundary, failure behavior, temporal assumption, or declared input distribution can be compared with those examples.",
                ],
                confirm=[
                    "An example violates a required field, enum, format, language, or behavioral rule.",
                    "The prompt relies on examples to define behavior but covers only the happy path while a declared material boundary or failure branch remains undefined.",
                    "An example relies on a materially stale fact, schema, capability, or policy assumption.",
                    "Examples are presented as representative but materially exclude a declared input class or distribution segment.",
                ],
                reject=[
                    "The example and rule are compatible after accounting for optional fields and stated variants.",
                    "The example is explicitly illustrative rather than exhaustive.",
                    "Boundary and failure behavior are defined textually even without a separate example.",
                    "Examples are current and representative for the declared scope, or their limitations are explicit.",
                ],
                insufficient=[
                    "Mark insufficient when the cited evidence does not contain both the relevant rule and example.",
                ]),
        ), extract_example_contract_mismatch,
    ),

    "semantic.prompt.tool_call_contract_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.tool_call_contract_gap",
            engine="prompt", defaultSeverity="high",
            subjectFields=[SemanticSubjectField(
                "contractGapKind", "enum",
                enum=["invocation_condition", "parameter_provenance",
                      "result_schema", "error_handling"])],
            subjectKeyFields=["contractGapKind"],
            falsificationQuestion=(
                "Does a required tool or function invocation lack a material "
                "condition, parameter provenance, result, or error contract?"),
            guidanceId="semantic.prompt.tool_call_contract_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt directs the model to invoke a tool, function, or API rather than merely discuss one.",
                ],
                confirm=[
                    "The invocation condition is materially ambiguous for a consequential or repeated call.",
                    "Required arguments can be invented or sourced from untrusted text because provenance and validation are undefined.",
                    "The result shape or failure behavior is required downstream but unspecified.",
                ],
                reject=[
                    "A named registered schema explicitly owns arguments and result validation.",
                    "Invocation conditions, argument sources, result shape, and bounded failure behavior are declared for the material call.",
                    "The prompt only analyzes or drafts a possible call and cannot execute it.",
                ],
                insufficient=[
                    "Mark insufficient when the referenced tool schema is not present in evidence.",
                ]),
        ), extract_tool_call_contract_gap,
    ),

    "semantic.prompt.capability_dependency_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.capability_dependency_gap",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "dependencyKind", "enum",
                enum=["realtime_data", "web_access", "vision", "audio",
                      "persistent_memory", "context_capacity", "plugin"])],
            subjectKeyFields=["dependencyKind"],
            falsificationQuestion=(
                "Does the task require a non-intrinsic model capability "
                "without declaring how it is provided or how to degrade?"),
            guidanceId="semantic.prompt.capability_dependency_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The requested result materially depends on realtime data, web access, media understanding, persistent memory, unusually large context, or a plugin.",
                ],
                confirm=[
                    "The capability is required but no tool, supplied input, target-platform guarantee, or fallback is declared.",
                    "The prompt encourages fabricating the unavailable observation instead of stopping or requesting input.",
                ],
                reject=[
                    "The target system explicitly provides the named capability or tool.",
                    "The needed observation is supplied as input rather than fetched implicitly.",
                    "A clear unavailable-capability fallback requests data or states the limitation.",
                ],
                insufficient=[
                    "Mark insufficient when trusted deployment configuration may provide the capability but is not evidenced.",
                ]),
        ), extract_capability_dependency_gap,
    ),

    "semantic.prompt.sensitive_data_handling_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.sensitive_data_handling_gap",
            engine="prompt", defaultSeverity="high",
            subjectFields=[SemanticSubjectField(
                "dataPolicyKind", "enum",
                enum=["minimization", "redaction", "authorization",
                      "retention", "disclosure"])],
            subjectKeyFields=["dataPolicyKind"],
            falsificationQuestion=(
                "Does a prompt direct sensitive-data handling while "
                "omitting a proportionate minimization, redaction, "
                "authorization, retention, or disclosure boundary?"),
            guidanceId="semantic.prompt.sensitive_data_handling_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt directs collection, storage, processing, sharing, or output of personal, medical, financial, contact, or credential data.",
                ],
                confirm=[
                    "The action exposes or retains more sensitive data than the stated task requires.",
                    "A material disclosure, authorization, redaction, or retention boundary is absent for the declared action.",
                ],
                reject=[
                    "The prompt only warns against sensitive data and does not direct handling it.",
                    "Collection is minimized and output is masked or redacted with explicit authorization and retention limits appropriate to the task.",
                    "Only synthetic or already-public non-sensitive data is in scope.",
                ],
                insufficient=[
                    "Mark insufficient when the data classification or external access-control layer is not evidenced.",
                ]),
        ), extract_sensitive_data_handling_gap,
    ),

    "semantic.prompt.role_scope_contract_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.role_scope_contract_gap",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "roleGapKind", "enum",
                enum=["audience", "duties", "exclusions",
                      "capability_claim"])],
            subjectKeyFields=["roleGapKind"],
            falsificationQuestion=(
                "Does an operational role omit a material audience, duty, "
                "exclusion, or capability boundary needed to route requests?"),
            guidanceId="semantic.prompt.role_scope_contract_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt establishes a persistent operational role or persona that changes how requests are handled.",
                ],
                confirm=[
                    "The role is only a title or personality and leaves its intended audience or material duties unclear.",
                    "The role claims expertise or authority without stating a necessary exclusion, escalation, or out-of-scope boundary.",
                ],
                reject=[
                    "The prompt is a one-off task and does not need a persistent role contract.",
                    "Audience, duties, capabilities, and material exclusions are explicit enough to route in-scope and out-of-scope requests.",
                ],
                insufficient=[
                    "Mark insufficient when a referenced role definition exists only outside the reviewed artifact.",
                ]),
        ), extract_role_scope_contract_gap,
    ),

    "semantic.prompt.workflow_dependency_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.workflow_dependency_gap",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "dependencyGapKind", "enum",
                enum=["missing_prerequisite", "reversed_order",
                      "unused_intermediate", "unreachable_step"])],
            subjectKeyFields=["dependencyGapKind"],
            falsificationQuestion=(
                "Does a multi-step workflow omit or contradict a material "
                "prerequisite, ordering edge, intermediate use, or branch?"),
            guidanceId="semantic.prompt.workflow_dependency_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt defines multiple dependent steps rather than an unordered checklist.",
                ],
                confirm=[
                    "A step consumes information that no prior step or input produces.",
                    "A required validation or transformation occurs after the action that depends on it.",
                    "A material intermediate result is produced but never used, or a branch cannot be reached under the declared conditions.",
                ],
                reject=[
                    "The steps are intentionally independent and may run in any order.",
                    "Prerequisites, produced results, consumers, and conditional branches form a coherent sequence.",
                ],
                insufficient=[
                    "Mark insufficient when the workflow references an unseen orchestrator that may own the dependency.",
                ]),
        ), extract_workflow_dependency_gap,
    ),

    "semantic.prompt.field_constraint_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.field_constraint_gap",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "fieldGapKind", "enum",
                enum=["type_or_unit", "precision", "enum_or_range",
                      "boundary_behavior"])],
            subjectKeyFields=["fieldGapKind"],
            falsificationQuestion=(
                "Does a machine-consumed or materially bounded field omit a "
                "type, unit, precision, range, enum, or boundary behavior?"),
            guidanceId="semantic.prompt.field_constraint_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt names a field whose value is machine-consumed, compared, calculated, or constrained.",
                ],
                confirm=[
                    "A numeric, monetary, temporal, or status field lacks a material type, unit, precision, timezone, enum, or range.",
                    "Empty, null, duplicate, overflow, rollover, or extrema behavior can change the result and is undefined.",
                ],
                reject=[
                    "The field is free-form prose with no material machine constraint.",
                    "A complete cited schema owns the type and value constraints.",
                    "Types, units, ranges, enums, and applicable boundary behavior are explicit.",
                ],
                insufficient=[
                    "Mark insufficient when the field schema is referenced but not available as evidence.",
                ]),
        ), extract_field_constraint_gap,
    ),

    "semantic.prompt.error_response_contract_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.error_response_contract_gap",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "errorGapKind", "enum",
                enum=["schema", "reason_code", "recoverability",
                      "format_consistency"])],
            subjectKeyFields=["errorGapKind"],
            falsificationQuestion=(
                "Does an applicable failure or refusal path lack a stable "
                "response schema, reason, recoverability, or format contract?"),
            guidanceId="semantic.prompt.error_response_contract_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt defines failure, refusal, invalid-input, missing-information, or permission-denied behavior whose output is consumed or displayed.",
                ],
                confirm=[
                    "Failure classes can emit incompatible or unspecified shapes where a stable consumer contract is required.",
                    "The response omits a material reason/code or whether the caller may retry, clarify, or stop.",
                ],
                reject=[
                    "A conversational refusal needs no machine-readable error schema.",
                    "The declared error schema, reason, and recovery action consistently cover the material failure classes.",
                    "A named external protocol unambiguously owns the error contract.",
                ],
                insufficient=[
                    "Mark insufficient when the consumer or external error schema is not evidenced.",
                ]),
        ), extract_error_response_contract_gap,
    ),

    "semantic.prompt.attention_dilution": (
        SemanticFindingType(
            findingType="semantic.prompt.attention_dilution",
            engine="prompt", defaultSeverity="low",
            subjectFields=[SemanticSubjectField(
                "dilutionKind", "enum",
                enum=["buried_critical_rule", "redundant_context",
                      "section_disorder"])],
            subjectKeyFields=["dilutionKind"],
            falsificationQuestion=(
                "Does a long or multi-section prompt materially bury a "
                "critical rule, repeat low-value context, or obscure hierarchy?"),
            guidanceId="semantic.prompt.attention_dilution",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt is long or multi-section enough that placement, hierarchy, and repetition can affect instruction salience.",
                ],
                confirm=[
                    "A critical safety, output, or authority rule appears only after extensive unrelated background without an authoritative summary or reference.",
                    "Repeated background, examples, or requirements add no decision-relevant information and materially obscure the operative instructions.",
                    "Responsibilities, inputs, workflow, output, and safety rules are interleaved so their precedence or ownership is unclear.",
                ],
                reject=[
                    "The prompt is short or has a clear authoritative summary and navigable hierarchy.",
                    "Long reference material is explicitly data and is separated from operative instructions.",
                    "Repeated text is a bounded summary or intentional cross-reference rather than duplicate instruction weight.",
                ],
                insufficient=[
                    "Mark insufficient when only an excerpt of the prompt is available.",
                ]),
        ), extract_attention_dilution,
    ),

    "semantic.prompt.streaming_recovery_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.streaming_recovery_gap",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "streamingGapKind", "enum",
                enum=["framing", "completion", "resume",
                      "partial_parse"])],
            subjectKeyFields=["streamingGapKind"],
            falsificationQuestion=(
                "Does an explicitly streamed or incremental result omit a "
                "material framing, completion, resume, or partial-parse rule?"),
            guidanceId="semantic.prompt.streaming_recovery_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt explicitly requires streaming, incremental, chunked, resumable, or event-based output.",
                ],
                confirm=[
                    "Chunks lack a stable delimiter, event type, sequence, or independently parseable frame where the consumer needs one.",
                    "Completion, interruption, duplicate delivery, partial parse, or resume behavior is materially undefined.",
                ],
                reject=[
                    "The prompt requests one complete non-streamed response.",
                    "Framing, ordering, completion, interruption, and resume behavior are explicit for the consumer.",
                    "The transport protocol named by the prompt fully owns these semantics.",
                ],
                insufficient=[
                    "Mark insufficient when the referenced transport contract is unavailable.",
                ]),
        ), extract_streaming_recovery_gap,
    ),

    "semantic.prompt.multi_turn_state_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.multi_turn_state_gap",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "stateGapKind", "enum",
                enum=["inheritance", "update", "reset",
                      "non_overridable_rule"])],
            subjectKeyFields=["stateGapKind"],
            falsificationQuestion=(
                "Does a multi-turn task omit a material state inheritance, "
                "update, reset, or non-overridable-rule contract?"),
            guidanceId="semantic.prompt.multi_turn_state_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt expects behavior or data to persist or change across conversation turns or sessions.",
                ],
                confirm=[
                    "It is unclear which prior facts, preferences, decisions, or constraints carry forward.",
                    "Conflicting later requests have no update precedence, reset rule, or protected invariant.",
                    "Session boundaries or requests to forget state have no defined effect.",
                ],
                reject=[
                    "The task is stateless or intentionally handles only the current message.",
                    "Inherited state, mutable preferences, reset behavior, and non-overridable rules are explicit.",
                ],
                insufficient=[
                    "Mark insufficient when state is owned by an unseen application layer.",
                ]),
        ), extract_multi_turn_state_gap,
    ),

    "semantic.prompt.safety_policy_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.safety_policy_gap",
            engine="prompt", defaultSeverity="high",
            subjectFields=[SemanticSubjectField(
                "safetyGapKind", "enum",
                enum=["refusal_boundary", "safe_alternative",
                      "escalation", "allowed_scope"])],
            subjectKeyFields=["safetyGapKind"],
            falsificationQuestion=(
                "Does a prompt handling a declared high-risk domain omit a "
                "material refusal, allowed-scope, alternative, or escalation rule?"),
            guidanceId="semantic.prompt.safety_policy_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt explicitly handles dangerous, illegal, self-harm, weapon, malware, violence, explosive, or comparably high-risk requests.",
                ],
                confirm=[
                    "It authorizes actionable harmful assistance without a clear allowed-versus-refused boundary.",
                    "A refusal is required but no safe alternative, emergency path, or escalation behavior is defined where materially appropriate.",
                ],
                reject=[
                    "The prompt only discusses safety policy or benign prevention at a high level.",
                    "Allowed scope, refusal boundary, safe alternatives, and applicable escalation are explicit.",
                    "A cited enforced policy owns these boundaries and is available in evidence.",
                ],
                insufficient=[
                    "Mark insufficient when the governing safety policy is referenced but absent.",
                ]),
        ), extract_safety_policy_gap,
    ),

    "semantic.prompt.source_use_policy_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.source_use_policy_gap",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "sourceGapKind", "enum",
                enum=["attribution", "reproduction_limit",
                      "transformation", "ownership_status"])],
            subjectKeyFields=["sourceGapKind"],
            falsificationQuestion=(
                "Does a task using third-party source material omit a material "
                "attribution, reproduction, transformation, or ownership rule?"),
            guidanceId="semantic.prompt.source_use_policy_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt directs quoting, copying, reproducing, summarizing, or transforming identifiable source material.",
                ],
                confirm=[
                    "It requests extensive or verbatim reproduction without a bounded excerpt, summary, transformation, or unavailable-content fallback.",
                    "Attribution or source identity is required by the task but omitted.",
                    "Ownership, license, public-domain, or user-provided status materially changes what may be reproduced and is unresolved.",
                ],
                reject=[
                    "The task uses user-owned, public-domain, or explicitly licensed material within the declared permission.",
                    "It requests a bounded short excerpt, facts, summary, or transformation with appropriate source attribution.",
                    "The prompt only creates original material inspired by high-level concepts.",
                ],
                insufficient=[
                    "Mark insufficient when ownership or license status cannot be established from evidence.",
                ]),
        ), extract_source_use_policy_gap,
    ),
}


def entry(finding_type: str) -> Optional[Tuple[SemanticFindingType, Extractor]]:
    return CATALOG.get(finding_type)
