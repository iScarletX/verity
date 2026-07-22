"""Offline, deterministic Golden Corpus validation and measurement.

The corpus answer key names only stable risk ids. Detector output is mapped to
those risks through the independent Round-14 detector map, so a detector cannot
write its own expected label. Reviewed Skill fixtures are parsed/read only and
are never executed.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .intake import IntakeBudget, intake_directory, intake_text
from .review import ReviewInputs, run_review
from .standards import load_detector_mappings, load_risks


CORPUS_DIR = Path(__file__).resolve().parents[2] / "evals" / "corpus" / "v1"
MANIFEST_PATH = CORPUS_DIR / "manifest.json"
SEMANTIC_REPLAY_PATH = CORPUS_DIR / "semantic_replay.json"
CASE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
ALLOWED_OBJECTS = {"prompt", "skill"}
ALLOWED_LABELS = {"unsafe", "safe_counterexample"}
ALLOWED_PROMPT_KINDS = {"user_prompt", "system_prompt"}
MAX_CASES = 1000
MAX_CASE_FILES = 500
MAX_CASE_BYTES = 8 * 1024 * 1024
TEST_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


class CorpusError(ValueError):
    pass


def _no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CorpusError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> Dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"),
                           object_pairs_hook=_no_duplicate_keys)
    except CorpusError:
        raise
    except Exception as exc:
        raise CorpusError("cannot read corpus manifest") from exc
    if not isinstance(value, dict):
        raise CorpusError("corpus manifest must be an object")
    return value


def _safe_case_path(relative: str) -> Path:
    if (not isinstance(relative, str) or not relative or "\\" in relative
            or relative.startswith("/") or "\x00" in relative):
        raise CorpusError("invalid corpus case path")
    parts = relative.split("/")
    if any(p in ("", ".", "..") for p in parts):
        raise CorpusError("invalid corpus case path")
    path = CORPUS_DIR / relative
    try:
        path.resolve().relative_to(CORPUS_DIR.resolve())
    except ValueError as exc:
        raise CorpusError("corpus case path escapes root") from exc
    if path.is_symlink():
        raise CorpusError("symlinked corpus case refused")
    return path


def _test_fixture_file_digests() -> set[str]:
    """Exact-byte leakage guard against the existing developer fixtures."""
    result = set()
    if not TEST_FIXTURES_DIR.is_dir():
        return result
    for path in TEST_FIXTURES_DIR.rglob("*"):
        if path.is_file() and not path.is_symlink():
            result.add(hashlib.sha256(path.read_bytes()).hexdigest())
    return result


def _case_files(path: Path) -> List[Tuple[str, Path]]:
    if path.is_file():
        return [(path.name, path)]
    if path.is_dir():
        files = []
        for item in sorted(path.rglob("*")):
            if item.is_symlink():
                raise CorpusError("symlinked corpus content refused")
            if item.is_file():
                files.append((item.relative_to(path).as_posix(), item))
        return files
    raise CorpusError("corpus case path missing")


def _case_payload_digest(path: Path) -> str:
    """Digest a file or directory using stable paths + bytes."""
    h = hashlib.sha256()
    files = _case_files(path)
    if not files:
        raise CorpusError("empty corpus case")
    total = 0
    for rel, item in files:
        data = item.read_bytes()
        total += len(data)
        if total > MAX_CASE_BYTES:
            raise CorpusError("corpus case exceeds byte budget")
        h.update(len(rel.encode()).to_bytes(4, "big"))
        h.update(rel.encode())
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


def load_manifest() -> Dict[str, Any]:
    value = _load_json(MANIFEST_PATH)
    top_keys = {"schemaVersion", "corpusId", "corpusVersion", "license",
                "provenance", "description", "cases"}
    if set(value) != top_keys or value.get("schemaVersion") != 1:
        raise CorpusError("corpus manifest violates strict schema")
    for field in ("corpusId", "corpusVersion", "license", "provenance",
                  "description"):
        if not isinstance(value[field], str) or not value[field].strip():
            raise CorpusError(f"invalid corpus {field}")
    if value["license"] != "Apache-2.0" or value["provenance"] != "verity_synthetic":
        raise CorpusError("unsupported corpus provenance/license")
    cases = value["cases"]
    if not isinstance(cases, list) or not cases or len(cases) > MAX_CASES:
        raise CorpusError("invalid corpus case list")

    risks = load_risks()
    case_ids = set()
    payload_digests = set()
    developer_fixture_digests = _test_fixture_file_digests()
    exact_base = {"caseId", "objectType", "language", "label",
                  "assessedRiskIds", "expectedRiskIds", "expectedSeverity",
                  "path", "rationale", "provenance", "license",
                  "labelStatus"}
    for case in cases:
        if not isinstance(case, dict):
            raise CorpusError("corpus case must be an object")
        expected_keys = set(exact_base)
        if case.get("objectType") == "prompt":
            expected_keys.add("promptKind")
        if set(case) != expected_keys:
            raise CorpusError("corpus case violates strict schema")
        cid = case.get("caseId")
        if (not isinstance(cid, str) or not CASE_ID_RE.fullmatch(cid)
                or cid in case_ids):
            raise CorpusError("invalid or duplicate corpus caseId")
        case_ids.add(cid)
        obj = case.get("objectType")
        if obj not in ALLOWED_OBJECTS or case.get("label") not in ALLOWED_LABELS:
            raise CorpusError(f"case {cid} has invalid object/label")
        if obj == "prompt" and case.get("promptKind") not in ALLOWED_PROMPT_KINDS:
            raise CorpusError(f"case {cid} has invalid prompt kind")
        if obj == "skill" and "promptKind" in case:
            raise CorpusError(f"case {cid} has unexpected prompt kind")
        if not all(isinstance(case.get(k), str) and case[k].strip()
                   for k in ("language", "rationale")):
            raise CorpusError(f"case {cid} lacks language/rationale")
        if (case.get("provenance") != "verity_synthetic"
                or case.get("license") != "Apache-2.0"):
            raise CorpusError(f"case {cid} has unsupported provenance/license")
        if case.get("labelStatus") not in {
                "provisional_single_review", "independent_ai_review"}:
            raise CorpusError(f"case {cid} has unsupported label status")
        assessed = case.get("assessedRiskIds")
        expected = case.get("expectedRiskIds")
        if (not isinstance(assessed, list) or not assessed
                or len(assessed) != len(set(assessed))
                or not set(assessed) <= set(risks)):
            raise CorpusError(f"case {cid} has invalid assessed risks")
        if (not isinstance(expected, list)
                or len(expected) != len(set(expected))
                or not set(expected) <= set(assessed)):
            raise CorpusError(f"case {cid} has invalid expected risks")
        if case["label"] == "unsafe" and not expected:
            raise CorpusError(f"unsafe case {cid} has no expected risk")
        if case["label"] == "safe_counterexample" and expected:
            raise CorpusError(f"safe case {cid} has expected risk")
        severity = case.get("expectedSeverity")
        if ((case["label"] == "unsafe"
             and severity not in {"low", "medium", "high", "critical"})
                or (case["label"] == "safe_counterexample"
                    and severity is not None)):
            raise CorpusError(f"case {cid} has invalid expected severity")
        path = _safe_case_path(case["path"])
        if obj == "prompt" and not path.is_file():
            raise CorpusError(f"prompt case {cid} must be a file")
        if obj == "skill" and not path.is_dir():
            raise CorpusError(f"skill case {cid} must be a directory")
        if any(hashlib.sha256(item.read_bytes()).hexdigest()
               in developer_fixture_digests for _, item in _case_files(path)):
            raise CorpusError(f"corpus/test fixture exact-byte leakage: {cid}")
        digest = _case_payload_digest(path)
        if case["labelStatus"] == "independent_ai_review":
            try:
                from .review_evidence import (load_independent_ai_attestation,
                                              require_independent_ai_case)
                require_independent_ai_case(
                    case_id=cid, source_class="l0", payload_digest=digest,
                    expected_decision=("present" if expected else "absent"),
                    attestation=load_independent_ai_attestation())
            except Exception as exc:
                raise CorpusError(
                    f"case {cid} independent review evidence invalid") from exc
        if digest in payload_digests:
            raise CorpusError(f"duplicate corpus payload: {cid}")
        payload_digests.add(digest)
        case["payloadDigest"] = digest
    return value


def _review_case(case: Dict[str, Any]):
    path = _safe_case_path(case["path"])
    if case["objectType"] == "prompt":
        text = path.read_text(encoding="utf-8")
        snapshot, file_bytes = intake_text(
            text, prompt_kind=case["promptKind"])
        return run_review(ReviewInputs(
            engine="prompt", snapshot=snapshot, file_bytes=file_bytes))
    snapshot, file_bytes = intake_directory(
        path, budget=IntakeBudget(max_files=MAX_CASE_FILES,
                                  max_file_size=512 * 1024,
                                  max_total_size=MAX_CASE_BYTES))
    # minimal makes the corpus independent of an external gitleaks binary.
    # It is an explicit corpus scope, not a product recommendation. Bandit is
    # a pinned Python dependency and remains part of these deterministic runs.
    return run_review(ReviewInputs(
        engine="skill", snapshot=snapshot, file_bytes=file_bytes,
        profile="minimal"))


def _observed_risks(review) -> Tuple[set[str], Dict[str, int]]:
    mappings = load_detector_mappings()
    event_to_rule = {event.eventId: event.ruleId for event in review.ruleMatches}
    observed = set()
    counts: Dict[str, int] = {}
    for finding in review.findings:
        event_ids = (finding.origin or {}).get("ruleMatchEventIds", [])
        finding_risks = set()
        for event_id in event_ids:
            rule_id = event_to_rule.get(event_id)
            mapping = mappings.get(("deterministic_rule", rule_id))
            if mapping:
                finding_risks.update(mapping["riskIds"])
        for rid in finding_risks:
            observed.add(rid)
            counts[rid] = counts.get(rid, 0) + 1
    return observed, counts


def _ratio(numerator: int, denominator: int) -> Optional[float]:
    return None if denominator == 0 else round(numerator / denominator, 6)


def evaluate(*, repetitions: int = 2) -> Dict[str, Any]:
    if not isinstance(repetitions, int) or repetitions < 2 or repetitions > 10:
        raise CorpusError("repetitions must be 2..10")
    manifest = load_manifest()
    risks = load_risks()
    per_risk = {
        rid: {"tp": 0, "fp": 0, "tn": 0, "fn": 0,
              "caseCount": 0, "languages": set(), "objectTypes": set()}
        for rid in risks
    }
    case_results = []
    stable_cases = 0
    high_or_critical = {"caseCount": 0, "tp": 0, "fn": 0}
    for case in manifest["cases"]:
        observations = []
        count_observations = []
        coverage_statuses = []
        for _ in range(repetitions):
            review = _review_case(case)
            observed, counts = _observed_risks(review)
            observations.append(observed)
            count_observations.append(counts)
            coverage_statuses.append(review.coverage.status)
        stable = all(x == observations[0] for x in observations[1:]) and all(
            x == count_observations[0] for x in count_observations[1:])
        if stable:
            stable_cases += 1
        observed = observations[0]
        expected = set(case["expectedRiskIds"])
        assessed = set(case["assessedRiskIds"])
        if case["expectedSeverity"] in {"high", "critical"}:
            high_or_critical["caseCount"] += 1
            if expected <= observed:
                high_or_critical["tp"] += 1
            else:
                high_or_critical["fn"] += 1
        for rid in assessed:
            row = per_risk[rid]
            row["caseCount"] += 1
            row["languages"].add(case["language"])
            row["objectTypes"].add(case["objectType"])
            is_expected = rid in expected
            is_observed = rid in observed
            key = ("tp" if is_expected and is_observed else
                   "fn" if is_expected else
                   "fp" if is_observed else "tn")
            row[key] += 1
        case_results.append({
            "caseId": case["caseId"],
            "label": case["label"],
            "expectedSeverity": case["expectedSeverity"],
            "labelStatus": case["labelStatus"],
            "assessedRiskIds": sorted(assessed),
            "expectedRiskIds": sorted(expected),
            "observedRiskIds": sorted(observed & assessed),
            "unexpectedOutOfScopeRiskIds": sorted(observed - assessed),
            "coverageStatuses": coverage_statuses,
            "stable": stable,
        })

    risk_results = []
    for rid, risk in risks.items():
        row = per_risk[rid]
        if row["caseCount"]:
            status = "measured"
        elif risk["currentCoverage"]["L0_static"] == "none":
            status = "unsupported"
        else:
            status = "unmeasured"
        tp, fp, tn, fn = row["tp"], row["fp"], row["tn"], row["fn"]
        risk_results.append({
            "riskId": rid,
            "severityPolicy": "not_scored_in_round15",
            "status": status,
            "caseCount": row["caseCount"],
            "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
            "precision": _ratio(tp, tp + fp),
            "recall": _ratio(tp, tp + fn),
            "safeFalsePositiveRate": _ratio(fp, fp + tn),
            "languages": sorted(row["languages"]),
            "objectTypes": sorted(row["objectTypes"]),
        })
    return {
        "schemaVersion": 1,
        "corpusId": manifest["corpusId"],
        "corpusVersion": manifest["corpusVersion"],
        "repetitions": repetitions,
        "caseCount": len(manifest["cases"]),
        "baselineClass": "minimal_pair_baseline",
        "aggregateSafetyScore": None,
        "highOrCriticalUnsafeCases": high_or_critical,
        "stability": {
            "stableCases": stable_cases,
            "unstableCases": len(manifest["cases"]) - stable_cases,
            "rate": _ratio(stable_cases, len(manifest["cases"])),
        },
        "riskResults": risk_results,
        "caseResults": case_results,
        "disclaimer": (
            "Minimal paired per-risk offline baseline only. One positive and "
            "one safe counterexample cannot establish broad precision/recall. "
            "This is not a safety score and not a semantic, black-box, or "
            "sandbox evaluation."
        ),
    }


class _ReplayGenerator:
    def __init__(self, case: Dict[str, Any]):
        self.case = case
        self.calls = 0

    def generate_candidates(self, *, call, request):
        from .semantic.provider import ProviderResponse
        self.calls += 1
        evidence = request.get("evidence") or []
        if self.calls > 1 or not evidence:
            return ProviderResponse(ok=True, payload={"candidates": []})
        ids = [x["evidenceId"] for x in evidence]
        return ProviderResponse(ok=True, payload={"candidates": [{
            "proposedCandidateId": "replay-candidate",
            "findingType": self.case["findingType"],
            "subject": self.case["candidateSubject"],
            "claim": self.case["claim"],
            "evidenceIds": ids[:8],
        }]})


class _ReplayValidator:
    def __init__(self, case: Dict[str, Any]):
        self.case = case
        self.calls = 0

    def validate_candidate(self, *, call, request):
        from .semantic.provider import ProviderResponse
        self.calls += 1
        return ProviderResponse(ok=True, payload={
            "candidateId": request["candidate"]["candidateId"],
            "decision": self.case["expectedAssessment"],
            "reasonCodes": self.case["validatorReasonCodes"],
        })


def load_semantic_replay() -> Dict[str, Any]:
    value = _load_json(SEMANTIC_REPLAY_PATH)
    top = {"schemaVersion", "replayClass", "license", "provenance",
           "description", "cases"}
    if (set(value) != top or value.get("schemaVersion") != 1
            or value.get("replayClass") != "semantic_contract_only"
            or value.get("license") != "Apache-2.0"
            or value.get("provenance") != "verity_synthetic"):
        raise CorpusError("semantic replay violates strict schema")
    from .semantic.catalog import CATALOG
    risks = load_risks()
    mappings = load_detector_mappings(risks)
    cases = value.get("cases")
    if not isinstance(cases, list) or not cases or len(cases) > MAX_CASES:
        raise CorpusError("invalid semantic replay cases")
    seen = set()
    payload_digests = set()
    developer_fixture_digests = _test_fixture_file_digests()
    base = {"caseId", "objectType", "language", "path", "findingType",
            "riskId", "expectedAssessment", "candidateSubject", "claim",
            "validatorReasonCodes", "provenance", "license",
            "labelStatus"}
    for case in cases:
        expected_keys = set(base)
        if case.get("objectType") == "prompt":
            expected_keys.add("promptKind")
        if not isinstance(case, dict) or set(case) != expected_keys:
            raise CorpusError("semantic replay case violates strict schema")
        cid = case.get("caseId")
        if (not isinstance(cid, str) or not CASE_ID_RE.fullmatch(cid)
                or cid in seen):
            raise CorpusError("invalid semantic replay caseId")
        seen.add(cid)
        obj = case.get("objectType")
        if obj not in ALLOWED_OBJECTS:
            raise CorpusError(f"semantic case {cid} has invalid object")
        if (case.get("provenance") != "verity_synthetic"
                or case.get("license") != "Apache-2.0"):
            raise CorpusError(
                f"semantic case {cid} has unsupported provenance/license")
        if case.get("labelStatus") != "provisional_single_review":
            raise CorpusError(
                f"semantic case {cid} has unsupported label status")
        if obj == "prompt" and case.get("promptKind") not in ALLOWED_PROMPT_KINDS:
            raise CorpusError(f"semantic case {cid} has invalid prompt kind")
        finding_type = case.get("findingType")
        if finding_type not in CATALOG or CATALOG[finding_type][0].engine != obj:
            raise CorpusError(f"semantic case {cid} has invalid finding type")
        mapping = mappings.get(("semantic_finding_type", finding_type))
        if not mapping or case.get("riskId") not in mapping["riskIds"]:
            raise CorpusError(f"semantic case {cid} has inconsistent risk mapping")
        if case.get("expectedAssessment") not in {"confirmed", "rejected"}:
            raise CorpusError(f"semantic case {cid} has invalid assessment")
        if (not isinstance(case.get("candidateSubject"), dict)
                or not isinstance(case.get("claim"), str)
                or not case["claim"].strip()
                or not isinstance(case.get("validatorReasonCodes"), list)
                or not case["validatorReasonCodes"]):
            raise CorpusError(f"semantic case {cid} has invalid replay payload")
        path = _safe_case_path(case["path"])
        if (obj == "prompt" and not path.is_file()) or (
                obj == "skill" and not path.is_dir()):
            raise CorpusError(f"semantic case {cid} path kind mismatch")
        if any(hashlib.sha256(item.read_bytes()).hexdigest()
               in developer_fixture_digests for _, item in _case_files(path)):
            raise CorpusError(
                f"semantic corpus/test fixture exact-byte leakage: {cid}")
        digest = _case_payload_digest(path)
        if digest in payload_digests:
            raise CorpusError(f"duplicate semantic replay payload: {cid}")
        payload_digests.add(digest)
    return value


def _review_semantic_case(case: Dict[str, Any]):
    from .semantic.config import (ProviderConfig, ProviderCredentials,
                                  SemanticConfig)
    cfg = SemanticConfig(
        enabled=True,
        egress_policy="metadata_only",
        enabled_finding_types=[case["findingType"]],
        provider_config={
            "candidate_generator": ProviderConfig(
                role="candidate_generator", provider_id="corpus-replay",
                model_id="fixed-contract", credentials=ProviderCredentials()),
            "validator": ProviderConfig(
                role="validator", provider_id="corpus-replay",
                model_id="fixed-contract", credentials=ProviderCredentials()),
        },
    )
    path = _safe_case_path(case["path"])
    if case["objectType"] == "prompt":
        snapshot, file_bytes = intake_text(
            path.read_text(encoding="utf-8"), prompt_kind=case["promptKind"])
        inputs = ReviewInputs("prompt", snapshot, file_bytes,
                              semantic_config=cfg)
    else:
        snapshot, file_bytes = intake_directory(
            path, budget=IntakeBudget(max_files=MAX_CASE_FILES,
                                      max_file_size=512 * 1024,
                                      max_total_size=MAX_CASE_BYTES))
        inputs = ReviewInputs("skill", snapshot, file_bytes, profile="minimal",
                              semantic_config=cfg)
    gen = _ReplayGenerator(case)
    val = _ReplayValidator(case)
    review = run_review(inputs, candidate_generator=gen, validator=val)
    return review, gen.calls, val.calls


def evaluate_semantic_replay(*, repetitions: int = 2) -> Dict[str, Any]:
    if not isinstance(repetitions, int) or repetitions < 2 or repetitions > 10:
        raise CorpusError("repetitions must be 2..10")
    replay = load_semantic_replay()
    results = []
    stable_count = 0
    correct_count = 0
    for case in replay["cases"]:
        observed_runs = []
        for _ in range(repetitions):
            review, generator_calls, validator_calls = _review_semantic_case(case)
            semantic = review.semantic or {}
            assessments = semantic.get("assessments") or []
            findings = semantic.get("findings") or []
            observed = assessments[0]["state"] if assessments else "no_assessment"
            emitted = sorted(x["findingType"] for x in findings)
            observed_runs.append({
                "assessment": observed,
                "emittedFindingTypes": emitted,
                "semanticStatus": semantic.get("status"),
                "generatorCalls": generator_calls,
                "validatorCalls": validator_calls,
            })
        stable = all(x == observed_runs[0] for x in observed_runs[1:])
        if stable:
            stable_count += 1
        first = observed_runs[0]
        should_emit = case["expectedAssessment"] == "confirmed"
        correct = (first["assessment"] == case["expectedAssessment"]
                   and ((case["findingType"] in first["emittedFindingTypes"])
                        == should_emit))
        if correct:
            correct_count += 1
        results.append({
            "caseId": case["caseId"],
            "findingType": case["findingType"],
            "riskId": case["riskId"],
            "expectedAssessment": case["expectedAssessment"],
            "labelStatus": case["labelStatus"],
            "observed": first,
            "stable": stable,
            "contractCorrect": correct,
        })
    return {
        "schemaVersion": 1,
        "replayClass": "semantic_contract_only",
        "modelQualityMeasured": False,
        "aggregateSafetyScore": None,
        "caseCount": len(results),
        "contractCorrectCases": correct_count,
        "stability": {
            "stableCases": stable_count,
            "unstableCases": len(results) - stable_count,
            "rate": _ratio(stable_count, len(results)),
        },
        "caseResults": results,
        "disclaimer": (
            "Fixed Provider replay validates semantic pipeline contracts only; "
            "it does not measure candidate generation or validator model quality."
        ),
    }


def canonical_report_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
