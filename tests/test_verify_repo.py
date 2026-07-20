"""Tests for ``tools/verify_repo.py``.

The gate script must be trustworthy — if it can be tricked, the whole
handover system is worthless. These tests exercise the individual
checks against fabricated failing inputs, plus a full-run smoke test.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


REPO = Path(__file__).parent.parent
VERIFY_PATH = REPO / "tools" / "verify_repo.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("verify_repo_under_test",
                                                    VERIFY_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # Register in sys.modules BEFORE exec_module so that
    # ``@dataclass`` inside the file can resolve ``cls.__module__``.
    sys.modules["verify_repo_under_test"] = mod
    spec.loader.exec_module(mod)   # type: ignore[union-attr]
    return mod


verify_repo = _load_module()


# ------------------------------------------------------------------ #
# Individual check helpers                                           #
# ------------------------------------------------------------------ #

def test_looks_like_secret_literal_flags_full_ghp():
    literal = "ghp_" + "1234567890abcdefghij1234567890abcdefgh"
    hits = verify_repo._looks_like_secret_literal("prefix " + literal + " suffix")
    assert "github-pat-full-literal" in hits


def test_looks_like_secret_literal_ignores_split_form():
    # Real tests in this repo assemble the literal from pieces to avoid
    # matching upstream scanners.
    literal_source = 'ghp_" + "1234567890abcdefghij1234567890abcdefgh'
    assert verify_repo._looks_like_secret_literal(literal_source) == []


def test_looks_like_secret_literal_flags_aws():
    lit = "AKIA" + "IOSFODNN7EXAMPLE"
    assert "aws-access-key-full-literal" in verify_repo._looks_like_secret_literal(lit)


def test_verified_block_regex_parses_current_state():
    text = (REPO / "docs" / "CURRENT_STATE.md").read_text()
    m = verify_repo.VERIFIED_BLOCK_RE.search(text)
    assert m is not None
    date, commit, collected, passed, skipped = m.groups()
    assert len(commit) >= 7
    assert int(passed) + int(skipped) == int(collected)


# ------------------------------------------------------------------ #
# Full-run smoke                                                     #
# ------------------------------------------------------------------ #

def test_default_run_passes(tmp_path):
    """Running verify_repo in --skip-tests mode from a fresh Python
    process against the checked-in repo must PASS."""
    proc = subprocess.run(
        [sys.executable, str(VERIFY_PATH), "--skip-tests"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stdout + "\n---STDERR---\n" + proc.stderr
    assert "PASS" in proc.stdout


def test_report_render_shows_pass_and_fail():
    rep = verify_repo.VerifyReport()
    rep.append_ok("ok_thing", "detail")
    rep.append_fail("bad_thing", "why")
    rendered = rep.render()
    assert "[PASS] ok_thing" in rendered
    assert "[FAIL] bad_thing" in rendered
    assert rep.ok is False


# ------------------------------------------------------------------ #
# Simulated failures against a scratch repo copy                     #
# ------------------------------------------------------------------ #

def _scratch_repo(tmp_path):
    """Copy just the files verify_repo inspects into tmp_path so we can
    corrupt them and confirm the corresponding check FAILS."""
    import shutil
    dst = tmp_path / "repo"
    dst.mkdir()
    for p in verify_repo.REQUIRED_FILES:
        src = REPO / p
        target = dst / p
        target.parent.mkdir(parents=True, exist_ok=True)
        if src.is_file():
            shutil.copy(src, target)
    # Also copy the src/verity tree so capability-matrix check can
    # find the runtime strings.
    shutil.copytree(REPO / "src", dst / "src")
    # Minimal .gitignore
    shutil.copy(REPO / ".gitignore", dst / ".gitignore")
    return dst


def test_absolute_path_in_current_state_is_detected(tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    (dst / "docs" / "CURRENT_STATE.md").write_text(
        (dst / "docs" / "CURRENT_STATE.md").read_text()
        + "\n\nprivate: /Users/attacker/thing"
    )
    monkeypatch.setattr(verify_repo, "REPO", dst)
    rep = verify_repo.VerifyReport()
    verify_repo.check_no_absolute_paths_in_docs(rep)
    names = {r.name: r.ok for r in rep.results}
    assert names["no_absolute_paths_in_docs"] is False


def test_missing_required_file_is_detected(tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    (dst / "AGENTS.md").unlink()
    monkeypatch.setattr(verify_repo, "REPO", dst)
    rep = verify_repo.VerifyReport()
    verify_repo.check_required_files(rep)
    r = [r for r in rep.results if r.name == "required_files_exist"][0]
    assert r.ok is False
    assert "AGENTS.md" in r.detail


def test_capability_matrix_mismatch_detected(tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    # Break the CURRENT_STATE capability label so it no longer matches
    # the runtime string in report.py.
    cs = dst / "docs" / "CURRENT_STATE.md"
    cs.write_text(cs.read_text().replace("not_implemented", "coming_soon"))
    monkeypatch.setattr(verify_repo, "REPO", dst)
    rep = verify_repo.VerifyReport()
    verify_repo.check_capability_matrix_matches_runtime(rep)
    r = [r for r in rep.results if r.name == "capability_matrix_matches_runtime"][0]
    assert r.ok is False


def test_secret_literal_in_scratch_repo_is_detected(tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    # Plant a full GHP literal in a nested doc.
    (dst / "docs" / "LESSONS.md").write_text(
        "GITHUB_TOKEN=ghp_" + "1234567890abcdefghij1234567890abcdefgh\n"
    )
    monkeypatch.setattr(verify_repo, "REPO", dst)
    rep = verify_repo.VerifyReport()
    verify_repo.check_no_secret_literals(rep)
    r = [r for r in rep.results if r.name == "no_secret_literals"][0]
    assert r.ok is False


def test_ci_yaml_is_parseable_and_declares_permissions(tmp_path, monkeypatch):
    import yaml
    text = (REPO / ".github" / "workflows" / "ci.yml").read_text()
    data = yaml.safe_load(text)
    assert isinstance(data, dict)
    perms = data.get("permissions") or {}
    assert perms.get("contents") in ("read", "write")
    # 'on' can parse as Python True depending on the yaml dialect.
    on_key = data.get("on") if "on" in data else data.get(True)
    assert on_key is not None
    assert "push" in on_key or "pull_request" in on_key
    jobs = data["jobs"]
    assert "verify" in jobs
    steps = jobs["verify"]["steps"]
    step_names = [s.get("uses") or s.get("name") for s in steps]
    assert any("actions/checkout" in (s or "") for s in step_names)
    assert any("actions/setup-python" in (s or "") for s in step_names)
