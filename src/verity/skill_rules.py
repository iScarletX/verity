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

# Minimal name syntax: 1..64 chars of [A-Za-z0-9._-], not starting/ending
# with a separator. Anything else is "unclear", but we don't try to be
# clever — just flag missing/blank/obviously-invalid.
_NAME_OK = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._\- ]{0,62}[A-Za-z0-9]\Z")


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
    if not isinstance(name, str) or not name.strip():
        return [_mkfield_hit(ctx, "name", "blank")]
    if not _NAME_OK.match(name):
        return [_mkfield_hit(ctx, "name", "invalid_syntax")]
    return []


def skill_manifest_description_missing(ctx: RuleContext) -> List[RuleHit]:
    m = (ctx.artifact_model or {}).get("manifest")
    if m is None:
        return []
    desc = m.get("description")
    if desc is None:
        return [_mkfield_hit(ctx, "description", "missing")]
    if not isinstance(desc, str) or not desc.strip():
        return [_mkfield_hit(ctx, "description", "blank")]
    return []


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
    """
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
