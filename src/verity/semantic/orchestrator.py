"""Semantic orchestration.

Public entry point: ``SemanticOrchestrator.run(review_dict, file_bytes,
config, generator, validator)``.

Responsibilities:

- Run the deterministic Evidence extractors for each enabled semantic
  finding type.
- Call the Candidate Generator with a whitelisted evidence bundle
  (§B: generator cannot invent evidence or set severity).
- Validate every candidate output against the strict JSON Schema and a
  post-schema containment layer (allowed evidenceIds, no unknown
  findingTypes, no severity fields).
- Re-derive an authoritative candidateId from Verity's own canonical
  fingerprint; providers cannot pin identity.
- Call the Validator PER CANDIDATE with a strict single-candidate
  request (§C). The reply must reference the SAME candidateId; any
  drift kicks the assessment into ``validation_failed``.
- Only ``confirmed`` yields a semantic Finding with the POLICY severity
  from the semantic catalog. The Validator cannot override severity.
- Track a payload-audit trail; deterministic findings are passed
  through unchanged.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError

from ..canonical import canonical_json, domain_tag, sha256_hex, subject_key
from .catalog import (CATALOG, SemanticFindingType,
                      extract_prompt_catalog_sweep)
from .config import SemanticConfig, SemanticBudget
from .egress import (PayloadAudit, audit_call, build_catalog_sweep_request,
                     build_generator_request, build_validator_request)
from .provider import (CandidateGeneratorProvider, ProviderCall,
                       ValidatorProvider)
from .schemas import CANDIDATE_LIST_SCHEMA, VALIDATION_RESULT_SCHEMA


# --------------------------------------------------------------------- #
# Result types                                                          #
# --------------------------------------------------------------------- #

@dataclass
class SemanticCandidateRecord:
    """Authoritative candidate representation (post-schema, post-checks)."""
    candidateId: str
    findingType: str
    subject: Dict[str, Any]
    claim: str
    evidenceIds: List[str]
    generatorConfidence: Optional[float]


@dataclass
class SemanticVoteRecord:
    """One independent Validator vote (one Provider, one call + its own
    bounded schema/identity repair retry). Multiple votes are aggregated
    by majority into the candidate's final SemanticAssessmentRecord."""
    state: str            # confirmed | rejected | insufficient_evidence | validation_failed
    reasonCodes: List[str]
    rationale: Optional[str] = None
    validationCallId: Optional[str] = None


@dataclass
class SemanticAssessmentRecord:
    candidateId: str
    state: str            # confirmed | rejected | insufficient_evidence | validation_failed | pending
    reasonCodes: List[str]
    validationCallId: Optional[str] = None
    # Optional short human-readable explanation from the Validator, capped at
    # schema length and never used for policy (decision/reasonCodes still
    # control state). Lets a human audit *why* a candidate was rejected
    # without rerunning the model. Absent for non-model states (pending,
    # validation_failed) since there is no Provider text to carry.
    rationale: Optional[str] = None
    # Every independent vote that contributed to this assessment (length 1
    # under today's default single-Validator configuration). A vote whose
    # own call failed does not count toward the majority, mirroring the
    # "provider errors don't vote" rule from the label-review consensus
    # protocol (semantic_benchmark.py::_independent_review_consensus).
    votes: List[SemanticVoteRecord] = field(default_factory=list)


@dataclass
class SemanticFindingProjection:
    """Projection ready to merge into report_dict['findings']."""
    findingId: str
    findingType: str
    subject: Dict[str, Any]
    subjectKey: str
    severity: str
    claim: str
    evidenceIds: List[str]
    origin: Dict[str, Any]
    findingOccurrenceFingerprint: str
    tags: List[str]
    controls: List[str]


@dataclass
class SemanticPlanItem:
    planItemId: str
    componentKind: str
    componentId: str
    status: str
    reasonCode: Optional[str] = None


@dataclass
class SemanticRunResult:
    status: str                              # off | completed | failed | budget_exhausted | provider_not_configured
    reasonCode: Optional[str] = None
    candidates: List[SemanticCandidateRecord] = field(default_factory=list)
    assessments: List[SemanticAssessmentRecord] = field(default_factory=list)
    findings: List[SemanticFindingProjection] = field(default_factory=list)
    planItems: List[SemanticPlanItem] = field(default_factory=list)
    evidences: List[Dict[str, Any]] = field(default_factory=list)
    payloadAudit: List[PayloadAudit] = field(default_factory=list)
    callCounts: Dict[str, int] = field(default_factory=dict)
    stageStats: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    egressPolicy: str = "off"


# --------------------------------------------------------------------- #
# Validators                                                            #
# --------------------------------------------------------------------- #

_CANDIDATE_LIST_VALIDATOR = Draft202012Validator(CANDIDATE_LIST_SCHEMA)
_VALIDATION_RESULT_VALIDATOR = Draft202012Validator(VALIDATION_RESULT_SCHEMA)


def _validate_subject(finding_type: SemanticFindingType,
                      subject: Any) -> Optional[str]:
    """Enforce the semantic subject taxonomy. Return reason code if bad."""
    if not isinstance(subject, dict):
        return "subject_not_object"
    declared = {f.fieldName for f in finding_type.subjectFields}
    for k in subject.keys():
        if k not in declared:
            return f"subject_extra_field:{k}"
    for f in finding_type.subjectFields:
        if f.fieldName in finding_type.subjectKeyFields and f.fieldName not in subject:
            return f"subject_key_field_missing:{f.fieldName}"
        v = subject.get(f.fieldName)
        if v is None:
            continue
        if f.valueKind == "enum":
            if f.enum is None or v not in f.enum:
                return f"subject_enum_violation:{f.fieldName}"
        elif f.valueKind == "string":
            if not isinstance(v, str) or len(v) > 200:
                return f"subject_bad_string:{f.fieldName}"
    return None


# --------------------------------------------------------------------- #
# Vote aggregation                                                      #
# --------------------------------------------------------------------- #

_VOTABLE_STATES = ("confirmed", "rejected", "insufficient_evidence")


def _aggregate_votes(
    votes: List["SemanticVoteRecord"],
) -> Tuple[str, List[str], Optional[str]]:
    """Three-state majority over independent Validator votes.

    A vote whose own call failed (``validation_failed``) never counts
    toward the majority -- mirrors the "provider errors don't vote" rule
    from the label-review consensus protocol
    (``semantic_benchmark.py::_independent_review_consensus``). If every
    vote failed, the aggregate is ``validation_failed``. If the decisive
    votes are tied (no state has a strict majority of decisive votes --
    e.g. 1-1 with two voters, or 1-1-1 with three), the aggregate is
    ``insufficient_evidence`` with a synthesized ``vote_split`` reason
    code: the disagreement itself is a signal that the evidence in front
    of the models does not settle the question, distinct from every model
    agreeing evidence is insufficient. Never invents ``confirmed`` from a
    non-majority; it can only be reached by an actual majority of votes.
    """
    decisive = [v for v in votes if v.state in _VOTABLE_STATES]
    if not decisive:
        failed_reasons = [
            code for v in votes for code in v.reasonCodes
        ] or ["validator_error"]
        return "validation_failed", failed_reasons, None

    counts: Dict[str, int] = {}
    for v in decisive:
        counts[v.state] = counts.get(v.state, 0) + 1
    winner, top_count = max(counts.items(), key=lambda item: item[1])
    runner_up = max(
        (c for s, c in counts.items() if s != winner), default=0)
    if top_count <= runner_up:
        # Tie among decisive votes: no state has a strict majority.
        return "insufficient_evidence", ["vote_split"], None

    winning_votes = [v for v in decisive if v.state == winner]
    reasons: List[str] = []
    for v in winning_votes:
        for code in v.reasonCodes:
            if code not in reasons:
                reasons.append(code)
    rationale = next(
        (v.rationale for v in winning_votes if v.rationale), None)
    return winner, reasons, rationale


# --------------------------------------------------------------------- #
# Orchestrator                                                          #
# --------------------------------------------------------------------- #

class SemanticOrchestrator:

    def __init__(self, config: SemanticConfig) -> None:
        self.config = config

    def _plan_item(self, kind: str, cid: str, status: str,
                   reason: Optional[str] = None) -> SemanticPlanItem:
        return SemanticPlanItem(
            planItemId=f"pi-semantic-{cid}",
            componentKind=kind, componentId=cid,
            status=status, reasonCode=reason,
        )

    @staticmethod
    def _set_provider_attempt_limit(
            provider: Any, *, call_id: str, remaining_calls: int) -> None:
        setter = getattr(provider, "set_call_attempt_limit", None)
        if callable(setter):
            setter(call_id=call_id, max_attempts=remaining_calls)

    @staticmethod
    def _record_provider_call(
            result: SemanticRunResult, *,
            call_id: str,
            call_role: str,
            egress_policy: str,
            request_obj: Dict[str, Any],
            response: Any,
            exc_reason: Optional[str],
            ) -> Tuple[int, str]:
        """Record real adapter attempts, falling back to one logical call."""
        attempts = tuple(getattr(response, "attempts", ()) or ())
        count_key = (
            "generator"
            if call_role == "candidate_generator"
            else "validator"
        )
        if attempts:
            effective_call_id = call_id
            for attempt in attempts:
                effective_call_id = attempt.call_id
                result.payloadAudit.append(audit_call(
                    call_id=attempt.call_id,
                    call_role=call_role,
                    egress_policy=egress_policy,
                    request_obj=request_obj,
                    response_bytes=attempt.response_bytes,
                    response_ok=attempt.response_ok,
                    reason_code=attempt.reason_code,
                ))
            result.callCounts[count_key] += len(attempts)
            return len(attempts), effective_call_id

        result.callCounts[count_key] += 1
        result.payloadAudit.append(audit_call(
            call_id=call_id,
            call_role=call_role,
            egress_policy=egress_policy,
            request_obj=request_obj,
            response_bytes=(response.response_bytes if response else 0),
            response_ok=bool(response and response.ok),
            reason_code=(response.reason_code if response else exc_reason),
        ))
        return 1, call_id

    # -----------------------------------------------------------------
    # Public entry
    # -----------------------------------------------------------------

    def run(self, review_dict: Dict[str, Any],
            file_bytes: Dict[str, bytes], *,
            generator: Optional[CandidateGeneratorProvider] = None,
            validator: Optional[ValidatorProvider] = None,
            validators: Optional[List[ValidatorProvider]] = None,
            ) -> SemanticRunResult:
        """``validators``, if given (non-empty), is a list of independently
        configured Validator Provider objects (e.g. 2-3 different models).
        Every candidate is judged by ALL of them and the outcome is decided
        by three-state majority (see ``_aggregate_votes``); a call failure
        on one provider does not count toward the majority (mirrors the
        "provider errors don't vote" rule from the label-review consensus
        protocol). ``validator`` (singular) remains supported for backward
        compatibility and is equivalent to ``validators=[validator]``.
        """
        cfg = self.config
        review_id = review_dict.get("reviewId") or "review"
        engine = review_dict.get("engine") or ""
        validator_pool: List[ValidatorProvider] = (
            list(validators) if validators else
            ([validator] if validator is not None else []))

        if not cfg.enabled:
            return SemanticRunResult(
                status="off",
                reasonCode="not_requested_by_profile",
                planItems=[self._plan_item("semantic", "orchestrator",
                                            "not_applicable",
                                            "semantic_disabled")],
                egressPolicy="off",
            )

        # Provider missing -> explicit not-configured, semantic axis failed.
        if generator is None or not validator_pool:
            return SemanticRunResult(
                status="provider_not_configured",
                reasonCode="provider_missing",
                planItems=[self._plan_item("semantic", "orchestrator",
                                            "failed",
                                            "provider_missing")],
                egressPolicy=cfg.egress_policy,
            )

        applicable = self._applicable_finding_types(engine)
        if not applicable:
            return SemanticRunResult(
                status="completed",
                reasonCode="no_applicable_semantic_types",
                planItems=[self._plan_item("semantic", "orchestrator",
                                            "not_applicable",
                                            "no_applicable_types")],
                egressPolicy=cfg.egress_policy,
            )

        # Execute extractors deterministically. Evidence records are
        # produced ONLY here; providers cannot inject new evidence.
        result = SemanticRunResult(status="completed",
                                    egressPolicy=cfg.egress_policy,
                                    callCounts={"generator": 0, "validator": 0})

        candidates_total: List[Tuple[SemanticCandidateRecord,
                                     SemanticFindingType,
                                     Dict[str, Dict[str, Any]]]] = []
        evidence_pool: Dict[str, Dict[str, Any]] = {}
        sweep_eligible: List[SemanticFindingType] = []

        for ft, extractor in applicable:
            result.planItems.append(self._plan_item(
                "extractor", f"extractor.{ft.findingType}", "completed"))
            seeds = extractor(review_dict, file_bytes)
            stats = {
                "extractorSeedCount": len(seeds),
                "evidenceCount": 0,
                "catalogHintProposedCount": 0,
                "catalogHintAcceptedCount": 0,
                "generatorRawCandidateCount": 0,
                "generatorAcceptedCandidateCount": 0,
                "queuedCandidateCount": 0,
                "validatorStates": {
                    "confirmed": 0,
                    "rejected": 0,
                    "insufficient_evidence": 0,
                    "validation_failed": 0,
                    "pending": 0,
                },
            }
            result.stageStats[ft.findingType] = stats
            if not seeds:
                if (cfg.candidate_strategy == "catalog_first"
                        and engine == "prompt"):
                    sweep_eligible.append(ft)
                continue
            # Merge extractor evidence into the pool (identity-stable ids).
            allowed_ids: List[str] = []
            deterministic_hint_payloads: List[Dict[str, Any]] = []
            model_candidate_policies: List[str] = []
            for (_hint, ev_ids, ev_records) in seeds:
                if isinstance(_hint, dict):
                    policy = _hint.get("modelCandidatePolicy")
                    if isinstance(policy, str):
                        model_candidate_policies.append(policy)
                for ev in ev_records:
                    evidence_pool.setdefault(ev["evidenceId"], ev)
                for eid in ev_ids:
                    if eid not in allowed_ids:
                        allowed_ids.append(eid)
                hints = (
                    _hint.get("candidateHints")
                    if isinstance(_hint, dict) else None)
                if isinstance(hints, list):
                    for index, hint in enumerate(hints):
                        if not isinstance(hint, dict):
                            continue
                        deterministic_hint_payloads.append({
                            "proposedCandidateId": (
                                f"catalog-hint-{index + 1}"),
                            "findingType": ft.findingType,
                            "subject": hint.get("subject"),
                            "claim": hint.get("claim"),
                            "evidenceIds": list(ev_ids),
                        })
            allowed_evidences = [evidence_pool[e] for e in allowed_ids
                                 if e in evidence_pool]
            stats["evidenceCount"] = len(allowed_evidences)
            stats["catalogHintProposedCount"] = len(
                deterministic_hint_payloads)
            skip_model_candidates = (
                cfg.candidate_strategy == "catalog_first"
                and not deterministic_hint_payloads
                and len(model_candidate_policies) == len(seeds)
                and all(policy == "skip_without_catalog_hint"
                        for policy in model_candidate_policies))
            if skip_model_candidates:
                result.planItems.append(self._plan_item(
                    "candidate_generator", ft.findingType,
                    "completed", "model_candidate_gate_skipped"))
                continue
            if (cfg.candidate_strategy == "catalog_first"
                    and deterministic_hint_payloads):
                # Catalog-owned deterministic hypotheses are already bounded
                # to extractor Evidence. Skip model candidate generation and
                # send only these hypotheses to the independent Validator.
                hint_candidates = self._parse_and_check_candidates(
                    ft, {"candidates": deterministic_hint_payloads},
                    allowed_evidence_ids=set(allowed_ids),
                    allowed_evidences=allowed_evidences,
                    review_snapshot_id=(
                        (review_dict.get("snapshot") or {}).get(
                            "snapshotId", "")),
                )
                if hint_candidates is None:
                    result.planItems.append(self._plan_item(
                        "candidate_generator", ft.findingType,
                        "failed", "catalog_candidate_hint_invalid"))
                    result.status = "failed"
                    if result.reasonCode is None:
                        result.reasonCode = "catalog_candidate_hint_invalid"
                    continue
                stats["catalogHintAcceptedCount"] = len(hint_candidates)
                combined = []
                seen_candidate_ids = set()
                for candidate in hint_candidates:
                    if candidate.candidateId in seen_candidate_ids:
                        continue
                    seen_candidate_ids.add(candidate.candidateId)
                    combined.append(candidate)
                    if len(combined) >= cfg.budget.max_candidates_per_extractor:
                        break
                stats["queuedCandidateCount"] = min(
                    len(combined),
                    max(0, cfg.budget.max_candidates_total
                        - len(candidates_total)),
                )
                for c in combined:
                    if len(candidates_total) >= cfg.budget.max_candidates_total:
                        break
                    candidates_total.append(
                        (c, ft, {e["evidenceId"]: e
                                 for e in allowed_evidences}))
                result.planItems.append(self._plan_item(
                    "candidate_generator", ft.findingType,
                    "completed", "catalog_hint_candidates"))
                continue

            # Call generator.
            if result.callCounts["generator"] >= cfg.budget.max_candidate_generation_calls:
                result.planItems.append(self._plan_item(
                    "candidate_generator", ft.findingType,
                    "failed", "budget_generation_exhausted"))
                result.status = "budget_exhausted"
                continue

            call_id = f"cg-{uuid.uuid4().hex[:12]}"
            req = build_generator_request(
                review_id=review_id, engine=engine,
                finding_type=ft.findingType,
                evidences=allowed_evidences,
                file_bytes=file_bytes,
                egress_policy=cfg.egress_policy,
                subject_taxonomy={
                    "fields": [{"fieldName": f.fieldName,
                                 "valueKind": f.valueKind,
                                 "enum": f.enum or []}
                                for f in ft.subjectFields],
                },
                max_evidence=cfg.budget.max_evidence_per_candidate,
                prompt_kind=(review_dict.get("snapshot") or {}).get("promptKind"),
                judgment_policy={
                    "appliesWhen": ft.judgmentPolicy.appliesWhen,
                    "confirmWhen": ft.judgmentPolicy.confirmWhen,
                    "rejectWhen": ft.judgmentPolicy.rejectWhen,
                    "insufficientWhen": ft.judgmentPolicy.insufficientWhen,
                },
            )
            body_bytes = len(json.dumps(req).encode())
            provider_call = ProviderCall(
                review_id=review_id,
                egress_policy=cfg.egress_policy,
                call_role="candidate_generator", call_id=call_id,
                request_bytes=body_bytes,
                request_digest_sha256=hashlib.sha256(
                    json.dumps(req, sort_keys=True).encode()
                ).hexdigest(),
            )
            self._set_provider_attempt_limit(
                generator,
                call_id=call_id,
                remaining_calls=(
                    cfg.budget.max_candidate_generation_calls
                    - result.callCounts["generator"]
                ),
            )
            try:
                response = generator.generate_candidates(call=provider_call,
                                                          request=req)
            except Exception as e:  # pragma: no cover
                response = None
                exc_reason = f"provider_raised:{type(e).__name__}"
            else:
                exc_reason = None

            self._record_provider_call(
                result,
                call_id=call_id,
                call_role="candidate_generator",
                egress_policy=cfg.egress_policy,
                request_obj=req,
                response=response,
                exc_reason=exc_reason,
            )

            if response is None or not response.ok:
                result.planItems.append(self._plan_item(
                    "candidate_generator", ft.findingType,
                    "failed",
                    (response.reason_code if response else exc_reason) or "generator_error"))
                result.status = "failed"
                if result.reasonCode is None:
                    result.reasonCode = (
                        (response.reason_code if response else exc_reason)
                        or "generator_error")
                continue

            raw_candidates = (
                response.payload.get("candidates")
                if isinstance(response.payload, dict) else None)
            stats["generatorRawCandidateCount"] = (
                len(raw_candidates) if isinstance(raw_candidates, list) else 0)
            candidates = self._parse_and_check_candidates(
                ft, response.payload,
                allowed_evidence_ids=set(allowed_ids),
                allowed_evidences=allowed_evidences,
                review_snapshot_id=(review_dict.get("snapshot") or {}).get("snapshotId", ""),
            )
            if candidates is None:
                result.planItems.append(self._plan_item(
                    "candidate_generator", ft.findingType,
                    "failed", "generator_output_schema_violation"))
                result.status = "failed"
                if result.reasonCode is None:
                    result.reasonCode = "generator_output_schema_violation"
                continue
            stats["generatorAcceptedCandidateCount"] = len(candidates)
            candidate_source = candidates
            combined = []
            seen_candidate_ids = set()
            for candidate in candidate_source:
                if candidate.candidateId in seen_candidate_ids:
                    continue
                seen_candidate_ids.add(candidate.candidateId)
                combined.append(candidate)
                if len(combined) >= cfg.budget.max_candidates_per_extractor:
                    break
            candidates = combined
            stats["queuedCandidateCount"] = min(
                len(candidates),
                max(0, cfg.budget.max_candidates_total
                    - len(candidates_total)),
            )
            # cap total
            for c in candidates:
                if len(candidates_total) >= cfg.budget.max_candidates_total:
                    break
                candidates_total.append((c, ft, {e["evidenceId"]: e
                                                  for e in allowed_evidences}))
            result.planItems.append(self._plan_item(
                "candidate_generator", ft.findingType, "completed"))

        # Butler-style whole-document recall pass, constrained to Verity's
        # registered catalog. It runs only for prompt types whose
        # deterministic extractor produced no seed. Types that reached an
        # explicit safe gate are deliberately excluded, preserving the
        # catalog-first precision boundary.
        #
        # ONE INDEPENDENT MODEL CALL PER FINDING TYPE (not one call for the
        # whole sweep-eligible set). A single call asked to judge many Finding
        # Types at once was observed to silently skip some of them under real
        # models -- the model's attention is diluted across the packed
        # catalog and it stops proposing candidates for a subset of the
        # requested types with no error signal. Splitting into one call per
        # type removes that dilution at the cost of more calls; the
        # per-review budget (`max_candidate_generation_calls`) is sized
        # to cover the full registered catalog for this reason.
        if sweep_eligible:
            sweep_seeds = extract_prompt_catalog_sweep(
                review_dict, file_bytes)
            if not sweep_seeds:
                for ft in sweep_eligible:
                    result.planItems.append(self._plan_item(
                        "candidate_generator", ft.findingType,
                        "not_applicable", "no_complete_prompt_evidence"))
            else:
                sweep_ids: List[str] = []
                for (_source, ev_ids, ev_records) in sweep_seeds:
                    for ev in ev_records:
                        evidence_pool.setdefault(ev["evidenceId"], ev)
                    for eid in ev_ids:
                        if eid not in sweep_ids:
                            sweep_ids.append(eid)
                sweep_evidences = [
                    evidence_pool[eid] for eid in sweep_ids
                    if eid in evidence_pool
                ]

                for ft in sweep_eligible:
                    stats = result.stageStats[ft.findingType]
                    stats["evidenceCount"] = len(sweep_evidences)

                    if (result.callCounts["generator"]
                            >= cfg.budget.max_candidate_generation_calls):
                        result.planItems.append(self._plan_item(
                            "candidate_generator", ft.findingType,
                            "failed", "budget_generation_exhausted"))
                        result.status = "budget_exhausted"
                        continue

                    finding_catalog = [{
                        "findingType": ft.findingType,
                        "subjectTaxonomy": {
                            "fields": [{
                                "fieldName": field.fieldName,
                                "valueKind": field.valueKind,
                                "enum": field.enum or [],
                            } for field in ft.subjectFields],
                        },
                        "judgmentPolicy": {
                            "appliesWhen": ft.judgmentPolicy.appliesWhen,
                            "confirmWhen": ft.judgmentPolicy.confirmWhen,
                            "rejectWhen": ft.judgmentPolicy.rejectWhen,
                            "insufficientWhen": ft.judgmentPolicy.insufficientWhen,
                        },
                    }]
                    req = build_catalog_sweep_request(
                        review_id=review_id,
                        evidences=sweep_evidences,
                        file_bytes=file_bytes,
                        egress_policy=cfg.egress_policy,
                        finding_catalog=finding_catalog,
                        max_evidence=cfg.budget.max_evidence_per_candidate,
                        prompt_kind=(
                            (review_dict.get("snapshot") or {}).get(
                                "promptKind")),
                    )
                    call_id = f"cg-sweep-{uuid.uuid4().hex[:12]}"
                    provider_call = ProviderCall(
                        review_id=review_id,
                        egress_policy=cfg.egress_policy,
                        call_role="candidate_generator",
                        call_id=call_id,
                        request_bytes=len(json.dumps(req).encode()),
                        request_digest_sha256=hashlib.sha256(
                            json.dumps(req, sort_keys=True).encode()
                        ).hexdigest(),
                    )
                    self._set_provider_attempt_limit(
                        generator,
                        call_id=call_id,
                        remaining_calls=(
                            cfg.budget.max_candidate_generation_calls
                            - result.callCounts["generator"]
                        ),
                    )
                    try:
                        response = generator.generate_candidates(
                            call=provider_call, request=req)
                    except Exception as exc:  # pragma: no cover
                        response = None
                        exc_reason = f"provider_raised:{type(exc).__name__}"
                    else:
                        exc_reason = None
                    self._record_provider_call(
                        result,
                        call_id=call_id,
                        call_role="candidate_generator",
                        egress_policy=cfg.egress_policy,
                        request_obj=req,
                        response=response,
                        exc_reason=exc_reason,
                    )

                    if response is None or not response.ok:
                        reason = (
                            (response.reason_code if response else exc_reason)
                            or "generator_error")
                        result.planItems.append(self._plan_item(
                            "candidate_generator", ft.findingType,
                            "failed", reason))
                        result.status = "failed"
                        if result.reasonCode is None:
                            result.reasonCode = reason
                        continue

                    try:
                        _CANDIDATE_LIST_VALIDATOR.validate(response.payload)
                    except SchemaValidationError:
                        sweep_invalid = True
                    else:
                        sweep_invalid = False

                    parsed_sweep: List[SemanticCandidateRecord] = []
                    if not sweep_invalid:
                        raw_candidates = response.payload["candidates"][
                            :cfg.budget.max_candidates_per_extractor]
                        for raw in raw_candidates:
                            if raw.get("findingType") != ft.findingType:
                                sweep_invalid = True
                                break
                            parsed = self._parse_and_check_candidates(
                                ft,
                                {"candidates": [raw]},
                                allowed_evidence_ids=set(sweep_ids),
                                allowed_evidences=sweep_evidences,
                                review_snapshot_id=(
                                    (review_dict.get("snapshot") or {}).get(
                                        "snapshotId", "")),
                            )
                            if parsed is None or len(parsed) != 1:
                                sweep_invalid = True
                                break
                            parsed_sweep.append(parsed[0])
                        # This sweep proposes at most one candidate per type
                        # by contract; more than one raw candidate for the
                        # single requested type is a catalog violation.
                        if len(parsed_sweep) > 1:
                            sweep_invalid = True

                    if sweep_invalid:
                        result.planItems.append(self._plan_item(
                            "candidate_generator", ft.findingType,
                            "failed", "catalog_sweep_output_violation"))
                        result.status = "failed"
                        if result.reasonCode is None:
                            result.reasonCode = (
                                "catalog_sweep_output_violation")
                        continue

                    stats["generatorRawCandidateCount"] = len(parsed_sweep)
                    if not parsed_sweep:
                        result.planItems.append(self._plan_item(
                            "candidate_generator", ft.findingType,
                            "completed", "catalog_sweep_no_candidate"))
                        continue

                    stats["generatorAcceptedCandidateCount"] = 1
                    candidate = parsed_sweep[0]
                    existing_keys = {
                        (
                            existing_candidate.findingType,
                            subject_key(
                                existing_candidate.findingType,
                                existing_candidate.subject,
                                existing_ft.subjectKeyFields,
                            ),
                        )
                        for existing_candidate, existing_ft, _pool
                        in candidates_total
                    }
                    candidate_key = (
                        candidate.findingType,
                        subject_key(
                            candidate.findingType,
                            candidate.subject,
                            ft.subjectKeyFields,
                        ),
                    )
                    if candidate_key in existing_keys:
                        result.planItems.append(self._plan_item(
                            "candidate_generator", ft.findingType,
                            "completed", "catalog_sweep_duplicate_candidate"))
                        continue
                    if len(candidates_total) >= cfg.budget.max_candidates_total:
                        result.status = "budget_exhausted"
                        result.planItems.append(self._plan_item(
                            "candidate_generator", ft.findingType,
                            "failed", "budget_candidates_total_exhausted"))
                        continue
                    stats["queuedCandidateCount"] = 1
                    candidates_total.append((
                        candidate,
                        ft,
                        {ev["evidenceId"]: ev for ev in sweep_evidences},
                    ))
                    result.planItems.append(self._plan_item(
                        "candidate_generator", ft.findingType,
                        "completed", "catalog_sweep_candidate"))

        result.candidates = [c for (c, _ft, _pool) in candidates_total]
        result.evidences = list(evidence_pool.values())

        # Validate each candidate. Every configured Validator Provider casts
        # one independent vote; votes are aggregated by three-state majority.
        for (cand, ft, ev_pool) in candidates_total:
            if (cfg.budget.max_validation_calls_per_candidate < 1
                    or result.callCounts["validator"]
                    >= cfg.budget.max_total_validation_calls):
                result.assessments.append(SemanticAssessmentRecord(
                    candidateId=cand.candidateId, state="pending",
                    reasonCodes=["budget_validation_exhausted"],
                ))
                result.stageStats[cand.findingType][
                    "validatorStates"]["pending"] += 1
                result.status = "budget_exhausted"
                continue

            votes: List[SemanticVoteRecord] = []
            last_call_id: Optional[str] = None
            for voter in validator_pool:
                if (result.callCounts["validator"]
                        >= cfg.budget.max_total_validation_calls):
                    result.status = "budget_exhausted"
                    break
                vote, vote_call_id = self._cast_validator_vote(
                    result=result, cand=cand, ft=ft, ev_pool=ev_pool,
                    review_id=review_id, file_bytes=file_bytes,
                    cfg=cfg, validator=voter,
                )
                votes.append(vote)
                last_call_id = vote_call_id

            state, reasons, rationale = _aggregate_votes(votes)
            result.assessments.append(SemanticAssessmentRecord(
                candidateId=cand.candidateId,
                state=state, reasonCodes=reasons,
                validationCallId=last_call_id,
                rationale=rationale, votes=votes,
            ))
            result.stageStats[cand.findingType][
                "validatorStates"][state] += 1
            if state == "validation_failed":
                result.status = "failed"
                if result.reasonCode is None:
                    result.reasonCode = reasons[0] if reasons else "validation_failed"

            if state == "confirmed":
                # Build a semantic Finding projection using the POLICY
                # severity from the catalog (Validator has zero say).
                sk = subject_key(cand.findingType, cand.subject,
                                  ft.subjectKeyFields)
                fp = sha256_hex(
                    domain_tag("finding-occurrence"),
                    canonical_json({
                        "candidateId": cand.candidateId,
                        "subjectKey": sk,
                        "origin": "semantic_validation",
                    }),
                )
                result.findings.append(SemanticFindingProjection(
                    findingId=f"F-{fp[:16]}",
                    findingType=cand.findingType,
                    subject=dict(cand.subject),
                    subjectKey=sk,
                    severity=ft.defaultSeverity,
                    claim=cand.claim,
                    evidenceIds=list(cand.evidenceIds),
                    origin={
                        "kind": "semantic_validation",
                        "candidateId": cand.candidateId,
                        "candidateAssessmentId": last_call_id,
                        "validationIds": [
                            v.validationCallId for v in votes
                            if v.validationCallId
                        ],
                    },
                    findingOccurrenceFingerprint=fp,
                    tags=["engine:" + ft.engine, "semantic"],
                    controls=list(ft.owaspAst10),
                ))
        return result

    def _cast_validator_vote(
        self, *, result: SemanticRunResult,
        cand: SemanticCandidateRecord, ft: SemanticFindingType,
        ev_pool: Dict[str, Dict[str, Any]], review_id: str,
        file_bytes: Dict[str, bytes], cfg: SemanticConfig,
        validator: ValidatorProvider,
    ) -> Tuple[SemanticVoteRecord, Optional[str]]:
        """One independent vote: a call, plus up to one bounded
        schema/identity repair retry on the SAME provider (unchanged from
        pre-voting behaviour -- this is a format-repair retry, not a second
        opinion). Returns (vote, last_call_id_used)."""
        validation_calls_for_candidate = 0
        allowed_evidences = [ev_pool[e] for e in cand.evidenceIds
                              if e in ev_pool]
        req = build_validator_request(
            review_id=review_id,
            candidate={
                "candidateId": cand.candidateId,
                "findingType": cand.findingType,
                "subject": cand.subject,
                "claim": cand.claim,
                "evidenceIds": cand.evidenceIds,
            },
            evidences=allowed_evidences,
            file_bytes=file_bytes,
            egress_policy=cfg.egress_policy,
            falsification_question=ft.falsificationQuestion,
            judgment_policy={
                "appliesWhen": ft.judgmentPolicy.appliesWhen,
                "confirmWhen": ft.judgmentPolicy.confirmWhen,
                "rejectWhen": ft.judgmentPolicy.rejectWhen,
                "insufficientWhen": ft.judgmentPolicy.insufficientWhen,
            },
        )
        call_id = f"vv-{uuid.uuid4().hex[:12]}"
        provider_call = ProviderCall(
            review_id=review_id,
            egress_policy=cfg.egress_policy,
            call_role="validator", call_id=call_id,
            request_bytes=len(json.dumps(req).encode()),
            request_digest_sha256=hashlib.sha256(
                json.dumps(req, sort_keys=True).encode()).hexdigest(),
        )
        self._set_provider_attempt_limit(
            validator,
            call_id=call_id,
            remaining_calls=min(
                cfg.budget.max_validation_calls_per_candidate,
                cfg.budget.max_total_validation_calls
                - result.callCounts["validator"],
            ),
        )
        try:
            response = validator.validate_candidate(call=provider_call,
                                                     request=req)
            exc_reason = None
        except Exception as e:  # pragma: no cover
            response = None
            exc_reason = f"provider_raised:{type(e).__name__}"

        call_count, call_id = self._record_provider_call(
            result,
            call_id=call_id,
            call_role="validator",
            egress_policy=cfg.egress_policy,
            request_obj=req,
            response=response,
            exc_reason=exc_reason,
        )
        validation_calls_for_candidate += call_count

        if response is None or not response.ok:
            reason = ((response.reason_code if response else exc_reason)
                      or "validator_error")
            # A real resource-exhaustion signal from the bound Provider is a
            # global event (further calls will likely also fail) and is
            # surfaced immediately. A single vote's transient failure
            # (timeout, http_error, malformed output) is NOT: whether the
            # overall candidate assessment/run status reflects failure is
            # decided once, after aggregation, by the caller in `run()` --
            # a lone bad vote must not poison a result the majority still
            # confirms.
            if reason == "run_budget_exhausted":
                result.status = "budget_exhausted"
                if result.reasonCode is None:
                    result.reasonCode = reason
            return (SemanticVoteRecord(
                state="validation_failed", reasonCodes=[reason],
                validationCallId=call_id), call_id)

        state, reasons, rationale = self._parse_and_check_validation(
            cand=cand, ft=ft, payload=response.payload,
        )
        if (state == "validation_failed"
                and reasons
                and reasons[0] in {"schema_violation",
                                   "candidateId_mismatch"}
                and validation_calls_for_candidate
                < cfg.budget.max_validation_calls_per_candidate
                and result.callCounts["validator"]
                < cfg.budget.max_total_validation_calls):
            retry_call_id = f"vv-{uuid.uuid4().hex[:12]}"
            retry_provider_call = ProviderCall(
                review_id=review_id,
                egress_policy=cfg.egress_policy,
                call_role="validator", call_id=retry_call_id,
                request_bytes=len(json.dumps(req).encode()),
                request_digest_sha256=hashlib.sha256(
                    json.dumps(req, sort_keys=True).encode()).hexdigest(),
            )
            self._set_provider_attempt_limit(
                validator,
                call_id=retry_call_id,
                remaining_calls=min(
                    (
                        cfg.budget.max_validation_calls_per_candidate
                        - validation_calls_for_candidate
                    ),
                    (
                        cfg.budget.max_total_validation_calls
                        - result.callCounts["validator"]
                    ),
                ),
            )
            try:
                retry_response = validator.validate_candidate(
                    call=retry_provider_call, request=req)
                retry_exc_reason = None
            except Exception as e:  # pragma: no cover
                retry_response = None
                retry_exc_reason = f"provider_raised:{type(e).__name__}"

            _retry_call_count, effective_retry_call_id = (
                self._record_provider_call(
                    result,
                    call_id=retry_call_id,
                    call_role="validator",
                    egress_policy=cfg.egress_policy,
                    request_obj=req,
                    response=retry_response,
                    exc_reason=retry_exc_reason,
                )
            )
            if retry_response is None or not retry_response.ok:
                state = "validation_failed"
                reasons = [(
                    retry_response.reason_code
                    if retry_response else retry_exc_reason)
                    or "validator_error"]
                rationale = None
                call_id = effective_retry_call_id
            else:
                retry_state, retry_reasons, retry_rationale = (
                    self._parse_and_check_validation(
                        cand=cand, ft=ft, payload=retry_response.payload))
                state = retry_state
                reasons = retry_reasons
                rationale = retry_rationale
                call_id = effective_retry_call_id

        # Note: unlike the pre-voting single-Validator design, a vote that
        # ends in validation_failed here does NOT set result.status --
        # aggregation across all votes decides that once, after every vote
        # is in (see `run()`), so one voter's schema/identity failure can't
        # override a majority the other voters still reach.
        return (SemanticVoteRecord(
            state=state, reasonCodes=reasons, rationale=rationale,
            validationCallId=call_id), call_id)

    # -----------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------

    def _applicable_finding_types(self, engine: str
                                  ) -> List[Tuple[SemanticFindingType, Any]]:
        want = self.config.enabled_finding_types or list(CATALOG.keys())
        out: List[Tuple[SemanticFindingType, Any]] = []
        for ft_id, (ft, extractor) in CATALOG.items():
            if ft_id not in want:
                continue
            if ft.engine != engine:
                continue
            out.append((ft, extractor))
        return out

    def _parse_and_check_candidates(
        self,
        ft: SemanticFindingType,
        payload: Any,
        *,
        allowed_evidence_ids: set,
        allowed_evidences: List[Dict[str, Any]],
        review_snapshot_id: str,
    ) -> Optional[List[SemanticCandidateRecord]]:
        # 1. Strict JSON Schema (rejects extra top-level fields, over-
        #    length strings, wrong types, etc.).
        try:
            _CANDIDATE_LIST_VALIDATOR.validate(payload)
        except SchemaValidationError:
            return None
        # 2. Per-candidate containment.
        out: List[SemanticCandidateRecord] = []
        seen_ids: set = set()
        for raw in payload["candidates"][: self.config.budget.max_candidates_per_extractor]:
            # findingType allow-list: must equal this run's ft.
            if raw["findingType"] != ft.findingType:
                continue
            # evidenceIds must all be in allow-list.
            ev_ids = list(raw.get("evidenceIds") or [])
            if not ev_ids:
                continue
            if not all(e in allowed_evidence_ids for e in ev_ids):
                continue
            if len(ev_ids) > self.config.budget.max_evidence_per_candidate:
                continue
            # subject taxonomy.
            if _validate_subject(ft, raw.get("subject") or {}):
                continue
            # Verity re-derives candidateId from subject + evidence
            # occurrences, so a malicious provider cannot pin identity.
            payload_id_input = {
                "findingType": ft.findingType,
                "subject": raw["subject"],
                "evidenceOccurrenceFingerprints": sorted(
                    ev["occurrenceFingerprint"]
                    for ev in allowed_evidences if ev["evidenceId"] in ev_ids
                ),
                "snapshotId": review_snapshot_id,
            }
            derived = "C-" + sha256_hex(
                domain_tag("semantic-candidate"),
                canonical_json(payload_id_input),
            )[:16]
            if derived in seen_ids:
                continue      # exact-occurrence dedup, no fuzzy merge
            seen_ids.add(derived)
            out.append(SemanticCandidateRecord(
                candidateId=derived,
                findingType=ft.findingType,
                subject=dict(raw["subject"]),
                claim=raw.get("claim") or "",
                evidenceIds=list(ev_ids),
                generatorConfidence=(
                    float(raw["confidence"]) if "confidence" in raw else None),
            ))
        return out

    def _parse_and_check_validation(
        self,
        *,
        cand: SemanticCandidateRecord,
        ft: SemanticFindingType,
        payload: Any,
    ) -> Tuple[str, List[str], Optional[str]]:
        # 1. Strict schema (rejects severity/ruleId/findingType additions).
        try:
            _VALIDATION_RESULT_VALIDATOR.validate(payload)
        except SchemaValidationError as e:
            return "validation_failed", ["schema_violation"], None
        # 2. candidateId identity match.
        if payload["candidateId"] != cand.candidateId:
            return "validation_failed", ["candidateId_mismatch"], None
        # 3. Decision -> state (severity is IGNORED entirely; policy value wins).
        # rationale is advisory/audit-only: schema-capped free text, never
        # consulted for decision, identity, severity, or evidence.
        rationale = payload.get("rationale") or None
        decision = payload["decision"]
        if decision == "confirmed":
            return "confirmed", list(payload.get("reasonCodes") or []), rationale
        if decision == "rejected":
            return "rejected", list(payload.get("reasonCodes") or []), rationale
        return ("insufficient_evidence", list(payload.get("reasonCodes") or []),
                rationale)
