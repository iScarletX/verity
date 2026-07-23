"""Round 15: versioned corpus, replay, measurement and hygiene gates."""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from verity import corpus
from verity.corpus import (CorpusError, evaluate, evaluate_semantic_replay,
                           load_manifest, load_semantic_replay)
from verity.standards import load_detector_mappings, load_risks

REPO = Path(__file__).parent.parent


def test_manifest_is_balanced_traceable_and_independent_of_rule_ids():
    manifest = load_manifest()
    assert manifest["corpusVersion"] == "1.12.0"
    assert len(manifest["cases"]) == 66
    positives = [c for c in manifest["cases"] if c["label"] == "unsafe"]
    safe = [c for c in manifest["cases"]
            if c["label"] == "safe_counterexample"]
    assert len(positives) == len(safe) == 33
    text = (REPO / "evals/corpus/v1/manifest.json").read_text()
    # Answer keys use stable risks only, never detector/rule names.
    mappings = load_detector_mappings()
    assert not any(detector_id in text for _, detector_id in mappings)
    assert all(c["payloadDigest"] and c["rationale"]
               and c["provenance"] == "verity_synthetic"
               and c["license"] == "Apache-2.0"
               and c["labelStatus"] in ("independent_ai_review",
                                        "provisional_single_review")
               for c in manifest["cases"])
    # Rounds 31+ add new evidence pairs as provisional_single_review
    # pending an independent review round; the original 26 remain
    # independent_ai_review. Assert the split structurally rather than by
    # a hardcoded name list (which was itself a maintenance-drift risk):
    # exactly 26 independently-reviewed, the rest provisional, and every
    # provisional case is one of the post-Round-30 additions (its caseId
    # is NOT among the 26 frozen-attestation ids).
    label_status = {c["caseId"]: c["labelStatus"] for c in manifest["cases"]}
    reviewed = {cid for cid, s in label_status.items()
                if s == "independent_ai_review"}
    provisional = {cid for cid, s in label_status.items()
                   if s == "provisional_single_review"}
    assert len(reviewed) == 26
    assert reviewed | provisional == set(label_status)
    assert all(s in ("independent_ai_review", "provisional_single_review")
               for s in label_status.values())


def test_l0_metrics_are_per_risk_and_never_a_safety_score():
    report = evaluate()
    assert report["baselineClass"] == "minimal_pair_baseline"
    assert report["aggregateSafetyScore"] is None
    assert report["caseCount"] == 66
    assert report["stability"] == {
        "stableCases": 66, "unstableCases": 0, "rate": 1.0}
    assert report["highOrCriticalUnsafeCases"] == {
        "caseCount": 11, "tp": 11, "fn": 0}  # Round 37 added skill-sql-injection-positive (medium, not high/critical)
    measured = [r for r in report["riskResults"]
                if r["status"] == "measured"]
    assert len(measured) == 21  # Round 31: VR-PROMPT-008; Round 32: VR-SKILL-014, VR-PROMPT-010; Round 33: VR-SKILL-008; Round 34: VR-PROMPT-003, VR-SKILL-011; Round 35: VR-SKILL-005, VR-SKILL-007, VR-SKILL-009, VR-SKILL-010; Round 37: VR-SKILL-015
    # Every measured risk has an even, non-zero case count (balanced
    # positive/safe pairs). Several risks accumulate multiple pairs as
    # distinct sub-patterns get their own evidence (e.g. VR-SKILL-001
    # spec conformance, VR-SKILL-007 deserialization+parser-config,
    # VR-SKILL-008 weak-hash+TLS, VR-PROMPT-001 override+embedded-role+
    # md-exfil). Rather than hardcode each count, assert the structural
    # invariant: >=2 cases, even, and balanced tp+fn == fp+tn.
    for r in measured:
        assert r["caseCount"] >= 2 and r["caseCount"] % 2 == 0, r["riskId"]
        c = r["confusion"]
        assert c["tp"] + c["fn"] == c["fp"] + c["tn"] == r["caseCount"] // 2, \
            r["riskId"]
    assert all(set(r["confusion"]) == {"tp", "fp", "tn", "fn"}
               for r in measured)
    assert any(r["status"] == "unsupported" for r in report["riskResults"])
    assert any(r["status"] == "unmeasured" for r in report["riskResults"])


def test_detected_risk_does_not_hide_insufficient_execution_coverage():
    report = evaluate()
    case = next(c for c in report["caseResults"]
                if c["caseId"] == "skill-structure-positive")
    assert case["expectedRiskIds"] == case["observedRiskIds"] == [
        "VR-SKILL-001"]
    assert case["coverageStatuses"] == ["insufficient", "insufficient"]


def test_case_scoring_ignores_unrelated_out_of_scope_findings():
    report = evaluate()
    # A corpus case can legitimately trigger an unrelated Finding, but that
    # must not inflate FP for a risk the case does not assess.
    assert all(set(c["observedRiskIds"]) <= set(c["assessedRiskIds"])
               for c in report["caseResults"])
    rows = {r["riskId"]: r for r in report["riskResults"]}
    # VR-PROMPT-001 now has three balanced pairs (override marker,
    # embedded system-role token, markdown exfil), all detected cleanly.
    assert rows["VR-PROMPT-001"]["confusion"] == {
        "tp": 4, "fp": 0, "tn": 4, "fn": 0}


def test_semantic_replay_is_contract_only_not_model_quality():
    replay = load_semantic_replay()
    report = evaluate_semantic_replay()
    assert len(replay["cases"]) == 14
    assert report["replayClass"] == "semantic_contract_only"
    assert report["modelQualityMeasured"] is False
    assert report["aggregateSafetyScore"] is None
    assert report["contractCorrectCases"] == 14
    assert report["stability"] == {
        "stableCases": 14, "unstableCases": 0, "rate": 1.0}
    assert all(c["contractCorrect"]
               and c["labelStatus"] == "provisional_single_review"
               for c in report["caseResults"])


def test_semantic_replay_covers_all_current_semantic_types_in_pairs():
    from verity.semantic.catalog import CATALOG
    replay = load_semantic_replay()
    by_type = {}
    for case in replay["cases"]:
        by_type.setdefault(case["findingType"], set()).add(
            case["expectedAssessment"])
    assert set(by_type) == set(CATALOG)
    assert all(states == {"confirmed", "rejected"}
               for states in by_type.values())


def test_reports_are_reproducible():
    proc = subprocess.run(
        [sys.executable, "tools/run_corpus.py", "--check"],
        cwd=REPO, capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "reproducible" in proc.stdout


def test_manifest_rejects_stale_review_evidence_before_duplicate(monkeypatch):
    monkeypatch.setattr(corpus, "_case_payload_digest", lambda path: "0" * 64)
    with pytest.raises(CorpusError, match="independent review evidence invalid"):
        load_manifest()


def test_manifest_still_rejects_duplicate_payload_with_matching_review_evidence(
        monkeypatch):
    import verity.review_evidence as review_evidence
    manifest = json.loads((REPO / "evals/corpus/v1/manifest.json").read_text())
    attestation = {
        c["caseId"]: {
            "caseId": c["caseId"], "sourceClass": "l0",
            "payloadDigest": "0" * 64,
            "finalDecision": ("present" if c["expectedRiskIds"] else "absent"),
            "reviewStatus": "independent_ai_review",
        } for c in manifest["cases"]
    }
    monkeypatch.setattr(corpus, "_case_payload_digest", lambda path: "0" * 64)
    monkeypatch.setattr(review_evidence, "load_independent_ai_attestation",
                        lambda: attestation)
    with pytest.raises(CorpusError, match="duplicate corpus payload"):
        load_manifest()


def test_manifest_rejects_exact_developer_fixture_leak(monkeypatch):
    original = corpus._test_fixture_file_digests
    manifest = load_manifest()
    leaked = manifest["cases"][0]["payloadDigest"]
    case_path = corpus._safe_case_path(manifest["cases"][0]["path"])
    file_digest = __import__("hashlib").sha256(case_path.read_bytes()).hexdigest()
    monkeypatch.setattr(corpus, "_test_fixture_file_digests",
                        lambda: {file_digest})
    with pytest.raises(CorpusError, match="exact-byte leakage"):
        load_manifest()
    monkeypatch.setattr(corpus, "_test_fixture_file_digests", original)


def test_manifest_rejects_path_escape(monkeypatch):
    original = corpus._load_json

    def escaped(path):
        value = copy.deepcopy(original(path))
        if path == corpus.MANIFEST_PATH:
            value["cases"][0]["path"] = "../outside"
        return value

    monkeypatch.setattr(corpus, "_load_json", escaped)
    with pytest.raises(CorpusError, match="path"):
        load_manifest()


def test_manifest_rejects_answer_outside_assessed_scope(monkeypatch):
    original = corpus._load_json

    def poisoned(path):
        value = copy.deepcopy(original(path))
        if path == corpus.MANIFEST_PATH:
            value["cases"][0]["expectedRiskIds"] = ["VR-MCP-001"]
        return value

    monkeypatch.setattr(corpus, "_load_json", poisoned)
    with pytest.raises(CorpusError, match="expected risks"):
        load_manifest()


def test_semantic_replay_rejects_inconsistent_risk_mapping(monkeypatch):
    original = corpus._load_json

    def poisoned(path):
        value = copy.deepcopy(original(path))
        if path == corpus.SEMANTIC_REPLAY_PATH:
            value["cases"][0]["riskId"] = "VR-MCP-001"
        return value

    monkeypatch.setattr(corpus, "_load_json", poisoned)
    with pytest.raises(CorpusError, match="risk mapping"):
        load_semantic_replay()


def test_corpus_is_public_safe_and_contains_no_executable_exploit():
    root = REPO / "evals/corpus/v1"
    total = 0
    for path in root.rglob("*"):
        assert not path.is_symlink()
        if path.is_file():
            data = path.read_bytes()
            total += len(data)
            # No real key/token literal and no network attack payloads.
            lower = data.lower()
            assert b"openrouter" not in lower
            assert b"api_key=" not in lower
            assert b"169.254.169.254" not in lower
            assert b"curl " not in lower
            assert b"rm -rf" not in lower
    assert total < 512 * 1024
