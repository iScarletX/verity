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
_JAILBREAK_TERMS = re.compile(
    rb"(?i)("
    # verb + (optional qualifier) + object  -- the core bypass phrase
    rb"(?:ignore|disregard|skip|forget|neglect|overlook|omit|bypass|"
    rb"pay no attention to|do not follow|do not obey)\s+"
    rb"(?:(?:all|any|the)\s+)?"
    rb"(?:prior|previous|preceding|above|foregoing|earlier|initial)?\s*"
    rb"(?:content|text|instructions?|directives?|commands?|context|"
    rb"conversation|inputs?|data|messages?|responses?|requests?|rules?|"
    rb"prompts?|guardrails?|guidelines?)"
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

# Phrases that indicate the prompt explicitly accepts external/user-
# supplied content (creative intent, scripts, retrieved data, tool output,
# attachments, etc). Deliberately narrow and literal, English + Chinese.
_INPUT_ACCEPTANCE_MARKERS = (
    rb"user input", rb"user-supplied", rb"user provided", rb"external content",
    rb"retrieved content", rb"tool output", rb"uploaded file", rb"attachment",
    rb"attached document", rb"customer's message", rb"customer message",
    rb"from the customer", rb"from the user", rb"messages from the user",
    rb"messages from the customer", rb"documents from the user",
    "用户输入".encode("utf-8"), "用户提供".encode("utf-8"),
    "用户上传".encode("utf-8"), "附件".encode("utf-8"),
    "参考文件".encode("utf-8"), "用户提交".encode("utf-8"),
    "用户提供的".encode("utf-8"), "参考输入".encode("utf-8"),
)

# Phrases that indicate the prompt already declares a trust boundary /
# anti-injection posture. If ANY of these are present, the rule does not
# fire — the mitigation is considered declared. Deliberately literal, not
# a semantic judgement of quality.
_TRUST_BOUNDARY_MARKERS = (
    rb"treat.{0,80}as data", rb"not as instructions", rb"never follow",
    rb"ignore.{0,80}instructions", rb"do not follow.{0,40}embedded",
    rb"untrusted", rb"prompt injection", rb"injection attack",
    rb"as data.{0,20}not as instructions", rb"never as instructions",
    "视为数据".encode("utf-8"), "不视为指令".encode("utf-8"),
    "越权指令".encode("utf-8"), "忽略.{0,40}指令".encode("utf-8"),
    "注入".encode("utf-8"), "不可信".encode("utf-8"),
    "拒绝执行".encode("utf-8"), "角色切换指令".encode("utf-8"),
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
    - Fenced/inline code excluded when scanning for BOTH marker sets, so
      an example embedded in a code block cannot itself satisfy or violate
      the check.
    - Literal phrase matching only; this cannot judge whether a present
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
        lower = data.lower()

        def _present_outside_code(markers) -> bool:
            for m in markers:
                if isinstance(m, bytes):
                    for match in re.finditer(re.escape(m), lower):
                        if not _in_ranges(match.start(), excluded):
                            return True
                else:
                    for match in m.finditer(lower):
                        if not _in_ranges(match.start(), excluded):
                            return True
            return False

        accepts_input = _present_outside_code(_INPUT_ACCEPTANCE_MARKERS)
        if not accepts_input:
            continue
        has_boundary = False
        for pat in _TRUST_BOUNDARY_RE:
            for match in pat.finditer(lower):
                if not _in_ranges(match.start(), excluded):
                    has_boundary = True
                    break
            if has_boundary:
                break
        if has_boundary:
            continue
        # Anchor the Finding at the first input-acceptance occurrence so
        # there is a concrete, navigable evidence location.
        anchor = None
        for m in _INPUT_ACCEPTANCE_MARKERS:
            idx = lower.find(m if isinstance(m, bytes) else m)
            if idx != -1 and not _in_ranges(idx, excluded):
                if anchor is None or idx < anchor[0]:
                    anchor = (idx, idx + len(m))
        if anchor is None:
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
