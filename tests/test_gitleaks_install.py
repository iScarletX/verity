"""Tests for the project-local gitleaks install machinery.

These tests do NOT re-download gitleaks; they only inspect the release
descriptor and the runner's discovery/verification path. When a
project-local install is present (``.tools/gitleaks/<version>/``) we
additionally verify the two-layer SHA policy on the running binary.

All tests are offline. See ``tools/install_gitleaks.py`` for the one-time
download step (developer-only, not part of pytest).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from verity.gitleaks_runner import (
    LOCAL_INSTALL_ROOT, RELEASE_JSON, REQUIRED_GITLEAKS_VERSION,
    GitleaksRunner, _expected_binary_sha_for, _local_install,
    _load_pinned_release,
)


REPO = Path(__file__).parent.parent


# ---------------------------------------------------------------------- #
# Release descriptor sanity                                              #
# ---------------------------------------------------------------------- #

class TestReleaseDescriptor:
    def test_release_json_is_committed_and_pinned(self):
        release = _load_pinned_release()
        assert release is not None
        assert release["tool"] == "gitleaks"
        assert release["license"] == "MIT"
        assert release["version"] == REQUIRED_GITLEAKS_VERSION == "8.28.0"
        # every asset entry must have a 64-hex SHA-256.
        for key, asset in release["assets"].items():
            assert asset["sha256"].startswith(("0", "1", "2", "3", "4", "5",
                                                "6", "7", "8", "9", "a", "b",
                                                "c", "d", "e", "f"))
            assert len(asset["sha256"]) == 64
            assert asset["url"].startswith(
                "https://github.com/gitleaks/gitleaks/releases/download/v8.28.0/")


# ---------------------------------------------------------------------- #
# Local install discovery                                                #
# ---------------------------------------------------------------------- #

def _install_present() -> bool:
    return (LOCAL_INSTALL_ROOT / "8.28.0" / "manifest.json").exists()


@pytest.mark.skipif(not _install_present(),
                    reason="project-local gitleaks install not present")
class TestLocalInstall:
    def test_manifest_shape(self):
        manifest = _local_install("8.28.0")
        assert manifest is not None
        for k in ("tool", "version", "platform", "archiveUrl", "archiveSha256",
                  "binarySha256", "installedPath"):
            assert k in manifest
        assert manifest["tool"] == "gitleaks"
        assert manifest["version"] == "8.28.0"
        assert len(manifest["archiveSha256"]) == 64
        assert len(manifest["binarySha256"]) == 64
        assert manifest["archiveSha256"] != manifest["binarySha256"], (
            "archive hash equals binary hash \u2014 the descriptor is confused")

    def test_runner_discovers_local_install(self):
        runner = GitleaksRunner()
        path = runner.binary_path()
        assert path is not None
        assert path.endswith("/gitleaks")
        assert "/.tools/gitleaks/8.28.0/" in path

    def test_two_layer_sha_check_passes(self):
        runner = GitleaksRunner()
        ok, path_or_reason, version, sha = runner.check_binary()
        assert ok is True, path_or_reason
        assert version == "8.28.0"
        assert len(sha) == 64
        # The runtime SHA equals the manifest's binary SHA.
        manifest = _local_install("8.28.0")
        assert sha == manifest["binarySha256"]
        assert sha != manifest["archiveSha256"]

    def test_env_var_overrides_local_install(self, monkeypatch):
        monkeypatch.setenv("VERITY_GITLEAKS_PATH", "/opt/other/gitleaks")
        runner = GitleaksRunner()
        assert runner.binary_path() == "/opt/other/gitleaks"

    def test_mismatched_binary_is_rejected(self, tmp_path, monkeypatch):
        """Copy the real binary, tamper with one byte, point Verity at the
        tampered copy: hash mismatch must trigger, no scan happens."""
        manifest = _local_install("8.28.0")
        real = Path(manifest["installedPath"])
        tampered_dir = tmp_path / "tools" / "gitleaks" / "8.28.0"
        tampered_dir.mkdir(parents=True)
        tampered_bin = tampered_dir / "gitleaks"
        data = real.read_bytes()
        # flip one byte far from the header so it still parses as a Mach-O
        tampered_bin.write_bytes(data[:-1] + bytes([(data[-1] + 1) % 256]))
        tampered_bin.chmod(0o755)
        # Write a manifest pointing at the tampered file but keeping the
        # real binary's expected hash \u2014 that is the attack we are
        # defending against.
        (tampered_dir / "manifest.json").write_text(json.dumps({
            **manifest,
            "installedPath": str(tampered_bin.resolve()),
        }))
        # Monkeypatch LOCAL_INSTALL_ROOT to the tampered layout.
        monkeypatch.setenv("VERITY_GITLEAKS_PATH", str(tampered_bin))
        import verity.gitleaks_runner as gr
        monkeypatch.setattr(gr, "LOCAL_INSTALL_ROOT", tmp_path / "tools" / "gitleaks")
        runner = GitleaksRunner()
        ok, reason, version, sha = runner.check_binary()
        assert ok is False
        assert reason == "gitleaks_hash_mismatch"


# ---------------------------------------------------------------------- #
# Runner behaviour independent of local install                          #
# ---------------------------------------------------------------------- #

class TestExpectedBinaryShaLookup:
    def test_returns_none_for_unrelated_path(self, tmp_path):
        # A path that isn't inside any registered manifest returns None.
        fake = tmp_path / "gitleaks"
        fake.write_bytes(b"not a binary")
        assert _expected_binary_sha_for(str(fake)) is None
