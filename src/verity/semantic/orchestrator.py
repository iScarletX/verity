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
class SemanticAssessmentRecord:
    candidateId: str
    state: str            # confirmed | rejected | insufficient_evidence | validation_failed | pending
    reasonCodes: List[str]
    validationCallId: Optional[str] = None


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
            ) -> SemanticRunResult:
        cfg = self.config
        review_id = review_dict.get("reviewId") or "review"
        engine = review_dict.get("engine") or ""

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
        if generator is None or validator is None:
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
        # registered catalog. It runs only for prompt types whose deterministic
        # extractor produced no seed. Types that reached an explicit safe gate
        # are deliberately excluded, preserving the catalog-first precision
        # boundary.
        if sweep_eligible:
            sweep_seeds = extract_prompt_catalog_sweep(
                review_dict, file_bytes)
            if not sweep_seeds:
                result.planItems.append(self._plan_item(
                    "candidate_generator", "semantic.catalog_sweep",
                    "not_applicable", "no_complete_prompt_evidence"))
            elif (result.callCounts["generator"]
                  >= cfg.budget.max_candidate_generation_calls):
                result.planItems.append(self._plan_item(
                    "candidate_generator", "semantic.catalog_sweep",
                    "failed", "budget_generation_exhausted"))
                result.status = "budget_exhausted"
            else:
                sweep_ids: List[str] = []
                sweep_evidences: List[Dict[str, Any]] = []
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
                eligible_by_id = {
                    ft.findingType: ft for ft in sweep_eligible
                }
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
                } for ft in sweep_eligible]
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
                        "candidate_generator", "semantic.catalog_sweep",
                        "failed", reason))
                    result.status = "failed"
                    if result.reasonCode is None:
                        result.reasonCode = reason
                else:
                    try:
                        _CANDIDATE_LIST_VALIDATOR.validate(response.payload)
                    except SchemaValidationError:
                        sweep_invalid = True
                    else:
                        sweep_invalid = False

                    parsed_sweep: List[
                        Tuple[SemanticCandidateRecord,
                              SemanticFindingType]] = []
                    seen_types = set()
                    if not sweep_invalid:
                        raw_candidates = response.payload["candidates"][
                            :cfg.budget.max_candidates_per_extractor]
                        for raw in raw_candidates:
                            finding_type = raw.get("findingType")
                            ft = eligible_by_id.get(finding_type)
                            if ft is None or finding_type in seen_types:
                                sweep_invalid = True
                                break
                            seen_types.add(finding_type)
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
                            parsed_sweep.append((parsed[0], ft))

                    if sweep_invalid:
                        result.planItems.append(self._plan_item(
                            "candidate_generator", "semantic.catalog_sweep",
                            "failed", "catalog_sweep_output_violation"))
                        result.status = "failed"
                        if result.reasonCode is None:
                            result.reasonCode = (
                                "catalog_sweep_output_violation")
                    else:
                        raw_counts: Dict[str, int] = {}
                        for candidate, _ft in parsed_sweep:
                            raw_counts[candidate.findingType] = (
                                raw_counts.get(candidate.findingType, 0) + 1)
                        existing_keys = {
                            (
                                candidate.findingType,
                                subject_key(
                                    candidate.findingType,
                                    candidate.subject,
                                    candidate_ft.subjectKeyFields,
                                ),
                            )
                            for candidate, candidate_ft, _pool
                            in candidates_total
                        }
                        queued_types = set()
                        for candidate, ft in parsed_sweep:
                            stats = result.stageStats[ft.findingType]
                            stats["evidenceCount"] = len(sweep_evidences)
                            stats["generatorRawCandidateCount"] = raw_counts[
                                ft.findingType]
                            stats["generatorAcceptedCandidateCount"] = 1
                            candidate_key = (
                                candidate.findingType,
                                subject_key(
                                    candidate.findingType,
                                    candidate.subject,
                                    ft.subjectKeyFields,
                                ),
                            )
                            if candidate_key in existing_keys:
                                continue
                            if (len(candidates_total)
                                    >= cfg.budget.max_candidates_total):
                                result.status = "budget_exhausted"
                                break
                            existing_keys.add(candidate_key)
                            queued_types.add(ft.findingType)
                            stats["queuedCandidateCount"] = 1
                            candidates_total.append((
                                candidate,
                                ft,
                                {ev["evidenceId"]: ev
                                 for ev in sweep_evidences},
                            ))
                        for ft in sweep_eligible:
                            stats = result.stageStats[ft.findingType]
                            stats["evidenceCount"] = len(sweep_evidences)
                            result.planItems.append(self._plan_item(
                                "candidate_generator",
                                ft.findingType,
                                "completed",
                                (
                                    "catalog_sweep_candidate"
                                    if ft.findingType in queued_types
                                    else "catalog_sweep_no_candidate"
                                ),
                            ))

        result.candidates = [c for (c, _ft, _pool) in candidates_total]
        result.evidences = list(evidence_pool.values())

        # Validate each candidate.
        for (cand, ft, ev_pool) in candidates_total:
            validation_calls_for_candidate = 0
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

            call_id = f"vv-{uuid.uuid4().hex[:12]}"
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
                result.assessments.append(SemanticAssessmentRecord(
                    candidateId=cand.candidateId,
                    state="validation_failed",
                    reasonCodes=[reason],
                    validationCallId=call_id,
                ))
                result.stageStats[cand.findingType][
                    "validatorStates"]["validation_failed"] += 1
                result.status = (
                    "budget_exhausted"
                    if reason == "run_budget_exhausted"
                    else "failed"
                )
                if result.reasonCode is None:
                    result.reasonCode = reason
                continue

            state, reasons = self._parse_and_check_validation(
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

                retry_call_count, effective_retry_call_id = (
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
                validation_calls_for_candidate += retry_call_count
                if retry_response is None or not retry_response.ok:
                    state = "validation_failed"
                    reasons = [(
                        retry_response.reason_code
                        if retry_response else retry_exc_reason)
                        or "validator_error"]
                    call_id = effective_retry_call_id
                else:
                    retry_state, retry_reasons = (
                        self._parse_and_check_validation(
                            cand=cand, ft=ft, payload=retry_response.payload))
                    state = retry_state
                    reasons = retry_reasons
                    call_id = effective_retry_call_id
            result.assessments.append(SemanticAssessmentRecord(
                candidateId=cand.candidateId,
                state=state, reasonCodes=reasons, validationCallId=call_id,
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
                        "candidateAssessmentId": call_id,
                        "validationIds": [call_id],
                    },
                    findingOccurrenceFingerprint=fp,
                    tags=["engine:" + ft.engine, "semantic"],
                    controls=list(ft.owaspAst10),
                ))
        return result

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
    ) -> Tuple[str, List[str]]:
        # 1. Strict schema (rejects severity/ruleId/findingType additions).
        try:
            _VALIDATION_RESULT_VALIDATOR.validate(payload)
        except SchemaValidationError as e:
            return "validation_failed", ["schema_violation"]
        # 2. candidateId identity match.
        if payload["candidateId"] != cand.candidateId:
            return "validation_failed", ["candidateId_mismatch"]
        # 3. Decision -> state (severity is IGNORED entirely; policy value wins).
        decision = payload["decision"]
        if decision == "confirmed":
            return "confirmed", list(payload.get("reasonCodes") or [])
        if decision == "rejected":
            return "rejected", list(payload.get("reasonCodes") or [])
        return "insufficient_evidence", list(payload.get("reasonCodes") or [])
