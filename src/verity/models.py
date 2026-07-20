"""Core Phase 0 data models.

Explicit separation between:
- Evidence (facts)             §5
- RuleMatchEvent (rule hits)   §5
- SemanticCandidate            §7
- CandidateAssessment          §7
- Finding                      §7.4  (deterministic OR semantic origin)

These types are dataclasses (not Pydantic) to keep the dependency surface
small; strict validation happens at construction time and via JSON Schema
exports (see schema.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Literal, Optional, Sequence

from . import LOCATION_SCHEMA_VERSION


Severity = Literal["low", "medium", "high", "critical"]
Engine = Literal["prompt", "skill"]

# Controlled prompt-kind enum. Not free text.
PromptKind = Literal["user_prompt", "system_prompt"]
PROMPT_KINDS: tuple = ("user_prompt", "system_prompt")


@dataclass(frozen=True)
class Location:
    fileId: str
    artifactPath: str
    fileDigest: str
    sourceEncoding: str = "utf-8"
    sourceByteRange: Optional[Dict[str, int]] = None
    structuralPath: Optional[str] = None
    displayLineColumn: Optional[Dict[str, int]] = None
    locationSchemaVersion: str = LOCATION_SCHEMA_VERSION

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: v for k, v in d.items() if v is not None}


@dataclass(frozen=True)
class Producer:
    componentId: str
    componentVersion: str
    executionId: str


@dataclass(frozen=True)
class EvidenceRecord:
    evidenceId: str
    snapshotId: str
    kind: Literal["source_span", "parsed_fact", "reference_path",
                  "dataflow_path", "capability_observation"]
    locations: List[Location]
    sensitivity: Literal["normal", "sensitive", "secret"]
    occurrenceFingerprint: str
    producer: Producer
    derivedFromEvidenceIds: List[str] = field(default_factory=list)
    identityPolicyId: str = "default-v1"
    redactedPreview: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuleMatchEvent:
    eventId: str
    snapshotId: str
    ruleId: str
    ruleVersion: str
    evidenceIds: List[str]
    eventDedupKey: str
    executionId: str


@dataclass(frozen=True)
class SemanticCandidate:
    candidateId: str
    snapshotId: str
    findingType: str
    subject: Dict[str, Any]
    claim: str
    evidenceIds: List[str]
    falsificationQuestion: str
    proposedSeverity: Severity
    generatorExecutionId: str
    generatorId: str
    generatorVersion: str


@dataclass(frozen=True)
class ValidationRecord:
    validationId: str
    candidateId: str
    executionId: str
    checkedEvidenceIds: List[str]
    validatorId: str
    validatorVersion: str
    status: Literal["completed", "failed", "cancelled"]
    verdict: Optional[Literal["confirmed", "rejected", "insufficient_evidence"]] = None
    rationale: Optional[str] = None
    errorCode: Optional[str] = None
    terminationReason: Optional[str] = None
    evidenceSufficiencyChallenge: Optional[Dict[str, Any]] = None
    timestamp: str = ""


@dataclass(frozen=True)
class CandidateAssessment:
    candidateAssessmentId: str
    candidateId: str
    validationPolicyId: str
    validationPolicyVersion: str
    validationIds: List[str]
    state: Literal["pending", "confirmed", "rejected",
                   "insufficient_evidence", "validation_failed"]
    reasonCodes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    findingId: str
    snapshotId: str
    findingOccurrenceFingerprint: str
    findingType: str
    subject: Dict[str, Any]
    subjectKey: str
    claim: str
    severity: Severity
    origin: Dict[str, Any]  # discriminated by origin.kind
    evidenceIds: List[str]
    controls: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)

    def is_deterministic(self) -> bool:
        return self.origin.get("kind") == "deterministic_rule"


# --- Snapshot / Artifact ------------------------------------------------

@dataclass(frozen=True)
class ArtifactFile:
    fileId: str
    normalizedPath: str
    size: int
    contentDigest: Optional[str]  # None for skipped/rejected
    status: Literal["included", "skipped", "rejected"]
    reasonCode: Optional[str] = None
    executable: bool = False
    symlinkTarget: Optional[str] = None
    raceDetected: bool = False
    entryType: Literal["file", "symlink", "special", "directory"] = "file"


@dataclass(frozen=True)
class ArtifactSnapshot:
    artifactId: str
    snapshotId: str
    snapshotManifestDigest: str
    contentRootDigest: str
    files: List[ArtifactFile]
    digestAlgorithm: str = "sha256"
    canonicalizationVersion: str = "1"
    # Prompt-engine only; None for skill engine.
    promptKind: Optional[PromptKind] = None


# --- Plan / Coverage ----------------------------------------------------

@dataclass(frozen=True)
class AnalysisPlanItem:
    planItemId: str
    componentKind: Literal["parser", "analyzer", "rule",
                           "candidate_generator", "validator"]
    componentId: str
    componentVersion: str
    scope: List[str]
    requirement: Literal["required", "optional"]
    gatingClass: Literal["critical", "normal"]


@dataclass(frozen=True)
class ExecutionRecord:
    executionId: str
    planItemId: str
    status: Literal["completed", "partial", "failed", "cancelled",
                    "unsupported", "not_applicable", "blocked_by_upstream_failure"]
    coveredScopes: List[str] = field(default_factory=list)
    skippedScopes: List[Dict[str, str]] = field(default_factory=list)
    reasonCode: Optional[str] = None


@dataclass(frozen=True)
class CoverageAssessment:
    coverageAssessmentId: str
    reviewId: str
    reviewPlanId: str
    reviewPlanRevision: int
    status: Literal["sufficient", "insufficient", "failed"]
    criticalGapPlanItemIds: List[str] = field(default_factory=list)
    reasonCodes: List[str] = field(default_factory=list)
    policyId: str = "coverage-policy-v1"
    policyVersion: str = "1"


@dataclass(frozen=True)
class ReviewPlan:
    reviewPlanId: str
    reviewId: str
    revision: int
    phase: Literal["initial", "expanded"]
    expansionDepth: int
    items: List[AnalysisPlanItem]


@dataclass(frozen=True)
class Review:
    reviewId: str
    artifactSnapshot: ArtifactSnapshot
    engine: Engine
    plan: ReviewPlan
    executions: List[ExecutionRecord]
    coverage: CoverageAssessment
    evidences: List[EvidenceRecord]
    ruleMatches: List[RuleMatchEvent]
    findings: List[Finding]
    # Optional ArtifactModel produced by the engine's Parser (Skill engine).
    artifactModel: Optional[Dict[str, Any]] = None


# --- Baseline ----------------------------------------------------------

@dataclass(frozen=True)
class FindingMatchRecord:
    findingMatchId: str
    baselineScopeId: str
    previousSnapshotId: str
    currentSnapshotId: str
    previousFindingIds: List[str]
    currentFindingIds: List[str]
    state: Literal["new", "existing", "changed", "resolved",
                   "regressed", "ambiguous", "unknown_due_to_coverage"]
    method: Literal["exact", "stable_subject", "rule_migration", "heuristic"]
    matcherPolicyId: str = "baseline-policy-v1"
    matcherPolicyVersion: str = "1"
    reasonCodes: List[str] = field(default_factory=list)


# --- PatchSet ----------------------------------------------------------

@dataclass(frozen=True)
class PatchEdit:
    operation: Literal["replace"]
    filePath: str
    baseFileDigest: str
    sourceEncoding: str
    sourceByteRange: Dict[str, int]
    expectedRangeDigest: str
    replacement: str


@dataclass(frozen=True)
class PatchSetProposal:
    patchSetId: str
    proposalDigest: str
    baseSnapshotId: str
    baseContentRootDigest: str
    sourceFindingIds: List[str]
    applyMode: Literal["independent", "transactional_all_or_none"]
    edits: List[PatchEdit]
    # PatchSet is ALWAYS a suggestion; V1 does not apply patches (spec §14).
    status: Literal["proposed"] = "proposed"
