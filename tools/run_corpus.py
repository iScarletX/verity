#!/usr/bin/env python3
"""Run the offline Round-15 corpus and optionally verify committed baselines."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from verity.corpus import (canonical_report_json, evaluate,
                           evaluate_semantic_replay)

REPORT_DIR = REPO / "evals" / "reports"
L0_REPORT = REPORT_DIR / "corpus-v1-l0.json"
SEMANTIC_REPORT = REPORT_DIR / "corpus-v1-semantic-contract.json"


def _reports():
    return {
        L0_REPORT: canonical_report_json(evaluate()),
        SEMANTIC_REPORT: canonical_report_json(evaluate_semantic_replay()),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true",
                        help="write the reproducible baseline reports")
    parser.add_argument("--check", action="store_true",
                        help="fail if committed reports differ")
    args = parser.parse_args(argv)
    if not args.write and not args.check:
        parser.error("choose --write or --check")
    reports = _reports()
    if args.write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        for path, text in reports.items():
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(REPO)}")
    if args.check:
        failures = []
        for path, expected in reports.items():
            actual = path.read_text(encoding="utf-8") if path.is_file() else ""
            if actual != expected:
                failures.append(str(path.relative_to(REPO)))
        if failures:
            print("corpus baseline drift: " + ", ".join(failures),
                  file=sys.stderr)
            return 1
        print("corpus baseline reports: reproducible")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
