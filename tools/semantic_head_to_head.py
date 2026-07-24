#!/usr/bin/env python3
"""Build answer-free semantic packets or compare scrubbed system observations."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from verity.corpus import CorpusError, canonical_report_json
from verity.semantic_benchmark import (
    BUTLER_REFERENCE_SKILLS,
    BUTLER_REFERENCE_SKILL_MAP_VERSION,
    _validate_mapping,
    build_independent_label_attestation,
    build_semantic_comparison_packet,
    compare_semantic_systems,
    evaluate_independent_label_reviewer_observations,
    evaluate_verity_comparison_observations,
    load_butler_crosswalk,
    validate_observations,
)
from verity.semantic.catalog import CATALOG
from verity.semantic.config import ProviderConfig, ProviderCredentials
from verity.semantic.eval_provider import (
    EVAL_ROLE_PROMPT_VERSION,
    EvalRunBudget,
    LABEL_REVIEW_PROMPT_VERSION,
    OpenAICompatibleEvalProvider,
)


def _read(path: str):
    try:
        value = json.loads(Path(path).read_text("utf-8"))
    except Exception as exc:
        raise CorpusError(f"cannot read JSON input: {path}") from exc
    if not isinstance(value, dict):
        raise CorpusError(f"JSON input must be an object: {path}")
    return value


def _output_path(raw: str, default_name: str) -> Path:
    path = (Path(raw).expanduser() if raw else
            REPO / ".verity-data" / "semantic-comparison" / default_name)
    if path.suffix.lower() != ".json":
        raise CorpusError("output must be a .json file")
    return path


def _packet(args) -> int:
    seed = os.environ.get(args.seed_env)
    if not seed:
        raise CorpusError(
            f"seed environment variable {args.seed_env!r} is missing or empty")
    packet, mapping = build_semantic_comparison_packet(
        system_id=args.system_id, seed=seed)
    output_dir = (
        Path(args.output_dir).expanduser() if args.output_dir
        else REPO / ".verity-data" / "semantic-comparison" / args.system_id)
    packet_path = output_dir / "packet.json"
    mapping_path = output_dir / "alias-map.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    packet_path.write_text(
        json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    mapping_path.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(f"wrote answer-free packet: {packet_path}")
    print(f"wrote local alias map: {mapping_path}")
    print("claim eligible: false (development calibration only)")
    return 0


def _compare(args) -> int:
    report = compare_semantic_systems(
        verity_packet=_read(args.verity_packet),
        verity_mapping=_read(args.verity_map),
        verity_observations=_read(args.verity_observations),
        butler_packet=_read(args.butler_packet),
        butler_mapping=_read(args.butler_map),
        butler_observations=_read(args.butler_observations),
        label_attestation=(_read(args.labels) if args.labels else None),
    )
    output = _output_path(args.output, "comparison-report.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_report_json(report), encoding="utf-8")
    print(f"wrote scrubbed comparison report: {output}")
    print("status:", report["status"])
    print("claim:", report.get("claim"))
    return 0 if report["status"] == "passed" else 3


def _attest_labels(args) -> int:
    reviewer_c = (
        args.reviewer_c_packet,
        args.reviewer_c_map,
        args.reviewer_c_observations,
    )
    if any(reviewer_c) and not all(reviewer_c):
        raise CorpusError(
            "third independent label reviewer inputs are incomplete")
    attestation = build_independent_label_attestation(
        reviewer_a_packet=_read(args.reviewer_a_packet),
        reviewer_a_mapping=_read(args.reviewer_a_map),
        reviewer_a_observations=_read(args.reviewer_a_observations),
        reviewer_b_packet=_read(args.reviewer_b_packet),
        reviewer_b_mapping=_read(args.reviewer_b_map),
        reviewer_b_observations=_read(args.reviewer_b_observations),
        reviewer_c_packet=(
            _read(args.reviewer_c_packet) if args.reviewer_c_packet else None),
        reviewer_c_mapping=(
            _read(args.reviewer_c_map) if args.reviewer_c_map else None),
        reviewer_c_observations=(
            _read(args.reviewer_c_observations)
            if args.reviewer_c_observations else None),
    )
    output = _output_path(args.output, "independent-label-attestation.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_report_json(attestation), encoding="utf-8")
    print(f"wrote digest-bound label attestation: {output}")
    print("reviewers:", len(attestation["reviewers"]))
    print("labels:", len(attestation["labels"]))
    return 0


def _run_verity(args) -> int:
    credentials = ProviderCredentials(args.api_key_env)
    if not credentials.resolve():
        raise CorpusError(
            f"credential environment variable {args.api_key_env!r} is missing or empty")
    common = {
        "provider_id": "openai-compatible-comparison",
        "base_url": args.base_url,
        "credentials": credentials,
        "timeout_seconds": args.timeout,
        "max_request_bytes": 200 * 1024,
        "max_response_bytes": 128 * 1024,
    }
    generator_config = ProviderConfig(
        role="candidate_generator", model_id=args.generator_model, **common)
    validator_config = ProviderConfig(
        role="validator", model_id=args.validator_model, **common)
    run_budget = EvalRunBudget(
        max_calls=args.max_total_calls,
        max_total_tokens=args.max_total_tokens,
        max_spend_usd=args.max_spend_usd)
    generator = OpenAICompatibleEvalProvider(
        generator_config, temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
        run_budget=run_budget,
        input_price_per_million=args.generator_input_price_per_million,
        output_price_per_million=args.generator_output_price_per_million)
    validator = OpenAICompatibleEvalProvider(
        validator_config, temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
        run_budget=run_budget,
        input_price_per_million=args.validator_input_price_per_million,
        output_price_per_million=args.validator_output_price_per_million)
    observations = evaluate_verity_comparison_observations(
        packet=_read(args.packet), mapping=_read(args.alias_map),
        repetitions=args.repetitions, generator=generator, validator=validator,
        generator_config=generator_config, validator_config=validator_config,
        temperature=args.temperature, max_output_tokens=args.max_output_tokens,
        max_total_calls=args.max_total_calls,
        role_prompt_version=EVAL_ROLE_PROMPT_VERSION)
    frozen_limits = {
        "maxTotalCalls": args.max_total_calls,
        "maxTotalTokens": args.max_total_tokens,
        "maxSpendUsd": args.max_spend_usd,
        "maxOutputTokens": args.max_output_tokens,
        "generatorInputPricePerMillion": (
            args.generator_input_price_per_million),
        "generatorOutputPricePerMillion": (
            args.generator_output_price_per_million),
        "validatorInputPricePerMillion": (
            args.validator_input_price_per_million),
        "validatorOutputPricePerMillion": (
            args.validator_output_price_per_million),
    }
    bound_fingerprint = hashlib.sha256(json.dumps(
        {
            "baseConfigurationFingerprint": observations[
                "configurationFingerprint"],
            "runLimits": frozen_limits,
        },
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    observations = {
        **observations, "configurationFingerprint": bound_fingerprint}
    output = _output_path(args.output, "verity-observations.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_report_json(observations), encoding="utf-8")
    budget_output = output.with_name(output.stem + "-budget.json")
    budget_output.write_text(canonical_report_json({
        "schemaVersion": 1,
        "protocolId": observations["protocolId"],
        "protocolVersion": observations["protocolVersion"],
        "systemId": observations["systemId"],
        "configurationFingerprint": bound_fingerprint,
        "limits": frozen_limits,
        "reservation": run_budget.snapshot(),
    }), encoding="utf-8")
    print(f"wrote scrubbed Verity observations: {output}")
    print(f"wrote conservative budget audit: {budget_output}")
    print("labels sent to Provider: false")
    print("claim eligible: false (comparison not yet performed)")
    return 0


def _run_label_reviewer(args) -> int:
    credentials = ProviderCredentials(args.api_key_env)
    if not credentials.resolve():
        raise CorpusError(
            f"credential environment variable {args.api_key_env!r} is missing or empty")
    reviewer_config = ProviderConfig(
        role="label_reviewer", provider_id="openai-compatible-label-review",
        model_id=args.model, base_url=args.base_url, credentials=credentials,
        timeout_seconds=args.timeout, max_request_bytes=200 * 1024,
        max_response_bytes=128 * 1024)
    run_budget = EvalRunBudget(
        max_calls=args.max_total_calls,
        max_total_tokens=args.max_total_tokens,
        max_spend_usd=args.max_spend_usd)
    reviewer = OpenAICompatibleEvalProvider(
        reviewer_config, temperature=args.temperature,
        max_output_tokens=args.max_output_tokens, run_budget=run_budget,
        input_price_per_million=args.input_price_per_million,
        output_price_per_million=args.output_price_per_million)
    observations = evaluate_independent_label_reviewer_observations(
        packet=_read(args.packet), mapping=_read(args.alias_map),
        repetitions=args.repetitions, reviewer=reviewer,
        reviewer_config=reviewer_config, temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
        max_total_calls=args.max_total_calls,
        max_attempts_per_repetition=args.max_attempts_per_repetition,
        role_prompt_version=LABEL_REVIEW_PROMPT_VERSION)
    frozen_limits = {
        "maxTotalCalls": args.max_total_calls,
        "maxTotalTokens": args.max_total_tokens,
        "maxSpendUsd": args.max_spend_usd,
        "maxOutputTokens": args.max_output_tokens,
        "maxAttemptsPerRepetition": args.max_attempts_per_repetition,
        "inputPricePerMillion": args.input_price_per_million,
        "outputPricePerMillion": args.output_price_per_million,
    }
    bound_fingerprint = hashlib.sha256(json.dumps(
        {
            "baseConfigurationFingerprint": observations[
                "configurationFingerprint"],
            "runLimits": frozen_limits,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    observations = {
        **observations, "configurationFingerprint": bound_fingerprint}
    output = _output_path(args.output, "label-reviewer-observations.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_report_json(observations), encoding="utf-8")
    budget_output = output.with_name(output.stem + "-budget.json")
    budget_output.write_text(canonical_report_json({
        "schemaVersion": 1,
        "protocolId": observations["protocolId"],
        "protocolVersion": observations["protocolVersion"],
        "systemId": observations["systemId"],
        "configurationFingerprint": bound_fingerprint,
        "limits": frozen_limits,
        "reservation": run_budget.snapshot(),
    }), encoding="utf-8")
    print(f"wrote scrubbed label-review observations: {output}")
    print(f"wrote conservative budget audit: {budget_output}")
    print("author labels sent to Provider: false")
    print("claim eligible: false (second independent reviewer required)")
    return 0


def _butler_source_fingerprint(root: Path) -> str:
    package = _read(str(root / "package.json"))
    if package.get("name") != "butler":
        raise CorpusError("Butler root package identity invalid")
    files = [root / "package.json", root / "package-lock.json"]
    source = root / "src"
    if not source.is_dir():
        raise CorpusError("Butler source directory missing")
    files.extend(
        path for path in sorted(source.rglob("*"))
        if path.is_file() and path.suffix.lower() in {
            ".ts", ".tsx", ".json", ".md"})
    if len(files) > 2500:
        raise CorpusError("Butler source file budget exceeded")
    digest = hashlib.sha256()
    total = 0
    for path in files:
        if path.is_symlink():
            raise CorpusError("Butler source symlink refused")
        data = path.read_bytes()
        total += len(data)
        if total > 32 * 1024 * 1024:
            raise CorpusError("Butler source byte budget exceeded")
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    builtin_source = (root / "src/core/skillLoader/loadBuiltinSkills.ts")
    if not builtin_source.is_file():
        raise CorpusError("Butler built-in Skill registry missing")
    registry_text = builtin_source.read_text("utf-8")
    crosswalk = load_butler_crosswalk()
    expected_skill_ids = {
        entry["butlerSkillId"] for entry in crosswalk["entries"]}
    actual_skill_ids = set(re.findall(
        r"\bid:\s*'([^']+)'", registry_text))
    if actual_skill_ids != expected_skill_ids:
        raise CorpusError("Butler built-in Skill inventory changed")
    revision = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False, timeout=10)
    if (revision.returncode != 0
            or revision.stdout.strip() != crosswalk["referenceCommit"]):
        raise CorpusError("Butler reference commit differs from crosswalk")
    fingerprint = digest.hexdigest()
    if fingerprint != crosswalk["referenceSourceFingerprint"]:
        raise CorpusError("Butler source fingerprint differs from crosswalk")
    return fingerprint


def _run_butler(args) -> int:
    packet = _read(args.packet)
    mapping = _read(args.alias_map)
    _validate_mapping(mapping, packet)
    if packet.get("systemId") != "butler":
        raise CorpusError("Butler run requires a system-id=butler packet")
    credentials = ProviderCredentials(args.api_key_env)
    if not credentials.resolve():
        raise CorpusError(
            f"credential environment variable {args.api_key_env!r} "
            "is missing or empty")
    ProviderConfig(
        role="candidate_generator",
        provider_id="butler-reference",
        model_id=args.model[0],
        base_url=args.base_url,
        credentials=credentials,
        timeout_seconds=args.timeout,
    )
    if not 2 <= len(args.model) <= 3:
        raise CorpusError("Butler reference requires two or three models")
    if len(set(args.model)) != len(args.model):
        raise CorpusError("Butler reference models must be distinct")
    if any(
            not isinstance(model, str) or not model or len(model) > 200
            for model in args.model):
        raise CorpusError("Butler reference model id invalid")
    if (len(args.input_price_per_million) != len(args.model)
            or len(args.output_price_per_million) != len(args.model)):
        raise CorpusError("Butler model and price counts must match")
    for price in (
            list(args.input_price_per_million)
            + list(args.output_price_per_million)):
        if not 0 <= price <= 1_000_000:
            raise CorpusError("Butler model price is out of bounds")
    if not 2 <= args.repetitions <= 10:
        raise CorpusError("Butler repetitions must be 2..10")
    if not 64 <= args.max_output_tokens <= 4096:
        raise CorpusError("Butler max output tokens must be 64..4096")
    if not 1 <= args.max_concurrency <= 8:
        raise CorpusError("Butler max concurrency must be 1..8")
    if not 1 <= args.wall_timeout_seconds <= 86400:
        raise CorpusError("Butler wall timeout must be 1..86400 seconds")
    EvalRunBudget(
        max_calls=args.max_total_calls,
        max_total_tokens=args.max_total_tokens,
        max_spend_usd=args.max_spend_usd)

    skill_map = {
        CATALOG[finding_type][0].falsificationQuestion: list(skill_ids)
        for finding_type, skill_ids in BUTLER_REFERENCE_SKILLS.items()
    }
    if set(BUTLER_REFERENCE_SKILLS) != set(CATALOG):
        raise CorpusError("Butler reference map does not cover the catalog")
    minimum_calls = args.repetitions * sum(
        1 + len(skill_map[item["targetRisk"]["falsificationQuestion"]])
        * len(args.model)
        for item in packet["items"]
    )
    if args.max_total_calls < minimum_calls:
        raise CorpusError(
            f"Butler comparison call budget requires at least {minimum_calls}, "
            f"configured {args.max_total_calls}")

    butler_root = Path(args.butler_root).expanduser().resolve()
    if not butler_root.is_dir():
        raise CorpusError("Butler root directory missing")
    vite = butler_root / "node_modules/.bin/vite"
    node = shutil.which("node")
    if not vite.is_file() or not node:
        raise CorpusError(
            "Butler reference runtime missing; existing node_modules and Node "
            "are required (the adapter will not install them)")
    source_fingerprint = _butler_source_fingerprint(butler_root)
    frozen_limits = {
        "maxTotalCalls": args.max_total_calls,
        "maxTotalTokens": args.max_total_tokens,
        "maxSpendUsd": args.max_spend_usd,
        "maxOutputTokens": args.max_output_tokens,
        "maxConcurrency": args.max_concurrency,
        "requestTimeoutSeconds": args.timeout,
        "wallTimeoutSeconds": args.wall_timeout_seconds,
        "inputPricePerMillion": list(args.input_price_per_million),
        "outputPricePerMillion": list(args.output_price_per_million),
    }
    configuration_fingerprint = hashlib.sha256(json.dumps({
        "adapter": "verity-butler-read-only-reference",
        "adapterVersion": 1,
        "butlerSourceFingerprint": source_fingerprint,
        "skillMapVersion": BUTLER_REFERENCE_SKILL_MAP_VERSION,
        "models": list(args.model),
        "providerEndpointSha256": hashlib.sha256(
            args.base_url.encode()).hexdigest(),
        "temperature": 0.1,
        "repetitions": args.repetitions,
        "corpusFingerprint": packet["corpusFingerprint"],
        "limits": frozen_limits,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    output = _output_path(args.output, "butler-observations.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    entry = REPO / "tools/butler_reference_entry.ts"
    vite_config = REPO / "tools/butler_reference.vite.config.mjs"
    with tempfile.TemporaryDirectory(prefix="verity-butler-reference-") as raw:
        temp = Path(raw)
        bundle_dir = temp / "bundle"
        packet_path = temp / "packet.json"
        runner_output = temp / "runner-output.json"
        config_path = temp / "runner-config.json"
        packet_path.write_text(
            json.dumps(packet, ensure_ascii=False, sort_keys=True),
            encoding="utf-8")
        config = {
            "packetPath": str(packet_path),
            "outputPath": str(runner_output),
            "apiKeyEnv": args.api_key_env,
            "baseUrl": args.base_url.rstrip("/"),
            "models": [
                {
                    "modelId": model_id,
                    "inputPricePerMillion": input_price,
                    "outputPricePerMillion": output_price,
                }
                for model_id, input_price, output_price in zip(
                    args.model, args.input_price_per_million,
                    args.output_price_per_million)
            ],
            "repetitions": args.repetitions,
            "maxOutputTokens": args.max_output_tokens,
            "maxConcurrency": args.max_concurrency,
            "maxTotalCalls": args.max_total_calls,
            "maxTotalTokens": args.max_total_tokens,
            "maxSpendUsd": args.max_spend_usd,
            "maxRequestBytes": 200 * 1024,
            "maxResponseBytes": 128 * 1024,
            "requestTimeoutMs": int(args.timeout * 1000),
            "configurationFingerprint": configuration_fingerprint,
            "skillMap": skill_map,
        }
        config_path.write_text(
            json.dumps(config, ensure_ascii=False, sort_keys=True),
            encoding="utf-8")
        os.chmod(config_path, 0o600)
        build_env = {
            **os.environ,
            "VERITY_BUTLER_ROOT": str(butler_root),
            "VERITY_BUTLER_ENTRY": str(entry),
            "VERITY_BUTLER_BUNDLE_OUTPUT": str(bundle_dir),
        }
        built = subprocess.run(
            [str(vite), "build", "--config", str(vite_config),
             "--logLevel", "error"],
            cwd=butler_root, env=build_env, capture_output=True, text=True,
            check=False, timeout=120)
        if built.returncode != 0:
            raise CorpusError(
                "Butler reference bundle failed: "
                + (built.stderr.strip() or built.stdout.strip())[-500:])
        runner = bundle_dir / "butler-reference-runner.mjs"
        if not runner.is_file():
            raise CorpusError("Butler reference bundle output missing")
        try:
            ran = subprocess.run(
                [node, str(runner), str(config_path)],
                cwd=butler_root, env=os.environ.copy(),
                capture_output=True, text=True, check=False,
                timeout=args.wall_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            raise CorpusError("Butler reference wall timeout exceeded") from exc
        if ran.returncode != 0 or not runner_output.is_file():
            raise CorpusError(
                "Butler reference run failed: "
                + (ran.stderr.strip() or ran.stdout.strip())[-500:])
        result = _read(str(runner_output))
    if set(result) != {"observations", "budget"}:
        raise CorpusError("Butler reference output schema invalid")
    observations = validate_observations(result["observations"], packet)
    output.write_text(canonical_report_json(observations), encoding="utf-8")
    audit_output = output.with_name(output.stem + "-budget.json")
    audit_output.write_text(canonical_report_json({
        "schemaVersion": 1,
        "protocolId": observations["protocolId"],
        "protocolVersion": observations["protocolVersion"],
        "systemId": "butler",
        "configurationFingerprint": configuration_fingerprint,
        "butlerSourceFingerprint": source_fingerprint,
        "skillMapVersion": BUTLER_REFERENCE_SKILL_MAP_VERSION,
        "limits": frozen_limits,
        "reservation": result["budget"],
    }), encoding="utf-8")
    print(f"wrote scrubbed Butler observations: {output}")
    print(f"wrote conservative budget audit: {audit_output}")
    print("Butler source modified: false")
    print("labels sent to Butler: false")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Verity/Butler answer-hidden semantic comparison")
    sub = parser.add_subparsers(dest="command", required=True)

    packet = sub.add_parser("packet", help="build one answer-free system packet")
    packet.add_argument("--system-id", required=True)
    packet.add_argument("--seed-env", required=True,
                        help="environment-variable NAME containing a private shuffle seed")
    packet.add_argument("--output-dir", default="")
    packet.set_defaults(handler=_packet)

    run_verity = sub.add_parser(
        "run-verity", help="run a frozen Verity model configuration on its packet")
    run_verity.add_argument("--packet", required=True)
    run_verity.add_argument("--alias-map", required=True)
    run_verity.add_argument("--repetitions", type=int, default=2)
    run_verity.add_argument("--base-url", required=True)
    run_verity.add_argument("--generator-model", required=True)
    run_verity.add_argument("--validator-model", required=True)
    run_verity.add_argument("--api-key-env", required=True)
    run_verity.add_argument("--temperature", type=float, default=0.0)
    run_verity.add_argument("--max-output-tokens", type=int, required=True)
    run_verity.add_argument(
        "--max-total-calls", type=int,
        required=True)
    run_verity.add_argument("--max-total-tokens", type=int, required=True)
    run_verity.add_argument("--max-spend-usd", type=float, required=True)
    run_verity.add_argument(
        "--generator-input-price-per-million", type=float, required=True)
    run_verity.add_argument(
        "--generator-output-price-per-million", type=float, required=True)
    run_verity.add_argument(
        "--validator-input-price-per-million", type=float, required=True)
    run_verity.add_argument(
        "--validator-output-price-per-million", type=float, required=True)
    run_verity.add_argument("--timeout", type=float, default=30.0)
    run_verity.add_argument("--output", required=True)
    run_verity.set_defaults(handler=_run_verity)

    run_reviewer = sub.add_parser(
        "run-label-reviewer",
        help="run one independent answer-hidden label reviewer on its packet")
    run_reviewer.add_argument("--packet", required=True)
    run_reviewer.add_argument("--alias-map", required=True)
    run_reviewer.add_argument("--repetitions", type=int, required=True)
    run_reviewer.add_argument("--base-url", required=True)
    run_reviewer.add_argument("--model", required=True)
    run_reviewer.add_argument("--api-key-env", required=True)
    run_reviewer.add_argument("--temperature", type=float, default=0.0)
    run_reviewer.add_argument("--max-output-tokens", type=int, required=True)
    run_reviewer.add_argument("--max-total-calls", type=int, required=True)
    run_reviewer.add_argument(
        "--max-attempts-per-repetition", type=int, default=1)
    run_reviewer.add_argument("--max-total-tokens", type=int, required=True)
    run_reviewer.add_argument("--max-spend-usd", type=float, required=True)
    run_reviewer.add_argument(
        "--input-price-per-million", type=float, required=True)
    run_reviewer.add_argument(
        "--output-price-per-million", type=float, required=True)
    run_reviewer.add_argument("--timeout", type=float, default=30.0)
    run_reviewer.add_argument("--output", required=True)
    run_reviewer.set_defaults(handler=_run_label_reviewer)

    run_butler = sub.add_parser(
        "run-butler",
        help="run the read-only Butler reference on its answer-hidden packet")
    run_butler.add_argument("--packet", required=True)
    run_butler.add_argument("--alias-map", required=True)
    run_butler.add_argument("--butler-root", required=True)
    run_butler.add_argument("--repetitions", type=int, required=True)
    run_butler.add_argument("--base-url", required=True)
    run_butler.add_argument("--model", action="append", required=True)
    run_butler.add_argument(
        "--input-price-per-million", action="append", type=float,
        required=True)
    run_butler.add_argument(
        "--output-price-per-million", action="append", type=float,
        required=True)
    run_butler.add_argument("--api-key-env", required=True)
    run_butler.add_argument("--max-output-tokens", type=int, required=True)
    run_butler.add_argument("--max-concurrency", type=int, default=1)
    run_butler.add_argument("--max-total-calls", type=int, required=True)
    run_butler.add_argument("--max-total-tokens", type=int, required=True)
    run_butler.add_argument("--max-spend-usd", type=float, required=True)
    run_butler.add_argument("--timeout", type=float, required=True)
    run_butler.add_argument(
        "--wall-timeout-seconds", type=int, required=True)
    run_butler.add_argument("--output", required=True)
    run_butler.set_defaults(handler=_run_butler)

    attest = sub.add_parser(
        "attest-labels",
        help=("derive labels from two error-free, two-thirds-consensus "
              "answer-hidden reviews"))
    attest.add_argument("--reviewer-a-packet", required=True)
    attest.add_argument("--reviewer-a-map", required=True)
    attest.add_argument("--reviewer-a-observations", required=True)
    attest.add_argument("--reviewer-b-packet", required=True)
    attest.add_argument("--reviewer-b-map", required=True)
    attest.add_argument("--reviewer-b-observations", required=True)
    attest.add_argument("--reviewer-c-packet")
    attest.add_argument("--reviewer-c-map")
    attest.add_argument("--reviewer-c-observations")
    attest.add_argument("--output", default="")
    attest.set_defaults(handler=_attest_labels)

    compare = sub.add_parser(
        "compare", help="compare two scrubbed repeated-observation files")
    compare.add_argument("--verity-packet", required=True)
    compare.add_argument("--verity-map", required=True)
    compare.add_argument("--verity-observations", required=True)
    compare.add_argument("--butler-packet", required=True)
    compare.add_argument("--butler-map", required=True)
    compare.add_argument("--butler-observations", required=True)
    compare.add_argument("--labels", default="",
                         help="digest-bound independent label attestation")
    compare.add_argument("--output", default="")
    compare.set_defaults(handler=_compare)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (CorpusError, ValueError) as exc:
        print(f"semantic comparison refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
