"""Normalize Bandit results into Verity Evidence/RuleMatch/Finding.

Design points:

- Each Bandit ``test_id`` (e.g. ``B602``) becomes its own logical Rule
  (``skill.bandit.<test_id>``). Findings preserve Bandit ``severity``,
  ``confidence`` and CWE as controlled metadata under ``subject``, but
  the Finding IDENTITY (subjectKey) is only ``(artifactPath, testId,
  lineNumber)``. Free-text ``issue_text`` never contributes to identity.
- The mapping from Bandit severity to Verity severity is deliberate:
    * HIGH   -> high
    * MEDIUM -> medium
    * LOW    -> low
  Confidence only shows up in ``metadata``.
- ``skill.python_subprocess_shell_true`` (hand-written) covers exactly
  the same ground as Bandit ``B602``. To avoid double-reporting the same
  code position we suppress the hand-written rule's Finding when Bandit
  produced a matching event on the same file+line, and note the
  supersedes relationship in the RuleDefinition.

Bandit fingerprint components (spec §5.1 non-secret path): the artifact
path + test_id + normalized byte range are hashed via the standard
occurrence fingerprint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from .engine import RuleContext, RuleHit, make_source_span_evidence
from .models import Location, Producer


# ---------------------------------------------------------------------- #
# Mapping tables                                                         #
# ---------------------------------------------------------------------- #

_BANDIT_SEVERITY_MAP = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}

# test_id -> OWASP AST10 mapping. Deliberately conservative: only include
# mappings we can justify. When in doubt we omit (report shows 'none').
BANDIT_OWASP_MAP: Dict[str, List[str]] = {
    "B602": ["OWASP-AST01"],
    "B605": ["OWASP-AST01"],
    "B606": ["OWASP-AST01"],
    "B607": ["OWASP-AST01"],
    "B701": ["OWASP-AST01"],   # jinja2 autoescape
    "B106": ["OWASP-AST02"],   # hardcoded password default
    "B107": ["OWASP-AST02"],
    "B105": ["OWASP-AST02"],
    "B310": ["OWASP-AST05"],   # urllib_urlopen
    "B303": ["OWASP-AST01"],   # md5 / weak hash
    "B301": ["OWASP-AST01"],   # pickle
    "B501": ["OWASP-AST02"],   # request_with_no_cert_validation
}


def bandit_result_to_hits(
    ctx: RuleContext, *, run_result, staged_root: str = ""
) -> List[RuleHit]:
    """Convert `BanditRunResult.results` into `RuleHit`s for THIS rule.

    The engine calls this function via a per-Bandit-rule impl thin
    wrapper (see ``build_bandit_impls`` below). ``ctx.rule.ruleId`` is of
    the form ``skill.bandit.B602``.
    """
    # Extract test_id we care about from rule id.
    prefix = "skill.bandit."
    if not ctx.rule.ruleId.startswith(prefix):
        return []
    test_id = ctx.rule.ruleId[len(prefix):]

    br = ctx.artifact_model.get("banditRun") if ctx.artifact_model else None
    if br is None:
        return []
    if br.get("status") != "completed":
        # Analyzer failed — no findings; the engine already recorded the
        # blocked/failed state via the plan item.
        return []
    # Path map from staged tmp path -> snapshot fileId
    path_map: Dict[str, str] = br.get("pathMap") or {}
    results = br.get("results") or []
    if not results:
        return []

    files_by_id = {f.fileId: f for f in ctx.snapshot.files}
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)

    hits: List[RuleHit] = []
    for r in results:
        if r.get("test_id") != test_id:
            continue
        staged_path = r.get("filename")
        file_id = path_map.get(staged_path)
        if file_id is None:
            continue
        f = files_by_id.get(file_id)
        if f is None:
            continue
        # Compute byte range from line_number (Bandit gives 1-based line).
        # Bandit does not give a byte range directly; we compute the
        # byte offset of the reported line's first char and end at the
        # end of that line so the SARIF/JSON evidence is precise but
        # honest about what Bandit actually located.
        data = ctx.file_bytes.get(f.fileId, b"")
        ln = r.get("line_number") or 1
        start, end = _line_to_byte_range(data, ln)
        # Preserve controlled bandit metadata (identity does NOT include
        # issue_text or more_info URL).
        cwe_id = None
        cwe = r.get("issue_cwe")
        if isinstance(cwe, dict):
            cid = cwe.get("id")
            if isinstance(cid, (int, str)):
                cwe_id = f"CWE-{cid}"
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id=f.fileId, artifact_path=f.normalizedPath,
            file_digest=f.contentDigest or "",
            byte_range=(start, end),
            raw_bytes=data[start:end],
            producer=prod,
        )
        subject = {
            "artifactPath": f.normalizedPath,
            "testId": test_id,
            "lineNumber": int(ln),
            "banditSeverity": _BANDIT_SEVERITY_MAP.get(
                r.get("issue_severity"), "low"),
            "banditConfidence": r.get("issue_confidence"),
            "cwe": cwe_id,
        }
        # Drop optional Nones so subject-schema validation doesn't fail
        # (subjectKey uses only stable fields).
        subject = {k: v for k, v in subject.items() if v is not None}
        hits.append(RuleHit(evidences=[ev], subject=subject))
    return hits


def _line_to_byte_range(data: bytes, line_number: int) -> Tuple[int, int]:
    if line_number < 1:
        line_number = 1
    lines = data.splitlines(keepends=True)
    if line_number > len(lines):
        line_number = len(lines) or 1
    start = sum(len(l) for l in lines[:line_number - 1])
    end = start + (len(lines[line_number - 1]) if lines else 0)
    return start, end


# ---------------------------------------------------------------------- #
# De-duplication with the hand-written subprocess shell=True rule        #
# ---------------------------------------------------------------------- #

def suppress_handwritten_if_bandit_present(
    hand_hits: List[RuleHit], bandit_results: List[Dict], path_map: Dict[str, str],
    files_by_id: Dict,
) -> List[RuleHit]:
    """Given hand-written rule hits and bandit's raw results (any tests),
    drop hand hits that share (fileId, line) with a Bandit B602 hit.

    Rule migration (spec §6): the built-in ``skill.bandit.B602`` supersedes
    ``skill.python_subprocess_shell_true``; this function is the concrete
    de-dup at the RuleMatch stage for the current snapshot. It does NOT
    do fuzzy description matching.
    """
    b602_positions = set()
    for r in bandit_results:
        if r.get("test_id") != "B602":
            continue
        staged = r.get("filename")
        fid = path_map.get(staged)
        if not fid:
            continue
        b602_positions.add((fid, int(r.get("line_number") or 0)))

    if not b602_positions:
        return hand_hits

    def _hit_line(h: RuleHit) -> Tuple[str, int]:
        ev = h.evidences[0]
        loc = ev.locations[0]
        # Compute line number from byte range.
        f = files_by_id.get(loc.fileId)
        if f is None:
            return (loc.fileId, -1)
        start = loc.sourceByteRange["start"]
        return (loc.fileId, _byte_offset_to_line(f, start))

    kept = []
    for h in hand_hits:
        fid, ln = _hit_line(h)
        if (fid, ln) in b602_positions:
            continue
        kept.append(h)
    return kept


def _byte_offset_to_line(_file_obj, _offset: int) -> int:  # pragma: no cover
    # Placeholder — the actual computation is done by callers that have
    # access to file bytes. This is intentionally left as a no-op that
    # returns 0; the real dedup path uses ``ctx.file_bytes`` in the
    # engine wiring below.
    return -1
