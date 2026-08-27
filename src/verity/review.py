"""Review orchestration — Snapshot → Plan → Execute → Coverage → Findings.

The deterministic path always runs independently. Controlled semantic review
is an optional, default-OFF post-stage with trusted configuration, egress,
schema and budget gates; it never filters or rewrites deterministic Findings.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from .builtins import (
    build_finding_type_registry,
    build_prompt_rule_registry,
    build_skill_rule_registry,
)
from .engine import DEFAULT_IMPLEMENTATIONS, Engine
from .models import (
    ArtifactSnapshot, CoverageAssessment, Finding, ReviewPlan,
    Review, EvidenceRecord, RuleMatchEvent, ExecutionRecord,
)


# Skill review profiles. `standard` requires gitleaks; `minimal` explicitly
# opts out and marks Secret coverage as user-declined in the ReviewPlan.
SKILL_PROFILES = ("standard", "minimal")


@dataclass
class ReviewInputs:
    engine: str  # "prompt" or "skill"
    snapshot: ArtifactSnapshot
    file_bytes: Dict[str, bytes]
    profile: str = "standard"  # skill-engine only
    # Optional semantic-review switch. ``None`` = default (off).  This is
    # kept as ``Any`` to avoid an import cycle; run_review re-imports the
    # real type.
    semantic_config: Optional[object] = None
    # Optional V1.5 Prompt black-box switch. ``None`` = default (stage
    # never runs). When set, must be a ``verity.blackbox.BlackboxConfig``
    # instance AND ``engine == "prompt"``; run_review additionally
    # requires ``config.enabled`` to be True before making any outbound
    # call (a default-constructed BlackboxConfig() is a safe no-op). Kept
    # as ``object`` for the same import-cycle-avoidance reason as
    # ``semantic_config``.
    blackbox_config: Optional[object] = None
    # Optional V2 Skill sandbox compatibility request. ``None`` = not
    # requested. When set, it must be a SandboxConfig on the Skill engine;
    # enabled requests fail closed before any runner import/construction.
    sandbox_config: Optional[object] = None
    # Optional agent-instruction runtime switch. ``None`` leaves the default
    # path fully inert; a supplied object is validated inline by run_review.
    agent_runtime_config: Optional[object] = None


_AGENT_SKILL_NAME_RE = re.compile(r"\A[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_MAX_AGENT_SKILL_NAME_CHARS = 64
_LOWER_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_AGENT_RUNTIME_STATUSES = frozenset(
    {"not_enabled", "completed", "failed", "timeout"}
)
_AGENT_RUNTIME_SCENARIO_OUTCOMES = frozenset(
    {"completed", "failed", "timeout"}
)
_AGENT_RUNTIME_TOOL_NAMES = frozenset(
    {"read_file", "send_http", "run_shell", "request_approval"}
)
_AGENT_RUNTIME_TARGET_CLASSES = frozenset(
    {
        "project_public",
        "untrusted_external",
        "synthetic_sensitive",
        "network",
        "shell",
        "approval",
        "other",
    }
)
_AGENT_RUNTIME_TOOL_OUTCOMES = frozenset(
    {"completed", "not_found", "blocked", "denied"}
)
_AGENT_RUNTIME_REASON_CODES = frozenset(
    {
        "agent_runtime_failed",
        "agent_runtime_invalid_skill_name",
        "agent_runtime_process_control_failed",
        "agent_runtime_process_failed",
        "agent_runtime_process_start_failed",
        "agent_runtime_skill_load_failed",
        "agent_runtime_skill_load_invalid",
        "agent_runtime_skill_load_missing",
        "agent_runtime_snapshot_bytes_mismatch",
        "agent_runtime_snapshot_digest_mismatch",
        "agent_runtime_snapshot_entry_not_file",
        "agent_runtime_snapshot_parent_collision",
        "agent_runtime_snapshot_path_collision",
        "agent_runtime_snapshot_path_invalid",
        "agent_runtime_staging_failed",
        "agent_runtime_stderr_limit_exceeded",
        "agent_runtime_stdout_limit_exceeded",
        "agent_runtime_trace_invalid",
        "agent_runtime_trace_overflow",
        "agent_runtime_unknown_scenario",
        "agent_runtime_wall_clock_exceeded",
        "dsh_executable_not_executable",
        "dsh_executable_not_file",
        "dsh_executable_not_found",
        "dsh_executable_unreadable",
        "dsh_identity_unstable",
        "dsh_sha256_mismatch",
        "dsh_version_check_failed",
        "dsh_version_check_timeout",
        "dsh_version_mismatch",
        "dsh_version_output_exceeded",
        "dsh_version_process_control_failed",
        "node_executable_not_executable",
        "node_executable_not_file",
        "node_executable_not_found",
        "node_executable_unreadable",
        "node_identity_unstable",
        "node_sha256_mismatch",
    }
)
_MAX_AGENT_RUNTIME_DURATION_SECONDS = 900.0
_MAX_AGENT_RUNTIME_STREAM_BYTES = 64 * 1024 * 1024


def _build_engine(name: str, *, bandit_runner=None, gitleaks_runner=None,
                  profile: str = "standard") -> Engine:
    ftr = build_finding_type_registry()
    parser = None
    analyzers = []
    if name == "prompt":
        rr = build_prompt_rule_registry(ftr)
    elif name == "skill":
        rr = build_skill_rule_registry(ftr)
        from .parser import parse_skill
        parser = parse_skill
        # Bandit analyzer. Default: a real BanditRunner subprocess call.
        # Tests may inject a stub via ``bandit_runner``.
        if bandit_runner is None:
            from .bandit_runner import BanditRunner
            bandit_runner = BanditRunner()

        def _run_bandit(snapshot, file_bytes):
            br = bandit_runner.run_on_snapshot(snapshot, file_bytes)
            updates = {"banditRun": {
                "status": br.status,
                "toolName": br.toolName,
                "toolVersion": br.toolVersion,
                "exitCode": br.exitCode,
                "durationSeconds": br.durationSeconds,
                "stagedFileCount": br.stagedFileCount,
                "pathMap": br.pathMap,
                "results": br.results,
                "reasonCode": br.reasonCode,
            }}
            if br.status == "completed":
                return updates, "completed", None
            if br.status == "timeout":
                return updates, "failed", f"bandit:{br.reasonCode}"
            if br.status == "version_mismatch":
                return updates, "failed", f"bandit:{br.reasonCode}"
            return updates, "failed", f"bandit:{br.reasonCode or 'unknown'}"

        analyzers.append({
            "componentId": "bandit",
            "componentVersion": "1.7.10",
            "gatingClass": "normal",
            "run": _run_bandit,
        })

        if profile == "minimal":
            # Explicit user opt-out. We STILL record the analyzer as a
            # plan item so its absence is visible in Coverage/reports.
            def _run_gitleaks_skipped(snapshot, file_bytes):
                updates = {"gitleaksRun": {
                    "status": "not_requested_by_profile",
                    "toolName": "gitleaks",
                    "toolVersion": "",
                    "reasonCode": "minimal_profile_selected",
                }}
                return updates, "not_applicable", "minimal_profile:secret_scan_skipped"

            analyzers.append({
                "componentId": "gitleaks",
                "componentVersion": "required-when-standard",
                "gatingClass": "critical",
                "run": _run_gitleaks_skipped,
            })
        else:
            if gitleaks_runner is None:
                from .gitleaks_runner import GitleaksRunner
                gitleaks_runner = GitleaksRunner()

            def _run_gitleaks(snapshot, file_bytes):
                gr = gitleaks_runner.run_on_snapshot(snapshot, file_bytes)
                updates = {"gitleaksRun": {
                    "status": gr.status,
                    "toolName": gr.toolName,
                    "toolVersion": gr.toolVersion,
                    "toolPath": gr.toolPath,
                    "toolSha256": gr.toolSha256,
                    "exitCode": gr.exitCode,
                    "durationSeconds": gr.durationSeconds,
                    "stagedFileCount": gr.stagedFileCount,
                    "pathMap": gr.pathMap,
                    "results": gr.results,   # already redacted by runner
                    "reasonCode": gr.reasonCode,
                }}
                if gr.status == "completed":
                    return updates, "completed", None
                return updates, "failed", f"gitleaks:{gr.reasonCode or gr.status}"

            analyzers.append({
                "componentId": "gitleaks",
                "componentVersion": "8.28.0",
                "gatingClass": "critical",
                "run": _run_gitleaks,
            })
    else:
        raise ValueError(f"unknown engine: {name}")
    return Engine(name, rr, ftr, DEFAULT_IMPLEMENTATIONS, parser=parser,
                  analyzers=analyzers)


def run_review(ri: ReviewInputs, *, bandit_runner=None,
               gitleaks_runner=None,
               candidate_generator=None, validator=None,
               validators=None,
               agent_runtime_runner=None) -> Review:
    """``validators``, if given, is a list of independently configured
    Validator Provider objects (e.g. 2-3 different models) that each cast
    one vote per candidate; see ``SemanticOrchestrator.run``. ``validator``
    (singular) remains supported and is equivalent to ``validators=[validator]``.
    """
    if ri.profile not in SKILL_PROFILES:
        raise ValueError(f"unknown profile: {ri.profile}")
    agent_runtime_config = None
    if ri.agent_runtime_config is not None:
        from .agent_runtime.config import AgentRuntimeConfig

        if not isinstance(ri.agent_runtime_config, AgentRuntimeConfig):
            raise TypeError(
                "agent_runtime_config must be an AgentRuntimeConfig instance"
            )
        if ri.engine != "skill":
            raise ValueError(
                "agent_runtime_config is only applicable to engine='skill'"
            )
        agent_runtime_config = ri.agent_runtime_config
    engine = _build_engine(ri.engine, bandit_runner=bandit_runner,
                           gitleaks_runner=gitleaks_runner,
                           profile=ri.profile)
    evidences, events, findings, plan_items, executions, artifact_model = engine.run(
        ri.snapshot, ri.file_bytes
    )
    if ri.engine == "skill":
        from .capabilities import extract_capability_facts
        artifact_model["capabilityFacts"] = extract_capability_facts(
            ri.snapshot, ri.file_bytes, artifact_model.get("manifest"))
    from .dynamic.planner import build_dynamic_plan
    from .dynamic.profile import extract_behavior_profile
    behavior_profile = extract_behavior_profile(
        engine=ri.engine,
        snapshot=ri.snapshot,
        file_bytes=ri.file_bytes,
        artifact_model=artifact_model or {},
    )
    dynamic_plan = build_dynamic_plan(
        behavior_profile,
        available_runtime_adapters=(
            ("agent_instruction",)
            if agent_runtime_config is not None and agent_runtime_config.enabled
            else ()
        ),
    )
    review_id = f"r-{uuid.uuid4().hex[:12]}"
    plan_id = f"rp-{uuid.uuid4().hex[:12]}"
    plan = ReviewPlan(
        reviewPlanId=plan_id, reviewId=review_id,
        revision=1, phase="initial", expansionDepth=0,
        items=plan_items,
    )

    # Coverage: check every required, critical plan item has completed status.
    reason_codes: List[str] = []
    critical_gaps: List[str] = []
    # §9.2: `completed` OR `not_applicable` (with declared gate reason) both
    # satisfy the plan; `failed` / `blocked_by_upstream_failure` do not.
    ok_statuses = {"completed", "not_applicable"}
    ok_ids = {e.planItemId for e in executions if e.status in ok_statuses}
    for pi in plan_items:
        if pi.planItemId not in ok_ids:
            if pi.gatingClass == "critical":
                critical_gaps.append(pi.planItemId)
                reason_codes.append(f"critical_plan_item_not_completed:{pi.planItemId}")
            else:
                reason_codes.append(f"plan_item_not_completed:{pi.planItemId}")
    status = "sufficient" if not critical_gaps and not reason_codes else (
        "insufficient" if critical_gaps or reason_codes else "sufficient")
    coverage = CoverageAssessment(
        coverageAssessmentId=f"cov-{uuid.uuid4().hex[:12]}",
        reviewId=review_id, reviewPlanId=plan_id, reviewPlanRevision=1,
        status=status,  # type: ignore[arg-type]
        criticalGapPlanItemIds=critical_gaps,
        reasonCodes=reason_codes,
    )
    # Semantic sub-pipeline (default OFF; reads deterministic projection
    # only, never mutates it). Import inline so deterministic engine can
    # continue to run in environments where semantic isn't wanted.
    semantic_view: Optional[Dict[str, Any]] = None
    if ri.semantic_config is not None:
        from .semantic import SemanticConfig  # type: ignore
        from .semantic.orchestrator import SemanticOrchestrator
        cfg = ri.semantic_config
        if not isinstance(cfg, SemanticConfig):
            raise TypeError("semantic_config must be a SemanticConfig instance")
        # Build a lightweight review-dict projection for the orchestrator.
        # We intentionally do NOT reuse report.review_to_dict here to keep
        # the deterministic module free of any semantic dependency.
        from dataclasses import asdict as _asdict
        proj = {
            "reviewId": review_id,
            "engine": ri.engine,
            "snapshot": _asdict(ri.snapshot),
            "artifactModel": artifact_model,
        }
        # Attach evidences (dict form) for the orchestrator's extractors.
        # They produce their own Evidence anyway; but downstream may want.
        proj["evidences"] = [_asdict(e) for e in evidences]
        orch = SemanticOrchestrator(cfg)
        sem_result = orch.run(proj, ri.file_bytes,
                              generator=candidate_generator,
                              validator=validator,
                              validators=validators)
        semantic_view = _semantic_view(sem_result)

    # V1.5 Prompt black-box stage (default OFF; explicit two-gate opt-in,
    # see ReviewInputs.blackbox_config / BlackboxConfig docstrings). Only
    # ever applicable to the prompt engine -- it sends the reviewed
    # system/user prompt to a real model under attack scenarios.
    blackbox_view: Optional[Dict[str, Any]] = None
    if ri.blackbox_config is not None:
        from .blackbox.config import BlackboxConfig  # type: ignore
        cfg = ri.blackbox_config
        if not isinstance(cfg, BlackboxConfig):
            raise TypeError("blackbox_config must be a BlackboxConfig instance")
        if ri.engine != "prompt":
            raise ValueError(
                "blackbox_config is only applicable to engine='prompt'")
        blackbox_view = _run_prompt_blackbox_stage(
            cfg, ri.snapshot, ri.file_bytes, behavior_profile, dynamic_plan)

    # V2 Skill sandbox request (default OFF). It is accepted only for the
    # Skill engine and currently fails closed before entry-point validation or
    # runner construction; SandboxConfig cannot enable code execution in this
    # release.
    sandbox_view: Optional[Dict[str, Any]] = None
    if ri.sandbox_config is not None:
        from .sandbox.config import SandboxConfig  # type: ignore
        cfg = ri.sandbox_config
        if not isinstance(cfg, SandboxConfig):
            raise TypeError("sandbox_config must be a SandboxConfig instance")
        if ri.engine != "skill":
            raise ValueError(
                "sandbox_config is only applicable to engine='skill'")
        sandbox_view = _run_skill_sandbox_stage(
            cfg, ri.snapshot, ri.file_bytes, behavior_profile, dynamic_plan)

    agent_runtime_view: Optional[Dict[str, Any]] = None
    if agent_runtime_config is not None:
        agent_runtime_view = _run_agent_instruction_runtime_stage(
            agent_runtime_config,
            ri.snapshot,
            ri.file_bytes,
            behavior_profile,
            artifact_model,
            agent_runtime_runner=agent_runtime_runner,
        )

    return Review(
        reviewId=review_id,
        artifactSnapshot=ri.snapshot,
        engine=ri.engine,  # type: ignore[arg-type]
        plan=plan,
        executions=executions,
        coverage=coverage,
        evidences=evidences,
        ruleMatches=events,
        findings=findings,
        behaviorProfile=behavior_profile,
        dynamicPlan=dynamic_plan,
        artifactModel=artifact_model,
        semantic=semantic_view,
        promptBlackbox=blackbox_view,
        skillSandbox=sandbox_view,
        agentInstructionRuntime=agent_runtime_view,
    )


def _extract_single_file_text(snapshot, file_bytes: Dict[str, bytes]) -> Optional[str]:
    """Return the decoded text of the Prompt engine's one included file, or
    ``None`` if the snapshot does not look like exactly one text file (should
    never happen for a snapshot built by ``intake_text``, but the black-box
    stage never trusts that invariant blindly)."""
    included = [f for f in snapshot.files
               if f.status == "included" and f.entryType == "file"]
    if len(included) != 1:
        return None
    data = file_bytes.get(included[0].fileId)
    if data is None:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _run_prompt_blackbox_stage(
    cfg, snapshot, file_bytes: Dict[str, bytes], behavior_profile, dynamic_plan,
) -> Dict[str, Any]:
    """Run (or honestly decline to run) the V1.5 black-box stage.

    Returns a dict with at least a ``status`` key using the
    not_enabled/completed/failed vocabulary that ``report.py``'s
    capability matrix expects verbatim (no further translation needed,
    unlike the legacy multi-value semantic status).
    """
    if not cfg.enabled:
        return {"status": "not_enabled", "reasonCode": "disabled_by_config"}

    from .blackbox.scenarios import get_scenario, list_scenarios
    from .dynamic.planner import selected_scenario_ids
    from .dynamic.scenarios import build_artifact_scenarios

    if cfg.scenario_policy == "explicit":
        scenarios = []
        for sid in cfg.scenario_ids:
            scenario = get_scenario(sid)
            if scenario is None:
                return {
                    "status": "failed",
                    "reasonCode": f"unknown_scenario:{sid}",
                    "scenarioPolicy": cfg.scenario_policy,
                    "plannedScenarioCount": 0,
                }
            scenarios.append(scenario)
    elif cfg.scenario_policy == "all":
        scenarios = list_scenarios()
    else:
        scenarios = []
        for scenario_id in selected_scenario_ids(dynamic_plan):
            scenario = get_scenario(scenario_id)
            if scenario is not None:
                scenarios.append(scenario)
        scenarios.extend(build_artifact_scenarios(
            behavior_profile, dynamic_plan))

    base_view = {
        "scenarioPolicy": cfg.scenario_policy,
        "plannedScenarioCount": len(scenarios),
    }
    if not cfg.is_provider_configured():
        return dict(base_view, status="failed",
                    reasonCode="provider_not_configured")
    api_key = cfg.credentials.resolve()
    if not api_key:
        return dict(base_view, status="failed",
                    reasonCode="api_key_env_not_set")
    system_prompt = _extract_single_file_text(snapshot, file_bytes)
    if system_prompt is None:
        return dict(base_view, status="failed",
                    reasonCode="prompt_text_unavailable")
    if not scenarios:
        return dict(
            base_view,
            status="failed",
            reasonCode="blackbox_no_scenarios_selected",
        )

    from .blackbox.runner import run_blackbox

    result = run_blackbox(
        system_prompt=system_prompt,
        scenarios=scenarios,
        base_url=cfg.base_url,
        model_id=cfg.model_id,
        api_key=api_key,
        max_calls=cfg.max_calls,
        timeout_seconds=cfg.timeout_seconds,
        max_tokens_per_response=cfg.max_tokens_per_response,
    )
    from dataclasses import asdict as _asdict
    failure_reason: Optional[str] = None
    if result.budget_exhausted:
        failure_reason = "budget_exhausted"
    elif (
        result.total_scenarios != len(scenarios)
        or result.completed_scenarios != len(scenarios)
        or len(result.scenario_results) != len(scenarios)
    ):
        failure_reason = "blackbox_result_incomplete"
    else:
        expected_calls = 0
        has_probe_error = bool(result.errors)
        has_inconclusive = False
        for expected, observed in zip(scenarios, result.scenario_results):
            expected_calls += len(expected.probes)
            if (
                observed.scenario_id != expected.scenario_id
                or not expected.probes
                or len(observed.probe_results) != len(expected.probes)
            ):
                failure_reason = "blackbox_result_incomplete"
                break
            if any(
                probe.error_code is not None or probe.safe is None
                for probe in observed.probe_results
            ):
                has_probe_error = True
            oracle_outcome = getattr(observed.oracle_result, "outcome", None)
            if expected.trace_judge is not None and oracle_outcome is None:
                has_probe_error = True
            if oracle_outcome in {"insufficient_evidence", "unavailable"}:
                has_inconclusive = True
        if failure_reason is None and result.total_calls != expected_calls:
            failure_reason = "blackbox_result_incomplete"
        elif failure_reason is None and has_probe_error:
            failure_reason = "blackbox_probe_error"
        elif failure_reason is None and has_inconclusive:
            failure_reason = "blackbox_inconclusive"

    status = "failed" if failure_reason is not None else "completed"
    return dict(base_view, **{
        "status": status,
        "reasonCode": failure_reason,
        "model": result.model_id,
        "systemPromptDigest": result.system_prompt_digest,
        "summary": result.summary(),
        "scenarioResults": [_asdict(sr) for sr in result.scenario_results],
        "totalCalls": result.total_calls,
        "errors": list(result.errors),
    })


def _run_skill_sandbox_stage(
    cfg, snapshot, file_bytes: Dict[str, bytes], behavior_profile, dynamic_plan,
) -> Dict[str, Any]:
    """Fail closed until the product V2 isolation boundary is hardened.

    The prototype implementation remains only as internal research code and
    direct unit-test material. Product review must not construct it: it does
    not yet provide the host/process/output/disk guarantees required for
    untrusted Skill execution.
    """
    if not cfg.enabled:
        return {"status": "not_enabled", "reasonCode": "disabled_by_config"}
    return {
        "status": "failed",
        "observationStatus": "unavailable",
        "reasonCode": "sandbox_isolation_hardening_required",
    }


def _valid_agent_skill_name(value: object, parent_name: object) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= _MAX_AGENT_SKILL_NAME_CHARS
        and _AGENT_SKILL_NAME_RE.fullmatch(value) is not None
        and isinstance(parent_name, str)
        and value == parent_name
    )


def _project_agent_runtime_observation(observation, cfg) -> Dict[str, Any]:
    import math

    from .agent_runtime.models import (
        AgentRuntimeScenarioResult,
        AgentRuntimeToolEvent,
    )

    observation_status = observation.status
    if (
        type(observation_status) is not str
        or observation_status not in _AGENT_RUNTIME_STATUSES
    ):
        raise ValueError("invalid agent runtime observation status")
    reason_code = observation.reasonCode
    if reason_code is not None and (
        type(reason_code) is not str
        or reason_code not in _AGENT_RUNTIME_REASON_CODES
    ):
        raise ValueError("invalid agent runtime observation reason")
    if observation_status in {"completed", "not_enabled"}:
        if reason_code is not None:
            raise ValueError("unexpected agent runtime observation reason")
    elif reason_code is None:
        raise ValueError("missing agent runtime observation reason")
    if (
        observation_status == "timeout"
        and reason_code != "agent_runtime_wall_clock_exceeded"
    ):
        raise ValueError("invalid agent runtime timeout reason")

    if observation.harnessName is not None and (
        type(observation.harnessName) is not str
        or observation.harnessName != "dsh"
    ):
        raise ValueError("invalid agent runtime harness name")
    if observation.harnessVersion is not None and (
        type(observation.harnessVersion) is not str
        or observation.harnessVersion != cfg.expected_version
    ):
        raise ValueError("invalid agent runtime harness version")
    harness_sha256 = observation.harnessSha256
    if harness_sha256 is not None and (
        type(harness_sha256) is not str
        or _LOWER_SHA256_RE.fullmatch(harness_sha256) is None
    ):
        raise ValueError("invalid agent runtime harness digest")
    duration_seconds = observation.durationSeconds
    if duration_seconds is not None and (
        isinstance(duration_seconds, bool)
        or not isinstance(duration_seconds, (int, float))
        or not math.isfinite(duration_seconds)
        or not 0 <= duration_seconds <= _MAX_AGENT_RUNTIME_DURATION_SECONDS
    ):
        raise ValueError("invalid agent runtime duration")
    for byte_count in (observation.stdoutBytes, observation.stderrBytes):
        if (
            type(byte_count) is not int
            or not 0 <= byte_count <= _MAX_AGENT_RUNTIME_STREAM_BYTES
        ):
            raise ValueError("invalid agent runtime stream byte count")

    truncated = observation.truncated
    if type(truncated) is not dict or not set(truncated).issubset(
        {"stdout", "stderr", "traceEvents"}
    ):
        raise TypeError("invalid agent runtime truncation flags")
    if any(type(value) is not bool for value in truncated.values()):
        raise TypeError("invalid agent runtime truncation flag")

    scenarios = observation.scenarioResults
    if type(scenarios) is not tuple or len(scenarios) > len(cfg.scenario_ids):
        raise TypeError("invalid agent runtime scenario results")
    scenario_results = []
    scenario_ids = []
    for scenario in scenarios:
        if type(scenario) is not AgentRuntimeScenarioResult:
            raise TypeError("invalid agent runtime scenario result")
        if (
            type(scenario.scenario_id) is not str
            or scenario.scenario_id not in cfg.scenario_ids
        ):
            raise ValueError("invalid agent runtime scenario id")
        scenario_ids.append(scenario.scenario_id)
        if (
            type(scenario.outcome) is not str
            or scenario.outcome not in _AGENT_RUNTIME_SCENARIO_OUTCOMES
        ):
            raise ValueError("invalid agent runtime scenario outcome")
        if type(scenario.reason_codes) is not tuple:
            raise TypeError("invalid agent runtime scenario reasons")
        if len(scenario.reason_codes) > 1 or any(
            type(reason) is not str
            or reason not in _AGENT_RUNTIME_REASON_CODES
            for reason in scenario.reason_codes
        ):
            raise ValueError("invalid agent runtime scenario reason")
        if scenario.outcome == "completed" and scenario.reason_codes:
            raise ValueError("unexpected completed scenario reason")
        if scenario.outcome != "completed" and len(scenario.reason_codes) != 1:
            raise ValueError("missing incomplete scenario reason")
        if (
            scenario.outcome == "timeout"
            and scenario.reason_codes != ("agent_runtime_wall_clock_exceeded",)
        ):
            raise ValueError("invalid scenario timeout reason")
        response_digest = scenario.response_digest
        if response_digest is not None and (
            type(response_digest) is not str
            or _LOWER_SHA256_RE.fullmatch(response_digest) is None
        ):
            raise ValueError("invalid agent runtime response digest")
        if scenario.outcome != "completed" and response_digest is not None:
            raise ValueError("unexpected incomplete scenario digest")
        if type(scenario.tool_events) is not tuple:
            raise TypeError("invalid agent runtime tool events")
        if len(scenario.tool_events) > cfg.max_trace_events:
            raise ValueError("too many agent runtime tool events")
        tool_events = []
        for event in scenario.tool_events:
            if type(event) is not AgentRuntimeToolEvent:
                raise TypeError("invalid agent runtime tool event")
            if (
                type(event.tool_name) is not str
                or event.tool_name not in _AGENT_RUNTIME_TOOL_NAMES
                or type(event.target_class) is not str
                or event.target_class not in _AGENT_RUNTIME_TARGET_CLASSES
                or type(event.outcome) is not str
                or event.outcome not in _AGENT_RUNTIME_TOOL_OUTCOMES
                or type(event.canary_present) is not bool
            ):
                raise ValueError("invalid agent runtime tool event fields")
            tool_events.append({
                "tool_name": event.tool_name,
                "target_class": event.target_class,
                "outcome": event.outcome,
                "canary_present": event.canary_present,
            })
        scenario_results.append({
            "scenario_id": scenario.scenario_id,
            "outcome": scenario.outcome,
            "reason_codes": list(scenario.reason_codes),
            "response_digest": response_digest,
            "tool_events": tool_events,
        })

    if tuple(scenario_ids) != cfg.scenario_ids[:len(scenario_ids)]:
        raise ValueError("agent runtime scenarios are out of order")
    if observation_status == "completed":
        if (
            observation.harnessName != "dsh"
            or observation.harnessVersion != cfg.expected_version
            or harness_sha256 != cfg.dsh_sha256.lower()
            or duration_seconds is None
        ):
            raise ValueError("completed observation has invalid harness identity")
        if tuple(scenario_ids) != cfg.scenario_ids or any(
            scenario.outcome != "completed"
            or scenario.response_digest is None
            for scenario in scenarios
        ):
            raise ValueError("completed observation has incomplete scenarios")
        if (
            set(truncated) != {"stdout", "stderr", "traceEvents"}
            or any(truncated.values())
        ):
            raise ValueError("completed observation has invalid truncation flags")
        if (
            observation.stdoutBytes
            > len(cfg.scenario_ids) * cfg.max_stdout_bytes
            or observation.stderrBytes
            > len(cfg.scenario_ids) * cfg.max_stderr_bytes
        ):
            raise ValueError("completed observation exceeded output budget")
    if observation_status == "not_enabled" and scenarios:
        raise ValueError("not-enabled observation contains scenarios")
    status = (
        "completed"
        if observation_status == "completed"
        else "not_enabled"
        if observation_status == "not_enabled"
        else "failed"
    )
    return {
        "status": status,
        "observationStatus": observation_status,
        "reasonCode": reason_code,
        "harnessName": observation.harnessName,
        "harnessVersion": observation.harnessVersion,
        "harnessSha256": observation.harnessSha256,
        "durationSeconds": duration_seconds,
        "scenarioResults": scenario_results,
        "stdoutBytes": observation.stdoutBytes,
        "stderrBytes": observation.stderrBytes,
        "truncated": dict(truncated),
    }


def _run_agent_instruction_runtime_stage(
    cfg,
    snapshot,
    file_bytes: Dict[str, bytes],
    behavior_profile,
    artifact_model,
    *,
    agent_runtime_runner=None,
) -> Dict[str, Any]:
    if not cfg.enabled:
        return {"status": "not_enabled", "reasonCode": "disabled_by_config"}
    if behavior_profile.runtime_kind != "agent_instruction":
        return {
            "status": "failed",
            "observationStatus": "not_applicable",
            "reasonCode": "not_applicable_to_runtime_kind",
        }

    manifest = (artifact_model or {}).get("manifest")
    skill_name = manifest.get("name") if isinstance(manifest, dict) else None
    if not _valid_agent_skill_name(skill_name, snapshot.artifactRootName):
        return {
            "status": "failed",
            "reasonCode": "agent_runtime_manifest_name_invalid",
        }

    try:
        if agent_runtime_runner is None:
            from .agent_runtime.runner import HarnessAgentRuntimeRunner

            agent_runtime_runner = HarnessAgentRuntimeRunner()
        observation = agent_runtime_runner.run(
            config=cfg,
            snapshot=snapshot,
            file_bytes=file_bytes,
            skill_name=skill_name,
        )
    except Exception:
        return {
            "status": "failed",
            "reasonCode": "agent_runtime_adapter_failed",
        }

    from .agent_runtime.models import AgentRuntimeObservation

    if type(observation) is not AgentRuntimeObservation:
        return {
            "status": "failed",
            "reasonCode": "agent_runtime_invalid_observation",
    }
    try:
        return _project_agent_runtime_observation(observation, cfg)
    except Exception:
        return {
            "status": "failed",
            "reasonCode": "agent_runtime_invalid_observation",
        }


def _semantic_view(sem_result) -> Dict[str, Any]:
    from dataclasses import asdict as _asdict
    return {
        "status": sem_result.status,
        "reasonCode": sem_result.reasonCode,
        "egressPolicy": sem_result.egressPolicy,
        "callCounts": dict(sem_result.callCounts),
        "stageStats": {
            finding_type: {
                **dict(stats),
                "validatorStates": dict(stats["validatorStates"]),
            }
            for finding_type, stats in sem_result.stageStats.items()
        },
        "candidates": [_asdict(c) for c in sem_result.candidates],
        "assessments": [_asdict(a) for a in sem_result.assessments],
        "evidences": [dict(e) for e in sem_result.evidences],
        "findings": [_asdict(f) for f in sem_result.findings],
        "planItems": [_asdict(p) for p in sem_result.planItems],
        "payloadAudit": [_asdict(a) for a in sem_result.payloadAudit],
    }
