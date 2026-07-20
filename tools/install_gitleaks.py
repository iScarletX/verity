#!/usr/bin/env python3
"""Install a pinned gitleaks binary into a target directory, verifying
SHA-256 against the release metadata in ``gitleaks_release.json``.

This is a one-time developer utility. It performs network IO and MUST
NOT be invoked from Verity's runtime path or its test suite. The Verity
runtime treats a missing / mismatched gitleaks binary as an Analyzer
failure with Coverage insufficient (see ``verity/gitleaks_runner.py``).

Usage:
    python3 tools/install_gitleaks.py --target ~/.local/bin
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
import urllib.request
from pathlib import Path


HERE = Path(__file__).parent


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", default=str(Path.home() / ".local" / "bin"),
                    help="Install directory (default ~/.local/bin)")
    ap.add_argument("--release-json", default=str(HERE / "gitleaks_release.json"),
                    help="Pinned release descriptor path")
    args = ap.parse_args()

    release = json.loads(Path(args.release_json).read_text())
    key, asset = _pick_asset(release)
    print(f"[install_gitleaks] platform={key}")
    print(f"[install_gitleaks] url={asset['url']}")
    print(f"[install_gitleaks] expected sha256={asset['sha256']}")

    with urllib.request.urlopen(asset["url"], timeout=60) as resp:
        data = resp.read()
    got = hashlib.sha256(data).hexdigest()
    if got != asset["sha256"]:
        raise SystemExit(f"sha256 mismatch: got {got}, expected {asset['sha256']}")
    print(f"[install_gitleaks] sha256 ok ({len(data)} bytes)")

    target_dir = Path(args.target)
    target_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        for member in tf.getmembers():
            if member.name == "gitleaks" and member.isfile():
                extracted = tf.extractfile(member).read()
                bin_path = target_dir / "gitleaks"
                bin_path.write_bytes(extracted)
                bin_path.chmod(0o755)
                print(f"[install_gitleaks] installed {bin_path}")
                print(f"[install_gitleaks] verify: {bin_path} version")
                return 0
    raise SystemExit("gitleaks binary not found inside the tarball")


if __name__ == "__main__":
    sys.exit(main())
