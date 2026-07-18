"""Built-in FindingType and Rule definitions.

Kept small on purpose — this is the walking skeleton. Later phases (see
README) will add more rules (bandit / semgrep / gitleaks integration per
the reuse decision table).
"""

from __future__ import annotations

from .registry import (
    FindingTypeDefinition, FindingTypeRegistry, RuleDefinition,
    RuleRegistry, SubjectField,
)


def build_finding_type_registry() -> FindingTypeRegistry:
    ftr = FindingTypeRegistry()
    ftr.register(FindingTypeDefinition(
        findingType="prompt.instruction_override_marker",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("markerCategory", "literal_enum",
                         allowedValues=["instruction_override"]),
        ],
        subjectKeyFields=["artifactPath", "markerCategory"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="skill.fake_secret_fixture",
        engine="skill",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("secretCategory", "literal_enum",
                         allowedValues=["fake_fixture_secret"]),
        ],
        subjectKeyFields=["artifactPath", "secretCategory"],
        defaultSeverity="high",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="skill.dangerous_shell_pattern",
        engine="skill",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("shellPatternCategory", "literal_enum",
                         allowedValues=["dangerous_shell"]),
        ],
        subjectKeyFields=["artifactPath", "shellPatternCategory"],
        defaultSeverity="high",
        requiredEvidenceKinds=["source_span"],
    ))
    return ftr


def build_prompt_rule_registry(ftr: FindingTypeRegistry) -> RuleRegistry:
    rr = RuleRegistry(ftr)
    rr.register(RuleDefinition(
        ruleId="prompt.instruction_override_marker",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title="Prompt contains a well-known instruction-override / jailbreak marker.",
        findingType="prompt.instruction_override_marker",
        implementationId="impl.prompt.jailbreak_marker.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium",
        controlIds=["OWASP-LLM-01"],
    ))
    return rr


def build_skill_rule_registry(ftr: FindingTypeRegistry) -> RuleRegistry:
    rr = RuleRegistry(ftr)
    rr.register(RuleDefinition(
        ruleId="skill.fake_secret_fixture",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="skill",
        title="Skill contains a secret-like fixture token.",
        findingType="skill.fake_secret_fixture",
        implementationId="impl.skill.fake_secret.v1",
        applicableKinds=["skill"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="high",
        controlIds=["OWASP-AST-03"],
    ))
    rr.register(RuleDefinition(
        ruleId="skill.dangerous_shell_pattern",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="skill",
        title="Skill text contains a dangerous shell pattern (curl|sh, rm -rf /, forkbomb).",
        findingType="skill.dangerous_shell_pattern",
        implementationId="impl.skill.dangerous_shell.v1",
        applicableKinds=["skill"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="high",
        controlIds=["OWASP-AST-01"],
    ))
    return rr
