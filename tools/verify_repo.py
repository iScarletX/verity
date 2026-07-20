#!/usr/bin/env python3
"""verify_repo.py — the machine acceptance gate for this repository.

This script is what turns "the round is done" into a computable claim.
It is intentionally:

- offline: never reaches out to a network
- read-only: never writes to the repo, never asks git for changes
- deterministic: runs pytest against the committed source and reports
  a simple PASS / FAIL per check
- self-testable: has its own tests in ``tests/test_verify_repo.py``

Exit codes:
  0  — every check passed
  non-zero — at least one check failed

Modes:
  default            — assumes local iterative development; a dirty
                        working tree is NOT a failure.
  --require-clean    — CI mode: additionally require ``git status`` to
                        be clean.
  --skip-tests       — skip the pytest run (useful for doc-only edits;
                        the CI job always runs the full suite).

The specific self-reference trap for CURRENT_STATE.md is avoided by
requiring only that ``verified_against.commit`` is an ancestor of the
current HEAD (or equal to it). See ``AGENTS.md §8``.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


HERE = Path(__file__).resolve().parent
REPO = HERE.parent


# --------------------------------------------------------------------- #
# Check result plumbing                                                 #
# --------------------------------------------------------------------- #

@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class VerifyReport:
    results: List[CheckResult] = field(default_factory=list)

    def append(self, r: CheckResult) -> None:
        self.results.append(r)

    def append_ok(self, name: str, detail: str = "") -> None:
        self.append(CheckResult(name, True, detail))

    def append_fail(self, name: str, detail: str) -> None:
        self.append(CheckResult(name, False, detail))

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    def render(self) -> str:
        lines = []
        for r in self.results:
            tag = "PASS" if r.ok else "FAIL"
            line = f"  [{tag}] {r.name}"
            if r.detail:
                line += f"  \u2014 {r.detail}"
            lines.append(line)
        return "\n".join(lines)


# --------------------------------------------------------------------- #
# Helpers                                                               #
# --------------------------------------------------------------------- #

def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def _git(*args: str, cwd: Path = REPO) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd,
                          capture_output=True, text=True, check=False)


def _looks_like_secret_literal(text: str) -> List[str]:
    """Return a list of forbidden literal substrings observed in text.
    We only flag PATTERNS that would trigger public secret scanners.
    Tests may split these into pieces so the source never contains a
    single matching literal.
    """
    forbidden = []
    # GitHub PAT format ``ghp_`` + 36 chars.
    if re.search(r"ghp_[A-Za-z0-9]{36}", text):
        forbidden.append("github-pat-full-literal")
    # AWS access key id format ``AKIA`` + 16 uppercase-alnum.
    if re.search(r"AKIA[0-9A-Z]{16}", text):
        forbidden.append("aws-access-key-full-literal")
    # Slack bot token ``xoxb-...`` (three digit groups then chars).
    if re.search(r"xoxb-\d{9,12}-\d{9,12}-[A-Za-z0-9]{20,}", text):
        forbidden.append("slack-bot-token-full-literal")
    return forbidden


# --------------------------------------------------------------------- #
# Individual checks                                                     #
# --------------------------------------------------------------------- #

REQUIRED_FILES = [
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "docs/CURRENT_STATE.md",
    "docs/SESSION_START.md",
    "docs/ARCHITECTURE.md",
    "docs/LESSONS.md",
    "docs/COLLABORATION.md",
    "docs/PROGRESS.md",
    "docs/spec/ENGINEERING_SPEC-v0.3.md",
    "docs/spec/REUSE_DECISIONS-v0.2.md",
    "plans/ACTIVE.md",
    "plans/TEMPLATE.md",
    "plans/archive/README.md",
    "evals/README.md",
    "tools/verify_repo.py",
    ".github/workflows/ci.yml",
    "requirements.lock",
    "requirements-dev.lock",
    "THIRD_PARTY_LICENSES.md",
    "LICENSE",
    "pyproject.toml",
    "tools/install_gitleaks.py",
    "tools/gitleaks_release.json",
    "tools/start_local_web.py",
]


def check_required_files(rep: VerifyReport) -> None:
    missing = [p for p in REQUIRED_FILES if not (REPO / p).is_file()]
    if missing:
        rep.append_fail("required_files_exist",
                        "missing: " + ", ".join(missing))
    else:
        rep.append_ok("required_files_exist",
                      f"{len(REQUIRED_FILES)} files present")


AGENTS_KEY_PHRASES = [
    "Session Start", "Session End", "Phase gates",
    "MUST NOT be added here",  # sanity check we point at the right file
]


def check_agents_md_has_ssot(rep: VerifyReport) -> None:
    text = _read_text(REPO / "AGENTS.md")
    missing_hdr = [h for h in ("Single Source of Truth", "Session Start",
                                "Session End", "Phase gates",
                                "Prohibited actions",
                                "Documentation drift protection")
                   if h not in text]
    if missing_hdr:
        rep.append_fail("agents_md_ssot_headers",
                        "missing sections: " + ", ".join(missing_hdr))
    else:
        rep.append_ok("agents_md_ssot_headers",
                      "AGENTS.md contains all canonical sections")


def check_claude_md_is_thin(rep: VerifyReport) -> None:
    text = _read_text(REPO / "CLAUDE.md")
    if len(text) > 2000:
        rep.append_fail("claude_md_is_thin",
                        f"CLAUDE.md is {len(text)} chars; keep it a pointer")
        return
    if "AGENTS.md" not in text:
        rep.append_fail("claude_md_is_thin",
                        "CLAUDE.md must point at AGENTS.md")
        return
    rep.append_ok("claude_md_is_thin", f"CLAUDE.md is {len(text)} chars")


VERIFIED_BLOCK_RE = re.compile(
    r"verified_against:\s*\n"
    r"\s*date:\s*\"?([0-9\-]+)\"?\s*\n"
    r".*?"
    r"\s*commit:\s*\"?([0-9a-fA-F]{7,40})\"?\s*\n"
    r".*?"
    r"\s*tests_collected:\s*(\d+)\s*\n"
    r"\s*tests_passed:\s*(\d+)\s*\n"
    r"\s*tests_skipped:\s*(\d+)",
    re.DOTALL,
)


def check_current_state_block(rep: VerifyReport) -> None:
    text = _read_text(REPO / "docs" / "CURRENT_STATE.md")
    m = VERIFIED_BLOCK_RE.search(text)
    if not m:
        rep.append_fail("current_state_verified_block",
                        "verified_against block not parseable")
        return
    date, commit, collected, passed, skipped = m.groups()
    if int(passed) > int(collected):
        rep.append_fail("current_state_verified_block",
                        "tests_passed > tests_collected")
        return
    if int(passed) + int(skipped) != int(collected):
        rep.append_fail(
            "current_state_verified_block",
            f"passed + skipped ({int(passed)} + {int(skipped)}) != collected ({collected})")
        return
    # Ancestor check (only when we are inside a git repo).
    proc = _git("cat-file", "-e", commit)
    if proc.returncode == 0:
        head = _git("rev-parse", "HEAD").stdout.strip()
        if head:
            mb = _git("merge-base", "--is-ancestor", commit, head)
            if mb.returncode != 0:
                rep.append_fail(
                    "current_state_verified_block",
                    f"verified_against commit {commit[:12]} is not an ancestor of HEAD")
                return
    rep.append_ok("current_state_verified_block",
                  f"date={date} commit={commit[:12]} tests={passed}/{collected}")


CAPABILITY_ROWS = [
    ("Static (deterministic) auditing", "completed"),
    ("Semantic (LLM-assisted) auditing", "not_enabled"),
    ("V1.5 Prompt black-box", "not_implemented"),
    ("V2 Skill isolated sandbox", "not_implemented"),
]


def check_capability_matrix_matches_runtime(rep: VerifyReport) -> None:
    """CURRENT_STATE capability strings must match the runtime strings
    that verity/report.py emits.  We don't execute Verity here; we
    just assert the four expected labels are present in the doc and
    that the report.py source contains matching literals for the two
    fixed strings ``not_implemented`` and ``not_enabled``.
    """
    doc = _read_text(REPO / "docs" / "CURRENT_STATE.md")
    for label, status in CAPABILITY_ROWS:
        if label not in doc:
            rep.append_fail("capability_matrix_matches_runtime",
                            f"missing label in CURRENT_STATE: {label}")
            return
        if status not in doc:
            rep.append_fail("capability_matrix_matches_runtime",
                            f"missing status {status!r} for {label}")
            return
    report_src = _read_text(REPO / "src" / "verity" / "report.py")
    for status in ("not_enabled", "not_implemented", "completed"):
        if f'"{status}"' not in report_src:
            rep.append_fail("capability_matrix_matches_runtime",
                            f"report.py missing literal {status!r}")
            return
    rep.append_ok("capability_matrix_matches_runtime",
                  "CURRENT_STATE labels + statuses agree with report.py")


def check_no_absolute_paths_in_docs(rep: VerifyReport) -> None:
    """Docs must not carry host paths."""
    offenders: List[str] = []
    doc_paths = list((REPO / "docs").rglob("*.md")) + [
        REPO / "AGENTS.md", REPO / "CLAUDE.md",
        REPO / "plans" / "ACTIVE.md", REPO / "plans" / "TEMPLATE.md",
        REPO / "plans" / "archive" / "README.md",
        REPO / "evals" / "README.md",
    ]
    for p in doc_paths:
        text = _read_text(p)
        if not text:
            continue
        # The standard handover prompt intentionally names the local
        # path in a fenced code block. It is the SSOT for the handover
        # prompt and is user-visible on purpose. We allow the host-path
        # prefix only inside ``docs/SESSION_START.md`` and nowhere else.
        allow = str(p).endswith("docs/SESSION_START.md")
        for pat in ("/Users/", "/private/", "/tmp/verity-"):
            if pat in text and not allow:
                offenders.append(f"{p.relative_to(REPO)}: {pat}")
    if offenders:
        rep.append_fail("no_absolute_paths_in_docs",
                        "; ".join(offenders))
    else:
        rep.append_ok("no_absolute_paths_in_docs",
                      "no host paths in docs (except SSOT handover prompt)")


def check_no_secret_literals(rep: VerifyReport) -> None:
    offenders: List[str] = []
    for root, _dirs, files in os.walk(REPO):
        # skip vendored / generated / cache
        rel_root = Path(root).relative_to(REPO)
        top = rel_root.parts[0] if rel_root.parts else ""
        if top in {".git", ".tools", ".pytest_cache", "__pycache__",
                    "out", "dist", "build", "node_modules"}:
            continue
        # deep-skip caches
        parts = set(rel_root.parts)
        if parts & {"__pycache__", ".pytest_cache", ".mypy_cache"}:
            continue
        for name in files:
            if not name.endswith((".py", ".md", ".yml", ".yaml",
                                    ".json", ".txt", ".toml", ".env",
                                    ".sh", ".css", ".js", ".html")):
                continue
            p = Path(root) / name
            # skip verify_repo.py itself (it CONTAINS the patterns it looks for)
            if p.resolve() == Path(__file__).resolve():
                continue
            text = _read_text(p)
            if not text:
                continue
            for hit in _looks_like_secret_literal(text):
                offenders.append(f"{p.relative_to(REPO)}: {hit}")
    if offenders:
        rep.append_fail("no_secret_literals",
                        "; ".join(offenders))
    else:
        rep.append_ok("no_secret_literals",
                      "no full-literal secret patterns")


def check_pyproject_and_readme_links(rep: VerifyReport) -> None:
    pyproj = _read_text(REPO / "pyproject.toml")
    for expect in ("[project]", "name = \"verity\"",
                    "dependencies", "starlette", "jsonschema", "PyYAML",
                    "bandit"):
        if expect not in pyproj:
            rep.append_fail("pyproject_and_readme_links",
                            f"pyproject.toml missing {expect!r}")
            return
    readme = _read_text(REPO / "README.md")
    if "docs/CURRENT_STATE.md" not in readme and "CURRENT_STATE" not in readme:
        rep.append_fail("pyproject_and_readme_links",
                        "README.md must link to CURRENT_STATE.md")
        return
    if "AGENTS.md" not in readme:
        rep.append_fail("pyproject_and_readme_links",
                        "README.md must link to AGENTS.md")
        return
    rep.append_ok("pyproject_and_readme_links",
                  "pyproject + README have expected pointers")


def check_git_ignored(rep: VerifyReport) -> None:
    """A handful of paths must be gitignored to keep the repo clean."""
    must_ignore = [".tools/", ".pytest_cache/", "__pycache__/", "out/"]
    proc = _git("status")
    if proc.returncode != 0:
        rep.append_fail("git_ignored", "not a git repository")
        return
    gitignore = _read_text(REPO / ".gitignore")
    missing = [p for p in must_ignore if p not in gitignore]
    if missing:
        rep.append_fail("git_ignored",
                        "gitignore missing: " + ", ".join(missing))
    else:
        rep.append_ok("git_ignored",
                      ".tools + caches are gitignored")


def check_ci_workflow_shape(rep: VerifyReport) -> None:
    """Parse CI YAML (best-effort without pyyaml at CI-verify time) and
    require some key strings. We rely on PyYAML if available (Verity's
    lock pins it), but degrade gracefully if not.
    """
    p = REPO / ".github" / "workflows" / "ci.yml"
    text = _read_text(p)
    if not text:
        rep.append_fail("ci_workflow_shape", "ci.yml missing or empty")
        return
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
    except Exception as e:  # pragma: no cover
        # Fall back to a substring check; better than nothing.
        data = None
        parse_err = str(e)
    else:
        parse_err = ""

    if data is not None:
        if not isinstance(data, dict):
            rep.append_fail("ci_workflow_shape",
                            "ci.yml top-level must be a mapping")
            return
        # 'on' is parsed as True by PyYAML (Python bool True) under some
        # dialects; accept both key spellings.
        trig = data.get("on", data.get(True))
        if trig is None:
            rep.append_fail("ci_workflow_shape",
                            "ci.yml missing 'on' triggers")
            return
        perms = data.get("permissions")
        if not (isinstance(perms, dict)
                and perms.get("contents") in ("read", "write")):
            rep.append_fail("ci_workflow_shape",
                            "ci.yml must set permissions.contents")
            return
        jobs = data.get("jobs") or {}
        if not jobs:
            rep.append_fail("ci_workflow_shape",
                            "ci.yml has no jobs")
            return
    else:
        # Substring fallback.
        for needle in ("on:", "permissions:", "jobs:"):
            if needle not in text:
                rep.append_fail("ci_workflow_shape",
                                f"ci.yml (unparsed: {parse_err}) missing {needle!r}")
                return
    # Regardless of parser path, verify the required action lines.
    for needle in ("actions/checkout", "actions/setup-python",
                    "verify_repo.py"):
        if needle not in text:
            rep.append_fail("ci_workflow_shape",
                            f"ci.yml missing {needle!r}")
            return
    rep.append_ok("ci_workflow_shape", "ci.yml permissions + steps ok")


def check_working_tree_clean(rep: VerifyReport) -> None:
    proc = _git("status", "--porcelain")
    if proc.returncode != 0:
        rep.append_fail("working_tree_clean", "git not available")
        return
    if proc.stdout.strip():
        rep.append_fail("working_tree_clean",
                        f"{len(proc.stdout.splitlines())} unstaged/untracked entries")
        return
    rep.append_ok("working_tree_clean", "clean")


def run_pytest(rep: VerifyReport) -> None:
    # We run pytest at the module level to avoid relying on the wrapper
    # script being on PATH.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q"], cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        tail = "\n".join(proc.stdout.splitlines()[-20:])
        rep.append_fail("pytest",
                        f"exit={proc.returncode}; last lines:\n{tail}")
        return
    last = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    rep.append_ok("pytest", last[:200])


# --------------------------------------------------------------------- #
# Main entry                                                            #
# --------------------------------------------------------------------- #

def run_all(*, require_clean: bool = False,
            skip_tests: bool = False) -> VerifyReport:
    rep = VerifyReport()
    check_required_files(rep)
    check_agents_md_has_ssot(rep)
    check_claude_md_is_thin(rep)
    check_current_state_block(rep)
    check_capability_matrix_matches_runtime(rep)
    check_no_absolute_paths_in_docs(rep)
    check_no_secret_literals(rep)
    check_pyproject_and_readme_links(rep)
    check_git_ignored(rep)
    check_ci_workflow_shape(rep)
    if require_clean:
        check_working_tree_clean(rep)
    if not skip_tests:
        run_pytest(rep)
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="verify_repo",
                                 description=__doc__)
    ap.add_argument("--require-clean", action="store_true",
                    help="also require ``git status`` to be clean")
    ap.add_argument("--skip-tests", action="store_true",
                    help="skip the pytest run")
    args = ap.parse_args(argv)

    rep = run_all(require_clean=args.require_clean,
                   skip_tests=args.skip_tests)
    print(rep.render())
    if rep.ok:
        print("\nverify_repo: PASS")
        return 0
    print("\nverify_repo: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
