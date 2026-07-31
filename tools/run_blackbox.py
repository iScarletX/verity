#!/usr/bin/env python3
"""V1.5 Prompt black-box evaluation runner.

Sends a system prompt against a real model under a set of controlled
attack/probe scenarios and reports whether the model resisted each attack.

This is an EXPLICIT research command, not part of the product review path.
It only runs when you deliberately invoke it. It never starts automatically.

Usage examples
--------------
# Run all built-in scenarios against a system prompt file:
python3 tools/run_blackbox.py \\
    --prompt-file path/to/system_prompt.txt \\
    --base-url https://openrouter.ai/api/v1 \\
    --model openai/gpt-4o-mini \\
    --api-key-env OPENROUTER_API_KEY \\
    --output /tmp/blackbox_results.json

# Run with a budget cap (number of API calls):
python3 tools/run_blackbox.py \\
    --prompt-file path/to/system_prompt.txt \\
    --base-url https://openrouter.ai/api/v1 \\
    --model openai/gpt-4o-mini \\
    --api-key-env OPENROUTER_API_KEY \\
    --max-calls 20

# Run specific scenarios only:
python3 tools/run_blackbox.py \\
    --prompt-file path/to/system_prompt.txt \\
    --base-url https://openrouter.ai/api/v1 \\
    --model openai/gpt-4o-mini \\
    --api-key-env OPENROUTER_API_KEY \\
    --scenario injection_override_simple \\
    --scenario system_prompt_extraction
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from verity.blackbox.runner import run_blackbox
from verity.blackbox.scenarios import list_scenarios, get_scenario


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="V1.5 Prompt black-box evaluation — test a system prompt against real attacks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--prompt-file", required=False, default="",
                   help="Path to the system prompt text file to test")
    p.add_argument("--base-url", required=False, default="",
                   help="OpenAI-compatible API base URL (e.g. https://openrouter.ai/api/v1)")
    p.add_argument("--model", required=False, default="",
                   help="Model ID to use as the target (e.g. openai/gpt-4o-mini)")
    p.add_argument("--api-key-env", required=False, default="",
                   help="Environment variable NAME that holds the API key (never pass the key itself)")
    p.add_argument("--scenario", action="append", default=[],
                   help="Run specific scenario id(s) only (repeatable); default = all built-in scenarios")
    p.add_argument("--max-calls", type=int, default=50,
                   help="Hard cap on total API calls (default: 50)")
    p.add_argument("--timeout", type=float, default=30.0,
                   help="Per-call timeout in seconds (default: 30)")
    p.add_argument("--max-tokens", type=int, default=800,
                   help="Max tokens per model response (default: 800)")
    p.add_argument("--output", default="",
                   help="Write full JSON report to this path (default: print summary only)")
    p.add_argument("--list-scenarios", action="store_true",
                   help="Print all available scenario IDs and exit")
    args = p.parse_args(argv)

    if args.list_scenarios:
        for s in list_scenarios():
            print(f"  {s.scenario_id:<40} [{s.severity}] {s.title}")
        return 0

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        print(f"error: environment variable {args.api_key_env!r} is not set",
              file=sys.stderr)
        return 1

    prompt_path = Path(args.prompt_file)
    if not prompt_path.is_file():
        print(f"error: prompt file not found: {prompt_path}", file=sys.stderr)
        return 1
    system_prompt = prompt_path.read_text(encoding="utf-8")
    if not system_prompt.strip():
        print("error: prompt file is empty", file=sys.stderr)
        return 1

    if args.scenario:
        scenarios = []
        for sid in args.scenario:
            s = get_scenario(sid)
            if s is None:
                print(f"error: unknown scenario id {sid!r}. "
                      f"Run with --list-scenarios to see available ids.",
                      file=sys.stderr)
                return 1
            scenarios.append(s)
    else:
        scenarios = list_scenarios()

    print(f"[blackbox] target model : {args.model}")
    print(f"[blackbox] system prompt: {len(system_prompt)} chars "
          f"(sha256 prefix: {__import__('hashlib').sha256(system_prompt.encode()).hexdigest()[:12]})")
    print(f"[blackbox] scenarios    : {len(scenarios)}")
    print(f"[blackbox] call budget  : {args.max_calls}")
    print()

    result = run_blackbox(
        system_prompt=system_prompt,
        scenarios=scenarios,
        base_url=args.base_url,
        model_id=args.model,
        api_key=api_key,
        max_calls=args.max_calls,
        timeout_seconds=args.timeout,
        max_tokens_per_response=args.max_tokens,
    )

    summary = result.summary()

    # Print per-scenario summary
    for sr in result.scenario_results:
        icon = {"passed": "✅", "failed": "❌", "error": "⚠️", "partial": "⚠️"}.get(sr.verdict, "?")
        print(f"{icon} [{sr.severity}] {sr.title}")
        print(f"   {sr.verdict.upper()} — "
              f"safe:{sr.safe_count} failed:{sr.failed_count} errors:{sr.error_count} "
              f"/ {sr.total_probes} probe(s)")
        for pr in sr.probe_results:
            status = "SAFE" if pr.safe is True else ("FAILED" if pr.safe is False else "ERROR")
            probe_short = pr.probe_text[:80].replace("\n", " ")
            print(f"   [{status}] probe {pr.probe_index}: {probe_short!r:.80}")
            if pr.response_text:
                resp_short = pr.response_text[:200].replace("\n", " ")
                print(f"           response: {resp_short!r:.200}")
            if pr.error_code:
                print(f"           error: {pr.error_code}")
        print()

    print(f"总结 / Summary: "
          f"{summary['passed']}/{summary['completed']} 场景通过 — "
          f"passed:{summary['passed']} failed:{summary['failed']} "
          f"errors:{summary['errors']} partial:{summary['partial']}")
    print(f"总调用次数: {summary['totalCalls']}"
          + ("（预算耗尽）" if summary['budgetExhausted'] else ""))

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "schemaVersion": result.schema_version,
            "model": result.model_id,
            "systemPromptDigest": result.system_prompt_digest,
            "summary": summary,
            "scenarios": [asdict(sr) for sr in result.scenario_results],
        }
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        print(f"\nwrote full report: {out_path}")

    failed = summary["failed"] + summary["errors"]
    return 1 if failed > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
