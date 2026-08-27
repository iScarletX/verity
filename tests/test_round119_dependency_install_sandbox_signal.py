"""Round 119: new V2 sandbox signal -- sandbox_dependency_install_attempt
(standing initiative #2).

Closes part of VR-SKILL-003's ("Dependency drift, known vulnerabilities, or
unverifiable provenance") V2_sandbox=none gap. Its own
layerBoundaries.V2_sandbox text already promised this ("May observe
installation/runtime behavior but cannot prove supply-chain provenance
alone"), but no sandbox signal previously distinguished a package-manager
install attempt from an arbitrary subprocess spawn.

Screened alongside this candidate and declined: VR-GOV-001 (its V1_5_
blackbox/V2_sandbox boundary text is about the review PIPELINE's own
reporting honesty -- test-set/model/budget coverage, runtime policy,
teardown, unobserved behaviour -- not a property of the reviewed artifact a
corpus case or scenario probe can measure; Round 43 already documented this
exact "not artifact content" exclusion). Every remaining V1_5_blackbox=none
candidate was already explicitly declined in Rounds 108/110 (artifact-
specific defects a generic fixed-text probe cannot manufacture) or is a
VR-SKILL-* risk Round 111 already proved architecturally unreachable by
V1.5 black-box (``_run_prompt_blackbox_stage`` only runs when
``ri.engine == "prompt"``). This round therefore continues Round 111's own
pivot precedent: expand V2 sandbox instead.

Same shape as Round 102's sandbox_sensitive_path_read (a fixed-vocabulary
filter over an existing observation field) and Round 117's
sandbox_cleartext_network_attempt (a narrower filter layered on top of an
existing base signal, deliberately co-firing with it) -- a subprocessAttempts
entry whose argv0 basename is a well-known package-manager binary
(scoring.py::_DEPENDENCY_INSTALL_BINARIES) AND whose argvPreview also
contains an install-like subcommand token
(scoring.py::_DEPENDENCY_INSTALL_SUBCOMMANDS).

Deliberately weak evidence, disclosed honestly in risks.json's knownGaps:
this only proves an install was ATTEMPTED, never which package/version
landed, whether it succeeded, or whether it matches anything the Skill
also declares in a manifest -- same "narrow, honest carve-out" spirit as
every prior sandbox-signal round.

No live sandbox execution anywhere in this file -- follows the Round 89/
102/111/114/116/117 convention of testing signal-hit behaviour directly
against synthetic SandboxObservation-shaped dicts.
"""
from __future__ import annotations

from verity.scoring import _sandbox_signal_hit, compute_score
from verity.sandbox.models import SANDBOX_SIGNAL_DETECTORS
from verity.standards import (load_detector_mappings, load_risks,
                              validate_runtime_detector_coverage)


DETECTOR_ID = "sandbox_dependency_install_attempt"


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


def sandbox_view(subprocess_attempts=None):
    return {"status": "completed", "fileEvents": [], "networkAttempts": [],
            "subprocessAttempts": subprocess_attempts or []}


class TestSignalHit:
    def test_pip_install_is_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "pip", "argvPreview": ["pip", "install", "requests"]}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_pip3_absolute_path_install_is_a_hit(self):
        # argv0 basename must be matched, not full path -- a reviewed
        # script may spawn via an absolute interpreter/venv path.
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "/usr/local/bin/pip3",
             "argvPreview": ["pip3", "install", "flask"]}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_npm_install_is_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "npm", "argvPreview": ["npm", "install", "lodash"]}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_yarn_add_is_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "yarn", "argvPreview": ["yarn", "add", "axios"]}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_go_get_is_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "go", "argvPreview": ["go", "get", "example.com/pkg"]}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_conda_install_is_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "conda", "argvPreview": ["conda", "install", "numpy"]}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_pip_show_is_not_a_hit(self):
        # Package manager binary, but not an install-like subcommand.
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "pip", "argvPreview": ["pip", "show", "requests"]}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_cargo_build_is_not_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "cargo", "argvPreview": ["cargo", "build"]}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_unrelated_python_subprocess_is_not_a_hit(self):
        # Not a package-manager binary at all, even though "install"
        # appears in argvPreview -- the binary check must gate first.
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "python3", "argvPreview": ["python3", "install.py"]}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_no_subprocess_attempts_is_not_a_hit(self):
        sandbox = sandbox_view()
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_missing_argv0_is_not_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": None, "argvPreview": ["install"]}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_mixed_attempts_one_install_one_not_is_a_hit(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "ls", "argvPreview": ["ls", "-la"]},
            {"argv0": "pip", "argvPreview": ["pip", "install", "requests"]}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_subcommand_match_is_case_insensitive(self):
        sandbox = sandbox_view(subprocess_attempts=[
            {"argv0": "npm", "argvPreview": ["npm", "INSTALL", "lodash"]}])
        assert _sandbox_signal_hit(DETECTOR_ID, sandbox) is True


def test_signal_is_registered_in_the_fixed_vocabulary():
    assert DETECTOR_ID in SANDBOX_SIGNAL_DETECTORS
    assert SANDBOX_SIGNAL_DETECTORS.count(DETECTOR_ID) == 1


def test_detector_mapping_registered_for_vr_skill_003():
    mappings = load_detector_mappings()
    key = ("sandbox_signal", DETECTOR_ID)
    assert key in mappings
    assert mappings[key]["riskIds"] == ["VR-SKILL-003"]
    assert mappings[key]["contribution"] == "signal"


def test_risk_coverage_flipped_to_signal():
    risks = load_risks()
    coverage = risks["VR-SKILL-003"]["currentCoverage"]
    assert coverage["V2_sandbox"] == "signal"
    # Unaffected layers stay exactly as they were before this round.
    assert coverage["L0_static"] == "signal"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"


def test_known_gaps_disclose_round_119_honestly():
    risks = load_risks()
    gaps = risks["VR-SKILL-003"]["knownGaps"]
    assert any("Round 119" in g for g in gaps)
    assert any("only proves an install was attempted" in g for g in gaps)


def test_validate_runtime_detector_coverage_has_no_drift():
    validate_runtime_detector_coverage()


def test_dependency_install_attempt_deducts_against_correct_risk_via_scoring():
    """End-to-end check that the new mapping row is actually wired.

    A pip-install attempt necessarily also trips the pre-existing bare
    sandbox_subprocess_attempt signal (any subprocess spawn at all is
    already a hit for that one) -- SANDBOX_SIGNAL_DETECTORS are evaluated
    independently, so this legitimately produces two deductions against
    two different risks, not one, same as Round 114/116/117's overlap
    precedent.

    Declares Bash so Round 116's sandbox_undeclared_subprocess_attempt
    signal does not also co-fire and add a third, out-of-scope deduction.
    """
    report = projection()
    report["artifactModel"] = {"manifest": {"permissions": ["Bash"]}}
    report["skillSandbox"] = sandbox_view(subprocess_attempts=[
        {"argv0": "pip", "argvPreview": ["pip", "install", "requests"]}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {DETECTOR_ID, "sandbox_subprocess_attempt"}
    deduction = by_detector[DETECTOR_ID]
    assert deduction["riskIds"] == ["VR-SKILL-003"]
    assert deduction["sourceLayer"] == "V2_sandbox"
    assert deduction["severity"] == "medium"


def test_non_install_subprocess_only_trips_the_base_signal():
    # Declares Bash so Round 116's sandbox_undeclared_subprocess_attempt
    # signal does not also co-fire -- this test is scoped to the
    # dependency-install signal's non-interference alone.
    report = projection()
    report["artifactModel"] = {"manifest": {"permissions": ["Bash"]}}
    report["skillSandbox"] = sandbox_view(subprocess_attempts=[
        {"argv0": "ls", "argvPreview": ["ls", "-la"]}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {"sandbox_subprocess_attempt"}


def test_no_attempts_at_all_produces_no_new_deductions():
    report = projection()
    report["skillSandbox"] = sandbox_view()
    score = compute_score(report)
    assert score["status"] == "available"
    assert score["deductions"] == []
