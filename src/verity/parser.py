"""Skill Manifest / SKILL.md parser.

Design constraints (spec §3, §17):

- READ-ONLY. We never execute Markdown code blocks or Python scripts.
- NO arbitrary object deserialization. YAML is parsed with the strict
  ``SafeLoader`` (never ``FullLoader`` / ``Loader``).
- Resource budgets are enforced BEFORE calling the YAML parser so that
  alias-bomb / billion-laughs / deeply-nested-node inputs fail fast with
  a specific reason code rather than exhausting memory or CPU.
- Failures are recorded as ``ParserRun.status`` (§3) and, at review-time,
  cause dependent rules to become ``blocked_by_upstream_failure`` — not
  ``not_applicable`` and never silently absent.

The parser emits a compact ``ArtifactModel``:

    {
      "hasSkillMd": bool,
      "manifestFile": {fileId, normalizedPath} | None,
      "manifest": {name, description, version, refs, deps, permissions,
                   external_reference_count, external_instruction_urls, tools} | None,
      "manifestRaw": {...}   # raw safe-loaded YAML mapping (for rules)
    }

Rules access ``ctx.artifact_model`` (attached by the engine).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# --- Resource budgets ---------------------------------------------------

MAX_MANIFEST_BYTES = 32 * 1024      # 32 KiB is generous for a frontmatter block
MAX_MANIFEST_LINES = 2000
MAX_YAML_DEPTH = 12                 # rough nesting cap
MAX_YAML_ANCHOR_TOKENS = 32         # &anchor / *alias appearances (alias-bomb guard)
MAX_YAML_MAPPING_KEYS = 500         # total number of keys across the tree
AGENT_SKILLS_SPEC_ID = "agentskills.io/specification"
AGENT_SKILLS_SPEC_SNAPSHOT = "retrieved-2026-07-21"


@dataclass
class ParserDiagnostic:
    code: str
    message: str
    line: Optional[int] = None
    column: Optional[int] = None


@dataclass
class ParserRun:
    """Simplified per spec §3 — enough for Phase 1 walking Skill audit."""
    parserRunId: str
    snapshotId: str
    parserId: str = "verity.skill.manifest.v1"
    parserVersion: str = "1.0.0"
    grammarOrDialectVersion: str = "yaml-1.1-safe"
    inputFileIds: List[str] = field(default_factory=list)
    outputModelType: str = "verity.skill.ArtifactModel"
    outputSchemaVersion: str = "1.0.0"
    status: str = "completed"       # completed|partial|failed|unsupported
    diagnostics: List[ParserDiagnostic] = field(default_factory=list)
    usedFallback: bool = False


# --- Frontmatter extraction ---------------------------------------------

_FM_START = re.compile(rb"\A---[ \t]*\r?\n")


def _split_frontmatter(data: bytes) -> Tuple[Optional[bytes], Optional[bytes], List[ParserDiagnostic]]:
    """Extract YAML frontmatter and Markdown body.

    Returns (yaml_bytes | None, body_bytes, diagnostics). If no frontmatter
    header is present, returns (None, whole_body, []) — that is a legal
    document, not an error.
    """
    diags: List[ParserDiagnostic] = []
    if not _FM_START.match(data):
        return None, data, diags
    # find closing '---' on its own line
    rest = data[data.index(b"\n") + 1:]
    m = re.search(rb"(?m)^---[ \t]*\r?\n?", rest)
    if not m:
        diags.append(ParserDiagnostic(
            "frontmatter_not_closed",
            "YAML frontmatter opened with '---' but no matching closing '---' was found",
        ))
        # Return a marker (b"") for yaml_bytes so the caller can distinguish
        # "unclosed frontmatter" (untrustworthy) from "no frontmatter at all"
        # (partial-but-usable, empty manifest).
        return b"", rest, diags
    yaml_bytes = rest[:m.start()]
    body = rest[m.end():]
    return yaml_bytes, body, diags


# --- Pre-parse safety scan ----------------------------------------------

def _preflight_yaml(yaml_bytes: bytes) -> List[ParserDiagnostic]:
    """Cheap resource / alias-bomb guard BEFORE handing to PyYAML."""
    diags: List[ParserDiagnostic] = []
    if len(yaml_bytes) > MAX_MANIFEST_BYTES:
        diags.append(ParserDiagnostic(
            "frontmatter_over_budget",
            f"YAML frontmatter exceeds byte budget ({len(yaml_bytes)} > {MAX_MANIFEST_BYTES})",
        ))
    line_count = yaml_bytes.count(b"\n") + 1
    if line_count > MAX_MANIFEST_LINES:
        diags.append(ParserDiagnostic(
            "frontmatter_too_many_lines",
            f"YAML frontmatter exceeds line budget ({line_count} > {MAX_MANIFEST_LINES})",
        ))
    # Alias-bomb crude guard: count '&' anchors and '*' aliases at start of
    # a token position. PyYAML SafeLoader still processes aliases; if a
    # document tries to expand a large tree via aliases, we cap by count
    # of alias/anchor tokens.
    anchor_tokens = len(re.findall(rb"(?m)(?:^|[\s\[\{\,])[&\*][A-Za-z0-9_-]+", yaml_bytes))
    if anchor_tokens > MAX_YAML_ANCHOR_TOKENS:
        diags.append(ParserDiagnostic(
            "frontmatter_alias_bomb_suspected",
            f"YAML anchor/alias tokens exceed budget ({anchor_tokens} > {MAX_YAML_ANCHOR_TOKENS})",
        ))
    return diags


def _walk_depth_and_size(node: Any, depth: int = 0) -> Tuple[int, int]:
    """Return (max_depth, total_key_count) for a loaded YAML tree."""
    if isinstance(node, dict):
        max_d = depth
        total = len(node)
        for k, v in node.items():
            d, c = _walk_depth_and_size(v, depth + 1)
            if d > max_d:
                max_d = d
            total += c
        return max_d, total
    if isinstance(node, list):
        max_d = depth
        total = 0
        for v in node:
            d, c = _walk_depth_and_size(v, depth + 1)
            if d > max_d:
                max_d = d
            total += c
        return max_d, total
    return depth, 0


# --- Manifest normalization ---------------------------------------------

def _normalize_manifest(raw: Any) -> Dict[str, Any]:
    """Collect the minimum internal Manifest view from a raw safe-loaded
    YAML mapping. Missing fields simply stay absent; rules decide what
    that means.
    """
    m: Dict[str, Any] = {}
    if not isinstance(raw, dict):
        return m
    m["name"] = raw.get("name")
    m["description"] = raw.get("description")
    m["version"] = raw.get("version")
    m["compatibility"] = raw.get("compatibility")
    m["metadata"] = raw.get("metadata")
    m["allowed-tools"] = raw.get("allowed-tools")

    # references: unify multiple common field names into one list
    refs: List[str] = []
    for key in ("scripts", "files", "refs", "entrypoints"):
        v = raw.get(key)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str):
                    refs.append(item)
                elif isinstance(item, dict):
                    p = item.get("path") or item.get("file") or item.get("script")
                    if isinstance(p, str):
                        refs.append(p)
        elif isinstance(v, str):
            refs.append(v)
    m["refs"] = refs

    # dependencies with (name, version) pairs where possible
    deps: List[Dict[str, Any]] = []
    d = raw.get("dependencies")
    if isinstance(d, dict):
        for name, ver in d.items():
            deps.append({"name": str(name),
                         "version": None if ver is None else str(ver)})
    elif isinstance(d, list):
        for item in d:
            if isinstance(item, str):
                deps.append({"name": item, "version": None})
            elif isinstance(item, dict):
                name = item.get("name") or item.get("package")
                ver = item.get("version")
                if isinstance(name, str):
                    deps.append({"name": name,
                                 "version": None if ver is None else str(ver)})
    m["dependencies"] = deps

    # Official `allowed-tools` is a space-separated string. Historical
    # extension keys remain accepted as capability signals, but are not
    # treated as official schema fields.
    perms: List[str] = []
    official_tools = raw.get("allowed-tools")
    if isinstance(official_tools, str):
        perms.extend(official_tools.split())
    for key in ("permissions", "allowed_tools", "tools"):
        v = raw.get(key)
        if isinstance(v, list):
            perms.extend(str(x) for x in v if isinstance(x, (str, int, float, bool)))
        elif isinstance(v, str):
            perms.append(v)
    m["permissions"] = perms

    # External references are parsed separately from dangerous execution mode.
    # Semantic review needs only a neutral presence/count seed; deterministic
    # rules retain the URL value only for explicitly executable modes.
    ext = raw.get("external_instructions")
    ext_urls: List[str] = []
    ext_reference_count = 0
    if isinstance(ext, dict):
        src = ext.get("source") or ext.get("url")
        mode = ext.get("mode")
        if isinstance(src, str):
            ext_reference_count += 1
            if mode in ("fetch_and_follow", "runtime_fetch"):
                ext_urls.append(src)
    elif isinstance(ext, list):
        for e in ext:
            if isinstance(e, dict):
                src = e.get("source") or e.get("url")
                mode = e.get("mode")
                if isinstance(src, str):
                    ext_reference_count += 1
                    if mode in ("fetch_and_follow", "runtime_fetch"):
                        ext_urls.append(src)
    m["external_reference_count"] = ext_reference_count
    m["external_instruction_urls"] = ext_urls
    return m


# --- Entry point --------------------------------------------------------

def parse_skill(snapshot, file_bytes: Dict[str, bytes]) -> Tuple[Dict[str, Any], ParserRun]:
    """Run the safe Skill Manifest parser.

    Never raises. On any error the returned ``ParserRun.status`` becomes
    ``failed`` and ``ArtifactModel['manifest']`` is None.
    """
    import yaml  # imported here so the module loads without PyYAML for prompt-only use
    run = ParserRun(parserRunId=f"pr-{uuid.uuid4().hex[:12]}",
                    snapshotId=snapshot.snapshotId)
    model: Dict[str, Any] = {
        "hasSkillMd": False,
        "agentSkillsSpec": {
            "specId": AGENT_SKILLS_SPEC_ID,
            "snapshot": AGENT_SKILLS_SPEC_SNAPSHOT,
        },
        "manifestFile": None,
        "manifest": None,
        "manifestRaw": None,
        "manifestByteRange": None,
    }

    # The Agent Skills specification requires exactly one root SKILL.md.
    # Lowercase/case variants and nested files are not substitutes.
    skill_md = next((f for f in snapshot.files
                     if f.status == "included"
                     and f.normalizedPath == "SKILL.md"), None)
    if skill_md is None:
        run.status = "failed"
        run.diagnostics.append(ParserDiagnostic("skill_md_missing", "no SKILL.md found in artifact"))
        return model, run

    model["hasSkillMd"] = True
    model["manifestFile"] = {"fileId": skill_md.fileId,
                              "normalizedPath": skill_md.normalizedPath}
    run.inputFileIds = [skill_md.fileId]

    data = file_bytes.get(skill_md.fileId, b"")
    yaml_bytes, body, diags = _split_frontmatter(data)
    run.diagnostics.extend(diags)

    if yaml_bytes is None:
        # No frontmatter at all: not a parse error. Present the manifest
        # as an empty mapping so downstream rules can flag missing fields
        # on their own terms (spec §A5).
        run.status = "partial"
        model["manifest"] = _normalize_manifest({})
        model["manifestRaw"] = {}
        model["manifestByteRange"] = None
        return model, run
    if yaml_bytes == b"":
        # Frontmatter started but never closed. The bytes between the
        # opening `---` and end-of-file are UNTRUSTED because we cannot
        # tell where the intended frontmatter ends and body begins.
        # We refuse to parse and mark the parser as failed so that
        # downstream manifest-dependent rules become
        # ``blocked_by_upstream_failure`` (§9.2).
        run.status = "failed"
        return model, run

    # Record the frontmatter byte range for evidence location.
    fm_start = 4  # '---\n'
    fm_end = fm_start + len(yaml_bytes)
    model["manifestByteRange"] = (fm_start, fm_end)

    pre = _preflight_yaml(yaml_bytes)
    if pre:
        run.diagnostics.extend(pre)
        run.status = "failed"
        return model, run

    try:
        raw = yaml.safe_load(yaml_bytes.decode("utf-8", errors="replace"))
    except yaml.YAMLError as e:
        run.status = "failed"
        run.diagnostics.append(ParserDiagnostic(
            "yaml_parse_error", str(e).splitlines()[0] if str(e) else "yaml parse error",
        ))
        return model, run
    except Exception as e:  # pragma: no cover — safety net
        run.status = "failed"
        run.diagnostics.append(ParserDiagnostic(
            "yaml_unexpected_error", f"{type(e).__name__}: {e}",
        ))
        return model, run

    if raw is None:
        # Empty frontmatter (`---\n---\n`) — treat as empty mapping.
        raw = {}
    if not isinstance(raw, dict):
        run.status = "failed"
        run.diagnostics.append(ParserDiagnostic(
            "yaml_root_not_mapping",
            f"YAML frontmatter root must be a mapping, got {type(raw).__name__}",
        ))
        return model, run

    # Post-load structural budgets.
    depth, key_count = _walk_depth_and_size(raw)
    if depth > MAX_YAML_DEPTH:
        run.status = "failed"
        run.diagnostics.append(ParserDiagnostic(
            "yaml_too_deep", f"YAML depth {depth} exceeds cap {MAX_YAML_DEPTH}",
        ))
        return model, run
    if key_count > MAX_YAML_MAPPING_KEYS:
        run.status = "failed"
        run.diagnostics.append(ParserDiagnostic(
            "yaml_too_many_keys",
            f"YAML mapping key count {key_count} exceeds cap {MAX_YAML_MAPPING_KEYS}",
        ))
        return model, run

    model["manifestRaw"] = raw
    model["manifest"] = _normalize_manifest(raw)
    return model, run
