"""Verity CLI.

Usage:
  verity review --engine prompt --text "..." [--out out/]
  verity review --engine prompt --input-file path.txt [--out out/]
  verity review --engine skill --input-dir path/ [--out out/]
  verity export-schema [--out out/schema.json]

V1 read-only: does not execute or install anything from the target.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .intake import IntakeBudget, IntakeError, intake_directory, intake_text
from .report import to_html, to_json
from .review import ReviewInputs, run_review
from .schema import export_schema


def _cmd_review(args: argparse.Namespace) -> int:
    if args.engine == "prompt":
        if args.input_dir:
            print("prompt engine expects --text or --input-file, not --input-dir", file=sys.stderr)
            return 2
        if args.text is not None:
            text = args.text
        elif args.input_file:
            text = Path(args.input_file).read_text(encoding="utf-8")
        else:
            print("prompt engine requires --text or --input-file", file=sys.stderr)
            return 2
        snap, byts = intake_text(text, prompt_kind=args.prompt_kind)
    else:
        if not args.input_dir:
            print("skill engine requires --input-dir", file=sys.stderr)
            return 2
        try:
            snap, byts = intake_directory(args.input_dir, budget=IntakeBudget())
        except IntakeError as e:
            print(f"intake error: {e}", file=sys.stderr)
            return 3

    review = run_review(ReviewInputs(engine=args.engine, snapshot=snap, file_bytes=byts))

    out_dir = Path(args.out) if args.out else Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(to_json(review), encoding="utf-8")
    (out_dir / "report.html").write_text(to_html(review), encoding="utf-8")

    n_findings = len(review.findings)
    n_high = sum(1 for f in review.findings if f.severity in ("high", "critical"))
    print(f"engine={args.engine} snapshot={snap.snapshotId} findings={n_findings} high_or_critical={n_high} coverage={review.coverage.status}")
    print(f"wrote {out_dir/'report.json'} and {out_dir/'report.html'}")
    # Exit non-zero when there are high/critical findings so CI can use it later.
    return 1 if n_high else 0


def _cmd_export_schema(args: argparse.Namespace) -> int:
    text = json.dumps(export_schema(), indent=2, ensure_ascii=False, sort_keys=True)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        sys.stdout.write(text + "\n")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="verity")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("review", help="Run a Phase 0 read-only review")
    pr.add_argument("--engine", choices=["prompt", "skill"], required=True)
    pr.add_argument("--prompt-kind", choices=["user_prompt", "system_prompt"],
                    default="user_prompt",
                    help="For --engine prompt: controlled prompt-kind enum.")
    pr.add_argument("--text")
    pr.add_argument("--input-file")
    pr.add_argument("--input-dir")
    pr.add_argument("--out", default="out")
    pr.set_defaults(func=_cmd_review)

    ps = sub.add_parser("export-schema", help="Export JSON Schema (Draft 2020-12)")
    ps.add_argument("--out")
    ps.set_defaults(func=_cmd_export_schema)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
