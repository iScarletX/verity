from verity.blackbox.scenarios import list_scenarios
from verity.blackbox.config import BlackboxConfig
from verity.dynamic.planner import CHECK_DEFINITIONS, build_dynamic_plan
from verity.dynamic.profile import ArtifactBehaviorProfile, ProfileFact
from verity.sandbox.models import SANDBOX_SIGNAL_DETECTORS
from verity.standards import load_detector_mappings


def _fact(kind: str, value: str, index: int) -> ProfileFact:
    return ProfileFact(
        fact_id=f"pf-test-{index}",
        kind=kind,
        value=value,
        source_path="prompt.txt",
        start_byte=index,
        end_byte=index + 1,
    )


def test_art_style_prompt_skips_unrelated_active_attacks():
    profile = ArtifactBehaviorProfile(
        runtime_kind="prompt",
        domain_tags=("art_style",),
        outputs=("positive_prompt", "negative_prompt"),
        constraints=("subject_preservation",),
        facts=(
            _fact("domain", "art_style", 1),
            _fact("output", "positive_prompt", 2),
            _fact("output", "negative_prompt", 3),
            _fact("constraint", "subject_preservation", 4),
        ),
    )

    plan = build_dynamic_plan(profile)

    assert plan.item("art_style.prompt_contract").status == "selected"
    assert plan.item("output_format_compliance").status == "not_applicable"
    assert plan.item("output_contract_violation").status == "not_applicable"
    assert plan.item("skill_boundary_bypass").status == "not_applicable"
    assert plan.item("embedded_credential_extraction_request").status == "not_applicable"
    assert plan.item("autonomous_side_effect_without_approval").status == "not_applicable"
    assert all(item.reason_codes for item in plan.items)
    assert plan.item("art_style.prompt_contract").supporting_fact_ids == (
        "pf-test-1", "pf-test-2", "pf-test-3"
    )


def test_legacy_format_and_scope_probes_require_their_exact_contracts():
    profile = ArtifactBehaviorProfile(
        runtime_kind="prompt",
        constraints=("bullet_output", "json_output", "role_scope"),
        facts=(
            _fact("constraint", "bullet_output", 1),
            _fact("constraint", "json_output", 2),
            _fact("constraint", "role_scope", 3),
        ),
    )

    plan = build_dynamic_plan(profile)

    assert plan.item("output_format_compliance").status == "selected"
    assert plan.item("output_contract_violation").status == "selected"
    assert plan.item("skill_boundary_bypass").status == "selected"


def test_executable_skill_arms_passive_network_observers():
    profile = ArtifactBehaviorProfile(
        runtime_kind="executable_skill",
        tool_families=("network_access",),
        facts=(
            ProfileFact(
                fact_id="pf-network",
                kind="tool_family",
                value="network_access",
                source_path="scripts/main.py",
                start_byte=0,
                end_byte=0,
                confidence="deterministic_observed",
            ),
        ),
    )

    plan = build_dynamic_plan(profile)

    network = plan.item("sandbox_network_attempt")
    undeclared = plan.item("sandbox_undeclared_network_attempt")
    assert network.status == "selected"
    assert undeclared.status == "selected"
    assert network.mode == "passive_invariant"
    assert network.reason_codes == ("executable_skill_passive_observer",)


def test_exact_agent_instruction_adapter_availability_selects_matching_profile():
    profile = ArtifactBehaviorProfile(runtime_kind="agent_instruction")

    plan = build_dynamic_plan(
        profile,
        available_runtime_adapters=("agent_instruction",),
    )

    runtime = plan.item("agent_instruction.runtime")
    assert runtime.status == "selected"
    assert runtime.reason_codes == ("runtime_adapter_available",)


def test_other_adapter_ids_do_not_select_runtime_checks():
    agent_profile = ArtifactBehaviorProfile(runtime_kind="agent_instruction")
    art_profile = ArtifactBehaviorProfile(
        runtime_kind="prompt",
        domain_tags=("art_style",),
    )

    agent_plan = build_dynamic_plan(
        agent_profile,
        available_runtime_adapters=(
            "agent-instruction",
            "agent_instruction.runtime",
            "image_runtime",
        ),
    )
    art_plan = build_dynamic_plan(
        art_profile,
        available_runtime_adapters=("agent_instruction", "image_runtime"),
    )

    assert agent_plan.item("agent_instruction.runtime").status == "unavailable"
    assert agent_plan.item("agent_instruction.runtime").reason_codes == (
        "agent_runtime_not_configured",
    )
    assert art_plan.item("art_style.visual_fidelity").status == "unavailable"
    assert art_plan.item("art_style.visual_fidelity").reason_codes == (
        "image_runtime_not_configured",
    )


def test_dynamic_registry_covers_every_runtime_scenario_and_signal():
    registered_scenarios = {
        definition.scenario_id
        for definition in CHECK_DEFINITIONS
        if definition.scenario_id is not None
    }
    registered_sandbox_signals = {
        definition.check_id
        for definition in CHECK_DEFINITIONS
        if definition.stage == "skill_sandbox"
    }

    assert registered_scenarios == {
        scenario.scenario_id for scenario in list_scenarios()
    }
    assert registered_sandbox_signals == set(SANDBOX_SIGNAL_DETECTORS)


def test_dynamic_registry_has_unique_checks_and_standard_risk_mappings():
    mappings = load_detector_mappings()
    keys = [definition.check_id for definition in CHECK_DEFINITIONS]

    assert len(keys) == len(set(keys))
    for definition in CHECK_DEFINITIONS:
        if definition.scenario_id is not None:
            standard = mappings[("blackbox_scenario", definition.scenario_id)]
            assert definition.risk_ids == tuple(standard["riskIds"])
        elif definition.stage == "skill_sandbox":
            standard = mappings[("sandbox_signal", definition.check_id)]
            assert definition.risk_ids == tuple(standard["riskIds"])


def test_generated_functional_checks_have_standard_mappings_but_unavailable_adapters_do_not():
    mappings = load_detector_mappings()
    generated = [
        definition for definition in CHECK_DEFINITIONS
        if definition.stage == "prompt_blackbox"
        and definition.scenario_id is None
    ]
    unavailable = [
        definition for definition in CHECK_DEFINITIONS
        if definition.mode == "runtime_adapter"
    ]

    for definition in generated:
        mapping = mappings[("blackbox_scenario", definition.check_id)]
        assert definition.risk_ids == tuple(mapping["riskIds"])
    assert all(
        ("blackbox_scenario", definition.check_id) not in mappings
        for definition in unavailable
    )


def test_blackbox_scenario_policy_defaults_to_artifact_aware():
    assert BlackboxConfig().scenario_policy == "artifact_aware"


def test_explicit_scenario_policy_requires_ids():
    import pytest

    with pytest.raises(ValueError, match="explicit.*scenario_ids"):
        BlackboxConfig(scenario_policy="explicit")


def test_nonempty_scenario_ids_force_explicit_policy():
    config = BlackboxConfig(
        scenario_policy="all",
        scenario_ids=("output_format_compliance",),
    )

    assert config.scenario_policy == "explicit"


def test_unknown_scenario_policy_is_rejected():
    import pytest

    with pytest.raises(ValueError, match="scenario_policy"):
        BlackboxConfig(scenario_policy="surprise")
