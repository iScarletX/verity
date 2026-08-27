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
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Tuple


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


def _right_boundary_ok(text: str, idx: int, term: str) -> bool:
    """True if nothing immediately after `term` at `idx` continues the same
    word -- allowing one trailing "s" (simple plural) before the boundary,
    so "shells"/"terminals"/"books" still count but "shellfish"/
    "terminally"/"bookkeeping" don't."""
    end = idx + len(term)
    if end >= len(text) or not text[end].isalpha():
        return True
    if text[end] == "s":
        end += 1
        return end >= len(text) or not text[end].isalpha()
    return False


def _term_hit_count(term: str, text: str, *, whole_word: bool = False) -> int:
    """Count occurrences of `term` in `text`.

    ASCII terms require a non-letter (or start-of-string) boundary right
    before the match, so a short bare-verb form like "read " or "edits "
    doesn't false-match inside an unrelated longer word ("widespread ",
    "credits "). Non-ASCII (e.g. Chinese) terms use plain substring
    counting: CJK text has no space-delimited word boundaries, so the
    same letter-boundary check would reject legitimate matches like "读取"
    appearing right after another Chinese character.

    ``whole_word=True`` additionally requires the match not continue into
    an unrelated longer word on the right ("shell" in "shellfish",
    "terminal" in "terminally") -- the default left-only check can't catch
    those since the false match still starts a word. A trailing "s" is
    still accepted so plain plurals ("shells", "books") keep matching.
    """
    if not term.isascii():
        return text.count(term)
    count = 0
    idx = text.find(term)
    while idx != -1:
        left_ok = idx == 0 or not text[idx - 1].isalpha()
        right_ok = not whole_word or _right_boundary_ok(text, idx, term)
        if left_ok and right_ok:
            count += 1
        idx = text.find(term, idx + 1)
    return count


def _term_hit_present(term: str, text: str, *, whole_word: bool = False) -> bool:
    return _term_hit_count(term, text, whole_word=whole_word) > 0


def _first_boundary_index(term: str, text: str, *, whole_word: bool = False) -> int:
    """Like str.find, but honors the same boundary rule as `_term_hit_count`."""
    if not term.isascii():
        return text.find(term)
    idx = text.find(term)
    while idx != -1:
        left_ok = idx == 0 or not text[idx - 1].isalpha()
        right_ok = not whole_word or _right_boundary_ok(text, idx, term)
        if left_ok and right_ok:
            return idx
        idx = text.find(term, idx + 1)
    return -1


def _sum_term_hits(text: str, terms: Tuple[str, ...],
                    boundary_terms: FrozenSet[str] = frozenset(),
                    whole_word_terms: FrozenSet[str] = frozenset()) -> int:
    """Sum occurrences of `terms` in `text`, boundary-checking only the terms
    named in `boundary_terms` (bare words known to collide with an unrelated
    longer word, e.g. "law" in "flaw") or `whole_word_terms` (bare words that
    are themselves a prefix of an unrelated longer word, e.g. "shell" in
    "shellfish"). Other terms keep plain substring counting, since some
    collisions are intentional (e.g. "write" should still count inside
    "overwrite")."""
    return sum(
        _term_hit_count(term, text, whole_word=True) if term in whole_word_terms
        else _term_hit_count(term, text) if term in boundary_terms
        else text.count(term)
        for term in terms
    )


def _any_term_hit(text: str, terms: Tuple[str, ...],
                   boundary_terms: FrozenSet[str] = frozenset(),
                   whole_word_terms: FrozenSet[str] = frozenset()) -> bool:
    """Presence check counterpart to `_sum_term_hits` -- see its docstring."""
    return any(
        _term_hit_present(term, text, whole_word=True) if term in whole_word_terms
        else _term_hit_present(term, text) if term in boundary_terms
        else term in text
        for term in terms
    )


def _hit_terms(text: str, terms: Tuple[str, ...],
                boundary_terms: FrozenSet[str] = frozenset(),
                whole_word_terms: FrozenSet[str] = frozenset()) -> List[str]:
    """List-producing counterpart to `_any_term_hit` -- see its docstring."""
    return [
        term for term in terms
        if (_term_hit_present(term, text, whole_word=True) if term in whole_word_terms
            else _term_hit_present(term, text) if term in boundary_terms
            else term in text)
    ]


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


_FORMAT_BOUNDARY_TERMS = frozenset({"table", "structured"})
# Bare "string"/"array" are suffixes of "hamstring"/"disarray" -- a plain
# left-boundary check is enough. Bare "list"/"object" are also prefixes of
# an unrelated longer word ("listen"/"objective") so they need the fuller
# whole-word check (which also covers the left side).
_TYPE_BOUNDARY_TERMS = frozenset({"string", "array"})
_TYPE_WHOLE_WORD_TERMS = frozenset({"object", "list"})
# Bare "unit" is a substring of "community"/"immunity" (suffix) and a
# prefix of "united"/"unity" -- whole-word covers both directions.
_UNIT_WHOLE_WORD_TERMS = frozenset({"unit"})
# Bare "enum" is a prefix of "enumerate"/"enumeration".
_ENUM_WHOLE_WORD_TERMS = frozenset({"enum"})


def _output_contract_metadata(text: str) -> Dict[str, Any]:
    requested = [
        name for name, terms in _FORMAT_TERMS.items()
        if _any_term_hit(text, terms, boundary_terms=_FORMAT_BOUNDARY_TERMS)
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
            _sum_term_hits(text, _TYPE_TERMS, boundary_terms=_TYPE_BOUNDARY_TERMS,
                           whole_word_terms=_TYPE_WHOLE_WORD_TERMS), 32),
        "requirednessMarkerCount": min(
            sum(text.count(term) for term in _REQUIRED_TERMS), 32),
        "enumMarkerCount": min(
            _sum_term_hits(text, _ENUM_TERMS, whole_word_terms=_ENUM_WHOLE_WORD_TERMS), 32),
        "unitMarkerCount": min(
            _sum_term_hits(text, _UNIT_TERMS, whole_word_terms=_UNIT_WHOLE_WORD_TERMS), 32),
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
# Bare "never " is a substring of "whenever " (suffix collision); bare
# "only " is a substring of "commonly " (also a suffix collision). Both
# already carry a trailing space, which pins the right edge, so a
# left-boundary-only check is enough.
_STRONG_CONSTRAINT_BOUNDARY_TERMS = frozenset({"never ", "only "})


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
        if _any_term_hit(text, _STRONG_CONSTRAINT_MARKERS,
                          boundary_terms=_STRONG_CONSTRAINT_BOUNDARY_TERMS):
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
                       allow_without_trigger=False,
                       boundary_terms=frozenset(),
                       whole_word_terms=frozenset()):
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
    found = _hit_terms(text, triggers, boundary_terms=boundary_terms,
                        whole_word_terms=whole_word_terms)
    if not found and not allow_without_trigger:
        return []
    if require_all_groups and not all(
            _any_term_hit(text, group, boundary_terms=boundary_terms,
                          whole_word_terms=whole_word_terms)
            for group in require_all_groups):
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


_MARKDOWN_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:[ \t]+|$)")
_MARKDOWN_LIST_ITEM_RE = re.compile(
    r"^\s{0,3}(?:[-+*]|\d{1,3}[.)])[ \t]+")
_LOCAL_RULE_MAX_LINES = 4
_LOCAL_RULE_MAX_CHARS = 512
_LOCAL_RULE_CHUNK_OVERLAP = 64


def _local_rule_windows(text):
    """Return bounded Markdown-aware windows for authored prompt rules."""
    windows = []
    current = []
    current_kind = ""

    def flush():
        nonlocal current, current_kind
        if current:
            block = "\n".join(current)
            step = _LOCAL_RULE_MAX_CHARS - _LOCAL_RULE_CHUNK_OVERLAP
            for start in range(0, len(block), step):
                window = block[start:start + _LOCAL_RULE_MAX_CHARS].strip()
                if window:
                    windows.append(window)
                if start + _LOCAL_RULE_MAX_CHARS >= len(block):
                    break
        current = []
        current_kind = ""

    def can_append(line):
        return (
            len(current) < _LOCAL_RULE_MAX_LINES
            and sum(len(item) for item in current) + len(current) + len(line)
            <= _LOCAL_RULE_MAX_CHARS
        )

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            flush()
            continue

        if _MARKDOWN_HEADING_RE.match(raw_line):
            flush()
            current = [line]
            current_kind = "heading"
            continue

        if _MARKDOWN_LIST_ITEM_RE.match(raw_line):
            if current_kind == "heading" and can_append(line):
                current.append(line)
                current_kind = "list"
                continue
            flush()
            current = [line]
            current_kind = "list"
            continue

        indented_continuation = (
            raw_line.startswith("\t")
            or len(raw_line) - len(raw_line.lstrip(" ")) >= 2
        )
        attach = (
            current_kind == "heading"
            or (current and indented_continuation)
        )
        if not attach or not can_append(line):
            flush()
            current = [line]
        else:
            current.append(line)
        if current_kind == "heading":
            current_kind = "rule"
        elif not current_kind:
            current_kind = "rule"

    flush()
    return windows


def _scoped_gap_count(text, *, signal_groups, control_terms,
                      defeating_terms=(), boundary_terms=frozenset(),
                      control_boundary_terms=frozenset()):
    """Count locally unsupported signal windows without cross-section vetoes.

    Whole-document counts are useful routing facts, but a control in one
    rule must not silently cancel a risky operation in another. Keep controls
    inside the same bounded Markdown rule window as their signals.

    ``boundary_terms`` names specific bare signal terms (e.g. "law", "all ")
    whose plain-substring match collides with an unrelated longer word
    ("flaw", "overall ") -- those are checked with the boundary-aware
    counter instead of plain substring containment. Only ``signal_groups``
    membership is checked this way by default; ``control_boundary_terms``
    applies the same treatment to ``control_terms`` (e.g. "cite" colliding
    with "excite") so a false collision can't wrongly mark a window as
    covered. ``defeating_terms`` are unaffected since none of the
    currently-flagged bare terms appear there.
    """
    total = 0
    uncovered = 0
    for window in _local_rule_windows(text):
        if not all(
                any(_term_hit_present(term, window) if term in boundary_terms
                    else term in window for term in group)
                for group in signal_groups):
            continue
        total += 1
        has_control = any(
            _term_hit_present(term, window) if term in control_boundary_terms
            else term in window for term in control_terms)
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
    # "read the"/"read supplied" alone miss ordinary third-person prose
    # ("Reads the uploaded file...") because the conjugated "reads" breaks
    # the compound-phrase match; "reads "/"read " give the same bare-verb
    # fallback already used for the sibling _TOOL_HIGH_IMPACT_TERMS list.
    "only read", "read the", "read supplied", "reads ", "read ",
    "summarize", "summarization", "summary task", "classify",
    "只需读取", "读取摘要", "只读", "读取", "摘要", "分类",
)
_TOOL_NO_APPROVAL_TERMS = (
    "without approval", "without asking", "act immediately", "无需批准",
    "无需询问", "立即执行",
)


_TOOL_HIGH_IMPACT_BOUNDARY_TERMS = frozenset({"edit"})
_TOOL_HIGH_IMPACT_WHOLE_WORD_TERMS = frozenset({"shell", "terminal"})


def _tool_scope_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=["declared_tool_scope"],
        toolDeclarationCount=sum(text.count(x) for x in _TOOL_SCOPE_TERMS),
        approvalSignalCount=sum(text.count(x) for x in _TOOL_BOUNDARY_TERMS),
        highImpactToolSignalCount=_sum_term_hits(
            text, _TOOL_HIGH_IMPACT_TERMS,
            boundary_terms=_TOOL_HIGH_IMPACT_BOUNDARY_TERMS,
            whole_word_terms=_TOOL_HIGH_IMPACT_WHOLE_WORD_TERMS),
        # Unlike _TOOL_HIGH_IMPACT_TERMS's bare "write"/"edit" (where a
        # plain-substring match inside "overwrite"/"re-edited" is still a
        # correct write/edit signal), this tuple's bare "read "/"reads "
        # have no such upside -- "widespread "/"threads " are unrelated
        # words, not read variants -- so this one needs the boundary-aware
        # counter to avoid a false read-only-task signal.
        readOnlyTaskSignalCount=sum(
            _term_hit_count(x, text) for x in _TOOL_READ_ONLY_TASK_TERMS),
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
    # Round 154: paraphrase expansion of the same "requesting an exhaustive,
    # nothing-omitted output" trigger concept -- the sibling AND-gate half
    # (_BUDGET_LIMIT_TERMS) and the separately-gated _PRIORITY_TERMS/
    # _CONTINUATION_TERMS control groups are untouched. First touch of this
    # tuple (deferred from Round 143 pending a dual-group-shape precedent,
    # since established by Rounds 137/151's _AUTONOMY_TERMS work).
    "spare no detail", "cover absolutely everything",
    "go through the entire process", "hold back nothing",
    "不放过任何细节", "务必面面俱到", "从头到尾梳理整个流程", "毫无保留地说明",
    # Round 168: second touch, same discipline as Round 154 -- another
    # paraphrase expansion of the same "requesting an exhaustive,
    # nothing-omitted output" trigger concept. This is the globally
    # sparsest tuple now that every tuple discovered by the triggers= scan
    # carries at least one prior "Round N" touch comment (the exhaustion
    # first identified in Round 164) and the Round 167 tie with
    # _ERROR_RESPONSE_TERMS resolved in that tuple's favor, leaving this
    # one alone at 22. The sibling AND-gate half (_BUDGET_LIMIT_TERMS) and
    # the separately-gated _PRIORITY_TERMS/_CONTINUATION_TERMS control
    # groups remain untouched.
    "leave nothing out", "cover the process from start to finish",
    "explain in full detail", "provide a thorough rundown",
    "不要遗漏任何内容", "把每一步都讲清楚", "把细节讲得非常透彻", "提供彻底的说明",
)
_BUDGET_LIMIT_TERMS = (
    "brief", "concise", "short", "under ", "at most", "no more than",
    "token", "words", "characters", "简洁", "精简", "不超过", "以内",
    "字", "字符",
    # Round 155: paraphrase expansion of the same "short/limited output
    # length constraint" trigger concept -- the sibling AND-gate half
    # (_BUDGET_PRESSURE_TERMS, closed in Round 154) and the separately-gated
    # _PRIORITY_TERMS/_CONTINUATION_TERMS control groups are untouched.
    # First touch of this tuple, completing the pair Round 143 deferred.
    "keep the response minimal", "restrict the response length",
    "trim your answer down", "stay within the length limit",
    "尽量压缩回答内容", "限制回答的长度", "删减回答内容", "控制在长度限制内",
    # Round 171: second touch, same discipline as Round 155 -- another
    # paraphrase expansion of the same "short/limited output length
    # constraint" trigger concept. This is the sole sparsest tuple after
    # Round 170 closed `_WORKFLOW_TERMS` (the last remaining member of the
    # prior 23-phrase tier). The sibling AND-gate half
    # (_BUDGET_PRESSURE_TERMS, closed in Round 168) and the separately-gated
    # _PRIORITY_TERMS/_CONTINUATION_TERMS control groups remain untouched.
    "cap the total length", "condense the explanation",
    "keep the answer terse", "impose a strict length ceiling",
    "限定总篇幅", "压缩说明内容", "回答要精炼扼要", "设定严格的篇幅上限",
)
_PRIORITY_TERMS = (
    "prioritize", "priority", "omit first", "if space", "优先", "空间不足",
    "无法全部", "可省略",
)
_CONTINUATION_TERMS = (
    "continue", "continuation", "next response", "分段", "续写", "下一轮",
)
# "all "/"each " are bare words that collide with an unrelated longer word
# ("overall ", "teach ") -- boundary-checked; the rest keep plain counting.
_BUDGET_PRESSURE_BOUNDARY_TERMS = frozenset({"all ", "each "})
# Bare "continue" is a substring of "discontinue" -- which means the
# opposite (stop), so this is a real false-positive risk, not an
# intentional collision like "write" inside "overwrite".
_CONTINUATION_BOUNDARY_TERMS = frozenset({"continue"})


def _budget_metadata(text):
    _total, uncovered = _scoped_gap_count(
        text,
        signal_groups=(_BUDGET_PRESSURE_TERMS, _BUDGET_LIMIT_TERMS),
        control_terms=_PRIORITY_TERMS + _CONTINUATION_TERMS,
        boundary_terms=_BUDGET_PRESSURE_BOUNDARY_TERMS,
        control_boundary_terms=_CONTINUATION_BOUNDARY_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["output_volume", "output_limit"],
        pressureSignalCount=_sum_term_hits(
            text, _BUDGET_PRESSURE_TERMS,
            boundary_terms=_BUDGET_PRESSURE_BOUNDARY_TERMS),
        limitSignalCount=sum(text.count(x) for x in _BUDGET_LIMIT_TERMS),
        prioritySignalCount=sum(text.count(x) for x in _PRIORITY_TERMS),
        continuationSignalCount=_sum_term_hits(
            text, _CONTINUATION_TERMS,
            boundary_terms=_CONTINUATION_BOUNDARY_TERMS),
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
        model_candidate_gate=_budget_model_gate,
        boundary_terms=_BUDGET_PRESSURE_BOUNDARY_TERMS)


_AUTONOMY_TERMS = (
    "autonomously", "without asking", "do not ask", "take initiative",
    "act immediately", "自行", "自主", "无需询问", "不要询问", "立即执行",
    # Round 137: paraphrase expansion of the same "acting autonomously
    # without approval" trigger concept -- the counterpart term group in
    # this finding type's require_all_groups AND-gate. _SIDE_EFFECT_TERMS
    # was already widened in Round 133; this round widens _AUTONOMY_TERMS
    # instead, following the same discipline.
    "without waiting for approval", "proceed without confirmation",
    "at your own discretion", "no need to check first",
    "无需等待许可", "无需确认即可执行", "全权处理", "不必核实",
    # Round 151: paraphrase expansion of the same "acting autonomously
    # without approval" trigger concept -- this is the sparsest single
    # primary-vocabulary tuple after Round 150 closed _EXAMPLE_TERMS. The
    # AND-gate (require_all_groups=(_AUTONOMY_TERMS, _SIDE_EFFECT_TERMS))
    # and the uncoveredAutonomousActionCount gap-count logic are unchanged;
    # this only widens which phrases can satisfy the autonomy half.
    "act without oversight", "skip the review process",
    "you have full authority to", "no sign-off needed",
    "不受监督地执行", "跳过审核流程", "你被授予完全决定权", "无需上级同意",
    # Round 178: third touch, same discipline as Round 151 -- another
    # paraphrase expansion of the same "acting autonomously without
    # approval/oversight" trigger concept. This is the older half of the
    # remaining two-way tie at 26 phrases (_AUTONOMY_TERMS/_SENSITIVE_
    # DATA_ACTION_TERMS) once Round 177 closed _EXAMPLE_TERMS; last-touch
    # rounds are 151/157 respectively, so _AUTONOMY_TERMS is picked. The
    # AND-gate (require_all_groups=(_AUTONOMY_TERMS, _SIDE_EFFECT_TERMS))
    # and uncoveredAutonomousActionCount gap-count logic are unchanged;
    # this only widens which phrases can satisfy the autonomy half.
    "use your own judgment", "bypass the approval chain",
    "act on your own accord", "you don't need permission",
    "凭自己判断处理", "绕过审批流程", "按个人意愿行事", "无需获得许可",
)
_SIDE_EFFECT_TERMS = (
    "send ", "publish", "deploy", "purchase", "delete", "transfer",
    "approve", "reject", "modify account", "发出", "发布", "部署", "购买",
    "删除", "转账", "批准", "拒绝", "修改账户",
    # Round 133: paraphrase expansion within the risk's own 6 declared
    # operationKinds categories (communication/publication/deployment/
    # financial/destructive/access_control) -- no new category added.
    # Each new phrase is qualified (not a bare generic word) to keep the
    # same low-false-positive discipline as Round 131's vocabulary work.
    "notify the customer", "post publicly", "push to production",
    "withdraw funds", "wipe the data", "revoke access",
    "通知客户", "公开发布", "上线生产环境",
    "提取资金", "清除所有记录", "撤销权限",
    # Round 187: second touch, same discipline as Round 133 -- another
    # within-category paraphrase expansion (communication/publication/
    # deployment/financial), still no new category. Each new phrase is
    # matched into its category in _authority_metadata's classification
    # dict below, mirroring Round 133's own convention.
    "alert the account holder", "make it publicly visible",
    "activate it in the live environment", "issue a payout",
    "提醒账户所有者", "对外公开可见", "在正式环境中启用", "发放款项",
)
# Bare "approve" is a substring of "disapprove" -- the opposite meaning,
# so an unprotected match is a real false positive, not an intentional
# collision.
_SIDE_EFFECT_BOUNDARY_TERMS = frozenset({"approve"})
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
            ("communication", ("send ", "发出", "发送", "notify the customer",
                                "通知客户", "alert the account holder",
                                "提醒账户所有者")),
            ("publication", ("publish", "发布", "post publicly", "公开发布",
                              "make it publicly visible", "对外公开可见")),
            ("deployment", ("deploy", "部署", "push to production",
                             "上线生产环境", "activate it in the live environment",
                             "在正式环境中启用")),
            ("financial", ("purchase", "transfer", "购买", "转账",
                            "withdraw funds", "提取资金", "issue a payout",
                            "发放款项")),
            ("destructive", ("delete", "删除", "wipe the data", "清除所有记录")),
            ("access_control", ("approve", "reject", "修改账户", "批准", "拒绝",
                                 "revoke access", "撤销权限")),
        ) if _any_term_hit(text, terms, boundary_terms=_SIDE_EFFECT_BOUNDARY_TERMS)
    ]
    _total, uncovered = _scoped_gap_count(
        text,
        signal_groups=(_AUTONOMY_TERMS, _SIDE_EFFECT_TERMS),
        control_terms=_APPROVAL_TERMS,
        defeating_terms=_NO_APPROVAL_TERMS,
        boundary_terms=_SIDE_EFFECT_BOUNDARY_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["autonomous_action", "external_side_effect"],
        autonomySignalCount=sum(text.count(x) for x in _AUTONOMY_TERMS),
        sideEffectSignalCount=_sum_term_hits(
            text, _SIDE_EFFECT_TERMS,
            boundary_terms=_SIDE_EFFECT_BOUNDARY_TERMS),
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
    # Round 160: paraphrase expansion of the same "failure-prone external
    # operation" trigger concept -- the separately-scoped
    # _FAILURE_STRATEGY_TERMS group is untouched. First touch of this tuple.
    "invoke a third-party service", "query a remote data store",
    "make an outbound network call", "look up records in an external system",
    "调用第三方服务", "查询远程数据存储", "发起外发网络调用", "在外部系统中查找记录",
    # Round 185: second touch, same discipline as Round 160 -- another
    # paraphrase expansion of the same "invoking a failure-prone external/
    # remote operation" trigger concept. The separately-scoped
    # _FAILURE_STRATEGY_TERMS control group and the _FAILURE_OPERATION_
    # BOUNDARY_TERMS guard on bare "api"/"parse" are untouched.
    "reach out to a remote endpoint", "contact a third-party gateway",
    "consult an external index service",
    "pull data from a downstream integration",
    "联系远程端点", "联络第三方网关", "查询外部索引服务", "从下游集成中拉取数据",
)
_FAILURE_STRATEGY_TERMS = (
    "timeout", "retry", "backoff", "fallback", "empty result",
    "malformed", "structured error", "partial failure", "超时", "重试",
    "退避", "回退", "空结果", "格式错误", "结构化错误", "部分失败",
)
# "api" collides with "rapidly" and "parse" collides with "sparse" --
# boundary-checked; the rest keep plain counting.
_FAILURE_OPERATION_BOUNDARY_TERMS = frozenset({"api", "parse"})


def _failure_metadata(text):
    operations = [
        name for name, terms in (
            ("network_call", ("api", "http request", "http call",
                              "http endpoint", "fetch", "接口", "请求")),
            ("retrieval", ("retrieve", "search", "检索", "搜索")),
            ("parsing", ("parse", "decode", "解析", "解码")),
            ("database", ("database", "数据库")),
            ("tool_call", ("tool call", "工具调用")),
        ) if _any_term_hit(text, terms,
                            boundary_terms=_FAILURE_OPERATION_BOUNDARY_TERMS)
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
        boundary_terms=_FAILURE_OPERATION_BOUNDARY_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["failure_prone_operation"],
        operationKinds=operations,
        strategyKinds=strategies,
        operationSignalCount=_sum_term_hits(
            text, _FAILURE_OPERATION_TERMS,
            boundary_terms=_FAILURE_OPERATION_BOUNDARY_TERMS),
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
        model_candidate_gate=_failure_model_gate,
        boundary_terms=_FAILURE_OPERATION_BOUNDARY_TERMS)


_VAGUE_CRITERIA_TERMS = (
    "appropriate", "reasonable", "as needed", "when necessary",
    "sufficiently", "high quality", "brief", "concise", "detailed",
    "comprehensive", "complex",
    "long content", "content is long", "适当", "合理", "酌情", "必要时",
    "尽量", "足够", "高质量", "简洁", "详细", "详尽", "复杂", "内容较长",
    # Round 163: paraphrase expansion of the same "vague operational
    # criterion lacking a concrete threshold, referent, example, or
    # decision rule" trigger concept -- the sibling OR-trigger group
    # (_VISUAL_STYLE_TERMS, part of the same concatenated triggers=
    # argument but not an AND-gate partner) and the separately-read
    # _BOUNDARY_CRITERIA_TERMS/_VISUAL_TASK_DIRECTIVES/_VISUAL_SUBJECT_
    # ANCHORS groups are untouched. First touch of this tuple.
    "to your best judgment", "keep it succinct", "as polished as possible",
    "to a suitable degree", "凭你的判断", "力求精炼", "尽善尽美", "适度处理",
)
_BOUNDARY_CRITERIA_TERMS = (
    "at least", "at most", "exactly", "between ", "if ", "when ",
    "characters", "words", "items", "至少", "至多", "恰好", "介于",
    "如果", "当", "字", "条",
)
# Bare "if " is a substring of "motif "/"motifs " -- needs the left-boundary
# counter so those don't count as a boundary marker.
_BOUNDARY_CRITERIA_BOUNDARY_TERMS = frozenset({"if "})
# Bare "appropriate"/"reasonable"/"sufficiently" are substrings of
# "inappropriate"/"unreasonable"/"insufficiently" -- the *opposite* meaning,
# so an unprotected match is a real false positive.
_VAGUE_CRITERIA_BOUNDARY_TERMS = frozenset(
    {"appropriate", "reasonable", "sufficiently"})
_VISUAL_STYLE_TERMS = (
    "photorealistic", "cinematic", "film still", "realistic actor",
    "natural lighting", "skin texture", "fabric detail", "visual style",
    "真人写实", "电影剧照", "真实演员", "实景光源", "皮肤纹理",
    "布料细节", "环境质感", "画风", "视觉风格",
    # Round 156: paraphrase expansion of the same "detailed photorealistic/
    # cinematic visual style description" trigger concept -- the sibling
    # OR-trigger group (_VAGUE_CRITERIA_TERMS, part of the same concatenated
    # triggers= argument but not an AND-gate partner) and the separately
    # read _VISUAL_TASK_DIRECTIVES/_VISUAL_SUBJECT_ANCHORS groups are
    # untouched. First touch of this tuple.
    "ultra-realistic rendering", "movie-grade visual quality",
    "studio-quality lighting setup", "lifelike material texture",
    "超写实渲染", "电影级画质", "专业级摄影棚布光", "逼真材质质感",
    # Round 174: second touch, same discipline as Round 156 -- another
    # paraphrase expansion of the same "detailed photorealistic/cinematic
    # visual style description" trigger concept. Re-running the systematic
    # scan after Round 173 closed `_FIELD_CONTRACT_TERMS` leaves this tuple
    # as the sole sparsest, no tie. The sibling OR-trigger group
    # (_VAGUE_CRITERIA_TERMS) and the separately read
    # _VISUAL_TASK_DIRECTIVES/_VISUAL_SUBJECT_ANCHORS groups remain
    # untouched. An earlier candidate ("hyper-detailed texture rendering")
    # was rejected: it bare-contained "detailed", itself a
    # _VAGUE_CRITERIA_TERMS entry, which would have leaked into the
    # sibling OR-trigger's vagueCriterionCount.
    "high-fidelity render", "ray-traced lighting",
    "cinema-grade color grading", "hyper-realistic texture rendering",
    "高保真渲染", "光线追踪光照", "电影级调色", "超逼真纹理渲染",
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
        vagueCriterionCount=_sum_term_hits(
            text, _VAGUE_CRITERIA_TERMS,
            boundary_terms=_VAGUE_CRITERIA_BOUNDARY_TERMS),
        boundaryMarkerCount=_sum_term_hits(
            text, _BOUNDARY_CRITERIA_TERMS,
            boundary_terms=_BOUNDARY_CRITERIA_BOUNDARY_TERMS),
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
        allow_without_trigger=True,
        boundary_terms=_VAGUE_CRITERIA_BOUNDARY_TERMS)


_GROUNDING_TASK_TERMS = (
    "law", "legal", "medical", "health", "financial", "tax", "fact",
    "statistics", "citation", "source", "research", "法律", "医疗", "健康",
    "金融", "财务", "税务", "事实", "统计", "引用", "来源", "研究",
    # Round 161: paraphrase expansion of the same "consequential or
    # verifiable-claim domain" trigger concept -- the separately-gated
    # _GROUNDING_CONTROL_TERMS group is untouched. First touch of this
    # tuple.
    "clinical diagnosis or treatment plan", "investment or portfolio guidance",
    "court ruling or case precedent", "peer-reviewed empirical findings",
    "临床诊断或治疗方案", "投资组合建议", "法庭裁决或判例", "同行评审的实证结论",
    # Round 188: second touch, same discipline as Round 161 -- another
    # paraphrase expansion of the same "consequential or verifiable-claim
    # domain" trigger concept. The separately-gated _GROUNDING_CONTROL_TERMS
    # group and the `domains` categorization dict below are both untouched,
    # mirroring Round 161's own choice to leave new phrases domain-
    # unclassified.
    "prescription drug dosage guidance", "regulatory compliance filing",
    "actuarial risk calculation", "systematic review meta-analysis",
    "处方药剂量指导", "监管合规备案", "精算风险计算", "系统综述荟萃分析",
)
_GROUNDING_CONTROL_TERMS = (
    "cite", "verify", "source", "uncertain", "do not guess", "do not invent",
    "human review", "核实", "引用", "来源", "不确定", "不要猜测", "不得编造",
    "人工复核",
)
# "law"/"fact"/"tax" are bare words that collide with an unrelated longer
# word ("flaw", "satisfaction", "syntax") -- boundary-checked; the rest
# keep plain counting.
_GROUNDING_TASK_BOUNDARY_TERMS = frozenset({"law", "fact", "tax"})
# Bare "cite" is a substring of "excite"/"exciting".
_GROUNDING_CONTROL_BOUNDARY_TERMS = frozenset({"cite"})


def _grounding_metadata(text):
    domains = [
        name for name, terms in (
            ("legal", ("law", "legal", "法律")),
            ("medical", ("medical", "health", "医疗", "健康")),
            ("financial", ("financial", "tax", "金融", "财务", "税务")),
            ("factual", ("fact", "statistics", "research", "事实", "统计", "研究")),
            ("citations", ("citation", "source", "引用", "来源")),
        ) if _any_term_hit(text, terms,
                            boundary_terms=_GROUNDING_TASK_BOUNDARY_TERMS)
    ]
    _total, uncovered = _scoped_gap_count(
        text,
        signal_groups=(_GROUNDING_TASK_TERMS,),
        control_terms=_GROUNDING_CONTROL_TERMS,
        boundary_terms=_GROUNDING_TASK_BOUNDARY_TERMS,
        control_boundary_terms=_GROUNDING_CONTROL_BOUNDARY_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["consequential_or_verifiable_claim"],
        operationKinds=domains,
        groundingSignalCount=_sum_term_hits(
            text, _GROUNDING_TASK_TERMS,
            boundary_terms=_GROUNDING_TASK_BOUNDARY_TERMS),
        mitigationSignalCount=_sum_term_hits(
            text, _GROUNDING_CONTROL_TERMS,
            boundary_terms=_GROUNDING_CONTROL_BOUNDARY_TERMS),
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
        model_candidate_gate=_grounding_model_gate,
        boundary_terms=_GROUNDING_TASK_BOUNDARY_TERMS)


_REASONING_TERMS = (
    "chain of thought", "reasoning", "scratchpad", "internal policy",
    "hidden rule", "decision rule", "思维链", "推理过程", "思考过程",
    "内部策略", "隐藏规则", "内部规则", "判断规则",
    # Round 142: paraphrase expansion of the same "chain-of-thought/
    # scratchpad/internal-policy reasoning process" trigger concept -- the
    # separately-gated exposure/containment structural checks inside
    # _reasoning_metadata (_REASONING_EXPOSURE_TERMS/_REASONING_CONTAINMENT_
    # TERMS) are untouched, mirroring Round 134-141's discipline.
    "internal deliberation", "working notes", "concealed logic",
    "thought process",
    "内部推演", "工作笔记", "隐藏逻辑", "思考轨迹",
    # Round 165: second touch, same discipline as Round 142 -- another
    # paraphrase expansion of the same "chain-of-thought/scratchpad/
    # internal-policy reasoning process" trigger concept. This is the
    # sparsest single primary-vocabulary tuple now that every tuple
    # discovered by the triggers= scan carries at least one prior "Round N"
    # touch comment (the first-touch tie-break precedent established in
    # Rounds 137/159-163 ran out of untouched candidates by Round 164,
    # which itself gave `_ATTENTION_STRUCTURE_TERMS` a second touch,
    # 20->28). The separately-gated exposure/containment structural checks
    # (_REASONING_EXPOSURE_TERMS/_REASONING_CONTAINMENT_TERMS) remain
    # untouched.
    "internal thought record", "step-by-step rationale",
    "unstated internal logic", "confidential deliberation notes",
    "内部思考记录", "逐步推理依据", "未言明的内在逻辑", "保密推演记录",
    # Round 186: third touch, same discipline as Rounds 142/165 -- another
    # paraphrase expansion of the same "chain-of-thought/scratchpad/
    # internal-policy reasoning process" trigger concept. The separately-
    # gated exposure/containment structural checks (_REASONING_EXPOSURE_
    # TERMS/_REASONING_CONTAINMENT_TERMS) remain untouched.
    "silent deliberation trail", "unspoken chain of inference",
    "backstage decision logic", "unrecorded internal calculus",
    "静默推演轨迹", "未言明的推理链条", "幕后决策逻辑", "未记录的内部演算",
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
# Bare "print" is a substring of "footprint"/"fingerprint"/"blueprint".
_REASONING_EXPOSURE_BOUNDARY_TERMS = frozenset({"print"})


def _reasoning_metadata(text):
    _total, uncovered = _scoped_gap_count(
        text,
        signal_groups=(_REASONING_TERMS, _REASONING_EXPOSURE_TERMS),
        control_terms=_REASONING_CONTAINMENT_TERMS,
        boundary_terms=_REASONING_EXPOSURE_BOUNDARY_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["reasoning_or_internal_policy"],
        reasoningSignalCount=sum(text.count(x) for x in _REASONING_TERMS),
        exposureSignalCount=_sum_term_hits(
            text, _REASONING_EXPOSURE_TERMS,
            boundary_terms=_REASONING_EXPOSURE_BOUNDARY_TERMS),
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


# Free-form prose pointers to other material in the same document. Unlike
# the deterministic prompt.dangling_section_reference / .named_dangling_
# reference rules (numbered sections / named rules only), whether the
# pointed-to material actually exists and covers the claimed behaviour is
# not decidable by term matching -- it needs the model's reading of the
# whole document. All terms here are multi-word/multi-character phrases,
# so bare-substring collisions are not a concern the way single words like
# "print" or "steps" are elsewhere in this file; no boundary_terms needed.
# Round 112 doubled this list (still a fixed, finite set -- see risks.json
# VR-PROMPT-010's knownGaps) with paraphrases of the original "as X above/
# below" shape that were previously missed: "previously"-anchored variants,
# "per"/"refer to" pointer idioms, and additional Chinese synonyms for
# "as stated above/below" and "the preceding section".
_PROSE_REFERENCE_TERMS = (
    "as described above", "as mentioned above", "as noted above",
    "as outlined above", "as explained above", "as stated above",
    "as detailed above", "described below", "mentioned below",
    "outlined below", "explained below", "the above section",
    "the following section", "the rules above", "the guidelines above",
    "as covered above", "as specified above", "as previously described",
    "as previously mentioned", "as previously stated",
    "as previously outlined", "as previously explained",
    "described previously", "outlined previously", "per the above",
    "per the section above", "refer to the above", "referenced above",
    "covered below", "specified below", "detailed below",
    "the preceding section", "the section above", "the section below",
    "如上所述", "如前所述", "如上文所述", "见上文", "见下文",
    "如下所述", "见前述", "上述规则", "上述要求", "下述规则",
    "详见上文", "详见下文", "参见上述", "参见上文", "参见前文",
    "如前文所述", "按照上述", "遵照上述", "前文提到",
)


def extract_prose_reference_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_PROSE_REFERENCE_TERMS,
        producer_id="extractor.prompt.prose_reference_gap")


# Free-form, prose-level template-incompleteness language. Disjoint from the
# deterministic prompt.unfilled_placeholder rule, which only proves
# mustache/dollar-brace/angle-bracket/square-bracket wrapped placeholder
# syntax (e.g. "{{ name }}", "[INSERT NAME]"); the terms below catch the
# unwrapped prose form of the same authoring-time gap (e.g. "insert your own
# signature block below", "lorem ipsum") that the static rule's regex cannot
# match. All terms are multi-word/multi-character phrases, so bare-substring
# collisions are not a concern here; no boundary_terms needed.
_TEMPLATE_GAP_TERMS = (
    "lorem ipsum", "placeholder text", "to be filled in", "to be completed",
    "still under construction", "content coming soon", "insert your own",
    "fill in your own", "replace this with your own", "add your content here",
    "占位符", "占位内容", "待补充", "待完善", "待填写", "此处填写", "此处插入",
    "尚未完成", "施工中",
    # Round 152: paraphrase expansion of the same "authoring-time template
    # incompleteness expressed in free-form prose" trigger concept -- this
    # is the first touch of this tuple since its Round 94 creation. Still
    # disjoint from the deterministic prompt.unfilled_placeholder rule's
    # mustache/bracket-syntax coverage.
    "not yet finalized", "requires further input from the author",
    "replace before publishing", "first draft pending content",
    "尚未定稿", "需要作者进一步补充信息", "发布前请替换", "初稿待定",
    # Round 180: second touch, same discipline as Round 152 -- another
    # paraphrase expansion of the same "authoring-time template
    # incompleteness expressed in free-form prose" trigger concept. Still
    # disjoint from the deterministic prompt.unfilled_placeholder rule's
    # mustache/bracket-syntax coverage.
    "work in progress, do not distribute",
    "sample content, update prior to launch",
    "author to complete this section",
    "boilerplate text pending revision",
    "内容正在编写中，请勿分发",
    "此为示例内容，上线前需要更新",
    "作者需在此处补充内容",
    "样板文字待修订",
)


def extract_template_completeness_gap(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_TEMPLATE_GAP_TERMS,
        producer_id="extractor.prompt.template_completeness_gap")


# Concrete-value field labels that typically introduce a specific personal,
# financial, medical, or identity-document value rather than an abstract
# handling policy (that structural question is
# semantic.prompt.sensitive_data_handling_gap's disjoint concern). Whether
# the value that follows is a real disclosed value or a fictional/
# anonymized placeholder ("Jane Doe", "example.com", "XXX-XX-XXXX") is not
# decidable by term matching -- it needs the model's reading of the actual
# value and its surrounding context. All terms here are multi-word/multi-
# character phrases, so bare-substring collisions are not a concern the way
# single words like "print" or "steps" are elsewhere in this file; no
# boundary_terms needed.
_EMBEDDED_SENSITIVE_VALUE_TERMS = (
    "social security number", "date of birth", "credit card number",
    "passport number", "driver's license number", "medical record number",
    "patient name", "account number", "routing number",
    "身份证号", "护照号码", "出生日期", "信用卡号", "驾驶证号",
    "病历号", "患者姓名", "银行账号", "社会保障号",
    # Round 149: paraphrase expansion of the same "concrete-value field
    # label introducing a specific personal/financial/medical/identity-
    # document value" trigger concept -- this extractor has no cascade at
    # all (bare _whole_prompt_seed, no metadata_builder/candidate_hint_
    # builder), so the expansion only widens which phrases can seed.
    "tax identification number", "insurance policy number",
    "vehicle registration number", "emergency contact number",
    "税号", "保单号", "车辆登记号", "紧急联系人电话",
    # Round 176: second touch, same discipline as Round 149 -- another
    # paraphrase expansion of the same "concrete-value field label
    # introducing a specific personal/financial/medical/identity-document
    # value" trigger concept. Re-running the systematic scan after Round
    # 175 closed `_ROLE_IDENTITY_TERMS` leaves a four-way tie at 26
    # phrases (_AUTONOMY_TERMS/_EMBEDDED_SENSITIVE_VALUE_TERMS/
    # _EXAMPLE_TERMS/_SENSITIVE_DATA_ACTION_TERMS); per the tied-size
    # tie-break rule (oldest last-touch round wins), this tuple's last
    # touch (Round 149) is older than the other three (150/151/157), so
    # it is picked. This extractor still has no cascade at all (bare
    # _whole_prompt_seed), so the expansion only widens which phrases can
    # seed; no candidateHints behavior to re-verify beyond that absence.
    "national insurance number", "health insurance id number",
    "employee identification number", "card verification code",
    "国民保险号", "医保号", "员工编号", "卡片验证码",
)


def extract_embedded_sensitive_information(review_dict, file_bytes):
    return _whole_prompt_seed(
        review_dict, file_bytes, triggers=_EMBEDDED_SENSITIVE_VALUE_TERMS,
        producer_id="extractor.prompt.embedded_sensitive_information")


_VERIFICATION_TASK_TERMS = (
    "fields", "steps", "requirements", "must include", "schema",
    "title", "summary", "tags", "字段", "步骤", "要求", "必须包含",
    "标题", "摘要", "标签",
    # Round 144: paraphrase expansion of the same "constrained-output task
    # requirement fields/steps/schema" trigger concept -- the
    # separately-gated _VERIFICATION_CONTROL_TERMS/_VERIFICATION_BYPASS_
    # TERMS/_DOWNSTREAM_TERMS groups inside _verification_metadata are
    # untouched, mirroring Round 134-143's discipline.
    "required elements", "output structure", "key attributes",
    "expected sections", "所需要素", "输出结构", "关键属性", "预期章节",
    # Round 169: second touch, same discipline as Round 144 -- another
    # paraphrase expansion of the same "constrained-output task
    # requirement fields/steps/schema" trigger concept. This is the
    # globally sparsest tuple now that every tuple discovered by the
    # triggers= scan carries at least one prior "Round N" touch comment
    # (the exhaustion first identified in Round 164); among the
    # tied-at-23-phrases tier (this tuple, _BUDGET_LIMIT_TERMS Round 155,
    # _WORKFLOW_TERMS Round 146), this one has the oldest last-touch
    # round, so it is picked over the other two, per the tied-size
    # tie-break rule established in Round 166. The separately-gated
    # _VERIFICATION_CONTROL_TERMS/_VERIFICATION_BYPASS_TERMS/
    # _DOWNSTREAM_TERMS groups inside _verification_metadata remain
    # untouched.
    "listed criteria", "itemized components", "designated data points",
    "prescribed content blocks",
    "所列标准", "分项内容", "指定的数据项", "规定的内容块",
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
# Bare "steps" is a substring of "footsteps"; bare "title" is a substring
# of "subtitle"/"entitled" -- both are left-side (suffix) collisions, so
# a left-boundary-only check is enough (it also leaves "titled"/"titles"
# matching, since those extend a real "title" to the right).
_VERIFICATION_TASK_BOUNDARY_TERMS = frozenset({"steps", "title"})
# Bare "validate" is a substring of "invalidate" -- the opposite meaning.
_VERIFICATION_CONTROL_BOUNDARY_TERMS = frozenset({"validate"})
# Bare "production" is a substring of "reproduction"; bare "decision" is a
# substring of "indecision" -- again the opposite meaning.
_DOWNSTREAM_BOUNDARY_TERMS = frozenset({"production", "decision"})


def _verification_metadata(text):
    _total, uncovered = _scoped_gap_count(
        text,
        signal_groups=(
            _VERIFICATION_TASK_TERMS,
            _DOWNSTREAM_TERMS + _VERIFICATION_BYPASS_TERMS,
        ),
        control_terms=_VERIFICATION_CONTROL_TERMS,
        boundary_terms=_VERIFICATION_TASK_BOUNDARY_TERMS
        | _DOWNSTREAM_BOUNDARY_TERMS,
        control_boundary_terms=_VERIFICATION_CONTROL_BOUNDARY_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["multi_constraint_output"],
        requirementSignalCount=_sum_term_hits(
            text, _VERIFICATION_TASK_TERMS,
            boundary_terms=_VERIFICATION_TASK_BOUNDARY_TERMS),
        verificationSignalCount=_sum_term_hits(
            text, _VERIFICATION_CONTROL_TERMS,
            boundary_terms=_VERIFICATION_CONTROL_BOUNDARY_TERMS),
        downstreamSignalCount=_sum_term_hits(
            text, _DOWNSTREAM_TERMS,
            boundary_terms=_DOWNSTREAM_BOUNDARY_TERMS),
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
        model_candidate_gate=_verification_model_gate,
        boundary_terms=_VERIFICATION_TASK_BOUNDARY_TERMS)


_INPUT_DEPENDENCY_TERMS = (
    "required input", "input field", "input fields", "request parameter",
    "user provides", "user-provided", "form field", "request body",
    "必填输入", "输入字段", "请求参数", "用户提供", "表单字段", "请求体",
    # Round 135: paraphrase expansion of the same "declared input
    # dependency" trigger concept -- no new completeness-check group
    # (_INPUT_REQUIREDNESS_TERMS/_INPUT_DEFAULT_TERMS/_INPUT_INVALID_TERMS/
    # _INPUT_HANDLING_TERMS are untouched, mirroring Round 134's discipline
    # of leaving the separately-gated completeness groups alone).
    "uploaded file", "query parameter", "path parameter", "attached file",
    "上传的文件", "查询参数", "路径参数", "附加文件",
    # Round 166: second touch, same discipline as Round 135 -- another
    # paraphrase expansion of the same "declared input dependency" trigger
    # concept. This is the globally sparsest tuple now that every tuple
    # discovered by the triggers= scan carries at least one prior "Round N"
    # touch comment (the exhaustion first identified in Round 164); among
    # the tied-at-22-phrases tier (this tuple, _BUDGET_PRESSURE_TERMS
    # Round 154, _ERROR_RESPONSE_TERMS Round 143), this one has the oldest
    # last-touch round, so it is picked over the other two. The four
    # separately-gated completeness groups (_INPUT_REQUIREDNESS_TERMS/
    # _INPUT_DEFAULT_TERMS/_INPUT_INVALID_TERMS/_INPUT_HANDLING_TERMS)
    # remain untouched.
    "submitted parameter", "incoming payload field", "user-supplied value",
    "client-submitted data",
    "提交的参数", "传入的负载字段", "用户填写的值", "客户端提交的数据",
    # Round 189: third touch, same discipline as Rounds 135/166 -- another
    # paraphrase expansion of the same "declared input dependency" trigger
    # concept. The four separately-gated completeness groups
    # (_INPUT_REQUIREDNESS_TERMS/_INPUT_DEFAULT_TERMS/_INPUT_INVALID_TERMS/
    # _INPUT_HANDLING_TERMS) remain untouched.
    "externally provided identifier", "caller-specified argument",
    "end-user submitted content", "third-party supplied dataset",
    "外部提供的标识符", "调用方指定的参数", "终端用户提交的内容", "第三方提供的数据集",
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
# Same "validate"/"invalidate" collision as _VERIFICATION_CONTROL_TERMS.
_INPUT_HANDLING_BOUNDARY_TERMS = frozenset({"validate"})


def _input_contract_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=["declared_input_dependency"],
        inputSignalCount=sum(text.count(x) for x in _INPUT_DEPENDENCY_TERMS),
        requirednessSignalCount=sum(
            text.count(x) for x in _INPUT_REQUIREDNESS_TERMS),
        defaultSignalCount=sum(text.count(x) for x in _INPUT_DEFAULT_TERMS),
        invalidInputSignalCount=sum(
            text.count(x) for x in _INPUT_INVALID_TERMS),
        handlingSignalCount=_sum_term_hits(
            text, _INPUT_HANDLING_TERMS,
            boundary_terms=_INPUT_HANDLING_BOUNDARY_TERMS),
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
    # Round 140: paraphrase expansion of the same "a normative example is
    # present in this prompt" trigger concept -- the separately-gated
    # structural violation check (_example_contract_metadata's
    # required-fields/enum-value comparison against the actual example
    # content) is untouched, mirroring Round 134-139's discipline.
    "annotated demonstration", "reference response", "demo input",
    "illustrative case",
    "标注演示", "参考回复", "演示输入", "示意案例",
    # Round 150: paraphrase expansion of the same "a normative example is
    # present in this prompt" trigger concept -- the separately-gated
    # structural violation check (_example_contract_metadata's
    # required-fields/enum-value comparison against the actual example
    # content) is untouched, mirroring Round 134-149's discipline.
    "worked demonstration", "prototype response", "canonical instance",
    "sample exchange",
    "示范演示", "样板回复", "典型实例", "样本对话",
    # Round 177: third touch, same discipline as Round 140/150 -- another
    # paraphrase expansion of the same "a normative example is present in
    # this prompt" trigger concept. The separately-gated structural
    # violation check (_example_contract_metadata's required-fields/
    # enum-value comparison against the actual example content) is
    # untouched: _example_contract_candidate_hints reads only
    # metadata["strategyKinds"], populated by regex checks fully decoupled
    # from this tuple's own content, so widening the vocabulary only
    # changes which phrases can cause the extractor to seed at all.
    "model answer", "template response", "exemplar case", "specimen output",
    "标准答案", "模板回复", "典范案例", "样本输出",
)
_EXAMPLE_RULE_TERMS = (
    "must", "required", "always", "never", "schema", "format",
    "field", "enum", "必须", "应当", "始终", "不得", "结构", "格式",
    "字段", "枚举",
)
# Bare "must" is a prefix of "mustache"/"mustang"/"mustard"; bare "enum" is
# a prefix of "enumerate"/"enumeration".
_EXAMPLE_RULE_WHOLE_WORD_TERMS = frozenset({"must", "enum"})
# Bare "never" is a substring of "whenever"; bare "field" is a substring
# of "battlefield" (both suffix collisions, left-check only).
_EXAMPLE_RULE_BOUNDARY_TERMS = frozenset({"never", "field"})
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
        ruleSignalCount=_sum_term_hits(
            text, _EXAMPLE_RULE_TERMS,
            boundary_terms=_EXAMPLE_RULE_BOUNDARY_TERMS,
            whole_word_terms=_EXAMPLE_RULE_WHOLE_WORD_TERMS),
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
    # Round 138: paraphrase expansion of the same "required tool
    # invocation" trigger concept -- the four separately-gated
    # completeness-check groups (_TOOL_INVOCATION_TERMS/
    # _TOOL_PARAMETER_CONTROL_TERMS/_TOOL_RESULT_TERMS/
    # _FAILURE_STRATEGY_TERMS) are untouched, mirroring Round 134-137's
    # discipline.
    "use the tool", "run the function", "make an api request",
    "trigger the endpoint",
    "使用该工具", "运行该函数", "发起 api 请求", "触发该接口",
    # Round 153: paraphrase expansion of the same "required tool/function/
    # API invocation" trigger concept -- the four separately-gated
    # completeness-check groups (_TOOL_INVOCATION_TERMS/
    # _TOOL_PARAMETER_CONTROL_TERMS/_TOOL_RESULT_TERMS/
    # _FAILURE_STRATEGY_TERMS) are untouched, mirroring Round 134-138's
    # discipline.
    "execute the tool", "hand off to the tool",
    "route the request to the api", "fire the function",
    "执行该工具", "交由该工具处理", "将请求路由至该 api", "触发该函数",
    # Round 181: third touch, same discipline as Rounds 138/153 -- another
    # paraphrase expansion of the same "required tool/function/API
    # invocation" trigger concept. The four separately-gated
    # completeness-check groups (_TOOL_INVOCATION_TERMS/
    # _TOOL_PARAMETER_CONTROL_TERMS/_TOOL_RESULT_TERMS/
    # _FAILURE_STRATEGY_TERMS) are untouched, mirroring Round 134-153's
    # discipline.
    "dispatch the tool", "activate the api endpoint",
    "kick off the function", "engage the tool integration",
    "调度该工具", "激活该 api 接口", "启动该函数", "接入该工具",
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
    # Round 134: paraphrase expansion within the risk's own 7 declared
    # operationKinds categories (realtime/web/vision/audio/memory/context/
    # plugin) -- no new category added. One new concept per sparsest
    # category (vision/audio/memory/context/plugin each had only one
    # phrase per language); realtime/web already had three and were left
    # untouched.
    "image recognition", "speech recognition", "remember across sessions",
    "extended context", "third-party plugin",
    "图像识别", "语音识别", "跨会话记忆", "扩展上下文", "第三方插件",
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
# Bare "vision" collides with "revision" -- boundary-checked; the rest
# (all multi-word phrases) keep plain counting.
_CAPABILITY_DEPENDENCY_BOUNDARY_TERMS = frozenset({"vision"})


def _capability_dependency_metadata(text):
    kinds = [
        name for name, terms in (
            ("realtime", ("real-time", "latest information", "current price",
                          "实时", "最新信息", "当前价格")),
            ("web", ("browse the web", "web access", "browser tool",
                     "浏览网页", "联网", "浏览器工具")),
            ("vision", ("vision", "analyze the image", "image recognition",
                        "视觉", "分析图片", "图像识别")),
            ("audio", ("audio input", "speech recognition",
                       "音频输入", "语音识别")),
            ("memory", ("persistent memory", "remember across sessions",
                        "持久记忆", "跨会话记忆")),
            ("context", ("context window", "extended context",
                         "上下文窗口", "扩展上下文")),
            ("plugin", ("plugin", "third-party plugin", "插件", "第三方插件")),
        ) if _any_term_hit(text, terms,
                            boundary_terms=_CAPABILITY_DEPENDENCY_BOUNDARY_TERMS)
    ]
    return _prompt_analysis_metadata(
        signal_families=["non_intrinsic_model_capability"],
        operationKinds=kinds,
        capabilitySignalCount=_sum_term_hits(
            text, _CAPABILITY_DEPENDENCY_TERMS,
            boundary_terms=_CAPABILITY_DEPENDENCY_BOUNDARY_TERMS),
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
        model_candidate_gate=_capability_dependency_model_gate,
        boundary_terms=_CAPABILITY_DEPENDENCY_BOUNDARY_TERMS)


_SENSITIVE_DATA_TERMS = (
    "personal data", "personal information", "pii", "email address",
    "phone number", "home address", "medical record", "financial account",
    "credential", "api key",
    # Round 131: paraphrase expansion within the risk's own 5 declared
    # categories (identity/contact/medical/financial/credential) -- no new
    # category added.
    "date of birth", "social security number", "mailing address",
    "health record", "medical diagnosis", "medical history",
    "credit card number", "bank account", "password", "access token",
    "个人数据", "个人信息", "邮箱地址", "电话号码",
    "家庭住址", "医疗记录", "金融账户", "凭据", "密钥",
    "出生日期", "社会保障号", "邮寄地址",
    "健康记录", "医疗诊断", "病史",
    "信用卡号", "银行账号", "密码", "访问令牌",
)
_SENSITIVE_DATA_ACTION_TERMS = (
    "collect", "store", "retain", "send", "share", "display", "output",
    "process", "summarize", "收集", "存储", "保留", "发送", "共享",
    "展示", "输出", "处理", "总结",
    # Round 157: paraphrase expansion of the same "perform an action on the
    # sensitive data" trigger concept -- the sibling AND-gate half
    # (_SENSITIVE_DATA_TERMS) and the separately-gated control groups
    # (_SENSITIVE_DATA_CONTROL_TERMS/_SENSITIVE_MINIMIZATION_TERMS/
    # _SENSITIVE_OUTBOUND_ACTION_TERMS/_SENSITIVE_COLLECTION_ACTION_TERMS)
    # are untouched. First touch of this tuple.
    "transmit the information externally", "forward the details to a third party",
    "log this information for later use", "archive the records long term",
    "对外传输相关信息", "将信息转发给第三方",
    "将这些信息记录下来备查", "将记录长期归档保存",
    # Round 179: second touch, same discipline as Round 157 -- another
    # paraphrase expansion of the same "perform an action on the sensitive
    # data" trigger concept. The sibling AND-gate half (_SENSITIVE_DATA_
    # TERMS) and the finer-grained metadata-only subsets (_SENSITIVE_
    # OUTBOUND_ACTION_TERMS/_SENSITIVE_COLLECTION_ACTION_TERMS) are
    # untouched, so these new phrases report outboundDisclosureSignalCount
    # == 0 and collectionStorageSignalCount == 0 on their own -- only the
    # unconditional "authorization" hint branch can fire for them, exactly
    # as Round 157 already established for its own new phrases.
    "compile the information into a report",
    "cross-reference the records with another database",
    "duplicate the records into a backup",
    "aggregate the data across multiple sources",
    "将信息汇总成报告", "将记录与另一数据库进行交叉核对",
    "将记录复制备份", "跨多个来源整合数据",
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
# Bare "store" is a substring of "drugstore"/"bookstore"/"restore".
_SENSITIVE_ACTION_BOUNDARY_TERMS = frozenset({"store"})
# Bare "mask" is a substring of "unmask"/"bitmask" -- "unmask" is the
# opposite meaning (reveal, not hide). Bare "authorized" is a substring of
# "unauthorized" -- also the opposite meaning.
_SENSITIVE_CONTROL_BOUNDARY_TERMS = frozenset({"mask", "authorized"})


def _sensitive_data_metadata(text):
    kinds = [
        name for name, terms in (
            ("identity", ("personal data", "personal information", "pii",
                          "date of birth", "social security number",
                          "个人数据", "个人信息", "出生日期", "社会保障号")),
            ("contact", ("email address", "phone number", "home address",
                         "mailing address",
                         "邮箱地址", "电话号码", "家庭住址", "邮寄地址")),
            ("medical", ("medical record", "health record",
                        "medical diagnosis", "medical history",
                        "医疗记录", "健康记录", "医疗诊断", "病史")),
            ("financial", ("financial account", "credit card number",
                           "bank account", "金融账户", "信用卡号", "银行账号")),
            ("credential", ("credential", "api key", "password",
                            "access token", "凭据", "密钥", "密码", "访问令牌")),
        ) if any(term in text for term in terms)
    ]
    return _prompt_analysis_metadata(
        signal_families=["sensitive_data_handling"],
        operationKinds=kinds,
        sensitiveDataSignalCount=sum(
            text.count(x) for x in _SENSITIVE_DATA_TERMS),
        dataActionSignalCount=_sum_term_hits(
            text, _SENSITIVE_DATA_ACTION_TERMS,
            boundary_terms=_SENSITIVE_ACTION_BOUNDARY_TERMS),
        dataControlSignalCount=_sum_term_hits(
            text, _SENSITIVE_DATA_CONTROL_TERMS,
            boundary_terms=_SENSITIVE_CONTROL_BOUNDARY_TERMS),
        minimizationSignalCount=sum(
            text.count(x) for x in _SENSITIVE_MINIMIZATION_TERMS),
        redactionSignalCount=_sum_term_hits(
            text, _SENSITIVE_REDACTION_TERMS,
            boundary_terms=_SENSITIVE_CONTROL_BOUNDARY_TERMS),
        authorizationControlSignalCount=_sum_term_hits(
            text, _SENSITIVE_AUTHORIZATION_TERMS,
            boundary_terms=_SENSITIVE_CONTROL_BOUNDARY_TERMS),
        retentionControlSignalCount=sum(
            text.count(x) for x in _SENSITIVE_RETENTION_TERMS),
        outboundDisclosureSignalCount=sum(
            text.count(x) for x in _SENSITIVE_OUTBOUND_ACTION_TERMS),
        collectionStorageSignalCount=_sum_term_hits(
            text, _SENSITIVE_COLLECTION_ACTION_TERMS,
            boundary_terms=_SENSITIVE_ACTION_BOUNDARY_TERMS),
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
        model_candidate_gate=_sensitive_data_model_gate,
        boundary_terms=_SENSITIVE_ACTION_BOUNDARY_TERMS)


_ROLE_IDENTITY_TERMS = (
    "you are", "act as", "your role", "persona", "assistant for",
    "你是", "作为", "你的角色", "角色身份", "助手",
    # Round 136: paraphrase expansion of the same "persistent operational
    # role identity" trigger concept -- the three completeness-check groups
    # (_ROLE_AUDIENCE_TERMS/_ROLE_DUTY_TERMS/_ROLE_EXCLUSION_TERMS) are
    # untouched, mirroring Round 134/135's discipline.
    "you play the role of", "your job is to", "you serve as",
    "your persona is",
    "你扮演", "你的工作是", "你担任", "你的人设是",
    # Round 148: paraphrase expansion of the same "persistent operational
    # role identity" trigger concept -- the three completeness-check groups
    # (_ROLE_AUDIENCE_TERMS/_ROLE_DUTY_TERMS/_ROLE_EXCLUSION_TERMS) are
    # untouched, mirroring Round 134-147's discipline.
    "you function as", "your designated identity is",
    "you take on the character of", "stepping into the character of",
    "你的职能设定为", "你现在的身份是", "你化身为", "你以这个身份登场",
    # Round 175: second touch, same discipline as Round 148 -- another
    # paraphrase expansion of the same "persistent operational role
    # identity" trigger concept. Re-running the systematic scan after
    # Round 174 closed `_VISUAL_STYLE_TERMS` surfaced a five-way tie at
    # 26 phrases (_AUTONOMY_TERMS/_EMBEDDED_SENSITIVE_VALUE_TERMS/
    # _EXAMPLE_TERMS/_ROLE_IDENTITY_TERMS/_SENSITIVE_DATA_ACTION_TERMS);
    # per the tied-size tie-break rule (oldest last-touch round wins),
    # this tuple's last touch (Round 148) is older than the other four
    # (149/150/151/157), so it is picked. The three completeness-check
    # groups (_ROLE_AUDIENCE_TERMS/_ROLE_DUTY_TERMS/_ROLE_EXCLUSION_TERMS)
    # remain untouched.
    "cast in the role of", "designated to operate as",
    "your operating identity is", "assigned the role of",
    "你被设定为", "你的运营身份是", "被赋予的角色是", "代入该角色设定",
)
_ROLE_AUDIENCE_TERMS = (
    "audience", "serve", "for users", "customer", "operator",
    "learner", "learners", "account holders", "retail learners",
    "面向", "服务对象", "用户", "客户", "操作员", "学习者",
)
# Bare "serve" is a substring of "preserve"/"reserve"/"deserve" -- needs the
# left-boundary counter so those don't count as an audience signal.
_ROLE_AUDIENCE_BOUNDARY_TERMS = frozenset({"serve"})
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
        audienceSignalCount=_sum_term_hits(
            text, _ROLE_AUDIENCE_TERMS,
            boundary_terms=_ROLE_AUDIENCE_BOUNDARY_TERMS),
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
    # Round 146: paraphrase expansion of the same "multi-step workflow/
    # procedure" trigger concept -- the separately-gated _WORKFLOW_
    # DEPENDENCY_TERMS/_WORKFLOW_RESULT_TERMS/_WORKFLOW_BRANCH_TERMS/
    # _WORKFLOW_SIDE_EFFECT_TERMS/_WORKFLOW_VALIDATION_TERMS/_WORKFLOW_
    # PREPARATION_TERMS groups inside _workflow_dependency_metadata, and the
    # text-order comparisons over them, are untouched, mirroring Round
    # 134-145's discipline.
    "multi-step procedure", "sequential stages", "procedural flow",
    "staged execution", "多步骤操作", "分阶段执行", "操作顺序", "执行环节",
    # Round 170: second touch, same discipline as Round 146 -- another
    # paraphrase expansion of the same "multi-step workflow/procedure"
    # trigger concept. Re-running the systematic scan after Round 169
    # closed surfaced a two-way tie at 23 phrases between this tuple
    # (Round 146) and _BUDGET_LIMIT_TERMS (Round 155); per the tied-size
    # tie-break rule (oldest last-touch round wins), 146 < 155, so this
    # tuple is picked. The separately-gated _WORKFLOW_DEPENDENCY_TERMS/
    # _WORKFLOW_RESULT_TERMS/_WORKFLOW_BRANCH_TERMS/_WORKFLOW_SIDE_EFFECT_
    # TERMS/_WORKFLOW_VALIDATION_TERMS/_WORKFLOW_PREPARATION_TERMS groups,
    # and the text-order comparisons over them, remain untouched.
    "ordered task sequence", "structured rollout plan",
    "systematic operating procedure", "successive stage progression",
    "有序任务序列", "结构化实施方案", "系统化操作规程", "逐阶段推进",
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
# Bare "import" is a prefix of "important"/"importance".
_WORKFLOW_PREPARATION_WHOLE_WORD_TERMS = frozenset({"import"})
# Bare "stop" is a suffix of its own negation-prefixed antonym "nonstop"
# (continuous, i.e. the opposite of a branch that stops).
_WORKFLOW_BRANCH_BOUNDARY_TERMS = frozenset({"stop"})
# Bare "publish"/"delete" are each a suffix of their own negation-prefixed
# antonym ("unpublish", "undelete") -- the opposite side effect.
_WORKFLOW_SIDE_EFFECT_BOUNDARY_TERMS = frozenset({"publish", "delete"})
# Bare "test"/"tests" collide with an unrelated longer word ("latest",
# "contests"); bare "validate" is a suffix of its own negation-prefixed
# antonym "invalidate" -- both boundary-checked, the rest keep plain
# matching.
_WORKFLOW_VALIDATION_BOUNDARY_TERMS = frozenset({"test", "tests", "validate"})


def _first_term_index(text: str, terms: Tuple[str, ...],
                      boundary_terms: FrozenSet[str] = frozenset(),
                      whole_word_terms: FrozenSet[str] = frozenset()) -> int:
    indexes = [
        idx for idx in (
            _first_boundary_index(term, text, whole_word=True)
            if term in whole_word_terms
            else _first_boundary_index(term, text) if term in boundary_terms
            else text.find(term)
            for term in terms
        ) if idx >= 0
    ]
    return min(indexes) if indexes else -1


def _workflow_dependency_metadata(text):
    side_effect_index = _first_term_index(
        text, _WORKFLOW_SIDE_EFFECT_TERMS,
        boundary_terms=_WORKFLOW_SIDE_EFFECT_BOUNDARY_TERMS)
    validation_index = _first_term_index(
        text, _WORKFLOW_VALIDATION_TERMS,
        boundary_terms=_WORKFLOW_VALIDATION_BOUNDARY_TERMS)
    preparation_index = _first_term_index(
        text, _WORKFLOW_PREPARATION_TERMS,
        whole_word_terms=_WORKFLOW_PREPARATION_WHOLE_WORD_TERMS)
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
        workflowBranchSignalCount=_sum_term_hits(
            text, _WORKFLOW_BRANCH_TERMS,
            boundary_terms=_WORKFLOW_BRANCH_BOUNDARY_TERMS),
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
    # Round 147: paraphrase expansion of the same "named machine-consumed
    # data field" trigger concept -- the separately-gated _FIELD_TYPE_TERMS/
    # _FIELD_UNIT_PRECISION_TERMS/_FIELD_RANGE_TERMS/_FIELD_BOUNDARY_TERMS/
    # _FIELD_MACHINE_CONSUMER_TERMS groups inside _field_constraint_metadata
    # are untouched, mirroring Round 134-146's discipline.
    "data attribute", "output parameter", "record column",
    "structured property", "数据属性", "输出参数", "记录列", "结构化属性",
    # Round 173: second touch, same discipline as Round 147 -- another
    # paraphrase expansion of the same "named machine-consumed data field"
    # trigger concept. Re-running the systematic scan after Round 172
    # closed `_STREAMING_TERMS` surfaced a two-way tie at 25 phrases
    # between this tuple (Round 147) and _VISUAL_STYLE_TERMS (Round 156);
    # per the tied-size tie-break rule (oldest last-touch round wins),
    # 147 < 156, so this tuple is picked. The separately-gated
    # _FIELD_TYPE_TERMS/_FIELD_UNIT_PRECISION_TERMS/_FIELD_RANGE_TERMS/
    # _FIELD_BOUNDARY_TERMS/_FIELD_MACHINE_CONSUMER_TERMS groups, and the
    # material_field OR-gate, remain untouched.
    "input variable", "response element", "data slot", "named value entry",
    "输入变量", "响应元素", "数据槽位", "命名数值项",
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
# Bare "date" is a substring of "update"/"validate"/"mandate"/"candidate".
# Bare "amount" is a substring of "tantamount" (suffix collision only).
_FIELD_CONTRACT_BOUNDARY_TERMS = frozenset({"date", "amount"})
# Bare "enum" is a prefix of "enumerate"/"enumeration".
_FIELD_CONTRACT_WHOLE_WORD_TERMS = frozenset({"enum"})
# Same collisions as _TYPE_TERMS above, plus bare "type" itself, which is a
# suffix of "prototype"/"stereotype"/"archetype" AND a prefix of
# "typical"/"typewriter" -- both directions need the whole-word check.
_FIELD_TYPE_BOUNDARY_TERMS = frozenset({"string", "array"})
_FIELD_TYPE_WHOLE_WORD_TERMS = frozenset({"object", "list", "type"})
# Bare "unit" collides both ways (see _UNIT_WHOLE_WORD_TERMS above). Bare
# "currency" is a substring of "concurrency" (suffix collision). Bare
# "precision" is a substring of "imprecision" (suffix collision).
_FIELD_UNIT_PRECISION_WHOLE_WORD_TERMS = frozenset({"unit"})
_FIELD_UNIT_PRECISION_BOUNDARY_TERMS = frozenset({"currency", "precision"})
# Bare "range" is a substring of "strange"/"arrangement". Bare "enum" is a
# prefix of "enumerate"/"enumeration" (same collision as above).
_FIELD_RANGE_BOUNDARY_TERMS = frozenset({"range"})
_FIELD_RANGE_WHOLE_WORD_TERMS = frozenset({"enum"})


def _field_constraint_metadata(text):
    numeric_ranges = len(re.findall(
        r"\b\d+\s*(?:-|–|to)\s*\d+\b", text, flags=re.IGNORECASE))
    return _prompt_analysis_metadata(
        signal_families=["typed_or_bounded_field"],
        fieldSignalCount=_sum_term_hits(
            text, _FIELD_CONTRACT_TERMS,
            boundary_terms=_FIELD_CONTRACT_BOUNDARY_TERMS,
            whole_word_terms=_FIELD_CONTRACT_WHOLE_WORD_TERMS),
        machineConsumerSignalCount=sum(
            text.count(x) for x in _FIELD_MACHINE_CONSUMER_TERMS),
        fieldTypeSignalCount=_sum_term_hits(
            text, _FIELD_TYPE_TERMS, boundary_terms=_FIELD_TYPE_BOUNDARY_TERMS,
            whole_word_terms=_FIELD_TYPE_WHOLE_WORD_TERMS),
        unitPrecisionSignalCount=_sum_term_hits(
            text, _FIELD_UNIT_PRECISION_TERMS,
            boundary_terms=_FIELD_UNIT_PRECISION_BOUNDARY_TERMS,
            whole_word_terms=_FIELD_UNIT_PRECISION_WHOLE_WORD_TERMS),
        rangeSignalCount=(
            _sum_term_hits(text, _FIELD_RANGE_TERMS,
                           boundary_terms=_FIELD_RANGE_BOUNDARY_TERMS,
                           whole_word_terms=_FIELD_RANGE_WHOLE_WORD_TERMS)
            + numeric_ranges),
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
        model_candidate_gate=_field_constraint_model_gate,
        boundary_terms=_FIELD_CONTRACT_BOUNDARY_TERMS)


_ERROR_RESPONSE_TERMS = (
    "error response", "on error", "if invalid", "cannot complete",
    "permission denied", "refuse", "failure response", "错误响应", "出错时",
    "无效时", "无法完成", "权限不足", "拒绝", "失败响应",
    # Round 143: paraphrase expansion of the same "declared failure/
    # error-handling response" trigger concept -- the separately-gated
    # _ERROR_SCHEMA_TERMS/_ERROR_RECOVERY_TERMS/_ERROR_FORMAT_TERMS/
    # _FIELD_MACHINE_CONSUMER_TERMS groups inside _error_response_metadata
    # are untouched, mirroring Round 134-142's discipline.
    "unable to proceed", "access denied", "decline the request",
    "failure handling", "无法处理", "访问受限", "婉拒请求", "失败处理",
    # Round 167: second touch, same discipline as Round 143 -- another
    # paraphrase expansion of the same "declared failure/error-handling
    # response" trigger concept. This is the globally sparsest tuple now
    # that every tuple discovered by the triggers= scan carries at least
    # one prior "Round N" touch comment (the exhaustion first identified in
    # Round 164); among the tied-at-22-phrases tier (this tuple,
    # _BUDGET_PRESSURE_TERMS Round 154), this one has the oldest last-touch
    # round, so it is picked over the other one, per the tied-size
    # tie-break rule established in Round 166. The separately-gated
    # _ERROR_SCHEMA_TERMS/_ERROR_RECOVERY_TERMS/_ERROR_FORMAT_TERMS/
    # _FIELD_MACHINE_CONSUMER_TERMS groups remain untouched.
    "operation failed", "request rejected", "unable to fulfill",
    "flags the failure",
    "操作失败", "请求驳回", "无法满足", "标记失败",
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
    # Round 141: paraphrase expansion of the same "large document structure
    # with a background/appendix/reference section" trigger concept -- the
    # separately-gated structural hint check (line count, character count,
    # critical-rule line position, hierarchy signal count) is untouched,
    # mirroring Round 134-140's discipline.
    "supporting material", "extended documentation",
    "extensive instructions", "supplementary notes",
    "支持性材料", "扩展文档", "详尽指令", "补充说明",
    # Round 164: second touch, same discipline as Round 141 -- another
    # paraphrase expansion of the same "large document structure with a
    # background/appendix/reference/critical-rule section" trigger concept.
    # This is the sparsest single primary-vocabulary tuple now that every
    # tuple discovered by the triggers= scan carries at least one prior
    # "Round N" touch comment (the first-touch tie-break precedent
    # established in Rounds 137/159-163 has run out of untouched
    # candidates). The separately-gated structural hint check (line count,
    # character count, critical-rule line position, hierarchy signal
    # count, all driven by _ATTENTION_HIERARCHY_TERMS/
    # _ATTENTION_REPETITION_TERMS, not this tuple) remains untouched.
    "background context", "sprawling multi-section document",
    "crucial requirement", "unwieldy documentation bundle",
    "背景信息", "篇幅冗长的多章节文档", "关键要求", "臃肿的文档合集",
    # Round 184: third touch, same discipline as Rounds 141/164 -- another
    # paraphrase expansion of the same "large document structure with a
    # background/appendix/reference/critical-rule section" trigger concept.
    # The separately-gated structural hint check (line count, character
    # count, critical-rule line position, hierarchy signal count, all
    # driven by _ATTENTION_HIERARCHY_TERMS/_ATTENTION_REPETITION_TERMS, not
    # this tuple) remains untouched.
    "hefty supplementary annex", "voluminous instruction manual",
    "sizable reference dossier", "bulky exhibit of attached materials",
    "篇幅厚重的附属说明", "内容庞杂的操作手册", "体量庞大的参考档案",
    "堆积如山的附件材料",
)
_ATTENTION_HIERARCHY_TERMS = (
    "summary", "priority", "must follow", "non-negotiable", "摘要", "优先级",
    "authoritative rules", "ordered procedure", "cannot override",
    "必须遵守", "不可覆盖", "权威规则",
)
_ATTENTION_REPETITION_TERMS = (
    "repeated", "duplicate", "again", "重复", "反复", "再次",
)
# Bare "again" is a prefix of the unrelated word "against" (opposition,
# not repetition).
_ATTENTION_REPETITION_WHOLE_WORD_TERMS = frozenset({"again"})


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
        repetitionSignalCount=_sum_term_hits(
            text, _ATTENTION_REPETITION_TERMS,
            whole_word_terms=_ATTENTION_REPETITION_WHOLE_WORD_TERMS),
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
    # Bare "resume" is a pure homonym with the career-document noun ("please
    # review my resume") -- no boundary check can tell them apart, so this
    # uses only multi-word phrases specific to resuming a stream, not the
    # bare verb.
    "streaming", "streamed", "stream response", "incremental", "chunked",
    "resume streaming", "resume the stream", "resumable", "stream resumption",
    "resume transfer", "server-sent events", "sse", "流式", "增量", "分块",
    "断点续传",
    # Round 145: paraphrase expansion of the same "streamed/incremental
    # output" trigger concept -- the separately-gated _STREAM_FRAMING_TERMS/
    # _STREAM_COMPLETION_TERMS/_STREAM_RESUME_TERMS/_STREAM_PARTIAL_TERMS
    # groups inside _streaming_recovery_metadata are untouched, mirroring
    # Round 134-144's discipline.
    "live output", "progressive rendering", "segmented delivery",
    "reconnect and continue", "实时输出", "渐进渲染", "分段返回", "断线重连",
    # Round 172: second touch, same discipline as Round 145 -- another
    # paraphrase expansion of the same "streamed/incremental output" trigger
    # concept. Re-running the systematic scan after Round 171 closed
    # `_BUDGET_LIMIT_TERMS` leaves this tuple (Round 145) as the sole
    # sparsest, no tie. The separately-gated _STREAM_FRAMING_TERMS/
    # _STREAM_COMPLETION_TERMS/_STREAM_RESUME_TERMS/_STREAM_PARTIAL_TERMS
    # groups, and the explicitly_missing negation helper, remain untouched.
    "token-by-token output", "piecewise delivery", "continuous data feed",
    "rolling output updates", "逐字输出", "分片传输", "持续数据流", "滚动更新输出",
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
    # Round 139: paraphrase expansion of the same "carrying state across a
    # multi-turn exchange" trigger concept -- the four separately-gated
    # completeness-check groups (_STATE_INHERITANCE_TERMS/
    # _STATE_UPDATE_TERMS/_STATE_RESET_TERMS/_STATE_INVARIANT_TERMS) are
    # untouched, mirroring Round 134-138's discipline.
    "across turns", "throughout this exchange", "over multiple messages",
    "in subsequent turns",
    "跨轮次", "在整个交流过程中", "在多条消息中", "在后续轮次中",
    # Round 158: second touch, same discipline -- another paraphrase
    # expansion of the same multi-turn-exchange concept, completeness-check
    # groups still untouched.
    "in the ongoing dialogue", "across this back-and-forth",
    "spanning several exchanges", "as this dialogue continues",
    "在持续的对话中", "在这轮来回交流中", "跨越多次交流", "随着交流不断推进",
    # Round 182: third touch, same discipline -- another paraphrase
    # expansion of the same multi-turn-exchange concept, completeness-check
    # groups (_STATE_INHERITANCE_TERMS/_STATE_UPDATE_TERMS/
    # _STATE_RESET_TERMS/_STATE_INVARIANT_TERMS) still untouched.
    "over repeated interactions", "as the chat continues",
    "in each successive reply", "over the course of many replies",
    "在反复互动中", "随着聊天的持续", "在每次后续回复中", "历经多次回复",
)
# Bare "session" is a substring of "possession"/"dispossession".
_MULTI_TURN_BOUNDARY_TERMS = frozenset({"session"})
_STATE_INHERITANCE_TERMS = (
    "inherit", "carry forward", "persist", "remember",
    "conversation memory", "previous turn", "继承", "沿用",
    "保持", "记住", "对话记忆", "上一轮",
)
# Bare "inherit" is a suffix of its own negation-prefixed antonym
# "disinherit" (explicitly excluded from inheriting, the opposite signal).
_STATE_INHERITANCE_BOUNDARY_TERMS = frozenset({"inherit"})
_STATE_UPDATE_TERMS = (
    "update preference", "change preference", "override", "latest request",
    "updates", "later request", "after confirmation", "confirmed preferences",
    "更新偏好", "修改偏好", "覆盖", "最新请求",
)
_STATE_RESET_TERMS = (
    "reset", "new session", "forget", "clear state", "重置", "新会话",
    "忘记", "清除状态",
)
# Bare "reset" is a suffix of the unrelated word "preset" (a pre-configured
# value, not a state reset).
_STATE_RESET_BOUNDARY_TERMS = frozenset({"reset"})
_STATE_INVARIANT_TERMS = (
    "cannot be overridden", "must always", "non-overridable", "system rule",
    "不可覆盖", "始终必须", "系统规则",
)


def _multi_turn_state_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=["multi_turn_state"],
        multiTurnSignalCount=_sum_term_hits(
            text, _MULTI_TURN_TERMS, boundary_terms=_MULTI_TURN_BOUNDARY_TERMS),
        stateInheritanceSignalCount=_sum_term_hits(
            text, _STATE_INHERITANCE_TERMS,
            boundary_terms=_STATE_INHERITANCE_BOUNDARY_TERMS),
        stateUpdateSignalCount=sum(text.count(x) for x in _STATE_UPDATE_TERMS),
        stateResetSignalCount=_sum_term_hits(
            text, _STATE_RESET_TERMS,
            boundary_terms=_STATE_RESET_BOUNDARY_TERMS),
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
        model_candidate_gate=_multi_turn_state_model_gate,
        boundary_terms=_MULTI_TURN_BOUNDARY_TERMS)


_SAFETY_DOMAIN_TERMS = (
    "dangerous", "high-risk", "illegal", "self-harm", "weapon", "malware",
    "violence", "explosive", "危险", "高风险", "违法", "自残", "武器",
    "恶意软件", "暴力", "爆炸物",
    # Round 115: the original 8 concepts were generic risk/weapon/violence
    # words and missed several named high-risk domains that OWASP/NIST
    # safety taxonomies list as distinct categories -- none of these are
    # substrings of the terms above, so they add real recall rather than
    # duplicate it. "poisoning" was deliberately left out: it collides
    # with the benign "food poisoning" (illness, not an attack).
    "terrorism", "extremism", "child sexual abuse", "suicide", "narcotics",
    "hate speech", "human trafficking", "fraud", "cyberattack",
    "恐怖主义", "极端主义", "儿童性虐待", "自杀", "毒品",
    "仇恨言论", "人口贩卖", "诈骗", "网络攻击",
)
# Bare "violence" is a suffix of its own negation-prefixed antonym
# "nonviolence" (the opposite of the safety-domain signal).
_SAFETY_DOMAIN_BOUNDARY_TERMS = frozenset({"violence"})
_SAFETY_REFUSAL_TERMS = (
    "refuse", "do not provide", "decline", "block", "拒绝", "不得提供",
    "不予回答", "阻止",
)
# Bare "block" is a prefix of "blockchain"/"blockade".
_SAFETY_REFUSAL_WHOLE_WORD_TERMS = frozenset({"block"})
_SAFETY_ALTERNATIVE_TERMS = (
    "safe alternative", "safer help", "benign", "安全替代", "安全帮助",
    "prevention guidance", "storage and disposal", "allowed prevention",
    "无害", "预防指导", "存储和处置",
)
_SAFETY_ESCALATION_TERMS = (
    "emergency", "professional help", "escalate", "human review", "紧急",
    "专业帮助", "转交", "人工复核",
)
# Bare "escalate" is a suffix of its own negation-prefixed antonym
# "deescalate" (calm down, the opposite of the escalation signal).
_SAFETY_ESCALATION_BOUNDARY_TERMS = frozenset({"escalate"})


def _safety_policy_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=["high_risk_content_or_action"],
        safetyDomainSignalCount=_sum_term_hits(
            text, _SAFETY_DOMAIN_TERMS,
            boundary_terms=_SAFETY_DOMAIN_BOUNDARY_TERMS),
        refusalSignalCount=_sum_term_hits(
            text, _SAFETY_REFUSAL_TERMS,
            whole_word_terms=_SAFETY_REFUSAL_WHOLE_WORD_TERMS),
        safeAlternativeSignalCount=sum(
            text.count(x) for x in _SAFETY_ALTERNATIVE_TERMS),
        escalationSignalCount=_sum_term_hits(
            text, _SAFETY_ESCALATION_TERMS,
            boundary_terms=_SAFETY_ESCALATION_BOUNDARY_TERMS),
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
        model_candidate_gate=_safety_policy_model_gate,
        boundary_terms=_SAFETY_DOMAIN_BOUNDARY_TERMS)


_SOURCE_USE_TERMS = (
    "copyright", "licensed", "source text", "article", "book", "long passage",
    "quote", "reproduce", "copy", "verbatim", "版权", "许可", "来源文本",
    "文章", "书籍", "长段落", "引用", "复刻", "复制", "逐字",
    # Round 159: paraphrase expansion of the same "reproducing/quoting a
    # copyrighted or licensed third-party source" trigger concept -- the
    # separately-gated _SOURCE_ATTRIBUTION_TERMS/_SOURCE_TRANSFORMATION_TERMS/
    # _SOURCE_LIMIT_TERMS groups are untouched. First touch of this tuple.
    "excerpt from a published work", "reprint the original passage",
    "replicate the protected work", "lift text directly from the source",
    "摘录已出版作品的内容", "转载原文段落", "翻印受保护的作品内容",
    "直接摘取原始来源的文字",
    # Round 183: second touch, same discipline as Round 159 -- another
    # paraphrase expansion of the same "reproducing/quoting a copyrighted or
    # licensed third-party source" trigger concept. The separately-gated
    # _SOURCE_ATTRIBUTION_TERMS/_SOURCE_TRANSFORMATION_TERMS/
    # _SOURCE_LIMIT_TERMS groups are untouched.
    "relay the original author's wording without modification",
    "carry the protected material into your answer unchanged",
    "transcribe the published piece from start to finish",
    "echo the proprietary text back in full",
    "原封不动地转述原作者的文字", "将受保护的材料原样带入回答",
    "从头到尾抄录已出版的作品", "完整地复述专有文本内容",
)
_SOURCE_ATTRIBUTION_TERMS = (
    "attribute", "attribution", "citation", "credit", "name the source",
    "标注来源", "引用", "署名", "出处",
)
# Bare "credit" is a suffix of the unrelated word "discredit" (to cast
# doubt on, not to give source credit); bare "citation" is a suffix of the
# unrelated word "recitation" (reciting from memory, not source citation).
_SOURCE_ATTRIBUTION_BOUNDARY_TERMS = frozenset({"credit", "citation"})
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
# Bare "license"/"licensed" are each a suffix of their own negation-prefixed
# antonym "unlicensed" (the opposite of a license boundary).
_SOURCE_LIMIT_BOUNDARY_TERMS = frozenset({"license", "licensed"})
# Bare "book" is a prefix of an unrelated longer word ("bookkeeping");
# bare "licensed" is a suffix of its own negation-prefixed antonym
# "unlicensed" -- whole-word/boundary-checked; the rest keep plain counting.
_SOURCE_USE_WHOLE_WORD_TERMS = frozenset({"book"})
_SOURCE_USE_BOUNDARY_TERMS = frozenset({"licensed"})


def _source_use_policy_metadata(text):
    return _prompt_analysis_metadata(
        signal_families=["third_party_source_use"],
        sourceUseSignalCount=_sum_term_hits(
            text, _SOURCE_USE_TERMS,
            boundary_terms=_SOURCE_USE_BOUNDARY_TERMS,
            whole_word_terms=_SOURCE_USE_WHOLE_WORD_TERMS),
        attributionSignalCount=_sum_term_hits(
            text, _SOURCE_ATTRIBUTION_TERMS,
            boundary_terms=_SOURCE_ATTRIBUTION_BOUNDARY_TERMS),
        transformationSignalCount=sum(
            text.count(x) for x in _SOURCE_TRANSFORMATION_TERMS),
        sourceLimitSignalCount=_sum_term_hits(
            text, _SOURCE_LIMIT_TERMS,
            boundary_terms=_SOURCE_LIMIT_BOUNDARY_TERMS),
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
        model_candidate_gate=_source_use_model_gate,
        boundary_terms=_SOURCE_USE_BOUNDARY_TERMS,
        whole_word_terms=_SOURCE_USE_WHOLE_WORD_TERMS)


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


# Bare terms whose plain-substring match is a known false positive inside
# an unrelated longer word: "widespread "/"threads " for "read "/"reads ";
# "credits " for "edits "; "cobweb(s)" for "web"; "rapid"/"capital"/
# "therapist" for "api"; "hourly"/"curly"/"curls" for "url". Unlike
# "write "/"writes " -- where a plain-substring match inside
# "overwrite(s)"/"rewrite(s)" is still a correct write signal, so those
# stay plain substring -- these have no such upside and need the
# boundary-aware counter.
_AMBIGUOUS_BARE_BEHAVIOR_TERMS = frozenset(
    {"read ", "reads ", "edits ", "web", "api", "url"})
# Bare "shell" is a prefix of the unrelated word "shellfish"; bare "secret"
# is a prefix of the unrelated word "secretary" (and of the adverb/adjective
# "secretly"/"secretive", which describe manner, not credential access) --
# both need the right-boundary (whole-word) check, not just the left one
# above.
_WHOLE_WORD_BARE_BEHAVIOR_TERMS = frozenset({"shell", "secret"})


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
            "read file", "reads ", "read ", "read-only", "读取文件",
            "读取", "只读")),
        ("file_write", (
            "write file", "writes ", "write ", "edit file", "edits ",
            "写入文件", "写入", "编辑文件", "编辑")),
        ("credential_access", (
            "credential", "secret", "environment variable", "凭据", "密钥",
            "环境变量")),
    )
    negations = (
        "without {term}", "no {term}", "never {term}",
        "does not use {term}", "不使用{term}", "无{term}", "禁止{term}",
    )
    for family, terms in definitions:
        present = any(
            _term_hit_present(term, text, whole_word=True)
            if term in _WHOLE_WORD_BARE_BEHAVIOR_TERMS
            else _term_hit_present(term, text)
            if term in _AMBIGUOUS_BARE_BEHAVIOR_TERMS else term in text
            for term in terms)
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


def extract_manifest_description_quality_gap(review_dict, file_bytes):
    """Seed the Manifest name/description alone, with no capability facts.

    Distinct from declared_behavior_mismatch's consistency check (does the
    description match observed capabilities?): this is an adequacy check
    (does the description alone give an invoking agent enough signal to
    decide when to use this Skill?) -- a judgment VR-SKILL-001 explicitly
    scopes to L1_semantic ("may assess description quality but should not
    replace schema validation").
    """
    if review_dict.get("engine") != "skill":
        return []
    am = review_dict.get("artifactModel") or {}
    manifest_file = am.get("manifestFile")
    manifest = am.get("manifest") or {}
    description = manifest.get("description")
    if (not manifest_file or not isinstance(description, str)
            or not description.strip()):
        return []
    name = manifest.get("name")
    snap = review_dict.get("snapshot") or {}
    location = {
        "fileId": manifest_file["fileId"],
        "artifactPath": manifest_file["normalizedPath"],
        "fileDigest": "",
        "sourceByteRange": {
            "start": 0,
            "end": min(500, len(file_bytes.get(manifest_file["fileId"], b""))),
        },
        "locationSchemaVersion": "1",
    }
    metadata = [{
        "evidenceRole": "manifest_declaration",
        "evidenceScope": "bounded_static_skill_snapshot",
        "declaredName": str(name)[:160] if isinstance(name, str) else "",
        "declaredDescriptionLength": min(len(description), 4096),
    }]
    evs = _make_evidence_records(
        [location], snapshot_id=snap.get("snapshotId", ""),
        producer_id="extractor.skill.manifest_description_quality",
        metadata_by_index=metadata)
    source = {"declaredDescriptionLength": min(len(description), 4096)}
    return [(source, [e["evidenceId"] for e in evs], evs)]


_UNTRUSTED_DESERIALIZATION_SOURCE_TERMS = (
    "untrusted", "external", "user-provided", "user provided", "user-submitted",
    "third-party", "third party", "remote", "downloaded", "received from",
    "submitted by", "fetched from", "another skill", "another agent",
    "peer skill", "attacker", "不可信", "外部", "第三方", "用户提供", "远程",
    "下载", "接收自",
)
_TRUSTED_DESERIALIZATION_SOURCE_TERMS = (
    "own bundled", "its own cache", "its own state", "self-generated",
    "generated by this skill", "internal only", "internal-only",
    "trusted internal", "bundled with this skill", "written by itself",
    "previously saved by itself", "仅内部", "自身生成", "自己写入", "自带",
)


def extract_deserialization_trust_gap(review_dict, file_bytes):
    """Seed a capability fact showing a pickle/marshal/yaml.load sink paired
    with the Manifest's own trust framing of the input source.

    Distinct from declared_behavior_mismatch (behavior-family presence vs
    denial) and permission_capability_mismatch (permission list vs capability
    family): this judges whether the Manifest text frames the deserialized
    input as coming from an untrusted/external source, which the sink
    pattern alone cannot establish (VR-SKILL-007's L1_semantic boundary).
    """
    if review_dict.get("engine") != "skill":
        return []
    am = review_dict.get("artifactModel") or {}
    manifest_file = am.get("manifestFile")
    manifest = am.get("manifest") or {}
    facts = ((am.get("capabilityFacts") or {}).get("facts") or [])
    deser_facts = [
        fact for fact in facts
        if fact.get("category") == "deserialization"
        and fact.get("sourceKind") != "manifest"
    ]
    if not manifest_file or not deser_facts:
        return []
    snap = review_dict.get("snapshot") or {}
    files = {f.get("normalizedPath"): f for f in (snap.get("files") or [])
             if f.get("status") == "included"}
    description = str(manifest.get("description") or "").lower()
    declared_untrusted_source = any(
        term in description
        for term in _UNTRUSTED_DESERIALIZATION_SOURCE_TERMS)
    declared_trusted_source = any(
        term in description
        for term in _TRUSTED_DESERIALIZATION_SOURCE_TERMS)
    locations = [{
        "fileId": manifest_file["fileId"],
        "artifactPath": manifest_file["normalizedPath"],
        "fileDigest": "",
        "sourceByteRange": {
            "start": 0,
            "end": min(500, len(file_bytes.get(manifest_file["fileId"], b""))),
        },
        "locationSchemaVersion": "1",
    }]
    for fact in deser_facts[:7]:
        f = files.get(fact.get("artifactPath"))
        if f:
            locations.append(_fact_location(f, fact, file_bytes))
    metadata = [{
        "evidenceRole": "manifest_declaration",
        "evidenceScope": "bounded_static_skill_snapshot",
        "deserializationFactCount": min(len(deser_facts), 128),
        "declaredUntrustedInputSource": declared_untrusted_source,
        "declaredTrustedInputSource": declared_trusted_source,
    }]
    for fact in deser_facts[:7]:
        metadata.append({
            "evidenceRole": "capability_fact",
            "evidenceScope": "bounded_static_skill_snapshot",
            "capabilityCategory": "deserialization",
            "capabilityOperation": str(fact.get("operation", ""))[:160],
        })
    evs = _make_evidence_records(
        locations, snapshot_id=snap.get("snapshotId", ""),
        producer_id="extractor.skill.deserialization_trust",
        metadata_by_index=metadata)
    source = {"deserializationFactCount": len(deser_facts)}
    candidate_hints = []
    if declared_untrusted_source:
        candidate_hints.append(_candidate_hint(
            {"trustGapKind": "untrusted_source_deserialized"},
            "The Skill deserializes input that its own Manifest describes "
            "as coming from an untrusted or external source."))
    if candidate_hints:
        source["candidateHints"] = candidate_hints
    else:
        source["modelCandidatePolicy"] = "skip_without_catalog_hint"
        source["modelCandidateSkipReason"] = (
            "static_skill_capability_controls_match")
    return [(source, [e["evidenceId"] for e in evs], evs)]


_WEAK_CRYPTO_SENSITIVE_DATA_TERMS = (
    "password", "credential", "api key", "api-key", "secret", "token",
    "personal data", "personally identifiable", "pii", "social security",
    "credit card", "private key", "session token", "auth token",
    "密码", "凭证", "令牌", "隐私", "个人信息", "个人数据", "信用卡", "私钥",
)
_WEAK_CRYPTO_NON_SENSITIVE_DATA_TERMS = (
    "test data", "dummy data", "sample data", "public data", "non-sensitive",
    "mock data", "placeholder data", "synthetic data", "测试数据", "示例数据",
    "公开数据", "模拟数据", "非敏感",
)
_WEAK_HASH_OPERATIONS = {"hashlib.md5", "hashlib.sha1", "hashlib.new",
                         "crypt.crypt"}


def extract_weak_crypto_sensitivity_gap(review_dict, file_bytes):
    """Seed a weak-hash/weak-cipher/disabled-TLS-verification capability
    fact paired with the Manifest's own sensitivity framing of the data it
    protects.

    Distinct from declared_behavior_mismatch, permission_capability_mismatch,
    external_instruction_trust_gap, manifest_description_quality_gap, and
    deserialization_trust_gap: this judges whether the Manifest text frames
    the data reaching a weak hash/cipher/TLS-bypass call as sensitive, which
    the API-pattern fact alone cannot establish (VR-SKILL-008's L1_semantic
    boundary: "may assess declared sensitivity but should not determine
    cryptographic correctness").
    """
    if review_dict.get("engine") != "skill":
        return []
    am = review_dict.get("artifactModel") or {}
    manifest_file = am.get("manifestFile")
    manifest = am.get("manifest") or {}
    facts = ((am.get("capabilityFacts") or {}).get("facts") or [])
    crypto_facts = [
        fact for fact in facts
        if fact.get("category") == "weak_crypto"
        and fact.get("sourceKind") != "manifest"
    ]
    if not manifest_file or not crypto_facts:
        return []
    snap = review_dict.get("snapshot") or {}
    files = {f.get("normalizedPath"): f for f in (snap.get("files") or [])
             if f.get("status") == "included"}
    description = str(manifest.get("description") or "").lower()
    declared_sensitive_data = any(
        term in description for term in _WEAK_CRYPTO_SENSITIVE_DATA_TERMS)
    declared_non_sensitive_data = any(
        term in description for term in _WEAK_CRYPTO_NON_SENSITIVE_DATA_TERMS)
    has_weak_hash = any(
        fact.get("operation") in _WEAK_HASH_OPERATIONS for fact in crypto_facts)
    sensitivity_gap_kind = (
        "weak_hash_algorithm" if has_weak_hash
        else "disabled_certificate_verification")
    locations = [{
        "fileId": manifest_file["fileId"],
        "artifactPath": manifest_file["normalizedPath"],
        "fileDigest": "",
        "sourceByteRange": {
            "start": 0,
            "end": min(500, len(file_bytes.get(manifest_file["fileId"], b""))),
        },
        "locationSchemaVersion": "1",
    }]
    for fact in crypto_facts[:7]:
        f = files.get(fact.get("artifactPath"))
        if f:
            locations.append(_fact_location(f, fact, file_bytes))
    metadata = [{
        "evidenceRole": "manifest_declaration",
        "evidenceScope": "bounded_static_skill_snapshot",
        "weakCryptoFactCount": min(len(crypto_facts), 128),
        "declaredSensitiveData": declared_sensitive_data,
        "declaredNonSensitiveData": declared_non_sensitive_data,
    }]
    for fact in crypto_facts[:7]:
        metadata.append({
            "evidenceRole": "capability_fact",
            "evidenceScope": "bounded_static_skill_snapshot",
            "capabilityCategory": "weak_crypto",
            "capabilityOperation": str(fact.get("operation", ""))[:160],
        })
    evs = _make_evidence_records(
        locations, snapshot_id=snap.get("snapshotId", ""),
        producer_id="extractor.skill.weak_crypto_sensitivity",
        metadata_by_index=metadata)
    source = {"weakCryptoFactCount": len(crypto_facts)}
    candidate_hints = []
    if declared_sensitive_data:
        candidate_hints.append(_candidate_hint(
            {"sensitivityGapKind": sensitivity_gap_kind},
            "The Skill protects data its own Manifest describes as "
            "sensitive using a weak hash algorithm, weak cipher, or "
            "disabled certificate verification."))
    if candidate_hints:
        source["candidateHints"] = candidate_hints
    else:
        source["modelCandidatePolicy"] = "skip_without_catalog_hint"
        source["modelCandidateSkipReason"] = (
            "static_skill_capability_controls_match")
    return [(source, [e["evidenceId"] for e in evs], evs)]


_SQL_USER_CONTROLLED_INPUT_TERMS = (
    "user input", "user-provided", "user provided", "user-supplied",
    "user supplied", "external input", "untrusted input", "query parameter",
    "form input", "request body", "用户输入", "用户提供", "外部输入", "不可信输入",
)
_SQL_SAFE_QUERY_CONSTRUCTION_TERMS = (
    "parameterized", "prepared statement", "sanitized", "hardcoded",
    "static query", "fixed query", "internal data only", "参数化查询",
    "预处理语句", "已消毒", "固定查询", "静态查询",
)


def extract_sql_injection_input_trust_gap(review_dict, file_bytes):
    """Seed a string-built SQL query capability fact paired with the
    Manifest's own trust framing of the value reaching that query.

    Distinct from declared_behavior_mismatch, permission_capability_mismatch,
    deserialization_trust_gap, and weak_crypto_sensitivity_gap: this judges
    whether the Manifest text frames the value interpolated into a
    string-built SQL query as user-controlled/external input, which the
    AST call-shape fact alone cannot establish (VR-SKILL-015's L1_semantic
    boundary: "may assess declared database access intent but cannot
    substitute for AST/data-flow facts").
    """
    if review_dict.get("engine") != "skill":
        return []
    am = review_dict.get("artifactModel") or {}
    manifest_file = am.get("manifestFile")
    manifest = am.get("manifest") or {}
    facts = ((am.get("capabilityFacts") or {}).get("facts") or [])
    sql_facts = [
        fact for fact in facts
        if fact.get("category") == "sql_query"
        and fact.get("sourceKind") != "manifest"
    ]
    if not manifest_file or not sql_facts:
        return []
    snap = review_dict.get("snapshot") or {}
    files = {f.get("normalizedPath"): f for f in (snap.get("files") or [])
             if f.get("status") == "included"}
    description = str(manifest.get("description") or "").lower()
    declared_user_controlled_input = any(
        term in description for term in _SQL_USER_CONTROLLED_INPUT_TERMS)
    declared_safe_query_construction = any(
        term in description for term in _SQL_SAFE_QUERY_CONSTRUCTION_TERMS)
    locations = [{
        "fileId": manifest_file["fileId"],
        "artifactPath": manifest_file["normalizedPath"],
        "fileDigest": "",
        "sourceByteRange": {
            "start": 0,
            "end": min(500, len(file_bytes.get(manifest_file["fileId"], b""))),
        },
        "locationSchemaVersion": "1",
    }]
    for fact in sql_facts[:7]:
        f = files.get(fact.get("artifactPath"))
        if f:
            locations.append(_fact_location(f, fact, file_bytes))
    metadata = [{
        "evidenceRole": "manifest_declaration",
        "evidenceScope": "bounded_static_skill_snapshot",
        "sqlQueryFactCount": min(len(sql_facts), 128),
        "declaredUserControlledInput": declared_user_controlled_input,
        "declaredSafeQueryConstruction": declared_safe_query_construction,
    }]
    for fact in sql_facts[:7]:
        metadata.append({
            "evidenceRole": "capability_fact",
            "evidenceScope": "bounded_static_skill_snapshot",
            "capabilityCategory": "sql_query",
            "capabilityOperation": str(fact.get("operation", ""))[:160],
        })
    evs = _make_evidence_records(
        locations, snapshot_id=snap.get("snapshotId", ""),
        producer_id="extractor.skill.sql_injection_input_trust",
        metadata_by_index=metadata)
    source = {"sqlQueryFactCount": len(sql_facts)}
    candidate_hints = []
    if declared_user_controlled_input:
        candidate_hints.append(_candidate_hint(
            {"injectionTrustGapKind": "user_controlled_query_input"},
            "The Skill builds a SQL query by string formatting/"
            "concatenation using a value its own Manifest describes as "
            "user-controlled or external input."))
    if candidate_hints:
        source["candidateHints"] = candidate_hints
    else:
        source["modelCandidatePolicy"] = "skip_without_catalog_hint"
        source["modelCandidateSkipReason"] = (
            "static_skill_capability_controls_match")
    return [(source, [e["evidenceId"] for e in evs], evs)]


_PATH_USER_CONTROLLED_INPUT_TERMS = (
    "user input", "user-provided", "user provided", "user-supplied",
    "user supplied", "external input", "untrusted input", "user-specified "
    "path", "arbitrary file", "file path parameter", "external file",
    "用户输入", "用户提供", "外部输入", "不可信输入", "外部文件",
)
_PATH_SAFE_REFERENCE_TERMS = (
    "sanitized", "hardcoded", "fixed path", "static path", "restricted to",
    "package-relative", "within the package directory", "within the skill "
    "directory", "已消毒", "固定路径", "静态路径", "限制在", "包目录内",
)


def extract_path_traversal_input_trust_gap(review_dict, file_bytes):
    """Seed a dynamically-built local file-reference capability fact paired
    with the Manifest's own trust framing of the value reaching that path.

    Distinct from declared_behavior_mismatch, permission_capability_mismatch,
    deserialization_trust_gap, weak_crypto_sensitivity_gap, and
    sql_injection_input_trust_gap: this judges whether the Manifest text
    frames the value used to build a local file path (via string
    formatting/concatenation/path-joining) as user-controlled/external
    input, which the AST call-shape fact alone cannot establish
    (VR-SKILL-002's L1_semantic boundary: "not the primary detector for
    path facts").
    """
    if review_dict.get("engine") != "skill":
        return []
    am = review_dict.get("artifactModel") or {}
    manifest_file = am.get("manifestFile")
    manifest = am.get("manifest") or {}
    facts = ((am.get("capabilityFacts") or {}).get("facts") or [])
    path_facts = [
        fact for fact in facts
        if fact.get("category") == "dynamic_path_reference"
        and fact.get("sourceKind") != "manifest"
    ]
    if not manifest_file or not path_facts:
        return []
    snap = review_dict.get("snapshot") or {}
    files = {f.get("normalizedPath"): f for f in (snap.get("files") or [])
             if f.get("status") == "included"}
    description = str(manifest.get("description") or "").lower()
    declared_user_controlled_input = any(
        term in description for term in _PATH_USER_CONTROLLED_INPUT_TERMS)
    declared_safe_path_reference = any(
        term in description for term in _PATH_SAFE_REFERENCE_TERMS)
    locations = [{
        "fileId": manifest_file["fileId"],
        "artifactPath": manifest_file["normalizedPath"],
        "fileDigest": "",
        "sourceByteRange": {
            "start": 0,
            "end": min(500, len(file_bytes.get(manifest_file["fileId"], b""))),
        },
        "locationSchemaVersion": "1",
    }]
    for fact in path_facts[:7]:
        f = files.get(fact.get("artifactPath"))
        if f:
            locations.append(_fact_location(f, fact, file_bytes))
    metadata = [{
        "evidenceRole": "manifest_declaration",
        "evidenceScope": "bounded_static_skill_snapshot",
        "pathReferenceFactCount": min(len(path_facts), 128),
        "declaredUserControlledInput": declared_user_controlled_input,
        "declaredSafePathReference": declared_safe_path_reference,
    }]
    for fact in path_facts[:7]:
        metadata.append({
            "evidenceRole": "capability_fact",
            "evidenceScope": "bounded_static_skill_snapshot",
            "capabilityCategory": "dynamic_path_reference",
            "capabilityOperation": str(fact.get("operation", ""))[:160],
        })
    evs = _make_evidence_records(
        locations, snapshot_id=snap.get("snapshotId", ""),
        producer_id="extractor.skill.path_traversal_input_trust",
        metadata_by_index=metadata)
    source = {"pathReferenceFactCount": len(path_facts)}
    candidate_hints = []
    if declared_user_controlled_input:
        candidate_hints.append(_candidate_hint(
            {"pathTrustGapKind": "user_controlled_path_reference"},
            "The Skill builds a local file path by string formatting/"
            "concatenation/path-joining using a value its own Manifest "
            "describes as user-controlled or external input."))
    if candidate_hints:
        source["candidateHints"] = candidate_hints
    else:
        source["modelCandidatePolicy"] = "skip_without_catalog_hint"
        source["modelCandidateSkipReason"] = (
            "static_skill_capability_controls_match")
    return [(source, [e["evidenceId"] for e in evs], evs)]


_TEMPLATE_USER_CONTROLLED_INPUT_TERMS = (
    "user input", "user-provided", "user provided", "user-supplied",
    "user supplied", "external input", "untrusted input", "user-submitted",
    "remote caller", "request body", "用户输入", "用户提供", "外部输入", "不可信输入",
)
_TEMPLATE_SAFE_CONSTRUCTION_TERMS = (
    "sanitized", "hardcoded", "fixed template", "static template",
    "internal data only", "bundled template file", "已消毒", "固定模板",
    "静态模板", "内部数据",
)


def extract_template_injection_input_trust_gap(review_dict, file_bytes):
    """Seed a dynamically-built Jinja2 template-source capability fact
    paired with the Manifest's own trust framing of the value reaching
    that template.

    Distinct from declared_behavior_mismatch, permission_capability_mismatch,
    deserialization_trust_gap, weak_crypto_sensitivity_gap,
    sql_injection_input_trust_gap, and path_traversal_input_trust_gap: this
    judges whether the Manifest text frames the value used to build a
    Jinja2 template's own source string (via string formatting/
    concatenation, not the render() call's context arguments) as
    user-controlled/external input, which the AST call-shape fact alone
    cannot establish (VR-SKILL-010's L1_semantic boundary: "may classify
    intended output use on cited evidence").
    """
    if review_dict.get("engine") != "skill":
        return []
    am = review_dict.get("artifactModel") or {}
    manifest_file = am.get("manifestFile")
    manifest = am.get("manifest") or {}
    facts = ((am.get("capabilityFacts") or {}).get("facts") or [])
    template_facts = [
        fact for fact in facts
        if fact.get("category") == "template_render"
        and fact.get("sourceKind") != "manifest"
    ]
    if not manifest_file or not template_facts:
        return []
    snap = review_dict.get("snapshot") or {}
    files = {f.get("normalizedPath"): f for f in (snap.get("files") or [])
             if f.get("status") == "included"}
    description = str(manifest.get("description") or "").lower()
    declared_user_controlled_input = any(
        term in description for term in _TEMPLATE_USER_CONTROLLED_INPUT_TERMS)
    declared_safe_construction = any(
        term in description for term in _TEMPLATE_SAFE_CONSTRUCTION_TERMS)
    locations = [{
        "fileId": manifest_file["fileId"],
        "artifactPath": manifest_file["normalizedPath"],
        "fileDigest": "",
        "sourceByteRange": {
            "start": 0,
            "end": min(500, len(file_bytes.get(manifest_file["fileId"], b""))),
        },
        "locationSchemaVersion": "1",
    }]
    for fact in template_facts[:7]:
        f = files.get(fact.get("artifactPath"))
        if f:
            locations.append(_fact_location(f, fact, file_bytes))
    metadata = [{
        "evidenceRole": "manifest_declaration",
        "evidenceScope": "bounded_static_skill_snapshot",
        "templateRenderFactCount": min(len(template_facts), 128),
        "declaredUserControlledInput": declared_user_controlled_input,
        "declaredSafeConstruction": declared_safe_construction,
    }]
    for fact in template_facts[:7]:
        metadata.append({
            "evidenceRole": "capability_fact",
            "evidenceScope": "bounded_static_skill_snapshot",
            "capabilityCategory": "template_render",
            "capabilityOperation": str(fact.get("operation", ""))[:160],
        })
    evs = _make_evidence_records(
        locations, snapshot_id=snap.get("snapshotId", ""),
        producer_id="extractor.skill.template_injection_input_trust",
        metadata_by_index=metadata)
    source = {"templateRenderFactCount": len(template_facts)}
    candidate_hints = []
    if declared_user_controlled_input:
        candidate_hints.append(_candidate_hint(
            {"templateTrustGapKind": "user_controlled_template_source"},
            "The Skill builds a Jinja2 template's own source string by "
            "string formatting/concatenation using a value its own "
            "Manifest describes as user-controlled or external input."))
    if candidate_hints:
        source["candidateHints"] = candidate_hints
    else:
        source["modelCandidatePolicy"] = "skip_without_catalog_hint"
        source["modelCandidateSkipReason"] = (
            "static_skill_capability_controls_match")
    return [(source, [e["evidenceId"] for e in evs], evs)]


_ISOLATION_CLAIM_TERMS = (
    "sandboxed", "sandbox", "no network access", "no internet access",
    "offline only", "offline-only", "does not access the network",
    "does not require network", "does not access network",
    "no external network calls", "no external calls", "read-only access",
    "isolated execution", "runs entirely locally", "fully local",
    "no subprocess", "does not spawn", "no external commands",
    "沙箱", "沙盒", "无网络访问", "不联网", "离线运行", "不访问网络",
    "只读", "不产生外部调用", "隔离运行", "完全本地运行", "不启动子进程",
    "不执行外部命令",
)
_DISCLOSED_HOST_ACCESS_TERMS = (
    "requires network access", "requires internet access",
    "needs internet access", "needs network access",
    "calls an external api", "calls out to", "makes network requests",
    "spawns a subprocess", "executes an external command",
    "runs an external command", "needs to access the network",
    "需要联网", "需要访问网络", "需要互联网", "调用外部api", "调用外部接口",
    "启动子进程", "执行外部命令", "需要网络访问",
)


def extract_isolation_claim_trust_gap(review_dict, file_bytes):
    """Seed process/network capability facts paired with the Manifest's own
    isolation/sandboxing claim.

    Distinct from declared_behavior_mismatch, permission_capability_mismatch,
    deserialization_trust_gap, weak_crypto_sensitivity_gap,
    sql_injection_input_trust_gap, path_traversal_input_trust_gap, and
    template_injection_input_trust_gap: this judges whether the Manifest
    text claims a sandboxed, offline, no-network, or no-subprocess
    execution boundary that observed process-spawn/network-call capability
    facts would contradict -- VR-SKILL-014's L1_semantic boundary ("may
    assess stated isolation requirements but cannot test them"; testing the
    claim itself is V2 sandbox's job).
    """
    if review_dict.get("engine") != "skill":
        return []
    am = review_dict.get("artifactModel") or {}
    manifest_file = am.get("manifestFile")
    manifest = am.get("manifest") or {}
    facts = ((am.get("capabilityFacts") or {}).get("facts") or [])
    host_facts = [
        fact for fact in facts
        if fact.get("category") in ("process", "network")
        and fact.get("sourceKind") != "manifest"
    ]
    if not manifest_file or not host_facts:
        return []
    snap = review_dict.get("snapshot") or {}
    files = {f.get("normalizedPath"): f for f in (snap.get("files") or [])
             if f.get("status") == "included"}
    description = str(manifest.get("description") or "").lower()
    claims_isolation = any(
        term in description for term in _ISOLATION_CLAIM_TERMS)
    discloses_host_access = any(
        term in description for term in _DISCLOSED_HOST_ACCESS_TERMS)
    locations = [{
        "fileId": manifest_file["fileId"],
        "artifactPath": manifest_file["normalizedPath"],
        "fileDigest": "",
        "sourceByteRange": {
            "start": 0,
            "end": min(500, len(file_bytes.get(manifest_file["fileId"], b""))),
        },
        "locationSchemaVersion": "1",
    }]
    for fact in host_facts[:7]:
        f = files.get(fact.get("artifactPath"))
        if f:
            locations.append(_fact_location(f, fact, file_bytes))
    metadata = [{
        "evidenceRole": "manifest_declaration",
        "evidenceScope": "bounded_static_skill_snapshot",
        "hostFacingFactCount": min(len(host_facts), 128),
        "declaredIsolationClaim": claims_isolation,
        "declaredHostAccessDisclosure": discloses_host_access,
    }]
    for fact in host_facts[:7]:
        metadata.append({
            "evidenceRole": "capability_fact",
            "evidenceScope": "bounded_static_skill_snapshot",
            "capabilityCategory": str(fact.get("category", ""))[:32],
            "capabilityOperation": str(fact.get("operation", ""))[:160],
        })
    evs = _make_evidence_records(
        locations, snapshot_id=snap.get("snapshotId", ""),
        producer_id="extractor.skill.isolation_claim_trust",
        metadata_by_index=metadata)
    source = {"hostFacingFactCount": len(host_facts)}
    candidate_hints = []
    if claims_isolation:
        candidate_hints.append(_candidate_hint(
            {"isolationTrustGapKind": "contradicted_isolation_claim"},
            "The Skill's own Manifest description claims a sandboxed, "
            "offline, or no-network/no-subprocess execution boundary, but "
            "the code contains process-spawning or network-call "
            "capability facts."))
    if candidate_hints:
        source["candidateHints"] = candidate_hints
    else:
        source["modelCandidatePolicy"] = "skip_without_catalog_hint"
        source["modelCandidateSkipReason"] = (
            "static_skill_capability_controls_match")
    return [(source, [e["evidenceId"] for e in evs], evs)]


_DEPENDENCY_SELF_CONTAINED_CLAIM_TERMS = (
    "no external dependencies", "no third-party dependencies",
    "zero dependencies", "dependency-free", "dependency free",
    "self-contained", "self contained", "no dependencies required",
    "fully vendored", "vendored dependencies only",
    "does not require any packages", "no packages required",
    "不依赖外部库", "无外部依赖", "零依赖", "自包含", "不需要安装任何依赖",
    "无第三方依赖",
)
_DISCLOSED_EXTERNAL_DEPENDENCY_TERMS = (
    "requires the following dependencies", "requires these dependencies",
    "depends on", "install dependencies", "see requirements.txt",
    "needs the following packages", "requires installing",
    "run pip install", "install the required packages",
    "需要安装依赖", "依赖以下库", "需要安装以下依赖", "请先安装依赖",
)


def extract_dependency_provenance_claim_gap(review_dict, file_bytes):
    """Seed installation/dependency-manifest capability facts paired with
    the Manifest's own self-contained/no-dependency claim.

    Distinct from every other trust-gap extractor above: this judges
    whether the Manifest text claims to be dependency-free or
    self-contained when the artifact snapshot itself contains a
    dependency-manifest file (requirements.txt, pyproject.toml,
    package.json, or similar lockfile) -- VR-SKILL-003's L1_semantic
    boundary ("should not invent dependency vulnerability facts"; this
    never asserts a CVE or version claim, only a disclosure-framing gap).
    """
    if review_dict.get("engine") != "skill":
        return []
    am = review_dict.get("artifactModel") or {}
    manifest_file = am.get("manifestFile")
    manifest = am.get("manifest") or {}
    facts = ((am.get("capabilityFacts") or {}).get("facts") or [])
    dependency_facts = [
        fact for fact in facts
        if fact.get("category") == "installation"
        and fact.get("operation") == "dependency_manifest"
    ]
    if not manifest_file or not dependency_facts:
        return []
    snap = review_dict.get("snapshot") or {}
    files = {f.get("normalizedPath"): f for f in (snap.get("files") or [])
             if f.get("status") == "included"}
    description = str(manifest.get("description") or "").lower()
    claims_self_contained = any(
        term in description for term in _DEPENDENCY_SELF_CONTAINED_CLAIM_TERMS)
    discloses_dependency = any(
        term in description for term in _DISCLOSED_EXTERNAL_DEPENDENCY_TERMS)
    locations = [{
        "fileId": manifest_file["fileId"],
        "artifactPath": manifest_file["normalizedPath"],
        "fileDigest": "",
        "sourceByteRange": {
            "start": 0,
            "end": min(500, len(file_bytes.get(manifest_file["fileId"], b""))),
        },
        "locationSchemaVersion": "1",
    }]
    for fact in dependency_facts[:7]:
        f = files.get(fact.get("artifactPath"))
        if f:
            locations.append(_fact_location(f, fact, file_bytes))
    metadata = [{
        "evidenceRole": "manifest_declaration",
        "evidenceScope": "bounded_static_skill_snapshot",
        "dependencyManifestFactCount": min(len(dependency_facts), 128),
        "declaredSelfContainedClaim": claims_self_contained,
        "declaredDependencyDisclosure": discloses_dependency,
    }]
    for fact in dependency_facts[:7]:
        metadata.append({
            "evidenceRole": "capability_fact",
            "evidenceScope": "bounded_static_skill_snapshot",
            "capabilityCategory": str(fact.get("category", ""))[:32],
            "capabilityOperation": str(fact.get("operation", ""))[:160],
        })
    evs = _make_evidence_records(
        locations, snapshot_id=snap.get("snapshotId", ""),
        producer_id="extractor.skill.dependency_provenance_claim",
        metadata_by_index=metadata)
    source = {"dependencyManifestFactCount": len(dependency_facts)}
    candidate_hints = []
    if claims_self_contained:
        candidate_hints.append(_candidate_hint(
            {"provenanceClaimGapKind": "undisclosed_external_dependency"},
            "The Skill's own Manifest description claims to be "
            "self-contained, dependency-free, or have no external/"
            "third-party dependencies, but the artifact snapshot contains "
            "a dependency-manifest file (requirements.txt, pyproject.toml, "
            "package.json, or similar) declaring external dependencies."))
    if candidate_hints:
        source["candidateHints"] = candidate_hints
    else:
        source["modelCandidatePolicy"] = "skip_without_catalog_hint"
        source["modelCandidateSkipReason"] = (
            "static_skill_capability_controls_match")
    return [(source, [e["evidenceId"] for e in evs], evs)]


_NO_CREDENTIAL_CLAIM_TERMS = (
    "no api key required", "no api key needed", "no credentials required",
    "no credentials needed", "does not require any api key",
    "does not require an api key", "no authentication required",
    "no authentication needed", "works without any api key",
    "works without an api key", "requires no credentials",
    "requires no api key", "no secrets required", "no token required",
    "不需要任何凭证", "不需要API密钥", "无需API密钥", "不需要身份验证",
    "无需身份验证", "不需要任何密钥", "不需要令牌",
)


def extract_credential_handling_claim_gap(review_dict, file_bytes):
    """Seed environment-variable-access capability facts paired with the
    Manifest's own no-credentials-required claim.

    VR-SKILL-011's L1_semantic boundary explicitly forbids receiving known
    secrets and forbids acting as the primary credential detector -- this
    extractor never inspects any secret value (that remains L0/gitleaks'
    job). It only compares the Manifest's own claim text against the
    existing category=="credential"/operation=="environment_access"
    capability fact (an os.getenv/os.environ.get call site) that
    extract_capability_facts already produces, the same claim-vs-fact
    shape as extract_dependency_provenance_claim_gap and
    extract_isolation_claim_trust_gap.
    """
    if review_dict.get("engine") != "skill":
        return []
    am = review_dict.get("artifactModel") or {}
    manifest_file = am.get("manifestFile")
    manifest = am.get("manifest") or {}
    facts = ((am.get("capabilityFacts") or {}).get("facts") or [])
    credential_facts = [
        fact for fact in facts
        if fact.get("category") == "credential"
        and fact.get("operation") == "environment_access"
    ]
    if not manifest_file or not credential_facts:
        return []
    snap = review_dict.get("snapshot") or {}
    files = {f.get("normalizedPath"): f for f in (snap.get("files") or [])
             if f.get("status") == "included"}
    description = str(manifest.get("description") or "").lower()
    claims_no_credentials = any(
        term in description for term in _NO_CREDENTIAL_CLAIM_TERMS)
    locations = [{
        "fileId": manifest_file["fileId"],
        "artifactPath": manifest_file["normalizedPath"],
        "fileDigest": "",
        "sourceByteRange": {
            "start": 0,
            "end": min(500, len(file_bytes.get(manifest_file["fileId"], b""))),
        },
        "locationSchemaVersion": "1",
    }]
    for fact in credential_facts[:7]:
        f = files.get(fact.get("artifactPath"))
        if f:
            locations.append(_fact_location(f, fact, file_bytes))
    metadata = [{
        "evidenceRole": "manifest_declaration",
        "evidenceScope": "bounded_static_skill_snapshot",
        "credentialAccessFactCount": min(len(credential_facts), 128),
        "declaredNoCredentialClaim": claims_no_credentials,
    }]
    for fact in credential_facts[:7]:
        metadata.append({
            "evidenceRole": "capability_fact",
            "evidenceScope": "bounded_static_skill_snapshot",
            "capabilityCategory": str(fact.get("category", ""))[:32],
            "capabilityOperation": str(fact.get("operation", ""))[:160],
        })
    evs = _make_evidence_records(
        locations, snapshot_id=snap.get("snapshotId", ""),
        producer_id="extractor.skill.credential_handling_claim",
        metadata_by_index=metadata)
    source = {"credentialAccessFactCount": len(credential_facts)}
    candidate_hints = []
    if claims_no_credentials:
        candidate_hints.append(_candidate_hint(
            {"credentialClaimGapKind": "undisclosed_credential_access"},
            "The Skill's own Manifest description claims no credentials, "
            "API key, or authentication are required, but the artifact "
            "snapshot contains an environment-variable-access capability "
            "fact (os.getenv/os.environ.get) consistent with reading a "
            "credential at runtime."))
    if candidate_hints:
        source["candidateHints"] = candidate_hints
    else:
        source["modelCandidatePolicy"] = "skip_without_catalog_hint"
        source["modelCandidateSkipReason"] = (
            "static_skill_capability_controls_match")
    return [(source, [e["evidenceId"] for e in evs], evs)]


_ENCODING_INSTRUCTION_TERMS = (
    "base64", "base-64", "hex-encoded", "hex encoded", "hex decode",
    "rot13", "rot-13", "url-encoded", "url encoded", "encoded instructions",
    "encoded payload", "escaped unicode", "zero-width character",
    "zero-width characters", "steganograph", "obfuscated text",
    "decode and execute", "decode and follow", "编码指令", "隐写",
    "零宽字符", "十六进制编码", "base64编码", "隐藏指令", "解码并执行",
    # Round 162: paraphrase expansion of the same "encoded/obfuscated
    # instruction representation" trigger concept -- the separately-scoped
    # _TRUST_SOURCE_TERMS/_TRUST_BOUNDARY_TERMS groups inside
    # _encoding_instruction_metadata are untouched. First touch of this
    # tuple.
    "caesar cipher", "morse code", "homoglyph substitution",
    "gzip-compressed payload", "凯撒密码", "摩斯密码", "同形字替换",
    "gzip压缩载荷",
)


def _encoding_instruction_metadata(text):
    _total, uncovered = _scoped_gap_count(
        text,
        signal_groups=(_ENCODING_INSTRUCTION_TERMS, _TRUST_SOURCE_TERMS),
        control_terms=_TRUST_BOUNDARY_TERMS,
    )
    return _prompt_analysis_metadata(
        signal_families=["hidden_encoding_instruction"],
        encodingSignalCount=sum(
            text.count(x) for x in _ENCODING_INSTRUCTION_TERMS),
        sourceSignalCount=sum(text.count(x) for x in _TRUST_SOURCE_TERMS),
        mitigationSignalCount=sum(text.count(x) for x in _TRUST_BOUNDARY_TERMS),
        uncoveredEncodingInstructionCount=uncovered,
    )


def _encoding_instruction_candidate_hints(metadata):
    if (metadata.get("encodingSignalCount", 0) == 0
            or metadata.get("sourceSignalCount", 0) == 0):
        return []
    if metadata.get("uncoveredEncodingInstructionCount", 0) == 0:
        return []
    return [_candidate_hint(
        {"encodingGapKind": "decoded_content_without_data_boundary"},
        "The prompt instructs decoding or interpreting an encoded/"
        "obfuscated representation of external, retrieved, or "
        "user-provided content without an evidenced data-only instruction "
        "boundary for the decoded result.")]


def _encoding_instruction_model_gate(metadata):
    if _encoding_instruction_candidate_hints(metadata):
        return True, "encoded_untrusted_content_without_data_boundary"
    return False, "encoding_controls_present_or_not_evidenced"


def extract_hidden_encoding_instruction_gap(review_dict, file_bytes):
    """Seed prompt text that instructs decoding/interpreting an encoded or
    obfuscated representation of externally-sourced content, paired with
    whether a data-only instruction boundary is evidenced for the decoded
    result.

    VR-PROMPT-005's own L1_semantic boundary ("should not be the primary
    detector for byte/encoding facts") rules out actually decoding bytes
    here -- this extractor never attempts that. It instead reuses
    VR-PROMPT-008's own trust-boundary vocabulary
    (_TRUST_SOURCE_TERMS/_TRUST_BOUNDARY_TERMS) to judge the PROMPT'S OWN
    INSTRUCTION about what to do with encoded content from an external
    source, the same "trust gap" shape Rounds 94-100 established for
    Manifest-declared-provenance gaps, applied here to prompt-authored
    instructions instead of a Skill Manifest description.
    """
    return _whole_prompt_seed(
        review_dict, file_bytes,
        triggers=_ENCODING_INSTRUCTION_TERMS,
        producer_id="extractor.prompt.hidden_encoding_instruction",
        metadata_builder=_encoding_instruction_metadata,
        candidate_hint_builder=_encoding_instruction_candidate_hints,
        model_candidate_gate=_encoding_instruction_model_gate)


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
                    "When directives contain numeric constraints (word counts, token limits, "
                    "character lengths, item counts), EXPLICITLY CHECK the arithmetic: if "
                    "directive A sets an upper bound and directive B sets a lower bound on "
                    "the SAME output, and the lower bound exceeds the upper bound, that is "
                    "a confirmed arithmetic contradiction — do not assume both can be "
                    "satisfied without actually computing whether the ranges overlap.",
                ],
                reject=[
                    "One directive governs an opening segment and the other a later segment "
                    "(e.g. 'start with a brief summary' vs 'then provide detail' — "
                    "different segments, both can be satisfied).",
                    "One rule governs an outer format and the other content inside a field.",
                    "A stated exception or condition makes both directives satisfiable.",
                    "Numeric bounds apply to different segments or are otherwise compatible "
                    "(lower bound < upper bound on the same output).",
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

    "semantic.skill.manifest_description_quality_gap": (
        SemanticFindingType(
            findingType="semantic.skill.manifest_description_quality_gap",
            engine="skill", defaultSeverity="low",
            subjectFields=[SemanticSubjectField(
                "descriptionGapKind", "enum",
                enum=["generic_boilerplate", "missing_scope_boundary",
                      "missing_trigger_condition"])],
            subjectKeyFields=["descriptionGapKind"],
            falsificationQuestion=(
                "Does the manifest description fail to give an invoking "
                "agent enough signal to decide when to use this Skill?"),
            guidanceId="semantic.skill.manifest_description_quality_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The manifest declares a name and/or description "
                    "meant to let an invoking agent decide whether and "
                    "when to use this Skill.",
                ],
                confirm=[
                    "The description is generic boilerplate that does "
                    "not convey the Skill's specific task, domain, or "
                    "input.",
                    "The description omits the trigger condition or "
                    "scope boundary an invoking agent needs to decide "
                    "correctly whether to invoke this Skill over a "
                    "sibling Skill.",
                ],
                reject=[
                    "The description concretely states the Skill's "
                    "specific task, domain, or input/output shape well "
                    "enough to support a correct invocation decision.",
                    "The name and description together already convey "
                    "the necessary scope even if either alone is terse.",
                ],
                insufficient=[
                    "Mark insufficient when adequacy cannot be judged "
                    "without knowing the full catalog of sibling Skills "
                    "the invoking agent must choose between.",
                ]),
        ), extract_manifest_description_quality_gap,
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

    "semantic.prompt.prose_reference_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.prose_reference_gap",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "referenceScope", "enum",
                enum=["prior_content", "subsequent_content",
                      "unspecified_location"])],
            subjectKeyFields=["referenceScope"],
            falsificationQuestion=(
                "Does a free-form prose reference point at material "
                "elsewhere in the document that does not actually exist "
                "or does not cover the claimed behaviour?"),
            guidanceId="semantic.prompt.prose_reference_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt uses a free-form prose pointer (not a numbered section or named-rule reference) to point at other material in the same document, e.g. \"as described above\", \"the following section\", or \"如上所述\".",
                ],
                confirm=[
                    "The pointed-to content does not actually appear anywhere else in the document.",
                    "The pointed-to content exists but does not cover the specific behaviour, rule, or claim the reference relies on.",
                ],
                reject=[
                    "The referenced material actually appears elsewhere in the document and covers the claimed behaviour.",
                    "The phrase is a numbered-section or named-rule reference already covered by the deterministic dangling-reference rules, not a free-form prose pointer.",
                    "The phrase is a generic transition attached to content immediately preceding or following it in the same paragraph, with no separate distal target to verify.",
                ],
                insufficient=[
                    "Mark insufficient when the referenced material's existence or scope cannot be determined from the cited evidence alone.",
                ]),
        ), extract_prose_reference_gap,
    ),

    "semantic.prompt.embedded_sensitive_information": (
        SemanticFindingType(
            findingType="semantic.prompt.embedded_sensitive_information",
            engine="prompt", defaultSeverity="high",
            subjectFields=[SemanticSubjectField(
                "sensitiveInformationKind", "enum",
                enum=["personal_identity", "financial", "medical",
                      "credential", "confidential_business"])],
            subjectKeyFields=["sensitiveInformationKind"],
            falsificationQuestion=(
                "Does the prompt embed a concrete, real-looking sensitive "
                "value (personal, financial, medical, credential, or "
                "confidential business data) as literal content rather "
                "than an abstract topic, policy, or placeholder example?"),
            guidanceId="semantic.prompt.embedded_sensitive_information",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt contains what appears to be a concrete data value in a personal-identity, financial, medical, credential, or confidential-business category, presented as literal content.",
                ],
                confirm=[
                    "The value is presented as real, live data (an actual identifier, a working-looking credential, a specific individual's detail, or genuine internal business/financial data) exposed to the model or a downstream reader.",
                    "Nothing in the surrounding text marks the value as fictional, anonymized, synthetic, or an illustrative placeholder.",
                ],
                reject=[
                    "The value is explicitly fictional, anonymized, redacted, or a well-known placeholder pattern (e.g. \"Jane Doe\", \"example.com\", \"555-0100\", \"XXX-XX-XXXX\").",
                    "The prompt only discusses the data category or a handling policy in the abstract, without embedding a concrete value.",
                    "The value is already public, non-identifying, or explicitly labeled synthetic test data.",
                ],
                insufficient=[
                    "Mark insufficient when the cited evidence does not show enough surrounding context to tell whether the value is real or a placeholder.",
                ]),
        ), extract_embedded_sensitive_information,
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

    "semantic.prompt.template_completeness_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.template_completeness_gap",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "templateGapKind", "enum",
                enum=["placeholder_language", "unfinished_section_marker"])],
            subjectKeyFields=["templateGapKind"],
            falsificationQuestion=(
                "Does the prompt contain free-form placeholder or "
                "unfinished-template language, distinct from the "
                "deterministic bracket/mustache/dollar-brace syntax already "
                "covered by the static placeholder rule, showing the "
                "author's own template was never completed?"),
            guidanceId="semantic.prompt.template_completeness_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt contains free-form placeholder or unfinished-template language that is not the deterministic mustache/dollar-brace/angle-bracket/square-bracket syntax already covered by the static unfilled-placeholder rule, e.g. \"lorem ipsum\", unwrapped \"insert your own ... here\", \"to be filled in\", or \"still under construction\".",
                ],
                confirm=[
                    "The surrounding text confirms this is unfinished authoring-time content that was never replaced with the intended real material.",
                    "A section, example, or value the prompt itself promises or depends on is never actually supplied anywhere in the document.",
                ],
                reject=[
                    "The phrase is a legitimate instruction directed at an end user or downstream agent (e.g. explaining how to fill out a field), not evidence that the reviewed prompt's own template is unfinished.",
                    "The placeholder-like text is clearly marked as illustrative example or demo content, not a gap in the reviewed prompt's own contract.",
                    "The match is deterministic bracket/mustache/dollar-brace placeholder syntax already covered by the static unfilled-placeholder rule.",
                ],
                insufficient=[
                    "Mark insufficient when the cited evidence alone cannot establish whether the flagged text is unfinished authoring content or intentional instructional/example text.",
                ]),
        ), extract_template_completeness_gap,
    ),

    "semantic.skill.deserialization_trust_gap": (
        SemanticFindingType(
            findingType="semantic.skill.deserialization_trust_gap",
            engine="skill", defaultSeverity="high",
            subjectFields=[SemanticSubjectField(
                "trustGapKind", "enum",
                enum=["untrusted_source_deserialized",
                      "cross_component_input_deserialized"])],
            subjectKeyFields=["trustGapKind"],
            falsificationQuestion=(
                "Does the Skill deserialize data via pickle, marshal, or "
                "an unguarded yaml.load call that its own Manifest "
                "describes as coming from an untrusted, external, or "
                "peer-component source?"),
            guidanceId="semantic.skill.deserialization_trust_gap",
            judgmentPolicy=_policy(
                applies=[
                    "A pickle/marshal/yaml.load capability fact is cited "
                    "alongside the Manifest description.",
                ],
                confirm=[
                    "The Manifest explicitly frames the deserialized "
                    "input as coming from an untrusted, external, "
                    "third-party, remote, downloaded, or user-provided "
                    "source.",
                    "The Manifest explicitly frames the deserialized "
                    "input as coming from another Skill, agent, or peer "
                    "component outside this Skill's own control.",
                    "An explicit declaredUntrustedInputSource=true "
                    "supports the corresponding trust gap.",
                ],
                reject=[
                    "The Manifest explicitly frames the deserialized "
                    "input as this Skill's own previously-written, "
                    "bundled, or otherwise internally generated state.",
                    "An explicit declaredTrustedInputSource=true "
                    "falsifies the corresponding trust gap.",
                ],
                insufficient=[
                    "Mark insufficient when the Manifest gives no "
                    "explicit framing of the deserialized input's "
                    "provenance either way.",
                ]),
            owaspAst10=["OWASP-AST04"],
        ), extract_deserialization_trust_gap,
    ),

    "semantic.skill.weak_crypto_sensitivity_gap": (
        SemanticFindingType(
            findingType="semantic.skill.weak_crypto_sensitivity_gap",
            engine="skill", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "sensitivityGapKind", "enum",
                enum=["weak_hash_algorithm",
                      "disabled_certificate_verification"])],
            subjectKeyFields=["sensitivityGapKind"],
            falsificationQuestion=(
                "Does the Skill protect data that its own Manifest "
                "describes as sensitive (passwords, credentials, tokens, "
                "personal data) using a weak hash algorithm, weak cipher, "
                "or a disabled TLS certificate-verification call?"),
            guidanceId="semantic.skill.weak_crypto_sensitivity_gap",
            judgmentPolicy=_policy(
                applies=[
                    "A weak-hash, weak-cipher, or disabled-certificate-"
                    "verification capability fact is cited alongside the "
                    "Manifest description.",
                ],
                confirm=[
                    "The Manifest explicitly frames the data reaching the "
                    "weak-crypto call as a password, credential, API key, "
                    "token, session identifier, or other personal/"
                    "personally-identifiable data.",
                    "An explicit declaredSensitiveData=true supports the "
                    "corresponding sensitivity gap.",
                ],
                reject=[
                    "The Manifest explicitly frames the data reaching the "
                    "weak-crypto call as test, dummy, sample, public, or "
                    "otherwise explicitly non-sensitive data.",
                    "An explicit declaredNonSensitiveData=true falsifies "
                    "the corresponding sensitivity gap.",
                ],
                insufficient=[
                    "Mark insufficient when the Manifest gives no explicit "
                    "framing of the protected data's sensitivity either "
                    "way.",
                ]),
            owaspAst10=["OWASP-AST02"],
        ), extract_weak_crypto_sensitivity_gap,
    ),

    "semantic.skill.sql_injection_input_trust_gap": (
        SemanticFindingType(
            findingType="semantic.skill.sql_injection_input_trust_gap",
            engine="skill", defaultSeverity="high",
            subjectFields=[SemanticSubjectField(
                "injectionTrustGapKind", "enum",
                enum=["user_controlled_query_input"])],
            subjectKeyFields=["injectionTrustGapKind"],
            falsificationQuestion=(
                "Does the Skill build a SQL query by string formatting or "
                "concatenation using a value that its own Manifest "
                "describes as user-controlled, external, or untrusted "
                "input?"),
            guidanceId="semantic.skill.sql_injection_input_trust_gap",
            judgmentPolicy=_policy(
                applies=[
                    "A string-built (f-string, %-formatted, concatenated, "
                    "or .format()-built) SQL execute/executemany call "
                    "capability fact is cited alongside the Manifest "
                    "description.",
                ],
                confirm=[
                    "The Manifest explicitly frames the interpolated "
                    "value as user input, user-provided/user-supplied "
                    "data, an external or untrusted input, a query "
                    "parameter, or request/form data.",
                    "An explicit declaredUserControlledInput=true "
                    "supports the corresponding trust gap.",
                ],
                reject=[
                    "The Manifest explicitly frames the query as "
                    "parameterized, using a prepared statement, "
                    "sanitized, hardcoded, or built only from static/"
                    "internal data.",
                    "An explicit declaredSafeQueryConstruction=true "
                    "falsifies the corresponding trust gap.",
                ],
                insufficient=[
                    "Mark insufficient when the Manifest gives no "
                    "explicit framing of the interpolated value's "
                    "provenance either way.",
                ]),
            owaspAst10=["OWASP-AST04"],
        ), extract_sql_injection_input_trust_gap,
    ),

    "semantic.skill.path_traversal_input_trust_gap": (
        SemanticFindingType(
            findingType="semantic.skill.path_traversal_input_trust_gap",
            engine="skill", defaultSeverity="high",
            subjectFields=[SemanticSubjectField(
                "pathTrustGapKind", "enum",
                enum=["user_controlled_path_reference"])],
            subjectKeyFields=["pathTrustGapKind"],
            falsificationQuestion=(
                "Does the Skill open, read, or write a local file using a "
                "path built by string formatting, concatenation, or "
                "path-joining from a value that its own Manifest describes "
                "as user-controlled, external, or untrusted input?"),
            guidanceId="semantic.skill.path_traversal_input_trust_gap",
            judgmentPolicy=_policy(
                applies=[
                    "A dynamically-built (f-string, concatenated, "
                    "%-formatted, .format()-built, or os.path.join-built) "
                    "local file-reference capability fact is cited "
                    "alongside the Manifest description.",
                ],
                confirm=[
                    "The Manifest explicitly frames the value used to "
                    "build the path as user input, user-provided/"
                    "user-supplied data, an external or untrusted input, "
                    "or an externally-supplied file/path parameter.",
                    "An explicit declaredUserControlledInput=true "
                    "supports the corresponding trust gap.",
                ],
                reject=[
                    "The Manifest explicitly frames the path as "
                    "sanitized, hardcoded, fixed/static, or restricted to "
                    "a package- or Skill-relative directory.",
                    "An explicit declaredSafePathReference=true "
                    "falsifies the corresponding trust gap.",
                ],
                insufficient=[
                    "Mark insufficient when the Manifest gives no "
                    "explicit framing of the path value's provenance "
                    "either way.",
                ]),
            owaspAst10=["OWASP-AST04"],
        ), extract_path_traversal_input_trust_gap,
    ),

    "semantic.skill.template_injection_input_trust_gap": (
        SemanticFindingType(
            findingType="semantic.skill.template_injection_input_trust_gap",
            engine="skill", defaultSeverity="high",
            subjectFields=[SemanticSubjectField(
                "templateTrustGapKind", "enum",
                enum=["user_controlled_template_source"])],
            subjectKeyFields=["templateTrustGapKind"],
            falsificationQuestion=(
                "Does the Skill construct a Jinja2 template's own source "
                "string by string formatting or concatenation using a "
                "value that its own Manifest describes as user-controlled, "
                "external, or untrusted input?"),
            guidanceId="semantic.skill.template_injection_input_trust_gap",
            judgmentPolicy=_policy(
                applies=[
                    "A string-built (f-string, %-formatted, concatenated, "
                    "or .format()-built) Jinja2 Template()/from_string() "
                    "source-construction capability fact is cited "
                    "alongside the Manifest description.",
                ],
                confirm=[
                    "The Manifest explicitly frames the interpolated "
                    "value used to build the template source as user "
                    "input, user-provided/user-supplied data, an "
                    "external or untrusted input, or remote-caller/"
                    "request data.",
                    "An explicit declaredUserControlledInput=true "
                    "supports the corresponding trust gap.",
                ],
                reject=[
                    "The Manifest explicitly frames the template source "
                    "as sanitized, hardcoded, a fixed/static template, a "
                    "bundled template file, or built only from static/"
                    "internal data.",
                    "An explicit declaredSafeConstruction=true falsifies "
                    "the corresponding trust gap.",
                ],
                insufficient=[
                    "Mark insufficient when the Manifest gives no "
                    "explicit framing of the template-source value's "
                    "provenance either way.",
                ]),
            owaspAst10=["OWASP-AST04"],
        ), extract_template_injection_input_trust_gap,
    ),
    "semantic.skill.isolation_claim_trust_gap": (
        SemanticFindingType(
            findingType="semantic.skill.isolation_claim_trust_gap",
            engine="skill", defaultSeverity="high",
            subjectFields=[SemanticSubjectField(
                "isolationTrustGapKind", "enum",
                enum=["contradicted_isolation_claim"])],
            subjectKeyFields=["isolationTrustGapKind"],
            falsificationQuestion=(
                "Does the Skill's own Manifest description claim a "
                "sandboxed, offline, or no-network/no-subprocess execution "
                "boundary that is contradicted by observed process-"
                "spawning or network-call capability facts?"),
            guidanceId="semantic.skill.isolation_claim_trust_gap",
            judgmentPolicy=_policy(
                applies=[
                    "A process-spawn or network-call capability fact is "
                    "cited alongside the Manifest description.",
                ],
                confirm=[
                    "The Manifest explicitly claims a sandboxed, isolated, "
                    "offline/no-internet, no-network, read-only, or "
                    "no-subprocess/no-external-command execution "
                    "boundary.",
                    "An explicit declaredIsolationClaim=true supports the "
                    "corresponding trust gap.",
                ],
                reject=[
                    "The Manifest explicitly discloses and requires the "
                    "cited network access, external API call, or "
                    "subprocess/external-command execution as part of its "
                    "stated behavior.",
                    "An explicit declaredHostAccessDisclosure=true "
                    "falsifies the corresponding trust gap.",
                ],
                insufficient=[
                    "Mark insufficient when the Manifest gives no explicit "
                    "isolation claim either way.",
                ]),
            owaspAst10=["OWASP-AST04"],
        ), extract_isolation_claim_trust_gap,
    ),

    "semantic.skill.dependency_provenance_claim_gap": (
        SemanticFindingType(
            findingType="semantic.skill.dependency_provenance_claim_gap",
            engine="skill", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "provenanceClaimGapKind", "enum",
                enum=["undisclosed_external_dependency"])],
            subjectKeyFields=["provenanceClaimGapKind"],
            falsificationQuestion=(
                "Does the Skill's own Manifest description claim to be "
                "self-contained, dependency-free, or have no external "
                "dependencies, when the artifact snapshot itself contains "
                "a dependency-manifest file (requirements.txt, "
                "pyproject.toml, package.json, or similar) declaring "
                "external dependencies?"),
            guidanceId="semantic.skill.dependency_provenance_claim_gap",
            judgmentPolicy=_policy(
                applies=[
                    "An installation/dependency_manifest capability fact "
                    "is cited alongside the Manifest description.",
                ],
                confirm=[
                    "The Manifest explicitly claims to be self-contained, "
                    "dependency-free, or to have no external/third-party "
                    "dependencies.",
                    "An explicit declaredSelfContainedClaim=true supports "
                    "the corresponding trust gap.",
                ],
                reject=[
                    "The Manifest explicitly discloses and requires "
                    "external dependencies as part of its stated "
                    "installation/behavior.",
                    "An explicit declaredDependencyDisclosure=true "
                    "falsifies the corresponding trust gap.",
                ],
                insufficient=[
                    "Mark insufficient when the Manifest gives no explicit "
                    "self-contained claim either way.",
                ]),
            owaspAst10=["OWASP-AST02"],
        ), extract_dependency_provenance_claim_gap,
    ),

    "semantic.skill.credential_handling_claim_gap": (
        SemanticFindingType(
            findingType="semantic.skill.credential_handling_claim_gap",
            engine="skill", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "credentialClaimGapKind", "enum",
                enum=["undisclosed_credential_access"])],
            subjectKeyFields=["credentialClaimGapKind"],
            falsificationQuestion=(
                "Does the Skill's own Manifest description claim that no "
                "credentials, API key, or authentication are required, "
                "when the artifact snapshot itself contains an "
                "environment-variable-access capability fact "
                "(os.getenv/os.environ.get) consistent with reading a "
                "credential at runtime?"),
            guidanceId="semantic.skill.credential_handling_claim_gap",
            judgmentPolicy=_policy(
                applies=[
                    "A credential/environment_access capability fact is "
                    "cited alongside the Manifest description.",
                ],
                confirm=[
                    "The Manifest explicitly claims no credentials, API "
                    "key, or authentication are required.",
                    "An explicit declaredNoCredentialClaim=true supports "
                    "the corresponding trust gap.",
                ],
                reject=[
                    "The Manifest explicitly discloses that a credential, "
                    "API key, or authentication is required as part of "
                    "its stated setup or usage.",
                    "The cited environment-variable access is explicitly "
                    "and clearly unrelated to any credential (e.g. a "
                    "locale or path setting), falsifying the "
                    "corresponding trust gap.",
                ],
                insufficient=[
                    "Mark insufficient when the Manifest gives no "
                    "explicit no-credentials claim either way.",
                ]),
            owaspAst10=["OWASP-AST02"],
        ), extract_credential_handling_claim_gap,
    ),

    "semantic.prompt.hidden_encoding_instruction_gap": (
        SemanticFindingType(
            findingType="semantic.prompt.hidden_encoding_instruction_gap",
            engine="prompt", defaultSeverity="medium",
            subjectFields=[SemanticSubjectField(
                "encodingGapKind", "enum",
                enum=["decoded_content_without_data_boundary"])],
            subjectKeyFields=["encodingGapKind"],
            falsificationQuestion=(
                "Does the prompt instruct decoding or interpreting an "
                "encoded/obfuscated representation of external, retrieved, "
                "or user-provided content without treating the decoded "
                "result as untrusted data?"),
            guidanceId="semantic.prompt.hidden_encoding_instruction_gap",
            judgmentPolicy=_policy(
                applies=[
                    "The prompt instructs decoding, translating, or "
                    "otherwise interpreting an encoded or obfuscated "
                    "representation (e.g. base64, hex, ROT13, zero-width "
                    "Unicode) of content that comes from an external, "
                    "retrieved, or user-provided source.",
                ],
                confirm=[
                    "The decoded or interpreted content can be followed as "
                    "instructions and no data-only boundary or "
                    "re-validation step is declared for it.",
                ],
                reject=[
                    "The prompt explicitly requires the decoded content to "
                    "be treated as untrusted data, re-validated, or "
                    "forbidden from being followed as instructions.",
                    "The encoding or decoding applies only to this Skill's "
                    "own internal, bundled, or otherwise trusted data, not "
                    "external or untrusted content.",
                ],
                insufficient=[
                    "Mark insufficient when the prompt does not show "
                    "whether the decoded content is treated as "
                    "instructions or as data.",
                ]),
        ), extract_hidden_encoding_instruction_gap,
    ),
}


def entry(finding_type: str) -> Optional[Tuple[SemanticFindingType, Extractor]]:
    return CATALOG.get(finding_type)
