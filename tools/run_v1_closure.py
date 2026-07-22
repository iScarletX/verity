#!/usr/bin/env python3
"""Recompute the offline V1 closure decision and check/write its baseline.

This command never calls a Provider and never consumes the sealed test. The
engineering booleans are backed by the named acceptance suites below; the
repository gate runs the complete pytest suite separately in the same run.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from verity.closure import evaluate_v1_closure

REPORT = REPO / "evals" / "reports" / "v1-closure.json"

# These evidence files are part of the full pytest gate. They intentionally
# cover overlapping boundaries; closure is a cross-format/product judgment,
# not a new accuracy metric.
ENGINEERING_EVIDENCE = {
    "prompt_web_cli": ["tests/test_acceptance_19.py", "tests/test_web_mvp.py"],
    "skill_web_cli": ["tests/test_round12.py", "tests/test_web_mvp.py"],
    "json_html_sarif": ["tests/test_acceptance_19.py", "tests/test_round20_closure.py"],
    "coverage_failure": ["tests/test_acceptance_19.py", "tests/test_round5_hotfix.py"],
    "score_confidence_remediation": ["tests/test_round19_scoring.py", "tests/test_round19_web_score.py"],
    "history_v1_v2_diff": ["tests/test_round19_history_score.py", "tests/test_round12.py"],
    "security_boundaries": ["tests/test_semantic.py", "tests/test_web_mvp.py"],
    "install_start_preflight": ["tests/test_web_mvp.py", "tests/test_verify_repo.py"],
    "tests_and_ci": ["tools/verify_repo.py", ".github/workflows/ci.yml"],
}


def _report() -> dict:
    missing = sorted(path for paths in ENGINEERING_EVIDENCE.values()
                     for path in paths if not (REPO / path).is_file())
    if missing:
        raise ValueError("missing closure evidence: " + ", ".join(missing))
    report = evaluate_v1_closure(
        engineering_checks={name: True for name in ENGINEERING_EVIDENCE},
        # Deliberately false until an approved, locally retained real-model
        # report is reviewed. Closure must not infer either fact from a Key.
        real_model_report_present=False,
        sealed_test_consumed=False,
    )
    report["engineeringEvidence"] = dict(sorted(ENGINEERING_EVIDENCE.items()))
    return report


def _canonical(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2,
                      sort_keys=True) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if not args.write and not args.check:
        parser.error("choose --write or --check")
    expected = _canonical(_report())
    if args.write:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(expected, encoding="utf-8")
        print(f"wrote {REPORT.relative_to(REPO)}")
    if args.check:
        actual = REPORT.read_text(encoding="utf-8") if REPORT.is_file() else ""
        if actual != expected:
            print("V1 closure baseline drift", file=sys.stderr)
            return 1
        decision = json.loads(actual)["decision"]
        print(f"V1 closure baseline: reproducible; decision={decision}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
