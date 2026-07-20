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
  3  ``gate=coverage_block`` Coverage is insufficient, OR an explicitly
                             requested semantic review did not complete, AND no
                             High/Critical Finding is present. Chosen instead
                             of 2 so it does not collide with argparse's
                             usage-error exit 2.
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
from .history import HistoryError, HistoryStore
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
    candidate_generator = None
    validator = None
    if args.semantic:
        from .semantic import (JsonCandidateGeneratorProvider,
                               JsonValidatorProvider, ProviderConfig,
                               ProviderCredentials, SemanticConfig)
        provider_values = (
            args.semantic_generator_url,
            args.semantic_generator_model,
            args.semantic_validator_url,
            args.semantic_validator_model,
        )
        has_any_provider_value = any(provider_values)
        has_complete_provider_config = all(provider_values)
        if has_any_provider_value and not has_complete_provider_config:
            print("invalid --semantic configuration: generator and validator "
                  "URL/model settings must be provided together", file=sys.stderr)
            return 2
        try:
            if has_complete_provider_config:
                gen_cfg = ProviderConfig(
                    role="candidate_generator",
                    provider_id="json_http",
                    model_id=args.semantic_generator_model,
                    base_url=args.semantic_generator_url,
                    credentials=ProviderCredentials(
                        api_key_env=args.semantic_generator_api_key_env),
                    timeout_seconds=args.semantic_timeout,
                )
                val_cfg = ProviderConfig(
                    role="validator",
                    provider_id="json_http",
                    model_id=args.semantic_validator_model,
                    base_url=args.semantic_validator_url,
                    credentials=ProviderCredentials(
                        api_key_env=args.semantic_validator_api_key_env),
                    timeout_seconds=args.semantic_timeout,
                )
                sem_cfg = SemanticConfig(
                    enabled=True,
                    egress_policy=args.egress_policy,
                    provider_config={
                        "candidate_generator": gen_cfg,
                        "validator": val_cfg,
                    },
                )
                # Deliberately distinct role-bound objects, even if the
                # endpoint and model happen to be the same.
                candidate_generator = JsonCandidateGeneratorProvider(gen_cfg)
                validator = JsonValidatorProvider(val_cfg)
            else:
                # Explicit opt-in without trusted Provider config remains
                # a visible provider_not_configured result.
                sem_cfg = SemanticConfig(enabled=True,
                                         egress_policy=args.egress_policy)
        except ValueError as exc:
            print(f"invalid --semantic configuration: {exc}", file=sys.stderr)
            return 2
    review = run_review(ReviewInputs(engine=args.engine, snapshot=snap,
                                     file_bytes=byts, profile=args.profile,
                                     semantic_config=sem_cfg),
                        candidate_generator=candidate_generator,
                        validator=validator)

    out_dir = Path(args.out) if args.out else Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    d = review_to_dict(review)
    (out_dir / "report.json").write_text(to_json(review), encoding="utf-8")
    (out_dir / "report.html").write_text(to_html(review), encoding="utf-8")
    (out_dir / "report.sarif").write_text(to_sarif_json(d), encoding="utf-8")

    n_findings = len(review.findings)
    n_high = sum(1 for f in review.findings if f.severity in ("high", "critical"))
    coverage_ok = review.coverage.status == "sufficient"
    semantic_status = ((review.semantic or {}).get("status")
                       if args.semantic else "not_enabled")
    semantic_ok = not args.semantic or semantic_status == "completed"

    # Findings gate wins over coverage/semantic gates. An explicitly
    # requested semantic review that fails must never silently return a
    # static-only pass.
    if n_high:
        gate = "findings_block"
        exit_code = 1
    elif not coverage_ok or not semantic_ok:
        gate = "coverage_block"
        exit_code = 3
    else:
        gate = "pass"
        exit_code = 0

    print(f"engine={args.engine} snapshot={snap.snapshotId} "
          f"findings={n_findings} high_or_critical={n_high} "
          f"coverage={review.coverage.status} semantic={semantic_status} "
          f"gate={gate}")
    print(f"wrote {out_dir/'report.json'}, {out_dir/'report.html'}, {out_dir/'report.sarif'}")
    return exit_code


def _cmd_project(args: argparse.Namespace) -> int:
    try:
        store=HistoryStore(args.data_dir)
        if args.project_cmd=="create":
            p=store.create_project(args.name,args.alias); print(f'created project {p["displayName"]} alias={p.get("alias") or "-"}')
        elif args.project_cmd=="list":
            for p in store.list_projects(): print(f'{p["displayName"]}\t{p.get("alias") or "-"}\t{len(p["versionIds"])} versions')
        elif args.project_cmd == "review":
            p = store.resolve(args.project)
            snap, byts = intake_directory(
                args.input_dir, artifact_id=p["artifactId"],
                budget=IntakeBudget())
            review = run_review(ReviewInputs(
                "skill", snap, byts, profile=args.profile))
            rec = store.add_review(
                p["artifactId"], review, profile=args.profile)
            high = sum(1 for f in review.findings
                       if f.severity in ("high", "critical"))
            if high:
                gate, exit_code = "findings_block", 1
            elif review.coverage.status != "sufficient":
                gate, exit_code = "coverage_block", 3
            else:
                gate, exit_code = "pass", 0
            print(f'recorded review={rec["reviewId"]} '
                  f'coverage={rec["coverage"]["status"]} '
                  f'high_or_critical={high} gate={gate}')
            return exit_code
        elif args.project_cmd=="diff":
            print(json.dumps(store.diff(args.project,args.previous,args.current),ensure_ascii=False,indent=2))
        return 0
    except (HistoryError,IntakeError) as e:
        print(f"project error: {e}",file=sys.stderr); return 3


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
    provider = pr.add_argument_group(
        "trusted semantic Provider",
        "All four URL/model flags are required together. Credentials are "
        "read only from the named environment variables; do not pass a key "
        "on the command line. The reviewed artifact cannot set these values.")
    provider.add_argument("--semantic-generator-url")
    provider.add_argument("--semantic-generator-model")
    provider.add_argument("--semantic-generator-api-key-env")
    provider.add_argument("--semantic-validator-url")
    provider.add_argument("--semantic-validator-model")
    provider.add_argument("--semantic-validator-api-key-env")
    provider.add_argument("--semantic-timeout", type=float, default=30.0)
    pr.set_defaults(func=_cmd_review)

    pp=sub.add_parser("project",help="Trusted local Skill project history")
    pp.add_argument("--data-dir")
    psub=pp.add_subparsers(dest="project_cmd",required=True)
    pc=psub.add_parser("create"); pc.add_argument("--name",required=True); pc.add_argument("--alias")
    psub.add_parser("list")
    prj=psub.add_parser("review"); prj.add_argument("--project",required=True); prj.add_argument("--input-dir",required=True); prj.add_argument("--profile",choices=["standard","minimal"],default="standard")
    pd=psub.add_parser("diff"); pd.add_argument("--project",required=True); pd.add_argument("--previous"); pd.add_argument("--current")
    pp.set_defaults(func=_cmd_project)

    ps = sub.add_parser("export-schema", help="Export JSON Schema (Draft 2020-12)")
    ps.add_argument("--out")
    ps.set_defaults(func=_cmd_export_schema)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
