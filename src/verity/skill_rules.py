"""Deterministic Skill Auditor rule implementations (round 3).

All rules are text- / AST-level static analysis. Nothing here executes
the skill under review, imports its code, or reads outside the frozen
Snapshot. Each rule returns ``RuleHit`` values with precise evidence.

Boundaries:

- Rules that need the manifest set ``requiresManifest=True`` on their
  RuleDefinition and pull data from ``ctx.artifact_model["manifest"]``.
  If the parser failed, the engine short-circuits them to
  ``blocked_by_upstream_failure``.
- File-level rules (dangerous shell text, Python AST, etc.) do NOT
  depend on the manifest and continue to run even when the manifest
  parser fails, so partial coverage is still useful.
"""

from __future__ import annotations

import ast
import re
from typing import Dict, List, Tuple

from .engine import RuleContext, RuleHit, make_source_span_evidence
from .models import Location, Producer


# --------------------------------------------------------------------- #
# Small helpers                                                         #
# --------------------------------------------------------------------- #

def _prod(ctx: RuleContext) -> Producer:
    return Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)


def _skill_md(ctx: RuleContext):
    mf = ctx.artifact_model.get("manifestFile") if ctx.artifact_model else None
    if not mf:
        return None
    for f in ctx.snapshot.files:
        if f.fileId == mf["fileId"]:
            return f
    return None


def _manifest_locations(ctx: RuleContext) -> List[Location]:
    """Return a single Location covering the whole SKILL.md file (or its
    frontmatter range if known)."""
    f = _skill_md(ctx)
    if f is None:
        return []
    br = ctx.artifact_model.get("manifestByteRange") if ctx.artifact_model else None
    if br:
        start, end = br
    else:
        data = ctx.file_bytes.get(f.fileId, b"")
        start, end = 0, len(data)
    return [Location(
        fileId=f.fileId, artifactPath=f.normalizedPath,
        fileDigest=f.contentDigest or "",
        sourceByteRange={"start": start, "end": end},
    )]


def _find_key_range(yaml_bytes: bytes, key: str) -> Tuple[int, int] | None:
    """Locate a top-level YAML key in the raw frontmatter bytes.

    Returns the byte range of the whole ``key: value`` line, or None if
    the key can't be found reliably. This is best-effort; the finding is
    still valid even if this returns None (we fall back to the whole
    frontmatter range).
    """
    m = re.search((r"(?m)^" + re.escape(key) + r"[ \t]*:.*$").encode("utf-8"), yaml_bytes)
    if m is None:
        return None
    return (m.start(), m.end())


def _skill_md_bytes(ctx: RuleContext) -> bytes:
    f = _skill_md(ctx)
    return ctx.file_bytes.get(f.fileId, b"") if f else b""


# --------------------------------------------------------------------- #
# S1. Missing SKILL.md                                                  #
# --------------------------------------------------------------------- #

def skill_missing_skill_md(ctx: RuleContext) -> List[RuleHit]:
    if ctx.artifact_model and ctx.artifact_model.get("hasSkillMd"):
        return []
    # Attach evidence to the root of the snapshot (a synthetic location).
    # We use the first included file as anchor if present, otherwise the
    # artifact-level location with an empty byte range.
    anchor = next((f for f in ctx.snapshot.files if f.status == "included"), None)
    if anchor is None:
        # Truly empty artifact; still produce a finding with a virtual location.
        # Locations are required in the model; use a synthetic file id/path.
        loc = Location(
            fileId="virtual:artifact-root",
            artifactPath="", fileDigest="",
            sourceByteRange={"start": 0, "end": 0},
        )
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id="virtual:artifact-root",
            artifact_path="", file_digest="",
            byte_range=(0, 0), raw_bytes=b"missing_skill_md",
            producer=_prod(ctx),
        )
        return [RuleHit(evidences=[ev], subject={
            "artifactPath": "",
            "manifestIssueCategory": "missing_skill_md",
        })]
    ev = make_source_span_evidence(
        snapshot_id=ctx.snapshot.snapshotId,
        file_id=anchor.fileId, artifact_path=anchor.normalizedPath,
        file_digest=anchor.contentDigest or "",
        byte_range=(0, 0), raw_bytes=b"missing_skill_md",
        producer=_prod(ctx),
    )
    return [RuleHit(evidences=[ev], subject={
        "artifactPath": anchor.normalizedPath,
        "manifestIssueCategory": "missing_skill_md",
    })]


# --------------------------------------------------------------------- #
# S2. Manifest parse failure surfaced as a Finding                      #
# --------------------------------------------------------------------- #

_PARSE_FAIL_CODES = {
    "frontmatter_not_closed", "yaml_parse_error", "yaml_root_not_mapping",
    "yaml_too_deep", "yaml_too_many_keys",
    "frontmatter_over_budget", "frontmatter_too_many_lines",
    "frontmatter_alias_bomb_suspected", "yaml_unexpected_error",
}


def skill_manifest_invalid(ctx: RuleContext) -> List[RuleHit]:
    if not ctx.artifact_model or not ctx.artifact_model.get("hasSkillMd"):
        return []
    diags = ctx.artifact_model.get("parserDiagnostics") or []
    hits: List[RuleHit] = []
    f = _skill_md(ctx)
    if f is None:
        return []
    br = ctx.artifact_model.get("manifestByteRange")
    if br:
        start, end = br
    else:
        data = ctx.file_bytes.get(f.fileId, b"")
        start, end = 0, min(len(data), 4)
    for d in diags:
        if d["code"] not in _PARSE_FAIL_CODES:
            continue
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id=f.fileId, artifact_path=f.normalizedPath,
            file_digest=f.contentDigest or "",
            byte_range=(start, end),
            raw_bytes=d["code"].encode("utf-8"),
            producer=_prod(ctx),
        )
        hits.append(RuleHit(evidences=[ev], subject={
            "artifactPath": f.normalizedPath,
            "parseErrorCode": d["code"],
        }))
    return hits


# --------------------------------------------------------------------- #
# S3 / S4.  name / description missing or blank                         #
# --------------------------------------------------------------------- #

# Agent Skills specification: 1..64 lowercase ASCII letters, digits and
# hyphens; no leading/trailing/consecutive hyphen; must match root directory.
_NAME_OK = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_SKILL_NAME_CHARS = 64
MAX_SKILL_DESCRIPTION_CHARS = 1024
MAX_SKILL_COMPATIBILITY_CHARS = 500


def _mkfield_hit(ctx: RuleContext, field: str, category: str) -> RuleHit:
    yaml_bytes_range = ctx.artifact_model.get("manifestByteRange")
    f = _skill_md(ctx)
    assert f is not None
    data = ctx.file_bytes.get(f.fileId, b"")
    if yaml_bytes_range:
        yaml_bytes = data[yaml_bytes_range[0]:yaml_bytes_range[1]]
        rng = _find_key_range(yaml_bytes, field)
        if rng:
            start = yaml_bytes_range[0] + rng[0]
            end = yaml_bytes_range[0] + rng[1]
        else:
            start, end = yaml_bytes_range
    else:
        start, end = 0, len(data)
    ev = make_source_span_evidence(
        snapshot_id=ctx.snapshot.snapshotId,
        file_id=f.fileId, artifact_path=f.normalizedPath,
        file_digest=f.contentDigest or "",
        byte_range=(start, end), raw_bytes=data[start:end],
        producer=_prod(ctx),
    )
    return RuleHit(evidences=[ev], subject={
        "artifactPath": f.normalizedPath,
        "fieldName": field,
        "fieldIssue": category,
    })


def skill_manifest_name_issue(ctx: RuleContext) -> List[RuleHit]:
    m = (ctx.artifact_model or {}).get("manifest")
    if m is None:
        return []
    name = m.get("name")
    if name is None:
        return [_mkfield_hit(ctx, "name", "missing")]
    if not isinstance(name, str):
        return [_mkfield_hit(ctx, "name", "invalid_type")]
    if not name.strip():
        return [_mkfield_hit(ctx, "name", "blank")]
    if len(name) > MAX_SKILL_NAME_CHARS:
        return [_mkfield_hit(ctx, "name", "too_long")]
    if not _NAME_OK.fullmatch(name):
        return [_mkfield_hit(ctx, "name", "invalid_syntax")]
    root_name = ctx.snapshot.artifactRootName
    if root_name is not None and name != root_name:
        return [_mkfield_hit(ctx, "name", "directory_mismatch")]
    return []


def skill_manifest_description_missing(ctx: RuleContext) -> List[RuleHit]:
    m = (ctx.artifact_model or {}).get("manifest")
    if m is None:
        return []
    desc = m.get("description")
    if desc is None:
        return [_mkfield_hit(ctx, "description", "missing")]
    if not isinstance(desc, str):
        return [_mkfield_hit(ctx, "description", "invalid_type")]
    if not desc.strip():
        return [_mkfield_hit(ctx, "description", "blank")]
    if len(desc) > MAX_SKILL_DESCRIPTION_CHARS:
        return [_mkfield_hit(ctx, "description", "too_long")]
    return []


def skill_manifest_optional_field_issue(ctx: RuleContext) -> List[RuleHit]:
    m = (ctx.artifact_model or {}).get("manifest")
    if m is None:
        return []
    hits: List[RuleHit] = []
    compatibility = m.get("compatibility")
    if compatibility is not None:
        if not isinstance(compatibility, str):
            hits.append(_mkfield_hit(ctx, "compatibility", "invalid_type"))
        elif not compatibility.strip():
            hits.append(_mkfield_hit(ctx, "compatibility", "blank"))
        elif len(compatibility) > MAX_SKILL_COMPATIBILITY_CHARS:
            hits.append(_mkfield_hit(ctx, "compatibility", "too_long"))
    metadata = m.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            hits.append(_mkfield_hit(ctx, "metadata", "invalid_type"))
        elif not all(isinstance(k, str) and isinstance(v, str)
                     for k, v in metadata.items()):
            hits.append(_mkfield_hit(ctx, "metadata", "invalid_value"))
    allowed_tools = m.get("allowed-tools")
    if allowed_tools is not None:
        if not isinstance(allowed_tools, str):
            hits.append(_mkfield_hit(ctx, "allowed-tools", "invalid_type"))
        elif not allowed_tools.strip():
            hits.append(_mkfield_hit(ctx, "allowed-tools", "blank"))
    return hits


# --------------------------------------------------------------------- #
# S5 / S6. Reference targets                                            #
# --------------------------------------------------------------------- #

def _reference_locations(ctx: RuleContext, ref: str) -> Location:
    """Best-effort location for a reference: the `refs`/`scripts` line in
    the frontmatter."""
    f = _skill_md(ctx)
    assert f is not None
    br = ctx.artifact_model.get("manifestByteRange")
    data = ctx.file_bytes.get(f.fileId, b"")
    if br:
        yaml_bytes = data[br[0]:br[1]]
        idx = yaml_bytes.find(ref.encode("utf-8"))
        if idx >= 0:
            return Location(
                fileId=f.fileId, artifactPath=f.normalizedPath,
                fileDigest=f.contentDigest or "",
                sourceByteRange={"start": br[0] + idx,
                                 "end": br[0] + idx + len(ref)},
            )
        start, end = br
    else:
        start, end = 0, len(data)
    return Location(
        fileId=f.fileId, artifactPath=f.normalizedPath,
        fileDigest=f.contentDigest or "",
        sourceByteRange={"start": start, "end": end},
    )


def _normalize_ref(ref: str) -> Tuple[str, str]:
    """Return (issue|"", normalized). Issue is one of:
    'absolute_path', 'path_escape', '' (ok).
    """
    if ref.startswith("/"):
        return "absolute_path", ref
    if "\\" in ref:
        return "backslash_path", ref
    parts = ref.split("/")
    if any(p == ".." for p in parts):
        return "path_escape", ref
    if any(p in ("", ".") for p in parts if p != parts[0] or parts[0] not in ("",)):
        # collapse multiple slashes / dotdirs
        pass
    return "", ref


_SCRIPT_SUFFIXES = {".py", ".sh", ".js", ".ts", ".rb", ".go"}


def skill_manifest_missing_reference(ctx: RuleContext) -> List[RuleHit]:
    m = (ctx.artifact_model or {}).get("manifest")
    if m is None:
        return []
    files_by_path = {f.normalizedPath: f for f in ctx.snapshot.files}
    manifest_file = _skill_md(ctx)
    assert manifest_file is not None
    base_dir = manifest_file.normalizedPath.rsplit("/", 1)[0]
    if base_dir == manifest_file.normalizedPath:
        base_dir = ""
    hits: List[RuleHit] = []
    for ref in m.get("refs", []):
        issue, _ = _normalize_ref(ref)
        if issue:
            continue  # a different rule handles path safety
        # resolve relative to the manifest directory
        target = ref if base_dir == "" else f"{base_dir}/{ref}"
        if target in files_by_path:
            continue
        # If a sibling with a different script suffix exists, the suffix-
        # mismatch rule will cover this case; do NOT double-report here.
        parts = ref.rsplit(".", 1)
        if len(parts) == 2 and ("." + parts[1].lower()) in _SCRIPT_SUFFIXES:
            stem = parts[0]
            found_sibling = any(
                (f"{stem}{ext}" if base_dir == "" else f"{base_dir}/{stem}{ext}") in files_by_path
                for ext in _SCRIPT_SUFFIXES if ext != "." + parts[1].lower()
            )
            if found_sibling:
                continue
        loc = _reference_locations(ctx, ref)
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id=loc.fileId, artifact_path=loc.artifactPath,
            file_digest=loc.fileDigest,
            byte_range=(loc.sourceByteRange["start"], loc.sourceByteRange["end"]),
            raw_bytes=ref.encode("utf-8"),
            producer=_prod(ctx),
        )
        hits.append(RuleHit(evidences=[ev], subject={
            "artifactPath": manifest_file.normalizedPath,
            "referencePath": ref,
            "referenceIssue": "not_found",
        }))
    return hits


def skill_manifest_unsafe_reference_path(ctx: RuleContext) -> List[RuleHit]:
    m = (ctx.artifact_model or {}).get("manifest")
    if m is None:
        return []
    manifest_file = _skill_md(ctx)
    assert manifest_file is not None
    hits: List[RuleHit] = []
    for ref in m.get("refs", []):
        issue, _ = _normalize_ref(ref)
        if not issue:
            continue
        loc = _reference_locations(ctx, ref)
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id=loc.fileId, artifact_path=loc.artifactPath,
            file_digest=loc.fileDigest,
            byte_range=(loc.sourceByteRange["start"], loc.sourceByteRange["end"]),
            raw_bytes=ref.encode("utf-8"),
            producer=_prod(ctx),
        )
        hits.append(RuleHit(evidences=[ev], subject={
            "artifactPath": manifest_file.normalizedPath,
            "referencePath": ref,
            "referenceIssue": issue,
        }))
    return hits


# --------------------------------------------------------------------- #
# S7. Unpinned dependency versions (structured deps only)               #
# --------------------------------------------------------------------- #

# We consider "pinned" only if the value is a bare exact version like
# "1.2.3" or an equality specifier "==1.2.3" / "= 1.2.3". Everything else
# (floating range, "latest", None, "*", ">=x") is flagged.
_PINNED_VERSION = re.compile(r"\A(?:==\s*)?\d+(?:\.\d+){0,3}(?:[+\-][A-Za-z0-9.]+)?\Z")


def skill_manifest_unpinned_dependency(ctx: RuleContext) -> List[RuleHit]:
    m = (ctx.artifact_model or {}).get("manifest")
    if m is None:
        return []
    manifest_file = _skill_md(ctx)
    assert manifest_file is not None
    hits: List[RuleHit] = []
    for dep in m.get("dependencies", []):
        name = dep.get("name") or ""
        ver = dep.get("version")
        if isinstance(ver, str) and _PINNED_VERSION.match(ver.strip()):
            continue
        # attribute the finding at the dependency line if we can find it
        loc = _reference_locations(ctx, name)
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id=loc.fileId, artifact_path=loc.artifactPath,
            file_digest=loc.fileDigest,
            byte_range=(loc.sourceByteRange["start"], loc.sourceByteRange["end"]),
            raw_bytes=name.encode("utf-8"),
            producer=_prod(ctx),
        )
        hits.append(RuleHit(evidences=[ev], subject={
            "artifactPath": manifest_file.normalizedPath,
            "dependencyName": name,
            "dependencyIssue": "unpinned",
        }))
    return hits


# --------------------------------------------------------------------- #
# S8. Open-ended permission / tool wildcard (structured field)          #
# --------------------------------------------------------------------- #

def skill_manifest_permission_wildcard(ctx: RuleContext) -> List[RuleHit]:
    m = (ctx.artifact_model or {}).get("manifest")
    if m is None:
        return []
    manifest_file = _skill_md(ctx)
    assert manifest_file is not None
    hits: List[RuleHit] = []
    for perm in m.get("permissions", []):
        if perm == "*" or perm == "/" or perm.endswith("/*") or perm == "**":
            loc = _reference_locations(ctx, perm)
            ev = make_source_span_evidence(
                snapshot_id=ctx.snapshot.snapshotId,
                file_id=loc.fileId, artifact_path=loc.artifactPath,
                file_digest=loc.fileDigest,
                byte_range=(loc.sourceByteRange["start"], loc.sourceByteRange["end"]),
                raw_bytes=perm.encode("utf-8"),
                producer=_prod(ctx),
            )
            hits.append(RuleHit(evidences=[ev], subject={
                "artifactPath": manifest_file.normalizedPath,
                "permissionValue": perm,
                "permissionIssue": "wildcard_or_root",
            }))
    return hits


# --------------------------------------------------------------------- #
# S9. Untrusted external instructions (OWASP-AST05)                     #
# --------------------------------------------------------------------- #

def skill_manifest_external_instructions(ctx: RuleContext) -> List[RuleHit]:
    m = (ctx.artifact_model or {}).get("manifest")
    if m is None:
        return []
    manifest_file = _skill_md(ctx)
    assert manifest_file is not None
    hits: List[RuleHit] = []
    for url in m.get("external_instruction_urls", []):
        loc = _reference_locations(ctx, url)
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id=loc.fileId, artifact_path=loc.artifactPath,
            file_digest=loc.fileDigest,
            byte_range=(loc.sourceByteRange["start"], loc.sourceByteRange["end"]),
            raw_bytes=url.encode("utf-8"),
            producer=_prod(ctx),
        )
        hits.append(RuleHit(evidences=[ev], subject={
            "artifactPath": manifest_file.normalizedPath,
            "externalInstructionUrl": url,
        }))
    return hits


# --------------------------------------------------------------------- #
# S10. Declared script does not exist / suffix mismatch                 #
# --------------------------------------------------------------------- #

# (moved earlier in the file, see _SCRIPT_SUFFIXES near the top)


def skill_manifest_script_suffix_mismatch(ctx: RuleContext) -> List[RuleHit]:
    m = (ctx.artifact_model or {}).get("manifest")
    if m is None:
        return []
    manifest_file = _skill_md(ctx)
    assert manifest_file is not None
    base_dir = manifest_file.normalizedPath.rsplit("/", 1)[0]
    if base_dir == manifest_file.normalizedPath:
        base_dir = ""
    files_by_path = {f.normalizedPath: f for f in ctx.snapshot.files}
    hits: List[RuleHit] = []
    for ref in m.get("refs", []):
        issue, _ = _normalize_ref(ref)
        if issue:
            continue
        parts = ref.rsplit(".", 1)
        if len(parts) != 2:
            continue
        declared_suffix = "." + parts[1].lower()
        if declared_suffix not in _SCRIPT_SUFFIXES:
            continue
        target = ref if base_dir == "" else f"{base_dir}/{ref}"
        if target in files_by_path:
            continue
        # look for a sibling with the same stem but different suffix
        stem = parts[0]
        for other_ext in _SCRIPT_SUFFIXES:
            if other_ext == declared_suffix:
                continue
            candidate = f"{stem}{other_ext}" if base_dir == "" else f"{base_dir}/{stem}{other_ext}"
            if candidate in files_by_path:
                loc = _reference_locations(ctx, ref)
                ev = make_source_span_evidence(
                    snapshot_id=ctx.snapshot.snapshotId,
                    file_id=loc.fileId, artifact_path=loc.artifactPath,
                    file_digest=loc.fileDigest,
                    byte_range=(loc.sourceByteRange["start"], loc.sourceByteRange["end"]),
                    raw_bytes=ref.encode("utf-8"),
                    producer=_prod(ctx),
                )
                hits.append(RuleHit(evidences=[ev], subject={
                    "artifactPath": manifest_file.normalizedPath,
                    "referencePath": ref,
                    "declaredPath": ref,
                    "foundPath": candidate,
                    "referenceIssue": "suffix_mismatch",
                }))
                break
    return hits


# --------------------------------------------------------------------- #
# S11. Python AST: subprocess.*(..., shell=True)                        #
# --------------------------------------------------------------------- #

def _iter_py_files(ctx: RuleContext):
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        if not f.normalizedPath.lower().endswith(".py"):
            continue
        yield f


def skill_python_subprocess_shell_true(ctx: RuleContext) -> List[RuleHit]:
    """Detect any call `subprocess.<name>(...)` with keyword arg
    ``shell=True`` — a direct, mechanically-provable dangerous pattern.

    A syntax error is not a Finding: it just means the Python analyzer
    could not analyze the file. We surface that fact via a diagnostic on
    the ExecutionRecord (the engine already records rule completion).

    De-duplication with Bandit (spec §6 supersedes): if the Bandit
    analyzer ran successfully and produced a B602 result for the same
    (file, line), we suppress the hand-written Finding here. This avoids
    double-reporting the same code position; the Bandit built-in rule
    ``skill.bandit.B602`` supersedes ``skill.python_subprocess_shell_true``.
    """
    b602_positions = set()
    br = (ctx.artifact_model or {}).get("banditRun")
    if br and br.get("status") == "completed":
        path_map = br.get("pathMap") or {}
        for r in (br.get("results") or []):
            if r.get("test_id") != "B602":
                continue
            fid = path_map.get(r.get("filename"))
            if fid:
                b602_positions.add((fid, int(r.get("line_number") or 0)))

    hits: List[RuleHit] = []
    for f in _iter_py_files(ctx):
        source = ctx.file_bytes.get(f.fileId, b"")
        try:
            tree = ast.parse(source, filename=f.normalizedPath)
        except SyntaxError:
            continue  # per docstring; not a Finding
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "subprocess"):
                continue
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                    # byte range using ast offsets (0-based line, col_offset).
                    # ast.get_source_segment can give exact text.
                    segment = ast.get_source_segment(source.decode("utf-8", errors="replace"), node) or ""
                    # translate line/col to byte offset
                    lines = source.splitlines(keepends=True)
                    start = sum(len(l) for l in lines[:node.lineno - 1]) + node.col_offset
                    end_line = node.end_lineno or node.lineno
                    end_col = node.end_col_offset or (node.col_offset + len(segment))
                    end = sum(len(l) for l in lines[:end_line - 1]) + end_col
                    if (f.fileId, node.lineno) in b602_positions:
                        break  # already reported by Bandit B602
                    ev = make_source_span_evidence(
                        snapshot_id=ctx.snapshot.snapshotId,
                        file_id=f.fileId, artifact_path=f.normalizedPath,
                        file_digest=f.contentDigest or "",
                        byte_range=(start, end),
                        raw_bytes=source[start:end],
                        producer=_prod(ctx),
                    )
                    hits.append(RuleHit(evidences=[ev], subject={
                        "artifactPath": f.normalizedPath,
                        "callee": f"subprocess.{func.attr}",
                    }))
                    break
    return hits


# --------------------------------------------------------------------- #
# S12. SKILL.md body declares upstream dependencies as prose only        #
# --------------------------------------------------------------------- #

# Chinese and English patterns indicating the Skill requires prior outputs
# from another Skill or upstream step.
_UPSTREAM_DEPENDENCY_PATTERNS = re.compile(
    r"(?:"
    # Chinese patterns
    r"仅在.{0,40}(?:已产出|完成|生成|产出)"
    r"|必须.{0,30}(?:先|已).{0,20}(?:产出|完成|生成)"
    r"|只有.{0,40}(?:产出|完成|生成).*才"
    r"|先使用.{0,40}(?:Skill|skill|技能)"
    r"|需要.{0,30}已有.{0,30}(?:企划|大纲|描述|资产|结果|输出)"
    r"|(?:outline-generator|asset-designer|分集|大纲生成).{0,20}已产出"
    r")",
    re.DOTALL,
)

# Keywords suggesting there's an upsteam Skill by name
_UPSTREAM_SKILL_NAME = re.compile(
    r"(?:outline-generator|asset-designer|asset-image-generator"
    r"|episode-generator|plan-storyboard|nextplay-state-protocol"
    r"|asset-voice-match|outline_generator|asset_designer)",
    re.IGNORECASE,
)


def skill_upstream_dependency_contract_gap(ctx: RuleContext) -> List[RuleHit]:
    """The SKILL.md body declares that this Skill requires upstream outputs
    from another Skill (detected via natural-language patterns), but the
    manifest's ``dependencies`` and ``metadata`` fields are both empty.

    When upstream dependencies are expressed only in body prose there is no
    machine-checkable contract — CI, routing logic, or an operator calling
    this Skill cannot programmatically verify prerequisites are met.

    Added in Round 70 after black-box testing showed that two NexPlay Skills
    (asset-voice-match, plan-storyboard-and-generate-video-biz) would
    generate content when called without valid upstream inputs, despite their
    SKILL.md bodies explicitly requiring prior Skill outputs.
    """
    f = _skill_md(ctx)
    if f is None:
        return []
    m = (ctx.artifact_model or {}).get("manifest") or {}

    # Only flag when the manifest has no dependencies/metadata that could
    # encode the upstream contract — if they're filled in, the caller at
    # least has a chance to check.
    has_deps = bool(m.get("dependencies"))
    has_meta = bool(m.get("metadata"))
    if has_deps or has_meta:
        return []

    raw = _skill_md_bytes(ctx)
    # Skip frontmatter — only scan the body portion
    body_start = 0
    if b"\n---\n" in raw:
        # Find the closing '---' of the frontmatter
        close = raw.find(b"\n---\n", 4)
        if close != -1:
            body_start = close + 5

    body_bytes = raw[body_start:]
    body_text = body_bytes.decode("utf-8", errors="replace")

    if not _UPSTREAM_DEPENDENCY_PATTERNS.search(body_text):
        return []

    # Find the first matching byte range in the body for the evidence span
    m_span = _UPSTREAM_DEPENDENCY_PATTERNS.search(body_text)
    if m_span is None:
        return []

    start = body_start + m_span.start()
    end = body_start + m_span.end()

    ev = make_source_span_evidence(
        snapshot_id=ctx.snapshot.snapshotId,
        file_id=f.fileId, artifact_path=f.normalizedPath,
        file_digest=f.contentDigest or "",
        byte_range=(start, min(end, start + 300)),
        raw_bytes=raw[start:min(end, start + 300)],
        producer=_prod(ctx),
    )
    return [RuleHit(evidences=[ev], subject={
        "artifactPath": f.normalizedPath,
        "dependencyKind": "upstream_skill_output",
    })]


# --------------------------------------------------------------------- #
# S13. No machine-checkable scope constraints in manifest                #
# --------------------------------------------------------------------- #

_SCOPE_RESTRICTION_PATTERNS = re.compile(
    r"(?:"
    # Chinese: "不要用于", "不得用于", "禁止用于", "不可用于"
    r"(?:不要|不得|禁止|不可)用于.{1,200}"
    r"|以下.{0,20}(?:场景|情况).{0,20}(?:不|禁止|不得)"
    r")",
    re.DOTALL,
)


def skill_scope_restrictions_prose_only(ctx: RuleContext) -> List[RuleHit]:
    """The SKILL.md body declares scope restrictions ('do not use for X')
    as prose only, with no corresponding entries in ``permissions``,
    ``allowed-tools``, or ``compatibility`` that an automated caller could
    enforce.

    This is a low-severity advisory finding: prose restrictions are valid
    design documentation, but they cannot be checked by routing logic or
    CI — they rely solely on the model following natural-language
    instructions. The finding surfaces the gap rather than claiming a
    defect.
    """
    f = _skill_md(ctx)
    if f is None:
        return []
    m = (ctx.artifact_model or {}).get("manifest") or {}

    # If the manifest already has allowed-tools or permissions that express
    # scope, the restriction has a machine-checkable component — don't flag.
    has_scope_fields = bool(m.get("allowed-tools") or m.get("permissions"))
    if has_scope_fields:
        return []

    raw = _skill_md_bytes(ctx)
    body_start = 0
    if b"\n---\n" in raw:
        close = raw.find(b"\n---\n", 4)
        if close != -1:
            body_start = close + 5

    body_text = raw[body_start:].decode("utf-8", errors="replace")

    restrictions = list(_SCOPE_RESTRICTION_PATTERNS.finditer(body_text))
    if not restrictions:
        return []

    # Report on the first match only (don't flood the report)
    first = restrictions[0]
    start = body_start + first.start()
    end = body_start + first.end()

    ev = make_source_span_evidence(
        snapshot_id=ctx.snapshot.snapshotId,
        file_id=f.fileId, artifact_path=f.normalizedPath,
        file_digest=f.contentDigest or "",
        byte_range=(start, min(end, start + 300)),
        raw_bytes=raw[start:min(end, start + 300)],
        producer=_prod(ctx),
    )
    return [RuleHit(evidences=[ev], subject={
        "artifactPath": f.normalizedPath,
        "restrictionCount": len(restrictions),
    })]


# --------------------------------------------------------------------- #
# S14. SKILL.md body declares a no-fabrication mandate as prose only    #
# --------------------------------------------------------------------- #

# Chinese patterns forbidding the Skill from inventing / hallucinating
# data instead of using a real upstream result.
_NO_FABRICATION_PATTERNS = re.compile(
    r"(?:不得虚构|不得自行补|不得脑补|不可伪造|不得伪造)",
)


def skill_no_fabrication_declared(ctx: RuleContext) -> List[RuleHit]:
    """The SKILL.md body declares a no-fabrication mandate ('不得虚构' /
    '不得自行补' / '不得脑补' / '不可伪造') in prose, but the manifest has
    no corresponding ``permissions`` or ``metadata`` field that could make
    the prohibition machine-checkable.

    This is an advisory finding, not a defect: forbidding fabrication in
    prose is legitimate design intent. But nothing in the manifest lets a
    caller, CI, or router verify the model actually honored it — the
    contract lives entirely in natural language. Added in Round 72 after
    black-box analysis of 8 NexPlay production Skills showed the pattern
    present in asset-voice-match and cover-0729_biz with no corresponding
    Verity rule.
    """
    f = _skill_md(ctx)
    if f is None:
        return []
    m = (ctx.artifact_model or {}).get("manifest") or {}

    has_perms = bool(m.get("permissions"))
    has_meta = bool(m.get("metadata"))
    if has_perms or has_meta:
        return []

    raw = _skill_md_bytes(ctx)
    body_start = 0
    if b"\n---\n" in raw:
        close = raw.find(b"\n---\n", 4)
        if close != -1:
            body_start = close + 5

    body_text = raw[body_start:].decode("utf-8", errors="replace")

    m_span = _NO_FABRICATION_PATTERNS.search(body_text)
    if m_span is None:
        return []

    start = body_start + m_span.start()
    end = body_start + m_span.end()

    ev = make_source_span_evidence(
        snapshot_id=ctx.snapshot.snapshotId,
        file_id=f.fileId, artifact_path=f.normalizedPath,
        file_digest=f.contentDigest or "",
        byte_range=(start, min(end, start + 300)),
        raw_bytes=raw[start:min(end, start + 300)],
        producer=_prod(ctx),
    )
    return [RuleHit(evidences=[ev], subject={
        "artifactPath": f.normalizedPath,
        "mandateKind": "no_fabrication",
    })]


# --------------------------------------------------------------------- #
# S15. SKILL.md body declares a strict single-JSON output contract as   #
#      prose only, with no schema reference in the manifest             #
# --------------------------------------------------------------------- #

# Chinese patterns declaring output must be a single, pure JSON object
# with no surrounding prose / markdown / internal analysis.
_STRICT_OUTPUT_CONTRACT_PATTERNS = re.compile(
    r"(?:"
    r"只返回一个完整.{0,10}JSON"
    r"|不添加.{0,20}说明"
    r"|不输出内部"
    r")",
)

# File-extension / name hints suggesting a manifest ref points at a
# schema definition for the output contract.
_SCHEMA_REF_HINT = re.compile(r"schema|contract", re.IGNORECASE)


def skill_strict_output_contract_prose_only(ctx: RuleContext) -> List[RuleHit]:
    """The SKILL.md body declares a strict single-JSON-object output
    contract ('只返回一个完整JSON', '不添加...说明', '不输出内部分析') in
    prose, but the manifest has no ``refs`` entry pointing at a schema
    file and no schema-related ``metadata``.

    This is an informational finding, not a defect: the contract itself
    may be exactly right. It surfaces that Verity has no machine-checkable
    schema to validate actual output against — the guarantee rests
    entirely on the model following the prose instruction. Added in
    Round 72; present in 5 of 8 sampled NexPlay Skills.
    """
    f = _skill_md(ctx)
    if f is None:
        return []
    m = (ctx.artifact_model or {}).get("manifest") or {}

    refs = m.get("refs") or []
    has_schema_ref = any(_SCHEMA_REF_HINT.search(r) for r in refs if isinstance(r, str))
    has_schema_meta = False
    meta = m.get("metadata")
    if isinstance(meta, dict):
        has_schema_meta = any(_SCHEMA_REF_HINT.search(str(k)) or _SCHEMA_REF_HINT.search(str(v))
                              for k, v in meta.items())
    if has_schema_ref or has_schema_meta:
        return []

    raw = _skill_md_bytes(ctx)
    body_start = 0
    if b"\n---\n" in raw:
        close = raw.find(b"\n---\n", 4)
        if close != -1:
            body_start = close + 5

    body_text = raw[body_start:].decode("utf-8", errors="replace")

    m_span = _STRICT_OUTPUT_CONTRACT_PATTERNS.search(body_text)
    if m_span is None:
        return []

    start = body_start + m_span.start()
    end = body_start + m_span.end()

    ev = make_source_span_evidence(
        snapshot_id=ctx.snapshot.snapshotId,
        file_id=f.fileId, artifact_path=f.normalizedPath,
        file_digest=f.contentDigest or "",
        byte_range=(start, min(end, start + 300)),
        raw_bytes=raw[start:min(end, start + 300)],
        producer=_prod(ctx),
    )
    return [RuleHit(evidences=[ev], subject={
        "artifactPath": f.normalizedPath,
        "contractKind": "single_json_object",
    })]


# --------------------------------------------------------------------- #
# S16. SKILL.md body declares a Tool-unavailable fallback as prose      #
#      only, with no machine-readable declaration in the manifest       #
# --------------------------------------------------------------------- #

# Chinese patterns declaring what happens when an injected Tool is
# unavailable at runtime.
_TOOL_UNAVAILABLE_PATTERNS = re.compile(
    r"(?:Tool.{0,10}不可用|工具不可用)",
)


def skill_tool_unavailable_contract_prose_only(ctx: RuleContext) -> List[RuleHit]:
    """The SKILL.md body declares how the Skill behaves when an injected
    Tool is unavailable ('Tool 不可用时返回X', '工具不可用') in prose, but
    there is no machine-readable fallback declaration in the manifest
    (``metadata`` is empty).

    This is an advisory finding: the fallback behavior itself may be
    entirely correct. Verity flags that the contract cannot be
    mechanically verified — a caller has no way to confirm the declared
    fallback (e.g. a specific error code) is actually what gets returned
    without running a black-box probe. Added in Round 72; present in 3 of
    8 sampled NexPlay Skills.
    """
    f = _skill_md(ctx)
    if f is None:
        return []
    m = (ctx.artifact_model or {}).get("manifest") or {}

    has_meta = bool(m.get("metadata"))
    if has_meta:
        return []

    raw = _skill_md_bytes(ctx)
    body_start = 0
    if b"\n---\n" in raw:
        close = raw.find(b"\n---\n", 4)
        if close != -1:
            body_start = close + 5

    body_text = raw[body_start:].decode("utf-8", errors="replace")

    m_span = _TOOL_UNAVAILABLE_PATTERNS.search(body_text)
    if m_span is None:
        return []

    start = body_start + m_span.start()
    end = body_start + m_span.end()

    ev = make_source_span_evidence(
        snapshot_id=ctx.snapshot.snapshotId,
        file_id=f.fileId, artifact_path=f.normalizedPath,
        file_digest=f.contentDigest or "",
        byte_range=(start, min(end, start + 300)),
        raw_bytes=raw[start:min(end, start + 300)],
        producer=_prod(ctx),
    )
    return [RuleHit(evidences=[ev], subject={
        "artifactPath": f.normalizedPath,
        "fallbackKind": "tool_unavailable",
    })]


# --------------------------------------------------------------------- #
# S17–S19. Cross-file rules: SKILL.md body vs reference files            #
# These rules read MULTIPLE files from the snapshot, not just SKILL.md. #
# All files are already in ctx.file_bytes from intake_directory.        #
# --------------------------------------------------------------------- #

_INLINE_REF_PATTERN = re.compile(
    rb"references/([^\s\)`'\"]{2,80})"
)


def _get_references_file(ctx: RuleContext, filename: str):
    """Return (ArtifactFile, bytes) for a references/* file, or (None, b'')."""
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        if f.normalizedPath == f"references/{filename}":
            return f, ctx.file_bytes.get(f.fileId, b"")
    return None, b""


def _get_skill_body_bytes(ctx: RuleContext) -> bytes:
    """Return only the body portion (after frontmatter) of SKILL.md."""
    raw = _skill_md_bytes(ctx)
    if b"\n---\n" in raw[4:]:
        close = raw.find(b"\n---\n", 4)
        if close != -1:
            return raw[close + 5:]
    return raw


# --------------------------------------------------------------------- #
# S17. References inline-linked in SKILL.md body but absent from snapshot #
# --------------------------------------------------------------------- #

def skill_missing_inline_reference(ctx: RuleContext) -> List[RuleHit]:
    """SKILL.md body contains a `references/foo.md` link to a file that
    does not exist in the Skill package.

    Unlike ``skill_manifest_missing_reference`` (which only checks the
    YAML ``refs:`` field), this rule reads the prose body and catches
    inline links like ``[load X](references/foo.md)`` or the plain text
    form ``references/foo.md`` that are common in NexPlay-style Skills.

    A missing reference file means the Skill's execution flow will fail
    when it tries to load the declared resource at runtime.
    """
    f = _skill_md(ctx)
    if f is None:
        return []

    raw = _skill_md_bytes(ctx)
    body_start = 0
    if b"\n---\n" in raw[4:]:
        close = raw.find(b"\n---\n", 4)
        if close != -1:
            body_start = close + 5

    body = raw[body_start:]
    snapshot_paths = {ff.normalizedPath for ff in ctx.snapshot.files
                      if ff.status == "included"}

    hits: List[RuleHit] = []
    seen_missing = set()
    for m in _INLINE_REF_PATTERN.finditer(body):
        ref_path = m.group(1).decode("utf-8", errors="replace").strip("`'\"")
        full_path = f"references/{ref_path}"
        if full_path in snapshot_paths:
            continue
        if ref_path in seen_missing:
            continue
        seen_missing.add(ref_path)
        abs_start = body_start + m.start()
        abs_end = body_start + m.end()
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id=f.fileId, artifact_path=f.normalizedPath,
            file_digest=f.contentDigest or "",
            byte_range=(abs_start, min(abs_end, abs_start + 200)),
            raw_bytes=raw[abs_start:min(abs_end, abs_start + 200)],
            producer=_prod(ctx),
        )
        hits.append(RuleHit(evidences=[ev], subject={
            "artifactPath": f.normalizedPath,
            "missingReference": ref_path,
        }))
    return hits


# --------------------------------------------------------------------- #
# S18. Business-interface contract version mismatch                      #
# --------------------------------------------------------------------- #

_CONTRACT_VERSION_IN_BIZ = re.compile(
    r"(?:business-interface|合同版本|contract.{0,10}version)[^`\n]{0,60}"
    r"(v\d+(?:\.\d+)*)",
    re.IGNORECASE,
)
_CONTRACT_VERSION_IN_SKILL = re.compile(
    r"(?:business-interface|合同版本|contract.{0,10}version)[^`\n]{0,60}"
    r"(v\d+(?:\.\d+)*)",
    re.IGNORECASE,
)


def skill_business_interface_version_gap(ctx: RuleContext) -> List[RuleHit]:
    """SKILL.md body references business-interface.md but does not mention
    the same contract version string declared in that file.

    When ``business-interface.md`` declares a versioned contract (e.g.
    ``asset-voice-match.business-interface.v1``) and the SKILL.md body
    references the file without naming that version, callers and tools
    have no machine-checkable way to verify which contract version they
    are using. A version bump in the interface file would be invisible.
    """
    f = _skill_md(ctx)
    if f is None:
        return []

    biz_f, biz_bytes = _get_references_file(ctx, "business-interface.md")
    if not biz_f or not biz_bytes:
        return []

    skill_body = _get_skill_body_bytes(ctx)
    if b"business-interface" not in skill_body:
        return []

    biz_text = biz_bytes.decode("utf-8", errors="replace")
    skill_text = skill_body.decode("utf-8", errors="replace")

    biz_versions = {m.group(1) for m in _CONTRACT_VERSION_IN_BIZ.finditer(biz_text)}
    if not biz_versions:
        return []

    skill_versions = {m.group(1) for m in _CONTRACT_VERSION_IN_SKILL.finditer(skill_text)}

    missing_from_skill = biz_versions - skill_versions
    if not missing_from_skill:
        return []

    raw = _skill_md_bytes(ctx)
    m_ref = re.search(rb"business-interface", raw)
    start = m_ref.start() if m_ref else 0
    ev = make_source_span_evidence(
        snapshot_id=ctx.snapshot.snapshotId,
        file_id=f.fileId, artifact_path=f.normalizedPath,
        file_digest=f.contentDigest or "",
        byte_range=(start, min(start + 200, len(raw))),
        raw_bytes=raw[start:min(start + 200, len(raw))],
        producer=_prod(ctx),
    )
    return [RuleHit(evidences=[ev], subject={
        "artifactPath": f.normalizedPath,
        "contractVersionsInInterface": sorted(biz_versions),
        "contractVersionsMentionedInSkill": sorted(skill_versions),
    })]


# --------------------------------------------------------------------- #
# S19. Runtime state file present but malformed or empty                 #
# --------------------------------------------------------------------- #

def skill_runtime_state_file_malformed(ctx: RuleContext) -> List[RuleHit]:
    """``current.json`` or ``history.jsonl`` is present but cannot be
    parsed as valid JSON/JSONL, or is completely empty.

    These files record the Skill's live operational state. A malformed
    state file causes the Skill to fail immediately at startup when it
    tries to load its context, producing confusing errors rather than
    a clear input-error response.
    """
    import json as _json

    STATE_FILES = {"current.json", "history.jsonl"}
    hits: List[RuleHit] = []

    for ff in ctx.snapshot.files:
        if ff.status != "included":
            continue
        if ff.normalizedPath not in STATE_FILES:
            continue
        data = ctx.file_bytes.get(ff.fileId, b"")
        if not data or not data.strip():
            # Empty file
            ev = make_source_span_evidence(
                snapshot_id=ctx.snapshot.snapshotId,
                file_id=ff.fileId, artifact_path=ff.normalizedPath,
                file_digest=ff.contentDigest or "",
                byte_range=(0, len(data)),
                raw_bytes=data,
                producer=_prod(ctx),
            )
            hits.append(RuleHit(evidences=[ev], subject={
                "artifactPath": ff.normalizedPath,
                "stateFileIssue": "empty",
            }))
            continue

        if ff.normalizedPath.endswith(".jsonl"):
            lines = [l.strip() for l in data.decode("utf-8", errors="replace").splitlines()
                     if l.strip()]
            bad = 0
            for line in lines[:50]:
                try:
                    _json.loads(line)
                except Exception:
                    bad += 1
            if bad > 0 and bad > len(lines) // 2:
                ev = make_source_span_evidence(
                    snapshot_id=ctx.snapshot.snapshotId,
                    file_id=ff.fileId, artifact_path=ff.normalizedPath,
                    file_digest=ff.contentDigest or "",
                    byte_range=(0, min(200, len(data))),
                    raw_bytes=data[:200],
                    producer=_prod(ctx),
                )
                hits.append(RuleHit(evidences=[ev], subject={
                    "artifactPath": ff.normalizedPath,
                    "stateFileIssue": "malformed_jsonl",
                    "badLineCount": bad,
                }))
        elif ff.normalizedPath.endswith(".json"):
            try:
                _json.loads(data.decode("utf-8", errors="replace"))
            except Exception:
                ev = make_source_span_evidence(
                    snapshot_id=ctx.snapshot.snapshotId,
                    file_id=ff.fileId, artifact_path=ff.normalizedPath,
                    file_digest=ff.contentDigest or "",
                    byte_range=(0, min(200, len(data))),
                    raw_bytes=data[:200],
                    producer=_prod(ctx),
                )
                hits.append(RuleHit(evidences=[ev], subject={
                    "artifactPath": ff.normalizedPath,
                    "stateFileIssue": "malformed_json",
                }))
    return hits
