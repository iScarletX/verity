#!/usr/bin/env python3
"""Run the synthetic real-model semantic quality protocol.

This is an explicit research command, not a product review entry point.  It
never accepts arbitrary user artifacts and never writes a committed baseline.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from verity.corpus import CorpusError, canonical_report_json
from verity.semantic.config import ProviderConfig, ProviderCredentials
from verity.semantic.eval_provider import (EVAL_ROLE_PROMPT_VERSION,
                                           OpenAICompatibleEvalProvider)
from verity.semantic_quality import evaluate_semantic_model_quality


def _output_path(raw: str, split: str) -> Path:
    if raw:
        path = Path(raw).expanduser()
        if path.exists() and path.is_dir():
            path = path / f"semantic-quality-{split}.json"
    else:
        path = REPO / ".verity-data" / "model-evals" / f"semantic-quality-{split}.json"
    if path.suffix.lower() != ".json":
        raise CorpusError("output must be a .json file or existing directory")
    return path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Evaluate one frozen real-model configuration on synthetic Verity semantic cases")
    p.add_argument("--split", choices=["calibration", "selection", "test"], required=True)
    p.add_argument("--repetitions", type=int, default=2)
    p.add_argument("--base-url", required=True,
                   help="trusted OpenAI-compatible base URL, usually ending in /v1")
    p.add_argument("--generator-model", required=True)
    p.add_argument("--validator-model", required=True)
    p.add_argument("--api-key-env", required=True,
                   help="environment-variable NAME; never pass the key itself")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--max-output-tokens", type=int, default=800)
    p.add_argument("--max-total-calls", type=int, default=60,
                   help="hard preflight cap across generator + validator calls")
    p.add_argument("--timeout", type=float, default=30.0)
    p.add_argument("--output", default="",
                   help="local .json path; default is gitignored .verity-data/model-evals/")
    p.add_argument("--acknowledge-sealed-test", action="store_true",
                   help="required for --split test; consuming test forbids tuning protocol v1 from its results")
    args = p.parse_args(argv)

    try:
        credentials = ProviderCredentials(args.api_key_env)
        if not credentials.resolve():
            raise CorpusError(
                f"credential environment variable {args.api_key_env!r} is missing or empty")
        gen_cfg = ProviderConfig(
            role="candidate_generator", provider_id="openai-compatible-eval",
            model_id=args.generator_model, base_url=args.base_url,
            credentials=credentials, timeout_seconds=args.timeout,
            max_request_bytes=200 * 1024, max_response_bytes=128 * 1024)
        val_cfg = ProviderConfig(
            role="validator", provider_id="openai-compatible-eval",
            model_id=args.validator_model, base_url=args.base_url,
            credentials=credentials, timeout_seconds=args.timeout,
            max_request_bytes=200 * 1024, max_response_bytes=128 * 1024)
        generator = OpenAICompatibleEvalProvider(
            gen_cfg, temperature=args.temperature,
            max_output_tokens=args.max_output_tokens)
        validator = OpenAICompatibleEvalProvider(
            val_cfg, temperature=args.temperature,
            max_output_tokens=args.max_output_tokens)
        report = evaluate_semantic_model_quality(
            split=args.split, repetitions=args.repetitions,
            generator=generator, validator=validator,
            generator_config=gen_cfg, validator_config=val_cfg,
            temperature=args.temperature,
            max_output_tokens=args.max_output_tokens,
            max_total_calls=args.max_total_calls,
            role_prompt_version=EVAL_ROLE_PROMPT_VERSION,
            acknowledge_sealed_test=args.acknowledge_sealed_test)
        output = _output_path(args.output, args.split)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(canonical_report_json(report), encoding="utf-8")
    except (CorpusError, ValueError) as exc:
        print(f"semantic model eval refused: {exc}", file=sys.stderr)
        return 2
    print(f"wrote scrubbed report: {output}")
    print("model quality measured: true")
    print("split:", report["split"], "sealed test consumed:",
          str(report["sealedTestConsumed"]).lower())
    print("metrics:", json.dumps(report["metrics"], sort_keys=True))
    print("stability:", json.dumps(report["stability"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
