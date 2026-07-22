"""Versioned, synthetic real-model semantic quality protocol.

The module is intentionally separate from product review entry points.  It uses
Verity's existing SemanticOrchestrator and closed catalog, but emits only a
scrubbed measurement report: no case text, source snippets, model response text,
claims, subjects, endpoints, credentials, or absolute paths.
"""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .corpus import (CASE_ID_RE, CORPUS_DIR, MAX_CASES, MAX_CASE_BYTES,
                     MAX_CASE_FILES, CorpusError, _case_files,
                     _case_payload_digest, _safe_case_path,
                     _test_fixture_file_digests)
from .intake import IntakeBudget, intake_directory, intake_text
from .report import review_to_dict
from .review import ReviewInputs, run_review
from .semantic.catalog import CATALOG
from .semantic.config import ProviderConfig, SemanticBudget, SemanticConfig
from .standards import load_detector_mappings, load_risks


QUALITY_MANIFEST_PATH = CORPUS_DIR / "semantic_quality.json"
SPLITS = ("calibration", "selection", "test")


def _load_strict_json(path: Path) -> Dict[str, Any]:
    def no_duplicates(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise CorpusError(f"duplicate JSON key: {key}")
            value[key] = item
        return value
    try:
        value = json.loads(path.read_text("utf-8"),
                           object_pairs_hook=no_duplicates)
    except CorpusError:
        raise
    except Exception as exc:
        raise CorpusError("cannot read semantic quality manifest") from exc
    if not isinstance(value, dict):
        raise CorpusError("semantic quality manifest must be an object")
    return value


def load_semantic_quality_manifest(path: Path = QUALITY_MANIFEST_PATH
                                   ) -> Dict[str, Any]:
    value = _load_strict_json(path)
    top = {"schemaVersion", "protocolId", "protocolVersion", "license",
           "provenance", "labelStatus", "description", "splitPolicy", "cases"}
    if set(value) != top or value.get("schemaVersion") != 1:
        raise CorpusError("semantic quality manifest violates strict schema")
    if (value.get("protocolId") != "verity-semantic-quality-v1"
            or value.get("protocolVersion") != "2.0.0"
            or value.get("license") != "Apache-2.0"
            or value.get("provenance") != "verity_synthetic"
            or value.get("labelStatus") != "mixed_independent_ai_and_provisional"):
        raise CorpusError("semantic quality manifest provenance/version invalid")
    if not isinstance(value.get("description"), str) or not value["description"].strip():
        raise CorpusError("semantic quality description required")
    policy = value.get("splitPolicy")
    if (not isinstance(policy, dict) or set(policy) != set(SPLITS)
            or not all(isinstance(policy[s], str) and policy[s].strip()
                       for s in SPLITS)):
        raise CorpusError("semantic quality split policy invalid")
    cases = value.get("cases")
    if not isinstance(cases, list) or not cases or len(cases) > MAX_CASES:
        raise CorpusError("semantic quality cases invalid")

    mappings = load_detector_mappings(load_risks())
    fixture_digests = _test_fixture_file_digests()
    case_ids = set()
    payload_digests: Dict[str, str] = {}
    coverage = defaultdict(lambda: defaultdict(set))
    base = {"caseId", "split", "objectType", "language", "path",
            "findingType", "riskId", "expectedAssessment", "provenance",
            "license", "labelStatus"}
    for case in cases:
        expected_keys = set(base)
        if isinstance(case, dict) and case.get("objectType") == "prompt":
            expected_keys.add("promptKind")
        if not isinstance(case, dict) or set(case) != expected_keys:
            raise CorpusError("semantic quality case violates strict schema")
        cid = case.get("caseId")
        if (not isinstance(cid, str) or not CASE_ID_RE.fullmatch(cid)
                or cid in case_ids):
            raise CorpusError("invalid or duplicate semantic quality caseId")
        case_ids.add(cid)
        split = case.get("split")
        obj = case.get("objectType")
        if split not in SPLITS or obj not in {"prompt", "skill"}:
            raise CorpusError(f"semantic quality case {cid} split/object invalid")
        expected_label_status = ("provisional_single_review" if split == "test"
                                 else "independent_ai_review")
        if (case.get("provenance") != "verity_synthetic"
                or case.get("license") != "Apache-2.0"
                or case.get("labelStatus") != expected_label_status):
            raise CorpusError(f"semantic quality case {cid} provenance invalid")
        if not isinstance(case.get("language"), str) or not case["language"].strip():
            raise CorpusError(f"semantic quality case {cid} language invalid")
        if obj == "prompt" and case.get("promptKind") not in {
                "user_prompt", "system_prompt"}:
            raise CorpusError(f"semantic quality case {cid} prompt kind invalid")
        ft = case.get("findingType")
        if ft not in CATALOG or CATALOG[ft][0].engine != obj:
            raise CorpusError(f"semantic quality case {cid} finding type invalid")
        mapping = mappings.get(("semantic_finding_type", ft))
        if not mapping or case.get("riskId") not in mapping["riskIds"]:
            raise CorpusError(f"semantic quality case {cid} risk mapping invalid")
        decision = case.get("expectedAssessment")
        if decision not in {"confirmed", "rejected"}:
            raise CorpusError(f"semantic quality case {cid} assessment invalid")
        case_path = _safe_case_path(case["path"])
        if ((obj == "prompt" and not case_path.is_file())
                or (obj == "skill" and not case_path.is_dir())):
            raise CorpusError(f"semantic quality case {cid} path kind mismatch")
        for _, item in _case_files(case_path):
            if hashlib.sha256(item.read_bytes()).hexdigest() in fixture_digests:
                raise CorpusError(f"semantic quality/test fixture leakage: {cid}")
        digest = _case_payload_digest(case_path)
        if case["labelStatus"] == "independent_ai_review":
            try:
                from .review_evidence import (load_independent_ai_attestation,
                                              require_independent_ai_case)
                require_independent_ai_case(
                    case_id=cid, source_class="semantic_quality_non_test",
                    payload_digest=digest,
                    expected_decision=("present" if decision == "confirmed"
                                       else "absent"),
                    attestation=load_independent_ai_attestation())
            except Exception as exc:
                raise CorpusError(
                    f"semantic quality case {cid} review evidence invalid") from exc
        if digest in payload_digests:
            raise CorpusError(
                f"semantic quality duplicate payload: {cid}/{payload_digests[digest]}")
        payload_digests[digest] = cid
        coverage[split][ft].add(decision)
        case["payloadDigest"] = digest

    # Selection and sealed test must independently cover every controlled type
    # with both an unsafe and a safe case. Calibration is allowed to evolve but
    # v1 currently follows the same stronger shape.
    for split in SPLITS:
        if set(coverage[split]) != set(CATALOG):
            raise CorpusError(f"semantic quality split lacks finding types: {split}")
        for ft in CATALOG:
            if coverage[split][ft] != {"confirmed", "rejected"}:
                raise CorpusError(f"semantic quality split lacks pair: {split}/{ft}")
    return value


def validate_semantic_quality_seed_coverage(
        path: Path = QUALITY_MANIFEST_PATH) -> int:
    """Offline gate: every quality case must actually reach its extractor.

    This does not call a Provider and does not inspect model decisions, so it
    does not consume the sealed test split. It prevents no-seed cases from
    being misreported as model-quality true negatives.
    """
    manifest = load_semantic_quality_manifest(path)
    checked = 0
    for case in manifest["cases"]:
        case_path = _safe_case_path(case["path"])
        if case["objectType"] == "prompt":
            snapshot, file_bytes = intake_text(
                case_path.read_text("utf-8"), prompt_kind=case["promptKind"])
            review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
        else:
            snapshot, file_bytes = intake_directory(
                case_path, budget=IntakeBudget(max_files=MAX_CASE_FILES,
                                               max_file_size=512 * 1024,
                                               max_total_size=MAX_CASE_BYTES))
            review = run_review(ReviewInputs("skill", snapshot, file_bytes,
                                             profile="minimal"))
        extractor = CATALOG[case["findingType"]][1]
        seeds = extractor(review_to_dict(review), file_bytes)
        if not seeds:
            raise CorpusError(
                f"semantic quality case has no deterministic seed: {case['caseId']}")
        checked += 1
    return checked


def _ratio(n: int, d: int) -> Optional[float]:
    return None if not d else round(n / d, 6)


SELECTION_GATE_POLICY_VERSION = "1.0.0"
SELECTION_THRESHOLDS = {
    "minimumRecall": 0.90,
    "maximumSafeFalsePositiveRate": 0.20,
    "minimumStabilityRate": 0.80,
    "maximumErrorRate": 0.05,
    "maximumInconclusiveRate": 0.10,
}


def _selection_gate(split: str, metrics: Dict[str, Any],
                    stability: Dict[str, Any]) -> Dict[str, Any]:
    base = {"policyVersion": SELECTION_GATE_POLICY_VERSION,
            "thresholds": dict(SELECTION_THRESHOLDS)}
    if split != "selection":
        return {**base, "status": "not_applicable", "failedMetrics": []}
    checks = {
        "recall": (metrics.get("recall") is not None
                   and metrics["recall"] >= SELECTION_THRESHOLDS["minimumRecall"]),
        "safeFalsePositiveRate": (
            metrics.get("safeFalsePositiveRate") is not None
            and metrics["safeFalsePositiveRate"]
            <= SELECTION_THRESHOLDS["maximumSafeFalsePositiveRate"]),
        "stabilityRate": (stability.get("rate") is not None
                          and stability["rate"]
                          >= SELECTION_THRESHOLDS["minimumStabilityRate"]),
        "errorRate": (metrics.get("errorRate") is not None
                      and metrics["errorRate"]
                      <= SELECTION_THRESHOLDS["maximumErrorRate"]),
        "inconclusiveRate": (
            metrics.get("inconclusiveRate") is not None
            and metrics["inconclusiveRate"]
            <= SELECTION_THRESHOLDS["maximumInconclusiveRate"]),
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    return {**base, "status": "eligible" if not failed else "not_eligible",
            "failedMetrics": failed}


def _config_fingerprint(generator: ProviderConfig, validator: ProviderConfig,
                        *, temperature: float, max_output_tokens: int,
                        repetitions: int, role_prompt_version: str,
                        protocol_version: str, corpus_fingerprint: str) -> str:
    # Endpoint and credential environment names intentionally do not enter the
    # public report. Their presence/values are deployment metadata, not model
    # quality dimensions.
    safe = {
        "generator": {"providerId": generator.provider_id,
                      "modelId": generator.model_id,
                      "endpointSha256": hashlib.sha256(
                          generator.base_url.encode()).hexdigest()},
        "validator": {"providerId": validator.provider_id,
                      "modelId": validator.model_id,
                      "endpointSha256": hashlib.sha256(
                          validator.base_url.encode()).hexdigest()},
        "temperature": temperature, "maxOutputTokens": max_output_tokens,
        "repetitions": repetitions, "egressPolicy": "redacted_evidence",
        "rolePromptVersion": role_prompt_version,
        "catalog": sorted(CATALOG), "protocolVersion": protocol_version,
        "corpusFingerprint": corpus_fingerprint,
    }
    raw = json.dumps(safe, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def _run_case(case: Dict[str, Any], *, generator, validator,
              generator_config: ProviderConfig,
              validator_config: ProviderConfig) -> Tuple[str, Dict[str, Any]]:
    config = SemanticConfig(
        enabled=True, egress_policy="redacted_evidence",
        enabled_finding_types=[case["findingType"]],
        provider_config={"candidate_generator": generator_config,
                         "validator": validator_config},
        budget=SemanticBudget(max_candidate_generation_calls=1,
                              max_validation_calls_per_candidate=1,
                              max_total_validation_calls=1,
                              max_candidates_per_extractor=1,
                              max_candidates_total=1,
                              max_evidence_per_candidate=8),
    )
    path = _safe_case_path(case["path"])
    if case["objectType"] == "prompt":
        snapshot, file_bytes = intake_text(path.read_text("utf-8"),
                                           prompt_kind=case["promptKind"])
        inputs = ReviewInputs("prompt", snapshot, file_bytes,
                              semantic_config=config)
    else:
        snapshot, file_bytes = intake_directory(
            path, budget=IntakeBudget(max_files=MAX_CASE_FILES,
                                      max_file_size=512 * 1024,
                                      max_total_size=MAX_CASE_BYTES))
        inputs = ReviewInputs("skill", snapshot, file_bytes, profile="minimal",
                              semantic_config=config)
    review = run_review(inputs, candidate_generator=generator,
                        validator=validator)
    semantic = review.semantic or {}
    status = semantic.get("status") or "failed"
    assessments = semantic.get("assessments") or []
    states = [str(x.get("state")) for x in assessments]
    if status not in {"completed"}:
        observed = "error"
    elif "confirmed" in states:
        observed = "confirmed"
    elif "validation_failed" in states:
        observed = "error"
    elif "insufficient_evidence" in states:
        observed = "insufficient_evidence"
    elif "rejected" in states:
        observed = "rejected"
    elif not assessments:
        observed = "no_candidate"
    else:
        observed = "error"
    safe_reasons = sorted({
        reason for item in assessments for reason in (item.get("reasonCodes") or [])
        if isinstance(reason, str) and len(reason) <= 100
    })
    reason = semantic.get("reasonCode")
    if isinstance(reason, str) and len(reason) <= 100:
        safe_reasons.append(reason)
    return observed, {
        "semanticStatus": status,
        "reasonCodes": sorted(set(safe_reasons)),
        "candidateCount": len(semantic.get("candidates") or []),
        "assessmentCount": len(assessments),
        "findingCount": len(semantic.get("findings") or []),
        "callCounts": dict(semantic.get("callCounts") or {}),
    }


def _metric(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    tp = fp = tn = fn = inconclusive = errors = 0
    for row in rows:
        expected = row["expectedAssessment"]
        observed = row["observedAssessment"]
        if observed == "error":
            errors += 1
        elif expected == "confirmed":
            if observed == "confirmed": tp += 1
            else: fn += 1
        elif observed == "confirmed":
            fp += 1
        elif observed in {"rejected", "no_candidate"}:
            tn += 1
        else:
            inconclusive += 1
    return {
        "runCount": len(rows),
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "inconclusive": inconclusive, "errors": errors,
        "precision": _ratio(tp, tp + fp),
        "recall": _ratio(tp, tp + fn),
        "safeFalsePositiveRate": _ratio(fp, fp + tn),
        "inconclusiveRate": _ratio(inconclusive, len(rows)),
        "errorRate": _ratio(errors, len(rows)),
    }


def evaluate_semantic_model_quality(*, split: str, repetitions: int,
                                    generator, validator,
                                    generator_config: ProviderConfig,
                                    validator_config: ProviderConfig,
                                    temperature: float = 0.0,
                                    max_output_tokens: int = 800,
                                    max_total_calls: int = 60,
                                    role_prompt_version: str = "unspecified",
                                    acknowledge_sealed_test: bool = False,
                                    manifest_path: Path = QUALITY_MANIFEST_PATH,
                                    ) -> Dict[str, Any]:
    if split not in SPLITS:
        raise CorpusError("semantic quality split invalid")
    if split == "test" and not acknowledge_sealed_test:
        raise CorpusError("sealed test requires explicit acknowledgement")
    if not isinstance(repetitions, int) or not 2 <= repetitions <= 10:
        raise CorpusError("semantic quality repetitions must be 2..10")
    if (not isinstance(role_prompt_version, str)
            or not role_prompt_version.strip()
            or len(role_prompt_version) > 32):
        raise CorpusError("semantic quality role prompt version invalid")
    if generator_config.role != "candidate_generator" or validator_config.role != "validator":
        raise CorpusError("semantic quality Provider roles invalid")
    if not generator_config.credentials.resolve() or not validator_config.credentials.resolve():
        raise CorpusError("semantic quality credentials missing before run")
    manifest = load_semantic_quality_manifest(manifest_path)
    selected = [c for c in manifest["cases"] if c["split"] == split]
    corpus_fingerprint = hashlib.sha256(json.dumps([
        {"caseId": c["caseId"], "payloadDigest": c["payloadDigest"],
         "expectedAssessment": c["expectedAssessment"]}
        for c in sorted(selected, key=lambda x: x["caseId"])
    ], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    required_call_budget = len(selected) * repetitions * 2
    if (not isinstance(max_total_calls, int) or max_total_calls < 1
            or required_call_budget > max_total_calls):
        raise CorpusError(
            f"semantic quality call budget requires {required_call_budget}, "
            f"configured {max_total_calls}")
    case_results = []
    all_runs = []
    calls = {"generator": 0, "validator": 0}
    stable_cases = 0
    for case in selected:
        runs = []
        for _ in range(repetitions):
            observed, detail = _run_case(
                case, generator=generator, validator=validator,
                generator_config=generator_config,
                validator_config=validator_config)
            row = {"observedAssessment": observed, **detail}
            runs.append(row)
            calls["generator"] += int(detail["callCounts"].get("generator", 0))
            calls["validator"] += int(detail["callCounts"].get("validator", 0))
            all_runs.append({"expectedAssessment": case["expectedAssessment"],
                             "observedAssessment": observed})
        stable = all(r == runs[0] for r in runs[1:])
        stable_cases += int(stable)
        case_results.append({
            "caseId": case["caseId"], "split": split,
            "findingType": case["findingType"], "riskId": case["riskId"],
            "objectType": case["objectType"], "language": case["language"],
            "expectedAssessment": case["expectedAssessment"],
            "observedAssessments": [r["observedAssessment"] for r in runs],
            "runDetails": runs, "stable": stable,
            "labelStatus": case["labelStatus"],
        })
    dimensions = {}
    for dimension in ("findingType", "language", "objectType"):
        grouped = defaultdict(list)
        for case in case_results:
            for observed in case["observedAssessments"]:
                grouped[case[dimension]].append({
                    "expectedAssessment": case["expectedAssessment"],
                    "observedAssessment": observed})
        dimensions[dimension] = {key: _metric(rows)
                                 for key, rows in sorted(grouped.items())}
    metrics = _metric(all_runs)
    stability = {"stableCases": stable_cases,
                 "unstableCases": len(case_results) - stable_cases,
                 "rate": _ratio(stable_cases, len(case_results))}
    return {
        "schemaVersion": 1,
        "protocolId": manifest["protocolId"],
        "protocolVersion": manifest["protocolVersion"],
        "evaluationClass": "synthetic_real_model_semantic_quality",
        "modelQualityMeasured": True,
        "aggregateSafetyScore": None,
        "split": split,
        "sealedTestConsumed": split == "test",
        "labelStatus": manifest["labelStatus"],
        "caseCount": len(case_results), "repetitions": repetitions,
        "configuration": {
            "generatorProviderId": generator_config.provider_id,
            "generatorModelId": generator_config.model_id,
            "validatorProviderId": validator_config.provider_id,
            "validatorModelId": validator_config.model_id,
            "temperature": temperature, "maxOutputTokens": max_output_tokens,
            "egressPolicy": "redacted_evidence",
            "rolePromptVersion": role_prompt_version,
            "corpusFingerprint": corpus_fingerprint,
            "configurationFingerprint": _config_fingerprint(
                generator_config, validator_config, temperature=temperature,
                max_output_tokens=max_output_tokens, repetitions=repetitions,
                role_prompt_version=role_prompt_version,
                protocol_version=manifest["protocolVersion"],
                corpus_fingerprint=corpus_fingerprint),
        },
        "metrics": metrics,
        "stability": stability,
        "selectionGate": _selection_gate(split, metrics, stability),
        "callBudget": {"configuredMax": max_total_calls,
                       "requiredMaximum": required_call_budget},
        "callCounts": calls,
        "dimensions": dimensions,
        "caseResults": case_results,
        "disclaimer": (
            "Synthetic provisional single-review corpus only. This measures one "
            "frozen model/configuration on one split; it is not a product safety "
            "score or evidence of broad production accuracy. Test results must not "
            "be used to tune this protocol version."
        ),
    }
