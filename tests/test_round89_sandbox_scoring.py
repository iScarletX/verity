"""Round 89: V2 sandbox observations feed the numeric score.

Companion to test_round19_scoring.py's projection() template and
test_round88_blackbox_scoring.py's structure, extended with a skillSandbox
block. Blackbox behavior is untouched by this round and is not re-tested
here.
"""
from __future__ import annotations

import verity.scoring as scoring
from verity.scoring import compute_confidence, compute_score


def projection(coverage="sufficient"):
    return {
        "engine": "skill", "coverage": {"status": coverage, "reasonCodes": []},
        "findings": [], "ruleMatches": [], "evidences": [],
        "capabilities": {
            "static": {"status": "completed"},
            "semantic": {"status": "not_enabled"},
            "promptBlackbox": {"status": "not_enabled"},
            "skillSandbox": {"status": "not_enabled"},
        },
    }


def sandbox_view(status="completed", file_events=None, network_attempts=None,
                 subprocess_attempts=None):
    return {
        "status": status,
        "fileEvents": file_events or [],
        "networkAttempts": network_attempts or [],
        "subprocessAttempts": subprocess_attempts or [],
    }


def test_write_outside_tmpdir_is_deducted_with_correct_risk_and_layer():
    report = projection()
    report["skillSandbox"] = sandbox_view(
        file_events=[{"op": "write", "path": "/etc/passwd", "insideSandbox": False},
                     {"op": "write", "path": "/tmp/x/out.txt", "insideSandbox": True}])
    report["capabilities"]["skillSandbox"]["status"] = "completed"
    score = compute_score(report)
    assert score["status"] == "available"
    assert score["includedLayers"] == ["V2_sandbox"]
    assert score["evaluatedLayers"] == ["L0_static", "V2_sandbox"]
    assert len(score["deductions"]) == 1
    deduction = score["deductions"][0]
    assert deduction["severity"] == "high"
    assert deduction["riskIds"] == ["VR-SKILL-002"]
    assert deduction["sourceLayer"] == "V2_sandbox"
    assert deduction["detectorIds"] == ["sandbox_write_outside_tmpdir"]
    assert score["value"] <= 59  # high severity cap


def test_network_attempt_is_deducted_with_correct_risk_and_layer():
    # Declares WebFetch so Round 116's sandbox_undeclared_network_attempt
    # signal (see test_round116_declared_vs_observed_sandbox_signal.py)
    # does not also co-fire here -- this test is scoped to the pre-existing
    # unconditional signal alone.
    report = projection()
    report["artifactModel"] = {"manifest": {"permissions": ["WebFetch"]}}
    report["skillSandbox"] = sandbox_view(
        network_attempts=[{"host": "evil.example", "port": 443, "allowed": False}])
    report["capabilities"]["skillSandbox"]["status"] = "completed"
    score = compute_score(report)
    assert score["status"] == "available"
    assert len(score["deductions"]) == 1
    deduction = score["deductions"][0]
    assert deduction["severity"] == "high"
    assert deduction["riskIds"] == ["VR-SKILL-009"]
    assert deduction["detectorIds"] == ["sandbox_network_attempt"]
    assert score["value"] <= 59


def test_subprocess_attempt_is_deducted_with_correct_risk_and_layer():
    # Declares a Bash permission so Round 116's
    # sandbox_undeclared_subprocess_attempt signal (see
    # test_round116_declared_vs_observed_sandbox_signal.py) does not also
    # co-fire here -- this test is scoped to the pre-existing unconditional
    # signal alone.
    report = projection()
    report["artifactModel"] = {"manifest": {"permissions": ["Bash(curl:*)"]}}
    report["skillSandbox"] = sandbox_view(
        subprocess_attempts=[{"argv0": "curl", "argvPreview": ["curl", "evil.example"]}])
    report["capabilities"]["skillSandbox"]["status"] = "completed"
    score = compute_score(report)
    assert score["status"] == "available"
    assert len(score["deductions"]) == 1
    deduction = score["deductions"][0]
    assert deduction["severity"] == "medium"
    assert deduction["riskIds"] == ["VR-SKILL-006"]
    assert deduction["detectorIds"] == ["sandbox_subprocess_attempt"]
    assert score["value"] <= 79  # medium severity cap


def test_clean_completed_run_is_not_deducted():
    report = projection()
    report["skillSandbox"] = sandbox_view()
    report["capabilities"]["skillSandbox"]["status"] = "completed"
    score = compute_score(report)
    assert score["status"] == "available"
    assert score["value"] == 100
    assert score["deductions"] == []
    assert score["includedLayers"] == []
    assert score["evaluatedLayers"] == ["L0_static", "V2_sandbox"]


def test_bare_read_outside_tmpdir_is_not_deducted():
    # file-read* is unconditionally allowed; a read outside the tmpdir is
    # noise from the stdlib starting up, not a signal.
    report = projection()
    report["skillSandbox"] = sandbox_view(
        file_events=[{"op": "read", "path": "/usr/lib/python3.11/os.py",
                      "insideSandbox": False}])
    report["capabilities"]["skillSandbox"]["status"] = "completed"
    score = compute_score(report)
    assert score["status"] == "available"
    assert score["value"] == 100
    assert score["deductions"] == []


def test_requested_but_failed_sandbox_makes_score_unavailable():
    report = projection()
    report["skillSandbox"] = {"status": "failed", "reasonCode": "no_entry_point"}
    report["capabilities"]["skillSandbox"]["status"] = "failed"
    score = compute_score(report)
    assert score["status"] == "unavailable"
    assert score["value"] is None
    assert score["reasonCodes"] == ["sandbox_requested_but_incomplete"]
    confidence = compute_confidence(report)
    assert "v2_sandbox_requested_but_failed" in confidence["limitations"]
    assert "v2_sandbox_not_enabled_by_default" not in confidence["limitations"]


def test_completed_sandbox_carries_no_limitation_code():
    report = projection()
    report["skillSandbox"] = sandbox_view()
    report["capabilities"]["skillSandbox"]["status"] = "completed"
    confidence = compute_confidence(report)
    assert "v2_sandbox_requested_but_failed" not in confidence["limitations"]
    assert "v2_sandbox_not_enabled_by_default" not in confidence["limitations"]


def test_not_enabled_sandbox_keeps_prior_limitation_code():
    report = projection()
    confidence = compute_confidence(report)
    assert "v2_sandbox_not_enabled_by_default" in confidence["limitations"]
    assert "v2_sandbox_requested_but_failed" not in confidence["limitations"]
    # Blackbox limitation vocabulary is untouched by this round.
    assert "v1_5_blackbox_not_enabled_by_default" in confidence["limitations"]


def test_unmapped_sandbox_signal_makes_score_unavailable_not_silently_dropped(
        monkeypatch):
    # Sandbox detector ids are a fixed hand-designed tuple rather than
    # data-driven scenario ids, so simulating a future unmapped detector
    # requires patching the vocabulary and its hit-test rather than just
    # inventing an id in the observation payload.
    original_hit = scoring._sandbox_signal_hit

    def patched_hit(detector_id, sandbox, **kwargs):
        if detector_id == "sandbox_future_signal":
            return True
        return original_hit(detector_id, sandbox, **kwargs)

    monkeypatch.setattr(scoring, "_sandbox_signal_hit", patched_hit)
    monkeypatch.setattr(
        scoring, "SANDBOX_SIGNAL_DETECTORS",
        scoring.SANDBOX_SIGNAL_DETECTORS + ("sandbox_future_signal",))

    report = projection()
    report["skillSandbox"] = sandbox_view()
    report["capabilities"]["skillSandbox"]["status"] = "completed"
    score = compute_score(report)
    assert score["status"] == "unavailable"
    assert score["reasonCodes"][0] == "finding_mapping_incomplete"
    assert "unmapped_sandbox_finding:sandbox_future_signal" in score["reasonCodes"]
