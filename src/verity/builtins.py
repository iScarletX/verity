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
    # --- Prompt engine ---------------------------------------------------
    ftr.register(FindingTypeDefinition(
        findingType="prompt.instruction_override_marker",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("markerCategory", "literal_enum",
                         allowedValues=["instruction_override"]),
        ],
        subjectKeyFields=["artifactPath", "markerCategory"],
        defaultSeverity="low",   # risk marker, not proven attack
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.unfilled_placeholder",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("placeholderCategory", "literal_enum",
                         allowedValues=["mustache", "dollar_brace",
                                        "angle_bracket", "square_bracket"]),
        ],
        subjectKeyFields=["artifactPath", "placeholderCategory"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.system_hardcoded_secret",
        engine="prompt",
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
        findingType="prompt.duplicate_numeric_assignment",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("keyName", "evidence_field", "assignment.key"),
        ],
        subjectKeyFields=["artifactPath", "keyName"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.control_character",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("controlCategory", "literal_enum",
                         allowedValues=["control_char", "bidi_override"]),
        ],
        subjectKeyFields=["artifactPath", "controlCategory"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.empty_or_whitespace",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("emptyCategory", "literal_enum",
                         allowedValues=["empty_or_whitespace"]),
        ],
        subjectKeyFields=["artifactPath", "emptyCategory"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.open_ended_tool_wildcard",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("wildcardCategory", "literal_enum",
                         allowedValues=["tool_wildcard"]),
        ],
        subjectKeyFields=["artifactPath", "wildcardCategory"],
        defaultSeverity="high",
        requiredEvidenceKinds=["source_span"],
    ))
    # --- Skill engine ---------------------------------------------------
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
        title=("Text contains a well-known instruction-override marker. "
               "This is a RISK SIGNAL, not a proven attack; the marker "
               "may be a benign quotation. Fenced/inline code is excluded."),
        findingType="prompt.instruction_override_marker",
        implementationId="impl.prompt.jailbreak_marker.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="low",
        controlIds=["OWASP-LLM-01"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.unfilled_placeholder",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("Prompt contains what appears to be an unfilled template "
               "placeholder ({{...}}, ${...}, <TODO>, [INSERT ...]). "
               "Fenced/inline code excluded."),
        findingType="prompt.unfilled_placeholder",
        implementationId="impl.prompt.unfilled_placeholder.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium",
        controlIds=["quality.template"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.system_hardcoded_secret",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("System prompt contains a hardcoded secret-like token. Any "
               "secret embedded in a system prompt is directly extractable "
               "and should be moved out of the prompt."),
        findingType="prompt.system_hardcoded_secret",
        implementationId="impl.prompt.system_hardcoded_secret.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="high",
        controlIds=["OWASP-LLM-06"],
        applicablePromptKinds=["system_prompt"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.duplicate_numeric_assignment",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("Same parameter key is assigned two different numeric "
               "values in strict `key: N` / `key = N` form."),
        findingType="prompt.duplicate_numeric_assignment",
        implementationId="impl.prompt.duplicate_numeric_assignment.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium",
        controlIds=["quality.consistency"],
        evidencePerFinding=2,
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.control_character",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("Prompt text contains an ASCII control character or Unicode "
               "bidi override. Bidi overrides are a documented prompt-"
               "injection vector; other control chars are usually copy/"
               "paste accidents."),
        findingType="prompt.control_character",
        implementationId="impl.prompt.control_character.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium",
        controlIds=["OWASP-LLM-01"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.empty_or_whitespace",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title="Prompt is empty or whitespace-only.",
        findingType="prompt.empty_or_whitespace",
        implementationId="impl.prompt.empty_or_whitespace.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium",
        controlIds=["quality.non_empty"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.open_ended_tool_wildcard",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("System prompt authorises tools via wildcard in a strict, "
               "machine-parseable form (allowed_tools: * / permissions: "
               "[\"*\"] / tools: [\"*\"])."),
        findingType="prompt.open_ended_tool_wildcard",
        implementationId="impl.prompt.open_ended_tool_wildcard.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="high",
        controlIds=["OWASP-AST-03"],
        applicablePromptKinds=["system_prompt"],
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
