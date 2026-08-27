"""Internal research dataclasses for the unavailable V2 Skill prototype.

These types describe the historical ``sandbox-exec`` research runner. They are
not a product isolation contract, are not exported from ``verity.sandbox``,
and must not cross the public report boundary. Supported Review, CLI, Web, and
standalone paths fail closed before this prototype is constructed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class SandboxConfigurationError(ValueError):
    """Trusted runtime environment configuration could not be staged safely."""


# ``status`` controlled values:
#   completed        — the driver ran to completion (the reviewed script
#                       may itself have raised; that is still "completed"
#                       from the sandbox's point of view, see
#                       ``raisedException``).
#   failed            — the sandbox harness itself could not run (e.g.
#                        malformed observation JSON, staging error).
#   timeout           — wall-clock budget exceeded; process killed.
#   killed_memory     — RSS watchdog breached the memory budget; killed.
#   killed_cpu        — CPU-time rlimit breached; process received SIGXCPU
#                        (surfaced here rather than as a bare "killed").
#   not_available     — sandbox-exec is not present on this host/platform.
#   no_entry_point    — the requested entry point does not exist in the
#                        staged snapshot.
SANDBOX_STATUSES = (
    "completed",
    "failed",
    "timeout",
    "killed_memory",
    "killed_cpu",
    "not_available",
    "no_entry_point",
)


@dataclass
class SandboxRunRequest:
    """Internal request shape retained for controlled research tests.

    ``entry_point`` is a snapshot-relative, forward-slash path (same
    normalization as ``ArtifactFile.normalizedPath``); it is validated
    against the staged tmpdir before use, never trusted as an absolute
    host path.
    """

    entry_point: str
    argv: List[str] = field(default_factory=list)
    cpu_seconds: int = 10
    memory_mb: int = 256
    wall_seconds: int = 20
    poll_interval_seconds: float = 0.2
    # None preserves the prototype's historical all-decoy behavior. Internal
    # research callers/tests may pass an artifact-aware list, including [],
    # when only passive observers should be active. Product review never
    # constructs this request.
    syntheticFixtures: Optional[List[Any]] = None


@dataclass
class SandboxObservation:
    """Raw internal prototype observation; never a public report schema."""
    status: str                                   # see SANDBOX_STATUSES
    reasonCode: Optional[str] = None
    isolationMechanism: str = "none"              # sandbox-exec|none
    entryPoint: Optional[str] = None
    argv: List[str] = field(default_factory=list)
    durationSeconds: Optional[float] = None
    exitCode: Optional[int] = None
    terminatedBySignal: Optional[str] = None
    peakMemoryMb: Optional[float] = None
    raisedException: Optional[Dict] = None         # {type, message}
    fileEvents: List[Dict] = field(default_factory=list)       # {op, path, insideSandbox}
    networkAttempts: List[Dict] = field(default_factory=list)  # {host, port, allowed}
    subprocessAttempts: List[Dict] = field(default_factory=list)  # {argv0, argvPreview}
    sqlAttempts: List[Dict] = field(default_factory=list)       # {statement}
    stdoutBytes: int = 0
    stderrBytes: int = 0
    syntheticFixtures: List[Dict] = field(default_factory=list)
    truncated: Dict = field(default_factory=lambda: {
        "fileEvents": False,
        "networkAttempts": False,
        "subprocessAttempts": False,
        "sqlAttempts": False,
    })


# Dormant, hand-designed research vocabulary derived from raw prototype
# observations (see scoring.py::mapped_finding_rows). Unlike
# blackbox's ProbeScenario, there is no runtime object per detector here --
# this tuple IS the registry that standards.validate_runtime_detector_coverage()
# drift-checks detector_mappings.json's "sandbox_signal" entries against, and
# retained historical scoring tests iterate to evaluate:
#   sandbox_write_outside_tmpdir -- a fileEvents write with insideSandbox=False.
#     The Seatbelt profile only allows file-write* under the tmpdir subpath,
#     so this is always an actually-denied escape attempt, never noise from
#     normal execution.
#   sandbox_network_attempt -- any networkAttempts entry. The profile has no
#     network-outbound allow clause at all, so `allowed` is always False by
#     construction (see runner.py's _record_network) -- any entry here is an
#     unambiguous denied egress attempt.
#   sandbox_subprocess_attempt -- any subprocessAttempts entry. The profile
#     allows process-exec/process-fork (Python itself needs it to start), so
#     this spawn actually SUCCEEDS; it is audited rather than denied, but a
#     reviewed Skill spawning a subprocess at review time is unusual enough
#     to surface as a signal.
#   sandbox_sensitive_path_read (Round 102) -- a fileEvents read whose path
#     matches verity.engine's _SENSITIVE_PATH_PATTERNS (the same well-known
#     SSH/cloud-credential/shell-history/system-password vocabulary the L0
#     static rule skill_sensitive_path_access already flags in Skill text).
#     This is a narrow, deliberate carve-out from the "bare reads are noise"
#     exclusion below: a generic outside-tmpdir read has no discriminating
#     power, but a read of one of these specific host-identity paths does --
#     no legitimate one-shot Skill needs to open ~/.ssh/id_rsa or /etc/shadow.
#   sandbox_fake_credential_read (Round 111) -- a fileEvents read of the one
#     fixed, synthetic decoy file (runner.py's _stage_fake_credential_decoy
#     plants "credentials.json" with an obviously-fake value at the tmpdir
#     root before every run) with insideSandbox=True. Deliberately a
#     DIFFERENT filename vocabulary from sandbox_sensitive_path_read's real
#     host-identity paths -- this signal exists to see whether a Skill
#     opportunistically reads a credential-shaped file that VERITY planted
#     in its own working directory, not whether it reaches for a real host
#     secret location. No legitimate one-shot Skill needs to open a
#     "credentials.json" it did not itself ship.
#   sandbox_injected_content_propagation (Round 114) -- the fixed, synthetic
#     canary marker embedded in runner.py's other planted decoy
#     ("external_tool_cache.json", _stage_injected_content_decoy) appears
#     verbatim inside a subprocessAttempts entry's argvPreview or a
#     networkAttempts entry's host. Unlike the four signals above, this one
#     is not "did the Skill touch a specific path" but "did untrusted
#     content the Skill read get propagated into a dangerous sink" -- a
#     qualitatively stronger indirect-injection signal for VR-SKILL-005's
#     "May inject controlled external/tool content and observe propagation"
#     layer boundary. A bare read of the decoy file (with no propagation)
#     does not trip this signal; that is deliberate, since merely reading a
#     file a Skill was given no reason to trust is not itself a finding.
#     Round 120 mapped this same detector row to a second riskId,
#     VR-PROMPT-008 ("Untrusted content boundary is undefined") -- its own
#     V2_sandbox boundary text ("may observe how a Skill propagates
#     retrieved content into tools and prompts") describes the identical
#     runtime behavior from a broader risk angle, the same "two risks, one
#     detector" reuse Round 92 established for a semantic_finding_type row
#     (see detector_mappings.json's riskIds list growing to a second entry,
#     not a new row).
#   sandbox_undeclared_network_attempt / sandbox_undeclared_subprocess_attempt
#     (Round 116) -- the first signals that cross-reference two independent
#     sources instead of observing the sandbox alone: a networkAttempts (resp.
#     subprocessAttempts) entry exists AND the Skill's manifest declares no
#     network_access (resp. process_execution) permission family (see
#     scoring.py::_declared_capability_families, which reuses semantic/
#     catalog.py::_permission_descriptor's own family-prefix rules so this
#     runtime comparison never drifts from the static
#     semantic.skill.permission_capability_mismatch comparison). This is
#     qualitatively different evidence from the bare sandbox_network_attempt/
#     sandbox_subprocess_attempt signals above: those fire on ANY attempt,
#     unconditionally; these fire only when the attempt also has no matching
#     declared permission -- a runtime-confirmed instance of VR-SKILL-004's
#     "overbroad/undeclared permissions" and VR-SKILL-012's "declared
#     behavior differs from implementation", stronger evidence than the
#     static AST-only comparison because the capability was actually
#     exercised, not just imported or call-sited. Deliberately mirrors
#     _permission_descriptor's existing precedent of NOT treating a bare "*"
#     wildcard permission as declaring every family -- the static comparison
#     already treats "*" as unmatched for the same reason (VR-SKILL-004 is
#     about overbroad permissions, so "*" being unmatched is the intended
#     behavior, not a bug), and the runtime signal stays consistent with it
#     rather than silently suppressing itself for wildcard-permission Skills.
#   sandbox_cleartext_network_attempt (Round 117) -- a networkAttempts entry
#     whose port is in a small fixed vocabulary of well-known plaintext
#     protocols (20/21 FTP, 23 Telnet, 25 SMTP, 80 HTTP, 110 POP3, 143 IMAP --
#     see scoring.py::_CLEARTEXT_PORTS). Every attempt observed here was
#     already denied by the sandbox profile (same as sandbox_network_attempt
#     above), so this is a narrower discriminator over the same field, not a
#     new observation source: it distinguishes "connected to a port whose
#     well-known protocol has no transport encryption" from an arbitrary
#     attempt, for VR-SKILL-008's "Weak cryptography or transport
#     protection". Deliberately weaker evidence than it sounds: a port
#     number is not proof of the actual protocol spoken on it (a Skill could
#     run TLS on 8080 or plaintext on 8443), and _driver_source.py's
#     _record_network leaves `port` as `None` for non-AF_INET/AF_INET6
#     addresses (e.g. AF_UNIX), which this signal correctly never matches.
#   sandbox_dependency_install_attempt (Round 119) -- a subprocessAttempts
#     entry whose argv0 basename is a well-known package-manager binary
#     (pip/pip3/npm/yarn/pnpm/conda/gem/cargo/go, see
#     scoring.py::_DEPENDENCY_INSTALL_BINARIES) AND whose argvPreview also
#     contains an install-like subcommand token (install/add/get, see
#     scoring.py::_DEPENDENCY_INSTALL_SUBCOMMANDS) -- e.g. a Skill shelling
#     out to `pip install <pkg>` at review time rather than declaring the
#     dependency ahead of time. For VR-SKILL-003's "Dependency drift, known
#     vulnerabilities, or unverifiable provenance": an ad-hoc runtime
#     install is inherently unpinned and unreviewable, matching the risk's
#     own V2_sandbox boundary text ("May observe installation/runtime
#     behavior but cannot prove supply-chain provenance alone") -- this
#     signal only proves an install was ATTEMPTED, never which package/
#     version actually landed, whether it succeeded, or whether it matches
#     anything in a requirements.txt/package.json the Skill also ships.
#   sandbox_deserialization_effect (Round 124) -- the fixed, synthetic canary
#     marker embedded in runner.py's third planted decoy ("cache.pkl",
#     _stage_deserialization_effect_decoy) appears inside a
#     subprocessAttempts entry's argvPreview. That decoy's bytes are a pickle
#     payload whose __reduce__ returns (os.system, (command,)) -- pickle's
#     REDUCE opcode calls that callable with those args purely from the
#     bytes themselves, with no dependency on the reviewed Skill importing
#     or even knowing about the class that produced it, so the embedded
#     os.system call fires the moment ANY code in the sandboxed process
#     calls pickle.load/pickle.loads on this file. This is qualitatively
#     different evidence from the read-only signals above (sandbox_
#     sensitive_path_read, sandbox_fake_credential_read): those only prove a
#     Skill opened a specific path, while this proves the Skill's own code
#     path actually deserialized attacker-shaped pickle bytes and let them
#     execute -- a real, observed instance of VR-SKILL-007's "Unsafe
#     deserialization or parser configuration", not merely an opportunity
#     for one. A bare read of "cache.pkl" with no accompanying deserialize
#     call does not trip this signal, matching the same "reading alone is
#     not a finding" discipline as sandbox_injected_content_propagation.
#     Deliberately narrow: this only covers the Python pickle format via one
#     fixed, guessable decoy filename, never yaml.load/XML/other-language
#     deserializers, and cannot prove any REAL untrusted input the Skill
#     receives through another channel is attacker-controlled.
#   sandbox_sql_injected_query (Round 130) -- the same fixed, synthetic
#     canary marker used by sandbox_injected_content_propagation (embedded
#     in runner.py's "external_tool_cache.json" decoy) appears verbatim
#     inside a sqlAttempts entry's captured SQL statement text. Unlike every
#     signal above, sqlAttempts is not populated by the sys.addaudithook
#     mechanism _driver_source.py otherwise uses throughout -- CPython only
#     added sqlite3's own execute/executemany/executescript audit events in
#     Python 3.12, and this sandbox supports 3.9+ -- so the driver instead
#     wraps sqlite3.connect() with a Connection subclass whose cursor()
#     override returns a Cursor subclass that records each statement's raw
#     text before delegating to the real implementation (subclassing an
#     extension type is allowed even though patching its methods directly is
#     not; Connection.execute/executemany/executescript all internally route
#     through self.cursor(), so overriding only Cursor's three methods
#     observes all of them without double-counting). A safely parameterized
#     query's captured text is only ever the fixed placeholder string (bound
#     values are never part of the captured statement); the canary can only
#     appear here if a Skill read the decoy and concatenated its content
#     directly into SQL text before executing it -- a real, observed
#     instance of VR-SKILL-015's "string formatting/concatenation instead of
#     parameterized binding", not merely an opportunity for one, matching
#     the same "reading alone is not a finding" discipline as
#     sandbox_injected_content_propagation and sandbox_deserialization_effect
#     above. Deliberately narrow: only the stdlib sqlite3 driver is
#     instrumented (never psycopg2/pymysql/any third-party driver), and only
#     a Skill that calls sqlite3.connect() itself is covered -- one that
#     constructs sqlite3.Connection directly bypasses the factory override
#     entirely.
# Deliberately excluded: bare file *read* events of anything NOT on that
# sensitive-path list (file-read* is allowed unconditionally so python3 can
# start at all -- nearly every real run reads paths outside the tmpdir just
# importing the stdlib, so this would be pure noise with no discriminating
# power) and terminal states like timeout/killed_memory/killed_cpu/
# raisedException (resource exhaustion or a crash is equally consistent with
# an inefficient/buggy Skill as with a hostile one -- no way to tell them
# apart from this signal alone).
SANDBOX_SIGNAL_DETECTORS = (
    "sandbox_write_outside_tmpdir",
    "sandbox_network_attempt",
    "sandbox_subprocess_attempt",
    "sandbox_sensitive_path_read",
    "sandbox_fake_credential_read",
    "sandbox_injected_content_propagation",
    "sandbox_undeclared_network_attempt",
    "sandbox_undeclared_subprocess_attempt",
    "sandbox_cleartext_network_attempt",
    "sandbox_dependency_install_attempt",
    "sandbox_deserialization_effect",
    "sandbox_sql_injected_query",
)
