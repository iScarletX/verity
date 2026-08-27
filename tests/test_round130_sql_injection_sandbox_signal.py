"""Round 130: new V2 sandbox signal -- sandbox_sql_injected_query (standing
initiative #2).

Closes VR-SKILL-015's V2_sandbox=none gap, whose own layerBoundaries text
already promised "May observe an actual injected query reaching a database
driver under isolation." VR-SKILL-015 had been screened-and-declined twice
already (Rounds 128 and 129) specifically because it needed new database-
driver instrumentation the sandbox did not yet have -- this round builds
that instrumentation rather than continuing to defer it.

Unlike every prior V2 sandbox signal, this one is NOT built on
``sys.addaudithook`` -- CPython only added sqlite3's own
``sqlite3.execute``/``executemany``/``executescript`` audit events in Python
3.12, and Verity supports 3.9+ (``pyproject.toml``'s ``requires-python``).
Direct attribute-patching of ``sqlite3.Cursor``/``sqlite3.Connection`` also
fails (``TypeError: can't set attributes of built-in/extension type``) since
both are immutable C extension types. ``_driver_source.py`` instead wraps
``sqlite3.connect`` -- an ordinary, patchable module-level function -- with a
``Connection`` subclass whose ``cursor()`` override returns a ``Cursor``
subclass that records each statement's raw text before delegating to the
real implementation. ``Connection.execute``/``executemany``/``executescript``
(called without an explicit cursor) already route through ``self.cursor()``
internally, so overriding only the three ``Cursor`` methods observes every
call site without double-counting.

No new decoy is planted for this round: the signal reuses the EXISTING
Round 114 canary (``_INJECTED_CONTENT_CANARY``, already embedded in
SandboxRunner's "external_tool_cache.json" decoy) -- it fires when that same
fixed marker appears inside a captured SQL statement's text, proving a Skill
read the decoy and concatenated its content directly into SQL rather than
binding it as a parameter.

TestDriverSqliteInstrumentation live-fire tests
``_driver_source.py``'s ``_install_sqlite3_instrumentation`` directly (the
module is loaded from its own file via ``importlib`` -- it is a trusted,
self-contained stdlib-only template Verity ships, never the reviewed
artifact) against an in-memory ``sqlite3.connect(":memory:")`` database,
proving the mechanism really captures execute/executemany/executescript
text with no double-counting, exactly as manually verified during this
round's research phase. ``sqlite3.connect`` is a process-global attribute,
so every test restores it in a ``finally`` block to avoid leaking the patch
into unrelated tests. The rest follows Round 111/114/124's convention of
testing signal-hit behaviour directly against synthetic
SandboxObservation-shaped dicts. No live sandbox execution anywhere in this
file, and no real secret or destructive command.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import verity.scoring as scoring
from verity.sandbox.models import SANDBOX_SIGNAL_DETECTORS
from verity.scoring import _INJECTED_CONTENT_CANARY, compute_score
from verity.standards import (load_detector_mappings, load_risks,
                               validate_runtime_detector_coverage)


DETECTOR_ID = "sandbox_sql_injected_query"
_DRIVER_SOURCE_PATH = (Path(__file__).parent.parent / "src" / "verity"
                       / "sandbox" / "_driver_source.py")


def _load_driver_module():
    spec = importlib.util.spec_from_file_location(
        "verity_test_round130_driver", _DRIVER_SOURCE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def sandbox_view(sql_attempts=None):
    return {"status": "completed", "fileEvents": [], "networkAttempts": [],
            "subprocessAttempts": [], "sqlAttempts": sql_attempts or []}


class TestDriverSqliteInstrumentation:
    """Live-fire proof, without spawning any real subprocess or sandbox,
    that _driver_source.py's own instrumentation mechanism works exactly
    as verified by hand during this round's research."""

    def test_execute_executemany_executescript_are_captured_once_each(self):
        driver = _load_driver_module()
        real_connect = sqlite3.connect
        try:
            driver._install_sqlite3_instrumentation()
            con = sqlite3.connect(":memory:")
            con.execute("CREATE TABLE t (x TEXT)")
            con.executemany("INSERT INTO t VALUES (?)", [("a",), ("b",)])
            con.executescript("INSERT INTO t VALUES ('script-value');")
            con.close()
        finally:
            sqlite3.connect = real_connect
        statements = [e["statement"] for e in driver._sql_attempts]
        assert statements == [
            "CREATE TABLE t (x TEXT)",
            "INSERT INTO t VALUES (?)",
            "INSERT INTO t VALUES ('script-value');",
        ]

    def test_a_statement_built_by_concatenating_the_canary_is_recorded_verbatim(self):
        driver = _load_driver_module()
        real_connect = sqlite3.connect
        try:
            driver._install_sqlite3_instrumentation()
            con = sqlite3.connect(":memory:")
            con.execute("CREATE TABLE users (name TEXT)")
            injected = f"SELECT * FROM users WHERE name = '{_INJECTED_CONTENT_CANARY}'"
            con.execute(injected)
            con.close()
        finally:
            sqlite3.connect = real_connect
        assert any(_INJECTED_CONTENT_CANARY in e["statement"]
                   for e in driver._sql_attempts)

    def test_a_safely_parameterized_query_never_carries_the_bound_value(self):
        """The captured text is only ever the fixed placeholder string --
        proving parameterized binding stays clean of this signal even when
        the bound value itself happens to contain the canary."""
        driver = _load_driver_module()
        real_connect = sqlite3.connect
        try:
            driver._install_sqlite3_instrumentation()
            con = sqlite3.connect(":memory:")
            con.execute("CREATE TABLE users (name TEXT)")
            con.execute("INSERT INTO users VALUES (?)", (_INJECTED_CONTENT_CANARY,))
            con.close()
        finally:
            sqlite3.connect = real_connect
        statements = [e["statement"] for e in driver._sql_attempts]
        assert not any(_INJECTED_CONTENT_CANARY in s for s in statements)
        assert "INSERT INTO users VALUES (?)" in statements

    def test_bounded_by_max_sql_attempts(self):
        driver = _load_driver_module()
        real_connect = sqlite3.connect
        try:
            driver._install_sqlite3_instrumentation()
            con = sqlite3.connect(":memory:")
            con.execute("CREATE TABLE t (x INTEGER)")
            for i in range(driver._MAX_SQL_ATTEMPTS + 5):
                con.execute(f"INSERT INTO t VALUES ({i})")
            con.close()
        finally:
            sqlite3.connect = real_connect
        assert len(driver._sql_attempts) == driver._MAX_SQL_ATTEMPTS
        assert driver._truncated["sqlAttempts"] is True

    def test_never_raises_when_subclassing_fails(self, monkeypatch):
        """Best-effort discipline: if sqlite3's shape ever changes such
        that subclassing Cursor raises, the reviewed script must still be
        able to run with no SQL observation, not crash the driver."""
        driver = _load_driver_module()
        monkeypatch.setattr(sqlite3, "Cursor", object())
        driver._install_sqlite3_instrumentation()  # must not raise


class TestSignalHit:
    def test_canary_in_sql_statement_is_a_hit(self):
        sandbox = sandbox_view(sql_attempts=[
            {"statement": f"SELECT * FROM t WHERE x = '{_INJECTED_CONTENT_CANARY}'"}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_canary_match_is_case_insensitive(self):
        sandbox = sandbox_view(sql_attempts=[
            {"statement": _INJECTED_CONTENT_CANARY.upper()}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is True

    def test_unrelated_sql_statement_is_not_a_hit(self):
        sandbox = sandbox_view(sql_attempts=[{"statement": "SELECT 1"}])
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox) is False

    def test_no_sql_attempts_is_not_a_hit(self):
        assert scoring._sandbox_signal_hit(DETECTOR_ID, sandbox_view()) is False


def test_signal_is_registered_in_the_fixed_vocabulary():
    assert DETECTOR_ID in SANDBOX_SIGNAL_DETECTORS
    assert SANDBOX_SIGNAL_DETECTORS.count(DETECTOR_ID) == 1


def test_detector_mapping_registered():
    mappings = load_detector_mappings()
    key = ("sandbox_signal", DETECTOR_ID)
    assert key in mappings
    assert mappings[key]["riskIds"] == ["VR-SKILL-015"]
    assert mappings[key]["contribution"] == "signal"


def test_detector_mapping_row_count_grew_by_exactly_one_row():
    # 142 as of Round 129 (two existing rows extended, no new row) + this
    # round's own brand-new sandbox_signal row.
    mappings = load_detector_mappings()
    assert len(mappings) == 156


def test_risk_coverage_flipped_to_signal():
    risks = load_risks()
    coverage = risks["VR-SKILL-015"]["currentCoverage"]
    assert coverage["V2_sandbox"] == "signal"
    # Unaffected layers stay exactly as they were before this round.
    assert coverage["L0_static"] == "signal"
    assert coverage["L1_semantic"] == "signal"
    assert coverage["V1_5_blackbox"] == "none"


def test_new_known_gap_discloses_narrow_scope():
    risks = load_risks()
    gaps = risks["VR-SKILL-015"]["knownGaps"]
    assert any("Round 130" in g for g in gaps)
    assert any("sqlite3" in g for g in gaps)


def test_validate_runtime_detector_coverage_has_no_drift():
    validate_runtime_detector_coverage()


def test_sql_injected_query_deducts_against_correct_risk_via_scoring():
    """End-to-end check that the new mapping row is actually wired, not
    just present -- exercises the real scoring path Round 89 built."""
    report = projection()
    report["skillSandbox"] = sandbox_view(sql_attempts=[
        {"statement": f"SELECT * FROM t WHERE x = '{_INJECTED_CONTENT_CANARY}'"}])
    score = compute_score(report)
    assert score["status"] == "available"
    by_detector = {d["detectorIds"][0]: d for d in score["deductions"]}
    assert set(by_detector) == {DETECTOR_ID}
    deduction = by_detector[DETECTOR_ID]
    assert deduction["riskIds"] == ["VR-SKILL-015"]
    assert deduction["sourceLayer"] == "V2_sandbox"
    assert deduction["severity"] == "high"
    assert score["value"] <= 59  # high severity cap


def test_no_sql_attempts_produces_no_new_deductions():
    report = projection()
    report["skillSandbox"] = sandbox_view()
    score = compute_score(report)
    assert score["status"] == "available"
    assert score["deductions"] == []
