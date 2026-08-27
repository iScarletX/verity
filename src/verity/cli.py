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
  1  ``gate=findings_block`` at least one High/Critical Finding or normalized
                             dynamic issue occurrence is present.
                             Wins over the coverage gate: if both are triggered
                             the exit code is 1 (High/Critical is the stricter
                             signal a CI needs to surface first).
  3  ``gate=coverage_block`` Coverage is insufficient, OR an explicitly
                             requested semantic or dynamic review did not
                             complete, AND no High/Critical result is present.
                             Chosen instead of 2 so it does not collide with
                             argparse's usage-error exit 2.
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


_AGENT_RUNTIME_CLI_FLAGS = frozenset({
    "--enable-agent-runtime",
    "--agent-runtime-node-path",
    "--agent-runtime-node-sha256",
    "--agent-runtime-dsh-path",
    "--agent-runtime-dsh-sha256",
    "--agent-runtime-version",
    "--agent-runtime-base-url",
    "--agent-runtime-model",
    "--agent-runtime-api-key-env",
    "--agent-runtime-scenario-id",
    "--agent-runtime-timeout",
})


def _gate_from_report(
    report: dict,
    *,
    semantic_requires_pass: bool,
    prompt_blackbox_requires_pass: bool = False,
    skill_sandbox_requires_pass: bool = False,
    agent_runtime_requires_pass: bool = False,
) -> tuple[str, int, int, int]:
    """Return gate, exit code, finding count and High/Critical count.

    ``semantic_requires_pass`` should be True only when the caller supplied
    a complete trusted Provider configuration for semantic review (i.e. it
    was actually attempted with real credentials), not merely whenever
    ``--semantic`` was passed -- semantic now defaults to attempted, and an
    unconfigured Provider honestly reporting ``provider_not_configured``
    must not by itself flip a CI-facing exit code from 0 to 3. A caller who
    explicitly wants that stricter gate can still get it: it is exactly
    "did I hand this run real credentials".

    The three dynamic ``*_requires_pass`` flags are True only for stages the
    trusted caller explicitly enabled. Such a stage must be ``completed`` in
    both its result view and capability projection; missing or malformed
    requested status fails closed. Unrequested ``not_enabled`` stages remain
    non-blocking.
    """
    from .findings_view import completed_findings
    findings, _ = completed_findings(report)
    from .issues import controlled_runtime_occurrence_projection
    dynamic_rows, dynamic_projection_ok = (
        controlled_runtime_occurrence_projection(report)
    )
    dynamic_severities = [severity for _, _, severity in dynamic_rows]
    high = sum(1 for f in findings
               if f.get("severity") in ("high", "critical"))
    high += sum(
        1 for severity in dynamic_severities
        if severity in ("high", "critical")
    )
    finding_count = len(findings) + len(dynamic_severities)
    coverage_ok = (report.get("coverage") or {}).get("status") == "sufficient"
    semantic_status = (report.get("semantic") or {}).get("status")
    semantic_ok = (not semantic_requires_pass
                  or semantic_status == "completed")
    capabilities = report.get("capabilities")

    def _stage_completed(result_key: str, capability_key: str) -> bool:
        result_view = report.get(result_key)
        capability_view = (
            capabilities.get(capability_key)
            if isinstance(capabilities, dict)
            else None
        )
        return (
            isinstance(result_view, dict)
            and result_view.get("status") == "completed"
            and isinstance(capability_view, dict)
            and capability_view.get("status") == "completed"
        )

    prompt_blackbox_ok = (
        not prompt_blackbox_requires_pass
        or _stage_completed("promptBlackbox", "promptBlackbox")
    )
    skill_sandbox_ok = (
        not skill_sandbox_requires_pass
        or _stage_completed("skillSandbox", "skillSandbox")
    )
    agent_runtime_ok = (
        not agent_runtime_requires_pass
        or _stage_completed(
            "agentInstructionRuntime",
            "agentInstructionRuntime",
        )
    )
    requested_dynamic = (
        prompt_blackbox_requires_pass
        or skill_sandbox_requires_pass
        or agent_runtime_requires_pass
    )
    if high:
        return "findings_block", 1, finding_count, high
    if (
        not coverage_ok
        or not semantic_ok
        or not prompt_blackbox_ok
        or not skill_sandbox_ok
        or not agent_runtime_ok
        or (requested_dynamic and not dynamic_projection_ok)
    ):
        return "coverage_block", 3, finding_count, high
    return "pass", 0, finding_count, high


def _cmd_review(args: argparse.Namespace) -> int:
    agent_runtime_values_supplied = any((
        args.agent_runtime_node_path is not None,
        args.agent_runtime_node_sha256 is not None,
        args.agent_runtime_dsh_path is not None,
        args.agent_runtime_dsh_sha256 is not None,
        args.agent_runtime_version is not None,
        args.agent_runtime_base_url is not None,
        args.agent_runtime_model is not None,
        args.agent_runtime_api_key_env is not None,
        bool(args.agent_runtime_scenario_id),
        args.agent_runtime_timeout is not None,
    ))
    if agent_runtime_values_supplied and not args.enable_agent_runtime:
        print(
            "agent runtime flags require --enable-agent-runtime",
            file=sys.stderr,
        )
        return 2
    if args.enable_agent_runtime and args.engine != "skill":
        print(
            "--enable-agent-runtime is only applicable to --engine skill",
            file=sys.stderr,
        )
        return 2

    agent_runtime_cfg = None
    if args.enable_agent_runtime:
        required_runtime_values = (
            args.agent_runtime_node_path,
            args.agent_runtime_node_sha256,
            args.agent_runtime_dsh_path,
            args.agent_runtime_dsh_sha256,
            args.agent_runtime_base_url,
            args.agent_runtime_model,
            args.agent_runtime_api_key_env,
        )
        if not all(required_runtime_values):
            print(
                "--enable-agent-runtime requires Node path/hash, DSH "
                "path/hash, base URL, model, and API-key "
                "environment-variable name",
                file=sys.stderr,
            )
            return 2
        from .agent_runtime import AgentRuntimeConfig, AgentRuntimeCredentials
        try:
            scenario_ids = (
                tuple(args.agent_runtime_scenario_id)
                if args.agent_runtime_scenario_id
                else ("agent_primary_task", "agent_untrusted_content")
            )
            timeout_seconds = (
                90.0
                if args.agent_runtime_timeout is None
                else float(args.agent_runtime_timeout)
            )
            agent_runtime_cfg = AgentRuntimeConfig(
                enabled=True,
                node_executable=args.agent_runtime_node_path,
                node_sha256=args.agent_runtime_node_sha256,
                dsh_executable=args.agent_runtime_dsh_path,
                dsh_sha256=args.agent_runtime_dsh_sha256,
                expected_version=(
                    "0.1.1-rc.2"
                    if args.agent_runtime_version is None
                    else args.agent_runtime_version
                ),
                base_url=args.agent_runtime_base_url,
                model_id=args.agent_runtime_model,
                credentials=AgentRuntimeCredentials(
                    api_key_env=args.agent_runtime_api_key_env,
                ),
                scenario_ids=scenario_ids,
                timeout_seconds=timeout_seconds,
            )
        except ValueError:
            print("invalid --agent-runtime configuration", file=sys.stderr)
            return 2

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
    validators = None
    has_complete_provider_config = False
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
                provider_kind = args.semantic_provider_kind
                gen_cfg = ProviderConfig(
                    role="candidate_generator",
                    provider_id=provider_kind,
                    model_id=args.semantic_generator_model,
                    base_url=args.semantic_generator_url,
                    credentials=ProviderCredentials(
                        api_key_env=args.semantic_generator_api_key_env),
                    timeout_seconds=args.semantic_timeout,
                )
                val_cfgs = [ProviderConfig(
                    role="validator",
                    provider_id=provider_kind,
                    model_id=args.semantic_validator_model,
                    base_url=args.semantic_validator_url,
                    credentials=ProviderCredentials(
                        api_key_env=args.semantic_validator_api_key_env),
                    timeout_seconds=args.semantic_timeout,
                )]
                for raw_vote in args.semantic_validator_vote:
                    parts = raw_vote.split(",")
                    if len(parts) != 3:
                        print("invalid --semantic-validator-vote: expected "
                              "URL,MODEL,API_KEY_ENV", file=sys.stderr)
                        return 2
                    vote_url, vote_model, vote_key_env = (
                        p.strip() for p in parts)
                    val_cfgs.append(ProviderConfig(
                        role="validator",
                        provider_id=provider_kind,
                        model_id=vote_model,
                        base_url=vote_url,
                        credentials=ProviderCredentials(
                            api_key_env=vote_key_env),
                        timeout_seconds=args.semantic_timeout,
                    ))
                sem_cfg = SemanticConfig(
                    enabled=True,
                    egress_policy=args.egress_policy,
                    provider_config={
                        "candidate_generator": gen_cfg,
                        # Product orchestrator reads validators= (the full
                        # list); this single entry keeps has_provider("validator")
                        # true for callers/tests that only check presence.
                        "validator": val_cfgs[0],
                    },
                )
                # Deliberately distinct role-bound objects per vote, even
                # when two votes happen to share an endpoint/model, so no
                # provider client accidentally shares state across votes.
                if provider_kind == "openai_compatible":
                    # Most real hosted providers (OpenRouter, OpenAI itself,
                    # and OpenAI-compatible self-hosted gateways) serve
                    # POST {base_url}/chat/completions, not Verity's custom
                    # /v1/verity/candidate-generator contract. This wire
                    # adapter is the same one the Web UI uses.
                    from .semantic.eval_provider import OpenAICompatibleEvalProvider
                    candidate_generator = OpenAICompatibleEvalProvider(
                        config=gen_cfg, max_output_tokens=args.semantic_max_output_tokens)
                    validators = [
                        OpenAICompatibleEvalProvider(
                            config=vc, max_output_tokens=args.semantic_max_output_tokens)
                        for vc in val_cfgs
                    ]
                else:
                    candidate_generator = JsonCandidateGeneratorProvider(gen_cfg)
                    validators = [JsonValidatorProvider(vc) for vc in val_cfgs]
            else:
                # Explicit opt-in without trusted Provider config remains
                # a visible provider_not_configured result.
                sem_cfg = SemanticConfig(enabled=True,
                                         egress_policy=args.egress_policy)
        except ValueError as exc:
            print(f"invalid --semantic configuration: {exc}", file=sys.stderr)
            return 2

    # V1.5 Prompt black-box: research-stage, explicit two-gate opt-in.
    # Both new flags default OFF; a bare `verity review` never touches
    # this code path, matching every existing call site's behavior.
    blackbox_cfg = None
    if args.enable_prompt_blackbox:
        if args.engine != "prompt":
            print("--enable-prompt-blackbox is only applicable to "
                  "--engine prompt", file=sys.stderr)
            return 2
        from .blackbox import BlackboxConfig, BlackboxCredentials
        try:
            blackbox_cfg = BlackboxConfig(
                enabled=True,
                base_url=args.blackbox_base_url or "",
                model_id=args.blackbox_model or "",
                credentials=BlackboxCredentials(
                    api_key_env=args.blackbox_api_key_env),
                scenario_policy=args.blackbox_scenario_policy,
                scenario_ids=tuple(args.blackbox_scenario_id),
                max_calls=args.blackbox_max_calls,
                timeout_seconds=args.blackbox_timeout,
                max_tokens_per_response=args.blackbox_max_tokens,
            )
        except ValueError as exc:
            print(f"invalid --blackbox-* configuration: {exc}", file=sys.stderr)
            return 2

    # V2 Skill sandbox compatibility request. The flag defaults OFF; when
    # present, run_review records a failed/unavailable capability without
    # importing or constructing the private research runner.
    sandbox_cfg = None
    if args.enable_skill_sandbox:
        if args.engine != "skill":
            print("--enable-skill-sandbox is only applicable to "
                  "--engine skill", file=sys.stderr)
            return 2
        from .sandbox import SandboxConfig
        try:
            sandbox_cfg = SandboxConfig(
                enabled=True,
                entry_point=args.sandbox_entry_point or "",
                argv=tuple(args.sandbox_argv),
                cpu_seconds=args.sandbox_cpu_seconds,
                memory_mb=args.sandbox_memory_mb,
                wall_seconds=args.sandbox_wall_seconds,
            )
        except ValueError as exc:
            print(f"invalid --sandbox-* configuration: {exc}", file=sys.stderr)
            return 2

    review = run_review(ReviewInputs(engine=args.engine, snapshot=snap,
                                     file_bytes=byts, profile=args.profile,
                                     semantic_config=sem_cfg,
                                     blackbox_config=blackbox_cfg,
                                     sandbox_config=sandbox_cfg,
                                     agent_runtime_config=agent_runtime_cfg),
                        candidate_generator=candidate_generator,
                        validators=validators)

    out_dir = Path(args.out) if args.out else Path("out")
    out_dir.mkdir(parents=True, exist_ok=True)
    d = review_to_dict(review)
    (out_dir / "report.json").write_text(to_json(review), encoding="utf-8")
    (out_dir / "report.html").write_text(to_html(review), encoding="utf-8")
    (out_dir / "report.sarif").write_text(to_sarif_json(d), encoding="utf-8")

    gate, exit_code, n_findings, n_high = _gate_from_report(
        d,
        semantic_requires_pass=has_complete_provider_config,
        prompt_blackbox_requires_pass=blackbox_cfg is not None,
        skill_sandbox_requires_pass=sandbox_cfg is not None,
        agent_runtime_requires_pass=agent_runtime_cfg is not None,
    )
    semantic_status = ((review.semantic or {}).get("status")
                       if args.semantic else "not_enabled")

    extra_status = []
    if blackbox_cfg is not None:
        extra_status.append(
            f"promptBlackbox={(review.promptBlackbox or {}).get('status')}")
    if sandbox_cfg is not None:
        extra_status.append(
            f"skillSandbox={(review.skillSandbox or {}).get('status')}")
    if agent_runtime_cfg is not None:
        agent_runtime_status = (
            (review.agentInstructionRuntime or {}).get("status")
        )
        if agent_runtime_status not in {"completed", "failed", "not_enabled"}:
            agent_runtime_status = "failed"
        extra_status.append(
            f"agentInstructionRuntime={agent_runtime_status}"
        )
    if review.dynamicPlan is not None:
        dynamic_counts = {key: 0 for key in (
            "selected", "not_applicable", "unavailable")}
        for item in review.dynamicPlan.items:
            if item.status in dynamic_counts:
                dynamic_counts[item.status] += 1
        extra_status.append(
            "dynamic="
            f"selected:{dynamic_counts['selected']},"
            f"not_applicable:{dynamic_counts['not_applicable']},"
            f"unavailable:{dynamic_counts['unavailable']}")
    extra_status_str = (" " + " ".join(extra_status)) if extra_status else ""

    print(f"engine={args.engine} snapshot={snap.snapshotId} "
          f"findings={n_findings} high_or_critical={n_high} "
          f"coverage={review.coverage.status} semantic={semantic_status}"
          f"{extra_status_str} gate={gate}")
    print(f"wrote {out_dir/'report.json'}, {out_dir/'report.html'}, {out_dir/'report.sarif'}")
    return exit_code


def _cmd_project(args: argparse.Namespace) -> int:
    try:
        store=HistoryStore(args.data_dir)
        if args.project_cmd=="create":
            p=store.create_project(args.name,args.alias); print(f'created project {p["displayName"]} alias={p.get("alias") or "-"}')  
        elif args.project_cmd=="list":
            for p in store.list_projects(): print(f'{p["displayName"]}\t{p.get("alias") or "-"}\t{len(p["versionIds"])} versions')
        elif args.project_cmd == "dispose":
            from datetime import datetime, timedelta, timezone
            expiry = datetime.now(timezone.utc) + timedelta(days=args.expiry)
            event = store.add_disposition(
                args.project, args.fingerprint, args.status, expiry, args.note)
            print(f'marked {args.fingerprint[:12]}... as {args.status} '
                  f'until {event["expiryDate"]}')
        elif args.project_cmd == "dispositions":
            disps = store.list_dispositions(args.project)
            if not disps:
                print("no active dispositions")
            else:
                print(f"{len(disps)} active dispositions:")
                for d in disps:
                    fp = d.get("fingerprint", "?")
                    print(f"  {fp[:12]}... {d['status']} '"
                          f"'{d.get('note', '')}' expires {d['expiryDate']}")
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
            
            # Check dispositions if requested
            if args.respect_dispositions:
                dispositions = store._effective_dispositions(p["artifactId"])
                accepted_high = 0
                for f in review.findings:
                    if (f.severity in ("high", "critical")
                            and f.findingOccurrenceFingerprint in dispositions
                            and dispositions[
                                f.findingOccurrenceFingerprint]["status"] == "accept_risk"):
                        accepted_high += 1
                effective_high = high - accepted_high
            else:
                effective_high = high
            
            if effective_high:
                gate, exit_code = "findings_block", 1
            elif review.coverage.status != "sufficient":
                gate, exit_code = "coverage_block", 3
            else:
                gate, exit_code = "pass", 0
            info = (f'recorded review={rec["reviewId"]} '
                    f'coverage={rec["coverage"]["status"]} '
                    f'high_or_critical={high}')
            if args.respect_dispositions and high > effective_high:
                info += f' (accepted_risk={high - effective_high})'
            print(f'{info} gate={gate}')
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

    pr = sub.add_parser("review", help="Run a local read-only V1 preview review")
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
    pr.add_argument("--semantic", action=argparse.BooleanOptionalAction,
                    default=True,
                    help=("Attempt the experimental semantic review "
                          "(default ON). Requires a configured Provider; "
                          "without one, the run honestly reports "
                          "provider_not_configured and this does not by "
                          "itself change the exit code. Pass "
                          "--no-semantic to skip semantic review entirely."))
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
    provider.add_argument(
        "--semantic-provider-kind",
        choices=["json_http", "openai_compatible"], default="json_http",
        help=("'json_http' (default) speaks Verity's own "
              "POST {base_url}/v1/verity/{candidate-generator,validator} "
              "contract. 'openai_compatible' speaks POST "
              "{base_url}/chat/completions, matching OpenRouter, OpenAI, "
              "and most self-hosted OpenAI-compatible gateways."))
    provider.add_argument("--semantic-generator-url")
    provider.add_argument("--semantic-generator-model")
    provider.add_argument("--semantic-generator-api-key-env")
    provider.add_argument("--semantic-validator-url")
    provider.add_argument("--semantic-validator-model")
    provider.add_argument("--semantic-validator-api-key-env")
    provider.add_argument(
        "--semantic-validator-vote", action="append", default=[],
        metavar="URL,MODEL,API_KEY_ENV",
        help=("Add one more independent Validator vote from a DIFFERENT "
              "model (repeatable, up to 4 additional votes on top of the "
              "required --semantic-validator-* above -- 2-3 total votes "
              "across different models is the recommended range). Every "
              "candidate is judged by all configured validators and the "
              "outcome is decided by majority; a tie becomes "
              "insufficient_evidence. Comma-separated URL,MODEL,API_KEY_ENV "
              "using the same --semantic-provider-kind wire contract as "
              "the primary validator."))
    provider.add_argument("--semantic-timeout", type=float, default=30.0)
    provider.add_argument("--semantic-max-output-tokens", type=int, default=800,
                          help="only used with --semantic-provider-kind openai_compatible")

    blackbox = pr.add_argument_group(
        "V1.5 Prompt black-box (research stage, --engine prompt only)",
        "Explicit opt-in only: default OFF, and the reviewed prompt can "
        "never turn this on itself. When enabled, Verity sends the "
        "reviewed prompt to a REAL model endpoint under adversarial "
        "scenarios. Credentials are read only from the named environment "
        "variable; do not pass a key on the command line.")
    blackbox.add_argument(
        "--enable-prompt-blackbox", action="store_true", default=False,
        help="Opt in to the V1.5 black-box evaluation stage (default OFF).")
    blackbox.add_argument("--blackbox-base-url")
    blackbox.add_argument("--blackbox-model")
    blackbox.add_argument("--blackbox-api-key-env")
    blackbox.add_argument(
        "--blackbox-scenario-id", action="append", default=[],
        help=("Repeatable. Supplying ids forces explicit policy; otherwise "
              "the default artifact-aware policy runs only applicable checks."))
    blackbox.add_argument(
        "--blackbox-scenario-policy",
        choices=["artifact_aware", "all", "explicit"],
        default="artifact_aware",
        help=("Scenario selection policy. artifact_aware is the default; "
              "all preserves historical research behavior; explicit "
              "requires --blackbox-scenario-id."))
    blackbox.add_argument("--blackbox-max-calls", type=int, default=50)
    blackbox.add_argument("--blackbox-timeout", type=float, default=30.0)
    blackbox.add_argument("--blackbox-max-tokens", type=int, default=800)

    sandbox = pr.add_argument_group(
        "V2 Skill sandbox (currently unavailable; --engine skill only)",
        "The current product release does not execute the reviewed Skill. "
        "An explicit request fails closed with "
        "sandbox_isolation_hardening_required until the host-read, output, "
        "disk, process-tree, and observer-integrity boundaries are hardened.")
    sandbox.add_argument(
        "--enable-skill-sandbox", action="store_true", default=False,
        help=("Record an explicit V2 request; it currently fails closed "
              "without executing the Skill (default OFF)."))
    sandbox.add_argument("--sandbox-entry-point")
    sandbox.add_argument(
        "--sandbox-argv", action="append", default=[],
        help="Repeatable, appended in order after the entry point.")
    sandbox.add_argument("--sandbox-cpu-seconds", type=int, default=10)
    sandbox.add_argument("--sandbox-memory-mb", type=int, default=256)
    sandbox.add_argument("--sandbox-wall-seconds", type=int, default=20)

    agent_runtime = pr.add_argument_group(
        "Agent-instruction runtime (--engine skill only)",
        "Explicit opt-in only: default OFF. The caller supplies exact Node "
        "and DSH paths and SHA-256 pins plus the trusted model endpoint and "
        "API-key environment-variable name; the reviewed Skill cannot set "
        "these values.",
    )
    agent_runtime.add_argument(
        "--enable-agent-runtime",
        action="store_true",
        default=False,
        help="Opt in to the agent-instruction runtime stage (default OFF).",
    )
    agent_runtime.add_argument("--agent-runtime-node-path")
    agent_runtime.add_argument("--agent-runtime-node-sha256")
    agent_runtime.add_argument("--agent-runtime-dsh-path")
    agent_runtime.add_argument("--agent-runtime-dsh-sha256")
    agent_runtime.add_argument("--agent-runtime-version")
    agent_runtime.add_argument("--agent-runtime-base-url")
    agent_runtime.add_argument("--agent-runtime-model")
    agent_runtime.add_argument("--agent-runtime-api-key-env")
    agent_runtime.add_argument(
        "--agent-runtime-scenario-id",
        action="append",
        default=[],
        help=("Repeatable. When supplied, replaces the two default bounded "
              "agent-runtime scenarios in caller order."),
    )
    agent_runtime.add_argument("--agent-runtime-timeout")

    pr.set_defaults(func=_cmd_review)

    pp=sub.add_parser("project",help="Trusted local Skill project history")
    pp.add_argument("--data-dir")
    psub=pp.add_subparsers(dest="project_cmd",required=True)
    pc=psub.add_parser("create"); pc.add_argument("--name",required=True); pc.add_argument("--alias")
    psub.add_parser("list")
    prj=psub.add_parser("review"); prj.add_argument("--project",required=True); prj.add_argument("--input-dir",required=True); prj.add_argument("--profile",choices=["standard","minimal"],default="standard"); prj.add_argument("--respect-dispositions",action="store_true",help="Accept-risk dispositions prevent gate failure")
    pd=psub.add_parser("diff"); pd.add_argument("--project",required=True); pd.add_argument("--previous"); pd.add_argument("--current")
    pdisp=psub.add_parser("dispose"); pdisp.add_argument("--project",required=True); pdisp.add_argument("--fingerprint",required=True); pdisp.add_argument("--status",choices=["acknowledged","accept_risk","false_positive","wont_fix"],required=True); pdisp.add_argument("--expiry",type=int,default=30,help="Days until expiry (default 30, max 180)"); pdisp.add_argument("--note",help="Optional note (max 200 chars)")
    psub.add_parser("dispositions").add_argument("--project",required=True)
    pp.set_defaults(func=_cmd_project)

    ps = sub.add_parser("export-schema", help="Export JSON Schema (Draft 2020-12)")
    ps.add_argument("--out")
    ps.set_defaults(func=_cmd_export_schema)

    cli_args = list(sys.argv[1:] if argv is None else argv)
    if cli_args[:1] == ["review"]:
        for token in cli_args[1:]:
            if token == "--":
                break
            option = token.partition("=")[0]
            if (
                option.startswith("--")
                and option not in _AGENT_RUNTIME_CLI_FLAGS
                and any(
                    exact_flag.startswith(option)
                    for exact_flag in _AGENT_RUNTIME_CLI_FLAGS
                )
            ):
                pr.error(f"unrecognized agent-runtime flag: {option}")

    args = p.parse_args(cli_args)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
