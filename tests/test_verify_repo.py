"""Tests for ``tools/verify_repo.py``.

The gate script must be trustworthy — if it can be tricked, the whole
handover system is worthless. These tests exercise the individual
checks against fabricated failing inputs, plus a full-run smoke test.
"""

from __future__ import annotations

import importlib.util
import json
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


def test_verified_block_regex_parses_progress():
    text = (REPO / "docs" / "PROGRESS.md").read_text()
    m = verify_repo.VERIFIED_BLOCK_RE.search(text)
    assert m is not None
    date, commit, collected, passed, skipped = m.groups()
    assert len(commit) >= 7
    assert int(passed) + int(skipped) == int(collected)


def test_verified_block_must_come_from_current_summary_not_history(
        tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    progress = dst / "docs" / "PROGRESS.md"
    summary, delimiter, history = progress.read_text().partition("\n---\n")
    assert delimiter
    match = verify_repo.VERIFIED_BLOCK_RE.search(summary)
    assert match is not None
    historical_valid_block = match.group(0)
    corrupted_summary = summary.replace(
        historical_valid_block,
        "verified_against:\n  date: missing-current-block\n",
        1,
    )
    progress.write_text(
        corrupted_summary + delimiter + history + "\n" + historical_valid_block,
    )
    monkeypatch.setattr(verify_repo, "REPO", dst)

    rep = verify_repo.VerifyReport()
    verify_repo.check_progress_verified_block(rep)

    result = [
        row for row in rep.results if row.name == "progress_verified_block"
    ][0]
    assert result.ok is False
    assert "not parseable" in result.detail


def test_verified_block_rejects_commit_that_does_not_exist(
        tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    progress = dst / "docs" / "PROGRESS.md"
    text = progress.read_text()
    match = verify_repo.VERIFIED_BLOCK_RE.search(text)
    assert match is not None
    nonexistent = "f" * 40
    progress.write_text(
        text[:match.start(2)] + nonexistent + text[match.end(2):],
    )
    monkeypatch.setattr(verify_repo, "REPO", dst)

    rep = verify_repo.VerifyReport()
    verify_repo.check_progress_verified_block(rep)

    result = [
        row for row in rep.results if row.name == "progress_verified_block"
    ][0]
    assert result.ok is False
    assert "does not exist" in result.detail


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
    for p in ("docs/project-explainer.html", "docs/verity-manual-zh.html"):
        src = REPO / p
        target = dst / p
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, target)
    # Also copy the src/verity tree so capability-matrix check can
    # find the runtime strings.
    shutil.copytree(REPO / "src", dst / "src")
    # Minimal .gitignore
    shutil.copy(REPO / ".gitignore", dst / ".gitignore")
    return dst


def test_absolute_path_in_progress_is_detected(tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    (dst / "docs" / "PROGRESS.md").write_text(
        (dst / "docs" / "PROGRESS.md").read_text()
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
    # Break the PROGRESS top-block capability label so it no longer
    # matches the runtime string in report.py. (Since Round 74,
    # promptBlackbox/skillSandbox share the same not_enabled/completed/
    # failed vocabulary as static/semantic, so corrupting the *label*
    # -- not the now-shared status literal -- is what actually exercises
    # the mismatch path.)
    cs = dst / "docs" / "PROGRESS.md"
    cs.write_text(
        cs.read_text().replace("V1.5 Prompt black-box", "V1.5 Something Else"))
    monkeypatch.setattr(verify_repo, "REPO", dst)
    rep = verify_repo.VerifyReport()
    verify_repo.check_capability_matrix_matches_runtime(rep)
    r = [r for r in rep.results if r.name == "capability_matrix_matches_runtime"][0]
    assert r.ok is False


def test_capability_matrix_includes_agent_instruction_runtime():
    rep = verify_repo.VerifyReport()
    verify_repo.check_capability_matrix_matches_runtime(rep)
    result = [
        row for row in rep.results
        if row.name == "capability_matrix_matches_runtime"
    ][0]
    assert result.ok is True
    assert "agentInstructionRuntime" in result.detail


def test_capability_matrix_reads_selected_repo_src_not_cache_or_root_package(
        tmp_path, monkeypatch):
    import shutil

    # Prime verity.* in this interpreter, then give the scratch checkout a
    # misleading root package plus a deliberately broken src implementation.
    priming = verify_repo.VerifyReport()
    verify_repo.check_capability_matrix_matches_runtime(priming)
    assert priming.ok is True
    dst = _scratch_repo(tmp_path)
    shutil.copytree(dst / "src" / "verity", dst / "verity")
    report_py = dst / "src" / "verity" / "report.py"
    text = report_py.read_text()
    old = 'agent_instruction_runtime_status = "not_enabled"'
    assert old in text
    report_py.write_text(text.replace(
        old, 'agent_instruction_runtime_status = "completed"', 1))
    monkeypatch.setattr(verify_repo, "REPO", dst)

    rep = verify_repo.VerifyReport()
    verify_repo.check_capability_matrix_matches_runtime(rep)

    result = [
        row for row in rep.results
        if row.name == "capability_matrix_matches_runtime"
    ][0]
    assert result.ok is False
    assert "default report mismatch" in result.detail


@pytest.mark.parametrize("corruption", ["label", "status"])
def test_agent_instruction_runtime_current_summary_cannot_be_spoofed_by_history(
        tmp_path, monkeypatch, corruption):
    dst = _scratch_repo(tmp_path)
    progress = dst / "docs" / "PROGRESS.md"
    summary, delimiter, history = progress.read_text().partition("\n---\n")
    assert delimiter
    label = "Agent-instruction runtime (CLI-only)"
    valid_row = (
        f"| {label} | `completed` / `failed` / `not_enabled` (default) |"
    )
    rows = summary.splitlines()
    matching = [index for index, row in enumerate(rows) if label in row]
    if matching:
        row_index = matching[0]
        valid_row = rows[row_index]
    else:
        row_index = len(rows)
        rows.append(valid_row)
    # Keep an exact valid row in history. A trustworthy gate must inspect the
    # current summary rather than satisfying the contract from old prose.
    history = history + "\n" + valid_row + "\n"
    if corruption == "label":
        rows[row_index] = valid_row.replace(label, "Agent runtime placeholder")
    else:
        rows[row_index] = valid_row.replace("`not_enabled`", "`not_available`")
    progress.write_text("\n".join(rows) + delimiter + history)

    monkeypatch.setattr(verify_repo, "REPO", dst)
    rep = verify_repo.VerifyReport()
    verify_repo.check_capability_matrix_matches_runtime(rep)
    result = [
        row for row in rep.results
        if row.name == "capability_matrix_matches_runtime"
    ][0]
    assert result.ok is False


def test_agent_instruction_runtime_missing_summary_delimiter_cannot_use_history(
        tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    progress = dst / "docs" / "PROGRESS.md"
    summary, delimiter, _history = progress.read_text().partition("\n---\n")
    assert delimiter
    label = "Agent-instruction runtime (CLI-only)"
    valid_row = next(
        row for row in summary.splitlines()
        if row.lstrip().startswith("|") and label in row
    )
    corrupted_row = valid_row.replace(label, "Agent runtime placeholder")
    corrupted_summary = summary.replace(valid_row, corrupted_row, 1)
    # Remove the boundary and leave an exact valid row in what used to be
    # history. Historical text must remain ineligible even if the delimiter
    # itself is corrupted or deleted.
    progress.write_text(
        corrupted_summary + "\n## Historical ledger\n" + valid_row + "\n")

    monkeypatch.setattr(verify_repo, "REPO", dst)
    rep = verify_repo.VerifyReport()
    verify_repo.check_capability_matrix_matches_runtime(rep)
    result = [
        row for row in rep.results
        if row.name == "capability_matrix_matches_runtime"
    ][0]
    assert result.ok is False


def _closure_policy_release_result(dst, monkeypatch):
    monkeypatch.setattr(verify_repo, "REPO", dst)
    rep = verify_repo.VerifyReport()
    verify_repo.check_closure_policy_release_contract(rep)
    return [
        row for row in rep.results
        if row.name == "closure_policy_release_contract"
    ][0]


def test_closure_policy_release_contract_passes_current_repo(monkeypatch):
    result = _closure_policy_release_result(REPO, monkeypatch)
    assert result.ok is True
    assert "policy=2.1.0" in result.detail
    assert "6 current release docs" in result.detail
    assert "PROGRESS history excluded" in result.detail


@pytest.mark.parametrize(
    ("relative_path", "current_token"),
    [
        ("README.md", "Closure policy v2.1.0 scopes"),
        (
            "docs/PROGRESS.md",
            "under closure policy **v2.1.0**",
        ),
        (
            "docs/ARCHITECTURE.md",
            "`verity.closure` (policy v2.1.0) computes",
        ),
        (
            "docs/project-explainer.html",
            "Closure policy v2.1.0 · no evaluated-accuracy claim",
        ),
        (
            "docs/verity-manual-zh.html",
            "收尾政策 v2.1.0 · 不含精度评测结论",
        ),
        (
            "evals/README.md",
            "under closure policy v2.1.0.",
        ),
    ],
)
def test_closure_policy_release_contract_rejects_current_doc_version_drift(
        tmp_path, monkeypatch, relative_path, current_token):
    dst = _scratch_repo(tmp_path)
    path = dst / relative_path
    text = path.read_text()
    assert current_token in text
    path.write_text(text.replace(
        current_token,
        current_token.replace("2.1.0", "2.0.0"),
        1,
    ))

    result = _closure_policy_release_result(dst, monkeypatch)
    assert result.ok is False
    assert relative_path in result.detail
    assert "expected 2.1.0, observed 2.0.0" in result.detail


def test_closure_policy_release_contract_reads_scratch_machine_version(
        tmp_path, monkeypatch):
    # Prime the real checkout first; the scratch source must still win over
    # any verity.closure module cached in this interpreter.
    assert _closure_policy_release_result(REPO, monkeypatch).ok is True
    dst = _scratch_repo(tmp_path)
    closure = dst / "src" / "verity" / "closure.py"
    text = closure.read_text()
    old = 'CLOSURE_POLICY_VERSION = "2.1.0"'
    assert old in text
    closure.write_text(text.replace(
        old, 'CLOSURE_POLICY_VERSION = "9.9.9"', 1))

    result = _closure_policy_release_result(dst, monkeypatch)
    assert result.ok is False
    assert "expected 9.9.9, observed 2.1.0" in result.detail


def test_closure_policy_release_contract_ignores_round190_history_versions(
        tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    progress = dst / "docs" / "PROGRESS.md"
    summary, delimiter, history = progress.read_text().partition("\n---\n")
    assert delimiter
    marker = (
        "## Round 190 (2026-08-10) → artifact-aware dynamic audit and "
        "unified issues"
    )
    assert marker in history
    history = history.replace(
        marker,
        marker + "\n\nHistorical closure policy **v2.0.0** remains protected.",
        1,
    )
    progress.write_text(summary + delimiter + history)

    result = _closure_policy_release_result(dst, monkeypatch)
    assert result.ok is True
    assert "PROGRESS history excluded" in result.detail


def test_closure_policy_release_contract_current_summary_cannot_use_history(
        tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    progress = dst / "docs" / "PROGRESS.md"
    summary, delimiter, history = progress.read_text().partition("\n---\n")
    assert delimiter
    current = "under closure policy **v2.1.0**"
    assert current in summary
    summary = summary.replace(
        current, "under closure policy **v2.0.0**", 1)
    history += "\nHistorical echo: " + current + "\n"
    progress.write_text(summary + delimiter + history)

    result = _closure_policy_release_result(dst, monkeypatch)
    assert result.ok is False
    assert "docs/PROGRESS.md" in result.detail
    assert "expected 2.1.0, observed 2.0.0" in result.detail


def _agent_runtime_release_result(dst, monkeypatch):
    monkeypatch.setattr(verify_repo, "REPO", dst)
    rep = verify_repo.VerifyReport()
    verify_repo.check_agent_runtime_release_contract(rep)
    return [
        row for row in rep.results
        if row.name == "agent_runtime_release_contract"
    ][0]


def test_agent_runtime_release_contract_passes_current_repo(monkeypatch):
    result = _agent_runtime_release_result(REPO, monkeypatch)
    assert result.ok is True
    assert "0.1.1-rc.2" in result.detail
    assert "156 mappings" in result.detail
    assert "V2_agent_runtime none=42 signal=4" in result.detail


def test_agent_runtime_release_contract_rejects_mapping_count_drift(
        tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    path = dst / "standards" / "detector_mappings.json"
    data = json.loads(path.read_text())
    data["detectors"].pop()
    path.write_text(json.dumps(data, indent=2) + "\n")
    assert _agent_runtime_release_result(dst, monkeypatch).ok is False


def test_agent_runtime_release_contract_rejects_fifth_layer_breadth_drift(
        tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    path = dst / "standards" / "risks.json"
    data = json.loads(path.read_text())
    covered = [
        risk for risk in data["risks"]
        if risk["currentCoverage"]["V2_agent_runtime"] == "signal"
    ]
    assert len(covered) == 4
    covered[0]["currentCoverage"]["V2_agent_runtime"] = "none"
    path.write_text(json.dumps(data, indent=2) + "\n")
    assert _agent_runtime_release_result(dst, monkeypatch).ok is False


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("@deepseek-ai/dsh | 0.1.1-rc.2 | MIT",
         "@deepseek-ai/dsh | 0.1.1-rc.1 | MIT"),
        ("@deepseek-ai/dsh | 0.1.1-rc.2 | MIT",
         "@deepseek-ai/dsh | 0.1.1-rc.2 | Apache-2.0"),
    ],
)
def test_agent_runtime_release_contract_rejects_dsh_notice_drift(
        tmp_path, monkeypatch, old, new):
    dst = _scratch_repo(tmp_path)
    path = dst / "THIRD_PARTY_LICENSES.md"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))
    assert _agent_runtime_release_result(dst, monkeypatch).ok is False


def test_agent_runtime_release_contract_rejects_python_lock_dependency(
        tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    lock = dst / "requirements.lock"
    lock.write_text(lock.read_text() + "\n@deepseek-ai/dsh==0.1.1-rc.2\n")
    assert _agent_runtime_release_result(dst, monkeypatch).ok is False


def test_agent_runtime_release_contract_reads_scratch_runtime_source_after_import(
        tmp_path, monkeypatch):
    # Prime the interpreter's verity.* cache from the real repository first.
    assert _agent_runtime_release_result(REPO, monkeypatch).ok is True
    dst = _scratch_repo(tmp_path)
    config = dst / "src" / "verity" / "agent_runtime" / "config.py"
    text = config.read_text()
    old = 'expected_version: str = "0.1.1-rc.2"'
    assert old in text
    config.write_text(text.replace(
        old, 'expected_version: str = "0.1.1-rc.1"', 1))
    # The gate must inspect the scratch REPO, not reuse the primed module.
    assert _agent_runtime_release_result(dst, monkeypatch).ok is False


def test_agent_runtime_release_contract_ignores_selected_repo_root_package(
        tmp_path, monkeypatch):
    import shutil

    dst = _scratch_repo(tmp_path)
    # A normal ``python -c`` run puts cwd first and would import this copied,
    # compliant package instead of the deliberately corrupted REPO/src tree.
    shutil.copytree(dst / "src" / "verity", dst / "verity")
    config = dst / "src" / "verity" / "agent_runtime" / "config.py"
    text = config.read_text()
    old = 'expected_version: str = "0.1.1-rc.2"'
    assert old in text
    config.write_text(text.replace(
        old, 'expected_version: str = "0.1.1-rc.1"', 1))

    assert _agent_runtime_release_result(dst, monkeypatch).ok is False


def test_capability_matrix_default_report_never_runs_agent_runtime(
        monkeypatch):
    from verity.agent_runtime.runner import HarnessAgentRuntimeRunner

    def forbidden_run(*_args, **_kwargs):
        raise AssertionError("capability gate attempted to run Agent runtime")

    monkeypatch.setattr(HarnessAgentRuntimeRunner, "run", forbidden_run)
    rep = verify_repo.VerifyReport()
    verify_repo.check_capability_matrix_matches_runtime(rep)
    result = [
        row for row in rep.results
        if row.name == "capability_matrix_matches_runtime"
    ][0]
    assert result.ok is True


def test_agent_runtime_release_contract_rejects_missing_safety_disclosure(
        tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    readme = dst / "README.md"
    token = "Harness is not an OS, process, or network security sandbox."
    text = readme.read_text()
    assert token in text
    readme.write_text(text.replace(token, "", 1))
    assert _agent_runtime_release_result(dst, monkeypatch).ok is False


def test_agent_runtime_release_contract_rejects_cli_authority_drift(
        tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    architecture = dst / "docs" / "ARCHITECTURE.md"
    text = architecture.read_text()
    accurate = (
        "The plugin, model-facing tool catalog, Cordis permission patch, "
        "output/trace ceilings, and temporary roots are fixed or generated "
        "by Verity on the CLI path; the CLI caller cannot replace them."
    )
    misleading = (
        "the trusted CLI caller can choose plugin, tools, permissions, "
        "and temporary roots."
    )
    normalized = " ".join(text.split())
    if accurate in normalized:
        text = normalized.replace(accurate, misleading, 1)
        normalized = text
    assert misleading in normalized or (
        "trusted CLI caller can enable the stage" in normalized
        and "plugin, tools, permissions," in normalized
    )
    architecture.write_text(text)

    assert _agent_runtime_release_result(dst, monkeypatch).ok is False


def test_agent_runtime_release_contract_rejects_stale_package_scope(
        tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    package_init = dst / "src" / "verity" / "__init__.py"
    text = package_init.read_text()
    accurate = (
        "The deterministic engineering-preview scope is a release candidate; "
        "optional semantic and dynamic stages remain experimental."
    )
    stale = (
        "V1 release decision remains ``not_ready`` and V1.5 Prompt black-box "
        "evaluation and V2 isolated Skill sandbox are not implemented."
    )
    if accurate in " ".join(text.split()):
        doc_end = text.index('"""', 3) + 3
        text = '"""' + stale + '"""' + text[doc_end:]
    assert (
        stale in " ".join(text.split())
        or "V1 release decision remains ``not_ready``" in text
        or "are not implemented" in text
    )
    package_init.write_text(text)

    assert _agent_runtime_release_result(dst, monkeypatch).ok is False


@pytest.mark.parametrize(
    ("relative_path", "required_token"),
    [
        (
            "docs/ARCHITECTURE.md",
            "Version and scenario launches execute the private snapshots, "
            "not the caller paths.",
        ),
        (
            "docs/project-explainer.html",
            "At most two Skill-loader result markers are written; exactly "
            "one successful marker is required, otherwise parsing fails closed.",
        ),
        (
            "docs/verity-manual-zh.html",
            "Skill-loader 结果标记最多写入两个；必须恰好有一个成功标记，"
            "否则解析会失败关闭。",
        ),
        (
            "docs/PROGRESS.md",
            "The adjacent npm closure linked for module resolution remains "
            "unpinned and unauthenticated.",
        ),
        (
            "plans/ACTIVE.md",
            "Version and scenarios run only from those snapshots.",
        ),
    ],
)
def test_agent_runtime_release_contract_rejects_snapshot_boundary_drift(
        tmp_path, monkeypatch, relative_path, required_token):
    dst = _scratch_repo(tmp_path)
    path = dst / relative_path
    normalized = " ".join(path.read_text().split())
    if required_token in normalized:
        normalized = normalized.replace(required_token, "", 1)
    path.write_text(normalized)

    assert _agent_runtime_release_result(dst, monkeypatch).ok is False


def test_agent_runtime_release_contract_architecture_uses_failed_literal():
    architecture = (REPO / "docs" / "ARCHITECTURE.md").read_text()
    assert "not_enabled / completed / fail  │" not in architecture
    assert "/ fail (CLI-only)" not in architecture
    assert "/ failed (CLI-only)" in architecture


@pytest.mark.parametrize(
    ("relative_path", "expected"),
    [
        (
            "docs/project-explainer.html",
            "High/Critical finding or High/Critical Agent-runtime occurrence",
        ),
        (
            "docs/verity-manual-zh.html",
            "High/Critical finding 或 High/Critical Agent 运行时 occurrence",
        ),
    ],
)
def test_agent_runtime_release_contract_html_exit_one_requires_high_severity(
        relative_path, expected):
    assert expected in (REPO / relative_path).read_text()


def test_detection_standards_detail_names_agent_runtime_counts():
    rep = verify_repo.VerifyReport()
    verify_repo.check_detection_standards(rep)
    result = [row for row in rep.results if row.name == "detection_standards"][0]
    assert result.ok is True
    assert "156 mappings" in result.detail
    assert "V2_agent_runtime none=42 signal=4" in result.detail


def test_run_all_skip_tests_includes_release_gates(monkeypatch):
    seen = []
    for name in (
        "check_required_files",
        "check_agents_md_has_ssot",
        "check_progress_verified_block",
        "check_no_absolute_paths_in_docs",
        "check_no_secret_literals",
        "check_pyproject_and_readme_links",
        "check_git_ignored",
        "check_ci_workflow_shape",
        "check_detection_standards",
        "check_corpus_baselines",
        "check_v1_closure_baseline",
        "check_independent_review_evidence",
        "check_semantic_quality_protocol",
        "check_semantic_comparison_protocol",
        "check_scoring_policy",
    ):
        monkeypatch.setattr(verify_repo, name, lambda _rep: None)
    monkeypatch.setattr(
        verify_repo,
        "check_capability_matrix_matches_runtime",
        lambda _rep: seen.append("capability"),
    )
    monkeypatch.setattr(
        verify_repo,
        "check_agent_runtime_release_contract",
        lambda _rep: seen.append("agent_runtime"),
        raising=False,
    )
    monkeypatch.setattr(
        verify_repo,
        "check_closure_policy_release_contract",
        lambda _rep: seen.append("closure_policy"),
        raising=False,
    )
    verify_repo.run_all(skip_tests=True)
    assert seen == ["capability", "agent_runtime", "closure_policy"]


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
    verify_job = jobs["verify"]
    assert verify_job.get("timeout-minutes") == 30
    steps = verify_job["steps"]
    step_names = [s.get("uses") or s.get("name") for s in steps]
    assert any("actions/checkout" in (s or "") for s in step_names)
    assert any("actions/setup-python" in (s or "") for s in step_names)
    checkout = next(
        step for step in steps
        if "actions/checkout" in str(step.get("uses") or "")
    )
    assert (checkout.get("with") or {}).get("fetch-depth") == 0


def test_ci_workflow_gate_requires_history_for_verified_commit(
        tmp_path, monkeypatch):
    dst = _scratch_repo(tmp_path)
    workflow = dst / ".github" / "workflows" / "ci.yml"
    text = workflow.read_text()
    assert "fetch-depth: 0" in text
    workflow.write_text(text.replace("          fetch-depth: 0\n", "", 1))
    monkeypatch.setattr(verify_repo, "REPO", dst)

    rep = verify_repo.VerifyReport()
    verify_repo.check_ci_workflow_shape(rep)

    result = next(row for row in rep.results if row.name == "ci_workflow_shape")
    assert result.ok is False
    assert "fetch-depth" in result.detail

    workflow.write_text(text.replace("    timeout-minutes: 30\n", "", 1))
    rep = verify_repo.VerifyReport()
    verify_repo.check_ci_workflow_shape(rep)

    result = next(row for row in rep.results if row.name == "ci_workflow_shape")
    assert result.ok is False
    assert "timeout-minutes" in result.detail
