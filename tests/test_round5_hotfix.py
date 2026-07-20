"""Round-5 hotfix: CLI exit codes + SARIF flat-key documentation.

Policy under test (see verity/cli.py module docstring):

    findings gate wins over coverage gate.
    0 -> gate=pass                (coverage sufficient AND no High/Critical)
    1 -> gate=findings_block      (any High/Critical Finding)
    3 -> gate=coverage_block      (coverage insufficient AND no High/Critical)
    2 -> reserved by argparse for CLI usage errors.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def _run_cli(args, cwd=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "src")
    # Ensure real gitleaks isn't picked up accidentally during the
    # regression run — we want the deterministic "not installed" path.
    env["VERITY_GITLEAKS_PATH"] = "/nonexistent/gitleaks"
    return subprocess.run(
        [sys.executable, "-m", "verity.cli"] + args,
        cwd=cwd or REPO, env=env, capture_output=True, text=True,
    )


class TestExitCodes:
    def test_standard_gitleaks_missing_clean_skill_is_coverage_block(self, tmp_path):
        """standard + gitleaks missing + clean skill: MUST be exit 3
        (coverage_block). Coverage-insufficient never returns 0."""
        p = _run_cli(["review", "--engine", "skill", "--profile", "standard",
                      "--input-dir", str(FIXTURES / "clean_skill"),
                      "--out", str(tmp_path)])
        assert p.returncode == 3, (p.returncode, p.stdout, p.stderr)
        assert "gate=coverage_block" in p.stdout

    def test_minimal_clean_skill_is_pass(self, tmp_path):
        p = _run_cli(["review", "--engine", "skill", "--profile", "minimal",
                      "--input-dir", str(FIXTURES / "clean_skill"),
                      "--out", str(tmp_path)])
        assert p.returncode == 0
        assert "gate=pass" in p.stdout

    def test_high_finding_and_insufficient_returns_findings_block(self, tmp_path):
        """python_shell_true_skill under standard profile has a High
        Bandit B602 AND (because gitleaks is unavailable) coverage
        insufficient. Findings gate wins -> exit 1."""
        p = _run_cli(["review", "--engine", "skill", "--profile", "standard",
                      "--input-dir", str(FIXTURES / "python_shell_true_skill"),
                      "--out", str(tmp_path)])
        assert p.returncode == 1
        assert "gate=findings_block" in p.stdout

    def test_high_finding_but_sufficient_still_findings_block(self, tmp_path):
        """Under `minimal` profile the same fixture has High findings but
        sufficient coverage — exit is still 1 (findings gate)."""
        p = _run_cli(["review", "--engine", "skill", "--profile", "minimal",
                      "--input-dir", str(FIXTURES / "python_shell_true_skill"),
                      "--out", str(tmp_path)])
        assert p.returncode == 1
        assert "gate=findings_block" in p.stdout

    def test_prompt_engine_unaffected_by_gitleaks(self, tmp_path):
        """Prompt engine has no gitleaks analyzer. A clean prompt must
        exit 0 regardless of the machine's gitleaks state."""
        p = _run_cli(["review", "--engine", "prompt",
                      "--prompt-kind", "user_prompt",
                      "--text", "Please summarise the following article politely.",
                      "--out", str(tmp_path)])
        assert p.returncode == 0
        assert "gate=pass" in p.stdout

    def test_prompt_high_finding_returns_findings_block(self, tmp_path):
        p = _run_cli(["review", "--engine", "prompt",
                      "--prompt-kind", "system_prompt",
                      "--input-file", str(FIXTURES / "prompt_risky_system" / "system.txt"),
                      "--out", str(tmp_path)])
        assert p.returncode == 1
        assert "gate=findings_block" in p.stdout

    def test_gate_marker_always_present(self, tmp_path):
        """Every review invocation must emit a `gate=` marker for CI."""
        p = _run_cli(["review", "--engine", "prompt",
                      "--prompt-kind", "user_prompt",
                      "--text", "hi", "--out", str(tmp_path)])
        assert " gate=" in p.stdout

    def test_argparse_usage_error_exit_two(self):
        """POSIX argparse still uses 2 for usage errors; documented."""
        p = _run_cli(["review", "--engine", "bogus"])
        assert p.returncode == 2


class TestSarifFlatProperties:
    """Documentation-alignment: SARIF exposes coverage as a flat
    ``verity.coverage`` key in ``run.properties``.  This test guards
    against accidental format drift."""

    def test_run_properties_uses_flat_verity_keys(self, tmp_path):
        p = _run_cli(["review", "--engine", "skill", "--profile", "minimal",
                      "--input-dir", str(FIXTURES / "clean_skill"),
                      "--out", str(tmp_path)])
        assert p.returncode == 0, p.stderr
        sarif = json.loads((tmp_path / "report.sarif").read_text())
        props = sarif["runs"][0]["properties"]
        # Flat, namespaced keys are what we ship.
        assert "verity.coverage" in props
        assert props["verity.coverage"] == "sufficient"
        # NOT a nested "coverage" dict; guard against a wrong migration.
        assert "coverage" not in props
        # And keep the rest of the surface stable.
        for key in ("verity.reviewId", "verity.snapshotId",
                    "verity.engine", "verity.verdict.subject",
                    "verity.verdict.reasonCodes", "verity.owaspCoverage"):
            assert key in props, f"missing SARIF flat property: {key}"

    def test_coverage_key_reflects_insufficient(self, tmp_path):
        p = _run_cli(["review", "--engine", "skill", "--profile", "standard",
                      "--input-dir", str(FIXTURES / "clean_skill"),
                      "--out", str(tmp_path)])
        # standard + gitleaks missing = coverage_block; exit 3.
        assert p.returncode == 3
        sarif = json.loads((tmp_path / "report.sarif").read_text())
        assert sarif["runs"][0]["properties"]["verity.coverage"] == "insufficient"
