#!/usr/bin/env python3
"""Install a pinned gitleaks binary into a target directory, verifying
SHA-256 against the release metadata in ``gitleaks_release.json``.

Layers of verification (see README):

1. **archive** SHA-256 against ``tools/gitleaks_release.json`` (this file
   is committed and is the pinned release descriptor).
2. **binary** SHA-256 recorded in an install-time manifest
   (``<target>/manifest.json``). Because the release descriptor lists the
   *tar.gz* hash, the binary hash cannot be predicted before extraction;
   we compute it once and re-verify it on every subsequent Verity run
   via the runner's ``verify_sha256`` gate.

Tar extraction is done manually (never ``TarFile.extractall``):

- Only the exact ``gitleaks`` entry is accepted.
- The entry must be a regular file and no larger than 200 MiB.
- No absolute paths, no ``..`` segments, no symlinks, no hardlinks.

This is a one-time developer utility. It performs network IO and MUST
NOT be invoked from Verity's runtime path or its test suite. The Verity
runtime treats a missing / mismatched gitleaks binary as an Analyzer
failure with Coverage insufficient.

Usage:
    python3 tools/install_gitleaks.py                 # installs into .tools/gitleaks/8.28.0/
    python3 tools/install_gitleaks.py --target /usr/local/bin
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


HERE = Path(__file__).parent
REPO = HERE.parent
DEFAULT_TARGET_DIR = REPO / ".tools" / "gitleaks"

MAX_ARCHIVE_BYTES = 40 * 1024 * 1024      # tar.gz > 40 MiB rejected up front
MAX_BINARY_BYTES = 200 * 1024 * 1024


def _pick_asset(release: dict) -> tuple[str, dict]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine in ("arm64", "aarch64"):
        key = "darwin_arm64"
    elif system == "darwin":
        key = "darwin_x64"
    elif system == "linux" and machine in ("arm64", "aarch64"):
        key = "linux_arm64"
    elif system == "linux":
        key = "linux_x64"
    else:
        raise SystemExit(f"unsupported platform: {system}/{machine}")
    if key not in release["assets"]:
        raise SystemExit(f"no pinned asset for platform {key}")
    return key, release["assets"][key]


def _safe_extract_gitleaks(archive_bytes: bytes) -> bytes:
    """Return the raw bytes of the ``gitleaks`` binary inside the tarball.

    Refuses anything other than the exact entry name ``gitleaks``,
    non-regular files, links, over-sized entries, or invalid names.
    """
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tf:
        # Do NOT use extractall / extract. Iterate manually.
        for member in tf.getmembers():
            if member.name != "gitleaks":
                continue
            if not member.isfile():
                raise SystemExit(f"unexpected non-file entry: type={member.type!r}")
            if member.issym() or member.islnk():
                raise SystemExit("symlink / hardlink entries are not allowed")
            if member.size > MAX_BINARY_BYTES:
                raise SystemExit(f"binary too large: {member.size} bytes")
            fh = tf.extractfile(member)
            if fh is None:
                raise SystemExit("tar member could not be opened")
            return fh.read()
    raise SystemExit("'gitleaks' binary not found inside the tarball")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", default=None,
                    help=("Install directory. Default: "
                          "<repo>/.tools/gitleaks/<version>/. Use an "
                          "absolute path to install elsewhere."))
    ap.add_argument("--release-json", default=str(HERE / "gitleaks_release.json"),
                    help="Pinned release descriptor path")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite an existing binary at the target path")
    args = ap.parse_args()

    release = json.loads(Path(args.release_json).read_text())
    key, asset = _pick_asset(release)
    if args.target:
        target_dir = Path(args.target)
    else:
        target_dir = DEFAULT_TARGET_DIR / release["version"]

    print(f"[install_gitleaks] platform={key}")
    print(f"[install_gitleaks] version={release['version']}")
    print(f"[install_gitleaks] url={asset['url']}")
    print(f"[install_gitleaks] expected archive sha256={asset['sha256']}")

    # Download to a temp file with a size cap.
    with tempfile.NamedTemporaryFile(prefix="verity-gitleaks-dl-", suffix=".tgz",
                                     delete=False) as fh:
        tmp_path = Path(fh.name)
    try:
        with urllib.request.urlopen(asset["url"], timeout=60) as resp:
            data = resp.read(MAX_ARCHIVE_BYTES + 1)
        if len(data) > MAX_ARCHIVE_BYTES:
            raise SystemExit(f"archive exceeds size cap ({len(data)} > {MAX_ARCHIVE_BYTES})")
        tmp_path.write_bytes(data)
        archive_sha = hashlib.sha256(data).hexdigest()
        if archive_sha != asset["sha256"]:
            raise SystemExit(f"archive sha256 mismatch: got {archive_sha}, "
                             f"expected {asset['sha256']}")
        print(f"[install_gitleaks] archive sha256 ok ({len(data)} bytes)")

        binary_bytes = _safe_extract_gitleaks(data)
        binary_sha = hashlib.sha256(binary_bytes).hexdigest()

        target_dir.mkdir(parents=True, exist_ok=True)
        bin_path = target_dir / "gitleaks"
        if bin_path.exists() and not args.force:
            existing = hashlib.sha256(bin_path.read_bytes()).hexdigest()
            if existing != binary_sha:
                raise SystemExit(f"{bin_path} exists with a different SHA-256. "
                                 f"Use --force to overwrite.")
        bin_path.write_bytes(binary_bytes)
        bin_path.chmod(0o755)

        manifest = {
            "tool": "gitleaks",
            "version": release["version"],
            "platform": key,
            "archiveUrl": asset["url"],
            "archiveSha256": archive_sha,
            "binarySha256": binary_sha,
            "installedPath": str(bin_path.resolve()),
        }
        manifest_path = target_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))

        print(f"[install_gitleaks] installed {bin_path}")
        print(f"[install_gitleaks] binary sha256={binary_sha}")
        print(f"[install_gitleaks] manifest written to {manifest_path}")
        print("[install_gitleaks] verify:")
        print(f"                     {bin_path} version")
        return 0
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


if __name__ == "__main__":
    sys.exit(main())
