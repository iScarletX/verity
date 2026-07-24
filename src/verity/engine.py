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

import base64
import binascii
import re
import uuid
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

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

@dataclass
class RuleHit:
    """A single rule finding candidate.

    A rule may attach multiple pieces of Evidence to one Finding (e.g. two
    conflicting assignments of the same key). ``evidences`` is order-
    preserving; the first evidence is treated as the primary anchor for
    display.
    """

    evidences: List[EvidenceRecord]
    subject: Dict


RuleImpl = Callable[
    ["RuleContext"],
    List[RuleHit],
]


@dataclass
class RuleContext:
    snapshot: ArtifactSnapshot
    file_bytes: Dict[str, bytes]
    execution_id: str
    rule: RuleDefinition
    # Optional artifact model produced by an upstream Parser (Skill engine).
    # ``parser_ok`` is False when the parser failed — rules that declare a
    # dependency on the manifest via ``rule.requiresManifest`` will not run.
    artifact_model: Dict = None  # type: ignore[assignment]
    parser_ok: bool = True


class Engine:
    """A named engine (prompt or skill). Rules registered against an engine
    only see files/inputs prepared for that engine.
    """

    def __init__(self, name: str, rule_registry: RuleRegistry,
                 finding_types: FindingTypeRegistry,
                 implementations: Dict[str, RuleImpl],
                 parser=None, analyzers: Optional[List] = None) -> None:
        assert name in ("prompt", "skill")
        self.name = name
        self.rules = rule_registry
        self.finding_types = finding_types
        self.impls = implementations
        # Optional Parser callable: (snapshot, file_bytes) -> (model, parser_run)
        self.parser = parser
        # Optional analyzers: each entry is a dict with keys:
        #   componentId, componentVersion, gatingClass, run(snapshot, file_bytes)
        # -> returns (artifact_model_updates: dict, status, reasonCode)
        self.analyzers = list(analyzers or [])

    def run(self, snapshot: ArtifactSnapshot, file_bytes: Dict[str, bytes]
            ) -> Tuple[List[EvidenceRecord], List[RuleMatchEvent], List[Finding],
                       List[AnalysisPlanItem], List[ExecutionRecord], Dict]:
        evidences: List[EvidenceRecord] = []
        events: List[RuleMatchEvent] = []
        findings: List[Finding] = []
        plan_items: List[AnalysisPlanItem] = []
        executions: List[ExecutionRecord] = []

        # Parser step (Skill engine). Runs BEFORE rules and is a first-class
        # AnalysisPlanItem so that its failure is reflected in Coverage.
        artifact_model: Dict = {"hasSkillMd": False, "manifest": None,
                                 "manifestFile": None, "manifestRaw": None,
                                 "manifestByteRange": None}
        parser_ok = True
        parser_diagnostics = []
        if self.parser is not None:
            parser_plan = AnalysisPlanItem(
                planItemId="pi-parser-manifest",
                componentKind="parser",
                componentId="verity.skill.manifest.v1",
                componentVersion="1.0.0",
                scope=[snapshot.snapshotId],
                requirement="required",
                gatingClass="critical",
            )
            plan_items.append(parser_plan)
            exec_id = f"e-{uuid.uuid4().hex[:12]}"
            try:
                artifact_model, parser_run = self.parser(snapshot, file_bytes)
            except Exception as e:  # pragma: no cover
                parser_ok = False
                executions.append(ExecutionRecord(
                    executionId=exec_id, planItemId=parser_plan.planItemId,
                    status="failed", reasonCode=f"parser_error:{type(e).__name__}",
                ))
            else:
                parser_diagnostics = list(parser_run.diagnostics)
                # Attach diagnostics EARLY so rules can inspect them.
                artifact_model["parserDiagnostics"] = [
                    {"code": d.code, "message": d.message}
                    for d in parser_diagnostics
                ]
                if parser_run.status in ("completed", "partial"):
                    executions.append(ExecutionRecord(
                        executionId=exec_id, planItemId=parser_plan.planItemId,
                        status="completed" if parser_run.status == "completed" else "partial",
                        coveredScopes=[snapshot.snapshotId],
                    ))
                    # "partial" is OK when the parser produced a usable
                    # manifest view (e.g. Markdown without frontmatter
                    # -> empty mapping). Rules will then flag missing
                    # fields on their own terms.
                    parser_ok = artifact_model.get("manifest") is not None
                else:
                    parser_ok = False
                    reason = ";".join(d.code for d in parser_diagnostics) or "parser_failed"
                    executions.append(ExecutionRecord(
                        executionId=exec_id, planItemId=parser_plan.planItemId,
                        status="failed", reasonCode=f"parser:{reason}",
                    ))

        # Analyzer step (e.g. Bandit). Each analyzer is a first-class
        # AnalysisPlanItem so its failure is visible in Coverage. Analyzers
        # publish results into ``artifact_model`` for consumption by rules.
        for an in self.analyzers:
            an_plan = AnalysisPlanItem(
                planItemId=f"pi-analyzer-{an['componentId']}",
                componentKind="analyzer",
                componentId=an["componentId"],
                componentVersion=an["componentVersion"],
                scope=[snapshot.snapshotId],
                requirement="required",
                gatingClass=an.get("gatingClass", "normal"),
            )
            plan_items.append(an_plan)
            exec_id = f"e-{uuid.uuid4().hex[:12]}"
            try:
                updates, status, reason = an["run"](snapshot, file_bytes)
            except Exception as e:  # pragma: no cover
                executions.append(ExecutionRecord(
                    executionId=exec_id, planItemId=an_plan.planItemId,
                    status="failed", reasonCode=f"analyzer_error:{type(e).__name__}",
                ))
                continue
            if updates:
                artifact_model.update(updates)
            if status == "completed":
                executions.append(ExecutionRecord(
                    executionId=exec_id, planItemId=an_plan.planItemId,
                    status="completed", coveredScopes=[snapshot.snapshotId],
                    reasonCode=reason,
                ))
            else:
                executions.append(ExecutionRecord(
                    executionId=exec_id, planItemId=an_plan.planItemId,
                    status=status, reasonCode=reason,
                ))
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

            # Applicability gate: prompt_kind not in rule.applicablePromptKinds.
            # This is a normal, non-failing skip (§9.2 not_applicable): the
            # gate condition is a declared precondition, not an upstream failure.
            if self.name == "prompt" and rule.applicablePromptKinds:
                pk = snapshot.promptKind
                if pk is None or pk not in rule.applicablePromptKinds:
                    executions.append(ExecutionRecord(
                        executionId=exec_id, planItemId=plan_item.planItemId,
                        status="not_applicable",
                        reasonCode=f"prompt_kind_gate:required={','.join(rule.applicablePromptKinds)};actual={pk or 'unset'}",
                    ))
                    continue

            if impl is None:
                executions.append(ExecutionRecord(
                    executionId=exec_id, planItemId=plan_item.planItemId,
                    status="failed", reasonCode="implementation_missing",
                ))
                continue

            # §9.2 blocked_by_upstream_failure: rules that require manifest
            # must not silently produce zero findings when the parser failed.
            requires_manifest = getattr(rule, "requiresManifest", False)
            if requires_manifest and not parser_ok:
                executions.append(ExecutionRecord(
                    executionId=exec_id, planItemId=plan_item.planItemId,
                    status="blocked_by_upstream_failure",
                    reasonCode="manifest_parser_failed",
                ))
                continue

            ctx = RuleContext(snapshot=snapshot, file_bytes=file_bytes,
                              execution_id=exec_id, rule=rule,
                              artifact_model=artifact_model, parser_ok=parser_ok)
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
            for hit in results:
                for ev in hit.evidences:
                    evidences.append(ev)
                ev_fps = [ev.occurrenceFingerprint for ev in hit.evidences]
                # Union of all locations belonging to this hit.
                all_locs = []
                for ev in hit.evidences:
                    all_locs.extend(l.to_dict() for l in ev.locations)
                edk = event_dedup_key(
                    rule_id=rule.ruleId,
                    rule_version=rule.ruleVersion,
                    rule_config_digest=rule.ruleConfigDigest,
                    occurrence_fingerprints=ev_fps,
                    locations=all_locs,
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
                    evidenceIds=[ev.evidenceId for ev in hit.evidences],
                    eventDedupKey=edk,
                    executionId=exec_id,
                )
                events.append(event)

                ftd = self.finding_types.get(rule.findingType)
                errs = ftd.validate_subject(hit.subject)
                if errs:
                    executions.append(ExecutionRecord(
                        executionId=f"e-{uuid.uuid4().hex[:12]}",
                        planItemId=plan_item.planItemId,
                        status="failed",
                        reasonCode="subject_schema_violation:" + ";".join(errs),
                    ))
                    continue
                sk = compute_subject_key(rule.findingType, hit.subject, ftd.subjectKeyFields)
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
                    subject=dict(hit.subject),
                    subjectKey=sk,
                    claim=rule.title,
                    severity=rule.defaultSeverity,
                    origin={"kind": "deterministic_rule", "ruleMatchEventIds": [event.eventId]},
                    evidenceIds=[ev.evidenceId for ev in hit.evidences],
                    controls=list(rule.controlIds),
                    tags=[f"engine:{self.name}"],
                )
                findings.append(finding)

            executions.append(ExecutionRecord(
                executionId=exec_id, planItemId=plan_item.planItemId,
                status="completed", coveredScopes=[snapshot.snapshotId],
            ))
        # Ensure parser diagnostics are always present in the returned model.
        artifact_model.setdefault("parserDiagnostics", [
            {"code": d.code, "message": d.message} for d in parser_diagnostics
        ])
        return evidences, events, findings, plan_items, executions, artifact_model


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

# --- Fenced-code / quote helpers ----------------------------------------

_FENCED_CODE = re.compile(rb"(?ms)^```.*?^```\s*$")
_INLINE_CODE = re.compile(rb"`[^`\n]{1,200}`")


def _excluded_ranges(data: bytes) -> List[Tuple[int, int]]:
    """Byte ranges of fenced/inline code blocks. Rules that want to
    suppress benign quoting of attack strings can drop matches inside
    these ranges. Cheap, deterministic, syntax-conservative.
    """
    ranges: List[Tuple[int, int]] = []
    for m in _FENCED_CODE.finditer(data):
        ranges.append((m.start(), m.end()))
    for m in _INLINE_CODE.finditer(data):
        ranges.append((m.start(), m.end()))
    return ranges


def _in_ranges(pos: int, ranges: Sequence[Tuple[int, int]]) -> bool:
    for s, e in ranges:
        if s <= pos < e:
            return True
    return False


# --- P1: instruction-override marker (rewritten, code-exclusion aware) ---

# Broadened instruction-bypass phrase set. Adapted (not copied) from the
# authoritative patterns in vigil-llm's InstructionBypass YARA rule
# (Adam M. Swanda, Apache-2.0) and NVIDIA garak's DAN/promptinject probes,
# which are the de-facto reference corpora for this attack class. The
# structure is verb + optional temporal qualifier + instruction-object, plus
# a few well-known named-jailbreak markers.
# Round 49 precision fix: the object must be SELF-REFERENTIAL to the
# instruction hierarchy (previous/above/your rules), which is what an
# ATTACK says ("ignore all previous instructions"). A DEFENSE says
# "ignore any text/content the user sends that tries to change your role"
# -- same verb, but the object is untrusted external data, not the
# prompt's own instructions. Requiring a self-referential qualifier
# (or the bare word instructions/rules/prompt without a data-source noun)
# keeps the defensive phrasing from being mis-flagged as an attack.
_JAILBREAK_TERMS = re.compile(
    rb"(?i)("
    # verb + REQUIRED self-referential qualifier + instruction-object
    rb"(?:ignore|disregard|skip|forget|neglect|overlook|omit|bypass|"
    rb"pay no attention to|do not follow|do not obey)\s+"
    rb"(?:all\s+|any\s+|the\s+)?"
    rb"(?:prior|previous|preceding|above|foregoing|earlier|initial)\s+"
    rb"(?:instructions?|directives?|commands?|context|rules?|"
    rb"prompts?|guardrails?|guidelines?|messages?|content|text)"
    # OR: verb + "your" + own-instruction object (also self-referential)
    rb"|(?:ignore|disregard|forget|bypass|override)\s+your\s+"
    rb"(?:instructions?|rules?|prompt|guidelines?|guardrails?|programming|training)"
    # well-known named jailbreaks / mode markers
    rb"|you are now dan\b|\bdan mode\b|jailbreak mode|developer mode enabled"
    rb"|do anything now|bypass (?:safety|guardrails|filters|restrictions)"
    rb"|without (?:any )?(?:restrictions|limitations|filters|censorship)"
    rb")"
)


def prompt_jailbreak_marker(ctx: RuleContext) -> List[RuleHit]:
    """Flags text spans that literally contain well-known instruction-
    override markers.

    Boundaries:
    - Text inside fenced/inline code is IGNORED (users often quote attack
      strings when documenting defenses; we don't want to flag those).
    - Severity is LOW: this is a *risk marker*, not a proven attack. The
      Finding claim says so.
    - Repeated occurrences at different byte ranges produce distinct
      Findings (deduplicated only if identical location).
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        excluded = _excluded_ranges(data)
        for m in _JAILBREAK_TERMS.finditer(data):
            if _in_ranges(m.start(), excluded):
                continue
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
            out.append(RuleHit(evidences=[ev], subject=subject))
    return out


# Fake-secret token used for tests / fixtures ONLY. Not a real credential.
_FAKE_SECRET_PATTERN = re.compile(rb"VERITY_FAKE_SECRET_[A-Z0-9]{8,32}")

_DANGEROUS_SHELL = re.compile(
    rb"(?m)(?:^|[\s;&|`\(])(?:curl|wget)\s+[^\n]*\|\s*(?:sh|bash|zsh)\b"
    rb"|(?:^|[\s;&|`\(])rm\s+-rf\s+/(?![A-Za-z0-9_])"
    rb"|:\(\)\{\s*:\|:&\s*\};:"
)

# --- Sensitive host-path access pattern -------------------------------

# Well-known credential/identity/host-config paths whose access from within
# a reviewed Skill is a strong risk signal regardless of language, since a
# Skill is meant to run with least privilege, not to reach into the host
# user's SSH keys, cloud credentials, shell history, or system password
# database. Text-level only (V1 never executes anything); a real access
# would need V2 sandbox observation to confirm actual effect.
_SENSITIVE_PATH_PATTERNS = [
    re.compile(rb"~?/\.ssh/(?:id_rsa|id_ed25519|id_ecdsa|authorized_keys|known_hosts)\b"),
    re.compile(rb"~?/\.aws/credentials\b"),
    re.compile(rb"~?/\.aws/config\b"),
    re.compile(rb"~?/\.gnupg/"),
    re.compile(rb"~?/\.netrc\b"),
    re.compile(rb"~?/\.docker/config\.json\b"),
    re.compile(rb"~?/\.kube/config\b"),
    re.compile(rb"/etc/passwd\b"),
    re.compile(rb"/etc/shadow\b"),
    re.compile(rb"~?/\.bash_history\b"),
    re.compile(rb"~?/\.zsh_history\b"),
    re.compile(rb"~?/\.env\b"),
]


def skill_sensitive_path_access(ctx: RuleContext) -> List[RuleHit]:
    """Skill-engine deterministic rule — flags a literal reference to a
    well-known sensitive host path (SSH keys, cloud credentials, shell
    history, system password files, etc.) anywhere in Skill text.

    IMPORTANT: this is text-level pattern detection only; it proves the
    *literal path string is present*, not that the Skill actually reads or
    exfiltrates it (that would require V2 sandbox observation, not yet
    implemented). It does NOT execute the skill.

    Boundaries:
    - Deliberately narrow, well-known credential/identity paths only — not
      a general "any dotfile" or "any /etc path" matcher, to keep false
      positives low.
    - Fenced/inline code is NOT excluded here (unlike Prompt rules): a
      Skill's own source/config files are the artifact under review, and a
      Markdown code block inside SKILL.md showing this path is exactly as
      actionable as a bare reference.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        for pat in _SENSITIVE_PATH_PATTERNS:
            for m in pat.finditer(data):
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
                    "sensitivePathCategory": "sensitive_host_path",
                }
                out.append(RuleHit(evidences=[ev], subject=subject))
    return out


def skill_secret_like_fixture(ctx: RuleContext) -> List[RuleHit]:
    """Skill-engine deterministic rule — flags fake-secret placeholders.

    This built-in demonstration rule is separate from the controlled external
    gitleaks adapter. It matches a visibly synthetic fixture token and proves
    the secret Evidence path (redactedPreview only, no raw persistence).
    """
    out: List[RuleHit] = []
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
                raw_bytes=b"",
                producer=prod,
                sensitivity="secret",
                redacted_preview="VERITY_FAKE_SECRET_" + "*" * 8,
                evidence_kind_tag="verity_fake_secret",
            )
            subject = {
                "artifactPath": f.normalizedPath,
                "secretCategory": "fake_fixture_secret",
            }
            out.append(RuleHit(evidences=[ev], subject=subject))
    return out


def skill_dangerous_shell(ctx: RuleContext) -> List[RuleHit]:
    """Skill-engine deterministic rule — flags dangerous shell text patterns.

    IMPORTANT: this is text-level detection only; it does NOT execute the
    skill or the shell (spec §17: V1 never executes target content).
    """
    out: List[RuleHit] = []
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
            out.append(RuleHit(evidences=[ev], subject=subject))
    return out


# --- P2: unfilled template placeholders ---------------------------------

# Deliberately narrow set: mustache-style {{...}}, dollar-brace ${...},
# angle-bracket <TODO> / <FIXME> / <INSERT ...> / <YOUR ... HERE>, and
# bracket [INSERT ...]. Rationale:
# - avoid clashing with plain JSON (`{"key":...}`) by requiring paired braces
#   with content that looks like a placeholder identifier;
# - avoid clashing with legitimate code by skipping fenced code blocks;
# - keep the identifier charset conservative.
_PLACEHOLDER_PATTERNS = [
    re.compile(rb"\{\{\s*([A-Za-z_][A-Za-z0-9_\.\- ]{0,80})\s*\}\}"),  # {{ name }}
    re.compile(rb"\$\{\s*([A-Za-z_][A-Za-z0-9_\.\- ]{0,80})\s*\}"),      # ${ name }
    re.compile(rb"<\s*(TODO(?:\s+[^>\n]{0,80})?|FIXME(?:\s+[^>\n]{0,80})?|INSERT[^>\n]{0,80}|YOUR[^>\n]{0,80}HERE)\s*>", re.IGNORECASE),
    re.compile(rb"\[\s*(INSERT[^\]\n]{0,80}|TODO[^\]\n]{0,80}|YOUR[^\]\n]{0,80}HERE)\s*\]", re.IGNORECASE),
]


def prompt_unfilled_placeholder(ctx: RuleContext) -> List[RuleHit]:
    """Flag likely unfilled placeholders in a Prompt.

    Boundaries and known limits:
    - Fenced/inline code is excluded (templates are commonly shown in code).
    - Severity is MEDIUM: unfilled placeholders often cause the model to
      literalise the token, which is a real quality bug — but this rule
      cannot know whether the template was intentional demo text.
    - Same (path, placeholder text, byte range) is emitted only once.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        excluded = _excluded_ranges(data)
        for pat in _PLACEHOLDER_PATTERNS:
            for m in pat.finditer(data):
                if _in_ranges(m.start(), excluded):
                    continue
                token = m.group(0)
                ev = make_source_span_evidence(
                    snapshot_id=ctx.snapshot.snapshotId,
                    file_id=f.fileId, artifact_path=f.normalizedPath,
                    file_digest=f.contentDigest or "",
                    byte_range=(m.start(), m.end()),
                    raw_bytes=token, producer=prod,
                )
                # placeholderCategory limited enum by pattern index.
                cat = (
                    "mustache" if pat is _PLACEHOLDER_PATTERNS[0]
                    else "dollar_brace" if pat is _PLACEHOLDER_PATTERNS[1]
                    else "angle_bracket" if pat is _PLACEHOLDER_PATTERNS[2]
                    else "square_bracket"
                )
                subject = {
                    "artifactPath": f.normalizedPath,
                    "placeholderCategory": cat,
                }
                out.append(RuleHit(evidences=[ev], subject=subject))
    return out


# --- P3: system-only hardcoded secret marker ----------------------------

def prompt_system_hardcoded_secret(ctx: RuleContext) -> List[RuleHit]:
    """System-only: detects synthetic VERITY_FAKE_SECRET_* tokens embedded
    in a system prompt. High severity (a hardcoded credential inside a
    system prompt is directly extractable).

    Boundaries:
    - Runs ONLY on prompt_kind == 'system_prompt' (enforced via rule
      applicability gate). If applied to a user prompt, ReviewPlan records
      the rule as not_applicable, not silently skipped.
    - The pattern is intentionally the same synthetic marker used in
      fixtures. Later phases will delegate real secret detection to
      gitleaks (see reuse decision table).
    """
    out: List[RuleHit] = []
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
                raw_bytes=b"", producer=prod,
                sensitivity="secret",
                redacted_preview="VERITY_FAKE_SECRET_" + "*" * 8,
                evidence_kind_tag="verity_fake_secret",
            )
            subject = {
                "artifactPath": f.normalizedPath,
                "secretCategory": "fake_fixture_secret",
            }
            out.append(RuleHit(evidences=[ev], subject=subject))
    return out


# --- P4: duplicate-key numeric conflict (dual-evidence) -----------------

# Match a strict `KEY: VALUE` or `KEY = VALUE` line where VALUE is a plain
# integer or decimal. Purposely conservative: only lines whose entire
# non-whitespace content is `key op number` count. This avoids matching
# natural-language text like "temperature is 0.7 or maybe 0.9".
_KV_ASSIGN = re.compile(
    rb"(?m)^[ \t]*([A-Za-z_][A-Za-z0-9_\-]{0,63})[ \t]*(?:[:=])[ \t]*(-?\d+(?:\.\d+)?)[ \t]*$"
)


def prompt_duplicate_numeric_assignment(ctx: RuleContext) -> List[RuleHit]:
    """Flag the case where the SAME key is mechanically assigned two
    DIFFERENT numeric values inside the same prompt file.

    Boundaries:
    - Only strict `key: N` or `key = N` full-line assignments (not JSON
      objects, not free text). Case-sensitive on the key.
    - Only pairs with different values are flagged; identical repeats are
      ignored (that's redundant, not conflicting).
    - Each Finding carries TWO Evidence records (both assignment sites).
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        by_key: Dict[bytes, list] = {}
        for m in _KV_ASSIGN.finditer(data):
            key = m.group(1)
            val = m.group(2)
            by_key.setdefault(key, []).append((m.start(), m.end(), val))
        for key, occs in by_key.items():
            distinct_values = {v for (_s, _e, v) in occs}
            if len(distinct_values) < 2:
                continue
            # emit a single Finding with the FIRST two conflicting sites
            # (deterministic ordering by byte position).
            occs_sorted = sorted(occs, key=lambda t: t[0])
            # find first pair with differing values
            first = occs_sorted[0]
            second = next(o for o in occs_sorted[1:] if o[2] != first[2])
            ev1 = make_source_span_evidence(
                snapshot_id=ctx.snapshot.snapshotId,
                file_id=f.fileId, artifact_path=f.normalizedPath,
                file_digest=f.contentDigest or "",
                byte_range=(first[0], first[1]),
                raw_bytes=data[first[0]:first[1]], producer=prod,
            )
            ev2 = make_source_span_evidence(
                snapshot_id=ctx.snapshot.snapshotId,
                file_id=f.fileId, artifact_path=f.normalizedPath,
                file_digest=f.contentDigest or "",
                byte_range=(second[0], second[1]),
                raw_bytes=data[second[0]:second[1]], producer=prod,
            )
            subject = {
                "artifactPath": f.normalizedPath,
                "keyName": key.decode("utf-8", errors="replace"),
            }
            out.append(RuleHit(evidences=[ev1, ev2], subject=subject))
    return out


# --- P5: control-character contamination -------------------------------

# NUL is caught at intake and never reaches here. This rule targets other
# control chars that are legal in UTF-8 but almost always accidents in
# prompts: BEL, backspace, ESC, form feed, and the Unicode bidi override
# characters (a known prompt-injection vector).
#
# Round 46 (adapted from ProtectAI llm-guard invisible_text, which bans
# unicode categories Cf/Co/Cn): also flag zero-width / invisible
# formatting characters and the Unicode "tag" block (U+E0000-E007F), which
# is the modern invisible-instruction "tag smuggling" vector. These are
# reported under a separate ``invisible_char`` category so they are
# distinguishable from ordinary C0 controls and bidi overrides.
_CONTROL_CHARS = re.compile(rb"[\x01-\x08\x0b\x0c\x0e-\x1f\x7f]|\xe2\x80[\xaa-\xae]|\xe2\x81[\xa6-\xa9]")
_INVISIBLE_CHARS = re.compile(
    # U+200B ZWSP, U+200C ZWNJ, U+200D ZWJ, U+2060 WORD JOINER
    rb"\xe2\x80[\x8b-\x8d]|\xe2\x81\xa0"
    # U+FEFF BOM / zero-width no-break space
    rb"|\xef\xbb\xbf"
    # U+180E MONGOLIAN VOWEL SEPARATOR
    rb"|\xe1\xa0\x8e"
    # U+E0000-U+E007F Unicode TAG block (tag smuggling): UTF-8 F3 A0 80-81 xx
    rb"|\xf3\xa0[\x80\x81][\x80-\xbf]"
)


def prompt_control_character(ctx: RuleContext) -> List[RuleHit]:
    """Flag control chars / bidi-override chars in a prompt.

    Boundaries:
    - Common whitespace (tab, LF, CR) is NOT flagged.
    - Severity MEDIUM: bidi override in particular is a documented
      prompt-injection vector, but the mere presence of a stray ESC is
      more likely a copy/paste accident. The claim text says so.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        for m in _CONTROL_CHARS.finditer(data):
            ev = make_source_span_evidence(
                snapshot_id=ctx.snapshot.snapshotId,
                file_id=f.fileId, artifact_path=f.normalizedPath,
                file_digest=f.contentDigest or "",
                byte_range=(m.start(), m.end()),
                raw_bytes=m.group(0), producer=prod,
            )
            cat = "bidi_override" if m.group(0).startswith(b"\xe2") else "control_char"
            subject = {
                "artifactPath": f.normalizedPath,
                "controlCategory": cat,
            }
            out.append(RuleHit(evidences=[ev], subject=subject))
        for m in _INVISIBLE_CHARS.finditer(data):
            ev = make_source_span_evidence(
                snapshot_id=ctx.snapshot.snapshotId,
                file_id=f.fileId, artifact_path=f.normalizedPath,
                file_digest=f.contentDigest or "",
                byte_range=(m.start(), m.end()),
                raw_bytes=m.group(0), producer=prod,
            )
            subject = {
                "artifactPath": f.normalizedPath,
                "controlCategory": "invisible_char",
            }
            out.append(RuleHit(evidences=[ev], subject=subject))
    return out


# --- P6: empty / whitespace-only prompt --------------------------------

_WS_ONLY = re.compile(rb"\A\s*\Z")


def prompt_empty_or_whitespace(ctx: RuleContext) -> List[RuleHit]:
    """Flag prompts whose content is empty or entirely whitespace.

    Intake budget/NUL rejection is handled in intake.py. This rule catches
    prompts that are technically valid ingestion inputs but functionally
    useless. Severity MEDIUM.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        if _WS_ONLY.match(data):
            ev = make_source_span_evidence(
                snapshot_id=ctx.snapshot.snapshotId,
                file_id=f.fileId, artifact_path=f.normalizedPath,
                file_digest=f.contentDigest or "",
                byte_range=(0, len(data)),
                raw_bytes=data, producer=prod,
            )
            subject = {
                "artifactPath": f.normalizedPath,
                "emptyCategory": "empty_or_whitespace",
            }
            out.append(RuleHit(evidences=[ev], subject=subject))
    return out


# --- P7: open-ended tool authorisation wildcard (system-only) ----------

# Strict-form matches only. Free-text "you may use any tool you like" is
# NOT matched by design — that would need semantics we do not have.
_TOOL_WILDCARD = re.compile(
    rb"(?m)^[ \t]*(?:"
    rb"allowed_tools[ \t]*[:=][ \t]*\*"                              # allowed_tools: *
    rb"|permissions[ \t]*[:=][ \t]*\[[ \t]*(?:\"\*\"|'\*')[ \t]*\]"  # permissions: ["*"]
    rb"|tools[ \t]*[:=][ \t]*\[[ \t]*(?:\"\*\"|'\*')[ \t]*\]"
    rb")[ \t]*$"
)


def prompt_open_ended_tool_wildcard(ctx: RuleContext) -> List[RuleHit]:
    """Detect wildcard tool authorisation in a strictly structured line.

    Boundaries:
    - Only these exact forms; anything more flexible would require a real
      config parser (deferred to Skill Manifest work in Phase 3).
    - Severity HIGH: an unrestricted tool grant in a system prompt is a
      severe least-privilege violation regardless of downstream context.
    - System-prompt only (applicability gate).
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        for m in _TOOL_WILDCARD.finditer(data):
            ev = make_source_span_evidence(
                snapshot_id=ctx.snapshot.snapshotId,
                file_id=f.fileId, artifact_path=f.normalizedPath,
                file_digest=f.contentDigest or "",
                byte_range=(m.start(), m.end()),
                raw_bytes=m.group(0), producer=prod,
            )
            subject = {
                "artifactPath": f.normalizedPath,
                "wildcardCategory": "tool_wildcard",
            }
            out.append(RuleHit(evidences=[ev], subject=subject))
    return out


# --- P8: untrusted-input trust-boundary declaration is absent ----------

# Detecting that the prompt accepts external/untrusted content.
#
# Round 52 rewrite. The old version was a flat list of EXACT literal byte
# phrases ("customer message", "from the customer", ...). It missed almost
# every realistic phrasing ("a customer sends a message, read it") and so a
# support/RAG/email system prompt returned zero findings. This is the fix.
#
# We now use a MULTI-SIGNAL co-occurrence gate (same discipline as
# prompt.topic_splice / Round 51) on three axes, matched on the DECODED str
# (never byte-class regex on UTF-8 -- Round 50 lesson) with the existing
# fenced/inline-code exclusion preserved. The dividing line is deliberate:
# fire on ingestion of RICH / THIRD-PARTY content (documents, emails, files,
# attachments, tickets, retrieved/web content, tool output, ...) -- the real
# OWASP-LLM01 indirect-injection surface -- and STAY SILENT on generic
# conversational Q&A ("answer the user's question"), which would otherwise
# fire on nearly every prompt (the forbidden false-positive mode). A response
# verb (answer/reply/respond/help) never counts as ingestion.
#
# All English fragments carry their own \b anchors; Chinese entries are plain
# substrings (CJK has no word boundaries). All patterns are re.IGNORECASE and
# matched against the ORIGINAL-case decoded str so char offsets stay aligned
# with byte-offset recovery (str.lower() can change length for exotic chars).

# Axis V -- ingestion verbs. Response verbs (answer/reply/respond/help/assist/
# bare use) are deliberately EXCLUDED. "summary" (the noun) is excluded so
# "write a summary ... as a PDF attachment" does not read as ingestion.
_UIB_VERB = re.compile(
    r"\bread(?:s|ing)?\b|\breceiv(?:e|es|ed|ing)\b|\baccept(?:s|ed|ing)?\b"
    r"|\bprocess(?:es|ed|ing)?\b|\breview(?:s|ed|ing)?\b"
    r"|\banaly[sz](?:e|es|ed|ing|is)?\b"
    r"|\bsummar(?:ize|ise|izes|ises|izing|ising|ized|ised)\b"
    r"|\bpars(?:e|es|ed|ing)\b|\bingest(?:s|ed|ing)?\b|\bextract(?:s|ed|ing)?\b"
    r"|\bgiven\b|\bhandl(?:e|es|ed|ing)\b|\bgo through\b|\blook (?:at|through)\b"
    r"|阅读|读取|接收|接受|处理|分析|总结|摘要|归纳|提取|解析|根据|查看|审阅|整理",
    re.IGNORECASE)

# Axis O0 -- explicit untrusted-content compounds. Specific enough to FIRE
# ALONE (no verb/source needed); no benign non-ingesting prompt writes these.
_UIB_O0 = re.compile(
    r"\bexternal content\b|\buntrusted (?:content|input|data)\b"
    r"|\bthird[- ]party content\b|\bretrieved content\b|\btool (?:output|results?)\b"
    r"|\buploaded files?\b|\battached (?:document|file)s?\b|\bpasted text\b"
    r"|\bweb content\b|\bsearch results?\b"
    r"|外部内容|不可信内容|工具输出|工具结果|检索结果|上传的文件|参考文件|参考输入",
    re.IGNORECASE)

# Axis O1 -- rich / third-party artifact objects. Need a V OR any S nearby.
_UIB_O1 = re.compile(
    r"\battachments?\b|\bdocuments?\b|\bfiles?\b|\be-?mails?\b|\btickets?\b"
    r"|\btranscripts?\b|\bscripts?\b|\bscreenshots?\b|\bpdf\b|\bspreadsheets?\b"
    r"|\bcsv\b|\breviews?\b|\bcomments?\b|\bposts?\b|\barticles?\b|\bweb ?pages?\b"
    r"|\bsubmissions?\b|\bfeedback\b|\bresumes?\b|\bcv\b|\bcontracts?\b|\binvoices?\b"
    r"|附件|文件|文档|邮件|电子邮件|工单|转录|剧本|截图|表格|评论|评价|帖子"
    r"|文章|网页|提交内容|反馈|简历|合同",
    re.IGNORECASE)

# Axis O2 -- bare interlocutor objects. Need a V AND a strong-S (not S-user).
_UIB_O2 = re.compile(
    r"\bmessages?\b|\bmsg\b|\bquestions?\b|\bquer(?:y|ies)\b|\brequests?\b"
    r"|\binput\b|\btext\b|\bcontent\b"
    r"|消息|问题|请求|输入|内容|文本",
    re.IGNORECASE)

# Axis S -- provenance. S-arrival + S-thirdparty are "strong"; S-user is weak
# (qualifies Branch 1 only, never Branch 2).
_UIB_S_ARRIVAL = re.compile(
    r"\bsent by\b|\bsubmitted by\b|\buploaded by\b|\bprovided by\b"
    r"|\bthey (?:send|provide|upload)\b|\bsends\b|\bsubmits\b|\buploads\b"
    r"|\bprovides\b|\bincoming\b|\binbound\b"
    r"|发来|提交|上传|提供|发送的",
    re.IGNORECASE)
_UIB_S_THIRD = re.compile(
    r"\bfrom the customer\b|\bfrom the client\b|\bfrom the web\b"
    r"|\bfrom the database\b|\bfrom search\b|\bthe customers?\b|\bthe clients?\b"
    r"|\bcustomers?\b|\bclients?\b|\bexternal\b|\bthird[- ]party\b|\buntrusted\b"
    r"|\bretrieved\b|\breturned by\b|\bfetched from\b|\bscraped from\b"
    r"|\bknowledge ?base\b"
    r"|客户|外部|第三方|不可信|检索|抓取|工具返回|接口返回|对方",
    re.IGNORECASE)
_UIB_S_USER = re.compile(
    r"\bfrom the user\b|\bfrom a user\b|\bthe user'?s\b|\buser-supplied\b"
    r"|\buser provided\b|\buser input\b|\busers?\b"
    r"|用户|用户提供|用户上传|用户提交",
    re.IGNORECASE)

# Segment on sentence enders only (NOT commas -- a comma-joined RAG sentence
# such as "given a set of documents retrieved from the web, read the
# documents" must stay one segment so its signals co-occur).
_UIB_SEG_SPLIT = re.compile(r"[\n.!?。！？]")


def _uib_segments(text: str) -> List[Tuple[int, int]]:
    """Char [start, end) spans of each sentence-level segment of ``text``."""
    segs: List[Tuple[int, int]] = []
    start = 0
    for m in _UIB_SEG_SPLIT.finditer(text):
        if m.start() > start:
            segs.append((start, m.start()))
        start = m.end()
    if start < len(text):
        segs.append((start, len(text)))
    return segs


def _uib_axis_present(regex, text: str, seg: Tuple[int, int],
                      excluded: Sequence[Tuple[int, int]]) -> bool:
    """True iff ``regex`` matches inside the segment, OUTSIDE code ranges."""
    sub = text[seg[0]:seg[1]]
    for m in regex.finditer(sub):
        cpos = seg[0] + m.start()
        bpos = len(text[:cpos].encode("utf-8"))
        if not _in_ranges(bpos, excluded):
            return True
    return False


def _uib_first_object_span(text: str, seg: Tuple[int, int],
                           excluded: Sequence[Tuple[int, int]]):
    """Byte span of the first non-code content-object match (O0>O1>O2) in the
    segment, used to anchor the Finding's evidence at a navigable location."""
    sub = text[seg[0]:seg[1]]
    for regex in (_UIB_O0, _UIB_O1, _UIB_O2):
        for m in regex.finditer(sub):
            cstart = seg[0] + m.start()
            bstart = len(text[:cstart].encode("utf-8"))
            if not _in_ranges(bstart, excluded):
                bend = len(text[:seg[0] + m.end()].encode("utf-8"))
                return (bstart, bend)
    return None


def _uib_detect_acceptance(text: str, excluded: Sequence[Tuple[int, int]]):
    """Return the byte span to anchor on if the prompt declares it ingests
    external/untrusted content, else None. Multi-signal per-segment gate:

    - Branch 0: any O0 compound            -> fire (self-sufficient).
    - Branch 1: O1 rich object AND (V OR any S incl. S-user).
    - Branch 2: O2 bare object AND V AND strong-S (arrival/third-party only).
    """
    for seg in _uib_segments(text):
        has_o0 = _uib_axis_present(_UIB_O0, text, seg, excluded)
        has_o1 = _uib_axis_present(_UIB_O1, text, seg, excluded)
        has_o2 = _uib_axis_present(_UIB_O2, text, seg, excluded)
        if not (has_o0 or has_o1 or has_o2):
            continue
        has_v = _uib_axis_present(_UIB_VERB, text, seg, excluded)
        strong_s = (_uib_axis_present(_UIB_S_ARRIVAL, text, seg, excluded)
                    or _uib_axis_present(_UIB_S_THIRD, text, seg, excluded))
        any_s = strong_s or _uib_axis_present(_UIB_S_USER, text, seg, excluded)
        fire = (has_o0
                or (has_o1 and (has_v or any_s))
                or (has_o2 and has_v and strong_s))
        if fire:
            span = _uib_first_object_span(text, seg, excluded)
            if span is not None:
                return span
    return None

# Phrases that indicate the prompt already declares a trust boundary /
# anti-injection posture. If ANY of these are present, the rule does not
# fire — the mitigation is considered declared. Deliberately literal, not
# a semantic judgement of quality.
# Trust-boundary / anti-injection DECLARATIONS. If any is present the rule
# treats the prompt as having declared a defense and does not fire.
# IMPORTANT: each marker must express an actual defensive posture, not just
# contain a keyword. Round 49 fix: earlier versions matched the bare word
# "注入"/"不可信"/"越权指令"/"拒绝执行" as substrings, which false-
# negatived any prompt that used those characters in unrelated business
# text (e.g. "不得重新注入下一轮" -- about data flow, not injection defense).
# Markers now require the surrounding defensive phrasing.
_TRUST_BOUNDARY_MARKERS = (
    rb"treat.{0,80}as data", rb"not as instructions", rb"never follow",
    rb"ignore.{0,80}(?:embedded|injected|user).{0,20}instructions",
    rb"do not follow.{0,40}embedded",
    rb"untrusted (?:input|content|data|user)", rb"prompt injection",
    rb"injection attack", rb"as data.{0,20}not as instructions",
    rb"never as instructions",
    "视为数据".encode("utf-8"), "不视为指令".encode("utf-8"),
    "当作数据".encode("utf-8"), "不当作指令".encode("utf-8"),
    # anti-override declarations: must pair "ignore/reject" with an
    # injection/override object, not the bare verb
    "忽略.{0,40}(?:越权|注入|覆盖|恶意).{0,10}指令".encode("utf-8"),
    "拒绝.{0,20}(?:越权|注入|覆盖|恶意)".encode("utf-8"),
    "提示注入".encode("utf-8"), "注入攻击".encode("utf-8"),
    "不可信(?:输入|内容|数据|来源)".encode("utf-8"),
    "角色切换指令".encode("utf-8"),
)
_TRUST_BOUNDARY_RE = [re.compile(p, re.IGNORECASE) for p in _TRUST_BOUNDARY_MARKERS]


def prompt_untrusted_input_boundary_undeclared(ctx: RuleContext) -> List[RuleHit]:
    """Flag a system prompt that declares it accepts external/user-supplied
    content but never states a trust-boundary / anti-injection-override
    rule anywhere in the document.

    This is a structural absence pattern ("declares X, never declares the
    companion mitigation Y"), the same class as existing manifest checks
    (e.g. missing reference). It is a SIGNAL, not proof of an actual
    vulnerability: a prompt could rely on an external system layer for
    this. Severity is therefore MEDIUM, matching the discipline used by
    other structural-absence rules in this registry.

    Boundaries:
    - System-prompt only (a user prompt is not itself a trust boundary
      policy document).
    - Only fires once per file (not once per input-acceptance mention):
      the absence is a whole-document fact, not a per-occurrence one.
    - Fenced/inline code excluded when scanning for BOTH the acceptance
      signals and the trust-boundary markers, so an example embedded in a
      code block cannot itself satisfy or violate the check.
    - Round 52: acceptance detection is a multi-signal co-occurrence gate
      (verb + content-object + provenance) on the decoded str, tuned to
      fire on ingestion of rich/third-party content and stay silent on
      generic conversational Q&A. It still cannot judge whether a present
      mitigation phrase is actually effective, only whether one exists.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        excluded = _excluded_ranges(data)
        text = data.decode("utf-8", "ignore")

        anchor = _uib_detect_acceptance(text, excluded)
        if anchor is None:
            continue
        has_boundary = False
        lower = data.lower()
        for pat in _TRUST_BOUNDARY_RE:
            for match in pat.finditer(lower):
                if not _in_ranges(match.start(), excluded):
                    has_boundary = True
                    break
            if has_boundary:
                break
        if has_boundary:
            continue
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id=f.fileId, artifact_path=f.normalizedPath,
            file_digest=f.contentDigest or "",
            byte_range=anchor,
            raw_bytes=data[anchor[0]:anchor[1]], producer=prod,
        )
        subject = {
            "artifactPath": f.normalizedPath,
            "boundaryCategory": "untrusted_input_boundary_undeclared",
        }
        out.append(RuleHit(evidences=[ev], subject=subject))
    return out


# --- P9: dangling section/rule reference ---------------------------------

# "see section 7" / "见第7节" / "见XX规则" style references. Captures the
# referenced token (a number or a short quoted/bare title) so it can be
# checked against the document's own headings.
# Only strict NUMBERED section references. A separate class of "named rule"
# reference (e.g. "见回复规则") would require verifying that some heading's
# *title text* matches the reference, which is far more prone to false
# positives from paraphrasing and is intentionally left out of this rule.
_SECTION_REF_PATTERNS = [
    re.compile(rb"(?:see|per)\s+section\s+([0-9]+(?:\.[0-9]+)*)", re.IGNORECASE),
    re.compile("见第\\s*([0-9０-９]+(?:\\.[0-9]+)*)\\s*节".encode("utf-8")),
]
# Matches numbered headings in either of two common forms:
#   "## 7. Title" / "7.2 Title" / "7）Title"  (bare numbered heading)
#   "## Section 7" / "Section 7.2"              (English word + number)
_HEADING_NUMBER_RE = re.compile(
    ("^\\s*(?:#+\\s*)?([0-9]+(?:\\.[0-9]+)*)[\\.\\s" + "）" + ")]").encode("utf-8"),
    re.MULTILINE)
# Anchored at line start (with optional markdown #/whitespace) so a mid-
# sentence reference occurrence ("See section 7...") is never mistaken for
# a heading declaration of that same section.
_HEADING_NUMBER_WORD_RE = re.compile(
    rb"^\s*(?:#+\s*)?section\s+([0-9]+(?:\.[0-9]+)*)", re.IGNORECASE | re.MULTILINE)


def prompt_dangling_section_reference(ctx: RuleContext) -> List[RuleHit]:
    """Flag a numbered-section reference ("see section 7", "见第7节") whose
    target section number does not appear as a heading/number anywhere in
    the document.

    Boundaries:
    - Only strict numbered-section reference forms are matched (English
      "see/per section N[.N]", Chinese "见第N节"). Free-form prose
      pointers ("see the rules above") are not matched — this rule cannot
      judge those without understanding context, and a false claim of
      "dangling" there would be worse than silence.
    - The existence check only looks for the referenced number appearing
      as a heading/numbered-item marker at line start; it cannot verify
      that the *content* under that number still covers the claimed
      rule (that is a semantic judgement, out of scope for a static rule).
    - Fenced/inline code excluded on both the reference occurrence and the
      heading scan.
    - Each distinct dangling reference text is reported once per file.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        excluded = _excluded_ranges(data)
        heading_numbers = {m.group(1).decode("utf-8", "ignore")
                           for m in _HEADING_NUMBER_RE.finditer(data)
                           if not _in_ranges(m.start(), excluded)}
        heading_numbers |= {m.group(1).decode("utf-8", "ignore")
                            for m in _HEADING_NUMBER_WORD_RE.finditer(data)
                            if not _in_ranges(m.start(), excluded)}
        seen_refs = set()
        for pat in _SECTION_REF_PATTERNS:
            for m in pat.finditer(data):
                if _in_ranges(m.start(), excluded):
                    continue
                target = m.group(1).decode("utf-8", "ignore")
                # Normalise full-width digits to ASCII for comparison.
                target_norm = target.translate(str.maketrans(
                    "０１２３４５６７８９", "0123456789"))
                if target_norm in heading_numbers:
                    continue
                key = (f.normalizedPath, target_norm)
                if key in seen_refs:
                    continue
                seen_refs.add(key)
                ev = make_source_span_evidence(
                    snapshot_id=ctx.snapshot.snapshotId,
                    file_id=f.fileId, artifact_path=f.normalizedPath,
                    file_digest=f.contentDigest or "",
                    byte_range=(m.start(), m.end()),
                    raw_bytes=m.group(0), producer=prod,
                )
                subject = {
                    "artifactPath": f.normalizedPath,
                    "referenceText": m.group(0).decode("utf-8", "ignore"),
                }
                out.append(RuleHit(evidences=[ev], subject=subject))
    return out


# --- P11: embedded system-role / chat-template control tokens -----------

# Literal control tokens that, if present inside prompt/skill *content*,
# can hijack the instruction hierarchy of a downstream model that renders
# the artifact into a chat template. Adapted from vigil-llm's
# SystemInstructions YARA rule (Adam M. Swanda, Apache-2.0) plus the common
# ChatML / Llama-2 / Guidance control tokens. These are exact literals, not
# heuristics -- a legitimate prompt almost never needs to embed another
# model's turn-delimiter tokens.
_SYSTEM_ROLE_MARKERS = (
    b"<|im_start|>system", b"<|im_start|>assistant", b"<|im_end|>",
    b"[system](#assistant)", b"[system](#context)",
    b"<<SYS>>", b"<</SYS>>", b"<s>[INST]", b"[/INST]",
    b"{{#system~}}", b"{{/system~}}", b"<|system|>", b"<|assistant|>",
    b"### System:", b"System Instruction: ",
)


def prompt_embedded_system_role_marker(ctx: RuleContext) -> List[RuleHit]:
    """Flag literal chat-template/system-role control tokens embedded in the
    reviewed text (indirect prompt-injection vector).

    Boundaries:
    - Exact-literal case-insensitive match on a closed token list; no
      heuristics, so false positives are limited to text that literally
      contains another model's control tokens.
    - Fenced/inline code is excluded (docs frequently quote these tokens
      when explaining them).
    - Severity MEDIUM: presence is a real injection vector but not proof of
      a successful attack.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        lower = data.lower()
        excluded = _excluded_ranges(data)
        seen = set()
        for marker in _SYSTEM_ROLE_MARKERS:
            start = 0
            ml = marker.lower()
            while True:
                idx = lower.find(ml, start)
                if idx == -1:
                    break
                start = idx + len(ml)
                if _in_ranges(idx, excluded):
                    continue
                if idx in seen:
                    continue
                seen.add(idx)
                ev = make_source_span_evidence(
                    snapshot_id=ctx.snapshot.snapshotId,
                    file_id=f.fileId, artifact_path=f.normalizedPath,
                    file_digest=f.contentDigest or "",
                    byte_range=(idx, idx + len(marker)),
                    raw_bytes=data[idx:idx + len(marker)], producer=prod,
                )
                out.append(RuleHit(evidences=[ev], subject={
                    "artifactPath": f.normalizedPath,
                    "markerCategory": "embedded_system_role_marker",
                }))
                break  # one finding per marker kind per file is enough
    return out


# --- P12: markdown data-exfiltration image ------------------------------

# Markdown image whose URL carries a query string: ![alt](https://h/p?...=...)
# A model induced to render this leaks whatever it puts in the query. From
# vigil-llm MarkdownExfiltration YARA + the Bing-Chat exfil PoC it cites.
_MD_EXFIL = re.compile(
    rb"!\[[^\]]*\]\(\s*https?://[^)\s]+\?[^)\s]*=[^)\s]*\)", re.IGNORECASE)


def prompt_markdown_data_exfiltration(ctx: RuleContext) -> List[RuleHit]:
    """Flag a markdown image whose URL has a query string (data-exfil shape).

    Boundaries:
    - Only markdown *image* syntax (`![...](url?...=...)`) with an http(s)
      URL that has a query parameter. Plain links and query-less images are
      not matched.
    - Fenced/inline code excluded.
    - Severity MEDIUM: this is the known exfil channel shape, not proof the
      querystring actually carries secret data.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        excluded = _excluded_ranges(data)
        for m in _MD_EXFIL.finditer(data):
            if _in_ranges(m.start(), excluded):
                continue
            ev = make_source_span_evidence(
                snapshot_id=ctx.snapshot.snapshotId,
                file_id=f.fileId, artifact_path=f.normalizedPath,
                file_digest=f.contentDigest or "",
                byte_range=(m.start(), m.end()),
                raw_bytes=m.group(0), producer=prod,
            )
            out.append(RuleHit(evidences=[ev], subject={
                "artifactPath": f.normalizedPath,
                "exfilCategory": "markdown_image_querystring",
            }))
    return out


# --- P13: encoded (base64/hex) instruction-bypass payload ---------------

# Inspired by NVIDIA garak's encoding-injection probes (InjectBase64 /
# InjectHex etc.), which smuggle instructions past filters by encoding
# them. For a STATIC auditor the analogous, low-false-positive detection
# is: the text contains a long base64/hex blob that *actually decodes to*
# an instruction-bypass phrase. We only flag when the decoded content
# matches the same authoritative bypass grammar used by
# prompt.instruction_override_marker -- so a benign base64 asset (an
# image, a token) is never flagged, only an encoded hidden instruction.
_B64_BLOB = re.compile(rb"[A-Za-z0-9+/]{24,}={0,2}")
_HEX_BLOB = re.compile(rb"(?:[0-9A-Fa-f]{2}){16,}")
# Decoded-content trigger: a compact subset of the bypass grammar, matched
# against decoded text (already lowercased).
_DECODED_BYPASS = re.compile(
    rb"(?:ignore|disregard|forget|bypass|override)\b[^\n]{0,40}"
    rb"(?:instruction|previous|prior|rule|prompt|system|above|safety|guardrail)"
    rb"|you are (?:now )?dan\b|do anything now|developer mode"
)


def _decoded_has_bypass(raw: bytes) -> bool:
    try:
        text = raw.decode("utf-8", "ignore").lower().encode("utf-8")
    except Exception:
        return False
    return bool(_DECODED_BYPASS.search(text))


def prompt_encoded_injection_payload(ctx: RuleContext) -> List[RuleHit]:
    """Flag a base64/hex blob that decodes to an instruction-bypass phrase.

    Boundaries:
    - Only fires when the DECODED bytes contain a known bypass phrase, so
      ordinary encoded assets/tokens are not flagged (very low false
      positive rate by construction).
    - Fenced/inline code excluded (docs may show encoded examples).
    - Severity MEDIUM: an encoded hidden instruction is a real smuggling
      vector; static analysis cannot prove the model would obey it.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        excluded = _excluded_ranges(data)
        seen: set = set()
        for pat, kind, decoder in (
            (_B64_BLOB, "base64", _try_b64),
            (_HEX_BLOB, "hex", _try_hex),
        ):
            for m in pat.finditer(data):
                if _in_ranges(m.start(), excluded):
                    continue
                decoded = decoder(m.group(0))
                if decoded is None or not _decoded_has_bypass(decoded):
                    continue
                key = (m.start(), kind)
                if key in seen:
                    continue
                seen.add(key)
                ev = make_source_span_evidence(
                    snapshot_id=ctx.snapshot.snapshotId,
                    file_id=f.fileId, artifact_path=f.normalizedPath,
                    file_digest=f.contentDigest or "",
                    byte_range=(m.start(), m.end()),
                    raw_bytes=m.group(0), producer=prod,
                )
                out.append(RuleHit(evidences=[ev], subject={
                    "artifactPath": f.normalizedPath,
                    "encodingCategory": kind,
                }))
    return out


def _try_b64(blob: bytes) -> Optional[bytes]:
    try:
        pad = blob + b"=" * (-len(blob) % 4)
        return base64.b64decode(pad, validate=True)
    except (binascii.Error, ValueError):
        return None


def _try_hex(blob: bytes) -> Optional[bytes]:
    try:
        return binascii.unhexlify(blob[: len(blob) - (len(blob) % 2)])
    except (binascii.Error, ValueError):
        return None


# --- P14: named dangling reference ("see the reply rules" / “见回复规则”) --

# Butler report #4: a prompt points at a named rule/section ("见回复规则",
# "见上文XX规则", "per the output rules") that never appears as an actual
# heading/definition anywhere else in the document. Complements the
# numbered-section rule (prompt.dangling_section_reference). Deterministic:
# extract the referenced NAME, then require that name to appear again as a
# heading-like line elsewhere; if it appears only at the reference site,
# it is dangling.
# Match on decoded text (str), not bytes, so Unicode classes work. A CJK
# name of 2-12 chars between the reference verb and a rule-noun suffix.
_NAMED_REF = re.compile(
    "(?:见|参见|详见|按)\\s*"
    "([\u4e00-\u9fff]{1,10}?(?:规则|约定|协议|流程|定义|说明|部分|机制))")
def prompt_named_dangling_reference(ctx: RuleContext) -> List[RuleHit]:
    """Flag a reference to a NAMED rule/section whose name never appears
    elsewhere in the document as a defined term/heading.

    Boundaries:
    - Only Chinese “见<name>规则/约定/协议/流程/定义/说明/部分/机制”
      forms (the shape Butler observed on the NexPlay SP). English named
      refs are left for a later iteration to keep false positives low.
    - Existence check: the captured name must appear at least once MORE in
      the document (outside the reference occurrence). If the name occurs
      only at the reference site, it is dangling.
    - Fenced/inline code excluded.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        excluded = _excluded_ranges(data)
        text = data.decode("utf-8", "ignore")
        seen = set()
        for m in _NAMED_REF.finditer(text):
            # byte offset of the match start for code-exclusion + evidence
            byte_start = len(text[:m.start()].encode("utf-8"))
            byte_end = len(text[:m.end()].encode("utf-8"))
            if _in_ranges(byte_start, excluded):
                continue
            name = m.group(1)
            # The name must appear somewhere else in the document (a
            # definition/heading). Count occurrences of the name overall;
            # >1 means it is defined elsewhere, ==1 means only the ref.
            if text.count(name) > 1:
                continue
            if name in seen:
                continue
            seen.add(name)
            ev = make_source_span_evidence(
                snapshot_id=ctx.snapshot.snapshotId,
                file_id=f.fileId, artifact_path=f.normalizedPath,
                file_digest=f.contentDigest or "",
                byte_range=(byte_start, byte_end),
                raw_bytes=data[byte_start:byte_end], producer=prod,
            )
            out.append(RuleHit(evidences=[ev], subject={
                "artifactPath": f.normalizedPath,
                "referenceText": m.group(0),
            }))
    return out


# --- P15: duplicated content line (Butler minor #2) ---------------------


def prompt_duplicate_content_line(ctx: RuleContext) -> List[RuleHit]:
    """Flag a substantial content line that appears verbatim more than once.

    Butler minor finding #2 (repeated statements dilute attention / risk
    inconsistent edits). Deterministic and low-false-positive:
    - Only lines >= 24 visible chars are considered (short lines like
      headings, separators, list bullets repeat legitimately).
    - Markdown table/separator lines and fenced code are ignored.
    - Reports the SECOND+ occurrence, citing it; identity is the
      normalized line text so one Finding per duplicated line.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        excluded = _excluded_ranges(data)
        seen_norm: Dict[bytes, int] = {}
        reported = set()
        offset = 0
        for raw in data.splitlines(keepends=True):
            line = raw.rstrip(b"\r\n")
            start = offset
            offset += len(raw)
            stripped = line.strip()
            # skip short lines, markdown separators/tables, fenced code
            if len(stripped) < 24:
                continue
            if _in_ranges(start, excluded):
                continue
            if set(stripped) <= set(b"|-=+:_ #*"):
                continue
            norm = b" ".join(stripped.split())
            if norm in seen_norm:
                if norm not in reported:
                    reported.add(norm)
                    ev = make_source_span_evidence(
                        snapshot_id=ctx.snapshot.snapshotId,
                        file_id=f.fileId, artifact_path=f.normalizedPath,
                        file_digest=f.contentDigest or "",
                        byte_range=(start, start + len(line)),
                        raw_bytes=line, producer=prod,
                    )
                    out.append(RuleHit(evidences=[ev], subject={
                        "artifactPath": f.normalizedPath,
                        "duplicateCategory": "repeated_content_line",
                    }))
            else:
                seen_norm[norm] = start
    return out


# --- P16: full-width / half-width mixed digits+latin (Butler minor #4) --

# Full-width LETTERS and DIGITS only (U+FF10-FF19 digits, U+FF21-FF3A
# uppercase, U+FF41-FF5A lowercase). Deliberately NOT full-width
# punctuation: Chinese prose legitimately uses ，。：（） etc., so
# flagging those would be pure noise. Full-width letters/digits, by
# contrast, almost always indicate an identifier/field-name/number that
# will fail exact half-width matching -- the actual parsing hazard Butler
# flagged.
# Matched on DECODED text (str): a byte-class on UTF-8 bytes would match
# individual continuation/lead bytes of ordinary CJK characters (e.g. the
# 0xE4 lead byte of 你), so this MUST run on decoded str.
_FULLWIDTH = re.compile("[\uff10-\uff19\uff21-\uff3a\uff41-\uff5a]")


def prompt_fullwidth_mixed(ctx: RuleContext) -> List[RuleHit]:
    """Flag full-width ASCII letters/digits, which mixed with half-width
    forms break exact field-name/JSON/number parsing.

    Butler minor finding #4. Deterministic: any full-width letter/digit
    (U+FF10-FF19 / U+FF21-FF3A / U+FF41-FF5A) is flagged once per file (a
    single Finding at the first occurrence, since the issue is
    document-level). Full-width PUNCTUATION is intentionally not flagged
    because it is normal in Chinese prose.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        excluded = _excluded_ranges(data)
        text = data.decode("utf-8", "ignore")
        chosen = None
        for cand in _FULLWIDTH.finditer(text):
            byte_start = len(text[:cand.start()].encode("utf-8"))
            if not _in_ranges(byte_start, excluded):
                byte_end = len(text[:cand.end()].encode("utf-8"))
                chosen = (byte_start, byte_end)
                break
        if chosen is None:
            continue
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id=f.fileId, artifact_path=f.normalizedPath,
            file_digest=f.contentDigest or "",
            byte_range=chosen,
            raw_bytes=data[chosen[0]:chosen[1]], producer=prod,
        )
        out.append(RuleHit(evidences=[ev], subject={
            "artifactPath": f.normalizedPath,
            "widthCategory": "fullwidth_ascii_variant",
        }))
    return out


_SMART_QUOTED_JSON_KEY = re.compile(
    r"(?:\{|,)\s*(?:“[^”\n]{1,80}”|‘[^’\n]{1,80}’)\s*:")
_SINGLE_QUOTED_JSON_KEY = re.compile(
    r"(?:\{|,)\s*'[^'\n]{1,80}'\s*:")
_BACKTICK_JSON_KEY = re.compile(
    r"(?:\{|,)\s*`[^`\n]{1,80}`\s*:")
_NEGATED_JSON_EXAMPLE_TERMS = (
    "invalid json", "bad json", "incorrect json", "do not use json",
    "错误 json", "无效 json", "不要使用 json",
)


def prompt_structured_quote_inconsistency(
        ctx: RuleContext) -> List[RuleHit]:
    """Detect non-JSON quote forms only inside an explicit JSON context."""
    out: List[RuleHit] = []
    prod = Producer(
        componentId=ctx.rule.ruleId,
        componentVersion=ctx.rule.ruleVersion,
        executionId=ctx.execution_id)
    patterns = (
        (_SMART_QUOTED_JSON_KEY, "smart_quote_json_key"),
        (_SINGLE_QUOTED_JSON_KEY, "single_quote_json_key"),
        (_BACKTICK_JSON_KEY, "backtick_json_key"),
    )
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        text = data.decode("utf-8", "ignore")
        chosen = None
        for pattern, category in patterns:
            for match in pattern.finditer(text):
                context = text[
                    max(0, match.start() - 240):
                    min(len(text), match.end() + 240)].lower()
                if "json" not in context:
                    continue
                if any(term in context for term in
                       _NEGATED_JSON_EXAMPLE_TERMS):
                    continue
                chosen = (match.start(), match.end(), category)
                break
            if chosen is not None:
                break
        if chosen is None:
            continue
        char_start, char_end, category = chosen
        byte_start = len(text[:char_start].encode("utf-8"))
        byte_end = len(text[:char_end].encode("utf-8"))
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id=f.fileId, artifact_path=f.normalizedPath,
            file_digest=f.contentDigest or "",
            byte_range=(byte_start, byte_end),
            raw_bytes=data[byte_start:byte_end], producer=prod,
        )
        out.append(RuleHit(evidences=[ev], subject={
            "artifactPath": f.normalizedPath,
            "quoteCategory": category,
        }))
    return out


# --- P17: head/body topic splice (Butler #1) ----------------------------
#
# Detects the specific, high-confidence splice Butler flagged on the
# NexPlay SP: an image/media STYLE description glued onto the head of an
# AGENT system prompt. This is a deterministic, dependency-free
# approximation of what OSS tools (llm-guard relevance/ban_topics) do with
# neural models -- it does NOT attempt general topic-coherence, only this
# concrete cross-domain-head pattern, and requires THREE independent
# signals to fire so ordinary prompts (incl. title-first and pure image
# prompts) are never flagged:
#   1. the first line carries >=2 image/media STYLE-domain terms, AND
#   2. the body carries >=2 AGENT-instruction terms, AND
#   3. head vs body character-3gram Jaccard overlap is near zero.
_STYLE_DOMAIN_TERMS = (
    "风格", "剧照", "皮肤纹理", "布料", "光线", "色彩", "景深", "构图",
    "镜头", "写实", "摄影", "画风", "质感", "氛围感", "调色",
    "photoreal", "cinematic", "lighting", "texture", "render",
    "aspect ratio", "depth of field", "bokeh", "color grading",
)
_AGENT_DOMAIN_TERMS = (
    "你是", "系统提示", "负责", "工作流", "工具", "用户", "任务",
    "system prompt", "agent", "skill", "you are", "assistant",
    "instruction", "role", "task", "workflow",
)


def _char_ngrams(s: str, n: int = 3):
    s = "".join(s.split()).lower()
    return set(s[i:i + n] for i in range(len(s) - n + 1)) if len(s) >= n else set()


def prompt_topic_splice(ctx: RuleContext) -> List[RuleHit]:
    """Flag an image/media STYLE description spliced onto the head of an
    AGENT system prompt (Butler report #1).

    Requires three independent signals (style head + agent body + near-zero
    lexical overlap) so ordinary prompts, title-first prompts and pure
    image prompts are not flagged. Deterministic, dependency-free.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        text = data.decode("utf-8", "ignore")
        lines = [(ln.strip()) for ln in text.splitlines() if ln.strip()]
        if len(lines) < 4:
            continue
        head = lines[0]
        body = " ".join(lines[1:])
        if len(head) < 12 or len(body) < 80:
            continue
        hl, bl = head.lower(), body.lower()
        head_style = sum(t in hl for t in _STYLE_DOMAIN_TERMS)
        body_agent = sum(t in bl for t in _AGENT_DOMAIN_TERMS)
        if head_style < 2 or body_agent < 2:
            continue
        overlap = (lambda a, b: len(a & b) / len(a | b) if a and b else 0.0)(
            _char_ngrams(head), _char_ngrams(body))
        if overlap >= 0.05:
            continue
        # anchor at the head line (byte range)
        head_bytes = head.encode("utf-8")
        idx = data.find(head_bytes)
        if idx < 0:
            idx = 0
            head_bytes = data[:len(head_bytes)]
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id=f.fileId, artifact_path=f.normalizedPath,
            file_digest=f.contentDigest or "",
            byte_range=(idx, idx + len(head_bytes)),
            raw_bytes=head_bytes, producer=prod,
        )
        out.append(RuleHit(evidences=[ev], subject={
            "artifactPath": f.normalizedPath,
            "spliceCategory": "style_head_on_agent_body",
        }))
    return out


# --- P18: version-naming inconsistency (Butler minor #1) ----------------
#
# Flags the SAME entity referred to with inconsistent version FORMS in one
# document ("v2.0" vs "version 2" vs "2.0.0" vs "V2"). Deterministic and
# precision-gated: an explicit version prefix is required on at least one
# side (so plain decimals like "3.14" are never versions), the two mentions
# must attach to the SAME normalized entity key (so "python 3.11" and
# "api v1" are never compared), and their numeric tuples must be
# prefix-compatible (so a genuine v1->v2 migration is not flagged).
_VERSION_PREFIXED = re.compile(
    r"(?P<entity>[A-Za-z][A-Za-z0-9_+.\-]{0,31})?\s*"
    r"\b(?P<prefix>v|ver|version)\.?\s*(?P<num>\d+(?:\.\d+){0,3})\b",
    re.IGNORECASE)
# A bare dotted number attached to a preceding entity word ("schema 2.0.0").
_VERSION_BARE = re.compile(
    r"\b(?P<entity>[A-Za-z][A-Za-z0-9_+.\-]{0,31})\s+(?P<num>\d+\.\d+(?:\.\d+){0,2})\b")
# Chinese "版本 2" / "版本2.0" / "第2版" style.
_VERSION_ZH = re.compile("(?P<prefix>版本|版)\\s*(?P<num>\\d+(?:\\.\\d+){0,3})")


def _version_tuple(num: str) -> Tuple[int, ...]:
    return tuple(int(p) for p in num.split(".") if p != "")


def _version_compatible(a: Tuple[int, ...], b: Tuple[int, ...]) -> bool:
    """True iff one tuple is a prefix of the other (same version, different
    notation), e.g. (2,) ~ (2,0) ~ (2,0,0). (1,) vs (2,) is NOT compatible."""
    n = min(len(a), len(b))
    return a[:n] == b[:n]


def prompt_version_naming_inconsistent(ctx: RuleContext) -> List[RuleHit]:
    """Flag one entity written with inconsistent version forms (Butler
    minor #1). Fires once per (file, entity) whose mentions differ in
    surface form but agree (prefix-compatible) on the numeric version.

    Boundaries:
    - At least one mention in the group must carry an explicit version
      prefix (v/ver/version/版本/版); bare decimals alone are never
      treated as versions.
    - Mentions are grouped by a normalized preceding-entity key; a mention
      with no attachable entity is skipped (conservative). Distinct
      entities are never compared.
    - Only numerically prefix-compatible tuples are flagged; a real
      v1->v2 change is left alone.
    - Fenced/inline code excluded; matched on decoded str with byte-offset
      recovery (Round 50).
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        excluded = _excluded_ranges(data)
        text = data.decode("utf-8", "ignore")
        # mention = (entity_key, tuple, has_prefix, surface, char_start, char_end)
        mentions = []

        def _add(entity, num, has_prefix, cstart, cend):
            if not entity:
                return
            key = entity.strip().lower().strip(".-_")
            if not key or key.isdigit():
                return
            bstart = len(text[:cstart].encode("utf-8"))
            if _in_ranges(bstart, excluded):
                return
            surface = text[cstart:cend]
            mentions.append((key, _version_tuple(num), has_prefix,
                             surface, cstart, cend))

        for m in _VERSION_PREFIXED.finditer(text):
            _add(m.group("entity"), m.group("num"), True,
                 m.start(), m.end())
        for m in _VERSION_BARE.finditer(text):
            _add(m.group("entity"), m.group("num"), False,
                 m.start(), m.end())
        for m in _VERSION_ZH.finditer(text):
            # Chinese: the entity is the token(s) immediately before 版本/版.
            pre = text[max(0, m.start() - 12):m.start()]
            ent = re.search(r"([A-Za-z0-9_+.\-]+|[一-鿿]{2,6})\s*$", pre)
            _add(ent.group(1) if ent else "", m.group("num"), True,
                 m.start(), m.end())

        # group by entity key
        groups: Dict[str, list] = {}
        for mm in mentions:
            groups.setdefault(mm[0], []).append(mm)

        for key, occs in groups.items():
            if len(occs) < 2:
                continue
            if not any(o[2] for o in occs):  # need >=1 explicit prefix
                continue
            occs_sorted = sorted(occs, key=lambda t: t[4])
            first = occs_sorted[0]
            partner = None
            for o in occs_sorted[1:]:
                # inconsistent SURFACE form but compatible numeric version
                if (o[3].strip().lower() != first[3].strip().lower()
                        and _version_compatible(o[1], first[1])):
                    partner = o
                    break
            if partner is None:
                continue
            evs = []
            for o in (first, partner):
                bstart = len(text[:o[4]].encode("utf-8"))
                bend = len(text[:o[5]].encode("utf-8"))
                evs.append(make_source_span_evidence(
                    snapshot_id=ctx.snapshot.snapshotId,
                    file_id=f.fileId, artifact_path=f.normalizedPath,
                    file_digest=f.contentDigest or "",
                    byte_range=(bstart, bend),
                    raw_bytes=data[bstart:bend], producer=prod,
                ))
            out.append(RuleHit(evidences=evs, subject={
                "artifactPath": f.normalizedPath,
                "entityKey": key,
            }))
    return out


# --- P19: pinned model/endpoint with no fallback (Butler minor #5) ------
#
# Flags a prompt that names a PINNED model/endpoint/API for an imperative
# step and declares NO fallback/degradation/retry path anywhere. This is a
# structural-absence rule (same shape as the trust-boundary rule). Honest
# scope: the DETERMINISTIC part is "pinned identifier present in imperative
# context" + "fallback vocabulary absent". Whether the step is truly
# critical, and whether a declared fallback actually covers THIS endpoint,
# are judgment calls left to the human (stated in guidance). Severity low.
_ENDPOINT_PINNED = re.compile(
    r"\bgpt-4o?\b|\bgpt-4\.\d\b|\bgpt-3\.5\b|\bo1(?:-[\w.]+)?\b"
    r"|\bclaude-\d[\w.\-]*\b|\bclaude-(?:opus|sonnet|haiku)[\w.\-]*\b"
    r"|\bgemini-\d[\w.\-]*\b|\btext-embedding-3-\w+\b|\bdeepseek-[\w.\-]+\b"
    r"|\bqwen[\w.\-]+\b"
    r"|https?://[^\s)\"']+"
    r"|\bmodel\s*[:=]\s*[\"'][^\"']+[\"']",
    re.IGNORECASE)
_ENDPOINT_VERB = re.compile(
    r"\buse\b|\buses\b|\bcall\b|\bcalls\b|\bquery\b|\bqueries\b|\binvoke\b"
    r"|\binvokes\b|\bsend to\b|\broute to\b|\bmust use\b|\bdepends on\b"
    r"|调用|使用|请求|发送到|依赖",
    re.IGNORECASE)
_ENDPOINT_FALLBACK = re.compile(
    r"\bfall\s?backs?\b|\bfalling back\b|\bretr(?:y|ies|ying)\b|\bback\s?off\b"
    r"|\bdegrad(?:e|es|ed|ation)\b|\bif [^.\n]{0,40}\bfails?\b|\bon failure\b"
    r"|\bif [^.\n]{0,40}\bunavailable\b|\btime ?out\b|\balternative model\b"
    r"|\bsecondary\b|\bbackup\b|\belse use\b|\bfailover\b"
    r"|回退|降级|重试|失败时|不可用时|备用|超时|容错|兜底",
    re.IGNORECASE)


def prompt_model_endpoint_no_fallback(ctx: RuleContext) -> List[RuleHit]:
    """Flag a pinned model/endpoint used imperatively with no declared
    fallback/degradation path (Butler minor #5).

    Boundaries:
    - Requires a PINNED, vendor-recognizable identifier (model id, URL, or
      `model: "..."`) co-occurring in a segment with an imperative verb;
      vague "the model" or a passive "built on gpt-4o" mention does not
      fire.
    - Suppressed if ANY fallback/retry/degradation vocabulary appears
      anywhere outside code (literal presence only -- cannot verify the
      fallback actually covers this endpoint).
    - Precision over recall: only catches recognizable pinned identifiers.
      Severity low (advisory robustness hygiene). Fenced/inline code
      excluded; decoded-str matching with byte-offset recovery.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        excluded = _excluded_ranges(data)
        text = data.decode("utf-8", "ignore")

        # whole-document fallback presence (outside code) => suppress
        has_fallback = False
        for m in _ENDPOINT_FALLBACK.finditer(text):
            if not _in_ranges(len(text[:m.start()].encode("utf-8")), excluded):
                has_fallback = True
                break
        if has_fallback:
            continue

        anchor = None
        for seg in _uib_segments(text):
            has_id = _uib_axis_present(_ENDPOINT_PINNED, text, seg, excluded)
            has_verb = _uib_axis_present(_ENDPOINT_VERB, text, seg, excluded)
            if has_id and has_verb:
                sub = text[seg[0]:seg[1]]
                for m in _ENDPOINT_PINNED.finditer(sub):
                    cstart = seg[0] + m.start()
                    bstart = len(text[:cstart].encode("utf-8"))
                    if not _in_ranges(bstart, excluded):
                        bend = len(text[:seg[0] + m.end()].encode("utf-8"))
                        anchor = (bstart, bend)
                        break
            if anchor is not None:
                break
        if anchor is None:
            continue
        ev = make_source_span_evidence(
            snapshot_id=ctx.snapshot.snapshotId,
            file_id=f.fileId, artifact_path=f.normalizedPath,
            file_digest=f.contentDigest or "",
            byte_range=anchor,
            raw_bytes=data[anchor[0]:anchor[1]], producer=prod,
        )
        out.append(RuleHit(evidences=[ev], subject={
            "artifactPath": f.normalizedPath,
            "endpointCategory": "pinned_no_fallback",
        }))
    return out


# --- P20: mutually exclusive top-level output formats -------------------

_OUTPUT_JSON = re.compile(r"\bjson\b", re.IGNORECASE)
_OUTPUT_TOP_LEVEL = re.compile(
    r"\b(?:return|output|respond|reply|produce|emit)\b"
    r"|输出|返回|回复|响应|生成",
    re.IGNORECASE,
)
_OUTPUT_STRICT = re.compile(
    r"\b(?:must|shall|only|exactly|strictly|required)\b"
    r"|必须|只能|仅|只输出|严格|唯一",
    re.IGNORECASE,
)
_OUTPUT_JSON_NEGATION = re.compile(
    r"\b(?:do\s+not|don't|must\s+not|never|avoid)\s+"
    r"(?:(?:return|output|emit|use|produce)\s+)?(?:any\s+)?json\b"
    r"|\b(?:respond|reply|return|output)\s+without\s+json\b"
    r"|\b(?:output|response|reply|format)\s+"
    r"(?:(?:must|shall|should)\s+be\s+|is\s+)?"
    r"(?:not|non[- ]?)\s*json\b"
    r"|\b(?:use|return|output|emit)\s+(?:a\s+)?non[- ]json\b"
    r"|(?:不要|不得|禁止|避免|不可|不能)"
    r"(?:返回|输出|回复|使用|采用)?(?:任何)?\s*JSON"
    r"|不使用\s*JSON|不采用\s*JSON"
    r"|(?:输出|返回|回复|格式)(?:必须|应当|应该|为|是)?\s*非\s*JSON",
    re.IGNORECASE,
)
_OUTPUT_PLAIN_ONLY = re.compile(
    r"\b(?:only\s+)?(?:reply|respond|return|output)\s+"
    r"(?:only\s+)?(?:(?:with|in)\s+)?(?:a\s+)?"
    r"(?:plain[- ]text|natural[- ]language)"
    r"(?:\s+(?:paragraph|response|reply|text))?(?:\s+only)?\b"
    r"|(?:只|仅|只能)输出[^。\n]{0,12}(?:纯文本|自然语言段落)",
    re.IGNORECASE,
)
_OUTPUT_CONDITIONAL = re.compile(
    r"\b(?:if|when|unless|except|otherwise|fallback)\b"
    r"|如果|若|仅当|除非|例外|除外|否则|不支持时|不可用时|失败时",
    re.IGNORECASE,
)


def _char_span_to_bytes(text: str, start: int, end: int) -> Tuple[int, int]:
    return (len(text[:start].encode("utf-8")),
            len(text[:end].encode("utf-8")))


def _line_char_spans(text: str) -> List[Tuple[int, int, str]]:
    out: List[Tuple[int, int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        visible = line.rstrip("\r\n")
        out.append((offset, offset + len(visible), visible))
        offset += len(line)
    if not out and text:
        out.append((0, len(text), text))
    return out


def _source_evidence_for_char_span(
        ctx: RuleContext, file_obj, data: bytes, text: str,
        span: Tuple[int, int], producer: Producer) -> EvidenceRecord:
    bstart, bend = _char_span_to_bytes(text, span[0], span[1])
    return make_source_span_evidence(
        snapshot_id=ctx.snapshot.snapshotId,
        file_id=file_obj.fileId,
        artifact_path=file_obj.normalizedPath,
        file_digest=file_obj.contentDigest or "",
        byte_range=(bstart, bend),
        raw_bytes=data[bstart:bend],
        producer=producer,
    )


def prompt_output_format_conflict(ctx: RuleContext) -> List[RuleHit]:
    """Prove the narrow JSON-vs-non-JSON top-level conflict.

    The rule intentionally requires two independently classified top-level
    directives. It does not infer a conflict from a natural-language field
    inside JSON, and it skips conditional/fallback format branches.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        text = data.decode("utf-8", "ignore")
        excluded = _excluded_ranges(data)
        positive: List[Tuple[int, int]] = []
        negative: List[Tuple[int, int]] = []
        for start, end, line in _line_char_spans(text):
            if not line.strip() or _OUTPUT_CONDITIONAL.search(line):
                continue
            bstart, _ = _char_span_to_bytes(text, start, end)
            if _in_ranges(bstart, excluded):
                continue
            explicit_negative = bool(_OUTPUT_JSON_NEGATION.search(line))
            plain_only = bool(_OUTPUT_PLAIN_ONLY.search(line))
            if explicit_negative or plain_only:
                negative.append((start, end))
                continue
            if (_OUTPUT_JSON.search(line)
                    and _OUTPUT_TOP_LEVEL.search(line)
                    and _OUTPUT_STRICT.search(line)):
                positive.append((start, end))
        if not positive or not negative:
            continue
        ev_pos = _source_evidence_for_char_span(
            ctx, f, data, text, positive[0], prod)
        ev_neg = _source_evidence_for_char_span(
            ctx, f, data, text, negative[0], prod)
        out.append(RuleHit(evidences=[ev_pos, ev_neg], subject={
            "artifactPath": f.normalizedPath,
            "conflictCategory": "top_level_json_vs_non_json",
        }))
    return out


# --- P21: mechanically impossible explicit output budget ----------------

_OUTPUT_ITEM_COUNT = re.compile(
    r"(?:输出|生成|返回|提供|包含|列出|制作)\s*"
    r"(?:至少|最少|恰好|正好)?\s*(\d{1,4})\s*"
    r"(?:个|条|项|段|组|张|轮|场景|方案|结果|记录)"
    r"|\b(?:return|output|generate|provide|include|list|produce)\s+"
    r"(?:exactly\s+|at\s+least\s+)?(\d{1,4})\s+"
    r"(?:items?|scenes?|entries|records?|results?|options?|sections?|paragraphs?)\b",
    re.IGNORECASE,
)
_OUTPUT_PER_ITEM_MIN = re.compile(
    r"每(?:个|条|项|段|组|张|轮|场景|方案|结果|记录)[^。\n]{0,35}"
    r"(?:至少|不少于|最少)\s*(\d{1,6})\s*(tokens?|字|字符)"
    r"|\beach\s+(?:item|scene|entry|record|result|option|section|paragraph)"
    r"[^.\n]{0,45}\b(?:at\s+least|minimum\s+of)\s+"
    r"(\d{1,6})\s*(tokens?|words?|characters?)\b",
    re.IGNORECASE,
)
_OUTPUT_TOTAL_MAX = re.compile(
    r"(?:总(?:输出|回复|响应|长度)?|整体(?:输出|回复|响应)?|全文|输出总长度)"
    r"[^。\n]{0,25}(?:不超过|不得超过|最多|上限(?:为|是)?|限制(?:为|在)?)"
    r"\s*(\d{1,7})\s*(tokens?|字|字符)"
    r"|\b(?:the\s+)?(?:total|entire|overall)\s+"
    r"(?:output|response|reply)?[^.\n]{0,30}"
    r"(?:must\s+not\s+exceed|at\s+most|maximum(?:\s+of)?|under)"
    r"\s*(\d{1,7})\s*(tokens?|words?|characters?)\b",
    re.IGNORECASE,
)


def _budget_unit(unit: str) -> str:
    value = unit.lower()
    if value.startswith("token"):
        return "token"
    if value.startswith("word"):
        return "word"
    return "character"


def _outside_text_matches(pattern: re.Pattern, text: str,
                          excluded: Sequence[Tuple[int, int]]):
    matches = []
    for match in pattern.finditer(text):
        bstart, _ = _char_span_to_bytes(text, match.start(), match.end())
        if not _in_ranges(bstart, excluded):
            matches.append(match)
    return matches


def prompt_output_budget_conflict(ctx: RuleContext) -> List[RuleHit]:
    """Flag only arithmetic contradictions with an explicit lower bound.

    No token/character conversion and no guessed average size is used.
    """
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        text = data.decode("utf-8", "ignore")
        excluded = _excluded_ranges(data)
        counts = _outside_text_matches(_OUTPUT_ITEM_COUNT, text, excluded)
        per_items = _outside_text_matches(_OUTPUT_PER_ITEM_MIN, text, excluded)
        totals = _outside_text_matches(_OUTPUT_TOTAL_MAX, text, excluded)
        hit = None
        for count_m in counts:
            count = int(count_m.group(1) or count_m.group(2))
            for per_m in per_items:
                per_value = int(per_m.group(1) or per_m.group(3))
                per_unit = _budget_unit(per_m.group(2) or per_m.group(4))
                for total_m in totals:
                    total = int(total_m.group(1) or total_m.group(3))
                    total_unit = _budget_unit(
                        total_m.group(2) or total_m.group(4))
                    if per_unit == total_unit and count * per_value > total:
                        hit = (count_m, per_m, total_m)
                        break
                if hit:
                    break
            if hit:
                break
        if hit is None:
            continue
        evidences = [
            _source_evidence_for_char_span(
                ctx, f, data, text, (m.start(), m.end()), prod)
            for m in hit
        ]
        out.append(RuleHit(evidences=evidences, subject={
            "artifactPath": f.normalizedPath,
            "budgetCategory": "explicit_minimum_exceeds_total",
        }))
    return out


# --- P22: autonomy + high-impact action without approval ----------------

_AUTONOMY_MANDATE = re.compile(
    r"\b(?:act|work|operate)\s+(?:proactively|autonomously)\b"
    r"|\b(?:proactively|autonomously)\s+(?:act|work|operate|decide|execute)\b"
    r"|主动工作|主动执行|自主执行|自主操作|自行决定|自行处理",
    re.IGNORECASE,
)
_HIGH_IMPACT_ACTION = re.compile(
    r"\b(?:delete|remove)\s+(?:files?|records?|data|accounts?)\b"
    r"|\b(?:publish|deploy)\s+(?:changes?|content|releases?)\b"
    r"|\bsend\s+(?:messages?|emails?|notifications?)\b"
    r"|\b(?:purchase|pay|charge)\b"
    r"|\b(?:modify|write|overwrite)\s+(?:files?|settings?|configuration|records?)\b"
    r"|\b(?:run|execute)\s+(?:commands?|scripts?)\b"
    r"|删除(?:文件|记录|数据|账户)|移除(?:文件|记录|数据)"
    r"|发布(?:变更|内容|版本)|部署(?:变更|版本|服务)"
    r"|发送(?:消息|邮件|通知)|购买|付款|扣款"
    r"|修改(?:文件|设置|配置|记录)|写入(?:文件|设置|配置|记录)"
    r"|执行(?:命令|脚本)",
    re.IGNORECASE,
)
_APPROVAL_BOUNDARY = re.compile(
    r"\b(?:must|required\s+to|shall)\s+(?:first\s+)?"
    r"(?:obtain|request|get)\s+(?:the\s+)?(?:user|human)?\s*"
    r"(?:approval|confirmation|authorization)\b"
    r"|\bbefore\s+[^.\n]{0,45}\b(?:ask|confirm|obtain approval)\b"
    r"|\b(?:ask|confirm\s+with)\s+(?:the\s+)?(?:user|human)"
    r"[^.\n]{0,35}\bbefore\b"
    r"|\b(?:request|obtain|get)\s+(?:the\s+)?(?:user|human)?\s*"
    r"(?:approval|confirmation|authorization)[^.\n]{0,35}\bbefore\b"
    r"|\bonly\s+after\s+(?:the\s+)?(?:user|human)\s+"
    r"(?:approves|confirms|authorizes)\b"
    r"|(?:删除|发送|发布|部署|修改|执行)[^。\n]{0,24}(?:前|之前)"
    r"[^。\n]{0,16}(?:必须|需要|需)(?:获得)?(?:用户|人工)?"
    r"(?:确认|批准|授权)"
    r"|(?:删除|发送|发布|部署|修改|执行)[^。\n]{0,24}(?:前|之前)"
    r"(?:必须|需要|需|先)?(?:询问|征得)(?:用户|人工)"
    r"|(?:必须|需要|需)(?:先)?获得(?:用户|人工)?(?:确认|批准|授权)"
    r"|经(?:用户|人工)(?:确认|批准|授权)后|不得未经(?:用户|人工)?(?:确认|批准|授权)",
    re.IGNORECASE,
)


def prompt_autonomy_without_approval(ctx: RuleContext) -> List[RuleHit]:
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        text = data.decode("utf-8", "ignore")
        excluded = _excluded_ranges(data)
        if _outside_text_matches(_APPROVAL_BOUNDARY, text, excluded):
            continue
        autonomy = _outside_text_matches(_AUTONOMY_MANDATE, text, excluded)
        actions = _outside_text_matches(_HIGH_IMPACT_ACTION, text, excluded)
        if not autonomy or not actions:
            continue
        evidences = [
            _source_evidence_for_char_span(
                ctx, f, data, text, (m.start(), m.end()), prod)
            for m in (autonomy[0], actions[0])
        ]
        out.append(RuleHit(evidences=evidences, subject={
            "artifactPath": f.normalizedPath,
            "autonomyCategory": "high_impact_action_without_approval",
        }))
    return out


# --- P23: failure-prone operation without a declared failure strategy ----

_FAILURE_STRATEGY = re.compile(
    r"\b(?:on\s+failure|if\s+[^.\n]{0,35}\bfails?|error|exception|timeout|"
    r"time\s*out|retry|backoff|fallback|failover|unavailable|empty\s+result|"
    r"no\s+results?|structured\s+error|malformed|invalid\s+(?:response|format)|"
    r"missing\s+(?:data|field|information|input)|permission\s+denied|"
    r"unauthorized)\b"
    r"|失败|错误|异常|超时|重试|回退|降级|不可用|空结果|无结果|未找到|"
    r"结构化错误|兜底|格式无效|格式错误|缺少|缺失|权限不足|无权限|无法访问",
    re.IGNORECASE,
)
_FAILURE_PRONE_OPERATIONS = {
    "external_call": re.compile(
        r"\b(?:call|invoke|query|request|connect\s+to)\s+(?:the\s+)?"
        r"(?:external\s+)?(?:api|endpoint|service|model)\b"
        r"|(?:调用|请求|访问|连接)(?:外部)?(?:\s*API|接口|服务|模型)",
        re.IGNORECASE,
    ),
    "retrieval": re.compile(
        r"\b(?:use|call)\s+(?:the\s+)?(?:search|retrieval|browser)\s+"
        r"(?:tool|service)\b"
        r"|\b(?:search|retrieve|fetch)\s+(?:documents?|records?|the\s+web|"
        r"knowledge\s+base)\b"
        r"|(?:使用|调用)[^。\n]{0,8}(?:搜索|检索|浏览器)[^。\n]{0,6}(?:工具|服务)"
        r"|(?:搜索|检索|抓取)(?:相关)?(?:文档|内容|网页|记录|知识库)",
        re.IGNORECASE,
    ),
    "parsing": re.compile(
        r"\b(?:parse|deserialize|decode)\s+(?:the\s+)?"
        r"(?:response|file|document|json|yaml|xml|attachment)\b"
        r"|(?:解析|反序列化|解码)(?:响应|文件|文档|JSON|YAML|XML|附件)",
        re.IGNORECASE,
    ),
}


def prompt_failure_strategy_missing(ctx: RuleContext) -> List[RuleHit]:
    out: List[RuleHit] = []
    prod = Producer(componentId=ctx.rule.ruleId,
                    componentVersion=ctx.rule.ruleVersion,
                    executionId=ctx.execution_id)
    for f in ctx.snapshot.files:
        if f.status != "included":
            continue
        data = ctx.file_bytes.get(f.fileId, b"")
        text = data.decode("utf-8", "ignore")
        excluded = _excluded_ranges(data)
        if _outside_text_matches(_FAILURE_STRATEGY, text, excluded):
            continue
        for category, pattern in _FAILURE_PRONE_OPERATIONS.items():
            matches = _outside_text_matches(pattern, text, excluded)
            if not matches:
                continue
            match = matches[0]
            ev = _source_evidence_for_char_span(
                ctx, f, data, text, (match.start(), match.end()), prod)
            out.append(RuleHit(evidences=[ev], subject={
                "artifactPath": f.normalizedPath,
                "operationCategory": category,
            }))
    return out


# Skill rules live in a separate module so this file stays focused on the
# engine mechanics and legacy examples.
from . import skill_rules as _sr  # noqa: E402
from .bandit_adapter import bandit_result_to_hits as _bandit_hits  # noqa: E402
from .gitleaks_adapter import gitleaks_result_to_hits as _gitleaks_hits  # noqa: E402


def _make_bandit_impl(_):  # closure per rule id, so ctx.rule.ruleId works
    def _impl(ctx):
        return _bandit_hits(ctx, run_result=None)
    return _impl


_BANDIT_TEST_IDS = (
    "B102", "B301", "B324", "B310", "B506", "B602", "B605", "B607",
    "B701", "B105", "B106", "B107", "B501", "B608", "B314",
)

DEFAULT_IMPLEMENTATIONS: Dict[str, RuleImpl] = {
    "impl.prompt.jailbreak_marker.v1": prompt_jailbreak_marker,
    "impl.prompt.unfilled_placeholder.v1": prompt_unfilled_placeholder,
    "impl.prompt.system_hardcoded_secret.v1": prompt_system_hardcoded_secret,
    "impl.prompt.duplicate_numeric_assignment.v1": prompt_duplicate_numeric_assignment,
    "impl.prompt.control_character.v1": prompt_control_character,
    "impl.prompt.empty_or_whitespace.v1": prompt_empty_or_whitespace,
    "impl.prompt.open_ended_tool_wildcard.v1": prompt_open_ended_tool_wildcard,
    "impl.prompt.untrusted_input_boundary_undeclared.v1": prompt_untrusted_input_boundary_undeclared,
    "impl.prompt.dangling_section_reference.v1": prompt_dangling_section_reference,
    "impl.prompt.embedded_system_role_marker.v1": prompt_embedded_system_role_marker,
    "impl.prompt.markdown_data_exfiltration.v1": prompt_markdown_data_exfiltration,
    "impl.prompt.encoded_injection_payload.v1": prompt_encoded_injection_payload,
    "impl.prompt.named_dangling_reference.v1": prompt_named_dangling_reference,
    "impl.prompt.duplicate_content_line.v1": prompt_duplicate_content_line,
    "impl.prompt.fullwidth_mixed.v1": prompt_fullwidth_mixed,
    "impl.prompt.structured_quote_inconsistency.v1":
        prompt_structured_quote_inconsistency,
    "impl.prompt.topic_splice.v1": prompt_topic_splice,
    "impl.prompt.version_naming_inconsistent.v1": prompt_version_naming_inconsistent,
    "impl.prompt.model_endpoint_no_fallback.v1": prompt_model_endpoint_no_fallback,
    "impl.prompt.output_format_conflict.v1": prompt_output_format_conflict,
    "impl.prompt.output_budget_conflict.v1": prompt_output_budget_conflict,
    "impl.prompt.autonomy_without_approval.v1": prompt_autonomy_without_approval,
    "impl.prompt.failure_strategy_missing.v1": prompt_failure_strategy_missing,
    "impl.skill.fake_secret.v1": skill_secret_like_fixture,
    "impl.skill.dangerous_shell.v1": skill_dangerous_shell,
    "impl.skill.sensitive_path_access.v1": skill_sensitive_path_access,
    "impl.skill.missing_skill_md.v1": _sr.skill_missing_skill_md,
    "impl.skill.manifest_parse_failure.v1": _sr.skill_manifest_invalid,
    "impl.skill.manifest_name_issue.v2": _sr.skill_manifest_name_issue,
    "impl.skill.manifest_description_missing.v2": _sr.skill_manifest_description_missing,
    "impl.skill.manifest_optional_field_issue.v1": _sr.skill_manifest_optional_field_issue,
    "impl.skill.manifest_missing_reference.v1": _sr.skill_manifest_missing_reference,
    "impl.skill.manifest_unsafe_reference_path.v1": _sr.skill_manifest_unsafe_reference_path,
    "impl.skill.manifest_unpinned_dependency.v1": _sr.skill_manifest_unpinned_dependency,
    "impl.skill.manifest_permission_wildcard.v1": _sr.skill_manifest_permission_wildcard,
    "impl.skill.manifest_external_instructions.v1": _sr.skill_manifest_external_instructions,
    "impl.skill.manifest_script_suffix_mismatch.v1": _sr.skill_manifest_script_suffix_mismatch,
    "impl.skill.python_subprocess_shell_true.v1": _sr.skill_python_subprocess_shell_true,
    **{f"impl.skill.bandit.{tid}": _make_bandit_impl(tid) for tid in _BANDIT_TEST_IDS},
    "impl.skill.gitleaks.v1": _gitleaks_hits,
}
