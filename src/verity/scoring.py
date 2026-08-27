"""Deterministic explainable safety score and remediation projection.

Scoring is a policy projection, never a detector and never model-authored.  It
uses only completed-review Findings plus the independently validated detector
mapping.  Coverage failure or an unmapped Finding makes the numeric score
unavailable rather than optimistic.
"""
from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .engine import _SENSITIVE_PATH_PATTERNS
from .guidance import lookup as guidance_lookup
from .sandbox.models import SANDBOX_SIGNAL_DETECTORS
from .semantic.catalog import _permission_descriptor
from .standards import load_detector_mappings, summarize_coverage


POLICY_ID = "verity-safety-score"
POLICY_VERSION = "1.1.0"
CONFIDENCE_POLICY_ID = "verity-review-confidence"
# 1.1.0 (Round 74): promptBlackbox/skillSandbox limitation codes changed
# from "*_not_implemented" to "*_not_enabled_by_default" (or
# "*_results_not_scored" when a caller did enable them) now that both
# stages are integrated behind an explicit opt-in config -- see
# review.py's BlackboxConfig/SandboxConfig wiring. `execution` also gained
# promptBlackbox/skillSandbox keys.
# 1.2.0 (Round 88): V1.5 black-box scenario failures are now mapped into
# the numeric score and evaluatedLayers (see standards/detector_mappings.json's
# "blackbox_scenario" entries) when the stage actually completes. The
# confidence limitation code for a requested-but-failed blackbox stage
# changed from a blanket "results_not_scored" to "v1_5_blackbox_requested
# _but_failed" now that a completed run has no limitation at all. V2
# sandbox scoring integration remains deliberately out of scope -- sandbox
# observations have no pre-declared risk taxonomy yet (unlike
# ProbeScenario.risk_ids) -- so sandbox limitation codes are unchanged.
# 1.3.0 (Round 89): a completed V2 sandbox run's SandboxObservation is now
# also mapped into the numeric score, via a small hand-designed signal
# vocabulary (see sandbox/models.py::SANDBOX_SIGNAL_DETECTORS) rather than
# a pre-declared per-scenario taxonomy: a denied write outside the tmpdir,
# any denied network attempt, or any observed subprocess spawn. The
# skillSandbox confidence limitation mirrors blackbox's Round-88 change --
# "v2_sandbox_results_not_scored" is retired; a completed run now carries
# no sandbox limitation code, and "v2_sandbox_requested_but_failed" covers
# an enabled-but-incomplete stage.
# (Round 102): SANDBOX_SIGNAL_DETECTORS grew a fourth entry,
# sandbox_sensitive_path_read, reusing engine.py's _SENSITIVE_PATH_PATTERNS
# against fileEvents reads. No confidence-limitation vocabulary changed, so
# CONFIDENCE_POLICY_VERSION is not bumped.
# (Round 111): SANDBOX_SIGNAL_DETECTORS grew a fifth entry,
# sandbox_fake_credential_read, matching a fileEvents read of the fixed
# synthetic decoy runner.py now plants at the tmpdir root. No confidence-
# limitation vocabulary changed, so CONFIDENCE_POLICY_VERSION is not bumped.
# (Round 114): SANDBOX_SIGNAL_DETECTORS grew a sixth entry,
# sandbox_injected_content_propagation, matching the fixed synthetic canary
# marker runner.py embeds in a second planted decoy appearing inside a
# subprocessAttempts argvPreview item or a networkAttempts host. No
# confidence-limitation vocabulary changed, so CONFIDENCE_POLICY_VERSION is
# not bumped.
# (Round 116): SANDBOX_SIGNAL_DETECTORS grew a seventh and eighth entry,
# sandbox_undeclared_network_attempt / sandbox_undeclared_subprocess_attempt
# -- the first sandbox signals that cross-reference the Skill's manifest
# (declared permission families, via _declared_capability_families /
# semantic/catalog.py::_permission_descriptor) against the sandbox's own
# observation instead of reading SandboxObservation fields alone. No
# confidence-limitation vocabulary changed, so CONFIDENCE_POLICY_VERSION is
# not bumped.
# (Round 117): SANDBOX_SIGNAL_DETECTORS grew a ninth entry,
# sandbox_cleartext_network_attempt -- a narrower filter over the same
# networkAttempts field sandbox_network_attempt already reads, matching
# connection attempts to a small fixed vocabulary of well-known plaintext-
# protocol ports (_CLEARTEXT_PORTS) for VR-SKILL-008. No confidence-
# limitation vocabulary changed, so CONFIDENCE_POLICY_VERSION is not bumped.
# (Round 119): SANDBOX_SIGNAL_DETECTORS grew a tenth entry,
# sandbox_dependency_install_attempt -- a subprocessAttempts entry whose
# argv0 basename is a well-known package-manager binary and whose
# argvPreview also contains an install-like subcommand, for VR-SKILL-003.
# No confidence-limitation vocabulary changed, so CONFIDENCE_POLICY_VERSION
# is not bumped.
# (Round 124): SANDBOX_SIGNAL_DETECTORS grew an eleventh entry,
# sandbox_deserialization_effect -- a fixed synthetic canary marker
# (embedded in a pickle __reduce__ payload runner.py plants as a third
# decoy, "cache.pkl") appearing in a subprocessAttempts argvPreview item,
# for VR-SKILL-007. No confidence-limitation vocabulary changed, so
# CONFIDENCE_POLICY_VERSION is not bumped.
# (Round 130): SANDBOX_SIGNAL_DETECTORS grew a twelfth entry,
# sandbox_sql_injected_query -- the same fixed synthetic canary marker as
# sandbox_injected_content_propagation, this time matched against a new
# sqlAttempts observation field populated by a sqlite3.connect() factory
# override in _driver_source.py (not sys.addaudithook, since CPython only
# added sqlite3's own execute/executemany/executescript audit events in
# Python 3.12, and this sandbox supports 3.9+), for VR-SKILL-015. No
# confidence-limitation vocabulary changed, so CONFIDENCE_POLICY_VERSION is
# not bumped.
# 1.4.0 (Harness adapter Task 3): the off-by-default agent-instruction
# runtime is a fifth execution axis. Completed bounded signals now feed the
# score; not-enabled and requested-but-failed states have explicit confidence
# limitations.
CONFIDENCE_POLICY_VERSION = "1.4.0"

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEVERITY_WEIGHTS = {"critical": 45, "high": 25, "medium": 10, "low": 3}
SEVERITY_CAPS = {"critical": 39, "high": 59, "medium": 79, "low": 99}
# Integer percentages.  The fourth and later duplicate root causes do not
# deduct further points, but remain visible remediation items.
DUPLICATE_FACTORS = (100, 50, 25)

# Fixed per-signal severity for SANDBOX_SIGNAL_DETECTORS -- there is no
# per-event severity source (unlike ProbeScenario.severity for blackbox), so
# this is a hand-designed policy choice. The two Seatbelt-denied signals
# (write outside the tmpdir, any network attempt) are unambiguous escape
# attempts and rated "high"; a subprocess spawn actually succeeds under the
# profile and is a weaker signal on its own (no argv-intent classification),
# rated "medium". A read of a well-known sensitive host path (Round 102) is
# rated "high" to match the equivalent L0 static rule
# (skill_sensitive_path_access's defaultSeverity) that flags the same path
# vocabulary in Skill text. A read of the planted fake-credential decoy
# (Round 111) is also rated "high", to match VR-SKILL-011's existing L0
# rules (skill.gitleaks_finding / skill.fake_secret_fixture both use
# defaultSeverity="high") for the same risk. Injected-content propagation
# into a subprocess/network sink (Round 114) is rated "high" to match
# VR-SKILL-005's existing L0/L1 detectors (skill.manifest_external_
# instructions / semantic.skill.external_instruction_trust_gap both use
# defaultSeverity="high") for the same risk. The two undeclared-capability
# signals (Round 116) are rated "high" even though the static counterpart
# (semantic.skill.permission_capability_mismatch) uses "medium" -- a runtime-
# confirmed attempt is stronger evidence than a static AST call-site that may
# never execute, and sandbox_undeclared_network_attempt additionally implies
# the base sandbox_network_attempt ("high") always co-fires with it.
# sandbox_cleartext_network_attempt (Round 117) is rated "medium", lower than
# the other network signals: a well-known plaintext port is suggestive but
# not proof of an actual insecure protocol exchange (see sandbox/models.py's
# design comment), qualitatively weaker evidence than a denied-by-profile
# escape attempt or a confirmed undeclared capability.
# sandbox_dependency_install_attempt (Round 119) is rated "medium", matching
# VR-SKILL-003's existing L0/L1 detectors (skill.manifest_unpinned_dependency
# / semantic.skill.dependency_provenance_claim_gap both use
# defaultSeverity="medium") -- an ad-hoc runtime install is meaningful but,
# like those two, only proves an unverifiable-provenance *opportunity*, not
# a confirmed vulnerable or malicious package.
# sandbox_deserialization_effect (Round 124) is rated "high": unlike the
# read-only decoy signals above, this one proves the reviewed Skill's own
# code actually deserialized attacker-shaped pickle bytes and let the
# embedded call execute -- a real, observed code-execution effect, not
# merely an opportunity for one, so it is rated at least as seriously as
# sandbox_fake_credential_read/sandbox_injected_content_propagation.
# sandbox_sql_injected_query (Round 130) is rated "high", the same tier as
# sandbox_injected_content_propagation/sandbox_deserialization_effect: it
# proves the canary actually reached raw SQL statement text passed to a
# database driver, a real observed effect rather than a bare read.
_SANDBOX_SIGNAL_SEVERITY = {
    "sandbox_write_outside_tmpdir": "high",
    "sandbox_network_attempt": "high",
    "sandbox_subprocess_attempt": "medium",
    "sandbox_sensitive_path_read": "high",
    "sandbox_fake_credential_read": "high",
    "sandbox_injected_content_propagation": "high",
    "sandbox_undeclared_network_attempt": "high",
    "sandbox_undeclared_subprocess_attempt": "high",
    "sandbox_cleartext_network_attempt": "medium",
    "sandbox_dependency_install_attempt": "medium",
    "sandbox_deserialization_effect": "high",
    "sandbox_sql_injected_query": "high",
}

_AGENT_RUNTIME_SIGNAL_SEVERITY = {
    "agent_runtime_sensitive_read_attempt": "high",
    "agent_runtime_network_attempt": "medium",
    "agent_runtime_shell_attempt": "high",
    "agent_runtime_canary_exfiltration_attempt": "high",
}

# Basename of the fixed synthetic decoy runner.py's _stage_fake_credential_
# decoy plants at the tmpdir root before every run (Round 111). Matched by
# basename, not full path, since a reviewed script may open it via a
# relative ("credentials.json") or absolute path -- either way fileEvents
# records whatever string the script itself passed to open().
_FAKE_CREDENTIAL_DECOY_BASENAME = "credentials.json"

# Canary marker embedded in the fixed synthetic decoy runner.py's
# _stage_injected_content_decoy plants at the tmpdir root before every run
# (Round 114). Matched as a case-insensitive substring, not an exact-equality
# check, since a reviewed script may embed it inside a larger constructed
# command or URL (e.g. "curl https://<marker>/status") rather than passing it
# verbatim as a standalone token.
_INJECTED_CONTENT_CANARY = "verity-injected-content-canary-a91f7d3c.invalid"

# Well-known ports whose registered protocol has no transport encryption
# (Round 117). A port number alone is not proof of the actual protocol
# spoken on it -- see sandbox/models.py's design comment for
# sandbox_cleartext_network_attempt -- so this is a narrow, honest signal,
# not a definitive cleartext-traffic finding.
_CLEARTEXT_PORTS = frozenset({20, 21, 23, 25, 80, 110, 143})

# Well-known package-manager binaries and install-like subcommands (Round
# 119). Matched against argv0's basename and argvPreview respectively -- a
# fixed, narrow vocabulary, not general command-intent classification. `go`
# and `gem`/`cargo` are included for their `get`/`install` subcommands only;
# a bare `go build` or `cargo run` does not match because the subcommand
# check still requires one of _DEPENDENCY_INSTALL_SUBCOMMANDS to appear.
_DEPENDENCY_INSTALL_BINARIES = frozenset({
    "pip", "pip2", "pip3", "easy_install",
    "npm", "npx", "yarn", "pnpm",
    "conda", "mamba", "gem", "cargo", "go",
})
_DEPENDENCY_INSTALL_SUBCOMMANDS = frozenset({"install", "add", "get"})

# Canary marker embedded in the fixed synthetic pickle decoy runner.py's
# _stage_deserialization_effect_decoy plants at the tmpdir root before every
# run (Round 124). Duplicated here rather than imported from runner.py, the
# same layering choice already made for _INJECTED_CONTENT_CANARY above --
# scoring.py must never depend on the module that actually spawns processes.
# Matched as a case-insensitive substring against subprocessAttempts
# argvPreview items, mirroring sandbox_injected_content_propagation's check
# exactly: this marker can only appear there if the planted "cache.pkl"
# decoy was actually unpickled and its embedded os.system call executed.
_DESERIALIZATION_CANARY = "verity-deserialization-canary-c74b1e02"


def _declared_capability_families(manifest: Dict[str, Any]) -> Set[str]:
    """Which of the two runtime-observable families the manifest declares.

    Reuses semantic/catalog.py::_permission_descriptor so this runtime
    comparison never drifts from the static
    semantic.skill.permission_capability_mismatch comparison -- including
    that function's existing precedent of NOT treating a bare "*" wildcard
    as declaring every family (VR-SKILL-004 is about overbroad permissions,
    so "*" staying unmatched there is intended, not a gap).
    """
    families: Set[str] = set()
    for permission in manifest.get("permissions") or []:
        if not isinstance(permission, str):
            continue
        family, _target = _permission_descriptor(permission)
        if family in {"network_access", "process_execution"}:
            families.add(family)
    return families


def _sandbox_signal_hit(detector_id: str, sandbox: Dict[str, Any], *,
                        declared_families: Optional[Set[str]] = None) -> bool:
    if detector_id == "sandbox_undeclared_network_attempt":
        return (bool(sandbox.get("networkAttempts"))
                and "network_access" not in (declared_families or set()))
    if detector_id == "sandbox_undeclared_subprocess_attempt":
        return (bool(sandbox.get("subprocessAttempts"))
                and "process_execution" not in (declared_families or set()))
    if detector_id == "sandbox_write_outside_tmpdir":
        return any(e.get("op") == "write" and e.get("insideSandbox") is False
                   for e in sandbox.get("fileEvents") or [])
    if detector_id == "sandbox_network_attempt":
        return bool(sandbox.get("networkAttempts"))
    if detector_id == "sandbox_subprocess_attempt":
        return bool(sandbox.get("subprocessAttempts"))
    if detector_id == "sandbox_sensitive_path_read":
        return any(
            e.get("op") == "read" and any(
                pat.search(str(e.get("path", "")).encode())
                for pat in _SENSITIVE_PATH_PATTERNS
            )
            for e in sandbox.get("fileEvents") or []
        )
    if detector_id == "sandbox_fake_credential_read":
        return any(
            e.get("op") == "read" and e.get("insideSandbox") is True
            and os.path.basename(str(e.get("path", ""))) == _FAKE_CREDENTIAL_DECOY_BASENAME
            for e in sandbox.get("fileEvents") or []
        )
    if detector_id == "sandbox_injected_content_propagation":
        marker = _INJECTED_CONTENT_CANARY.lower()
        for e in sandbox.get("networkAttempts") or []:
            if marker in str(e.get("host", "")).lower():
                return True
        for e in sandbox.get("subprocessAttempts") or []:
            if any(marker in str(item).lower() for item in e.get("argvPreview") or []):
                return True
        return False
    if detector_id == "sandbox_cleartext_network_attempt":
        return any(e.get("port") in _CLEARTEXT_PORTS
                   for e in sandbox.get("networkAttempts") or [])
    if detector_id == "sandbox_dependency_install_attempt":
        for e in sandbox.get("subprocessAttempts") or []:
            argv0 = os.path.basename(str(e.get("argv0") or ""))
            if argv0 not in _DEPENDENCY_INSTALL_BINARIES:
                continue
            preview = [str(item).lower() for item in e.get("argvPreview") or []]
            if any(token in _DEPENDENCY_INSTALL_SUBCOMMANDS for token in preview):
                return True
        return False
    if detector_id == "sandbox_deserialization_effect":
        marker = _DESERIALIZATION_CANARY.lower()
        for e in sandbox.get("subprocessAttempts") or []:
            if any(marker in str(item).lower() for item in e.get("argvPreview") or []):
                return True
        return False
    if detector_id == "sandbox_sql_injected_query":
        marker = _INJECTED_CONTENT_CANARY.lower()
        return any(marker in str(e.get("statement", "")).lower()
                   for e in sandbox.get("sqlAttempts") or [])
    return False


def _unavailable(reason: str) -> Dict[str, Any]:
    return {
        "status": "unavailable", "value": None,
        "policyId": POLICY_ID, "policyVersion": POLICY_VERSION,
        "reasonCodes": [reason], "highestSeverity": None,
        "baseScore": 100, "deductionTotal": None, "severityCap": None,
        "deductions": [], "includedLayers": [], "evaluatedLayers": [],
    }


def _agent_runtime_score_state(review: Dict[str, Any]) -> str:
    """Reconcile the runtime view, capability, and exact selected plan item."""
    runtime = review.get("agentInstructionRuntime")
    runtime_status = runtime.get("status") if type(runtime) is dict else None
    capabilities = review.get("capabilities")
    if type(capabilities) is not dict:
        capabilities = {}
    capability = capabilities.get("agentInstructionRuntime")
    capability_status = (
        capability.get("status") if type(capability) is dict else None
    )
    dynamic_plan = review.get("dynamicPlan")
    items = dynamic_plan.get("items") if type(dynamic_plan) is dict else None
    if type(items) is not list:
        items = []
    exact_items = [
        item
        for item in items
        if type(item) is dict
        and item.get("check_id") == "agent_instruction.runtime"
        and item.get("stage") == "agent_runtime"
    ]
    selected_items = [
        item for item in exact_items if item.get("status") == "selected"
    ]

    if (
        runtime_status == "completed"
        and capability_status == "completed"
        and len(exact_items) == 1
        and len(selected_items) == 1
    ):
        return "completed"
    if len(selected_items) == 1 and runtime_status != "completed":
        return "requested_incomplete"
    if runtime_status == "completed" or capability_status == "completed":
        return "plan_inconsistent"
    if selected_items:
        return "requested_incomplete"
    return "legacy"


def mapped_finding_rows(
        review: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Shared read-only adapter from layer outputs to unified risk rows."""
    mappings = load_detector_mappings()
    event_to_rule = {e.get("eventId"): e.get("ruleId")
                     for e in review.get("ruleMatches") or []}
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    for finding in review.get("findings") or []:
        event_ids = (finding.get("origin") or {}).get("ruleMatchEventIds") or []
        risk_ids = set()
        rule_ids = set()
        for event_id in event_ids:
            rule_id = event_to_rule.get(event_id)
            if rule_id:
                rule_ids.add(rule_id)
                mapping = mappings.get(("deterministic_rule", rule_id))
                if mapping:
                    risk_ids.update(mapping["riskIds"])
        if not risk_ids:
            errors.append("unmapped_finding:" + str(finding.get("findingId", "")))
            continue
        rows.append({"finding": finding, "riskIds": sorted(risk_ids),
                     "detectorIds": sorted(rule_ids), "layer": "L0_static"})

    semantic = review.get("semantic") or {}
    if semantic.get("status") == "completed":
        for finding in semantic.get("findings") or []:
            finding_type = finding.get("findingType")
            mapping = mappings.get(("semantic_finding_type", finding_type))
            if not mapping:
                errors.append("unmapped_semantic_finding:" + str(
                    finding.get("findingId", "")))
                continue
            rows.append({"finding": finding,
                         "riskIds": sorted(mapping["riskIds"]),
                         "detectorIds": [finding_type],
                         "layer": "L1_semantic"})

    blackbox = review.get("promptBlackbox") or {}
    generated_check_ids = {
        item.get("check_id")
        for item in (review.get("dynamicPlan") or {}).get("items") or []
        if item.get("stage") == "prompt_blackbox"
        and item.get("scenario_id") is None
    }
    blackbox_status = blackbox.get("status")
    if blackbox_status in {"completed", "failed"}:
        for scenario_result in blackbox.get("scenarioResults") or []:
            if not isinstance(scenario_result, dict):
                continue
            # A failed stage can retain a completed unsafe scenario only when
            # the report boundary authenticated its complete runner shape.
            # ``review_to_dict`` recomputes ``definitive`` and ignores any
            # same-named value supplied in the raw stage dictionary.
            if (
                blackbox_status == "failed"
                and scenario_result.get("definitive") is not True
            ):
                continue
            scenario_id = scenario_result.get("scenario_id")
            probe_results = scenario_result.get("probe_results") or []
            total = len(probe_results)
            failed_count = sum(1 for p in probe_results if p.get("safe") is False)
            error_count = sum(1 for p in probe_results if p.get("safe") is None)
            # Recomputed from raw probe results, not a "verdict" key: the
            # source ScenarioResult.verdict is a @property, so
            # dataclasses.asdict() never serializes it into this JSON.
            oracle_outcome = (scenario_result.get("oracle_result") or {}).get(
                "outcome")
            if oracle_outcome in {"passed", "failed"}:
                verdict = oracle_outcome
            elif oracle_outcome in {"insufficient_evidence", "unavailable"}:
                verdict = "partial"
            elif total == 0 or error_count == total:
                verdict = "error"
            elif failed_count > 0:
                verdict = "failed"
            elif error_count > 0:
                verdict = "partial"
            else:
                verdict = "passed"
            if verdict != "failed":
                continue
            mapping = mappings.get(("blackbox_scenario", scenario_id))
            if not mapping:
                if scenario_id in generated_check_ids:
                    # Artifact-specific functional checks are projected as
                    # issues through their versioned dynamic-plan risk ids;
                    # they do not alter the legacy safety score.
                    continue
                errors.append("unmapped_blackbox_finding:" + str(scenario_id))
                continue
            finding = {
                "findingId": "blackbox:" + str(scenario_id),
                "findingType": scenario_id,
                "severity": scenario_result.get("severity"),
                "subjectKey": scenario_id,
            }
            rows.append({"finding": finding,
                         "riskIds": sorted(mapping["riskIds"]),
                         "detectorIds": [scenario_id],
                         "layer": "V1_5_blackbox"})

    sandbox = review.get("skillSandbox") or {}
    if sandbox.get("status") == "completed":
        manifest = (review.get("artifactModel") or {}).get("manifest") or {}
        declared_families = _declared_capability_families(manifest)
        for detector_id in SANDBOX_SIGNAL_DETECTORS:
            if not _sandbox_signal_hit(detector_id, sandbox,
                                       declared_families=declared_families):
                continue
            mapping = mappings.get(("sandbox_signal", detector_id))
            if not mapping:
                errors.append("unmapped_sandbox_finding:" + detector_id)
                continue
            finding = {
                "findingId": "sandbox:" + detector_id,
                "findingType": detector_id,
                "severity": _SANDBOX_SIGNAL_SEVERITY[detector_id],
                "subjectKey": detector_id,
            }
            rows.append({"finding": finding,
                         "riskIds": sorted(mapping["riskIds"]),
                         "detectorIds": [detector_id],
                         "layer": "V2_sandbox"})

    if _agent_runtime_score_state(review) == "completed":
        # Import only after the top-level view, capability, and exact plan item
        # all agree. The default deterministic path must not import the
        # agent-runtime package (whose initializer imports the runner).
        from .agent_runtime.models import (
            AGENT_RUNTIME_SIGNAL_DETECTORS,
            agent_runtime_signal_hits,
        )

        runtime = review.get("agentInstructionRuntime")
        hits = agent_runtime_signal_hits(runtime)
        for detector_id in AGENT_RUNTIME_SIGNAL_DETECTORS:
            if hits[detector_id] is not True:
                continue
            mapping = mappings.get(("agent_runtime_signal", detector_id))
            if not mapping:
                errors.append(
                    "unmapped_agent_runtime_finding:" + detector_id
                )
                continue
            finding = {
                "findingId": "agent-runtime:" + detector_id,
                "findingType": detector_id,
                "severity": _AGENT_RUNTIME_SIGNAL_SEVERITY[detector_id],
                "subjectKey": detector_id,
            }
            rows.append({
                "finding": finding,
                "riskIds": sorted(mapping["riskIds"]),
                "detectorIds": [detector_id],
                "layer": "V2_agent_runtime",
            })
    return rows, errors


# Backward-compatible private name for older internal callers.
_mapped_findings = mapped_finding_rows


def _ceil_percent(value: int, percent: int) -> int:
    return (value * percent + 99) // 100


def compute_score(review: Dict[str, Any]) -> Dict[str, Any]:
    coverage = review.get("coverage") or {}
    if coverage.get("status") != "sufficient":
        return _unavailable("coverage_insufficient")
    semantic = review.get("semantic")
    if (
        isinstance(semantic, dict)
        and semantic.get("status") not in {None, "off", "completed"}
    ):
        return _unavailable("semantic_requested_but_incomplete")
    blackbox = review.get("promptBlackbox")
    if (
        isinstance(blackbox, dict)
        and blackbox.get("status") not in {"not_enabled", "completed"}
    ):
        return _unavailable("blackbox_requested_but_incomplete")
    sandbox = review.get("skillSandbox")
    if (
        isinstance(sandbox, dict)
        and sandbox.get("status") not in {"not_enabled", "completed"}
    ):
        return _unavailable("sandbox_requested_but_incomplete")
    agent_runtime_state = _agent_runtime_score_state(review)
    if agent_runtime_state == "requested_incomplete":
        return _unavailable("agent_runtime_requested_but_incomplete")
    if agent_runtime_state == "plan_inconsistent":
        return _unavailable("agent_runtime_plan_inconsistent")
    rows, errors = mapped_finding_rows(review)
    if errors:
        result = _unavailable("finding_mapping_incomplete")
        result["reasonCodes"] = ["finding_mapping_incomplete", *sorted(errors)]
        return result

    rows.sort(key=lambda row: (
        SEVERITY_ORDER.get(row["finding"].get("severity"), 99),
        row["riskIds"][0], str(row["finding"].get("findingId", ""))))
    root_counts: Dict[Tuple[str, str], int] = defaultdict(int)
    deductions = []
    total = 0
    included_layers = set()
    severities = []
    for row in rows:
        finding = row["finding"]
        severity = finding.get("severity")
        if severity not in SEVERITY_WEIGHTS:
            return _unavailable("invalid_finding_severity")
        # One finding can support several risk mappings but is deducted once.
        # The lexicographically first stable unified risk id is the arithmetic
        # root; every mapped risk remains visible in the explanation.
        root = row["riskIds"][0]
        stable_subject = str(finding.get("subjectKey") or finding.get("findingId") or "")
        root_key = (root, stable_subject)
        occurrence = root_counts[root_key]
        root_counts[root_key] += 1
        factor = DUPLICATE_FACTORS[occurrence] if occurrence < len(
            DUPLICATE_FACTORS) else 0
        points = _ceil_percent(SEVERITY_WEIGHTS[severity], factor) if factor else 0
        total += points
        included_layers.add(row["layer"])
        severities.append(severity)
        deductions.append({
            "findingId": finding.get("findingId"),
            "findingType": finding.get("findingType"),
            "severity": severity, "riskIds": row["riskIds"],
            "primaryRiskId": root, "detectorIds": row["detectorIds"],
            "sourceLayer": row["layer"], "baseWeight": SEVERITY_WEIGHTS[severity],
            "duplicateIndex": occurrence, "factorPercent": factor,
            "points": points,
        })
    highest = min(severities, key=lambda x: SEVERITY_ORDER[x]) if severities else None
    cap = SEVERITY_CAPS.get(highest) if highest else 100
    before_cap = max(0, 100 - min(total, 100))
    value = min(before_cap, cap)
    semantic_status = (review.get("semantic") or {}).get("status")
    evaluated_layers = ["L0_static"]
    if semantic_status == "completed":
        evaluated_layers.append("L1_semantic")
    if (review.get("promptBlackbox") or {}).get("status") == "completed":
        evaluated_layers.append("V1_5_blackbox")
    if (review.get("skillSandbox") or {}).get("status") == "completed":
        evaluated_layers.append("V2_sandbox")
    if agent_runtime_state == "completed":
        evaluated_layers.append("V2_agent_runtime")
    return {
        "status": "available", "value": value,
        "policyId": POLICY_ID, "policyVersion": POLICY_VERSION,
        "reasonCodes": [], "highestSeverity": highest,
        "baseScore": 100, "deductionTotal": total,
        "scoreBeforeSeverityCap": before_cap, "severityCap": cap,
        "deductions": deductions,
        "includedLayers": sorted(included_layers),
        "evaluatedLayers": evaluated_layers,
    }


def compute_confidence(review: Dict[str, Any]) -> Dict[str, Any]:
    limitations = []
    coverage = (review.get("coverage") or {}).get("status")
    capabilities = review.get("capabilities") or {}
    semantic_status = (capabilities.get("semantic") or {}).get(
        "status", "not_enabled")
    static_status = (capabilities.get("static") or {}).get("status", "failed")
    if coverage != "sufficient" or static_status == "failed":
        grade = "D"
        limitations.append("deterministic_coverage_incomplete")
    elif semantic_status == "completed":
        grade = "B"
    elif semantic_status == "failed":
        grade = "D"
        limitations.append("semantic_requested_but_failed")
    else:
        grade = "C"
        limitations.append("semantic_not_enabled")
    if review.get("engine") == "skill":
        gitleaks = (((review.get("artifactModel") or {}).get("gitleaksRun")
                     or {}).get("status"))
        if gitleaks not in {None, "completed"}:
            if "secret_scan_incomplete" not in limitations:
                limitations.append("secret_scan_incomplete")
            if grade in {"A", "B"}:
                grade = "C"
    # V1.5 black-box is integrated (Round 74) and, as of Round 88, its
    # completed-scenario failures feed the numeric score (see
    # _mapped_findings); a completed run therefore carries no limitation.
    # Round 89's V2 signal vocabulary remains as dormant research history.
    # Supported product paths cannot currently produce a completed sandbox
    # run: an explicit request is failed/unavailable until isolation is
    # hardened. Prompt black-box remains explicit opt-in and default-OFF.
    prompt_blackbox_status = (capabilities.get("promptBlackbox") or {}).get(
        "status", "not_enabled")
    skill_sandbox_status = (capabilities.get("skillSandbox") or {}).get(
        "status", "not_enabled")
    agent_runtime_status = (
        capabilities.get("agentInstructionRuntime") or {}
    ).get("status", "not_enabled")
    if prompt_blackbox_status == "not_enabled":
        limitations.append("v1_5_blackbox_not_enabled_by_default")
    elif prompt_blackbox_status != "completed":
        limitations.append("v1_5_blackbox_requested_but_failed")
    if skill_sandbox_status == "not_enabled":
        limitations.append("v2_sandbox_not_enabled_by_default")
    elif skill_sandbox_status != "completed":
        limitations.append("v2_sandbox_requested_but_failed")
    if agent_runtime_status == "not_enabled":
        limitations.append("v2_agent_runtime_not_enabled_by_default")
    elif agent_runtime_status != "completed":
        limitations.append("v2_agent_runtime_requested_but_failed")
    limitations.append("capability_breadth_not_evaluated")
    breadth = summarize_coverage()
    return {
        "grade": grade,
        "policyId": CONFIDENCE_POLICY_ID,
        "policyVersion": CONFIDENCE_POLICY_VERSION,
        "limitations": limitations,
        "execution": {"static": static_status, "semantic": semantic_status,
                     "promptBlackbox": prompt_blackbox_status,
                     "skillSandbox": skill_sandbox_status,
                     "agentInstructionRuntime": agent_runtime_status},
        "breadthSummary": breadth,
        "note": ("Grade describes review scope and known capability limits; "
                 "it is separate from the safety score and is not a guarantee."),
    }


def build_remediations(review: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows, errors = mapped_finding_rows(review)
    if errors:
        return []
    ev_ids = {e.get("evidenceId") for e in review.get("evidences") or []}
    semantic_ev_ids = {e.get("evidenceId")
                       for e in (review.get("semantic") or {}).get("evidences") or []}
    result = []
    for row in rows:
        finding = row["finding"]
        guidance = guidance_lookup(finding)
        evidence = [eid for eid in finding.get("evidenceIds") or []
                    if eid in ev_ids or eid in semantic_ev_ids]
        checks = [
            {"code": "finding_absent_after_rerun",
             "label": "使用相同审查范围复查后，该问题不再出现。"},
            {"code": "no_new_high_or_critical",
             "label": "复查没有新增 High 或 Critical 问题。"},
            {"code": "coverage_not_reduced",
             "label": "复查 Coverage 不低于本次，相关检查均成功完成。"},
        ]
        if row["layer"] == "L1_semantic":
            checks.append({
                "code": "same_semantic_configuration",
                "label": "使用同一语义模型、契约和出境策略复查，避免不可比。"})
        result.append({
            "remediationId": "rem-" + str(finding.get("findingId", "unknown")),
            "findingId": finding.get("findingId"),
            "findingType": finding.get("findingType"),
            "severity": finding.get("severity"),
            "riskIds": row["riskIds"], "sourceLayer": row["layer"],
            "priority": guidance.get("priority", "P1"),
            "title": guidance.get("plainTitle", "需要人工复核"),
            "actions": list(guidance.get("whatToDo") or []),
            "evidenceIds": evidence,
            "verificationChecks": checks,
            "applyMode": "proposal_only",
        })
    result.sort(key=lambda item: (
        {"P0": 0, "P1": 1, "P2": 2}.get(item["priority"], 9),
        SEVERITY_ORDER.get(item["severity"], 9), item["remediationId"]))
    return result


def enrich_review(review: Dict[str, Any]) -> Dict[str, Any]:
    """Mutate and return a report projection after capabilities are present."""
    review["score"] = compute_score(review)
    review["reviewConfidence"] = compute_confidence(review)
    review["remediations"] = build_remediations(review)
    return review
