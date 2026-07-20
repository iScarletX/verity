"""Controlled subprocess adapter for Bandit.

Bandit is a static Python security scanner (PyCQA, Apache-2.0). We call
it as a **read-only** subprocess against a temporary directory that
contains **only** the ``.py`` files taken from the immutable Snapshot.
We never install anything from the reviewed skill, we never run the
skill's code, we never invoke a shell.

The runner is careful about:

- fixed timeout (soft SIGKILL after ``timeout_seconds``);
- no shell, args are a fixed list;
- controlled env (only ``PATH``, ``HOME``, ``LC_ALL``);
- output-size cap (stdout + stderr);
- JSON parsing + basic shape validation on the parsed dict;
- pinned Bandit version check via ``python -m bandit --version``;
- temporary directory is removed in a ``finally`` block.

Failure modes are surfaced as ``BanditRunResult.status`` and reason
codes. The caller decides whether to emit Findings, and Coverage will
reflect a failed analyzer plan-item accordingly (§9.2).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


REQUIRED_BANDIT_VERSION = "1.7.10"
DEFAULT_TIMEOUT_SECONDS = 30
MAX_OUTPUT_BYTES = 4 * 1024 * 1024   # 4 MiB combined stdout+stderr


@dataclass
class BanditRunResult:
    status: str                        # completed|failed|timeout|version_mismatch
    reasonCode: Optional[str] = None
    toolName: str = "bandit"
    toolVersion: str = ""
    exitCode: Optional[int] = None
    durationSeconds: float = 0.0
    stagedFileCount: int = 0
    pathMap: Dict[str, str] = field(default_factory=dict)   # staged path -> Snapshot fileId
    results: List[Dict] = field(default_factory=list)


class BanditRunner:
    """Injectable runner: swap ``spawn`` in tests to simulate timeout /
    malformed output / version mismatch without touching a real binary.
    """

    def __init__(self, *, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
                 max_output_bytes: int = MAX_OUTPUT_BYTES,
                 required_version: str = REQUIRED_BANDIT_VERSION,
                 python_executable: str = sys.executable) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.required_version = required_version
        self.python_executable = python_executable

    # ------------------------------------------------------------------
    # Injection points

    def spawn(self, args: List[str], cwd: str, env: Dict[str, str]) -> subprocess.CompletedProcess:
        """Actual subprocess call. Overridable in tests."""
        return subprocess.run(
            args, cwd=cwd, env=env,
            capture_output=True, timeout=self.timeout_seconds,
            check=False, shell=False,
        )

    def _controlled_env(self) -> Dict[str, str]:
        keep = ("PATH", "HOME", "LC_ALL", "LANG", "TMPDIR", "SYSTEMROOT")
        return {k: os.environ[k] for k in keep if k in os.environ}

    # ------------------------------------------------------------------
    # Public API

    def check_version(self) -> Tuple[bool, str]:
        try:
            proc = self.spawn(
                [self.python_executable, "-m", "bandit", "--version"],
                cwd=".", env=self._controlled_env(),
            )
        except FileNotFoundError as e:
            return False, f"bandit_not_found:{e}"
        except subprocess.TimeoutExpired:
            return False, "bandit_version_timeout"
        out = (proc.stdout or b"") + (proc.stderr or b"")
        text = out.decode("utf-8", errors="replace")
        # First line looks like: "__main__.py <version>"
        for line in text.splitlines():
            line = line.strip()
            if line and line[:1].isdigit():
                return True, line.split()[0]
            for token in line.split():
                if token[:1].isdigit():
                    return True, token
        return False, f"bandit_version_parse_failed:{text[:80]}"

    def run_on_snapshot(self, snapshot, file_bytes: Dict[str, bytes]) -> BanditRunResult:
        """Stage included `.py` files into a private tmpdir and run bandit."""
        # Version gate.
        ok, ver = self.check_version()
        if not ok:
            return BanditRunResult(status="failed", reasonCode=ver, toolVersion="")
        if ver != self.required_version:
            return BanditRunResult(
                status="version_mismatch",
                reasonCode=f"required={self.required_version};found={ver}",
                toolVersion=ver,
            )

        tmpdir = tempfile.mkdtemp(prefix="verity-bandit-")
        try:
            path_map: Dict[str, str] = {}
            for f in snapshot.files:
                if f.status != "included":
                    continue
                if not f.normalizedPath.lower().endswith(".py"):
                    continue
                if f.entryType != "file":
                    continue
                data = file_bytes.get(f.fileId, b"")
                dst = Path(tmpdir) / f.normalizedPath
                dst.parent.mkdir(parents=True, exist_ok=True)
                # Reject any path that would escape tmpdir (should not
                # happen because intake already normalized paths).
                try:
                    dst.resolve().relative_to(Path(tmpdir).resolve())
                except ValueError:
                    continue
                dst.write_bytes(data)
                path_map[str(dst)] = f.fileId

            if not path_map:
                # Nothing to analyze.
                return BanditRunResult(status="completed", toolVersion=ver,
                                       stagedFileCount=0, pathMap={},
                                       results=[])

            args = [self.python_executable, "-m", "bandit",
                    "-f", "json", "-r", tmpdir]
            import time
            t0 = time.monotonic()
            try:
                proc = self.spawn(args, cwd=tmpdir, env=self._controlled_env())
            except subprocess.TimeoutExpired:
                return BanditRunResult(
                    status="timeout", reasonCode="subprocess_timeout",
                    toolVersion=ver, durationSeconds=time.monotonic() - t0,
                    stagedFileCount=len(path_map), pathMap=path_map,
                )
            except FileNotFoundError as e:
                return BanditRunResult(status="failed",
                                       reasonCode=f"bandit_not_found:{e}",
                                       toolVersion=ver)
            duration = time.monotonic() - t0

            stdout = proc.stdout or b""
            stderr = proc.stderr or b""
            if len(stdout) + len(stderr) > self.max_output_bytes:
                return BanditRunResult(
                    status="failed", reasonCode="output_over_budget",
                    toolVersion=ver, exitCode=proc.returncode,
                    durationSeconds=duration, stagedFileCount=len(path_map),
                    pathMap=path_map,
                )

            # Bandit exit codes: 0 = no issues; 1 = issues found. Both are
            # completed analyzer runs (spec §B4).
            if proc.returncode not in (0, 1):
                return BanditRunResult(
                    status="failed",
                    reasonCode=f"bandit_exit:{proc.returncode}:{stderr[:200].decode('utf-8', 'replace')}",
                    toolVersion=ver, exitCode=proc.returncode,
                    durationSeconds=duration, stagedFileCount=len(path_map),
                    pathMap=path_map,
                )
            try:
                parsed = json.loads(stdout.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return BanditRunResult(
                    status="failed", reasonCode="malformed_json",
                    toolVersion=ver, exitCode=proc.returncode,
                    durationSeconds=duration, stagedFileCount=len(path_map),
                    pathMap=path_map,
                )
            if not isinstance(parsed, dict) or "results" not in parsed:
                return BanditRunResult(
                    status="failed", reasonCode="unexpected_shape",
                    toolVersion=ver, exitCode=proc.returncode,
                    durationSeconds=duration, stagedFileCount=len(path_map),
                    pathMap=path_map,
                )
            results = parsed.get("results", []) or []
            if not isinstance(results, list):
                return BanditRunResult(
                    status="failed", reasonCode="results_not_list",
                    toolVersion=ver, exitCode=proc.returncode,
                    durationSeconds=duration, stagedFileCount=len(path_map),
                    pathMap=path_map,
                )
            # Validate each result minimally.
            validated: List[Dict] = []
            required = ("test_id", "issue_severity", "issue_confidence",
                        "line_number", "filename")
            for r in results:
                if not isinstance(r, dict):
                    continue
                if not all(k in r for k in required):
                    continue
                validated.append(r)
            return BanditRunResult(
                status="completed", toolVersion=ver,
                exitCode=proc.returncode, durationSeconds=duration,
                stagedFileCount=len(path_map), pathMap=path_map,
                results=validated,
            )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
