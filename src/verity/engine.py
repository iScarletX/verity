"""Engine + Rule execution.

Two independent engines (Prompt / Skill) with independent Rule registries
share the same base data model and reporting infrastructure. Rules produce
Evidence + RuleMatchEvent; the fixed deterministic pipeline then converts
each RuleMatchEvent into a Finding through a pure function — no LLM step,
no validator, no filter.  (spec §7.4 bullet 1)

deterministic Findings, once produced, are only removed from the report
projection by:
  (a) rule not applicable — decided BEFORE rule execution;
  (b) an explicit DispositionRecord.
No component (candidate generator, validator, meta LLM) has a code path
that can drop or downgrade a deterministic Finding. Architectural test in
tests/test_architecture.py enforces this by AST-level import inspection.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Sequence, Tuple

from .canonical import (
    canonical_json,
    domain_tag,
    event_dedup_key,
    occurrence_fingerprint,
    sha256_hex,
    subject_key as compute_subject_key,
)
from .models import (
    EvidenceRecord, Finding, Location, Producer, RuleMatchEvent,
    ArtifactSnapshot, AnalysisPlanItem, ExecutionRecord,
)
from .registry import RuleDefinition, RuleRegistry, FindingTypeRegistry


# --- Rule implementations ------------------------------------------------

RuleImpl = Callable[
    ["RuleContext"],
    List[Tuple[EvidenceRecord, Dict]],   # (evidence, subject_dict)
]


@dataclass
class RuleContext:
    snapshot: ArtifactSnapshot
    file_bytes: Dict[str, bytes]
    execution_id: str
    rule: RuleDefinition


class Engine:
    """A named engine (prompt or skill). Rules registered against an engine
    only see files/inputs prepared for that engine.
    """

    def __init__(self, name: str, rule_registry: RuleRegistry,
                 finding_types: FindingTypeRegistry,
                 implementations: Dict[str, RuleImpl]) -> None:
        assert name in ("prompt", "skill")
        self.name = name
        self.rules = rule_registry
        self.finding_types = finding_types
        self.impls = implementations

    def run(self, snapshot: ArtifactSnapshot, file_bytes: Dict[str, bytes]
            ) -> Tuple[List[EvidenceRecord], List[RuleMatchEvent], List[Finding],
                       List[AnalysisPlanItem], List[ExecutionRecord]]:
        evidences: List[EvidenceRecord] = []
        events: List[RuleMatchEvent] = []
        findings: List[Finding] = []
        plan_items: List[AnalysisPlanItem] = []
        executions: List[ExecutionRecord] = []
        # Deterministic ordering by ruleId
        for rule in sorted(self.rules.by_engine(self.name), key=lambda r: (r.ruleId, r.ruleVersion)):
            impl = self.impls.get(rule.implementationId)
            plan_item = AnalysisPlanItem(
                planItemId=f"pi-{rule.ruleId}",
                componentKind="rule",
                componentId=rule.ruleId,
                componentVersion=rule.ruleVersion,
                scope=[snapshot.snapshotId],
                requirement="required",
                gatingClass="critical" if rule.defaultSeverity in ("high", "critical") else "normal",
            )
            plan_items.append(plan_item)
            exec_id = f"e-{uuid.uuid4().hex[:12]}"
            if impl is None:
                executions.append(ExecutionRecord(
                    executionId=exec_id, planItemId=plan_item.planItemId,
                    status="failed", reasonCode="implementation_missing",
                ))
                continue
            ctx = RuleContext(snapshot=snapshot, file_bytes=file_bytes,
                              execution_id=exec_id, rule=rule)
            try:
                results = impl(ctx)
            except Exception as e:  # pragma: no cover — safety net
                executions.append(ExecutionRecord(
                    executionId=exec_id, planItemId=plan_item.planItemId,
                    status="failed", reasonCode=f"impl_error:{type(e).__name__}",
                ))
                continue

            # Deterministic Finding pipeline. NO LLM path exists here.
            per_event_dedup: set[str] = set()
            for evidence, subject in results:
                evidences.append(evidence)
                edk = event_dedup_key(
                    rule_id=rule.ruleId,
                    rule_version=rule.ruleVersion,
                    rule_config_digest=rule.ruleConfigDigest,
                    occurrence_fingerprints=[evidence.occurrenceFingerprint],
                    locations=[l.to_dict() for l in evidence.locations],
                )
                if edk in per_event_dedup:
                    continue  # §5.2 same-snapshot same-rule same-occurrence exact dedup
                per_event_dedup.add(edk)
                event = RuleMatchEvent(
                    eventId=sha256_hex(domain_tag("event-id"),
                                       canonical_json({"snapshot": snapshot.snapshotId, "edk": edk}))[:32],
                    snapshotId=snapshot.snapshotId,
                    ruleId=rule.ruleId,
                    ruleVersion=rule.ruleVersion,
                    evidenceIds=[evidence.evidenceId],
                    eventDedupKey=edk,
                    executionId=exec_id,
                )
                events.append(event)

                ftd = self.finding_types.get(rule.findingType)
                errs = ftd.validate_subject(subject)
                if errs:
                    # subject validation failure -> not a finding; recorded via execution
                    executions.append(ExecutionRecord(
                        executionId=f"e-{uuid.uuid4().hex[:12]}",
                        planItemId=plan_item.planItemId,
                        status="failed",
                        reasonCode="subject_schema_violation:" + ";".join(errs),
                    ))
                    continue
                sk = compute_subject_key(rule.findingType, subject, ftd.subjectKeyFields)
                fof = sha256_hex(
                    domain_tag("finding-occurrence"),
                    canonical_json({
                        "edk": edk, "subjectKey": sk, "origin": "deterministic_rule",
                    }),
                )
                finding = Finding(
                    findingId=f"F-{fof[:16]}",
                    snapshotId=snapshot.snapshotId,
                    findingOccurrenceFingerprint=fof,
                    findingType=rule.findingType,
                    subject=dict(subject),
                    subjectKey=sk,
                    claim=rule.title,
                    severity=rule.defaultSeverity,
                    origin={"kind": "deterministic_rule", "ruleMatchEventIds": [event.eventId]},
                    evidenceIds=[evidence.evidenceId],
                    controls=list(rule.controlIds),
                    tags=[f"engine:{self.name}"],
                )
                findings.append(finding)

            executions.append(ExecutionRecord(
                executionId=exec_id, planItemId=plan_item.planItemId,
                status="completed", coveredScopes=[snapshot.snapshotId],
            ))
        return evidences, events, findings, plan_items, executions


# --- Convenience helpers for rule implementations ------------------------

def make_source_span_evidence(*, snapshot_id: str, file_id: str, artifact_path: str,
                              file_digest: str, byte_range: Tuple[int, int],
                              raw_bytes: bytes, producer: Producer,
                              sensitivity: str = "normal",
                              redacted_preview: str | None = None,
                              evidence_kind_tag: str | None = None,
                              ) -> EvidenceRecord:
    loc = Location(
        fileId=file_id, artifactPath=artifact_path, fileDigest=file_digest,
        sourceByteRange={"start": byte_range[0], "end": byte_range[1]},
    )
    fp = occurrence_fingerprint(
        sensitivity=sensitivity,
        locations=[loc.to_dict()],
        raw_bytes=raw_bytes if sensitivity != "secret" else None,
        evidence_kind_tag=evidence_kind_tag,
        producer_component_version=producer.componentVersion,
        identity_policy_id="default-v1",
    )
    return EvidenceRecord(
        evidenceId=f"ev-{sha256_hex(fp.encode())[:16]}",
        snapshotId=snapshot_id,
        kind="source_span",
        locations=[loc],
        sensitivity=sensitivity,  # type: ignore[arg-type]
        occurrenceFingerprint=fp,
        producer=producer,
        redactedPreview=redacted_preview,
    )


# --- Built-in rule implementations --------------------------------------

_JAILBREAK_TERMS = re.compile(
    rb"(?i)\b(ignore (all )?previous instructions|disregard (all )?prior|"
    rb"you are now dan|jailbreak|bypass (safety|guardrails))\b"
)

def prompt_jailbreak_marker(ctx: RuleContext) -> List[Tuple[EvidenceRecord, Dict]]:
    """Prompt-engine deterministic rule.

    Flags text spans that literally include well-known jailbreak / instruction
    override markers. Purely regex; repeatable; no LLM.
    """
    out: List[Tuple[EvidenceRecord, Dict]] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        for m in _JAILBREAK_TERMS.finditer(data):
            ev = make_source_span_evidence(
                snapshot_id=ctx.snapshot.snapshotId,
                file_id=f.fileId, artifact_path=f.normalizedPath,
                file_digest=f.contentDigest or "",
                byte_range=(m.start(), m.end()),
                raw_bytes=m.group(0),
                producer=prod,
            )
            subject = {
                "artifactPath": f.normalizedPath,
                "markerCategory": "instruction_override",
            }
            out.append((ev, subject))
    return out


# Fake-secret token used for tests / fixtures ONLY. Not a real credential.
_FAKE_SECRET_PATTERN = re.compile(rb"VERITY_FAKE_SECRET_[A-Z0-9]{8,32}")

_DANGEROUS_SHELL = re.compile(
    rb"(?m)(?:^|[\s;&|`\(])(?:curl|wget)\s+[^\n]*\|\s*(?:sh|bash|zsh)\b"
    rb"|(?:^|[\s;&|`\(])rm\s+-rf\s+/(?![A-Za-z0-9_])"
    rb"|:\(\)\{\s*:\|:&\s*\};:"
)

def skill_secret_like_fixture(ctx: RuleContext) -> List[Tuple[EvidenceRecord, Dict]]:
    """Skill-engine deterministic rule — flags fake-secret placeholders.

    We intentionally do NOT ship a real gitleaks ruleset here (out of scope
    for the walking skeleton, spec constraint). The rule matches a fixture
    token that is safe to include in tests, and demonstrates the secret
    evidence code path (redactedPreview only, no raw persistence).
    """
    out: List[Tuple[EvidenceRecord, Dict]] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        for m in _FAKE_SECRET_PATTERN.finditer(data):
            ev = make_source_span_evidence(
                snapshot_id=ctx.snapshot.snapshotId,
                file_id=f.fileId, artifact_path=f.normalizedPath,
                file_digest=f.contentDigest or "",
                byte_range=(m.start(), m.end()),
                raw_bytes=b"",  # sensitive: no raw bytes hashed
                producer=prod,
                sensitivity="secret",
                redacted_preview="VERITY_FAKE_SECRET_" + "*" * 8,
                evidence_kind_tag="verity_fake_secret",
            )
            subject = {
                "artifactPath": f.normalizedPath,
                "secretCategory": "fake_fixture_secret",
            }
            out.append((ev, subject))
    return out


def skill_dangerous_shell(ctx: RuleContext) -> List[Tuple[EvidenceRecord, Dict]]:
    """Skill-engine deterministic rule — flags dangerous shell text patterns.

    IMPORTANT: this is text-level detection only; it does NOT execute the
    skill or the shell (spec §17: V1 never executes target content).
    """
    out: List[Tuple[EvidenceRecord, Dict]] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        for m in _DANGEROUS_SHELL.finditer(data):
            ev = make_source_span_evidence(
                snapshot_id=ctx.snapshot.snapshotId,
                file_id=f.fileId, artifact_path=f.normalizedPath,
                file_digest=f.contentDigest or "",
                byte_range=(m.start(), m.end()),
                raw_bytes=m.group(0),
                producer=prod,
            )
            subject = {
                "artifactPath": f.normalizedPath,
                "shellPatternCategory": "dangerous_shell",
            }
            out.append((ev, subject))
    return out


DEFAULT_IMPLEMENTATIONS: Dict[str, RuleImpl] = {
    "impl.prompt.jailbreak_marker.v1": prompt_jailbreak_marker,
    "impl.skill.fake_secret.v1": skill_secret_like_fixture,
    "impl.skill.dangerous_shell.v1": skill_dangerous_shell,
}
