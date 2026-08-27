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
from collections import Counter
import json
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


def _run_isolated_repo_probe(
        probe_source: str, *, timeout: float = 15.0
) -> subprocess.CompletedProcess:
    """Run a read-only Python probe importing only the selected REPO/src.

    The child uses the selected ``REPO/src`` itself as cwd, ``-E`` ignores
    user-controlled Python environment settings, and the bootstrap places the
    resolved source tree first.  It then clears any unexpectedly preloaded
    ``verity`` modules and verifies the imported package origin before running
    the caller's probe.  A fresh process also prevents this verifier's own
    ``sys.modules`` cache from satisfying a scratch-repository check with
    modules from another checkout.  Unlike ``-I``, this keeps the user-site
    dependencies supported by the project's Python 3.9 baseline available.
    """
    bootstrap = r'''
import sys
from pathlib import Path

repo = Path(sys.argv[1]).resolve()
source_root = (repo / "src").resolve()
for module_name in tuple(sys.modules):
    if module_name == "verity" or module_name.startswith("verity."):
        del sys.modules[module_name]
sys.path.insert(0, str(source_root))
import verity
package_root = Path(verity.__file__).resolve().parent
expected_root = (source_root / "verity").resolve()
if package_root != expected_root:
    raise RuntimeError(
        f"selected REPO/src import mismatch: {package_root} != {expected_root}"
    )
''' + probe_source
    env = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONINSPECT"):
        env.pop(name, None)
    return subprocess.run(
        [sys.executable, "-E", "-c", bootstrap, str(REPO)],
        cwd=REPO / "src",
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


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

# Minimal handover-file set (round 10). No CLAUDE.md, no
# SESSION_START, no CURRENT_STATE, no docs/spec, no .githooks, no
# plans/TEMPLATE. Everything else that had substance in round 9 was
# merged into one of the files below.
REQUIRED_FILES = [
    # SSOT trio
    "README.md",
    "AGENTS.md",
    "docs/PROGRESS.md",
    # Plans
    "plans/ACTIVE.md",
    "plans/archive/README.md",
    # Docs
    "docs/ARCHITECTURE.md",
    "docs/LESSONS.md",
    "docs/MEMORY.md",
    # Eval + gates
    "evals/README.md",
    "tools/verify_repo.py",
    ".github/workflows/ci.yml",
    # Build / dependency baseline
    "requirements.lock",
    "requirements-dev.lock",
    "THIRD_PARTY_LICENSES.md",
    "LICENSE",
    "pyproject.toml",
    "setup.py",
    # Tooling scripts already in the repo
    "tools/install_gitleaks.py",
    "tools/gitleaks_release.json",
    "tools/start_local_web.py",
    # Detection capability baseline
    "standards/README.md",
    "standards/sources.json",
    "standards/risks.json",
    "standards/detector_mappings.json",
    "standards/detector_candidates.json",
    # Versioned offline detection corpus and reproducible baselines
    "evals/corpus/v1/manifest.json",
    "evals/corpus/v1/semantic_replay.json",
    "evals/corpus/v1/semantic_quality.json",
    "evals/corpus/v1/semantic_comparison_v3.json",
    "evals/reference/butler_crosswalk.json",
    "evals/reports/corpus-v1-l0.json",
    "evals/reports/corpus-v1-semantic-contract.json",
    "evals/reports/v1-closure.json",
    "evals/reviews/corpus-v1-independent-ai-review.json",
    "evals/reviews/semantic-selection-v1-invalidation.json",
    "tools/run_corpus.py",
    "tools/run_v1_closure.py",
    "tools/semantic_head_to_head.py",
    "tools/butler_reference_entry.ts",
    "tools/butler_reference.vite.config.mjs",
]


def check_required_files(rep: VerifyReport) -> None:
    missing = [p for p in REQUIRED_FILES if not (REPO / p).is_file()]
    if missing:
        rep.append_fail("required_files_exist",
                        "missing: " + ", ".join(missing))
    else:
        rep.append_ok("required_files_exist",
                      f"{len(REQUIRED_FILES)} files present")


def check_agents_md_has_ssot(rep: VerifyReport) -> None:
    """AGENTS.md is the single agent-rulebook and must contain the
    canonical sections that other files may link to."""
    text = _read_text(REPO / "AGENTS.md")
    required = (
        "Single Source of Truth",
        "Session Start",
        "Session End",
        "Phase gates",
        "Prohibited actions",
        "Standard handover prompt",
    )
    missing = [h for h in required if h not in text]
    if missing:
        rep.append_fail("agents_md_ssot_headers",
                        "missing sections: " + ", ".join(missing))
    else:
        rep.append_ok("agents_md_ssot_headers",
                      "AGENTS.md contains all canonical sections")


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


def check_progress_verified_block(rep: VerifyReport) -> None:
    """docs/PROGRESS.md carries the top-of-file verified_against block
    (replaces the round-9 docs/CURRENT_STATE.md)."""
    text = _read_text(REPO / "docs" / "PROGRESS.md")
    current_summary, summary_delimiter, _history = text.partition("\n---\n")
    if not summary_delimiter:
        rep.append_fail(
            "progress_verified_block",
            "docs/PROGRESS.md is missing the current-summary delimiter",
        )
        return
    matches = list(VERIFIED_BLOCK_RE.finditer(current_summary))
    m = matches[0] if len(matches) == 1 else None
    if not m:
        rep.append_fail("progress_verified_block",
                        "verified_against block not parseable")
        return
    date, commit, collected, passed, skipped = m.groups()
    if int(passed) > int(collected):
        rep.append_fail("progress_verified_block",
                        "tests_passed > tests_collected")
        return
    if int(passed) + int(skipped) != int(collected):
        rep.append_fail(
            "progress_verified_block",
            f"passed + skipped ({int(passed)} + {int(skipped)}) != collected ({collected})")
        return
    proc = _git("cat-file", "-e", commit + "^{commit}")
    if proc.returncode != 0:
        rep.append_fail(
            "progress_verified_block",
            f"verified_against commit {commit[:12]} does not exist",
        )
        return
    head_proc = _git("rev-parse", "--verify", "HEAD^{commit}")
    head = head_proc.stdout.strip()
    if head_proc.returncode != 0 or not head:
        rep.append_fail("progress_verified_block", "HEAD commit is not readable")
        return
    mb = _git("merge-base", "--is-ancestor", commit, head)
    if mb.returncode != 0:
        rep.append_fail(
            "progress_verified_block",
            f"verified_against commit {commit[:12]} is not an ancestor of HEAD")
        return
    rep.append_ok("progress_verified_block",
                  f"date={date} commit={commit[:12]} tests={passed}/{collected}")


CAPABILITY_ROWS = [
    ("Static (deterministic) auditing", "static", "completed"),
    ("Semantic (LLM-assisted) auditing", "semantic", "not_enabled"),
    ("V1.5 Prompt black-box", "promptBlackbox", "not_enabled"),
    ("V2 Skill sandbox (hardening required)", "skillSandbox", "not_enabled"),
    ("Agent-instruction runtime (CLI-only)",
     "agentInstructionRuntime", "not_enabled"),
]


def check_capability_matrix_matches_runtime(rep: VerifyReport) -> None:
    """Keep the current PROGRESS matrix aligned with a default report.

    Historical rounds are an append-only ledger, so only the summary before
    the first delimiter may satisfy this contract. The report is generated
    offline with every optional dynamic stage explicitly unrequested.
    """
    doc = _read_text(REPO / "docs" / "PROGRESS.md")
    current_summary, summary_delimiter, _history = doc.partition("\n---\n")
    if not summary_delimiter:
        rep.append_fail(
            "capability_matrix_matches_runtime",
            "docs/PROGRESS.md is missing the current-summary delimiter",
        )
        return
    table_rows = []
    for line in current_summary.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and len(cells) >= 2:
            table_rows.append(cells)
    for label, _runtime_key, expected_default in CAPABILITY_ROWS:
        matches = [row for row in table_rows if row[0] == label]
        if len(matches) != 1:
            rep.append_fail("capability_matrix_matches_runtime",
                            f"current PROGRESS summary must contain one row: {label}")
            return
        if f"`{expected_default}`" not in matches[0][1]:
            rep.append_fail("capability_matrix_matches_runtime",
                            f"{label} row missing default {expected_default!r}")
            return

    try:
        probe_source = r'''
import json
from verity.intake import intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
snapshot, data = intake_text("Summarize this text.")
report = review_to_dict(
    run_review(ReviewInputs("prompt", snapshot, data)))
capabilities = report.get("capabilities") or {}
observed = {
    key: (capabilities.get(key) or {}).get("status")
    for key in (
        "static",
        "semantic",
        "promptBlackbox",
        "skillSandbox",
        "agentInstructionRuntime",
    )
}
print(json.dumps(observed, sort_keys=True))
'''
        probe = _run_isolated_repo_probe(probe_source)
        if probe.returncode != 0:
            detail = (probe.stderr.strip() or probe.stdout.strip())[-300:]
            raise ValueError(f"isolated capability probe failed: {detail}")
        observed = json.loads(probe.stdout)
        expected = {key: default for _label, key, default in CAPABILITY_ROWS}
        if observed != expected:
            raise ValueError(
                f"default report mismatch: expected={expected} observed={observed}")
    except Exception as exc:
        rep.append_fail("capability_matrix_matches_runtime", str(exc)[:500])
        return
    rep.append_ok("capability_matrix_matches_runtime",
                  "current PROGRESS summary matches default report keys: "
                  + ", ".join(expected))


def check_agent_runtime_release_contract(rep: VerifyReport) -> None:
    """Verify the bounded Agent-runtime contract without launching it."""
    expected_signals = {
        "agent_runtime_sensitive_read_attempt": ("VR-SKILL-014", "high"),
        "agent_runtime_network_attempt": ("VR-SKILL-009", "medium"),
        "agent_runtime_shell_attempt": ("VR-SKILL-006", "high"),
        "agent_runtime_canary_exfiltration_attempt": (
            "VR-SKILL-011", "high"),
    }
    try:
        # Use a fresh interpreter rooted at REPO so scratch-repository tests
        # cannot be masked by verity.* modules cached from the real checkout.
        # This probe imports definitions and constructs only the disabled
        # config; it never instantiates a runner or starts DSH/model/network.
        probe_source = r'''
import json
from verity.agent_runtime.config import AgentRuntimeConfig
from verity.agent_runtime.models import AGENT_RUNTIME_SIGNAL_DETECTORS
from verity.dynamic.planner import CHECK_DEFINITIONS
from verity.scoring import (
    CONFIDENCE_POLICY_VERSION,
    POLICY_VERSION,
    _AGENT_RUNTIME_SIGNAL_SEVERITY,
)
config = AgentRuntimeConfig()
print(json.dumps({
    "enabled": config.enabled,
    "expectedVersion": config.expected_version,
    "signals": list(AGENT_RUNTIME_SIGNAL_DETECTORS),
    "severities": _AGENT_RUNTIME_SIGNAL_SEVERITY,
    "scorePolicy": POLICY_VERSION,
    "confidencePolicy": CONFIDENCE_POLICY_VERSION,
    "dynamicCheckCount": sum(
        definition.check_id == "agent_instruction.runtime"
        for definition in CHECK_DEFINITIONS
    ),
}, sort_keys=True))
'''
        probe = _run_isolated_repo_probe(probe_source)
        if probe.returncode != 0:
            detail = (probe.stderr.strip() or probe.stdout.strip())[-300:]
            raise ValueError(f"isolated runtime-contract probe failed: {detail}")
        runtime_contract = json.loads(probe.stdout)
        if runtime_contract.get("enabled") is not False:
            raise ValueError("Agent runtime must be disabled by default")
        if runtime_contract.get("expectedVersion") != "0.1.1-rc.2":
            raise ValueError("Agent runtime DSH version contract drifted")
        if runtime_contract.get("signals") != list(expected_signals):
            raise ValueError("Agent runtime signal registry drifted")
        if runtime_contract.get("severities") != {
                signal_id: severity
                for signal_id, (_risk_id, severity) in expected_signals.items()
        }:
            raise ValueError("Agent runtime signal severity contract drifted")
        if (runtime_contract.get("scorePolicy") != "1.1.0"
                or runtime_contract.get("confidencePolicy") != "1.4.0"):
            raise ValueError("Agent runtime policy versions drifted")
        if runtime_contract.get("dynamicCheckCount") != 1:
            raise ValueError("agent_instruction.runtime check contract drifted")

        mappings_doc = json.loads(_read_text(
            REPO / "standards" / "detector_mappings.json"))
        mappings = mappings_doc.get("detectors")
        if not isinstance(mappings, list) or len(mappings) != 156:
            raise ValueError(
                f"expected 156 mappings, observed "
                f"{len(mappings) if isinstance(mappings, list) else 'invalid'}")
        runtime_mappings = {
            row.get("detectorId"): row
            for row in mappings
            if row.get("detectorType") == "agent_runtime_signal"
        }
        if set(runtime_mappings) != set(expected_signals):
            raise ValueError("Agent runtime detector mappings drifted")
        for signal_id, (risk_id, _severity) in expected_signals.items():
            row = runtime_mappings[signal_id]
            if (row.get("riskIds") != [risk_id]
                    or row.get("contribution") != "signal"):
                raise ValueError(f"mapping drift for {signal_id}")

        risks_doc = json.loads(_read_text(REPO / "standards" / "risks.json"))
        risks = risks_doc.get("risks")
        if not isinstance(risks, list):
            raise ValueError("standards/risks.json has invalid risks")
        breadth = Counter(
            (risk.get("currentCoverage") or {}).get("V2_agent_runtime")
            for risk in risks
        )
        expected_breadth = {
            "none": 42,
            "signal": 4,
            "partial": 0,
            "substantial": 0,
            "evaluated": 0,
        }
        if any(breadth.get(level, 0) != count
               for level, count in expected_breadth.items()):
            raise ValueError(
                f"V2_agent_runtime breadth drifted: {dict(breadth)}")

        for lock_name in ("requirements.lock", "requirements-dev.lock"):
            if "@deepseek-ai/dsh" in _read_text(REPO / lock_name).lower():
                raise ValueError(f"DSH must not be a Python lock dependency: {lock_name}")

        third_party = " ".join(
            _read_text(REPO / "THIRD_PARTY_LICENSES.md").split())
        third_party_tokens = (
            "@deepseek-ai/dsh | 0.1.1-rc.2 | MIT",
            "https://github.com/deepseek-ai/DeepSeek-Harness",
            "optional external",
            "not vendored",
            "not auto-installed",
            "not a Python dependency",
            "two entry files",
            "adjacent npm dependency closure",
        )
        missing = [token for token in third_party_tokens
                   if token not in third_party]
        if missing:
            raise ValueError(
                "THIRD_PARTY Agent runtime disclosure missing: "
                + ", ".join(missing))

        readme = " ".join(_read_text(REPO / "README.md").split())
        disclosure_tokens = (
            "Agent-instruction Harness (CLI-only; OFF by default)",
            "synthetic/no-side-effect tools",
            "real Provider network egress",
            "Harness is not an OS, process, or network security sandbox.",
            "No real Provider/model/scenario E2E",
            "two entry files",
            "adjacent npm dependency closure",
            "`setsid()`",
            "outer container or microVM",
            "destination-allowlisted egress",
            "only an API-key environment-variable name, never the key value",
            "A clean completed run is not a safety proof.",
        )
        missing = [token for token in disclosure_tokens if token not in readme]
        if missing:
            raise ValueError(
                "README Agent runtime disclosure missing: "
                + ", ".join(missing))

        architecture = " ".join(
            _read_text(REPO / "docs" / "ARCHITECTURE.md").split()
        )
        fixed_cli_authority = (
            "The plugin, model-facing tool catalog, Cordis permission patch, "
            "output/trace ceilings, and temporary roots are fixed or generated "
            "by Verity on the CLI path; the CLI caller cannot replace them."
        )
        if fixed_cli_authority not in architecture:
            raise ValueError(
                "ARCHITECTURE must distinguish caller-selected runtime "
                "values from Verity-fixed CLI authority"
            )
        if (
            "scenarios, budgets, plugin, tools, permissions, and temporary roots"
            in architecture
        ):
            raise ValueError(
                "ARCHITECTURE incorrectly grants fixed Harness authority "
                "to the CLI caller"
            )

        package_scope = " ".join(
            _read_text(REPO / "src" / "verity" / "__init__.py").split()
        )
        current_scope = (
            "The deterministic engineering-preview scope is a release "
            "candidate; optional semantic and dynamic stages remain "
            "experimental."
        )
        if current_scope not in package_scope:
            raise ValueError("verity package scope summary is stale")
        if (
            "V1 release decision remains ``not_ready``" in package_scope
            or "are not implemented" in package_scope
        ):
            raise ValueError("verity package scope retains superseded status")

        snapshot_boundary_tokens = {
            "docs/ARCHITECTURE.md": (
                "Version and scenario launches execute the private snapshots, "
                "not the caller paths.",
                "that closure remains unpinned and unauthenticated by the two "
                "entry hashes.",
                "At most two Skill-loader result markers are written; exactly "
                "one successful marker is required, otherwise parsing fails closed.",
            ),
            "docs/project-explainer.html": (
                "private snapshot while the same bytes are hashed; version and "
                "scenario launches execute only those snapshots.",
                "that closure remains unpinned and unauthenticated by the two "
                "entry hashes.",
                "At most two Skill-loader result markers are written; exactly "
                "one successful marker is required, otherwise parsing fails closed.",
            ),
            "docs/verity-manual-zh.html": (
                "同一批字节一边计算哈希，一边写入仅所有者可读的私有快照。",
                "该闭包仍未固定、未认证，不在两个入口哈希的认证范围内。",
                "Skill-loader 结果标记最多写入两个；必须恰好有一个成功标记，"
                "否则解析会失败关闭。",
            ),
            "docs/PROGRESS.md": (
                "private snapshot while the same bytes are hashed. Version and "
                "scenarios run only from those snapshots.",
                "The adjacent npm closure linked for module resolution remains "
                "unpinned and unauthenticated.",
                "At most two Skill-loader result markers are written; exactly "
                "one successful marker is required, otherwise parsing fails closed.",
            ),
            "plans/ACTIVE.md": (
                "private snapshot while the same bytes are hashed. Version and "
                "scenarios run only from those snapshots.",
                "npm closure linked for module resolution remains unpinned and "
                "unauthenticated.",
                "At most two Skill-loader result markers are written; exactly "
                "one successful marker is required, otherwise parsing fails closed.",
            ),
        }
        for relative_path, required_tokens in snapshot_boundary_tokens.items():
            document = " ".join(_read_text(REPO / relative_path).split())
            missing = [token for token in required_tokens if token not in document]
            if missing:
                raise ValueError(
                    f"{relative_path} runtime snapshot boundary missing: "
                    + ", ".join(missing)
                )
    except Exception as exc:
        rep.append_fail("agent_runtime_release_contract", str(exc)[:500])
        return
    rep.append_ok(
        "agent_runtime_release_contract",
        "DSH 0.1.1-rc.2 disabled by default; 4 exact signals; "
        "156 mappings; V2_agent_runtime none=42 signal=4; disclosures pinned",
    )


def check_closure_policy_release_contract(rep: VerifyReport) -> None:
    """Bind current release documentation to the machine closure policy."""
    try:
        # Read the selected checkout in a fresh interpreter. Scratch-repository
        # corruption tests must not be masked by a cached verity.closure import.
        probe = _run_isolated_repo_probe(r'''
from verity.closure import CLOSURE_POLICY_VERSION
print(CLOSURE_POLICY_VERSION)
''')
        if probe.returncode != 0:
            detail = (probe.stderr.strip() or probe.stdout.strip())[-300:]
            raise ValueError(f"isolated closure-policy probe failed: {detail}")
        machine_version = probe.stdout.strip()
        if not re.fullmatch(r"\d+\.\d+\.\d+", machine_version):
            raise ValueError(
                f"invalid machine closure policy version: {machine_version!r}")

        progress = _read_text(REPO / "docs" / "PROGRESS.md")
        current_progress, summary_delimiter, _history = progress.partition(
            "\n---\n")
        if not summary_delimiter:
            raise ValueError(
                "docs/PROGRESS.md is missing the current-summary delimiter")

        current_claims = {
            "README.md": (
                _read_text(REPO / "README.md"),
                r"Closure policy v(?P<version>\d+\.\d+\.\d+)\s+scopes",
            ),
            "docs/PROGRESS.md": (
                current_progress,
                r"under closure policy \*\*v"
                r"(?P<version>\d+\.\d+\.\d+)\*\*",
            ),
            "docs/ARCHITECTURE.md": (
                _read_text(REPO / "docs" / "ARCHITECTURE.md"),
                r"`verity\.closure` \(policy v"
                r"(?P<version>\d+\.\d+\.\d+)\) computes",
            ),
            "docs/project-explainer.html": (
                _read_text(REPO / "docs" / "project-explainer.html"),
                r"Closure policy v(?P<version>\d+\.\d+\.\d+)\s*·\s*"
                r"no evaluated-accuracy claim",
            ),
            "docs/verity-manual-zh.html": (
                _read_text(REPO / "docs" / "verity-manual-zh.html"),
                r"收尾政策 v(?P<version>\d+\.\d+\.\d+)\s*·\s*"
                r"不含精度评测结论",
            ),
            "evals/README.md": (
                _read_text(REPO / "evals" / "README.md"),
                r"under closure policy v"
                r"(?P<version>\d+\.\d+\.\d+)\.",
            ),
        }
        for relative_path, (document, pattern) in current_claims.items():
            versions = [
                match.group("version")
                for match in re.finditer(pattern, document)
            ]
            if len(versions) != 1:
                raise ValueError(
                    f"{relative_path}: expected exactly one current closure "
                    f"policy version claim, observed {len(versions)}")
            observed = versions[0]
            if observed != machine_version:
                raise ValueError(
                    f"{relative_path}: closure policy version drift; "
                    f"expected {machine_version}, observed {observed}")
    except Exception as exc:
        rep.append_fail("closure_policy_release_contract", str(exc)[:500])
        return
    rep.append_ok(
        "closure_policy_release_contract",
        f"policy={machine_version}; 6 current release docs match; "
        "PROGRESS history excluded",
    )


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
        # The standard handover prompt in AGENTS.md §9 intentionally
        # names the local path in a fenced code block. That is the
        # SSOT for the prompt and is user-visible on purpose.
        allow = str(p).endswith("AGENTS.md")
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
    if "docs/PROGRESS.md" not in readme:
        rep.append_fail("pyproject_and_readme_links",
                        "README.md must link to docs/PROGRESS.md")
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


def check_detection_standards(rep: VerifyReport) -> None:
    """Validate authoritative-source taxonomy and exact runtime mapping."""
    try:
        sys.path.insert(0, str(REPO / "src"))
        from verity.standards import (load_detector_candidates,
                                      load_detector_mappings, load_risks,
                                      load_sources,
                                      validate_runtime_detector_coverage)
        sources = load_sources()
        risks = load_risks(sources)
        mappings = load_detector_mappings(risks)
        candidates = load_detector_candidates(sources, risks)
        validate_runtime_detector_coverage()
        runtime_breadth = Counter(
            risk["currentCoverage"]["V2_agent_runtime"]
            for risk in risks.values()
        )
    except Exception as exc:
        rep.append_fail("detection_standards", str(exc)[:500])
        return
    rep.append_ok(
        "detection_standards",
        f"{len(sources)} sources; {len(risks)} risks; "
        f"{len(candidates)} candidates; {len(mappings)} mappings; "
        f"V2_agent_runtime none={runtime_breadth['none']} "
        f"signal={runtime_breadth['signal']}; runtime mapped")


def check_corpus_baselines(rep: VerifyReport) -> None:
    """Re-run the offline corpus and reject answer/report drift."""
    proc = subprocess.run(
        [sys.executable, "tools/run_corpus.py", "--check"], cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr.strip() or proc.stdout.strip())[-500:]
        rep.append_fail("corpus_baselines", detail)
        return
    rep.append_ok("corpus_baselines",
                  "84 L0 cases + 82 semantic contract replays reproducible")


def check_v1_closure_baseline(rep: VerifyReport) -> None:
    """Recompute the binary V1 release decision without network/model calls."""
    proc = subprocess.run(
        [sys.executable, "tools/run_v1_closure.py", "--check"], cwd=REPO,
        env={**os.environ, "PYTHONPATH": str(REPO / "src")},
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr.strip() or proc.stdout.strip())[-500:]
        rep.append_fail("v1_closure_baseline", detail)
        return
    try:
        import json
        closure = json.loads(_read_text(REPO / "evals/reports/v1-closure.json"))
        if closure.get("decision") not in {"release_candidate", "not_ready"}:
            raise ValueError("decision is not binary")
        if closure.get("decision") == "not_ready" and not closure.get("blockers"):
            raise ValueError("not_ready has no explicit blockers")
    except Exception as exc:
        rep.append_fail("v1_closure_baseline", str(exc)[:500])
        return
    rep.append_ok(
        "v1_closure_baseline",
        f"reproducible; decision={closure['decision']}; "
        f"engineeringReady={closure.get('engineeringReady')}")


def check_independent_review_evidence(rep: VerifyReport) -> None:
    """Bind reviewed labels to current payloads and preserve sealed isolation."""
    try:
        sys.path.insert(0, str(REPO / "src"))
        import json
        from verity.corpus import load_manifest
        from verity.review_evidence import load_independent_ai_attestation
        from verity.semantic_quality import load_semantic_quality_manifest
        attestation = load_independent_ai_attestation()
        l0 = load_manifest()
        semantic = load_semantic_quality_manifest()
        invalidation = json.loads(_read_text(
            REPO / "evals/reviews/semantic-selection-v1-invalidation.json"))
        l0_reviewed = sum(c["labelStatus"] == "independent_ai_review"
                         for c in l0["cases"])
        l0_provisional = sum(c["labelStatus"] == "provisional_single_review"
                             for c in l0["cases"])
        # The merged attestation (frozen Round-22 + supplemental round files)
        # must cover every L0 case currently marked independent_ai_review.
        # The frozen Round-22 subset is always exactly 54; total grows when new
        # supplemental rounds complete (e.g. Round-67 provisional review).
        # We do not re-constrain to 54 here -- that would prevent future rounds
        # from advancing. We DO verify l0_reviewed + l0_provisional == total.
        if (l0_reviewed < 26  # can only grow, never shrink
                or l0_reviewed != sum(
                    1 for c in l0["cases"]
                    if c["labelStatus"] == "independent_ai_review")
                or l0_reviewed + l0_provisional != len(l0["cases"])
                or len(attestation) < l0_reviewed  # attestation covers >= reviewed
                or sum(c["labelStatus"] == "independent_ai_review"
                       for c in semantic["cases"]) != 28
                or sum(c["labelStatus"] == "provisional_single_review"
                       for c in semantic["cases"]) != 14
                or semantic["protocolVersion"] != "2.0.0"
                or invalidation.get("status")
                    != "invalidated_by_label_adjudication"
                or invalidation.get("sealedTestConsumed") is not False):
            raise ValueError("independent review/protocol state mismatch")
        new_l0_reviewed = l0_reviewed - 26  # newly reviewed beyond Round-22 set
    except Exception as exc:
        rep.append_fail("independent_review_evidence", str(exc)[:500])
        return
    rep.append_ok(
        "independent_review_evidence",
        f"{l0_reviewed} AI-reviewed current payloads; "
        f"{new_l0_reviewed} new L0 case(s) promoted this session; "
        f"{l0_provisional} new L0 case(s) "
        "provisional pending review; 14 sealed labels provisional; "
        "v1 Selection invalidated")


def check_semantic_quality_protocol(rep: VerifyReport) -> None:
    """Validate split isolation and deterministic seed eligibility offline."""
    try:
        sys.path.insert(0, str(REPO / "src"))
        from verity.semantic_quality import (
            load_semantic_quality_manifest,
            validate_semantic_quality_seed_coverage)
        manifest = load_semantic_quality_manifest()
        checked = validate_semantic_quality_seed_coverage()
    except Exception as exc:
        rep.append_fail("semantic_quality_protocol", str(exc)[:500])
        return
    counts = {split: sum(c["split"] == split for c in manifest["cases"])
              for split in ("calibration", "selection", "test")}
    rep.append_ok(
        "semantic_quality_protocol",
        f"{checked} synthetic eligible cases; splits={counts}; no model called")


def check_semantic_comparison_protocol(rep: VerifyReport) -> None:
    """Validate answer-hidden v3 calibration and claim prerequisites."""
    try:
        sys.path.insert(0, str(REPO / "src"))
        from verity.semantic_benchmark import (
            butler_breadth_summary,
            build_semantic_comparison_packet,
            compare_semantic_systems,
            load_butler_crosswalk,
            load_semantic_comparison_manifest,
            validate_semantic_comparison_seed_coverage)
        manifest = load_semantic_comparison_manifest()
        checked = validate_semantic_comparison_seed_coverage()
        breadth = butler_breadth_summary(load_butler_crosswalk())
        verity_packet, verity_map = build_semantic_comparison_packet(
            system_id="verity", seed="verify-repo-verity-seed")
        butler_packet, butler_map = build_semantic_comparison_packet(
            system_id="butler", seed="verify-repo-butler-seed")

        def observations(packet):
            return {
                "schemaVersion": 1,
                "protocolId": packet["protocolId"],
                "protocolVersion": packet["protocolVersion"],
                "systemId": packet["systemId"],
                "configurationFingerprint": "0" * 64,
                "corpusFingerprint": packet["corpusFingerprint"],
                "repetitions": 2,
                "observations": [
                    {"itemId": item["itemId"],
                     "runs": ["inconclusive", "inconclusive"]}
                    for item in packet["items"]
                ],
            }
        report = compare_semantic_systems(
            verity_packet=verity_packet, verity_mapping=verity_map,
            verity_observations=observations(verity_packet),
            butler_packet=butler_packet, butler_mapping=butler_map,
            butler_observations=observations(butler_packet),
            label_attestation=None)
        if (len(manifest["cases"]) != 164 or checked != 164
                or breadth.get("inventoryCount") != 45
                or breadth.get("openGapCount") != 0
                or breadth.get("claimReady") is not True
                or report.get("status") != "not_eligible"
                or "labels_missing"
                not in report.get("reasonCodes", [])
                or "butler_breadth_gaps_open"
                in report.get("reasonCodes", [])
                or report.get("claim") is not None):
            raise ValueError("semantic comparison development gate mismatch")
    except Exception as exc:
        rep.append_fail("semantic_comparison_protocol", str(exc)[:500])
        return
    rep.append_ok(
        "semantic_comparison_protocol",
        "164 fresh paired calibration cases; Butler inventory=45 with "
        "0 open breadth gaps; labels provisional; superiority claim "
        "still refused; no model called")


def check_scoring_policy(rep: VerifyReport) -> None:
    """Smoke-test user-facing score invariants independently of UI tests."""
    try:
        sys.path.insert(0, str(REPO / "src"))
        from verity.intake import intake_text
        from verity.report import review_to_dict
        from verity.review import ReviewInputs, run_review
        from verity.scoring import POLICY_VERSION, compute_score
        snap, data = intake_text("Summarize this text.")
        safe = review_to_dict(run_review(ReviewInputs("prompt", snap, data)))
        snap, data = intake_text(
            'permissions: ["*"]', prompt_kind="system_prompt")
        high = review_to_dict(run_review(ReviewInputs("prompt", snap, data)))
        unavailable = compute_score({"coverage": {"status": "insufficient"},
                                     "findings": [], "ruleMatches": []})
        if (safe["score"]["value"] != 100
                or safe["reviewConfidence"]["grade"] == "A"
                or high["score"]["highestSeverity"] not in {"high", "critical"}
                or high["score"]["value"] > 59
                or unavailable["status"] != "unavailable"
                or unavailable["value"] is not None):
            raise ValueError("score invariant mismatch")
    except Exception as exc:
        rep.append_fail("scoring_policy", str(exc)[:500])
        return
    rep.append_ok("scoring_policy",
                  f"policy={POLICY_VERSION}; safe=100; high<=59; gaps=unavailable")


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
    check_progress_verified_block(rep)
    check_capability_matrix_matches_runtime(rep)
    check_agent_runtime_release_contract(rep)
    check_closure_policy_release_contract(rep)
    check_no_absolute_paths_in_docs(rep)
    check_no_secret_literals(rep)
    check_pyproject_and_readme_links(rep)
    check_git_ignored(rep)
    check_ci_workflow_shape(rep)
    check_detection_standards(rep)
    check_corpus_baselines(rep)
    check_v1_closure_baseline(rep)
    check_independent_review_evidence(rep)
    check_semantic_quality_protocol(rep)
    check_semantic_comparison_protocol(rep)
    check_scoring_policy(rep)
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
