"""Rule and FindingType registries.

The registries are the enforcement point for:
- §6  supersedes must be declared, no accidental "new" findings on version bump
- §8  subject_key taxonomy — subject fields must come from declared sources
- §18.1  built-in rules cannot be silently overridden by user rules;
         user rules cannot elevate severity to High/Critical over built-in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Sequence


# ----- FindingType --------------------------------------------------------

@dataclass(frozen=True)
class SubjectField:
    fieldName: str
    sourceKind: Literal["artifact_model_path", "evidence_field", "literal_enum"]
    sourcePath: Optional[str] = None
    allowedValues: Optional[List[str]] = None


@dataclass(frozen=True)
class FindingTypeDefinition:
    findingType: str
    engine: Literal["prompt", "skill"]
    subjectFields: List[SubjectField]
    subjectKeyFields: List[str]
    defaultSeverity: Literal["low", "medium", "high", "critical"]
    requiredEvidenceKinds: List[str]

    def validate_subject(self, subject: Dict) -> List[str]:
        errors: List[str] = []
        declared = {f.fieldName: f for f in self.subjectFields}
        for key in subject.keys():
            if key not in declared:
                errors.append(f"subject field {key!r} not declared for {self.findingType!r}")
        for f in self.subjectFields:
            if f.fieldName not in subject:
                # subject can omit optional-declared fields; but subjectKeyFields must be present
                if f.fieldName in self.subjectKeyFields:
                    errors.append(f"subjectKeyField {f.fieldName!r} missing")
                continue
            v = subject[f.fieldName]
            if f.sourceKind == "literal_enum":
                if f.allowedValues is None or v not in f.allowedValues:
                    errors.append(f"subject field {f.fieldName!r} value {v!r} not in enum")
        return errors


# ----- Rule ---------------------------------------------------------------

@dataclass(frozen=True)
class RuleDefinition:
    ruleId: str
    ruleVersion: str
    supersedes: List[str]  # e.g. ["RULE_ID@1.0.0"] — REQUIRED, may be empty list
    engine: Literal["prompt", "skill"]
    title: str
    findingType: str
    implementationId: str
    applicableKinds: List[str]
    requiredEvidenceKinds: List[str]
    defaultSeverity: Literal["low", "medium", "high", "critical"]
    controlIds: List[str] = field(default_factory=list)
    fixtureIds: List[str] = field(default_factory=list)
    builtIn: bool = True
    ruleConfigDigest: str = "builtin"
    # Prompt-engine gate: which prompt kinds this rule applies to. Empty
    # means "any prompt kind". Skill-engine rules must leave this empty.
    applicablePromptKinds: List[str] = field(default_factory=list)
    # Number of Evidence records this rule produces per Finding. Most rules
    # produce exactly one Evidence per Finding; conflict-style rules like
    # duplicate-key can produce N>1. Not enforced structurally at register
    # time — rule impls are responsible for building consistent Evidence.
    evidencePerFinding: int = 1


class RegistryError(Exception):
    pass


class FindingTypeRegistry:
    def __init__(self) -> None:
        self._by_id: Dict[str, FindingTypeDefinition] = {}

    def register(self, ftd: FindingTypeDefinition) -> None:
        if ftd.findingType in self._by_id:
            raise RegistryError(f"findingType already registered: {ftd.findingType}")
        # subjectKeyFields must be subset of declared fields
        declared = {f.fieldName for f in ftd.subjectFields}
        for k in ftd.subjectKeyFields:
            if k not in declared:
                raise RegistryError(
                    f"subjectKeyField {k!r} not present in subjectFields for {ftd.findingType!r}"
                )
        self._by_id[ftd.findingType] = ftd

    def get(self, finding_type: str) -> FindingTypeDefinition:
        if finding_type not in self._by_id:
            raise RegistryError(f"unknown findingType: {finding_type}")
        return self._by_id[finding_type]


_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


class RuleRegistry:
    def __init__(self, finding_types: FindingTypeRegistry) -> None:
        self._ft = finding_types
        self._by_key: Dict[str, RuleDefinition] = {}  # ruleId@ruleVersion
        self._by_id: Dict[str, List[RuleDefinition]] = {}

    def register(self, rule: RuleDefinition) -> None:
        # findingType must exist
        ft = self._ft.get(rule.findingType)

        # §18.1: user rules cannot elevate severity above built-in for the same findingType
        if not rule.builtIn:
            builtin_max = 0
            for existing in self._by_key.values():
                if existing.findingType == rule.findingType and existing.builtIn:
                    builtin_max = max(builtin_max, _SEVERITY_ORDER[existing.defaultSeverity])
            if _SEVERITY_ORDER[rule.defaultSeverity] > builtin_max and builtin_max > 0:
                raise RegistryError(
                    f"user-defined rule {rule.ruleId!r} cannot elevate severity above built-in"
                )
            if _SEVERITY_ORDER[rule.defaultSeverity] >= _SEVERITY_ORDER["high"]:
                # user-defined rules default cap: cannot default to high/critical
                raise RegistryError(
                    f"user-defined rule {rule.ruleId!r} cannot declare high/critical severity"
                )

        # §6: enforce supersedes discipline. If another rule (any version) with the
        # same findingType already exists, and none of them are in supersedes, refuse.
        existing_family = [r for r in self._by_key.values() if r.findingType == rule.findingType]
        if existing_family:
            supersedes_set = set(rule.supersedes)
            covered = any(f"{r.ruleId}@{r.ruleVersion}" in supersedes_set for r in existing_family)
            # Same ruleId (bumped version) MUST declare supersedes for its previous version.
            same_id = [r for r in existing_family if r.ruleId == rule.ruleId]
            if same_id and not covered:
                raise RegistryError(
                    f"rule {rule.ruleId}@{rule.ruleVersion} bumps an existing rule of the same "
                    f"findingType but does not declare supersedes; add supersedes explicitly"
                )

        key = f"{rule.ruleId}@{rule.ruleVersion}"
        if key in self._by_key:
            raise RegistryError(f"rule already registered: {key}")
        self._by_key[key] = rule
        self._by_id.setdefault(rule.ruleId, []).append(rule)

    def get(self, rule_id: str, rule_version: str) -> RuleDefinition:
        return self._by_key[f"{rule_id}@{rule_version}"]

    def all(self) -> List[RuleDefinition]:
        return list(self._by_key.values())

    def by_engine(self, engine: str) -> List[RuleDefinition]:
        return [r for r in self._by_key.values() if r.engine == engine]
