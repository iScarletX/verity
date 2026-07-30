#!/usr/bin/env python3
"""Run independent multi-model review over the Corpus's
`provisional_single_review` cases (disjoint from the frozen 54-item
blind_review packet). See src/verity/provisional_review.py's module
docstring for the full rationale and safety discipline.

This is an explicit research/maintenance command, not part of the product
review path. It never touches reviewed-user content -- it only reviews
Verity's own already-committed synthetic Corpus fixtures.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from verity.corpus import CorpusError
from verity.provisional_review import (list_provisional_cases,
                                       run_provisional_review)
from verity.semantic.config import ProviderConfig, ProviderCredentials
from verity.semantic.eval_provider import OpenAICompatibleEvalProvider


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=("Independent multi-model review of provisional "
                     "Corpus labels (research/maintenance command)."))
    p.add_argument("--base-url", required=True,
                   help="OpenAI-compatible base URL, e.g. https://openrouter.ai/api/v1")
    p.add_argument("--model", action="append", dest="models", required=True,
                   help="Reviewer model id; repeat for each independent "
                        "reviewer (>=2 required, different models "
                        "recommended per this tool's pilot findings)")
    p.add_argument("--api-key-env", required=True,
                   help="Environment-variable NAME holding the API key; "
                        "never pass the key itself on the command line")
    p.add_argument("--max-cases", type=int, default=0,
                   help="0 = all provisional cases; otherwise cap for a "
                        "bounded pilot run")
    p.add_argument("--case-id", action="append", default=[],
                   help="Restrict to specific caseId(s) (repeatable)")
    p.add_argument("--max-output-tokens", type=int, default=400)
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--output", default="",
                   help="Write a JSON report here; default prints to stdout only")
    args = p.parse_args(argv)

    if len(args.models) < 2:
        print("refused: at least 2 --model reviewers are required "
              "(see provisional_review.py's module docstring for why a "
              "single cheap model was found unreliable on this task)",
              file=sys.stderr)
        return 2

    try:
        cases = list_provisional_cases()
    except CorpusError as exc:
        print(f"failed to load provisional cases: {exc}", file=sys.stderr)
        return 1

    if args.case_id:
        wanted = set(args.case_id)
        cases = [c for c in cases if c.caseId in wanted]
        missing = wanted - {c.caseId for c in cases}
        if missing:
            print(f"warning: case id(s) not found or not provisional: "
                  f"{sorted(missing)}", file=sys.stderr)
    if args.max_cases > 0:
        cases = cases[:args.max_cases]

    if not cases:
        print("no provisional cases matched; nothing to do")
        return 0

    reviewers = []
    for model_id in args.models:
        cfg = ProviderConfig(
            role="label_reviewer", provider_id="provisional-review-cli",
            model_id=model_id, base_url=args.base_url,
            credentials=ProviderCredentials(args.api_key_env),
            timeout_seconds=args.timeout,
        )
        if not cfg.credentials.resolve():
            print(f"refused: credential env var {args.api_key_env!r} is "
                  f"missing or empty", file=sys.stderr)
            return 1
        provider = OpenAICompatibleEvalProvider(
            cfg, temperature=0.0, max_output_tokens=args.max_output_tokens)
        reviewers.append((model_id, provider))

    print(f"reviewing {len(cases)} provisional case(s) with "
          f"{len(reviewers)} independent reviewer(s): "
          f"{', '.join(m for m, _ in reviewers)}")

    results = run_provisional_review(cases, reviewers=reviewers)

    agree = sum(1 for r in results if r.agreesWithAuthor is True)
    disagree = sum(1 for r in results if r.agreesWithAuthor is False)
    undecided = sum(1 for r in results if r.consensus is None)
    print(f"consensus agrees with author label: {agree}/{len(results)}")
    print(f"consensus DISAGREES with author label: {disagree}/{len(results)}")
    print(f"no majority consensus reached: {undecided}/{len(results)}")

    if disagree:
        print("\ncases where independent reviewers disagree with the "
              "current Corpus label (needs human attention before "
              "promoting labelStatus):")
        for r in results:
            if r.agreesWithAuthor is False:
                votes_str = ", ".join(f"{v.reviewerLabel}={v.state}"
                                      for v in r.votes)
                print(f"  - {r.caseId} ({r.riskId}): author={r.authorDecision} "
                      f"consensus={r.consensus} votes=[{votes_str}]")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "reviewerModels": args.models,
            "caseCount": len(results),
            "agreeCount": agree,
            "disagreeCount": disagree,
            "undecidedCount": undecided,
            "results": [asdict(r) for r in results],
        }
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
        print(f"\nwrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
