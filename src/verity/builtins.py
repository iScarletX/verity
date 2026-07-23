"""Built-in deterministic FindingType and Rule definitions.

This registry is intentionally bounded and taxonomy-mapped. Detection breadth
remains signal/partial; adding rules or scanner adapters requires standards,
Corpus and containment evidence rather than an implied completeness claim.
"""

from __future__ import annotations

from .registry import (
    FindingTypeDefinition, FindingTypeRegistry, RuleDefinition,
    RuleRegistry, SubjectField,
)


from typing import List, Tuple


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
                         allowedValues=["control_char", "bidi_override",
                                        "invisible_char"]),
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
    ftr.register(FindingTypeDefinition(
        findingType="prompt.untrusted_input_boundary_undeclared",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("boundaryCategory", "literal_enum",
                         allowedValues=["untrusted_input_boundary_undeclared"]),
        ],
        subjectKeyFields=["artifactPath", "boundaryCategory"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.dangling_section_reference",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("referenceText", "evidence_field", "reference.text"),
        ],
        subjectKeyFields=["artifactPath", "referenceText"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.embedded_system_role_marker",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("markerCategory", "literal_enum",
                         allowedValues=["embedded_system_role_marker"]),
        ],
        subjectKeyFields=["artifactPath", "markerCategory"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.markdown_data_exfiltration",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("exfilCategory", "literal_enum",
                         allowedValues=["markdown_image_querystring"]),
        ],
        subjectKeyFields=["artifactPath", "exfilCategory"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.encoded_injection_payload",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("encodingCategory", "literal_enum",
                         allowedValues=["base64", "hex"]),
        ],
        subjectKeyFields=["artifactPath", "encodingCategory"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.named_dangling_reference",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("referenceText", "evidence_field", "reference.text"),
        ],
        subjectKeyFields=["artifactPath", "referenceText"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.duplicate_content_line",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("duplicateCategory", "literal_enum",
                         allowedValues=["repeated_content_line"]),
        ],
        subjectKeyFields=["artifactPath", "duplicateCategory"],
        defaultSeverity="low",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.fullwidth_mixed",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("widthCategory", "literal_enum",
                         allowedValues=["fullwidth_ascii_variant"]),
        ],
        subjectKeyFields=["artifactPath", "widthCategory"],
        defaultSeverity="low",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.topic_splice",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("spliceCategory", "literal_enum",
                         allowedValues=["style_head_on_agent_body"]),
        ],
        subjectKeyFields=["artifactPath", "spliceCategory"],
        defaultSeverity="medium",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.version_naming_inconsistent",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("entityKey", "evidence_field", "version.entity"),
        ],
        subjectKeyFields=["artifactPath", "entityKey"],
        defaultSeverity="low",
        requiredEvidenceKinds=["source_span"],
    ))
    ftr.register(FindingTypeDefinition(
        findingType="prompt.model_endpoint_no_fallback",
        engine="prompt",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("endpointCategory", "literal_enum",
                         allowedValues=["pinned_no_fallback"]),
        ],
        subjectKeyFields=["artifactPath", "endpointCategory"],
        defaultSeverity="low",
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
                         allowedValues=["name", "description", "compatibility",
                                        "metadata", "allowed-tools"]),
            SubjectField("fieldIssue", "literal_enum",
                         allowedValues=["missing", "blank", "invalid_syntax",
                                        "too_long", "directory_mismatch",
                                        "invalid_type", "invalid_value"]),
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
    # Gitleaks findings (redacted).
    ftr.register(FindingTypeDefinition(
        findingType="skill.gitleaks_finding",
        engine="skill",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("gitleaksRuleId", "evidence_field", "gitleaks.ruleID"),
            SubjectField("lineNumber", "evidence_field", "gitleaks.startLine"),
            SubjectField("secretLengthBucket", "literal_enum",
                         allowedValues=["0", "1-16", "17-32", "33-64",
                                        "65-128", "129+"]),
            SubjectField("entropy", "evidence_field", "gitleaks.entropy"),
        ],
        subjectKeyFields=["artifactPath", "gitleaksRuleId", "lineNumber"],
        defaultSeverity="high",
        requiredEvidenceKinds=["source_span"],
    ))
    # Bandit-normalised findings (one FindingType covers all test_ids).
    ftr.register(FindingTypeDefinition(
        findingType="skill.bandit_finding",
        engine="skill",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("testId", "evidence_field", "bandit.test_id"),
            SubjectField("lineNumber", "evidence_field", "bandit.line_number"),
            SubjectField("banditSeverity", "literal_enum",
                         allowedValues=["low", "medium", "high"]),
            SubjectField("banditConfidence", "literal_enum",
                         allowedValues=["LOW", "MEDIUM", "HIGH", "UNDEFINED"]),
            SubjectField("cwe", "evidence_field", "bandit.cwe"),
        ],
        subjectKeyFields=["artifactPath", "testId", "lineNumber"],
        defaultSeverity="medium",
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
        findingType="skill.sensitive_path_access",
        engine="skill",
        subjectFields=[
            SubjectField("artifactPath", "artifact_model_path", "file.normalizedPath"),
            SubjectField("sensitivePathCategory", "literal_enum",
                         allowedValues=["sensitive_host_path"]),
        ],
        subjectKeyFields=["artifactPath", "sensitivePathCategory"],
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
        controlIds=["OWASP-LLM01:2025"],
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
        controlIds=["OWASP-LLM02:2025", "OWASP-LLM07:2025"],
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
        controlIds=["OWASP-LLM01:2025"],
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
    rr.register(RuleDefinition(
        ruleId="prompt.untrusted_input_boundary_undeclared",
        ruleVersion="1.1.0",
        supersedes=[],
        engine="prompt",
        title=("System prompt declares it accepts external/user-supplied "
               "content but has no explicit trust-boundary or anti-"
               "injection-override statement anywhere in the document."),
        findingType="prompt.untrusted_input_boundary_undeclared",
        implementationId="impl.prompt.untrusted_input_boundary_undeclared.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium",
        controlIds=["OWASP-LLM01:2025"],
        applicablePromptKinds=["system_prompt"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.dangling_section_reference",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("Prompt references a numbered section/rule (e.g. \"see "
               "section 7\"/\"见第7节\") that does not exist anywhere in the "
               "document's own headings."),
        findingType="prompt.dangling_section_reference",
        implementationId="impl.prompt.dangling_section_reference.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium",
        controlIds=["quality.consistency"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.embedded_system_role_marker",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("Prompt text embeds chat-template/system-role control tokens "
               "(e.g. <|im_start|>system, [system](#assistant), <<SYS>>, "
               "{{#system~}}) that can hijack the instruction hierarchy "
               "when the artifact is treated as data. Adapted from vigil-llm "
               "SystemInstructions YARA signatures."),
        findingType="prompt.embedded_system_role_marker",
        implementationId="impl.prompt.embedded_system_role_marker.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium",
        controlIds=["OWASP-LLM01:2025"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.markdown_data_exfiltration",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("Prompt text contains a markdown image whose URL carries a "
               "query string, a known data-exfiltration channel (the model "
               "is induced to render an image URL with secret data in the "
               "query). Adapted from vigil-llm MarkdownExfiltration YARA."),
        findingType="prompt.markdown_data_exfiltration",
        implementationId="impl.prompt.markdown_data_exfiltration.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium",
        controlIds=["OWASP-LLM01:2025"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.encoded_injection_payload",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("Prompt text contains a base64/hex blob that decodes to an "
               "instruction-bypass phrase (encoded hidden-instruction "
               "smuggling). Only fires when the decoded bytes match the "
               "bypass grammar, keeping false positives near zero. Inspired "
               "by NVIDIA garak encoding-injection probes."),
        findingType="prompt.encoded_injection_payload",
        implementationId="impl.prompt.encoded_injection_payload.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium",
        controlIds=["OWASP-LLM01:2025"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.named_dangling_reference",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("Prompt references a NAMED rule/section (e.g. \u201c见回复规则\u201d, "
               "\u201c见输出约定\u201d) whose name never appears as a heading or "
               "definition elsewhere in the document."),
        findingType="prompt.named_dangling_reference",
        implementationId="impl.prompt.named_dangling_reference.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium",
        controlIds=["quality.consistency"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.duplicate_content_line",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("A substantial content line (>=24 chars) appears verbatim more "
               "than once in the prompt, diluting attention and risking "
               "inconsistent edits."),
        findingType="prompt.duplicate_content_line",
        implementationId="impl.prompt.duplicate_content_line.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="low",
        controlIds=["quality.consistency"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.fullwidth_mixed",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("Prompt uses full-width ASCII-variant characters (U+FF01-FF5E) "
               "or ideographic space, which mixed with half-width forms can "
               "break exact field-name/JSON parsing."),
        findingType="prompt.fullwidth_mixed",
        implementationId="impl.prompt.fullwidth_mixed.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="low",
        controlIds=["quality.consistency"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.topic_splice",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("An image/media style description is spliced onto the head of "
               "an agent system prompt (cross-domain head vs body). "
               "Deterministic, dependency-free approximation of neural "
               "topic-coherence checks; requires style-head + agent-body + "
               "near-zero lexical overlap to fire."),
        findingType="prompt.topic_splice",
        implementationId="impl.prompt.topic_splice.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium",
        controlIds=["quality.consistency"],
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.version_naming_inconsistent",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("The same entity is referred to with inconsistent version "
               "forms (e.g. \"v2.0\" vs \"version 2\" vs \"2.0.0\"). Only "
               "fires when the forms differ but the numeric version is "
               "prefix-compatible for the SAME entity; genuine distinct "
               "versions of different entities are not flagged."),
        findingType="prompt.version_naming_inconsistent",
        implementationId="impl.prompt.version_naming_inconsistent.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="low",
        controlIds=["quality.consistency"],
        evidencePerFinding=2,
    ))
    rr.register(RuleDefinition(
        ruleId="prompt.model_endpoint_no_fallback",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="prompt",
        title=("Prompt names a pinned model/endpoint/API for an imperative "
               "step but declares no fallback/degradation/retry path "
               "anywhere. Deterministic structural-absence signal; whether "
               "the step is truly critical is a human judgement."),
        findingType="prompt.model_endpoint_no_fallback",
        implementationId="impl.prompt.model_endpoint_no_fallback.v1",
        applicableKinds=["prompt"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="low",
        controlIds=["quality.consistency"],
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
        ruleVersion="2.0.0",
        supersedes=["skill.manifest_name_issue@1.0.0"], engine="skill",
        title="Manifest `name` is missing, blank, or has invalid syntax.",
        findingType="skill.manifest_field_issue",
        implementationId="impl.skill.manifest_name_issue.v2",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium", controlIds=["OWASP-AST04"],
        owaspAst10=["OWASP-AST04"], requiresManifest=True,
    ))
    # S4 description
    rr.register(RuleDefinition(
        ruleId="skill.manifest_description_missing",
        ruleVersion="2.0.0",
        supersedes=["skill.manifest_description_missing@1.0.0"], engine="skill",
        title="Manifest `description` is missing or blank.",
        findingType="skill.manifest_field_issue",
        implementationId="impl.skill.manifest_description_missing.v2",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium", controlIds=["OWASP-AST04"],
        owaspAst10=["OWASP-AST04"], requiresManifest=True,
    ))
    # S4b optional official Agent Skills fields
    rr.register(RuleDefinition(
        ruleId="skill.manifest_optional_field_issue",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title=("An optional Agent Skills frontmatter field violates the "
               "official type or length constraint."),
        findingType="skill.manifest_field_issue",
        implementationId="impl.skill.manifest_optional_field_issue.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="medium", controlIds=["AGENT-SKILLS-SPEC"],
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
    # S11 subprocess shell=True (hand-written; suppressed at runtime when
    # Bandit's B602 already reported the same (file, line) — see engine).
    rr.register(RuleDefinition(
        ruleId="skill.python_subprocess_shell_true",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title=("Python source contains a subprocess.* call with shell=True. "
               "This is a mechanically detected dangerous pattern; the code "
               "is NOT executed by Verity. Superseded by skill.bandit.B602 "
               "when Bandit ran successfully at the same location."),
        findingType="skill.python_subprocess_shell_true",
        implementationId="impl.skill.python_subprocess_shell_true.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="high", controlIds=["OWASP-AST01"],
        owaspAst10=["OWASP-AST01"],
    ))
    # --- Bandit-normalised rules ------------------------------------
    # Curated subset: test_ids we've verified produce high-signal Findings
    # in Python source. Each has an explicit severity + OWASP mapping.
    _BANDIT_RULES: List[Tuple[str, str, str, List[str]]] = [
        # test_id, verity_severity, human title, owasp
        ("B102", "high",
         "exec() used on untrusted input.", ["OWASP-AST01"]),
        ("B301", "high",
         "pickle load from untrusted source (arbitrary code execution).",
         ["OWASP-AST01"]),
        ("B324", "medium",
         "Use of insecure MD4/MD5/SHA1 hash (hashlib or crypt); CWE-327. "
         "(Bandit's blacklist-based B303 was superseded by this AST-based "
         "check for Python 3.9+ and never actually fires on this Python "
         "version -- verified empirically, see Round 39.)",
         ["OWASP-AST01"]),
        ("B310", "medium",
         "urllib_urlopen with untrusted scheme (file://, ftp://).",
         ["OWASP-AST05"]),
        ("B506", "high",
         "yaml.load used without SafeLoader (arbitrary object deserialisation).",
         ["OWASP-AST01"]),
        ("B602", "high",
         "subprocess call with shell=True (Bandit).", ["OWASP-AST01"]),
        ("B605", "high",
         "os.system used with a shell command string.", ["OWASP-AST01"]),
        ("B607", "medium",
         "Process started with a partial executable path.", ["OWASP-AST01"]),
        ("B701", "medium",
         "jinja2 template autoescape disabled.", ["OWASP-AST01"]),
        ("B105", "medium",
         "Hardcoded password string.", ["OWASP-AST02"]),
        ("B106", "medium",
         "Hardcoded password default argument.", ["OWASP-AST02"]),
        ("B107", "medium",
         "Hardcoded password default parameter.", ["OWASP-AST02"]),
        ("B501", "high",
         "HTTPS/TLS request made with certificate verification disabled "
         "(e.g. requests.get(..., verify=False)); CWE-295.", ["OWASP-AST02"]),
        ("B608", "medium",
         "SQL query built via string formatting/concatenation instead of "
         "parameterized binding; CWE-89.", ["OWASP-AST01"]),
        ("B314", "medium",
         "xml.etree.ElementTree used to parse untrusted XML (XXE/entity-"
         "expansion risk); CWE-20. Not the same as B405 (import-level).",
         ["OWASP-AST01"]),
    ]
    for test_id, sev, title, owasp in _BANDIT_RULES:
        rr.register(RuleDefinition(
            ruleId=f"skill.bandit.{test_id}",
            ruleVersion="1.0.0", supersedes=[], engine="skill",
            title=(f"[Bandit {test_id}] {title} (Verity runs Bandit as an "
                   "external subprocess against a temporary copy of the "
                   "skill's Python files; the code is NOT executed.)"),
            findingType="skill.bandit_finding",
            implementationId=f"impl.skill.bandit.{test_id}",
            applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
            defaultSeverity=sev, controlIds=owasp,
            owaspAst10=owasp,
        ))
    # --- Gitleaks -----------------------------------------------------
    rr.register(RuleDefinition(
        ruleId="skill.gitleaks_finding",
        ruleVersion="1.0.0", supersedes=[], engine="skill",
        title=("gitleaks detected a secret-like token in a staged skill "
               "file. Verity runs gitleaks as an external, pinned "
               "subprocess against a temporary copy of the skill's "
               "files; the code is NOT executed and the raw secret is "
               "redacted from every Verity output."),
        findingType="skill.gitleaks_finding",
        implementationId="impl.skill.gitleaks.v1",
        applicableKinds=["skill"], requiredEvidenceKinds=["source_span"],
        defaultSeverity="high", controlIds=["OWASP-AST02"],
        owaspAst10=["OWASP-AST02"],
    ))
    # legacy file-level rules -----------------------------------------
    # NOTE: skill.fake_secret_fixture is retained as a limited, deterministic
    # FALLBACK for the fixture token used in Verity's own tests. When
    # gitleaks is available and completed, real secret coverage is provided
    # by skill.gitleaks_finding; the fallback does NOT amount to full
    # secret-scanning coverage on its own (see README).
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
    rr.register(RuleDefinition(
        ruleId="skill.sensitive_path_access",
        ruleVersion="1.0.0",
        supersedes=[],
        engine="skill",
        title=("Skill text literally references a well-known sensitive host "
               "path (SSH keys, cloud credentials, shell history, system "
               "password files)."),
        findingType="skill.sensitive_path_access",
        implementationId="impl.skill.sensitive_path_access.v1",
        applicableKinds=["skill"],
        requiredEvidenceKinds=["source_span"],
        defaultSeverity="high",
        controlIds=["OWASP-AST06"],
        owaspAst10=["OWASP-AST06"],
    ))
    return rr
