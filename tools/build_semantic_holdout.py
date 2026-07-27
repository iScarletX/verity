#!/usr/bin/env python3
"""Build and freeze a local answer-hidden semantic holdout."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from verity.corpus import CORPUS_DIR, CorpusError, canonical_report_json
from verity.intake import IntakeBudget, intake_directory, intake_text
from verity.report import review_to_dict
from verity.review import ReviewInputs, run_review
from verity.semantic.catalog import CATALOG
from verity.semantic_benchmark import (
    COMPARISON_MANIFEST_PATH,
    COMPARISON_PROTOCOL_ID,
    COMPARISON_PROTOCOL_V5,
    PROTOCOL_V5_EVALUATION_POLICY,
    _corpus_fingerprint,
    build_semantic_comparison_packet,
    load_semantic_comparison_manifest,
    validate_semantic_comparison_seed_coverage,
)

SYSTEM_IDS = (
    "verity",
    "butler",
    "label-reviewer-a",
    "label-reviewer-b",
    "label-reviewer-c",
)
VERSION_RE = re.compile(r"v[1-9][0-9]{0,2}")
SAFE_FILE_RE = re.compile(r"[A-Za-z0-9_.-]{1,120}")


def _read_json_value(path: Path, label: str) -> Any:
    def no_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CorpusError(f"{label} contains duplicate key: {key}")
            result[key] = value
        return result
    try:
        return json.loads(
            path.read_text("utf-8"), object_pairs_hook=no_duplicates)
    except CorpusError:
        raise
    except Exception as exc:
        raise CorpusError(f"cannot read {label}") from exc


def _read_json(path: Path, label: str) -> Dict[str, Any]:
    value = _read_json_value(path, label)
    if not isinstance(value, dict):
        raise CorpusError(f"{label} must be an object")
    return value


def _safe_relative_file(raw: Any) -> Path:
    if not isinstance(raw, str) or not raw or len(raw) > 512:
        raise CorpusError("holdout source file path invalid")
    path = Path(raw)
    if (path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts)
            or any(not SAFE_FILE_RE.fullmatch(part) for part in path.parts)):
        raise CorpusError("holdout source file path invalid")
    return path


def _preferred_risk_ids() -> Dict[str, str]:
    manifest = load_semantic_comparison_manifest(COMPARISON_MANIFEST_PATH)
    out = {}
    for case in manifest["cases"]:
        finding_type = case["findingType"]
        risk_id = case["riskId"]
        previous = out.setdefault(finding_type, risk_id)
        if previous != risk_id:
            raise CorpusError(
                "development comparison maps one Finding Type to many risks")
    if set(out) != set(CATALOG):
        raise CorpusError("development comparison risk map is incomplete")
    return out


def _validate_source(
        source: Dict[str, Any], version_name: str) -> List[Dict[str, Any]]:
    if set(source) != {
            "schemaVersion", "versionName", "description", "cases"}:
        raise CorpusError("holdout source schema invalid")
    if (source.get("schemaVersion") != 1
            or source.get("versionName") != version_name
            or not isinstance(source.get("description"), str)
            or not source["description"].strip()):
        raise CorpusError("holdout source identity invalid")
    cases = source.get("cases")
    if not isinstance(cases, list) or len(cases) != len(CATALOG) * 4:
        raise CorpusError(
            f"holdout source requires exactly {len(CATALOG) * 4} cases")

    coverage = defaultdict(list)
    seen_payloads = set()
    normalized = []
    for index, row in enumerate(cases, start=1):
        if not isinstance(row, dict):
            raise CorpusError("holdout source case invalid")
        object_type = row.get("objectType")
        expected = {
            "findingType", "assessment", "objectType", "language",
            "files", "rationale",
        }
        if object_type == "prompt":
            expected.add("promptKind")
        if set(row) != expected:
            raise CorpusError("holdout source case schema invalid")
        finding_type = row.get("findingType")
        if (finding_type not in CATALOG
                or CATALOG[finding_type][0].engine != object_type):
            raise CorpusError("holdout source Finding Type invalid")
        assessment = row.get("assessment")
        if assessment not in {"present", "absent"}:
            raise CorpusError("holdout source assessment invalid")
        language = row.get("language")
        rationale = row.get("rationale")
        if (not isinstance(language, str) or not 1 <= len(language) <= 40
                or not isinstance(rationale, str)
                or not 20 <= len(rationale) <= 2000):
            raise CorpusError("holdout source explanation invalid")
        if (object_type == "prompt"
                and row.get("promptKind")
                not in {"user_prompt", "system_prompt"}):
            raise CorpusError("holdout source prompt kind invalid")
        files = row.get("files")
        if not isinstance(files, list) or not 1 <= len(files) <= 32:
            raise CorpusError("holdout source files invalid")
        normalized_files = []
        file_paths = set()
        total_bytes = 0
        for file_info in files:
            if not isinstance(file_info, dict) or set(file_info) != {
                    "path", "content"}:
                raise CorpusError("holdout source file invalid")
            path = _safe_relative_file(file_info["path"])
            content = file_info["content"]
            if (path.as_posix() in file_paths
                    or not isinstance(content, str) or not content.strip()):
                raise CorpusError("holdout source file invalid")
            size = len(content.encode("utf-8"))
            if size > 128 * 1024:
                raise CorpusError("holdout source file too large")
            total_bytes += size
            file_paths.add(path.as_posix())
            normalized_files.append({
                "path": path.as_posix(),
                "content": content,
            })
        if total_bytes > 512 * 1024:
            raise CorpusError("holdout source case too large")
        if object_type == "prompt" and file_paths != {"prompt.txt"}:
            raise CorpusError("prompt holdout case must contain prompt.txt only")
        if object_type == "skill" and "SKILL.md" not in file_paths:
            raise CorpusError("Skill holdout case requires SKILL.md")
        payload = json.dumps(
            normalized_files, ensure_ascii=False, sort_keys=True,
            separators=(",", ":")).encode()
        payload_digest = hashlib.sha256(payload).hexdigest()
        if payload_digest in seen_payloads:
            raise CorpusError("holdout source contains duplicate payload")
        seen_payloads.add(payload_digest)
        coverage[finding_type].append(assessment)
        normalized.append({
            **row,
            "files": normalized_files,
            "_index": index,
        })

    if set(coverage) != set(CATALOG):
        raise CorpusError("holdout source lacks a controlled Finding Type")
    for finding_type, assessments in coverage.items():
        if sorted(assessments) != ["absent", "absent", "present", "present"]:
            raise CorpusError(
                f"holdout source is not 2-present/2-absent: {finding_type}")
    return normalized


def _manifest(
        *, cases: Iterable[Dict[str, Any]], version_name: str,
        hidden_name: str, description: str) -> Dict[str, Any]:
    risk_ids = _preferred_risk_ids()
    rows = []
    for row in cases:
        index = row["_index"]
        case_root = f"{hidden_name}/holdout/case-{index:03d}"
        path = (
            f"{case_root}/prompt.txt"
            if row["objectType"] == "prompt" else case_root
        )
        manifest_case = {
            "caseId": (
                f"semantic-comparison-{version_name}-holdout-{index:03d}"),
            "objectType": row["objectType"],
            "language": row["language"],
            "path": path,
            "findingType": row["findingType"],
            "riskId": risk_ids[row["findingType"]],
            "authorAssessment": row["assessment"],
            "labelStatus": "provisional_single_review",
        }
        if row["objectType"] == "prompt":
            manifest_case["promptKind"] = row["promptKind"]
        rows.append(manifest_case)
    return {
        "schemaVersion": 1,
        "protocolId": COMPARISON_PROTOCOL_ID,
        "protocolVersion": COMPARISON_PROTOCOL_V5,
        "status": "hidden_holdout",
        "license": "Apache-2.0",
        "provenance": "verity_synthetic",
        "labelStatus": "provisional_single_review",
        "description": description.strip(),
        "evaluationPolicy": dict(PROTOCOL_V5_EVALUATION_POLICY),
        "cases": rows,
    }


def _payload_digests(path: Path) -> set[str]:
    return {
        case["payloadDigest"]
        for case in load_semantic_comparison_manifest(path)["cases"]
    }


def _assert_disjoint(
        manifest_path: Path, comparison_paths: Iterable[Path]) -> None:
    current = _payload_digests(manifest_path)
    for other_path in comparison_paths:
        if not other_path.is_file():
            raise CorpusError(f"disjoint manifest missing: {other_path}")
        overlap = current & _payload_digests(other_path)
        if overlap:
            raise CorpusError(
                f"holdout payload overlaps {other_path}: {len(overlap)} case(s)")


def _catalog_seeds(case: Dict[str, Any]):
    case_path = CORPUS_DIR / case["path"]
    if case["objectType"] == "prompt":
        snapshot, file_bytes = intake_text(
            case_path.read_text("utf-8"), prompt_kind=case["promptKind"])
        review = run_review(ReviewInputs("prompt", snapshot, file_bytes))
    else:
        snapshot, file_bytes = intake_directory(
            case_path, budget=IntakeBudget(
                max_files=64, max_file_size=512 * 1024,
                max_total_size=2 * 1024 * 1024))
        review = run_review(ReviewInputs(
            "skill", snapshot, file_bytes, profile="minimal"))
    return CATALOG[case["findingType"]][1](
        review_to_dict(review), file_bytes)


def _catalog_contract_report(
        manifest: Dict[str, Any]) -> Dict[str, Any]:
    rows = []
    counts = defaultdict(int)
    unreachable = []
    for case in manifest["cases"]:
        seeds = _catalog_seeds(case)
        hint_count = 0
        skipped_seed_count = 0
        for source, _, _ in seeds:
            hints = source.get("candidateHints") if isinstance(
                source, dict) else None
            if isinstance(hints, list):
                hint_count += sum(isinstance(item, dict) for item in hints)
            if (isinstance(source, dict)
                    and source.get("modelCandidatePolicy")
                    == "skip_without_catalog_hint"):
                skipped_seed_count += 1
        all_skipped = skipped_seed_count == len(seeds)
        assessment = case["authorAssessment"]
        if hint_count:
            route = (
                "catalog_hypothesis_present"
                if assessment == "present"
                else "validator_guarded_safe_case"
            )
        elif all_skipped:
            route = (
                "unreachable_positive"
                if assessment == "present"
                else "catalog_suppressed_safe_case"
            )
        else:
            route = (
                "model_fallback_positive"
                if assessment == "present"
                else "model_guarded_safe_case"
            )
        counts[route] += 1
        row = {
            "caseId": case["caseId"],
            "findingType": case["findingType"],
            "assessment": assessment,
            "seedCount": len(seeds),
            "candidateHintCount": hint_count,
            "modelSkippedSeedCount": skipped_seed_count,
            "route": route,
        }
        rows.append(row)
        if route == "unreachable_positive":
            unreachable.append(case["caseId"])
    return {
        "schemaVersion": 1,
        "protocolId": manifest["protocolId"],
        "protocolVersion": manifest["protocolVersion"],
        "corpusFingerprint": _corpus_fingerprint(manifest),
        "caseCount": len(rows),
        "routeCounts": dict(sorted(counts.items())),
        "unreachablePositiveCaseIds": unreachable,
        "cases": rows,
    }


def _require_frozen_catalog_contract(report: Dict[str, Any]) -> None:
    expected_per_class = report["caseCount"] // 2
    expected = {
        "catalog_hypothesis_present": expected_per_class,
        "catalog_suppressed_safe_case": expected_per_class,
    }
    if (report["routeCounts"] != expected
            or report["unreachablePositiveCaseIds"]):
        raise CorpusError(
            "holdout does not fully bind positive hypotheses and safe "
            "suppression to the catalog-first product path")


def _audit(args) -> int:
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = load_semantic_comparison_manifest(manifest_path)
    report = _catalog_contract_report(manifest)
    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(canonical_report_json(report), encoding="utf-8")
    print(json.dumps(report["routeCounts"], sort_keys=True))
    print(
        "unreachable positives: "
        f"{len(report['unreachablePositiveCaseIds'])}")
    _require_frozen_catalog_contract(report)
    return 0


def _compose(args) -> int:
    version_name = args.version_name
    if not VERSION_RE.fullmatch(version_name):
        raise CorpusError("version name must look like v6")
    output = Path(args.output).expanduser().resolve()
    if output.exists():
        raise CorpusError("holdout source output already exists")
    cases = []
    for raw_path in args.group:
        path = Path(raw_path).expanduser().resolve()
        group = _read_json_value(path, f"holdout group {path.name}")
        if not isinstance(group, list) or not group:
            raise CorpusError(f"holdout group must be a non-empty array: {path}")
        cases.extend(group)
    source = {
        "schemaVersion": 1,
        "versionName": version_name,
        "description": args.description.strip(),
        "cases": cases,
    }
    normalized = _validate_source(source, version_name)
    source["cases"] = [
        {key: value for key, value in row.items() if key != "_index"}
        for row in normalized
    ]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_report_json(source), encoding="utf-8")
    print(f"composed holdout source: {output}")
    print(f"cases: {len(source['cases'])}")
    print(f"Finding Types: {len(CATALOG)}")
    return 0


def _build(args) -> int:
    version_name = args.version_name
    if not VERSION_RE.fullmatch(version_name):
        raise CorpusError("version name must look like v6")
    source_path = Path(args.source).expanduser().resolve()
    manifest_out = Path(args.manifest_out).expanduser().resolve()
    hidden_root = Path(args.hidden_root).expanduser().resolve()
    corpus_root = CORPUS_DIR.resolve()
    try:
        hidden_root.relative_to(corpus_root)
    except ValueError as exc:
        raise CorpusError("hidden root must stay under the Corpus root") from exc
    if hidden_root.exists() or manifest_out.exists():
        raise CorpusError("holdout draft output already exists")
    source = _read_json(source_path, "holdout source")
    cases = _validate_source(source, version_name)
    hidden_name = hidden_root.relative_to(corpus_root).as_posix()
    manifest = _manifest(
        cases=cases, version_name=version_name, hidden_name=hidden_name,
        description=source["description"])

    hidden_root.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    temp_root = Path(tempfile.mkdtemp(
        prefix=f".{version_name}-holdout-build-",
        dir=hidden_root.parent))
    try:
        for row in cases:
            case_root = temp_root / "holdout" / f"case-{row['_index']:03d}"
            case_root.mkdir(parents=True)
            for file_info in row["files"]:
                destination = case_root / file_info["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(file_info["content"], encoding="utf-8")
        os.replace(temp_root, hidden_root)
    except Exception:
        shutil.rmtree(temp_root, ignore_errors=True)
        raise

    manifest_out.write_text(
        canonical_report_json(manifest), encoding="utf-8")
    loaded = load_semantic_comparison_manifest(manifest_out)
    checked = validate_semantic_comparison_seed_coverage(manifest_out)
    _assert_disjoint(
        manifest_out, [Path(item).expanduser().resolve()
                       for item in args.disjoint_manifest])
    print(f"built local holdout draft: {manifest_out}")
    print(f"cases: {len(loaded['cases'])}")
    print(f"extractor coverage: {checked}/{len(loaded['cases'])}")
    print("frozen: false")
    return 0


def _freeze(args) -> int:
    manifest_path = Path(args.manifest).expanduser().resolve()
    output_root = Path(args.output_root).expanduser().resolve()
    freeze_path = output_root / "freeze.json"
    if freeze_path.exists():
        raise CorpusError("holdout is already frozen")
    manifest = load_semantic_comparison_manifest(manifest_path)
    if manifest["protocolVersion"] != COMPARISON_PROTOCOL_V5:
        raise CorpusError("only protocol v5 holdouts can use this freezer")
    checked = validate_semantic_comparison_seed_coverage(manifest_path)
    catalog_contract = _catalog_contract_report(manifest)
    _require_frozen_catalog_contract(catalog_contract)
    _assert_disjoint(
        manifest_path, [Path(item).expanduser().resolve()
                        for item in args.disjoint_manifest])
    for system_id in SYSTEM_IDS:
        system_root = output_root / system_id
        if system_root.exists():
            raise CorpusError(f"packet output already exists: {system_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    for system_id in SYSTEM_IDS:
        packet, mapping = build_semantic_comparison_packet(
            system_id=system_id, seed=secrets.token_hex(32),
            manifest_path=manifest_path)
        system_root = output_root / system_id
        system_root.mkdir()
        (system_root / "packet.json").write_text(
            canonical_report_json(packet), encoding="utf-8")
        (system_root / "alias-map.json").write_text(
            canonical_report_json(mapping), encoding="utf-8")
    raw_manifest = manifest_path.read_bytes()
    freeze = {
        "schemaVersion": 1,
        "protocolId": COMPARISON_PROTOCOL_ID,
        "protocolVersion": COMPARISON_PROTOCOL_V5,
        "phase": "frozen_before_first_remote_observation",
        "postObservationTuningAllowed": False,
        "candidateStrategyBound": True,
        "labelQualityGateBound": True,
        "targetRiskPolicyBound": True,
        "evaluationPolicy": dict(PROTOCOL_V5_EVALUATION_POLICY),
        "caseCount": len(manifest["cases"]),
        "extractorCoverageCount": checked,
        "catalogContractRouteCounts": catalog_contract["routeCounts"],
        "catalogContractSha256": hashlib.sha256(
            canonical_report_json(catalog_contract).encode()).hexdigest(),
        "corpusFingerprint": _corpus_fingerprint(manifest),
        "manifestSha256": hashlib.sha256(raw_manifest).hexdigest(),
        "remotePayloadAuthorized": False,
        "remoteObservationsStarted": False,
        "frozenAtEpochSeconds": int(time.time()),
    }
    freeze_path.write_text(
        canonical_report_json(freeze), encoding="utf-8")
    print(f"froze local holdout: {freeze_path}")
    print(f"corpus fingerprint: {freeze['corpusFingerprint']}")
    print(f"packets: {len(SYSTEM_IDS)}")
    print("remote payload authorized: false")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    compose = sub.add_parser("compose")
    compose.add_argument("--group", action="append", required=True)
    compose.add_argument("--version-name", required=True)
    compose.add_argument("--description", required=True)
    compose.add_argument("--output", required=True)
    compose.set_defaults(handler=_compose)
    audit = sub.add_parser("audit")
    audit.add_argument("--manifest", required=True)
    audit.add_argument("--output")
    audit.set_defaults(handler=_audit)
    build = sub.add_parser("build")
    build.add_argument("--source", required=True)
    build.add_argument("--version-name", required=True)
    build.add_argument("--hidden-root", required=True)
    build.add_argument("--manifest-out", required=True)
    build.add_argument("--disjoint-manifest", action="append", default=[])
    build.set_defaults(handler=_build)
    freeze = sub.add_parser("freeze")
    freeze.add_argument("--manifest", required=True)
    freeze.add_argument("--output-root", required=True)
    freeze.add_argument("--disjoint-manifest", action="append", default=[])
    freeze.set_defaults(handler=_freeze)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except CorpusError as exc:
        print(f"semantic holdout refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
