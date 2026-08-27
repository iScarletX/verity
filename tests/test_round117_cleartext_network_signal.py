"""Round 117: new V2 sandbox signal -- sandbox_cleartext_network_attempt
(standing initiative #2).

Closes part of VR-SKILL-008's ("Weak cryptography or transport
protection") V2_sandbox=none gap. Its own layerBoundaries.V2_sandbox text
already promised this ("May observe connections but cannot prove
cryptographic design correctness alone"), but no sandbox signal previously
distinguished a plaintext-protocol port from an arbitrary attempt.

Unlike Round 116's cross-referencing signals, this is a narrower filter
over the SAME field the pre-existing sandbox_network_attempt signal reads
(networkAttempts) -- same shape as Round 102's sandbox_sensitive_path_read
(a path-pattern filter over fileEvents) and Round 111's
sandbox_fake_credential_read (a basename filter over fileEvents). Every
attempt observed here was already denied by the sandbox profile (no
network-allow clause exists), so `allowed` is always False by construction;
this signal instead asks whether the attempted port belongs to a small
fixed vocabulary of well-known plaintext protocols (scoring.py::
_CLEARTEXT_PORTS: 20/21 FTP, 23 Telnet, 25 SMTP, 80 HTTP, 110 POP3, 143
IMAP).

Deliberately weak evidence, disclosed honestly in risks.json's knownGaps:
a port number is not proof of the actual protocol spoken on it. A Skill
running TLS on 8080 or plaintext on 8443 is not observed either way. This
signal only recognizes the well-known-port case, same spirit as Round 102/
111's narrow, honest carve-outs.

No live sandbox execution anywhere in this file -- follows the Round 89/
102/111/114/116 convention of testing signal-hit behaviour directly
against synthetic SandboxObservation-shaped dicts.
"""
from __future__ import annotations

from verity.scoring import _sandbox_signal_hit, compute_score
from verity.sandbox.models import SANDBOX_SIGNAL_DETECTORS
from verity.standards import (load_detector_mappings, load_risks,
                              validate_runtime_detector_coverage)


DETECTOR_ID = "sandbox_cleartext_network_attempt"


def projection():
    return {
        "engine": "skill", "coverage": {"status": "sufficient", "reasonCodes": []},
        "findings": [], "ruleMatches": [], "evidences": [],
        "capabilities": {
            "static": {"status": "completed"},
            "semantic": {"status": "not_enabled"},
            "promptBlackbox": {"status": "not_enabled"},
            "skillSandbox": {"status": "completed"},
        },
    }


def sandbox_view(network_attempts=None):
    return {"status": "completed", "fileEvents": [],
            "networkAttempts": network_attempts or [], "subprocessAttempts": []}


class TestSignalHit:
    def test_http_port_80_is_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "evil.example", "port": 80, "allowed": False}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_ftp_port_21_is_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "evil.example", "port": 21, "allowed": False}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_ftp_data_port_20_is_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "evil.example", "port": 20, "allowed": False}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_telnet_port_23_is_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "evil.example", "port": 23, "allowed": False}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_smtp_port_25_is_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "evil.example", "port": 25, "allowed": False}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_pop3_port_110_is_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "evil.example", "port": 110, "allowed": False}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_imap_port_143_is_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "evil.example", "port": 143, "allowed": False}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_https_port_443_is_not_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "evil.example", "port": 443, "allowed": False}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_arbitrary_high_port_is_not_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "evil.example", "port": 8443, "allowed": False}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_missing_port_is_not_a_hit(self):
        # _record_network leaves port=None for non-AF_INET/AF_INET6
        # addresses (e.g. AF_UNIX) -- must never match by accident.
        sandbox = sandbox_view(network_attempts=[
            {"host": "/tmp/x/sock", "port": None, "allowed": False}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_no_network_attempts_is_not_a_hit(self):
        sandbox = sandbox_view()
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_mixed_attempts_one_cleartext_one_not_is_a_hit(self):
        sandbox = sandbox_view(network_attempts=[
            {"host": "a.example", "port": 443, "allowed": False},
            {"host": "b.example", "port": 80, "allowed": False}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True


def test_signal_is_registered_in_the_fixed_vocabulary():
    assert DETECTOR_ID in SANDBOX_SIGNAL_DETECTORS
    assert SANDBOX_SIGNAL_DETECTORS.count(DETECTOR_ID) == 1


def test_detector_mapping_registered_for_vr_skill_008():
    mappings = load_detector_mappings()
    key = ("sandbox_signal", DETECTOR_ID)
    assert key in mappings
    assert mappings[key]["riskIds"] == ["VR-SKILL-008"]
    assert mappings[key]["contribution"] == "signal"


def test_risk_coverage_flipped_to_signal():
    risks = load_risks()
    coverage = risks["VR-SKILL-008"]["currentCoverage"]
    assert coverage["V2_sandbox"] == "signal"
    # Unaffected layers stay exactly as they were before this round.
    assert coverage["L0_static"] == "signal"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"


def test_known_gaps_disclose_round_117_honestly():
    risks = load_risks()
    gaps = risks["VR-SKILL-008"]["knownGaps"]
    assert any("Round 117" in g for g in gaps)
    assert any("port number is not proof" in g for g in gaps)


def test_validate_runtime_detector_coverage_has_no_drift():
    validate_runtime_detector_coverage()


def test_cleartext_network_attempt_deducts_against_correct_risk_via_scoring():
    """End-to-end check that the new mapping row is actually wired.

    A port-80 attempt necessarily also trips the pre-existing bare
    sandbox_network_attempt signal (any attempt at all is already a hit
    for that one) -- SANDBOX_SIGNAL_DETECTORS are evaluated independently,
    so this legitimately produces two deductions against two different
    risks, not one, same as Round 114/116's overlap precedent.

    Declares WebFetch so Round 116's sandbox_undeclared_network_attempt
    signal does not also co-fire and add a third, out-of-scope deduction.
    """
    report = projection()
    report["artifactModel"] = {"manifest": {"permissions": ["WebFetch"]}}
    report["skillSandbox"] = sandbox_view(network_attempts=[
        {"host": "evil.example", "port": 80, "allowed": False}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {DETECTOR_ID, "sandbox_network_attempt"}
    deduction = by_detector[DETECTOR_ID]
    assert deduction["riskIds"] == ["VR-SKILL-008"]
    assert deduction["sourceLayer"] == "V2_sandbox"
    assert deduction["severity"] == "medium"


def test_encrypted_port_attempt_only_trips_the_base_signal():
    # Declares WebFetch so Round 116's sandbox_undeclared_network_attempt
    # signal does not also co-fire -- this test is scoped to the
    # cleartext-port signal's non-interference alone.
    report = projection()
    report["artifactModel"] = {"manifest": {"permissions": ["WebFetch"]}}
    report["skillSandbox"] = sandbox_view(network_attempts=[
        {"host": "api.example.com", "port": 443, "allowed": False}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {"sandbox_network_attempt"}


def test_no_attempts_at_all_produces_no_new_deductions():
    report = projection()
    report["skillSandbox"] = sandbox_view()
    score = compute_score(report)
    assert score["status"] == "available"
    assert score["deductions"] == []
