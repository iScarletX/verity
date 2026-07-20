"""Controlled subprocess adapter for gitleaks.

Gitleaks is a Go binary (MIT). Verity does NOT vendor the binary; the
user must install a pinned version (see ``tools/install_gitleaks.py``
and ``tools/gitleaks_release.json``). At runtime this module:

- resolves the binary from CLI arg / env / PATH, in that order;
- optionally verifies its SHA-256 against the pinned release descriptor;
- checks ``gitleaks version`` and fails on a version mismatch;
- runs ``gitleaks detect --no-git`` in a private tmpdir where only
  intake'd, safety-checked files have been staged (no symlinks, no
  special files, no ``.git`` history, no user-supplied ``.gitleaks.toml``);
- enforces a fixed timeout, no shell, controlled env, and a stdout+stderr
  budget;
- parses the JSON report from a fixed path (report file, not stdout);
- treats gitleaks-detected findings as ``completed`` (found or not
  found are both success); only tool errors are ``failed``.

Failure modes surface as ``GitleaksRunResult.status`` with a specific
``reasonCode``; the engine layer maps them into ExecutionRecord
statuses so Coverage reflects the failure honestly.

SECURITY: the raw ``Secret`` / ``Match`` fields from gitleaks are read
transiently. This runner does NOT persist them, does not log them, and
does not include them in any exception message.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


HERE = Path(__file__).parent
# When Verity is installed as a package (site-packages), the repo layout
# is not available. We still probe for the release descriptor and the
# optional project-local install directory relative to the source tree.
REPO_HINT = HERE.parent.parent
RELEASE_JSON = REPO_HINT / "tools" / "gitleaks_release.json"
LOCAL_INSTALL_ROOT = REPO_HINT / ".tools" / "gitleaks"

REQUIRED_GITLEAKS_VERSION = "8.28.0"
DEFAULT_TIMEOUT_SECONDS = 45
MAX_OUTPUT_BYTES = 4 * 1024 * 1024


@dataclass
class GitleaksRunResult:
    status: str                          # completed|failed|timeout|version_mismatch|not_installed|hash_mismatch
    reasonCode: Optional[str] = None
    toolName: str = "gitleaks"
    toolVersion: str = ""
    toolPath: str = ""
    toolSha256: str = ""
    exitCode: Optional[int] = None
    durationSeconds: float = 0.0
    stagedFileCount: int = 0
    pathMap: Dict[str, str] = field(default_factory=dict)   # staged path -> Snapshot fileId
    # ``results`` is the redacted, normalised view. Raw ``Secret``/``Match``
    # values are NEVER stored here — the runner scrubs them at parse time.
    results: List[Dict[str, Any]] = field(default_factory=list)


def _load_pinned_release() -> Optional[dict]:
    try:
        return json.loads(RELEASE_JSON.read_text())
    except FileNotFoundError:
        return None


def _local_install(version: str) -> Optional[dict]:
    """Return the install manifest for a version installed under the
    project-local ``.tools/gitleaks/<version>/`` directory, or None."""
    manifest_path = LOCAL_INSTALL_ROOT / version / "manifest.json"
    try:
        raw = manifest_path.read_text()
    except FileNotFoundError:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _expected_binary_sha_for(binary_path: str) -> Optional[str]:
    """Locate a manifest whose ``installedPath`` matches ``binary_path``.

    We only enforce hash equality when a manifest exists (i.e. the user
    installed via ``tools/install_gitleaks.py``). For hand-installed
    binaries there is nothing to compare against; the version check
    remains authoritative.
    """
    if not LOCAL_INSTALL_ROOT.is_dir():
        return None
    real = os.path.realpath(binary_path)
    for version_dir in LOCAL_INSTALL_ROOT.iterdir():
        manifest_path = version_dir / "manifest.json"
        try:
            data = json.loads(manifest_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        installed = data.get("installedPath")
        if not installed:
            continue
        if os.path.realpath(installed) == real:
            return data.get("binarySha256")
    return None


def _pick_asset_key() -> Optional[str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin" and machine in ("arm64", "aarch64"):
        return "darwin_arm64"
    if system == "darwin":
        return "darwin_x64"
    if system == "linux" and machine in ("arm64", "aarch64"):
        return "linux_arm64"
    if system == "linux":
        return "linux_x64"
    return None


# -------------------------------------------------------------------- #
# Redaction of raw scanner output                                      #
# -------------------------------------------------------------------- #

_SENSITIVE_KEYS = ("Secret", "Match", "Line")


def _redact_finding(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return a redacted view of a single gitleaks finding.

    We deliberately keep only fields that cannot reconstitute the secret:
    rule id, file, line/column ranges, entropy, and a length bucket for
    the secret to help human triage without leaking the value itself.
    """
    secret = raw.get("Secret") or ""
    if not isinstance(secret, str):
        secret = ""
    length_bucket = _length_bucket(len(secret))

    file_path = raw.get("File") or ""
    if not isinstance(file_path, str):
        file_path = ""

    return {
        "ruleID": _safe_str(raw.get("RuleID"), 64),
        "description": _safe_str(raw.get("Description"), 200),
        "file": file_path,
        "startLine": _safe_int(raw.get("StartLine")),
        "endLine": _safe_int(raw.get("EndLine")),
        "startColumn": _safe_int(raw.get("StartColumn")),
        "endColumn": _safe_int(raw.get("EndColumn")),
        "entropy": _safe_float(raw.get("Entropy")),
        "secretLengthBucket": length_bucket,
        # NOTE: raw Secret / Match / Line are NOT copied.
    }


def _length_bucket(n: int) -> str:
    if n <= 0:
        return "0"
    if n <= 16:
        return "1-16"
    if n <= 32:
        return "17-32"
    if n <= 64:
        return "33-64"
    if n <= 128:
        return "65-128"
    return "129+"


def _safe_str(v: Any, cap: int) -> str:
    if not isinstance(v, str):
        return ""
    if len(v) > cap:
        return v[:cap]
    return v


def _safe_int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    return None


def _safe_float(v: Any) -> Optional[float]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


# -------------------------------------------------------------------- #
# Runner                                                               #
# -------------------------------------------------------------------- #

class GitleaksRunner:
    """Injection-friendly gitleaks driver.

    ``spawn`` is the single external boundary; tests override it to
    exercise failure modes without touching a real binary.
    """

    def __init__(self, *, binary_path: Optional[str] = None,
                 timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
                 max_output_bytes: int = MAX_OUTPUT_BYTES,
                 required_version: str = REQUIRED_GITLEAKS_VERSION,
                 verify_sha256: bool = True) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.required_version = required_version
        self.verify_sha256 = verify_sha256
        self._binary_path = binary_path or self._resolve_binary()

    # --- Resolution -------------------------------------------------

    @staticmethod
    def _resolve_binary() -> Optional[str]:
        # 1) explicit env variable takes precedence.
        env_path = os.environ.get("VERITY_GITLEAKS_PATH")
        if env_path:
            return env_path
        # 2) project-local install created by tools/install_gitleaks.py.
        #    This lets developers use Verity without changing their
        #    global PATH; the tool_path still only comes from trusted
        #    sources (env var, install manifest, PATH), never from any
        #    file in the reviewed skill.
        pinned = _load_pinned_release() or {}
        pinned_version = pinned.get("version")
        if pinned_version:
            manifest = _local_install(pinned_version)
            if manifest:
                candidate = manifest.get("installedPath")
                if candidate and os.path.isfile(candidate):
                    return candidate
        # 3) fall back to PATH.
        return shutil.which("gitleaks")

    def binary_path(self) -> Optional[str]:
        return self._binary_path

    # --- Injection point -------------------------------------------

    def spawn(self, args: List[str], cwd: str, env: Dict[str, str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            args, cwd=cwd, env=env,
            capture_output=True, timeout=self.timeout_seconds,
            check=False, shell=False,
        )

    def _controlled_env(self) -> Dict[str, str]:
        keep = ("PATH", "HOME", "LC_ALL", "LANG", "TMPDIR", "SYSTEMROOT")
        env = {k: os.environ[k] for k in keep if k in os.environ}
        # Neutralise anything that gitleaks might read for config discovery.
        env["GITLEAKS_CONFIG"] = ""      # do not import user config
        return env

    # --- Sanity helpers --------------------------------------------

    def check_binary(self) -> Tuple[bool, str, str, str]:
        """Return (ok, tool_path, tool_version, tool_sha256).

        On failure the second-and-later values are populated with a
        short reason code in ``tool_path`` (misuse of the tuple for
        error signalling avoids two nearly-identical return types).
        """
        path = self._binary_path
        if not path:
            return False, "gitleaks_not_installed", "", ""
        if not os.path.isfile(path):
            return False, "gitleaks_binary_missing", "", ""
        # Two-layer SHA-256 policy:
        #   * ``tools/gitleaks_release.json`` records the *archive*
        #     (tar.gz) SHA-256 published upstream.
        #   * ``tools/install_gitleaks.py`` verifies that archive hash,
        #     extracts the ``gitleaks`` binary safely, computes the
        #     resulting *binary* SHA-256, and writes it to an install
        #     manifest. The runtime re-verifies the binary hash on every
        #     invocation using the manifest — the archive hash cannot be
        #     compared against the extracted binary (different bytes).
        # For hand-installed binaries (no manifest present) the version
        # check below remains authoritative.
        sha = ""
        if self.verify_sha256:
            h = hashlib.sha256()
            with open(path, "rb") as fh:
                while True:
                    chunk = fh.read(1 << 20)
                    if not chunk:
                        break
                    h.update(chunk)
            sha = h.hexdigest()
            expected_binary = _expected_binary_sha_for(path)
            if expected_binary and sha != expected_binary:
                return False, "gitleaks_hash_mismatch", "", sha
        # Version.
        try:
            proc = self.spawn([path, "version"], cwd=".", env=self._controlled_env())
        except subprocess.TimeoutExpired:
            return False, "gitleaks_version_timeout", "", sha
        except FileNotFoundError:
            return False, "gitleaks_binary_missing", "", sha
        text = ((proc.stdout or b"") + (proc.stderr or b"")).decode("utf-8", "replace")
        version = ""
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # gitleaks 8.x prints just the version (e.g. "8.28.0").
            if line[:1].isdigit():
                version = line.split()[0]
                break
        if not version:
            return False, f"gitleaks_version_parse_failed:{text[:80]}", "", sha
        if version != self.required_version:
            return False, f"required={self.required_version};found={version}", version, sha
        return True, path, version, sha

    # --- Public API ------------------------------------------------

    def run_on_snapshot(self, snapshot, file_bytes: Dict[str, bytes]) -> GitleaksRunResult:
        # 1) Sanity checks.
        ok, path_or_reason, version, sha = self.check_binary()
        if not ok:
            status = "not_installed"
            if path_or_reason == "gitleaks_hash_mismatch":
                status = "hash_mismatch"
            elif path_or_reason.startswith("required="):
                status = "version_mismatch"
            elif "version_" in path_or_reason:
                status = "failed"
            return GitleaksRunResult(status=status,
                                     reasonCode=path_or_reason,
                                     toolVersion=version,
                                     toolSha256=sha)

        tmpdir = tempfile.mkdtemp(prefix="verity-gitleaks-")
        report_path = os.path.join(tmpdir, "_report.json")
        source_dir = os.path.join(tmpdir, "src")
        os.makedirs(source_dir, exist_ok=True)
        try:
            # 2) Stage safe files.
            path_map: Dict[str, str] = {}
            for f in snapshot.files:
                if f.status != "included":
                    continue
                if f.entryType != "file":
                    continue
                # Never stage a user-supplied gitleaks config as if it were data.
                name = f.normalizedPath.rsplit("/", 1)[-1]
                if name in (".gitleaks.toml", "gitleaks.toml"):
                    # We do not stage it (so gitleaks won't pick it up),
                    # but we don't error — the file may exist by
                    # coincidence. It's simply excluded from scanning.
                    continue
                data = file_bytes.get(f.fileId, b"")
                dst = Path(source_dir) / f.normalizedPath
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    dst.resolve().relative_to(Path(source_dir).resolve())
                except ValueError:
                    continue
                dst.write_bytes(data)
                path_map[str(dst)] = f.fileId

            if not path_map:
                return GitleaksRunResult(status="completed",
                                         toolVersion=version,
                                         toolPath=path_or_reason,
                                         toolSha256=sha,
                                         stagedFileCount=0,
                                         pathMap={}, results=[])

            args = [
                path_or_reason, "detect",
                "--no-git",
                "--redact",                       # tell gitleaks to redact its own output
                "--exit-code", "0",               # avoid non-zero exit on findings; runner classifies
                "--source", source_dir,
                "--report-format", "json",
                "--report-path", report_path,
            ]
            t0 = time.monotonic()
            try:
                proc = self.spawn(args, cwd=tmpdir, env=self._controlled_env())
            except subprocess.TimeoutExpired:
                return GitleaksRunResult(status="timeout",
                                         reasonCode="subprocess_timeout",
                                         toolVersion=version,
                                         toolPath=path_or_reason,
                                         toolSha256=sha,
                                         durationSeconds=time.monotonic() - t0,
                                         stagedFileCount=len(path_map),
                                         pathMap=path_map)
            except FileNotFoundError:
                return GitleaksRunResult(status="not_installed",
                                         reasonCode="gitleaks_binary_missing",
                                         toolVersion=version)
            duration = time.monotonic() - t0

            stdout = proc.stdout or b""
            stderr = proc.stderr or b""
            if len(stdout) + len(stderr) > self.max_output_bytes:
                return GitleaksRunResult(status="failed",
                                         reasonCode="output_over_budget",
                                         toolVersion=version,
                                         toolPath=path_or_reason,
                                         toolSha256=sha,
                                         exitCode=proc.returncode,
                                         durationSeconds=duration,
                                         stagedFileCount=len(path_map),
                                         pathMap=path_map)

            if proc.returncode not in (0, 1):
                # Anything other than 0 (no findings) or 1 (findings, in
                # newer versions where --exit-code changes this) means
                # gitleaks itself errored.
                return GitleaksRunResult(status="failed",
                                         reasonCode=f"gitleaks_exit:{proc.returncode}",
                                         toolVersion=version,
                                         toolPath=path_or_reason,
                                         toolSha256=sha,
                                         exitCode=proc.returncode,
                                         durationSeconds=duration,
                                         stagedFileCount=len(path_map),
                                         pathMap=path_map)

            # 3) Read report from file (not stdout: this avoids logging).
            if not os.path.isfile(report_path):
                return GitleaksRunResult(status="failed",
                                         reasonCode="report_missing",
                                         toolVersion=version,
                                         toolPath=path_or_reason,
                                         toolSha256=sha,
                                         exitCode=proc.returncode,
                                         durationSeconds=duration,
                                         stagedFileCount=len(path_map),
                                         pathMap=path_map)
            with open(report_path, "rb") as fh:
                raw_bytes = fh.read(self.max_output_bytes + 1)
            if len(raw_bytes) > self.max_output_bytes:
                return GitleaksRunResult(status="failed",
                                         reasonCode="report_over_budget",
                                         toolVersion=version)
            try:
                raw = json.loads(raw_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return GitleaksRunResult(status="failed",
                                         reasonCode="malformed_json",
                                         toolVersion=version,
                                         toolPath=path_or_reason,
                                         toolSha256=sha,
                                         exitCode=proc.returncode,
                                         durationSeconds=duration,
                                         stagedFileCount=len(path_map),
                                         pathMap=path_map)
            if raw is None:
                raw = []
            if not isinstance(raw, list):
                return GitleaksRunResult(status="failed",
                                         reasonCode="report_not_list",
                                         toolVersion=version)
            # 4) Redact and normalise. Raw secrets are dropped here.
            redacted: List[Dict[str, Any]] = []
            for item in raw:
                if isinstance(item, dict):
                    redacted.append(_redact_finding(item))
            # Overwrite the local report file to reduce leftover risk
            # (even though tmpdir will be removed in ``finally``).
            try:
                with open(report_path, "wb") as fh:
                    fh.write(b"{}")
            except OSError:  # pragma: no cover
                pass
            del raw
            del raw_bytes
            return GitleaksRunResult(status="completed",
                                     toolVersion=version,
                                     toolPath=path_or_reason,
                                     toolSha256=sha,
                                     exitCode=proc.returncode,
                                     durationSeconds=duration,
                                     stagedFileCount=len(path_map),
                                     pathMap=path_map,
                                     results=redacted)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
