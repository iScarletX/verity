"""Artifact-aware selection of bounded dynamic checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

from verity.blackbox.scenarios import list_scenarios

from .profile import ArtifactBehaviorProfile, ProfileFact


@dataclass(frozen=True)
class DynamicCheckDefinition:
    check_id: str
    stage: str
    risk_ids: Tuple[str, ...]
    mode: str
    requires_all: Tuple[str, ...] = ()
    requires_any: Tuple[str, ...] = ()
    excludes: Tuple[str, ...] = ()
    scenario_id: Optional[str] = None
    severity: str = "medium"
    unavailable_reason: Optional[str] = None


@dataclass(frozen=True)
class DynamicPlanItem:
    check_id: str
    stage: str
    status: str
    mode: str
    reason_codes: Tuple[str, ...]
    supporting_fact_ids: Tuple[str, ...]
    risk_ids: Tuple[str, ...]
    scenario_id: Optional[str] = None


@dataclass(frozen=True)
class DynamicReviewPlan:
    items: Tuple[DynamicPlanItem, ...]
    policy: str = "artifact_aware"
    schema_version: str = "verity.dynamic-plan.v1"

    def item(self, check_id: str) -> DynamicPlanItem:
        for entry in self.items:
            if entry.check_id == check_id:
                return entry
        raise KeyError(check_id)


_SCENARIO_REQUIREMENTS = {
    "injection_override_simple": {
        "requires_any": ("has:external_content", "has:tools", "has:side_effects"),
    },
    "injection_override_roleplay": {
        "requires_any": ("has:external_content", "has:tools", "has:side_effects"),
    },
    "system_prompt_extraction": {
        "requires_any": ("has:sensitive_data", "constraint:prompt_confidentiality"),
    },
    "authority_impersonation": {
        "requires_any": ("has:tools", "has:side_effects", "has:state"),
    },
    "output_format_compliance": {
        "requires_all": ("constraint:bullet_output",),
    },
    "topic_boundary_enforcement": {
        "requires_all": ("constraint:topic_boundary",),
    },
    "multi_turn_context_drift": {"requires_all": ("has:state",)},
    "skill_boundary_bypass": {"requires_all": ("constraint:role_scope",)},
    "upstream_dependency_skip": {"requires_all": ("has:inputs",)},
    "state_injection": {"requires_all": ("has:state",)},
    "output_contract_violation": {
        "requires_all": ("constraint:json_output",),
    },
    "confidential_reference_leak": {
        "requires_any": ("has:sensitive_data", "constraint:reference_confidentiality"),
    },
    "image_content_safety": {
        "requires_all": ("constraint:image_content_safety",),
    },
    "untrusted_content_instruction_injection": {
        "requires_all": ("has:external_content",),
    },
    "encoded_payload_instruction_injection": {
        "requires_any": ("has:external_content", "has:tools"),
    },
    "synthetic_sensitive_data_disclosure_request": {
        "requires_all": ("has:sensitive_data",),
    },
    "tool_authorization_scope_expansion": {"requires_all": ("has:tools",)},
    "unverifiable_claim_confabulation_probe": {
        "requires_all": ("constraint:evidence_required",),
    },
    "excessive_reproduction_request": {
        "requires_all": ("constraint:reproduction_policy",),
    },
    "internal_rule_reference_confabulation_probe": {
        "requires_all": ("constraint:internal_reference_accuracy",),
    },
    "missing_required_input_confabulation": {"requires_all": ("has:inputs",)},
    "prior_call_failure_confabulation_probe": {"requires_all": ("has:tools",)},
    "malformed_input_silent_accept_probe": {"requires_all": ("has:inputs",)},
    "boundary_value_silent_accept_probe": {"requires_all": ("has:inputs",)},
}


def _blackbox_definitions() -> Tuple[DynamicCheckDefinition, ...]:
    definitions = []
    separately_defined = {
        "embedded_credential_extraction_request",
        "autonomous_side_effect_without_approval",
    }
    for scenario in list_scenarios():
        if scenario.scenario_id in separately_defined:
            continue
        requirements = _SCENARIO_REQUIREMENTS[scenario.scenario_id]
        definitions.append(DynamicCheckDefinition(
            check_id=scenario.scenario_id,
            stage="prompt_blackbox",
            risk_ids=tuple(scenario.risk_ids),
            mode=(
                "functional_contract"
                if scenario.scenario_id == "output_format_compliance"
                else "active_probe"
            ),
            requires_all=requirements.get("requires_all", ()),
            requires_any=requirements.get("requires_any", ()),
            scenario_id=scenario.scenario_id,
            severity=scenario.severity,
        ))
    return tuple(definitions)


CHECK_DEFINITIONS = (
    DynamicCheckDefinition(
        check_id="agent_instruction.runtime",
        stage="agent_runtime",
        risk_ids=("VR-SKILL-012",),
        mode="runtime_adapter",
        requires_all=("runtime:agent_instruction",),
        unavailable_reason="agent_runtime_not_configured",
    ),
    DynamicCheckDefinition(
        check_id="director.required_input_handling",
        stage="prompt_blackbox",
        risk_ids=("VR-PROMPT-016",),
        mode="functional_contract",
        requires_all=("domain:director_storyboard", "input:script"),
    ),
    DynamicCheckDefinition(
        check_id="director.shot_contract",
        stage="prompt_blackbox",
        risk_ids=("VR-PROMPT-006",),
        mode="functional_contract",
        requires_all=("domain:director_storyboard", "output:shot_list"),
    ),
    DynamicCheckDefinition(
        check_id="director.duration_budget",
        stage="prompt_blackbox",
        risk_ids=("VR-PROMPT-023",),
        mode="functional_contract",
        requires_all=("domain:director_storyboard", "output:shot_list"),
    ),
    DynamicCheckDefinition(
        check_id="director.content_preservation",
        stage="prompt_blackbox",
        risk_ids=("VR-PROMPT-006",),
        mode="functional_contract",
        requires_all=("domain:director_storyboard", "constraint:content_preservation"),
    ),
    DynamicCheckDefinition(
        check_id="director.revision_state",
        stage="prompt_blackbox",
        risk_ids=("VR-PROMPT-027",),
        mode="functional_contract",
        requires_all=("domain:director_storyboard", "state:continuity"),
    ),
    DynamicCheckDefinition(
        check_id="art_style.required_subject",
        stage="prompt_blackbox",
        risk_ids=("VR-PROMPT-016",),
        mode="functional_contract",
        requires_all=("domain:art_style",),
    ),
    DynamicCheckDefinition(
        check_id="art_style.prompt_contract",
        stage="prompt_blackbox",
        risk_ids=("VR-PROMPT-006",),
        mode="functional_contract",
        requires_all=(
            "domain:art_style",
            "output:positive_prompt",
            "output:negative_prompt",
        ),
    ),
    DynamicCheckDefinition(
        check_id="art_style.subject_preservation",
        stage="prompt_blackbox",
        risk_ids=("VR-PROMPT-006",),
        mode="functional_contract",
        requires_all=("domain:art_style", "constraint:subject_preservation"),
    ),
    DynamicCheckDefinition(
        check_id="art_style.term_conflict",
        stage="prompt_blackbox",
        risk_ids=("VR-PROMPT-006",),
        mode="functional_contract",
        requires_all=(
            "domain:art_style",
            "output:positive_prompt",
            "output:negative_prompt",
        ),
    ),
    DynamicCheckDefinition(
        check_id="art_style.visual_fidelity",
        stage="image_runtime",
        risk_ids=("VR-PROMPT-006",),
        mode="runtime_adapter",
        requires_all=("domain:art_style",),
        unavailable_reason="image_runtime_not_configured",
    ),
    DynamicCheckDefinition(
        check_id="embedded_credential_extraction_request",
        stage="prompt_blackbox",
        risk_ids=("VR-PROMPT-003",),
        mode="active_probe",
        requires_any=("has:sensitive_data", "tool:credential_access"),
        scenario_id="embedded_credential_extraction_request",
        severity="high",
    ),
    DynamicCheckDefinition(
        check_id="autonomous_side_effect_without_approval",
        stage="prompt_blackbox",
        risk_ids=("VR-PROMPT-012",),
        mode="active_probe",
        requires_all=("has:side_effects",),
        scenario_id="autonomous_side_effect_without_approval",
        severity="high",
    ),
) + _blackbox_definitions() + (
    DynamicCheckDefinition(
        check_id="sandbox_write_outside_tmpdir",
        stage="skill_sandbox",
        risk_ids=("VR-SKILL-002",),
        mode="passive_invariant",
    ),
    DynamicCheckDefinition(
        check_id="sandbox_network_attempt",
        stage="skill_sandbox",
        risk_ids=("VR-SKILL-009",),
        mode="passive_invariant",
    ),
    DynamicCheckDefinition(
        check_id="sandbox_subprocess_attempt",
        stage="skill_sandbox",
        risk_ids=("VR-SKILL-006",),
        mode="passive_invariant",
    ),
    DynamicCheckDefinition(
        check_id="sandbox_sensitive_path_read",
        stage="skill_sandbox",
        risk_ids=("VR-SKILL-014",),
        mode="passive_invariant",
    ),
    DynamicCheckDefinition(
        check_id="sandbox_undeclared_network_attempt",
        stage="skill_sandbox",
        risk_ids=("VR-SKILL-004", "VR-SKILL-012", "VR-PROMPT-007"),
        mode="passive_invariant",
    ),
    DynamicCheckDefinition(
        check_id="sandbox_undeclared_subprocess_attempt",
        stage="skill_sandbox",
        risk_ids=("VR-SKILL-004", "VR-SKILL-012", "VR-PROMPT-007"),
        mode="passive_invariant",
    ),
    DynamicCheckDefinition(
        check_id="sandbox_cleartext_network_attempt",
        stage="skill_sandbox",
        risk_ids=("VR-SKILL-008",),
        mode="passive_invariant",
    ),
    DynamicCheckDefinition(
        check_id="sandbox_dependency_install_attempt",
        stage="skill_sandbox",
        risk_ids=("VR-SKILL-003",),
        mode="passive_invariant",
    ),
    DynamicCheckDefinition(
        check_id="sandbox_fake_credential_read",
        stage="skill_sandbox",
        risk_ids=("VR-SKILL-011",),
        mode="active_probe",
        requires_any=("has:sensitive_data", "tool:credential_access"),
    ),
    DynamicCheckDefinition(
        check_id="sandbox_injected_content_propagation",
        stage="skill_sandbox",
        risk_ids=("VR-SKILL-005", "VR-PROMPT-008", "VR-SKILL-013", "VR-SKILL-010"),
        mode="active_probe",
        requires_all=("has:external_content", "has:tools"),
    ),
    DynamicCheckDefinition(
        check_id="sandbox_deserialization_effect",
        stage="skill_sandbox",
        risk_ids=("VR-SKILL-007",),
        mode="active_probe",
        requires_all=("tool:deserialization",),
    ),
    DynamicCheckDefinition(
        check_id="sandbox_sql_injected_query",
        stage="skill_sandbox",
        risk_ids=("VR-SKILL-015",),
        mode="active_probe",
        requires_all=("tool:database",),
    ),
)


def _profile_tokens(profile: ArtifactBehaviorProfile) -> Tuple[str, ...]:
    tokens = [f"runtime:{profile.runtime_kind}"]
    for prefix, generic, values in (
        ("domain", "has:domain", profile.domain_tags),
        ("input", "has:inputs", profile.inputs),
        ("output", "has:outputs", profile.outputs),
        ("constraint", "has:constraints", profile.constraints),
        ("tool", "has:tools", profile.tool_families),
        ("state", "has:state", profile.state_requirements),
        ("side_effect", "has:side_effects", profile.side_effects),
        ("sensitive_data", "has:sensitive_data", profile.sensitive_data),
    ):
        tokens.extend(f"{prefix}:{value}" for value in values)
        if values:
            tokens.append(generic)
    if profile.external_content:
        tokens.append("has:external_content")
    return tuple(tokens)


def _fact_token(fact: ProfileFact) -> str:
    if fact.kind == "external_content":
        return "has:external_content"
    prefix = {
        "domain": "domain",
        "input": "input",
        "output": "output",
        "constraint": "constraint",
        "tool_family": "tool",
        "state_requirement": "state",
        "side_effect": "side_effect",
        "sensitive_data": "sensitive_data",
    }.get(fact.kind, fact.kind)
    return f"{prefix}:{fact.value}"


def _supporting_facts(
    profile: ArtifactBehaviorProfile, matched_tokens: Iterable[str]
) -> Tuple[str, ...]:
    matched = set(matched_tokens)
    result = []
    generic_by_prefix = {
        "domain": "has:domain",
        "input": "has:inputs",
        "output": "has:outputs",
        "constraint": "has:constraints",
        "tool": "has:tools",
        "state": "has:state",
        "side_effect": "has:side_effects",
        "sensitive_data": "has:sensitive_data",
    }
    for fact in profile.facts:
        token = _fact_token(fact)
        generic = (
            "has:external_content"
            if token == "has:external_content"
            else generic_by_prefix.get(token.split(":", 1)[0], "")
        )
        if token in matched or generic in matched:
            result.append(fact.fact_id)
    return tuple(result)


def build_dynamic_plan(
    profile: ArtifactBehaviorProfile,
    *,
    available_runtime_adapters: Tuple[str, ...] = (),
) -> DynamicReviewPlan:
    tokens = set(_profile_tokens(profile))
    items = []
    for definition in CHECK_DEFINITIONS:
        passive = definition.mode == "passive_invariant"
        runtime_adapter = definition.mode == "runtime_adapter"
        runtime_adapter_available = (
            definition.check_id == "agent_instruction.runtime"
            and "agent_instruction" in available_runtime_adapters
        )
        has_all = all(requirement in tokens for requirement in definition.requires_all)
        has_any = (not definition.requires_any
                   or any(requirement in tokens for requirement in definition.requires_any))
        excluded = any(requirement in tokens for requirement in definition.excludes)
        requirements_matched = has_all and has_any and not excluded
        requires_executable = definition.stage == "skill_sandbox"
        runtime_matched = (
            not requires_executable
            or profile.runtime_kind == "executable_skill"
        )
        selected = (
            requirements_matched
            and runtime_matched
            and (not runtime_adapter or runtime_adapter_available)
        )
        status = (
            "unavailable"
            if runtime_adapter and requirements_matched and not selected
            else "selected"
            if selected
            else "not_applicable"
        )
        matched_tokens = [
            requirement
            for requirement in (*definition.requires_all, *definition.requires_any)
            if requirement in tokens
        ]
        items.append(DynamicPlanItem(
            check_id=definition.check_id,
            stage=definition.stage,
            status=status,
            mode=definition.mode,
            reason_codes=(
                definition.unavailable_reason or "runtime_adapter_not_configured"
                if status == "unavailable"
                else "runtime_adapter_available"
                if selected and runtime_adapter
                else "executable_skill_passive_observer"
                if selected and passive
                else "runtime_kind_not_executable"
                if requires_executable and not runtime_matched
                else "profile_requirements_matched"
                if selected
                else "profile_requirements_not_matched",
            ),
            supporting_fact_ids=(
                _supporting_facts(profile, matched_tokens)
                if status in {"selected", "unavailable"} else ()
            ),
            risk_ids=definition.risk_ids,
            scenario_id=definition.scenario_id,
        ))
    return DynamicReviewPlan(items=tuple(items))


def selected_scenario_ids(plan: DynamicReviewPlan) -> Tuple[str, ...]:
    return tuple(
        item.scenario_id
        for item in plan.items
        if item.status == "selected" and item.scenario_id is not None
    )
