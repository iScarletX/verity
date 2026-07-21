"""Safe intake — text and local directory (V1).

Explicit non-goals for V1 (spec §13, §17):
- No ZIP extraction (would require full safe streaming decompressor).
- No GitHub URL fetch.
- No recursive expansion of nested archives.
- No symlink follow. Symlinks are recorded as metadata only.

Enforced:
- Path normalization / escape rejection.
- Per-file and total size budget.
- File count budget.
- symlink / special files: recorded, not followed, not read.
- TOCTOU: pre-stat + read-once + post-stat; mismatch marks raceDetected.
"""

from __future__ import annotations

import hashlib
import os
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import List, Optional

from .canonical import (
    content_root_digest,
    snapshot_manifest_digest,
    sha256_hex,
    domain_tag,
)
from .models import ArtifactFile, ArtifactSnapshot, PROMPT_KINDS


@dataclass(frozen=True)
class IntakeBudget:
    max_files: int = 500
    max_file_size: int = 512 * 1024      # 512 KiB per file
    max_total_size: int = 8 * 1024 * 1024  # 8 MiB total


class IntakeError(Exception):
    pass


_FORBIDDEN_PATH_SEGMENTS = {"", ".", ".."}


def _normalize_relative(root: Path, path: Path) -> str:
    """Normalize a path relative to root. Reject escapes, NUL, absolute,
    dotdot, backslash, duplicates handled by caller.
    """
    rel = path.relative_to(root)
    s = str(rel)
    if "\x00" in s:
        raise IntakeError(f"NUL in path: {s!r}")
    if "\\" in s:
        raise IntakeError(f"backslash in path (not allowed): {s!r}")
    parts = rel.parts
    for p in parts:
        if p in _FORBIDDEN_PATH_SEGMENTS:
            raise IntakeError(f"forbidden path segment in {s!r}")
        if p.startswith("/") or ":" in p:
            raise IntakeError(f"illegal char in path segment {p!r}")
    return "/".join(parts)


def _file_content_digest(data: bytes) -> str:
    return sha256_hex(domain_tag("file-content"), data)


# Intake-level rejections (§17: intake reject != Finding). Values must be
# distinguishable from Finding reason codes so downstream can tell them apart.
MAX_PROMPT_BYTES = 256 * 1024   # 256 KiB is a reasonable hard cap; anything
                                # bigger is almost certainly not an ordinary
                                # prompt and should be handled as a Skill or
                                # rejected outright at intake.


def intake_text(text: str, *, artifact_id: Optional[str] = None,
                virtual_filename: str = "prompt.txt",
                prompt_kind: str = "user_prompt") -> tuple[ArtifactSnapshot, dict[str, bytes]]:
    """Ingest plain text (a Prompt / System Prompt).

    ``prompt_kind`` must be one of the controlled enum values in
    ``models.PROMPT_KINDS``.  It is preserved on the Snapshot so that
    system-only rules do not run against ordinary user prompts.
    """
    if not isinstance(text, str):
        raise IntakeError("text must be str")
    if prompt_kind not in PROMPT_KINDS:
        raise IntakeError(f"unknown prompt_kind: {prompt_kind!r}; expected one of {PROMPT_KINDS}")
    data = text.encode("utf-8")
    if len(data) > MAX_PROMPT_BYTES:
        raise IntakeError(f"prompt text exceeds intake budget ({len(data)} > {MAX_PROMPT_BYTES} bytes)")
    if b"\x00" in data:
        raise IntakeError("prompt text contains NUL byte (rejected at intake)")
    digest = _file_content_digest(data)
    # Content-addressed fileId so identical content across independent runs
    # yields identical locations (spec §5.1 stable occurrenceFingerprint).
    file_id = f"f-{sha256_hex(domain_tag('file-id'), virtual_filename.encode('utf-8'), digest.encode('utf-8'))[:16]}"
    af = ArtifactFile(
        fileId=file_id,
        normalizedPath=virtual_filename,
        size=len(data),
        contentDigest=digest,
        status="included",
        entryType="file",
    )
    manifest_entries = [_manifest_entry(af)]
    smd = snapshot_manifest_digest(manifest_entries)
    crd = content_root_digest(manifest_entries)
    aid = artifact_id or f"a-{uuid.uuid4().hex[:12]}"
    snap = ArtifactSnapshot(
        artifactId=aid,
        snapshotId=f"s-{uuid.uuid4().hex[:12]}",
        snapshotManifestDigest=smd,
        contentRootDigest=crd,
        files=[af],
        promptKind=prompt_kind,  # type: ignore[arg-type]
    )
    return snap, {af.fileId: data}


def _manifest_entry(af: ArtifactFile) -> dict:
    return {
        "normalizedPath": af.normalizedPath,
        "entryType": af.entryType,
        "size": af.size,
        "executable": af.executable,
        "symlinkTarget": af.symlinkTarget or "",
        "status": af.status,
        "reasonCode": af.reasonCode or "",
        "contentDigest": af.contentDigest or "",
        "raceDetected": af.raceDetected,
    }


def intake_directory(root_path: str | os.PathLike, *,
                     budget: IntakeBudget | None = None,
                     artifact_id: Optional[str] = None,
                     artifact_root_name: Optional[str] = None
                     ) -> tuple[ArtifactSnapshot, dict[str, bytes]]:
    """Read a directory into an immutable Snapshot. Returns (snapshot, file_bytes_by_id).

    file_bytes_by_id is kept in-memory (bounded by budget) so that downstream
    read-only analyzers do not need to touch the user's original directory.
    """
    budget = budget or IntakeBudget()
    root = Path(root_path).resolve(strict=True)
    if not root.is_dir():
        raise IntakeError(f"not a directory: {root}")
    root_name = artifact_root_name if artifact_root_name is not None else root.name
    if (not isinstance(root_name, str) or not 1 <= len(root_name) <= 255
            or root_name in (".", "..") or "/" in root_name
            or "\\" in root_name or "\x00" in root_name
            or any(ord(c) < 32 for c in root_name)):
        raise IntakeError("invalid artifact root name")

    entries: List[ArtifactFile] = []
    byte_store: dict[str, bytes] = {}
    seen_paths: set[str] = set()
    total_bytes = 0

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # deterministic ordering
        dirnames.sort()
        filenames.sort()
        for name in filenames:
            if len(entries) >= budget.max_files:
                raise IntakeError(f"budget exceeded: >{budget.max_files} files")
            full = Path(dirpath) / name
            try:
                pre = os.lstat(full)
            except OSError as e:
                raise IntakeError(f"cannot stat {full}: {e}")
            mode = pre.st_mode
            try:
                normalized = _normalize_relative(root, full)
            except IntakeError as e:
                raise IntakeError(f"path rejected: {e}") from None
            if normalized in seen_paths:
                raise IntakeError(f"duplicate normalized path: {normalized}")
            lower = normalized.lower()
            if any(lower == s.lower() for s in seen_paths):
                raise IntakeError(f"case-insensitive path collision: {normalized}")
            seen_paths.add(normalized)

            # placeholder; for included files we recompute a content-addressed
            # id after reading (see below); for skipped/rejected entries we
            # derive it from the path alone.
            file_id = f"f-{sha256_hex(domain_tag('file-id'), normalized.encode('utf-8'))[:16]}"

            if stat.S_ISLNK(mode):
                target = os.readlink(full)
                entries.append(ArtifactFile(
                    fileId=file_id, normalizedPath=normalized, size=0,
                    contentDigest=None, status="skipped",
                    reasonCode="symlink_not_followed",
                    symlinkTarget=target, entryType="symlink",
                ))
                continue
            if not stat.S_ISREG(mode):
                entries.append(ArtifactFile(
                    fileId=file_id, normalizedPath=normalized, size=int(pre.st_size),
                    contentDigest=None, status="rejected",
                    reasonCode="special_file", entryType="special",
                ))
                continue

            size = int(pre.st_size)
            if size > budget.max_file_size:
                entries.append(ArtifactFile(
                    fileId=file_id, normalizedPath=normalized, size=size,
                    contentDigest=None, status="skipped",
                    reasonCode="file_too_large",
                ))
                continue
            if total_bytes + size > budget.max_total_size:
                raise IntakeError("budget exceeded: total bytes")

            # Read once, no follow.
            with open(full, "rb") as fh:
                # Sanity: opened path must not be a symlink race.
                st_after_open = os.fstat(fh.fileno())
                if st_after_open.st_ino != pre.st_ino:
                    entries.append(ArtifactFile(
                        fileId=file_id, normalizedPath=normalized, size=size,
                        contentDigest=None, status="rejected",
                        reasonCode="intake_inode_mismatch",
                    ))
                    continue
                data = fh.read(size + 1)
            race = False
            if len(data) != size:
                race = True
            try:
                post = os.lstat(full)
                if post.st_size != pre.st_size or int(post.st_mtime_ns) != int(pre.st_mtime_ns):
                    race = True
            except OSError:
                race = True
            digest = _file_content_digest(data)
            file_id = f"f-{sha256_hex(domain_tag('file-id'), normalized.encode('utf-8'), digest.encode('utf-8'))[:16]}"
            total_bytes += len(data)
            af = ArtifactFile(
                fileId=file_id, normalizedPath=normalized, size=len(data),
                contentDigest=digest, status="included",
                executable=bool(mode & 0o111), raceDetected=race,
            )
            entries.append(af)
            byte_store[file_id] = data

    manifest_entries = [_manifest_entry(e) for e in entries]
    smd = snapshot_manifest_digest(manifest_entries)
    crd = content_root_digest(manifest_entries)
    aid = artifact_id or f"a-{uuid.uuid4().hex[:12]}"
    snap = ArtifactSnapshot(
        artifactId=aid,
        snapshotId=f"s-{uuid.uuid4().hex[:12]}",
        snapshotManifestDigest=smd,
        contentRootDigest=crd,
        files=entries,
        artifactRootName=root_name,
    )
    return snap, byte_store
