"""Verity CLI.

Usage:
  verity review --engine prompt --text "..." [--out out/]
  verity review --engine prompt --input-file path.txt [--out out/]
  verity review --engine skill --input-dir path/ [--out out/]
  verity export-schema [--out out/schema.json]

V1 read-only: does not execute or install anything from the target.

Exit codes for ``review``:

  0  ``gate=pass``           coverage is sufficient AND no High/Critical findings.
                             Medium/Low findings do NOT block (documented policy;
                             use downstream tooling to enforce stricter gates).
  1  ``gate=findings_block`` at least one High/Critical Finding is present.
                             Wins over the coverage gate: if both are triggered
                             the exit code is 1 (High/Critical is the stricter
                             signal a CI needs to surface first).
  3  ``gate=coverage_block`` Coverage is insufficient AND no High/Critical
                             Finding is present. Chosen instead of 2 so it does
                             not collide with argparse's usage-error exit 2.
  2  reserved by argparse for CLI usage errors (POSIX convention).

A one-line ``gate=...`` marker is printed on stdout for both CI and human
readers. Coverage-insufficient runs NEVER exit 0.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .intake import IntakeBudget, IntakeError, intake_directory, intake_text
from .report import review_to_dict, to_html, to_json
from .review import ReviewInputs, run_review
from .sarif import to_sarif_json
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

    sem_cfg = None
    if args.semantic:
        # Round 8: opt-in only; no real Provider in this repo. When the
        # user passes --semantic without configuring a Provider (out of
        # scope here) the semantic pipeline will honestly emit
        # provider_not_configured. That is the intended behaviour.
        from .semantic import SemanticConfig
        try:
            sem_cfg = SemanticConfig(enabled=True,
                                     egress_policy=args.egress_policy)
        except ValueError as exc:
            print(f"invalid --semantic configuration: {exc}", file=sys.stderr)
            return 2
    review = run_review(ReviewInputs(engine=args.engine, snapshot=snap,
                                     file_bytes=byts, profile=args.profile,
                                     semantic_config=sem_cfg))

    out_dir = Path(args.out) if args.out else Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    d = review_to_dict(review)
    (out_dir / "report.json").write_text(to_json(review), encoding="utf-8")
    (out_dir / "report.html").write_text(to_html(review), encoding="utf-8")
    (out_dir / "report.sarif").write_text(to_sarif_json(d), encoding="utf-8")

    n_findings = len(review.findings)
    n_high = sum(1 for f in review.findings if f.severity in ("high", "critical"))
    coverage_ok = review.coverage.status == "sufficient"

    # Findings gate wins over coverage gate (see module docstring).
    if n_high:
        gate = "findings_block"
        exit_code = 1
    elif not coverage_ok:
        gate = "coverage_block"
        exit_code = 3
    else:
        gate = "pass"
        exit_code = 0

    print(f"engine={args.engine} snapshot={snap.snapshotId} "
          f"findings={n_findings} high_or_critical={n_high} "
          f"coverage={review.coverage.status} gate={gate}")
    print(f"wrote {out_dir/'report.json'}, {out_dir/'report.html'}, {out_dir/'report.sarif'}")
    return exit_code


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
    pr.add_argument("--profile", choices=["standard", "minimal"],
                    default="standard",
                    help=("Skill-engine review profile. `standard` requires "
                          "gitleaks and marks Coverage insufficient when "
                          "unavailable. `minimal` explicitly opts out of "
                          "secret scanning and the report says so."))
    pr.add_argument("--out", default="out")
    pr.add_argument("--semantic", action="store_true",
                    help=("Opt into the experimental semantic review "
                          "(default OFF). Requires a configured Provider; "
                          "without one, the run honestly reports "
                          "provider_not_configured."))
    pr.add_argument("--egress-policy",
                    choices=["off", "metadata_only", "redacted_evidence"],
                    default="metadata_only",
                    help=("Data-egress policy for semantic Provider calls. "
                          "Only used when --semantic is set. "
                          "'redacted_evidence' includes short evidence "
                          "snippets; 'metadata_only' sends locations only."))
    pr.set_defaults(func=_cmd_review)

    ps = sub.add_parser("export-schema", help="Export JSON Schema (Draft 2020-12)")
    ps.add_argument("--out")
    ps.set_defaults(func=_cmd_export_schema)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
