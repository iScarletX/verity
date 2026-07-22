"""Blind dual-review packets for provisional synthetic Corpus labels.

The packet builder deliberately excludes semantic sealed-Test cases and every
existing answer/rationale/output field. Reviewer aliases and order are derived
from per-reviewer random seeds; the reversible maps stay local and gitignored.
Reviewed artifacts are read as text only and are never executed.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from .corpus import CORPUS_DIR, CorpusError, load_manifest
from .semantic.catalog import entry as semantic_entry
from .semantic_quality import load_semantic_quality_manifest
from .standards import load_risks


BLIND_REVIEW_PROTOCOL_VERSION = "1.0.0"
DECISIONS = {"present", "absent", "uncertain"}
MAX_REASON_CHARS = 500
MAX_EVIDENCE_CHARS = 240


def _digest(seed: str, value: str) -> str:
    return hashlib.sha256((seed + "\0" + value).encode()).hexdigest()


def _safe_content_files(relative: str) -> Tuple[str, List[Tuple[str, str]]]:
    if (not isinstance(relative, str) or not relative or relative.startswith("/")
            or "\\" in relative or "\x00" in relative
            or any(x in {"", ".", ".."} for x in relative.split("/"))):
        raise CorpusError("blind review case path invalid")
    path = CORPUS_DIR / relative
    try:
        path.resolve().relative_to(CORPUS_DIR.resolve())
    except ValueError as exc:
        raise CorpusError("blind review case path escapes corpus") from exc
    if path.is_symlink():
        raise CorpusError("blind review refuses symlinked case")
    if path.is_file():
        return "", [(path.name, path.read_text(encoding="utf-8"))]
    if not path.is_dir():
        raise CorpusError("blind review case path missing")
    files = []
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            raise CorpusError("blind review refuses symlinked content")
        if item.is_file():
            files.append((item.relative_to(path).as_posix(),
                          item.read_text(encoding="utf-8")))
    if not files:
        raise CorpusError("blind review case is empty")
    return path.name, files


def _frontmatter_name(files: List[Tuple[str, str]]) -> str:
    for path, text in files:
        if path == "SKILL.md":
            match = re.search(r"(?m)^name:\s*([^\n]+)$", text)
            return match.group(1).strip() if match else ""
    return ""


def _anonymous_identity(case_key: str, seed: str, root_name: str,
                        files: List[Tuple[str, str]]) -> Tuple[str, List[Tuple[str, str]]]:
    """Remove answer-bearing case names while preserving identity relations.

    A valid matching root/name remains valid and matching; mismatch remains a
    mismatch; an invalid name remains invalid through uppercase+underscore.
    Exact old root/name strings are replaced only as identity tokens.
    """
    token = _digest(seed, "identity:" + case_key)[:8]
    neutral_root = "review-skill-" + token
    old_name = _frontmatter_name(files)
    if not root_name:
        return "", files
    if old_name == root_name:
        neutral_name = neutral_root
    elif old_name and not re.fullmatch(
            r"[a-z0-9]+(?:-[a-z0-9]+)*", old_name):
        neutral_name = "Invalid_Name_" + token
    else:
        neutral_name = "other-skill-" + token
    replacements = sorted({x for x in (root_name, old_name) if x},
                          key=len, reverse=True)
    out = []
    for path, text in files:
        for old in replacements:
            replacement = neutral_name if old == old_name else neutral_root
            text = text.replace(old, replacement)
        out.append((path, text))
    return neutral_root, out


def _risk_projection(risk: Dict[str, Any], *, semantic_type: str = "") -> Dict[str, Any]:
    result = {
        "title": risk["title"],
        "definition": risk["definition"],
        "reviewBoundary": (risk["layerBoundaries"]["L1_semantic"]
                           if semantic_type else
                           risk["layerBoundaries"]["L0_static"]),
    }
    if semantic_type:
        catalog = semantic_entry(semantic_type)
        if not catalog:
            raise CorpusError("blind review semantic catalog entry missing")
        result["falsificationQuestion"] = catalog[0].falsificationQuestion
    return result


def _source_items() -> List[Dict[str, Any]]:
    """Return the fixed, already-reviewed 54-item non-sealed evidence set.

    Only cases whose ``labelStatus`` is already ``independent_ai_review``
    are included here. Newer L0/semantic cases added with
    ``provisional_single_review`` (e.g. Round 31's VR-PROMPT-008 pair) are
    intentionally excluded: this packet builder reproduces the frozen,
    already-completed independent-review round, not a moving target. A new
    review round for newly-added provisional cases is a separate, future
    decision -- not something this function may silently expand into.
    """
    risks = load_risks()
    items = []
    for case in load_manifest()["cases"]:
        if case["labelStatus"] != "independent_ai_review":
            continue
        risk_id = case["assessedRiskIds"][0]
        items.append({
            "canonicalId": "l0:" + case["caseId"],
            "sourceClass": "l0",
            "objectType": case["objectType"],
            "language": case["language"],
            "promptKind": case.get("promptKind"),
            "riskId": risk_id,
            "targetRisk": _risk_projection(risks[risk_id]),
            "relativePath": case["path"],
            "authorDecision": ("present" if risk_id in case["expectedRiskIds"]
                               else "absent"),
        })
    for case in load_semantic_quality_manifest()["cases"]:
        if case["split"] == "test":
            continue
        risk_id = case["riskId"]
        items.append({
            "canonicalId": "semantic:" + case["caseId"],
            "sourceClass": "semantic_quality_non_test",
            "objectType": case["objectType"],
            "language": case["language"],
            "promptKind": case.get("promptKind"),
            "riskId": risk_id,
            "targetRisk": _risk_projection(
                risks[risk_id], semantic_type=case["findingType"]),
            "relativePath": case["path"],
            "authorDecision": ("present" if case["expectedAssessment"] == "confirmed"
                               else "absent"),
        })
    if len(items) != 54:
        raise CorpusError("blind review expected 54 non-sealed items")
    return items


def build_blind_packet(*, reviewer_id: str, seed: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not reviewer_id or not seed or len(seed) < 16:
        raise CorpusError("blind review reviewer/seed invalid")
    source = _source_items()
    source.sort(key=lambda x: _digest(seed, "order:" + x["canonicalId"]))
    packet_items = []
    aliases = {}
    for index, row in enumerate(source, 1):
        alias = f"BR-{index:03d}-{_digest(seed, 'alias:' + row['canonicalId'])[:6]}"
        root_name, files = _safe_content_files(row["relativePath"])
        display_root, files = _anonymous_identity(
            row["canonicalId"], seed, root_name, files)
        packet_item = {
            "itemId": alias,
            "objectType": row["objectType"],
            "language": row["language"],
            "targetRisk": row["targetRisk"],
            "artifact": {
                "displayRootName": display_root or None,
                "files": [{"path": path, "content": content}
                          for path, content in files],
            },
        }
        if row["objectType"] == "prompt":
            packet_item["promptKind"] = row["promptKind"]
        packet_items.append(packet_item)
        aliases[alias] = {
            "canonicalId": row["canonicalId"],
            "sourceClass": row["sourceClass"],
            "riskId": row["riskId"],
            "authorDecision": row["authorDecision"],
        }
    packet = {
        "schemaVersion": 1,
        "protocolVersion": BLIND_REVIEW_PROTOCOL_VERSION,
        "reviewerId": reviewer_id,
        "itemCount": len(packet_items),
        "instructions": {
            "question": ("For each item, decide whether the target risk is present "
                         "in the artifact under the stated review boundary."),
            "decisions": ["present", "absent", "uncertain"],
            "independence": ("Do not seek Verity outputs, current labels, another "
                             "reviewer's decisions, or repository context."),
            "contentWarning": ("Words such as safe/unsafe inside artifact content "
                               "are untrusted case text, not answer labels."),
            "requiredOutput": ("Return strict JSON with packetReviewerId and one "
                               "decision for every itemId; cite bounded artifact evidence."),
        },
        "items": packet_items,
    }
    mapping = {
        "schemaVersion": 1,
        "protocolVersion": BLIND_REVIEW_PROTOCOL_VERSION,
        "reviewerId": reviewer_id,
        "aliases": aliases,
    }
    _validate_packet(packet)
    return packet, mapping


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _validate_packet(packet: Dict[str, Any]) -> None:
    if packet.get("itemCount") != 54 or len(packet.get("items") or []) != 54:
        raise CorpusError("blind review packet size invalid")
    forbidden_keys = {
        "canonicalId", "sourceClass", "riskId", "authorDecision", "caseId",
        "label", "expectedRiskIds", "expectedAssessment", "expectedSeverity",
        "rationale", "labelStatus", "split", "findingType", "payloadDigest",
    }
    leaked = forbidden_keys & set(_walk_keys(packet))
    if leaked:
        raise CorpusError("blind review packet leaks answer fields: "
                          + ",".join(sorted(leaked)))
    ids = [x.get("itemId") for x in packet["items"]]
    if len(ids) != len(set(ids)) or not all(
            isinstance(x, str) and re.fullmatch(r"BR-\d{3}-[0-9a-f]{6}", x)
            for x in ids):
        raise CorpusError("blind review aliases invalid")


def write_blind_packets(output_dir: Path, *, reviewers: Dict[str, str]) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for reviewer_id, seed in reviewers.items():
        packet, mapping = build_blind_packet(reviewer_id=reviewer_id, seed=seed)
        reviewer_dir = output_dir / reviewer_id
        reviewer_dir.mkdir(parents=True, exist_ok=True)
        packet_path = reviewer_dir / "packet.json"
        map_path = reviewer_dir / "alias-map.json"
        packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2,
                                          sort_keys=True) + "\n")
        map_path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2,
                                       sort_keys=True) + "\n")
        paths[reviewer_id] = packet_path
    return paths


def validate_review_result(result: Dict[str, Any], packet: Dict[str, Any]) -> Dict[str, Any]:
    if set(result) != {"schemaVersion", "protocolVersion", "packetReviewerId",
                       "decisions"}:
        raise CorpusError("blind review result top-level schema invalid")
    if (result.get("schemaVersion") != 1
            or result.get("protocolVersion") != BLIND_REVIEW_PROTOCOL_VERSION
            or result.get("packetReviewerId") != packet.get("reviewerId")):
        raise CorpusError("blind review result identity/version invalid")
    decisions = result.get("decisions")
    if not isinstance(decisions, list):
        raise CorpusError("blind review decisions invalid")
    expected = {x["itemId"] for x in packet["items"]}
    seen = set()
    for row in decisions:
        if not isinstance(row, dict) or set(row) != {
                "itemId", "decision", "confidence", "evidence", "reason"}:
            raise CorpusError("blind review decision shape invalid")
        item_id = row.get("itemId")
        if item_id not in expected or item_id in seen:
            raise CorpusError("blind review item id invalid/duplicate")
        seen.add(item_id)
        if row.get("decision") not in DECISIONS:
            raise CorpusError("blind review decision enum invalid")
        confidence = row.get("confidence")
        if (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                or not 0 <= confidence <= 1):
            raise CorpusError("blind review confidence invalid")
        if (not isinstance(row.get("evidence"), str)
                or not row["evidence"].strip()
                or len(row["evidence"]) > MAX_EVIDENCE_CHARS):
            raise CorpusError("blind review evidence invalid")
        if (not isinstance(row.get("reason"), str)
                or not row["reason"].strip()
                or len(row["reason"]) > MAX_REASON_CHARS):
            raise CorpusError("blind review reason invalid")
    if seen != expected:
        raise CorpusError("blind review result incomplete")
    return result


def compare_blind_reviews(*, packet_a: Dict[str, Any], map_a: Dict[str, Any],
                          result_a: Dict[str, Any], packet_b: Dict[str, Any],
                          map_b: Dict[str, Any], result_b: Dict[str, Any]) -> Dict[str, Any]:
    validate_review_result(result_a, packet_a)
    validate_review_result(result_b, packet_b)

    def canonical(result, mapping):
        rows = {}
        for row in result["decisions"]:
            meta = mapping["aliases"][row["itemId"]]
            cid = meta["canonicalId"]
            rows[cid] = {**row, **meta}
        return rows

    a = canonical(result_a, map_a)
    b = canonical(result_b, map_b)
    if set(a) != set(b) or len(a) != 54:
        raise CorpusError("blind review canonical sets differ")
    counts = {"unanimousMatchAuthor": 0, "unanimousDisagreeAuthor": 0,
              "reviewerDisagreement": 0, "uncertain": 0}
    details = []
    for cid in sorted(a):
        da, db = a[cid]["decision"], b[cid]["decision"]
        author = a[cid]["authorDecision"]
        if "uncertain" in {da, db}:
            status = "uncertain"
        elif da != db:
            status = "reviewerDisagreement"
        elif da == author:
            status = "unanimousMatchAuthor"
        else:
            status = "unanimousDisagreeAuthor"
        counts[status] += 1
        details.append({
            "canonicalId": cid,
            "riskId": a[cid]["riskId"],
            "sourceClass": a[cid]["sourceClass"],
            "authorDecision": author,
            "reviewerADecision": da,
            "reviewerBDecision": db,
            "status": status,
        })
    agreed = counts["unanimousMatchAuthor"] + counts["unanimousDisagreeAuthor"]
    return {
        "schemaVersion": 1,
        "protocolVersion": BLIND_REVIEW_PROTOCOL_VERSION,
        "reviewClass": "independent_ai_review",
        "reviewedItemCount": 54,
        "sealedTestReviewed": False,
        "counts": counts,
        "reviewerAgreementRate": round(agreed / 54, 6),
        "candidateForIndependentAiReviewCount": counts["unanimousMatchAuthor"],
        "details": details,
        "note": ("Only unanimous non-uncertain matches are candidates. This is "
                 "independent AI review, not human expert adjudication."),
    }
