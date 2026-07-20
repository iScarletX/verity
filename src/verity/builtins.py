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
    # Manifest / metadata findings
    ftr.register(FindingTypeDefinition(
        findingType="skill.manifest_issue",
        engine="skill",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("manifestIssueCategory", "literal_enum",
                         allowedValues=["missing_skill_md"]),
        ],
        subjectKeyFields=["artifactPath", "manifestIssueCategory"],
        defaultSeverity="high",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="skill.manifest_parse_failure",
        engine="skill",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("parseErrorCode", "literal_enum", allowedValues=[
                "frontmatter_not_closed", "yaml_parse_error",
                "yaml_root_not_mapping", "yaml_too_deep",
                "yaml_too_many_keys", "frontmatter_over_budget",
                "frontmatter_too_many_lines",
                "frontmatter_alias_bomb_suspected",
                "yaml_unexpected_error",
            ]),
        ],
        subjectKeyFields=["artifactPath", "parseErrorCode"],
        defaultSeverity="high",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="skill.manifest_field_issue",
        engine="skill",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("fieldName", "literal_enum",
                         allowedValues=["name", "description"]),
            SubjectField("fieldIssue", "literal_enum",
                         allowedValues=["missing", "blank", "invalid_syntax"]),
        ],
        subjectKeyFields=["artifactPath", "fieldName", "fieldIssue"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="skill.manifest_reference_issue",
        engine="skill",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("referencePath", "evidence_field", "reference.path"),
            SubjectField("referenceIssue", "literal_enum", allowedValues=[
                "not_found", "absolute_path", "path_escape",
                "backslash_path", "suffix_mismatch",
            ]),
            SubjectField("declaredPath", "evidence_field", "reference.declared_path"),
            SubjectField("foundPath", "evidence_field", "reference.found_path"),
        ],
        subjectKeyFields=["artifactPath", "referencePath", "referenceIssue"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="skill.manifest_dependency_issue",
        engine="skill",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("dependencyName", "evidence_field", "dependency.name"),
            SubjectField("dependencyIssue", "literal_enum",
                         allowedValues=["unpinned"]),
        ],
        subjectKeyFields=["artifactPath", "dependencyName", "dependencyIssue"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="skill.manifest_permission_wildcard",
        engine="skill",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("permissionValue", "evidence_field", "permission.value"),
            SubjectField("permissionIssue", "literal_enum",
                         allowedValues=["wildcard_or_root"]),
        ],
        subjectKeyFields=["artifactPath", "permissionValue", "permissionIssue"],
        defaultSeverity="high",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="skill.manifest_external_instructions",
        engine="skill",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("externalInstructionUrl", "evidence_field",
                         "external_instructions.url"),
        ],
        subjectKeyFields=["artifactPath", "externalInstructionUrl"],
        defaultSeverity="high",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="skill.python_subprocess_shell_true",
        engine="skill",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("callee", "evidence_field", "call.callee"),
        ],
        subjectKeyFields=["artifactPath", "callee"],
        defaultSeverity="high",
        requiredEvidenceKinds=["source_span"],
    ))
    # existing file-level rules from round 1 remain below --------------
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
    # S1
    rr.register(RuleDefinition(
        ruleId="skill.missing_skill_md",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title="Artifact is missing SKILL.md — no machine-readable manifest to review.",
        findingType="skill.manifest_issue",
        implementationId="impl.skill.missing_skill_md.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="high", controlIds=["OWASP-AST04"],
        owaspAst10=["OWASP-AST04"],
    ))
    # S2
    rr.register(RuleDefinition(
        ruleId="skill.manifest_parse_failure",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title="SKILL.md frontmatter failed to parse or exceeded a safety budget.",
        findingType="skill.manifest_parse_failure",
        implementationId="impl.skill.manifest_parse_failure.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="high", controlIds=["OWASP-AST04"],
        owaspAst10=["OWASP-AST04"],
    ))
    # S3 name
    rr.register(RuleDefinition(
        ruleId="skill.manifest_name_issue",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title="Manifest `name` is missing, blank, or has invalid syntax.",
        findingType="skill.manifest_field_issue",
        implementationId="impl.skill.manifest_name_issue.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium", controlIds=["OWASP-AST04"],
        owaspAst10=["OWASP-AST04"], requiresManifest=True,
    ))
    # S4 description
    rr.register(RuleDefinition(
        ruleId="skill.manifest_description_missing",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title="Manifest `description` is missing or blank.",
        findingType="skill.manifest_field_issue",
        implementationId="impl.skill.manifest_description_missing.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium", controlIds=["OWASP-AST04"],
        owaspAst10=["OWASP-AST04"], requiresManifest=True,
    ))
    # S5 missing reference
    rr.register(RuleDefinition(
        ruleId="skill.manifest_missing_reference",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title=("Manifest references a local file that is not present in the "
               "artifact snapshot."),
        findingType="skill.manifest_reference_issue",
        implementationId="impl.skill.manifest_missing_reference.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium", controlIds=["OWASP-AST04"],
        owaspAst10=["OWASP-AST04"], requiresManifest=True,
    ))
    # S6 unsafe reference path
    rr.register(RuleDefinition(
        ruleId="skill.manifest_unsafe_reference_path",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title=("Manifest reference uses an absolute path, `..` escape, or a "
               "back-slash separator."),
        findingType="skill.manifest_reference_issue",
        implementationId="impl.skill.manifest_unsafe_reference_path.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="high", controlIds=["OWASP-AST04"],
        owaspAst10=["OWASP-AST04"], requiresManifest=True,
    ))
    # S7 unpinned dependencies
    rr.register(RuleDefinition(
        ruleId="skill.manifest_unpinned_dependency",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title=("Manifest dependency version is not pinned to a specific "
               "release (e.g. floating range, `latest`, `*`, missing)."),
        findingType="skill.manifest_dependency_issue",
        implementationId="impl.skill.manifest_unpinned_dependency.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium", controlIds=["OWASP-AST02"],
        owaspAst10=["OWASP-AST02", "OWASP-AST07"], requiresManifest=True,
    ))
    # S8 permission wildcard
    rr.register(RuleDefinition(
        ruleId="skill.manifest_permission_wildcard",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title=("Manifest permission grants an open-ended wildcard or root "
               "path (e.g. '*', '/', '**')."),
        findingType="skill.manifest_permission_wildcard",
        implementationId="impl.skill.manifest_permission_wildcard.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="high", controlIds=["OWASP-AST03"],
        owaspAst10=["OWASP-AST03"], requiresManifest=True,
    ))
    # S9 external instructions
    rr.register(RuleDefinition(
        ruleId="skill.manifest_external_instructions",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title=("Manifest declares an external URL as a runtime instruction "
               "source (fetch_and_follow / runtime_fetch mode)."),
        findingType="skill.manifest_external_instructions",
        implementationId="impl.skill.manifest_external_instructions.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="high", controlIds=["OWASP-AST05"],
        owaspAst10=["OWASP-AST05"], requiresManifest=True,
    ))
    # S10 suffix mismatch
    rr.register(RuleDefinition(
        ruleId="skill.manifest_script_suffix_mismatch",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title=("Manifest declares a script path whose suffix does not match "
               "the actual file present in the artifact."),
        findingType="skill.manifest_reference_issue",
        implementationId="impl.skill.manifest_script_suffix_mismatch.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium", controlIds=["OWASP-AST04"],
        owaspAst10=["OWASP-AST04"], requiresManifest=True,
    ))
    # S11 subprocess shell=True
    rr.register(RuleDefinition(
        ruleId="skill.python_subprocess_shell_true",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title=("Python source contains a subprocess.* call with shell=True. "
               "This is a mechanically detected dangerous pattern; the code "
               "is NOT executed by Verity."),
        findingType="skill.python_subprocess_shell_true",
        implementationId="impl.skill.python_subprocess_shell_true.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="high", controlIds=["OWASP-AST01"],
        owaspAst10=["OWASP-AST01"],
    ))
    # legacy file-level rules -----------------------------------------
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
        controlIds=["OWASP-AST02"],
        owaspAst10=["OWASP-AST02"],
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
        controlIds=["OWASP-AST01"],
        owaspAst10=["OWASP-AST01"],
    ))
    return rr
