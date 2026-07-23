"""Round 18 three-split semantic quality protocol and scrubbed metrics."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from verity.corpus import CorpusError
from verity.semantic.config import ProviderConfig, ProviderCredentials
from verity.semantic.provider import ProviderResponse
from verity.semantic_quality import (QUALITY_MANIFEST_PATH,
                                     PROTOCOL_V2_FINDING_TYPES,
                                     evaluate_semantic_model_quality,
                                     load_semantic_quality_manifest,
                                     validate_semantic_quality_seed_coverage)


SUBJECTS = {
    "semantic.prompt.instruction_conflict": {"conflictKind": "contradictory_directive"},
    "semantic.prompt.missing_output_contract": {"expectedFormat": "json"},
    "semantic.prompt.trust_boundary_ambiguity": {"boundaryKind": "retrieved_content"},
    "semantic.prompt.excessive_tool_scope": {"scopeKind": "unnecessary_tool"},
    "semantic.skill.declared_behavior_mismatch": {"mismatchKind": "capability_undeclared"},
    "semantic.skill.permission_capability_mismatch": {"mismatchKind": "undeclared_capability"},
    "semantic.skill.external_instruction_trust_gap": {"trustGapKind": "unverified_source"},
}


class Generator:
    def generate_candidates(self, *, call, request):
        evidence = request.get("evidence") or []
        return ProviderResponse(ok=True, payload={"candidates": [{
            "proposedCandidateId": "stub",
            "findingType": request["findingType"],
            "subject": SUBJECTS[request["findingType"]],
            "claim": "Controlled synthetic claim.",
            "evidenceIds": [x["evidenceId"] for x in evidence[:8]],
        }]})


class Validator:
    def __init__(self, decisions): self.decisions = iter(decisions)
    def validate_candidate(self, *, call, request):
        decision = next(self.decisions)
        if decision == "error":
            return ProviderResponse(ok=False, reason_code="synthetic_network_error")
        reason = ("evidence_supports_claim" if decision == "confirmed" else
                  "evidence_contradicts_claim" if decision == "rejected" else
                  "not_enough_evidence")
        return ProviderResponse(ok=True, payload={
            "candidateId": request["candidate"]["candidateId"],
            "decision": decision, "reasonCodes": [reason]})


def configs(monkeypatch):
    monkeypatch.setenv("VERITY_TEST_QUALITY_KEY", "VERITY_LOCAL_TEST_VALUE")
    cred = ProviderCredentials("VERITY_TEST_QUALITY_KEY")
    common = dict(provider_id="quality-stub", model_id="fixed-stub",
                  base_url="https://quality.example/v1", credentials=cred)
    return (ProviderConfig(role="candidate_generator", **common),
            ProviderConfig(role="validator", **common))


def decisions_for_split(split, repetitions):
    manifest = load_semantic_quality_manifest()
    out = []
    for case in manifest["cases"]:
        if case["split"] == split:
            out.extend([case["expectedAssessment"]] * repetitions)
    return out


def test_all_quality_cases_have_deterministic_semantic_seeds():
    assert validate_semantic_quality_seed_coverage() == 42


def test_manifest_has_disjoint_complete_three_split_pairs():
    manifest = load_semantic_quality_manifest()
    assert len(manifest["cases"]) == 42
    assert manifest["protocolVersion"] == "2.0.0"
    assert manifest["labelStatus"] == "mixed_independent_ai_and_provisional"
    ids = set()
    digests = set()
    for split in ("calibration", "selection", "test"):
        cases = [c for c in manifest["cases"] if c["split"] == split]
        assert len(cases) == 14
        expected_status = ("provisional_single_review" if split == "test"
                           else "independent_ai_review")
        assert {c["labelStatus"] for c in cases} == {expected_status}
        by_type = {}
        for case in cases:
            assert case["caseId"] not in ids; ids.add(case["caseId"])
            assert case["payloadDigest"] not in digests; digests.add(case["payloadDigest"])
            by_type.setdefault(case["findingType"], set()).add(case["expectedAssessment"])
        assert set(by_type) == set(PROTOCOL_V2_FINDING_TYPES)
        assert all(x == {"confirmed", "rejected"} for x in by_type.values())


def _write_manifest(tmp_path, mutate):
    data = json.loads(QUALITY_MANIFEST_PATH.read_text())
    mutate(data)
    path = tmp_path / "semantic_quality.json"
    path.write_text(json.dumps(data))
    return path


@pytest.mark.parametrize("mutate", [
    lambda d: d["cases"][0].update({"unknown": True}),
    lambda d: d["cases"][0].update({"split": "hidden"}),
    lambda d: d["cases"][0].update({"provenance": "internet"}),
    lambda d: d["cases"][1].update({"caseId": d["cases"][0]["caseId"]}),
    lambda d: d["cases"][1].update({"path": d["cases"][0]["path"]}),
])
def test_manifest_rejects_unknown_overlap_and_untrusted_provenance(tmp_path, mutate):
    with pytest.raises(CorpusError):
        load_semantic_quality_manifest(_write_manifest(tmp_path, mutate))


def test_perfect_stub_metrics_and_report_are_scrubbed(monkeypatch):
    gen_cfg, val_cfg = configs(monkeypatch)
    report = evaluate_semantic_model_quality(
        split="selection", repetitions=2, generator=Generator(),
        validator=Validator(decisions_for_split("selection", 2)),
        generator_config=gen_cfg, validator_config=val_cfg)
    assert report["modelQualityMeasured"] is True
    assert report["aggregateSafetyScore"] is None
    assert report["metrics"]["confusion"] == {"tp": 14, "fp": 0, "tn": 14, "fn": 0}
    assert report["metrics"]["errors"] == 0
    assert report["stability"] == {"stableCases": 14, "unstableCases": 0, "rate": 1.0}
    assert report["callCounts"] == {"generator": 28, "validator": 28}
    assert report["callBudget"] == {"configuredMax": 60,
                                    "requiredMaximum": 56}
    text = json.dumps(report, ensure_ascii=False)
    assert "compare two read-only notes" not in text
    assert "semantic-quality/selection" not in text
    assert "https://quality.example" not in text
    assert "VERITY_LOCAL_TEST_VALUE" not in text
    assert "VERITY_TEST_QUALITY_KEY" not in text
    assert "Controlled synthetic claim" not in text
    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(x) for x in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(x) for x in value)) if value else set()
        return set()
    assert "claim" not in keys(report)


def test_inconclusive_error_and_unstable_are_not_counted_safe(monkeypatch):
    gen_cfg, val_cfg = configs(monkeypatch)
    decisions = decisions_for_split("selection", 2)
    # First unsafe case becomes unstable confirmed/rejected; first safe case
    # is inconclusive twice; next unsafe case errors twice.
    decisions[0:2] = ["confirmed", "rejected"]
    decisions[2:4] = ["insufficient_evidence", "insufficient_evidence"]
    decisions[4:6] = ["error", "error"]
    report = evaluate_semantic_model_quality(
        split="selection", repetitions=2, generator=Generator(),
        validator=Validator(decisions), generator_config=gen_cfg,
        validator_config=val_cfg)
    assert report["stability"]["unstableCases"] == 1
    assert report["metrics"]["inconclusive"] == 2
    assert report["metrics"]["errors"] == 2
    assert report["metrics"]["confusion"]["fn"] == 1
    # Inconclusive safe decisions are not TN; errors are excluded.
    assert report["metrics"]["confusion"]["tn"] == 12


def test_call_budget_is_refused_before_any_provider_use(monkeypatch):
    gen_cfg, val_cfg = configs(monkeypatch)
    with pytest.raises(CorpusError, match="requires 56, configured 55"):
        evaluate_semantic_model_quality(
            split="selection", repetitions=2, generator=Generator(),
            validator=Validator([]), generator_config=gen_cfg,
            validator_config=val_cfg, max_total_calls=55)


def test_sealed_test_and_missing_credentials_are_refused(monkeypatch):
    gen_cfg, val_cfg = configs(monkeypatch)
    with pytest.raises(CorpusError, match="sealed test"):
        evaluate_semantic_model_quality(
            split="test", repetitions=2, generator=Generator(),
            validator=Validator([]), generator_config=gen_cfg,
            validator_config=val_cfg)
    monkeypatch.delenv("VERITY_TEST_QUALITY_KEY")
    with pytest.raises(CorpusError, match="credentials missing"):
        evaluate_semantic_model_quality(
            split="selection", repetitions=2, generator=Generator(),
            validator=Validator([]), generator_config=gen_cfg,
            validator_config=val_cfg)


class _EvalHandler(BaseHTTPRequestHandler):
    calls = 0
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        wire = json.loads(self.rfile.read(size))
        user = json.loads(wire["messages"][1]["content"])["input"]
        if "findingType" in user:
            evidence = user.get("evidence") or []
            payload = {"candidates": [{
                "proposedCandidateId": "local-e2e",
                "findingType": user["findingType"],
                "subject": SUBJECTS[user["findingType"]],
                "claim": "Local synthetic e2e claim.",
                "evidenceIds": [x["evidenceId"] for x in evidence[:8]],
            }]}
        else:
            payload = {"candidateId": user["candidate"]["candidateId"],
                       "decision": "confirmed",
                       "reasonCodes": ["evidence_supports_claim"]}
        raw = json.dumps({"choices": [{"message": {
            "content": json.dumps(payload)}}]}).encode()
        self.__class__.calls += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers(); self.wfile.write(raw)
    def log_message(self, *_): pass


def test_research_cli_end_to_end_with_local_stub_and_scrubbed_report(tmp_path):
    root = Path(__file__).resolve().parent.parent
    _EvalHandler.calls = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _EvalHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    output = tmp_path / "quality.json"
    secret = "VERITY_LOCAL_E2E_VALUE"
    env = dict(os.environ); env["VERITY_TEST_LOCAL_EVAL"] = secret
    try:
        proc = subprocess.run([
            sys.executable, "tools/run_semantic_model_eval.py",
            "--split", "selection", "--repetitions", "2",
            "--base-url", f"http://127.0.0.1:{server.server_port}/v1",
            "--generator-model", "local-generator",
            "--validator-model", "local-validator",
            "--api-key-env", "VERITY_TEST_LOCAL_EVAL",
            "--output", str(output),
        ], cwd=root, env=env, capture_output=True, text=True, timeout=60)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)
    assert proc.returncode == 0, proc.stderr
    assert _EvalHandler.calls == 56
    report_text = output.read_text()
    report = json.loads(report_text)
    assert report["modelQualityMeasured"] is True
    assert report["split"] == "selection"
    assert report["callCounts"] == {"generator": 28, "validator": 28}
    assert secret not in report_text + proc.stdout + proc.stderr
    assert "Local synthetic e2e claim" not in report_text
    assert "semantic-quality/selection" not in report_text
    assert str(tmp_path) not in report_text


def test_cli_without_key_refuses_before_output_or_network(tmp_path):
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ); env.pop("VERITY_MISSING_EVAL_KEY", None)
    output = tmp_path / "should-not-exist.json"
    proc = subprocess.run([
        sys.executable, "tools/run_semantic_model_eval.py",
        "--split", "selection", "--base-url", "https://127.0.0.1.invalid/v1",
        "--generator-model", "g", "--validator-model", "v",
        "--api-key-env", "VERITY_MISSING_EVAL_KEY", "--output", str(output),
    ], cwd=root, env=env, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 2
    assert "missing or empty" in proc.stderr
    assert not output.exists()
